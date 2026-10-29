# R4: Theory frame - programming by demonstration, spec inference, generalisation (2026-10-24)

## 1. Search log

- OpenAlex (about 35 queries, 6-8 hits each) and Crossref (about 12 bibliographic queries) for discovery and DOI checks. OpenAlex hit its free daily budget partway through; later checks used Crossref and arXiv.
- arXiv API and Semantic Scholar returned HTTP 429 for most of the session, so the bundled arXiv scripts were mostly unusable. Fallback: each arXiv ID was checked by fetching `arxiv.org/abs/<id>` and reading `citation_title`, `citation_date` and `citation_author`. Title and year matched for every arXiv ID listed below.
- WebSearch, 11 queries. Topics: HAR-to-API tools, PBD for LLM agents, Daikon vs LLM, L* with LLMs, clarification benchmarks, hard-coding and test exploitation, Okta HAR breach, pragmatic synthesis, REST spec inference, deprecated APIs.
- Query topics: PBE/PBD lineage, web scraping by demonstration, induction vs transduction, trace-based synthesis, Daikon and LLM spec inference, API spec mining from traffic, active automata learning, clarifying questions, overfitting and test exploitation, secrets in code and HAR files.
- Not verified, so excluded: Pei et al. "Can LLMs reason about program invariants?" (ICML 2023). I saw it only in a search snippet.
- Numbers below come from abstracts I fetched. Items marked (snippet) come from search summaries of abstracts only.
- Finding: I found no paper or benchmark that evaluates "HAR in, executable client out" on a fresh environment. The nearest are listed in the Gaps section.

## 2. Papers

IDs are arXiv IDs or DOIs. Years are the first-appearance year.

### 2a. Programming by example and demonstration

| ID | Title | Year, venue | What it does | Matters / borrow |
|---|---|---|---|---|
| 10.1145/1926385.1926423; 10.1145/2814270.2814310; 10.1145/3571226 | FlashFill (Gulwani); FlashMeta (Polozov, Gulwani); FlashFill++ | 2011 POPL; 2015 OOPSLA; 2023 PACMPL | DSL plus version-space PBE. A ranker picks among many consistent programs. | Names the core problem: many programs fit the demo, so a ranking prior decides. Borrow "consistent set plus ranker" as our ambiguity model. |
| 10.1145/2983990.2984020 | Ringer: web automation by demonstration | 2016 OOPSLA | Records low-level browser events and replays them. | Closest classical task to ours. Its central problem is replay breaking when the page changes. Our fresh mock is the same test. |
| 10.1145/3242587.3242661 | Rousillon (Chasins et al.) | 2018 UIST | Scraping by demonstration. Generalises one demo to hierarchical, relational data. | Borrow its "demo plus loop generalisation" framing for pagination. |
| (no ID) Chasins PhD thesis "Democratizing Web Automation" (Helena). Found in OpenAlex only. | | 2019 UC Berkeley | End-user web automation by demonstration. | Citable as thesis. Not verified beyond the OpenAlex record. |
| 10.1145/3453483.3454046 | DIY assistant (Diya) | 2021 ACM, PLDI 2021 volume | Multimodal end-user programming of a virtual assistant. | Demonstration plus natural-language intent. Same pair as HAR plus goal.md. |
| 10.1145/3519939.3523711 | WebRobot: web RPA via interactive PBD | 2022 PLDI | Synthesizes web automation from a partial action trace. It predicts the next action and asks the user to confirm. | Closest to our "generalise to unseen inputs" claim. It treats the demo as a prefix, not a full spec. |
| 1703.03539 | Interactive Program Synthesis (Le, Perelman, Polozov, Gulwani) | 2017 arXiv | Formalises ambiguity in PBE. Clarifying interaction narrows the program set. | Gives the identifiability vocabulary: examples leave a space of programs, and queries shrink it. |
| 10.1145/3385412.3386025 | Question selection for interactive program synthesis (Ji et al.) | 2020 PLDI | Picks the question that best splits the remaining program set. | Borrow for an optimal-query oracle baseline in the active condition. |
| 2311.05740 | Generating Pragmatic Examples to Train Neural Program Synthesizers (Vaduguru, Fried, Pu) | 2023 arXiv (ICLR 2024, snippet) | Informative examples chosen by Rational Speech Acts help the synthesizer. | Shows demo informativeness is a controllable variable. Borrow: choose demos by informativeness, not at random. |
| 10.1145/3368089.3409732 | Inductive program synthesis over noisy data | 2020 FSE | PBE when some examples are wrong. | Template for the misleading-demo condition. |
| 2406.08316 | Is Programming by Example solved by LLMs? (Ellis, Sablé-Meyer) | 2024 NeurIPS | Pretrained LLMs are weak at PBE. Fine-tuning helps only in-distribution. | Our held-out fresh-mock grading is an OOD test. Same finding to expect. |
| 2503.23145 | CodeARC (Wei et al.) | 2025 arXiv | Agents may query a hidden function and use a differential-testing oracle. 1114 functions, 18 models. | Direct precedent for passive vs active, on pure functions only. Borrow its query-budget protocol. |
| 2411.02272 | Combining Induction and Transduction for Abstract Reasoning (Li et al.) | 2024 arXiv | Induction writes a program. Transduction predicts the output directly. The two solve different tasks. | Our setting is induction by design. A cheap control: a "transductive" arm that answers goal calls from memorised HAR responses. |
| 2305.16744 | Demo2Code (Wang et al.) | 2023 arXiv | Summarise demos into a spec, then generate code from the spec. | Two-stage "HAR to spec to client" arm. It lets us score spec quality apart from code quality. |

