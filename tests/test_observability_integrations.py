"""Tests for Sentry, Datadog, and logging integrations."""
import json
import logging
from unittest.mock import MagicMock

import pytest

from because.buffer import OpType
from because.enrichment import ContextChain, SwallowedExc, enrich
from because.integrations.serialize import chain_to_dict
from because.integrations.sentry import before_send, _attach_breadcrumbs
from because.integrations.datadog import tag_span
from because.integrations.logging import BecauseFilter, BecauseFormatter


# ── shared fixture ────────────────────────────────────────────────────────────

def _make_exc_with_chain(patterns=True):
    """Build a RuntimeError with a pre-populated __context_chain__."""
    from because.buffer import Op
    import time

    ops = [
        Op(OpType.DB_QUERY, timestamp=time.monotonic(), duration_ms=5.0,
           success=True, metadata={"statement": "SELECT 1"}),
        Op(OpType.HTTP_REQUEST, timestamp=time.monotonic(), duration_ms=120.0,
           success=False, metadata={"method": "GET", "url": "http://api/x",
                                    "error": "ConnectionError"}),
    ]
    swallowed = [SwallowedExc("TimeoutError", "pool timeout", time.monotonic())]

    from because.patterns.base import PatternMatch
    matches = [PatternMatch(
        name="silent_failure",
        confidence="likely_cause",
        description="Prior exception was swallowed.",
        evidence=["Caught TimeoutError"],
    )]

    exc = RuntimeError("downstream crash")
    exc.__context_chain__ = ContextChain(  # type: ignore[attr-defined]
        operations=ops, swallowed=swallowed, pattern_matches=matches
    )
    return exc


# ── serialize ─────────────────────────────────────────────────────────────────

def test_chain_to_dict_structure():
    exc = _make_exc_with_chain()
    d = chain_to_dict(exc.__context_chain__)
    assert "patterns" in d
    assert "swallowed" in d
    assert "operations" in d
    assert d["patterns"][0]["name"] == "silent_failure"
    assert d["swallowed"][0]["exc_type"] == "TimeoutError"
    assert len(d["operations"]) == 2


def test_chain_to_dict_is_json_serializable():
    exc = _make_exc_with_chain()
    d = chain_to_dict(exc.__context_chain__)
    json.dumps(d)  # must not raise


# ── sentry ────────────────────────────────────────────────────────────────────

def test_sentry_before_send_attaches_extra():
    exc = _make_exc_with_chain()
    event = {}
    hint = {"exc_info": (RuntimeError, exc, None)}
    result = before_send(event, hint)
    assert "because" in result["extra"]
    assert result["extra"]["because"]["patterns"][0]["name"] == "silent_failure"


def test_sentry_before_send_adds_breadcrumbs():
    exc = _make_exc_with_chain()
    event = {}
    hint = {"exc_info": (RuntimeError, exc, None)}
    result = before_send(event, hint)
    crumbs = result["breadcrumbs"]["values"]
    assert any(c["category"] == "because.db_query" for c in crumbs)
    assert any(c["category"] == "because.http_request" for c in crumbs)
    assert any(c["category"] == "because.swallowed" for c in crumbs)


def test_sentry_before_send_no_chain_is_noop():
    exc = RuntimeError("no chain")
    event = {"extra": {"existing": "data"}}
    hint = {"exc_info": (RuntimeError, exc, None)}
    result = before_send(event, hint)
    assert "because" not in result.get("extra", {})


def test_sentry_before_send_no_exc_info_is_noop():
    event = {}
    result = before_send(event, {})
    assert result == {}


def test_sentry_failed_op_breadcrumb_is_error_level():
    exc = _make_exc_with_chain()
    event = {}
    _attach_breadcrumbs(event, exc.__context_chain__)
    http_crumbs = [c for c in event["breadcrumbs"]["values"]
                   if c["category"] == "because.http_request"]
    assert http_crumbs[0]["level"] == "error"


# ── datadog ───────────────────────────────────────────────────────────────────

def test_datadog_tag_span_sets_tags():
    exc = _make_exc_with_chain()
    span = MagicMock()
    tag_span(span, exc)
    calls = {call.args[0] for call in span.set_tag.call_args_list}
    assert "because.operation_count" in calls
    assert "because.swallowed_count" in calls
    assert "because.pattern.0.name" in calls
    assert "because.context" in calls


def test_datadog_tag_span_none_is_noop():
    tag_span(None, RuntimeError("x"))  # must not raise


def test_datadog_tag_span_no_chain_is_noop():
    span = MagicMock()
    tag_span(span, RuntimeError("no chain"))
    span.set_tag.assert_not_called()


def test_datadog_context_tag_is_valid_json():
    exc = _make_exc_with_chain()
    span = MagicMock()
    tag_span(span, exc)
    context_calls = [c for c in span.set_tag.call_args_list
                     if c.args[0] == "because.context"]
    assert context_calls
    json.loads(context_calls[0].args[1])  # must not raise


# ── logging ───────────────────────────────────────────────────────────────────

def test_logging_filter_attaches_because_on_exc_info():
    exc = _make_exc_with_chain()
    record = logging.LogRecord(
        name="test", level=logging.ERROR, pathname="", lineno=0,
        msg="boom", args=(), exc_info=(RuntimeError, exc, None),
    )
    f = BecauseFilter()
    f.filter(record)
    assert hasattr(record, "because")
    assert record.because["patterns"][0]["name"] == "silent_failure"


def test_logging_filter_no_chain_no_attribute():
    exc = RuntimeError("no chain")
    record = logging.LogRecord(
        name="test", level=logging.ERROR, pathname="", lineno=0,
        msg="boom", args=(), exc_info=(RuntimeError, exc, None),
    )
    f = BecauseFilter()
    f.filter(record)
    assert not hasattr(record, "because")


def test_logging_formatter_emits_json_with_because():
    exc = _make_exc_with_chain()
    record = logging.LogRecord(
        name="test", level=logging.ERROR, pathname="", lineno=0,
        msg="something broke", args=(), exc_info=(RuntimeError, exc, None),
    )
    fmt = BecauseFormatter()
    output = fmt.format(record)
    data = json.loads(output)
    assert data["message"] == "something broke"
    assert data["exc_type"] == "RuntimeError"
    assert "because" in data
    assert data["because"]["patterns"][0]["confidence"] == "likely_cause"


def test_logging_formatter_no_chain_omits_because_key():
    exc = RuntimeError("no chain")
    record = logging.LogRecord(
        name="test", level=logging.ERROR, pathname="", lineno=0,
        msg="err", args=(), exc_info=(RuntimeError, exc, None),
    )
    fmt = BecauseFormatter()
    data = json.loads(fmt.format(record))
    assert "because" not in data
    assert data["exc_type"] == "RuntimeError"
