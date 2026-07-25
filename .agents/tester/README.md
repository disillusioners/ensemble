# Tester — agents-ensemble

## Project Overview
- **Language**: Python (backend), TypeScript (Angular frontend)
- **Test framework**: pytest
- **DB**: PostgreSQL is the PRIMARY dev/test DB (also supports SQLite for unit tests)
- **Dev env**: `./dev.sh` on port 8079

## Test Structure
- `tests/unit/` — Unit tests (fast, in-memory SQLite)
- `tests/test_registry.py` — Agent registry discovery tests
- `tests/test_tool_filter.py` — Tool filter resolution tests
- `tests/test_spawn_team_members.py` — Team member spawn authorization tests

## Key Patterns
- KB_AGENT_IDS must be synced across 3 locations: backend repository.py, frontend instance.service.ts, repository docstring
- Agent tool allow-lists are enforced via `resolve_tool_filter` in `daemon/tools/instance.py`
- Agent definitions live in `agents/<agent-id>/meta.json`

## ensure.md
- Status: ✅ ACTIVE — `.agents/tester/rules/ensure.md` exists with Core + Release Gate requirements
- E2E Release Gate (4 tests): last run 2026-07-25, all PASS (happy_path 98s, pause+resume 43s, terminate+revive 93s, 3-level cascade 131s)
- Pack: `test/packs/e2e_workflows_ensure_test.sh` (run individually per ensure.md "one by one" rule)
