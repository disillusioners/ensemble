# coder→developer Rename — Testing Findings

## Date: 2026-06-25
## Branch: feature/rename-coder-to-developer @ 12122f93

## Summary
Full test suite validated across 6 opencode sessions. **0 rename-caused failures** out of 7764 tests run.

## Key Findings

### 1. Registry Alias Pattern Works Correctly
- `AGENT_ID_ALIASES` dict in `daemon/registry.py:29` maps `"coder" → "developer"`
- Three functions use the alias: `resolve_pure_id()`, `resolve_path_to_id()`, `exists()`
- Instance creation normalizes "coder" → "developer" via `resolve_pure_id()`
- This pattern is the backward-compatibility mechanism — remaining "coder" references in daemon/ are INTENTIONAL

### 2. Dual-Engine DB Migration Validated
- **PostgreSQL**: Migration in `daemon/manager.py:1831-1845` using `_ensure_postgres_columns()` pattern
  - 5 plain UPDATE statements for live tables
  - Legacy `jobqueue` wrapped in `DO $$ EXCEPTION WHEN undefined_table` block
  - **Idempotent**: UPDATE 0 on re-run
- **SQLite**: Migration in `daemon/repositories/factory.py:316-332` via `run_migrations()`
- Both tested with manual E2E: insert "coder" row → migrate → verify "developer"
- All 6 tables covered: instances, instance_mappings, job_queue_items, dead_letter_items, projects.creator_agent_id, jobqueue (legacy)

### 3. Frontend Backward-Compat Color Maps
- 3 intentional `coder` references remain in frontend `agentColorMap` entries
- These handle cached responses with old `agent_id="coder"` values
- Pattern mirrors the Python backend alias approach

### 4. Pre-existing Failures (13 total, all unrelated to rename)
- **Group A (4)**: Env var leak — `MCP_DISABLE_BUILT_IN_WEBFETCH=true` and `POSTGRES_*` leak into tests
- **Group B (6)**: SQLAlchemy `TypeError: fromisoformat` on Python 3.14 — datetime column reading issue
- **Group C (2)**: Fernet `InvalidToken` in `test_sources_persistence.py` — credential encryption key handling
- **Group D (1)**: Threading flake in `test_worker_notification.py` — passes in isolation
- All confirmed by running against `latest`/`master` base branch

### 5. Testing Approach
- Used 5 opencode sessions across 2 execution groups
- Group 1 (parallel): critical tests, unit tests, frontend tests
- Group 2 (sequential): PostgreSQL migration, job queue + root tests, pre-existing verification
- PostgreSQL connection `ensemble_dev` available on localhost:5432 for migration testing
