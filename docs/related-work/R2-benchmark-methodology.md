# R2: How to build a rigorous, publishable benchmark (agentic/code focus)

Date: 2026-10-24. Scope: methodology for har2client (Tier A saturated at 0.963/0.996; Tier B factorial, invented APIs).

## 1. Search log

- paper-lookup scripts: `arxiv_atom.py` (title and id_list queries; arXiv throttled me with empty replies after about 10 calls, so I switched to OpenAlex DOI lookups `10.48550/arxiv.<id>` for batch verification). Semantic Scholar returned 429.
- OpenAlex: 3 batch lookups, about 70 IDs. Each ID resolved to a title that matches. For several, OpenAlex returned the published version (NeurIPS/ACL/ACM DOI) instead of the arXiv record. In those cases title and year match the arXiv ID.
- WebSearch: about 12 queries. Topics: IRT for LLM benchmarks, SWE-bench grader flaws, code renaming contamination, dynamic/procedural benchmarks, saturation, eval statistics, construct validity, reward hacking, run-to-run variance, SWE-bench memorisation, half-life, contamination reporting, NeurIPS 2026 hosting.
- WebFetch: arXiv abstract pages for about 25 papers. Full-text extraction (pdftotext) for ABC (2507.02825) and BetterBench (2411.12990). NeurIPS 2026 hosting page. Miller (2411.00640) HTML.
- Not found: no paper using the phrase "benchmark half-life" (closest: 2602.16763, 2511.01365). No single paper combines clustered SEs and multiple-comparison correction for LLM evals. For multiple comparisons I cite none; use standard Holm/Benjamini-Hochberg and say so.
- Everything below was fetched and title-matched. Numbers come from abstracts or full text I read. Where I only saw the title, I say "title only".

## 2. Papers

