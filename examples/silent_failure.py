"""
Silent Failure Demo
===================

The scenario
------------
A user profile service fetches a user record from the database. The DB
connection is flaky and occasionally raises an OperationalError. The
original developer added a bare except to "handle" the error gracefully —
but instead of logging or re-raising, they just returned None.

Downstream code then tries to access user.email to send a welcome email.
It crashes with:

    AttributeError: 'NoneType' object has no attribute 'email'

Without because, the engineer sees only the AttributeError and spends time
hunting through the user lookup code, the email template, the serializer —
everywhere except the real culprit: the silently-swallowed DB error.

With because, the swallowed OperationalError is surfaced immediately as
the likely cause.

Run this file:
    python examples/silent_failure.py
"""

import sys

import because
from because.instruments.sqlalchemy import instrument

because.install()

from sqlalchemy import Column, Integer, String, create_engine, text
from sqlalchemy.orm import DeclarativeBase, Session

engine = create_engine("sqlite:///:memory:")
instrument(engine)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    name = Column(String)
    email = Column(String)


Base.metadata.create_all(engine)

with Session(engine) as s:
    s.add(User(id=1, name="Alice", email="alice@example.com"))
    s.commit()


class FlakyDB:
    """Simulates a DB connection that raises on the second call."""

    def __init__(self):
        self._call_count = 0

    def get_user(self, user_id: int):
        self._call_count += 1
        if self._call_count >= 2:
            raise Exception("OperationalError: server closed the connection unexpectedly")
        with Session(engine) as s:
            row = s.execute(
                text("SELECT id, name, email FROM users WHERE id = :uid"),
                {"uid": user_id},
            ).fetchone()
            return row


db = FlakyDB()


def get_user_profile(user_id: int):
    """
    The buggy lookup. The developer meant well — they didn't want a DB hiccup
    to crash the whole request. But swallowing the error creates a worse crash
    downstream.

    because.catch() records the swallowed exception into the ring buffer
    so it's visible when the downstream AttributeError fires.
    """
    with because.catch(Exception):
        return db.get_user(user_id)
    return None  # only reached when the exception was swallowed


def send_welcome_email(user_id: int) -> None:
    """Uses the profile to send a welcome email."""
    user = get_user_profile(user_id)

    # This crashes with AttributeError when user is None —
    # but the real cause is the swallowed DB error above.
    recipient = user.email  # type: ignore[union-attr]
    print(f"Sending welcome email to {recipient}")


# ── separator ────────────────────────────────────────────────────────────────

print("=" * 70, file=sys.stderr)
print("SILENT FAILURE DEMO", file=sys.stderr)
print("=" * 70, file=sys.stderr)
print(file=sys.stderr)
print("First call (succeeds): fetching user profile...", file=sys.stderr)

send_welcome_email(1)  # first call succeeds

print(file=sys.stderr)
print("Second call (DB flakes, error is swallowed): fetching user profile...",
      file=sys.stderr)
print(file=sys.stderr)

try:
    send_welcome_email(1)
except AttributeError as exc:
    from because.enrichment import enrich_with_swallowed, format_context_chain

    enrich_with_swallowed(exc)

    print(f"Caught: {type(exc).__name__}: {exc}", file=sys.stderr)
    print(format_context_chain(exc), file=sys.stderr)
