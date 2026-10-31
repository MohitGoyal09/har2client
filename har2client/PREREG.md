# Pre-registration: har2client v0

Written 2026-10-23, before any model was run on any task. Committed to git so the timestamp is public. Changes after the first Beam run go in "Deviations" at the end, with a date and a reason.

## Systems
- S1 Beam, bare prompt, `reasoning_effort=low`, raw HAR.
- S2 Beam, bare prompt, `reasoning_effort=high`, raw HAR.
- S3 Beam, bare prompt, `high`, compact HAR (same-host entries only, bodies cut to 2,000 chars).
- S4 reverse-api-engineer (offline `engineer` mode) with Beam as its model, through OpenCode.
- S5 mitmproxy2swagger (no LLM): spec metrics only, no client.
- S6 one or two other open models with the S2 prompt (chosen before runs; listed here on day 3).
- Reference client generated from the gold spec: must score 100% on every task (task validity check, not a system).

Each LLM system: 3 runs per task.

## Primary metric
Execution pass rate = share of hidden tests passed (DESIGN.md "Hidden tests and grading"). A test runs against a fresh mock with new data and new credentials.

## Secondary metrics
Invented-endpoint calls; hard-coded recording secret; hard-coded spec host; failure reasons; tokens and wall time.

## Task features (tagged at build time)
`auth_type` (none, apiKey-header, apiKey-query, bearer, basic), `has_path_params`, `has_body`, `has_query`, `noise_ratio`, `har_tokens`, `n_goal_ops`, `n_spec_ops`. Tier B adds `login_flow`, `pagination`, `dependent_calls`, `hidden_header`.

## Hypotheses
| ID | Hypothesis | Test | Supported if | Rejected if |
|---|---|---|---|---|
| H1 | Auth and dependent calls, not endpoint discovery, cause most failures | Pass rate on tasks with login flow or dependent calls vs tasks with neither (S2) | Gap >= 25 points | Gap < 10 points |
| H2 | Models invent endpoints | Share of S2 clients with >= 1 invented-endpoint call | >= 15% (and higher in S1) | < 3% |
| H3 | Noise costs Beam little | S2 (raw) vs S3 (compact), same tasks | S3 - S2 < 5 points | S3 - S2 >= 10 points |
| H4 | Effort helps only on hard tasks | S2 - S1 on simple tasks vs on auth/dependent tasks | < 3 points on simple, >= 10 on hard | Same gain on both |
| H5 | An agent harness beats a bare prompt | S4 vs S2 | S4 - S2 >= 10 points | No difference (CI covers 0) |

"Simple" task: auth `none` or `apiKey-header`, no login flow, no dependent calls, no pagination.

