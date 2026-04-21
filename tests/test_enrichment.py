import sys
from io import StringIO
from unittest.mock import patch

import pytest

import because
from because.buffer import OpType, get_context, record
from because.enrichment import (
    ContextChain,
    _because_excepthook,
    catch,
    enrich,
    enrich_with_swallowed,
    format_context_chain,
)


# --- enrich() ---

def test_enrich_attaches_context_chain():
    exc = ValueError("boom")
    enrich(exc)
    assert hasattr(exc, "__context_chain__")
    assert isinstance(exc.__context_chain__, ContextChain)


def test_enrich_snapshots_current_ops():
    record(OpType.DB_QUERY, duration_ms=5.0, success=True, statement="SELECT 1")
    exc = ValueError("boom")
    enrich(exc)
    chain: ContextChain = exc.__context_chain__
    assert any(op.op_type == OpType.DB_QUERY for op in chain.operations)


def test_enrich_is_idempotent():
    exc = ValueError("boom")
    enrich(exc)
    original_chain = exc.__context_chain__
    record(OpType.HTTP_REQUEST, duration_ms=10.0, success=True)
    enrich(exc)  # second call — should not overwrite
    assert exc.__context_chain__ is original_chain


# --- format_context_chain() ---

def test_format_includes_operations():
    record(OpType.DB_QUERY, duration_ms=3.5, success=True, statement="SELECT * FROM users")
    exc = ValueError("db error")
    enrich(exc)
    output = format_context_chain(exc)
    assert "db_query" in output
    assert "SELECT * FROM users" in output


def test_format_shows_failed_ops():
    record(OpType.HTTP_REQUEST, duration_ms=100.0, success=False, method="GET",
           url="http://example.com/api", error="ConnectionError")
    exc = RuntimeError("network failure")
    enrich(exc)
    output = format_context_chain(exc)
    assert "FAIL" in output
    assert "ConnectionError" in output


def test_format_returns_empty_string_without_chain():
    exc = ValueError("no chain")
    assert format_context_chain(exc) == ""


def test_format_shows_no_ops_message():
    # use a fresh thread so buffer is empty
    import threading
    result = {}

    def worker():
        exc = ValueError("empty context")
        enrich(exc)
        result["output"] = format_context_chain(exc)

    t = threading.Thread(target=worker)
    t.start()
    t.join()
    assert "No recent operations recorded" in result["output"]


# --- catch() context manager ---

def test_catch_records_swallowed_exception():
    with catch(ValueError):
        raise ValueError("silent failure")

    ops = [op for op in get_context().snapshot() if op.op_type == OpType.EXCEPTION_SWALLOWED]
    assert ops
    last = ops[-1]
    assert last.metadata["exc_type"] == "ValueError"
    assert "silent failure" in last.metadata["message"]


def test_catch_does_not_suppress_unmatched_exception():
    with pytest.raises(RuntimeError):
        with catch(ValueError):
            raise RuntimeError("not caught")


def test_catch_default_catches_any_exception():
    with catch():
        raise KeyError("swallowed")

    ops = [op for op in get_context().snapshot() if op.op_type == OpType.EXCEPTION_SWALLOWED]
    assert any(op.metadata["exc_type"] == "KeyError" for op in ops)


# --- enrich_with_swallowed() ---

def test_enrich_with_swallowed_includes_prior_catches():
    with catch(TimeoutError):
        raise TimeoutError("pool timeout")

    exc = RuntimeError("follow-on failure")
    enrich_with_swallowed(exc)
    chain: ContextChain = exc.__context_chain__
    assert any(s.exc_type == "TimeoutError" for s in chain.swallowed)


# --- sys.excepthook integration ---

def test_excepthook_enriches_and_prints_context(capsys):
    record(OpType.DB_QUERY, duration_ms=2.0, success=False,
           statement="SELECT * FROM orders", error="OperationalError")
    exc = RuntimeError("something broke")
    with patch("sys.__excepthook__"):
        _because_excepthook(RuntimeError, exc, None)

    captured = capsys.readouterr()
    assert "[because context]" in captured.err
    assert hasattr(exc, "__context_chain__")
