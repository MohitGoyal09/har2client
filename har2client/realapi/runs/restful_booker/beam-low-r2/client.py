import httpx
from typing import Any


class Client:
    def __init__(self, base_url: str, username: str, password: str):
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self._token: str | None = None
        self._http = httpx.Client()

    def _login(self) -> None:
        if self._token is not None:
            return
        response = self._http.post(
            f"{self.base_url}/auth",
            json={"username": self.username, "password": self.password},
            headers={"Accept": "application/json"},
        )
        response.raise_for_status()
        self._token = response.json()["token"]

    def _auth_headers(self) -> dict[str, str]:
        self._login()
        return {"Cookie": f"token={self._token}"}

    def list_bookings(
        self, firstname: str | None = None, lastname: str | None = None
    ) -> list[int]:
        params: dict[str, str] = {}
        if firstname is not None:
            params["firstname"] = firstname
        if lastname is not None:
            params["lastname"] = lastname

        response = self._http.get(
            f"{self.base_url}/booking",
            params=params,
            headers={"Accept": "application/json"},
        )
        response.raise_for_status()
        return [item["bookingid"] for item in response.json()]

    def get_booking(self, id: int) -> dict:
        response = self._http.get(
            f"{self.base_url}/booking/{id}",
            headers={"Accept": "application/json"},
        )
        response.raise_for_status()
        return response.json()

    def create_booking(self, body: dict) -> int:
        response = self._http.post(
            f"{self.base_url}/booking",
            json=body,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
        )
        response.raise_for_status()
        return response.json()["bookingid"]

    def update_booking(self, id: int, body: dict) -> dict:
        response = self._http.put(
            f"{self.base_url}/booking/{id}",
            json=body,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                **self._auth_headers(),
            },
        )
        response.raise_for_status()
        return response.json()

    def delete_booking(self, id: int) -> None:
        response = self._http.delete(
            f"{self.base_url}/booking/{id}",
            headers={
                "Accept": "*/*",
                "Content-Type": "application/json",
                **self._auth_headers(),
            },
        )
        response.raise_for_status()
        return None
