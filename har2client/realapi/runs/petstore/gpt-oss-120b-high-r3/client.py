import httpx
from typing import Any, Dict, List


class Client:
    """
    Simple client for the Swagger Petstore v3 API.

    Parameters
    ----------
    base_url: str
        API root including the base path, e.g. ``https://petstore3.swagger.io/api/v3``.
    username: str
        Unused – the API uses an API key instead of a login.  Kept for signature compatibility.
    password: str
        API key to be sent as the ``api_key`` header.
    """

    def __init__(self, base_url: str, username: str, password: str) -> None:
        # Normalise the base URL (no trailing slash)
        self.base_url = base_url.rstrip("/")
        # Store the API key (password) – username is ignored
        self.api_key = password
        # Persistent httpx client with the api_key header attached to all requests
        self._http = httpx.Client(headers={"api_key": self.api_key})

    def create_pet(self, *, body: Dict[str, Any]) -> Dict[str, Any]:
        """
        Create a new pet.

        Parameters
        ----------
        body: dict
            JSON payload describing the pet (including its ``id``).

        Returns
        -------
        dict
            The created pet object.
        """
        resp = self._http.post(f"{self.base_url}/pet", json=body)
        resp.raise_for_status()
        return resp.json()

    def get_pet(self, *, id: int) -> Dict[str, Any]:
        """
        Retrieve a pet by its identifier.

        Parameters
        ----------
        id: int
            Pet identifier.

        Returns
        -------
        dict
            The pet object.
        """
        resp = self._http.get(f"{self.base_url}/pet/{id}")
        resp.raise_for_status()
        return resp.json()

    def find_pets_by_status(self, *, status: str) -> List[Dict[str, Any]]:
        """
        Find pets matching a given status.

        Parameters
        ----------
        status: str
            Desired status (e.g., ``available``, ``pending``, ``sold``).

        Returns
        -------
        list[dict]
            List of pet objects.
        """
        resp = self._http.get(f"{self.base_url}/pet/findByStatus", params={"status": status})
        resp.raise_for_status()
        return resp.json()

    def update_pet(self, *, body: Dict[str, Any]) -> Dict[str, Any]:
        """
        Update an existing pet (full replace).

        Parameters
        ----------
        body: dict
            JSON payload for the pet. Must include the ``id`` field.

        Returns
        -------
        dict
            The updated pet object.
        """
        resp = self._http.put(f"{self.base_url}/pet", json=body)
        resp.raise_for_status()
        return resp.json()

    def delete_pet(self, *, id: int) -> None:
        """
        Delete a pet.

        Parameters
        ----------
        id: int
            Pet identifier.

        Returns
        -------
        None
            No value is returned.
        """
        resp = self._http.delete(f"{self.base_url}/pet/{id}")
        resp.raise_for_status()
        # The delete endpoint returns a short message; we intentionally discard it.
        return None

    def __del__(self):
        # Ensure the underlying httpx client is closed when the instance is garbage‑collected.
        self._http.close()
