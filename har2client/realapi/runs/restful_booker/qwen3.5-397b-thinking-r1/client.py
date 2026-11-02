import httpx
from typing import Optional


class Client:
    def __init__(self, base_url: str, username: str, password: str):
        self.base_url = base_url
        self._client = httpx.Client()
        
        # Login to get auth token
        response = self._client.post(
            f"{self.base_url}/auth",
            json={"username": username, "password": password}
        )
        data = response.json()
        token = data["token"]
        
        # Set token as cookie for authenticated requests
        url = httpx.URL(base_url)
        self._client.cookies.set("token", token, domain=url.host)
    
    def list_bookings(self, firstname: Optional[str] = None, lastname: Optional[str] = None) -> list[int]:
        params = {}
        if firstname is not None:
            params["firstname"] = firstname
        if lastname is not None:
            params["lastname"] = lastname
        
        response = self._client.get(f"{self.base_url}/booking", params=params)
        bookings = response.json()
        return [b["bookingid"] for b in bookings]
    
    def get_booking(self, id: int) -> dict:
        response = self._client.get(f"{self.base_url}/booking/{id}")
        return response.json()
    
    def create_booking(self, body: dict) -> int:
        response = self._client.post(f"{self.base_url}/booking", json=body)
        data = response.json()
        return data["bookingid"]
    
    def update_booking(self, id: int, body: dict) -> dict:
        response = self._client.put(f"{self.base_url}/booking/{id}", json=body)
        return response.json()
    
    def delete_booking(self, id: int) -> None:
        self._client.delete(f"{self.base_url}/booking/{id}")
