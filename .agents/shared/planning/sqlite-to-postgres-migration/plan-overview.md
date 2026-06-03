# Plan Overview: SQLite → PostgreSQL Migration

## Objective

Add PostgreSQL support to the ensemble daemon with a one-click migration flow that transfers both databases (instances.db + checkpoints.db) from SQLite to PostgreSQL, with streaming progress, rollback support, and conditional frontend UI.

## Scope Assessment

**LARGE** — Spans backend config system, database abstraction layer, migration worker with SSE, API router, Angular frontend component, and integration testing. Estimated 30-40 hours total.

## Context

- **Project**: agents-ensemble
- **Working Directory**: `/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble`
- **Current DB**: Two SQLite databases (instances.db with 22 tables via SQLModel, checkpoints.db via LangGraph AsyncSqliteSaver)
- **Key enabler**: Repository layer is already DB-agnostic (takes `Engine` parameter). `DatabaseConfig` in `factory.py` already has a PostgreSQL branch.

## Phase Index

| Phase | Name | Objective | Dependencies | Coupling | Est. Time |
|-------|------|-----------|-------------|----------|-----------|
| 1 | Engine Abstraction, SQLite Coupling Cleanup & Config | Expose public engine property (13 accesses across 7 files); fix all `sqlite_master`/`PRAGMA`/`sqlite_insert` usage; create `ensemble.json` config system with Postgres ENV auto-detection | None | — | 6-8h |
| 2 | PostgreSQL Drivers, Checkpointer Adapter & Compatibility | Add psycopg/asyncpg deps; implement PostgreSQL engine creation; create `CheckpointerAdapter` to decouple from AsyncSqliteSaver internals; verify checkpoint serialization compatibility | Phase 1 | tight | 6-8h |
| 3 | Migration Worker, API & Write-Pausing | Implement FK-aware table migration with idempotent retries; checkpoint export/import; write-pause mechanism; SSE progress; migration API router with cancel support; `ensemble.json` update on success | Phase 2 | tight | 10-14h |
| 4 | Frontend Migration UI | Angular migration component with SSE streaming, progress bar, conditional visibility, settings menu integration; consumes API contract from Phase 3 | Phase 3 | loose | 4-6h |
| 5 | Integration Testing & Hardening | End-to-end migration testing with Docker PostgreSQL, rollback verification, error recovery, edge cases, large data | Phase 3, 4 | loose | 4-6h |

### Coupling Assessment

| From → To | Coupling | Reason |
|-----------|----------|--------|
| Phase 1 → Phase 2 | **tight** | Phase 2 imports `manager.engine` property and reads `ensemble.json` config created in Phase 1 |
| Phase 2 → Phase 3 | **tight** | Phase 3 uses PostgreSQL engine + checkpointer adapter abstractions from Phase 2 |
| Phase 3 → Phase 4 | **loose** | Phase 4 only needs the API contract (endpoints + SSE event schema), not implementation |
| Phase 3, 4 → Phase 5 | **loose** | Phase 5 tests the completed system end-to-end |

### Parallelism Opportunity

Phases 4 (Frontend) and 5 (Testing) can partially overlap — frontend can be developed against the API contract while backend testing proceeds independently.

## Key Technical Decisions

