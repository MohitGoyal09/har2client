"""Case 3 repair environment: determinism, reward rules, no hidden data in the observation, splits, TRL function, dataset."""

import json
import random
import sys
from pathlib import Path

import pytest

from har2client.env import repair_splits, repair_tasks
from har2client.env.repair_env import RepairEnv, grade_repair, make_repair_reward_fn, repair_reward_fn
from har2client.env.reward import GradePool, RewardConfig
from har2client.heal import build as heal_build
from har2client.heal.mutations import BY_NAME

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
import make_repair_dataset  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
EMPTY = "class Client:\n    def __init__(self, base_url, username, password):\n        pass\n"
HIDDEN_NAMES = ("tests.json", "engine.json", "ref_client.py", "task.json")
OBS_KEYS = {"task_id", "goal", "client_v1", "failure", "observation", "observation_format"}


@pytest.fixture(scope="module")
def cache(tmp_path_factory):
    return tmp_path_factory.mktemp("repair_cache")


@pytest.fixture(scope="module")
def pool():
    p = GradePool(0)
    yield p
    p.close()


def spec_at(level, seed=5, split="train"):
    return repair_splits.sample_spec(split, random.Random(f"t:{level}:{seed}"), level=level)


@pytest.fixture(scope="module")
def tasks_by_level(cache):
    """One built task per level R0-R3 (train split); the builder runs its own validity gates."""
    out = {}
    for level in range(4):
        spec = spec_at(level)
        out[level] = (spec, repair_tasks.ensure_task(spec, cache))
    return out


def task_json(task_dir):
    return json.loads((Path(task_dir) / "task.json").read_text())


def ref(task_dir):
    return (Path(task_dir) / "ref_client.py").read_text()


# ---------------------------------------------------------------- determinism


def test_env_sequence_and_rewards_are_deterministic(cache, pool):
    runs = []
    for _ in range(2):
        env = RepairEnv("train", "R0", seed=11, cache_dir=cache, pool=pool)
        obs = env.reset()
        out = env.step({"type": "submit", "code": env.reference_code()})
        runs.append((obs, out["reward"], {k: v for k, v in out["info"].items() if k != "grade_time_s"}))
    assert runs[0] == runs[1]
    other = RepairEnv("train", "R0", seed=12, cache_dir=cache, pool=pool).reset()
    assert other["task_id"] != runs[0][0]["task_id"]


def test_same_spec_reuses_the_cached_build(cache):
    spec = spec_at(0)
    assert repair_tasks.ensure_task(spec, cache) == repair_tasks.ensure_task(json.dumps(spec), cache)
    assert repair_tasks.spec_key(spec) == repair_tasks.spec_key(repair_tasks.normalize(spec))
    assert repair_tasks.spec_key(spec) != repair_tasks.spec_key({**spec, "build_seed": 1})


def test_step_protocol(cache, pool):
    env = RepairEnv("train", "R0", seed=1, cache_dir=cache, pool=pool)
    with pytest.raises(RuntimeError):
        env.step({"type": "submit", "code": EMPTY})  # before reset
    env.reset()
    with pytest.raises(ValueError):
        env.step({"type": "probe"})
    out = env.step({"type": "submit", "code": EMPTY})
    assert out["info"]["identifiable"] is True  # train never draws an unsolvable task
    with pytest.raises(RuntimeError):
        env.step({"type": "submit", "code": EMPTY})  # episode is over


# ---------------------------------------------------------------- reward and metrics


@pytest.mark.parametrize("level", range(4))
def test_reference_v2_client_scores_one_and_client_v1_less(tasks_by_level, pool, level):
    _, task_dir = tasks_by_level[level]
    good = grade_repair(task_dir, ref(task_dir), pool=pool)
    old = grade_repair(task_dir, (task_dir / "client_v1.py").read_text(), pool=pool)
    assert good.reward == 1.0 and good.info["regression_rate"] == 0.0 and good.info["patch_lines"] > 0
    assert old.reward < 1.0
    assert old.info["patch_lines"] == 0 and old.info["patch_ast_nodes"] == 0 and old.info["regression_rate"] == 0.0


