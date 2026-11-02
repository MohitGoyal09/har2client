from typing import Any, Dict, List, Optional

import httpx


class Client:
    def __init__(self, base_url: str, username: str, password: str):
        self.base_url = base_url
        self.username = username
        self.password = password
        self._client = httpx.Client()
        self._login()

    def _login(self) -> None:
        response = self._client.post(
            f"{self.base_url}/auth",
            json={"username": self.username, "password": self.password},
        )
        response.raise_for_status()
        data = response.json()
        token = data["token"]
        self._client.cookies.set("token", token)

    def list_bookings(
        self, firstname: Optional[str] = None, lastname: Optional[str] = None
    ) -> List[int]:
        params: Dict[str, Any] = {}
        if firstname is not None:
            params["firstname"] = firstname
        if lastname is not None:
            params["lastname"] = lastname
        response = self._client.get(f"{self.base_url}/booking", params=params)
        response.raise_for_status()
        data = response.json()
        return [item["bookingid"] for item in data]

    def get_booking(self, id: int) -> Dict[str, Any]:
        response = self._client.get(f"{self.base_url}/booking/{id}")
        response.raise_for_status()
        return response.json()

    def create_booking(self, body: Dict[str, Any]) -> int:
        response = self._client.post(f"{self.base_url}/booking", json=body)
        response.raise_for_status()
        data = response.json()
        return data["bookingid"]

    def update_booking(self, id: int, body: Dict[str, Any]) -> Dict[str, Any]:
        response = self._client.put(f"{self.base_url}/booking/{id}", json=body)
        response.raise_for_status()
        return response.json()

    def delete_booking(self, id: int) -> None:
        response = self._client.delete(f"{self.base_url}/booking/{id}")
        response.raise_for_status()
