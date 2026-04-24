from __future__ import annotations

from typing import Any

from because.buffer import RingBuffer, _ctx_buffer


class BecauseFlask:
    """Flask extension that scopes a fresh ring buffer to each request and
    enriches exceptions before Flask's error handlers receive them.

    Usage::

        app = Flask(__name__)
        BecauseFlask(app)

        # app factory pattern:
        ext = BecauseFlask()
        ext.init_app(app)
    """

    def __init__(self, app: Any = None) -> None:
        if app is not None:
            self.init_app(app)

    def init_app(self, app: Any) -> None:
        @app.before_request
        def _reset_buffer():
            # Fresh buffer per request. No teardown needed — before_request
            # always overwrites before the next request on this thread.
            _ctx_buffer.set(RingBuffer())

        # Patch handle_user_exception so we enrich the exception before any
        # error handler (registered or default) receives it. In Flask 3.x,
        # got_request_exception only fires for unhandled 500s, so patching
        # the method is the only way to intercept all exception types.
        _patch_handle_user_exception(app)


def _patch_handle_user_exception(app: Any) -> None:
    if getattr(app, "_because_patched", False):
        return
    app._because_patched = True

    original = app.handle_user_exception

    def patched(e: Exception):
        from because.enrichment import enrich_with_swallowed
        enrich_with_swallowed(e)
        return original(e)

    app.handle_user_exception = patched
