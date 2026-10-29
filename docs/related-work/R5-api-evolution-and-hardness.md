# R5: API-evolution benchmarks, HTTP-API benchmarks, and how hard benchmarks stay hard

Date of search: 2026-10-24. Scope: decide if case 3 (repair an API client after the API changes) is already covered, and which design levers apply to the v3 repair tasks. Read first: `R3-tooluse-stateful-envs.md` and `R2-benchmark-methodology.md`. I do not repeat their rows (API-Bank, ToolBench, RestBench, NESTFUL, AppWorld, tau-bench, SWE-ABS, UTBoost, BFCL, AutomationBench, LiveCodeBench). I only point to them.

## 0. Method and trust levels

- About 30 web searches and about 35 page fetches. Main sources: arXiv abstract and HTML pages, ACL Anthology, GitHub API (licences).
- "Fetched" means I read the arXiv page or full text and the number is from it. "Snippet" means the number came from a search summary only. "Secondary" means a news or blog page. I mark every non-fetched number.
- OpenAI's page on SWE-bench Verified returned HTTP 403. The Verified claims below are from a news summary (secondary).
- Not found by search: any paper or repo that gives HAR or traffic input for HTTP client repair (see section 2). The search used arXiv, the open web and GitHub only. Private or vendor work can exist.
- Name clash: two different papers use the name "DepBench" (rows 3 and 4 in table 1). Sizes differ (95 and 203). I did not confirm they are one dataset.

## 1. Table (a): benchmarks

### Table 1. Code that must adapt to library, dependency or API change

