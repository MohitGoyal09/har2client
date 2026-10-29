# Bookshelf API

Write one Python file, `client.py`, that defines `class Client` for this API.
The file `session.har` is a browser recording of a user session. It holds real
requests and responses for this API, mixed with unrelated traffic.

## Constructor

`Client(base_url: str, api_key: str)`

- `base_url` is the API root and already includes any base path, with no trailing slash.
  In the recording it looks like `https://api.bookshelf.example/v1`. Tests use another host and port.
- `api_key` is the credential as one string. (If the API uses HTTP basic auth, it is `"user:pass"`.)

## Methods

Implement exactly these methods. Each argument is a keyword argument; `body` is the JSON
request body as a `dict`. A method returns the parsed JSON response body, or `None` if the
response has no body.

- `list_books(self, limit: int | None = None, genre: str | None = None, tags: list | None = None) -> Any`: List books
- `create_book(self, body: dict) -> Any`: Create a book
- `get_book(self, book_id: str) -> Any`: Fetch one book
- `books_by_one_author(self, author_id: int, page: int, include_draft: bool | None = None) -> Any`: Books by one author
- `category_tree(self) -> Any`: Category tree
- `get_stats(self) -> Any`: Reading stats
- `health(self) -> Any`: Liveness probe

## Rules

- Use only `httpx`, `requests` and the standard library.
- Do not hard-code the host or any credential. Use `base_url` and `api_key`.
- Add no methods for endpoints that are not listed.
- Tests run against a fresh server with new data and a new key.
- `client.py` must import everything it uses (for example `from typing import Any` for the return annotations).
