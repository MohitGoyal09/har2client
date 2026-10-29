# R1: Inferring APIs from traffic, LLM web reverse engineering, tool-making, REST testing metrics

Date of search: 2026-10-24. Scope: literature on traffic-to-API inference.

## 1. Search log

- arXiv Atom API (via `arxiv_atom.py`): about 12 queries (REST spec inference, OpenAPI+traffic, API spec mining, LRASGen-type, APISensor). **The arXiv API returned HTTP 429 mid-session and stayed blocked**, so later arXiv checks used `arxiv.org/abs/<id>` pages through WebFetch.
- OpenAlex search (title/abstract, `oa.py` / `o.sh`): about 45 queries. Topics: spec inference, protocol RE (classic and LLM), tool-making, API-based web agents, HAR-to-client, REST testing, mock/service virtualization, state-machine inference, SDK-generation benchmarks. OpenAlex hit 429 near the end.
- Crossref: 4 bibliographic queries (Discoverer, Netzob, APID2Spec, WebAPISearch). Found nothing usable for APID2Spec/WebAPISearch.
- WebSearch: about 13 queries (tools, protocol RE with LLMs, "traffic to client" benchmarks, APIs.guru as a benchmark, WAPIIBench details).
- WebFetch: about 30 arXiv abstract/HTML pages and 5 GitHub READMEs.
- Every paper in section 2 has a fetched record (arXiv abs page or OpenAlex/Crossref record with matching title and year). Items marked **UNVERIFIED** were not fetched. Abstracts only, not full texts, unless I say so.
- Caveat on the "nobody did it" claim: it rests on about 8 targeted searches plus the citation lists I saw (APISensor baseline list, Unbrowse related work). It is not an exhaustive systematic review.

## 2. Papers

### 2a. API spec inference/mining from traffic or logs

| ID | Title | Year, venue | What it does | Metric that matters to us | Borrow |
|---|---|---|---|---|---|
| arXiv 2603.23852 | APISENSOR: Robust Discovery of Web API from Runtime Traffic Logs | 2026, arXiv | Unsupervised black-box recovery of endpoint templates from mixed, noisy multi-app traffic (Drain3 template mining + graph clustering). Output is API groups, not specs or clients. | PGA / RGA / FGA = precision / recall / F1 of endpoint groups vs ground truth. 6 apps (Train-Ticket, HumHub, Memos, Overleaf, Nextcloud, Dify), >10k requests, 10 baselines incl. Optic, Mitmproxy2Swagger, APICARV. Avg PGA 95.9%, F1 94.9%. | Noise-injection protocol (mixed-app traffic at several noise levels). Endpoint-group P/R/F1 as a diagnostic on "which endpoints did the client call". |
| arXiv 2402.05102 | You Can REST Now: Automated REST API Documentation and Testing via LLM-Assisted Request Mutations (RESTSpecIT) | 2024 (v2 2025), arXiv | LLM generates and mutates requests against a live API and infers an OpenAPI doc. Needs only the API name. | Route recovery 88.62%, query-parameter recovery 89.25% (DeepSeek V3, GPT-4.1, GPT-3.5). Partly measures what the LLM already knows of the API. | Route/parameter recovery rates as secondary metrics. Also a memorisation warning: it works partly because the model remembers popular APIs. |
| DOI 10.1109/ICSE48619.2023.00167 | Carving UI Tests to Generate API Tests and API Specification (ApiCarv) | 2023, ICSE | Replays UI tests, records the API calls, and derives API tests and a spec. | Spec accuracy vs hand-written specs (abstract only read). | Closest classic "recorded UI session -> API spec". It stops at a spec and tests. |
| DOI 10.1145/3597503.3639137 | Generating REST API Specifications through Static Analysis (Respector) | 2024, ICSE | OpenAPI generation from server source code. | Completeness of generated specs vs ground truth. | Spec-diff scoring ideas only. White-box, so not our input. |
| arXiv 2601.12735 | OOPS: Automated generation of REST API specification via LLMs | 2026, arXiv (also J. Syst. Softw.) | LLM-based static analysis of server code to produce OpenAPI. | Spec accuracy on real projects (abstract only). | Same as Respector. |
| arXiv 2504.16833 | LRASGen: LLM-based RESTful API Specification Generation | 2025, arXiv (TOSEM 2026, DOI 10.1145/3810241) | LLM generates OAS from source code. | Spec accuracy (abstract only). | Same as above. |
| arXiv 2607.02442 | HTTP REST API Structure Learning (HRAL) | 2026, arXiv | Unsupervised model of endpoint structure from network traffic, for anomaly detection. | Avg recall 82.07% on attacks, at varying OpenAPI detail levels. | A baseline family APISensor compares with. Security framing. |
| arXiv 2604.19471 | API Security Based on Automatic OpenAPI Mapping (MRG) | 2026, arXiv | Learns routes, methods and parameter formats from traffic, emits OpenAPI-compliant docs. | Up to 11.4% higher recall than HRAL. | Same family. No client generation. |
| DOI 10.1145/3763055 | RestPi: Path-Sensitive Type Inference for REST APIs | 2025, PACMPL | Infers all response types per endpoint from Ruby server code. | 132 endpoints in 5 apps; more precise than types from an HTTP proxy. | Evidence that proxy-observed types are incomplete (response variants not seen in one trace). Supports a "response types unseen in HAR" test. |
| DOI 10.1145/3533767.3534401 | Automated test generation for REST APIs: no time to rest yet | 2022, ISSTA | Empirical comparison of 10 REST testers on shared benchmarks and metrics. | Code coverage and fault finding. Shows that tools evaluated on different benchmarks cannot be compared. | Argument for a fixed shared protocol and public task set. |

