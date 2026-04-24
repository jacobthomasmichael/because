import pytest
import fakeredis
import fakeredis.aioredis

from because.buffer import OpType, get_context
from because.instruments.redis import instrument


def _cache_ops():
    return [op for op in get_context().snapshot() if op.op_type == OpType.CACHE]


# ── sync ─────────────────────────────────────────────────────────────────────

@pytest.fixture
def client():
    c = fakeredis.FakeRedis()
    instrument(c)
    return c


def test_sync_set_recorded(client):
    before = len(_cache_ops())
    client.set("foo", "bar")
    ops = _cache_ops()
    assert len(ops) == before + 1
    op = ops[-1]
    assert op.success is True
    assert op.metadata["command"] == "SET"
    assert op.metadata["key"] == "foo"
    assert op.duration_ms is not None and op.duration_ms >= 0


def test_sync_get_recorded(client):
    client.set("x", "1")
    before = len(_cache_ops())
    client.get("x")
    op = _cache_ops()[before]
    assert op.metadata["command"] == "GET"
    assert op.metadata["key"] == "x"


def test_sync_error_recorded(client):
    before = len(_cache_ops())
    # LPUSH on a string key → WRONGTYPE error
    client.set("strkey", "val")
    with pytest.raises(Exception):
        client.lpush("strkey", "item")
    op = _cache_ops()[before + 1]
    assert op.success is False
    assert "error" in op.metadata


def test_sync_multiple_commands_recorded(client):
    before = len(_cache_ops())
    client.set("a", 1)
    client.incr("a")
    client.get("a")
    assert len(_cache_ops()) == before + 3


def test_sync_instrument_idempotent(client):
    instrument(client)
    before = len(_cache_ops())
    client.set("k", "v")
    assert len(_cache_ops()) == before + 1


def test_sync_keyless_command_recorded(client):
    before = len(_cache_ops())
    client.ping()
    op = _cache_ops()[before]
    assert op.metadata["command"] == "PING"
    assert op.metadata["key"] is None


# ── async ─────────────────────────────────────────────────────────────────────

@pytest.fixture
def async_client():
    c = fakeredis.aioredis.FakeRedis()
    instrument(c)
    return c


@pytest.mark.asyncio
async def test_async_set_recorded(async_client):
    before = len(_cache_ops())
    await async_client.set("foo", "bar")
    ops = _cache_ops()
    assert len(ops) == before + 1
    op = ops[-1]
    assert op.success is True
    assert op.metadata["command"] == "SET"
    assert op.metadata["key"] == "foo"


@pytest.mark.asyncio
async def test_async_get_recorded(async_client):
    await async_client.set("y", "2")
    before = len(_cache_ops())
    await async_client.get("y")
    op = _cache_ops()[before]
    assert op.metadata["command"] == "GET"


@pytest.mark.asyncio
async def test_async_multiple_commands_recorded(async_client):
    before = len(_cache_ops())
    await async_client.set("a", 1)
    await async_client.incr("a")
    await async_client.get("a")
    assert len(_cache_ops()) == before + 3


@pytest.mark.asyncio
async def test_async_instrument_idempotent(async_client):
    instrument(async_client)
    before = len(_cache_ops())
    await async_client.set("k", "v")
    assert len(_cache_ops()) == before + 1
