from __future__ import annotations

import time
from typing import Any

from because.buffer import OpType, record

_installed_engines: set[int] = set()


def instrument(engine: Any) -> None:
    """Attach because instrumentation to a SQLAlchemy engine."""
    if id(engine) in _installed_engines:
        return
    _installed_engines.add(id(engine))

    from sqlalchemy import event

    # keyed by execution_context id — stable across before/after/error events
    _start_times: dict[int, float] = {}

    @event.listens_for(engine, "before_cursor_execute")
    def before_execute(conn, cursor, statement, parameters, context, executemany):
        if context is not None:
            _start_times[id(context)] = time.monotonic()

    @event.listens_for(engine, "after_cursor_execute")
    def after_execute(conn, cursor, statement, parameters, context, executemany):
        start = _start_times.pop(id(context), None)
        duration_ms = (time.monotonic() - start) * 1000 if start is not None else None
        record(
            OpType.DB_QUERY,
            duration_ms=duration_ms,
            success=True,
            statement=_truncate(statement),
            executemany=executemany,
        )

    @event.listens_for(engine, "handle_error")
    def on_error(exception_context):
        ctx = exception_context.execution_context
        start = _start_times.pop(id(ctx), None) if ctx is not None else None
        duration_ms = (time.monotonic() - start) * 1000 if start is not None else None
        record(
            OpType.DB_QUERY,
            duration_ms=duration_ms,
            success=False,
            statement=_truncate(str(exception_context.statement or "")),
            error=type(exception_context.original_exception).__name__,
        )


def _truncate(s: str, max_len: int = 200) -> str:
    return s if len(s) <= max_len else s[:max_len] + "…"