### 2b. Network protocol reverse engineering (classic and LLM)

| ID | Title | Year, venue | What it does | Metric | Borrow |
|---|---|---|---|---|---|
| OpenAlex record only (no DOI) | Discoverer: automatic protocol reverse engineering from network traces | 2007 (venue UNVERIFIED) | Foundational message-format inference from traces. | Format accuracy. | Defines the problem. |
| DOI 10.14722/ndss.2021.24531 | NetPlier: Probabilistic Network Protocol Reverse Engineering from Message Traces | 2021, NDSS | Multiple sequence alignment + probabilistic keyword-field identification. | Format-inference accuracy on public protocols. | Field-level correctness against ground truth. |
| DOI 10.14722/ndss.2024.24083 | DynPRE: Protocol Reverse Engineering via Dynamic Inference | 2024, NDSS | Replays and rewrites messages against a live server (session-id detection, message rewriting) to learn semantics. | Beats static-trace methods. | Same issue as ours: replay breaks on session-specific values. Our "fresh credentials/host" is a version of this test. |
| DOI 10.14722/ndss.2024.24556 | Large Language Model guided Protocol Fuzzing (ChatAFL) | 2024, NDSS | LLM extracts message grammars and state machines from RFCs to drive fuzzing. | State/code coverage, bugs found. | LLM reads text, not traces. Shows the LLM-for-protocol line is mostly spec-driven. |
| arXiv 2405.00393 | Unleashing the Power of LLM to Infer State Machine from the Protocol Implementation (ProtocolGPT) | 2024 (v4 2025), arXiv; IWQoS 2025 | LLM + RAG infers state machines from implementation source. | Precision >90% on 6 implementations. | Source-code input, not traffic. |
| arXiv 2507.11222 | An Agentic Flow for Finite State Machine Extraction using Prompt Chaining (FlowFSM) | 2025, arXiv | Agentic prompt chain extracts FSMs from RFC text (FTP, RTSP). | Qualitative only. | Weak evaluation; a contrast case. |
| arXiv 2609.13389 | RFCLLM: Evaluating LLMs' Reasoning Ability of Network Protocol State Machines | 2026, arXiv | Tests whether LLM-read RFC state machines match a hand-made ground truth. | Agreement with ground-truth transition system. | Ground-truth FSM as a gold artifact. |

I found no 2023-2026 paper in which an LLM infers message formats or state machines from raw traces and is scored against hidden execution. LLM protocol work reads RFCs or source code.

### 2c. LLM web agents using APIs, API discovery, tool-making

