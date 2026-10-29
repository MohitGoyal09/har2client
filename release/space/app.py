"""har2client leaderboard Space: Leaderboard, Explore a task, Case 3, Findings, About.

Every number is computed from the CSV and analysis files in `data/` (copies of `har2client/results/`); see `tables.py`.
Run locally:  python app.py
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import gradio as gr

import tables as T

HERE = Path(__file__).resolve().parent
FIGURES = HERE / "data" / "figures"
LINKS_FILE = HERE / "data" / "links.json"
BUNDLE = T.load_bundle()
CHOICES = T.task_choices(BUNDLE)

CSS = """
.gradio-container { max-width: 1180px !important; margin: 0 auto; }
#h2c-title h1 { font-family: 'Source Serif 4', Georgia, serif; font-size: 2.2rem; letter-spacing: -0.01em; margin-bottom: 0.1rem; }
#h2c-title p { color: var(--body-text-color-subdued); max-width: 62rem; }
.h2c-note { border-left: 3px solid var(--color-accent); padding: 0.4rem 0.9rem; background: var(--background-fill-secondary); border-radius: 0 6px 6px 0; }
.h2c-big { font-size: 1.05rem; line-height: 1.55; }
"""

THEME = gr.themes.Base(
    primary_hue="teal", neutral_hue="stone",
    font=[gr.themes.GoogleFont("IBM Plex Sans"), "system-ui", "sans-serif"],
    font_mono=[gr.themes.GoogleFont("IBM Plex Mono"), "ui-monospace", "monospace"],
)


# The Findings figures: (file in data/figures/, one-line caption). The Space folder is staged on its own, so this list lives here.
FINDINGS_FIGURES = (
    ("final_scoreboard.png", "Beam is best on 3 of 5 benchmarks: pass rate of every model setting on the five benchmarks (outlined cell = highest in its column; gaps of a few points are within noise)."),
    ("final_leaderboard.png", "Each model at its best setting (Beam high, GPT-OSS-120B high, Qwen3.5 thinking on), with 95% intervals over tasks: Beam leads once it gets the missing information; GPT-OSS-120B leads without it."),
    ("final_effort_curves.png", "More thinking effort helps, but how much depends on the benchmark and the model."),
    ("final_information_gain.png", "Beam gains the most from added information: the hint in case 3 and the probes in case 2, for each model at high effort."),
    ("final_gap_vs_beam.png", "Difference from Beam (high) with 95% intervals over tasks: without extra information GPT-OSS-120B matches or beats Beam; with it, Beam pulls ahead."),
    ("final_beam_missing_info.png", "Tier B, 25 tasks each: Beam builds clients well when the recording shows everything, and recovers when it is given the missing piece (the hidden header)."),
    ("final_cost_vs_score.png", "Pass rate against mean output tokens per call: higher scores cost more tokens; Beam (high) uses fewer output tokens than GPT-OSS-120B (high) on 4 of the 5 benchmarks."),
    ("story_effort_curves.png", "Pass rate against output tokens at low, medium and high effort: Tier B, and case 3 with and without the hint (low and medium: 1 run per task; high: mean of 3 runs per task; 95% CI over tasks)."),
    ("story_hint_vs_effort.png", "Case 3: the hint at low effort beats the base prompt at high effort, with fewer output tokens (paired hint minus base gain per effort)."),
    ("story_cost_per_solve.png", "Output tokens per fully solved task: the cost of a solve rises with effort, and the hint lowers it in case 3."),
    ("story_family_heatmap.png", "Case 3 pass rate by task family, arm and effort: the hint lifts every family; family cells hold 8 to 14 tasks."),
    ("story_header_gap.png", "Tier B: tasks with the hidden required header on score far lower than tasks with it off, at every effort."),
    ("story_hint_decomposition.png", "Case 3 at low effort: base, placebo (same-length neutral text), notice only (says the API changed) and the full hint, with the paired gain between neighbouring bars."),
)


def links() -> dict:
    defaults = {"dataset": "", "report": ""}
    if LINKS_FILE.is_file():
        defaults.update(json.loads(LINKS_FILE.read_text(encoding="utf-8")))
    return defaults


def link_line(label: str, url: str, fallback: str) -> str:
    return f"[{label}]({url})" if url else f"{label}: {fallback}"


# ---------------------------------------------------------------- tab text


def leaderboard_notes() -> str:
    n_runs = [s["runs_per_task"] for per_effort in BUNDLE.summaries.values() for s in per_effort.values()]
    one_run = all(lo == hi == 1 for lo, hi in n_runs)
    return (
        "**How to read this.** One row per system, tier and reasoning effort. The pass rate is the share of hidden tests "
        "passed, averaged over tasks (the pre-registered primary metric). The interval is a 95% percentile bootstrap that redraws "
        "whole tasks, so it shows task-sampling noise only. "
        + ("Every cell is a **single run per task**, so run-to-run noise is not measured yet. " if one_run else
           "Cells with a **Runs / task** of 3 (high effort in Tier B and case 3) are the mean of 3 runs per task; the other cells are 1 run, except two runs for case 3 base and hint at low effort. ")
        + "This table lists Beam only; the other two models are in the Model comparison tab. Source files are named in the last column; they are in the `results/` folder of the dataset repo. "
        "The Tier A rows are computed on the 30 tasks that were run. The 27-task row leaves out the 3 tasks whose API spec has a restrictive "
        "licence; those 3 tasks are not in the published dataset."
    )


def explore(choice: str, only_json: bool):
    """Goal, feature table, Beam results, summary line, request table and excerpt for one task."""
    if not choice:
        return "Pick a task.", None, None, "", None, ""
    import pandas as pd  # noqa: PLC0415 - gradio already depends on pandas

    task_id = choice.split(" | ", 1)[1]
    rec = BUNDLE.tasks[task_id]
    rows = [r for r in rec["requests"] if not only_json or "json" in str(r[3])]
    suite = T.SUITE_LABELS.get(rec.get("suite", ""), f"Tier {rec['tier']}")
    summary = (f"**{rec['title']}** ({task_id}). {suite}. The recording has {rec['n_entries']} entries; "
               f"showing {len(rows)}.")
    return (rec["goal_md"], T.feature_table(rec, task_id, BUNDLE), T.task_results(BUNDLE, task_id), summary,
            pd.DataFrame(rows, columns=["Method", "URL", "Status", "Content type"]), rec["excerpt"])


def findings_markdown() -> tuple[str, str, str]:
    hh = T.header_result(BUNDLE)
    low, high = hh["low"], hh["high"]
    headline = (
        "### The hidden-header result\n"
        f"A Tier B task can require one custom header on every API call (for example `X-App-Build: 2381`). It sits among about a dozen "
        f"ordinary browser headers in the recording. Marginal mean pass rate over the other switches, {high['off']['n_tasks']} tasks with the "
        f"header off against {high['on']['n_tasks']} with it on:\n\n"
        f"- **High effort:** {high['off']['mean_pct']:.1f}% off, {high['on']['mean_pct']:.1f}% on ({high['on']['mean_pct'] - high['off']['mean_pct']:+.1f} points).\n"
        f"- **Low effort:** {low['off']['mean_pct']:.1f}% off, {low['on']['mean_pct']:.1f}% on ({low['on']['mean_pct'] - low['off']['mean_pct']:+.1f} points).\n\n"
        "The regression below, which holds the other switches fixed, gives the same sign with intervals that exclude zero at both efforts.\n\n"
        "<div class='h2c-note'>**Caveats.** Low and medium effort are one run per task in this table; high effort is the mean of 3 runs per task. The feature is one we invented, and the requirement may not be "
        "identifiable from the recording (no failing call shows it). At high effort the header-off tasks average 99.8%, so that group's interval "
        "is almost zero width and understates the uncertainty. The pre-registered ablation H10 (hint in the goal, a failed call in the recording) "
        "was run on the 25 header-on tasks at high effort. It is supported: either change lifts the pass rate by more than 20 points, and the hint "
        "arm passes 90% (the rule's mark for \"a prompting effect\"). The table below has the numbers.</div>"
    )
    effort = (
        "### Effort buys accuracy, at a token price\n"
        + "\n".join(T.effort_sentences(BUNDLE))
        + "\n\nOn Tier A the gain is small because the tier is nearly solved (see the first row of the leaderboard)."
    )
    counts = sorted({int(n) for n in T.variant_table(BUNDLE)["Tasks run"]})
    covered = " to ".join(str(n) for n in (counts[0], counts[-1])) if len(counts) > 1 else str(counts[0])
    controls = (
        "### Name and memory controls on Tier A (partial)\n"
        "Beam at high effort, one run, on variants of the 30 public-API tasks. The daily token limit stopped the run early, so each variant "
        f"covers only {covered} of the 30 tasks and the comparison below uses only tasks present in both columns. Source: `set30v_high_partial.csv`. "
        "These are not the final H6 and H7 estimates: the pre-registered fix (descriptions on the reworded variant, and a closed-book control on the "
        "renamed twin) is not yet in this file."
    )
    return headline, effort, controls


def about_markdown() -> str:
    lk = links()
    return f"""
