# RL environment design: observed traffic -> working API client

Builds on Tier B ([DESIGN-TIERB.md](DESIGN-TIERB.md)) and the grader ([DESIGN.md](DESIGN.md)).
Scope of the first release: **two sources (`browser_har`, `mobile_har`), levels L0-L3, splits `train`, `test_combo`, `test_domain`, no probe actions.** What is built is described below; what is not built is marked "future work".

## Framing
The skill is general: **given observed traffic of an API (from any vantage point) and a goal, write a client that works on a fresh instance.** The browser HAR is one vantage point. Others are common in practice:

| Source | Where it comes from in real life | What it shows | What it hides / adds | Status |
|---|---|---|---|---|
| `browser_har` | Chrome/Firefox DevTools export | full requests, responses, browser headers | browser noise (CDN, analytics, fonts), CORS/sec-fetch headers | built (existing output, byte-identical) |
| `mobile_har` | phone app traffic through a proxy (mitmproxy/Charles) | full requests, responses | app headers (device id, app version, okhttp / CFNetwork user agent), mobile SDK noise (crash reporting, attribution, push, remote config, analytics), no browser headers | built |
| `raw_http` | mitmproxy/tcpdump text dump of HTTP/1.1 | raw request/response text | no JSON structure; parsing is part of the task | future work |
| `curl_transcript` | a developer's terminal history (`curl -v` with output) | commands + verbose output | fewer calls, human-chosen; typos and failed attempts | future work |
| `otel_spans` | OpenTelemetry/APM HTTP spans between services | method, URL, status, selected headers, bodies only when captured | partial observability: some bodies missing, other services' spans as noise | future work |

One canonical session (list of exchanges) is rendered into any source, so **the same task can be shown through different vantage points**: credentials, seeds and hidden tests do not depend on the source. This gives a research axis: how observability changes success, and links to identifiability (H8).

Code: `src/har2client/env/sources/`. `canon.py` holds `Exchange` and `Session` (API-specific headers only; each renderer adds its own vantage-point headers) and the shared noise helpers. A source is a module with `render(session, rng) -> str` plus one entry in `SOURCES` (`__init__.py`); adding one needs no change elsewhere. API calls and noise go through one header profile per source, so the format does not separate them (same idea as `browser.py`): on `mobile_har` every call has the same base headers, the app headers sit on API calls, first-party noise and about half of the SDK noise, and SDK noise carries its own auth headers.

## Builder hooks
`build_tierb_task(row, out, *, noise, source="browser_har", compact=False, ...)` writes the observation as `session.<ext>` (`.har` for both current sources). `goal.md` names the file and says what it is. For the default source the task directory is byte-identical to before (golden digests in `tests/fixtures/golden_tierb/`); `task.json` gets `source` and `observation_file` only for other sources and `compact` only when set. The solver (`solve/beam.py`) reads `goal.md` and the single visible `session.*` file (`find_session_file`), still never the hidden files (`ref_client.py` is now on that list).

## Environment API (`src/har2client/env/env.py`)
```python
env = TrafficToClientEnv(source="browser_har", tier="B", split="train", curriculum="adaptive",
                         probe_budget=0, seed=0)          # source="auto": the sources of the split
obs = env.reset()            # {"task_id", "goal", "observation", "observation_format", "probe_budget", "source"}
out = env.step({"type": "submit", "code": client_py_text})
#   -> {"reward", "done": True, "info": {pass_rate, failure_counts, min_pass_rate_k, flags, milestones, ...}}
```
- Episode = observe -> one submit. `probe_budget=0` is the single-turn (bandit) setting used for GRPO.
- `curriculum`: `"adaptive"`, `"uniform"`, or a fixed level (`"L2"`, `"fixed:2"`, `2`). `compact=True` renders short observations.
- **Probe actions (built, case 2).** `{"type": "probe", "request": {...}}` goes to a practice instance (own seed, data and credentials; never the grading one). `reset()` returns `obs["practice"]` with the practice URL, credentials and budget. Each sent probe gives reward -0.01 (RL only); refused probes cost 0. Details: `DESIGN-CASES.md` and `src/har2client/env/probe.py`.

