from because.buffer import get_context, gather
from because.enrichment import catch, enrich, enrich_with_swallowed, format_context_chain, watch
from because.enrichment import install as _install_enrichment
from because.buffer import install as _install_buffer
from because.explainer import (
    Explanation,
    AnthropicProvider,
    OpenAIProvider,
    configure_llm,
    explain,
    explain_async,
    build_prompt,
)


def install(buffer_size: int = 128) -> None:
    """Zero-config setup. Call once at process startup."""
    _install_buffer(buffer_size)
    _install_enrichment()


__all__ = [
    "install",
    "get_context",
    "enrich",
    "enrich_with_swallowed",
    "format_context_chain",
    "catch",
    "watch",
    "gather",
    # LLM explainer
    "Explanation",
    "AnthropicProvider",
    "OpenAIProvider",
    "configure_llm",
    "explain",
    "explain_async",
    "build_prompt",
]