### What this is
**har2client** measures one skill: given recorded traffic of an API and a goal, write a client that works on a fresh copy of that API.
A task gives the model a goal (the methods to write) and a recording (a HAR file, mixed with browser noise). The model writes `client.py`.
We run it against a new server with new data and new credentials, and count how many hidden tests pass. Code that copies the recording fails.

### The three cases
1. **Build** (traffic to client). Released. Tier A: 27 published tasks from public APIs (30 were run; see Limits), spec-driven mocks, synthetic sessions. Tier B: 50 invented stateful APIs with six feature switches (login style, paging, token expiry, ETag version check, a hidden header, a response envelope).
2. **Investigate** (probe, then write). The agent may send a few requests to a practice server before it writes the client. Complete (all 50 Tier B tasks, 3 arms, 3 effort levels, 1 run per cell, 450 cells; run with protocol `submission-tool-all-arms-v1`). At high effort the pass rate is 49.7% for the no-probe placebo, 95.7% for up to 5 probes (P5) and 98.0% for up to 20 probes (P20); P20 minus placebo is +48.3 points [32.9, 62.6]. With the hidden header on, the gain is +64.0 [40.0, 84.0] (placebo 32.0%, P20 96.0%); with it off, +32.6 [16.0, 50.3] (placebo 67.4%, P20 100.0%). H11 is not supported: the header-off control fails (the gain should be under 5 points). One placebo cell produced no code (it hit the output-token cap) and counts as zero. A post-hoc check (tasks 20-49 only, not pre-registered) found a stray closing triple quote at the end of submitted code: placebo 15 of 89 clients, P5 3 of 90, P20 3 of 90. Removing it halves the header-off gain, which stays above 5 points. The placebo at high effort (49.7%) is far below case 1 Tier B as built (74.5%, mean of 3 runs) on the same tasks; the submission-tool interface is one known reason. Case 2 is 1 run per cell, so its intervals cover variation over tasks only; repetition is deferred. Tasks 00-19 ran on one machine and tasks 20-49 on a second, from the same frozen runtime archive (hashes checked). Case 2 uses the same 50 Tier B task rows as case 1 (the `tier_b` config). Its results are in `results/case2_probe/main_full/` of the dataset repo.
3. **Maintain** (the API changes, the agent repairs). Released as the v3 evaluation suite: 64 repair tasks in two arms (base and with a contract hint). The agent gets a working v1 client, a failure log and a short new recording, and writes the repaired client. See the Case 3 tab.


