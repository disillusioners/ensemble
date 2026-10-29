# Phase-0 measurement harness — eventloop-sync-DB hot paths lane

**NOT SHIPPED.** Measurement scaffolding committed under
`.agents/tester/harness/eventloop-phase0/` for the
`fix/eventloop-sync-db-hotpaths` lane (board `wi-3f5bea95`). Zero
production code; the daemon is imported and booted read-only.

## What it measures (BASE baseline at the unmodified lane base)

The lane's premise: the FE's hot read paths issue **synchronous DB
round-trips on the event loop** (`daemon/routers/instances.py` calls
`manager.list_instances` directly in the async handler; the per-row
`list_child_ids_permanent` N+1 rides the same frame), and the jobs
enrichment (`/api/jobs` → `list_work` no-limit) saturates the default
executor. The harness baselines exactly the numbers that prove the
phase-1 fix (ruling Q3):

| # | Measurement | Instrument |
|---|---|---|
| N1 | FE-shape load: badge 8s forkJoin composite, home 10s BFS(100), jobs page 30s no-limit, per-endpoint floor | concurrent httpx vs an in-process real daemon |
| N2 | wedge-capture stack pin — MAIN thread contains `list_child_ids_permanent` + `routers/instances.py` (positive at BASE, flips negative at HEAD) | the daemon's own wedge-capture, knobs tightened via env |
| N3 | loop-lag under load (p50/p95/max per profile) | the daemon's own `LoopLagProbe` via a harness-only route |
| N4 | per-request SQL round-trips (formula validation: flat=3+N, BFS=4+levels+rows, defer-blocked=2+holders, missions=3) + on-loop vs off-loop attribution | SQLAlchemy `before_cursor_execute` listener registered by the harness on the manager engine; requests tagged via `X-Evloop-Tag` header → contextvar |
| N5 | expected-collected-count fence on EVERY gate | `Fences` collector; exit 1 on any miss |

## Entrypoint (re-runnable; the tester gate RE-RUNS this at BASE and HEAD)

```bash
bash .agents/tester/harness/eventloop-phase0/run_all.sh --profile=smoke  # ~5 min
bash .agents/tester/harness/eventloop-phase0/run_all.sh --profile=full   # ~20 min
```

Outputs land in `raw/` (`BASELINE.<profile>.json`, `run_<profile>.out`,
`faulthandler_*.txt`, per-run workdirs incl. the wedge-capture
artifacts). The per-run JSON report is PROFILE-SUFFIXED
(`BASELINE.full.json` / `BASELINE.smoke.json`) — a `--profile=smoke`
re-run can never overwrite the committed full-scale baseline. The
committed evidence set is `BASELINE.full.json` + `run_full.out` +
`evidence/`; the smoke outputs (`BASELINE.smoke.json`,
`run_smoke.out`) are gitignored transient re-run artifacts.

## The daemon child

`child.py` boots the REAL daemon in-process (`create_app()` + manual
`lifespan` + uvicorn on a free TCP port — the
`tests/helpers/soak_gate_daemon_child.py` shape) and layers
harness-only instrumentation AFTER `create_app()`:

* a pure-ASGI tagging middleware (`X-Evloop-Tag` header → contextvar;
  undeclared headers change no endpoint behaviour);
* `/harness/lag`, `/harness/counters`, `/harness/loopinfo` routes;
* the N4 event listener on `manager._engine`.

The capture ROOT is `ENSEMBLE_DATA_DIR`-derived: the real boot passes
`data_dir` to `WedgeCaptureConfig.from_env`, which WINS over
`ENSEMBLE_WEDGE_CAPTURE_DIR` — so the env var alone cannot redirect
it. The harness pins `ENSEMBLE_DATA_DIR` at a mktemp tree instead
(documented gotcha; no production change needed).

## Fences (binding)

* **Scratch PG only** — throwaway LOGIN role + owned DB created and
  DROPPed by the harness (`common.create_scratch_pg` /
  `drop_scratch_pg`, the soak-gate `soak_pack_scratch_db` shape). The
  child REFUSES to boot unless the DB name is scratch-shaped (guard
  kept verbatim at `child.py` import).
* **Per-variable env scrub** — `run_all.sh` unsets every
  `POSTGRES_*`/`PORT`/`DAEMON_PORT` variable BY NAME before exec
  (`env -u POSTGRES_*` is a silent no-op; never used), and
  `common.assert_clean_env()` re-checks in-process, refusing to run if
  anything survived. The child env is then constructed explicitly
  (POSTGRES_* pins, `ENSEMBLE_SELF_ENV=demo`, `ENSEMBLE_AUTH_ENABLED=false`,
  `MCP_DISABLE_BUILT_IN_{CONTEXT7,PLANE}=true`, tightened
  `ENSEMBLE_WEDGE_CAPTURE_*` knobs).
* **No live contact** — free port picked by bind (never 9797/8079/7979,
  asserted + proven freed at teardown), `HOME` and `ENSEMBLE_DATA_DIR`
  under a harness mktemp tree, no live install path.
* **No `.env` edits** — the child env is constructed in-process; the
  worktree `.env` is never read by the harness.
* **Dual-layer timeouts** — runner `timeout` (full 2400s / smoke 600s)
  + per-phase internal budgets.

## Fixture (deterministic, seed=20261029)

`seed.py` bulk-inserts the prod-shaped population through the daemon's
real table metadata after `create_all`:

* full: **3372 instances** (392 roots; branching 2–3 depth 2–3 + ten
  depth-10 chains + loose leaves + KB rows), **4000 done JobItems** on
  real instances, **1250 orphan-instance JobItems**, 30 active + 50
  queued, ~25 defer-gate witness Task rows (paused+running holders);
* smoke: the same shape at ~1/10 scale (350 instances / 400 done / 125
  orphans).

## Formula table (N4 validation targets)

| Shape | Census formula |
|---|---|
| `/api/instances?limit=10…include_descendants=false` | 3 + limit = 13 round-trips |
| `/api/instances?limit=100…include_descendants=true` | 4 + levels + rows |
| `/api/queues/defer-blocked` | 2 + holders |
| `/api/missions?liveness=…&limit=20` | 3 / page |

The jobs shapes are reported as measured (enrichment-heavy; the
decomposition splits row-hydration vs orphan `_lookup_instance`
statements by shape).

## Scale / runtime disclosure

The `full` profile is prod-shaped (~3.3k instances / ~5.3k JobItems).
If a host cannot afford the full window, `--profile=smoke` exercises
EVERY measurement path at 1/10 scale — that is the re-run profile the
tester's verifier uses.
