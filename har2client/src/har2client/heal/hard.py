"""v3 hard repair task builder (DESIGN-CASE3-HARD.md): a release of 1-4 changes -> task directory, with its gates.

Task dir (as `heal.build`, plus): `CHANGELOG.md` (visible, unless the changelog level is `none`); `tests.json` holds 15 or more
tests, each with `needs` (the changes whose single-flaw reference client fails that test, for per-change credit); `ref_client.py`
is the v2 reference client of the task's `client_style` (`compact` or `sdk`). `task.json["heal"]` records the factors: set,
mutations, changes, error_detail, evidence, evidence_scope, changelog, client_style, identifiable, fix_lines, fix_classes, seeds.

Gates (`verify_hard_task`): `client_v1.py` scores 1.0 on v1 and less than 1.0 on v2; the v2 reference client scores 1.0; each change
is needed (the v2 reference client with the flaw of that one change scores below 1.0: DepBench four-state check); the fix
(v1 client -> v2 reference client of the same style) has at least 30 changed lines for 3 changes or more and, for `sdk`, touches
2 classes or more; at least 15 tests with every operation at least twice; no hidden data in a visible file; the recording shows each
change; a misleading changelog states the wrong fact against a recording that shows the truth."""

from __future__ import annotations

import ast
import json
import random
import re
import tempfile
from pathlib import Path

from har2client.env.sources import canon
from har2client.env.sources.browser import render_har
from har2client.heal import changelog as changelog_mod
from har2client.heal import failure, hardtests, identify, mutations, session
from har2client.heal.build import (
    CHANGELOG_FILE, EVIDENCE_FILES, HEAL_NOISE, HealBuildError, _grade_copy, _item_ids, _write_json, find_fix_wordings, find_leaks,
    render_heal_goal, visible_files,
)
from har2client.heal.patch import patch_size
from har2client.heal.select import SEED_SPACE
from har2client.tierb.build import features
from har2client.tierb.config import DRIFT_KINDS
from har2client.tierb.engine import ERROR_DETAILS
from har2client.tierb.refclient import generate_client_source
from har2client.tierb.spec import OPS, make_credentials, make_spec, method_name

CLIENT_STYLES = ("compact", "sdk")
CHANGELOG_LEVELS = changelog_mod.LEVELS
MIN_FIX_LINES = 30  # for a release of this many changes or more
MIN_FIX_LINES_CHANGES = 3
MIN_FIX_CLASSES_SDK = 2
MIN_OPS_TIMES = 2
RECORDING_TRIES = 12
TEST_SEED_TRIES = 400
ENUM_RECORDING_ITEMS = 20  # a recording of this many items shows every value of the enum field almost always
# the reference-client flaw that leaves one change out (`tierb.refclient.FLAWS`)
CHANGE_FLAW = {
    **{f"add_drift_{k}": f"no_drift_{k}" for k in DRIFT_KINDS},
    "add_rate_limit": "no_rate_limit_retry", "add_async_create": "no_job_poll", "add_refresh_rotation": "no_refresh",
    "add_idempotency_key": "no_idempotency_key", "add_moved": "no_moved_path",
}
CHANGELOG_NAME = CHANGELOG_FILE
RULE_RETURNS = "- Keep what each method returns as `client_v1.py` returns it: the same field names, value types and formats, whatever the new API sends."
ANCHOR_RULE = "- Keep the constructor, the method names and the arguments as `client_v1.py` has them."


def generate_source(style: str, spec: dict, flaws: tuple[str, ...] = ()) -> str:
    """Source of the client of `style` for `spec`. The sdk renderer may raise NotImplementedError for a switch it lacks."""
    if style == "compact":
        return generate_client_source(spec, flaws)
    if style == "sdk":
        from har2client.heal.sdk_client import generate_sdk_client_source  # imported here: the module is built apart

        return generate_sdk_client_source(spec, flaws)
    raise ValueError(f"client_style must be one of {CLIENT_STYLES}, got {style!r}")


# ------------------------------------------------------------ fix size


def _class_sources(source: str) -> dict[str, str]:
    tree = ast.parse(source)
    return {n.name: ast.get_source_segment(source, n) or "" for n in tree.body if isinstance(n, ast.ClassDef)}


def fix_size(old_source: str, new_source: str) -> tuple[int, int]:
    """(changed lines, classes touched) between the v1 client and the v2 reference client: a class counts when its text changed
    or it is in only one of the files."""
    old, new = _class_sources(old_source), _class_sources(new_source)
    touched = sum(1 for name in {*old, *new} if old.get(name) != new.get(name))
    return patch_size(old_source, new_source)["changed_lines"], touched