| # | Name | Year / venue | Task | Input to model | Grading | Size | Best score / saturated? | Link |
|---|---|---|---|---|---|---|---|---|
| 1 | BUMP | 2024, SANER | Dataset of Java builds that break after a dependency update | Repo at the breaking commit | Build fails before fix, passes at the good commit; Docker images | 571 updates, 153 Maven projects (fetched) | Dataset only. Not saturated (see Byam). Zenodo images need about 250 GB (search summary) | https://arxiv.org/abs/2401.09906 |
| 2 | Byam (on BUMP) | 2025, arXiv (journal version not named) | LLM patches client code broken by an update | Buggy line, API diff, compiler errors, step-by-step prompt | Build, file and single-error level | BUMP | o3-mini: fully fixes 27% of builds, 78% of single errors (fetched). Not saturated | https://arxiv.org/abs/2505.07522 |
| 3 | DepBench / "Update from Hell" | 2026-08, arXiv | Agent upgrades a dependency and repairs hidden breakage | Base repo plus a one-line upgrade instruction. No failing test shown. Upstream PR pages blocked | Hidden test patch overlaid; install, build, test; four-state oracle | 203 tasks, 5 ecosystems (fetched) | Best 104/203 (51.2%), GPT-5.5 in Codex. Opus 4.8 best 83. Not saturated | https://arxiv.org/abs/2608.30300 |
| 4 | DepBench (in DepRepair) | 2026-07, arXiv | Repair consumer code after a dependency update | Release notes, API diff, repo. No failing test localises the break | Docker oracle runs consumer tests | 95 instances, 4 ecosystems (fetched) | DepRepair method: 89.5% (GPT-5.5), 82.1% (Opus 4.6). The paper says raw upstream evidence lowers pass rate; filtered evidence raises it | https://arxiv.org/abs/2607.17957 |
| 5 | TimeMachine-bench | 2026, EACL (main) | Python repo whose tests fail after dependency updates; agent migrates | Repo, target date, initial failing test run; 10 tools; cannot edit tests | pass@1 (all tests pass in 100 LLM calls, 10 test runs); prec@1 vs minimal human patch | 1,145 repos (Full); 100 (Verified) (fetched) | Verified: Claude Sonnet 4 99.0%, GPT-5 91.0%, Qwen3-Coder-480B 90.0% (fetched). Close to saturated. Authors name spurious solutions that exploit low test coverage | https://arxiv.org/abs/2601.22597 |
| 6 | MigrationBench | 2025, arXiv (v3 2026-05) | Java 8 to 17/21 repo migration | Repo | Maven checks, class versions, test count not lower | 5,102 repos; 300-repo subset (fetched) | Claude 4.5 Sonnet agent: 71.67% minimal, 53.33% maximal (fetched) | https://arxiv.org/abs/2505.09569 |
| 7 | FreshBrew | 2025 arXiv; listed for ICSE 2026 | Java JDK 8 to 17 migration by agents | Repo, CodeAct tools | Compiles, tests pass, coverage within 5 points of baseline (anti test-deletion) | 228 repos (fetched) | Gemini 2.5 Flash 52.3% JDK 17 (fetched) | https://arxiv.org/abs/2510.04852 |
| 8 | swe-bump-bench | 2024, GitHub | TypeScript package bump, patch breaking changes | Codebase, package, target version | Not described (tsc is the break signal) | Not stated | No results published. MIT | https://github.com/xeol-io/swe-bump-bench |
| 9 | GitChameleon 1 and 2.0 | 2024 arXiv; 2.0 listed at ACL 2026 | Python code completion for a named library version | Prompt plus version | Executable unit tests | 116 (v1); 328 (v2) | v1 GPT-4o pass@10 39.9% (snippet). v2 baselines 48-51% (fetched). Not saturated | https://arxiv.org/abs/2507.12367 |
| 10 | VersiCode | 2024, arXiv | Version-specific completion and version migration | Masked code plus version | EM; CDC@1 (critical diff check); executable subset | Over 300 libraries, 2,000+ versions; 119 executable items (fetched) | GPT-4o EM 70.44; exec Pass@1 65.97 with imports, 44.54 without (fetched) | https://arxiv.org/abs/2406.07411 |
| 11 | CodeUpdateArena | 2024, arXiv | Solve a problem that needs a synthetic API update | Update doc prepended or not | UPass@k: new function passes, old function fails | 54 functions, 7 packages, 670 examples (fetched) | Claude 3.5 Sonnet UPass@1 58.7 with doc; about 3 without (fetched). Synthetic updates avoid contamination | https://arxiv.org/abs/2407.06249 |
| 12 | LibEvolutionEval | 2025, NAACL | Version-specific API call completion | Code plus optional version docs | F1 on API call | 8 libraries; about 4.5K assorted examples (fetched) | Best F1 73.3 (PyTorch), 75.4 (Matplotlib), Starcoder2-7B with RAG (fetched) | https://arxiv.org/abs/2412.04478 |
| 13 | RustEvo2 | 2025, arXiv | Rust code for API changes (stabilised, signature, behaviour, deprecated) | Task; RAG as an option | Tests that separate old from new usage (grading not stated on abstract) | 588 changes (fetched) | Stabilised 65.8% vs behavioural change 38.0%; before cutoff 56.1% vs after 32.5% (fetched) | https://arxiv.org/abs/2503.16922 |
| 14 | CodeSync / CodeSyncBench | 2025, ICML | Code with updated library APIs | Task | Three tasks; BLEU on completion (snippet) | 220 APIs, 6 libraries, 3,300 tests (fetched) | GPT-4o and Claude 3.5 below 20% BLEU on completion (snippet) | https://arxiv.org/abs/2502.16645 |
| 15 | Knowledge conflicts from evolving APIs | 2026-04, arXiv | Follow updated docs, not old habits | Docs as context | Share of examples that execute | 270 updates, 8 Python libraries, 11 models (fetched) | 42.55% executable without full docs; 66.36% best with structured docs (fetched) | https://arxiv.org/abs/2604.09515 |
| 16 | quantum-api-drift | 2026-07, arXiv | Qiskit code for v0.43, v1.3, v2.0; repair with docs | Prompt plus version | Execution Pass@1; repair rate | 50 tasks, 17 models (fetched) | Diagonal Pass@1 0.02 to 0.85; doc-guided repair 0.19 to 0.59 (fetched) | https://arxiv.org/abs/2607.04072 |
| 17 | ScrambleToolBench | 2026-08, arXiv | Terminal tools with hidden behaviour; mapping drift; random failures | Interactive environment, no semantic cues | Episode completion | 28 tools; 5 tasks per episode; 15 models (fetched) | Completion: 0.93 unscrambled, 0.32 scrambled, 0.23 drift only, 0.03 all challenges combined (fetched) | https://arxiv.org/abs/2608.02358 |
| 18 | Robust function calling under interface drift | 2026-10, NGEN-AI 2026 | Tool descriptions, argument schemas and tool list change | Drifted tool specs | Call accuracy (DICE-300, BFCL-200) | 300 and 200 items | Qwen3.5-9B 0.717 to 0.813 and 0.580 to 0.805 with a repair layer. Distractor tools cause most damage (fetched) | https://arxiv.org/abs/2610.23342 |

