# SQLite → PostgreSQL Migration Plan

> **Status**: Planning
> **Created**: 2026-06-02
> **Scope**: Add PostgreSQL support with live data migration
> **Total Effort**: ~30-40 hours across 8 phases

## Overview

This plan adds PostgreSQL support to the ensemble daemon, allowing users to migrate from the default SQLite databases to PostgreSQL with a one-click migration flow. The migration is additive—SQLite data is never modified—and supports full rollback via config change.

## Documents

| File | Phase | Effort | Status |
|------|-------|--------|--------|
| [00-architecture-overview.md](./00-architecture-overview.md) | Architecture decisions | — | Planning |
| [01-phase-0-engine-abstraction.md](./01-phase-0-engine-abstraction.md) | Phase 0: Engine Access Abstraction | 1-2h | Planning |
| [02-phase-1-config-system.md](./02-phase-1-config-system.md) | Phase 1: Config System (`ensemble.json`) | 4-6h | Planning |
| [03-phase-2-driver-checkpoint.md](./03-phase-2-driver-checkpoint.md) | Phase 2: Driver + Checkpoint Abstraction | 4-6h | Planning |
| [04-phase-3-schema-compatibility.md](./04-phase-3-schema-compatibility.md) | Phase 3: Schema Compatibility | 2-3h | Planning |
| [05-phase-4-checkpoint-strategy.md](./05-phase-4-checkpoint-strategy.md) | Phase 4: Checkpoint Migration Strategy | 2-3h | Planning |
| [06-phase-5-migration-worker.md](./06-phase-5-migration-worker.md) | Phase 5: Migration Worker + API | 8-12h | Planning |
| [07-phase-6-frontend.md](./07-phase-6-frontend.md) | Phase 6: Frontend Settings Sub-page | 4-6h | Planning |
| [08-phase-7-integration-testing.md](./08-phase-7-integration-testing.md) | Phase 7: Integration Testing | 4-6h | Planning |

## Dependency Graph

```
Phase 0: Engine Access Abstraction [BLOCKER]
    │
    ├──► Phase 1: Config System
    │       │
    │       └──► Phase 3: Schema Compatibility ──┐
    │                                           │
    └──► Phase 2: Driver + Checkpoint ──────────┤
            │                                   │
            └──► Phase 4: Checkpoint Strategy ──┤
                                                 │
                                          Phase 5: Migration Worker
                                                 │
                                          Phase 6: Frontend UI
                                                 │
                                          Phase 7: Integration Testing
```

## Key Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Config file | Auto-create `ensemble.json` on first start | Minimal disruption, backward compatible |
| Migration trigger | Manual with explicit user action | User controls timing, no hidden state changes |
| Checkpoint migration | Migrate all checkpoints | Preserve full conversation history |
| Frontend location | New settings sub-page | Clean separation, matches existing patterns |
| PostgreSQL driver | `psycopg[binary]` for SQLAlchemy, `asyncpg` for LangGraph | `langgraph-checkpoint-postgres` requires asyncpg |
| Checkpoint strategy | Export/import with downtime window | Simple, reliable, no replay complexity |
| Transaction strategy | Per-table commits, 500-row batches | Resumable, progress-reportable, OOM-safe |
| Rollback | Config flip (`"database": "sqlite"`) | Additive migration, source untouched |

## Requirements Mapping

| Requirement | Phase |
|-------------|-------|
| 1. Config file `ensemble.json` with `"database"` field | Phase 1 |
| 2. Auto-detect Postgres via ENV on first start | Phase 1 |
| 3. Migration worker API with SSE streaming | Phase 5 |
| 4. Frontend migration UI in settings menu | Phase 6 |
| 5. Fallback to SQLite via config edit | Phase 1, 3 |
| 6. Conditional feature visibility | Phase 6 |

## Risk Register

| Priority | Risk | Mitigation Phase |
|----------|------|------------------|
| P0 | Direct `manager._engine` access in 6 sites | Phase 0 |
| P1 | SQLite-specific SQL in migration runner | Phase 3 |
| P2 | Checkpoint binary data integrity | Phase 4 |
| P3 | Schema type compatibility | Phase 3 |
| P4 | Connection pool exhaustion | Phase 2 |

## Quick Start

1. Read [00-architecture-overview.md](./00-architecture-overview.md) for high-level context
2. Execute phases in order (Phase 0 → Phase 7)
3. Each phase has its own acceptance criteria
4. Integration testing (Phase 7) is mandatory before shipping
