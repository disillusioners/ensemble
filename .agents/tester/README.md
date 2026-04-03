# Agents Ensemble — Tester README

## Project Overview
Persistent multi-agent daemon built with LangGraph. Agents defined by markdown files with HTTP API, OpenAI-compatible LLM support, instance hierarchy for agent spawning/communication, and SQLite checkpoints for crash recovery.

## Test Framework
- **pytest** with `tests/conftest.py` that mocks langgraph modules
- Integration tests under `tests/integration/` (require OPENAI_API_KEY)
- Unit tests at `tests/test_*.py`

## Key Test Patterns
- `conftest.py` pre-populates `sys.modules` with langgraph mocks — all unit tests use these
- Tools tested by creating tool then calling `.invoke({"param": value})`
- Filesystem tests use `tmp_path` fixture
- Cache tests use `time.sleep(0.1)` between mtime changes

## Current Focus
Testing memory system improvements (inner_soul, access_memory, load_recent_memories, cache invalidation).

## Recent Test Runs
- **2026-04-02**: Title generation fire-and-forget fix — 7 new unit tests added (all 38 pass). See `RESULTS/2026-04-02-title-generation-fix.md`
