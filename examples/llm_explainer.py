"""
LLM Explainer Demo
==================

The scenario
------------
Same silent-failure cascade as examples/silent_failure.py: a flaky DB
connection is swallowed, downstream code crashes with an AttributeError.

This demo adds one more step: after because captures and formats the
context chain, it sends the full structured context to Claude and prints
a plain-English root cause analysis.

Without because + LLM:
    AttributeError: 'NoneType' object has no attribute 'email'
    (engineer has no idea why)

With because + LLM:
    Root cause (high confidence): The database lookup silently failed and
    returned None, causing the downstream attribute access to crash.
    Suggested fix: Re-raise or log the OperationalError in get_user() ...

Requirements:
    pip install "because-py[llm]"
    export ANTHROPIC_API_KEY=sk-ant-...

Run this file:
    python examples/llm_explainer.py
"""

import asyncio
import os
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
    def __init__(self):
        self._call_count = 0

    def get_user(self, user_id: int):
        self._call_count += 1
        if self._call_count >= 2:
            raise Exception("OperationalError: server closed the connection unexpectedly")
        with Session(engine) as s:
            return s.execute(
                text("SELECT id, name, email FROM users WHERE id = :uid"),
                {"uid": user_id},
            ).fetchone()


db = FlakyDB()


def get_user_profile(user_id: int):
    with because.catch(Exception):
        return db.get_user(user_id)
    return None


def send_welcome_email(user_id: int) -> None:
    user = get_user_profile(user_id)
    recipient = user.email  # type: ignore[union-attr]
    print(f"Sending welcome email to {recipient}")


# ── run the demo ──────────────────────────────────────────────────────────────

async def main():
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("Error: set ANTHROPIC_API_KEY before running this demo.", file=sys.stderr)
        sys.exit(1)

    because.configure_llm(api_key=api_key)

    print("=" * 70, file=sys.stderr)
    print("LLM EXPLAINER DEMO", file=sys.stderr)
    print("=" * 70, file=sys.stderr)
    print(file=sys.stderr)
    print("First call (succeeds): fetching user profile...", file=sys.stderr)

    send_welcome_email(1)

    print(file=sys.stderr)
    print("Second call (DB flakes, error is swallowed)...", file=sys.stderr)
    print(file=sys.stderr)

    try:
        send_welcome_email(1)
    except AttributeError as exc:
        from because.enrichment import enrich_with_swallowed, format_context_chain

        enrich_with_swallowed(exc)

        print(f"Caught: {type(exc).__name__}: {exc}", file=sys.stderr)
        print(file=sys.stderr)
        print("── because context ──────────────────────────────────────────────",
              file=sys.stderr)
        print(format_context_chain(exc), file=sys.stderr)
        print(file=sys.stderr)
        print("── asking Claude for root cause analysis... ─────────────────────",
              file=sys.stderr)
        print(file=sys.stderr)

        explanation = await because.explain_async(exc)

        print(str(explanation), file=sys.stderr)
        print(file=sys.stderr)


asyncio.run(main())
