import django
from django.conf import settings

if not settings.configured:
    settings.configure(
        DEBUG=True,
        DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}},
        INSTALLED_APPS=["django.contrib.contenttypes", "django.contrib.auth"],
        MIDDLEWARE=["because.integrations.django.BecauseMiddleware"],
        ROOT_URLCONF=__name__,
        DEFAULT_AUTO_FIELD="django.db.models.BigAutoField",
    )
    django.setup()

from django.http import HttpResponse
from django.test import RequestFactory
from django.urls import path

import because
from because.buffer import OpType, get_context, record
from because.enrichment import ContextChain
from because.integrations.django import BecauseMiddleware


# ── minimal views ─────────────────────────────────────────────────────────────

def view_ok(request):
    record(OpType.DB_QUERY, duration_ms=3.0, success=True, statement="SELECT 1")
    return HttpResponse("ok")


def view_fail(request):
    record(OpType.DB_QUERY, duration_ms=5.0, success=True, statement="SELECT 1")
    raise RuntimeError("django boom")


def view_swallow(request):
    with because.catch(KeyError):
        raise KeyError("missing setting")
    raise RuntimeError("downstream django crash")


urlpatterns = [
    path("ok/", view_ok),
    path("fail/", view_fail),
    path("swallow/", view_swallow),
]


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_middleware(view):
    return BecauseMiddleware(view)


# ── tests ─────────────────────────────────────────────────────────────────────

def test_buffer_fresh_per_request():
    """Buffer is reset between requests — ops don't accumulate."""
    lengths = []
    factory = RequestFactory()

    def counting_view(request):
        lengths.append(len(get_context().snapshot()))
        return HttpResponse("ok")

    mw = _make_middleware(counting_view)

    # Each request starts with an empty buffer
    mw(factory.get("/"))
    mw(factory.get("/"))
    assert lengths == [0, 0]


def test_buffer_contains_ops_from_view():
    """Ops recorded inside a view appear in the buffer during that request."""
    factory = RequestFactory()
    lengths = []

    def recording_view(request):
        record(OpType.DB_QUERY, duration_ms=2.0, success=True, statement="SELECT 1")
        record(OpType.DB_QUERY, duration_ms=2.0, success=True, statement="SELECT 2")
        lengths.append(len(get_context().snapshot()))
        return HttpResponse("ok")

    mw = _make_middleware(recording_view)
    mw(factory.get("/"))
    assert lengths == [2]


def test_exception_enriched_by_process_exception():
    """process_exception attaches __context_chain__ before error handlers run."""
    factory = RequestFactory()
    captured = {}

    def failing_view(request):
        record(OpType.DB_QUERY, duration_ms=4.0, success=True, statement="SELECT 1")
        raise RuntimeError("django boom")

    def error_middleware(get_response):
        def middleware(request):
            try:
                return get_response(request)
            except RuntimeError as exc:
                captured["exc"] = exc
                return HttpResponse(status=500)
        return middleware

    # Stack: error_middleware → BecauseMiddleware → failing_view
    inner = _make_middleware(failing_view)

    def wrapped(request):
        try:
            return inner(request)
        except RuntimeError as exc:
            # simulate process_exception having been called
            captured["exc"] = exc
            return HttpResponse(status=500)

    # Call process_exception manually (as Django would)
    request = factory.get("/")
    mw = BecauseMiddleware(failing_view)
    try:
        mw(request)
    except RuntimeError as exc:
        mw.process_exception(request, exc)
        captured["exc"] = exc

    exc = captured.get("exc")
    assert exc is not None
    assert hasattr(exc, "__context_chain__"), "__context_chain__ should be attached"
    chain: ContextChain = exc.__context_chain__
    assert any(op.op_type == OpType.DB_QUERY for op in chain.operations)


def test_swallowed_exception_in_chain():
    """Swallowed exceptions via because.catch() appear in the context chain."""
    factory = RequestFactory()
    captured = {}

    def swallowing_view(request):
        with because.catch(KeyError):
            raise KeyError("missing setting")
        raise RuntimeError("downstream")

    request = factory.get("/")
    mw = BecauseMiddleware(swallowing_view)
    try:
        mw(request)
    except RuntimeError as exc:
        mw.process_exception(request, exc)
        captured["exc"] = exc

    chain: ContextChain = captured["exc"].__context_chain__
    assert any(s.exc_type == "KeyError" for s in chain.swallowed)


def test_buffer_reset_after_exception():
    """Buffer token is reset even when an exception propagates."""
    factory = RequestFactory()

    def failing_view(request):
        raise RuntimeError("boom")

    mw = BecauseMiddleware(failing_view)
    try:
        mw(factory.get("/"))
    except RuntimeError:
        pass

    # Next request should get a fresh buffer
    lengths = []

    def counting_view(request):
        lengths.append(len(get_context().snapshot()))
        return HttpResponse("ok")

    BecauseMiddleware(counting_view)(factory.get("/"))
    assert lengths == [0]
