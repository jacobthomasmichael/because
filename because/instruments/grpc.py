from __future__ import annotations

import time
from typing import Any

from because.buffer import OpType, record


def instrument(channel: Any) -> Any:
    """Wrap a gRPC channel with because instrumentation.

    Returns a new channel that records every RPC call into the ring buffer.
    Use the returned channel in place of the original.

    Works with both grpc.Channel and grpc.aio.Channel::

        import grpc
        from because.instruments.grpc import instrument

        channel = grpc.insecure_channel("localhost:50051")
        channel = instrument(channel)
        stub = MyServiceStub(channel)

    Install the extra::

        pip install "because-py[grpc]"
    """
    try:
        import grpc
    except ImportError:
        raise ImportError(
            "grpc package required: pip install \"because-py[grpc]\""
        )

    if isinstance(channel, grpc.Channel):
        return _BecauseSyncChannel(channel)

    # grpc.aio is only available in newer grpc versions
    try:
        import grpc.aio
        if isinstance(channel, grpc.aio.Channel):
            return _BecauseAsyncChannel(channel)
    except (ImportError, AttributeError):
        pass

    raise TypeError(f"Expected grpc.Channel or grpc.aio.Channel, got {type(channel)}")


def _rpc_url(channel: Any, method: str) -> str:
    try:
        target = channel._channel.target().decode()
        return f"grpc://{target}{method}"
    except Exception:
        return method


class _BecauseInterceptor:
    """Shared logic for recording gRPC calls."""

    def _record_success(self, method: str, target: str, duration_ms: float) -> None:
        record(
            OpType.HTTP_REQUEST,
            duration_ms=duration_ms,
            success=True,
            kind="grpc",
            method=method,
            url=f"grpc://{target}{method}",
        )

    def _record_failure(self, method: str, target: str, duration_ms: float, error: str) -> None:
        record(
            OpType.HTTP_REQUEST,
            duration_ms=duration_ms,
            success=False,
            kind="grpc",
            method=method,
            url=f"grpc://{target}{method}",
            error=error,
        )

    def _target(self, channel: Any) -> str:
        try:
            return channel._channel.target().decode()
        except Exception:
            return "unknown"


class _BecauseSyncChannel(_BecauseInterceptor):
    def __init__(self, channel: Any) -> None:
        self._channel = channel

    def unary_unary(self, method: str, *args: Any, **kwargs: Any) -> Any:
        inner = self._channel.unary_unary(method, *args, **kwargs)
        return _WrappedCallable(inner, method, self._target(self._channel), self)

    def unary_stream(self, method: str, *args: Any, **kwargs: Any) -> Any:
        inner = self._channel.unary_stream(method, *args, **kwargs)
        return _WrappedCallable(inner, method, self._target(self._channel), self)

    def stream_unary(self, method: str, *args: Any, **kwargs: Any) -> Any:
        inner = self._channel.stream_unary(method, *args, **kwargs)
        return _WrappedCallable(inner, method, self._target(self._channel), self)

    def stream_stream(self, method: str, *args: Any, **kwargs: Any) -> Any:
        inner = self._channel.stream_stream(method, *args, **kwargs)
        return _WrappedCallable(inner, method, self._target(self._channel), self)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._channel, name)


class _WrappedCallable(_BecauseInterceptor):
    def __init__(self, inner: Any, method: str, target: str, recorder: _BecauseInterceptor) -> None:
        self._inner = inner
        self._method = method
        self._target = target
        self._recorder = recorder

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        start = time.monotonic()
        try:
            result = self._inner(*args, **kwargs)
            duration_ms = (time.monotonic() - start) * 1000
            self._recorder._record_success(self._method, self._target, duration_ms)
            return result
        except Exception as exc:
            duration_ms = (time.monotonic() - start) * 1000
            self._recorder._record_failure(
                self._method, self._target, duration_ms, type(exc).__name__
            )
            raise

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


class _BecauseAsyncChannel(_BecauseInterceptor):
    def __init__(self, channel: Any) -> None:
        self._channel = channel

    def unary_unary(self, method: str, *args: Any, **kwargs: Any) -> Any:
        inner = self._channel.unary_unary(method, *args, **kwargs)
        return _WrappedAsyncCallable(inner, method, self._target(self._channel), self)

    def unary_stream(self, method: str, *args: Any, **kwargs: Any) -> Any:
        inner = self._channel.unary_stream(method, *args, **kwargs)
        return _WrappedAsyncCallable(inner, method, self._target(self._channel), self)

    def stream_unary(self, method: str, *args: Any, **kwargs: Any) -> Any:
        inner = self._channel.stream_unary(method, *args, **kwargs)
        return _WrappedAsyncCallable(inner, method, self._target(self._channel), self)

    def stream_stream(self, method: str, *args: Any, **kwargs: Any) -> Any:
        inner = self._channel.stream_stream(method, *args, **kwargs)
        return _WrappedAsyncCallable(inner, method, self._target(self._channel), self)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._channel, name)


class _WrappedAsyncCallable(_BecauseInterceptor):
    def __init__(self, inner: Any, method: str, target: str, recorder: _BecauseInterceptor) -> None:
        self._inner = inner
        self._method = method
        self._target = target
        self._recorder = recorder

    async def __call__(self, *args: Any, **kwargs: Any) -> Any:
        start = time.monotonic()
        try:
            result = await self._inner(*args, **kwargs)
            duration_ms = (time.monotonic() - start) * 1000
            self._recorder._record_success(self._method, self._target, duration_ms)
            return result
        except Exception as exc:
            duration_ms = (time.monotonic() - start) * 1000
            self._recorder._record_failure(
                self._method, self._target, duration_ms, type(exc).__name__
            )
            raise

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)
