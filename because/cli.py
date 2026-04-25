"""
because CLI

Usage:
    because explain [options] [FILE]   Explain a stack trace using an LLM
    because last                       Print the most recent explanation
    because dashboard [--port PORT]    Open the local web dashboard

Examples:
    cat error.log | because explain
    because explain error.log
    because explain --provider openai --model gpt-4o error.log
    because last
    because dashboard
    because dashboard --port 8080 --no-open
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path


# ── explanation store (temp file) ─────────────────────────────────────────────

_STORE_PATH = Path(tempfile.gettempdir()) / "because_last_explanation.json"
_CHAIN_PATH = Path(tempfile.gettempdir()) / "because_last_chain.json"


def save_last_explanation(explanation) -> None:
    """Persist the most recent Explanation to a temp file for `because last`."""
    try:
        data = {
            "root_cause": explanation.root_cause,
            "contributing_factors": explanation.contributing_factors,
            "suggested_fix": explanation.suggested_fix,
            "confidence": explanation.confidence,
        }
        _STORE_PATH.write_text(json.dumps(data))
    except Exception:
        pass


def save_last_chain(exc: BaseException) -> None:
    """Persist the most recent context chain to a temp file for the dashboard."""
    try:
        from because.integrations.serialize import chain_from_exc, chain_to_dict
        chain = chain_from_exc(exc)
        if chain is None:
            return
        _CHAIN_PATH.write_text(json.dumps(chain_to_dict(chain), default=str))
    except Exception:
        pass


def load_last_chain() -> dict | None:
    try:
        return json.loads(_CHAIN_PATH.read_text())
    except Exception:
        return None


def load_last_explanation() -> dict | None:
    try:
        return json.loads(_STORE_PATH.read_text())
    except Exception:
        return None


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
        XAIProvider,
        GeminiProvider,
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
            print("because: set ANTHROPIC_API_KEY or pass --api-key", file=sys.stderr)
            return 1
        kwargs = {"api_key": api_key}
        if args.model:
            kwargs["model"] = args.model
        provider = AnthropicProvider(**kwargs)

    elif provider_name == "openai":
        api_key = api_key or os.environ.get("OPENAI_API_KEY")
        if not api_key:
            print("because: set OPENAI_API_KEY or pass --api-key", file=sys.stderr)
            return 1
        kwargs = {"api_key": api_key}
        if args.model:
            kwargs["model"] = args.model
        provider = OpenAIProvider(**kwargs)

    elif provider_name == "xai":
        api_key = api_key or os.environ.get("XAI_API_KEY")
        if not api_key:
            print("because: set XAI_API_KEY or pass --api-key", file=sys.stderr)
            return 1
        kwargs = {"api_key": api_key}
        if args.model:
            kwargs["model"] = args.model
        provider = XAIProvider(**kwargs)

    elif provider_name == "gemini":
        api_key = api_key or os.environ.get("GEMINI_API_KEY")
        if not api_key:
            print("because: set GEMINI_API_KEY or pass --api-key", file=sys.stderr)
            return 1
        kwargs = {"api_key": api_key}
        if args.model:
            kwargs["model"] = args.model
        provider = GeminiProvider(**kwargs)

    else:
        print(f"because: unknown provider {provider_name!r}. Use 'anthropic', 'openai', 'xai', or 'gemini'.",
              file=sys.stderr)
        return 1

    print("Analyzing stack trace...\n", file=sys.stderr)

    prompt = _build_cli_prompt(text)
    raw = await provider.complete(prompt)
    explanation = _parse_response(raw)
    save_last_explanation(explanation)
    print(str(explanation))
    return 0


# ── last subcommand ───────────────────────────────────────────────────────────

def _run_last() -> int:
    data = load_last_explanation()
    if data is None:
        print("because: no explanation stored yet. Run `because explain` first.",
              file=sys.stderr)
        return 1

    confidence = data.get("confidence", "low")
    root_cause = data.get("root_cause", "")
    factors = data.get("contributing_factors", [])
    fix = data.get("suggested_fix", "")

    lines = [f"Root cause ({confidence} confidence): {root_cause}"]
    if factors:
        lines.append("Contributing factors:")
        for f in factors:
            lines.append(f"  • {f}")
    if fix:
        lines.append(f"Suggested fix: {fix}")

    print("\n".join(lines))
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
        choices=["anthropic", "openai", "xai", "gemini"],
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

    sub.add_parser(
        "last",
        help="Print the most recent explanation stored by because explain.",
    )

    dashboard_parser = sub.add_parser(
        "dashboard",
        help="Open the local web dashboard.",
    )
    dashboard_parser.add_argument(
        "--port",
        type=int,
        default=7331,
        help="Port to listen on (default: 7331).",
    )
    dashboard_parser.add_argument(
        "--no-open",
        action="store_true",
        default=False,
        help="Don't open the browser automatically.",
    )

    args = parser.parse_args()

    if args.command == "explain":
        sys.exit(asyncio.run(_run_explain(args)))
    elif args.command == "last":
        sys.exit(_run_last())
    elif args.command == "dashboard":
        from because.dashboard import run as run_dashboard
        run_dashboard(port=args.port, open_browser=not args.no_open)
    else:
        parser.print_help()
        sys.exit(0)


if __name__ == "__main__":
    main()
