"""client.py – Swagger Petstore v3 client.

Implements the minimal set of methods required by the test suite:
- create_pet
- get_pet
- find_pets_by_status
- update_pet
- delete_pet
"""

from __future__ import annotations

from typing import Any, Dict, List

import httpx


class Client:
    """Simple client for the Swagger Petstore v3 API.

    Args:
        base_url: API root URL (e.g. "https://petstore3.swagger.io/api/v3").
                  Must not end with a trailing slash.
        username: Unused – kept for signature compatibility.
        password: API key to be sent as the ``api_key`` header.
    """

    def __init__(self, base_url: str, username: str, password: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = password  # ``username`` is ignored.
        # A single httpx client gives connection‑pooling benefits.
        self._http = httpx.Client()

    def _auth_headers(self) -> Dict[str, str]:
        """Headers required for authentication."""
        # The Petstore expects the API key in the ``api_key`` header.
        return {"api_key": self.api_key} if self.api_key else {}

    def create_pet(self, *, body: Dict[str, Any]) -> Dict[str, Any]:
        """Create a new pet.

        Sends a POST request to ``/pet`` with a JSON body and returns the created pet.
        """
        url = f"{self.base_url}/pet"
        resp = self._http.post(url, json=body, headers=self._auth_headers())
        resp.raise_for_status()
        return resp.json()

    def get_pet(self, *, id: int) -> Dict[str, Any]:
        """Retrieve a pet by its identifier."""
        url = f"{self.base_url}/pet/{id}"
        resp = self._http.get(url, headers=self._auth_headers())
        resp.raise_for_status()
        return resp.json()

    def find_pets_by_status(self, *, status: str) -> List[Dict[str, Any]]:
        """Find pets that match a given status.

        The ``status`` query parameter may be a comma‑separated list; here we send it as‑is.
        """
        url = f"{self.base_url}/pet/findByStatus"
        resp = self._http.get(
            url,
            params={"status": status},
            headers=self._auth_headers(),
        )
        resp.raise_for_status()
        return resp.json()

    def update_pet(self, *, body: Dict[str, Any]) -> Dict[str, Any]:
        """Replace an existing pet with the data in ``body``."""
        url = f"{self.base_url}/pet"
        resp = self._http.put(url, json=body, headers=self._auth_headers())
        resp.raise_for_status()
        return resp.json()

    def delete_pet(self, *, id: int) -> None:
        """Delete a pet by its identifier.

        The response body is ignored; the method returns ``None`` on success.
        """
        url = f"{self.base_url}/pet/{id}"
        resp = self._http.delete(url, headers=self._auth_headers())
        resp.raise_for_status()
        return None

    def __del__(self) -> None:
        # Ensure the underlying httpx client is closed when the Client is garbage‑collected.
        self._http.close()
