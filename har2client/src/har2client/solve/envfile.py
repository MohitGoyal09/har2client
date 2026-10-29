"""Read one key from a `.env` file into the process environment."""

from __future__ import annotations

from pathlib import Path
from typing import MutableMapping

API_KEY_ENV = "REFLECTION_API_KEY"


def load_env_key(env_path: Path, environ: MutableMapping[str, str], name: str = API_KEY_ENV) -> bool:
    """Put the key `name` (default REFLECTION_API_KEY) from a .env file into `environ` unless it is already set.
    Returns True when the key was read from the file. Never prints the value."""
    if environ.get(name):
        return False
    try:
        lines = Path(env_path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return False
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        key_name, sep, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if sep and key_name.strip() == name and value:
            environ[name] = value
            return True
    return False
