"""Case 3 task builder: row -> repair task directory, plus the from-scratch comparison task of the same API.

Task dir: `goal.md`, `client_v1.py`, `failure.txt`, `session.har` (visible; `failure.txt` is absent for `evidence="recording_only"`
and `session.har` for `"failure_only"`); `task.json` (tier "B"), `engine.json` (v2 spec),
`tests.json`, `ref_client.py` (v2 reference client) (hidden). Grading is `grade.grade(task_dir, client)`, unchanged.
`row["mutations"]` lists 1 or 2 mutations (the old single `row["mutation"]` is still accepted)."""

from __future__ import annotations

import json
import random
import tempfile
from pathlib import Path

from har2client import grade as grading
from har2client.env.sources import canon
from har2client.env.sources.browser import render_har
from har2client.heal import failure, identify, mutations, session
from har2client.heal.select import SEED_SPACE, scratch_id_of, task_id_of
from har2client.tierb import domains
from har2client.tierb.engine import ERROR_DETAILS
from har2client.tierb.build import build_tierb_task, features, render_goal
from har2client.tierb.refclient import generate_client_source
from har2client.tierb.spec import OPS, make_credentials, make_spec, method_name
from har2client.tierb.testgen import make_tests

HEAL_NOISE = 0.3
TEST_SEED_TRIES = 60
VISIBLE_FILES = ("goal.md", "client_v1.py", "failure.txt", "session.har")
CHANGELOG_FILE = "CHANGELOG.md"  # v3 tasks only (heal.hard); not in VISIBLE_FILES, which lists the files of the older tasks
EVIDENCE_FILES = {  # the evidence files each level shows, besides goal.md and client_v1.py
    "both": ("failure.txt", "session.har"), "failure_only": ("failure.txt",), "recording_only": ("session.har",),
}
# what the verbose engine says in its error messages: the wording of the fix (header name, If-Match, log in again, ...)
FIX_WORDINGS = (
    "is required", "If-Match header is required", "GET the item for its ETag", "the item changed; GET it again", "log in again",
    "the session expired", "missing or invalid API key", "missing or invalid CSRF token", "not logged in", "wrong username or password",
)


class HealBuildError(RuntimeError):
    """The built task failed its own validity gate."""


def render_heal_goal(spec: dict, evidence: str = "both") -> str:
    """Repair instructions, then the constructor and method sections of the case 1 goal (same text), then the rules.
    The file list names only the evidence files the task has; `both` gives the text of the first task set."""
    base = render_goal(spec)
    shared = "## Constructor" + base.split("## Constructor", 1)[1].split("## Rules", 1)[0]
    if "session.har" not in EVIDENCE_FILES[evidence]:  # no recording in this task: the case 1 text must not point at one
        shared = shared.replace("In the recording it looks like", "In the failure output it looks like")
    files = ["- `client_v1.py`: the client for the old version of the API."]
    if "failure.txt" in EVIDENCE_FILES[evidence]:
        files.append("""- `failure.txt`: what happened when `client_v1.py` ran against a practice server of the new version. It shows the
  first call that failed; other calls may fail too.""")
    if "session.har" in EVIDENCE_FILES[evidence]:
        files.append("""- `session.har`: a short browser recording of a user session against the new version. It holds real requests and
  responses for the new version, mixed with unrelated traffic.""")
    file_list = "\n".join(files)
    return f"""# {spec['title']}: repair the client

The API behind this client has changed. `client_v1.py` is a correct client for the old version of the API.
Against the new version it fails. Write `client.py`: the repaired client, one Python file that defines `class Client`
and works with the new version.

## Files

{file_list}

{shared}## Rules

- Keep the constructor, the method names and the arguments as `client_v1.py` has them.
- Use only `httpx`, `requests` and the standard library.
- Do not hard-code the host or any credential. Use `base_url`, `username` and `password`.
- Add no methods for endpoints that are not listed.
- Tests run against a fresh server of the new version with new data and new credentials.
- `client.py` must import everything it uses (for example `from typing import Any` for the return annotations).
"""


def _write_json(path: Path, data: object) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _item_ids(domain: str, seed: int, n_items: int | None = None) -> set[str]:
    return {i["id"] for i in domains.initial_items(domain, seed, n_items)}


