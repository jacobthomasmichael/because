"""
because CLI

Usage:
    because explain [options] [FILE]

Reads a Python stack trace from FILE or stdin and returns a plain-English
root cause analysis powered by an LLM.

Examples:
    cat error.log | because explain
    because explain error.log
    because explain --provider openai --model gpt-4o error.log
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys


# ── prompt ────────────────────────────────────────────────────────────────────

def _build_cli_prompt(stack_trace: str) -> str:
    return f"""\
You are a senior engineer helping diagnose a Python exception. Below is a \
stack trace pasted from a production system.

Be honest about uncertainty. Use hedged language like "likely", "possibly", \
"may be caused by". Do NOT invent causes that aren't supported by the evidence.

## Stack trace

{stack_trace.strip()}

## Instructions

Respond with valid JSON matching this schema exactly — no markdown, no prose outside the JSON:

{{
  "root_cause": "<one sentence explaining the most likely cause>",
  "contributing_factors": ["<factor 1>", "<factor 2>"],
  "suggested_fix": "<one concrete action the engineer should take first>",
  "confidence": "<low|medium|high>"
}}

Base confidence on how much information is available in the trace:
- high: clear error message with obvious cause
- medium: some signal but ambiguous
- low: minimal context or generic error
"""


# ── explain subcommand ────────────────────────────────────────────────────────

async def _run_explain(args: argparse.Namespace) -> int:
    from because.explainer import (
        AnthropicProvider,
        OpenAIProvider,
        _parse_response,
    )

    # Read input
    if args.file:
        try:
            with open(args.file) as f:
                text = f.read()
        except OSError as e:
            print(f"because: cannot read file: {e}", file=sys.stderr)
            return 1
    else:
        if sys.stdin.isatty():
            print("Paste your stack trace below. Press Ctrl-D when done.\n",
                  file=sys.stderr)
        text = sys.stdin.read()

    if not text.strip():
        print("because: no input provided.", file=sys.stderr)
        return 1

    # Resolve provider
    provider_name = args.provider or "anthropic"
    api_key = args.api_key

    if provider_name == "anthropic":
        api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            print(
                "because: set ANTHROPIC_API_KEY or pass --api-key",
                file=sys.stderr,
            )
            return 1
        kwargs = {"api_key": api_key}
        if args.model:
            kwargs["model"] = args.model
        provider = AnthropicProvider(**kwargs)

    elif provider_name == "openai":
        api_key = api_key or os.environ.get("OPENAI_API_KEY")
        if not api_key:
            print(
                "because: set OPENAI_API_KEY or pass --api-key",
                file=sys.stderr,
            )
            return 1
        kwargs = {"api_key": api_key}
        if args.model:
            kwargs["model"] = args.model
        from because.explainer import OpenAIProvider
        provider = OpenAIProvider(**kwargs)

    else:
        print(f"because: unknown provider {provider_name!r}. Use 'anthropic' or 'openai'.",
              file=sys.stderr)
        return 1

    print("Analyzing stack trace...\n", file=sys.stderr)

    prompt = _build_cli_prompt(text)
    raw = await provider.complete(prompt)
    explanation = _parse_response(raw)
    print(str(explanation))
    return 0


# ── entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        prog="because",
        description="Plain-English root cause analysis for Python exceptions.",
    )
    sub = parser.add_subparsers(dest="command")

    explain_parser = sub.add_parser(
        "explain",
        help="Explain a stack trace using an LLM.",
    )
    explain_parser.add_argument(
        "file",
        nargs="?",
        help="Path to a file containing the stack trace (default: stdin).",
    )
    explain_parser.add_argument(
        "--provider",
        choices=["anthropic", "openai"],
        default=None,
        help="LLM provider (default: anthropic).",
    )
    explain_parser.add_argument(
        "--model",
        default=None,
        help="Model override (e.g. claude-opus-4-7, gpt-4o).",
    )
    explain_parser.add_argument(
        "--api-key",
        default=None,
        help="API key (default: ANTHROPIC_API_KEY or OPENAI_API_KEY env var).",
    )

    args = parser.parse_args()

    if args.command == "explain":
        sys.exit(asyncio.run(_run_explain(args)))
    else:
        parser.print_help()
        sys.exit(0)


if __name__ == "__main__":
    main()
