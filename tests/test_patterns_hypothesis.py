"""
Property-based tests for heuristic pattern matchers using Hypothesis.

These tests verify invariants that must hold for any valid input — not just
the hand-crafted cases in the unit tests. Key properties:
  - Patterns never crash on arbitrary input
  - Confidence values are always valid when a match is returned
  - Thresholds are respected
  - Empty / minimal input is handled gracefully
"""
import time
from typing import Optional

import pytest
from hypothesis import given, assume, settings, HealthCheck
from hypothesis import strategies as st

from because.buffer import Op, OpType
from because.enrichment import ContextChain, SwallowedExc
from because.patterns import pool_exhaustion, retry_storm, silent_failure
from because.patterns.base import PatternMatch


# ── strategies ────────────────────────────────────────────────────────────────

_VALID_CONFIDENCES = {"likely_cause", "contributing_factor"}

def _op(op_type: OpType, success: bool, metadata: dict) -> Op:
    return Op(op_type, timestamp=time.monotonic(), duration_ms=1.0,
              success=success, metadata=metadata)

def _db_op(success: bool = True) -> Op:
    return _op(OpType.DB_QUERY, success=success, metadata={"statement": "SELECT 1"})

def _http_op(url: str = "https://api.example.com/check", success: bool = True) -> Op:
    return _op(OpType.HTTP_REQUEST, success=success,
               metadata={"method": "GET", "url": url,
                         **({} if success else {"error": "ReadTimeout"})})

def _swallowed_op(exc_type: str = "TimeoutError", message: str = "timed out") -> Op:
    return _op(OpType.EXCEPTION_SWALLOWED, success=False,
               metadata={"exc_type": exc_type, "message": message})

# Strategies
exc_messages = st.text(min_size=0, max_size=200)
exc_types = st.sampled_from([
    "RuntimeError", "ValueError", "ConnectionError", "TimeoutError",
    "OperationalError", "AttributeError", "OSError", "Exception",
])
bool_lists = st.lists(st.booleans(), min_size=0, max_size=20)
url_hosts = st.sampled_from([
    "https://api.example.com",
    "https://fraud.internal",
    "https://other.service.io",
    "https://third.host.net",
])


# ── pool_exhaustion properties ─────────────────────────────────────────────────

@given(msg=exc_messages, successes=bool_lists)
@settings(max_examples=300, suppress_health_check=[HealthCheck.too_slow])
def test_pool_exhaustion_never_crashes(msg, successes):
    exc = RuntimeError(msg)
    ops = [_db_op(s) for s in successes]
    chain = ContextChain(operations=ops)
    result = pool_exhaustion.match(exc, chain)
    assert result is None or isinstance(result, PatternMatch)


@given(msg=exc_messages, successes=bool_lists)
@settings(max_examples=200)
def test_pool_exhaustion_confidence_is_valid(msg, successes):
    exc = RuntimeError(msg)
    ops = [_db_op(s) for s in successes]
    chain = ContextChain(operations=ops)
    result = pool_exhaustion.match(exc, chain)
    if result is not None:
        assert result.confidence in _VALID_CONFIDENCES


@given(successes=bool_lists)
@settings(max_examples=200)
def test_pool_exhaustion_definitive_message_always_fires(successes):
    """QueuePool limit in the message must always produce likely_cause."""
    exc = RuntimeError("QueuePool limit of size 5 overflow 10 reached")
    ops = [_db_op(s) for s in successes]
    chain = ContextChain(operations=ops)
    result = pool_exhaustion.match(exc, chain)
    assert result is not None
    assert result.confidence == "likely_cause"
    assert result.name == "pool_exhaustion"


@given(msg=exc_messages)
@settings(max_examples=200)
def test_pool_exhaustion_no_match_without_connection_signal(msg):
    """Messages with no pool/connection keywords and no connection-type exc → no match."""
    assume(not any(kw in msg.lower() for kw in [
        "connection", "pool", "connect", "queuepool", "overflow",
        "remaining connection", "too many"
    ]))
    exc = RuntimeError(msg)  # RuntimeError has no connection signal in its type
    chain = ContextChain(operations=[_db_op(True)] * 5)
    result = pool_exhaustion.match(exc, chain)
    assert result is None


@given(successes=bool_lists)
@settings(max_examples=200)
def test_pool_exhaustion_evidence_is_non_empty_on_match(successes):
    exc = RuntimeError("QueuePool limit of size 5 reached")
    ops = [_db_op(s) for s in successes]
    chain = ContextChain(operations=ops)
    result = pool_exhaustion.match(exc, chain)
    if result is not None:
        assert len(result.evidence) > 0


# ── silent_failure properties ─────────────────────────────────────────────────

@given(msg=exc_messages, n_swallowed=st.integers(min_value=0, max_value=10))
@settings(max_examples=300, suppress_health_check=[HealthCheck.too_slow])
def test_silent_failure_never_crashes(msg, n_swallowed):
    exc = RuntimeError(msg)
    ops = [_swallowed_op() for _ in range(n_swallowed)]
    chain = ContextChain(operations=ops)
    result = silent_failure.match(exc, chain)
    assert result is None or isinstance(result, PatternMatch)


@given(n_swallowed=st.integers(min_value=1, max_value=10))
@settings(max_examples=200)
def test_silent_failure_always_fires_with_swallowed_ops(n_swallowed):
    """Any swallowed op in context must produce a match."""
    exc = RuntimeError("downstream crash")
    ops = [_swallowed_op("TimeoutError", "timed out") for _ in range(n_swallowed)]
    chain = ContextChain(operations=ops)
    result = silent_failure.match(exc, chain)
    assert result is not None
    assert result.name == "silent_failure"


