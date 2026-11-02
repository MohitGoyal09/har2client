import requests
from typing import List, Optional, Dict, Any


class Client:
    """
    Simple client for the Restful‑Booker API.

    Parameters
    ----------
    base_url: str
        Root URL of the API (no trailing slash). Example:
        "https://restful-booker.herokuapp.com"
    username: str
        User name for the authentication endpoint.
    password: str
        Password for the authentication endpoint.
    """

    def __init__(self, base_url: str, username: str, password: str):
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self._session = requests.Session()
        self._token: Optional[str] = None
        # Obtain a token immediately – it will be reused for PUT/DELETE calls.
        self._login()

    # --------------------------------------------------------------------- #
    # private helpers
    # --------------------------------------------------------------------- #
    def _login(self) -> None:
        """Authenticate against `/auth` and store the bearer token."""
        url = f"{self.base_url}/auth"
        payload = {"username": self.username, "password": self.password}
        resp = self._session.post(url, json=payload)
        resp.raise_for_status()
        data = resp.json()
        token = data.get("token")
        if not token:
            raise RuntimeError("Authentication response did not contain a token")
        self._token = token

    def _auth_cookies(self) -> Dict[str, str]:
        """Return a cookie dict containing the authentication token."""
        if not self._token:
            self._login()
        return {"token": self._token}  # type: ignore[return-value]

    # --------------------------------------------------------------------- #
    # public API
    # --------------------------------------------------------------------- #
    def list_bookings(
        self, firstname: Optional[str] = None, lastname: Optional[str] = None
    ) -> List[int]:
        """
        Return a list of booking IDs.

        If ``firstname`` or ``lastname`` is supplied, the request is filtered
        accordingly. The API may return either a flat list of integers or a list
        of objects ``{"bookingid": <int>}``.  This method normalises the result to
        ``List[int]``.
        """
        url = f"{self.base_url}/booking"
        params: Dict[str, str] = {}
        if firstname is not None:
            params["firstname"] = firstname
        if lastname is not None:
            params["lastname"] = lastname

        resp = self._session.get(url, params=params)
        resp.raise_for_status()
        data = resp.json()

        ids: List[int] = []
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict) and "bookingid" in item:
                    ids.append(int(item["bookingid"]))
                else:
                    # assume item itself is the numeric id
                    ids.append(int(item))
        return ids

    def get_booking(self, id: int) -> Dict[str, Any]:
        """
        Retrieve the details of a single booking.

        The ``id`` is the booking identifier obtained from ``list_bookings``.
        The method returns the JSON body as a dictionary (without the id).
        """
        url = f"{self.base_url}/booking/{id}"
        resp = self._session.get(url)
        resp.raise_for_status()
        return resp.json()

    def create_booking(self, body: Dict[str, Any]) -> int:
        """
        Create a new booking.

        ``body`` must be a mapping containing the booking fields as required by
        the API. The method returns the newly created booking's id.
        """
        url = f"{self.base_url}/booking"
        resp = self._session.post(url, json=body)
        resp.raise_for_status()
        data = resp.json()
        if "bookingid" not in data:
            raise RuntimeError("Create booking response missing 'bookingid'")
        return int(data["bookingid"])

    def update_booking(self, id: int, body: Dict[str, Any]) -> Dict[str, Any]:
        """
        Replace an existing booking with ``body``.

        The request is authenticated using the token obtained from the ``/auth``
        endpoint.  The updated booking object (as returned by the API) is
        returned.
        """
        url = f"{self.base_url}/booking/{id}"
        resp = self._session.put(url, json=body, cookies=self._auth_cookies())
        resp.raise_for_status()
        return resp.json()

    def delete_booking(self, id: int) -> None:
        """
        Delete a booking.

        The request is authenticated using the token obtained from the ``/auth``
        endpoint.  No value is returned on success.
        """
        url = f"{self.base_url}/booking/{id}"
        resp = self._session.delete(url, cookies=self._auth_cookies())
        resp.raise_for_status()
        return None
