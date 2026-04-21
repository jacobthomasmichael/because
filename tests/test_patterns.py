import threading

import pytest

from because.buffer import OpType, get_context, record
from because.enrichment import ContextChain, SwallowedExc, catch, enrich, enrich_with_swallowed
from because.patterns import match_all
from because.patterns.pool_exhaustion import match as pool_match
from because.patterns.silent_failure import match as silent_match


# helpers

def _make_chain(ops=None, swallowed=None):
    return ContextChain(operations=ops or [], swallowed=swallowed or [])


def _db_op(success=True, statement="SELECT 1", error=None):
    from because.buffer import Op
    import time
    meta = {"statement": statement, "executemany": False}
    if error:
        meta["error"] = error
    return Op(OpType.DB_QUERY, timestamp=time.monotonic(), duration_ms=5.0,
              success=success, metadata=meta)


def _swallowed_op(exc_type="TimeoutError", message="timed out"):
    from because.buffer import Op
    import time
    return Op(OpType.EXCEPTION_SWALLOWED, timestamp=time.monotonic(), duration_ms=None,
              success=False, metadata={"exc_type": exc_type, "message": message})


# --- pool_exhaustion ---

def test_pool_exhaustion_triggers_on_refusal_with_db_ops():
    chain = _make_chain(ops=[_db_op()] * 5 + [_db_op(success=False, error="OperationalError")] * 3)
    exc = Exception("connection refused on localhost:5432")
    result = pool_match(exc, chain)
    assert result is not None
    assert result.name == "pool_exhaustion"


def test_pool_exhaustion_high_failure_rate_is_likely_cause():
    chain = _make_chain(ops=[_db_op(success=False, error="OperationalError")] * 4 + [_db_op()])
    exc = Exception("QueuePool limit of size 5 overflow 10 reached")
    result = pool_match(exc, chain)
    assert result is not None
    assert result.confidence == "likely_cause"


def test_pool_exhaustion_no_match_without_db_ops():
    chain = _make_chain()
    exc = Exception("connection refused on localhost:5432")
    result = pool_match(exc, chain)
    assert result is None


def test_pool_exhaustion_no_match_unrelated_exception():
    chain = _make_chain(ops=[_db_op()] * 5)
    exc = ValueError("invalid input syntax")
    result = pool_match(exc, chain)
    assert result is None


def test_pool_exhaustion_no_match_too_few_db_ops():
    chain = _make_chain(ops=[_db_op()])
    exc = Exception("connection refused")
    result = pool_match(exc, chain)
    assert result is None


# --- silent_failure ---

def test_silent_failure_triggers_on_swallowed_ops():
    chain = _make_chain(ops=[_swallowed_op("TimeoutError", "pool timeout")])
    exc = RuntimeError("NoneType has no attribute 'execute'")
    result = silent_match(exc, chain)
    assert result is not None
    assert result.name == "silent_failure"


def test_silent_failure_upstream_type_is_likely_cause():
    chain = _make_chain(
        swallowed=[SwallowedExc("ConnectionError", "refused", 0.0)]
    )
    exc = RuntimeError("downstream boom")
    result = silent_match(exc, chain)
    assert result is not None
    assert result.confidence == "likely_cause"


def test_silent_failure_generic_swallow_is_contributing():
    chain = _make_chain(
        swallowed=[SwallowedExc("NotImplementedError", "stub", 0.0)]
    )
    exc = RuntimeError("boom")
    result = silent_match(exc, chain)
    assert result is not None
    assert result.confidence == "contributing_factor"


def test_silent_failure_no_match_clean_context():
    chain = _make_chain(ops=[_db_op()] * 3)
    exc = ValueError("bad input")
    result = silent_match(exc, chain)
    assert result is None


# --- match_all integration ---

def test_match_all_returns_multiple_patterns():
    chain = _make_chain(
        ops=[_db_op()] * 5 + [_db_op(success=False, error="OperationalError")] * 3,
        swallowed=[SwallowedExc("TimeoutError", "timeout", 0.0)],
    )
    exc = Exception("connection refused on localhost:5432")
    matches = match_all(exc, chain)
    names = {m.name for m in matches}
    assert "pool_exhaustion" in names
    assert "silent_failure" in names


def test_match_all_returns_empty_on_clean_trace():
    chain = _make_chain(ops=[_db_op()] * 2)
    exc = ValueError("user error")
    assert match_all(exc, chain) == []


def test_match_all_swallows_broken_pattern(monkeypatch):
    import because.patterns as patterns_mod
    original = list(patterns_mod._PATTERNS)

    class BrokenPattern:
        def match(self, exc, chain):
            raise RuntimeError("pattern bug")

    monkeypatch.setattr(patterns_mod, "_PATTERNS", [BrokenPattern()])
    chain = _make_chain()
    exc = ValueError("x")
    assert match_all(exc, chain) == []  # no crash
    monkeypatch.setattr(patterns_mod, "_PATTERNS", original)


# --- enrich() integration ---

def test_enrich_populates_pattern_matches():
    def worker():
        record(OpType.DB_QUERY, duration_ms=5.0, success=False,
               statement="SELECT 1", error="OperationalError")
        record(OpType.DB_QUERY, duration_ms=5.0, success=False,
               statement="SELECT 1", error="OperationalError")
        record(OpType.DB_QUERY, duration_ms=5.0, success=False,
               statement="SELECT 1", error="OperationalError")
        record(OpType.DB_QUERY, duration_ms=5.0, success=True, statement="SELECT 1")
        exc = Exception("connection refused on localhost:5432")
        enrich(exc)
        return exc.__context_chain__.pattern_matches

    # run in a thread for isolated buffer
    result = {}
    t = threading.Thread(target=lambda: result.update({"m": worker()}))
    t.start()
    t.join()
    assert any(m.name == "pool_exhaustion" for m in result["m"])


def test_format_shows_pattern_output(capsys):
    from because.enrichment import format_context_chain

    def worker():
        record(OpType.DB_QUERY, duration_ms=5.0, success=False,
               statement="SELECT 1", error="OperationalError")
        record(OpType.DB_QUERY, duration_ms=5.0, success=False,
               statement="SELECT 1", error="OperationalError")
        record(OpType.DB_QUERY, duration_ms=5.0, success=False,
               statement="SELECT 1", error="OperationalError")
        record(OpType.DB_QUERY, duration_ms=5.0, success=True, statement="SELECT 1")
        exc = Exception("connection refused on localhost:5432")
        enrich(exc)
        return format_context_chain(exc)

    result = {}
    t = threading.Thread(target=lambda: result.update({"out": worker()}))
    t.start()
    t.join()
    assert "pool_exhaustion" in result["out"] or "pool" in result["out"].lower()
