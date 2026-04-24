"""Tests for the gRPC instrument — fully mocked, no real gRPC server."""
import time
from unittest.mock import MagicMock, AsyncMock, patch
import pytest

from because.buffer import OpType, get_context, _ctx_buffer, RingBuffer
from because.instruments.grpc import (
    _BecauseSyncChannel,
    _BecauseAsyncChannel,
    _WrappedCallable,
    _WrappedAsyncCallable,
    instrument,
)


# ── fixtures ──────────────────────────────────────────────────────────────────

def _grpc_ops():
    return [
        op for op in get_context().snapshot()
        if op.op_type == OpType.HTTP_REQUEST
        and op.metadata.get("kind") == "grpc"
    ]


def _mock_channel(target: str = "localhost:50051") -> MagicMock:
    channel = MagicMock()
    channel._channel.target.return_value = target.encode()
    return channel


# ── instrument() type checking ────────────────────────────────────────────────

def test_instrument_raises_on_wrong_type():
    with patch.dict("sys.modules", {"grpc": MagicMock()}):
        import grpc
        grpc.Channel = type("Channel", (), {})
        with pytest.raises(TypeError):
            instrument("not a channel")


def test_instrument_raises_import_error_without_grpc():
    with patch.dict("sys.modules", {"grpc": None}):
        with pytest.raises(ImportError, match="grpc package required"):
            instrument(MagicMock())


# ── sync channel ──────────────────────────────────────────────────────────────

def test_sync_channel_records_successful_call():
    token = _ctx_buffer.set(RingBuffer())
    try:
        inner_callable = MagicMock(return_value="response")
        channel = _mock_channel()
        channel.unary_unary.return_value = inner_callable

        wrapped = _BecauseSyncChannel(channel)
        stub = wrapped.unary_unary("/mypackage.MyService/GetUser")
        result = stub(request=MagicMock())

        ops = _grpc_ops()
        assert len(ops) == 1
        assert ops[0].success is True
        assert ops[0].metadata["kind"] == "grpc"
        assert "/mypackage.MyService/GetUser" in ops[0].metadata["url"]
    finally:
        _ctx_buffer.reset(token)


def test_sync_channel_records_failed_call():
    token = _ctx_buffer.set(RingBuffer())
    try:
        inner_callable = MagicMock(side_effect=RuntimeError("deadline exceeded"))
        channel = _mock_channel()
        channel.unary_unary.return_value = inner_callable

        wrapped = _BecauseSyncChannel(channel)
        stub = wrapped.unary_unary("/mypackage.MyService/GetUser")

        with pytest.raises(RuntimeError):
            stub(request=MagicMock())

        ops = _grpc_ops()
        assert len(ops) == 1
        assert ops[0].success is False
        assert ops[0].metadata["error"] == "RuntimeError"
    finally:
        _ctx_buffer.reset(token)


def test_sync_channel_records_duration():
    token = _ctx_buffer.set(RingBuffer())
    try:
        def slow_call(*args, **kwargs):
            time.sleep(0.01)
            return "ok"

        channel = _mock_channel()
        channel.unary_unary.return_value = slow_call

        wrapped = _BecauseSyncChannel(channel)
        stub = wrapped.unary_unary("/svc/Method")
        stub()

        ops = _grpc_ops()
        assert ops[-1].duration_ms >= 10
    finally:
        _ctx_buffer.reset(token)


def test_sync_channel_delegates_unknown_attrs():
    channel = _mock_channel()
    channel.close = MagicMock()
    wrapped = _BecauseSyncChannel(channel)
    wrapped.close()
    channel.close.assert_called_once()


def test_sync_channel_wraps_all_rpc_types():
    channel = _mock_channel()
    wrapped = _BecauseSyncChannel(channel)
    for method in ("unary_unary", "unary_stream", "stream_unary", "stream_stream"):
        stub = getattr(wrapped, method)("/svc/Method")
        assert isinstance(stub, _WrappedCallable)


# ── async channel ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_async_channel_records_successful_call():
    token = _ctx_buffer.set(RingBuffer())
    try:
        inner_callable = AsyncMock(return_value="response")
        channel = _mock_channel()
        channel.unary_unary.return_value = inner_callable

        wrapped = _BecauseAsyncChannel(channel)
        stub = wrapped.unary_unary("/mypackage.MyService/GetUser")
        await stub(request=MagicMock())

        ops = _grpc_ops()
        assert len(ops) == 1
        assert ops[0].success is True
        assert ops[0].metadata["kind"] == "grpc"
    finally:
        _ctx_buffer.reset(token)


@pytest.mark.asyncio
async def test_async_channel_records_failed_call():
    token = _ctx_buffer.set(RingBuffer())
    try:
        inner_callable = AsyncMock(side_effect=TimeoutError("deadline exceeded"))
        channel = _mock_channel()
        channel.unary_unary.return_value = inner_callable

        wrapped = _BecauseAsyncChannel(channel)
        stub = wrapped.unary_unary("/svc/Method")

        with pytest.raises(TimeoutError):
            await stub()

        ops = _grpc_ops()
        assert ops[0].success is False
        assert ops[0].metadata["error"] == "TimeoutError"
    finally:
        _ctx_buffer.reset(token)


@pytest.mark.asyncio
async def test_async_channel_wraps_all_rpc_types():
    channel = _mock_channel()
    wrapped = _BecauseAsyncChannel(channel)
    for method in ("unary_unary", "unary_stream", "stream_unary", "stream_stream"):
        stub = getattr(wrapped, method)("/svc/Method")
        assert isinstance(stub, _WrappedAsyncCallable)


def test_async_channel_delegates_unknown_attrs():
    channel = _mock_channel()
    channel.close = MagicMock()
    wrapped = _BecauseAsyncChannel(channel)
    wrapped.close()
    channel.close.assert_called_once()


# ── url formatting ────────────────────────────────────────────────────────────

def test_grpc_url_includes_target_and_method():
    token = _ctx_buffer.set(RingBuffer())
    try:
        inner_callable = MagicMock(return_value="ok")
        channel = _mock_channel(target="payments.internal:50051")
        channel.unary_unary.return_value = inner_callable

        wrapped = _BecauseSyncChannel(channel)
        stub = wrapped.unary_unary("/payments.PaymentsService/Charge")
        stub()

        ops = _grpc_ops()
        assert "payments.internal:50051" in ops[0].metadata["url"]
        assert "/payments.PaymentsService/Charge" in ops[0].metadata["url"]
    finally:
        _ctx_buffer.reset(token)