def test_empty_client_regression_is_the_share_of_tests_v1_passed(tasks_by_level, pool):
    from har2client.env.repair_env import _BASELINES

    _, task_dir = tasks_by_level[0]
    result = grade_repair(task_dir, EMPTY, pool=pool)
    baseline = _BASELINES[str(task_dir)]
    assert result.reward == 0.0
    assert result.info["regression_rate"] == pytest.approx(sum(baseline) / len(baseline))


def test_regression_is_positive_when_a_patch_breaks_tests_v1_passed(cache, pool):
    """add_etag: client_v1 still passes the 6 tests without an update, so a broken client regresses on exactly those."""
    spec = repair_splits.sample_spec("train", random.Random(2), level=0, mutations=("add_etag",))
    task_dir = repair_tasks.ensure_task(spec, cache)
    v1 = (task_dir / "client_v1.py").read_text()
    assert grade_repair(task_dir, EMPTY, pool=pool).info["regression_rate"] == pytest.approx(6 / 7)
    assert grade_repair(task_dir, v1, pool=pool).info["regression_rate"] == 0.0
    result = grade_repair(task_dir, ref(task_dir), pool=pool)
    assert result.reward == 1.0 and result.info["regression_rate"] == 0.0


def test_regression_counts_tests_v1_passed_but_the_patch_fails(tasks_by_level, pool):
    """A client that breaks every call fails all tests: regression = share v1 passed. A v1 copy never regresses."""
    for _, task_dir in tasks_by_level.values():
        v1 = (task_dir / "client_v1.py").read_text()
        broken = grade_repair(task_dir, v1 + "\n\nClient.__init__ = lambda self, *a, **k: (_ for _ in ()).throw(RuntimeError('x'))\n", pool=pool)
        same = grade_repair(task_dir, v1, pool=pool)
        assert same.info["regression_rate"] == 0.0
        assert broken.info["regression_rate"] >= same.info["regression_rate"]
        assert broken.reward == 0.0


def test_case_1_penalties_apply(tasks_by_level, pool):
    _, task_dir = tasks_by_level[0]
    host = json.loads((task_dir / "engine.json").read_text())["host"]
    assert grade_repair(task_dir, ref(task_dir) + f"\nHOST = '{host}'\n", pool=pool).info["flags"] in ([], ["hardcoded_host"])
    assert grade_repair(task_dir, "def x(:\n", pool=pool).info["code_problem"].startswith("does not parse")
    assert grade_repair(task_dir, "x = 1\n", pool=pool).reward == 0.0


def test_reliability_k_is_not_available():
    with pytest.raises(ValueError):
        RepairEnv("train", reward_config=RewardConfig(reliability_k=2))


# ---------------------------------------------------------------- no hidden data in what the agent sees


@pytest.mark.parametrize("level", range(4))
def test_observation_has_no_hidden_data(tasks_by_level, cache, pool, level):
    spec, task_dir = tasks_by_level[level]
    env = RepairEnv("train", "R0", seed=0, cache_dir=cache, pool=pool)
    obs = env.reset(spec)
    task = task_json(task_dir)
    assert set(obs) == OBS_KEYS
    text = "\n".join(str(v) for v in obs.values() if v)
    secrets = [*task["credentials"]["test"].values(), *heal_build._item_ids(task["domain"], task["seeds"]["test"]), *task["heal"]["v1_credentials"].values()]
    assert not [s for s in secrets if s and s in text]
    assert not [n for n in HIDDEN_NAMES if n in text]
    assert not [m for m in spec["row"]["mutations"] if m in text]  # the mutation names are labels, not evidence
    assert ref(task_dir) not in text


