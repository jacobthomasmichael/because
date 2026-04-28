"""
Benchmark 3: Time-to-diagnosis on a complex multi-pattern cascade.

Simulates a realistic production incident:

  /api/checkout under elevated load
  ├── Payment service starts timing out → naive retry loop fires (retry_storm)
  ├── DB connection pool saturates → 3 TimeoutErrors caught and swallowed (silent_failure)
  ├── Inventory check fails silently → returns None
  └── Final crash: AttributeError on None.quantity (the *symptom*)

Without because: engineer sees only "AttributeError: 'NoneType' object has no
attribute 'quantity'" and must manually correlate logs, traces, and recent deploys.

With because: full causal chain attached to the exception in microseconds —
all three contributing patterns identified before the error even hits the logger.

Measures:
  - Time to build the simulated context (instrument overhead)
  - Time to enrich the exception (pattern matching)
  - Time to format the human-readable chain
  - Total wall time from "exception raised" to "diagnosis ready"
  - What the engineer sees with vs. without because
"""
from __future__ import annotations

import time
import traceback
from io import StringIO

from because.buffer import Op, OpType, RingBuffer, _ctx_buffer
from because.enrichment import (
    ContextChain, SwallowedExc, enrich, format_context_chain,
)
from because.patterns import match_all


# ── simulate the incident ─────────────────────────────────────────────────────

def _simulate_incident() -> tuple[RingBuffer, list[SwallowedExc], BaseException]:
    """Build up a realistic context buffer, then trigger the cascade."""
    buf = RingBuffer(maxsize=128)

    t = time.monotonic

    def op(op_type, success=True, **meta):
        buf.record(Op(op_type=op_type, timestamp=t(), duration_ms=5.0,
                      success=success, metadata=meta))

    # ── normal checkout ops before the incident ───────────────────────────────
    op(OpType.HTTP_REQUEST, True,  method="GET",  url="https://auth.internal/verify")
    op(OpType.DB_QUERY,     True,  statement="SELECT * FROM users WHERE id=$1")
    op(OpType.HTTP_REQUEST, True,  method="GET",  url="https://catalog.internal/items")
    op(OpType.DB_QUERY,     True,  statement="SELECT stock FROM inventory WHERE sku=$1")
    op(OpType.HTTP_REQUEST, True,  method="POST", url="https://payment.internal/charge")

    # ── payment service degrades → retry storm ────────────────────────────────
    for _ in range(5):
        op(OpType.HTTP_REQUEST, False, method="POST",
           url="https://payment.internal/charge", error="ReadTimeout: timed out")

    # ── DB pool saturates under retry load ────────────────────────────────────
    swallowed: list[SwallowedExc] = []
    for i in range(3):
        op(OpType.DB_QUERY, False, statement="BEGIN",
           error="TimeoutError: pool timeout after 30s")
        swallowed.append(SwallowedExc(
            exc_type="TimeoutError",
            message=f"pool timeout after 30s (db/pool.py:142) attempt {i+1}",
            timestamp=t(),
        ))

    # ── more failed DB ops ────────────────────────────────────────────────────
    op(OpType.DB_QUERY, False, statement="SELECT quantity FROM inventory WHERE sku=$1",
       error="OperationalError: server closed the connection unexpectedly")
    op(OpType.DB_QUERY, False, statement="UPDATE orders SET status='pending' WHERE id=$1",
       error="OperationalError: connection refused")

    # ── inventory lookup silently returns None ────────────────────────────────
    op(OpType.HTTP_REQUEST, True, method="GET",
       url="https://catalog.internal/items/qty", status_code=200)

    # ── the crash: downstream AttributeError on the None result ───────────────
    try:
        result = None  # silently failed DB lookup returned None
        _ = result.quantity  # type: ignore[union-attr]
    except AttributeError as exc:
        return buf, swallowed, exc

    raise RuntimeError("unreachable")


# ── without because ───────────────────────────────────────────────────────────

def diagnosis_without_because(exc: BaseException) -> str:
    out = StringIO()
    out.write("AttributeError: 'NoneType' object has no attribute 'quantity'\n")
    out.write("  File \"app/api/checkout.py\", line 84, in process_order\n")
    out.write("    total = result.quantity * item.price\n")
    out.write("\n[No further context available. Manual log correlation required.]\n")
    return out.getvalue()


# ── with because ─────────────────────────────────────────────────────────────

def diagnosis_with_because(
    exc: BaseException,
    buf: RingBuffer,
    swallowed: list[SwallowedExc],
) -> tuple[str, float, float, float]:
    t0 = time.perf_counter()

    # attach chain (simulates what enrichment hook does at throw time)
    chain = ContextChain(operations=buf.snapshot(), swallowed=swallowed)

    t1 = time.perf_counter()

    # run all pattern matchers
    chain.pattern_matches = match_all(exc, chain)

    t2 = time.perf_counter()

    exc.__context_chain__ = chain  # type: ignore[attr-defined]
    output = format_context_chain(exc)

    t3 = time.perf_counter()

    chain_time_us = (t1 - t0) * 1e6
    pattern_time_us = (t2 - t1) * 1e6
    format_time_us = (t3 - t2) * 1e6

    out = StringIO()
    out.write("AttributeError: 'NoneType' object has no attribute 'quantity'\n")
    out.write("  File \"app/api/checkout.py\", line 84, in process_order\n")
    out.write("    total = result.quantity * item.price\n")
    out.write(output)
    out.write("\n")

    return out.getvalue(), chain_time_us, pattern_time_us, format_time_us


# ── runner ────────────────────────────────────────────────────────────────────

N_TIMING_TRIALS = 1000


def run() -> dict:
    # single run for output sample
    buf, swallowed, exc = _simulate_incident()
    without = diagnosis_without_because(exc)

    # re-simulate fresh exc for with-because (clear __context_chain__ if set)
    buf2, swallowed2, exc2 = _simulate_incident()
    with_str, chain_us, pattern_us, format_us = diagnosis_with_because(exc2, buf2, swallowed2)

    # timing trials for stable numbers
    chain_times, pattern_times, format_times = [], [], []
    for _ in range(N_TIMING_TRIALS):
        b, sw, e = _simulate_incident()
        _, c, p, f = diagnosis_with_because(e, b, sw)
        chain_times.append(c)
        pattern_times.append(p)
        format_times.append(f)

    import statistics
    return {
        "chain_snapshot_us": round(statistics.mean(chain_times), 2),
        "pattern_match_us": round(statistics.mean(pattern_times), 2),
        "format_us": round(statistics.mean(format_times), 2),
        "total_us": round(
            statistics.mean(chain_times) +
            statistics.mean(pattern_times) +
            statistics.mean(format_times), 2
        ),
        "patterns_detected": [m.name for m in exc2.__context_chain__.pattern_matches],
        "n_ops_in_chain": len(exc2.__context_chain__.operations),
        "n_swallowed": len(exc2.__context_chain__.swallowed),
        "n_timing_trials": N_TIMING_TRIALS,
        "sample_without": without,
        "sample_with": with_str,
    }


if __name__ == "__main__":
    import json
    result = run()
    print("\n" + "="*60 + " WITHOUT because")
    print(result["sample_without"])
    print("="*60 + " WITH because")
    print(result["sample_with"])
    print("="*60)
    del result["sample_without"]
    del result["sample_with"]
    print(json.dumps(result, indent=2))
