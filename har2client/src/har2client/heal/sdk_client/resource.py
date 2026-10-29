"""Paginator, resource and client layers of the SDK-style client."""

from __future__ import annotations

from har2client.heal.sdk_client.models import model_name
from har2client.heal.sdk_client.plan import Plan, fill
from har2client.tierb.spec import method_name

# ----------------------------------------------------------------------------------------------------- paginators

_PAGER_BASE = '''

# ------------------------------------------------------------------------------------------------------ paginators

MAX_PAGES: int | None = @max_pages@  # stop after this many pages (None: read them all)


@dataclass(frozen=True)
class Page:
    """One page of a list: its items and its number (the first page is 1)."""

    items: list
    number: int


class Paginator:
    """Iterator over the items of a list endpoint, one page request at a time.

    Subclasses implement `pages`; iterating the paginator yields the items of every page in order.
    """

    def __init__(self, transport: Transport, path: str, params: dict | None = None) -> None:
        self.transport = transport
        self.path = path
        self.params = dict(params or {})

    def _done(self, pages_read: int) -> bool:
        return MAX_PAGES is not None and pages_read >= MAX_PAGES

    def pages(self) -> Iterator[Page]:
        raise NotImplementedError

    def __iter__(self) -> Iterator[Any]:
        for page in self.pages():
            yield from page.items
'''

_PAGER_OFFSET = '''

class OffsetPaginator(Paginator):
    """Pages are addressed by item offset in the `@param@` query parameter; the first page has no offset."""

    PARAM = @param@

    def pages(self) -> Iterator[Page]:
        offset = 0
        number = 0
        while True:
            query = dict(self.params)
            if offset:
                query[self.PARAM] = offset
            body = self.transport.send("GET", self.path, params=query).json()
            items = self.transport.page_items(body)
            number += 1
            yield Page(items, number)
            if self._done(number):
                return
            offset += len(items)
            if not items or offset >= self.transport.page_meta(body)["total"]:
                return
'''

_PAGER_CURSOR = '''

class CursorPaginator(Paginator):
    """Pages are chained by a cursor: the `@param@` query parameter takes the `@next_key@` value of the last page."""

    PARAM = @param@
    NEXT_KEY = @next_key@

    def pages(self) -> Iterator[Page]:
        cursor: Any = None
        number = 0
        while True:
            query = dict(self.params)
            if cursor:
                query[self.PARAM] = cursor
            body = self.transport.send("GET", self.path, params=query).json()
            number += 1
            yield Page(self.transport.page_items(body), number)
            if self._done(number):
                return
            cursor = self.transport.page_meta(body)[self.NEXT_KEY]
            if not cursor:
                return
'''

_PAGER_LINK = '''
NEXT_LINK = re.compile(r'<([^>]+)>\\s*;\\s*rel="?next"?')


class LinkHeaderPaginator(Paginator):
    """Pages are chained by the `Link` response header; the item list is the whole payload of a page."""

    def pages(self) -> Iterator[Page]:
        url: str | None = self.path
        params: dict | None = self.params
        number = 0
        while url:
            resp = self.transport.send("GET", url, params=params)
            number += 1
            yield Page(self.transport.unwrap(resp), number)
            params = None  # the next link already holds the query
            if self._done(number):
                return
            match = NEXT_LINK.search(resp.headers.get("link", ""))
            url = match.group(1) if match else None
'''


def paginators_source(spec: dict, flaws: set[str]) -> str:
    style, page = spec["config"]["pagination"], spec["page"]
    if style == "none":
        return ""
    base = fill(_PAGER_BASE, max_pages="1" if "ignore_pagination" in flaws else "None")
    if style == "offset":
        return base + fill(_PAGER_OFFSET, param=repr(page["param"]))
    if style == "cursor":
        return base + fill(_PAGER_CURSOR, param=repr(page["param"]), next_key=repr(page["next_key"]))
    return base + _PAGER_LINK


# ------------------------------------------------------------------------------------------------------- resource

_RESOURCE = '''

# ------------------------------------------------------------------------------------------------------- resource


class @plural_class@:
    """The @domain@ collection: list, get, create, update and delete."""

    def __init__(self, transport: Transport) -> None:
        self._transport = transport

    def _item_path(self, item_id: str) -> str:
        return @item_path@

    def _item(self, resp: httpx.Response) -> dict:
        """The canonical item of a response."""
@item_return@

    def list(self, @filter@: str | None = None) -> list[dict]:
        """Every @singular@ across all pages, in server order; only those whose `@filter@` matches when it is given."""
        params: dict = @model@.query_to_wire({FILTER_FIELD: @filter@}) if @filter@ is not None else {}
@list_body@

    def get(self, id: str) -> dict:
        """The @singular@ with this id."""
        return self._item(self._transport.send("GET", self._item_path(id)))

    def create(self, body: dict) -> dict:
        """Create a @singular@ from `body` and return it."""
        payload = @model@.from_dict(body).to_wire()
        resp = self._transport.send("POST", RESOURCE_PATH, body=payload)
@create_return@

    def update(self, id: str, changes: dict) -> dict:
        """Change the given fields of a @singular@ and return the updated item."""
        payload = @model@.from_dict(changes).to_wire()
@update_headers@
        return self._item(self._transport.send("PATCH", self._item_path(id), body=payload, headers=headers))

    def delete(self, id: str) -> None:
        """Delete the @singular@. Returns nothing."""
        self._transport.send("DELETE", self._item_path(id))
        return None
'''


