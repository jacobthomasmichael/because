from __future__ import annotations

import time
from typing import Any

from because.buffer import OpType, record


def instrument(session: Any) -> None:
    """Attach because instrumentation to a requests.Session."""
    from requests import Response
    from requests.adapters import HTTPAdapter

    if getattr(session, "_because_instrumented", False):
        return
    session._because_instrumented = True

    class BecauseAdapter(HTTPAdapter):
        def send(self, request, **kwargs):
            start = time.monotonic()
            try:
                response: Response = super().send(request, **kwargs)
                duration_ms = (time.monotonic() - start) * 1000
                record(
                    OpType.HTTP_REQUEST,
                    duration_ms=duration_ms,
                    success=True,
                    method=request.method,
                    url=_sanitize_url(request.url),
                    status_code=response.status_code,
                )
                return response
            except Exception as exc:
                duration_ms = (time.monotonic() - start) * 1000
                record(
                    OpType.HTTP_REQUEST,
                    duration_ms=duration_ms,
                    success=False,
                    method=request.method,
                    url=_sanitize_url(request.url),
                    error=type(exc).__name__,
                )
                raise

    session.mount("https://", BecauseAdapter())
    session.mount("http://", BecauseAdapter())


def _sanitize_url(url: str) -> str:
    """Strip query string to avoid capturing credentials or sensitive params."""
    return url.split("?")[0] if url else url
