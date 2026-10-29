"""analyze_heal.py on synthetic scored data with planted effects (no model runs, no Beam calls)."""

import csv
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
import analyze_heal as ah  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "data" / "heal_suite.jsonl"
CONFIGS = ROOT / "data" / "heal_configs.jsonl"
EFFORTS = ("low", "medium", "high")
HEADER = ("add_hidden_header", "rename_hidden_header")
# planted: successes out of the 6 core bases of a mutation, per effort (header mutations are hardest)
CORE_OK = {"low": (1, 4), "medium": (2, 5), "high": (3, 6)}  # (header, other)
SCRATCH_OK = (1, 4)  # at high
TOKENS = {"low": 500, "medium": 1000, "high": 2000}
REGRESSION = {"low": 0.10, "medium": 0.05, "high": 0.05}
FIELDS = ["task", "effort", "run", "status", "pass_rate", "completion_tokens", "reasoning_tokens", "set", "mutations", "changes", "error_detail",
          "evidence", "identifiable", "regression_rate", "patch_lines", "patch_ast_nodes"]


def manifest_rows():
    return [json.loads(line) for line in MANIFEST.read_text().splitlines() if line.strip()]


def config_rows():
    return [json.loads(line) for line in CONFIGS.read_text().splitlines() if line.strip()]


def row(entry, effort, pass_rate, run=1, tokens=None, regression=0.05):
    return {
        "task": entry["task_id"], "effort": effort, "run": run, "status": "ok", "pass_rate": pass_rate,
        "completion_tokens": TOKENS[effort] if tokens is None else tokens, "reasoning_tokens": 100, "set": entry["set"],
        "mutations": "+".join(entry["mutations"]), "changes": entry["changes"], "error_detail": entry["error_detail"],
        "evidence": entry["evidence"], "identifiable": entry["identifiable"], "regression_rate": regression,
        "patch_lines": 8 if effort == "low" else 12, "patch_ast_nodes": 30,
    }


def core_pass(entry, effort, position):
    header_ok, other_ok = CORE_OK[effort]
    ok = header_ok if entry["mutations"][0] in HEADER else other_ok
    return 1.0 if position < ok else 0.0


def positions():
    """Core row -> position among the 6 core rows of its mutation."""
    seen, out = {}, {}
    for e in manifest_rows():
        if e["set"] == "core":
            out[e["index"]] = seen.get(e["mutations"][0], 0)
            seen[e["mutations"][0]] = out[e["index"]] + 1
    return out


