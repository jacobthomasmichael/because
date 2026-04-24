# because

Stack traces show symptoms. `because` shows causes.

---

## The problem

Your monitoring dashboard lights up with `ConnectionError: connection refused on localhost:5432`. The stack trace points to `db/pool.py:142`. You have no idea why.

The real story: a recent deploy added a synchronous DB call to a hot path. Under load, 48 of 50 connections filled up. Three `TimeoutError`s were silently swallowed over the prior 10 seconds. By the time the `ConnectionError` fired, all the context was gone.

This is the ABS warning light telling you the battery is dead. Today's error tooling stops at the symptom.

`because` captures the context *before* the crash and attaches it to the exception at throw time.

---

## What it looks like

**Without because:**
```
ConnectionError: connection refused on localhost:5432
  File "db/pool.py", line 142, in execute
```

**With because:**
```
ConnectionError: connection refused on localhost:5432
  File "db/pool.py", line 142, in execute

[because context]
  Likely cause: Database connection pool may be exhausted
    • Exception message contains 'QueuePool limit'
    • 8 DB queries in context window (pool was active)
    • 5/8 recent DB queries failed (63% failure rate)
  Recent operations (8):
    [ok] db_query          2.1ms  SELECT * FROM orders WHERE user_id = ?
    [ok] db_query          1.8ms  SELECT * FROM orders WHERE user_id = ?
    [ok] db_query          2.4ms  SELECT * FROM orders WHERE user_id = ?
    [FAIL] db_query        0.5ms  SELECT * FROM orders WHERE user_id = ?  error=OperationalError
    [FAIL] db_query        0.5ms  SELECT * FROM orders WHERE user_id = ?  error=OperationalError
    [FAIL] db_query        0.5ms  SELECT * FROM orders WHERE user_id = ?  error=OperationalError
    [FAIL] db_query        0.5ms  SELECT * FROM orders WHERE user_id = ?  error=OperationalError
    [FAIL] db_query        0.5ms  SELECT * FROM orders WHERE user_id = ?  error=OperationalError
```

---

## Install

```bash
pip install because-py
```

With instrumentation extras:

```bash
pip install "because-py[sqlalchemy]"
pip install "because-py[sqlalchemy,requests,httpx]"
```

With the LLM explainer:

```bash
pip install "because-py[llm]"        # Anthropic (default)
pip install "because-py[llm,openai]" # + OpenAI support
```

---

## Zero-config setup

```python
import because
because.install()
```

That's it. `because` hooks `sys.excepthook` and starts recording operations in the background. Any uncaught exception automatically gets enriched context appended to stderr — no changes to your exception handlers required.

---

## Instrumenting libraries

`because` ships with instruments for common libraries. Attach them to your existing clients:

```python
from because.instruments.sqlalchemy import instrument as instrument_sa
from because.instruments.requests import instrument as instrument_requests
from because.instruments.httpx import instrument as instrument_httpx
from because.instruments.redis import instrument as instrument_redis
from because.instruments.logging import instrument as instrument_logging
import requests

instrument_sa(engine)                  # SQLAlchemy engine
instrument_requests(requests.Session()) # requests Session
instrument_httpx(httpx_client)         # httpx Client or AsyncClient
instrument_redis(redis_client)         # redis-py client (sync or async)
instrument_logging()                   # root logger (WARNING and above)
```

Each instrument records operation timing, success/failure, and relevant metadata into a per-thread/per-task ring buffer. Zero I/O on the hot path.

---

## Framework integrations

```python
# Flask
from because.integrations.flask import instrument as instrument_flask
instrument_flask(app)

# FastAPI
from because.integrations.fastapi import BecauseMiddleware
app.add_middleware(BecauseMiddleware)

# Django — add to MIDDLEWARE in settings.py
MIDDLEWARE = [
    "because.integrations.django.BecauseMiddleware",
    ...
]
```

---

## Recording swallowed exceptions

Caught-and-not-reraised exceptions are often the real cause of a downstream crash. Wrap risky calls with `because.catch()` to make them visible:

```python
def get_user(user_id):
    with because.catch(Exception):
        return db.query(User).filter_by(id=user_id).one()
    return None  # reached only if exception was swallowed
```

When a downstream `AttributeError: 'NoneType' object has no attribute 'email'` fires, `because` will surface the swallowed DB error as the likely cause.

---

## Enriching caught exceptions manually

For exceptions you handle yourself:

```python
try:
    process_order(order_id)
except Exception as exc:
    because.enrich_with_swallowed(exc)
    logger.error("Order processing failed", extra={"context": exc.__context_chain__})
    raise
```

`__context_chain__` serializes cleanly into Sentry `extra`, Datadog error attributes, or structured log fields.

---

## LLM-based explanation (v0.2)

Get a plain-English root cause analysis powered by Claude or GPT-4o:

```python
import because

because.configure_llm(api_key="sk-ant-...")  # Anthropic by default

try:
    risky_operation()
except Exception as exc:
    because.enrich_with_swallowed(exc)
    explanation = await because.explain_async(exc)
    print(explanation.root_cause)
    print(explanation.suggested_fix)
```

Sync version (avoid in async contexts):

```python
explanation = because.explain(exc)
```

Use OpenAI instead:

```python
because.configure_llm(api_key="sk-...", provider="openai")
```

Bring your own provider by implementing the `LLMProvider` protocol:

```python
class MyProvider:
    async def complete(self, prompt: str) -> str:
        ...
```

---

## CLI

Analyze any stack trace from the command line — no code changes required:

```bash
# pipe from a log file
cat error.log | because explain

# or pass a file directly
because explain error.log

# paste interactively (Ctrl-D to submit)
because explain

# use OpenAI instead
because explain --provider openai --model gpt-4o error.log
```

Reads `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` from the environment, or pass `--api-key` directly.

---

## Heuristic patterns

`because` ships with deterministic cascade patterns that run at throw time — no API key required:

| Pattern | Fires when |
|---|---|
| `pool_exhaustion` | Connection/pool error + recent DB activity or explicit pool message |
| `silent_failure` | Swallowed exception preceded the current error |
| `retry_storm` | Timeout + high concentration of repeated HTTP requests to the same host |

Each pattern is a small, independently testable unit. Output always uses hedged language ("likely cause", "contributing factor") — `because` never claims certainty.

---

## Observability integrations

```python
# Sentry
from because.integrations.sentry import before_send
sentry_sdk.init(..., before_send=before_send)

# Datadog
from because.integrations.datadog import tag_current_span
tag_current_span(exc)

# Structured logging
from because.integrations.logging import BecauseFormatter
handler.setFormatter(BecauseFormatter())
```

---

## Design principles

- **Honest framing.** Output uses "likely cause" and "contributing factor." Wrong-but-confident destroys trust.
- **Zero-config default.** `import because; because.install()` does something useful immediately.
- **No hot-path cost.** Instrumentation is bounded ring buffers. Enrichment runs only on exception.
- **Composable with existing tools.** Attaches to Sentry, Datadog, and structured logging — doesn't replace them.
- **Library, not platform.** Pure Python, no required backend, ships as a pip package.

---

## Examples

Runnable demos in `examples/`:

```bash
python examples/pool_exhaustion.py   # connection pool saturated under load
python examples/silent_failure.py    # swallowed DB error causes downstream crash
```

Each demo shows the cascade being triggered and `because` surfacing the cause.

---

## Roadmap

- **v1.0** — Cross-process / cross-service causal reasoning
- More instruments: stdlib `socket`, `grpc`
- OpenTelemetry span export
