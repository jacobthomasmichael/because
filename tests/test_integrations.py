import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient as FastAPITestClient
from flask import Flask

from because.buffer import OpType, get_context, record
from because.enrichment import ContextChain
from because.integrations.fastapi import BecauseMiddleware
from because.integrations.flask import BecauseFlask


# ── Flask ─────────────────────────────────────────────────────────────────────

@pytest.fixture
def flask_app():
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.config["PROPAGATE_EXCEPTIONS"] = False  # ensure error handlers + signals fire
    BecauseFlask(app)

    @app.route("/ok")
    def ok():
        record(OpType.DB_QUERY, duration_ms=2.0, success=True, statement="SELECT 1")
        return {"status": "ok"}

    @app.route("/fail")
    def fail():
        record(OpType.DB_QUERY, duration_ms=5.0, success=True, statement="SELECT 1")
        raise RuntimeError("something broke")

    @app.route("/swallow")
    def swallow():
        import because
        with because.catch(ValueError):
            raise ValueError("upstream silent failure")
        raise RuntimeError("downstream crash")

    return app


def test_flask_buffer_fresh_per_request(flask_app):
    """Each request starts with an empty buffer — ops don't bleed across requests."""
    captured_lengths = []

    @flask_app.errorhandler(RuntimeError)
    def handle(exc):
        captured_lengths.append(len(get_context().snapshot()))
        return {"error": str(exc)}, 500

    with flask_app.test_client() as c:
        c.get("/fail")  # 1 DB op recorded
        c.get("/fail")  # should also have exactly 1, not 2

    assert captured_lengths == [1, 1]


def test_flask_exception_enriched(flask_app):
    """got_request_exception enriches exc before errorhandler receives it."""
    captured = {}

    @flask_app.errorhandler(RuntimeError)
    def handle(exc):
        captured["exc"] = exc
        return {"error": str(exc)}, 500

    with flask_app.test_client() as c:
        c.get("/fail")

    exc = captured.get("exc")
    assert exc is not None
    assert hasattr(exc, "__context_chain__"), "exception should have __context_chain__"
    chain: ContextChain = exc.__context_chain__
    assert any(op.op_type == OpType.DB_QUERY for op in chain.operations)


def test_flask_swallowed_exception_in_chain(flask_app):
    """Swallowed exceptions recorded via because.catch() appear in the chain."""
    captured = {}

    @flask_app.errorhandler(RuntimeError)
    def handle(exc):
        captured["exc"] = exc
        return {"error": str(exc)}, 500

    with flask_app.test_client() as c:
        c.get("/swallow")

    chain: ContextChain = captured["exc"].__context_chain__
    assert any(s.exc_type == "ValueError" for s in chain.swallowed)


# ── FastAPI ───────────────────────────────────────────────────────────────────
# BecauseMiddleware sits between ServerErrorMiddleware and ExceptionMiddleware
# in Starlette's stack, so its except block fires for truly unhandled exceptions.
# For exceptions with registered exception_handlers, enrich manually in the handler.

@pytest.fixture
def fastapi_app():
    app = FastAPI()
    app.add_middleware(BecauseMiddleware)

    @app.get("/ok")
    def ok():
        record(OpType.HTTP_REQUEST, duration_ms=3.0, success=True,
               method="GET", url="http://upstream/api", status_code=200)
        return {"status": "ok"}

    @app.get("/fail-unhandled")
    def fail_unhandled():
        record(OpType.DB_QUERY, duration_ms=4.0, success=True, statement="SELECT 1")
        raise RuntimeError("unhandled fastapi boom")

    @app.get("/fail-handled")
    def fail_handled():
        record(OpType.DB_QUERY, duration_ms=4.0, success=True, statement="SELECT 1")
        raise ValueError("handled error")

    @app.get("/swallow")
    def swallow():
        import because
        with because.catch(KeyError):
            raise KeyError("missing config key")
        raise RuntimeError("downstream fastapi crash")

    return app


def test_fastapi_ok_request(fastapi_app):
    client = FastAPITestClient(fastapi_app, raise_server_exceptions=False)
    resp = client.get("/ok")
    assert resp.status_code == 200


def test_fastapi_buffer_fresh_per_request(fastapi_app):
    """Buffer is reset between requests — ops don't accumulate across them."""
    from starlette.requests import Request
    from starlette.responses import JSONResponse

    captured = []

    @fastapi_app.exception_handler(ValueError)
    async def handle(request: Request, exc: ValueError):
        captured.append(len(get_context().snapshot()))
        return JSONResponse({"error": str(exc)}, status_code=422)

    client = FastAPITestClient(fastapi_app, raise_server_exceptions=False)
    client.get("/fail-handled")  # 1 DB op
    client.get("/fail-handled")  # should also be 1, not 2
    assert captured == [1, 1]


def test_fastapi_unhandled_exception_enriched(fastapi_app):
    """Unhandled exceptions are enriched by the middleware's except block."""
    client = FastAPITestClient(fastapi_app, raise_server_exceptions=True)
    with pytest.raises(RuntimeError) as exc_info:
        client.get("/fail-unhandled")

    exc = exc_info.value
    assert hasattr(exc, "__context_chain__"), "middleware should have enriched the exception"
    chain: ContextChain = exc.__context_chain__
    assert any(op.op_type == OpType.DB_QUERY for op in chain.operations)


def test_fastapi_handled_exception_manual_enrich(fastapi_app):
    """For handled exceptions, users enrich in their exception_handler."""
    from because.enrichment import enrich_with_swallowed
    from starlette.requests import Request
    from starlette.responses import JSONResponse

    captured = {}

    @fastapi_app.exception_handler(ValueError)
    async def handle(request: Request, exc: ValueError):
        enrich_with_swallowed(exc)  # explicit enrich in handler
        captured["exc"] = exc
        return JSONResponse({"error": str(exc)}, status_code=422)

    client = FastAPITestClient(fastapi_app, raise_server_exceptions=False)
    client.get("/fail-handled")

    exc = captured.get("exc")
    assert exc is not None
    assert hasattr(exc, "__context_chain__")
    chain: ContextChain = exc.__context_chain__
    assert any(op.op_type == OpType.DB_QUERY for op in chain.operations)


def test_fastapi_swallowed_in_chain(fastapi_app):
    """Swallowed exceptions appear in the chain on unhandled errors."""
    client = FastAPITestClient(fastapi_app, raise_server_exceptions=True)
    with pytest.raises(RuntimeError) as exc_info:
        client.get("/swallow")

    chain: ContextChain = exc_info.value.__context_chain__
    assert any(s.exc_type == "KeyError" for s in chain.swallowed)