### How Beam was used
- **Model and API.** `Beam-1.0-501B-A23B` (501B parameters, 23B active) through Reflection's OpenAI-compatible API, `https://api.reflection.ai/openai/v1`. We set `reasoning_effort` to `low`, `medium` or `high` on every call.
- **Beam is the model under test in all three cases.** Case 1: it builds an API client from recorded traffic (one call, no tools). Case 2: it probes a live practice server with a tool-calling agent, then submits the client. Case 3: it repairs a client after the API changed (one call, no tools, no retries).
- **Multi-turn tool calling (case 2).** The agent has two tools, `http_request` (to the practice server) and `submit_client`. The `reasoning_content` of every assistant turn is sent back in the next request. The placebo arm has no `http_request` tool; P5 and P20 allow up to 5 and 20 requests.
- **Grading.** Hidden tests run the submitted `client.py` on a fresh server (new data, new credentials, new host). There is no LLM judge.
- **Pre-registered.** The hypotheses were written down with a date before the runs; every later change is logged with a reason. High effort in Tier B and case 3 is the mean of 3 runs per task.

### Method
- Hidden tests run on a fresh instance of the same API. Tier A checks each request and return value. Tier B and case 3 check return values and saved server state.
- Every task has a reference client that must score 1.0, so our bugs do not count as model failures. A grader audit injects many kinds of fault into correct clients and checks that the grader catches them (see Findings).
- Hypotheses were pre-registered: written down with a date before the first run, and every later change is logged with a date and a reason. The registration file is kept with the benchmark code.