@pytest.mark.parametrize("level,evidence", [(0, "both"), (1, None), (3, None)])
def test_evidence_channels_follow_the_spec(tasks_by_level, cache, pool, level, evidence):
    spec, task_dir = tasks_by_level[level]
    obs = RepairEnv("train", "R0", cache_dir=cache, pool=pool).reset(spec)
    want = {"both": (True, True), "failure_only": (True, False), "recording_only": (False, True)}[spec["evidence"]]
    assert (obs["failure"] is not None, obs["observation"] is not None) == want
    assert obs["observation_format"] == ("har" if want[1] else None)


# ---------------------------------------------------------------- splits and levels


def test_committed_splits_match_the_code():
    assert json.loads(repair_splits.SPLITS_PATH.read_text()) == json.loads(json.dumps(repair_splits.default_splits()))


def test_splits_are_disjoint_in_mutations_pairs_and_seeds():
    splits = repair_splits.load_splits()
    train_sets = [tuple(m) for m in splits["mutations"]["train"]]
    train_singles = {m[0] for m in train_sets if len(m) == 1}
    train_pairs = {frozenset(m) for m in train_sets if len(m) == 2}
    held_m, held_p = set(repair_splits.HELD_OUT_MUTATIONS), {frozenset(p) for p in repair_splits.HELD_OUT_PAIRS}
    assert len(train_singles) == 6 and not train_singles & held_m
    assert not any(set(p) & held_m for p in train_pairs) and not train_pairs & held_p
    assert {m for ms in splits["mutations"]["test_mutation"] for m in ms} == held_m
    assert {frozenset(p) for p in splits["mutations"]["test_pair"]} == held_p
    assert all(set(p) <= train_singles for p in held_p)  # each part is seen alone in train
    assert {m for p in train_pairs for m in p} == train_singles  # and in other pairs
    seeds = [spec["row"]["seed"] for name in ("test_mutation", "test_pair") for spec in splits["eval"][name]]
    assert len(seeds) == len(set(seeds)) == 2 * repair_splits.EVAL_TASKS
    core = {json.loads(line)["seed"] for line in (ROOT / "data" / "heal_configs.jsonl").read_text().splitlines()}
    assert not set(seeds) & core


def test_sampled_train_tasks_never_use_held_out_parts_or_eval_seeds():
    splits = repair_splits.load_splits()
    eval_seeds = repair_splits.reserved_seeds(splits)
    specs = repair_splits.sample_specs("train", 80, seed=3)
    held_m = set(repair_splits.HELD_OUT_MUTATIONS)
    held_p = {frozenset(p) for p in repair_splits.HELD_OUT_PAIRS}
    for spec in specs:
        names = spec["row"]["mutations"]
        assert not set(names) & held_m and frozenset(names) not in held_p
        assert spec["row"]["seed"] not in eval_seeds
        assert spec["level"] == repair_tasks.level_of(len(names), spec["error_detail"], spec["evidence"])
    assert {s["level"] for s in specs} == {0, 1, 2, 3}


def test_train_sampling_never_draws_a_task_that_cannot_be_solved():
    from har2client.heal.identify import NEEDS_RECORDING, identifiable

    for spec in repair_splits.sample_specs("train", 300, seed=4):
        assert spec["identifiable"] is True
        assert identifiable(spec["row"]["mutations"], spec["evidence"])
    needy = [m for m in repair_splits.mutation_sets("train", 1) if m[0] in NEEDS_RECORDING]
    needy_pairs = [m for m in repair_splits.mutation_sets("train", 3) if set(m) & set(NEEDS_RECORDING)]
    assert needy and needy_pairs
    for level, sets in ((1, needy), (3, needy_pairs)):
        for names in sets:
            seen = {repair_splits.sample_spec("train", random.Random(i), level=level, mutations=names)["evidence"] for i in range(25)}
            assert "failure_only" not in seen and seen  # R1 keeps terse/recording_only; R3 only recording_only


def test_eval_splits_keep_unsolvable_tasks_tagged_as_a_guessing_probe():
    from har2client.heal.identify import identifiable

    specs = [s for name in ("test_mutation", "test_pair") for s in repair_splits.load_splits()["eval"][name]]
    assert all(s["identifiable"] == identifiable(s["row"]["mutations"], s["evidence"]) for s in specs)
    assert any(s["identifiable"] is False for s in specs) and any(s["identifiable"] is True for s in specs)
    assert repair_tasks.normalize(specs[0])["identifiable"] == specs[0]["identifiable"]


