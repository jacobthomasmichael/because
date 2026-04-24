from __future__ import annotations

import time
from typing import Any

from because.buffer import OpType, record


def instrument(client: Any) -> None:
    """Attach because instrumentation to a redis.Redis or redis.asyncio.Redis client."""
    if getattr(client, "_because_instrumented", False):
        return

    try:
        import redis as redis_mod
    except ImportError:
        raise ImportError("redis-py is required: pip install redis")

    is_async = _is_async_client(client, redis_mod)
    client._because_instrumented = True

    if is_async:
        _wrap_async(client)
    else:
        _wrap_sync(client)


def _is_async_client(client: Any, redis_mod: Any) -> bool:
    try:
        return isinstance(client, redis_mod.asyncio.Redis)
    except AttributeError:
        return False


def _wrap_sync(client: Any) -> None:
    original = client.execute_command

    def wrapped(command, *args, **options):
        start = time.monotonic()
        try:
            result = original(command, *args, **options)
            duration_ms = (time.monotonic() - start) * 1000
            record(
                OpType.CACHE,
                duration_ms=duration_ms,
                success=True,
                command=command,
                key=str(args[0]) if args else None,
            )
            return result
        except Exception as exc:
            duration_ms = (time.monotonic() - start) * 1000
            record(
                OpType.CACHE,
                duration_ms=duration_ms,
                success=False,
                command=command,
                key=str(args[0]) if args else None,
                error=type(exc).__name__,
            )
            raise

    client.execute_command = wrapped


def _wrap_async(client: Any) -> None:
    original = client.execute_command

    async def wrapped(command, *args, **options):
        start = time.monotonic()
        try:
            result = await original(command, *args, **options)
            duration_ms = (time.monotonic() - start) * 1000
            record(
                OpType.CACHE,
                duration_ms=duration_ms,
                success=True,
                command=command,
                key=str(args[0]) if args else None,
            )
            return result
        except Exception as exc:
            duration_ms = (time.monotonic() - start) * 1000
            record(
                OpType.CACHE,
                duration_ms=duration_ms,
                success=False,
                command=command,
                key=str(args[0]) if args else None,
                error=type(exc).__name__,
            )
            raise

    client.execute_command = wrapped