### 2b. PBD for LLM agents and shadow APIs

| ID | Title | Year | Result that matters / borrow |
|---|---|---|---|
| 2510.10049 | ALLOY: reusable agent workflows from user demonstration | 2025 | Demos beat prompts for capturing intent in a 12-person study. Borrow: demos carry intent that prompts omit. |
| 2510.14308 | ReUseIt: reusable agent workflows with execution guards | 2025 | Synthesises workflows from successful and failed attempts. Success 24.2% to 70.1% over 15 tasks. Borrow: failed attempts in the trace are signal and noise at once. |
| 2606.20978 | How Should Agents Read Demonstrations? | 2026 | Same actions, different structure. Hierarchical grouping lifts pass rate 76.7% to 90.7% on vague tasks (p=0.034), no gain on precise ones. Borrow: demo-format ablation (raw HAR, filtered, grouped). |
| 2604.00694 | Internal APIs Are All You Need (Unbrowse) | 2026 | Learns routes passively from browsing traffic, then calls them directly. 3.6x speedup over Playwright on 94 domains. System paper, no correctness benchmark. |
| 2504.07079 | SkillWeaver | 2025 | Agents explore a site, write reusable API-like skills, and test them. Borrow: active exploration as a distinct data source. |

### 2c. Spec inference from traces, API mining

| ID | Title | Year, venue | What it does / borrow |
|---|---|---|---|
| 10.1109/32.908957 | Daikon: dynamically discovering likely invariants (Ernst et al.) | 2001 TSE | Invariants from observed runs. They are only as good as the runs: "likely, not sound". This is exactly our demo problem. |
| 10.1145/503272.503275 | Mining specifications (Ammons, Bodik, Larus) | 2002 POPL | Temporal API specs from traces. Defines API usage mining. |
| 2503.04779 | FormalBench (Le-Cong et al.) | 2025 | LLMs on formal spec inference. Daikon-style tools often give trivial invariants. |
| 2509.21629 | Quokka / InvBench | 2025 | Argues that comparing LLM invariants to Daikon output by string match is flawed. Use solver-based checks (snippet). Lesson: judge by behaviour, which our fresh mock does. |
| 2608.13240 | Can Formal Specifications Be Synthesized from Tests Alone? | 2026 | Closest to "spec from a handful of observed runs". Borrow its test-only setting for our "k demos" arm. |
| 10.1145/3660791 | Can LLMs transform NL intent into postconditions? (nl2postcond) | 2024 PACMSE | Metrics for correctness and discrimination of inferred specs. Borrow "does the spec reject wrong behaviours". |
| 10.1109/icse48619.2023.00167 | Carving UI tests to generate API tests and API specification | 2023 ICSE | OpenAPI from recorded UI sessions. Scored against the spec, not by running a client. |
| 10.1007/978-3-319-61482-3_16 | Example-driven web API specification discovery | 2017 LNCS | Spec from example calls. |
| 2603.23852 | APISENSOR | 2026 | API discovery from runtime traffic logs, noise-robust. Closest on the noise side. |
| 2402.05102 | RESTSpecIT | 2024 | LLM infers an OpenAPI spec by probing a live API. Reports 88.62% of routes found (snippet). Precedent for active probing of web APIs. |

### 2d. Active learning, queries, clarification

