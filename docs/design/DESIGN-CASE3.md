> Superseded by DESIGN-CASE3-HARD.md (v3); kept for the record.

# Case 3 v2: self-healing client task suite, effort sweep, RL

Extends `DESIGN-CASES.md` "Case 3" (the 48-task core set, `runs/heal48`).

## Why v2 (weaknesses of the core set)
1. **The error message often names the fix.** In 4 of 8 mutations the practice server's error says what to do (`header X-Client-Version is required`; `If-Match header is required; GET the item for its ETag`; `the session expired, log in again`). Those tasks measure instruction following, not diagnosis. Real APIs often answer a bare `400 Bad Request`.
2. **Two evidence channels are always both present** (failure output + new recording). We cannot tell which one the agent uses.
3. **One change at a time.** Real API releases often change several things.
4. **Only pass rate.** A repair can fix the change and break something that worked. We do not measure that yet.
5. **Patch size is computed but not reported.**

## Task factors
| Factor | Levels | How |
|---|---|---|
| `error_detail` | `verbose` (today), `terse` | `terse`: the engine answers with the status code and a generic body only, e.g. `{"error": "bad_request"}`, `{"error": "unauthorized"}`, `{"error": "precondition_failed"}`, `{"error": "token_invalid"}`. Same status codes. Applies to the practice server (what `failure.txt` shows), the new recording, and the grading server. Engine option, default `verbose` (existing tasks unchanged). |
| `evidence` | `both` (today), `failure_only`, `recording_only` | which visible files exist besides `goal.md` and `client_v1.py` |
| `changes` | 1 (today), 2 | two mutations applied together; only compatible pairs (no two mutations on the same switch) |

## Task suite (`runs/heal_suite/`, 144 tasks)
| Set | Tasks | Factors |
|---|---|---|
| `core` | 48 = 8 mutations x 6 bases (the existing `heal48`) | verbose, both, 1 change |
| `terse` | 24 = 8 mutations x 3 bases (the first 3 bases of each mutation in `data/heal_configs.jsonl`) | terse, both, 1 |
| `evidence` | 48 = the same 24 pairs x {failure_only, recording_only} | verbose, 1 |
| `compound` | 24 = 12 compatible mutation pairs x 2 bases | verbose, both, 2 |
Each set has a manifest row per task: set, mutation(s), base config, factors, seeds.

## Validity gates (every task)
- `client_v1.py` scores 1.0 on v1 and < 1.0 on v2; a no-op patch fails; the v2 reference client scores 1.0.
- `terse` tasks: no visible file contains the fix wording (header name in an error message, "If-Match", "log in again"); the header name may still appear in the recording (that is evidence, not a hint).
- `failure_only` tasks still contain enough to repair in principle? Not always (e.g. the hidden header VALUE is only in the recording). Label each task `identifiable: yes/no` automatically from the mutation x evidence table and report results split by it.
- No hidden data in visible files (leak check).

## Metrics per attempt
- `pass_rate` and `strict_pass_rate` on v2 (primary: `pass_rate`).
- `regression_rate`: share of hidden tests that `client_v1.py` passed on v2 but the patched client fails (broke what worked).
- `patch_lines`, `patch_ast_nodes` (vs `client_v1.py`); reported, never rewarded in evaluation.
- Tokens (prompt, completion, reasoning) and wall time.

## Effort sweep (Beam)
Efforts `low`, `medium`, `high`, `xhigh` (Beam also has `max`, its default; not in the sweep).
| Phase | Tasks | Efforts | Runs | Solves |
|---|---|---|---|---|
| 1 | core 48 | 4 | 1 | 192 |
| 2 | core 48 | 4 | +2 | 384 |
| 3 | terse + evidence + compound (96) | low, high | 2 | 384 |

## RL environment
`RepairEnv` (new, `src/har2client/env/repair_env.py`):
- `reset()` -> `{task_id, goal, client_v1, failure (or None), observation (or None), observation_format}`; `step({"type": "submit", "code"})` -> reward = `pass_rate` on a fresh v2 grading instance (same penalties as case 1); `info` adds `regression_rate`, patch size.
- Curriculum: R0 (1 change, verbose, both) -> R1 (1 change, terse or one evidence channel) -> R2 (2 changes, verbose) -> R3 (2 changes, terse, one channel). Adaptive promotion as in `DESIGN-RL.md`.
- Splits: `train` = 6 mutations; `test_mutation` = held-out `rename_hidden_header` and `none_to_link_header`; `test_pair` = held-out compound pairs. Committed in `data/heal_splits.json`.
- TRL: same `reward_fn` contract as case 1 (task spec carries the heal factors).

