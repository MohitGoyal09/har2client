import httpx
from typing import Any, Optional


class Client:
    def __init__(self, base_url: str, username: str, password: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self._token: Optional[str] = None
        self._http = httpx.Client()

    def _authenticate(self) -> str:
        if self._token is None:
            response = self._http.post(
                f"{self.base_url}/auth",
                json={"username": self.username, "password": self.password},
            )
            response.raise_for_status()
            self._token = response.json()["token"]
        return self._token

    def list_bookings(
        self, firstname: Optional[str] = None, lastname: Optional[str] = None
    ) -> list[int]:
        params: dict[str, str] = {}
        if firstname is not None:
            params["firstname"] = firstname
        if lastname is not None:
            params["lastname"] = lastname

        response = self._http.get(f"{self.base_url}/booking", params=params)
        response.raise_for_status()
        return [item["bookingid"] for item in response.json()]

    def get_booking(self, id: int) -> dict:
        response = self._http.get(f"{self.base_url}/booking/{id}")
        response.raise_for_status()
        return response.json()

    def create_booking(self, body: dict) -> int:
        response = self._http.post(
            f"{self.base_url}/booking",
            json=body,
        )
        response.raise_for_status()
        return response.json()["bookingid"]

    def update_booking(self, id: int, body: dict) -> dict:
        token = self._authenticate()
        response = self._http.put(
            f"{self.base_url}/booking/{id}",
            json=body,
            cookies={"token": token},
        )
        response.raise_for_status()
        return response.json()

    def delete_booking(self, id: int) -> None:
        token = self._authenticate()
        response = self._http.delete(
            f"{self.base_url}/booking/{id}",
            cookies={"token": token},
        )
        response.raise_for_status()
        return None
