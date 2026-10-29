> Superseded by DESIGN-CASE3-HARD.md (v3); kept for the record.

# Cases 2 and 3: investigate and maintain

For the Case 2 protocol and how to reproduce its runs, see
[../../har2client/results/case2_probe/REPRODUCE.md](../../har2client/results/case2_probe/REPRODUCE.md).

The suite has 3 cases along the life of an API integration: **build** (case 1, traffic -> client, done), **investigate** (case 2), **maintain** (case 3). All three use the Tier B engine, its 6 switches, the semantic grader, and the RL environment (`DESIGN-RL.md`). Controls are built in.

## Case 2: Scientist agent (probe, then write)

### Task
Same visible files as case 1 (`goal.md`, `session.*`), plus a **practice server**: a separate engine instance with the same config, its **own seed, own data and own credentials**, never the grading instance. `goal.md` (probe arms only) states the practice base URL and practice credentials and the probe budget. The agent may send up to N HTTP requests to it, then submits `client.py`. Grading is unchanged (fresh grading instance, hidden tests).

### Arms (same 50 Tier B tasks, Beam high)
| Arm | Tool | Budget | Prompt |
|---|---|---|---|
| P-none | none | 0 | case 1 prompt (existing results) |
| P-placebo | `http_request` described | 0 (tool not offered; text identical to P5/P20 except the budget number) | probe prompt |
| P5 | `http_request` | 5 | probe prompt |
| P20 | `http_request` | 20 | probe prompt |
Effect of probing = P5/P20 - P-placebo (same wording). P-placebo - P-none = effect of the wording alone.

### Agent loop (Beam)
OpenAI-style tool calling. Tools: `http_request(method, path, query, headers, body)` -> `{status, headers, body (cut to 4,000 chars)}` against the practice server; `submit_client(code)` ends the episode. Every assistant turn's `reasoning_content` is sent back (Reflection API requirement). Max turns = budget + 3. Log per turn: usage including cached prompt tokens (`prompt_tokens_details.cached_tokens` if present), the probe, and the response.

### Metrics
Pass rate; probes used; a **probe label** per probe (automatic): `header_ablation` (same request minus a header the recording had), `auth_test`, `paging_test` (cursor/page/Link follow), `etag_test`, `other`; whether the probes that preceded a correct handling of a feature tested that feature. Report pass rate vs probes used (no fixed penalty in evaluation; the RL reward uses -0.01 per probe, stated).

## Case 3: Self-healing client (the API changes, the agent repairs)

### Task
- Start: a **correct** v1 client (the Tier B reference client for config c1) — so repair skill is measured alone, not first-draft quality.
- Change: flip one switch to get config c2 (same domain, host, path words, credentials format where possible). Mutation set: `add_hidden_header`, `apikey_to_bearer_login`, `offset_to_cursor`, `none_to_link_header`, `add_envelope`, `add_etag`, `add_token_expiry`, `rename_hidden_header`.
- Visible: `goal.md` (repair instructions + same method list), `client_v1.py`, `failure.txt` (output of running `client_v1.py` against a **practice** v2 instance: the exception and the last request/response), `session.har` (a short new recording on v2: 4-10 API calls that exercise the changed behaviour, noise 0.3).
- Hidden: v2 engine spec, tests, credentials. Graded on a fresh v2 grading instance with the Tier B grader.

### Metrics
Pass rate on v2; **patch size** reported separately (changed lines and changed AST nodes between `client_v1.py` and the submission); no reward for small diffs in evaluation. Comparison: the same v2 config solved from scratch (case 1) — repair vs build.

## Validity gates (both cases)
- Reference solution scores 1.0 (case 2: reference client; case 3: reference client of c2).
- Case 2: the practice server never shares data, ids or credentials with the grading instance (test).
- Case 3: `client_v1.py` scores 1.0 on v1 and < 1.0 on v2 for every mutation (the change must break it); a no-op patch fails.