| ID | Title | Year, venue | What it does | Metric | Borrow |
|---|---|---|---|---|---|
| arXiv 2604.00694 | Internal APIs Are All You Need: Shadow APIs, Shared Discovery... (Unbrowse) | 2026, arXiv | Passively learns first-party routes from browsing traffic (heuristics, not an LLM), shares them in a route graph, calls them directly. | Latency only: 950 ms vs 3,404 ms Playwright (3.6x mean) on 94 domains. **No correctness or task-success metric**; only output-field equivalence, and tasks Playwright failed were dropped. Protected sites gave 2.1x vs 6.8x. | The most-cited claim that "traffic -> callable API" works. Its own limits section is our motivation. |
| arXiv 2410.16464 | Beyond Browsing: API-Based Web Agents | 2024 (v3 2025), ACL Findings 2025 | API-calling and hybrid agents on WebArena. | Hybrid agent 38.9% success, +24 points over browsing. | Evidence that APIs beat GUI. APIs are given, not derived. |
| arXiv 2504.07079 | SkillWeaver: Web Agents can Self-Improve by Discovering and Honing Skills | 2025, arXiv | Agent explores a site and synthesises reusable skills as APIs (code over the browser). | +31.8% (WebArena), +39.8% (real sites) relative. | Practice-and-refine loop. Skills are GUI-backed, not HTTP. |
| arXiv 2604.03465 | The Tool Illusion: Rethinking Tool Use in Web Agents | 2026, arXiv | Controlled study of tool use in web agents across tool sources and models. | Shows earlier gains do not always hold. | Cite when arguing for controlled comparisons. |
| arXiv 2602.17245 | Web Agents Should Use Typed Actions Instead of Click-Based Browsing | 2026, arXiv (position) | Proposes "web verbs": typed functions with pre/postconditions. | None (position paper). | Frames our typed client as a "web verb". |
| arXiv 2506.19998 | Doc2Agent: Scalable Generation of Tool-Using Agents from API Documentation | 2025, arXiv | Generates and iteratively refines Python tools from API docs. | WebArena: +55% relative at 90% lower cost vs direct API calling. | Doc-to-tool, not traffic-to-tool. Same output type as ours. |
| arXiv 2501.16945 | ToolFactory: Automating Tool Generation by Leveraging LLM to Understand REST API Documentations | 2025, arXiv | Open-source pipeline from unstructured API docs to tools. | Abstract only. | Same. |
| arXiv 2305.17126 | Large Language Models as Tool Makers (LATM) | 2023, arXiv | One LLM writes reusable tools, another uses them. | Cost/accuracy gains on reasoning tasks. | Tool-maker vs tool-user split: our task is tool-making, and the hidden tests play the tool-user. |
| arXiv 2309.17428 | CRAFT: Customizing LLMs by Creating and Retrieving from Specialized Toolsets | 2023, arXiv | Creates and retrieves tools from a toolset. | Abstract only. | Tool validation by execution. |
| arXiv 2502.11705 | LLM Agents Making Agent Tools (ToolMaker) | 2025, ACL | Turns a paper+GitHub repo into a working tool with a self-correction loop. | Own benchmark: 15 tasks, >100 unit tests, 80% of tasks implemented correctly. | Closest "tool creation graded by hidden unit tests". Input is a repo, not traffic. |
| arXiv 2507.16044 | From REST to MCP: An Empirical Study of API Wrapping and Automated Server Generation | 2025, arXiv | Studies 116 MCP servers; generates servers from 80 OpenAPI specs. | 88.6% REST-backed; median 19% of operations exposed. | Operation-exposure ratio as a metric. |

### 2d. Execution-graded API/tool benchmarks (closest on grading)

