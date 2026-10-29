# Architecture

Covers what is built in `har2client/`. The contract for module interfaces is [`design/DESIGN.md`](design/DESIGN.md); the hypotheses and analysis plan are in [`har2client/PREREG.md`](../har2client/PREREG.md). Section 3 to 9 describe the Tier A pipeline in detail. Section 10 is a short map of Tier B, case 3 and the RL environment.

## 0. The short version (no technical background needed)

### The problem
Apps and websites talk to their servers with short messages, called **API calls**. For example: "give me my account list" and the server answers "here are your 3 accounts".

Developers often want to use these calls from their own code. But many services do not document them. So developers **record** what the website sends while they click around (the recording is called a **HAR file**). Then they read the recording and write code that makes the same calls. That code is called an **API client**.

AI models can now write that code for you. But nobody has measured **how often they get it right**. That is what we test.

### The idea, as a driving test
Think of a driving test. The examiner does not ask "do you know the rules?". They put you in a car and watch you drive on a real road.

We do the same with AI:

```
  1. We make a fake shop            2. We record someone using it
  ┌────────────────────┐            ┌────────────────────────────────┐
  │  FAKE SERVER        │  ◀──────── │  a pretend user clicks around   │
  │  (acts like a real  │  ────────▶ │  we save every message (HAR)    │
  │   online service)   │            │  + we add junk: ads, fonts,     │
  └────────────────────┘            │    trackers (like real traffic) │
                                     └───────────────┬────────────────┘
                                                     │
  3. We give the AI two things                       ▼
  ┌──────────────────────────────────────────────────────────────────┐
  │  "Here is the recording. Write code with these 8 functions."      │
  └───────────────────────────────┬──────────────────────────────────┘
                                  │  the AI writes its code
                                  ▼
  4. The driving test
  ┌──────────────────────────────────────────────────────────────────┐
  │  We start a NEW fake server: new data, new password.              │
  │  We run the AI's code against it and check every message:         │
  │    right address?  right password?  right details?                │
  │    did it bring back the new data?                                │
  └───────────────────────────────┬──────────────────────────────────┘
                                  ▼
  5. Score: "Beam passed 14 of 16 checks. The 2 fails: wrong password place."
```

### Why the test is fair
- **The AI cannot copy answers.** At test time the data and the password are new. Code that only repeats the recording fails.
- **The test itself is checked.** For every task we also build a "perfect" answer from the official API description. It must score 100%. If it does not, the task is broken and we fix it before any AI sees it.
- **The rules are written down first.** Our predictions (in `PREREG.md`) are saved in git before any results come in, so we cannot change them to fit the results.

### What each part does, in one line
| Part | Plain meaning |
|---|---|
| API picker | Chooses 165 real public services to copy (for example Twilio, BBC, Pinecone). |
| Fake server | Pretends to be one of those services. Checks the password, answers with made-up data, writes down every message. |
| Recorder | Plays a pretend user and saves the traffic, mixed with ads and trackers. |
| Task builder | Packs one exam: the recording and the instructions (shown to the AI), plus the hidden answer key. |
| Grader | The examiner. Runs the AI's code against a fresh fake server and marks each check pass or fail, with the reason. |
| Perfect answer | The answer key's own test: proves every exam can be passed. |
| Beam solver | Sends the exam to Beam and saves Beam's code. |
| Batch runner | Runs many exams in a row and makes a results table. |

