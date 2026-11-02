import requests


class Client:
    def __init__(self, base_url: str, username: str, password: str):
        self.base, self.s = base_url, requests.Session()
        self.s.headers.update({"Accept": "application/json", "api_key": password})

    def create_pet(self, body):
        return self.s.post(f"{self.base}/pet", json=body, timeout=30).json()

    def get_pet(self, id):
        return self.s.get(f"{self.base}/pet/{id}", timeout=30).json()

    def find_pets_by_status(self, status):
        return self.s.get(f"{self.base}/pet/findByStatus", params={"status": status}, timeout=30).json()

    def update_pet(self, body):
        return self.s.put(f"{self.base}/pet", json=body, timeout=30).json()

    def delete_pet(self, id):
        self.s.delete(f"{self.base}/pet/{id}", timeout=30).raise_for_status()
