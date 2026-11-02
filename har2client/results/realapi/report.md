# Real-API demo

Clients written from real browser recordings of three public practice APIs, graded by live tests with fresh data (`har2client/realapi/`). 3 runs per setting and API; the unchanged Case 1 prompt.

| Setting | Restful-Booker | DummyJSON | Petstore v3 | All tests | Perfect runs |
|---|---|---|---|---|---|
| Beam (high) | 18/18 | 18/18 | 15/15 | 51/51 (100.0%) | 9/9 |
| Beam (low) | 18/18 | 18/18 | 15/15 | 51/51 (100.0%) | 9/9 |
| GPT-OSS-120B (high) | 18/18 | 0/18 | 15/15 | 33/51 (64.7%) | 6/9 |
| Qwen3.5-397B-A17B (thinking on) | 18/18 | 18/18 | 15/15 | 51/51 (100.0%) | 9/9 |

Failed tests per setting and API:

- GPT-OSS-120B (high), DummyJSON: context_overflow

Scope: a small external check (3 APIs, 17 tests per run), not a benchmark split. Public practice APIs are shared and may appear in training data.
