from __future__ import annotations

import time
from collections import deque
from contextvars import ContextVar
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

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


def install(buffer_size: int = DEFAULT_BUFFER_SIZE) -> None:
    global _installed, DEFAULT_BUFFER_SIZE
    if _installed:
        return
    DEFAULT_BUFFER_SIZE = buffer_size
    _installed = True