### Table 2. HTTP and web API integration, client and backend generation

| # | Name | Year / venue | Task | Input to model | Grading | Size | Best score / saturated? | Link |
|---|---|---|---|---|---|---|---|---|
| 19 | Agent-Diff | 2026, arXiv (under review KDD 2026) | Agent writes code against replicas of Slack, Box, Linear, Google Calendar | Task; docs ablated | State-diff: expected changes must appear; any unexplained change fails (closed world); `updated_at` ignored | 224 tasks, 108 endpoints (fetched) | DeepSeek-v3.2 76% pass, score 88.1 without docs. Docs add about 7 points pass rate. Irrelevant docs hurt (Linear -4.0). Mid-range, not saturated. MIT code | https://arxiv.org/abs/2602.11224 |
| 20 | BaxBench | 2025, ICML | Generate a backend from an OpenAPI spec | Spec, framework | Functional tests plus security exploits on running app | 392 tasks (28 scenarios x 14 frameworks) (fetched) | o1 about 62% correct; about half of correct programs exploitable (fetched). Not saturated | https://arxiv.org/abs/2502.11844 |
| 21 | BackendForge | 2026, EMNLP Findings | Build a Dockerized backend from spec | Spec plus OpenAPI contract | Black-box HTTP tests; test agent and code agent co-evolve the oracle | 56 tasks (fetched) | GPT-5.5: 55.4% (base oracle), 28.6% (final oracle). The stronger oracle halves the score | https://aclanthology.org/2026.findings-emnlp.2236 |
| 22 | WebApp1K / Tests as Prompt | 2024 / 2025, arXiv | React apps from tests | Tests as prompt | pass@1 over 10 tries | 1,000 tasks, 20 domains (search summary) | Described as partly "solved" by top models (secondary) | https://arxiv.org/abs/2505.09027 |
| 23 | RESTestBench | 2026-04, arXiv | LLM writes a REST test from a requirement plus OpenAPI | Requirement (precise or vague), spec | Requirement-based mutation score | 3 services (fetched) | Test quality drops when the generator sees faulty code (fetched) | https://arxiv.org/abs/2604.25862 |
| 24 | dcFix (REST misuse repair) | 2025, arXiv | Repair client code that breaks the REST spec | Non-conforming fragment plus spec parts | Not stated on abstract | Not stated | Beats a no-fragment baseline; no numbers on abstract (fetched). Repairs misuse against one spec. It is not a spec-version change | https://arxiv.org/abs/2510.25148 |
| 25 | AFT-Bench | 2026-08, arXiv | Agent tool interface with injected failures (response lost after commit, stale state, false success) | Tool interface variants | Duplicate effects, unsafe commits, false terminal claims | 6 workloads, 2,385 result rows, 3 models (fetched) | Effect-aware interface cuts duplicate effects by 56.9 points (fetched) | https://arxiv.org/abs/2608.23628 |
| 26 | SDK generators (Speakeasy, Stainless, Fern) | 2026, vendor pages | Compare SDK generators | OpenAPI spec | Feature lists; one customer bake-off with a human rubric | n/a | No LLM benchmark found. Vendor claims only | https://www.speakeasy.com/eval.md |
| - | API-Bank, ToolBench, RestBench, NESTFUL, AutomationBench, AppWorld, tau-bench, BFCL | 2023-2026 | Tool and API use | Docs or schemas | Calls, state or reward | See R3 table 2a | See R3. All give specs or docs, not traffic | `R3-tooluse-stateful-envs.md` |