def write_csv(path, rows, fields=FIELDS):
    with open(path, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def planted_world(tmp_path):
    """Scored CSV + scratch CSV with these effects: effort curve CORE_OK, repair easier than build at high, terse errors cost 50 points
    on raising mutations and nothing on silent ones, compound tasks never fully repaired, one-channel evidence tasks at 0.5."""
    pos = positions()
    scored, scratch = [], []
    for e in manifest_rows():
        if e["set"] == "core":
            for effort in EFFORTS:
                scored.append(row(e, effort, core_pass(e, effort, pos[e["index"]]), regression=REGRESSION[effort]))
        elif e["set"] == "terse":
            core = core_pass(e, "high", pos[e["base_index"]])
            scored.append(row(e, "high", 0.5 if e["mutations"][0] in ah.RAISING else core))
        elif e["set"] == "evidence":
            scored.append(row(e, "high", 0.5))
        else:
            scored.append(row(e, "high", 0.2))
    for c in config_rows():
        ok = SCRATCH_OK[0] if c["mutation"] in HEADER else SCRATCH_OK[1]
        scratch.append({"task": c["scratch_id"], "effort": "high", "run": 1, "status": "ok", "pass_rate": 1.0 if pos[c["index"]] < ok else 0.0,
                        "completion_tokens": 1500})
    write_csv(tmp_path / "scored.csv", scored)
    write_csv(tmp_path / "scratch.csv", scratch, ["task", "effort", "run", "status", "pass_rate", "completion_tokens"])
    return tmp_path / "scored.csv", tmp_path / "scratch.csv"


def run_main(tmp_path, scored, scratch=None, n_boot=300, seed=7, out="out"):
    args = ["--scored", str(scored), "--out", str(tmp_path / out), "--n-boot", str(n_boot), "--seed", str(seed)]
    if scratch:
        args += ["--scratch", str(scratch)]
    assert ah.main(args) == 0
    return tmp_path / out


def estimates(out):
    with open(out / "results.csv") as fh:
        return {r["key"]: (float(r["estimate"]) if r["estimate"] else None, r["ci_low"], r["ci_high"]) for r in csv.DictReader(fh)}


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("planted")
    scored, scratch = planted_world(tmp)
    out = run_main(tmp, scored, scratch)
    return out, estimates(out), json.loads((out / "hypotheses.json").read_text())


def test_effort_curve_recovers_the_planted_means(world):
    _, est, _ = world
    for effort, (header_ok, other_ok) in CORE_OK.items():
        assert est[f"core.pass|{effort}"][0] == pytest.approx((2 * header_ok + 6 * other_ok) / 48 * 100)
        assert est[f"core.tok|{effort}"][0] == TOKENS[effort]
        assert est[f"core.reg|{effort}"][0] == pytest.approx(REGRESSION[effort] * 100)
    assert est["core.lines|low"][0] == 8 and est["core.nodes|high"][0] == 30
    assert est["mut.pass|add_hidden_header|high"][0] == 50.0 and est["mut.pass|add_etag|low"][0] == pytest.approx(4 / 6 * 100)


def test_points_per_1k_tokens_between_adjacent_efforts(world):
    _, est, _ = world
    gain = (2 * 2 + 6 * 5) / 48 * 100 - (2 * 1 + 6 * 4) / 48 * 100
    assert est["adj.gain|low>medium"][0] == pytest.approx(gain)
    assert est["adj.dtok|low>medium"][0] == 500
    assert est["adj.per1k|low>medium"][0] == pytest.approx(gain / 0.5)
    assert est["adj.per1k|medium>high"][0] == pytest.approx(((3 * 2 + 6 * 6) - (2 * 2 + 6 * 5)) / 48 * 100 / 1.0)


def test_h12_repair_vs_scratch_paired_by_config(world):
    _, est, hyp = world
    repair, build = (2 * 3 + 6 * 6) / 48 * 100, (2 * 1 + 6 * 4) / 48 * 100
    assert est["h12.repair|high"][0] == pytest.approx(repair) and est["h12.build|high"][0] == pytest.approx(build)
    assert est["h12.diff|high"][0] == pytest.approx(repair - build)
    assert hyp["H12"]["verdict"] == "supported"


def test_h13_terse_vs_verbose_splits_raising_and_silent(world):
    _, est, hyp = world
    # verbose on the first 3 bases of each raising mutation: header 3/3, others 3/3 (all fully repaired at high) -> 100 vs 50
    assert est["h13.diff|raising|all|high"][0] == pytest.approx(50.0)
    assert est["h13.diff|silent|all|high"][0] == pytest.approx(0.0)
    assert hyp["H13"]["verdict"] == "supported"


def test_h14_is_not_tested_because_xhigh_was_dropped(world):
    out, est, hyp = world
    assert est["gain.low>high"][0] > 0 and not any("xhigh" in k for k in est)
    assert hyp["H14"]["verdict"] == "not tested"
    assert "xhigh dropped 2026-10-27" in hyp["H14"]["reason"]
    for name in ("summary.md", "hypotheses.json", "results.csv"):
        lines = (out / name).read_text().splitlines()
        assert [ln for ln in lines if "xhigh" in ln and "dropped" not in ln] == []


def test_h15_compound_below_the_product_of_singles(world):
    _, est, hyp = world
    assert est["h15.compound|all|high"][0] == 0.0
    assert est["h15.expected|all|high"][0] > 10
    assert est["h15.diff|all|high"][0] == pytest.approx(-est["h15.expected|all|high"][0])
    assert hyp["H15"]["verdict"] == "supported"


def test_evidence_arms_and_identifiable_split(world):
    _, est, _ = world
    assert est["ev.pass|failure_only|all|high"][0] == 50.0 and est["ev.pass|recording_only|all|high"][0] == 50.0
    assert est["set.pass|evidence|yes|high"][0] == 50.0
    assert est["set.pass|evidence|no|high"][0] == 50.0  # failure_only tasks that need the recording exist and are labelled


def test_summary_is_plain_language_with_verbatim_thresholds_and_cis(world):
    out, _, _ = world
    text = (out / "summary.md").read_text()
    for h in ("H12", "H13", "H14", "H15"):
        assert f"## {h}:" in text
        assert ah.PREREG[h]["supported"] in text and ah.PREREG[h]["rejected"] in text
    assert text.count("SUPPORTED") >= 3 and "NOT TESTED" in text and "95% CI" in text and "points per 1K extra tokens" in text
    assert "Results split by `identifiable`" in text and "[" in text


def test_ci_brackets_the_point_estimate_when_tasks_vary(world):
    _, est, _ = world
    point, lo, hi = est["core.pass|high"]
    assert float(lo) <= point <= float(hi) and float(lo) < float(hi)


def test_bootstrap_is_deterministic_for_a_seed(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("det")
    scored, scratch = planted_world(tmp)
    a = estimates(run_main(tmp, scored, scratch, seed=3, out="a"))
    b = estimates(run_main(tmp, scored, scratch, seed=3, out="b"))
    c = estimates(run_main(tmp, scored, scratch, seed=4, out="c"))
    assert a == b
    assert a["core.pass|high"][0] == c["core.pass|high"][0] and a["core.pass|high"][1:] != c["core.pass|high"][1:]
    assert (tmp / "a" / "summary.md").read_text() == (tmp / "b" / "summary.md").read_text()


def test_null_world_is_not_supported_for_every_hypothesis(tmp_path):
    """Same everywhere: no effort effect, repair = build, terse = verbose, compound = product."""
    rows, scratch = [], []
    for e in manifest_rows():
        for effort in (EFFORTS if e["set"] == "core" else ("high",)):
            rows.append(row(e, effort, 1.0))
    for c in config_rows():
        scratch.append({"task": c["scratch_id"], "effort": "high", "run": 1, "status": "ok", "pass_rate": 1.0, "completion_tokens": 1500})
    write_csv(tmp_path / "s.csv", rows)
    write_csv(tmp_path / "x.csv", scratch, ["task", "effort", "run", "status", "pass_rate", "completion_tokens"])
    hyp = json.loads((run_main(tmp_path, tmp_path / "s.csv", tmp_path / "x.csv") / "hypotheses.json").read_text())
    assert {h: v["verdict"] for h, v in hyp.items()} == {"H12": "not supported", "H13": "not supported", "H14": "not tested", "H15": "not supported"}
    assert all(v["rejected"] for h, v in hyp.items() if h != "H14")


def test_missing_data_is_not_yet_testable(tmp_path):
    rows = [row(e, "high", 1.0) for e in manifest_rows() if e["set"] == "core"]
    write_csv(tmp_path / "s.csv", rows)
    out = run_main(tmp_path, tmp_path / "s.csv")
    hyp = json.loads((out / "hypotheses.json").read_text())
    assert {h: v["verdict"] for h, v in hyp.items()} == {"H12": "not yet testable", "H13": "not yet testable", "H14": "not tested", "H15": "not yet testable"}
    assert "NOT YET TESTABLE" in (out / "summary.md").read_text()


def test_heal48_without_factor_columns_or_manifest_entries(tmp_path):
    """Old heal48 results (no factor columns) still load: ids give mutation and row; the manifest is optional."""
    rows = [{"task": c["task_id"], "effort": "high", "run": 1, "status": "ok", "pass_rate": 0.5, "completion_tokens": 10} for c in config_rows()]
    write_csv(tmp_path / "old.csv", rows, ["task", "effort", "run", "status", "pass_rate", "completion_tokens"])
    assert ah.main(["--scored", str(tmp_path / "old.csv"), "--out", str(tmp_path / "o"), "--manifest", str(tmp_path / "none.jsonl"), "--n-boot", "50"]) == 0
    est = estimates(tmp_path / "o")
    assert est["core.pass|high"][0] == 50.0 and est["mut.pass|add_etag|high"][0] == 50.0


def test_failed_runs_count_as_zero_and_runs_are_averaged(tmp_path):
    entries = [e for e in manifest_rows() if e["set"] == "core"][:4]
    rows = [row(e, "high", 1.0, run=1) for e in entries] + [row(e, "high", 0.0, run=2) for e in entries]
    rows[0]["status"] = "no_code"  # a failed run with a pass_rate on it counts as 0
    write_csv(tmp_path / "s.csv", rows)
    est = estimates(run_main(tmp_path, tmp_path / "s.csv", n_boot=50))
    assert est["core.pass|high"][0] == pytest.approx((0.0 + 0.5 * 3) / 4 * 100)
    assert est["core.full|high"][0] == pytest.approx(37.5)  # share of attempts with pass_rate 1.0: 3 of 4 tasks have one such run of two


def test_api_error_runs_are_left_out_not_counted_as_zero_and_are_reported(tmp_path):
    entries = [e for e in manifest_rows() if e["set"] == "core"][:4]
    rows = [row(e, "high", 1.0, run=1) for e in entries] + [row(e, "high", 0.0, run=2) for e in entries]
    rows[len(entries)]["status"] = "api_error"  # task 0, run 2: the model call failed
    write_csv(tmp_path / "s.csv", rows)
    out = run_main(tmp_path, tmp_path / "s.csv", n_boot=50)
    est = estimates(out)
    assert est["core.pass|high"][0] == pytest.approx((1.0 + 0.5 * 3) / 4 * 100)
    assert "api_error rows excluded: high 1" in (out / "summary.md").read_text()


def test_summary_has_no_api_error_line_when_there_are_none(tmp_path):
    entries = [e for e in manifest_rows() if e["set"] == "core"][:4]
    write_csv(tmp_path / "s.csv", [row(e, "high", 1.0) for e in entries])
    assert "api_error rows excluded" not in (run_main(tmp_path, tmp_path / "s.csv", n_boot=50) / "summary.md").read_text()


def _task(task_id, kind="repair", set_name="core", mutations=("add_etag",), cluster="row0"):
    return ah.Task(task_id, kind, set_name, mutations, "verbose", "both", True, cluster)


def test_a_cluster_drawn_twice_pairs_each_variant_with_its_own_draw():
    """Resampled runs differ per draw of a cluster: the base of draw 1 must not pair with the variant of draw 2."""
    base, other = _task("heal_00_x"), _task("scratch_00_x", "scratch", "scratch")

    def cell(p):
        return {"high": {"pass": p, "full": p, "reg": None, "lines": None, "nodes": None, "tok": None}}

    views = [ah.View(base, cell(1.0), 0), ah.View(other, cell(1.0), 0), ah.View(base, cell(0.0), 1), ah.View(other, cell(0.0), 1)]
    pairs = ah._pairs(views, ah.is_core, lambda t: t.kind == "scratch", "high")
    assert [(a.cells["high"]["pass"], b.cells["high"]["pass"]) for a, b in pairs] == [(1.0, 1.0), (0.0, 0.0)]
    assert ah._diff_pp(pairs, "high") == 0.0


def test_bootstrap_draw_gives_the_variants_of_a_cluster_one_slot():
    runs = lambda p: [ah.Run(p, None, None, None, None), ah.Run(1 - p, None, None, None, None)]  # noqa: E731
    core, twin = _task("heal_00_x"), _task("scratch_00_x", "scratch", "scratch")
    for t in (core, twin):
        t.runs["high"] = runs(1.0)
    sampler = ah.Sampler([core, twin])
    import random

    views = sampler.draw(random.Random(1))
    assert sorted(v.slot for v in views) == [0, 0]  # one cluster, one draw, both tasks in it


def test_zero_padded_and_plain_row_indexes_name_the_same_cluster():
    assert ah._row_index("heal_03_add_etag_samples") == ah._row_index("scratch_3_add_etag_samples") == "3"
    core = ah.make_task("heal_03_add_etag_samples", {}, {"heal_03_add_etag_samples": {"base_index": 3, "mutations": ["add_etag"]}}, {})
    twin = ah.make_task("scratch_03_add_etag_samples", {}, {}, {"scratch_03_add_etag_samples": 3})
    assert core.cluster == twin.cluster == "row3"
    fallback = ah.make_task("heal_03_add_etag_samples", {}, {}, {})
    assert fallback.cluster == "row3"
