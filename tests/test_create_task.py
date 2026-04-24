"""Tests for because.create_task — async task buffer merging."""
import asyncio
import pytest

import because
from because.buffer import OpType, get_context, record, _ctx_buffer, RingBuffer
from because.buffer import create_task as because_create_task


def _ops_of_type(op_type: OpType) -> list:
    return [op for op in get_context().snapshot() if op.op_type == op_type]


async def _record_http(url: str, success: bool = True) -> str:
    record(OpType.HTTP_REQUEST, duration_ms=10.0, success=success,
           method="GET", url=url)
    return url


async def _record_db() -> str:
    record(OpType.DB_QUERY, duration_ms=5.0, success=True, statement="SELECT 1")
    return "db"


# ── merging ───────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_task_merges_child_ops():
    token = _ctx_buffer.set(RingBuffer())
    try:
        task = because_create_task(_record_http("https://api.example.com/users"))
        await task
        ops = _ops_of_type(OpType.HTTP_REQUEST)
        assert len(ops) == 1
        assert ops[0].metadata["url"] == "https://api.example.com/users"
    finally:
        _ctx_buffer.reset(token)


@pytest.mark.asyncio
async def test_create_task_merges_multiple_tasks():
    token = _ctx_buffer.set(RingBuffer())
    try:
        t1 = because_create_task(_record_http("https://api.example.com/a"))
        t2 = because_create_task(_record_http("https://api.example.com/b"))
        await asyncio.gather(t1, t2)
        urls = {op.metadata["url"] for op in _ops_of_type(OpType.HTTP_REQUEST)}
        assert "https://api.example.com/a" in urls
        assert "https://api.example.com/b" in urls
    finally:
        _ctx_buffer.reset(token)


@pytest.mark.asyncio
async def test_create_task_preserves_parent_ops():
    token = _ctx_buffer.set(RingBuffer())
    try:
        record(OpType.DB_QUERY, duration_ms=2.0, success=True, statement="SELECT setup")
        task = because_create_task(_record_http("https://api.example.com"))
        await task
        statements = [op.metadata.get("statement") for op in get_context().snapshot()
                      if op.op_type == OpType.DB_QUERY]
        assert "SELECT setup" in statements
    finally:
        _ctx_buffer.reset(token)


@pytest.mark.asyncio
async def test_create_task_merges_ops_sorted_by_timestamp():
    token = _ctx_buffer.set(RingBuffer())
    try:
        t1 = because_create_task(_record_http("https://first.example.com"))
        t2 = because_create_task(_record_http("https://second.example.com"))
        await asyncio.gather(t1, t2)
        timestamps = [op.timestamp for op in get_context().snapshot()]
        assert timestamps == sorted(timestamps)
    finally:
        _ctx_buffer.reset(token)


# ── merge_on_done=False ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_task_no_merge_when_disabled():
    token = _ctx_buffer.set(RingBuffer())
    try:
        task = because_create_task(
            _record_http("https://api.example.com"),
            merge_on_done=False,
        )
        await task
        ops = _ops_of_type(OpType.HTTP_REQUEST)
        assert len(ops) == 0  # not merged back
    finally:
        _ctx_buffer.reset(token)


# ── task naming ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_task_passes_name():
    token = _ctx_buffer.set(RingBuffer())
    try:
        task = because_create_task(_record_http("https://api.example.com"), name="my-task")
        assert task.get_name() == "my-task"
        await task
    finally:
        _ctx_buffer.reset(token)


# ── return value ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_task_returns_correct_value():
    token = _ctx_buffer.set(RingBuffer())
    try:
        task = because_create_task(_record_http("https://api.example.com/result"))
        result = await task
        assert result == "https://api.example.com/result"
    finally:
        _ctx_buffer.reset(token)


# ── exception propagation ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_task_propagates_exception():
    async def boom():
        raise ValueError("task failed")

    token = _ctx_buffer.set(RingBuffer())
    try:
        task = because_create_task(boom())
        with pytest.raises(ValueError, match="task failed"):
            await task
    finally:
        _ctx_buffer.reset(token)


@pytest.mark.asyncio
async def test_create_task_merges_ops_even_on_exception():
    async def record_then_fail():
        record(OpType.HTTP_REQUEST, duration_ms=10.0, success=False,
               method="GET", url="https://fail.example.com", error="Timeout")
        raise TimeoutError("upstream timed out")

    token = _ctx_buffer.set(RingBuffer())
    try:
        task = because_create_task(record_then_fail())
        with pytest.raises(TimeoutError):
            await task
        ops = _ops_of_type(OpType.HTTP_REQUEST)
        assert len(ops) == 1
        assert ops[0].metadata["url"] == "https://fail.example.com"
    finally:
        _ctx_buffer.reset(token)


# ── public API ────────────────────────────────────────────────────────────────

def test_create_task_exported_from_because():
    assert hasattr(because, "create_task")
    assert because.create_task is because_create_task
