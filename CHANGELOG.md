# Changelog

All notable changes to `because-py` are documented here.

---

## [0.2.13] — 2026-04-24

### Changed
- Added author LinkedIn URL to project metadata.

---

## [0.2.12] — 2026-04-24

### Added
- Dashboard screenshot in README/PyPI documentation.

---

## [0.2.11] — 2026-04-24

### Added
- `because dashboard` CLI subcommand: starts a local HTTP server (default port 7331) and opens a live dashboard in your browser. Shows the most recent explanation, context chain, pattern matches, swallowed exceptions, and operations timeline. Auto-refreshes every 3 seconds. Zero new dependencies (stdlib only).

---

## [0.2.10] — 2026-04-24

### Added
- Context chain persistence: `explain_async()` now saves the full context chain (operations, swallowed exceptions, pattern matches) to a temp file alongside the explanation, making it available to the dashboard.

---

## [0.2.9] — 2026-04-24

### Added
- `because.create_task()`: drop-in for `asyncio.create_task()` that merges child task ring buffers back into the parent on completion via done callback. `merge_on_done=False` opt-out for fire-and-forget tasks.

---

## [0.2.8] — 2026-04-24

### Added
- `because last` CLI subcommand: prints the most recent explanation stored by any `because explain` or `explain_async()` call. Explanations are persisted to a temp file automatically.

---

## [0.2.7] — 2026-04-24

### Added
- gRPC instrument (`because.instruments.grpc`): wraps `grpc.Channel` and `grpc.aio.Channel`, recording all four RPC types (unary/stream combos). Adds `because-py[grpc]` extra.

---

## [0.2.6] — 2026-04-24

### Added
- `because.gather()`: drop-in for `asyncio.gather()` that merges child task ring buffers back into the parent context after all tasks complete, sorted by timestamp.
- `pool_exhaustion` pattern extended to HTTP connection pools: fires on `urllib3`, `httpx`, and `Max retries exceeded` messages. Description adapts to say "HTTP connection pool" vs "database connection pool".
- `format_context_chain(exc, within_seconds=30)`: filter the operation list to a time window. Label in output shows the window used.

---

## [0.2.5] — 2026-04-24

### Added
- pytest plugin (`because.pytest_plugin`): auto-registered via `pytest11` entry point. Appends `because` context section to failing test reports. Supports `--no-because` flag and `@pytest.mark.because_off` per-test opt-out.
- `@because.watch` decorator: zero-boilerplate enrichment for sync and async functions. Supports bare `@because.watch` and parameterised `@because.watch(reraise=False)` forms.
- Hypothesis property-based tests for all three pattern matchers (183 tests at time of release).
- `CHANGELOG.md`.

---

## [0.2.4] — 2026-04-24

### Added
- OpenTelemetry integration (`because.integrations.otel`): `tag_span()`, `tag_current_span()`, `record_spans()`. Adds `because-py[otel]` extra.
- Socket instrument (`because.instruments.socket`): records raw TCP `connect` / `connect_ex` calls into the ring buffer.
- CLI (`because explain`): reads a stack trace from stdin or a file and returns a plain-English root cause analysis via Claude or GPT-4o.
- `examples/retry_storm.py`: runnable demo of the retry storm cascade pattern.
- `examples/llm_explainer.py`: runnable demo of the LLM explainer on top of the silent failure cascade.

### Changed
- README overhauled with concrete before/after output, full LLM explanation example, and CLI sample output.

---

## [0.2.3] — 2026-04-24

### Added
- CLI tests (`tests/test_cli.py`): 14 tests covering prompt building, provider selection, model override, error cases, and argparse dispatch.
- Property-based tests (`tests/test_patterns_hypothesis.py`): Hypothesis tests for all three pattern matchers verifying crash-safety, confidence validity, and threshold invariants across arbitrary inputs.

### Changed
- README: added CLI section with example output, fixed install command to `because-py`.

---

## [0.2.2] — 2026-04-24

### Added
- `because explain` CLI with `--provider`, `--model`, `--api-key` flags. Registered as a console script.

---

## [0.2.1] — 2026-04-24

### Changed
- README: updated install name to `because-py`, added LLM explainer section, all instruments and integrations, retry_storm pattern table entry, updated roadmap.

---

## [0.2.0] — 2026-04-24

### Added
- LLM-based exception explainer (`because.explainer`): `Explanation` dataclass, `LLMProvider` protocol, `AnthropicProvider` (claude-sonnet-4-6 default), `OpenAIProvider` (gpt-4o default), `configure_llm()`, `explain_async()`, `explain()`, `build_prompt()`, `_parse_response()`.
- `because-py[llm]` extra (`anthropic>=0.25`) and `because-py[openai]` extra.
- All explainer symbols exported from the top-level `because` package.
- 25 tests for the explainer module.

---

## [0.1.0] — 2026-04-24

### Added
- Ring buffer (`because.buffer`): `RingBuffer`, `Op`, `OpType`, `ContextVar` plumbing, `get_context()`, `record()`, `install()`.
- Exception enrichment (`because.enrichment`): `ContextChain`, `SwallowedExc`, `enrich()`, `enrich_with_swallowed()`, `catch()`, `format_context_chain()`, `sys.excepthook` integration.
- Instruments: SQLAlchemy, requests, httpx, redis-py, Python logging.
- Heuristic patterns: `pool_exhaustion`, `silent_failure`, `retry_storm`.
- Framework integrations: Flask, FastAPI, Django.
- Observability integrations: Sentry, Datadog, structured logging formatter.
- Runnable examples: `pool_exhaustion.py`, `silent_failure.py`.
- Full test suite (130 tests at time of first publish).
