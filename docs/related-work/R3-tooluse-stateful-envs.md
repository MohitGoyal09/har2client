# R3: Executable benchmarks and environments for tool/API use, code, and synthetic RL environments

Date of search: 2026-10-24. Status labels: V = record fetched (arXiv ID resolved via Semantic Scholar batch API; title and year match). "Content-checked" = I also read the abstract or full text and quote it. UNVERIFIED = not confirmed from a primary record.

## 1. Search log

- arXiv API (Atom): title lookups for AppWorld and ToolSandbox worked. The API then returned HTTP 429, so I switched to the Semantic Scholar batch endpoint (`/paper/batch`, ARXIV ids). About 9 batches, about 75 IDs. All IDs in the tables resolved with matching titles and years, except BFCL (see table).
- Semantic Scholar search: 3 queries succeeded, about 5 more were rate-limited (429) and were redone with WebSearch.
- WebSearch: about 12 queries (AutomationBench, BFCL v3/v4, HAR-to-client, environment scaling 2026, tau2 leaderboard, SWE-bench saturation, API-integration benchmarks, spec inference from traffic, weak-test papers). Mostly 1-3 useful hits each.
- WebFetch: arXiv abstract pages (AutomationBench, AWM, Unbrowse), the BFCL v3 multi-turn blog, and full-text PDFs for AppWorld and tau-bench (converted with `pdftotext`, then grep). ToolSandbox and ToolRL were read through arXiv HTML.
- Not found: any academic paper on "HAR file to typed API client". Searches for HAR, network-traffic and client-generation terms returned only agent skills and tools (Vercel `derive-client`, `reverse-api-engineer`, `har-mcp`). These are GitHub or skill-registry items, not papers.
- Caveat: leaderboard numbers from aggregator sites (benchlm, codesota, taubench.com summaries) came through search snippets. I mark them "secondary". I did not re-verify them against the official boards.

## 2. Papers

### 2a. API / tool-use benchmarks

