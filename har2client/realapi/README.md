# Real-API demo

Does a client written from a recording work on a **real** API, not only on our generated servers?

Three public practice APIs, chosen to cover the same features as Tier B:

| API | Auth in the recording | What the client must do |
|---|---|---|
| [Restful-Booker](https://restful-booker.herokuapp.com/apidoc/index.html) | login token, then sent as a `token` cookie on writes | list (with name filters), get, create, full update, delete |
| [DummyJSON](https://dummyjson.com/docs) | JWT login, then `Authorization: Bearer` | current user, every product across pages (`limit`/`skip`), category, search, get, add |
| [Swagger Petstore v3](https://petstore3.swagger.io/) | `api_key` header on delete | create, get, find by status, update, delete |

**How it works**

1. `record.py` opens each API's own web page in Chromium (Playwright) and runs the API calls from that page, so the HAR
   has the page's real noise (HTML, scripts, styles, fonts). Bodies of the noise entries are cut to 300 characters,
   as in the generated tasks; API entries stay whole.
2. Each task has a `goal.md` in the Case 1 format. The model gets the unchanged Case 1 prompt (`har2client.solve`).
3. `live_tests.py` runs the client against the **live** API with fresh data (random names and ids), so no recorded
   value helps. Validity gates: the reference clients in `reference/` pass every test, and an empty client passes none.

**Run it**

```bash
uv run --with playwright python realapi/record.py            # re-record (optional; the recordings are committed)
REFLECTION_API_KEY=... uv run python realapi/run.py --model beam --effort high --runs 3
HF_TOKEN=... uv run python realapi/run.py --model gpt-oss-120b-high --runs 3
```

**Credentials.** The recordings and tests use only the public demo credentials each service documents
(`admin`/`password123`, `emilys`/`emilyspass`, `special-key`). The session tokens in the recordings expired long ago.

**Scope.** This is a small external check (3 APIs, 17 tests), not a benchmark split: public practice APIs are shared,
can change, and may be in training data. Results are in `results.csv` and `../results/realapi/`.
