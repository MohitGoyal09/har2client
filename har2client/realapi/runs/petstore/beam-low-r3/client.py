import requests
from typing import Any


class Client:
    def __init__(self, base_url: str, username: str, password: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/json",
            "api_key": password,
        })

    def create_pet(self, body: dict) -> dict:
        response = self.session.post(
            f"{self.base_url}/pet",
            json=body,
        )
        response.raise_for_status()
        return response.json()

    def get_pet(self, id: int) -> dict:
        response = self.session.get(f"{self.base_url}/pet/{id}")
        response.raise_for_status()
        return response.json()

    def find_pets_by_status(self, status: str) -> list[dict]:
        response = self.session.get(
            f"{self.base_url}/pet/findByStatus",
            params={"status": status},
        )
        response.raise_for_status()
        return response.json()

    def update_pet(self, body: dict) -> dict:
        response = self.session.put(
            f"{self.base_url}/pet",
            json=body,
        )
        response.raise_for_status()
        return response.json()

    def delete_pet(self, id: int) -> None:
        response = self.session.delete(f"{self.base_url}/pet/{id}")
        response.raise_for_status()
        return None
