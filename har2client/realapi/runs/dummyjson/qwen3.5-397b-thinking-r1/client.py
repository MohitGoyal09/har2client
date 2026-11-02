from typing import Any, Optional
import httpx


class Client:
    def __init__(self, base_url: str, username: str, password: str):
        self.base_url = base_url
        self.username = username
        self.password = password
        self._access_token: Optional[str] = None
        self._client = httpx.Client()
        self._login()
    
    def _login(self) -> None:
        """Perform login and store the access token."""
        response = self._client.post(
            f"{self.base_url}/auth/login",
            json={
                "username": self.username,
                "password": self.password,
                "expiresInMins": 30
            }
        )
        response.raise_for_status()
        data = response.json()
        self._access_token = data["accessToken"]
    
    def _headers(self) -> dict:
        """Return headers with authorization."""
        return {"Authorization": f"Bearer {self._access_token}"}
    
    def get_me(self) -> dict:
        """Return the logged-in user."""
        response = self._client.get(
            f"{self.base_url}/auth/me",
            headers=self._headers()
        )
        response.raise_for_status()
        return response.json()
    
    def list_products(self, category: Optional[str] = None) -> list[dict]:
        """Return every product, across all pages, in the order the server gives them."""
        products = []
        skip = 0
        limit = 100
        
        while True:
            params = {"limit": limit, "skip": skip}
            if category:
                url = f"{self.base_url}/products/category/{category}"
            else:
                url = f"{self.base_url}/products"
            
            response = self._client.get(
                url,
                headers=self._headers(),
                params=params
            )
            response.raise_for_status()
            data = response.json()
            products.extend(data["products"])
            
            if len(data["products"]) < limit:
                break
            
            skip += limit
        
        return products
    
    def search_products(self, query: str) -> list[dict]:
        """Return every product that matches the search text, across all pages."""
        products = []
        skip = 0
        limit = 100
        
        while True:
            params = {"q": query, "limit": limit, "skip": skip}
            response = self._client.get(
                f"{self.base_url}/products/search",
                headers=self._headers(),
                params=params
            )
            response.raise_for_status()
            data = response.json()
            products.extend(data["products"])
            
            if len(data["products"]) < limit:
                break
            
            skip += limit
        
        return products
    
    def get_product(self, id: int) -> dict:
        """Return the product with this id."""
        response = self._client.get(
            f"{self.base_url}/products/{id}",
            headers=self._headers()
        )
        response.raise_for_status()
        return response.json()
    
    def add_product(self, body: dict) -> dict:
        """Add a product from body. Return the product the server returns."""
        response = self._client.post(
            f"{self.base_url}/products/add",
            headers=self._headers(),
            json=body
        )
        response.raise_for_status()
        return response.json()
