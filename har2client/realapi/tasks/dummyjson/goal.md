# DummyJSON Store API

Write one Python file, `client.py`, that defines `class Client` for this API.
The file `session.har` is a browser recording of a user session. It holds real
requests and responses for this API, mixed with unrelated traffic.

## Constructor

`Client(base_url: str, username: str, password: str)`

- `base_url` is the API root, with no trailing slash. In the recording it is `https://dummyjson.com`.
- `username` and `password` are the account credentials.

## Methods

Tests call these methods in order, all on one `Client` instance, so anything the client
keeps (such as a login session) carries over from call to call. Each argument is a keyword
argument. A method that returns a product returns the product object itself, not a wrapper around it.

- `get_me() -> dict`: Return the logged-in user.
- `list_products(category: str | None = None) -> list[dict]`: Return every product, across all pages, in the order the server gives them. When `category` is given, return every product in that category.
- `search_products(query: str) -> list[dict]`: Return every product that matches the search text, across all pages.
- `get_product(id: int) -> dict`: Return the product with this id.
- `add_product(body: dict) -> dict`: Add a product from `body`. Return the product the server returns.

## Rules

- Use only `httpx`, `requests` and the standard library.
- Do not hard-code the host or any credential. Use `base_url`, `username` and `password`.
- Add no methods for endpoints that are not listed.
- Tests run against the live API with new data.
- `client.py` must import everything it uses (for example `from typing import Any` for the return annotations).
