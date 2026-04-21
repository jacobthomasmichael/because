from __future__ import annotations

from typing import TYPE_CHECKING

from because.patterns import pool_exhaustion, silent_failure
from because.patterns.base import PatternMatch

if TYPE_CHECKING:
    from because.enrichment import ContextChain

_PATTERNS = [pool_exhaustion, silent_failure]


def match_all(exc: BaseException, chain: "ContextChain") -> list[PatternMatch]:
    results = []
    for pattern in _PATTERNS:
        try:
            m = pattern.match(exc, chain)
            if m is not None:
                results.append(m)
        except Exception:
            pass  # never let a broken pattern crash the enrichment path
    return results
