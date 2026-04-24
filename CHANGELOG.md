# Changelog

All notable changes to `because-py` are documented here.

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
