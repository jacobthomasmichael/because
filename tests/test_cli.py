"""Tests for the because CLI — all mocked, no real API calls."""
import asyncio
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from because.cli import _build_cli_prompt, main, _run_explain


# ── _build_cli_prompt ─────────────────────────────────────────────────────────

def test_build_cli_prompt_includes_stack_trace():
    trace = "ValueError: something went wrong\n  File app.py line 10"
    prompt = _build_cli_prompt(trace)
    assert "ValueError: something went wrong" in prompt
    assert "app.py" in prompt


def test_build_cli_prompt_requests_json_schema():
    prompt = _build_cli_prompt("some trace")
    assert "root_cause" in prompt
    assert "confidence" in prompt
    assert "suggested_fix" in prompt


def test_build_cli_prompt_strips_input():
    prompt = _build_cli_prompt("   \nsome trace\n   ")
    assert "some trace" in prompt


# ── _run_explain ──────────────────────────────────────────────────────────────

def _make_args(**kwargs):
    defaults = dict(file=None, provider=None, model=None, api_key=None)
    defaults.update(kwargs)
    ns = MagicMock()
    for k, v in defaults.items():
        setattr(ns, k.replace("-", "_"), v)
    return ns


_GOOD_JSON = """{
  "root_cause": "Pool exhausted.",
  "contributing_factors": ["Too many connections"],
  "suggested_fix": "Increase pool size.",
  "confidence": "high"
}"""


@pytest.mark.asyncio
async def test_run_explain_reads_stdin(tmp_path, monkeypatch):
    import io
    monkeypatch.setattr("sys.stdin", io.StringIO("Traceback:\n  ValueError: oops"))
    monkeypatch.setattr("sys.stdin.isatty", lambda: False, raising=False)

    with patch("because.explainer.AnthropicProvider.complete", new=AsyncMock(return_value=_GOOD_JSON)):
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-ant-test"}):
            args = _make_args()
            code = await _run_explain(args)
    assert code == 0


@pytest.mark.asyncio
async def test_run_explain_reads_file(tmp_path):
    trace_file = tmp_path / "trace.txt"
    trace_file.write_text("Traceback:\n  TimeoutError: timed out")

    with patch("because.explainer.AnthropicProvider.complete", new=AsyncMock(return_value=_GOOD_JSON)):
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-ant-test"}):
            args = _make_args(file=str(trace_file))
            code = await _run_explain(args)
    assert code == 0


@pytest.mark.asyncio
async def test_run_explain_missing_api_key_returns_1(monkeypatch):
    import io
    monkeypatch.setattr("sys.stdin", io.StringIO("some trace"))
    monkeypatch.setattr("sys.stdin.isatty", lambda: False, raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    args = _make_args()
    code = await _run_explain(args)
    assert code == 1


@pytest.mark.asyncio
async def test_run_explain_empty_input_returns_1(monkeypatch):
    import io
    monkeypatch.setattr("sys.stdin", io.StringIO("   "))
    monkeypatch.setattr("sys.stdin.isatty", lambda: False, raising=False)

    with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-ant-test"}):
        args = _make_args()
        code = await _run_explain(args)
    assert code == 1


@pytest.mark.asyncio
async def test_run_explain_missing_file_returns_1():
    args = _make_args(file="/nonexistent/path/trace.txt")
    with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-ant-test"}):
        code = await _run_explain(args)
    assert code == 1


@pytest.mark.asyncio
async def test_run_explain_unknown_provider_returns_1(monkeypatch):
    import io
    monkeypatch.setattr("sys.stdin", io.StringIO("some trace"))
    monkeypatch.setattr("sys.stdin.isatty", lambda: False, raising=False)

    args = _make_args(provider="cohere", api_key="key")
    code = await _run_explain(args)
    assert code == 1


@pytest.mark.asyncio
async def test_run_explain_openai_provider(monkeypatch, tmp_path):
    trace_file = tmp_path / "trace.txt"
    trace_file.write_text("Traceback:\n  ConnectionError: refused")

    with patch("because.explainer.OpenAIProvider.complete", new=AsyncMock(return_value=_GOOD_JSON)):
        with patch.dict("os.environ", {"OPENAI_API_KEY": "sk-openai-test"}):
            args = _make_args(file=str(trace_file), provider="openai")
            code = await _run_explain(args)
    assert code == 0


@pytest.mark.asyncio
async def test_run_explain_model_override(tmp_path):
    trace_file = tmp_path / "trace.txt"
    trace_file.write_text("Traceback:\n  ValueError: bad input")

    captured_model = {}

    class CapturingProvider:
        def __init__(self, api_key, model="claude-sonnet-4-6", **kw):
            captured_model["model"] = model

        async def complete(self, prompt):
            return _GOOD_JSON

    with patch("because.explainer.AnthropicProvider", CapturingProvider):
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-ant-test"}):
            args = _make_args(file=str(trace_file), model="claude-opus-4-7")
            code = await _run_explain(args)

    assert code == 0
    assert captured_model["model"] == "claude-opus-4-7"


@pytest.mark.asyncio
async def test_run_explain_prints_explanation(tmp_path, capsys):
    trace_file = tmp_path / "trace.txt"
    trace_file.write_text("Traceback:\n  TimeoutError: timed out")

    with patch("because.explainer.AnthropicProvider.complete", new=AsyncMock(return_value=_GOOD_JSON)):
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-ant-test"}):
            args = _make_args(file=str(trace_file))
            await _run_explain(args)

    out = capsys.readouterr().out
    assert "Pool exhausted" in out
    assert "high" in out


# ── main (argparse) ───────────────────────────────────────────────────────────

def test_main_no_args_exits_0(capsys):
    with pytest.raises(SystemExit) as exc_info:
        with patch("sys.argv", ["because"]):
            main()
    assert exc_info.value.code == 0


def test_main_explain_subcommand_dispatches(tmp_path):
    trace_file = tmp_path / "trace.txt"
    trace_file.write_text("Traceback:\n  ValueError: oops")

    with patch("because.cli._run_explain", new=AsyncMock(return_value=0)):
        with patch("sys.argv", ["because", "explain", str(trace_file)]):
            with pytest.raises(SystemExit) as exc_info:
                main()
    assert exc_info.value.code == 0
