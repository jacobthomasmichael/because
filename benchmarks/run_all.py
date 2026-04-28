"""
Run all because benchmarks and update README.md with results.

Usage:
    python benchmarks/run_all.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import bench_overhead
import bench_accuracy
import bench_ttd


MARKER_START = "<!-- BENCHMARK_RESULTS_START -->"
MARKER_END   = "<!-- BENCHMARK_RESULTS_END -->"


def _bar(value: float, max_val: float, width: int = 10) -> str:
    filled = int(value / max_val * width) if max_val else 0
    return "█" * filled + "░" * (width - filled)


def build_section(overhead: dict, accuracy: dict, ttd: dict) -> str:
    date = time.strftime("%Y-%m-%d")

    pe = accuracy["pool_exhaustion"]
    rs = accuracy["retry_storm"]
    sf = accuracy["silent_failure"]

    patterns_detected = ", ".join(f"`{p}`" for p in ttd["patterns_detected"])

    return f"""\
{MARKER_START}
## Benchmarks

*Generated {date} on Python {sys.version.split()[0]}. [Source](benchmarks/).*

### 1 — Ring buffer overhead

The hot-path instrumentation cost per recorded operation:

| Metric | Value |
|---|---|
| Baseline (no-op) | `{overhead['baseline_us']:.3f} µs` |
| `because` record() | `{overhead['record_us']:.3f} µs` |
| **Net overhead** | **`{overhead['overhead_us']:.3f} µs` per op** |
| Snapshot (128 ops) | `{overhead['snapshot_us']:.3f} µs` |

> Overhead is measured as the difference between recording an Op and a bare `time.monotonic()` call.
> At 1,000 ops/sec that's **{overhead['overhead_us'] * 1000:.1f} ms/s** of total overhead — effectively zero.

---

### 2 — Pattern detection accuracy

Each pattern was tested against {pe['n_positive_cases']} positive and {pe['n_negative_cases']} negative scenarios:

| Pattern | Precision | Recall | F1 | TP | FP | FN | TN |
|---|---|---|---|---|---|---|---|
| `pool_exhaustion` | {pe['precision']:.0%} | {pe['recall']:.0%} | {pe['f1']:.2f} | {pe['true_positives']} | {pe['false_positives']} | {pe['false_negatives']} | {pe['true_negatives']} |
| `retry_storm` | {rs['precision']:.0%} | {rs['recall']:.0%} | {rs['f1']:.2f} | {rs['true_positives']} | {rs['false_positives']} | {rs['false_negatives']} | {rs['true_negatives']} |
| `silent_failure` | {sf['precision']:.0%} | {sf['recall']:.0%} | {sf['f1']:.2f} | {sf['true_positives']} | {sf['false_positives']} | {sf['false_negatives']} | {sf['true_negatives']} |

---

### 3 — Time-to-diagnosis: multi-layer cascade

A realistic production incident is simulated:
- Payment service degrades → retry loop fires against `payment.internal` (`retry_storm`)
- DB connection pool saturates → 3 `TimeoutError`s caught and swallowed (`silent_failure`)
- Inventory lookup returns `None` silently
- Final crash: `AttributeError: 'NoneType' has no attribute 'quantity'` (the *symptom*)

**Patterns detected:** {patterns_detected}
**Context captured:** {ttd['n_ops_in_chain']} operations + {ttd['n_swallowed']} swallowed exceptions

| Step | Time |
|---|---|
| Snapshot context chain | `{ttd['chain_snapshot_us']:.1f} µs` |
| Run all pattern matchers | `{ttd['pattern_match_us']:.1f} µs` |
| Format human-readable output | `{ttd['format_us']:.1f} µs` |
| **Total diagnosis time** | **`{ttd['total_us']:.1f} µs`** |

**Without `because`** — engineer sees:
```
{ttd['sample_without'].strip()}
```

**With `because`** — attached to the same exception in `{ttd['total_us']:.0f} µs`:
```
{ttd['sample_with'].strip()}
```
{MARKER_END}"""


def main() -> None:
    print("Running overhead benchmarks...", flush=True)
    overhead = bench_overhead.run()
    print(f"  record: {overhead['record_us']:.3f} µs/op  overhead: {overhead['overhead_us']:.3f} µs")

    print("Running accuracy benchmarks...", flush=True)
    accuracy = bench_accuracy.run()
    for name, r in accuracy.items():
        print(f"  {name}: precision={r['precision']:.0%}  recall={r['recall']:.0%}  f1={r['f1']:.2f}")

    print("Running time-to-diagnosis benchmark...", flush=True)
    ttd = bench_ttd.run()
    print(f"  total diagnosis time: {ttd['total_us']:.1f} µs")
    print(f"  patterns detected: {ttd['patterns_detected']}")

    section = build_section(overhead, accuracy, ttd)

    readme = ROOT / "README.md"
    content = readme.read_text()

    if MARKER_START in content and MARKER_END in content:
        before = content[:content.index(MARKER_START)]
        after = content[content.index(MARKER_END) + len(MARKER_END):]
        content = before + section + after
    else:
        # insert before the first ## section after the intro
        insert_at = content.find("\n## ")
        if insert_at == -1:
            content = content + "\n\n" + section
        else:
            content = content[:insert_at] + "\n\n" + section + "\n" + content[insert_at:]

    readme.write_text(content)
    print(f"\nREADME.md updated with benchmark results.")


if __name__ == "__main__":
    main()
