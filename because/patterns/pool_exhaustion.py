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
    r"too many connections)",
    re.IGNORECASE,
)
# Softer signals that need corroborating evidence
_POOL_MESSAGES = re.compile(
    r"(connection pool|connect timeout|connection refused|could not connect)",
    re.IGNORECASE,
)

# Fraction of recent DB ops that must be failures to flag saturation
_FAILURE_RATE_THRESHOLD = 0.4
# Minimum DB ops in window to consider the pattern
_MIN_DB_OPS = 3


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
    failed_db = [op for op in db_ops if not op.success]
    failure_rate = len(failed_db) / len(db_ops) if db_ops else 0.0

    evidence: list[str] = []

    if msg_match:
        evidence.append(f"Exception message contains '{msg_match.group(0)}'")

    if len(db_ops) >= _MIN_DB_OPS:
        evidence.append(f"{len(db_ops)} DB queries in context window (pool was active)")

    if failure_rate >= _FAILURE_RATE_THRESHOLD:
        evidence.append(
            f"{len(failed_db)}/{len(db_ops)} recent DB queries failed "
            f"({failure_rate:.0%} failure rate)"
        )

    # Definitive pool message alone is sufficient; soft messages need corroboration
    if definitive_match:
        confidence = "likely_cause"
    elif soft_match and (len(db_ops) >= _MIN_DB_OPS or failure_rate >= _FAILURE_RATE_THRESHOLD):
        confidence = "likely_cause" if failure_rate >= _FAILURE_RATE_THRESHOLD else "contributing_factor"
    else:
        return None

    return PatternMatch(
        name="pool_exhaustion",
        confidence=confidence,
        description="Database connection pool may be exhausted",
        evidence=evidence,
    )
