"""Retry-helper and transport layers of the SDK-style client."""

from __future__ import annotations

from har2client.heal.sdk_client.plan import Plan, fill

# ---------------------------------------------------------------------------------------------------------- retry

RETRY = '''

# ----------------------------------------------------------------------------------------------------- retry helper


@dataclass(frozen=True)
class RetryPolicy:
    """How often and how long to wait when a connection cannot be made."""

    attempts: int = RETRY_ATTEMPTS
    backoff: float = RETRY_BACKOFF

    def __post_init__(self) -> None:
        if self.attempts < 1:
            raise ValueError("attempts must be at least 1")
        if self.backoff < 0:
            raise ValueError("backoff must not be negative")

    def delay(self, attempt: int) -> float:
        """Seconds to wait after the failed try number `attempt` (1-based): linear backoff."""
        return self.backoff * attempt


DEFAULT_RETRY = RetryPolicy()


def with_retries(call: Callable[[], T], policy: RetryPolicy = DEFAULT_RETRY) -> T:
    """Run `call`; try again when the connection could not be made.

    Only connection errors are retried: nothing reached the server, so a retry cannot repeat a write. A response with
    any HTTP status, including 5xx, is returned to the caller as it is.
    """
    for attempt in range(1, policy.attempts + 1):
        try:
            return call()
        except (httpx.ConnectError, httpx.ConnectTimeout) as error:
            if attempt == policy.attempts:
                raise
            logger.debug("connection failed (%s); try %d of %d", error.__class__.__name__, attempt, policy.attempts)
            time.sleep(policy.delay(attempt))
    raise AssertionError("unreachable")  # pragma: no cover
'''


# ------------------------------------------------------------------------------------------------------- transport

_TRANSPORT = '''

# --------------------------------------------------------------------------------------------------------- transport

@default_headers@

class Transport:
    """HTTP layer: base URL, default headers, auth, retries, response decoding and error mapping.

    Protocol-level behaviour of the API belongs here (headers on every call, rate limits, job polling, moved paths).
    """

    def __init__(
        self,
        base_url: str,
        auth: Auth,
        *,
        default_headers: dict | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        retry: RetryPolicy = DEFAULT_RETRY,
@init_param@
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.auth = auth
        self.default_headers = dict(default_headers or {})
        self.retry = retry
        self._http = httpx.Client(timeout=timeout, trust_env=False)
@init_body@

    def close(self) -> None:
        self._http.close()

    def build_headers(self, write: bool) -> dict:
        """Default headers first, then the ones of the auth strategy."""
        return {**self.default_headers, **self.auth.headers(write)}

    def url_for(self, url: str) -> str:
        """Absolute URLs (for example the next page link) are used as they are; paths go below the base URL."""
        return url if url.startswith("http") else self.base_url + url

@dispatch@
    def send(
        self, method: str, url: str, *, params: Any = None, body: Any = None, headers: dict | None = None
    ) -> httpx.Response:
        """Send an authenticated request and return the successful response.

        A 401 gives the auth strategy one chance to renew the session; then the request is sent again.
        """
        write = method != "GET"
@write_key@
        self.auth.ensure(self)
        for attempt in (0, 1):
            resp = self.dispatch(
                method, url, params=params or None, json=body, headers={**self.build_headers(write), **(headers or {})}
            )
            if resp.status_code == 401 and attempt == 0 and self.auth.refresh(self):
                continue
            break
        return self.check(resp)

    def check(self, resp: httpx.Response) -> httpx.Response:
        """Return `resp` when its status is 2xx, raise the typed exception otherwise."""
        if not resp.is_success:
            raise _error_from(resp)
        return resp
'''

_DISPATCH_PLAIN = '''    def dispatch(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        """One HTTP exchange, with connection retries. No auth handling and no status check."""
        target = self.url_for(url)
        logger.debug("%s %s", method, target)
        resp = with_retries(lambda: self._http.request(method, target, **kwargs), self.retry)
        logger.debug("%s %s -> %d", method, target, resp.status_code)
        return resp
'''