def resource_source(spec: dict, flaws: set[str], plan: Plan) -> str:
    style = spec["config"]["pagination"]
    model = model_name(spec)
    field_ = spec["filter"]
    if "return_envelope" in flaws:
        item_return = "        return resp.json()"
    else:
        item_return = f"        return {model}.parse(self._transport.unwrap(resp))"
    if style == "none":
        list_body = (
            "        resp = self._transport.send(\"GET\", RESOURCE_PATH, params=params)\n"
            f"        return {model}.parse_all(self._transport.unwrap(resp))"
        )
    else:
        pager = {"offset": "OffsetPaginator", "cursor": "CursorPaginator", "link_header": "LinkHeaderPaginator"}[style]
        list_body = f"        pages = {pager}(self._transport, RESOURCE_PATH, params)\n        return [{model}.parse(item) for item in pages]"
    if spec["config"]["etag_update"] == "on" and "no_if_match" not in flaws:
        update_headers = (
            "        # the API wants the current version of the item: read its ETag first\n"
            "        current = self._transport.send(\"GET\", self._item_path(id))\n"
            "        headers = {\"If-Match\": current.headers[\"etag\"]}"
        )
    else:
        update_headers = "        headers = None"
    if plan.poll:
        create_return = f"        return {model}.parse(self._transport.await_job(resp))"
    elif plan.async_create:
        create_return = "        return self._transport.unwrap(resp)  # the 202 answer describes the job, not the item"
    else:
        create_return = "        return self._item(resp)"
    return fill(
        _RESOURCE, plural_class=spec["domain"].capitalize(), domain=spec["domain"], singular=spec["singular"], filter=field_,
        model=model, item_return=item_return, list_body=list_body, update_headers=update_headers, create_return=create_return,
        item_path="self._transport.item_path(item_id)" if plan.moved else 'RESOURCE_PATH + "/" + quote(item_id, safe="")',
    )


# --------------------------------------------------------------------------------------------------------- client

_CLIENT = '''

# ---------------------------------------------------------------------------------------------------------- client


def _make_auth(username: str, password: str) -> Auth:
    """The auth strategy of this API."""
    return @auth_ctor@


class Client:
    """Public client. A thin facade: every method delegates to the resource, the transport does the HTTP.

    `base_url` is the API root including any base path. For API-key APIs `password` is the key.
    The client keeps its session (login, cookies) between calls; use it as a context manager or call `close`.
    """

    def __init__(self, base_url: str, username: str, password: str):
        self._transport = Transport(
            base_url, _make_auth(username, password), default_headers=DEFAULT_HEADERS, timeout=DEFAULT_TIMEOUT@identity@
        )
        self._resource = @plural_class@(self._transport)

    def __repr__(self) -> str:
        return f"Client(base_url={self._transport.base_url!r})"  # never show credentials

    def __enter__(self) -> "Client":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()

    def close(self) -> None:
        """Close the HTTP connections."""
        self._transport.close()

    def @list_name@(self, @filter@: str | None = None) -> list[dict]:
        return self._resource.list(@filter@)

    def @get_name@(self, id: str) -> dict:
        return self._resource.get(id)

    def @create_name@(self, body: dict) -> dict:
        return self._resource.create(body)

    def @update_name@(self, id: str, changes: dict) -> dict:
        return self._resource.update(id, changes)

    def @delete_name@(self, id: str) -> None:
        return self._resource.delete(id)
'''


def client_source(spec: dict, plan: Plan) -> str:
    auth = spec["config"]["auth"]
    ctor = {"apikey_header": "ApiKeyAuth(password)", "bearer_login": "BearerAuth(username, password)", "cookie_csrf": "CsrfAuth(username, password)"}[auth]
    names = {op: method_name(spec, op) for op in ("list", "get", "create", "update", "delete")}
    return fill(
        _CLIENT, auth_ctor=ctor, identity=", identity=username" if plan.idempotency else "", plural_class=spec["domain"].capitalize(), filter=spec["filter"],
        **{f"{op}_name": name for op, name in names.items()},
    )
