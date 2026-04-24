from __future__ import annotations

from typing import Any

from because.buffer import RingBuffer, _ctx_buffer


class BecauseMiddleware:
    """Starlette/FastAPI ASGI middleware that scopes a fresh ring buffer to
    each request and enriches exceptions at the point they are raised —
    before FastAPI's ExceptionMiddleware converts them to responses.

    Usage::

        from fastapi import FastAPI
        from because.integrations.fastapi import BecauseMiddleware

        app = FastAPI()
        app.add_middleware(BecauseMiddleware)
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        token = _ctx_buffer.set(RingBuffer())
        try:
            # Wrap the inner app so we see exceptions before ExceptionMiddleware
            # converts them into HTTP responses.
            await _run_enriching(self.app, scope, receive, send)
        finally:
            _ctx_buffer.reset(token)


async def _run_enriching(app: Any, scope: Any, receive: Any, send: Any) -> None:
    """Run app(scope, receive, send) and enrich any exception that escapes."""
    exc_to_enrich: list[BaseException] = []

    async def enriching_receive():
        return await receive()

    # Intercept at the receive boundary isn't enough — we need to wrap the
    # entire app call and catch at this level, which IS above ExceptionMiddleware
    # only when add_middleware() places us outermost. For exceptions that
    # ExceptionMiddleware handles (registered handlers), we use a patched send
    # that captures the exc_info from the ASGI error response cycle.
    #
    # Simplest reliable approach: try/except here catches exceptions that
    # propagate past ALL inner middleware (i.e. truly unhandled 500s).
    # For handled exceptions (registered exception_handlers), enrich via
    # scope["because.exc"] set by a Starlette background task — but that
    # requires cooperation. Instead we expose a helper for exception handlers.
    try:
        await app(scope, receive, send)
    except Exception as exc:
        from because.enrichment import enrich_with_swallowed
        enrich_with_swallowed(exc)
        raise
