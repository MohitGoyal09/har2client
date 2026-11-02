import httpx
from typing import Any


class Client:
    def __init__(self, base_url: str, username: str, password: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self._token: str | None = None
        self._login()

    def _login(self) -> None:
        response = httpx.post(
            f"{self.base_url}/auth/login",
            json={"username": self.username, "password": self.password},
        )
        response.raise_for_status()
        self._token = response.json()["accessToken"]

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"}

    def get_me(self) -> dict:
        response = httpx.get(
            f"{self.base_url}/auth/me",
            headers=self._headers(),
        )
        response.raise_for_status()
        return response.json()

    def list_products(self, category: str | None = None) -> list[dict]:
        products: list[dict] = []
        limit = 30
        skip = 0
        while True:
            if category is None:
                url = f"{self.base_url}/products"
            else:
                url = f"{self.base_url}/products/category/{category}"
            response = httpx.get(
                url,
                params={"limit": limit, "skip": skip},
                headers=self._headers(),
            )
            response.raise_for_status()
            data = response.json()
            page_products = data.get("products", [])
            products.extend(page_products)
            total = data.get("total", len(products))
            skip += len(page_products)
            if not page_products or skip >= total:
                break
        return products

    def search_products(self, query: str) -> list[dict]:
        products: list[dict] = []
        limit = 30
        skip = 0
        while True:
            response = httpx.get(
                f"{self.base_url}/products/search",
                params={"q": query, "limit": limit, "skip": skip},
                headers=self._headers(),
            )
            response.raise_for_status()
            data = response.json()
            page_products = data.get("products", [])
            products.extend(page_products)
            total = data.get("total", len(products))
            skip += len(page_products)
            if not page_products or skip >= total:
                break
        return products

    def get_product(self, id: int) -> dict:
        response = httpx.get(
            f"{self.base_url}/products/{id}",
            headers=self._headers(),
        )
        response.raise_for_status()
        return response.json()

    def add_product(self, body: dict) -> dict:
        response = httpx.post(
            f"{self.base_url}/products/add",
            json=body,
            headers=self._headers(),
        )
        response.raise_for_status()
        return response.json()
