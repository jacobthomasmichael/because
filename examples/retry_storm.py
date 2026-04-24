"""
Retry Storm Demo
================

The scenario
------------
A payments service calls an upstream fraud-check API. The API starts
timing out under load. The client has a retry loop — well-intentioned,
but it hammers the same endpoint repeatedly, making the upstream
degradation worse.

Without because, the engineer sees:
    TimeoutError: upstream fraud check timed out

...and has no idea whether this is a one-off blip or a retry storm
that's actively making things worse.

With because, the pattern is immediately visible: 8 HTTP requests to
the same host in the prior few seconds, 7 of them failing with timeouts.
The retry storm pattern fires and surfaces it as the likely cause.

Run this file:
    python examples/retry_storm.py
"""

import sys
import time

import because
from because.instruments.httpx import instrument

because.install()

import httpx

# ── fake transport — simulates a degraded upstream ────────────────────────────

class DegradedUpstream(httpx.BaseTransport):
    """Returns one success then times out on every subsequent request."""

    def __init__(self):
        self._call_count = 0

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self._call_count += 1
        time.sleep(0.05)  # small delay so timestamps are meaningful
        if self._call_count == 1:
            return httpx.Response(200, json={"approved": True})
        raise httpx.ReadTimeout("upstream fraud check timed out", request=request)


transport = DegradedUpstream()
client = httpx.Client(transport=transport, base_url="https://fraud.internal")
instrument(client)


# ── payment handler with naive retry loop ─────────────────────────────────────

MAX_RETRIES = 8

def check_fraud(order_id: str) -> bool:
    """Calls the fraud API with a naive retry loop — no backoff, same endpoint."""
    last_exc = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = client.get("/check", params={"order_id": order_id})
            resp.raise_for_status()
            return resp.json()["approved"]
        except (httpx.TimeoutException, httpx.HTTPStatusError) as exc:
            last_exc = exc
            # no backoff — hammers the same host immediately
            continue
    raise TimeoutError(f"Fraud check failed after {MAX_RETRIES} attempts") from last_exc


def process_payment(order_id: str) -> dict:
    approved = check_fraud(order_id)
    return {"order_id": order_id, "approved": approved}


# ── run the demo ──────────────────────────────────────────────────────────────

print("=" * 70, file=sys.stderr)
print("RETRY STORM DEMO", file=sys.stderr)
print("=" * 70, file=sys.stderr)
print(file=sys.stderr)
print("Processing order ORD-001 (first call — fraud API healthy)...", file=sys.stderr)

result = process_payment("ORD-001")
print(f"  Result: {result}", file=sys.stderr)

print(file=sys.stderr)
print("Processing order ORD-002 (fraud API degraded, retry storm begins)...",
      file=sys.stderr)
print(file=sys.stderr)

try:
    process_payment("ORD-002")
except TimeoutError as exc:
    from because.enrichment import enrich_with_swallowed, format_context_chain

    enrich_with_swallowed(exc)

    print(f"Caught: {type(exc).__name__}: {exc}", file=sys.stderr)
    print(file=sys.stderr)
    print(format_context_chain(exc), file=sys.stderr)