### Limits you should know
- Tier B and case 3 are **controlled stateful APIs** from one engine. They are not a sample of real-world APIs or real service releases.
- All traffic is synthetic. Tier A uses OpenAPI 3.0 specs from APIs.guru only. 20 of the 27 published Tier A specs declare no licence, and 3 more specs with a restrictive licence were run but are not published.
- Beam is the main system. GPT-OSS-120B and Qwen3.5-397B-A17B are in the Model comparison tab, with 1 run per cell. High effort for Beam in Tier B and case 3 (base and hint) is the mean of 3 runs per task. Case 3 at low effort (base and hint) has two runs. All other results are one run per task. The pre-registered default of three runs is a recorded deviation. Intervals show task-sampling noise only.
- Three effort levels: low, medium and high. xhigh was dropped on 2026-10-27 (recorded in the pre-registration deviations).

### Links
- {link_line('Dataset', lk['dataset'], 'published with the release')}
- [Benchmark code](https://github.com/MohitGoyal09/har2client) (task builders, mock servers, grader; Apache-2.0). Clone it, run `cd har2client && uv sync`, then `uv run har2client grade TASK_DIR client.py` to grade a client. The dataset card describes the method.
- {link_line('Report', lk['report'], 'to come')}

Licence: this Space (code) is Apache-2.0. The dataset is CC-BY-4.0; Tier A rows keep the terms of the API descriptions they derive from (see the dataset card).

Canary: this benchmark's data must not appear in training corpora (the GUID is in every dataset row).
"""


def case3_markdown() -> tuple[str, str, str, str, str]:
    intro = (
        "### Case 3: the API changes, the model repairs the client\n"
        "Each of the 64 v3 tasks gives the model a client that worked on API v1, the first failure on v2, and a short recording of v2. "
        "A release changes the API in 1, 3 or 4 ways (five silent wire changes and five protocol changes). The model writes the repaired "
        "`client.py` in one call, with no tools and no retries. Each task has 17 hidden tests; **full repair** means all 17 pass. "
        "Two arms: **base** (the goal as built) and **hint** (the same goal plus one paragraph that warns the wire format may have changed, "
        "lists the kinds of change, and says to return what `client_v1.py` returns).\n\n"
        "Beam-1.0-501B-A23B, one run per task at low and medium effort, and the mean of 3 runs per task at high effort. The low-effort base and hint cells have a second run (see the repeat table below). xhigh was dropped on 2026-10-27."
    )
    gaps = (
        "**Paired contrasts** (over the same 64 tasks, 95% bootstrap interval over tasks, from the committed analysis). "
        "The hint adds information as well as a warning, so the hint gap is not a pure measure of \"not noticing\". "
        "The controls table below separates the two parts: a placebo paragraph (neutral text of the same length) and a notice-only paragraph "
        "(says only that the API changed), both at low effort."
    )
    controls = (
        "**Controls at low effort** (paired by task, 64 tasks, points, 95% bootstrap interval over tasks, from `analysis/heal_controls/summary.json`). "
        "H22 is supported when the placebo does nothing and the hint adds at least 20 points over the placebo. H23 asks whether the notice alone gives "
        "at least 20 points over base."
    )
    repeats = (
        "**Repeat runs.** At low effort the second run moves the means by about a point or less. At high effort there are three runs per task (the high scores "
        "are their mean); the run means differ by up to 8 points, with no CI that excludes 0, and the per-task SD is 10 to 16 points. Single tasks change: the last column is the "
        "share of tasks whose full-repair status differs between runs. Read the claims as claims about means."
    )
    caveats = (
        "<div class='h2c-note'>**Caveats.** One run per cell at low and medium, except the low-effort base and hint cells (two runs); high is the mean of 3 runs. One model. The 64 tasks come from one invented engine "
        "with ten kinds of change, so they test those changes, not real service releases. Family cells hold 8 to 14 tasks and have no interval. "
        "The hint arm is a prompting variant: do not mix its scores with base-arm scores. The first reading of the failures (the main one is "
        "returning the new wire format instead of the v1 return shape) comes from a hand check on the pilot, not from a test.</div>"
    )
    return intro, gaps, controls, repeats, caveats


def comparison_markdown() -> tuple[str, str, str]:
    intro = (
        "### Beam against GPT-OSS-120B and Qwen3.5-397B-A17B\n"
        "The same five benchmarks, scored by the same hidden tests: case 1 (Tier B, 50 tasks), case 2 on the placebo and P5 arms (the same 50 tasks), "
        "and case 3 base and with the hint (64 tasks each). Mean pass rate in %. Every cell for GPT-OSS-120B and Qwen is **one run per task**."
    )
    method = (
        "<div class='h2c-note'>**Method notes.** GPT-OSS-120B ran at low, medium and high `reasoning_effort`. Qwen3.5-397B-A17B ran with thinking on (`enable_thinking=true`); "
        "it has no effort levels, so it matches no Beam effort. The effort labels are API settings, not equal compute: compare the token table as well. "
        "Both models ran through the Hugging Face Inference Providers router, with Novita as the provider (DeepInfra returned 504 errors in smoke tests). "
        "Beam used Reflection's API. Beam (high) in cases 1 and 3 is the mean of 3 runs per task; Beam low and medium, and all of Beam's case 2, are 1 run. "
        "The other models were capped at 32,768 output tokens per call and Beam at 65,536; no call of the other models came near the cap (largest: 20,316 tokens) and no cell had an API error. "
        "In case 2 the placebo and P5 arms both have the `submit_client` tool; only P5 has `http_request`. "
        "Intervals are 95% percentile bootstrap over tasks (10,000 resamples) and do not include run-to-run noise.</div>"
    )
    gaps = (
        "**Difference from Beam (high)**, in points, with the 95% interval over tasks (paired by task, from `analysis/model_comparison/summary.json`). "
        "A negative value means the setting scores below Beam (high)."
    )
    return intro, method, gaps


# ---------------------------------------------------------------- UI


def build_app() -> gr.Blocks:
    headline, effort_md, controls_md = findings_markdown()
    case3_intro, case3_gaps, case3_controls, case3_repeats, case3_caveats = case3_markdown()
    cmp_intro, cmp_method, cmp_gaps = comparison_markdown()
    with gr.Blocks(title="har2client: traffic to client") as demo:
        gr.Markdown(
            "# har2client\nCan a model turn recorded API traffic into a client that works on a fresh server? "
            "A benchmark with hidden execution tests, built to test Beam-1.0-501B-A23B and compared with GPT-OSS-120B and Qwen3.5-397B-A17B.",
            elem_id="h2c-title",
        )
        with gr.Tabs():
            with gr.Tab("Leaderboard"):
                with gr.Row():
                    tier = gr.Dropdown(["all", *T.TIERS], value="all", label="Tier", scale=1)
                    effort = gr.Dropdown(["all", "low", "medium", "high"], value="all", label="Effort", scale=1)
                table = gr.Dataframe(T.leaderboard(BUNDLE), interactive=False, wrap=True)
                gr.Markdown(leaderboard_notes(), elem_classes="h2c-note")
                for control in (tier, effort):
                    control.change(lambda t, e: T.leaderboard(BUNDLE, t, e), [tier, effort], table)

            with gr.Tab("Explore a task"):
                with gr.Row():
                    pick = gr.Dropdown(CHOICES, value=CHOICES[0] if CHOICES else None, label="Task (tier | id)", scale=3)
                    only_json = gr.Checkbox(value=False, label="Only JSON responses", scale=1)
                summary = gr.Markdown()
                with gr.Row():
                    with gr.Column(scale=3):
                        gr.Markdown("#### Goal (what the model reads)")
                        goal = gr.Markdown(elem_classes="h2c-big")
                    with gr.Column(scale=2):
                        gr.Markdown("#### Which features are on")
                        feats = gr.Dataframe(interactive=False, wrap=True)
                        gr.Markdown("#### Beam results on this task")
                        res = gr.Dataframe(interactive=False)
                gr.Markdown("#### Observation excerpt (first JSON API exchange)")
                excerpt = gr.Code(language="json", interactive=False, lines=18)
                gr.Markdown("#### Every request in the recording")
                reqs = gr.Dataframe(interactive=False, wrap=True, max_height=420)
                outputs = [goal, feats, res, summary, reqs, excerpt]
                pick.change(explore, [pick, only_json], outputs)
                only_json.change(explore, [pick, only_json], outputs)
                demo.load(explore, [pick, only_json], outputs)

            with gr.Tab("Case 3"):
                gr.Markdown(case3_intro)
                gr.Markdown("**Pass rate and full repairs by arm and effort** (64 tasks, 17 hidden tests each).")
                gr.Dataframe(T.case3_table(BUNDLE), interactive=False, wrap=True)
                gr.Markdown(T.case3_floor_sentence(BUNDLE))
                gr.Markdown(case3_gaps)
                gr.Dataframe(T.case3_gap_table(BUNDLE), interactive=False, wrap=True)
                gr.Markdown(case3_controls)
                gr.Dataframe(T.case3_controls_table(BUNDLE), interactive=False, wrap=True)
                gr.Markdown(case3_repeats)
                gr.Dataframe(T.case3_repeat_table(BUNDLE), interactive=False, wrap=True)
                gr.Markdown("**Mean pass rate (%) by task family.** Families: single (1 change), release3 and release4 (3 or 4 changes), partial (thin recording), "
                            "sdk (layered old client), misleading (wrong release note), terse (short errors).")
                gr.Dataframe(T.case3_family_table(BUNDLE), interactive=False, wrap=True)
                gr.Markdown(case3_caveats)

            with gr.Tab("Model comparison"):
                gr.Markdown(cmp_intro)
                gr.Markdown("**Mean pass rate (%)**")
                gr.Dataframe(T.comparison_table(BUNDLE), interactive=False, wrap=True)
                gr.Markdown(cmp_gaps)
                gr.Dataframe(T.comparison_gap_table(), interactive=False, wrap=True)
                gr.Markdown("**Mean output tokens per call** (reasoning included).")
                gr.Dataframe(T.comparison_tokens_table(BUNDLE), interactive=False, wrap=True)
                gr.Markdown("**Beam (high) against GPT-OSS-120B (high) and Qwen3.5-397B-A17B (thinking on)**\n" + "\n".join(T.comparison_sentences(BUNDLE)))
                gr.Markdown(cmp_method)

            with gr.Tab("Findings"):
                gr.Markdown("### The results at a glance\nLow and medium effort are one run per task and high effort is the mean of 3 runs per task; intervals show task-sampling noise only.")
                for name, caption in FINDINGS_FIGURES:
                    gr.Image(str(FIGURES / name), show_label=False, interactive=False, buttons=["download"])
                    gr.Markdown(f"*{caption}*")
                gr.Markdown(headline)
                gr.Markdown("**H10: the 25 hidden-header tasks at high effort** (as built: mean of 3 runs; hint and evidence: 1 run each). The goal as built, the goal plus a one-line hint, and the recording with a failed call that shows the header.")
                gr.Dataframe(T.h10_table(BUNDLE), interactive=False)
                gr.Markdown("**Marginal pass rate by switch (Tier B)**. Each row averages over the other switches. Intervals redraw tasks within the group.")
                gr.Dataframe(T.switch_table(BUNDLE), interactive=False, wrap=True)
                gr.Markdown("**Regression: change in pass rate when one switch is turned on, others held fixed**. Copied from `analysis/tierb50_v1/coefficients.csv`.")
                gr.Dataframe(T.coefficient_table(BUNDLE), interactive=False)
                gr.Markdown(effort_md)
                gr.Dataframe(T.effort_table(BUNDLE), interactive=False)
                gr.ScatterPlot(T.effort_table(BUNDLE), x="Output tokens / run", y="Mean pass rate (%)", color="Effort", title="Pass rate against output tokens (one point per tier and effort)")
                gr.Markdown(controls_md)
                gr.Dataframe(T.variant_table(BUNDLE), interactive=False, wrap=True)
                gr.Markdown("### Is the grader trustworthy?\nFrom `grader_audit_set30.md` (Tier A, the 30 tasks that were run; 27 are published). The reference client passes everything; trivial agents score zero; "
                            "survivors of the 501 injected faults are documented leniencies and extra calls that are reported but not scored.")
                gr.Markdown(T.audit_section(BUNDLE))

            with gr.Tab("About"):
                gr.Markdown(about_markdown())
    return demo


if __name__ == "__main__":
    build_app().launch(
        theme=THEME, css=CSS, allowed_paths=[str(HERE / "data")],
        server_name=os.environ.get("GRADIO_SERVER_NAME", "0.0.0.0"),
        server_port=int(os.environ.get("PORT", os.environ.get("GRADIO_SERVER_PORT", "7860"))),
    )
