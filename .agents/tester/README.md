# Testing — agents-ensemble

## Project
- **Type**: Python (async daemon, LangGraph + FastAPI + SQLite/PostgreSQL)
- **Python**: >=3.13
- **Test Framework**: pytest + pytest-asyncio (asyncio_mode=auto)
- **Integration tests**: Excluded by default (`-m 'not integration'`), run with `-m integration`

## Test Structure
- `tests/` — root-level test files (~80+ files)
- `tests/unit/` — unit tests
- `tests/unit/services/` — service-layer unit tests
- `tests/unit/tools/` — tool unit tests
- `tests/integration/` — integration tests (need live server, marked)
- `tests/e2e/` — end-to-end tests
- `tests/job_queue/` — job queue specific tests
- `tests/services/` — service tests
- `tests/tools/` — tool tests
- `tests/repositories/` — repository tests
- `tests/migration/` — migration tests
- `tests/opencode/` — opencode skill tests
- `tests/message_queue_redesign/` — message queue redesign tests

## Key Test Files
- `tests/test_deadlock_fix.py` — Deadlock fix verification (asyncio.to_thread wrapping)
- `tests/conftest.py` — Root test fixtures

## Test Command
```bash
cd /Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble
python -m pytest tests/ -x --tb=short -q  # non-integration only
```

## ensure.md
See `.agents/tester/rules/ensure.md` for project-specific quality requirements (user-defined).