def test_test_splits_only_hold_their_own_members_and_levels():
    for spec in repair_splits.sample_specs("test_mutation", 24):
        assert set(spec["row"]["mutations"]) <= set(repair_splits.HELD_OUT_MUTATIONS) and spec["level"] in (0, 1)
    for spec in repair_splits.sample_specs("test_pair", 24):
        assert frozenset(spec["row"]["mutations"]) in {frozenset(p) for p in repair_splits.HELD_OUT_PAIRS} and spec["level"] in (2, 3)
    assert repair_splits.sample_specs("test_pair", 24) == repair_splits.sample_specs("test_pair", 24)


def test_level_factors():
    levels = {lvl: {(s["error_detail"], s["evidence"]) for s in (spec_at(lvl, seed=i) for i in range(12))} for lvl in range(4)}
    assert levels[0] == {("verbose", "both")} and levels[2] == {("verbose", "both")}
    assert ("verbose", "both") not in levels[1] and len(levels[1]) > 1
    assert levels[3] <= {("terse", "failure_only"), ("terse", "recording_only")}
    assert all(len(spec_at(2, seed=i)["row"]["mutations"]) == 2 for i in range(5))


def test_curriculum_levels_per_split(cache, pool):
    assert RepairEnv("test_pair", "adaptive", cache_dir=cache, pool=pool).curriculum.level == 2
    assert RepairEnv("test_mutation", "adaptive", cache_dir=cache, pool=pool).curriculum.level == 0
    assert RepairEnv("train", "R3", cache_dir=cache, pool=pool).curriculum.fixed == 3
    with pytest.raises(ValueError):
        RepairEnv("test_mutation", "R3")


def test_adaptive_curriculum_promotes_on_good_rewards():
    env = RepairEnv("train", "adaptive")
    for _ in range(64):
        env.curriculum.record(1.0)
    assert env.curriculum.level == 1


def test_invalid_specs_are_rejected():
    row = {"index": 0, "mutations": ["add_hidden_header", "rename_hidden_header"], "domain": "tickets", "seed": 1,
           "config": {"auth": "apikey_header", "pagination": "none", "token_expiry": "off", "etag_update": "off", "hidden_header": "on", "envelope": "off"}}
    with pytest.raises(ValueError):
        repair_tasks.make_spec(row)  # two mutations on the same switch
    with pytest.raises(ValueError):
        repair_tasks.make_spec({**row, "mutations": ["add_etag"]})  # does not apply to this config
    with pytest.raises(ValueError):
        repair_tasks.make_spec({**row, "mutations": ["add_hidden_header"]}, evidence="nothing")
    assert set(BY_NAME) >= set(repair_splits.TRAIN_MUTATIONS)


def test_adapter_works_with_the_single_mutation_builder(tmp_path, monkeypatch):
    seen = {}

    def old_builder(row, out_dir, *, build_seed=0, noise=0.3, task_id=None):
        seen.update(row=row, kwargs=dict(build_seed=build_seed, noise=noise, task_id=task_id))
        return Path(out_dir) / task_id

    monkeypatch.setattr(heal_build, "build_heal_task", old_builder)
    spec = repair_tasks.normalize(spec_at(0))
    repair_tasks.build_task(spec, tmp_path, "tid")
    assert seen["row"]["mutation"] == spec["row"]["mutations"][0] and seen["kwargs"]["task_id"] == "tid"
    with pytest.raises(repair_tasks.FactorsUnavailable):
        repair_tasks.build_task(repair_tasks.normalize(spec_at(1, seed=0) | {"error_detail": "terse"}), tmp_path, "tid")
    pair = repair_tasks.normalize(spec_at(2))
    seen.clear()
    repair_tasks.build_task(pair, tmp_path, "tid")
    assert "mutation" not in seen["row"] and len(seen["row"]["mutations"]) == 2  # a pair must never become a single change