| ID | Paper (year, venue) | What it does (given / grading) | Result that matters | Borrow |
|---|---|---|---|---|
| 2304.08244 | API-Bank (2023, EMNLP) | Runnable eval with 73 API tools and 314 annotated dialogues (753 calls). Model gets API docs. Graded on calls and responses. | Early benchmark; frames plan / retrieve / call as separate skills. | Split failures by stage (our factorial design does this by protocol feature). |
| 2307.16789 | ToolLLM / ToolBench (2023, ICLR) | 16,464 RapidAPI REST APIs. LLM-generated instructions and solution paths. | Scale comes from LLM-written tasks over real APIs. | Counter-example: real APIs drift and LLM labels are noisy. |
| 2403.07714 | StableToolBench (2024, ACL) | Virtual API server (cache plus LLM simulators). GPT-4 judges "solvable pass/win rate". | Fixes API-status drift, but the judge is an LLM. | Our mock engine plus no LLM judge is the cleaner version of the same fix. |
| 2306.06624 | RestGPT / RestBench (2023) | LLM plans over real REST APIs (OpenAPI docs). Human-annotated gold solution paths. | Spec-driven REST use. Grading by path match (detail not re-checked). | Closest "REST" benchmark. Input is specs, not traffic. |
| 2305.15334 | Gorilla / APIBench (2023, NeurIPS) | API-call generation from docs. Eval by AST matching against reference. | Source of AST-based output grading. Mentions hallucinated API calls. | Memorisation risk is the same one we saw with GoDaddy. |
| BFCL (no arXiv ID; ICML 2025, Patil et al., "The Berkeley Function Calling Leaderboard (BFCL): From Tool Use to Agentic Evaluation of LLMs") | Verified only via the Semantic Scholar record (662 citations) and the ICML 2025 poster listing. v3 multi-turn (blog, content-checked): the model gets function docs only and must explore state. **State-based check** at the end of each turn: backend state equals ground-truth end state, `_`-prefixed attributes excluded, any valid path accepted. **Response-based check** for read-only calls: ground-truth calls must be a subset of the model's calls. A turn passes only if both pass. 20-step cap. 200 base entries plus 4 augmented sets (Missing Params, Missing Functions, Long-Context, Composite). | Paper abstract: top models are strong single-turn; memory, dynamic decisions and long horizons remain open. I found no scores in the blog. | Two-checker split: state for writes, response for reads. Exclude private attributes from diffs. |
| 2409.03797 | NESTFUL (2024, EMNLP) | 1,800+ executable nested call sequences (output of call A feeds call B). Full-sequence match plus win-rate. | GPT-4o: 28% full-sequence match, 60% win-rate (at publication). | Our "login token feeds later calls" and "cursor feeds next page" are nested sequences. Report sequence-level and call-level scores. |
| 2501.10132 | ComplexFuncBench (2025) | Multi-step, constrained, 128k-context function calling over real APIs. Automatic ComplexEval. | Shows long parameter filling and value reasoning fail. | Long HAR (70% noise) is the same long-context stress. |
| 2408.04682 | ToolSandbox (2024, Apple) | Stateful tools, implicit state dependencies, LLM user simulator. **Milestones** (DAG of required events) and **minefields** (forbidden events). Per-milestone similarity in [0,1] (exact, ROUGE-L, AST). Score = best turn-to-milestone mapping x indicator(no minefield hit). 1,032 scenarios, 34 tools, 11 domains. | GPT-4o 73.0, Claude-3-Opus 69.2 average similarity. Hard categories: State Dependency, Canonicalization, Insufficient Information. | Milestone/minefield scoring gives partial credit and a shaped reward. Per-category tags allow attribution. |
| 2406.12045 | tau-bench (2024) | Model gets a domain policy doc plus tool schemas; LLM user. Reward r = r_action x r_output in {0,1}: final DB **identical** to the unique ground-truth DB, and required substrings present in replies. Introduces pass^k. | gpt-4o: below 50% pass^1, below 25% pass^8 on retail. | Unique-outcome task design. pass^k over seeds. |
| 2506.07982 | tau2-bench (2025) | Adds Telecom with a dual-control Dec-POMDP. **Compositional task generator** builds verifiable tasks from atomic components with controlled complexity. | Secondary: official board overall about 88% (Qwen3.5-397B, Oct 2026); telecom 97-99%. Near saturation. | Atomic components plus composition rule is the template for our feature switches. |
| 2407.18901 | AppWorld (2024, ACL) | 9 apps, 457 APIs, about 100 fictitious users. 750 tasks (250 scenarios x 3). Model writes code and reads API docs via `apis.api_docs`. **State-based unit tests on a DB diff.** | GPT-4o: 48.8 TGC on Test-N, about 30 on Test-C. TGC drops from 58.3 to 21.0 as difficulty goes 1 to 3. | See section 3 for exact mechanisms. |
| 2607.20536 | AppWorld-UL (2026) | 516 tasks with ambiguity needing user interaction. Knowledge-bounded LLM user. | Claude Opus 4.7: 48.6% overall, 21.3% scenario-level on the compositional subset. Not saturated. | Scenario-level metric (all variants must pass). |
| 2604.18934 | AutomationBench (2026, Zapier) | Cross-app REST workflows. Agent gets BM25 search over API schemas plus an execute tool. **End-state, programmatic grading, no LLM judge.** Distractor and misleading records. | Abstract: "best frontier models score below 10%". An independent variant (AA, 657 tasks) reports about 48% on a different metric (secondary, UNVERIFIED). | Must discover endpoints (we give traffic). Distractor records in state. |
| 2510.25726 | Toolathlon (2025) | 32 apps, 604 tools (MCP), realistic initial states, execution-based checks. | Long-horizon, diverse. | Realistic initial states. |
| 2508.20453, 2508.14704 | MCP-Bench (2025); MCP-Universe (2025) | Live MCP servers. Fuzzy instructions. Multi-faceted scoring. | Tool retrieval plus planning. | Live servers drift; we avoid them. |
| 2501.02506 | ToolHop (2025, ACL) | Multi-hop tool use, query-driven. | Multi-hop chaining. | Optional. |
| 2412.14161 | TheAgentCompany (2024, NeurIPS) | Consequential workplace tasks on self-hosted apps. | Checkpoint-based partial credit. | Checkpoint credit idea (not re-verified in text). |

