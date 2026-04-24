"""Tests for the OpenTelemetry integration — fully mocked, no OTel SDK required."""
import json
import time
from unittest.mock import MagicMock, call, patch

import pytest

from because.buffer import Op, OpType
from because.enrichment import ContextChain, SwallowedExc
from because.integrations.otel import (
    _safe_add_event,
    record_spans,
    tag_current_span,
    tag_span,
)
from because.patterns.base import PatternMatch


# ── fixtures ──────────────────────────────────────────────────────────────────

def _make_exc():
    ops = [
        Op(OpType.DB_QUERY, timestamp=time.monotonic(), duration_ms=5.0,
           success=False, metadata={"statement": "SELECT 1", "error": "OperationalError"}),
        Op(OpType.HTTP_REQUEST, timestamp=time.monotonic(), duration_ms=120.0,
           success=True, metadata={"method": "GET", "url": "https://api.example.com/check"}),
    ]
    swallowed = [SwallowedExc("TimeoutError", "pool timeout", time.monotonic())]
    matches = [PatternMatch("pool_exhaustion", "likely_cause", "Pool exhausted.", ["QueuePool limit"])]
    exc = RuntimeError("connection refused")
    exc.__context_chain__ = ContextChain(operations=ops, swallowed=swallowed, pattern_matches=matches)
    return exc


def _mock_span():
    span = MagicMock()
    span.set_attribute = MagicMock()
    span.add_event = MagicMock()
    return span


# ── tag_span ──────────────────────────────────────────────────────────────────

def test_tag_span_sets_operation_count():
    exc = _make_exc()
    span = _mock_span()
    tag_span(span, exc)
    span.set_attribute.assert_any_call("because.operation_count", 2)


def test_tag_span_sets_swallowed_count():
    exc = _make_exc()
    span = _mock_span()
    tag_span(span, exc)
    span.set_attribute.assert_any_call("because.swallowed_count", 1)


def test_tag_span_sets_pattern_count():
    exc = _make_exc()
    span = _mock_span()
    tag_span(span, exc)
    span.set_attribute.assert_any_call("because.pattern_count", 1)


def test_tag_span_sets_pattern_attributes():
    exc = _make_exc()
    span = _mock_span()
    tag_span(span, exc)
    span.set_attribute.assert_any_call("because.pattern.0.name", "pool_exhaustion")
    span.set_attribute.assert_any_call("because.pattern.0.confidence", "likely_cause")


def test_tag_span_sets_context_json():
    exc = _make_exc()
    span = _mock_span()
    tag_span(span, exc)
    calls = {c.args[0]: c.args[1] for c in span.set_attribute.call_args_list}
    assert "because.context" in calls
    parsed = json.loads(calls["because.context"])
    assert "operations" in parsed
    assert "patterns" in parsed


def test_tag_span_adds_operation_events():
    exc = _make_exc()
    span = _mock_span()
    tag_span(span, exc)
    event_names = [c.args[0] for c in span.add_event.call_args_list]
    assert "because.db_query" in event_names
    assert "because.http_request" in event_names


def test_tag_span_adds_swallowed_event():
    exc = _make_exc()
    span = _mock_span()
    tag_span(span, exc)
    event_names = [c.args[0] for c in span.add_event.call_args_list]
    assert "because.swallowed_exception" in event_names


def test_tag_span_none_span_is_noop():
    exc = _make_exc()
    tag_span(None, exc)  # must not raise


def test_tag_span_no_chain_is_noop():
    exc = RuntimeError("bare")
    span = _mock_span()
    tag_span(span, exc)
    span.set_attribute.assert_not_called()


def test_tag_span_event_attributes_are_strings():
    exc = _make_exc()
    span = _mock_span()
    tag_span(span, exc)
    for c in span.add_event.call_args_list:
        attrs = c.kwargs.get("attributes") or (c.args[1] if len(c.args) > 1 else {})
        for v in attrs.values():
            assert isinstance(v, str), f"expected str, got {type(v)}: {v!r}"


# ── tag_current_span ──────────────────────────────────────────────────────────

def test_tag_current_span_uses_active_span():
    exc = _make_exc()
    mock_span = _mock_span()

    mock_trace = MagicMock()
    mock_trace.get_current_span.return_value = mock_span

    with patch.dict("sys.modules", {"opentelemetry": MagicMock(), "opentelemetry.trace": mock_trace}):
        with patch("because.integrations.otel.tag_span") as mock_tag:
            tag_current_span(exc)
            mock_tag.assert_called_once()


def test_tag_current_span_silent_if_otel_not_installed():
    exc = _make_exc()
    with patch.dict("sys.modules", {"opentelemetry": None}):
        tag_current_span(exc)  # must not raise


# ── record_spans ──────────────────────────────────────────────────────────────

def test_record_spans_creates_span_per_operation():
    exc = _make_exc()
    tracer = MagicMock()
    ctx_mgr = MagicMock()
    ctx_mgr.__enter__ = MagicMock(return_value=MagicMock())
    ctx_mgr.__exit__ = MagicMock(return_value=False)
    tracer.start_as_current_span.return_value = ctx_mgr

    record_spans(tracer, exc)

    names = [c.args[0] for c in tracer.start_as_current_span.call_args_list]
    assert "because.db_query" in names
    assert "because.http_request" in names
    assert "because.swallowed_exception" in names


def test_record_spans_no_chain_is_noop():
    exc = RuntimeError("bare")
    tracer = MagicMock()
    record_spans(tracer, exc)
    tracer.start_as_current_span.assert_not_called()


# ── _safe_add_event ───────────────────────────────────────────────────────────

def test_safe_add_event_swallows_errors():
    span = MagicMock()
    span.add_event.side_effect = RuntimeError("otel internal error")
    _safe_add_event(span, "test.event", {"key": "value"})  # must not raise
