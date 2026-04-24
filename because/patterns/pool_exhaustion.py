from __future__ import annotations

import re
from typing import TYPE_CHECKING

from because.buffer import OpType
from because.patterns.base import PatternMatch

if TYPE_CHECKING:
    from because.enrichment import ContextChain

# Unambiguous pool-specific messages → always likely_cause when matched
_POOL_DEFINITIVE = re.compile(
    r"(QueuePool limit|pool limit|max_overflow|remaining connection slots|"
    r"too many connections|connection pool exhausted|PoolTimeout|"
    r"pool_timeout|pool size|ConnectionPool|urllib3.*pool|"
    r"Max retries exceeded)",
    re.IGNORECASE,
)
# Softer signals that need corroborating evidence
_POOL_MESSAGES = re.compile(
    r"(connection pool|connect timeout|connection refused|could not connect)",
    re.IGNORECASE,
)

# Fraction of recent ops that must be failures to flag saturation
_FAILURE_RATE_THRESHOLD = 0.4
# Minimum DB ops in window to consider the DB pool pattern
_MIN_DB_OPS = 3
# Minimum HTTP ops in window to consider the HTTP pool pattern
_MIN_HTTP_OPS = 3


def match(exc: BaseException, chain: "ContextChain") -> PatternMatch | None:
    exc_msg = str(exc)
    exc_type = type(exc).__name__

    definitive_match = _POOL_DEFINITIVE.search(exc_msg)
    soft_match = _POOL_MESSAGES.search(exc_msg)
    msg_match = definitive_match or soft_match
    is_connection_type = "Connection" in exc_type or "Operational" in exc_type or "Pool" in exc_type

    if not (msg_match or is_connection_type):
        return None

    db_ops = [op for op in chain.operations if op.op_type == OpType.DB_QUERY]
    http_ops = [op for op in chain.operations if op.op_type == OpType.HTTP_REQUEST]
    failed_db = [op for op in db_ops if not op.success]
    failed_http = [op for op in http_ops if not op.success]

    db_failure_rate = len(failed_db) / len(db_ops) if db_ops else 0.0
    http_failure_rate = len(failed_http) / len(http_ops) if http_ops else 0.0

    evidence: list[str] = []

    if msg_match:
        evidence.append(f"Exception message contains '{msg_match.group(0)}'")

    # DB pool signals
    if len(db_ops) >= _MIN_DB_OPS:
        evidence.append(f"{len(db_ops)} DB queries in context window (pool was active)")
    if db_failure_rate >= _FAILURE_RATE_THRESHOLD:
        evidence.append(
            f"{len(failed_db)}/{len(db_ops)} recent DB queries failed "
            f"({db_failure_rate:.0%} failure rate)"
        )

    # HTTP pool signals
    if len(http_ops) >= _MIN_HTTP_OPS:
        evidence.append(f"{len(http_ops)} HTTP requests in context window")
    if http_failure_rate >= _FAILURE_RATE_THRESHOLD:
        evidence.append(
            f"{len(failed_http)}/{len(http_ops)} recent HTTP requests failed "
            f"({http_failure_rate:.0%} failure rate)"
        )

    has_db_signal = len(db_ops) >= _MIN_DB_OPS or db_failure_rate >= _FAILURE_RATE_THRESHOLD
    has_http_signal = len(http_ops) >= _MIN_HTTP_OPS or http_failure_rate >= _FAILURE_RATE_THRESHOLD
    has_activity = has_db_signal or has_http_signal
    has_failures = db_failure_rate >= _FAILURE_RATE_THRESHOLD or http_failure_rate >= _FAILURE_RATE_THRESHOLD

    pool_type = "HTTP connection" if (has_http_signal and not has_db_signal) else "database connection"

    # Definitive pool message alone is sufficient; soft messages need corroboration
    if definitive_match:
        confidence = "likely_cause"
    elif soft_match and has_activity:
        confidence = "likely_cause" if has_failures else "contributing_factor"
    else:
        return None

    return PatternMatch(
        name="pool_exhaustion",
        confidence=confidence,
        description=f"{pool_type.capitalize()} pool may be exhausted",
        evidence=evidence,
    )
