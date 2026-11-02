import httpx


class Client:
    def __init__(self, base_url: str, username: str, password: str):
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self._headers = {"api_key": password, "Accept": "application/json"}

    def create_pet(self, body: dict) -> dict:
        r = httpx.post(
            f"{self.base_url}/pet",
            json=body,
            headers=self._headers,
        )
        r.raise_for_status()
        return r.json()

    def get_pet(self, id: int) -> dict:
        r = httpx.get(
            f"{self.base_url}/pet/{id}",
            headers=self._headers,
        )
        r.raise_for_status()
        return r.json()

    def find_pets_by_status(self, status: str) -> list[dict]:
        r = httpx.get(
            f"{self.base_url}/pet/findByStatus",
            params={"status": status},
            headers=self._headers,
        )
        r.raise_for_status()
        return r.json()

    def update_pet(self, body: dict) -> dict:
        r = httpx.put(
            f"{self.base_url}/pet",
            json=body,
            headers=self._headers,
        )
        r.raise_for_status()
        return r.json()

    def delete_pet(self, id: int) -> None:
        r = httpx.delete(
            f"{self.base_url}/pet/{id}",
            headers=self._headers,
        )
        r.raise_for_status()
        return None
