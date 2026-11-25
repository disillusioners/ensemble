# Post-fix fenced boot smoke — fix/post-outage-recovery (ratification ITEM 1)

**Date:** 2026-11-24 · **Worktree:** `/home/nea/ensemble-src-wt-post-outage-recovery`
**Branch:** `fix/post-outage-recovery` · **HEAD:** `1ab6cceaeab8afaa350445e985127d283f2d2b08`
**Purpose:** persist the POST-fix boot-smoke artifact that closes the reviewer's BOOT-EVID nit — the
pre-fix smoke (tester gate §4, worker `d13b2512`) RED'd on `[STALE_RUNNING_SWEEP_DB_ERROR]`
(`UndefinedColumn: task_completed_at`, PG ORDER-BY alias). HEAD carries the D1 derived-table fix.

## Verdict: PASS — clean boot, clean sweep, clean shutdown

## Env fence (POK `engine-boot-fence-env-recipe`, VERBATIM)

Sourced the worktree `.env` (`ENSEMBLE_SELF_ENV=dev`, `POSTGRES_DB=ensemble_dev@127.0.0.1:5432`,
user `ensemble_dev_app`, mode 600, gitignored), then applied the recipe verbatim:

```
source .env
PORT=8092                                   # fresh (8090/8091 used by tester lane; 8079 free)
DAEMON_PORT=8092                            # fresh
POSTGRES_DB=ensemble_dev                    # explicit — dev only, never prod
DATA_DIR=./data_dev
PERSISTENCE_DB_PATH=./data_dev/instances.db
ENSEMBLE_DB_AMBIENT_ALLOW=1
ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0    # PINNED EXPLICITLY (no `env -u` — that strips the guard)
LIMITS_METRICS_LISTENER_PORT=8093           # fresh (default 9798 held by live ensemble-prod)
.venv/bin/python -m daemon                  # worktree venv (Python 3.14)
```

Guard was load-bearing and present: log line 1 records `SWEEP_GUARD=0`. ZERO process kills occurred.
Live `ensemble-prod` listeners on 9797 / 9798 / 7979 were untouched.

## Load-bearing lines (verbatim from the log)

```
# line 1   — fence record
=== BOOT SMOKE START 2026-11-24T16:21:58Z PORT=8092 DAEMON_PORT=8092 DB=ensemble_dev SWEEP_GUARD=0 ===

# line 197 — sweep service start
16:22:27 - daemon.api - INFO - StaleRunningSweepService started: interval=300s (default 300s) kill_switch=on

# line 222 — THE money line (pre-fix this never emitted; DB_ERROR fired instead)
16:22:27 - daemon.services.stale_running_sweep - INFO - [STALE_RUNNING_SWEEP] summary: candidates=0 healed=0 downgraded=0 no_effect=0 skips(task=0 msg=0 inj=0 bus=0 tree=0 member_task=0 question=0 young=0 posture=0 truncated=0 probe_err=0) errors=0 duration_s=0.053 mode=on

# line 223 — supervisor leg also clean
16:22:27 - daemon.services.scheduled_fire_supervisor - INFO - [SCHEDULED_SUPERVISOR] summary: candidates=0 redriven=0 recovered=0 skipped_processing=0 dead_lineage=0 cap_terminal=0 cas_lost=0 refused=0 cancelled=0 errors=0 duration_s=0.052 mode=on

# lines 290-291 — ready
INFO:     Application startup complete.
16:22:29 - __main__ - INFO - Uvicorn running on http://0.0.0.0:8092 (Press CTRL+C to quit)

# lines 375-376 — graceful shutdown
INFO:     Application shutdown complete.
INFO:     Finished server process [2295416]
```

## Health probes

- `GET /livez` → **HTTP 200**
- `GET /readyz` → **`"status":"ready"`** — components all true
  (`database`, `queue_freshness`, `services`, `checkpoint_saver`; `checked_at 2026-11-24T16:22:49Z`).

## Error census (whole boot log, 300 lines)

| Pattern | Count |
|---|---|
| `[STALE_RUNNING_SWEEP_DB_ERROR]` | **0** |
| ` ERROR ` (all ERROR-level lines) | **0** |
| `Traceback` | **0** |
| `CRITICAL` | **0** |

Contrast pre-fix: 1 `[STALE_RUNNING_SWEEP_DB_ERROR]` (UndefinedColumn), summary line never emitted.

## Boot wall-clock

- Process launch (log line 1) `16:21:58Z` → `Application startup complete` `16:22:29Z` = **~31 s**.
- Internal `boot_epoch=2026-11-24T16:22:19.500804Z` → readyz-healthy leg `16:22:29Z` ≈ **~9.5 s**.
- Emperor boot SLO (2026-11-21): **≤ 120 s → PASS** (prior fix measured 8 s internal; same order).

## Shutdown / hygiene

- SIGTERM (graceful, no force): `Shutting down` → `Waiting for application shutdown.` →
  `Graceful shutdown complete` → `Application shutdown complete.` → `Finished server process [2295416]`.
- PID 2295416 confirmed **gone** (`kill -0` fails); `pgrep -af 'venv/bin/python -m daemon'` → none.
- Port 8092 **released** (`ss -ltnp` shows no listener). No stray daemon or orphan processes.

## Evidence

- Log: `/tmp/ratification_bootsmoke_20261124.log` (51,815 bytes, 300 lines)
- sha256: `da274039545e7ab8cf30623adbf0a1b59a0f9b2dd1f4347aa30c87171b48ddaa`
- Launcher: `/tmp/ratification_bootsmoke_launch.sh` (fence setup, `exec .venv/bin/python -m daemon`)
