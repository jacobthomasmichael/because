"""
Structured logging integration for ``because``.

Adds because context to Python log records so it flows into any JSON
logging pipeline (structlog, python-json-logger, etc.).

Usage::

    import logging
    from because.integrations.logging import BecauseFilter

    handler = logging.StreamHandler()
    handler.addFilter(BecauseFilter())
    logging.getLogger().addHandler(handler)

Or with a custom formatter::

    from because.integrations.logging import BecauseFormatter

    handler = logging.StreamHandler()
    handler.setFormatter(BecauseFormatter())
    logging.getLogger().addHandler(handler)
"""
from __future__ import annotations

import json
import logging
import traceback

from because.integrations.serialize import chain_from_exc, chain_to_dict


class BecauseFilter(logging.Filter):
    """Logging filter that attaches because context to any log record that
    has exc_info set and the exception carries a __context_chain__."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.exc_info:
            exc = record.exc_info[1]
            chain = chain_from_exc(exc) if exc else None
            if chain is not None:
                record.because = chain_to_dict(chain)  # type: ignore[attr-defined]
        return True


class BecauseFormatter(logging.Formatter):
    """Formatter that emits JSON log records with because context embedded
    under a ``because`` key when an exception with __context_chain__ is present."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict = {
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        if record.exc_info:
            exc = record.exc_info[1]
            payload["exc_type"] = type(exc).__name__ if exc else None
            payload["exc_message"] = str(exc) if exc else None
            chain = chain_from_exc(exc) if exc else None
            if chain is not None:
                payload["because"] = chain_to_dict(chain)

        return json.dumps(payload, default=str)
