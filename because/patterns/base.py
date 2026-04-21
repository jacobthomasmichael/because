from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from because.enrichment import ContextChain


@dataclass(slots=True)
class PatternMatch:
    name: str
    confidence: str  # "likely_cause" | "contributing_factor"
    description: str
    evidence: list[str] = field(default_factory=list)


class Pattern(Protocol):
    def match(self, exc: BaseException, chain: "ContextChain") -> PatternMatch | None:
        ...
