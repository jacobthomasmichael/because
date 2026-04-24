"""
pytest plugin for ``because``.

Auto-discovered when ``because-py`` is installed — no conftest.py needed.
On any test failure, appends the because context chain (recent operations,
swallowed exceptions, pattern matches) as a section in the pytest report.

Disable per-test with the ``because_off`` marker::

    @pytest.mark.because_off
    def test_something():
        ...

Or disable globally with ``--no-because`` on the command line.
"""
from __future__ import annotations

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--no-because",
        action="store_true",
        default=False,
        help="Disable because context enrichment in test failure output.",
    )


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "because_off: disable because context enrichment for this test.",
    )
    if not config.getoption("--no-because", default=False):
        from because.buffer import install
        install()


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo):
    outcome = yield
    report = outcome.get_result()

    if report.when != "call" or not report.failed:
        return

    if item.get_closest_marker("because_off"):
        return

    if item.config.getoption("--no-because", default=False):
        return

    excinfo = call.excinfo
    if excinfo is None:
        return

    exc = excinfo.value

    try:
        from because.enrichment import enrich_with_swallowed, format_context_chain
        enrich_with_swallowed(exc)
        context = format_context_chain(exc)
        if context.strip():
            report.sections.append(("because", context))
    except Exception:
        pass  # never let the plugin crash a test run
