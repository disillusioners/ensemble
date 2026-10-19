# Lessons — resource-telemetry PostgreSQL verification (2026-10-19)

Commission: verify the `estimated` boolean fix (A–H) on worktree `ensemble-src-wt-resource-telemetry-estimated-bool` @ `69cea24ea` (+ `7b0920021` test pin). Full report: `RESULTS/2026-10-19-resource-telemetry-fix-verification.md`.

## 1. PG test packs: `PG_TEST_*`, not `POSTGRES_*`
`tests/postgres/conftest.py` builds its URL from **`PG_TEST_HOST/PORT/DB/USER/PASSWORD`** (default DB `ensemble_test`). Ambient `POSTGRES_*` is ignored — good (prod poisoning can't leak), but it means a scratch DB must be created **before** the run and passed via the `PG_TEST_*` names.
The session `pg_engine` fixture does `drop_all` → `create_all` → `drop_all` on that DB. **Never point it at a shared/live DB**; always a dedicated scratch DB, dropped after.
The new pack's own tripwire rejects `PG_TEST_DB ∈ {ensemble_prod, ensemble_dev, ensemble_sandbox, ensemble}` and any name without `test`/`scratch`.

## 2. Unit packs self-fence on `POSTGRES_DB`
The six resource unit suites (storage, control_seam, sampler, api, self_cost, pid_registry) have a session autouse fence whose allow-list is `{ensemble_dev_test_only, ensemble_test_only}`. Any other non-empty `POSTGRES_DB` → `pytest.exit` ("HARD ABORT"), 0 tests executed. Unset/empty/`ensemble_prod` is rewritten in-process to the fictional test DB.
**Recipe for scoped pack runs:** `env -u POSTGRES_DB ENSEMBLE_SELF_ENV=dev ... pytest ...` (do not "helpfully" point `POSTGRES_DB` at your scratch DB — it aborts). The PG pack is immune because it reads `PG_TEST_*`.

## 3. Fresh SQLite boot has TWO pre-existing blockers
1. `20260714_000001` — `ALTER TABLE ... DROP CONSTRAINT IF EXISTS` → SQLite syntax error (documented).
2. `20261018_000001` — **duplicate version prefix** shared by `create_app_settings.sql` and `secrets_audit_add_context.sql` → `UNIQUE constraint failed: schema_migrations.version`. NOT documented in the critical note; affects `latest`, not just this branch.
A faithful fresh boot = `SQLModel.metadata.create_all` **then** `MigrationRunner.run_pending_migrations()` (the runner is not a schema bootstrapper; a truly empty DB fails earlier at `20240101_000001` with `no such table: projects`).
MANUAL files (`-- MANUAL: TRUE`) are skipped **without a ledger row** — the ledger is consistent but records nothing for them.

## 4. Repair path vs fresh schema: server defaults differ
- Fresh `create_all` (fixed model): `boolean NOT NULL`, **`column_default = NULL`** (Python-side ORM default only).
- Integer→boolean repair: `DROP DEFAULT` → `TYPE boolean USING (col <> 0)` → `SET DEFAULT true|false`.
- The boot hook short-circuits to `already_ok` on an already-boolean column, so fresh DBs keep the NULL default forever. No functional impact (flusher binds explicitly); documentation-level asymmetry.

## 5. Flusher verification patterns that work
- Construct a private `PidRegistry()` (not the singleton); `register(kind ∈ {bash,chart_render,mcp_child,proc,service})` then `mark_exit(...)` — `mark_exit` MOVES the entry to `_history`, so assert counts on `_history`/DB, not `_live`.
- Same-second flush windows are idempotent (`UNIQUE(bucket_ts, interval_s)` + `ON CONFLICT DO NOTHING`): use distinct `bucket_ts` offsets to measure deltas, or a "second run" looks like a silent failure.
- `_ensure_postgres_columns` first calls `_migrate_overloaded_image_refs_rows()` (fail-loud `UPDATE message_queue`): to unit-drive the hook, import `daemon.manager` and `create_all` the full model surface, or it raises `UndefinedTable` before reaching its own DDL. The `_MinimalManagerProxy` in `tests/postgres/test_report_deferred_migration_pg.py:321` lacks `_ensemble_config` → pre-existing failure.
- The new per-tier swallow path increments `counters.errors`; a benign missing relation returns 0 WITHOUT incrementing (pinned by item H).

## 6. FE verification: `/live` is transient, `estimated` is not rendered
- `/live` pid/rss/threads/fds are null in ~33% of frames (raw ring drained every 60s, refilled every 30s). Poll for a populated frame; a `—` placeholder is not a defect.
- `/live.instances` is empty until an instance exists in the sampler universe (graph-cache/charge registry) — `POST /api/instances` then a message turn produces real charge rows.
- `estimated` is serialized only by `/live`; no FE surface renders it. History/traceback expose buckets/pid-map only.
- Worktree `frontend/node_modules` is absent: symlink the main repo's (Angular CLI + Playwright 1.60.0 + cached chromium), run `./node_modules/.bin/ng serve`, remove the symlink after. Never bare `npx`.

## 7. Scratch DB recipe (PG 16.15 local)
```
sudo -n -u postgres psql -c "CREATE ROLE rt_verify_x LOGIN PASSWORD '…';"
sudo -n -u postgres psql -c "CREATE DATABASE ensemble_rt_scratch_x OWNER rt_verify_x;"
# ... run ...
sudo -n -u postgres psql -c "DROP DATABASE ensemble_rt_scratch_x;"
sudo -n -u postgres psql -c "DROP ROLE rt_verify_x;"
```
Verify with `pg_database`/`pg_roles` counts (not `psql -lqt | grep`, which substring-matches sibling names like `..._fe` vs `..._f`).
