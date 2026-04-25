"""Tests for the LLM explainer — all mocked, no real API calls."""
import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import because
from because.enrichment import ContextChain, SwallowedExc
from because.explainer import (
    AnthropicProvider,
    Explanation,
    GeminiProvider,
    LLMProvider,
    OpenAIProvider,
    XAIProvider,
    _parse_response,
    build_prompt,
    configure_llm,
    explain,
    explain_async,
)
from because.buffer import Op, OpType
from because.patterns.base import PatternMatch
import time


# ── fixture ───────────────────────────────────────────────────────────────────

def _make_exc():
    ops = [
        Op(OpType.DB_QUERY, timestamp=time.monotonic(), duration_ms=5.0,
           success=False, metadata={"statement": "SELECT 1", "error": "OperationalError"}),
    ]
    swallowed = [SwallowedExc("TimeoutError", "pool timeout", time.monotonic())]
    matches = [PatternMatch("pool_exhaustion", "likely_cause", "Pool exhausted.", ["QueuePool limit"])]
    exc = RuntimeError("connection refused")
    exc.__context_chain__ = ContextChain(operations=ops, swallowed=swallowed, pattern_matches=matches)
    return exc


_GOOD_RESPONSE = json.dumps({
    "root_cause": "The database connection pool was exhausted under load.",
    "contributing_factors": ["Pool size too small", "Slow queries holding connections"],
    "suggested_fix": "Increase pool_size or add connection timeout handling.",
    "confidence": "high",
})


# ── Explanation dataclass ─────────────────────────────────────────────────────

def test_explanation_str_includes_root_cause():
    e = Explanation(root_cause="pool exhausted", confidence="high",
                    suggested_fix="increase pool size")
    assert "pool exhausted" in str(e)
    assert "high" in str(e)
    assert "increase pool size" in str(e)


def test_explanation_str_includes_contributing_factors():
    e = Explanation(root_cause="x", contributing_factors=["factor A", "factor B"])
    output = str(e)
    assert "factor A" in output
    assert "factor B" in output


# ── _parse_response ───────────────────────────────────────────────────────────

def test_parse_valid_json():
    result = _parse_response(_GOOD_RESPONSE)
    assert result.root_cause == "The database connection pool was exhausted under load."
    assert result.confidence == "high"
    assert len(result.contributing_factors) == 2
    assert "pool_size" in result.suggested_fix


def test_parse_strips_markdown_fences():
    wrapped = f"```json\n{_GOOD_RESPONSE}\n```"
    result = _parse_response(wrapped)
    assert result.confidence == "high"


def test_parse_malformed_json_falls_back_gracefully():
    result = _parse_response("sorry I can't help with that")
    assert result.root_cause  # not empty
    assert result.confidence == "low"
    assert result.raw_response == "sorry I can't help with that"


def test_parse_empty_string():
    result = _parse_response("")
    assert result.root_cause  # not empty, graceful fallback


# ── build_prompt ──────────────────────────────────────────────────────────────

def test_build_prompt_includes_exception():
    exc = _make_exc()
    prompt = build_prompt(exc)
    assert "RuntimeError" in prompt
    assert "connection refused" in prompt


def test_build_prompt_includes_pattern():
    exc = _make_exc()
    prompt = build_prompt(exc)
    assert "pool_exhaustion" in prompt


def test_build_prompt_includes_operations():
    exc = _make_exc()
    prompt = build_prompt(exc)
    assert "db_query" in prompt


def test_build_prompt_includes_swallowed():
    exc = _make_exc()
    prompt = build_prompt(exc)
    assert "TimeoutError" in prompt


def test_build_prompt_no_chain():
    exc = RuntimeError("bare exception")
    prompt = build_prompt(exc)
    assert "RuntimeError" in prompt
    assert "No because context" in prompt


def test_build_prompt_requests_json():
    exc = _make_exc()
    prompt = build_prompt(exc)
    assert "root_cause" in prompt
    assert "confidence" in prompt


# ── LLMProvider protocol ──────────────────────────────────────────────────────

def test_anthropic_provider_implements_protocol():
    provider = AnthropicProvider(api_key="test-key")
    assert isinstance(provider, LLMProvider)


def test_openai_provider_implements_protocol():
    provider = OpenAIProvider(api_key="test-key")
    assert isinstance(provider, LLMProvider)


def test_xai_provider_implements_protocol():
    provider = XAIProvider(api_key="test-key")
    assert isinstance(provider, LLMProvider)


def test_gemini_provider_implements_protocol():
    provider = GeminiProvider(api_key="test-key")
    assert isinstance(provider, LLMProvider)


