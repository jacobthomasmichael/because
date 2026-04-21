"""
Pool Exhaustion Demo
====================

The scenario
------------
An e-commerce app has a /checkout endpoint. A recent deploy added a
synchronous DB call to a hot path — every request now holds a connection
open for the entire duration of the handler. Under load, the pool fills
up and new requests start getting "connection refused" errors.

Without because, the engineer sees:
    OperationalError: (sqlite3.OperationalError) unable to open database file

...and has no idea why. The pool stats and the pattern of recent failures
are invisible.

With because, the engineer sees exactly what happened in the 30 seconds
before the crash: a flood of queries holding connections, then failures
as the pool maxed out.

Run this file:
    python examples/pool_exhaustion.py
"""

import sys
import threading
import time

import because
from because.instruments.sqlalchemy import instrument

because.install()

from sqlalchemy import Column, Integer, String, create_engine, text
from sqlalchemy.orm import DeclarativeBase, Session

# Tight pool: size 2, no overflow — easy to exhaust.
# Named temp file (not :memory:) so multiple threads share the same DB.
engine = create_engine(
    "sqlite:////tmp/because_pool_demo.db",
    connect_args={"check_same_thread": False},
    pool_size=2,
    max_overflow=0,
    pool_timeout=0.5,  # fail fast so the demo doesn't hang
)
instrument(engine)


class Base(DeclarativeBase):
    pass


class Order(Base):
    __tablename__ = "orders"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer)
    total = Column(Integer)


Base.metadata.create_all(engine)

# Seed some data
with Session(engine) as session:
    session.add_all([Order(user_id=i, total=i * 10) for i in range(20)])
    session.commit()


def lookup_user_orders(session: Session, user_id: int) -> list:
    """Quick lookup — releases connection immediately."""
    return session.execute(
        text("SELECT * FROM orders WHERE user_id = :uid"), {"uid": user_id}
    ).fetchall()


def checkout_handler(user_id: int) -> dict:
    """
    Simulates the buggy handler: does a lookup, then holds a connection open
    during slow processing (e.g. a payment API call), then tries a second query.
    Under concurrent load the second query can't get a connection.
    """
    # Phase 1: quick read — succeeds
    with Session(engine) as session:
        orders = lookup_user_orders(session, user_id)

    total = sum(row.total for row in orders)

    # Simulate slow external call (payment gateway, fraud check, etc.)
    time.sleep(0.2)

    # Phase 2: write the completed order — this is where we fail under load
    with Session(engine) as session:
        session.execute(
            text("UPDATE orders SET total = :t WHERE user_id = :uid"),
            {"t": total, "uid": user_id},
        )
        session.commit()

    return {"user_id": user_id, "total": total}


# Pool saturation helpers — hold connections open to simulate concurrent load
_held_connections: list = []
_held_lock = threading.Lock()


def _hold_connection():
    """Grab a raw connection and keep it open until release() is called."""
    conn = engine.connect()
    with _held_lock:
        _held_connections.append(conn)


def _release_held():
    with _held_lock:
        for conn in _held_connections:
            conn.close()
        _held_connections.clear()


def simulate_load():
    """
    Saturate the pool (size=2) by holding both connections open, then try
    to run a checkout — that thread has already done some successful queries
    so its context window is non-empty.
    """
    errors = []

    # Do some warm-up queries in the target thread first
    def worker():
        try:
            # Warm-up: these succeed and land in this thread's ring buffer
            for uid in range(3):
                with Session(engine) as s:
                    lookup_user_orders(s, uid)

            # Now saturate the pool from the background before our write phase
            t1 = threading.Thread(target=_hold_connection)
            t2 = threading.Thread(target=_hold_connection)
            t1.start(); t2.start()
            t1.join(); t2.join()
            time.sleep(0.05)  # let both connections settle

            # This will fail — pool is full
            checkout_handler(99)
        except Exception as exc:
            from because.enrichment import enrich_with_swallowed
            enrich_with_swallowed(exc)
            errors.append(exc)
        finally:
            _release_held()

    t = threading.Thread(target=worker)
    t.start()
    t.join()
    return errors


# ── separator ────────────────────────────────────────────────────────────────

print("=" * 70, file=sys.stderr)
print("POOL EXHAUSTION DEMO", file=sys.stderr)
print("=" * 70, file=sys.stderr)
print(file=sys.stderr)
print("Simulating 6 concurrent checkout requests against a pool of size 2...",
      file=sys.stderr)
print(file=sys.stderr)

errors = simulate_load()

if not errors:
    print("No errors captured — pool may not have been exhausted.", file=sys.stderr)
    sys.exit(0)

exc = errors[0]
print(f"Caught: {type(exc).__name__}: {exc}", file=sys.stderr)
print(file=sys.stderr)

from because.enrichment import format_context_chain

# Already enriched in-thread; just format and print
print(format_context_chain(exc), file=sys.stderr)
