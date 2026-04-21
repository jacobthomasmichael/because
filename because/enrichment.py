from __future__ import annotations

import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Iterator, Type

from because.buffer import Op, OpType, get_context, record
from because.patterns.base import PatternMatch


@dataclass(slots=True)
class SwallowedExc:
    exc_type: str
    message: str
    timestamp: float


@dataclass(slots=True)
class ContextChain:
    operations: list[Op]
    swallowed: list[SwallowedExc] = field(default_factory=list)
    pattern_matches: list[PatternMatch] = field(default_factory=list)


def enrich(exc: BaseException) -> BaseException:
    """Snapshot current context, run patterns, attach as __context_chain__. Idempotent."""
    if not hasattr(exc, "__context_chain__"):
        from because.patterns import match_all
        chain = ContextChain(operations=get_context().snapshot())
        chain.pattern_matches = match_all(exc, chain)
        exc.__context_chain__ = chain  # type: ignore[attr-defined]
    return exc


def format_context_chain(exc: BaseException) -> str:
    chain: ContextChain | None = getattr(exc, "__context_chain__", None)
    if chain is None:
        return ""

    lines = ["", "[because context]"]

    if chain.pattern_matches:
        for m in chain.pattern_matches:
            label = "Likely cause" if m.confidence == "likely_cause" else "Contributing factor"
            lines.append(f"  {label}: {m.description}")
            for ev in m.evidence:
                lines.append(f"    • {ev}")

    if chain.swallowed:
        lines.append(f"  Caught-and-swallowed ({len(chain.swallowed)}):")
        for s in chain.swallowed:
            lines.append(f"    {s.exc_type}: {s.message}")

    if chain.operations:
        lines.append(f"  Recent operations ({len(chain.operations)}):")
        for op in chain.operations[-20:]:
            status = "ok" if op.success else "FAIL"
            dur = f"{op.duration_ms:.1f}ms" if op.duration_ms is not None else "—"
            meta = _format_meta(op)
            lines.append(f"    [{status}] {op.op_type.value:<14} {dur:>8}  {meta}")
    else:
        lines.append("  No recent operations recorded.")

    return "\n".join(lines)


def _format_meta(op: Op) -> str:
    m = op.metadata
    if op.op_type == OpType.DB_QUERY:
        stmt = m.get("statement", "")[:60]
        suffix = "…" if len(m.get("statement", "")) > 60 else ""
        error = f"  error={m['error']}" if "error" in m else ""
        return f"{stmt}{suffix}{error}"
    if op.op_type == OpType.HTTP_REQUEST:
        error = f"  error={m['error']}" if "error" in m else f"  {m.get('status_code', '')}"
        return f"{m.get('method', '')} {m.get('url', '')}{error}"
    if op.op_type == OpType.EXCEPTION_SWALLOWED:
        return m.get("exc_type", "")
    return str(m) if m else ""


_original_excepthook = sys.excepthook
_installed = False


def install() -> None:
    global _installed
    if _installed:
        return
    _installed = True
    sys.excepthook = _because_excepthook


def _because_excepthook(
    exc_type: Type[BaseException],
    exc_value: BaseException,
    exc_tb,
) -> None:
    enrich(exc_value)
    _original_excepthook(exc_type, exc_value, exc_tb)
    print(format_context_chain(exc_value), file=sys.stderr)


@contextmanager
def catch(*exc_types: Type[BaseException]) -> Iterator[None]:
    """Context manager that records swallowed exceptions into the ring buffer.

    Usage::

        with because.catch(TimeoutError, ConnectionError):
            risky_operation()
    """
    types = exc_types or (Exception,)
    try:
        yield
    except tuple(types) as exc:  # type: ignore[misc]
        record(
            OpType.EXCEPTION_SWALLOWED,
            duration_ms=None,
            success=False,
            exc_type=type(exc).__name__,
            message=str(exc)[:200],
        )
        # also track on the current context chain if one is being built
        buf = get_context()
        _note_swallowed(buf, exc)


def _note_swallowed(buf, exc: BaseException) -> None:
    swallowed = SwallowedExc(
        exc_type=type(exc).__name__,
        message=str(exc)[:200],
        timestamp=time.monotonic(),
    )
    if not hasattr(buf, "_swallowed"):
        buf._swallowed: list[SwallowedExc] = []
    buf._swallowed.append(swallowed)


def enrich_with_swallowed(exc: BaseException) -> BaseException:
    """Like enrich() but also pulls in recorded swallowed exceptions, then re-runs patterns."""
    enrich(exc)
    from because.patterns import match_all
    buf = get_context()
    chain: ContextChain = exc.__context_chain__  # type: ignore[attr-defined]
    chain.swallowed = list(getattr(buf, "_swallowed", []))
    chain.pattern_matches = match_all(exc, chain)
    return exc
