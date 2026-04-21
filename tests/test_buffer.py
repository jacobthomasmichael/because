import asyncio
import threading

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from because.buffer import Op, OpType, RingBuffer, get_context, record


# --- RingBuffer unit tests ---

def test_ring_buffer_appends():
    buf = RingBuffer(maxsize=4)
    for i in range(4):
        buf.record(Op(OpType.DB_QUERY, timestamp=float(i), duration_ms=1.0, success=True))
    assert len(buf) == 4


def test_ring_buffer_evicts_oldest():
    buf = RingBuffer(maxsize=3)
    for i in range(5):
        buf.record(Op(OpType.DB_QUERY, timestamp=float(i), duration_ms=None, success=True))
    ops = buf.snapshot()
    assert len(ops) == 3
    assert ops[0].timestamp == 2.0


def test_snapshot_is_copy():
    buf = RingBuffer(maxsize=4)
    buf.record(Op(OpType.HTTP_REQUEST, timestamp=0.0, duration_ms=10.0, success=True))
    snap1 = buf.snapshot()
    buf.record(Op(OpType.HTTP_REQUEST, timestamp=1.0, duration_ms=10.0, success=True))
    assert len(snap1) == 1


@given(st.lists(st.floats(min_value=0, max_value=1e9, allow_nan=False), min_size=0, max_size=500))
@settings(max_examples=200)
def test_ring_buffer_never_exceeds_maxsize(timestamps):
    maxsize = 10
    buf = RingBuffer(maxsize=maxsize)
    for ts in timestamps:
        buf.record(Op(OpType.LOG, timestamp=ts, duration_ms=None, success=True))
    assert len(buf) <= maxsize


# --- ContextVar isolation tests ---

def test_context_isolated_across_threads():
    results = {}

    def worker(name, count):
        for _ in range(count):
            record(OpType.DB_QUERY, duration_ms=1.0)
        results[name] = len(get_context().snapshot())

    t1 = threading.Thread(target=worker, args=("a", 3))
    t2 = threading.Thread(target=worker, args=("b", 7))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert results["a"] == 3
    assert results["b"] == 7


def test_context_isolated_across_asyncio_tasks():
    async def worker(count):
        for _ in range(count):
            record(OpType.HTTP_REQUEST, duration_ms=5.0)
        return len(get_context().snapshot())

    async def run():
        results = await asyncio.gather(worker(4), worker(9))
        return results

    r1, r2 = asyncio.run(run())
    # asyncio tasks share context by default (copied at creation); each task
    # writes to its own copy after the first mutation via ContextVar.set
    assert r1 == 4
    assert r2 == 9


def test_record_helper_stores_metadata():
    # fresh context per test — threads get isolated buffers
    buf = get_context()
    before = len(buf.snapshot())
    record(OpType.DB_QUERY, duration_ms=12.5, success=False, table="users")
    ops = buf.snapshot()
    new_ops = ops[before:]
    assert len(new_ops) == 1
    op = new_ops[0]
    assert op.op_type == OpType.DB_QUERY
    assert op.duration_ms == 12.5
    assert op.success is False
    assert op.metadata["table"] == "users"
