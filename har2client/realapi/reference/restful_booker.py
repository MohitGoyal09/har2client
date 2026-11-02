import requests


class Client:
    def __init__(self, base_url: str, username: str, password: str):
        self.base, self.s = base_url, requests.Session()
        self.s.headers.update({"Accept": "application/json"})
        token = self.s.post(f"{self.base}/auth", json={"username": username, "password": password}, timeout=30).json()["token"]
        self.s.cookies.set("token", token)

    def list_bookings(self, firstname=None, lastname=None):
        params = {k: v for k, v in {"firstname": firstname, "lastname": lastname}.items() if v is not None}
        return [b["bookingid"] for b in self.s.get(f"{self.base}/booking", params=params, timeout=30).json()]

    def get_booking(self, id):
        return self.s.get(f"{self.base}/booking/{id}", timeout=30).json()

    def create_booking(self, body):
        return self.s.post(f"{self.base}/booking", json=body, timeout=30).json()["bookingid"]

    def update_booking(self, id, body):
        return self.s.put(f"{self.base}/booking/{id}", json=body, timeout=30).json()

    def delete_booking(self, id):
        self.s.delete(f"{self.base}/booking/{id}", timeout=30).raise_for_status()