### 2b. Execution-based code benchmarks and environments

| ID | Paper | What it does | Result that matters | Borrow |
|---|---|---|---|---|
| 2310.06770 | SWE-bench (2023, ICLR) | GitHub issue to patch, hidden unit tests. | Defines the fail-to-pass / pass-to-pass pattern. | Pass-to-pass = "don't break other behaviour" (our collateral check). |
| 2506.09289 | UTBoost (2025, ACL) | LLM-generated extra tests for SWE-bench. | 345 erroneous "passed" patches; affects 24.4% of Verified leaderboard entries. | Test the grader, not just the model. |
| 2603.00520 | SWE-ABS (2026) | Adversarial strengthening: coverage-driven tests plus mutation (plausible but wrong patches). | Rejects 19.71% of passing patches; top Verified score 78.80 -> 62.20. | Mutation-test our graders (idea 5). |
| 2509.16941 | SWE-Bench Pro (2025) | 1,865 tasks, 41 repos, held-out and commercial sets. | Aimed at contamination and long horizon. Secondary: OpenAI stopped reporting Verified (Feb 2026), UNVERIFIED. | Held-out private split. |
| 2410.03859 | SWE-bench Multimodal (2024) | 617 JS tasks with images. | Visual and cross-language generalisation weak. | Optional. |
| 2505.23419; 2505.20411 | SWE-bench-Live (2025); SWE-rebench (2025) | Continuously refreshed, decontaminated tasks. | Fresh instances against contamination. | Procedural freshness. |
| 2601.11868 | Terminal-Bench 2.0 (2026) | 89 hard terminal tasks, each with its own environment, human solution and tests. | Frontier agents below 65% at publication. | Per-task env plus reference solution plus tests. |
| 2504.02605 | Multi-SWE-bench (2025) | Multilingual issue resolving. | Language breadth. | Optional. |
| 2502.12115 | SWE-Lancer (2025, ICML) | Freelance tasks, end-to-end tests, payout metric. | Tests written by pros. | Optional. |
| 2406.15877 | BigCodeBench (2024, ICLR) | Code with diverse function (library) calls. | Library-use correctness via tests. | Optional. |
| 2406.07411; 2407.06249 | VersiCode (2024); CodeUpdateArena (2024) | API version and update sensitivity. | Models use stale API knowledge. | Backs our memorisation concern. |
| 2406.11612; 2509.09614 | Long Code Arena (2024); LoCoBench (2025) | Long-context code. | Long-context degradation. | Pair with 70% HAR noise. |
| 2505.07473; 2412.00535 | Web-Bench (2025); FullStack Bench (2024) | Web-dev code benchmarks. | Docs-driven, not traffic-driven. | None found that integrate a third-party API from traffic. |

### 2c. Synthetic / procedural environments and RL

