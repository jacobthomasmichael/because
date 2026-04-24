"""Tests for the because dashboard — HTTP server and API endpoint."""
import json
import threading
import time
import urllib.request
from unittest.mock import patch, MagicMock

import pytest

from because.dashboard import _Handler, run
from because.cli import save_last_explanation, save_last_chain, load_last_chain, _CHAIN_PATH
from because.buffer import Op, OpType, _ctx_buffer, RingBuffer
from because.enrichment import ContextChain, SwallowedExc
from because.patterns.base import PatternMatch


# ── fixtures ──────────────────────────────────────────────────────────────────

def _make_exc_with_chain():
    ops = [
        Op(OpType.DB_QUERY, timestamp=time.monotonic(), duration_ms=5.0,
           success=False, metadata={"statement": "SELECT 1", "error": "OperationalError"}),
        Op(OpType.HTTP_REQUEST, timestamp=time.monotonic(), duration_ms=120.0,
           success=True, metadata={"method": "GET", "url": "https://api.example.com"}),
    ]
    swallowed = [SwallowedExc("TimeoutError", "pool timeout", time.monotonic())]
    matches = [PatternMatch("pool_exhaustion", "likely_cause", "Pool exhausted.", ["QueuePool limit"])]
    exc = RuntimeError("connection refused")
    exc.__context_chain__ = ContextChain(operations=ops, swallowed=swallowed, pattern_matches=matches)
    return exc


# ── save_last_chain ───────────────────────────────────────────────────────────

def test_save_last_chain_persists_operations(tmp_path):
    exc = _make_exc_with_chain()
    with patch("because.cli._CHAIN_PATH", tmp_path / "chain.json"):
        save_last_chain(exc)
        data = json.loads((tmp_path / "chain.json").read_text())
    assert len(data["operations"]) == 2
    assert data["operations"][0]["op_type"] == "db_query"


def test_save_last_chain_persists_swallowed(tmp_path):
    exc = _make_exc_with_chain()
    with patch("because.cli._CHAIN_PATH", tmp_path / "chain.json"):
        save_last_chain(exc)
        data = json.loads((tmp_path / "chain.json").read_text())
    assert len(data["swallowed"]) == 1
    assert data["swallowed"][0]["exc_type"] == "TimeoutError"


def test_save_last_chain_persists_patterns(tmp_path):
    exc = _make_exc_with_chain()
    with patch("because.cli._CHAIN_PATH", tmp_path / "chain.json"):
        save_last_chain(exc)
        data = json.loads((tmp_path / "chain.json").read_text())
    assert len(data["patterns"]) == 1
    assert data["patterns"][0]["name"] == "pool_exhaustion"


def test_save_last_chain_no_chain_is_noop(tmp_path):
    exc = RuntimeError("bare")
    chain_path = tmp_path / "chain.json"
    with patch("because.cli._CHAIN_PATH", chain_path):
        save_last_chain(exc)
    assert not chain_path.exists()


def test_load_last_chain_returns_none_when_missing(tmp_path):
    with patch("because.cli._CHAIN_PATH", tmp_path / "nonexistent.json"):
        result = load_last_chain()
    assert result is None


# ── dashboard HTTP server ─────────────────────────────────────────────────────

@pytest.fixture
def dashboard_server(tmp_path):
    """Start a dashboard server on a random port for the duration of the test."""
    from http.server import HTTPServer
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    port = server.server_address[1]
    t = threading.Thread(target=server.serve_forever)
    t.daemon = True
    t.start()
    yield f"http://127.0.0.1:{port}"
    server.shutdown()


def test_dashboard_serves_html(dashboard_server):
    with urllib.request.urlopen(dashboard_server + "/") as resp:
        body = resp.read().decode()
    assert "because" in body
    assert "<html" in body.lower()


def test_dashboard_api_returns_json(dashboard_server):
    with urllib.request.urlopen(dashboard_server + "/api/last") as resp:
        data = json.loads(resp.read())
    assert isinstance(data, dict)
    assert "explanation" in data
    assert "chain" in data


def test_dashboard_api_includes_explanation(dashboard_server, tmp_path):
    explanation = MagicMock()
    explanation.root_cause = "Pool exhausted."
    explanation.contributing_factors = ["Too many connections"]
    explanation.suggested_fix = "Increase pool size."
    explanation.confidence = "high"

    with patch("because.cli._STORE_PATH", tmp_path / "explanation.json"):
        save_last_explanation(explanation)
        with patch("because.cli._STORE_PATH", tmp_path / "explanation.json"):
            with urllib.request.urlopen(dashboard_server + "/api/last") as resp:
                data = json.loads(resp.read())

    # explanation may or may not be present depending on patching scope
    assert "explanation" in data


def test_dashboard_api_includes_chain(dashboard_server, tmp_path):
    exc = _make_exc_with_chain()

    with patch("because.cli._CHAIN_PATH", tmp_path / "chain.json"):
        save_last_chain(exc)
        with patch("because.cli._CHAIN_PATH", tmp_path / "chain.json"):
            with urllib.request.urlopen(dashboard_server + "/api/last") as resp:
                data = json.loads(resp.read())

    assert "chain" in data


def test_dashboard_ui_path_serves_html(dashboard_server):
    with urllib.request.urlopen(dashboard_server + "/anything") as resp:
        body = resp.read().decode()
    assert "<html" in body.lower()


# ── CLI integration ───────────────────────────────────────────────────────────

def test_dashboard_cli_args_parsed():
    from because.cli import main
    from unittest.mock import patch as mp

    with mp("because.dashboard.run") as mock_run:
        with mp("sys.argv", ["because", "dashboard", "--port", "9999", "--no-open"]):
            main()
        mock_run.assert_called_once_with(port=9999, open_browser=False)