| ID | Title | Year, venue | What it does | Metric | Borrow |
|---|---|---|---|---|---|
| arXiv 2509.20172 (+ 2607.05936) | Benchmarking Web API Integration Code Generation (WAPIIBench) | 2025, AIware; follow-up 2026 | LLM completes code that calls a web API from a comment. Code runs in a sandbox that intercepts the request; the captured request is compared element-wise with ground truth. 395 tasks, 4 APIs (Asana, Google Calendar, Sheets, Slack), 21 open models + GPT-4o. Spec is *not* in context, so models use memory. | No open model solved >40%. Errors: hallucinated endpoints, wrong arguments. | **Closest grader to Tier A**: intercept the request, compare to spec-derived ground truth. It tests memory, not derivation from evidence. |
| arXiv 2602.11224 | Agent-Diff | 2026, arXiv | Agents write code against containerised API replicas. Success = expected state change ("state-diff contract"). 224 tasks, 9 LLMs. | State-diff pass rate. | Template for Tier B semantic-state checks. |
| arXiv 2406.12045 | tau-bench | 2024, arXiv | Success = final database state equals annotated goal state. | pass^k reliability over k trials (GPT-4o pass^8 below 25% retail). | pass^k across fresh mock seeds. |
| arXiv 2403.07714 | StableToolBench | 2024, ACL Findings | Virtual API server (cache + simulators) for stable tool evaluation. | Solvable pass rate (LLM judge). | Argument for mocks over live APIs. We avoid its LLM judge. |
| arXiv 2610.22412 | EnterpriseSynth | 2026, arXiv | Static validation of tool-use data against OpenAPI. | Accuracy fell from 48.9% (endpoint-level) to 21.3% (with arguments). | Evidence that endpoint-level scoring overstates correctness; score arguments. |
| arXiv 2609.01185 | Reveree: Diagnosing LLM Reverse-Engineering Agents | 2026, arXiv | Scores RE agents by milestones; tests recall (flags reproduced from description alone) and perturbation (do solves survive surface changes). | picoCTF showed recall, NYU-CTF minimal. | Direct template for our memorisation controls. |

### 2e. REST API testing with LLMs (borrowable metrics)

| ID | Title | Year, venue | What it does | Metric | Borrow |
|---|---|---|---|---|---|
| arXiv 2312.00894 | Leveraging LLMs to Improve REST API Testing (RESTGPT) | 2023, ICSE-NIER 2024 | LLM extracts rules and example values from NL descriptions into the spec. | Rule extraction, value generation (no numbers in abstract). | Parameter-constraint extraction as a feature tag. |
| arXiv 2411.07098 | A Multi-Agent Approach for REST API Testing with Semantic Graphs and LLM-Driven Inputs (AutoRestTest) | 2024, ICSE 2025 | MARL + semantic property-dependency graph. | Code coverage, **operation coverage**, 5xx faults, vs 4 tools. | Operation coverage (fraction of operations exercised successfully). |
| arXiv 2501.08598 | LlamaRestTest | 2025, FSE 2025 | Fine-tuned small LMs for inputs and parameter dependencies. 12 services. | Dependency rules, input validity, code coverage, 5xx. | "Input validity" and inter-parameter dependency correctness. |
| arXiv 2604.08007 | Log-based, Business-aware REST API Testing (LoBREST) | 2026, arXiv | Uses historical request logs, sliced into operation sequences, as fuzz seeds. 17 services. | 2.1x operation coverage vs runner-up. | Logs as the source of multi-step workflows. Supports Tier B sequences. |
| arXiv 2608.17546 | REST API Testing with Verified LLM-Inferred Dependencies (APIPilot) | 2026, arXiv | Treats LLM-inferred producer/consumer dependencies as hypotheses and validates them by execution. | Abstract only. | Execution-validated hypotheses; supports an "agent may probe" condition. |
| arXiv 2504.17287 | RBCTest: Mine and Verify Oracles of API Response Bodies | 2025, arXiv | LLM mines response-body constraints. | UNVERIFIED beyond title (abstract not read). | Response-body oracle checks for return values. |

### 2f. Open-source tools (HAR/traffic to spec or client). Not peer reviewed; facts from README fetches, 2026-10-24.