## 2. Table (b): gap analysis

**Question.** Does any existing benchmark already do "repair an HTTP API client from traffic and a failure, under API change, graded on a fresh stateful server"?

**Verdict: No.** I found no benchmark that has all of these parts. Evidence:

| Feature of case 3 | Closest prior work | Has it? | What is missing |
|---|---|---|---|
| The API changes between version 1 and version 2 | BUMP, Byam, DepBench, TimeMachine-bench, RustEvo2 | Yes, but for libraries and compilers | The change is in a Python or Java library, not an HTTP wire format |
| Output is a patch to existing client code | BUMP, Byam, DepBench, TimeMachine-bench, MigrationBench, FreshBrew | Yes | Client code for a library, not for a remote service |
| Evidence is a recorded HTTP session (HAR) | None found | No | Agent-Diff and AFT-Bench give docs or tool schemas; DepBench gives release notes and diffs |
| HTTP or REST wire contract | Agent-Diff, BaxBench, BackendForge, RESTestBench, dcFix | Partly | They test an agent that calls the API, a generated server, or a test. None repairs a client after a wire change |
| Silent change (wrong value, no error) | DepBench (implicit breakage), ScrambleToolBench (mapping drift) | Partly | Not on HTTP. In DepBench 322 of 521 non-passes saw the visible tests pass first (fetched) |
| Graded on a fresh stateful server | Agent-Diff (state diff on API replicas), AppWorld, tau-bench | Partly | They grade an online agent, not a reusable client re-run on a new host, credentials and data |
| Protocol change (429, 202 polling, token rotation, idempotency) | AFT-Bench (lost response, duplicate effects) | Partly | Not as a client-repair task |

Nearest neighbours, in order: (1) DepBench (2608.30300): hidden breakage, hidden tests, agent repair. Same shape, different object. (2) TimeMachine-bench: Python, visible failing tests. (3) Agent-Diff: HTTP replicas plus a state-diff grader, but no change and no repair. No search found a HAR to client benchmark (R3 also found none). Claim to make in the paper: "we found no prior benchmark", with the search log. Do not claim "first".

## 3. Table (c): difficulty levers from strong 2025-2026 benchmarks

