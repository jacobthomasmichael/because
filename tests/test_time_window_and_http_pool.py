"""Tests for within_seconds filtering and HTTP pool exhaustion pattern."""
import time
import pytest

from because.buffer import Op, OpType
from because.enrichment import ContextChain, format_context_chain
from because.patterns import pool_exhaustion
from because.patterns.base import PatternMatch


# ── within_seconds filtering ──────────────────────────────────────────────────

def _make_exc_with_ops(ops):
    exc = RuntimeError("QueuePool limit reached")
    exc.__context_chain__ = ContextChain(operations=ops)
    return exc


def test_within_seconds_shows_recent_ops():
    now = time.monotonic()
    ops = [
        Op(OpType.DB_QUERY, timestamp=now - 60, duration_ms=1.0, success=True,
           metadata={"statement": "OLD"}),
        Op(OpType.DB_QUERY, timestamp=now - 1, duration_ms=1.0, success=True,
           metadata={"statement": "RECENT"}),
    ]
    exc = _make_exc_with_ops(ops)
    output = format_context_chain(exc, within_seconds=30)
    assert "RECENT" in output
    assert "OLD" not in output


def test_within_seconds_excludes_old_ops():
    now = time.monotonic()
    ops = [
        Op(OpType.DB_QUERY, timestamp=now - 120, duration_ms=1.0, success=True,
           metadata={"statement": "OLD1"}),
        Op(OpType.DB_QUERY, timestamp=now - 90, duration_ms=1.0, success=True,
           metadata={"statement": "OLD2"}),
    ]
    exc = _make_exc_with_ops(ops)
    output = format_context_chain(exc, within_seconds=30)
    assert "No recent operations recorded" in output


def test_within_seconds_none_shows_all_ops():
    now = time.monotonic()
    ops = [
        Op(OpType.DB_QUERY, timestamp=now - 120, duration_ms=1.0, success=True,
           metadata={"statement": "OLD"}),
        Op(OpType.DB_QUERY, timestamp=now - 1, duration_ms=1.0, success=True,
           metadata={"statement": "RECENT"}),
    ]
    exc = _make_exc_with_ops(ops)
    output = format_context_chain(exc, within_seconds=None)
    assert "OLD" in output
    assert "RECENT" in output


def test_within_seconds_label_shown_in_output():
    now = time.monotonic()
    ops = [Op(OpType.DB_QUERY, timestamp=now - 1, duration_ms=1.0, success=True,
              metadata={"statement": "SELECT 1"})]
    exc = _make_exc_with_ops(ops)
    output = format_context_chain(exc, within_seconds=30)
    assert "30s" in output


def test_within_seconds_zero_shows_no_ops():
    now = time.monotonic()
    ops = [Op(OpType.DB_QUERY, timestamp=now - 1, duration_ms=1.0, success=True,
              metadata={"statement": "SELECT 1"})]
    exc = _make_exc_with_ops(ops)
    output = format_context_chain(exc, within_seconds=0)
    assert "No recent operations recorded" in output


# ── HTTP pool exhaustion pattern ──────────────────────────────────────────────

def _http_op(success=True):
    return Op(OpType.HTTP_REQUEST, timestamp=time.monotonic(), duration_ms=200.0,
              success=success, metadata={"method": "GET", "url": "https://api.example.com",
                                         **({} if success else {"error": "ConnectionError"})})


def _make_chain(ops):
    return ContextChain(operations=ops)


def test_pool_exhaustion_fires_on_http_pool_message():
    exc = RuntimeError("urllib3 connection pool is full, discarding connection")
    chain = _make_chain([_http_op(False)] * 4)
    result = pool_exhaustion.match(exc, chain)
    assert result is not None
    assert result.name == "pool_exhaustion"


def test_pool_exhaustion_http_definitive_message_is_likely_cause():
    exc = RuntimeError("ConnectionPool is full, discarding connection: api.example.com")
    chain = _make_chain([_http_op(False)] * 3)
    result = pool_exhaustion.match(exc, chain)
    assert result is not None
    assert result.confidence == "likely_cause"


def test_pool_exhaustion_http_description_mentions_http():
    exc = RuntimeError("urllib3 connection pool is full")
    chain = _make_chain([_http_op(False)] * 4)
    result = pool_exhaustion.match(exc, chain)
    assert result is not None
    assert "HTTP" in result.description or "http" in result.description.lower()


def test_pool_exhaustion_http_evidence_includes_http_ops():
    exc = RuntimeError("urllib3 connection pool is full")
    chain = _make_chain([_http_op(False)] * 5)
    result = pool_exhaustion.match(exc, chain)
    assert result is not None
    assert any("HTTP" in e or "http" in e.lower() for e in result.evidence)


def test_pool_exhaustion_max_retries_exceeded_fires():
    exc = RuntimeError("Max retries exceeded with url: /api/endpoint")
    chain = _make_chain([_http_op(False)] * 4)
    result = pool_exhaustion.match(exc, chain)
    assert result is not None


def test_pool_exhaustion_db_description_unchanged_when_only_db_ops():
    exc = RuntimeError("QueuePool limit of size 5 reached")
    chain = _make_chain([
        Op(OpType.DB_QUERY, timestamp=time.monotonic(), duration_ms=1.0,
           success=False, metadata={"statement": "SELECT 1"})
    ] * 4)
    result = pool_exhaustion.match(exc, chain)
    assert result is not None
    assert "database" in result.description.lower()
