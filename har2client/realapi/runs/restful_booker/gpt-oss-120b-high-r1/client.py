import httpx
from urllib.parse import urlparse
from typing import List, Dict, Optional


class Client:
    """
    Simple client for the Restful‑Booker API.

    Parameters
    ----------
    base_url: str
        Base URL of the API (no trailing slash), e.g. ``https://restful-booker.herokuapp.com``.
    username: str
        Account username.
    password: str
        Account password.
    """

    def __init__(self, base_url: str, username: str, password: str):
        self.base_url = base_url.rstrip("/")
        self._username = username
        self._password = password
        # a single httpx client preserves cookies and connection pooling
        self._client = httpx.Client()
        self._token: Optional[str] = None
        self._authenticate()

    # --------------------------------------------------------------------- #
    # Authentication
    # --------------------------------------------------------------------- #
    def _authenticate(self) -> None:
        """Obtain a token via ``POST /auth`` and store it as a cookie."""
        auth_url = f"{self.base_url}/auth"
        resp = self._client.post(
            auth_url,
            json={"username": self._username, "password": self._password},
            timeout=10.0,
        )
        resp.raise_for_status()
        data = resp.json()
        token = data.get("token")
        if not token:
            raise RuntimeError("Authentication failed – no token in response")
        self._token = token

        # Store the token as a cookie for the API domain.
        domain = urlparse(self.base_url).hostname
        if domain:
            self._client.cookies.set("token", token, domain=domain, path="/")
        else:
            # fallback – set without domain (httpx will include it for the target host)
            self._client.cookies.set("token", token)

    # --------------------------------------------------------------------- #
    # Public API methods
    # --------------------------------------------------------------------- #
    def list_bookings(
        self, firstname: Optional[str] = None, lastname: Optional[str] = None
    ) -> List[int]:
        """
        Return a list of booking IDs. Optional ``firstname`` and ``lastname``
        filter the results.
        """
        params: Dict[str, str] = {}
        if firstname is not None:
            params["firstname"] = firstname
        if lastname is not None:
            params["lastname"] = lastname

        url = f"{self.base_url}/booking"
        resp = self._client.get(url, params=params, timeout=10.0)
        resp.raise_for_status()
        data = resp.json()
        # Expected format: [{ "bookingid": 1 }, { "bookingid": 2 }, ...]
        return [int(item["bookingid"]) for item in data]

    def get_booking(self, id: int) -> Dict:
        """Retrieve the details of a booking (without the ID)."""
        url = f"{self.base_url}/booking/{id}"
        resp = self._client.get(url, timeout=10.0)
        resp.raise_for_status()
        return resp.json()

    def create_booking(self, body: Dict) -> int:
        """
        Create a new booking.

        Parameters
        ----------
        body: dict
            JSON object containing the booking fields.

        Returns
        -------
        int
            The newly created booking's ID.
        """
        url = f"{self.base_url}/booking"
        resp = self._client.post(url, json=body, timeout=10.0)
        resp.raise_for_status()
        data = resp.json()
        return int(data["bookingid"])

    def update_booking(self, id: int, body: Dict) -> Dict:
        """
        Replace an existing booking.

        Returns the updated booking (JSON object without the ID).
        """
        url = f"{self.base_url}/booking/{id}"
        resp = self._client.put(url, json=body, timeout=10.0)
        resp.raise_for_status()
        return resp.json()

    def delete_booking(self, id: int) -> None:
        """Delete a booking; returns ``None``."""
        url = f"{self.base_url}/booking/{id}"
        resp = self._client.delete(url, timeout=10.0)
        resp.raise_for_status()
        # No data to return; the API returns a plain‑text body.
