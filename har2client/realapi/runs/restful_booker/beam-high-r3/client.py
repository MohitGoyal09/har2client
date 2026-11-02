import httpx
from typing import Any


class Client:
    def __init__(self, base_url: str, username: str, password: str):
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self._token: str | None = None

    def _auth_token(self) -> str:
        if self._token is None:
            response = httpx.post(
                f"{self.base_url}/auth",
                json={"username": self.username, "password": self.password},
            )
            response.raise_for_status()
            self._token = response.json()["token"]
        return self._token

    def list_bookings(
        self, firstname: str | None = None, lastname: str | None = None
    ) -> list[int]:
        params: dict[str, str] = {}
        if firstname is not None:
            params["firstname"] = firstname
        if lastname is not None:
            params["lastname"] = lastname

        response = httpx.get(f"{self.base_url}/booking", params=params)
        response.raise_for_status()
        return [item["bookingid"] for item in response.json()]

    def get_booking(self, id: int) -> dict:
        response = httpx.get(f"{self.base_url}/booking/{id}")
        response.raise_for_status()
        return response.json()

    def create_booking(self, body: dict) -> int:
        response = httpx.post(
            f"{self.base_url}/booking",
            json=body,
            headers={"Accept": "application/json"},
        )
        response.raise_for_status()
        return response.json()["bookingid"]

    def update_booking(self, id: int, body: dict) -> dict:
        token = self._auth_token()
        response = httpx.put(
            f"{self.base_url}/booking/{id}",
            json=body,
            cookies={"token": token},
            headers={"Accept": "application/json"},
        )
        response.raise_for_status()
        return response.json()

    def delete_booking(self, id: int) -> None:
        token = self._auth_token()
        response = httpx.delete(
            f"{self.base_url}/booking/{id}",
            cookies={"token": token},
        )
        response.raise_for_status()
        return None
