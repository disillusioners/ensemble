# Compaction Phase 4 Testing — Lessons Learned

## Key Findings
- **config.yaml was missing from repo** — dev.sh requires it but it was deleted. Fixed by recreating.
- **conftest.py mock interference** — The top-level conftest.py mocks langgraph modules. Integration tests need to save/restore real langgraph imports.
- **tiktoken encoding variation** — Token counts vary across platforms/versions. Use `context_window_override=1000` in tests to reliably trigger compaction thresholds.
- **AsyncSqliteSaver needs setup()** — Must call `await saver.setup()` after creating in-memory SQLite connection.
- **Patch ThinkingChatOpenAI** — `patch("daemon.graph.ThinkingChatOpenAI")` works for integration test LLM mocking.

## Test Architecture
- Unit tests: tests/unit/test_compaction.py (41 tests, no external deps)
- Integration tests: tests/integration/test_compaction_e2e.py (4 tests, real langgraph + SQLite, mocked LLM)
- Both run in <1 second combined
