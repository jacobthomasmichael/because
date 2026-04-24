# because

**Stack traces show symptoms. `because` shows causes.**

Your error tracker fires. The stack trace points to `db/pool.py:142`. You open the file, stare at the code, and start the slow walk backwards through logs, traces, and recent deploys — trying to reconstruct what actually happened.

`because` does that reconstruction for you. It captures a rolling timeline of recent operations in-process, matches known failure patterns at throw time, and — optionally — sends the full context to an LLM for a plain-English explanation. All before you open a single log file.

---

## Before and after

**Before `because`:**
```
sqlalchemy.exc.TimeoutError: QueuePool limit of size 5 overflow 10 reached,
connection timed out, timeout 30
  File "app/api/checkout.py", line 54, in handle_checkout
    result = db.execute(query)
```
You have a pool error. You don't know why.

**After `because`:**
```
sqlalchemy.exc.TimeoutError: QueuePool limit of size 5 overflow 10 reached,
connection timed out, timeout 30
  File "app/api/checkout.py", line 54, in handle_checkout
    result = db.execute(query)

[because context]
  Likely cause: Database connection pool may be exhausted
    • Exception message contains 'QueuePool limit'
    • 12 DB queries in context window (pool was active)
    • 9/12 recent DB queries failed (75% failure rate)
  Caught-and-swallowed (2):
    OperationalError: server closed the connection unexpectedly  (8.3s ago)
    OperationalError: server closed the connection unexpectedly  (3.1s ago)
  Recent operations (12):
    [ok]   db_query       2.1ms  SELECT * FROM orders WHERE user_id = ?
    [ok]   db_query       1.8ms  SELECT * FROM orders WHERE user_id = ?
    [ok]   db_query      18.4ms  UPDATE orders SET status = 'processing' ...
    [FAIL] db_query       0.5ms  error=OperationalError
    [FAIL] db_query       0.5ms  error=OperationalError
    [FAIL] db_query       0.5ms  error=OperationalError
    ...
```
You see the pattern. Two swallowed errors in the 10 seconds before the crash. The pool draining under load. You know exactly where to look.

---

## Install

```bash
pip install because-py
```

With instrumentation extras:

```bash
pip install "because-py[sqlalchemy]"
pip install "because-py[sqlalchemy,requests,httpx,redis]"
pip install "because-py[grpc]"
```

With the LLM explainer (Claude or GPT-4o):

```bash
pip install "because-py[llm]"         # Anthropic
pip install "because-py[llm,openai]"  # + OpenAI
```

With OpenTelemetry export:

```bash
pip install "because-py[otel]"
```

---

## Zero-config setup

```python
import because
because.install()
```

That's it. `because` hooks `sys.excepthook` and starts recording operations in a per-thread ring buffer. Any uncaught exception automatically gets an enriched context chain appended to stderr — no changes to your existing error handling required.

---

## Instrumenting libraries

Attach instruments to your existing clients — they record timing, success/failure, and metadata on every operation:

```python
from because.instruments.sqlalchemy import instrument as instrument_sa
from because.instruments.requests import instrument as instrument_requests
from because.instruments.httpx import instrument as instrument_httpx
from because.instruments.redis import instrument as instrument_redis
from because.instruments.logging import instrument as instrument_logging
from because.instruments.socket import instrument as instrument_socket
from because.instruments.grpc import instrument as instrument_grpc

instrument_sa(engine)                   # SQLAlchemy engine
instrument_requests(requests.Session()) # requests Session
instrument_httpx(client)                # httpx Client or AsyncClient
instrument_redis(redis_client)          # redis-py (sync or async)
instrument_logging()                    # root logger, WARNING and above
instrument_socket()                     # raw TCP connect / connect_ex
instrument_grpc(channel)                # gRPC channel (sync or async)
```

All instruments write to a bounded ring buffer — **zero I/O on the hot path**.

---

## Zero-boilerplate enrichment with `@because.watch`

The easiest way to enrich exceptions — just decorate the function:

```python
@because.watch
def process_order(order_id):
    ...  # any exception that escapes gets auto-enriched

@because.watch
async def fetch_user(user_id):
    ...  # works on async functions too
```

Use `reraise=False` for background tasks where you want context captured but don't want the caller to crash:

```python
@because.watch(reraise=False)
async def background_sync():
    ...  # exception is enriched and swallowed
```

---

## Async context propagation

By default, `asyncio` tasks get isolated ring buffers — ops recorded in subtasks don't roll up to the parent. Use `because.gather()` and `because.create_task()` as drop-in replacements to merge child buffers back automatically:

```python
# Drop-in for asyncio.gather()
results = await because.gather(
    fetch_user(user_id),
    fetch_inventory(item_id),
    query_db(),
)

# Drop-in for asyncio.create_task()
task = because.create_task(fetch_user(user_id))
result = await task
```

Both merge child ops back into the parent buffer on completion, sorted by timestamp. `return_exceptions=True` and task naming are fully supported.

---

## Recording swallowed exceptions

Silently caught exceptions are often the real cause of a downstream crash. `because.catch()` makes them visible:

```python
def get_user(user_id: int):
    with because.catch(Exception):
        return db.query(User).filter_by(id=user_id).one()
    return None  # only reached when exception was swallowed
```

When a downstream `AttributeError: 'NoneType' object has no attribute 'email'` fires, `because` surfaces the swallowed DB error as the likely cause — not the symptom.

---

## Enriching caught exceptions manually

```python
try:
    process_order(order_id)
except Exception as exc:
    because.enrich_with_swallowed(exc)
    logger.error("Order processing failed", extra={"because": exc.__context_chain__})
    raise
```

`__context_chain__` serializes cleanly into Sentry `extra`, Datadog span tags, or structured log fields.

Filter the output to a time window to cut noise on long-running requests:

```python
print(because.format_context_chain(exc, within_seconds=30))
```

---

## LLM-based root cause analysis

Go beyond pattern matching — get a plain-English explanation with a concrete suggested fix:

```python
import because

because.configure_llm(api_key="sk-ant-...")  # Anthropic by default

try:
    risky_operation()
except Exception as exc:
    because.enrich_with_swallowed(exc)
    explanation = await because.explain_async(exc)
    print(explanation)
```

Output:
```
Root cause (high confidence): The database lookup silently failed due to a dropped
connection (OperationalError), returning None instead of a user object. The downstream
attribute access on that None value then raised AttributeError.
Contributing factors:
  • An OperationalError was caught and swallowed without re-raising, masking the failure.
  • No None-check exists before accessing .email on the return value.
Suggested fix: Re-raise or propagate the OperationalError in get_user(), and add a
guard — if user is None: raise ValueError('User not found') — before accessing attributes.
```

Use OpenAI instead:

```python
because.configure_llm(api_key="sk-...", provider="openai", model="gpt-4o")
```

Bring your own provider by implementing the `LLMProvider` protocol:

```python
class MyProvider:
    async def complete(self, prompt: str) -> str:
        ...  # call any LLM you like
```

Sync usage (avoid in async contexts):

```python
explanation = because.explain(exc)
```

---

## CLI

Analyze any stack trace without touching your code:

```bash
# pipe from a log file
cat error.log | because explain

# pass a file directly
because explain error.log

# paste interactively (Ctrl-D to submit)
because explain

# use OpenAI
because explain --provider openai --model gpt-4o error.log

# print the most recent explanation stored by any because explain call
because last
```

`because last` works across both the CLI and in-process `explain_async()` calls — every explanation is persisted automatically so you can review it any time without re-running the LLM.

### `because dashboard`

