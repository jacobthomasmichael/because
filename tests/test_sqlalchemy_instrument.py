import pytest
from sqlalchemy import create_engine, text

from because.buffer import OpType, get_context
from because.instruments.sqlalchemy import instrument


@pytest.fixture
def engine():
    e = create_engine("sqlite:///:memory:")
    instrument(e)
    return e


def _query_ops():
    return [op for op in get_context().snapshot() if op.op_type == OpType.DB_QUERY]


def test_successful_query_recorded(engine):
    before = len(_query_ops())
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    ops = _query_ops()
    assert len(ops) == before + 1
    op = ops[-1]
    assert op.success is True
    assert op.duration_ms is not None and op.duration_ms >= 0
    assert "SELECT 1" in op.metadata["statement"]


def test_failed_query_recorded(engine):
    before = len(_query_ops())
    with pytest.raises(Exception):
        with engine.connect() as conn:
            conn.execute(text("SELECT * FROM nonexistent_table"))
    ops = _query_ops()
    assert len(ops) == before + 1
    op = ops[-1]
    assert op.success is False
    assert "error" in op.metadata


def test_multiple_queries_all_recorded(engine):
    before = len(_query_ops())
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
        conn.execute(text("SELECT 2"))
        conn.execute(text("SELECT 3"))
    assert len(_query_ops()) == before + 3


def test_instrument_idempotent(engine):
    instrument(engine)  # second call should be a no-op
    before = len(_query_ops())
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    assert len(_query_ops()) == before + 1


def test_long_statement_truncated(engine):
    long_stmt = "SELECT " + "1, " * 300 + "1"
    before = len(_query_ops())
    with engine.connect() as conn:
        conn.execute(text(long_stmt))
    op = _query_ops()[before]
    assert len(op.metadata["statement"]) <= 201  # 200 chars + ellipsis