### What we want to learn
- Where do AI models fail: finding the right address, logging in, or handling linked steps?
- Do they invent calls that were never in the recording?
- Does the junk traffic confuse them?
- Does "thinking harder" (Beam's effort setting) help, and on which tasks?

The rest of this file is the technical detail.

## 1. What the project is

`har2client` is a benchmark. It asks one question: **can a model read recorded browser traffic and write a working API client?**

Many tools do this job today (reverse-api-engineer, Integuru, Vercel `derive-client`). None of them publishes an accuracy number. We build the test they are missing and run Beam (and other models) on it.

One task, in one line: *here is a HAR file and a list of methods; write `client.py`.* We grade the file by running it against a fresh fake server with new data and a new key.

## 2. Repo layout

```
har2client/
├── README.md                  front page: results, quickstart, how to evaluate a model
├── LICENSE, LICENSE-DATA.md, NOTICE   Apache-2.0 code, CC BY 4.0 data, third-party notes
├── CITATION.cff
├── docs/
│   ├── architecture.md       this file
│   ├── design/               design notes: Tier A, Tier B, cases 2 and 3, RL environment
│   └── related-work/         literature reviews R1-R5
├── release/                   Hugging Face kit: dataset export, leak check, Croissant, leaderboard Space
└── har2client/
    ├── README.md              developer guide
    ├── PREREG.md              hypotheses, fixed before runs; deviations log
    ├── pyproject.toml         uv project, Python 3.12 (extra `plots` for matplotlib)
    ├── .env                   REFLECTION_API_KEY, HF_TOKEN (git-ignored, you create it)
    ├── scripts/               build, run, score, analyse and plot (list: har2client/README.md, section 6)
    ├── src/har2client/
    │   ├── ops.py             OpenAPI spec -> OpTable
    │   ├── datagen.py         JSON schema -> random valid value
    │   ├── mock.py            MockServer: fake API from an OpTable (Tier A)
    │   ├── browser.py         shared Chrome header profile
    │   ├── player.py          plays a user session against the mock
    │   ├── har.py             HAR recorder, host rewrite, noise mixer
    │   ├── testgen.py         hidden test calls + type check
    │   ├── build_task.py      spec -> task directory
    │   ├── runner.py          runs client.py in a child process
    │   ├── grade.py           scores a client against a fresh mock
    │   ├── refclient.py       gold client generated from the OpTable
    │   ├── variants.py, namegen.py, mutants.py   name/memory variants, grader-audit faults
    │   ├── cli.py             `har2client build|ref|grade|solve`
    │   ├── tierb/             Tier B: stateful engine with six switches, builder, grader
    │   ├── heal/              case 3: mutations, v3 hard changes, changelog, release builder, SDK-style clients
    │   ├── env/               RL environments (TrafficToClientEnv, RepairEnv), reward, curriculum, TRL
    │   └── solve/             solvers: Beam, any OpenAI-compatible endpoint, case 2 agent, prompts
    ├── tests/                 about 1,570 tests (pytest)
    ├── data/                  selected_specs.jsonl (165 APIs), Tier B and case 3 configs, RL splits
    ├── results/               committed result tables, analysis reports, figures
    └── runs/                  git-ignored: built tasks and raw run output
```

## 3. The big picture

```
                         ┌──────────────────────────────┐
  APIs.guru (2,529 APIs) │  scripts/select_specs.py     │  filter: OpenAPI 3.0, 5-40 ops,
  ──────────────────────▶│  download, filter, cap       │  >=3 JSON responses, valid,
                         └──────────────┬───────────────┘  <=2 per provider
                                        │ data/selected_specs.jsonl (165 APIs)
                                        ▼
                         ┌──────────────────────────────┐
                         │  build_task.py  (per API)    │  §4
                         └──────────────┬───────────────┘
                                        │ runs/<set>/<task_id>/
            ┌───────────────────────────┼─────────────────────────────┐
            │ VISIBLE to the model      │ HIDDEN from the model       │
            │  goal.md                  │  op_table.json  (gold)      │
            │  session.har              │  tests.json     (hidden)    │
            │                           │  task.json      (seeds, keys)│
            └─────────────┬─────────────┴──────────────┬──────────────┘
                          ▼                            │
            ┌──────────────────────────┐               │
            │  SOLVER (system under    │               │
            │  test)                   │               │
            │  - solve/beam.py         │               │
            │  - refclient.py (gold)   │               │
            │  - later: RAE, others    │               │
            └─────────────┬────────────┘               │
                          │ client.py                  │
                          ▼                            ▼
                         ┌──────────────────────────────┐
                         │  grade.py  +  runner.py      │  §5
                         │  fresh MockServer, new data, │
                         │  new key, child process      │
                         └──────────────┬───────────────┘
                                        │ grade.json
                                        ▼
                         ┌──────────────────────────────┐
                         │  scripts/run_batch.py        │  many tasks x efforts x runs
                         │  results.csv + summary       │  -> analysis per PREREG.md
                         └──────────────────────────────┘
```

## 4. Building one task (`build_task.py`)

```
 spec.json ──▶ ops.load_spec ──▶ ops.normalize ──▶ OpTable
                (resolve $ref,      (op ids, params, body,
                 cap depth/size)     response, security;
                                     drop what we cannot grade)
                                          │
                                          │ usability check: 5-40 ops, >=3 JSON responses,
                                          │ supported auth (none/apiKey/bearer/basic)
                                          ▼
          rng = Random(seed:api_id)  ──▶ pick goal ops (<=8, JSON response only)
                                          │ make 2 secrets: recording key, test key
                                          ▼
 ┌─────────────────── RECORDING ──────────────────────────────────────────┐
 │                                                                         │
 │   player.play_session ──HTTP──▶ MockServer(seed=rec_seed,               │
 │   (1-3 calls per goal op,       secret=recording key)                   │
 │    browser fetch headers,       on 127.0.0.1:<port>/<base_path>         │
 │    datagen args)                                                        │
 │         │                                                               │
 │         └── httpx event hooks ──▶ HarRecorder ──▶ raw HAR               │
 └─────────────────────────────────────────────────────────────────────────┘
                                          │
                                          ▼
              har.rewrite_host  (127.0.0.1 -> real host, e.g. api.up.com.au)
                                          ▼
              har.mix_noise     (ratio 0.7: CDN js/css, fonts, analytics,
                                 beacons, same-origin JSON like /__flags;
                                 same browser headers as real calls)
                                          ▼
              pick test seed so no test response can be copied from the HAR
              testgen.make_tests (2 calls per goal op, fresh args)
              testgen.check_arg_types (args must match goal.md types, else fail)
                                          ▼
              write goal.md, session.har, op_table.json, tests.json, task.json
```

## 5. Grading one client (`grade.py`, `runner.py`)

```
   task.json ─┐
   tests.json ├──▶ grade.grade(task_dir, client.py)
   op_table ──┘          │
                         │ start MockServer(seed=TEST seed, secret=TEST key)
                         ▼
   ┌──────── parent process ────────┐        ┌────── child process ───────┐
   │ MockServer (thread)            │◀─HTTP──│ runner.py  (python -I)     │
   │  route match  -> 404           │        │  import client.py          │
   │  auth check   -> 401           │        │  c = Client(base_url, key) │
   │  params/body  -> 400           │        │  for each test:            │
   │  else 2xx + datagen response   │        │    t0; ret = c.method(**kw)│
   │  log every request + response  │        │    t1; write result line   │
   │  with timestamp                │        │  60 s total timeout        │
   └───────────────┬────────────────┘        └──────────────┬─────────────┘
                   │ mock.log                               │ results [index, ok, return, t0, t1]
                   └───────────────┬────────────────────────┘
                                   ▼
                 for each test: log entries inside [t0, t1)
                   pass  = right op, 2xx, path params equal, query equal,
                           body equal, return value == logged response
                   else one reason: exception | no_request | wrong_op |
                           auth_401 | bad_request_400 | not_found_404 |
                           args_mismatch | return_mismatch | timeout
                                   ▼
                 grade.json: pass_rate, per-test reasons,
                   invented_endpoint_calls (requests outside the goal),
                   hardcoded_secret (recording key in client.py),
                   hardcoded_host (real host in client.py)
```

Why a client cannot cheat:

| Cheat | What catches it |
|---|---|
| Copy response bodies from the HAR | Test mock has a new seed, so new data -> `return_mismatch` |
| Copy the API key from the HAR | Test mock has a new key -> `auth_401`, and `hardcoded_secret` |
| Hard-code the real host | Tests use `127.0.0.1:<port>` -> no request; `hardcoded_host` |
| Call extra endpoints | `invented_endpoint_calls` |
| Hang or crash | child-process timeout -> `timeout` / `exception` |

## 6. The Beam solver (`solve/beam.py`)

```
 goal.md ──┐
 session.har ─┼─▶ prepare_har_text(mode)  raw: whole HAR
           │                              compact: main API host only,
           │                              bodies cut to 2,000 chars
           ▼
   prompt.build_user_prompt(goal, har)  +  system prompt
           ▼
   OpenAI SDK -> https://api.reflection.ai/openai/v1
     model Beam-1.0-501B-A23B, reasoning_effort=<low..max>,
     max_completion_tokens=65,536, temperature unset
           │
           ├─ 429/503: wait Retry-After (or backoff + jitter), max 5 tries
           ├─ x-should-retry:false or daily_allowance_exhausted: stop
           ▼
   extract last ```python block -> client.py   (none -> status no_code)
   solve_log.json: usage, reasoning text, raw answer, wall time,
                   rate-limit headers (remaining daily tokens)
```

The solver opens only `goal.md` and `session.har`. A test checks that it never reads the hidden files.

## 7. Batch runs (`scripts/run_batch.py`)

```
 tasks/ × efforts × har_modes × runs  ──▶  cells
      │ thread pool (concurrency 4)
      ▼
 for each cell:  solve() -> client.py -> grade() -> grade.json
      │ skip a cell if grade.json exists (resume)
      │ daily limit hit -> finish in-flight cells, stop, exit 3
      ▼
 results.csv  (task, auth, effort, har_mode, run, pass_rate,
               fail_<reason> columns, invented, secrets, tokens, time)
 summary: mean pass rate by effort × har_mode, total tokens
```

## 8. Key data shapes

`OpTable` (from `ops.normalize`):
```
api_id, title, server_url, base_path,
security {type: none|apiKey|bearer|basic, in, name},
operations [ {op_id, method, path, summary,
              params [{name, py_name, in: path|query, required, schema}],
              body {required, schema} | null,
              response {status, schema},
              auth_required} ],
dropped [ {op, reason} ]
```

Task directory:
```
goal.md        visible   API title, base_url format, Client(base_url, api_key), method list, rules
session.har    visible   HAR 1.2: real calls + noise, real host, recording key
op_table.json  hidden    gold OpTable
tests.json     hidden    [{method, kwargs}] with fresh args
task.json      hidden    seeds, both keys, goal ops, noise ratio, feature tags
```

## 9. Design choices and why

| Choice | Why |
|---|---|
| Own Python mock, not Prism | Needs new keys per instance, seeded data, and a full request log for grading. Prism is stateless and has no key rotation. |
| Synthetic traffic from public specs | Known answers; no website terms-of-service risk. |
| Real host in the HAR, localhost at test | HAR looks real; a hard-coded host fails. |
| Noise with the same browser headers | A model must read paths and content to drop noise, not header names. |
| Test seed chosen so no response matches the HAR | Copying cannot pass by luck. |
| Child process with `python -I` and a timeout | Generated code is untrusted. (Docker is a later step.) |
| Build fails on a type mismatch | Our bugs must not show up as model failures. |
| Reference client must score 1.0 on every task | Proves each task is solvable and the grader is fair. |

## 10. Tier B, case 3, RL and status

Tier A (sections 3 to 9) is the first and easiest tier. The other parts reuse its grader idea (fresh server, hidden tests) with a stateful engine.

| Part | Where | Short description | Design file |
|---|---|---|---|
| Tier B | `tierb/` | Invented stateful APIs. One engine, six switches: login type, paging, token expiry, version check, hidden header, response envelope. | [`DESIGN-TIERB.md`](design/DESIGN-TIERB.md) |
| Case 3 | `heal/` | The API changes between v1 and v2. The model repairs `client_v1.py`. v3 adds silent wire changes and protocol changes (64 tasks, 7 families). | [`DESIGN-CASE3-HARD.md`](design/DESIGN-CASE3-HARD.md) |
| RL environments | `env/` | `TrafficToClientEnv` (case 1) and `RepairEnv` (case 3): one episode is one submitted client, reward is the grade. | [`DESIGN-RL.md`](design/DESIGN-RL.md) |
| Solvers | `solve/` | Beam (`beam.py`), any OpenAI-compatible endpoint (`openai_compat.py`), case 2 agent loop (`beam_agent.py`). | [`DESIGN.md`](design/DESIGN.md) |
| Release kit | `release/` | Dataset export, leak check, Croissant file, leaderboard Space. | `release/README.md` |

Design notes are in [design/](design/).

### What is implemented

| Part | State |
|---|---|
| Spec selection | 165 usable APIs |
| Task builder, mock, HAR, noise, grader, reference client | Implemented. The reference client scores 1.0 on 30 of 30 Tier A tasks |
| Beam solver and batch runner | Implemented |
| Tier A (30 tasks) | Evaluated with Beam at low and high effort |
| Tier B (50 tasks) | Evaluated with Beam at low, medium and high effort |
| Case 3 v3 (64 tasks, base and hint arms) | Evaluated with Beam at low, medium and high effort, with hint controls |
| Case 2 (probe) | 450 cells (`har2client/results/case2_probe/main_full/`) |
| Grader audit | `har2client/results/grader_audit_set30.md` |
| RL environments | Implemented and tested. No training run is reported |
| Hugging Face dataset and leaderboard Space | Release kit in `release/` |
| Large HARs (100K-800K tokens) | Not implemented |
| Docker sandbox for the grader | Not implemented. The grader is a child process, not an OS sandbox |