| # | Decision | Choice | Rationale |
|---|----------|--------|-----------|
| 1 | Config file | Auto-create `ensemble.json` on first start if Postgres ENV detected | Backward compatible, no breaking change |
| 2 | Migration trigger | Manual — user clicks "Start Migration" in UI | User controls timing, no hidden state changes |
| 3 | Checkpoint migration | Migrate all checkpoints (preserve full history) | No data loss, user trust |
| 4 | PostgreSQL driver | `psycopg[binary]` for SQLAlchemy sync, `asyncpg` for LangGraph | `langgraph-checkpoint-postgres` requires asyncpg |
| 5 | Checkpoint access | `CheckpointerAdapter` abstraction (6 methods) over raw `.conn`/`.lock` access | AsyncSqliteSaver and AsyncPostgresSaver have incompatible internals |
| 6 | Error recovery | `INSERT ... ON CONFLICT DO NOTHING` per table (idempotent) | No persistent state needed, safe to retry |
| 7 | Rollback | Config flip (`"database": "sqlite"`) + restart | Additive migration — source SQLite is never modified |
| 8 | Write pausing | `threading.Event` + atomic counter (`WritePauseGuard`); async gate above `to_thread()` + sync guard via `WriteGuardSession` | Works across async/sync boundary (`asyncio` primitives crash from worker threads) |
| 9 | Post-migration switch | Restart-required (update `ensemble.json`, user restarts daemon) | Safest approach, no hot-swap complexity |
| 10 | Migration state | In-memory only with `asyncio.Lock` for concurrency; add `CANCELLED` state + cancel endpoint | One-shot operation, no persistent tracking needed |

## Risk Register

| Priority | Risk | Impact | Mitigation |
|----------|------|--------|------------|
| P0 | Direct `manager._engine` in 7 files / 13 accesses (including `api.py` 7x) | Migration breaks if not abstracted | Phase 1: public `engine` property |
| P1 | `maintenance.py` uses `checkpointer.conn`/`.lock` 10+ times — crashes on PostgreSQL | Daemon crashes on first PG startup | Phase 2: `CheckpointerAdapter` abstraction |
| P2 | `sqlite_insert` dialect in `project/repository.py` — upsert fails on PostgreSQL | Runtime errors on metadata operations | Phase 1: dialect-aware upsert helper |
| P3 | `factory.py` uses `sqlite_master` 3x in `run_migrations()`/`_add_agent_id_column()` (4 PRAGMAs already guarded) | Crashes when engine is PostgreSQL | Phase 1: guard those 3 sites with `is_sqlite` check |
| P4 | `runner.py` uses `sqlite_master` + `PRAGMA table_info()` | Migration runner crashes on PostgreSQL | Phase 1: skip runner when engine is PostgreSQL |
| P5 | 31 SQL migration files contain SQLite-specific syntax (5 files confirmed) | Migration fails on PostgreSQL | Phase 1: skip SQL migrations for PG, use `create_all()` |
| P6 | Checkpoint binary data serialization differs between SQLite and PostgreSQL | Corrupted conversation history | Phase 2: investigation + round-trip validation |
| P7 | SQLAlchemy type mismatches (JSON→JSONB, Boolean 0/1 vs true/false) | Schema creation or data loss | Phase 2: type mapping audit |
| P8 | Connection pool exhaustion under migration load | Daemon hangs | Phase 2: pool sizing + limits |
| P9 | Large data sets OOM during batch read | Migration crashes | Phase 3: streaming cursor reads |

## Success Criteria

- [ ] Daemon starts with PostgreSQL when `ensemble.json` has `"database": "postgres"` (after restart)
- [ ] Daemon falls back to SQLite when no `ensemble.json` exists (backward compatible)
- [ ] First start with Postgres ENV vars auto-creates `ensemble.json` with `"database": "postgres"`
- [ ] Migration API transfers all 22 instances.db tables with correct row counts
- [ ] Migration API transfers all checkpoint data preserving conversation history
- [ ] SSE stream provides real-time progress updates during migration
- [ ] Migration can be cancelled via API endpoint
- [ ] Frontend shows migration UI only when Postgres ENV is set and current DB is SQLite
- [ ] After migration completes and daemon restarts, daemon uses PostgreSQL
- [ ] Rollback works by editing `ensemble.json` to `"database": "sqlite"` and restarting
- [ ] Zero data loss — SQLite files are never modified during migration
- [ ] Existing SQLite-only functionality remains unchanged
- [ ] `maintenance.py` checkpoint cleanup works on both SQLite and PostgreSQL

## Tracking

- Created: 2026-06-02
- Last Updated: 2026-06-02
- Status: draft
