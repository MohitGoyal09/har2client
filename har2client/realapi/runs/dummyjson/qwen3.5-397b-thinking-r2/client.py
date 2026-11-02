from typing import Any, Optional
import httpx


class Client:
    def __init__(self, base_url: str, username: str, password: str):
        self._base_url = base_url
        self._username = username
        self._password = password
        self._client = httpx.Client()
        self._access_token: Optional[str] = None
        self._login()

    def _login(self) -> None:
        response = self._client.post(
            f"{self._base_url}/auth/login",
            json={"username": self._username, "password": self._password, "expiresInMins": 30},
        )
        data = response.json()
        self._access_token = data["accessToken"]

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._access_token}"}

    def get_me(self) -> dict[str, Any]:
        response = self._client.get(
            f"{self._base_url}/auth/me",
            headers=self._headers(),
        )
        return response.json()

    def list_products(self, category: Optional[str] = None) -> list[dict[str, Any]]:
        all_products: list[dict[str, Any]] = []
        skip = 0
        limit = 100

        while True:
            if category is None:
                url = f"{self._base_url}/products"
            else:
                url = f"{self._base_url}/products/category/{category}"

            response = self._client.get(
                url,
                headers=self._headers(),
                params={"limit": limit, "skip": skip},
            )
            data = response.json()
            products = data["products"]
            all_products.extend(products)

            skip += limit
            if skip >= data["total"]:
                break

        return all_products

    def search_products(self, query: str) -> list[dict[str, Any]]:
        all_products: list[dict[str, Any]] = []
        skip = 0
        limit = 100

        while True:
            response = self._client.get(
                f"{self._base_url}/products/search",
                headers=self._headers(),
                params={"q": query, "limit": limit, "skip": skip},
            )
            data = response.json()
            products = data["products"]
            all_products.extend(products)

            skip += limit
            if skip >= data["total"]:
                break

        return all_products

    def get_product(self, id: int) -> dict[str, Any]:
        response = self._client.get(
            f"{self._base_url}/products/{id}",
            headers=self._headers(),
        )
        return response.json()

    def add_product(self, body: dict[str, Any]) -> dict[str, Any]:
        response = self._client.post(
            f"{self._base_url}/products/add",
            headers=self._headers(),
            json=body,
        )
        return response.json()
