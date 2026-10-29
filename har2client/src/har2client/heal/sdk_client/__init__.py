"""SDK-style client renderer for hard repair tasks (`client_style = "sdk"`, DESIGN-CASE3-HARD.md).

`generate_sdk_client_source(spec, flaws)` renders the same client as `tierb.refclient.generate_client_source(spec, flaws)`
(same public class, constructor, methods, return values and requests on the wire) but written like a hand-maintained
SDK of 400-700 lines. The generated file has these layers, from the bottom up:

1. exceptions     `ApiError`, `AuthError`, `NotFoundError`
2. models         one dataclass for the resource, `from_dict` / `to_dict` keep unknown keys; wire adapters live here
3. retry helper   connection errors only, never HTTP status codes
4. auth           one strategy class for the spec's auth mode (headers, login, re-login)
5. transport      httpx client, base URL, default headers, auth, envelope decoding, error mapping
6. paginators     one iterator class for the spec's pagination mode
7. resource       the five operations; builds models from payloads
8. client         the public `Client`, a thin facade over the resource

The generated file has no `from __future__ import annotations`: `runner.py` loads a client without registering the module in
`sys.modules`, and `dataclasses` fails on string annotations then.

Only the classes and code the spec needs are emitted. Where each switch lives:

- wire drift (`drift`)  models: `_from_wire` / `_to_wire`; the list filter goes through `Model.query_to_wire`
- `rate_limit`          transport: `dispatch` waits `Retry-After` and sends the request again (login and refresh calls too)
- `async_create`        transport: `await_job` polls the job that a 202 answer points to; the resource calls it
- `moved`               transport: `item_path` (the item paths live below `ITEM_PREFIX`); the resource calls it
- `idempotency_key`     transport: `send` adds a fresh key header to every write
- `refresh_rotation`    auth: `BearerAuth.refresh` trades the refresh token for a new pair

A flaw leaves out the code of its switch. A flaw, config switch or level that this renderer does not know raises
`NotImplementedError` with its name.
"""

from har2client.heal.sdk_client.plan import KNOWN_FLAWS, SUPPORTED_LEVELS
from har2client.heal.sdk_client.render import generate_sdk_client_source

__all__ = ["KNOWN_FLAWS", "SUPPORTED_LEVELS", "generate_sdk_client_source"]
