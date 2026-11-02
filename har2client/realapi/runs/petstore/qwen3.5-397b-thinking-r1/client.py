from typing import Any

import httpx


class Client:
    def __init__(self, base_url: str, username: str, password: str):
        self.base_url = base_url
        self.api_key = password
        self.client = httpx.Client()

    def _headers(self) -> dict[str, str]:
        return {"api_key": self.api_key, "Content-Type": "application/json"}

    def create_pet(self, body: dict) -> dict:
        response = self.client.post(f"{self.base_url}/pet", json=body, headers=self._headers())
        response.raise_for_status()
        return response.json()

    def get_pet(self, id: int) -> dict:
        response = self.client.get(f"{self.base_url}/pet/{id}", headers=self._headers())
        response.raise_for_status()
        return response.json()

    def find_pets_by_status(self, status: str) -> list[dict]:
        response = self.client.get(
            f"{self.base_url}/pet/findByStatus",
            params={"status": status},
            headers=self._headers(),
        )
        response.raise_for_status()
        return response.json()

    def update_pet(self, body: dict) -> dict:
        response = self.client.put(f"{self.base_url}/pet", json=body, headers=self._headers())
        response.raise_for_status()
        return response.json()

    def delete_pet(self, id: int) -> None:
        response = self.client.delete(f"{self.base_url}/pet/{id}", headers=self._headers())
        response.raise_for_status()