## Reward (`env/reward.py`)
- Primary: `pass_rate` of the Tier B semantic grader on a fresh grading instance, through `grade.grade` (grading rules unchanged). Option `reliability_k`: grade on k fresh grading instances (new test seed, credentials and tests; `tasks.grading_variants`) and take the mean, or the min with `reliability_agg="min"` (pass^k). `min_pass_rate_k` is the min over the k grades (equal to `pass_rate` for k = 1). (Not to be confused with the grader's `strict_pass_rate`, which fails tests with extra unrequested calls.)
- Gates/penalties (default on): `hardcoded_secret` or `hardcoded_host` -> reward x 0.5; any invented endpoint call -> -0.1; code that does not parse or has no top-level `class Client` -> 0.
- Shaping (default off, `RewardConfig(milestones=True)`): `+ 0.2 x share of applicable milestones x (1 - reward)` computed from the grading log (the engine's request log and the per-test results). Milestones: logged in, sent the hidden header, fetched a second page, sent If-Match, re-logged in after 401, unwrapped envelope. **Changed from the first draft** (`+0.2 x share`): the factor `(1 - reward)` keeps an imperfect client below 1.0 (the plain sum lifted a client with 6 of 7 tests passing to the cap).
- Reward is deterministic for a fixed (task, code). Grading runs in a pool of worker processes (`GradePool`, spawn context); `info["grade_time_s"]` and `GradePool.median_grade_time()` report the grade time (median about 0.2-0.3 s per grade).
- The milestone log is taken by wrapping the engine class inside the grader module while a grade runs in a worker (no change to `grader.py`). If a future grader returns its log, drop the wrapper.

## Task generation and curriculum
- A task = a spec dict `{domain, config, seed, source, level, noise, build_seed, compact}`, built on first use into a cache dir (`HAR2CLIENT_RL_CACHE`, default a temp dir) with the Tier B builder, keyed by a hash of the spec. The reference client must score 1.0 on every build; this is the solvability proof.
- Levels (`env/curriculum.py`): L0 no switches, noise 0; L1 one switch, noise 0.15; L2 two switches, noise 0.3; L3 three or more switches, noise 0.7. (L1 noise was not in the first draft. **L4 is dropped**: it needed `otel_spans`.)
- `adaptive`: promote when the mean reward over a full window of 64 episodes at the level is >= 0.7; demote when <= 0.2; the window restarts after a move.
- Prompt length control: `noise`, and `compact` rendering = no noise, every JSON list in a response body cut to 3 elements (cursors and links stay), minified JSON. Prompt size in tokens (chars/4), median over 8 tasks, system + user prompt:

| source | level | full | compact |
|---|---|---|---|
| browser_har | L0 / L1 / L2 / L3 | 9.5K / 14.2K / 17.3K / 35.5K | 4.4K / 6.7K / 7.1K / 7.3K |
| mobile_har | L0 / L1 / L2 / L3 | 8.4K / 12.1K / 15.1K / 29.9K | 3.8K / 5.7K / 6.2K / 6.2K |

The target "<= 8K tokens at L0-L2" holds with `compact` (max seen 8.4K, browser L2); `scripts/make_rl_dataset.py` is compact by default.

## Splits (`env/splits.py`, `data/rl_splits.json`)
- `train`: 4 domains x all switch combinations except the held-out ones x sources `browser_har`, `mobile_har`.
- `test_combo`: only held-out combinations: (`cookie_csrf` x `link_header` x `etag_update` on) and (`bearer_login` x `cursor` x `envelope` on); each part also occurs in train. Level 3 only.
- `test_domain`: held-out 5th domain (`samples`), train-style combinations.
- `test_source` (held-out sources): future work, needs more sources.
Membership, the base seed and 60 fixed evaluation tasks per test split are committed; train specs are sampled deterministically by seed and never use an evaluation seed. `python -m har2client.env.splits` rewrites the file; a test checks that it matches the code.

## Integrations
- **TRL GRPO** (`env/trl.py`): `reward_fn(prompts, completions, task_spec, **kwargs) -> list[float]`, a plain function (TRL is not imported). Signature checked against the TRL GRPO docs: TRL passes `prompts`, `completions` (strings, or lists of messages in conversational format), `completion_ids`, `trainer_state`, and every other dataset column by name, so the column `task_spec` arrives as a list of specs; extra kwargs are ignored. It extracts the last ```python block (0 when there is none), builds or fetches the task from the cache, and grades all completions in the pool. `make_reward_fn(config, cache_dir, pool)` builds one with another `RewardConfig`. Set `remove_unused_columns=False`.
- `scripts/make_rl_dataset.py --split train --n 2000 --out data/rl/train_2000.jsonl`: JSONL with `prompt` (system + user chat messages) and `task_spec`; load with `datasets.load_dataset("json", ...)`. The column was called `task_specs` in the first draft; it is `task_spec` everywhere now.
- `scripts/rl_demo.py`: 20 episodes with the reference client and 20 with an empty client; prints reward stats and median grade time.
- **Teacher data** (separate step, not built): Beam solves train tasks; keep clients with reward 1.0 -> SFT dataset.

## Validity checks (`tests/test_env_validity.py`, all pass)
- Reference client reward = 1.0 on generated tasks for every source and every level (8 tasks).
- Empty client = 0; observation-replay client <= 0.1; the 6 Tier B flawed clients (`tierb/refclient.py` FLAWS; `mutants.py` is Tier A only) give reward < 1 on an all-switches task.
- Same (task, code) -> same reward on two calls.
- No hidden data in the observation: no test credentials, no test-seed item ids, no engine value that the browser recording of the same task does not show, no hidden file names.
- Existing browser build output unchanged (golden digests on 2 configs); same task through both sources has the same hidden tests.

## Ambiguities found
- `task_specs` (design) vs `task_spec` (brief): the dataset column and the argument are `task_spec`; `task_specs` is accepted as a keyword alias.
- `min_pass_rate_k` (named `strict_pass_rate` in an early draft; renamed to avoid a clash with the grader metric) is the min pass rate over the k grading instances.
- `observation_format` was not defined: it is the file extension (`"har"`).
- Reward floor: the invented-endpoint penalty can give -0.1; the reward is not clamped at 0.
- L4 and the "all sources" rule cannot be built with two sources; the split's sources always win over the level, so the train split never shows held-out sources.