## Build notes (suite as built)
- Build: `uv run python scripts/build_heal.py --suite --out runs/heal_suite --seed 1` (writes `data/heal_suite.jsonl`; `--sets` picks sets). Code: `src/har2client/heal/suite.py` (rows, manifest), `identify.py` (label), `build.py` (`build_heal_task(row, out, error_detail=, evidence=, task_id=)`; `row["mutations"]` has 1 or 2 names).
- Task names: `heal_<i>_<mutation>_<domain>` (core = the `heal48` names), `terse_<i>_...`, `evfail_<i>_...` (`failure_only`), `evrec_<i>_...` (`recording_only`), `compound_<j>_<a>+<b>_<domain>`. `<i>` of terse and evidence tasks is the core row index.
- Paired design: the variants of one core row share spec seed, credentials, data, hidden tests and `client_v1.py` (same `build_seed`), so terse and single-channel tasks differ from the core task in the studied factor only. The core set rebuilds `heal48` byte for byte in every visible and hidden file; `task.json` differs only by the new `heal` keys (`set`, `mutations`, `error_detail`, `evidence`, `identifiable`) and by the random port inside `heal.failure.detail`.
- `error_detail=terse`: engine body `{"error": <generic code>}` and the same status; the code is chosen by the verbose code: `bad_request` (missing hidden header, bad body, bad page), `unauthorized` (no API key or session, bad login), `token_invalid` (expired session), `forbidden` (CSRF), `precondition_required` (428), `precondition_failed` (412), `not_found`. It is stored as `error_detail` in `engine.json`, so the practice server, the recording and the grader all use it. Headers (`WWW-Authenticate`, `Link`, `ETag`) are unchanged.
- Compound pairs (12, each mutation in 3; two bases each): `add_hidden_header` with `offset_to_cursor`, `add_etag`, `add_token_expiry`; `rename_hidden_header` with `none_to_link_header`, `add_envelope`, `apikey_to_bearer_login`; `apikey_to_bearer_login` with `add_etag`, `none_to_link_header`; `offset_to_cursor` with `add_envelope`, `add_token_expiry`; `none_to_link_header` with `add_etag`; `add_envelope` with `add_token_expiry`. A pair is compatible when the two mutations edit different switches and both apply to one v2 config (so `apikey_to_bearer_login` + `add_token_expiry`, and the two header or two paging mutations, are out).
- Solver: a task without `session.har` (`failure_only`) gets a prompt without the "Recorded session" section; a task without `failure.txt` has no failure section. Case 1 prompts are unchanged (byte-identical, tested). A repair task without a recording gets its own system prompt (`NO_RECORDING_SYSTEM_PROMPT`: `client_v1.py` and `failure.txt` only, no HAR wording), and its `goal.md` says "in the failure output" (fixed 2026-10-24, before any `failure_only` run; the 24 `evfail` tasks were rebuilt).
- `regression_rate` (scripts/score_heal.py) = tests that `client_v1.py` passes on v2 and the patched client fails, divided by all hidden tests; `regression_count` and `v1_pass_count` are in the CSV, so the share among the tests v1 passes is `regression_count / v1_pass_count`.

## Identifiable label
`identifiable` (yes/no) says whether the repair can be worked out from the visible files at all. It is set by rule from mutation x evidence (`heal/identify.py`) and does not depend on `error_detail` (terse errors keep the status codes the standard facts rest on).

A fix needs facts. Standard facts follow from a status code or a header that the failure output shows (428 -> send `If-Match` with the `ETag` of a GET; 401 after some calls -> log in again; `Link: rel="next"`; the `{data, meta}` wrapper). Arbitrary facts appear only in the new recording: the failure output shows the failing call and its response, which holds neither the value of the hidden header, nor the login call, nor the cursor parameter (the first list page is cut before its next-cursor key).

| Mutation | Arbitrary fact needed | `both` | `recording_only` | `failure_only` |
|---|---|---|---|---|
| `add_hidden_header` | header value | yes | yes | **no** |
| `rename_hidden_header` | new header name and value | yes | yes | **no** |
| `apikey_to_bearer_login` | login path, body fields, token field | yes | yes | **no** |
| `offset_to_cursor` | cursor parameter name | yes | yes | **no** |
| `none_to_link_header` | none (Link header shown) | yes | yes | yes |
| `add_envelope` | none (wrapper shown) | yes | yes | yes |
| `add_etag` | none (428 and `ETag` convention) | yes | yes | yes |
| `add_token_expiry` | none (401, login call is in `client_v1.py`) | yes | yes | yes |

Two changes: identifiable only when both are. In the suite 12 of 144 tasks are `identifiable: no` (the `failure_only` tasks of the 4 mutations above, 3 bases each); all other 132 are yes. A test checks the table against the real `failure.txt` and `session.har` of the built tasks. Report results split by this label: a `no` task measures guessing, not repair.
