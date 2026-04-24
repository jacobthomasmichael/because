"""
Sentry integration for ``because``.

Install as a ``before_send`` hook::

    import sentry_sdk
    from because.integrations.sentry import before_send

    sentry_sdk.init(dsn="...", before_send=before_send)

This attaches the because context chain to every Sentry event as:
- ``extra["because"]`` — full structured context (patterns, ops, swallowed)
- ``breadcrumbs`` — one breadcrumb per recent operation, ordered oldest-first
"""
from __future__ import annotations

from typing import Any

from because.integrations.serialize import chain_from_exc, chain_to_dict


def before_send(event: dict[str, Any], hint: dict[str, Any]) -> dict[str, Any]:
    """Sentry before_send hook. Pass directly to sentry_sdk.init()."""
    exc_info = hint.get("exc_info")
    if not exc_info:
        return event

    exc = exc_info[1]
    chain = chain_from_exc(exc)
    if chain is None:
        return event

    event.setdefault("extra", {})["because"] = chain_to_dict(chain)
    _attach_breadcrumbs(event, chain)
    return event


def _attach_breadcrumbs(event: dict[str, Any], chain: Any) -> None:
    crumbs = event.setdefault("breadcrumbs", {}).setdefault("values", [])

    for op in chain.operations[-50:]:
        crumb: dict[str, Any] = {
            "type": "query" if op.op_type.value == "db_query" else "http_request"
            if op.op_type.value == "http_request" else "default",
            "category": f"because.{op.op_type.value}",
            "level": "error" if not op.success else "info",
            "timestamp": op.timestamp,
            "data": {
                k: v
                for k, v in op.metadata.items()
                if isinstance(v, (str, int, float, bool, type(None)))
            },
        }
        if op.op_type.value == "db_query":
            crumb["message"] = op.metadata.get("statement", "")[:200]
        elif op.op_type.value == "http_request":
            crumb["message"] = (
                f"{op.metadata.get('method', '')} {op.metadata.get('url', '')}"
            )
        crumbs.append(crumb)

    for s in chain.swallowed:
        crumbs.append({
            "type": "default",
            "category": "because.swallowed",
            "level": "warning",
            "message": f"{s.exc_type}: {s.message}",
        })
