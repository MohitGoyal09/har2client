from typing import Any

import httpx


class Client:
    def __init__(self, base_url: str, username: str, password: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self._access_token: str | None = None
        self._http = httpx.Client(base_url=self.base_url, timeout=30.0)

    def _login(self) -> None:
        response = self._http.post(
            "/auth/login",
            json={
                "username": self.username,
                "password": self.password,
                "expiresInMins": 30,
            },
        )
        response.raise_for_status()
        data = response.json()
        self._access_token = data["accessToken"]
        self._http.headers["Authorization"] = f"Bearer {self._access_token}"

    def _ensure_authenticated(self) -> None:
        if self._access_token is None:
            self._login()

    def _get_all_products(self, path: str, extra_params: dict[str, Any] | None = None) -> list[dict]:
        self._ensure_authenticated()
        extra_params = extra_params or {}
        products: list[dict] = []
        skip = 0
        limit = 30

        while True:
            params = {"limit": limit, "skip": skip}
            params.update(extra_params)
            response = self._http.get(path, params=params)
            response.raise_for_status()
            data = response.json()
            page = data.get("products", [])
            products.extend(page)

            total = data.get("total", len(products))
            skip += len(page) if page else limit

            if not page or len(products) >= total:
                break

        return products

    def get_me(self) -> dict:
        self._ensure_authenticated()
        response = self._http.get("/auth/me")
        response.raise_for_status()
        return response.json()

    def list_products(self, category: str | None = None) -> list[dict]:
        if category is None:
            return self._get_all_products("/products")
        from urllib.parse import quote

        encoded_category = quote(str(category), safe="")
        return self._get_all_products(f"/products/category/{encoded_category}")

    def search_products(self, query: str) -> list[dict]:
        return self._get_all_products("/products/search", {"q": query})

    def get_product(self, id: int) -> dict:
        self._ensure_authenticated()
        response = self._http.get(f"/products/{id}")
        response.raise_for_status()
        return response.json()

    def add_product(self, body: dict) -> dict:
        self._ensure_authenticated()
        response = self._http.post("/products/add", json=body)
        response.raise_for_status()
        return response.json()
