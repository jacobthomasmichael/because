"""
OpenTelemetry integration for ``because``.

Attaches because context to OTel spans as span events and attributes.
Requires ``opentelemetry-api`` — does NOT require the SDK (users provide
their own tracer/exporter setup).

Usage patterns::

    # 1. Tag the current active span
    from because.integrations.otel import tag_current_span

    try:
        risky_operation()
    except Exception as exc:
        because.enrich_with_swallowed(exc)
        tag_current_span(exc)
        raise

    # 2. Tag a specific span
    from because.integrations.otel import tag_span
    from opentelemetry import trace

    span = trace.get_current_span()
    tag_span(span, exc)

    # 3. Emit each operation as a child span (detailed trace)
    from because.integrations.otel import record_spans
    from opentelemetry import trace

    tracer = trace.get_tracer("because")
    record_spans(tracer, exc)

Install the extra::

    pip install "because-py[otel]"
"""
from __future__ import annotations

import json
from typing import Any

from because.integrations.serialize import chain_from_exc, chain_to_dict


def tag_span(span: Any, exc: BaseException) -> None:
    """Attach because context from exc.__context_chain__ to an OTel Span.

    Adds:
    - ``because.*`` attributes summarising pattern matches and op counts
    - One span event per operation in the context window
    - One span event per swallowed exception
    """
    if span is None:
        return

    chain = chain_from_exc(exc)
    if chain is None:
        return

    data = chain_to_dict(chain)

    # Summary attributes — cheap to index, useful for filtering in APM
    span.set_attribute("because.operation_count", len(data["operations"]))
    span.set_attribute("because.swallowed_count", len(data["swallowed"]))
    span.set_attribute("because.pattern_count", len(data["patterns"]))

    for i, match in enumerate(data["patterns"]):
        span.set_attribute(f"because.pattern.{i}.name", match["name"])
        span.set_attribute(f"because.pattern.{i}.confidence", match["confidence"])
        span.set_attribute(f"because.pattern.{i}.description", match["description"])

    # Full payload as a single JSON attribute for search/export
    try:
        span.set_attribute("because.context", json.dumps(data, default=str))
    except Exception:
        pass

    # Span events — one per operation, one per swallowed exception
    for op in data["operations"]:
        _safe_add_event(span, f"because.{op['op_type']}", {
            k: str(v) for k, v in op.items()
            if k != "op_type" and v is not None
        })

    for s in data["swallowed"]:
        _safe_add_event(span, "because.swallowed_exception", {
            "exc_type": s.get("exc_type", ""),
            "message": s.get("message", "")[:200],
        })


def tag_current_span(exc: BaseException) -> None:
    """Convenience: tag the active OTel span with because context."""
    try:
        from opentelemetry import trace
        span = trace.get_current_span()
        tag_span(span, exc)
    except ImportError:
        pass


def record_spans(tracer: Any, exc: BaseException) -> None:
    """Emit each because operation as a child span under the current span.

    Use this for detailed tracing — each DB query, HTTP request, and
    swallowed exception becomes a named child span with full metadata.
    """
    chain = chain_from_exc(exc)
    if chain is None:
        return

    data = chain_to_dict(chain)

    for op in data["operations"]:
        op_name = f"because.{op['op_type']}"
        attrs = {k: str(v) for k, v in op.items() if k != "op_type" and v is not None}
        with tracer.start_as_current_span(op_name, attributes=attrs):
            pass  # span closes immediately — we're recording history, not wrapping live calls

    for s in data["swallowed"]:
        attrs = {
            "exc_type": s.get("exc_type", ""),
            "message": s.get("message", "")[:200],
        }
        with tracer.start_as_current_span("because.swallowed_exception", attributes=attrs):
            pass


def _safe_add_event(span: Any, name: str, attributes: dict[str, Any]) -> None:
    try:
        span.add_event(name, attributes=attributes)
    except Exception:
        pass
