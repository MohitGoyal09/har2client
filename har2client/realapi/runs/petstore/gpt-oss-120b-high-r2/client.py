import requests
from typing import List, Dict, Any


class Client:
    """
    Simple client for the Swagger Petstore (OpenAPI v3) using an API key.

    Parameters
    ----------
    base_url: str
        The API root URL, already including the base path (e.g. ``https://example.com/api/v3``).
        No trailing slash should be present; the constructor normalises it.
    username: str
        Unused – the Petstore API does not require a username.
    password: str
        The API key; it is sent in the ``api_key`` request header.
    """

    def __init__(self, base_url: str, username: str, password: str):
        # Normalise base_url (remove trailing slash if present)
        self.base_url = base_url.rstrip("/")
        self.api_key = password

        # Re‑use a single underlying ``requests`` session for connection pooling.
        self.session = requests.Session()
        # The API expects the key in a header called ``api_key``.
        self.session.headers.update(
            {
                "Accept": "application/json",
                "api_key": self.api_key,
            }
        )
        # ``username`` is not used for this API.

    def create_pet(self, *, body: Dict[str, Any]) -> Dict[str, Any]:
        """
        Create a new pet.

        Sends a POST request to ``/pet`` with the JSON body.
        Returns the created pet object as returned by the server.
        """
        url = f"{self.base_url}/pet"
        response = self.session.post(url, json=body)
        response.raise_for_status()
        return response.json()

    def get_pet(self, *, id: int) -> Dict[str, Any]:
        """
        Retrieve a pet by its identifier.

        Sends a GET request to ``/pet/{petId}``.
        Returns the pet object.
        """
        url = f"{self.base_url}/pet/{id}"
        response = self.session.get(url)
        response.raise_for_status()
        return response.json()

    def find_pets_by_status(self, *, status: str) -> List[Dict[str, Any]]:
        """
        Find pets that match a given status.

        Sends a GET request to ``/pet/findByStatus`` with the ``status`` query parameter.
        Returns a list of pet objects.
        """
        url = f"{self.base_url}/pet/findByStatus"
        response = self.session.get(url, params={"status": status})
        response.raise_for_status()
        return response.json()

    def update_pet(self, *, body: Dict[str, Any]) -> Dict[str, Any]:
        """
        Replace an existing pet.

        Sends a PUT request to ``/pet`` with the full pet representation.
        Returns the updated pet object.
        """
        url = f"{self.base_url}/pet"
        response = self.session.put(url, json=body)
        response.raise_for_status()
        return response.json()

    def delete_pet(self, *, id: int) -> None:
        """
        Delete a pet by its identifier.

        Sends a DELETE request to ``/pet/{petId}``. The API returns a plain‑text
        confirmation which we discard, as the method contract specifies ``None``.
        """
        url = f"{self.base_url}/pet/{id}"
        response = self.session.delete(url)
        response.raise_for_status()
        # Discard any response body; the method should return ``None``.
        return None
