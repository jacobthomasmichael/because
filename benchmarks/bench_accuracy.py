"""
Benchmark 2: Pattern detection accuracy (precision & recall).

Runs each heuristic pattern against a labeled set of positive and negative
scenarios. Reports true/false positives, precision, recall, and F1.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from because.buffer import Op, OpType
from because.enrichment import ContextChain, SwallowedExc
from because.patterns.base import PatternMatch
from because.patterns import pool_exhaustion, retry_storm, silent_failure


def _op(op_type: OpType, success: bool = True, **meta) -> Op:
    return Op(op_type=op_type, timestamp=time.monotonic(),
              duration_ms=5.0, success=success, metadata=meta)


def _chain(*ops, swallowed=None) -> ContextChain:
    return ContextChain(
        operations=list(ops),
        swallowed=swallowed or [],
    )


# ── pool_exhaustion test cases ────────────────────────────────────────────────

POOL_POSITIVES = [
    # definitive keyword in message
    (OperationalError := type("OperationalError", (Exception,), {}))(
        "QueuePool limit of size 5 overflow 10 reached"
    ),
    (PoolTimeout := type("PoolTimeout", (Exception,), {}))("pool_timeout exceeded"),
    (ConnectionError2 := type("ConnectionError", (Exception,), {}))(
        "connection pool exhausted after 30s"
    ),
    # soft keyword + high DB failure rate
    (ConnRefused := type("ConnectionRefusedError", (Exception,), {}))(
        "connection refused on localhost:5432"
    ),
    # urllib3 pool
    (MaxRetries := type("MaxRetries", (Exception,), {}))(
        "Max retries exceeded with url: /api"
    ),
]

POOL_POSITIVE_CHAINS = [
    # lots of failed DB ops for the soft-match cases
    _chain(*[_op(OpType.DB_QUERY, False, statement="SELECT 1")] * 8),
    _chain(*[_op(OpType.DB_QUERY, False, statement="SELECT 1")] * 5),
    _chain(*[_op(OpType.DB_QUERY, False, statement="SELECT 1")] * 6),
    _chain(*[_op(OpType.DB_QUERY, False, statement="BEGIN")] * 4 +
            [_op(OpType.DB_QUERY, True)] * 1),
    _chain(*[_op(OpType.HTTP_REQUEST, False, url="https://api.example.com/data")] * 6),
]

POOL_NEGATIVES = [
    ValueError("invalid literal for int()"),
    KeyError("user_id"),
    RuntimeError("list index out of range"),
    AttributeError("NoneType has no attribute 'email'"),
    TypeError("unsupported operand type"),
]

POOL_NEGATIVE_CHAINS = [_chain() for _ in POOL_NEGATIVES]


# ── retry_storm test cases ────────────────────────────────────────────────────

RETRY_POSITIVES = [
    (TimeoutError2 := type("TimeoutError", (Exception,), {}))("read timeout"),
    (ReadTimeout := type("ReadTimeout", (Exception,), {}))("timed out waiting for response"),
    (ConnTimeout := type("ConnectTimeout", (Exception,), {}))("connect timeout"),
    (TimeoutError3 := type("TimeoutError", (Exception,), {}))("deadline exceeded"),
    (TimeoutError4 := type("TimeoutError", (Exception,), {}))("timed out"),
]

def _retry_chain(n_fail: int = 6, n_ok: int = 1) -> ContextChain:
    url = "https://payment.internal/charge"
    return _chain(
        *[_op(OpType.HTTP_REQUEST, False, url=url, method="POST")] * n_fail,
        *[_op(OpType.HTTP_REQUEST, True, url=url, method="POST")] * n_ok,
    )

RETRY_POSITIVE_CHAINS = [_retry_chain() for _ in RETRY_POSITIVES]

RETRY_NEGATIVES = [
    (TimeoutError5 := type("TimeoutError", (Exception,), {}))("timed out"),  # no HTTP ops at all
    ValueError("timeout config invalid"),                                      # not a timeout exc
    RuntimeError("connection timed out"),                                      # only DB ops
    (TimeoutError7 := type("TimeoutError", (Exception,), {}))("timed out"),  # too few HTTP ops
    KeyError("upstream_service"),                                              # unrelated error
]

RETRY_NEGATIVE_CHAINS = [
    _chain(),                                                  # no ops → below MIN_HTTP_OPS
    _chain(),                                                  # no timeout signal
    _chain(*[_op(OpType.DB_QUERY, False)] * 6),               # only DB ops, no HTTP
    _chain(*[_op(OpType.HTTP_REQUEST, False,                   # only 3 HTTP ops → below MIN
                 url="https://api.example.com")] * 3),
    _chain(*[_op(OpType.HTTP_REQUEST, True,                    # high volume, low failure rate
                 url=f"https://host{i}.com/path") for i in range(6)]),
]


# ── silent_failure test cases ─────────────────────────────────────────────────

SF_POSITIVES = [
    AttributeError("'NoneType' object has no attribute 'email'"),
    AttributeError("'NoneType' object has no attribute 'id'"),
    RuntimeError("downstream processing failed"),
    KeyError("user"),
    TypeError("expected str, got NoneType"),
]

def _sf_chain(exc_type: str, msg: str) -> ContextChain:
    return _chain(
        swallowed=[SwallowedExc(exc_type, msg, time.monotonic())]
    )

SF_POSITIVE_CHAINS = [
    _sf_chain("OperationalError", "connection refused on db:5432"),
    _sf_chain("TimeoutError", "pool timeout after 30s"),
    _sf_chain("ConnectionError", "remote end closed connection"),
    _sf_chain("KeyError", "user_id missing from session"),
    _sf_chain("AttributeError", "NoneType has no attribute 'fetch'"),
]

SF_NEGATIVES = [
    ValueError("bad input"),
    RuntimeError("unexpected state"),
    KeyError("config"),
    TypeError("wrong type"),
    AttributeError("missing field"),
]

SF_NEGATIVE_CHAINS = [_chain() for _ in SF_NEGATIVES]


# ── scorer ────────────────────────────────────────────────────────────────────

@dataclass
class PatternResult:
    name: str
    tp: int
    fp: int
    fn: int
    tn: int

    @property
    def precision(self) -> float:
        return self.tp / (self.tp + self.fp) if (self.tp + self.fp) else 0.0

    @property
    def recall(self) -> float:
        return self.tp / (self.tp + self.fn) if (self.tp + self.fn) else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) else 0.0


def _score(pattern_mod, positives, pos_chains, negatives, neg_chains) -> PatternResult:
    name = pattern_mod.__name__.split(".")[-1]
    tp = fp = fn = tn = 0

    for exc, chain in zip(positives, pos_chains):
        result = pattern_mod.match(exc, chain)
        if result is not None:
            tp += 1
        else:
            fn += 1

    for exc, chain in zip(negatives, neg_chains):
        result = pattern_mod.match(exc, chain)
        if result is not None:
            fp += 1
        else:
            tn += 1

    return PatternResult(name=name, tp=tp, fp=fp, fn=fn, tn=tn)


def run() -> dict:
    results = [
        _score(pool_exhaustion, POOL_POSITIVES, POOL_POSITIVE_CHAINS,
               POOL_NEGATIVES, POOL_NEGATIVE_CHAINS),
        _score(retry_storm, RETRY_POSITIVES, RETRY_POSITIVE_CHAINS,
               RETRY_NEGATIVES, RETRY_NEGATIVE_CHAINS),
        _score(silent_failure, SF_POSITIVES, SF_POSITIVE_CHAINS,
               SF_NEGATIVES, SF_NEGATIVE_CHAINS),
    ]

    return {
        r.name: {
            "true_positives": r.tp,
            "false_positives": r.fp,
            "false_negatives": r.fn,
            "true_negatives": r.tn,
            "precision": round(r.precision, 3),
            "recall": round(r.recall, 3),
            "f1": round(r.f1, 3),
            "n_positive_cases": len(POOL_POSITIVES),
            "n_negative_cases": len(POOL_NEGATIVES),
        }
        for r in results
    }


if __name__ == "__main__":
    import json
    print(json.dumps(run(), indent=2))
