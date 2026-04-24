import pytest
import httpx

from because.buffer import OpType, get_context
from because.instruments.httpx import instrument


def _http_ops():
    return [op for op in get_context().snapshot() if op.op_type == OpType.HTTP_REQUEST]


# ── sync ─────────────────────────────────────────────────────────────────────

class MockSyncTransport(httpx.BaseTransport):
    def __init__(self, status_code=200, raise_exc=None):
        self._status_code = status_code
        self._raise_exc = raise_exc

    def handle_request(self, request):
        if self._raise_exc:
            raise self._raise_exc
        return httpx.Response(self._status_code, request=request)


@pytest.fixture
def sync_client():
    client = httpx.Client(transport=MockSyncTransport())
    instrument(client)
    return client


def test_sync_successful_request_recorded(sync_client):
    before = len(_http_ops())
    sync_client.get("http://example.com/api")
    ops = _http_ops()
    assert len(ops) == before + 1
    op = ops[-1]
    assert op.success is True
    assert op.metadata["method"] == "GET"
    assert op.metadata["url"] == "http://example.com/api"
    assert op.metadata["status_code"] == 200
    assert op.duration_ms is not None and op.duration_ms >= 0


def test_sync_non_2xx_recorded(sync_client):
    client = httpx.Client(transport=MockSyncTransport(status_code=404))
    instrument(client)
    before = len(_http_ops())
    client.get("http://example.com/missing")
    op = _http_ops()[before]
    assert op.success is True
    assert op.metadata["status_code"] == 404


def test_sync_network_error_recorded():
    client = httpx.Client(transport=MockSyncTransport(raise_exc=httpx.ConnectError("refused")))
    instrument(client)
    before = len(_http_ops())
    with pytest.raises(httpx.ConnectError):
        client.get("http://example.com/api")
    op = _http_ops()[before]
    assert op.success is False
    assert op.metadata["error"] == "ConnectError"


def test_sync_query_string_stripped(sync_client):
    before = len(_http_ops())
    sync_client.get("http://example.com/search?token=secret&q=test")
    op = _http_ops()[before]
    assert "token" not in op.metadata["url"]
    assert op.metadata["url"] == "http://example.com/search"


def test_sync_instrument_idempotent(sync_client):
    instrument(sync_client)
    before = len(_http_ops())
    sync_client.get("http://example.com/api")
    assert len(_http_ops()) == before + 1


def test_sync_multiple_requests_recorded(sync_client):
    before = len(_http_ops())
    sync_client.get("http://example.com/a")
    sync_client.post("http://example.com/b")
    sync_client.get("http://example.com/c")
    assert len(_http_ops()) == before + 3


# ── async ─────────────────────────────────────────────────────────────────────

class MockAsyncTransport(httpx.AsyncBaseTransport):
    def __init__(self, status_code=200, raise_exc=None):
        self._status_code = status_code
        self._raise_exc = raise_exc

    async def handle_async_request(self, request):
        if self._raise_exc:
            raise self._raise_exc
        return httpx.Response(self._status_code, request=request)


@pytest.fixture
def async_client():
    client = httpx.AsyncClient(transport=MockAsyncTransport())
    instrument(client)
    return client


@pytest.mark.asyncio
async def test_async_successful_request_recorded(async_client):
    before = len(_http_ops())
    async with async_client:
        await async_client.get("http://example.com/api")
    ops = _http_ops()
    assert len(ops) == before + 1
    op = ops[-1]
    assert op.success is True
    assert op.metadata["method"] == "GET"
    assert op.metadata["status_code"] == 200


@pytest.mark.asyncio
async def test_async_network_error_recorded():
    client = httpx.AsyncClient(
        transport=MockAsyncTransport(raise_exc=httpx.ConnectError("refused"))
    )
    instrument(client)
    before = len(_http_ops())
    with pytest.raises(httpx.ConnectError):
        async with client:
            await client.get("http://example.com/api")
    op = _http_ops()[before]
    assert op.success is False
    assert op.metadata["error"] == "ConnectError"


@pytest.mark.asyncio
async def test_async_query_string_stripped(async_client):
    before = len(_http_ops())
    async with async_client:
        await async_client.get("http://example.com/search?token=secret")
    op = _http_ops()[before]
    assert "token" not in op.metadata["url"]
    assert op.metadata["url"] == "http://example.com/search"


@pytest.mark.asyncio
async def test_async_multiple_requests_recorded(async_client):
    before = len(_http_ops())
    async with async_client:
        await async_client.get("http://example.com/a")
        await async_client.get("http://example.com/b")
    assert len(_http_ops()) == before + 2


def test_instrument_rejects_wrong_type():
    with pytest.raises(TypeError):
        instrument("not a client")
