from typing import Any
import httpx


class Client:
    def __init__(self, base_url: str, username: str, password: str):
        """
        Initialize the Petstore API client.
        
        Args:
            base_url: The API root URL (e.g., https://petstore3.swagger.io/api/v3)
            username: Not used for this API
            password: The API key used for authentication
        """
        self.base_url = base_url
        self.api_key = password
        self.client = httpx.Client()

    def _get_headers(self) -> dict[str, str]:
        """Return headers with API key authentication."""
        return {"api_key": self.api_key, "Accept": "application/json", "Content-Type": "application/json"}

    def create_pet(self, body: dict) -> dict:
        """
        Create a pet from body (a JSON object with the pet fields, including its id).
        Return the created pet.
        """
        url = f"{self.base_url}/pet"
        response = self.client.post(url, json=body, headers=self._get_headers())
        response.raise_for_status()
        return response.json()

    def get_pet(self, id: int) -> dict:
        """
        Return the pet with this id.
        """
        url = f"{self.base_url}/pet/{id}"
        response = self.client.get(url, headers=self._get_headers())
        response.raise_for_status()
        return response.json()

    def find_pets_by_status(self, status: str) -> list[dict]:
        """
        Return the pets whose status is status.
        """
        url = f"{self.base_url}/pet/findByStatus"
        params = {"status": status}
        response = self.client.get(url, params=params, headers=self._get_headers())
        response.raise_for_status()
        return response.json()

    def update_pet(self, body: dict) -> dict:
        """
        Replace the pet that has body["id"] with body.
        Return the updated pet.
        """
        url = f"{self.base_url}/pet"
        response = self.client.put(url, json=body, headers=self._get_headers())
        response.raise_for_status()
        return response.json()

    def delete_pet(self, id: int) -> None:
        """
        Delete the pet. Return None.
        """
        url = f"{self.base_url}/pet/{id}"
        response = self.client.delete(url, headers=self._get_headers())
        response.raise_for_status()
        return None
