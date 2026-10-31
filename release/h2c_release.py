"""Shared helpers for the har2client Hugging Face release scripts (standard library only).

Used by `export_dataset.py`, `check_no_leak.py` and `sync_space_data.py`.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

RELEASE_DIR = Path(__file__).resolve().parent
REPO_ROOT = RELEASE_DIR.parent
H2C_DIR = REPO_ROOT / "har2client"
BUILD_DIR = RELEASE_DIR / "build"
DATASET_DIR = BUILD_DIR / "dataset"
CANARY_FILE = RELEASE_DIR / "CANARY.txt"
RESULTS_DIR = H2C_DIR / "results"

# Model comparison: the settings and suites that are compared (case 2 on the placebo `probe0` and P5 `probe5` arms only; no P20).
COMPARISON_MODELS = ("gpt-oss-120b-low", "gpt-oss-120b-medium", "gpt-oss-120b-high", "qwen3.5-397b-thinking")
COMPARISON_SUITES = ("tierb50", "v3base", "v3hint", "probe0", "probe5")
# The final figures, in display order (the Space shows them first on the Findings tab).
FINAL_FIGURES = tuple(f"final_{n}.png" for n in (
    "scoreboard", "leaderboard", "effort_curves", "information_gain", "gap_vs_beam", "beam_missing_info", "cost_vs_score",
    "effort_matched", "all_scores", "beam_by_effort", "probing", "probe_curve", "failure_kinds"))
CASE2_CSV = "case2_probe/main_full/results.csv"
COMPARISON_SUMMARY = "analysis/model_comparison/summary.json"

# Files of har2client/results/ that go into the dataset repo under `results/` (same relative path). The card's "Source"
# columns point at these. `results/README.md` is left out: it cites local script paths.
RESULTS_FILES = (
    "set30_v1_tierA.csv", "tierb50_v1.csv", "tierb50_medium.csv",
    "heal_v3_eval_base_low.csv", "heal_v3_eval_base_medium.csv", "heal_v3_eval_base_high.csv",
    "heal_v3_eval_hint_low.csv", "heal_v3_eval_hint_medium.csv", "heal_v3_eval_hint_high.csv",
    "heal_v3_eval_base_high_r2.csv", "heal_v3_eval_base_high_r3.csv", "heal_v3_eval_hint_high_r2.csv",
    "heal_v3_eval_hint_high_r3.csv", "tierb50_high_r2.csv", "tierb50_high_r3.csv",
    "heal_v3_eval_v1_floor.json", "tierb50_validity.json",
    # cited by the card (validity evidence, controls, regression)
    "grader_audit_set30.md", "set30_v1_regrade_newtests.json", "set30v_high_partial.csv",
    "analysis/tierb50_v1/summary.md", "analysis/tierb50_v1/coefficients.csv",
    "analysis/heal_v3_eval/report.md", "analysis/heal_v3_eval/summary.json",
    "analysis/tierb50_effort/report.md", "analysis/tierb50_effort/summary.json",
    "analysis/heal_controls/report.md", "analysis/heal_controls/summary.json",
    # case 2 (probe): the full run, the same 50 Tier B tasks (P20 and the header-off control are described in the card)
    *(f"case2_probe/main_full/{n}" for n in ("REPORT.md", "summary.json", "results.csv", "SENSITIVITY.md")),
    # case 2 exploratory extension (protocol case2-ext-v1): P5 high runs 2 and 3, 1 and 2 probes at high, and P5 at max effort (only when finished)
    "case2_probe/ext/PROTOCOL.md", "case2_probe/ext/R1_P5_high_r2r3/results.csv", "case2_probe/ext/R2_P1P2_high/results.csv",
    *(("case2_probe/ext/R3_P5_max/results.csv",) if (RESULTS_DIR / "case2_probe/ext/R3_P5_max/results.csv").is_file() else ()),
    # model comparison: Beam, GPT-OSS-120B and Qwen3.5-397B-A17B (the P20 arm is not part of the comparison and is not listed)
    "analysis/model_comparison/report.md", "analysis/model_comparison/summary.json", "model_comparison/README.md",
    # case 2 probing behaviour and Beam failure kinds (scripts/analyze_probing.py)
    "analysis/probing/report.md", "analysis/probing/summary.json",
    *(f"model_comparison/{m}/{s}/results.csv" for m in COMPARISON_MODELS for s in COMPARISON_SUITES),
    # the final figures (the card and the Space show them; built by scripts/plot_final.py)
    *(f"figures/{n}" for n in FINAL_FIGURES),
)
# The copies are exact except for these wording changes. The leak check bans the name of a hidden grading file in published text,
# and this report names one in passing. Fix it at the source (the analysis script) and this entry can go.
RESULTS_REWRITES: dict = {}
RESULTS_FIGURES = (  # glob patterns, PNG only
    "story_*.png", "heal_v3_eval_frontier.png", "tierb50_frontier.png", "heal_v3_eval_families.png", "models_*.png",
)
SPACE_FIGURES = (  # the Findings tab, in display order (the captions are in space/app.py, FINDINGS_FIGURES)
    *FINAL_FIGURES, "story_effort_curves.png", "story_hint_vs_effort.png", "story_cost_per_solve.png", "story_family_heatmap.png", "story_header_gap.png",
    "story_hint_decomposition.png",
)

# config name -> (case, tier). Cases: build (case 1), probe (case 2), heal (case 3).
CONFIGS: dict[str, tuple[str, str]] = {
    "tier_a": ("build", "A"),
    "tier_b": ("build", "B"),
    "probe": ("probe", "B"),
    "heal": ("heal", "B"),
    "heal_v3": ("heal", "B"),  # case 3 v3 evaluation suite, base arm
    "heal_v3_hint": ("heal", "B"),  # the same tasks with the contract hint paragraph in goal.md
}
HEAL_V3_ARMS: dict[str, str] = {"heal_v3": "base", "heal_v3_hint": "hint"}  # config -> arm label in the metadata

DEFAULT_DIRS: dict[str, Path] = {
    "tier_a": H2C_DIR / "runs" / "set30",
    "tier_b": H2C_DIR / "runs" / "tierb50",
}

# Files a solver may see. Everything else in a task dir is hidden and is never opened by the exporter.
GOAL_FILE = "goal.md"
OBSERVATION_FILES = ("session.har",)  # add new traffic formats here (the file extension is the format)
_HEAL_FILES = {"client_v1.py": "client_v1", "failure.txt": "failure_txt"}
EXTRA_VISIBLE: dict[str, dict[str, str]] = {
    # config -> {file name in the task dir: row column}. Case 3 shows the old client and its failure;
    # v3 adds the optional release notes (the column is None for a task without CHANGELOG.md).
    "heal": _HEAL_FILES,
    "heal_v3": {**_HEAL_FILES, "CHANGELOG.md": "changelog_md"},
    "heal_v3_hint": {**_HEAL_FILES, "CHANGELOG.md": "changelog_md"},
}
# Every file a solver may see, in any config. The leak check treats a value found in one of these as visible by design.
VISIBLE_FILES = (GOAL_FILE, "client_v1.py", "failure.txt", "CHANGELOG.md")

# Files that hold hidden grading data. The leak check reads them; the exporter never does.
HIDDEN_FILES = ("task.json", "op_table.json", "tests.json", "engine.json", "ref_client.py", "grade.json")


def results_figure_names(results_dir: Path = RESULTS_DIR) -> list[str]:
    """Relative paths (`figures/<name>.png`) of the figures that ship with the release, sorted, no duplicates."""
    found = {p.name for pat in RESULTS_FIGURES for p in (results_dir / "figures").glob(pat)}
    return [f"figures/{n}" for n in sorted(found)]


def read_any(path: Path) -> str:
    """Text of a file; binary files (PNG) are read as latin-1 so metadata chunks can still be scanned."""
    data = Path(path).read_bytes()
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("latin-1")


def read_canary() -> str:
    return CANARY_FILE.read_text(encoding="utf-8").strip()


def canary_guid() -> str:
    return read_canary().rsplit(" ", 1)[-1]


def add_dir_args(parser: argparse.ArgumentParser) -> None:
    """The task-set arguments shared by the exporter and the leak check."""
    parser.add_argument("--tier-a", type=Path, default=DEFAULT_DIRS["tier_a"], help="Tier A task dirs (default: har2client/runs/set30)")
    parser.add_argument("--tier-b", type=Path, default=DEFAULT_DIRS["tier_b"], help="Tier B task dirs (default: har2client/runs/tierb50)")
    parser.add_argument("--probe", type=Path, default=None, help="case 2 task dirs (placeholder until they are built)")
    parser.add_argument("--heal", type=Path, default=None, help="case 3 task dirs (placeholder until they are built)")
    parser.add_argument("--heal-v3", type=Path, default=None, help="case 3 v3 evaluation suite, base arm (har2client/runs/heal_v3_eval)")
    parser.add_argument("--heal-v3-hint", type=Path, default=None, help="case 3 v3 evaluation suite, hint arm (har2client/runs/heal_v3_eval_hint)")


def selected_dirs(args: argparse.Namespace) -> dict[str, Path]:
    """config name -> task-set directory, for the configs that were given and exist."""
    wanted = {"tier_a": args.tier_a, "tier_b": args.tier_b, "probe": args.probe, "heal": args.heal,
              "heal_v3": args.heal_v3, "heal_v3_hint": args.heal_v3_hint}
    out: dict[str, Path] = {}
    for name, path in wanted.items():
        if path is None:
            continue
        if not Path(path).is_dir():
            print(f"note: {name}: {path} does not exist, config skipped")
            continue
        out[name] = Path(path)
    return out


def task_dirs(root: Path) -> list[Path]:
    """Task dirs (a dir with a goal.md) directly under root, sorted by name."""
    return sorted(d for d in Path(root).iterdir() if d.is_dir() and (d / GOAL_FILE).is_file())


def find_observation(task_dir: Path) -> Path:
    found = [task_dir / name for name in OBSERVATION_FILES if (task_dir / name).is_file()]
    if len(found) != 1:
        raise ValueError(f"{task_dir}: expected exactly one observation file {OBSERVATION_FILES}, found {len(found)}")
    return found[0]


def load_json(path: Path):
    return json.loads(Path(path).read_text(encoding="utf-8"))
