# 04 — Known Traps

last-verified-against: v0.12.4

Each trap: symptom → recipe → file:line anchor. Re-encountering any of
these classes → check this list first.

## (i) Stale SQLite relic at `data/instances.db`

**Symptom:** `data/instances.db` looks like prod state but is dead
since 2026-06-04. Investigating against the file yields wrong answers.

**Fix:** prod truth is the PostgreSQL `ensemble_prod` database ONLY
(boot marker `Creating PostgreSQL engine ensemble_prod` in
`data/logs/ensemble.log`). Always query `ensemble_prod`, never the
SQLite relic. Consider deleting the relic.

## (ii) Migration runner is SQLite-only by design

**Symptom:** migrations against PG no-op; fresh-SQLite boot fails with
`OperationalError` (DROP CONSTRAINT IF EXISTS).

**Fix:** PG schema evolves via `SQLModel.metadata.create_all` +
`EnsembleManager._ensure_postgres_columns` — NEVER the `.sql` files in
`daemon/migrations/versions/`. The runner's naive `split(";")` parser
does not respect SQL comments, and the `.sql` files use
Postgres-incompatible constructs (rowid dedup, `ON CONFLICT DO
NOTHING`).

**Anchor:** `runner.py:486-491` — `if "sqlite" not in str(self.engine.url): return []` early-exit with the rationale comment immediately above.

## (iii) Idempotent `DROP NOT NULL` recipe

**Symptom:** column needs NOT NULL → NULL but the change must be safe
across DBs that already migrated.

**Fix:** wrap the ALTER in `DO $$ BEGIN IF EXISTS (SELECT 1 FROM
information_schema.columns WHERE ... AND is_nullable = 'NO') THEN
ALTER TABLE ... ALTER COLUMN ... DROP NOT NULL; END IF; END $$`.

**Anchor:** `daemon/manager.py:4996-5012` — the `report_injections`
migration block with the `DO $$` guard.

## (iv) pause-first then quiesce

**Symptom:** feature needs a quiescent instance (config flip,
activation toggle, in-place migration); naïve approaches crash
mid-flight.

**Fix:** `pause_instance_cascade` FIRST → bounded quiescence
confirmation → state mutation → resume. Pause cancels the in-flight
task via `graph_task.cancel()`; LangGraph checkpoints at node
boundaries. Resume is DB-only (`PAUSED → RUNNING`); `is_retry=True`
resumes from checkpoint. Pause-cancelled tasks stay in `PROCESSING`
(not `FAILED`); `CancellationReason` discriminates pause from
shutdown.

## (v) Auto-promote kill-switch default ON (merge `feb5e915`)

**Symptom:** auto-promote behavior fires unexpectedly after deploy.

**Fix:** `feb5e915` turned auto-promote ON by default. Disable via
`=0` / `false` / `no` / `off` (case-insensitive). Restart required to
activate. Open follow-ups: W2 arm-3 pin; PG int-bind fixture;
`_defer_block_resolver` removal; logger `:2975`; repo splits.

**Anchor:** commit `feb5e915` + docs/job-task-system.md §8.5.

## (vi) `report_injections.content` NOT-NULL drift sentinel bridge

**Symptom:** models layer says `content` is nullable but the prod NOT
NULL constraint rejects sentinel writes; the table has had four
defects on its DDL.

**Fix:** the sentinel `''` value bridges the gap (factory at
`daemon/repositories/report_injection/repository.py:788`
`_insert_deferred_marker`). The durable fix is the 4th defect
elimination: DROP NOT NULL on `content` (mirror the `DROP NOT NULL`
recipe above). The migration runner NO-OPs on PG (`runner.py:486-491`).

## (vii) TOCTOU terminal↔INSERT re-spawn

**Symptom:** a job whose status went terminal gets a fresh INSERT that
re-spawns work already finished.

**Fix:** re-check `is_terminal` at CAS time; `child_reports.py:2696-2706`
`idempotency_skip` absorbs the racy re-INSERT. Sibling gap unfixed:
`error_reporting.py:594` (no-watcher class).

## (viii) `time-bracket` log line-number trap

**Symptom:** line-range log forensics returns unrelated timestamps;
investigators conclude the wrong cause.

**Fix:** always time-bracket (start/end timestamps), never line ranges.
The log has interleaved append regions — line numbers are NOT
globally chronological.

## (ix) 3-factor gate nonce relay

**Symptom:** agent-side nonce fabrication / echo turns the 3-factor
gate into a 2-factor-and-a-trust-me.

**Fix:** relay the user nonce verbatim. The agent MUST NOT manufacture
or echo the nonce — the user provides it; the agent verifies F1
(`user_confirmed`) + F2 (per-instance user-origin window from message
source) + F3 (nonce match) from the real user-origin message.

**Anchor:** `daemon/tools/upgrade_tools.py:1877-2036` (gate body);
USER_ORIGIN_SOURCES whitelist at `upgrade_journal.py:1076-1084`.

## (x) `adopt_stale_txn` busy refusal

**Symptom:** `promote.sh` refuses with `txn-busy` when an in-flight
txn is unresolved.

**Fix:** run `adopt_stale_txn` BEFORE promote if `promote.sh` rejects
on txn-busy. The adopt diagnostics output gives the unblocking recipe.

**Anchor:** `scripts/upgrade/lib.sh:1352` (`adopt_stale_txn`); caller
at `scripts/upgrade/promote.sh:151`.

## Cross-refs

- §01 architecture (migration runner scope)
- §03 log forensics (time-bracket rule in full)
- §05 repair runbooks (runbook triggers derived from these traps)
- §06 restart/upgrade runbook (3-factor + adopt_stale_txn flow)
