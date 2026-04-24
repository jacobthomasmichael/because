from __future__ import annotations

import socket
import time
from typing import Any

from because.buffer import OpType, record

_original_connect = socket.socket.connect
_original_connect_ex = socket.socket.connect_ex
_installed = False


def instrument() -> None:
    """Monkey-patch stdlib socket.connect to record TCP connection attempts.

    Idempotent — safe to call multiple times. Records to the current
    execution context's ring buffer, so per-thread and per-asyncio-task
    isolation is preserved automatically.
    """
    global _installed
    if _installed:
        return
    _installed = True

    def _patched_connect(self: socket.socket, address: Any) -> None:
        host, port = address[0], address[1]
        start = time.monotonic()
        try:
            _original_connect(self, address)
            duration_ms = (time.monotonic() - start) * 1000
            record(
                OpType.HTTP_REQUEST,
                duration_ms=duration_ms,
                success=True,
                kind="tcp_connect",
                host=host,
                port=port,
            )
        except OSError as exc:
            duration_ms = (time.monotonic() - start) * 1000
            record(
                OpType.HTTP_REQUEST,
                duration_ms=duration_ms,
                success=False,
                kind="tcp_connect",
                host=host,
                port=port,
                error=type(exc).__name__,
                errno=exc.errno,
            )
            raise

    def _patched_connect_ex(self: socket.socket, address: Any) -> int:
        host, port = address[0], address[1]
        start = time.monotonic()
        result = _original_connect_ex(self, address)
        duration_ms = (time.monotonic() - start) * 1000
        record(
            OpType.HTTP_REQUEST,
            duration_ms=duration_ms,
            success=(result == 0),
            kind="tcp_connect",
            host=host,
            port=port,
            errno=result,
        )
        return result

    socket.socket.connect = _patched_connect  # type: ignore[method-assign]
    socket.socket.connect_ex = _patched_connect_ex  # type: ignore[method-assign]


def uninstall() -> None:
    """Restore the original socket.connect methods. Useful in tests."""
    global _installed
    socket.socket.connect = _original_connect  # type: ignore[method-assign]
    socket.socket.connect_ex = _original_connect_ex  # type: ignore[method-assign]
    _installed = False