def _pick_test_seed(domain: str, rng: random.Random, avoid_seeds: set[int], avoid_ids: set[str], visible_text: str) -> int:
    """A seed whose start data shares no id with the recording, the practice server or any visible text."""
    for _ in range(TEST_SEED_TRIES):
        seed = rng.randrange(SEED_SPACE)
        ids = _item_ids(domain, seed)
        if seed not in avoid_seeds and not ids & avoid_ids and not any(i in visible_text for i in ids):
            return seed
    raise HealBuildError("no test seed without ids from the recording or the practice server")


def find_leaks(task_dir: Path, task: dict) -> list[str]:
    """Hidden test data that shows in a visible file: test credentials or ids of the test start data."""
    secrets = [*task["credentials"]["test"].values(), *_item_ids(task["domain"], task["seeds"]["test"])]
    leaks = []
    for name in visible_files(task_dir):
        text = (task_dir / name).read_text(encoding="utf-8")
        leaks += [f"{name}: {s}" for s in secrets if s in text]
    return leaks


def visible_files(task_dir: Path) -> list[str]:
    """The visible files a task dir has (the evidence files that the task's evidence level left out are absent; a v3 task may
    also have a `CHANGELOG.md`)."""
    return [n for n in (*VISIBLE_FILES, CHANGELOG_FILE) if (Path(task_dir) / n).is_file()]


def _error_texts(task_dir: Path) -> list[tuple[str, str]]:
    """(file, text) of every error response (status 400 and up) in the visible files."""
    out = []
    har = task_dir / "session.har"
    if har.is_file():
        for entry in json.loads(har.read_text(encoding="utf-8"))["log"]["entries"]:
            if entry["response"]["status"] >= 400:
                out.append(("session.har", entry["response"]["content"].get("text", "")))
    report = task_dir / "failure.txt"
    if report.is_file() and "Its response:\n  HTTP " in (text := report.read_text(encoding="utf-8")):
        response = text.split("Its response:\n", 1)[1]
        if int(response.split()[1]) >= 400:
            out.append(("failure.txt", response))
    return out


def find_fix_wordings(task_dir: Path, task: dict) -> list[str]:
    """Terse tasks only: wording of the fix in a visible file. The verbose error messages must not show anywhere, and the
    hidden header name must not show in an error response (it may show in the recording's requests: that is evidence)."""
    task_dir = Path(task_dir)
    spec = json.loads((task_dir / "engine.json").read_text(encoding="utf-8"))
    found = []
    for name in visible_files(task_dir):
        text = (task_dir / name).read_text(encoding="utf-8")
        found += [f"{name}: {w!r}" for w in FIX_WORDINGS if w in text]
    if spec["hidden_header"]:
        header = spec["hidden_header"]["name"].lower()
        found += [f"{name} (error response): {header}" for name, text in _error_texts(task_dir) if header in text.lower()]
    found += [f"{name} (error response): message key" for name, text in _error_texts(task_dir) if '"message"' in text]
    return found


def _grade_copy(task_dir: Path, client_source: str, tmp: Path) -> dict:
    """Grade source text; the client file lives outside the task dir so no __pycache__ appears there."""
    path = tmp / "candidate.py"
    path.write_text(client_source, encoding="utf-8")
    return grading.grade(task_dir, path)