| ID | Paper | What it does | Result that matters | Borrow |
|---|---|---|---|---|
| 2504.21798 | SWE-smith (2025, NeurIPS) | Build an env for any Python repo; synthesise 100s-1000s of tasks that break existing tests. 50k instances, 128 repos. | SWE-agent-LM-32B: 40.2% on Verified. | "Break something that works" = mutate the engine feature switches. |
| 2504.07164 | R2E-Gym (2025) | 8.7K tasks; SynGen back-translates tests and issues from commits; hybrid verifiers. | 34.4% (32B); 51% with hybrid test-time scaling; test-based verifiers have "low distinguishability". | Back-translation (engine to traffic). Verifier weakness. |
| 2412.21139 | SWE-Gym (2024, ICML) | 2,438 real tasks with runtime and tests. | +19% absolute on Verified/Lite. | Baseline for "env as training set". |
| 2505.24760 | Reasoning Gym (2025, NeurIPS) | 100+ generators and verifiers; adjustable complexity; "virtually infinite" data. | Continuous evaluation across difficulty levels. | Difficulty knob = number of enabled switches. |
| 2602.10090 | Agent World Model (2026, ICML) | 1,000 synthetic, code-driven, **database-backed** environments, about 35 tools each; reward functions use accessible DB state. | Synthetic-only training generalised to 3 benchmarks (details not in the abstract). | Database-state reward; contrast with LLM-simulated envs. |
| 2605.18703 | EnvFactory (2026) | Automatically explores and verifies stateful executable envs; topology-aware trajectory sampling. | 85 envs yield 2,575 trajectories; up to +15% BFCLv3, +6% tau2/VitaBench. Fewer envs beat prior 5x. | Quality-verified envs beat raw count. Verify each env before use. |
| 2509.13311 | AgentScaler / Environment Scaling (2025, ACL) | Automatic construction of fully simulated heterogeneous function-calling envs; two-phase fine-tuning. | Gains on tau-bench, tau2, ACEBench. | Environment diversity predicts function-calling breadth. |
| 2603.10505 | VeriEnv (2026) | Clones real sites into executable envs with a Python SDK; self-generated tasks with deterministic rewards, no LLM judge. | Scaling env count helps; generalises to unseen sites. | Clone-then-verify, same philosophy as our mocks. |
| 2504.13958 | ToolRL (2025, NeurIPS) | Reward design study for tool RL (GRPO). Correctness = tool-name Jaccard + param-name Jaccard + param-value exact match, scaled to [-3,3]; plus binary format reward. | BFCL overall, Qwen2.5-1.5B: 46.2 (fine-grained) vs 36.7 (coarse); Llama-3.2-3B 44.1 vs 36.0. Abrupt two-stage reward switch hurts. | Graded reward over method / param / value. Avoid hard stage switches. |
| 2505.00024 | Tool-N1 (2025) | Binary rule-based reward on format plus functional correctness. | Pure RL not worse than SFT-then-RL. | Binary is a fine primary reward. |
| 2502.18449 | SWE-RL (2025, NeurIPS) | Rule-based similarity reward to the gold patch. | 41.0% on Verified (Llama3-70B). | Similarity reward is usable but proxy-only. |
| 2411.02337 | WebRL (2024) | Self-evolving curriculum: new tasks from failed attempts; outcome reward model. | WebArena-Lite: Llama-3.1-8B 4.8 -> 42.4. | Failure-driven curriculum. |
| 2509.08755 | AgentGym-RL (2025) | Multi-turn RL framework; ScalingInter-RL grows the interaction horizon over training. | Stable long-horizon RL. | Horizon curriculum. |
| 2504.03601; 2409.00920; 2406.18518 | APIGen-MT (2025); ToolACE (2024); APIGen (2024) | Verified synthetic function-calling data (blueprints with ground-truth actions, committee review). | xLAM-2 beats GPT-4o on tau-bench/BFCL. | Verify before train (execute and check). |
| 2412.19723 | OS-Genesis (2024, ACL) | Reverse task synthesis: act first, then derive the task, then reward-model filter. | GUI-agent trajectories without predefined tasks. | Direct analogue of "HAR first, goal second". |
| 2503.09516 | Search-R1 (2025) | RL with outcome reward for tool-like search. | Outcome-only reward trains tool use. | Optional. |

### 2d. Web agents, API vs browsing, traffic

| ID | Paper | What it does | Result that matters | Borrow |
|---|---|---|---|---|
| 2307.13854 | WebArena (2023, ICLR) | Self-hosted sites, functional-correctness checks. | Reproducible, resettable environments. | Fresh instance per run. |
| 2401.13649; 2403.07718; 2306.06070; 2407.15711 | VisualWebArena (2024); WorkArena (2024, ICML); Mind2Web (2023, NeurIPS); AssistantBench (2024, EMNLP) | UI-level benchmarks. AssistantBench: 214 tasks, none above 25 points. | UI actions, not network requests. | Contrast class. |
| 2506.01952 | WebChoreArena (2025) | Tedious multi-step web chores. | Long-memory stress. | Optional. |
| 2410.16464 | Beyond Browsing: API-Based Web Agents (2024, ACL) | API agents vs browsing agents on WebArena; hybrid agent. | Hybrid 38.9% (+24.0 absolute over browsing alone). APIs help when available. | Motivation: a client compiled from traffic is the missing "API" for sites without docs. |
| 2604.00694 | Internal APIs Are All You Need / Unbrowse (2026) | Learns first-party "shadow API" routes from browsing traffic into a shared route graph. 94 domains, live-web benchmark vs Playwright. | 3.6x mean, 5.4x median speedup; about 950 ms vs 3,404 ms. | Closest to our setting but evaluates latency, not typed-client correctness. |
| 2402.05102 | RESTSpecIT (2024, v2 2025) | Infers OpenAPI docs by LLM-mutated live probing. | Probing, not recorded traffic. | Spec-inference baseline. |

