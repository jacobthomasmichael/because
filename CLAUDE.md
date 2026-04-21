# CLAUDE.md

This file provides context to Claude Code when working in this repository.

---

## Project: `because` (working name)

A Python library that enriches exceptions at throw time with a plain-English causal chain derived from recent in-process context. Drop-in adoption, no platform lock-in, works alongside existing Sentry / Datadog / OpenTelemetry setups.

---

## The problem

Stack traces in logs, observability platforms, and error trackers usually show the *symptom*, not the *cause*. A `ConnectionError` at the database layer might really be a connection pool exhaustion triggered by a recent deploy that added a synchronous DB call to a hot path. Engineers spend disproportionate time walking backwards through logs, traces, and recent changes to reconstruct the causal chain.

Analogy: a car dashboard says "ABS error" but the real root cause is a dying battery dropping voltage across the CAN bus. Today's error tooling stops at "ABS error."

---

## Target output

When an exception is thrown, the error payload gets decorated with structured context like:

```
ConnectionError: connection refused on localhost:5432
  at db/pool.py:142

[Context]
  Likely cause: Postgres connection pool exhausted (48/50 active for 30s before failure)
  Contributing: elevated request rate on /api/checkout in prior 60s
  Caught-and-swallowed: 3 TimeoutErrors in db/pool.py in prior 10s
  Recent operations: [timeline of last N DB/HTTP/cache calls on this thread]
```

---

## V0 scope (what ships first)

Python library, three components:

### 1. Ring buffer instrumentation
Auto-hook into common libraries (`requests`, `httpx`, `sqlalchemy`, `redis-py`, `logging`, stdlib `socket` if feasible) to capture a rolling per-thread / per-asyncio-task timeline of recent operations. Near-zero overhead — bounded buffer, no I/O on the hot path. Leverage OpenTelemetry instrumentation libraries where they already exist rather than reinventing.

### 2. Exception enrichment hook
On exception, attach a `__context_chain__` attribute (or similar) containing:
- The recent operation timeline for the current execution context
- Resource state snapshots (pool stats, open file handles, memory if cheap)
- Recently caught-and-swallowed exceptions in the same context (huge signal — often the real cause)
- Matched heuristic patterns (see below)

Install via `sys.excepthook`, context manager, framework middleware (Flask / FastAPI / Django), and/or decorators.

### 3. Heuristic pattern library
A starter set of known cascade patterns, e.g.:
- `connection refused` + pool at capacity → pool exhaustion
- `timeout` + recent retry storm → upstream degradation
- `NoneType has no attribute` + recent caught exception → silent failure upstream
- `OOM` + recent memory growth → leak or load spike

Each pattern is a small, testable unit. Deterministic, explainable, fast. **This is the v0 intelligence — LLM explainer comes later.**

---

## Explicitly out of scope for V0

- LLM-based explanation (add in v0.2 as an optional, async, deferred enricher with BYO API key)
- Cross-process / cross-service causal reasoning (that's the sidecar/agent story — v1+)
- Non-Python languages
- Hosted backend, dashboard, or SaaS component
- Distributed tracing replacement — this complements traces, doesn't replace them

---

## Design principles

- **Honest framing.** Output uses "likely cause," "contributing factors," "recent operations to consider." Never confident RCA claims. Wrong-but-confident loses trust instantly.
- **Zero-config default.** `import because; because.install()` should do something useful immediately.
- **No hot-path cost.** Instrumentation is bounded ring buffers, lock-free where possible. Enrichment runs only on exception.
- **Composable with existing tools.** Output should serialize cleanly into Sentry `extra`, Datadog error attributes, structured log fields. Don't fight the existing ecosystem.
- **Library, not platform.** Ship as a pip package. No required backend.

---

## Tech stack

- Python 3.10+ (modern typing, better asyncio introspection)
- Core in pure Python, no C extensions in v0
- `pytest` + `hypothesis` for property-based tests on pattern matchers
- `contextvars` for per-request / per-task context
- Optional integrations behind extras: `because[sqlalchemy]`, `because[fastapi]`, etc.

---

## Repo layout (suggested)

```
because/
  __init__.py          # public API: install(), enrich(), etc.
  buffer.py            # ring buffer, contextvar plumbing
  instruments/         # one module per instrumented library
    sqlalchemy.py
    requests.py
    httpx.py
    redis.py
    logging.py
  patterns/            # heuristic pattern library
    pool_exhaustion.py
    retry_storm.py
    silent_failure.py
  enrichment.py        # exception hook, context chain assembly
  integrations/        # sentry, datadog, logging formatters
  cli.py               # optional: `because explain <paste stack trace>`
tests/
examples/              # runnable demos of each cascade pattern
```

---

## First milestones

1. **Ring buffer + contextvar plumbing**, with `sqlalchemy` and `requests` as the two reference instruments.
2. **Exception hook** that dumps the raw timeline on any uncaught exception.
3. **First two heuristic patterns:** pool exhaustion, silent failure (caught-and-swallowed upstream).
4. **Runnable `examples/` demo for each pattern** — a tiny Flask app that deliberately triggers the cascade, showing before/after output.
5. **README** with the ABS/battery framing, the before/after example, and install instructions.

The `examples/` directory is important — it's what turns this into a portfolio piece. Each example is a blog-post-sized story.

---

## Open questions to resolve early

- **Naming.** Check PyPI availability. Alternatives if `because` is taken: `postmortem`, `rootcause`, `whence`, `tracelight`.
- **Enrichment mechanism.** Does enrichment mutate the exception object, wrap it, or attach via a side-channel registry? Each has tradeoffs for downstream tooling compatibility — decide before building the hook.
- **Sync vs async.** asyncio task context needs deliberate design. `contextvars` is the right primitive but task boundaries matter.

---

## Working preferences

- Direct, minimal communication. No filler, no hedging where facts are known.
- Show the code and the reasoning; skip the preamble.
- When making design decisions, call out tradeoffs explicitly rather than picking silently.
- Tests alongside code — don't defer testing to a later milestone.
- Prefer boring, well-understood tools over novel ones unless there's a clear reason.