def test_custom_provider_implements_protocol():
    class MyProvider:
        async def complete(self, prompt: str) -> str:
            return _GOOD_RESPONSE

    assert isinstance(MyProvider(), LLMProvider)


# ── explain_async ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_explain_async_returns_explanation():
    mock_provider = AsyncMock(spec=LLMProvider)
    mock_provider.complete.return_value = _GOOD_RESPONSE

    exc = _make_exc()
    result = await explain_async(exc, provider=mock_provider)

    assert isinstance(result, Explanation)
    assert result.confidence == "high"
    mock_provider.complete.assert_awaited_once()


@pytest.mark.asyncio
async def test_explain_async_attaches_to_exception():
    mock_provider = AsyncMock(spec=LLMProvider)
    mock_provider.complete.return_value = _GOOD_RESPONSE

    exc = _make_exc()
    await explain_async(exc, provider=mock_provider)

    assert hasattr(exc, "__llm_explanation__")
    assert exc.__llm_explanation__.confidence == "high"


@pytest.mark.asyncio
async def test_explain_async_prompt_sent_to_provider():
    mock_provider = AsyncMock(spec=LLMProvider)
    mock_provider.complete.return_value = _GOOD_RESPONSE

    exc = _make_exc()
    await explain_async(exc, provider=mock_provider)

    call_args = mock_provider.complete.call_args
    prompt = call_args.args[0]
    assert "RuntimeError" in prompt
    assert "pool_exhaustion" in prompt


@pytest.mark.asyncio
async def test_explain_async_no_provider_raises():
    import because.explainer as explainer_mod
    original = explainer_mod._default_provider
    explainer_mod._default_provider = None
    try:
        with pytest.raises(RuntimeError, match="No LLM provider"):
            await explain_async(RuntimeError("x"))
    finally:
        explainer_mod._default_provider = original


# ── explain (sync) ────────────────────────────────────────────────────────────

def test_explain_sync_returns_explanation():
    mock_provider = AsyncMock(spec=LLMProvider)
    mock_provider.complete.return_value = _GOOD_RESPONSE

    exc = _make_exc()
    result = explain(exc, provider=mock_provider)

    assert isinstance(result, Explanation)
    assert result.root_cause


# ── configure_llm ─────────────────────────────────────────────────────────────

def test_configure_llm_sets_anthropic_default():
    import because.explainer as explainer_mod
    original = explainer_mod._default_provider
    try:
        configure_llm(api_key="sk-ant-test", provider="anthropic")
        assert isinstance(explainer_mod._default_provider, AnthropicProvider)
    finally:
        explainer_mod._default_provider = original


def test_configure_llm_sets_openai_default():
    import because.explainer as explainer_mod
    original = explainer_mod._default_provider
    try:
        configure_llm(api_key="sk-openai-test", provider="openai")
        assert isinstance(explainer_mod._default_provider, OpenAIProvider)
    finally:
        explainer_mod._default_provider = original


def test_configure_llm_sets_xai_default():
    import because.explainer as explainer_mod
    original = explainer_mod._default_provider
    try:
        configure_llm(api_key="xai-test", provider="xai")
        assert isinstance(explainer_mod._default_provider, XAIProvider)
    finally:
        explainer_mod._default_provider = original


def test_configure_llm_sets_gemini_default():
    import because.explainer as explainer_mod
    original = explainer_mod._default_provider
    try:
        configure_llm(api_key="gemini-test", provider="gemini")
        assert isinstance(explainer_mod._default_provider, GeminiProvider)
    finally:
        explainer_mod._default_provider = original


def test_configure_llm_unknown_provider_raises():
    with pytest.raises(ValueError, match="Unknown provider"):
        configure_llm(api_key="key", provider="cohere")


def test_configure_llm_model_override():
    import because.explainer as explainer_mod
    original = explainer_mod._default_provider
    try:
        configure_llm(api_key="sk-ant-test", model="claude-opus-4-7")
        assert explainer_mod._default_provider.model == "claude-opus-4-7"
    finally:
        explainer_mod._default_provider = original


# ── public API surface ────────────────────────────────────────────────────────

def test_public_api_exports():
    assert hasattr(because, "configure_llm")
    assert hasattr(because, "explain")
    assert hasattr(because, "explain_async")
    assert hasattr(because, "build_prompt")
    assert hasattr(because, "Explanation")
    assert hasattr(because, "AnthropicProvider")
    assert hasattr(because, "OpenAIProvider")
    assert hasattr(because, "XAIProvider")
    assert hasattr(because, "GeminiProvider")