# ---------------------------------------------------------------- TRL and dataset


def test_repair_reward_fn_contract(tasks_by_level, cache, pool):
    spec, task_dir = tasks_by_level[0]
    fn = make_repair_reward_fn(cache_dir=cache, pool=pool)
    good = f"```python\n{ref(task_dir)}\n```"
    chat = [{"role": "user", "content": "x"}, {"role": "assistant", "content": good}]
    rewards = fn(prompts=["p"] * 3, completions=[good, "no code here", chat], task_spec=[spec] * 3, completion_ids=[[1]] * 3, trainer_state=None)
    assert rewards == [1.0, 0.0, 1.0]
    assert fn(completions=[good], task_specs=[json.dumps(spec)]) == [1.0]  # the alias of case 1
    with pytest.raises(ValueError):
        fn(completions=[good, good], task_spec=[spec])
    assert fn.__name__ == repair_reward_fn.__name__


def test_dataset_rows_and_determinism(tmp_path):
    a, b = tmp_path / "a.jsonl", tmp_path / "b.jsonl"
    for path, cache in ((a, "c1"), (b, "c2")):
        assert make_repair_dataset.main(["--split", "train", "--n", "4", "--out", str(path), "--cache-dir", str(tmp_path / cache), "--level", "0"]) == 0
    assert a.read_text() == b.read_text()
    rows = [json.loads(line) for line in a.read_text().splitlines()]
    assert len(rows) == 4
    for row in rows:
        assert set(row) == {"prompt", "task_spec"}
        user = row["prompt"][1]["content"]
        assert [m["role"] for m in row["prompt"]] == ["system", "user"]
        assert "class Client" in user and "client_v1.py" in user
        assert not [n for n in HIDDEN_NAMES if n in user]
        assert not [m for m in row["task_spec"]["row"]["mutations"] if m in user]


class _BaselineErrorPool:
    """A pool whose grade of client_v1 (the baseline job) crashes; every other job is graded for real."""

    def __init__(self, real):
        self.real, self.fail_baseline = real, True

    def run(self, jobs):
        grades = self.real.run(jobs)
        if self.fail_baseline:
            return [
                {"error": "RuntimeError: boom", "pass_rate": 0.0, "failure_counts": {"grader_error": 1}, "invented_endpoint_calls": 0,
                 "hardcoded_secret": False, "hardcoded_host": False, "results": [], "log": [], "grade_time_s": 0.0}
                if job["code"] == (Path(job["task_dir"]) / "client_v1.py").read_text() else g
                for job, g in zip(jobs, grades)
            ]
        return grades


def test_failed_baseline_grade_is_not_cached_and_regression_is_unknown_not_zero(tasks_by_level, pool):
    from har2client.env import repair_env

    _, task_dir = tasks_by_level[0]
    repair_env._BASELINES.pop(str(task_dir), None)
    bad = _BaselineErrorPool(pool)
    result = grade_repair(task_dir, EMPTY, pool=bad)
    assert result.info["regression_rate"] is None and "baseline_error" in result.info
    assert str(task_dir) not in repair_env._BASELINES  # a crashed baseline must not stay as "client_v1 passes nothing"
    bad.fail_baseline = False
    again = grade_repair(task_dir, EMPTY, pool=bad)
    assert again.info["regression_rate"] > 0.0 and "baseline_error" not in again.info


def test_dataset_row_of_a_failure_only_task_has_no_har_wording(cache):
    base = repair_splits.sample_spec("train", random.Random(3), level=0, mutations=("add_etag",))
    spec = repair_tasks.make_spec(base["row"], evidence="failure_only")
    row = make_repair_dataset.make_row(spec, cache)
    system, user = (m["content"] for m in row["prompt"])
    assert "HAR" not in system and "HAR" not in user and "failure.txt" in user
    both = make_repair_dataset.make_row(repair_tasks.make_spec(base["row"]), cache)
    assert both["prompt"][0]["content"].count("HAR file") == 1
