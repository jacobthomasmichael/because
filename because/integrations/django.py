"""
Django integration for ``because``.

Add to MIDDLEWARE in settings.py::

    MIDDLEWARE = [
        "because.integrations.django.BecauseMiddleware",
        ...
    ]

This gives you:
- A fresh ring buffer scoped to each request
- Automatic enrichment of exceptions before Django's error handlers see them
"""
from __future__ import annotations

from because.buffer import RingBuffer, _ctx_buffer


class BecauseMiddleware:
    """Django middleware that scopes a fresh ring buffer to each request
    and enriches exceptions before they reach Django's error handlers.

    process_exception is called by Django before error views run, so
    __context_chain__ is already attached when your error handler receives it.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        buf = RingBuffer()
        token = _ctx_buffer.set(buf)
        # Store the buffer on the request so process_exception can access it
        # even after the finally block below has reset the ContextVar.
        request._because_buffer = buf
        try:
            response = self.get_response(request)
        finally:
            _ctx_buffer.reset(token)
        return response

    def process_exception(self, request, exception):
        """Called by Django before its error handlers. Enrich the exception
        using the request's buffer, which may already be reset in the ContextVar."""
        from because.enrichment import enrich_with_swallowed

        buf = getattr(request, "_because_buffer", None)
        if buf is not None:
            # Temporarily restore this request's buffer so enrich() snapshots
            # the right ops, then immediately reset.
            token = _ctx_buffer.set(buf)
            try:
                enrich_with_swallowed(exception)
            finally:
                _ctx_buffer.reset(token)
        else:
            enrich_with_swallowed(exception)

        return None  # let Django continue normal exception handling
