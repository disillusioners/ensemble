# Phase D — Dependency Bus & Cleanup: Coder Execution Plan

## Reference
- Plan: `docs/plans/decouple-execution-plan.md` Phase D (M7+M8)
- Review: `docs/plans/decouple-review.md` §9, §2.5, §3.2, §7.2, §7.4
- Branch: `feature/decouple-phase-d` (clean, last commit 89b61db1)

## Key Constraints (from leader + critical notes)
1. PostgreSQL is PRIMARY dev/test DB — tests against PG
2. Use `_ensure_postgres_columns()` for NEW columns on EXISTING tables (NOT for new tables — new tables use `create_all()`)
3. D10 column drop is IRREVERSIBLE — gate behind `USE_DEPENDENCY_BUS=ON`, document data loss
4. Generation counter + post-commit re-arm (Phase A) must still work on bus path
5. D9 shadow-equivalence tests are the safety net
6. Bus persistence must use `WriteGuardSession` pattern for high-concurrency inserts
7. Reviewer §7.2: Make column drop TWO-step (D10a shadow, D10b actual drop). We follow this — D10 only runs when flag ON.
8. Reviewer §3.2/§7.4: In-flight migration handler — either snapshot CM `_pending` → watchers rows, OR drain in-flight jobs before flipping flag. We document drain approach.

## Important Technical Clarifications
- **D2 migration creates a NEW table** (`dependency_watchers`) → use standard `.sql` migration + ensure `create_all()` picks up the SQLModel. For PostgreSQL, `create_all()` creates new tables correctly (only COLUMN additions need `_ensure_postgres_columns()`).
- **D10 drops columns** → this is a `.sql` migration that NO-OPs on PostgreSQL per the runner. So for PG we need a parallel `_ensure_postgres_columns()`-style approach OR execute the DROP directly. This needs care.
