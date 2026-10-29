# Tier B design: stateful APIs with hard features

Why: Tier A scored 0.963 (low) and 0.996 (high) with Beam (`results/set30_v1_tierA.csv`); it does not separate systems. Tier B adds the parts real sites have and public specs miss.

## Decisions
| # | Decision | Why |
|---|---|---|
| B1 | One configurable stateful engine, not 50 hand-written apps | Each hard feature is a switch, so tasks form a factorial design and H1 can attribute failures to features. |
| B2 | Grading is semantic: return values and server state, not request shape | Many request shapes are correct (e.g. page size); what matters is the right data and the right state change. |
| B3 | Constructor `Client(base_url: str, username: str, password: str)` for every Tier B task | Login flows need user credentials; one constructor for the tier. `goal.md` states it. |
| B4 | Same task dir layout, `task.json` gets `"tier": "B"` and `"config": {...}` | Solver, runner, batch runner stay the same; `grade.grade` dispatches on `tier`. |
| B5 | New APIs, invented names, on invented hosts | No memorisation possible (this is the memorisation control for Tier B). |

## Engine (`src/har2client/tierb/`)
A Starlette app built from `(domain, config, seed, credentials)`.

### Domains (5)
Each domain has one main resource with 5-8 typed fields and one filter field. Names are invented-but-plausible, e.g. `shipments` (status filter), `tickets` (priority), `invoices` (state), `bookings` (room), `samples` (lab). Host per task: `api.<invented-brand>.<tld>`; base path `/v{1-3}` or `/api`.

### Endpoints (always)
`POST login` (only for login auth modes), `GET /<res>` (list, filter param), `GET /<res>/{id}`, `POST /<res>`, `PATCH /<res>/{id}`, `DELETE /<res>/{id}`. Path words vary per task (e.g. `/items`, `/records`, `/<res>`), chosen by seed.

### Feature switches (`config`)
| Switch | Values | Behaviour the client must reproduce |
|---|---|---|
| `auth` | `apikey_header` (baseline), `bearer_login`, `cookie_csrf` | `bearer_login`: POST credentials (JSON or form, by seed) -> token in body; send `Authorization: Bearer`. `cookie_csrf`: POST login sets a session cookie; a CSRF token (from a response header or a JSON field) must be sent as a header on writes. For `apikey_header`, `password` is the key and `username` is ignored; header name varies. |
| `pagination` | `none`, `offset`, `cursor`, `link_header` | List returns one page (size fixed by server, 10). The goal method `list_<res>` must return ALL matching items in server order. `cursor`: next cursor in body; `link_header`: RFC 8288 `Link: <...>; rel="next"`. |
| `token_expiry` | `off`, `on` (login modes only) | Token/session expires after 4 authenticated calls -> 401 with a JSON error. Client must log in again and retry once. |
| `etag_update` | `off`, `on` | PATCH requires `If-Match` with the ETag from a GET of that item; else 428. Goal method `update_<res>(id, changes)` must GET first. |
| `hidden_header` | `off`, `on` | Every API call needs a constant header (e.g. `X-Client-Version: 4.12.0`, name/value by seed); missing -> 400. |
| `envelope` | `off`, `on` | Responses wrap data: `{"data": ..., "meta": {...}}`. Goal says methods return the resource (unwrapped). |

### Goal methods (fixed per tier, names use the task's resource word)
- `list_<res>(<filter>: str | None = None) -> list[dict]`: all items, all pages.
- `get_<res>(id: str) -> dict`
- `create_<res>(body: dict) -> dict`: the created item.
- `update_<res>(id: str, changes: dict) -> dict`: the updated item.
- `delete_<res>(id: str) -> None`

`goal.md` describes these in plain words (including "return all items" and "return the item itself, not a wrapper") but never names the auth mode, paging style, header, or ETag rule. The HAR shows them.

### Recording session
Seed the store with 25-45 items. The player logs in (if needed), lists with and without the filter (following 2+ pages), gets 2 items, creates 1, updates 1 (with GET first when `etag_update`), deletes 1, and in `token_expiry` mode hits one 401 and re-logs in. Then noise is mixed with the existing `har.mix_noise` and browser profile.

### Hidden tests and semantic grading
Fresh engine instance: new seed (new items, new ids), new credentials, same config. Tests run in order in one client instance:
1. `list_<res>()` and `list_<res>(<filter>=v)` -> equal to the server's full matching list (order as server). Fail reason `incomplete_list` if a strict subset of the right items.
2. `get_<res>(id)` x2 -> equal to stored item.
3. `create_<res>(body)` -> returned item equals the stored new item; store has it. Reason `state_mismatch` if not stored.
4. `update_<res>(id, changes)` -> returned equals stored; stored has the changes.
5. `delete_<res>(id)` -> returns None; store no longer has it.
6. In `token_expiry` mode the test sequence is long enough to cross at least one expiry.
Shared reasons from Tier A apply (`exception`, `timeout`, `auth_401`, `bad_request_400`, `not_found_404`), plus `incomplete_list`, `state_mismatch`, `precondition_428`. `invented_endpoint_calls` and `hardcoded_secret`/`hardcoded_host` as in Tier A.

### Task set
50 tasks: a balanced fractional factorial over `auth` (3) x `pagination` (4) x `token_expiry` (2, only with login modes) x `etag_update` (2) x `hidden_header` (2) x `envelope` (2), spread over the 5 domains. Include all-off baselines (apikey, no paging, no switches) so the effect of each switch is measured against them. The config list is generated by a seeded script and committed (`data/tierb_configs.jsonl`).

## Validity gate
A reference client generated from the config scores 1.0 on all 50 tasks. A client that ignores pagination fails list tests with `incomplete_list`; a client without re-login fails in `token_expiry` mode; a client without `If-Match` fails update with `precondition_428`.
