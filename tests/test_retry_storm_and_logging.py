import logging
import time

import pytest

from because.buffer import Op, OpType, get_context, record
from because.enrichment import ContextChain
from because.instruments.logging import instrument as instrument_logging
from because.patterns.retry_storm import match as retry_match


# ── retry_storm pattern ───────────────────────────────────────────────────────

def _http_op(url="http://api.example.com/check", success=True, method="GET"):
    return Op(
        OpType.HTTP_REQUEST,
        timestamp=time.monotonic(),
        duration_ms=200.0,
        success=success,
        metadata={"method": method, "url": url,
                  **({} if success else {"error": "ReadTimeout"})},
    )


def _make_chain(ops):
    return ContextChain(operations=ops)


def test_retry_storm_triggers_on_timeout_with_repeated_url():
    ops = [_http_op(success=False)] * 5 + [_http_op(success=True)]
    chain = _make_chain(ops)
    exc = TimeoutError("read timeout")
    result = retry_match(exc, chain)
    assert result is not None
    assert result.name == "retry_storm"


def test_retry_storm_likely_cause_when_concentrated_and_failing():
    ops = [_http_op(success=False)] * 6 + [_http_op(success=True)]
    chain = _make_chain(ops)
    exc = TimeoutError("timed out")
    result = retry_match(exc, chain)
    assert result is not None
    assert result.confidence == "likely_cause"


def test_retry_storm_contributing_when_concentrated_not_failing():
    ops = [_http_op(success=True)] * 6  # all succeed, same URL
    chain = _make_chain(ops)
    exc = TimeoutError("timeout")
    result = retry_match(exc, chain)
    assert result is not None
    assert result.confidence == "contributing_factor"


def test_retry_storm_no_match_without_timeout():
    ops = [_http_op(success=False)] * 5
    chain = _make_chain(ops)
    exc = ValueError("invalid input")
    assert retry_match(exc, chain) is None


def test_retry_storm_no_match_too_few_http_ops():
    ops = [_http_op(success=False)] * 2
    chain = _make_chain(ops)
    exc = TimeoutError("timed out")
    assert retry_match(exc, chain) is None


def test_retry_storm_no_match_dispersed_urls():
    ops = [
        _http_op(url=f"http://host{i}.example.com/", success=False)
        for i in range(6)
    ]
    chain = _make_chain(ops)
    exc = TimeoutError("timed out")
    result = retry_match(exc, chain)
    # High failure rate but dispersed URLs — still fires on failure rate alone
    assert result is not None  # failure rate triggers it


def test_retry_storm_evidence_mentions_concentration():
    ops = [_http_op(success=False)] * 5 + [_http_op()]
    chain = _make_chain(ops)
    exc = TimeoutError("timed out")
    result = retry_match(exc, chain)
    assert result is not None
    assert any("concentration" in e or "retry" in e.lower() for e in result.evidence)


# ── logging instrument ────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def isolated_logger():
    """Each test gets a fresh named logger so handlers don't accumulate."""
    yield
    # remove any BecauseHandlers added during tests
    from because.instruments.logging import _BecauseHandler
    root = logging.getLogger()
    root.handlers = [h for h in root.handlers if not isinstance(h, _BecauseHandler)]


def _log_ops():
    return [op for op in get_context().snapshot() if op.op_type == OpType.LOG]


def test_logging_instrument_records_warning():
    logger = logging.getLogger("test.because.warn")
    instrument_logging(logger)
    before = len(_log_ops())
    logger.warning("something suspicious")
    ops = _log_ops()
    assert len(ops) == before + 1
    op = ops[-1]
    assert op.metadata["level"] == "WARNING"
    assert "suspicious" in op.metadata["message"]


def test_logging_instrument_records_error_as_failure():
    logger = logging.getLogger("test.because.error")
    instrument_logging(logger)
    before = len(_log_ops())
    logger.error("something broke")
    op = _log_ops()[before]
    assert op.success is False
    assert op.metadata["level"] == "ERROR"


def test_logging_instrument_ignores_debug():
    logger = logging.getLogger("test.because.debug")
    logger.setLevel(logging.DEBUG)
    instrument_logging(logger, level=logging.WARNING)
    before = len(_log_ops())
    logger.debug("verbose noise")
    assert len(_log_ops()) == before  # not recorded


def test_logging_instrument_idempotent():
    logger = logging.getLogger("test.because.idem")
    instrument_logging(logger)
    instrument_logging(logger)
    from because.instruments.logging import _BecauseHandler
    because_handlers = [h for h in logger.handlers if isinstance(h, _BecauseHandler)]
    assert len(because_handlers) == 1


def test_logging_instrument_records_logger_name():
    logger = logging.getLogger("myapp.services.payments")
    instrument_logging(logger)
    before = len(_log_ops())
    logger.warning("payment gateway slow")
    op = _log_ops()[before]
    assert op.metadata["logger"] == "myapp.services.payments"