## 3. Gaps (what har2client does that no paper does)

1. **Input is observed traffic, not docs.** AppWorld (`apis.api_docs`), tau-bench (policy plus tool schemas), BFCL (function docs), RestBench (OpenAPI) and AutomationBench (BM25 over schemas) all give specs. Closest on traffic: Unbrowse (2604.00694) learns routes from traffic but measures speed. RESTSpecIT infers specs by probing a live API. OS-Genesis uses the "act first, derive the task later" idea but for GUIs. I found no benchmark where the model must recover auth flow, pagination and headers from a noisy recording. Noise (70% browser requests) is also absent elsewhere; AutomationBench's distractors are in state, not in the input.
2. **The graded artefact is code, run later on a fresh instance.** AppWorld and tau-bench grade an online agent's actions. har2client grades a reusable `client.py` on a new host, new credentials and new data. That tests generalisation (no hardcoded ids, hosts or tokens) and memorisation, which nobody checks. Nearest: AppWorld's contrast sets and VersiCode/CodeUpdateArena on API-knowledge staleness.
3. **Factorial attribution to protocol features.** ToolSandbox tags categories; tau2 ablates reasoning vs communication. None crosses auth x pagination x expiry x ETag x envelope in a designed experiment.
4. **No LLM judge on a saturated headline.** Beam at 0.963/0.996 on Tier A shows saturation. SWE-ABS and UTBoost show saturated test-based numbers can be inflated by weak checks, so the grader needs its own audit (see `har2client/results/grader_audit_set30.md`).

### State-based grading mechanisms to copy (precise)

- **AppWorld diff check** (paper section 3.2): compute the DB diff D_Δ between start and end state (fast hash-based). Require C_expect ⊆ D_Δ (expected changes) and D_Δ ⊆ C_expect ∪ C_allow (nothing else changed except allowed non-mandatory changes). This catches "collateral damage" without enumerating bad states. Each assertion also gets a natural-language statement.
- **AppWorld TGC and SGC**: TGC = tasks passing all tests; SGC = scenarios where all 3 task variants (different initial states) pass. Catches hardcoding.
- **AppWorld validation solutions**: a programmatic reference solution must pass the evaluator from the task DB. This proves solvability. For us: a reference client must pass from the HAR alone, which proves the HAR contains enough information.
- **tau-bench reward**: r = r_action x r_output. r_action: final state identical to the unique ground-truth outcome. r_output: required substrings in replies (for us: return values). Tasks are written so only one outcome is valid. pass^k = all k i.i.d. trials succeed.
- **BFCL split**: state check for writes; subset-of-ground-truth-calls check for reads; skip `_`-prefixed state fields; any path accepted.
- **ToolSandbox**: milestones (DAG) and minefields. A minefield hit zeroes the score.
- **tau2**: compositional generator from atomic components, so difficulty is controlled and tasks are verifiable by construction.

## 4. Caveats

- Saturation levels for API-Bank, ToolBench, RestBench and ComplexFuncBench are not stated because I did not retrieve current numbers. Treat any such claim as unknown.
- tau2 and SWE-bench figures marked "secondary" come from search snippets of aggregator sites, which sometimes disagree. Cite official boards before using them in the paper.
- AutomationBench "below 10%" is from the paper abstract. The ~48% figure is from a different metric on a different task set (Artificial Analysis variant). Do not compare them.
- BFCL has no arXiv ID in my searches. Cite as ICML 2025 (Patil et al.). Multi-turn grading details come from the project's own blog.
