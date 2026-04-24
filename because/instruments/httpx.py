from __future__ import annotations

import time
from typing import Any

from because.buffer import OpType, record


def instrument(client: Any) -> None:
    """Attach because instrumentation to an httpx.Client or httpx.AsyncClient."""
    import httpx

    if not isinstance(client, (httpx.Client, httpx.AsyncClient)):
        raise TypeError(f"Expected httpx.Client or httpx.AsyncClient, got {type(client)}")

    if getattr(client, "_because_instrumented", False):
        return
    client._because_instrumented = True

    if isinstance(client, httpx.AsyncClient):
        client._transport = _BecauseAsyncTransport(client._transport)
    else:
        client._transport = _BecauseSyncTransport(client._transport)


class _BecauseSyncTransport:
    def __init__(self, wrapped: Any) -> None:
        self._wrapped = wrapped

    def handle_request(self, request: Any) -> Any:
        import httpx

        start = time.monotonic()
        try:
            response = self._wrapped.handle_request(request)
            duration_ms = (time.monotonic() - start) * 1000
            record(
                OpType.HTTP_REQUEST,
                duration_ms=duration_ms,
                success=True,
                method=request.method,
                url=_sanitize_url(str(request.url)),
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
                url=_sanitize_url(str(request.url)),
                error=type(exc).__name__,
            )
            raise

    # Delegate everything else (close, etc.) to the wrapped transport
    def __getattr__(self, name: str) -> Any:
        return getattr(self._wrapped, name)


class _BecauseAsyncTransport:
    def __init__(self, wrapped: Any) -> None:
        self._wrapped = wrapped

    async def handle_async_request(self, request: Any) -> Any:
        start = time.monotonic()
        try:
            response = await self._wrapped.handle_async_request(request)
            duration_ms = (time.monotonic() - start) * 1000
            record(
                OpType.HTTP_REQUEST,
                duration_ms=duration_ms,
                success=True,
                method=request.method,
                url=_sanitize_url(str(request.url)),
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
                url=_sanitize_url(str(request.url)),
                error=type(exc).__name__,
            )
            raise

    def __getattr__(self, name: str) -> Any:
        return getattr(self._wrapped, name)


def _sanitize_url(url: str) -> str:
    return url.split("?")[0] if url else url