def check_fix_size(names: tuple[str, ...], style: str, lines: int, classes: int) -> str | None:
    """The reason the fix is too small, or None."""
    if len(names) >= MIN_FIX_LINES_CHANGES and lines < MIN_FIX_LINES:
        return f"the fix has {lines} changed lines, expected at least {MIN_FIX_LINES} for {len(names)} changes"
    if style == "sdk" and classes < MIN_FIX_CLASSES_SDK:
        return f"the sdk fix touches {classes} class, expected at least {MIN_FIX_CLASSES_SDK}"
    return None


# ------------------------------------------------------------ what the recording shows


def _key(name: str) -> re.Pattern:
    """A JSON object key in a body text that is JSON itself or a JSON string (quotes escaped)."""
    return re.compile(r'\\?"' + re.escape(name) + r'\\?"\s*:')


def _bodies(har: dict, side: str) -> list[str]:
    out = []
    for e in har["log"]["entries"]:
        if side == "response":
            out.append(e["response"].get("content", {}).get("text") or "")
        else:
            out.append((e["request"].get("postData") or {}).get("text") or "")
    return out


def _wire_enum_values(spec: dict) -> list[str]:
    d = spec["drift"]["enum"]
    return [v.upper() if d["mode"] == "upper" else d["prefix"] + v for v in d["values"]]


def _shows(change: str, spec: dict, har: dict) -> bool:
    entries = har["log"]["entries"]
    responses = _bodies(har, "response")
    urls = [e["request"]["url"] for e in entries]
    d = spec.get("drift", {})
    if change == "add_drift_rename":
        return any(_key(d["rename"]["to"]).search(t) for t in responses)
    if change == "add_drift_cents":
        return any(_key(d["cents"]["to"]).search(t) for t in responses)
    if change == "add_drift_epoch":
        return any(re.search(r'\\?"created_at\\?"\s*:\s*\d{9,}', t) for t in responses)
    if change == "add_drift_nest":
        return any(re.search(r'\\?"' + re.escape(d["nest"]["key"]) + r'\\?"\s*:\s*\{', t) for t in responses)
    if change == "add_drift_enum":
        text = "\n".join(responses)
        return all(re.search(r'\\?"' + re.escape(v) + r'\\?"', text) for v in _wire_enum_values(spec))
    if change == "add_rate_limit":
        return any(
            e["response"]["status"] == 429 and any(h["name"].lower() == "retry-after" for h in e["response"]["headers"]) for e in entries
        )
    if change == "add_async_create":
        return any(e["response"]["status"] == 202 for e in entries)
    if change == "add_refresh_rotation":
        return any(u.split("?")[0].endswith(spec["refresh"]["path"]) for u in urls)
    if change == "add_idempotency_key":
        name = spec["idempotency"]["header"].lower()
        return any(e["request"]["method"] != "GET" and any(h["name"].lower() == name for h in e["request"]["headers"]) for e in entries)
    if change == "add_moved":
        return any(spec["moved"]["prefix"] + "/" in u for u in urls)
    raise ValueError(f"unknown change {change}")


def recording_misses(spec: dict, names: tuple[str, ...], har: dict) -> list[str]:
    """The changes that the recording does not show (an empty list: every change shows at least once)."""
    return [n for n in names if not _shows(n, spec, har)]


# ------------------------------------------------------------ goal


def render_hard_goal(spec: dict, evidence: str = "both", has_changelog: bool = False) -> str:
    """The repair goal of `heal.build` plus the rule that return values keep the v1 shape and, with a changelog, its file entry."""
    text = render_heal_goal(spec, evidence)
    if has_changelog:
        text = text.replace("\n\n## Constructor", f"\n- `{CHANGELOG_NAME}`: the release notes of the new version of the API.\n\n## Constructor", 1)
    if ANCHOR_RULE not in text:
        raise HealBuildError("the repair goal has no constructor rule to extend")
    return text.replace(ANCHOR_RULE, f"{ANCHOR_RULE}\n{RULE_RETURNS}", 1)


# ------------------------------------------------------------ build


def _credentials(spec: dict, v1_spec: dict, rng: random.Random) -> tuple[dict, dict, dict, dict]:
    recording, practice, test = (make_credentials(spec, rng) for _ in range(3))
    while len({recording["password"], practice["password"], test["password"]}) < 3 or len({recording["username"], practice["username"], test["username"]}) < 3:
        recording, practice, test = (make_credentials(spec, rng) for _ in range(3))
    return recording, practice, test, make_credentials(v1_spec, rng)


