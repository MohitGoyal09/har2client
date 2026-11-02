import httpx
from typing import Any


class Client:
    def __init__(self, base_url: str, username: str, password: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self._http = httpx.Client(
            headers={
                "api_key": password,
                "Accept": "application/json",
            }
        )

    def create_pet(self, body: dict) -> dict:
        response = self._http.post(f"{self.base_url}/pet", json=body)
        response.raise_for_status()
        return response.json() if response.content else None

    def get_pet(self, id: int) -> dict:
        response = self._http.get(f"{self.base_url}/pet/{id}")
        response.raise_for_status()
        return response.json() if response.content else None

    def find_pets_by_status(self, status: str) -> list[dict]:
        response = self._http.get(
            f"{self.base_url}/pet/findByStatus", params={"status": status}
        )
        response.raise_for_status()
        return response.json() if response.content else None

    def update_pet(self, body: dict) -> dict:
        response = self._http.put(f"{self.base_url}/pet", json=body)
        response.raise_for_status()
        return response.json() if response.content else None

    def delete_pet(self, id: int) -> None:
        response = self._http.delete(f"{self.base_url}/pet/{id}")
        response.raise_for_status()
        return None