def verify_heal_task(task_dir: str | Path) -> dict:
    """Re-run the validity gates from the files of a built task. Raises HealBuildError on the first violation.
    Returns {v1_on_v1, v1_on_v2, ref_on_v2}."""
    task_dir = Path(task_dir)
    task = json.loads((task_dir / "task.json").read_text(encoding="utf-8"))
    v1_spec = task["heal"]["v1_spec"]
    v1_source = (task_dir / "client_v1.py").read_text(encoding="utf-8")
    with tempfile.TemporaryDirectory(prefix="h2c_heal_") as tmp:
        tmp = Path(tmp)
        v1_dir = tmp / "v1_task"
        v1_dir.mkdir()
        _write_json(v1_dir / "engine.json", v1_spec)
        (v1_dir / "tests.json").write_text((task_dir / "tests.json").read_text(encoding="utf-8"), encoding="utf-8")
        v1_creds = task["heal"]["v1_credentials"]
        _write_json(v1_dir / "task.json", {**task, "config": v1_spec["config"], "credentials": {"recording": task["credentials"]["recording"], "test": v1_creds}})
        v1_on_v1 = _grade_copy(v1_dir, v1_source, tmp)["pass_rate"]
        v1_on_v2 = _grade_copy(task_dir, v1_source, tmp)["pass_rate"]
        ref = _grade_copy(task_dir, (task_dir / "ref_client.py").read_text(encoding="utf-8"), tmp)
    name = task["task_id"]
    if v1_on_v1 != 1.0:
        raise HealBuildError(f"{name}: client_v1.py scored {v1_on_v1} on v1, expected 1.0")
    if v1_on_v2 >= 1.0:
        raise HealBuildError(f"{name}: client_v1.py still scores 1.0 on v2; the change does not break it")
    if ref["pass_rate"] != 1.0 or ref["invented_endpoint_calls"]:
        raise HealBuildError(f"{name}: the v2 reference client scored {ref['pass_rate']} {ref['failure_counts']}")
    if leaks := find_leaks(task_dir, task):
        raise HealBuildError(f"{name}: hidden data in visible files: {leaks}")
    heal = task["heal"]
    expected = {"goal.md", "client_v1.py", *EVIDENCE_FILES[heal.get("evidence", "both")]}
    if set(visible_files(task_dir)) != expected:
        raise HealBuildError(f"{name}: visible files {visible_files(task_dir)}, expected {sorted(expected)}")
    if heal.get("error_detail", "verbose") == "terse" and (words := find_fix_wordings(task_dir, task)):
        raise HealBuildError(f"{name}: fix wording in visible files of a terse task: {words}")
    return {"v1_on_v1": v1_on_v1, "v1_on_v2": v1_on_v2, "ref_on_v2": ref["pass_rate"]}


def set_of(names: tuple[str, ...], error_detail: str, evidence: str) -> str:
    """The task set a factor combination belongs to (DESIGN-CASE3.md)."""
    if len(names) > 1:
        return "compound"
    return "terse" if error_detail == "terse" else ("core" if evidence == "both" else "evidence")


