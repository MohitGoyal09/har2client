# har2client: design contract (v0, day 1)

This file is the contract between modules. If code and this file disagree, fix one of them on purpose.

## Goal
A task gives a model: (1) a HAR file recorded from a user session against an API, and (2) a goal that lists client methods. The model writes `client.py`. We grade it by running hidden tests against a **fresh** mock of the same API with **new data and new credentials**.

## Decisions
| # | Decision | Why |
|---|---|---|
| D1 | Own spec-driven mock in Python (Starlette + uvicorn in a thread), not Prism | Need per-instance secrets, a request/response log for grading, seeded data, and one design for Tier A and Tier B. Prism is stateless and has no token rotation. |
| D2 | OpenAPI 3.0.x only in v0 | 1,486 APIs on APIs.guru; Swagger 2.0 conversion later if needed. |
| D3 | Output is one Python file `client.py` with `class Client` | One language keeps grading simple; may use only `httpx`, `requests`, stdlib. |
| D4 | Request body is one `body: dict` argument | Unambiguous mapping; difficulty comes from auth, paths, query, base path, unwrapping. |
| D5 | Method returns the parsed JSON response body (`None` if no body) | Grader compares to the logged response, which changes per mock instance. |
| D6 | Generated client runs in a subprocess with a timeout (Docker later) | Untrusted code. |

## Module map (`src/har2client/`)
| Module | Owns |
|---|---|
| `ops.py` | `load_spec(path) -> dict` (YAML/JSON, `$ref` resolved with jsonref, recursion capped); `normalize(spec, api_id) -> OpTable` |
| `datagen.py` | `generate(schema: dict, rng: random.Random, depth=0) -> Any` (valid value for a JSON schema subset) |
| `mock.py` | `MockServer` |
| `browser.py` | shared browser header profile (request and response) |
| `har.py` | HAR 1.2 recorder (httpx event hooks) + `mix_noise(har, ratio, rng)` + `rewrite_host(har, base_url)` |
| `player.py` | `play_session(op_table, ops, base_url, secret, rng) -> list[calls]` |
| `testgen.py` | hidden test cases |
| `grade.py` + `runner.py` | run `client.py` in a subprocess against a fresh mock; score |
| `refclient.py` | code-generates a gold `client.py` from an OpTable (proves each task is solvable) |
| `build_task.py` | spec -> task dir |
| `solve/beam.py` | Beam baseline: task dir -> `client.py` |
| `cli.py` | `har2client build|grade|solve|ref` |

## OpTable (JSON-serializable dict)
```json
{
  "api_id": "provider:service",
  "title": "Example API",
  "server_url": "https://api.example.com/v2",
  "base_path": "/v2",
  "security": {"type": "apiKey|bearer|basic|none", "in": "header|query|null", "name": "X-API-Key|null"},
  "operations": [
    {
      "op_id": "list_orders",
      "method": "GET",
      "path": "/orders/{order_id}",
      "summary": "List orders",
      "params": [{"name": "order_id", "py_name": "order_id", "in": "path|query", "required": true, "schema": {}}],
      "body": {"required": true, "schema": {}},
      "response": {"status": 200, "schema": {}},
      "auth_required": true
    }
  ]
}
```
Rules:
- `op_id` = snake_case of `operationId`, else snake_case of `summary` (1-6 words, valid, unused, not a keyword), else `{method}_{path words}`; unique (suffix `_2` on clash); valid Python identifier, not a keyword.
- `py_name` = snake_case param name, valid identifier, unique within the op (`body` and `self` are reserved).
- `body` is `null` unless the request body is `application/json`. Drop ops whose only body type is not JSON.
- `response` = first 2xx response; `schema` is `null` when there is no JSON content.
- Drop header and cookie params (except auth). Drop ops with array or object **path** params.
- `security`: pick the first supported scheme among global/op security. `oauth2` and `openIdConnect` map to `bearer`. If none is supported, the API is skipped (except `none`).
- An API is usable when it has 5 to 40 ops after normalization and at least 3 ops with a JSON response schema.

## MockServer (`mock.py`)
```python
m = MockServer(op_table, seed=123, secret="sk_live_abc...")   # secret: str; for basic auth it is "user:pass"
base_url = m.start()          # "http://127.0.0.1:{port}{base_path}"; serves under base_path
m.log                         # list[LoggedRequest] in arrival order
m.stop()
```
`LoggedRequest` dict: `{"method", "path" (no base_path, no query), "query": {name: [values]}, "headers": {lower-case}, "body": parsed JSON or raw str or None, "op_id": matched op or None, "path_params": {name: str}, "status": int, "response": parsed JSON or None}`.