def _record(spec: dict, names: tuple[str, ...], scope: str, recording: dict, rng: random.Random, noise: float, seed_tag: str):
    """(visible HAR text, HAR dict, summary, recording seed): the first recording that shows every change (and, for an enum
    drift, every value of the field)."""
    ops = session.partial_ops(names) if scope == "partial" else OPS
    n_items = ENUM_RECORDING_ITEMS if "add_drift_enum" in names else None
    for _ in range(RECORDING_TRIES):
        rec_seed = rng.randrange(SEED_SPACE)
        recorded, summary = session.record_session(spec, recording, rec_seed, rng, ops=ops, n_items=n_items)
        canon_session = canon.from_har(recorded, base_path=spec["base_path"], noise=noise, seed=seed_tag)
        har = render_har(canon_session, rng)
        text = canon.dumps_har(har, canon_session.compact)
        if not recording_misses(spec, names, json.loads(text)):
            return text, har, summary, rec_seed
    raise HealBuildError(f"{'+'.join(names)}: no recording shows every change in {RECORDING_TRIES} tries")


def _pick_test_seed(spec: dict, rng: random.Random, avoid_seeds: set[int], avoid_ids: set[str], visible_text: str) -> int:
    """A test seed with two multi-page list tests whose start data shares no id with a recording, a practice server or a visible text."""
    for _ in range(TEST_SEED_TRIES):
        seed = rng.randrange(SEED_SPACE)
        if seed in avoid_seeds or hardtests.multi_page_lists(spec, seed) < hardtests.MIN_PAGED_LISTS:
            continue
        ids = _item_ids(spec["domain"], seed)
        if not ids & avoid_ids and not any(i in visible_text for i in ids):
            return seed
    raise HealBuildError("no test seed with two multi-page lists and without ids from the recording or the practice server")