| Lever | Evidence | Number | Source |
|---|---|---|---|
| Task size and spread over files | SWE-Bench Pro: reference patch averages 107.4 lines over 4.1 files. Trivial 1-10 line edits are excluded | Best public Pass@1 43.6% (Sonnet 4.5); commercial 17.8% (Opus 4.1) (fetched) | https://arxiv.org/abs/2509.16941 |
| Hidden requirements, hidden interface | SWE-Bench Pro ablation: remove written requirements and interface. Tests then reject valid patches | GPT-5 (high) 25.9% to 8.40%; Opus 4.1 22.7% to 8.20% (fetched) | https://arxiv.org/html/2509.16941 |
| Private and legally protected data | Public and held-out sets use GPL repos; commercial set is private | 731 public, 858 held-out, 276 commercial (fetched) | same |
| Silent or implicit failure | DepBench: agents see visible tests pass, hidden tests fail | 322 of 521 non-passes; largest agent failure class is incomplete migration (wrappers, mocks, call sites) (fetched) | https://arxiv.org/html/2608.30300 |
| Propagation depth | DepBench: tasks that need changes over several layers are harder; larger hidden-test patches are harder | Qualitative (fetched) | same |
| Stacked challenges | ScrambleToolBench: each challenge alone costs about 0.1 to 0.7 of completion; all together nearly zero. More reasoning gave more brute-force search, not better deduction | 0.93, 0.32, 0.23, 0.26, 0.19, 0.03 (fetched) | https://arxiv.org/html/2608.02358v1 |
| Long horizon | METR: 50% time horizon of Claude 3.7 Sonnet is about 50 minutes; doubling about every 7 months (fetched). RE-Bench: agents win at 2 h; humans win at 8 h and 32 h | Agents 4x human at 2 h; humans about 2x agents at 32 h (fetched) | https://arxiv.org/abs/2503.14499 ; https://arxiv.org/abs/2411.15114 |
| Curated hard tasks, per-task env, reference solution, tests | Terminal-Bench 2.0 | 89 tasks; frontier below 65% (fetched) | https://arxiv.org/abs/2601.11868 |
| Real paid work, expert-verified tests | SWE-Lancer | 1,400+ tasks, $1M payouts; models fail most (fetched) | https://arxiv.org/abs/2502.12115 |
| State-diff grading with closed world | Agent-Diff: any unexplained change gives zero | 224 tasks; best pass 76% (fetched) | https://arxiv.org/abs/2602.11224 |
| Reliability (pass^k) and noise | See R3 (tau-bench: below 25% at pass^8) and R2 (single-run swing 2.2-6.0 points) | see R2, R3 | R2, R3 |
| Adversarial filtering against current models | HLE: over 70,000 attempts gave about 13,000 failing questions; experts kept 2,500. Multiple-choice items kept if models scored below chance | Release scores 2.7% (GPT-4o) to 13.4% (o3-mini high) (fetched) | https://arxiv.org/abs/2501.14249 |
| Bias of that filtering | HLE-Verified: 668 verified, 1,143 revised, 689 uncertain. Models gain 7-10 points on average, 30-40 points where the original item was wrong. Model confidence links to item error. Difficulty is tied to the filter models (HLE authors) | see left (fetched) | https://arxiv.org/abs/2602.13964 |
| Error audits of hard items | Epoch audit: 22 of 48 sampled HLE items had accuracy-changing errors (secondary). OpenAI: at least 59.4% of audited hard Verified tasks had flawed tests (secondary) | secondary | https://epoch.ai/benchmarks/hle/review ; https://aiweekly.co/node/5874 |
| Freshness | SWE-bench-Live: 1,319 tasks from issues since 2024, 93 repos, "substantial gap" to static sets (fetched). TimeMachine-bench: change two dates, get new tasks; package index hides later releases | see left | https://arxiv.org/abs/2505.23419 ; https://arxiv.org/abs/2601.22597 |
| Saturation of an older benchmark | Verified moved only 74.9% to 80.9% in six months; OpenAI stopped reporting it in 2026-02 (secondary) | secondary | https://aiweekly.co/node/5874 |
| Stronger oracle lowers the score | BackendForge: base oracle 55.4%, final oracle 28.6% | fetched | https://aclanthology.org/2026.findings-emnlp.2236 |
| Dual control and compositional generator | tau2-bench | see R3 | R3 |

Reading of the table. Models fail on silent changes (no visible error), on changes that spread over layers, on stacked challenges, and on tasks where the grader is stricter than the visible signal. Models do not fail on one loud change. This matches the v2 result (Beam 1.0).

## 4. Caveats

- Absence claim: based on the searches listed in section 0. A private or very new benchmark can exist.
- Scores from search snippets (GitChameleon v1, CodeSync, WebApp1K) and secondary pages (Epoch, OpenAI via news summary, FutureHouse) are not verified. Check them before they go into a paper.
- Model names (GPT-5.5, Opus 4.8, Sonnet 5, and so on) are as the papers state them. I did not check them.
- Benchmark scores from different papers use different agents, budgets and graders. Do not rank across rows.
- Items 3, 4 of table 1 may be one dataset at two sizes. Not confirmed.
- ACL 2026 venue for GitChameleon 2.0, ICSE 2026 for FreshBrew and ICML 2025 for BaxBench come from search results or the arXiv listing, not from the proceedings pages.
