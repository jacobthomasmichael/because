from __future__ import annotations

import re
from collections import Counter
from typing import TYPE_CHECKING

from because.buffer import OpType
from because.patterns.base import PatternMatch

if TYPE_CHECKING:
    from because.enrichment import ContextChain

_TIMEOUT_MESSAGES = re.compile(
    r"(timeout|timed out|deadline exceeded|read timeout|connect timeout)",
    re.IGNORECASE,
)
_TIMEOUT_TYPES = {"TimeoutError", "ReadTimeout", "ConnectTimeout", "asyncio.TimeoutError"}

# Minimum HTTP ops to consider the pattern
_MIN_HTTP_OPS = 4
# Fraction of recent HTTP ops that must target the same URL prefix
_URL_CONCENTRATION_THRESHOLD = 0.5
# Fraction of HTTP ops that must be failures
_FAILURE_RATE_THRESHOLD = 0.4


def match(exc: BaseException, chain: "ContextChain") -> PatternMatch | None:
    exc_msg = str(exc)
    exc_type = type(exc).__name__

    is_timeout = bool(_TIMEOUT_MESSAGES.search(exc_msg)) or exc_type in _TIMEOUT_TYPES
    if not is_timeout:
        return None

    http_ops = [op for op in chain.operations if op.op_type == OpType.HTTP_REQUEST]
    if len(http_ops) < _MIN_HTTP_OPS:
        return None

    failed_http = [op for op in http_ops if not op.success]
    failure_rate = len(failed_http) / len(http_ops)

    # Check URL concentration — many requests to the same upstream host
    urls = [op.metadata.get("url", "") for op in http_ops]
    host_counts = Counter(_host(u) for u in urls if u)
    top_host, top_count = host_counts.most_common(1)[0] if host_counts else ("", 0)
    url_concentration = top_count / len(http_ops) if http_ops else 0.0

    evidence: list[str] = []

    if is_timeout:
        evidence.append(f"Exception indicates timeout: {exc_type}")

    if len(http_ops) >= _MIN_HTTP_OPS:
        evidence.append(f"{len(http_ops)} HTTP requests in context window")

    if url_concentration >= _URL_CONCENTRATION_THRESHOLD and top_host:
        evidence.append(
            f"{top_count}/{len(http_ops)} requests targeted '{top_host}' "
            f"({url_concentration:.0%} concentration — possible retry loop)"
        )

    if failure_rate >= _FAILURE_RATE_THRESHOLD:
        evidence.append(
            f"{len(failed_http)}/{len(http_ops)} HTTP requests failed "
            f"({failure_rate:.0%} failure rate)"
        )

    # Need timeout + volume + either URL concentration or high failure rate
    has_signal = (
        url_concentration >= _URL_CONCENTRATION_THRESHOLD
        or failure_rate >= _FAILURE_RATE_THRESHOLD
    )
    if not has_signal:
        return None

    confidence = (
        "likely_cause"
        if (failure_rate >= _FAILURE_RATE_THRESHOLD and url_concentration >= _URL_CONCENTRATION_THRESHOLD)
        else "contributing_factor"
    )

    return PatternMatch(
        name="retry_storm",
        confidence=confidence,
        description=(
            "A timeout exception combined with repeated HTTP requests to the same "
            "upstream suggests a retry loop against a degraded service."
        ),
        evidence=evidence,
    )


def _host(url: str) -> str:
    """Extract scheme+host from a URL for grouping purposes."""
    try:
        # simple split — avoids importing urllib for a hot path
        parts = url.split("/")
        return "/".join(parts[:3]) if len(parts) >= 3 else url
    except Exception:
        return url
