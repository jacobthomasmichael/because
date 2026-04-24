from __future__ import annotations

from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from because.enrichment import ContextChain


def chain_to_dict(chain: "ContextChain") -> dict[str, Any]:
    """Return a JSON-serializable dict representation of a ContextChain.

    Compatible with Sentry ``extra``, Datadog span tags, and structured log fields.
    """
    return {
        "patterns": [
            {
                "name": m.name,
                "confidence": m.confidence,
                "description": m.description,
                "evidence": m.evidence,
            }
            for m in chain.pattern_matches
        ],
        "swallowed": [
            {"exc_type": s.exc_type, "message": s.message}
            for s in chain.swallowed
        ],
        "operations": [
            {
                "op_type": op.op_type.value,
                "success": op.success,
                "duration_ms": round(op.duration_ms, 2) if op.duration_ms is not None else None,
                **{k: v for k, v in op.metadata.items() if isinstance(v, (str, int, float, bool, type(None)))},
            }
            for op in chain.operations
        ],
    }


def chain_from_exc(exc: BaseException) -> "ContextChain | None":
    return getattr(exc, "__context_chain__", None)
