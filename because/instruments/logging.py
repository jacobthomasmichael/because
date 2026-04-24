from __future__ import annotations

import logging

from because.buffer import OpType, record

_INSTALLED = False


class _BecauseHandler(logging.Handler):
    """Logging handler that records WARNING+ log entries into the ring buffer."""

    def emit(self, log_record: logging.LogRecord) -> None:
        try:
            record(
                OpType.LOG,
                duration_ms=None,
                success=log_record.levelno < logging.ERROR,
                level=log_record.levelname,
                logger=log_record.name,
                message=log_record.getMessage()[:200],
            )
        except Exception:
            pass


def instrument(logger: logging.Logger | None = None, level: int = logging.WARNING) -> None:
    """Attach because instrumentation to a logger (default: root logger).

    Records WARNING and above into the ring buffer so log events appear
    in the operation timeline alongside DB queries and HTTP requests.
    """
    global _INSTALLED

    target = logger or logging.getLogger()

    # Avoid adding a second handler if already instrumented
    if any(isinstance(h, _BecauseHandler) for h in target.handlers):
        return

    handler = _BecauseHandler(level)
    handler.setLevel(level)
    target.addHandler(handler)

    if logger is None:
        _INSTALLED = True