def build_heal_task(
    row: dict, out_dir: str | Path, *, build_seed: int = 0, noise: float = HEAL_NOISE, error_detail: str = "verbose",
    evidence: str = "both", task_id: str | None = None,
) -> Path:
    """Build `out_dir/<task_id>/` from one row {index, mutations (or the old single mutation), domain, config (v2), seed};
    run the validity gates. `error_detail` is the engine's error option (the practice server, the recording and the grading
    server use it); `evidence` picks which of `failure.txt` and `session.har` the task shows. Everything else (credentials,
    seeds, data, hidden tests) depends on the row and `build_seed` only, so the variants of one row are paired."""
    names = mutations.names_of(row.get("mutations") or row["mutation"])
    if error_detail not in ERROR_DETAILS:
        raise ValueError(f"error_detail must be one of {ERROR_DETAILS}, got {error_detail!r}")
    if evidence not in identify.EVIDENCE_LEVELS:
        raise ValueError(f"evidence must be one of {identify.EVIDENCE_LEVELS}, got {evidence!r}")
    label = "+".join(names)
    spec = make_spec(row["domain"], row["config"], row["seed"])
    if error_detail != "verbose":
        spec["error_detail"] = error_detail  # engine.json of a verbose task stays as it always was
    v1_spec = mutations.derive_v1_spec(spec, names, row["seed"])
    flaws = mutations.v1_flaws(v1_spec)
    client_v1 = generate_client_source(v1_spec, flaws)
    rng = random.Random(f"heal:{build_seed}:{row['seed']}")
    recording, practice, test = (make_credentials(spec, rng) for _ in range(3))
    while len({recording["password"], practice["password"], test["password"]}) < 3 or len({recording["username"], practice["username"], test["username"]}) < 3:
        recording, practice, test = (make_credentials(spec, rng) for _ in range(3))
    v1_test = make_credentials(v1_spec, rng)
    rec_seed, practice_seed = rng.randrange(SEED_SPACE), rng.randrange(SEED_SPACE)

    recorded, summary = session.record_session(spec, recording, rec_seed, rng)
    canon_session = canon.from_har(recorded, base_path=spec["base_path"], noise=noise, seed=f"heal:{build_seed}:{row['seed']}")
    har = render_har(canon_session, rng)
    har_text = json.dumps(har)

    with tempfile.TemporaryDirectory(prefix="h2c_heal_build_") as tmp:
        v1_path = Path(tmp) / "client_v1.py"
        v1_path.write_text(client_v1, encoding="utf-8")
        # tests need a test seed, the failure report needs the practice tests: both come from the same test generator
        practice_tests = make_tests(spec, practice_seed)
        try:
            report = failure.capture_failure(spec, v1_path, practice_seed, practice, practice_tests)
        except failure.NoFailure as exc:
            raise HealBuildError(f"{label} on {row['domain']}: {exc}") from exc
    avoid_ids = _item_ids(spec["domain"], rec_seed, summary["items"]) | _item_ids(spec["domain"], practice_seed)
    test_seed = _pick_test_seed(spec["domain"], rng, {rec_seed, practice_seed}, avoid_ids, har_text + report["text"])
    tests = make_tests(spec, test_seed)

    task_id = task_id or task_id_of(row)
    task_dir = Path(out_dir) / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "goal.md").write_text(render_heal_goal(spec, evidence), encoding="utf-8")
    (task_dir / "client_v1.py").write_text(client_v1, encoding="utf-8")
    if "failure.txt" in EVIDENCE_FILES[evidence]:
        (task_dir / "failure.txt").write_text(report["text"], encoding="utf-8")
    if "session.har" in EVIDENCE_FILES[evidence]:
        (task_dir / "session.har").write_text(canon.dumps_har(har, canon_session.compact), encoding="utf-8")
    _write_json(task_dir / "engine.json", spec)
    _write_json(task_dir / "tests.json", tests)
    (task_dir / "ref_client.py").write_text(generate_client_source(spec), encoding="utf-8")
    _write_json(
        task_dir / "task.json",
        {
            "task_id": task_id,
            "tier": "B",
            "api_id": f"heal:{label}:{row['index']:02d}",
            "title": spec["title"],
            "server_url": spec["server_url"],
            "domain": spec["domain"],
            "config": spec["config"],
            "goal_ops": [method_name(spec, op) for op in OPS],
            "seeds": {"recording": rec_seed, "test": test_seed, "spec": row["seed"]},
            "build_seed": build_seed,
            "credentials": {"recording": recording, "test": test},
            "noise_ratio": noise,
            "session_calls": summary["requests"],
            "har_entries": len(har["log"]["entries"]),
            "recorded_items": summary["listed"],
            "features": [*features(spec), "case:heal", *(f"mutation:{n}" for n in names)],
            "heal": {
                "mutation": label,
                "base_config": v1_spec["config"],
                "v1_spec": v1_spec,
                "v1_flaws": list(flaws),
                "v1_credentials": v1_test,
                "practice": {"seed": practice_seed, "credentials": practice},
                "failure": {k: report[k] for k in ("index", "reason", "detail")},
                "token_expiries_in_recording": summary["expired"],
                "set": row.get("set") or set_of(names, error_detail, evidence),
                "mutations": list(names),
                "error_detail": error_detail,
                "evidence": evidence,
                "identifiable": identify.identifiable(names, evidence),
            },
        },
    )
    task = json.loads((task_dir / "task.json").read_text(encoding="utf-8"))
    scores = verify_heal_task(task_dir)
    task["heal"]["gate"] = scores
    _write_json(task_dir / "task.json", task)
    return task_dir


def build_scratch_task(row: dict, out_dir: str | Path, *, build_seed: int = 0, negatives: bool = True, noise: float = HEAL_NOISE) -> Path:
    """The case 1 (build from scratch) Tier B task for the v2 API of `row`: same domain, config and spec seed, so the same
    API; its own recording, credentials and test data. The reference client must score 1.0."""
    tier_b_row = {"index": row["index"], "domain": row["domain"], "config": row["config"], "seed": row["seed"]}
    task_dir = build_tierb_task(tier_b_row, out_dir, noise=noise, build_seed=build_seed, negatives=negatives, task_id=scratch_id_of(row))
    if json.loads((task_dir / "engine.json").read_text(encoding="utf-8")) != make_spec(row["domain"], row["config"], row["seed"]):
        raise HealBuildError(f"{task_dir.name}: the scratch task does not have the v2 spec")
    return task_dir
