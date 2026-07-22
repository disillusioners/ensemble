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
- Status: NOT YET CREATED — `.agents/tester/rules/ensure.md` does not exist yet. Ask user to create project-specific quality gates.