def build_hard_task(row: dict, out_dir: str | Path, *, build_seed: int = 0, noise: float = HEAL_NOISE) -> Path:
    """Build `out_dir/<task_id>/` from a manifest row {task_id, set, index, mutations, domain, config (v2), seed, error_detail,
    evidence, evidence_scope, changelog, client_style}; run the gates. Raises HealBuildError when one fails."""
    names = mutations.names_of(row["mutations"])
    error_detail, evidence = row.get("error_detail", "verbose"), row.get("evidence", "both")
    scope, level, style = row.get("evidence_scope", "full"), row.get("changelog", "none"), row.get("client_style", "compact")
    if error_detail not in ERROR_DETAILS or evidence not in identify.EVIDENCE_LEVELS or level not in CHANGELOG_LEVELS or style not in CLIENT_STYLES:
        raise ValueError(f"bad factor: {error_detail}, {evidence}, {level}, {style}")
    label = "+".join(names)
    spec = make_spec(row["domain"], row["config"], row["seed"])
    if error_detail != "verbose":
        spec["error_detail"] = error_detail
    v1_spec = mutations.derive_v1_spec(spec, names, row["seed"])
    flaws = mutations.v1_flaws(v1_spec)
    client_v1, ref_source = generate_source(style, v1_spec, flaws), generate_source(style, spec)
    fix_lines, fix_classes = fix_size(client_v1, ref_source)
    if (small := check_fix_size(names, style, fix_lines, fix_classes)) is not None:
        raise HealBuildError(f"{label} on {row['domain']}: {small}")
    seed_tag = f"heal3:{build_seed}:{row['seed']}"
    rng = random.Random(seed_tag)
    recording, practice, test, v1_test = _credentials(spec, v1_spec, rng)
    har_text, har, summary, rec_seed = _record(spec, names, scope, recording, rng, noise, seed_tag)
    notes = changelog_mod.render(spec, list(names), level, seed_tag)
    practice_seed = rng.randrange(SEED_SPACE)
    with tempfile.TemporaryDirectory(prefix="h2c_heal3_build_") as tmp:
        v1_path = Path(tmp) / "client_v1.py"
        v1_path.write_text(client_v1, encoding="utf-8")
        try:
            report = failure.capture_failure(spec, v1_path, practice_seed, practice, hardtests.make_hard_tests(spec, practice_seed), show_values=True)
        except failure.NoFailure as exc:
            raise HealBuildError(f"{label} on {row['domain']}: {exc}") from exc
    avoid_ids = _item_ids(spec["domain"], rec_seed, summary["items"]) | _item_ids(spec["domain"], practice_seed)
    visible_text = har_text + report["text"] + (notes[0] if notes else "")
    test_seed = _pick_test_seed(spec, rng, {rec_seed, practice_seed}, avoid_ids, visible_text)
    tests = hardtests.make_hard_tests(spec, test_seed)

    task_id = row["task_id"]
    task_dir = Path(out_dir) / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "goal.md").write_text(render_hard_goal(spec, evidence, notes is not None), encoding="utf-8")
    (task_dir / "client_v1.py").write_text(client_v1, encoding="utf-8")
    if "failure.txt" in EVIDENCE_FILES[evidence]:
        (task_dir / "failure.txt").write_text(report["text"], encoding="utf-8")
    if "session.har" in EVIDENCE_FILES[evidence]:
        (task_dir / "session.har").write_text(har_text, encoding="utf-8")
    if notes is not None:
        (task_dir / CHANGELOG_NAME).write_text(notes[0], encoding="utf-8")
    _write_json(task_dir / "engine.json", spec)
    _write_json(task_dir / "tests.json", tests)
    (task_dir / "ref_client.py").write_text(ref_source, encoding="utf-8")
    _write_json(
        task_dir / "task.json",
        {
            "task_id": task_id, "tier": "B", "api_id": f"heal3:{label}:{row['index']:02d}", "title": spec["title"],
            "server_url": spec["server_url"], "domain": spec["domain"], "config": spec["config"],
            "goal_ops": [method_name(spec, op) for op in OPS],
            "seeds": {"recording": rec_seed, "test": test_seed, "spec": row["seed"]}, "build_seed": build_seed,
            "credentials": {"recording": recording, "test": test}, "noise_ratio": noise,
            "session_calls": summary["requests"], "har_entries": len(har["log"]["entries"]), "recorded_items": summary["listed"],
            "features": [*features(spec), "case:heal3", *(f"mutation:{n}" for n in names)],
            "heal": {
                "mutation": label, "base_config": v1_spec["config"], "v1_spec": v1_spec, "v1_flaws": list(flaws), "v1_credentials": v1_test,
                "practice": {"seed": practice_seed, "credentials": practice},
                "failure": {k: report[k] for k in ("index", "reason", "detail")},
                "set": row.get("set", "v3"), "mutations": list(names), "changes": len(names), "error_detail": error_detail,
                "evidence": evidence, "evidence_scope": scope, "changelog": level, "changelog_info": notes[1] if notes else None,
                "client_style": style, "identifiable": identify.label(names, evidence, scope), "fix_lines": fix_lines,
                "fix_classes": fix_classes,
                "seeds": {"spec": row["seed"], "build": build_seed, "recording": rec_seed, "practice": practice_seed, "test": test_seed},
            },
        },
    )
    scores = verify_hard_task(task_dir)
    task = json.loads((task_dir / "task.json").read_text(encoding="utf-8"))
    task["heal"]["gate"] = {k: scores[k] for k in ("v1_on_v1", "v1_on_v2", "ref_on_v2")}
    task["heal"]["necessity"] = scores["necessity"]
    _write_json(task_dir / "task.json", task)
    _write_json(task_dir / "tests.json", [{**t, "needs": scores["needs"][i]} for i, t in enumerate(tests)])
    return task_dir


# ------------------------------------------------------------ verify


def _failed_indices(grade: dict) -> list[int]:
    return [r["index"] for r in grade["results"] if not r["passed"]]


def _check_tests(tests: list[dict], name: str) -> None:
    if len(tests) < hardtests.MIN_TESTS:
        raise HealBuildError(f"{name}: {len(tests)} hidden tests, expected at least {hardtests.MIN_TESTS}")
    for op in OPS:
        if sum(t["op"] == op for t in tests) < MIN_OPS_TIMES:
            raise HealBuildError(f"{name}: operation {op} is tested fewer than {MIN_OPS_TIMES} times")


def _check_changelog(task: dict) -> None:
    name, heal = task["task_id"], task["heal"]
    info = heal.get("changelog_info")
    if heal["changelog"] == "none":
        return
    if info is None or info["level"] != heal["changelog"]:
        raise HealBuildError(f"{name}: changelog info does not match the level")
    if heal["changelog"] == "misleading" and info["misleading"]["stated"] == info["misleading"]["truth"]:
        raise HealBuildError(f"{name}: the misleading changelog states the truth")
    if heal["changelog"] == "incomplete" and info["omitted"] not in heal["mutations"]:
        raise HealBuildError(f"{name}: the incomplete changelog omits a change that is not in the release")