Behaviour, in order:
1. No route matches -> 404 (`op_id: None`).
2. `auth_required` and credential missing or wrong -> 401. apiKey: header/query `name` equals secret. bearer: `Authorization: Bearer {secret}`. basic: `Authorization: Basic base64(secret)`.
3. Missing required path/query param, or required body missing / not JSON -> 400.
4. Else response status from `response.status`, body = `datagen.generate(response.schema, rng)` with `rng = Random(f"{seed}:{op_id}:{n}")`, `n` = count of earlier 2xx calls to that op on this instance. Same seed -> same data; a new seed -> new data.

## HAR
- HAR 1.2. One entry per request: method, full URL, headers, queryString, postData (JSON text), response status, headers, content (text, mimeType).
- Recorded against `http://127.0.0.1:port/...` and then rewritten to the spec's `server_url` host, so the HAR looks real.
- Noise entries (no requests sent): third-party analytics, fonts, CDN JS/CSS, error trackers, plus same-origin static assets and HTML. `ratio` = share of noise entries in the final HAR. Time-ordered interleaving.
- One shared Chrome-on-macOS header profile (`browser.py`) builds request headers for real API calls (`fetch`: `accept: application/json`, `sec-fetch-dest: empty`) and for noise (values follow resource type). Real and fetch-type noise entries have the same header names and order, except `authorization`, `content-type` and the API-key header. `referer`/`origin` are the app origin `https://app.<registered domain>`.
- Response headers share one base set for real and noise entries: `date`, `content-type`, `content-length`, `server`, `cache-control`, `access-control-allow-origin`.
- About 15% of noise is same-origin JSON XHR on the API host, outside the base path and not a real route (`/__flags`, `/config.json`, `/sockjs-node/info`, `/ping`).

## Task directory
```
tasks/{task_id}/
  goal.md          # visible: API title, base_url format, constructor, method signatures + one-line summaries
  session.har      # visible
  task.json        # hidden: task_id, api_id, ops in goal, seeds, recording secret, noise ratio, features
  op_table.json    # hidden: gold
  tests.json       # hidden: [{"method": "list_orders", "kwargs": {...}}]
```
Goal ops = ops played in the session (1 to 3 calls each, max 8 ops). Partial coverage is natural: the spec has more ops than the goal.

Visible constructor (all tiers in v0): `Client(base_url: str, api_key: str)`. For basic auth, `api_key` is `"user:pass"`. For `none` it is ignored. The goal never says where the key goes; the model must read it from the HAR.

## Hidden tests and grading
- `tests.json`: for each goal op, 2 calls with fresh args (seed differs from the recording). Required args always; optional args with p=0.5, except that each optional query/path param is forced into one randomly chosen call of its op, so every optional param (and so every boolean and integer param) is exercised at least once per op.
- Grade = start `MockServer(op_table, seed=test_seed, secret=test_secret)`; subprocess runs `runner.py` which imports `client.py`, builds `Client(base_url, test_secret)`, runs each call, writes `{index, ok, return or error}` JSON. Timeout 60 s total.
- A test **passes** when: exactly the new log entries made during that call include one entry with the right `op_id`, status 2xx, path params equal to the args (string compare), query equal to the supplied query args (string compare; lists compared as lists), body equal to `body`, and the return value equals that entry's `response`.
- Leniency 1: an extra query param the test did not pass is ignored when its value equals the test secret (or, for basic auth, either part of it); sending the credential you were given is not wrong. A param the test did pass must still match exactly.
- Leniency 2: when the test kwargs have no `body`, a request with no body or an empty JSON object `{}` is accepted; any non-empty body is a mismatch. When the test passes `body`, equality is exact.
- Score output (`grade.json`): `pass_rate`, per-test results with a failure reason (`exception`, `no_request`, `wrong_op`, `auth_401`, `bad_request_400`, `not_found_404`, `args_mismatch`, `return_mismatch`, `timeout`), `invented_endpoint_calls` (requests with `op_id` None or an op not in the goal), `hardcoded_secret` (recording secret string appears in `client.py`), `hardcoded_host` (spec host appears in `client.py`).
- `strict_pass_rate` (secondary; `pass_rate` stays the pre-registered primary metric and is unchanged): a test counts only if it passes normally AND no invented-endpoint request was logged in its time window (Tier A: unmatched route or op not in the goal ops; Tier B: unmatched route, login and the five ops are legitimate). Requests made before the first test starts (client constructor) count against the first test. Per-test `invented_calls` and `strict_passed` are in `results`; `scripts/run_batch.py` writes the column and a `strict` mean in its summary.

## Gate for day 1
`refclient` passes 100% on every built task; a client that copies HAR responses scores 0 on `return_mismatch` tests.
