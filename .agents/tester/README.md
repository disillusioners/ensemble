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
- E2E Release Gate (4 tests): last run **2026-08-02 on `latest` @ `70a22d62`** → ✅ PASS (4/4). happy_path 51s, pause+resume 42s, terminate+revive 45s, 3-level cascade 111s. Validates premature root COMPLETED fix — no premature completion at any level in the 3-level cascade. See `RESULTS/2026-08-02-premature-completion-fix-e2e.md`.
- Previous run: 2026-07-28 on `feature/context-injection-restructure` → ✅ PASS (4/4). happy_path 68s, pause+resume 44s, terminate+revive 50s, 3-level cascade 115s. See `RESULTS/2026-07-28-e2e-workflows-release-gate.md`.
- Pack: `test/packs/e2e_workflows_ensure_test.sh` (run individually per ensure.md "one by one" rule)
- **Note:** dev mode (`./dev.sh`) logs to stdout (no `.log` file) — use the process buffer for log grep, not `tail -f daemon.log`.
- **Context Injection Gate Fix E2E:** 2026-07-28 on `latest` @ `78338bb4` → ✅ PASS. Verified fix `df0a603c` (gate on injection mode, not legacy boolean). 4/4 instances that started graph execution (developer×3, reviewer×1) received `[SYSTEM CONTEXT: Related Project]` with `context_kind=project, is_synthetic=True`. 5/9 instances stalled in job queue (concurrency bottleneck, not context issue). See `RESULTS/2026-07-28-context-injection-fix-e2e-verify.md`.