| ID | Title | Year, venue | What it does / borrow |
|---|---|---|---|
| 10.1016/0890-5401(87)90052-6 | Angluin, Learning regular sets from queries and counterexamples (L*) | 1987 | Membership and equivalence queries make learning polynomial. Passive learning from a sample alone cannot match this. Gives the theory for the active condition. |
| 10.1145/2967606 | Vaandrager, Model learning | 2017 CACM | Applies L* to real protocols and services (SSH, TCP). Practice precedent for a black-box service. |
| 2402.07051 | L*LM (Vazquez-Chanlatte et al.) | 2024 | Learns automata from demos plus an LLM as membership oracle with an "unsure" answer. |
| 2410.08197 | DRAFT (titled "From Exploration to Mastery") | 2024 | Agents rewrite tool docs from their own trial calls. Borrow: probing improves documentation. |
| 2503.22674 | QuestBench | 2025 | LLMs struggle to pick the right clarifying question (40-50% on Logic-Q and Planning-Q). Warns that agents may waste queries. |
| 2208.05950 | Interactive Code Generation via Test-Driven User-Intent Formalization (TiCoder) | 2022 | Asks the user to approve tests, which prunes candidate programs. |

### 2e. Copying vs abstraction, overfitting to visible examples

| ID | Title | Year, venue | What it does / borrow |
|---|---|---|---|
| 2307.02477 | Reasoning or Reciting? (Wu et al.) | 2023 NAACL | Counterfactual task variants separate recall from reasoning. Borrow for value-swapped HARs. |
| 2510.03415 | LLMs Lean on Priors, Not Programming Language Semantics | 2025 | Models follow priors over semantics. Supports a memorisation-of-real-API risk. |
| 2510.20270 | ImpossibleBench | 2025 | Measures how often models exploit tests when the spec and tests conflict. Borrow: a conflicting-demo condition. |
| 2512.21028 | Artificial or Just Artful? | 2025 | Models use test cues even when told not to. |
| 10.1145/2786805.2786825 | Is the cure worse than the disease? Overfitting in program repair (Smith et al.) | 2015 FSE | Patches that pass visible tests but fail held-out tests. Same failure as a client that hard-codes the demo. |
| 2305.01210 | EvalPlus | 2023 | Weak test sets over-credit wrong code. Argues for held-out, richer tests. |
| 2004.07780 | Shortcut Learning in Deep Neural Networks (Geirhos et al.) | 2020 | Defines shortcut learning. Borrow the vocabulary. |
| 2406.09834 | LLMs Meet Library Evolution (deprecated API usage) | 2024 | Models emit deprecated or memorised API forms. Matches our GoDaddy path-from-memory error. |
| 2404.11018 | Many-Shot In-Context Learning | 2024 | More in-context examples help on many tasks. Prior for the k-demos curve. |
| 10.1006/jcss.1995.1003 | Goldman and Kearns, On the complexity of teaching | 1995 JCSS | Teaching dimension: the least examples that identify a concept. Formal basis for a per-task identifiability label. |

### 2f. Secrets and privacy

| ID | Title | Year | What it does / borrow |
|---|---|---|---|
| 10.1145/3660818 | Your Code Secret Belongs to Me (Huang et al.) | 2024 PACMSE | Code-completion tools memorise and emit hard-coded credentials. Borrow: a secret-emission metric. |
| 2503.09780 | AgentDAM | 2025 | Measures privacy leakage by web agents. Borrow: data-minimisation scoring. |
| (news, not a paper) Okta support-system breach, Oct 2023 | | 2023 | Attackers used session tokens in customer HAR files. Okta advised sanitising HARs. Source: Krebs, BleepingComputer (secondary coverage). Motivates the metric. |

## 3. Gaps (what no paper does, with nearest work)

1. No benchmark scores "recorded traffic in, executable client out" against a fresh environment. Nearest: Unbrowse (2604.00694) has latency numbers only. Carving UI Tests and APISENSOR score the inferred spec, not a running client.
2. No identifiability labels. PBE papers discuss ambiguity (Le et al. 1703.03539; Goldman and Kearns), but no benchmark states, per task, which behaviours the demo determines and which must be guessed. A stateful engine can compute this by replay, as described in idea 1.
3. No controlled passive-vs-active comparison on stateful, authenticated web behaviour. CodeARC (2503.23145) does it for pure functions. RESTSpecIT (2402.05102) probes APIs but starts from the API name and scores route coverage.
4. No measure of copy vs abstract for API clients. Counterfactual testing exists for reasoning (2307.02477), but not for hard-coded observed values.
5. No study of adversarial or misleading HARs (failed attempts, stale endpoints, injected text). Noisy PBE (10.1145/3368089.3409732) covers wrong examples for pure functions only.
6. Secret leakage from HAR into generated clients is untested, though the Okta case shows the exposure in real HARs.
