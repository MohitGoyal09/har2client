import httpx
from typing import Any


class Client:
    def __init__(self, base_url: str, username: str, password: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self._client = httpx.Client(
            headers={"api_key": password},
            timeout=30.0,
        )

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    def create_pet(self, body: dict) -> dict:
        response = self._client.post(self._url("/pet"), json=body)
        response.raise_for_status()
        return response.json()

    def get_pet(self, id: int) -> dict:
        response = self._client.get(self._url(f"/pet/{id}"))
        response.raise_for_status()
        return response.json()

    def find_pets_by_status(self, status: str) -> list[dict]:
        response = self._client.get(
            self._url("/pet/findByStatus"),
            params={"status": status},
        )
        response.raise_for_status()
        return response.json()

    def update_pet(self, body: dict) -> dict:
        response = self._client.put(self._url("/pet"), json=body)
        response.raise_for_status()
        return response.json()

    def delete_pet(self, id: int) -> None:
        response = self._client.delete(self._url(f"/pet/{id}"))
        response.raise_for_status()
        return None
