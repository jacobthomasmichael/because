"""
LLM-based exception explainer for ``because`` (v0.2).

Provides plain-English root cause analysis by sending the ContextChain
to an LLM. Designed as an optional, async, deferred enricher with BYO key.

Install the extra::

    pip install "because-py[llm]"         # Anthropic (default)
    pip install "because-py[llm,openai]"  # + OpenAI support

Basic usage::

    import because

    because.configure_llm(api_key="sk-ant-...")

    try:
        risky_operation()
    except Exception as exc:
        because.enrich_with_swallowed(exc)
        explanation = await because.explain_async(exc)
        print(explanation.root_cause)
        print(explanation.suggested_fix)

Sync usage (blocks — avoid in async contexts)::

    explanation = because.explain(exc)
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

# ── data types ────────────────────────────────────────────────────────────────

@dataclass
class Explanation:
    root_cause: str
    contributing_factors: list[str] = field(default_factory=list)
    suggested_fix: str = ""
    confidence: str = "low"  # "low" | "medium" | "high"
    raw_response: str = ""

    def __str__(self) -> str:
        lines = [f"Root cause ({self.confidence} confidence): {self.root_cause}"]
        if self.contributing_factors:
            lines.append("Contributing factors:")
            for f in self.contributing_factors:
                lines.append(f"  • {f}")
        if self.suggested_fix:
            lines.append(f"Suggested fix: {self.suggested_fix}")
        return "\n".join(lines)


# ── provider protocol ─────────────────────────────────────────────────────────

@runtime_checkable
class LLMProvider(Protocol):
    async def complete(self, prompt: str) -> str:
        """Send prompt, return the model's text response."""
        ...


# ── built-in providers ────────────────────────────────────────────────────────

class AnthropicProvider:
    """Uses the Anthropic API (claude-sonnet-4-6 by default)."""

    def __init__(
        self,
        api_key: str,
        model: str = "claude-sonnet-4-6",
        max_tokens: int = 1024,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.max_tokens = max_tokens

    async def complete(self, prompt: str) -> str:
        try:
            import anthropic
        except ImportError:
            raise ImportError(
                "anthropic package required: pip install \"because-py[llm]\""
            )
        client = anthropic.AsyncAnthropic(api_key=self.api_key)
        message = await client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        return message.content[0].text


class OpenAIProvider:
    """Uses the OpenAI API (gpt-4o by default)."""

    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4o",
        max_tokens: int = 1024,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.max_tokens = max_tokens

    async def complete(self, prompt: str) -> str:
        try:
            import openai
        except ImportError:
            raise ImportError(
                "openai package required: pip install \"because-py[openai]\""
            )
        client = openai.AsyncOpenAI(api_key=self.api_key)
        response = await client.chat.completions.create(
            model=self.model,
            max_tokens=self.max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.choices[0].message.content or ""


# ── global configuration ──────────────────────────────────────────────────────

_default_provider: LLMProvider | None = None


def configure_llm(
    api_key: str,
    model: str | None = None,
    provider: str = "anthropic",
) -> None:
    """Configure the default LLM provider used by explain() / explain_async().

    Args:
        api_key: Your API key (Anthropic or OpenAI).
        model: Model name override. Defaults to claude-sonnet-4-6 or gpt-4o.
        provider: "anthropic" (default) or "openai".
    """
    global _default_provider
    if provider == "anthropic":
        kwargs = {"api_key": api_key}
        if model:
            kwargs["model"] = model
        _default_provider = AnthropicProvider(**kwargs)
    elif provider == "openai":
        kwargs = {"api_key": api_key}
        if model:
            kwargs["model"] = model
        _default_provider = OpenAIProvider(**kwargs)
    else:
        raise ValueError(f"Unknown provider: {provider!r}. Use 'anthropic' or 'openai'.")


# ── prompt builder ────────────────────────────────────────────────────────────

def build_prompt(exc: BaseException) -> str:
    from because.enrichment import ContextChain
    from because.integrations.serialize import chain_to_dict

    chain: ContextChain | None = getattr(exc, "__context_chain__", None)

    exc_block = f"{type(exc).__name__}: {exc}"

    if chain is None:
        context_block = "No because context available."
    else:
        data = chain_to_dict(chain)
        context_block = json.dumps(data, indent=2, default=str)

    return f"""\
You are a senior engineer helping diagnose a Python exception. The exception \
occurred in a production application. Use the structured context below (captured \
by the `because` library) to explain the most likely root cause.

Be honest about uncertainty. Use hedged language like "likely", "possibly", \
"may be caused by". Do NOT invent causes that aren't supported by the evidence.

## Exception

{exc_block}

## Context captured before the exception

{context_block}

## Instructions

Respond with valid JSON matching this schema exactly — no markdown, no prose outside the JSON:

{{
  "root_cause": "<one sentence explaining the most likely cause>",
  "contributing_factors": ["<factor 1>", "<factor 2>"],
  "suggested_fix": "<one concrete action the engineer should take first>",
  "confidence": "<low|medium|high>"
}}

Base confidence on how much evidence is available:
- high: multiple corroborating signals (patterns + ops + swallowed exceptions)
- medium: one clear signal
- low: limited context or ambiguous signals
"""


# ── core explain functions ────────────────────────────────────────────────────

async def explain_async(
    exc: BaseException,
    provider: LLMProvider | None = None,
) -> Explanation:
    """Async: explain the exception using the LLM provider."""
    p = provider or _default_provider
    if p is None:
        raise RuntimeError(
            "No LLM provider configured. Call because.configure_llm(api_key=...) first."
        )
    prompt = build_prompt(exc)
    raw = await p.complete(prompt)
    explanation = _parse_response(raw)
    exc.__llm_explanation__ = explanation  # type: ignore[attr-defined]
    try:
        from because.cli import save_last_explanation, save_last_chain
        save_last_explanation(explanation)
        save_last_chain(exc)
    except Exception:
        pass
    return explanation


def explain(
    exc: BaseException,
    provider: LLMProvider | None = None,
) -> Explanation:
    """Sync wrapper around explain_async. Avoid in async contexts."""
    return asyncio.run(explain_async(exc, provider=provider))


# ── response parser ───────────────────────────────────────────────────────────

def _parse_response(raw: str) -> Explanation:
    """Parse the LLM JSON response into an Explanation. Gracefully handles
    malformed output by falling back to the raw text as the root_cause."""
    text = raw.strip()

    # Strip markdown code fences if the model included them
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

    try:
        data = json.loads(text)
        return Explanation(
            root_cause=str(data.get("root_cause", "Unknown")),
            contributing_factors=list(data.get("contributing_factors", [])),
            suggested_fix=str(data.get("suggested_fix", "")),
            confidence=str(data.get("confidence", "low")),
            raw_response=raw,
        )
    except (json.JSONDecodeError, KeyError):
        return Explanation(
            root_cause=text[:500] if text else "LLM response could not be parsed.",
            confidence="low",
            raw_response=raw,
        )