def verify_hard_task(task_dir: str | Path) -> dict:
    """Re-run the gates from the files of a built task. Raises HealBuildError on the first violation.
    Returns {v1_on_v1, v1_on_v2, ref_on_v2, necessity: {change: pass rate without it}, needs: [[changes] per test]}."""
    task_dir = Path(task_dir)
    task = json.loads((task_dir / "task.json").read_text(encoding="utf-8"))
    heal, name = task["heal"], task["task_id"]
    names, style = tuple(heal["mutations"]), heal["client_style"]
    spec = json.loads((task_dir / "engine.json").read_text(encoding="utf-8"))
    tests = json.loads((task_dir / "tests.json").read_text(encoding="utf-8"))
    v1_source = (task_dir / "client_v1.py").read_text(encoding="utf-8")
    ref_source = (task_dir / "ref_client.py").read_text(encoding="utf-8")
    _check_tests(tests, name)
    lines, classes = fix_size(v1_source, ref_source)
    if (small := check_fix_size(names, style, lines, classes)) is not None:
        raise HealBuildError(f"{name}: {small}")
    if (lines, classes) != (heal["fix_lines"], heal["fix_classes"]):
        raise HealBuildError(f"{name}: fix size {(lines, classes)} differs from the recorded {(heal['fix_lines'], heal['fix_classes'])}")
    with tempfile.TemporaryDirectory(prefix="h2c_heal3_") as tmp:
        tmp = Path(tmp)
        v1_dir = tmp / "v1_task"
        v1_dir.mkdir()
        _write_json(v1_dir / "engine.json", heal["v1_spec"])
        (v1_dir / "tests.json").write_text((task_dir / "tests.json").read_text(encoding="utf-8"), encoding="utf-8")
        _write_json(v1_dir / "task.json", {**task, "config": heal["v1_spec"]["config"], "credentials": {"recording": task["credentials"]["recording"], "test": heal["v1_credentials"]}})
        v1_on_v1 = _grade_copy(v1_dir, v1_source, tmp)["pass_rate"]
        v1_on_v2 = _grade_copy(task_dir, v1_source, tmp)["pass_rate"]
        ref = _grade_copy(task_dir, ref_source, tmp)
        flawed = {n: _grade_copy(task_dir, generate_source(style, spec, (CHANGE_FLAW[n],)), tmp) for n in names}
    if v1_on_v1 != 1.0:
        raise HealBuildError(f"{name}: client_v1.py scored {v1_on_v1} on v1, expected 1.0")
    if v1_on_v2 >= 1.0:
        raise HealBuildError(f"{name}: client_v1.py still scores 1.0 on v2; the release does not break it")
    if ref["pass_rate"] != 1.0 or ref["invented_endpoint_calls"]:
        raise HealBuildError(f"{name}: the v2 reference client scored {ref['pass_rate']} {ref['failure_counts']}")
    for change, grade in flawed.items():
        if grade["pass_rate"] >= 1.0:
            raise HealBuildError(f"{name}: the v2 reference client without {change} still scores 1.0; the change is not needed")
    if leaks := find_leaks(task_dir, task):
        raise HealBuildError(f"{name}: hidden data in visible files: {leaks}")
    expected = {"goal.md", "client_v1.py", *EVIDENCE_FILES[heal["evidence"]], *([CHANGELOG_NAME] if heal["changelog"] != "none" else [])}
    if set(visible_files(task_dir)) != expected:
        raise HealBuildError(f"{name}: visible files {visible_files(task_dir)}, expected {sorted(expected)}")
    har_path = task_dir / "session.har"
    if har_path.is_file() and (missing := recording_misses(spec, names, json.loads(har_path.read_text(encoding="utf-8")))):
        raise HealBuildError(f"{name}: the recording does not show {missing}")
    _check_changelog(task)
    if heal["error_detail"] == "terse" and (words := find_fix_wordings(task_dir, task)):
        raise HealBuildError(f"{name}: fix wording in visible files of a terse task: {words}")
    needs = [sorted(n for n in names if i in _failed_indices(flawed[n])) for i in range(len(tests))]
    return {
        "v1_on_v1": v1_on_v1, "v1_on_v2": v1_on_v2, "ref_on_v2": ref["pass_rate"],
        "necessity": {n: g["pass_rate"] for n, g in flawed.items()}, "needs": needs,
    }
