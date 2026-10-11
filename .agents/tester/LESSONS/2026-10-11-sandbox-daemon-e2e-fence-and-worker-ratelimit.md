# Sandbox daemon E2E — provisioning fallback, fence recipe, worker rate-limit hygiene

**Date:** 2026-10-11 · **Commission:** designer-pipeline dev-env E2E (RESULTS/2026-10-11-designer-pipeline-dev-e2e.md)

## 1. `ensemble_dev_app` CANNOT create databases on the shared dev PG (127.0.0.1:5432)

`rolcreatedb=f, rolsuper=f` — `createdb` and `CREATE DATABASE` both denied. Do NOT hunt for superuser or ALTER the shared role. **Standing fallback: disposable local PG cluster** — `initdb -D $SBX/pgdata -A trust -U ensemble_dev_app && pg_ctl -D $SBX/pgdata -o "-p <port> -k /tmp" -l pg.log start && createdb … ensemble_sandbox`. Precedents: 2026-09-09 W1 lane (15432), snapshots-v2 (15532), this lane (15442). Bonus isolation: the sandbox DB physically cannot collide with any shared state. Daemon boots fine on a fresh empty DB (column-ensure migration path, 56 tables, ~60s to readyz).

## 2. Reproducible env-poison fence for bare-script sandbox boots

Write the launcher as a `boot.sh` (itself evidence): **scrub-first** (`unset POSTGRES_* ENSEMBLE_DB_DSN ENSEMBLE_SELF_ENV ENSEMBLE_DATA_DIR DATA_DIR PERSISTENCE_DB_PATH QUEUE_DISCARD_ON_STARTUP MCP_POOL_TOOL_CALL_TIMEOUT` — ambient poison IS present in worker shells) → `set -a; source .env; set +a` → **RE-PIN the sandbox triple AFTER sourcing** (source re-sets POSTGRES_DB to ensemble_dev) → `ENSEMBLE_SELF_ENV=sandbox` → `DATA_DIR=$SBX/data` → uvicorn on a ≥8377 port, no `--reload`. Double DB proof: pre-boot `current_database()` + post-boot `pg_stat_activity` (all daemon conns must show the sandbox datname). **boot.sh gotcha: use append (`>>`) or timestamped log names on relaunch — `>` truncation destroyed the first-era daemon.log in this lane (evidence loss on an unexplained death).**

## 3. Observer-worker LLM rate limits: survive via batched bash, key liveness on PORT not PID

Two workers died mid-drive on their own model lane (`openai: Rate limit reached`). The sandbox daemon is a separate OS process — pipeline runs to completion unattended; ALL evidence is re-derivable from durable stores (API transcripts, sandbox-DB SELECTs, daemon.log). Hygiene for observation workers: poll with in-bash sleep loops (`for i in $(seq 1 20); do curl …; sleep 15; done` inside `timeout 300`) instead of many LLM turns; recovery worker should be spawned on `model_tier='high'` (different lane). **Never trust a stale PID across a daemon relaunch — verify via `ss -ltnp` port→pid mapping + `/proc/<pid>/cmd` + cwd before any kill.**

## 4. Sandbox agents' file-writes are NOT fenced (containment leak)

Agent filesystem writes resolve against the daemon CWD (the checkout) — the designer wrote planning artifacts into the MAIN checkout `.agents/shared/planning/<feature>/`. Until a write-root fence exists, sandbox E2E teardown must sweep the checkout for run-window-mtime artifacts and `mv` them into the sandbox evidence dir (gate on mtimes inside the run window; `find -printf '%TY-%Tm-%Td %TH:%TM'` — NOT `%H` which is start-point/mode).
