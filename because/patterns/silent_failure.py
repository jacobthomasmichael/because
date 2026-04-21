from __future__ import annotations

import re
from typing import TYPE_CHECKING

from because.buffer import OpType
from because.patterns.base import PatternMatch

if TYPE_CHECKING:
    from because.enrichment import ContextChain

# Common exception types that indicate a swallowed upstream problem
_UPSTREAM_TYPES = {
    "TimeoutError", "ConnectionError", "ConnectionRefusedError",
    "OperationalError", "InterfaceError", "BrokenPipeError",
    "OSError", "IOError", "KeyError", "AttributeError", "TypeError",
}


def match(exc: BaseException, chain: "ContextChain") -> PatternMatch | None:
    swallowed_ops = [
        op for op in chain.operations if op.op_type == OpType.EXCEPTION_SWALLOWED
    ]
    explicit_swallowed = list(chain.swallowed)

    all_swallowed_types = [
        op.metadata.get("exc_type", "") for op in swallowed_ops
    ] + [s.exc_type for s in explicit_swallowed]

    if not all_swallowed_types:
        return None

    upstream_hits = [t for t in all_swallowed_types if t in _UPSTREAM_TYPES]

    evidence: list[str] = []

    if explicit_swallowed:
        for s in explicit_swallowed:
            evidence.append(f"Caught-and-swallowed: {s.exc_type}: {s.message[:80]}")
    elif swallowed_ops:
        for op in swallowed_ops[-3:]:
            exc_type = op.metadata.get("exc_type", "unknown")
            msg = op.metadata.get("message", "")[:80]
            evidence.append(f"Swallowed in context: {exc_type}: {msg}")

    # also check messages for upstream keywords when types are generic
    upstream_keywords = re.compile(
        r"(connection|timeout|refused|pool|operational|broken pipe|reset|eof|closed)",
        re.IGNORECASE,
    )
    message_hits = []
    for s in explicit_swallowed:
        if upstream_keywords.search(s.message):
            message_hits.append(s.exc_type)
    for op in swallowed_ops:
        msg = op.metadata.get("message", "")
        if upstream_keywords.search(msg):
            message_hits.append(op.metadata.get("exc_type", ""))

    if upstream_hits or message_hits:
        signal_types = set(upstream_hits + message_hits) - {""}
        evidence.append(
            f"Swallowed exception content suggests upstream failure: {', '.join(signal_types)}"
        )

    confidence = "likely_cause" if (upstream_hits or message_hits) else "contributing_factor"

    return PatternMatch(
        name="silent_failure",
        confidence=confidence,
        description=(
            "A prior exception was caught and not re-raised. "
            "The current error may be a downstream consequence."
        ),
        evidence=evidence,
    )
