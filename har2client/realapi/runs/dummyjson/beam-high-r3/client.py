import urllib.parse

import httpx


class Client:
    def __init__(self, base_url: str, username: str, password: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self._http = httpx.Client()
        self._access_token: str | None = None
        self._login()

    def _login(self) -> None:
        response = self._http.post(
            f"{self.base_url}/auth/login",
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

    def _get(self, path: str, params: dict | None = None) -> dict:
        response = self._http.get(f"{self.base_url}{path}", params=params)
        response.raise_for_status()
        return response.json()

    def get_me(self) -> dict:
        return self._get("/auth/me")

    def _fetch_all(self, path: str, params: dict) -> list[dict]:
        products: list[dict] = []
        skip = 0
        limit = 30

        while True:
            page_params = dict(params)
            page_params["limit"] = limit
            page_params["skip"] = skip

            data = self._get(path, page_params)
            page_products = data.get("products", [])
            products.extend(page_products)

            if not page_products:
                break

            skip += len(page_products)
            total = data.get("total")
            if total is not None and skip >= total:
                break
            if total is None and len(page_products) < limit:
                break

        return products

    def list_products(self, category: str | None = None) -> list[dict]:
        if category is None:
            return self._fetch_all("/products", {})
        encoded_category = urllib.parse.quote(str(category), safe="")
        return self._fetch_all(f"/products/category/{encoded_category}", {})

    def search_products(self, query: str) -> list[dict]:
        return self._fetch_all("/products/search", {"q": query})

    def get_product(self, id: int) -> dict:
        return self._get(f"/products/{id}")

    def add_product(self, body: dict) -> dict:
        response = self._http.post(
            f"{self.base_url}/products/add",
            json=body,
        )
        response.raise_for_status()
        return response.json()
