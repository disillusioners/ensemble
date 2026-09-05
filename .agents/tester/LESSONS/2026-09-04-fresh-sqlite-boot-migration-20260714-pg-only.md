# Fresh-SQLite boot is broken at migration 20260714_000001 (PG-only SQL)

Date: 2026-09-04
Found during: live smoke for `fix/hide-polling-access-logs` (worktree `agents-ensemble-wt-hide-logs` @ `673270ec`).
Pre-existing: yes — not caused by the branch under test. NOT fixed (read-only mandate).

## Defect
- `daemon/migrations/20260714_000001*` executes `ALTER TABLE job_queues DROP CONSTRAINT IF EXISTS …`.
- PostgreSQL-only syntax. SQLite has no `DROP CONSTRAINT` (3.35 added only `DROP COLUMN`) → `sqlite3.OperationalError: near "CONSTRAINT"`.
- The migration's own header **wrongly claims SQLite 3.35+ support** — doc-truth rot inside the migration file.
- Impact: any fresh-SQLite boot (empty data dir) fails at this migration. Existing SQLite DBs that already passed it are unaffected.

## Boot traps hit while routing around it (generalizable)
1. **`dev.sh` hardcodes `export PORT=8079`** — a passed `PORT=…` env is overridden. For an alternate-port smoke, bypass dev.sh and invoke uvicorn directly (`uvicorn daemon.api:app --host 127.0.0.1 --port <N>`).
2. **Inherited `POSTGRES_*` env auto-detection** — a shell carrying prod `POSTGRES_*` vars silently points a "SQLite" smoke boot at prod PG. Neutralize by placing `{"database":"postgres"}`… actually: point an explicit data dir + config so detection is pinned; worker used `/tmp/smoke-data/ensemble.json` to block env-driven detection. Zero shared state with prod.
3. **macOS `$!` is the wrapper PID, not the listener** — collect the real listener via `lsof -i :<PORT>` (command line + cwd check) before killing; verify port release after.

## Safe workaround used
Disposable homebrew `postgresql@14` cluster in `/tmp` (port 55432, trust auth, db `ensemble_smoke`), uvicorn direct boot on 127.0.0.1:8090, exact-PID kill + port-release verification. Full recipe captured in skill `62958be5` ("Isolated Live-Smoke Boot for agents-ensemble", created by worker f65a6aed).

## Follow-up suggestion (for developer/tidier, not tester-owned)
Fix the migration for SQLite (guard the PG-only statement or provide a SQLite branch) and correct its header claim; alternatively mark the migration PG-only and fail with a clear message on SQLite. Also consider making `dev.sh` respect a pre-set `PORT`.
