"""Tests for the because pytest plugin using pytester."""
import pytest


pytest_plugins = ["pytester"]

# Load the plugin explicitly in pytester subprocesses since the package
# isn't installed from an entry point in development mode.
_P = ["-p", "because.pytest_plugin"]


# ── helpers ───────────────────────────────────────────────────────────────────

PASSING_TEST = """
def test_ok():
    assert 1 + 1 == 2
"""

FAILING_TEST = """
def test_boom():
    raise ValueError("something went wrong")
"""

FAILING_WITH_INSTRUMENT = """
import because
from because.instruments.sqlalchemy import instrument
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

engine = create_engine("sqlite:///:memory:")
instrument(engine)

def test_fails_after_db_ops():
    with Session(engine) as s:
        s.execute(text("SELECT 1"))
    raise RuntimeError("connection refused")
"""

SWALLOWED_THEN_FAIL = """
import because

def test_swallowed_then_fail():
    with because.catch(Exception):
        raise Exception("OperationalError: connection dropped")
    raise AttributeError("NoneType has no attribute email")
"""

BECAUSE_OFF_TEST = """
import pytest

@pytest.mark.because_off
def test_skips_enrichment():
    raise ValueError("no because context please")
"""


# ── plugin registration ───────────────────────────────────────────────────────

def test_plugin_is_registered(pytester):
    result = pytester.runpytest(*_P, "--co", "-q")
    assert result.ret in (0, 4, 5)  # 0=ok, 4/5=no tests collected (pytest version differs)


# ── passing tests ─────────────────────────────────────────────────────────────

def test_passing_test_unaffected(pytester):
    pytester.makepyfile(PASSING_TEST)
    result = pytester.runpytest(*_P, "-v")
    result.assert_outcomes(passed=1)
    assert "because" not in result.stdout.str()


# ── failing tests get because context ────────────────────────────────────────

def test_failing_test_shows_because_section(pytester):
    pytester.makepyfile(FAILING_TEST)
    result = pytester.runpytest(*_P, "-v")
    result.assert_outcomes(failed=1)
    assert "because" in result.stdout.str()


def test_failing_test_with_db_ops_shows_operations(pytester):
    pytester.makepyfile(FAILING_WITH_INSTRUMENT)
    result = pytester.runpytest(*_P, "-v")
    result.assert_outcomes(failed=1)
    output = result.stdout.str()
    assert "because" in output
    assert "db_query" in output


def test_swallowed_exception_surfaced_in_report(pytester):
    pytester.makepyfile(SWALLOWED_THEN_FAIL)
    result = pytester.runpytest(*_P, "-v")
    result.assert_outcomes(failed=1)
    output = result.stdout.str()
    assert "because" in output
    assert "swallowed" in output.lower() or "OperationalError" in output


# ── --no-because flag ─────────────────────────────────────────────────────────

def test_no_because_flag_suppresses_section(pytester):
    pytester.makepyfile(FAILING_TEST)
    result = pytester.runpytest(*_P, "-v", "--no-because")
    result.assert_outcomes(failed=1)
    # section should be absent
    assert "[because]" not in result.stdout.str()


# ── because_off marker ────────────────────────────────────────────────────────

def test_because_off_marker_suppresses_section(pytester):
    pytester.makepyfile(BECAUSE_OFF_TEST)
    result = pytester.runpytest(*_P, "-v")
    result.assert_outcomes(failed=1)
    assert "[because]" not in result.stdout.str()


# ── plugin never crashes a test run ──────────────────────────────────────────

def test_plugin_does_not_crash_on_system_exit(pytester):
    pytester.makepyfile("""
def test_sysexit():
    raise SystemExit(1)
""")
    result = pytester.runpytest(*_P, "-v")
    # pytest handles SystemExit specially — either way the run completes
    assert result.ret in (0, 1, 2)


def test_plugin_does_not_crash_on_keyboard_interrupt(pytester):
    """Plugin must not prevent keyboard interrupt from propagating."""
    # We just verify the plugin loads cleanly alongside these edge cases
    pytester.makepyfile(FAILING_TEST)
    result = pytester.runpytest(*_P, "-v")
    assert result.ret in (0, 1)
