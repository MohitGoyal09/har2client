# Swagger Petstore API (v3)

Write one Python file, `client.py`, that defines `class Client` for this API.
The file `session.har` is a browser recording of a user session. It holds real
requests and responses for this API, mixed with unrelated traffic.

## Constructor

`Client(base_url: str, username: str, password: str)`

- `base_url` is the API root and already includes the base path, with no trailing slash.
  In the recording it is `https://petstore3.swagger.io/api/v3`.
- This API uses an API key instead of a login: `password` is the API key and `username` is not used.

## Methods

Tests call these methods in order, all on one `Client` instance. Each argument is a keyword
argument. A method that returns a pet returns the pet object itself, not a wrapper around it.

- `create_pet(body: dict) -> dict`: Create a pet from `body` (a JSON object with the pet fields, including its `id`). Return the created pet.
- `get_pet(id: int) -> dict`: Return the pet with this id.
- `find_pets_by_status(status: str) -> list[dict]`: Return the pets whose status is `status`.
- `update_pet(body: dict) -> dict`: Replace the pet that has `body["id"]` with `body`. Return the updated pet.
- `delete_pet(id: int) -> None`: Delete the pet. Return `None`.

## Rules

- Use only `httpx`, `requests` and the standard library.
- Do not hard-code the host or any credential. Use `base_url`, `username` and `password`.
- Add no methods for endpoints that are not listed.
- Tests run against the live API with new data.
- `client.py` must import everything it uses (for example `from typing import Any` for the return annotations).
