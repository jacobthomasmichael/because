from unittest.mock import patch

import pytest
import requests

from because.buffer import OpType, get_context
from because.instruments.requests import instrument


@pytest.fixture
def session():
    s = requests.Session()
    instrument(s)
    return s


def _http_ops():
    return [op for op in get_context().snapshot() if op.op_type == OpType.HTTP_REQUEST]


def _mock_response(status_code=200):
    resp = requests.Response()
    resp.status_code = status_code
    resp.url = "http://example.com/api"
    return resp


def test_successful_request_recorded(session):
    before = len(_http_ops())
    with patch("requests.adapters.HTTPAdapter.send", return_value=_mock_response(200)):
        session.get("http://example.com/api")

    ops = _http_ops()
    assert len(ops) == before + 1
    op = ops[-1]
    assert op.success is True
    assert op.metadata["method"] == "GET"
    assert op.metadata["url"] == "http://example.com/api"
    assert op.metadata["status_code"] == 200
    assert op.duration_ms is not None and op.duration_ms >= 0


def test_non_2xx_recorded_as_success(session):
    """Non-2xx responses are recorded — success=True since the request completed."""
    before = len(_http_ops())
    with patch("requests.adapters.HTTPAdapter.send", return_value=_mock_response(404)):
        session.get("http://example.com/missing")
    op = _http_ops()[before]
    assert op.success is True
    assert op.metadata["status_code"] == 404


def test_connection_error_recorded(session):
    before = len(_http_ops())
    with patch("requests.adapters.HTTPAdapter.send", side_effect=requests.ConnectionError("refused")):
        with pytest.raises(requests.ConnectionError):
            session.get("http://example.com/api")
    op = _http_ops()[before]
    assert op.success is False
    assert op.metadata["error"] == "ConnectionError"


def test_query_string_stripped_from_url(session):
    before = len(_http_ops())
    with patch("requests.adapters.HTTPAdapter.send", return_value=_mock_response(200)):
        session.get("http://example.com/search?token=secret&q=test")
    op = _http_ops()[before]
    assert "token" not in op.metadata["url"]
    assert op.metadata["url"] == "http://example.com/search"


def test_instrument_idempotent(session):
    instrument(session)  # second call should be a no-op
    before = len(_http_ops())
    with patch("requests.adapters.HTTPAdapter.send", return_value=_mock_response(200)):
        session.get("http://example.com/api")
    assert len(_http_ops()) == before + 1


def test_multiple_requests_all_recorded(session):
    before = len(_http_ops())
    with patch("requests.adapters.HTTPAdapter.send", return_value=_mock_response(200)):
        session.get("http://example.com/a")
        session.post("http://example.com/b")
        session.get("http://example.com/c")
    assert len(_http_ops()) == before + 3
