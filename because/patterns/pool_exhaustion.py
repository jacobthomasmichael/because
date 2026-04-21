from __future__ import annotations

import re
from typing import TYPE_CHECKING

from because.buffer import OpType
from because.patterns.base import PatternMatch

if TYPE_CHECKING:
    from because.enrichment import ContextChain

_POOL_MESSAGES = re.compile(
    r"(QueuePool limit|pool limit|too many connections|connection pool|"
    r"remaining connection slots|max_overflow|connect timeout|"
    r"connection refused|could not connect)",
    re.IGNORECASE,
)

# Fraction of recent DB ops that must be failures to flag saturation
_FAILURE_RATE_THRESHOLD = 0.4
# Minimum DB ops in window to consider the pattern
_MIN_DB_OPS = 3


def match(exc: BaseException, chain: "ContextChain") -> PatternMatch | None:
    exc_msg = str(exc)
    exc_type = type(exc).__name__

    # Check exception message looks connection/pool related
    msg_match = _POOL_MESSAGES.search(exc_msg)
    is_connection_type = "Connection" in exc_type or "Operational" in exc_type or "Pool" in exc_type

    if not (msg_match or is_connection_type):
        return None

    db_ops = [op for op in chain.operations if op.op_type == OpType.DB_QUERY]
    if not db_ops:
        return None

    failed_db = [op for op in db_ops if not op.success]
    failure_rate = len(failed_db) / len(db_ops)

    evidence: list[str] = []

    if msg_match:
        evidence.append(f"Exception message contains '{msg_match.group(0)}'")

    if len(db_ops) >= _MIN_DB_OPS:
        evidence.append(f"{len(db_ops)} DB queries recorded in context window")

    if failure_rate >= _FAILURE_RATE_THRESHOLD:
        evidence.append(
            f"{len(failed_db)}/{len(db_ops)} recent DB queries failed "
            f"({failure_rate:.0%} failure rate)"
        )

    # need at least the message signal + one corroborating DB signal
    db_signal = len(db_ops) >= _MIN_DB_OPS or failure_rate >= _FAILURE_RATE_THRESHOLD
    if not (msg_match and db_signal):
        return None

    confidence = "likely_cause" if failure_rate >= _FAILURE_RATE_THRESHOLD else "contributing_factor"

    return PatternMatch(
        name="pool_exhaustion",
        confidence=confidence,
        description="Database connection pool may be exhausted",
        evidence=evidence,
    )
