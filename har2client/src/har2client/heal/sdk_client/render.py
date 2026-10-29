"""Entry point of the SDK-style renderer: assembles the layers into one `client.py` source."""

from __future__ import annotations

from har2client.heal.sdk_client.auth import auth_source
from har2client.heal.sdk_client.models import EXCEPTIONS, HEAD, models_source
from har2client.heal.sdk_client.plan import JOB_POLL_DELAY_S, JOB_POLLS, RATE_LIMIT_RETRIES, Plan, check_supported, fill
from har2client.heal.sdk_client.resource import client_source, paginators_source, resource_source
from har2client.heal.sdk_client.transport import RETRY, transport_source

def _imports(plan: Plan) -> str:
    lines = ["import logging", "import re", "import time"]
    if plan.idempotency:
        lines.append("import uuid")
    lines.append("from dataclasses import dataclass, field, fields")
    if "epoch" in plan.drift:
        lines.append("from datetime import datetime, timezone")
    lines += ["from typing import Any, Callable, Iterator, TypeVar", "from urllib.parse import quote" + (", urljoin" if plan.poll else "")]
    return "\n".join(lines)


def _protocol_constants(spec: dict, plan: Plan) -> str:
    lines = []
    if plan.rate_limit:
        lines.append(f"RATE_LIMIT_RETRIES = {RATE_LIMIT_RETRIES}  # tries of one request while the API answers 429 with Retry-After")
    if plan.poll:
        lines += [f"JOB_POLLS = {JOB_POLLS}  # looks at a job before giving up", f"JOB_POLL_DELAY = {JOB_POLL_DELAY_S}  # seconds between two looks"]
    if plan.moved:
        lines.append(f"ITEM_PREFIX = {spec['moved']['prefix']!r}  # item paths (get, update, delete) live below this prefix")
    if plan.idempotency:
        lines.append(f"IDEMPOTENCY_HEADER = {spec['idempotency']['header']!r}  # every write needs a unique value here")
    return "\n".join(lines)


def generate_sdk_client_source(spec: dict, flaws: tuple[str, ...] | set[str] = ()) -> str:
    """Source of an SDK-style `client.py` for `spec`; behaves like `tierb.refclient.generate_client_source(spec, flaws)`.

    `flaws` are the names of `tierb.refclient.FLAWS`; each switches off one behaviour. Raises NotImplementedError
    with the switch name for a flaw, config switch or level that is not rendered.
    """
    flaws = set(flaws)
    check_supported(spec, flaws)
    plan = Plan(spec, flaws)
    path = "/" + spec["path_word"].strip("/")
    uses_items_key = spec["config"]["pagination"] in ("offset", "cursor") and spec["config"]["envelope"] == "off"
    items_key = f"ITEMS_KEY = {spec['items_key']!r}  # key of the item list in a paged response\n" if uses_items_key else ""
    head = fill(
        HEAD, title=spec["title"], resource_path=repr(path), filter_field=repr(spec["filter"]), items_key=items_key,
        logger=spec["domain"] + "_sdk", imports=_imports(plan), protocol_constants=_protocol_constants(spec, plan),
    )
    parts = [
        head, EXCEPTIONS, models_source(spec, plan), RETRY, auth_source(spec, flaws, plan), transport_source(spec, flaws, plan),
        paginators_source(spec, flaws), resource_source(spec, flaws, plan), client_source(spec, plan),
    ]
    return "".join(parts).rstrip() + "\n"
