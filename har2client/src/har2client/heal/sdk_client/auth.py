"""Auth layer of the SDK-style client: one strategy class for the spec's auth mode."""

from __future__ import annotations

from har2client.heal.sdk_client.plan import Plan, fill

# ------------------------------------------------------------------------------------------------------------ auth

_AUTH_BASE = '''

# ------------------------------------------------------------------------------------------------------------ auth


class Auth:
    """Auth strategy. The transport asks it for headers, calls `ensure` before a request and `refresh` after a 401."""

    def headers(self, write: bool) -> dict:
        """Headers this strategy adds to a request. `write` is true for every method except GET."""
        return {}

    def ensure(self, transport: "Transport") -> None:
        """Make the credentials ready before a request (for example log in once)."""

    def refresh(self, transport: "Transport") -> bool:
        """Called after a 401. Return true when the credentials were renewed and the request may be sent again."""
        return False
'''

_AUTH_APIKEY = '''

class ApiKeyAuth(Auth):
    """A static API key in a request header. The `password` argument of the client is the key."""

    HEADER = @api_key_header@

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    def headers(self, write: bool) -> dict:
        return {self.HEADER: self._api_key}
'''

_AUTH_LOGIN = '''

class LoginAuth(Auth):
    """Base of the strategies that log in with a username and a password.

    The login call carries only the default headers: it must not send the credentials that it is about to replace.
    """

    LOGIN_PATH = @login_path@
    USER_FIELD = @user_field@
    PASS_FIELD = @pass_field@

    def __init__(self, username: str, password: str) -> None:
        self._username = username
        self._password = password

    def _reset(self) -> None:
        raise NotImplementedError

    def _store(self, resp: httpx.Response, body: Any) -> None:
        raise NotImplementedError

    def login(self, transport: "Transport") -> None:
        """Log in and keep the new session."""
        self._reset()
        payload = {self.USER_FIELD: self._username, self.PASS_FIELD: self._password}
        resp = transport.dispatch(
            "POST", self.LOGIN_PATH, @login_send@, headers=transport.build_headers(False)
        )
        transport.check(resp)
        self._store(resp, transport.unwrap(resp))
@refresh@'''

_REFRESH_OK = '''
    def refresh(self, transport: "Transport") -> bool:
        """The session expired: log in again, then the transport repeats the request once."""
        self.login(transport)
        return True
'''

_REFRESH_NEVER = '''
    def refresh(self, transport: "Transport") -> bool:
        return False
'''

_AUTH_BEARER = '''

class BearerAuth(LoginAuth):
    """Login returns a token; every request carries it as `Authorization: Bearer <token>`."""

    TOKEN_FIELD = @token_field@

    def __init__(self, username: str, password: str) -> None:
        super().__init__(username, password)
        self._token: str | None = None
@refresh_init@

    def _reset(self) -> None:
        self._token = None
@refresh_reset@

    def _store(self, resp: httpx.Response, body: Any) -> None:
        self._token = body[self.TOKEN_FIELD]
@refresh_store@

    def headers(self, write: bool) -> dict:
        if self._token:
            return {"Authorization": "Bearer " + self._token}
        return {}

    def ensure(self, transport: "Transport") -> None:
        if self._token is None:
            self.login(transport)
@rotation@'''

_BEARER_ROTATION = '''
    REFRESH_PATH = @refresh_path@
    REFRESH_FIELD = @refresh_field@

    def refresh(self, transport: "Transport") -> bool:
        """The access token expired: trade the refresh token for a new pair; the old pair stops working.

        No password login here: the API refuses a second one for the same session.
        """
        if self._refresh_token is None:
            self.login(transport)
            return True
        resp = transport.dispatch(
            "POST", self.REFRESH_PATH, json={self.REFRESH_FIELD: self._refresh_token}, headers=transport.build_headers(False)
        )
        transport.check(resp)
        body = transport.unwrap(resp)
        self._token = body[self.TOKEN_FIELD]
        self._refresh_token = body[self.REFRESH_FIELD]
        return True
'''

_AUTH_COOKIE = '''

class CsrfAuth(LoginAuth):
    """Cookie session with a CSRF token.

    The session cookie lives in the httpx cookie jar of the transport. The CSRF token comes from the login response
    and is sent in the `@csrf_raw@` header of writes.
    """

    CSRF_HEADER = @csrf_header@

    def __init__(self, username: str, password: str) -> None:
        super().__init__(username, password)
        self._csrf: str | None = None

    def _reset(self) -> None:
        self._csrf = None

    def _store(self, resp: httpx.Response, body: Any) -> None:
        @csrf_store@

    def headers(self, write: bool) -> dict:
        @csrf_headers@

    def ensure(self, transport: "Transport") -> None:
        if self._csrf is None:
            self.login(transport)
'''


def _bearer(spec: dict, plan: Plan) -> str:
    refresh = spec.get("refresh", {})
    if not plan.refresh:
        return fill(_AUTH_BEARER, token_field=repr(spec["login"]["token_field"]), refresh_init="", refresh_reset="", refresh_store="", rotation="")
    return fill(
        _AUTH_BEARER, token_field=repr(spec["login"]["token_field"]),
        refresh_init="        self._refresh_token: str | None = None",
        refresh_reset="        self._refresh_token = None",
        refresh_store=f"        self._refresh_token = body[{refresh['field']!r}]",
        rotation=fill(_BEARER_ROTATION, refresh_path=repr(refresh["path"]), refresh_field=repr(refresh["field"])).rstrip("\n") + "\n",
    )


def auth_source(spec: dict, flaws: set[str], plan: Plan) -> str:
    mode, login, csrf = spec["config"]["auth"], spec["login"], spec["csrf"]
    parts = [_AUTH_BASE]
    if mode == "apikey_header":
        return "".join(parts) + fill(_AUTH_APIKEY, api_key_header=repr(spec["api_key_header"]))
    refresh = _REFRESH_NEVER if "no_relogin" in flaws else _REFRESH_OK
    parts.append(
        fill(
            _AUTH_LOGIN,
            login_path=repr(login["path"]), user_field=repr(login["user_field"]), pass_field=repr(login["pass_field"]),
            login_send="json=payload" if login["format"] == "json" else "data=payload", refresh=refresh,
        )
    )
    if mode == "bearer_login":
        parts.append(_bearer(spec, plan))
    else:
        store = f"self._csrf = resp.headers[{csrf['header']!r}]" if csrf["in"] == "header" else f"self._csrf = body[{csrf['field']!r}]"
        headers = (
            "return {}  # the token is stored but not sent"
            if "no_csrf" in flaws
            else "if write and self._csrf:\n            return {self.CSRF_HEADER: self._csrf}\n        return {}"
        )
        parts.append(fill(_AUTH_COOKIE, csrf_raw=csrf["header"], csrf_header=repr(csrf["header"]), csrf_store=store, csrf_headers=headers))
    return "".join(parts)
