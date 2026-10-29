# Case 3 v3: hard repair tasks

Extends [DESIGN-CASE3.md](DESIGN-CASE3.md).

## Why
Beam at `low` effort repaired 48/48 core tasks (pass rate 1.0) and the first 3 compound tasks (1.0); medium was 1.0 on its first 11. The v2 suite does not separate effort levels or models. Causes:
1. The API is one resource with 5 operations. The client is about 150 lines. Little to search, little to break.
2. Every change raises an HTTP error on the first call, and the error or the recording shows the fix.
3. The recording exercises every operation that the tests use. Nothing must be generalised.
4. The public interface of the client and the wire format change together. No adapter logic is needed.
The v2 result is kept and reported as a finding ("single, loud API changes are solved at low effort"). v3 adds difficulty along the four causes.

## Principles
- **Stable public interface.** `goal.md` keeps the v1 method signatures and return shapes. The client must hide wire changes from its caller (adapter logic). The grader compares return values with the canonical (v1-shaped) item and checks server state.
- **Identifiable.** Every fix must follow from the visible files, or from a stated general rule over them (label `generalize`). A task that needs a pure guess is labelled `no` and reported apart.
- **Validity gates** (all tasks): v1 client 1.0 on v1, < 1.0 on v2; v2 reference client 1.0; no-op patch fails; leak check; deterministic under a seed.
- **Calibrate on dev seeds, evaluate on fresh seeds.** A pilot on dev seeds sets which families stay. The evaluation suite is then built on new seeds and frozen in `PREREG.md` before any run of it. We never drop an evaluation task because of a Beam result.

## New switches (engine; default off, existing tasks unchanged byte for byte)
### Silent wire drift (`drift`, list of kinds; no HTTP error)
The engine keeps canonical items and translates at the wire, both ways (responses and request bodies). Each kind is a bijection.
| Kind | v1 wire | v2 wire |
|---|---|---|
| `rename` | `"status": ...` | `"state": ...` (one field, chosen by seed) |
| `cents` | `"amount": 12.5` | `"amount_cents": 1250` |
| `epoch` | `"created_at": "2026-01-02T03:04:05Z"` | `"created_at": 1767323045` |
| `nest` | `"customer_name": "A", "customer_email": "b"` | `"customer": {"name": "A", "email": "b"}` |
| `enum` | `"open"`, `"closed"` | `"OPEN"`, `"CLOSED"` (case or prefix map; every value used by tests appears in the recording: gate) |
A drifted client gets wrong values, not errors. `failure.txt` shows the wrong return value of one hidden-test-like call, not the cause.

### Protocol changes (control flow)
| Switch | v2 behaviour |
|---|---|
| `rate_limit` | after N requests in a window, `429` with `Retry-After: 1`; the client must wait and retry |
| `async_create` | `POST` answers `202` with `Location: /jobs/{id}`; `GET` the job until `status: done`, then read `result`; `create()` still returns the item |
| `refresh_rotation` | login returns `access_token` and `refresh_token`; access expires after K calls; `POST <refresh path>` gives a new pair and the old refresh token stops working; a second password login answers `429` |
| `idempotency_key` | every write (`POST`, `PATCH`, `DELETE`) needs a unique `Idempotency-Key`; missing gives `400`; a reused key with another body gives `422` |
| `moved` | item paths move to a new prefix; the old path answers `308` with `Location` |

## New task factors
| Factor | Levels | Effect |
|---|---|---|
| `evidence_scope` | `full` (v2 today), `partial` | `partial`: the recording exercises only some operations (e.g. `create` for `idempotency_key`; `list` for `rename`). Tests use all. Label `generalize`. |
| `changelog` | `none`, `complete`, `incomplete`, `misleading` | visible `CHANGELOG.md`. `incomplete` omits one change. `misleading` states one change wrongly (e.g. a wrong header name); the recording shows the truth. |
| `changes` | 1, 2, 3, 4 | a release mixes kinds: at most one per switch; 3-4 changes include at least one drift and one protocol change |
| `client_style` | `compact` (today), `sdk` | `sdk`: the same v1 behaviour as a 400-700 line SDK (transport, auth, retry, pagination iterator, resource class, dataclass models). The fix must go in the right layer, and more code can break. |
| `error_detail` | `verbose`, `terse` | as v2 |

## Metrics
As v2 (`pass_rate`, `strict_pass_rate`, `regression_rate`, patch size, tokens), plus per-change credit: for each change in a release, whether the tests that need it pass. This shows which change kinds fail.

## Pilot, then suite
1. **Pilot (dev seeds 1001+).** Each new drift kind and protocol switch alone; `partial` scope; 3-change and 4-change releases; `sdk` style; `misleading` changelog. 2 bases each, about 40 tasks. Beam `low` and `high`, 1 run.
2. **Keep rule (fixed now).** A family stays if Beam `low` pass rate < 0.9, or `high` - `low` >= 0.10. Otherwise it is reported as "solved" and leaves the evaluation suite.
3. **Evaluation suite (fresh seeds 2001+).** About 96 tasks over the kept families, balanced. Frozen and listed in `PREREG.md` (H16+) before any run. Effort sweep low/medium/high/xhigh, 2-3 runs.

