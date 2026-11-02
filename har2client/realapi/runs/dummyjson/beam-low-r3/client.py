import httpx
from typing import Any
from urllib.parse import quote


class Client:
    def __init__(self, base_url: str, username: str, password: str) -> None:
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
            json={"username": self.username, "password": self.password},
        )
        response.raise_for_status()
        data = response.json()
        self._token = data.get("accessToken") or data.get("token")

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        self._login()
        headers = kwargs.pop("headers", {})
        if self._token is not None:
            headers = {"Authorization": f"Bearer {self._token}", **headers}
        response = self._http.request(
            method, f"{self.base_url}{path}", headers=headers, **kwargs
        )
        response.raise_for_status()
        return response

    def get_me(self) -> dict:
        return self._request("GET", "/auth/me").json()

    def _all_products(self, path: str, params: dict[str, Any] | None = None) -> list[dict]:
        params = dict(params or {})
        limit = 30
        skip = 0
        products: list[dict] = []
        while True:
            query = {"limit": limit, "skip": skip, **params}
            data = self._request("GET", path, params=query).json()
            page = data.get("products", [])
            products.extend(page)
            total = data.get("total", len(products))
            skip += len(page) if page else limit
            if not page or len(products) >= total:
                break
        return products

    def list_products(self, category: str | None = None) -> list[dict]:
        if category is None:
            return self._all_products("/products")
        return self._all_products(f"/products/category/{quote(category, safe='')}")

    def search_products(self, query: str) -> list[dict]:
        return self._all_products("/products/search", {"q": query})

    def get_product(self, id: int) -> dict:
        return self._request("GET", f"/products/{id}").json()

    def add_product(self, body: dict) -> dict:
        return self._request("POST", "/products/add", json=body).json()
