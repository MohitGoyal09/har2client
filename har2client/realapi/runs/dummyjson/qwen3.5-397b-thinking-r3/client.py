from typing import Optional
import httpx


class Client:
    def __init__(self, base_url: str, username: str, password: str):
        self.base_url = base_url
        self.username = username
        self.password = password
        self._access_token: Optional[str] = None
        self._client = httpx.Client()

    def _ensure_authenticated(self) -> None:
        """Perform login if not already authenticated."""
        if self._access_token is None:
            response = self._client.post(
                f"{self.base_url}/auth/login",
                json={"username": self.username, "password": self.password, "expiresInMins": 30}
            )
            response.raise_for_status()
            data = response.json()
            self._access_token = data["accessToken"]

    def _get_headers(self) -> dict:
        """Get headers with authorization."""
        self._ensure_authenticated()
        return {"Authorization": f"Bearer {self._access_token}"}

    def get_me(self) -> dict:
        """Return the logged-in user."""
        response = self._client.get(
            f"{self.base_url}/auth/me",
            headers=self._get_headers()
        )
        response.raise_for_status()
        return response.json()

    def list_products(self, category: Optional[str] = None) -> list:
        """Return every product, across all pages, in the order the server gives them."""
        self._ensure_authenticated()
        all_products = []
        skip = 0

        while True:
            if category:
                url = f"{self.base_url}/products/category/{category}"
            else:
                url = f"{self.base_url}/products"

            response = self._client.get(
                url,
                headers=self._get_headers(),
                params={"limit": 100, "skip": skip}
            )
            response.raise_for_status()
            data = response.json()

            products = data.get("products", [])
            if not products:
                break

            all_products.extend(products)
            skip += len(products)

            if len(products) < 100:
                break

        return all_products

    def search_products(self, query: str) -> list:
        """Return every product that matches the search text, across all pages."""
        self._ensure_authenticated()
        all_products = []
        skip = 0

        while True:
            response = self._client.get(
                f"{self.base_url}/products/search",
                headers=self._get_headers(),
                params={"q": query, "limit": 100, "skip": skip}
            )
            response.raise_for_status()
            data = response.json()

            products = data.get("products", [])
            if not products:
                break

            all_products.extend(products)
            skip += len(products)

            if len(products) < 100:
                break

        return all_products

    def get_product(self, id: int) -> dict:
        """Return the product with this id."""
        response = self._client.get(
            f"{self.base_url}/products/{id}",
            headers=self._get_headers()
        )
        response.raise_for_status()
        return response.json()

    def add_product(self, body: dict) -> dict:
        """Add a product from body. Return the product the server returns."""
        response = self._client.post(
            f"{self.base_url}/products/add",
            headers=self._get_headers(),
            json=body
        )
        response.raise_for_status()
        return response.json()
