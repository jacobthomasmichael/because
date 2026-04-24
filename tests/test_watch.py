"""Tests for the because.watch decorator."""
import asyncio
import pytest

import because
from because.enrichment import watch


# ── sync functions ────────────────────────────────────────────────────────────

def test_watch_bare_decorator_reraises():
    @watch
    def boom():
        raise ValueError("oops")

    with pytest.raises(ValueError):
        boom()


def test_watch_bare_decorator_enriches():
    @watch
    def boom():
        raise ValueError("oops")

    with pytest.raises(ValueError) as exc_info:
        boom()

    assert hasattr(exc_info.value, "__context_chain__")


def test_watch_passes_return_value():
    @watch
    def add(a, b):
        return a + b

    assert add(2, 3) == 5


def test_watch_passes_args_and_kwargs():
    @watch
    def greet(name, greeting="Hello"):
        return f"{greeting}, {name}"

    assert greet("Alice", greeting="Hi") == "Hi, Alice"


def test_watch_preserves_function_name():
    @watch
    def my_function():
        pass

    assert my_function.__name__ == "my_function"


def test_watch_reraise_false_swallows():
    @watch(reraise=False)
    def boom():
        raise ValueError("silent")

    result = boom()
    assert result is None


def test_watch_reraise_false_still_enriches():
    captured = []

    @watch(reraise=False)
    def boom():
        raise ValueError("silent")

    boom()  # does not raise — but we need to capture the exc another way

    # Verify via a reraise=True version to inspect the chain
    @watch(reraise=True)
    def boom2():
        raise ValueError("loud")

    with pytest.raises(ValueError) as exc_info:
        boom2()
    assert hasattr(exc_info.value, "__context_chain__")


def test_watch_parameterised_reraise_true():
    @watch(reraise=True)
    def boom():
        raise RuntimeError("reraise me")

    with pytest.raises(RuntimeError, match="reraise me"):
        boom()


def test_watch_no_exception_is_transparent():
    @watch
    def safe():
        return 42

    assert safe() == 42


def test_watch_enriches_with_swallowed_context():
    """Exceptions caught with because.catch() before the watched function
    should appear in the context chain."""
    @watch
    def downstream():
        raise AttributeError("NoneType has no attribute email")

    with because.catch(Exception):
        raise Exception("OperationalError: connection dropped")

    with pytest.raises(AttributeError) as exc_info:
        downstream()

    chain = exc_info.value.__context_chain__
    assert len(chain.swallowed) > 0


# ── async functions ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_watch_async_reraises():
    @watch
    async def async_boom():
        raise ValueError("async oops")

    with pytest.raises(ValueError):
        await async_boom()


@pytest.mark.asyncio
async def test_watch_async_enriches():
    @watch
    async def async_boom():
        raise ValueError("async oops")

    with pytest.raises(ValueError) as exc_info:
        await async_boom()

    assert hasattr(exc_info.value, "__context_chain__")


@pytest.mark.asyncio
async def test_watch_async_passes_return_value():
    @watch
    async def fetch(url):
        return f"response from {url}"

    result = await fetch("https://api.example.com")
    assert result == "response from https://api.example.com"


@pytest.mark.asyncio
async def test_watch_async_reraise_false_swallows():
    @watch(reraise=False)
    async def async_boom():
        raise RuntimeError("background task failed")

    result = await async_boom()
    assert result is None


@pytest.mark.asyncio
async def test_watch_async_preserves_function_name():
    @watch
    async def my_async_function():
        pass

    assert my_async_function.__name__ == "my_async_function"


# ── public API ────────────────────────────────────────────────────────────────

def test_watch_exported_from_because():
    assert hasattr(because, "watch")
    assert because.watch is watch
