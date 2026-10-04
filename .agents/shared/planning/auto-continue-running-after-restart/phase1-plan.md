# Phase 1: Schema + CAS column & repository changes

## Objective

`task.auto_continued_at` (nullable timestamp) exists on both SQLite and PostgreSQL; the repository exposes exactly two new methods — candidate selection (`find_auto_continue_candidates`) and the post-schedule CAS stamp (`mark_task_auto_continued`) — with the selection predicate mirroring the claim-guard's folded single-statement convention. No behavior change yet: nothing calls the new methods until Phase 2.

## Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 1.1 | Add `auto_continued_at: datetime \| None = Field(default=None)` to the `Task` SQLModel (`daemon/repositories/task/models.py:169` class; place adjacent to `last_heartbeat_at` at `:284` for reviewer locality). Docstring: "Boot auto-continue CAS marker (feature/auto-continue-running-after-restart): boot epoch of the last boot pass that successfully scheduled this task's continuation. NULL = never auto-continued. Advisory bookkeeping ONLY — never a status." | none | `models.py` imports fine; SQLModel metadata includes the column; existing tests unaffected (`pytest tests/unit/repositories -x -q` green) |
| 1.2 | New SQLite migration `daemon/migrations/versions/20261004_000001_add_task_auto_continued_at.sql` — template: `20260606_000001_add_task_last_heartbeat_at.sql` verbatim structure. UP: `ALTER TABLE task ADD COLUMN auto_continued_at TIMESTAMP;` DOWN: `ALTER TABLE task DROP COLUMN IF EXISTS auto_continued_at;`. Header comment explains: dual-driver (SQLite-only runner; PG lives in `_ensure_postgres_columns`), a′ semantics (stamped AFTER resume scheduled), advisory-only. NO new index (selection is gated on `status='running'` — served by `idx_task_status_type_created`; stamp lookups are PK-scoped) | 1.1 | Migration applies cleanly on a fresh SQLite DB and on a copy of a populated dev DB; second apply is a no-op via the runner's version ledger; DOWN removes the column |
| 1.3 | Add the PostgreSQL leg to `EnsembleManager._ensure_postgres_columns` (`daemon/manager.py:5834`): `ALTER TABLE task ADD COLUMN IF NOT EXISTS auto_continued_at TIMESTAMP` following the `last_heartbeat_at` / `is_deferred` entry pattern, with the same docstring style (why: SQLModel `create_all` doesn't add columns to existing tables; the .sql runner is a NO-OP on PG — see `manager.py:5837-5843`) | 1.1 | Boot on a PG dev DB adds the column idempotently; second boot is a no-op (`IF NOT EXISTS`); `\d task` shows the column |
| 1.4 | Add `TaskRepository.find_auto_continue_candidates(boot_epoch: datetime) -> list[Task]` (`daemon/repositories/task/repository.py`, adjacent to `find_paused_or_cancellable_turn:743`). Single folded SELECT (claim-guard convention, `repository.py:2230-2294`): `status='running'` AND `task_type IN ('process_message','process_report')` AND `(auto_continued_at IS NULL OR auto_continued_at < :boot_epoch)` AND instance NOT PAUSED/TERMINAL (subquery on instances `status IN ('paused','terminated','completed','error','failed')`) AND instance NOT WAITING_CHILDREN (subquery `status='waiting_children'`). Comment block must cite: claim-guard anti-starvation precedent (folded gates, one statement), WC exclusion rationale (bus owns, `dependency_bus.py:1499-1560`), PAUSED/TERMINATED exclusion precedent comment (`repository.py:3172-3181` "recovery must not auto-resume such tasks") | 1.1 | Unit tests (1.6) prove each predicate: matches a dormant RUNNING task; excludes auto_continued_at ≥ boot_epoch; excludes paused/terminal/WC instances; excludes task_type ∉ set; ordered deterministically (`created_at ASC` — oldest first) |
| 1.5 | Add `TaskRepository.mark_task_auto_continued(task_id: int, boot_epoch: datetime) -> bool` in the same file. Atomic single-statement CAS inside `with self.engine.begin()`: `UPDATE task SET auto_continued_at = :boot_epoch WHERE id = :task_id AND status = 'running' AND (auto_continued_at IS NULL OR auto_continued_at < :boot_epoch)` → returns `rowcount == 1`. Docstring: "Post-schedule CAS (Option a′): stamps ONLY when the resume was actually scheduled; False = already stamped this epoch or row no longer running (caller must treat as skip, never retry-stamp)" | 1.1 | Unit tests: first call True; second call same epoch False; older-epoch re-stamp after a newer epoch False; stamps nothing if row left `status='running'` (e.g. completed between schedule and stamp → False) |
| 1.6 | Write `tests/unit/repositories/test_auto_continue_candidates.py` covering 1.4/1.5 (in-memory SQLite engine fixture per repo conventions — see existing `tests/unit/repositories/` patterns). Include the PAUSED-does-not-block note: the CAS predicate excludes PAUSED instances but the test must also pin that the FEATURE never widens the claim-guard (`status='running'` only) — guard the S3 invariant indirectly by asserting `find_auto_continue_candidates` does not mutate rows | 1.4, 1.5 | `pytest tests/unit/repositories/test_auto_continue_candidates.py -q` green; fixtures clean up after themselves |

## Coupling

- **Tight with Phase 2** — P2 consumes exactly these two methods; signature changes after P2 starts are a contract break. Freeze signatures in this phase.
- **Tight with Phase 3** — the AC4 pinning test reads candidate-selection semantics (which rows qualify) but not internals.
- **Independent of** P4 (packs) and P5 (E2E) beyond consuming the finished column.
- **Never touches:** `claim_pending_task` (`:2230-2294` — READ-ONLY precedent), `find_paused_or_cancellable_turn` (consumed as-is by P2), `StaleTaskRecovery` predicates.

## Verification Steps

1. `cd /home/nea/ensemble-src && .venv/bin/pytest tests/unit/repositories/test_auto_continue_candidates.py -q` → green.
2. Full repositories unit dir: `.venv/bin/pytest tests/unit/repositories -q` → green (no regressions).
3. SQLite migration: run against a COPY of a populated dev DB (never the live one): `sqlite3 /tmp/dev-copy.db < <(sed -n '/-- UP/,/-- DOWN/p' daemon/migrations/versions/20261004_000001_add_task_auto_continued_at.sql)` → column exists; re-run → error-free no-op or already-exists handled by ledger (runner applies each version once — verify via `schema_migrations` row).
4. PG leg (if a PG dev DB is reachable): boot daemon once against dev PG; `\d task` shows `auto_continued_at | timestamp |`; boot again → no error.
5. Multi-edit read-back verification (repo trap #2): after editing `models.py` + `manager.py`, re-read both diffs (`git diff`) and confirm exactly the intended hunks landed — especially that `_ensure_postgres_columns` gained ONE entry and no existing entry was clobbered.

## AC Mapping

- **AC5** (no double-continue across restarts): the CAS column + folded selection predicate are the durable half of the double-fire defense (the claim-guard is the other half, pre-existing). Tasks 1.4/1.5/1.6.
- **AC1** support: the PAUSED/terminal/WC exclusion subqueries encode "PAUSED stays parked, terminal never touched, WC bus-owned" at the selection layer.
- **AC2** support: repository addition is data-access only — no messaging path created.

## Rollback Note

Revert commits delete the migration file, the `_ensure_postgres_columns` entry, the two repo methods, the model field, and the test file. The column is advisory-only (no reader outside the feature) so dropping it is safe at any time; no backfill exists to unwind; no `NOT NULL` constraint ever lands, so old code reading new rows sees `NULL` and ignores it.