### Added 2026-10-24, before any run of these conditions (from the related-work review in `docs/related-work/`)
| ID | Hypothesis | Test | Supported if | Rejected if |
|---|---|---|---|---|
| H6 | Name matching drives Tier A scores | Same tasks with goal names exact vs reworded vs opaque (Beam high) | opaque >= 15 points below exact | gap < 5 points |
| H7 | Memory contributes to Tier A scores | Original vs renamed twin (paired); closed-book run (goal only, empty HAR) | twin >= 5 points below original, or closed-book >= 20% | drop < 2 points and closed-book < 5% |
| H8 | Failures concentrate on behaviours the HAR does not show (Tier B) | Failure rate by identifiability label | unobservable >= 2x identifiable | ratio < 1.25 |
| H10 | The hidden-header failure is about evidence, not prompting (added 2026-10-24 after external review, before these runs) | 25 hidden-header tasks, Beam high, 2 runs each, 3 arms: (a) as built; (b) `hint`: goal.md gains one line "Replicate any custom (non-browser) request headers the API calls use."; (c) `evidence`: the HAR gains one failed call without the header (400, error names the header) followed by the retried call with it | (c) - (a) >= 20 points and (b) - (a) >= 20 points: both prompting and evidence matter; report which is larger | if (b) reaches >= 90%, the headline is a prompting effect, and we say so |
| H11 | Probing fixes what the recording cannot show (case 2; added 2026-10-24 before any probe run) | 50 Tier B tasks, Beam high; arms P-placebo, P5, P20 (`DESIGN-CASES.md`) | on hidden-header tasks P20 - P-placebo >= 20 points, and on header-off tasks abs(P20 - P-placebo) < 5 | P20 - P-placebo < 5 on hidden-header tasks |
| H12 | Repair is easier than building (case 3; added 2026-10-24 before any repair run) | 8 mutations x 6 base configs, Beam high; repair pass rate on v2 vs case-1 pass rate on the same v2 configs | repair >= build + 10 points; `add_hidden_header` and `rename_hidden_header` are the hardest mutations | repair <= build |
| H13 | Repair depends on error detail (case 3; added 2026-10-24 before any repair run) | `terse` vs the same 24 base x mutation pairs in `core`, Beam high | terse >= 15 points below verbose on mutations that raise errors (header, auth, etag, expiry); < 5 points difference on silent ones (paging, envelope) | difference < 5 points on raising mutations |
| H14 | Effort has diminishing returns on repair (case 3) | core 48, efforts low/medium/high/xhigh, 3 runs | pass rate rises from low to high; xhigh - high < 3 points while xhigh output tokens >= 1.5x high | xhigh - high >= 10 points, or no rise from low to high |
| H15 | Two changes are harder than one, beyond independence (case 3) | compound 24 vs the single-change rates of their two mutations on the same bases, Beam high | share of compound tasks fully repaired (pass_rate = 1.0) is >= 10 points below the product of the two single-change full-repair shares (same bases) | compound >= product |
| H9 | Reliability is lower than single-run success | pass^3 (fresh mock seeds x runs) vs pass@1 on Tier B | pass^3 >= 10 points below pass@1 | gap < 3 points |

### Case 3 v3 pilot and evaluation plan (written 2026-10-24, before any v3 run)
- Pilot: about 40 tasks on dev seeds 1001+ (`data/heal_v3_pilot.jsonl`), Beam `low` and `high`, 1 run. Pilot results are used only to choose families, never reported as evaluation results.
- Keep rule per family (fixed now): keep if Beam `low` pass rate < 0.90, or `high` - `low` >= 0.10. A family that fails both is reported as "solved at low effort" and leaves the evaluation suite.
- Against selection bias (R5 review): (1) every kept family must pass all validity gates (reference 1.0, necessity of each change, minimum fix size); (2) for every failed pilot task, one person checks by hand that the visible files make the fix possible; a task that fails this check is a task bug, fixed or labelled `no`, not a hard task; (3) when `HF_TOKEN` is available, one open model of another family runs the pilot too, and the kept set is reported for both models.
- Evaluation suite: built on fresh seeds 2001+ from the kept families, listed in this file (H16+) before its first run. No evaluation task is dropped because of a model result.