## Build split
- Engine and reference client: `tierb/spec.py`, `config.py`, `engine.py`, `refclient.py`, `grader.py`.
- SDK-style client renderer: new `heal/sdk_client.py`.
- Mutations, sessions (partial scope), changelog, identifiable labels, suite builder: `heal/*`, `scripts/build_heal.py`.

## Build notes (generator and pilot as built)
- Build: `uv run python scripts/build_heal_v3.py --out runs/heal_v3_pilot --seed 1` (writes `data/heal_v3_pilot.jsonl`, 38 tasks, dev spec seeds 1001-1038; `--families` picks some). Code: `heal/release.py` (releases, pilot rows), `heal/hard.py` (builder, gates, `verify_hard_task`), `heal/hardtests.py` (hidden tests), `heal/changelog.py`, `heal/identify.py` (`label`), `heal/failure.py` (`show_values`), `heal/session.py` (`record_session(ops=...)`), `heal/mutations.py` (`HARD_MUTATIONS`; `MUTATIONS` and the v2 suite are unchanged).
- Mutations: `add_drift_{rename,cents,epoch,nest,enum}`, `add_rate_limit`, `add_async_create`, `add_refresh_rotation`, `add_idempotency_key`, `add_moved`. v1 = v2 spec with that switch reverted (the drift list loses that kind); the extra item fields of a drift (`amount`-like money, the nested pair) stay in the v1 data. `add_refresh_rotation` needs a `bearer_login` base without `token_expiry`; its v1 client has no re-login, as in v2.
- Releases: 1-4 changes, one per switch. 3-4 changes have at least one drift and one protocol change and at least one of the seven changes that stand alone in the pilot (cents, epoch, nest, rate_limit, async_create, refresh_rotation, idempotency_key), so a release is never only rename, enum and moved.
- Hidden tests (`hardtests.make_hard_tests`): 17 = 3 lists, 4 gets, 3 creates, 4 updates, 3 deletes. Reads first (the grader compares lists and gets with the start data); all targets are different items; the updates take turns in changing each drift kind; with paging two of the three lists need two pages (the test seed is picked for it). `tests.json` carries `needs` per test: the changes whose single-flaw reference client fails that test (empirical, from the necessity runs; the rate-limit and refresh flaws cascade, so they are needed by most tests). Per-change credit = pass share over the tests that need the change.
- Gates (`verify_hard_task`): v1 client 1.0 on v1 and < 1.0 on v2; v2 reference 1.0; necessity (DepBench four states: the reference client without one change scores < 1.0, for every change; recorded in `heal.necessity`); fix size (`fix_lines` = changed lines v1 client -> v2 reference of the same style, >= 30 for 3+ changes; `fix_classes` = classes whose text changed, >= 2 for `sdk`); >= 15 tests with every operation twice; leak check (the changelog is a visible file); the recording shows each change (key, header, status or path pattern; for an enum drift every wire value of the field; for `partial` too); terse tasks: no fix wording; changelog info is consistent. A task that fails is removed and reported. Deterministic per seed (tested).
- `evidence_scope`: `full` plays list, get, create, update, delete; `partial` plays one operation per change (drift, rate_limit, refresh_rotation: list, repeated until the 429 or the refresh shows; idempotency_key, async_create: create; moved: get), so the recording never has an update or a delete. The goal does not say that the recording is partial. Label `identifiable`: for v3 tasks a string `yes`, `no` or `generalize` (`identify.label`); the v2 tasks keep the bool. `failure_only` shows one failing check, so only a single `rate_limit` is identifiable there.
- `failure.txt` (v3): the first failing call of a practice smoke run. An exception shows as before (frames, last request, response). A wrong value shows as `list_x() returned N items; item i is {...}. The web app shows N items; item i is {...}.` (or `get_x('id') returned {...}; the web app shows {...}.`, `... shows no new <item>` for an unfinished async create), with no HTTP exchange, since the exchange shows the cause. A rate limit or a moved path fails loud on the first call that meets it, so those tasks show the exchange.
- Changelog: `CHANGELOG.md` (version, date, "Breaking changes", other items). `incomplete` leaves one change out (needs 2+ changes); `misleading` states one change wrongly: header (rate_limit, idempotency_key), field (rename, nest), unit (cents, epoch: hundredths vs thousandths, seconds vs milliseconds) or path (moved, async_create, refresh_rotation); `add_drift_enum` is never the target. `task.json` `heal.changelog_info` records `omitted` / `misleading {change, kind, stated, truth}`. The solver sends the file (`solve/beam.py`, `solve/openai_compat.py`); the system prompt gets one neutral sentence only when the file exists (`CHANGELOG_NOTE`); Case 1 and older repair prompts are byte-identical (tested).
- Goal: the v2 repair goal, a `CHANGELOG.md` entry when present, and the rule "Keep what each method returns as `client_v1.py` returns it".
- Pilot (38 tasks, built in `runs/heal_v3_pilot`): single 14 (7 kinds x 2), release3 4, release4 4, partial 4, sdk 4, misleading 4, terse 4; all with verbose errors, both evidence channels, full scope, no changelog and the compact client unless the family changes that. Balanced over the five domains (7-8 each) and the three auth modes (12-14 each).
- Known limits: single-change fixes are small (idempotency_key 5-18 lines, async_create 14); the size gate applies to 3+ changes. `needs` for rate_limit depends on the grading run staying inside one rate window (it does on a normal machine). `scripts/score_heal.py` does not read `needs` yet (per-change credit is not scored).
