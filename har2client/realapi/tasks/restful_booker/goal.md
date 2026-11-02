# Restful-Booker API

Write one Python file, `client.py`, that defines `class Client` for this API.
The file `session.har` is a browser recording of a user session. It holds real
requests and responses for this API, mixed with unrelated traffic.

## Constructor

`Client(base_url: str, username: str, password: str)`

- `base_url` is the API root, with no trailing slash. In the recording it is `https://restful-booker.herokuapp.com`.
- `username` and `password` are the account credentials.

## Methods

Tests call these methods in order, all on one `Client` instance, so anything the client
keeps (such as a login session) carries over from call to call. Each argument is a keyword
argument.

- `list_bookings(firstname: str | None = None, lastname: str | None = None) -> list[int]`: Return the ids of the bookings. When `firstname` or `lastname` is given, return only the matching bookings.
- `get_booking(id: int) -> dict`: Return the booking with this id (its fields, without the id).
- `create_booking(body: dict) -> int`: Create a booking from `body` (a JSON object with the booking fields). Return the new booking's id.
- `update_booking(id: int, body: dict) -> dict`: Replace the booking with `body` (all fields). Return the updated booking.
- `delete_booking(id: int) -> None`: Delete the booking. Return `None`.

## Rules

- Use only `httpx`, `requests` and the standard library.
- Do not hard-code the host or any credential. Use `base_url`, `username` and `password`.
- Add no methods for endpoints that are not listed.
- Tests run against the live API with new data.
- `client.py` must import everything it uses (for example `from typing import Any` for the return annotations).