| Tool | Claim | Evaluated? | Notes |
|---|---|---|---|
| Integuru (Integuru-AI/Integuru) | HAR + cookies + task prompt -> dependency graph of requests -> runnable Python. | **No** benchmark, success rate or site count in the README. | AGPL-3.0, about 4.8k stars. Needs an OpenAI key. Input variables for code-gen listed as "coming soon". Its dependency-graph idea (find the request that produces each dynamic parameter) is exactly our Tier B chaining. |
| reverse-api-engineer (kalil0321) | Browser capture -> HAR -> client in 10 languages. Manual, re-process, or autonomous agent modes. | **No** metrics. README does not describe validation, only a `run` command. | MIT, about 1.8k stars (README). |
| agent-browser `derive-client` skill (vercel-labs) | Record HAR, find endpoints, extract auth, one function per flow, verify by calling each endpoint. Advises running each flow twice with different inputs to expose parameters. | No evaluation. I read only third-party skill listings (tessl, skills.cat), not the repo skill file. | Repo has 44.5k stars. The "run twice with different inputs" trick is worth copying into task design. |
| web2sdk (jasonwcfan) | HAR -> OpenAPI -> typed Python SDK with Pydantic models. | No. README lists gaps: no OAuth/custom auth, no token refresh, no templated paths. | MIT, 22 stars. No LLM in the current release. Good deterministic baseline. |
| mitmproxy2swagger (alufers) | mitmproxy or HAR -> OpenAPI 3. Two passes; the user edits path templates by hand. | No accuracy claims, but APISENSOR benchmarks it as a baseline. | MIT, about 9.7k stars. |
| Hermes `har-derived-api-client` skill | `har_to_client.py` groups XHR/fetch traffic by endpoint. | No. | Seen only in a search listing. UNVERIFIED beyond that. |

## 3. Gaps

**Key question.** Has anyone evaluated "traffic -> executable client" end to end with hidden execution tests? **I found no such paper.** Evidence by component:

1. **Tools (Integuru, reverse-api-engineer, derive-client, web2sdk, mitmproxy2swagger)** produce clients or specs from HAR. None of their READMEs reports a benchmark, a success rate, or hidden tests. The only third-party evaluation I found is APISENSOR benchmarking mitmproxy2swagger, and it scores endpoint grouping.
2. **Closest on the input side: APISENSOR** (traffic -> endpoint templates, noisy mixed traffic, P/R/F1 against ground-truth endpoints). It never produces or executes a client, and does not infer parameter types or response schemas, as far as the page I read says. **Unbrowse** goes from traffic to callable routes, but measures latency only. It checks output-field equivalence, not correctness, and drops tasks where the baseline failed.
3. **Closest on the grading side: WAPIIBench** (run generated code in a request-intercepting sandbox, compare the request to ground truth element by element). Its input is a natural-language comment plus the model's memory. It has no traffic, covers 4 APIs, and cannot separate derivation from recall. **Agent-Diff** and **tau-bench** grade by state, but the agent acts through tools it was handed.
4. **Closest on the output side: ToolMaker** (agent builds a tool, graded by more than 100 unit tests). The input is a code repository.
5. **Spec inference papers** (RESTSpecIT, ApiCarv, Respector, OOPS, LRASGen) emit OpenAPI and score it against a reference spec. Nobody executes the result.
6. **LLM protocol RE** reads RFCs or source, not traces. Classical trace-based RE (Discoverer, NetPlier, DynPRE) scores field boundaries, not working clients.

**What ours adds, concretely:**
- Input is a raw HAR with about 70% browser noise. APISENSOR has noise, but its target is clustering.
- The output is an executable typed client, tested against a **fresh** mock (new data, credentials, host). This forces use of evidence in the HAR and punishes replaying recorded values. DynPRE's session-specific-value problem is the nearest classic analogue, with no benchmark attached.
- Grading is deterministic (request shape, return values, then state). StableToolBench and similar use an LLM judge.
- Tier B uses invented APIs with a factorial design over auth, pagination, expiry, ETag, hidden headers and envelopes. This separates feature failures from memorisation. WAPIIBench and RESTSpecIT both depend on model memory of real APIs, and Reveree shows recall effects exist in a nearby RE setting.
- Two errors we saw (path taken from memory, base path doubled) match WAPIIBench's hallucinated-endpoint finding. We can measure them with a closed-book control.
- I found no paper that uses APIs.guru as an agent benchmark. That is a search result, not proof.

**Risks to the novelty claim.** (a) Unpublished or closed-source industry evals (Integuru's hosted product, Unbrowse) may exist. (b) A paper that appeared after my search window could exist. (c) The arXiv API was blocked for part of the session, so my arXiv-title coverage relies on OpenAlex and WebSearch. Re-run a title search on arXiv before citing this claim.
