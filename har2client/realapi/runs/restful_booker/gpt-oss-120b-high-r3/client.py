import httpx
from typing import List, Optional, Dict, Any


class Client:
    """
    Client for the Restful‑Booker API.
    """

    def __init__(self, base_url: str, username: str, password: str):
        """
        Initialise the client and perform authentication.

        Args:
            base_url: Root URL of the API (no trailing slash).
            username: Account username.
            password: Account password.
        """
        # Normalise base URL – httpx will join relative paths correctly.
        self.base_url = base_url.rstrip("/")
        # Re‑use a single HTTP/2 capable connection pool.
        self._client = httpx.Client(base_url=self.base_url, timeout=30.0)
        # Obtain a token and store it as a cookie for later authenticated calls.
        self._token = self._authenticate(username, password)

        # Attach the token cookie to the client.  The domain is taken from the base URL.
        try:
            domain = httpx.URL(self.base_url).host
        except Exception:
            domain = None
        if domain:
            self._client.cookies.set("token", self._token, domain=domain)
        else:
            # Fallback – set without explicit domain.
            self._client.cookies.set("token", self._token)

    # --------------------------------------------------------------------- #
    # Internal helpers
    # --------------------------------------------------------------------- #
    def _authenticate(self, username: str, password: str) -> str:
        """POST /auth and return the authentication token."""
        resp = self._client.post("/auth", json={"username": username, "password": password})
        resp.raise_for_status()
        data = resp.json()
        token = data.get("token")
        if not token:
            raise RuntimeError("Authentication token not found in response")
        return token

    # --------------------------------------------------------------------- #
    # Public API
    # --------------------------------------------------------------------- #
    def list_bookings(self, firstname: Optional[str] = None,
                      lastname: Optional[str] = None) -> List[int]:
        """
        Return the ids of the bookings. When ``firstname`` or ``lastname`` is given,
        return only the matching bookings.

        Args:
            firstname: Optional filter by first name.
            lastname:  Optional filter by last name.

        Returns:
            List of booking identifiers.
        """
        params: dict = {}
        if firstname is not None:
            params["firstname"] = firstname
        if lastname is not None:
            params["lastname"] = lastname

        resp = self._client.get("/booking", params=params)
        resp.raise_for_status()
        data = resp.json()

        ids: List[int] = []
        for entry in data:
            if isinstance(entry, dict):
                # Standard response shape: {"bookingid": 123}
                if "bookingid" in entry:
                    ids.append(entry["bookingid"])
                else:
                    # Some API flavours may embed the id elsewhere; fall back to any int value.
                    for v in entry.values():
                        if isinstance(v, int):
                            ids.append(v)
            elif isinstance(entry, int):
                ids.append(entry)
        return ids

    def get_booking(self, id: int) -> Dict[str, Any]:
        """
        Retrieve a booking.

        Args:
            id: Booking identifier.

        Returns:
            Booking data as a dictionary (fields only, without the id).
        """
        resp = self._client.get(f"/booking/{id}")
        resp.raise_for_status()
        return resp.json()

    def create_booking(self, body: Dict[str, Any]) -> int:
        """
        Create a new booking.

        Args:
            body: JSON‑compatible dict with the booking fields.

        Returns:
            Identifier of the newly created booking.
        """
        resp = self._client.post("/booking", json=body)
        resp.raise_for_status()
        data = resp.json()
        # Typical shape: {"bookingid": 123, "booking": {...}}
        if isinstance(data, dict) and "bookingid" in data:
            return data["bookingid"]
        # Some implementations may return the id directly.
        if isinstance(data, int):
            return data
        raise RuntimeError("Unexpected response format when creating a booking")

    def update_booking(self, id: int, body: Dict[str, Any]) -> Dict[str, Any]:
        """
        Replace an existing booking.

        Args:
            id: Identifier of the booking to replace.
            body: JSON‑compatible dict with the new booking fields.

        Returns:
            The updated booking data.
        """
        resp = self._client.put(f"/booking/{id}", json=body)
        resp.raise_for_status()
        return resp.json()

    def delete_booking(self, id: int) -> None:
        """
        Delete a booking.

        Args:
            id: Identifier of the booking to delete.

        Returns:
            None.
        """
        resp = self._client.delete(f"/booking/{id}")
        resp.raise_for_status()
        # No content required – simply ensure the request succeeded.
        return None
