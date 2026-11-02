import httpx
from typing import Any


class Client:
    def __init__(self, base_url: str, username: str, password: str):
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self._token: str | None = None
        self._http = httpx.Client()

    def _login(self) -> None:
        if self._token is not None:
            return
        response = self._http.post(
            f"{self.base_url}/auth/login",
            json={
                "username": self.username,
                "password": self.password,
                "expiresInMins": 30,
            },
        )
        response.raise_for_status()
        self._token = response.json()["accessToken"]

    def _headers(self) -> dict[str, str]:
        self._login()
        return {"Authorization": f"Bearer {self._token}"}

    def _get(self, path: str, params: dict[str, Any] | None = None) -> httpx.Response:
        response = self._http.get(
            f"{self.base_url}{path}",
            params=params,
            headers=self._headers(),
        )
        response.raise_for_status()
        return response

    def get_me(self) -> dict:
        response = self._get("/auth/me")
        return response.json()

    def list_products(self, category: str | None = None) -> list[dict]:
        if category is None:
            path = "/products"
        else:
            path = f"/products/category/{category}"

        products: list[dict] = []
        skip = 0
        limit = 30

        while True:
            response = self._get(
                path,
                params={"limit": limit, "skip": skip},
            )
            data = response.json()
            page_products = data.get("products", [])
            products.extend(page_products)

            total = data.get("total", len(products))
            if not page_products or len(products) >= total:
                break
            skip += len(page_products)

        return products

    def search_products(self, query: str) -> list[dict]:
        products: list[dict] = []
        skip = 0
        limit = 30

        while True:
            response = self._get(
                "/products/search",
                params={"q": query, "limit": limit, "skip": skip},
            )
            data = response.json()
            page_products = data.get("products", [])
            products.extend(page_products)

            total = data.get("total", len(products))
            if not page_products or len(products) >= total:
                break
            skip += len(page_products)

        return products

    def get_product(self, id: int) -> dict:
        response = self._get(f"/products/{id}")
        return response.json()

    def add_product(self, body: dict) -> dict:
        response = self._http.post(
            f"{self.base_url}/products/add",
            json=body,
            headers=self._headers(),
        )
        response.raise_for_status()
        return response.json()
