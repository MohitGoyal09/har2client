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
        data = response.json()
        self._token = data["accessToken"]

    def _headers(self) -> dict[str, str]:
        if self._token is None:
            self._login()
        return {"Authorization": f"Bearer {self._token}"}

    def _get_json(self, path: str, params: dict[str, Any] | None = None) -> Any:
        response = httpx.get(
            f"{self.base_url}{path}",
            headers=self._headers(),
            params=params,
        )
        response.raise_for_status()
        if response.status_code == 204 or not response.content:
            return None
        return response.json()

    def _post_json(self, path: str, body: dict) -> Any:
        response = httpx.post(
            f"{self.base_url}{path}",
            headers=self._headers(),
            json=body,
        )
        response.raise_for_status()
        if response.status_code == 204 or not response.content:
            return None
        return response.json()

    def _fetch_all(self, path: str, extra_params: dict[str, Any] | None = None) -> list[dict]:
        result: list[dict] = []
        skip = 0
        limit = 30
        while True:
            params: dict[str, Any] = {"limit": limit, "skip": skip}
            if extra_params:
                params.update(extra_params)
            data = self._get_json(path, params=params)
            products = data.get("products", []) if isinstance(data, dict) else []
            result.extend(products)
            total = data.get("total", len(result)) if isinstance(data, dict) else len(result)
            skip += len(products)
            if not products or skip >= total:
                break
        return result

    def get_me(self) -> dict:
        return self._get_json("/auth/me")

    def list_products(self, category: str | None = None) -> list[dict]:
        if category is None:
            return self._fetch_all("/products")
        from urllib.parse import quote

        return self._fetch_all(f"/products/category/{quote(str(category), safe='')}")

    def search_products(self, query: str) -> list[dict]:
        return self._fetch_all("/products/search", {"q": query})

    def get_product(self, id: int) -> dict:
        return self._get_json(f"/products/{id}")

    def add_product(self, body: dict) -> dict:
        return self._post_json("/products/add", body)
