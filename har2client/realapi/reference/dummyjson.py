import requests


class Client:
    def __init__(self, base_url: str, username: str, password: str):
        self.base, self.s = base_url, requests.Session()
        token = self.s.post(f"{self.base}/auth/login", json={"username": username, "password": password}, timeout=30).json()["accessToken"]
        self.s.headers["Authorization"] = f"Bearer {token}"

    def _pages(self, path, params=None):
        out, skip = [], 0
        while True:
            page = self.s.get(f"{self.base}{path}", params={**(params or {}), "limit": 30, "skip": skip}, timeout=30).json()
            out += page["products"]
            skip += len(page["products"])
            if not page["products"] or skip >= page["total"]:
                return out

    def get_me(self):
        return self.s.get(f"{self.base}/auth/me", timeout=30).json()

    def list_products(self, category=None):
        return self._pages(f"/products/category/{category}" if category else "/products")

    def search_products(self, query):
        return self._pages("/products/search", {"q": query})

    def get_product(self, id):
        return self.s.get(f"{self.base}/products/{id}", timeout=30).json()

    def add_product(self, body):
        return self.s.post(f"{self.base}/products/add", json=body, timeout=30).json()
