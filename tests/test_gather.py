"""Tests for because.gather — async context merging."""
import asyncio
import pytest

import because
from because.buffer import OpType, get_context, record, _ctx_buffer, RingBuffer
from because.buffer import gather as because_gather


# ── helpers ───────────────────────────────────────────────────────────────────

async def _record_http(url: str, success: bool = True) -> str:
    record(OpType.HTTP_REQUEST, duration_ms=10.0, success=success,
           method="GET", url=url)
    return url


async def _record_db(success: bool = True) -> str:
    record(OpType.DB_QUERY, duration_ms=5.0, success=success,
           statement="SELECT 1")
    return "db"


def _ops_of_type(op_type: OpType) -> list:
    return [op for op in get_context().snapshot() if op.op_type == op_type]


# ── basic merging ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_gather_merges_child_http_ops():
    token = _ctx_buffer.set(RingBuffer())
    try:
        await because_gather(
            _record_http("https://api.example.com/a"),
            _record_http("https://api.example.com/b"),
        )
        ops = _ops_of_type(OpType.HTTP_REQUEST)
        urls = {op.metadata["url"] for op in ops}
        assert "https://api.example.com/a" in urls
        assert "https://api.example.com/b" in urls
    finally:
        _ctx_buffer.reset(token)


@pytest.mark.asyncio
async def test_gather_merges_mixed_op_types():
    token = _ctx_buffer.set(RingBuffer())
    try:
        await because_gather(
            _record_http("https://api.example.com/check"),
            _record_db(),
        )
        snapshot = get_context().snapshot()
        types = {op.op_type for op in snapshot}
        assert OpType.HTTP_REQUEST in types
        assert OpType.DB_QUERY in types
    finally:
        _ctx_buffer.reset(token)


@pytest.mark.asyncio
async def test_gather_merges_ops_sorted_by_timestamp():
    token = _ctx_buffer.set(RingBuffer())
    try:
        await because_gather(
            _record_http("https://first.example.com"),
            _record_http("https://second.example.com"),
        )
        ops = get_context().snapshot()
        timestamps = [op.timestamp for op in ops]
        assert timestamps == sorted(timestamps)
    finally:
        _ctx_buffer.reset(token)


@pytest.mark.asyncio
async def test_gather_returns_results():
    token = _ctx_buffer.set(RingBuffer())
    try:
        results = await because_gather(
            _record_http("https://api.example.com/a"),
            _record_http("https://api.example.com/b"),
        )
        assert set(results) == {
            "https://api.example.com/a",
            "https://api.example.com/b",
        }
    finally:
        _ctx_buffer.reset(token)


@pytest.mark.asyncio
async def test_gather_empty_coros():
    token = _ctx_buffer.set(RingBuffer())
    try:
        results = await because_gather()
        assert results == []
    finally:
        _ctx_buffer.reset(token)


# ── parent ops preserved ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_gather_preserves_parent_ops():
    token = _ctx_buffer.set(RingBuffer())
    try:
        record(OpType.DB_QUERY, duration_ms=2.0, success=True, statement="SELECT setup")
        await because_gather(_record_http("https://api.example.com/check"))
        snapshot = get_context().snapshot()
        statements = [op.metadata.get("statement") for op in snapshot if op.op_type == OpType.DB_QUERY]
        assert "SELECT setup" in statements
    finally:
        _ctx_buffer.reset(token)


# ── exception handling ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_gather_propagates_exception():
    async def boom():
        raise ValueError("child task failed")

    token = _ctx_buffer.set(RingBuffer())
    try:
        with pytest.raises(ValueError, match="child task failed"):
            await because_gather(boom())
    finally:
        _ctx_buffer.reset(token)


@pytest.mark.asyncio
async def test_gather_return_exceptions_true():
    async def boom():
        raise ValueError("oops")

    token = _ctx_buffer.set(RingBuffer())
    try:
        results = await because_gather(boom(), return_exceptions=True)
        assert len(results) == 1
        assert isinstance(results[0], ValueError)
    finally:
        _ctx_buffer.reset(token)


@pytest.mark.asyncio
async def test_gather_merges_ops_even_when_one_task_fails():
    async def good():
        record(OpType.HTTP_REQUEST, duration_ms=10.0, success=True,
               method="GET", url="https://ok.example.com")

    async def bad():
        record(OpType.HTTP_REQUEST, duration_ms=10.0, success=False,
               method="GET", url="https://fail.example.com", error="Timeout")
        raise TimeoutError("upstream timed out")

    token = _ctx_buffer.set(RingBuffer())
    try:
        results = await because_gather(good(), bad(), return_exceptions=True)
        ops = _ops_of_type(OpType.HTTP_REQUEST)
        urls = {op.metadata["url"] for op in ops}
        assert "https://ok.example.com" in urls
        assert "https://fail.example.com" in urls
        assert any(isinstance(r, TimeoutError) for r in results)
    finally:
        _ctx_buffer.reset(token)


# ── public API ────────────────────────────────────────────────────────────────

def test_gather_exported_from_because():
    assert hasattr(because, "gather")
    assert because.gather is because_gather
