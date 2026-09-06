# PG Smoke Verifier Dialect Traps (raw-SQLModel verifier against local PG)

Date: 2026-09-06 | Gate: terminal-report-wake verification | Commits: 4964c105, 419283c0, b934a6a8, 2385144a, b7870b66, c5e3259c, 79f19c08, 0e456df7 (worker 31ca31cc)

A hand-rolled repository-level PG verifier (inline python heredoc + raw SQL/SQLModel calls) hit 8 traps before passing. Reuse this checklist for any new PG-dialect pack:

1. **Driver dialect**: project uses psycopg3 → URL must be `postgresql+psycopg://` (bare `postgresql://` resolves to psycopg2, not installed).
2. **Table name**: Task model `__tablename__ = "task"` — SINGULAR. Raw SQL `FROM tasks` fails.
3. **SQLModel session API**: `Session.exec()` takes `params=` keyword-only; positional params raise.
4. **Required args**: `claim_pending_task()` requires `worker_id=` (no default).
5. **Naive vs aware datetimes**: PG TIMESTAMP round-trip needs tz-aware values (`.replace(tzinfo=timezone.utc)`) or seed comparisons drift.
6. **Server timezone**: local PG runs Asia/Ho_Chi_Minh (UTC+7) → `connect_args={"options": "-c TimeZone=UTC"}` for deterministic comparisons (timestamptz-column packs absorb this implicitly; naive-TIMESTAMP packs do not).
7. **Per-instance concurrency gate**: seeding the priority task on an instance that already has a RUNNING task silently filters it out of claim candidates — use a separate instance for the report task.
8. **Trap-vs-exit ordering**: cleanup `trap` must fire AFTER the RESULT block; an `exit` inside cleanup swallows the pack's exit signal (breaks 0/1/124 contract).

Also: the 2 new integration files are hard-wired file-backed SQLite (own `engine` fixture, no `postgres` marker) — the repo's `-m postgres` mechanism (scoped to tests/postgres/ by conftest) cannot select them; PG validation of such files must go repository-level, as this pack does. Disposable-DB pattern: `ensemble_test_<name>` with DROP-IF-EXISTS→CREATE→trap-DROP (WITH FORCE fallback), HARD GUARD aborting on any `ensemble_prod` match in DB name or resolved URL — production DB never touched, verified by before/after `pg_database` snapshot.
