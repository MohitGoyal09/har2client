import csv
import importlib.util
import json
import random
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "analyze_tierb.py"
DOMAINS = ["bookings", "invoices", "samples", "shipments", "tickets"]
AUTH = ["apikey_header", "bearer_login", "cookie_csrf"]
PAGING = ["none", "offset", "cursor", "link_header"]

TRUE_LOW = {
    "intercept": 70.0, "auth=bearer_login": 5.0, "auth=cookie_csrf": -8.0, "pagination=offset": -10.0,
    "pagination=cursor": 12.0, "pagination=link_header": -20.0, "token_expiry=on": -15.0, "etag_update=on": 6.0,
    "hidden_header=on": -30.0, "envelope=on": 4.0, "domain=invoices": 3.0, "domain=samples": -6.0,
    "domain=shipments": 9.0, "domain=tickets": 0.0,
}
EFFORT_SHIFT = {"effort=high": 12.0, "effort=high x hidden_header=on": 18.0, "effort=high x pagination=offset": -7.0}


@pytest.fixture(scope="module")
def mod():
    spec = importlib.util.spec_from_file_location("analyze_tierb", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses look the module up here
    spec.loader.exec_module(module)
    return module


def synthetic(mod, n=150, noise=0.01, runs=1, seed=3):
    """obs and feats where pass rate (fraction) follows TRUE_LOW at low and TRUE_LOW + shifts at high."""
    rng = random.Random(seed)
    feats, obs = {}, {}
    for i in range(n):
        config = {"auth": rng.choice(AUTH), "pagination": rng.choice(PAGING)}
        config.update({k: rng.choice(["on", "off"]) for k in mod.BINARY})
        feats[f"t{i:03d}"] = {"domain": DOMAINS[i % len(DOMAINS)], "config": config}
    for task, f in feats.items():
        names, x = mod.feature_vector(f, DOMAINS)
        row = dict(zip(names, x))
        low = TRUE_LOW["intercept"] + sum(TRUE_LOW[k] * v for k, v in row.items())
        high = low + EFFORT_SHIFT["effort=high"] + EFFORT_SHIFT["effort=high x hidden_header=on"] * row["hidden_header=on"] \
            + EFFORT_SHIFT["effort=high x pagination=offset"] * row["pagination=offset"]
        obs[task] = {e: [mod.Run(v / 100 + rng.gauss(0, noise), 1000.0 + 2000 * (e == "high"), 5000.0) for _ in range(runs)]
                     for e, v in (("low", low), ("high", high))}
    return obs, feats


def coef(a, model, effort, term):
    return next(c for c in a.coefs if (c["model"], c["effort"], c["term"]) == (model, effort, term))


def test_per_effort_regression_recovers_known_coefficients(mod):
    obs, feats = synthetic(mod)
    a = mod.analyze(obs, feats, n_boot=100, seed=1)
    for term, truth in TRUE_LOW.items():
        assert coef(a, "per_effort", "low", term)["estimate_pp"] == pytest.approx(truth, abs=1.5)
    assert coef(a, "per_effort", "high", "hidden_header=on")["estimate_pp"] == pytest.approx(-12.0, abs=1.5)


def test_pooled_model_recovers_effort_term_and_interactions(mod):
    obs, feats = synthetic(mod)
    a = mod.analyze(obs, feats, n_boot=100, seed=1)
    for term, truth in EFFORT_SHIFT.items():
        assert coef(a, "pooled", "all", term)["estimate_pp"] == pytest.approx(truth, abs=1.5)
    assert coef(a, "pooled", "all", "hidden_header=on")["estimate_pp"] == pytest.approx(-30.0, abs=1.5)
    assert coef(a, "pooled", "all", "effort=high x etag_update=on")["estimate_pp"] == pytest.approx(0.0, abs=1.5)


def test_bootstrap_intervals_cover_the_truth_and_bracket_the_estimate(mod):
    obs, feats = synthetic(mod, noise=0.05)
    a = mod.analyze(obs, feats, n_boot=300, seed=1)
    for term, truth in TRUE_LOW.items():
        c = coef(a, "per_effort", "low", term)
        assert c["ci_low_pp"] <= c["estimate_pp"] <= c["ci_high_pp"] and c["ci_low_pp"] - 2 <= truth <= c["ci_high_pp"] + 2


def test_bootstrap_is_deterministic_for_a_fixed_seed(mod):
    obs, feats = synthetic(mod, n=60, noise=0.05, runs=2)
    a, b, c = (mod.analyze(obs, feats, n_boot=80, seed=s) for s in (7, 7, 8))
    assert a.coefs == b.coefs and a.contrast == b.contrast and a.passk == b.passk
    assert a.coefs != c.coefs
    assert [x["estimate_pp"] for x in a.coefs] == [x["estimate_pp"] for x in c.coefs]  # point estimates do not depend on the seed


def test_constant_switch_is_dropped_not_crashing(mod):
    obs, feats = synthetic(mod, n=60)
    for f in feats.values():
        f["config"]["envelope"] = "off"
    a = mod.analyze(obs, feats, n_boot=20, seed=1)
    assert not any("envelope" in c["term"] for c in a.coefs)
    assert any("envelope=on" in n for n in a.notes)


def test_effort_contrast_and_tokens_per_point(mod):
    obs, feats = synthetic(mod, n=60, noise=0.0)
    a = mod.analyze(obs, feats, n_boot=50, seed=1)
    gain, extra = a.contrast["gain_pp"][0], a.contrast["extra_out_tokens"][0]
    assert extra == pytest.approx(2000.0)
    assert a.contrast["out_tokens_per_pp"][0] == pytest.approx(extra / gain)
    lo, hi = a.contrast["gain_pp"][1:]
    assert lo <= gain <= hi


def test_no_gain_gives_undefined_tokens_per_point(mod):
    obs, feats = synthetic(mod, n=40, noise=0.0)
    for task in obs:
        obs[task]["high"] = obs[task]["low"]
    a = mod.analyze(obs, feats, n_boot=20, seed=1)
    assert a.contrast["out_tokens_per_pp"][0] is None


def _fixed_obs(mod, pattern):
    """task -> {low: runs}; pattern: list of per-task pass_rate lists."""
    obs = {f"t{i}": {"low": [mod.Run(r, 1.0, 1.0) for r in rates]} for i, rates in enumerate(pattern)}
    feats = {t: {"domain": DOMAINS[i % 2], "config": {"auth": AUTH[i % 3], "pagination": PAGING[i % 4], "token_expiry": "off",
                                                      "etag_update": "on" if i % 2 else "off", "hidden_header": "off", "envelope": "on" if i % 5 else "off"}}
             for i, t in enumerate(obs)}
    return obs, feats


def test_pass_hat_k_counts_tasks_where_every_run_fully_passes(mod):
    obs, feats = _fixed_obs(mod, [[1, 1, 1], [1, 1, 0.5], [1, 0, 1], [0, 0, 0]])
    s = mod.analyze(obs, feats, n_boot=30, seed=1).passk["low"]
    assert s["k"] == 3 and s["n_tasks"] == 4
    assert s["pass_hat_k"][0] == pytest.approx(25.0)
    assert s["full_pass_at_1"][0] == pytest.approx(100 * 7 / 12)
    assert s["mean_pass_at_1"][0] == pytest.approx(100 * 7.5 / 12)
    assert s["gap"][0] == pytest.approx(25.0 - 100 * 7 / 12)


def test_single_run_has_no_pass_hat_k(mod):
    obs, feats = _fixed_obs(mod, [[1], [0], [1], [1]])
    assert mod.analyze(obs, feats, n_boot=10, seed=1).passk == {}


def arms(mod, n=40, hint=10.0, evidence=50.0):
    base = {f"t{i}": {"high": [mod.Run(0.4, 1, 1), mod.Run(0.2, 1, 1)]} for i in range(n)}
    plus = lambda d: {t: {"high": [mod.Run(r.pass_rate + d / 100, 1, 1) for r in v["high"]]} for t, v in base.items()}  # noqa: E731
    return base, plus(hint), plus(evidence)


def test_header_arms_recover_known_paired_differences(mod):
    h = mod.header_arms(*arms(mod), n_boot=100, seed=1)
    assert h["efforts"] == ["high"] and len(h["tasks"]) == 40
    assert h["stats"]["hint-base|high"][0] == pytest.approx(10.0)
    assert h["stats"]["evidence-base|high"][0] == pytest.approx(50.0)
    assert h["stats"]["evidence-hint|all"][0] == pytest.approx(40.0)
    lo, est, hi = h["stats"]["hint-base|high"][1], h["stats"]["hint-base|high"][0], h["stats"]["hint-base|high"][2]
    assert lo <= est <= hi


def test_header_arms_ci_reflects_task_variation_and_is_deterministic(mod):
    rng = random.Random(2)
    base, hint, evidence = arms(mod)
    for t in hint:
        hint[t]["high"] = [mod.Run(min(1.0, max(0.0, r.pass_rate + rng.choice([-0.3, 0.5]))), 1, 1) for r in base[t]["high"]]
    a = mod.header_arms(base, hint, evidence, n_boot=200, seed=5)
    b = mod.header_arms(base, hint, evidence, n_boot=200, seed=5)
    assert a == b
    est, lo, hi = a["stats"]["hint-base|high"]
    assert hi - lo > 5 and lo <= est <= hi


def write_csv(path, rows):
    fields = ["task", "effort", "har_mode", "run", "status", "pass_rate", "prompt_tokens", "completion_tokens"]
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow({"har_mode": "raw", "run": 1, "status": "ok", "prompt_tokens": 100, "completion_tokens": 50, **r})


def test_load_runs_strips_arm_suffix_and_refuses_mixed_har_modes(mod, tmp_path):
    p = tmp_path / "a.csv"
    write_csv(p, [{"task": "x__hint", "effort": "high", "pass_rate": 1.0}, {"task": "x__evidence", "effort": "high", "pass_rate": 0.0}])
    assert list(mod.load_runs([p], strip=True)) == ["x"] and len(mod.load_runs([p], strip=True)["x"]["high"]) == 2
    assert set(mod.load_runs([p])) == {"x__hint", "x__evidence"}
    write_csv(p, [{"task": "x", "effort": "high", "pass_rate": 1.0}, {"task": "x", "effort": "high", "har_mode": "compact", "pass_rate": 1.0}])
    with pytest.raises(SystemExit):
        mod.load_runs([p])
    assert len(mod.load_runs([p], har_mode="raw")["x"]["high"]) == 1


def test_non_ok_status_counts_as_a_failed_run(mod, tmp_path):
    p = tmp_path / "a.csv"
    write_csv(p, [{"task": "x", "effort": "low", "pass_rate": "", "status": "error"}])
    assert mod.load_runs([p])["x"]["low"][0].pass_rate == 0.0


def test_api_error_rows_are_excluded_and_counted_per_effort(mod, tmp_path):
    p = tmp_path / "a.csv"
    write_csv(p, [
        {"task": "x", "effort": "low", "pass_rate": 1.0},
        {"task": "x", "effort": "low", "pass_rate": 0.0, "status": "api_error"},
        {"task": "x", "effort": "high", "pass_rate": 0.0, "status": "no_code"},
        {"task": "y", "effort": "high", "pass_rate": 0.0, "status": "api_error"},
    ])
    excluded = {}
    obs = mod.load_runs([p], excluded=excluded)
    assert [r.pass_rate for r in obs["x"]["low"]] == [1.0]  # the failed call is not a 0
    assert [r.pass_rate for r in obs["x"]["high"]] == [0.0]  # no_code still is
    assert "y" not in obs
    assert excluded == {"low": 1, "high": 1}


def test_summary_reports_api_error_rows_only_when_there_are_some(mod):
    obs, feats = synthetic(mod, n=30, noise=0.05)
    a = mod.analyze(obs, feats, n_boot=20, seed=1)
    assert "api_error rows excluded" not in mod.render_summary(a, ["r.csv"], None, 20, 1)
    a.api_errors = {"low": 2, "high": 1}
    text = mod.render_summary(a, ["r.csv"], None, 20, 1)
    assert "api_error rows excluded: low 2, high 1" in text


def test_cli_writes_summary_and_coefficients(mod, tmp_path):
    obs, feats = synthetic(mod, n=30, noise=0.05)
    tasks = tmp_path / "tasks"
    rows = []
    for t, f in feats.items():
        (tasks / t).mkdir(parents=True)
        (tasks / t / "task.json").write_text(json.dumps({"domain": f["domain"], "config": f["config"]}))
        rows += [{"task": t, "effort": e, "pass_rate": round(min(1, max(0, runs[0].pass_rate)), 3)} for e, runs in obs[t].items()]
    results = tmp_path / "r.csv"
    write_csv(results, rows)
    out = tmp_path / "out"
    assert mod.main(["--runs", str(results), "--tasks", str(tasks), "--out", str(out), "--n-boot", "40"]) == 0
    text = (out / "summary.md").read_text()
    assert "Effort contrast" in text and "hidden_header=on" in text and "95%" in text
    lines = list(csv.DictReader(open(out / "coefficients.csv")))
    assert {r["model"] for r in lines} == {"per_effort", "pooled"} and {"estimate_pp", "ci_low_pp", "ci_high_pp"} <= set(lines[0])


def test_cli_header_arms_mode(mod, tmp_path):
    tasks = tmp_path / "tasks"
    (tasks / "t0").mkdir(parents=True)
    (tasks / "t0").joinpath("task.json").write_text(json.dumps({"domain": "tickets", "config": {}}))
    paths = []
    for name, suffix, rate in (("base", "", 0.0), ("hint", "__hint", 0.5), ("evid", "__evidence", 1.0)):
        p = tmp_path / f"{name}.csv"
        write_csv(p, [{"task": f"t{i}{suffix}", "effort": "high", "pass_rate": rate} for i in range(5)])
        paths.append(str(p))
    out = tmp_path / "out"
    assert mod.main(["--tasks", str(tasks), "--out", str(out), "--n-boot", "30", "--header-arms", *paths]) == 0
    text = (out / "summary.md").read_text()
    assert "Header ablation" in text and "+50.0" in text and "+100.0" in text


def test_header_arms_report_tasks_missing_from_an_arm_and_fail_clearly_on_no_overlap(mod):
    base, hint, evidence = arms(mod, n=6)
    del evidence["t5"]
    h = mod.header_arms(base, hint, evidence, n_boot=20, seed=1)
    assert len(h["tasks"]) == 5 and h["dropped"] == ["t5"]
    assert "t5" in "\n".join(mod._header_section(h))  # the summary says which tasks were left out
    with pytest.raises(SystemExit):
        mod.header_arms(base, {}, evidence, n_boot=20, seed=1)


def test_header_arms_flag_tasks_without_the_hidden_header(mod):
    base, hint, evidence = arms(mod, n=3)
    feats = {f"t{i}": {"domain": "tickets", "config": {"hidden_header": "on" if i else "off"}} for i in range(3)}
    h = mod.header_arms(base, hint, evidence, n_boot=20, seed=1, feats=feats)
    assert h["no_hidden_header"] == ["t0"]
    assert "t0" in "\n".join(mod._header_section(h))