![because dashboard](https://raw.githubusercontent.com/jacobthomasmichael/because/main/docs/dashboard_screenshot.png)

Start a local web dashboard that shows the most recent explanation and context chain, auto-refreshing every 3 seconds:

```bash
because dashboard            # opens browser at http://127.0.0.1:7331
because dashboard --port 8080
because dashboard --no-open  # start server without opening browser
```

The dashboard displays:
- **Root cause** with confidence badge
- **Contributing factors** and **suggested fix**
- **Pattern matches** with evidence
- **Swallowed exceptions** (caught-and-suppressed errors that contributed to the failure)
- **Operations timeline** — last 50 DB queries, HTTP requests, cache calls, etc. with pass/fail status and duration

The dashboard reads from the same temp files written by `explain_async()` and `because explain`, so it works whether you triggered analysis from the CLI or from inside your app. No extra dependencies — stdlib only.

Example output:

```
Root cause (high confidence): The SQLAlchemy connection pool has been exhausted —
all 15 allowed connections are in use and new requests are timing out after 30s.
Contributing factors:
  • Connections may not be released promptly due to missing session closes or
    long-running transactions.
  • A spike in concurrent checkout requests may be exceeding pool capacity.
Suggested fix: Audit the checkout path to ensure sessions are always closed via
context managers (with db.connect() as conn:) and check for uncommitted transactions
holding connections open.
```

Reads `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` from the environment, or pass `--api-key` directly.

---

## pytest plugin

Install `because-py` and your failing tests automatically get a `because` context section — no config required:

```
FAILED tests/test_checkout.py::test_checkout_under_load

─────────────────────────────── because ───────────────────────────────
[because context]
  Likely cause: Database connection pool may be exhausted
    • 8/10 recent DB queries failed (80% failure rate)
  Recent operations (10):
    [ok]   db_query   2.1ms  SELECT * FROM orders WHERE user_id = ?
    [FAIL] db_query   0.5ms  error=OperationalError
    ...
```

Disable per-test with `@pytest.mark.because_off` or globally with `--no-because`.

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

## Observability integrations

`because` attaches to your existing observability stack — it doesn't replace it:

```python
# Sentry: attach context chain to every error event
from because.integrations.sentry import before_send
sentry_sdk.init(..., before_send=before_send)

# Datadog: tag the active span with because context
from because.integrations.datadog import tag_current_span
tag_current_span(exc)

# OpenTelemetry: tag the current span and add span events per operation
from because.integrations.otel import tag_current_span
tag_current_span(exc)

# OpenTelemetry: emit each operation as a child span (detailed tracing)
from because.integrations.otel import record_spans
from opentelemetry import trace
record_spans(trace.get_tracer("because"), exc)

# Structured logging: emit because context as a JSON field
from because.integrations.logging import BecauseFormatter
handler.setFormatter(BecauseFormatter())
```

---

## Heuristic patterns

Pattern matching runs at throw time — no API key, no latency:

| Pattern | Fires when |
|---|---|
| `pool_exhaustion` | DB or HTTP connection pool error + recent failures or explicit pool message |
| `silent_failure` | A swallowed exception preceded the current error |
| `retry_storm` | Timeout + high concentration of repeated HTTP requests to the same host |

Each pattern is a small, independently testable unit. Output always uses hedged language — `because` never claims certainty.

---

## Design principles

- **Honest framing.** Output uses "likely cause" and "contributing factor." Wrong-but-confident destroys trust faster than no answer.
- **Zero-config default.** `import because; because.install()` does something useful immediately.
- **No hot-path cost.** Instrumentation writes to bounded ring buffers. Enrichment and LLM calls happen only on exception.
- **Composable.** Attaches to Sentry, Datadog, and structured logging. Doesn't replace them.
- **Library, not platform.** Pure Python, no required backend, ships as a pip package.

---

## Runnable examples

```bash
python examples/pool_exhaustion.py   # connection pool saturated under load
python examples/silent_failure.py    # swallowed DB error causes downstream crash
python examples/retry_storm.py       # naive retry loop hammers a degraded API

# LLM explainer (requires ANTHROPIC_API_KEY)
python examples/llm_explainer.py
```

---

## Roadmap

- **v1.0** — Cross-process / cross-service causal reasoning
- Node.js / TypeScript port