| ID | Title (year, venue) | What it does | Number that matters to us | Borrow |
|---|---|---|---|---|
| 2411.12990 | BetterBench (2024, NeurIPS D&B spotlight) | 46 best practices over benchmark lifecycle; scored 24 benchmarks | Weakest areas: implementation, maintenance. Mean 5.62 on "report statistical significance", 3.75 on "script to replicate". Checklist in App. I | Fill the BetterBench checklist (App. I) in our appendix |
| 2507.02825 | Establishing Best Practices for Building Rigorous Agentic Benchmarks, ABC (2025, arXiv) | Checklist: task validity T.1-10, outcome validity O.*, reporting R.1-13; applied to 10 benchmarks | Flaws can mis-estimate agent performance by up to 100% relative. Applying ABC to CVE-Bench cut overestimation by 33% | Item-by-item compliance table (see section 4) |
| 2511.04703 | Measuring what Matters: Construct Validity in LLM Benchmarks (2025, NeurIPS D&B) | Review of 445 benchmarks by 29 experts; 8 recommendations | Defining the phenomenon and measuring only it; planning for contamination | A "construct" paragraph: we measure trace-to-client synthesis, not API recall |
| 10.1145/3458723 | Datasheets for Datasets (Gebru et al., 2021, CACM) | Question template for dataset documentation | Motivation/composition/collection/uses/maintenance sections | Datasheet in appendix (HAR source, noise generation, specs) |
| 10.1145/3531146.3533231 | Data Cards (Pushkarna et al., 2022, FAccT) | Structured dataset documentation | Telescopic/periscopic/microscopic detail levels | Optional; datasheet is enough |
| 10.1145/3650203.3663326 | Croissant: A Metadata Format for ML-Ready Datasets (Akhtar et al., 2024) | Machine-readable dataset metadata | Required by the NeurIPS Evaluations & Datasets track | Generate Croissant for the HAR+goal set |
| 2411.00640 | Adding Error Bars to Evals (Miller, 2024) | Treats evals as experiments; SE formulas | 5 recs: CLT SEs, clustered SEs (up to about 3x wider on DROP), resample answers to cut variance, paired differences, power analysis. Example: delta=0.03, 80% power needs about 969 questions | Clustered, paired CIs and power table |
| 2503.01747 | Position: Don't Use the CLT in LLM Evals With Fewer Than a Few Hundred Datapoints (Bowyer et al., 2025) | Shows CLT error bars too narrow on small sets | Use Bayesian/frequentist small-n intervals (library `bayes_evals`) | Wilson/Bayesian intervals for Tier B cells (n small) and for scores near 1.0 |
| 2602.07150 | On Randomness in Agentic Evals (2026) | 60,000 SWE-bench-Verified trajectories | Single-run pass@1 swings 2.2-6.0 points; SD >1.5 at temperature 0; recommends multiple runs, power analysis, pass@k and pass^k | k>=5 runs per task; report pass^k |
| 2608.22331 | Noise Floor Audit for Agent Benchmarks (2026) | BFCL audit: reruns vs paraphrased prompts | Paraphrase noise 11x-58x larger than rerun noise at temp 0 | Paraphrase goal.md and perturb HAR order as a noise-floor test |
| 2510.11977 | Holistic Agent Leaderboard (Kapoor et al., 2025) | Standard harness; 21,730 rollouts, 9 models, 9 benchmarks | LLM log analysis found agents searching HuggingFace for the benchmark itself | Post-hoc log audit for grader probing |
| 2407.01502 | AI Agents That Matter (Kapoor et al., 2024) | Critique of agent benchmarking | Report cost with accuracy; need held-out sets; shortcuts and overfitting | Cost-accuracy table; private held-out split |
| 2406.12045 | tau-bench (Yao et al., 2024, title only) | Tool-agent-user benchmark | Introduces pass^k (all k trials succeed); ABC notes it counted empty responses as success | pass^k; the trivial-agent baseline |
| 2410.05229 | GSM-Symbolic (Mirzadeh et al., 2024) | Symbolic templates generate many instances of each problem | Scores vary across instantiations; drop when only numbers change; irrelevant clause drops up to 65% | Template-instantiation distribution per task, not single instance |
| 2307.02477 | Reasoning or Reciting? Counterfactual Tasks (Wu et al., 2023, NAACL 2024) | 11 tasks with changed default assumptions | Substantial, consistent drop under counterfactual variants | Counterfactual-API variant (renamed/relocated) as control |
| 2503.06643 | Is Your Benchmark Still Useful? Dynamic Benchmarking for Code LMs (Guan et al., 2025) | Semantic-preserving mutations of code-benchmark inputs | All 10 models score lower on mutated versions; rankings shift | Mutation operators (renaming) and report rank shifts |
| 2510.03178 | When Names Disappear (ClassEval-Obf, 2025) | Obfuscated ClassEval removes naming cues | Memorised naming shortcuts inflate scores (no numbers in abstract) | Same logic for API names |
| 2604.21579 | Metamorphic Testing for Memorization in LLM Program Repair (2026) | Semantics-preserving transforms on Defects4J, GitBug-Java | 7 LLMs; drops -4.1% (GPT-4o) to -15.98% (Llama-3.1); drop correlates with NLL | Correlate drop with a memorisation proxy; v1 preprint |
| 2512.10218 | Does SWE-Bench-Verified Test Agent Ability or Model Memory? (2025) | File-localization with issue text only | Models 3x better on SWE-bench Verified than on fresh sets; 6x at finding edited files | "No-HAR" probe (see Top 5) |
| 2609.27891 | Schrodinger's Code Repository (2026) | Memory test before repo access | Results not read (background only) | Cite as related; UNVERIFIED results |
| 2403.07974 | LiveCodeBench (Jain et al., 2024) | Time-stamped new contest problems | 400 problems, May 2023-May 2024; test after model cutoff | Dated "live" split from fresh generator seeds |
| 2505.23419 | SWE-bench Goes Live! (NeurIPS 2025) | Continuously refreshed SWE-bench | Title and venue verified; abstract not read | Refresh protocol |
| 2411.03923 | Evaluation data contamination in LLMs: ConTAM (Singh et al., 2024) | Judges contamination metrics by benefit to scores | Longest-match beats union; large n gives false negatives; model/benchmark-specific thresholds | Report which metric and threshold |
| 2308.08493 | Time Travel in LLMs (Golchin & Surdeanu, 2023, title only) | Contamination detection via guided prompts | -- | Optional |
| 2404.18824 / 2311.01964 | Benchmarking Benchmark Leakage (2024) / Don't Make Your LLM an Evaluation Benchmark Cheater (2023), titles only | Leakage studies | -- | Optional background |
| 2402.14992 | tinyBenchmarks (Polo et al., 2024) | Curated 100-example subsets reproduce MMLU | ~100 examples reproduce a 14K benchmark (abstract does not mention IRT) | Cheap eval on many models |
| 2505.15055 | Lost in Benchmarks? IRT for LLM benchmarking (PSN-IRT, 2025) | IRT on 11 benchmarks, 41,871 items | Poor separability among top models; measurement-quality shortcomings | Per-item difficulty and discrimination plot |
| 2511.04689 | Adaptive Testing for LLM Evaluation (ATLAS, 2025) | IRT + Fisher-information item selection | Up to 90% fewer items; about 41-42 items on HellaSwag (versions differ); 23-31% of models move >10 ranks vs accuracy; 3-6% items negative discrimination (from search snippet) | Flag negative-discrimination items as grader bugs |
| 2407.21227 / 10.1145/3773285 | TaskEval (2025, TOSEM) | IRT for code-gen task difficulty | Title/venue verified, abstract only via search | Cite for code-task IRT |
| 2602.16763 | When AI Benchmarks Plateau (Akhtar, Reuel et al., ICML 2026) | Uncertainty-aware saturation index; 60 benchmarks | Nearly half saturate; rate rises with age; hidden test data gives no protection; expert-curated resists better | Quote to justify Tier B; report an index for Tier A |
| 2511.01365 | The Ouroboros of Benchmarking (2025) | Reasoning-benchmark trends under saturation | Abstract not read | Related |
| 2309.17167 | DyVal (Zhu et al., ICLR 2024) | DAG-based on-the-fly generation, controllable complexity | Accuracy falls as complexity rises | Template for knob-to-difficulty curves |
| 10.18653/v1/2024.acl-long.225 | NPHardEval (ACL 2024) | Complexity-class benchmark, monthly refresh | Refresh-based dynamics | Contrast: ours is parametric |
| 10.52202/085713-1913 | Reasoning Gym (NeurIPS 2025, title only) | Procedurally generated verifiable-reward tasks | -- | Same bench-to-RL-environment path as ours |
| 2506.09289 | UTBoost (Yu et al., ACL 2025) | LLM test augmentation for SWE-bench | 36 instances with weak tests, 345 wrong patches accepted; changed 40.9% of Lite and 24.4% of Verified leaderboard entries | Weak-test analogue for our graders |
| 2603.00520 | SWE-ABS (2026) | Coverage + mutation-driven test strengthening | Strengthens 50.2% of Verified instances; top agent 78.80% to 62.20%; about 1 in 5 "solved" patches wrong | Mutated-client test of our grader |
| 2510.20270 | ImpossibleBench (Zhong et al., 2025) | Tests contradict spec; any pass = cheat | Cheating rate = pass rate on impossible tasks | Contradictory-mock variants |
| 2511.21654 | EvilGenie (2025) | Reward-hacking benchmark | Says ImpossibleBench misses hacks that pass tests | Cite as limits |
| 2609.08149 | SWE-Bench Pro Verified (2026) | Adds anti-hacking safeguards | Models score lower than reported (from search; abstract not read) | Related |
| 10.52202/075280-0943 | EvalPlus (Liu et al., NeurIPS 2023, title only) | Test augmentation for HumanEval | -- | Same idea for grader strength |
| 10.52202/085713-2620 | The Leaderboard Illusion (NeurIPS 2025, title only) | Leaderboard gaming | -- | Disclose private-eval policy |

Not used: 2506.17627 and 2310.17589 did not resolve to a verified title in OpenAlex (UNVERIFIED, not cited).

## 3. Gaps (what this benchmark does that no paper does)

1. Memorisation control for **API knowledge** (not puzzle or repository recall). Closest: 2512.10218 (SWE-bench file-path probe), 2503.06643 and 2510.03178 (renaming). None perturbs a public API's surface while keeping the trace self-consistent. The GoDaddy error is a real example.
2. Mock APIs with **procedural difficulty knobs and IRT**: DyVal (2309.17167) has knobs but no IRT; 2505.15055 and 2511.04689 apply IRT to static benchmarks. We found none that regresses item difficulty on known generator switches (factor attribution).
3. Grader audits for **fresh-mock replay graders**: SWE-ABS (2603.00520) and UTBoost (2506.09289) strengthen unit tests; ImpossibleBench (2510.20270) targets test-passing hacks. None covers a grader whose defence is host/credential/data change.
4. Joint report of clustered, paired CIs with factorial attribution: Miller (2411.00640) covers the first; none combines it with feature-level attribution.