_DISPATCH_RATE_LIMITED = '''    def dispatch(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        """One HTTP exchange, with connection retries. No auth handling and no status check.

        The API throttles: a 429 with a `Retry-After` header is not an error. Wait that long, then send the same request
        again (login and refresh calls too), up to RATE_LIMIT_RETRIES tries.
        """
        target = self.url_for(url)
        for attempt in range(1, RATE_LIMIT_RETRIES + 1):
            logger.debug("%s %s", method, target)
            resp = with_retries(lambda: self._http.request(method, target, **kwargs), self.retry)
            logger.debug("%s %s -> %d", method, target, resp.status_code)
            if resp.status_code != 429 or "retry-after" not in resp.headers or attempt == RATE_LIMIT_RETRIES:
                break
@wait@
        return resp
'''

_WAIT = '''            delay = float(resp.headers["retry-after"])
            logger.info("rate limited; waiting %.1f s", delay)
            time.sleep(delay)'''

_HAMMER = '''            logger.info("rate limited; trying again at once")'''

_WRITE_KEY = '''        if write:  # every write carries a new key; a repeated key would replay the first answer
            headers = {**(headers or {}), IDEMPOTENCY_HEADER: self._next_key()}'''

_NEXT_KEY = '''
    def _next_key(self) -> str:
        """A key per write, from the account and a counter: unique per account, the same in every run."""
        self._writes += 1
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{self._identity}/{self._writes}"))
'''

_ITEM_PATH = '''
    def item_path(self, item_id: str) -> str:
        """Path of one item. The API moved the item paths below ITEM_PREFIX; the collection path stayed."""
        return ITEM_PREFIX + RESOURCE_PATH + "/" + quote(item_id, safe="")
'''

_AWAIT_JOB = '''
    def await_job(self, resp: httpx.Response) -> dict:
        """Create is asynchronous: the 202 answer points to a job. Poll it until it is done; its result is the item."""
        location = resp.headers["location"]
        for _ in range(JOB_POLLS):
            job = self.unwrap(self.send("GET", urljoin(self.base_url + "/", location)))
            if job["status"] == "done":
                return job["result"]
            time.sleep(JOB_POLL_DELAY)
        raise TimeoutError("the job did not finish")
'''

_DECODE_PLAIN = '''
    # ---- response decoding: the API sends bare payloads

    def unwrap(self, resp: httpx.Response) -> Any:
        """The payload of a response."""
        return resp.json()

    def page_items(self, body: Any) -> list:
        """The items of one page of a paged list."""
        return body[ITEMS_KEY]

    def page_meta(self, body: Any) -> Any:
        """The paging fields (total, next cursor) of one page of a paged list."""
        return body
'''

_DECODE_ENVELOPE = '''
    # ---- response decoding: the API wraps every payload in {"data": ..., "meta": ...}

    def unwrap(self, resp: httpx.Response) -> Any:
        """The payload of a response, taken out of the envelope."""
        return resp.json()["data"]

    def page_items(self, body: Any) -> list:
        """The items of one page of a paged list."""
        return body["data"]

    def page_meta(self, body: Any) -> Any:
        """The paging fields (total, next cursor) of one page of a paged list."""
        return body["meta"]
'''


def transport_source(spec: dict, flaws: set[str], plan: Plan) -> str:
    hidden = spec["hidden_header"]
    if hidden and "drop_hidden_header" not in flaws:
        headers = f"DEFAULT_HEADERS: dict[str, str] = {{{hidden['name']!r}: {hidden['value']!r}}}  # sent with every request\n"
    else:
        headers = "DEFAULT_HEADERS: dict[str, str] = {}  # the API needs no fixed header\n"
    enveloped = spec["config"]["envelope"] == "on"
    dispatch = fill(_DISPATCH_RATE_LIMITED, wait=_HAMMER if plan.hammer else _WAIT) if plan.rate_limit else _DISPATCH_PLAIN
    text = fill(
        _TRANSPORT, default_headers=headers.rstrip("\n"), dispatch=dispatch,
        init_param='        identity: str = "",' if plan.idempotency else "",
        init_body="        self._identity = identity\n        self._writes = 0" if plan.idempotency else "",
        write_key=_WRITE_KEY if plan.idempotency else "",
    )
    extras = (_NEXT_KEY if plan.idempotency else "") + (_ITEM_PATH if plan.moved else "") + (_AWAIT_JOB if plan.poll else "")
    return text + extras + (_DECODE_ENVELOPE if enveloped else _DECODE_PLAIN)
