"""
Datadog integration for ``because``.

Two usage patterns:

1. Attach to the active span manually::

    from because.integrations.datadog import tag_span
    from ddtrace import tracer

    try:
        risky_operation()
    except Exception as exc:
        because.enrich_with_swallowed(exc)
        tag_span(tracer.current_span(), exc)
        raise

2. Use as a ddtrace pin hook (attach to a service)::

    from because.integrations.datadog import on_start
    from ddtrace import Pin

    Pin.override(my_client, hooks={"on_start": on_start})
"""
from __future__ import annotations

from typing import Any

from because.integrations.serialize import chain_from_exc, chain_to_dict


def tag_span(span: Any, exc: BaseException) -> None:
    """Attach because context from exc.__context_chain__ to a ddtrace Span."""
    if span is None:
        return
    chain = chain_from_exc(exc)
    if chain is None:
        return

    data = chain_to_dict(chain)

    span.set_tag("because.operation_count", len(data["operations"]))
    span.set_tag("because.swallowed_count", len(data["swallowed"]))

    for i, match in enumerate(data["patterns"]):
        span.set_tag(f"because.pattern.{i}.name", match["name"])
        span.set_tag(f"because.pattern.{i}.confidence", match["confidence"])
        span.set_tag(f"because.pattern.{i}.description", match["description"])

    # Most recent 5 ops as individual tags for quick scanning in APM
    for i, op in enumerate(data["operations"][-5:]):
        prefix = f"because.op.{i}"
        span.set_tag(f"{prefix}.type", op["op_type"])
        span.set_tag(f"{prefix}.success", op["success"])
        if op.get("duration_ms") is not None:
            span.set_tag(f"{prefix}.duration_ms", op["duration_ms"])

    # Full payload as a single JSON tag for search/filtering
    import json
    try:
        span.set_tag("because.context", json.dumps(data, default=str))
    except Exception:
        pass


def tag_current_span(exc: BaseException) -> None:
    """Convenience: tag the active ddtrace span with because context."""
    try:
        from ddtrace import tracer
        tag_span(tracer.current_span(), exc)
    except ImportError:
        pass
