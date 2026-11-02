import httpx
from typing import Any


class Client:
    def __init__(self, base_url: str, username: str, password: str):
        self.base_url = base_url.rstrip("/")
        self.username = username  # Not used by this API.
        self._client = httpx.Client(
            headers={"api_key": password, "Accept": "application/json"}
        )

    def create_pet(self, body: dict) -> dict:
        response = self._client.post(f"{self.base_url}/pet", json=body)
        response.raise_for_status()
        return response.json()

    def get_pet(self, id: int) -> dict:
        response = self._client.get(f"{self.base_url}/pet/{id}")
        response.raise_for_status()
        return response.json()

    def find_pets_by_status(self, status: str) -> list[dict]:
        response = self._client.get(
            f"{self.base_url}/pet/findByStatus", params={"status": status}
        )
        response.raise_for_status()
        return response.json()

    def update_pet(self, body: dict) -> dict:
        response = self._client.put(f"{self.base_url}/pet", json=body)
        response.raise_for_status()
        return response.json()

    def delete_pet(self, id: int) -> None:
        response = self._client.delete(f"{self.base_url}/pet/{id}")
        response.raise_for_status()
        return None
