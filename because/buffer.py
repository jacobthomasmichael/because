from __future__ import annotations

import asyncio
import time
from collections import deque
from contextvars import ContextVar
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Coroutine, TypeVar

_T = TypeVar("_T")

DEFAULT_BUFFER_SIZE = 128


class OpType(str, Enum):
    DB_QUERY = "db_query"
    HTTP_REQUEST = "http_request"
    CACHE = "cache"
    LOG = "log"
    EXCEPTION_SWALLOWED = "exception_swallowed"


@dataclass(slots=True)
class Op:
    op_type: OpType
    timestamp: float
    duration_ms: float | None
    success: bool
    metadata: dict[str, Any] = field(default_factory=dict)


class RingBuffer:
    def __init__(self, maxsize: int = DEFAULT_BUFFER_SIZE) -> None:
        self._buf: deque[Op] = deque(maxlen=maxsize)

    def record(self, op: Op) -> None:
        self._buf.append(op)

    def snapshot(self) -> list[Op]:
        return list(self._buf)

    def __len__(self) -> int:
        return len(self._buf)


_ctx_buffer: ContextVar[RingBuffer | None] = ContextVar("because_buffer", default=None)

_installed = False


def get_context() -> RingBuffer:
    buf = _ctx_buffer.get()
    if buf is None:
        buf = RingBuffer()
        _ctx_buffer.set(buf)
    return buf


def record(
    op_type: OpType,
    *,
    duration_ms: float | None = None,
    success: bool = True,
    **metadata: Any,
) -> None:
    get_context().record(
        Op(
            op_type=op_type,
            timestamp=time.monotonic(),
            duration_ms=duration_ms,
            success=success,
            metadata=metadata,
        )
    )


async def gather(*coros: Coroutine, return_exceptions: bool = False) -> list:
    """Drop-in replacement for asyncio.gather() that merges child task buffers
    back into the parent context after all tasks complete.

    Without this, each asyncio task gets its own isolated ring buffer copy
    (ContextVar semantics), so DB/HTTP ops recorded in subtasks are invisible
    when the parent exception fires.

    Usage::

        results = await because.gather(fetch(url1), fetch(url2), query_db())

    Equivalent to asyncio.gather() in every other respect.
    """
    parent_buf = get_context()
    child_buffers: list[RingBuffer] = []

    async def _wrap(coro: Coroutine) -> Any:
        child_buf = RingBuffer(maxsize=parent_buf._buf.maxlen or DEFAULT_BUFFER_SIZE)
        _ctx_buffer.set(child_buf)
        child_buffers.append(child_buf)
        return await coro

    wrapped = [_wrap(c) for c in coros]
    results = await asyncio.gather(*wrapped, return_exceptions=return_exceptions)

    # Merge all child ops into the parent buffer, sorted by timestamp
    all_ops = sorted(
        (op for buf in child_buffers for op in buf.snapshot()),
        key=lambda op: op.timestamp,
    )
    for op in all_ops:
        parent_buf.record(op)

    return list(results)


def install(buffer_size: int = DEFAULT_BUFFER_SIZE) -> None:
    global _installed, DEFAULT_BUFFER_SIZE
    if _installed:
        return
    DEFAULT_BUFFER_SIZE = buffer_size
    _installed = True