@given(msg=exc_messages)
@settings(max_examples=200)
def test_silent_failure_no_match_without_swallowed(msg):
    """No swallowed ops or explicit swallowed → no match."""
    exc = RuntimeError(msg)
    ops = [_db_op(True), _http_op()]
    chain = ContextChain(operations=ops, swallowed=[])
    result = silent_failure.match(exc, chain)
    assert result is None


@given(n_swallowed=st.integers(min_value=1, max_value=10))
@settings(max_examples=200)
def test_silent_failure_confidence_is_valid(n_swallowed):
    exc = RuntimeError("something broke")
    swallowed = [SwallowedExc("TimeoutError", "pool timeout", time.monotonic())
                 for _ in range(n_swallowed)]
    chain = ContextChain(operations=[], swallowed=swallowed)
    result = silent_failure.match(exc, chain)
    if result is not None:
        assert result.confidence in _VALID_CONFIDENCES


@given(n_swallowed=st.integers(min_value=1, max_value=5))
@settings(max_examples=200)
def test_silent_failure_upstream_type_yields_likely_cause(n_swallowed):
    """Known upstream exception types must produce likely_cause."""
    exc = RuntimeError("downstream crash")
    swallowed = [SwallowedExc("TimeoutError", "timed out", time.monotonic())
                 for _ in range(n_swallowed)]
    chain = ContextChain(operations=[], swallowed=swallowed)
    result = silent_failure.match(exc, chain)
    assert result is not None
    assert result.confidence == "likely_cause"


# ── retry_storm properties ────────────────────────────────────────────────────

@given(
    msg=exc_messages,
    successes=bool_lists,
    host=url_hosts,
)
@settings(max_examples=300, suppress_health_check=[HealthCheck.too_slow])
def test_retry_storm_never_crashes(msg, successes, host):
    exc = RuntimeError(msg)
    ops = [_http_op(url=f"{host}/check", success=s) for s in successes]
    chain = ContextChain(operations=ops)
    result = retry_storm.match(exc, chain)
    assert result is None or isinstance(result, PatternMatch)


@given(
    n_fail=st.integers(min_value=4, max_value=15),
    n_succeed=st.integers(min_value=0, max_value=3),
)
@settings(max_examples=200)
def test_retry_storm_fires_on_timeout_with_failures(n_fail, n_succeed):
    """Timeout + enough failures → always fires."""
    exc = TimeoutError("upstream timed out")
    ops = [_http_op(success=False)] * n_fail + [_http_op(success=True)] * n_succeed
    chain = ContextChain(operations=ops)
    result = retry_storm.match(exc, chain)
    assert result is not None
    assert result.name == "retry_storm"


@given(msg=exc_messages)
@settings(max_examples=200)
def test_retry_storm_no_match_without_timeout(msg):
    """Non-timeout exceptions never match regardless of HTTP ops."""
    assume(not any(kw in msg.lower() for kw in [
        "timeout", "timed out", "deadline", "read timeout", "connect timeout"
    ]))
    exc = ValueError(msg)
    ops = [_http_op(success=False)] * 10
    chain = ContextChain(operations=ops)
    result = retry_storm.match(exc, chain)
    assert result is None


@given(n_ops=st.integers(min_value=0, max_value=3))
@settings(max_examples=100)
def test_retry_storm_no_match_too_few_ops(n_ops):
    """Fewer than MIN_HTTP_OPS → never matches."""
    exc = TimeoutError("timed out")
    ops = [_http_op(success=False)] * n_ops
    chain = ContextChain(operations=ops)
    result = retry_storm.match(exc, chain)
    assert result is None


@given(
    n_fail=st.integers(min_value=4, max_value=15),
    n_succeed=st.integers(min_value=0, max_value=3),
)
@settings(max_examples=200)
def test_retry_storm_confidence_is_valid(n_fail, n_succeed):
    exc = TimeoutError("timed out")
    ops = [_http_op(success=False)] * n_fail + [_http_op(success=True)] * n_succeed
    chain = ContextChain(operations=ops)
    result = retry_storm.match(exc, chain)
    if result is not None:
        assert result.confidence in _VALID_CONFIDENCES


# ── cross-pattern: match_all never crashes ────────────────────────────────────

@given(
    msg=exc_messages,
    exc_type=exc_types,
    n_db=st.integers(min_value=0, max_value=10),
    n_http=st.integers(min_value=0, max_value=10),
    n_swallowed=st.integers(min_value=0, max_value=5),
)
@settings(max_examples=300, suppress_health_check=[HealthCheck.too_slow])
def test_match_all_never_crashes(msg, exc_type, n_db, n_http, n_swallowed):
    from because.patterns import match_all

    DynamicExc = type(exc_type, (Exception,), {})
    exc = DynamicExc(msg)

    ops = (
        [_db_op(True)] * n_db
        + [_http_op(success=False)] * n_http
        + [_swallowed_op() for _ in range(n_swallowed)]
    )
    swallowed = [SwallowedExc("TimeoutError", "timeout", time.monotonic())
                 for _ in range(n_swallowed)]
    chain = ContextChain(operations=ops, swallowed=swallowed)

    results = match_all(exc, chain)
    assert isinstance(results, list)
    for r in results:
        assert isinstance(r, PatternMatch)
        assert r.confidence in _VALID_CONFIDENCES
