"""
Benchmark 1: Ring buffer write and snapshot overhead.

Measures the per-operation cost of recording into because's ring buffer
versus a no-op baseline, showing the hot-path overhead is negligible.
"""
from __future__ import annotations

import statistics
import time

from because.buffer import Op, OpType, RingBuffer, _ctx_buffer

N_OPS = 100_000
N_TRIALS = 5


def _make_op() -> Op:
    return Op(
        op_type=OpType.DB_QUERY,
        timestamp=time.monotonic(),
        duration_ms=1.2,
        success=True,
        metadata={"statement": "SELECT 1"},
    )


def bench_record() -> list[float]:
    """Cost of recording one Op into a live RingBuffer via get_context()."""
    from because.buffer import record

    buf = RingBuffer(maxsize=128)
    token = _ctx_buffer.set(buf)
    trial_times = []
    try:
        for _ in range(N_TRIALS):
            start = time.perf_counter()
            for _ in range(N_OPS):
                record(OpType.DB_QUERY, duration_ms=1.2, success=True,
                       statement="SELECT 1")
            elapsed = time.perf_counter() - start
            trial_times.append(elapsed / N_OPS * 1e6)  # µs per op
            buf._buf.clear()
    finally:
        _ctx_buffer.reset(token)
    return trial_times


def bench_baseline() -> list[float]:
    """Cost of a comparable no-op (just calling time.monotonic())."""
    trial_times = []
    for _ in range(N_TRIALS):
        start = time.perf_counter()
        for _ in range(N_OPS):
            _ = time.monotonic()
        elapsed = time.perf_counter() - start
        trial_times.append(elapsed / N_OPS * 1e6)
    return trial_times


def bench_snapshot() -> list[float]:
    """Cost of snapshotting a full 128-op buffer."""
    buf = RingBuffer(maxsize=128)
    for _ in range(128):
        buf.record(_make_op())

    trial_times = []
    for _ in range(N_TRIALS):
        start = time.perf_counter()
        for _ in range(N_OPS):
            buf.snapshot()
        elapsed = time.perf_counter() - start
        trial_times.append(elapsed / N_OPS * 1e6)
    return trial_times


def run() -> dict:
    baseline = bench_baseline()
    record_times = bench_record()
    snapshot_times = bench_snapshot()

    overhead = statistics.mean(record_times) - statistics.mean(baseline)

    return {
        "baseline_us": statistics.mean(baseline),
        "record_us": statistics.mean(record_times),
        "record_stdev_us": statistics.stdev(record_times),
        "overhead_us": overhead,
        "snapshot_us": statistics.mean(snapshot_times),
        "snapshot_stdev_us": statistics.stdev(snapshot_times),
        "n_ops": N_OPS,
        "n_trials": N_TRIALS,
    }


if __name__ == "__main__":
    import json
    print(json.dumps(run(), indent=2))