### Case 3 v3 evaluation suite (frozen 2026-10-24, before its first run)
- Tasks: 64, built by `uv run python scripts/build_heal_v3.py --suite eval --out runs/heal_v3_eval --seed 2` (fresh spec seeds 2001-2064, no overlap with the pilot). Manifest `data/heal_v3_eval.jsonl`, sha256 `5a90a8dfb5d3c932dd5d31a422be2bed380c8a0b3b8d0faa940b25e5bcbc2e27`. All 64 passed every gate (v1 on v1 1.0, v1 on v2 < 1.0, reference 1.0, necessity of each change, fix size, >= 15 tests, leak check, recording shows each change); none were dropped.
- Families: single 14 (7 kinds x 2), release3 10, release4 8, partial 8, sdk 8, misleading 8, terse 8. Domains 12-13 each; auth modes 21-22 each; identifiable `yes` 56, `generalize` 8. Each of the 10 changes appears 15-18 times.
- Arms (factor): `base` (as built) and `hint` (`scripts/add_contract_hint.py`: the same tasks with the pilot's contract paragraph in `goal.md`; all other files byte-identical). 
- Efforts: low, high, medium, xhigh (in this order), Beam, 1 run per cell (3 runs do not fit the output allowance; the arm and effort contrasts are paired by task instead). If xhigh cannot finish by 2026-10-30, xhigh is reported on the tasks it finished, and its contrasts use only those tasks (paired).
- Primary metric: `pass_rate` (share of the 17 hidden tests). Secondary: `strict_pass_rate`, full repair (pass_rate = 1.0), output tokens per task, and per-change credit (pass share over the tests whose `needs` list holds the change; scoring code to be added before analysis, with no change to pass rates).

| ID | Hypothesis | Test | Supported if | Rejected if |
|---|---|---|---|---|
| H16 | Beam fails to notice a silent interface break more than it fails to fix it | hint - base, paired by task, at each effort | >= 20 points at low and at high, CI excludes 0 | < 5 points at low and at high |
| H17 | Effort does not fix noticing | (high - low) in the base arm | < 5 points, while high output >= 3x low | >= 10 points |
| H18 | Effort has diminishing returns on repair (H14 on v3; per arm) | low, medium, high, xhigh pass rates and output tokens | xhigh - high < 3 points while xhigh output >= 1.5x high | xhigh - high >= 10 points |
| H19 | More changes per release are harder (H15 on v3) | full-repair share by changes: single (1) vs release3 (3) vs release4 (4), hint arm, high | strictly falls with the number of changes, release4 >= 15 points below single | release4 >= single |
| H20 | Missing or wrong side information costs more than more code (k = 3 families, hint arm, high) | partial, misleading, terse vs release3 | each of partial and misleading >= 10 points below release3 | both within 3 points of release3 |
| H21 | A layered SDK client is harder to repair than a compact one (k = 3) | sdk vs release3, both arms, high | sdk >= 10 points below release3 in both arms | sdk >= release3 in both arms |

- Saturation rule (added 2026-10-24 after the low-effort steps, before any high, medium or xhigh result): if Beam's base arm reaches >= 0.85 at high or xhigh, or the hint arm reaches >= 0.95 at xhigh, the suite is reported as near saturation for Beam. This suite is then not changed; a separate hard split may be added and reported apart. The low-effort results (base 0.316, hint 0.716) do not trigger the rule.

### Case 1 Tier B effort curve (added 2026-10-24, before these runs)
- The same 50 Tier B tasks as `results/tierb50_v1.csv` (`runs/tierb50`, unchanged), Beam medium and xhigh, 1 run, run after the case 3 v3 suite. Graded with the current grader (the saved low/high clients regrade identically). Together with run 1 (low 0.571, high 0.780) this gives the four-point effort curve for case 1. Test as H18: xhigh - high < 3 points while xhigh output >= 1.5x high supports diminishing returns; xhigh - high >= 10 points rejects it.

### Case 3 controls and repeat runs; H10 run (added 2026-10-26, before any of these runs)
Reason: an adversarial review of the design (2026-10-26) found that the `hint` arm adds information (a list of change types, and "translate in both directions"), not only a warning. So hint - base (H16) does not by itself show that Beam fails to *notice* the break. These controls test that. Written after the low/medium/high results of `base` and `hint` were known; no result of the arms below was known.
- New arms, the same 64 tasks, Beam `low`, 1 run; each built by `scripts/add_contract_hint.py --variant <v>` (one paragraph after the same anchor in `goal.md`; all other files byte-identical):
  - `placebo`: a paragraph of similar length with no information about the API change ("work carefully, read every file, keep changes small, keep the method names and arguments").
  - `notice`: only the warning and the requirement: "the new API may also have changed its wire format, without raising errors. Your methods must still return exactly what `client_v1.py` returns." No list of change types, no request-side instruction.
- Repeat runs: `base` and `hint` at `low`, a second run (r2), to measure run-to-run variation.
- Analysis: `scripts/analyze_heal_controls.py`. Paired by task, 95% CI by paired bootstrap over tasks (10,000 resamples, seed 20261026). Verdicts are decided by these rules, on the CI where stated.

| ID | Hypothesis | Test | Supported if | Rejected if |
|---|---|---|---|---|
| H22 | The hint effect is not a generic prompt effect | placebo - base and hint - placebo, low | placebo - base < 5 points, and hint - placebo >= 20 points with CI excluding 0 | placebo - base >= 20 points |
| H23 | A warning alone (no change list, no translation rule) gives a large part of the hint effect | notice - base, low | notice - base >= 20 points, CI excluding 0 | notice - base < 5 points and its CI upper end < 10 points |

- Reading rule: if H23 is rejected, the H16 effect is attributed to the information in the hint, and the words "fails to notice" are not used in any claim. If H23 is supported, the claim is "a warning alone recovers X of the Y points". hint - notice is reported as the value of the extra detail.
- Repeat runs (descriptive, no hypothesis): r2 - r1 with CI, flip rate of full repair, mean absolute per-task difference, pass^2 vs pass@1. If mean |r2 - r1| per task is above 5 points, single-run differences below that size are reported as within noise.
- H10 (registered 2026-10-24) is run now: the 25 hidden-header Tier B tasks, arms `__hint` and `__evidence` (`scripts/make_header_controls.py`, `runs/tierb50_header`), Beam `high`, **1 run** (H10 says 2; the output allowance allows 1). Arm (a) "as built" is run 1 of `results/tierb50_v1.csv` at high. Verdict rule as in H10.
- Run order: the rest of `base` xhigh (10 tasks), then placebo, notice, base r2, hint r2, H10; then `hint` xhigh and Tier B xhigh.

## Analysis
- Unit = task. Per-task score = mean pass rate over 3 runs.
- Differences between systems are paired by task. 95% CI by paired bootstrap over tasks (10,000 resamples).
- Feature effects (H1, H4): difference of means between groups, bootstrap CI; plus one logistic regression of test pass on features, as a check only.
- No task is dropped after runs start, except tasks where the reference client fails (reported).

## Known threat: memorisation
Tier A uses public APIs that models may have seen in training. Controls:
1. Tier B APIs are new; report Tier A and Tier B separately.
2. Credentials, data and hosts differ at test time, so a memorised client still must read the HAR for auth placement and base path.
3. If time allows: a "renamed" variant of 20 Tier A tasks (path segments and param names replaced) to measure the drop.

## Deviations
- 2026-10-24, after a 6-call smoke run (3 pilot tasks x low/high; not part of the analysis set):
  - Harness bug: `datagen` gave a string for an array param with an array-level `enum`; tests contradicted `goal.md`. Fixed; build now checks test args against the goal's types.
  - `goal.md` Rules gain one line: `client.py` must import everything it uses. Reason: a client failed on a missing `from typing import Any` that the goal's own signatures introduced.
  - Smoke-run results are discarded; all pilot tasks are rebuilt before the real runs.

- 2026-10-24, after the 30-task Tier A run (`set30_v1`, 30 tasks x low/high, 1 run): two grading rules were too strict and were relaxed; saved clients were regraded with no new model calls.
  - An extra query param whose value is the given credential is ignored (APIs with security `none` but an optional `apikey`/`key` param).
  - When the test passes no `body`, an empty `{}` body is accepted.
  - Result after regrade: low 0.963 (28/30 tasks perfect), high 0.996 (29/30). Real errors: base path added twice (1 task, low only); a path taken from memory of the real API instead of the HAR (GoDaddy, both efforts).
  - Decision: Tier A alone does not separate systems. Add Tier B (stateful, login flows, pagination, dependent calls, hidden headers), a "renamed" memorisation control for Tier A, and large HARs. Tier A stays as the easy baseline. H1-H5 are unchanged.

- 2026-10-24, partial H6/H7 run (`set30v_high`, Beam high, 96 of 150 cells before the daily limit):
  - Design flaw found: the `reworded` variant gives new names without argument descriptions. For APIs with many similar parameters (Google standard params `key`, `oauth_token`, `access_token`, ...), the goal-to-wire mapping is then not identifiable from the HAR (recorded values never appear in the goal). `opaque` has descriptions and scored higher (-0.04 vs -0.14 vs exact). Fix: `reworded` gets the same per-argument descriptions as `opaque`, so name overlap is the only difference between the two. The old `reworded` results are kept as "names only" and reported separately, not used for H6.
  - Confound found: closed-book success (0.495) mixes memory with guessing from the goal (exact names often equal wire names; auth `none` needs no guess). Added control: closed-book on the renamed twin (`twin + closed`), where memory cannot help. H7's memory estimate = closed-book(original) - closed-book(twin).

- 2026-10-24, code review before the case 3 runs (no model calls; all saved grades unchanged: 160 saved clients regraded before and after, identical per test; Tier B reference 1.0 and empty 0.0 on 50/50):
  - Grader: a successful write that the test did not ask for (e.g. an update that also creates an item) now fails as `state_mismatch`. An empty list for a query argument now expects no parameter on the wire. Neither occurs in saved results.
  - Runner: the client's process group is killed on every exit; stderr is capped (20 MB) and stdout discarded; a lingering thread no longer causes a false timeout.
  - Memorisation twin: the API key header or query name is now renamed too (it named the real API, e.g. `hapikey`). Affects 3 twins on rebuild (`goog.io`, `hubapi.com_crm`, `microsoft.com_cognitiveservices-ComputerVision`); their saved twin results are kept but flagged, and the twins are rebuilt before the remaining H7 runs.
  - Case 3 `failure_only` tasks: the system prompt and `goal.md` no longer mention a recording that the task does not have. The 24 `evfail` tasks were rebuilt before any run of them.
  - Model-call errors (`api_error`) are retried and excluded from means, with their count reported; they are not model failures. RL training splits exclude `identifiable: no` repair tasks; evaluation keeps them as a guessing probe.

- 2026-10-24, first case 3 runs (Beam, `752d0e6`): the v2 core set is saturated. Low effort: 48/48 tasks at pass rate 1.0 (mean output 1,082 tokens). Medium: 11/11 finished cells at 1.0 before we stopped it. The harder v2 sets: 3/3 finished compound cells at 1.0 at low. H14 (effort curve) cannot be tested on the v2 sets: there is no headroom. Decision: stop the v2 effort sweep, keep these results as a finding ("single, loud API changes are repaired at low effort"), and build v3 hard tasks (`DESIGN-CASE3-HARD.md`): silent wire drift behind a stable interface, protocol changes, partial evidence, 3-4 change releases, SDK-style clients, misleading changelogs. v3 families are kept or dropped by a rule fixed before the pilot (Beam low < 0.9, or high - low >= 0.10) on dev seeds; the evaluation suite is built on fresh seeds and pre-registered before its first run. H12-H15 move to the v3 suite.

- 2026-10-24, case 3 v3 pilot (38 tasks, dev seeds; not evaluation data). Base: low 48.0%, high 49.8% (output 1,490 vs 7,548 tokens). Hand check of failures: the main failure is that Beam handles protocol changes but returns the new wire format (e.g. `deposit_cents`) instead of the v1 return shape that `goal.md` requires; its reasoning says "Should we transform deposit? No." To separate "not noticing the contract" from "cannot translate", the same 38 tasks ran with one added `goal.md` paragraph (the "contract hint": the wire format may have changed; return what client_v1 returns; translate both ways): low 85.1%, high 90.4%. Most of the base gap is noticing, not doing. Decision (2026-10-24, before the evaluation suite): both arms run as a factor ("noticing" vs "doing"). The keep rule is applied to the pilot as built (the base arm): all 7 families stay (every family has low < 0.90). Under the hint arm `single` (low 0.98) and `sdk` (low 0.91, high < low) would count as solved; they stay in the suite and are reported per arm.

- 2026-10-24, before any high, medium or xhigh run: run order changed. Low, high and medium are the core sweep for both cases (case 3 v3 first, then case 1 Tier B medium). xhigh becomes an optional extra that runs only after the core sweep, for case 3 and then Tier B. Reason: xhigh costs about as much output as the other three efforts together, and the core results must be ready before the 31 Oct release. If xhigh does not finish, H18 (both arms) and the Tier B xhigh test are reported as not tested; all other hypotheses are unaffected. No task, grader or threshold changes.

- 2026-10-26, entries missing until now (found by a review of this log, no effect on any number):
  - Case 1 (Tier A and Tier B) was run with 1 run per task, not the 3 runs that "Analysis" states, because of the daily output allowance. All case 1 CIs are over tasks only and do not include run-to-run variation. The Tier B per-switch analysis (`results/analysis/tierb50_v1/`) was not pre-registered; it is exploratory.
  - H16 and H17 were written after the v3 pilot, which had shown a hint effect (+37 points at low) and a small effort effect in the base arm (+1.8). They are confirmatory only in the sense that they were tested on fresh seeds (2001-2064). The suite was frozen and the first run was started within seconds (commit `b31739c`).
  - The v3 verdict script (`scripts/heal_v3_verdicts.py`) decides H17-H21 on point estimates; only H16 also uses the CI. The CIs are given beside every verdict.

- 2026-10-26, local Case 2 preparation before any local Beam calls: fixed Windows
  runner cleanup (Unix-only `killpg` -> Windows Job Object on Windows) and switched
  server/request-window timestamps to the same cross-process `perf_counter`
  clock. The coarse Windows wall clock produced false `no_request` failures on
  correct clients. Temp-directory cleanup now retries Windows sharing violations,
  because a killed child can hold its inherited stderr or cwd handle briefly
  after `proc.wait()` returns. Reference validation and synthetic smoke runs are
  repeated after these fixes. No saved model results have been changed in this
  checkout. The pre-registered high-effort Case 2 comparison remains primary; an
  exploratory low/medium/high/xhigh/max sweep is staged after it, three runs per
  cell by default. Paired comparisons use complete task/run/effort blocks (all
  three arms for the same cell), so a missing or infrastructure-error cell in an
  exploratory effort does not discard the same task/run from the high-effort
  comparison. The frontier plot uses blocks complete at every plotted effort;
  an observed effort with no complete block is omitted from the frontier.
  Paired effects use per-task means and a task bootstrap. This is evaluation,
  not RL training.

- 2026-10-27, xhigh dropped: all results are reported at three effort levels (low, medium, high). Reason: one effort scale for every case, and the daily output allowance before the release. The decision was made after the case 3 `base` xhigh step had finished and its mean was known. `hint` xhigh was stopped by the daily allowance after 33 of 64 tasks; Tier B xhigh never ran. As the 2026-10-24 run-order entry allows, H18 (both arms) and the Tier B xhigh test are reported as not tested. The xhigh clauses of the saturation rule are dropped; the rule is tested on `base`, high only. So that no result is hidden, the one finished xhigh step: `base`, xhigh, 64/64 tasks, pass rate 48.4% (high 42.7%; xhigh - high +5.7 points, 95% CI -4.5 to +16.2), full repair 35.9%, mean output 10,400 tokens. The partial `hint` xhigh step is not analysed. Raw cells: `runs/batch/v3eval_base_xhigh/`, `runs/batch/v3eval_hint_xhigh/` (not in the release).

- 2026-10-27, before any of these runs: two more runs (r2, r3) at high for case 3 `base` and `hint` (64 tasks each) and Tier B as built (50 tasks), to meet the 3-run rule of "Analysis" at the headline effort. Order: all r2 steps, then all r3 steps. Same solver, grader code, model and endpoint as run 1 (frozen checkout `97a4585`). Analysis: at high, the per-task score is the mean over the runs that finished for every task (3, or 2 if r3 does not finish by 31 Oct); low keeps 2 runs (case 3) or 1 (Tier B), medium 1. H16, H17 and the Tier B effort curve are recomputed on these per-task means; the 1-run numbers stay in the record. Thresholds, tasks and graders are unchanged. Run-to-run spread at high (mean absolute per-task difference, full-repair flip rate) is reported as in the low-effort repeat.
- 2026-10-28, before any of these cells: case 2 main evaluation, the 30 tasks not yet run (`tierb_20`–`tierb_49`; 270 cells: placebo, P5, P20 × low, medium, high × 1 run). Same protocol (`submission-tool-all-arms-v1`) and code as the first 20 tasks: the frozen `results/case2_probe/runtime_source.zip` (archive SHA256 and all 401 file hashes in `reproducibility.json` checked equal), run on a second machine. Only the launcher differs: it resumes after the provider's daily limit resets, and runs at concurrency 4 instead of 2 (no effect on a cell). The task set is a copy of the 30 frozen task directories, so the output directory is new; rows are joined with the first 20 tasks by task. No protocol change: the first 20 tasks show every placebo cell submitted on turn 1, so the 1-turn placebo cap did not bind. Analysis and thresholds as already written; the 20-task numbers stay in the record.
- 2026-10-29, after the case 2 run: all 450 cells done (`results/case2_probe/main_full/`, 50/50 complete blocks at each effort; 449 `ok`, 1 placebo `no_code` where the `submit_client` call ran to the 65,536-token output cap). The 7 cells of `tierb_20` from the first machine are not in its committed CSVs; the complete `tierb_20` block from the second machine is used. H11 at high, as written: hidden-header P20 − placebo +64.0 [40.0, 84.0] (≥ 20: met); header-off |P20 − placebo| 32.6 [16.0, 50.3] (< 5: not met); the falsification condition (header-on gain < 5) is not met. Verdict: not supported, by the header-off control. A post-hoc check, not pre-registered, is in `results/case2_probe/main_full/SENSITIVITY.md`: many failing placebo clients end with a stray `"""`; removing it (tasks 20-49 only) roughly halves the header-off gain, which stays above 5.
- 2026-10-31, after the case 2 main results (exploratory extension, protocol `case2-ext-v1`; no pre-registered hypothesis is tested): three extra sets of Beam runs on the same 50 Tier B tasks, with the same prompts, tools, grader and endpoint as the main run. Only new probe budgets were added (`results/case2_probe/ext/PROTOCOL.md`). (1) P5 at high effort, runs 2 and 3, to check how stable the high-effort P5 headline is across runs (`ext/R1_P5_high_r2r3/`). (2) New arms with 1 and 2 probes at high effort, 1 run, to draw a probe-budget curve together with the placebo (0 probes) and P5 (`ext/R2_P1P2_high/`). (3) P5 at effort `max`, 1 run, to see whether more reasoning changes the result (`ext/R3_P5_max/`; reported only if all 50 tasks finish). These runs were chosen after the main results were known, so they are exploratory: no threshold, verdict or hypothesis (H11 included) is changed by them. The main run stays the pre-registered record. P5 at high effort in the model comparison is now the mean of 3 runs (run 1 of the main run and the two extra runs), as is already done for Beam at high effort on Cases 1 and 3; its pass rate stays 95.7% and the interval narrows. `api_error` cells are excluded; every (task, arm, run) has exactly one scored row. Analysis: `scripts/analyze_probing.py` (`results/analysis/probing/`).
