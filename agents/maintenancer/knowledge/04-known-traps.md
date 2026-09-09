# 04 — Known Traps

last-verified-against: v0.12.4

- **(i) `data/instances.db`:** looks like prod state but is dead since 2026-06-04; prod truth is PG `ensemble_prod` ONLY (boot marker in `data/logs/ensemble.log`).
- **(ii) Migration runner:** PG migrations no-op; fresh-SQLite boot fails with `OperationalError`. PG schema via `create_all + _ensure_postgres_columns` — NEVER `.sql` files in `daemon/migrations/versions/`. **A:** `runner.py:486-491`.
- **(iii) `DROP NOT NULL`:** wrap in `DO $$ BEGIN IF EXISTS (SELECT 1 FROM information_schema.columns WHERE ... AND is_nullable='NO') THEN ALTER TABLE ... ALTER COLUMN ... DROP NOT NULL; END IF; END $$`. **A:** `daemon/manager.py:4996-5015`.
- **(iv) pause-first:** feature needs quiescent instance. `pause_instance_cascade` FIRST → quiescence → mutate → resume. Resume DB-only. **A:** `daemon/services/instance_lifecycle.py`.
- **(v) Auto-promote (`feb5e915`):** auto-promote fires unexpectedly. `feb5e915` turned it ON by default; disable via `=0`/`false`/`no`/`off`. Restart required. **A:** commit `feb5e915` + docs/job-task-system.md §8.5.
- **(vi) `report_injections.content`:** models nullable but prod NOT NULL rejects sentinel writes; sentinel `''` bridges (factory at `repository.py:756-815`). Durable fix: DROP NOT NULL on `content`. Migration runner NO-OPs on PG.
- **(vii) TOCTOU re-spawn:** job terminal, fresh INSERT re-spawns finished work. Re-check `is_terminal` at CAS time; `child_reports.py:2696-2706` `idempotency_skip` absorbs. Sibling gap: `error_reporting.py:594`.
- **(viii) `time-bracket` log:** line-range forensics returns unrelated timestamps. Always time-bracket, never line ranges. Log has interleaved append regions. (See §03.)
- **(ix) 3-factor nonce:** agent-side fabrication/echo turns gate into 2-factor-and-a-trust-me. Relay nonce verbatim. Agent MUST NOT manufacture or echo — user provides; verifies F1+F2+F3. **A:** `upgrade_tools.py:1877-2036`; `USER_ORIGIN_SOURCES` at `upgrade_journal.py:1081-1083`.
- **(x) `adopt_stale_txn`:** `promote.sh` refuses with `txn-busy` on unresolved txn. Run `adopt_stale_txn` BEFORE promote if rejected. **A:** `lib.sh:1352`; caller `promote.sh:151`.
