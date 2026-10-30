# BASELINE — event-loop sync-DB hot paths (Phase-0, board `wi-3f5bea95`)

**Tree:** `b1183a637649f94c23b94dfc745df44381dc54ab` (unmodified lane base ==
`origin/latest` == the v0.27.13 bump commit). **Harness:**
`.agents/tester/harness/eventloop-phase0/` — `run_all.sh --profile=full`,
**34/34 fences PASS, exit 0** (smoke re-run PASS before it). **Raw record:**
`…/eventloop-phase0/raw/BASELINE.full.json` (full schema) + `raw/run_full.out` +
`raw/evidence/` (5 wedge-captures incl. the 4 narrow-pin stacks + the
faulthandler dump). This document carries the headline numbers; the JSON is
authoritative.

**Method:** the REAL daemon (`create_app` + real lifespan + uvicorn, banner
v0.27.13) booted in-process against a throwaway scratch PostgreSQL 16.15
database (throwaway LOGIN role + owned DB, created and DROPPed by the harness —
`db_rows_after=0 role_rows_after=0` drop proof in the log), loaded with the
deterministic prod-shaped fixture (seed=20261029): **3372 instances** (392
roots; branching 2–3 depth 2–3 + ten depth-10 chains + loose + 12 KB rows),
**4000 done JobItems** on real instances, **1250 orphan-instance JobItems**,
30 active + 50 queued, 21 defer-gate witness Task rows (20 paused + running
holders). Load = concurrent httpx against `127.0.0.1:<free-port>`
(bind-verified ∉ {9797, 8079, 7979}, proven freed at teardown).

**Instruments** (harness-side only, zero production edits): per-request SQL
round-trip counter = SQLAlchemy `before_cursor_execute` listener on the
manager engine, requests tagged `X-Evloop-Tag` header → contextvar, statements
attributed loop-thread vs other via the daemon's own `LoopLagProbe`
`loop_thread_native_id`; loop-lag = the daemon's own probe (window-local
latest-sample series, see §4); stack pin = the daemon's own wedge-capture with
tightened env knobs; executor saturation = `sys._current_frames` series +
verbatim faulthandler dumps.

---

## 1. Census formulas — VALIDATED EXACT (unloaded floor, serialized)

| Endpoint shape (FE-verbatim) | Census formula | Expected | **Measured** | on-loop | p50 | p95 | max |
|---|---|---|---|---|---|---|---|
| `/api/instances?limit=10&offset=0&exclude_kb=true&include_descendants=false&order=activity` | `3 + limit` | 13 | **13.0** | 13/13 | 59.2 ms | 67.8 ms | 126.9 ms |
| `/api/instances?limit=100&offset=0&exclude_kb=true&include_descendants=true` | `4 + levels + rows` | 4+3+241=248 | **248.0** | 248/248 | 858.7 ms | 1000.1 ms | 1143.8 ms |
| `/api/queues/defer-blocked` | `2 + probed holders`¹ | 2+87=89 | **89.0** | 89/89 | 729.0 ms | 822.3 ms | 822.3 ms |
| `/api/missions?liveness=processing,pending,paused&limit=20` | `3 / page` | 3 | **3.0** | 3/3 | 18.4 ms | 20.9 ms | 20.9 ms |

¹ refined from the census's `2+holders`: the WS1 stall-classification probe
skips PAUSED holders — with 100 holders (13 paused / 87 live) expected =
2 + 87 = 89. The census's literal `2+holders` would predict 102.

`levels` for BFS is measured from the returned rows' parent chains
(`page_levels=3`, 241 rows, total 380 roots); flat `page_levels=0` (no
descendants loaded), rows=10 of total 3360 (KB excluded).

## 2. Round-trips per request — all shapes (floor, ×30 instance shapes / ×10 rest)

| Shape | rts/req | **on-loop rts** | p50 | statements by shape (per req) |
|---|---|---|---|---|
| instances flat(10) badge | 13 | **13 (100%)** | 59 ms | `SELECT instances`×11 (count+page+10 children), `instance_ui_prefs`×2 |
| instances BFS(100) home | 248 | **248 (100%)** | 859 ms | `SELECT instances`×246, prefs×2 |
| `/api/work` | **1873** | **1873 (100%)** | **8112 ms** | `SELECT instances`×~1867 + jobitem/task selects |
| `/api/queues/defer-blocked` | 89 | **89 (100%)** | 729 ms | witness×1 + pending×1 + 87 per-holder probes |
| `/api/jobs?status=queued,active` (badge active) | 7 | **0 (0%)** | 69 ms | all via `asyncio.to_thread` |
| `/api/jobs?status=…&limit=10` (badge recent) | **1439** | **0 (0%)** | **6294 ms** | `SELECT instances`×1434, `job_queue_items`×4, `task`×1 |
| `/api/jobs?limit=100` (jobs page) | **1874** | **0 (0%)** | **8105 ms** | same family, ×page size |
| `/api/missions…&limit=20` | 3 | **3 (100%)** | 18 ms | count+page+jobitem batch |

**The two defect families, cleanly separated by the on-loop attribution:**
the instances/defer-blocked/work/missions shapes issue **every** round-trip
synchronously ON THE EVENT LOOP (sync calls directly inside the async
handlers); the jobs shapes issue **zero** on the loop — their no-limit
enrichment runs via `asyncio.to_thread` and lands on the DEFAULT EXECUTOR
(saturation profile §5). Both are the lane's targets (R-1 hot paths + the
badge storm).

## 3. Loaded latencies (badge 8s forkJoin composite, sustained 90s; home 10s tick, 60s)

Badge composite (5 concurrent legs/tick, 12 ticks): per-leg p50 / p95 —

| Leg | p50 | p95 |
|---|---|---|
| jobs recent(10) — the no-limit enrichment | **7391 ms** | 8020 ms |
| jobs active | 1086 ms | 1181 ms |
| defer-blocked | 876 ms | 998 ms |
| instances flat(10) | 78 ms | **914 ms** (unloaded p95 was 68 ms — queued behind loop-blockers) |
| missions(20) | 75 ms | 1022 ms |

Home BFS(100) under its own tick: p50 901 ms / p95 983 ms (n=6). **One badge
tick holds the event loop for seconds; every concurrent leg queues behind
it** — the flat badge leg's p95 inflates 13× purely from loop queueing.

## 4. Loop-lag (the daemon's own probe; window-local latest-sample series)

| Profile | samples | p50 | p95 | max |
|---|---|---|---|---|
| badge composite | 169 | 0.499 s | 0.529 s | 0.903 s |
| home BFS | 111 | 0.500 s | 0.508 s | 0.555 s |
| jobs page | 288 | 0.500 s | 0.510 s | 0.764 s |
| capture phase (badge+home @ 4s/2s) | 212 | 0.499 s | **1.408 s** | **2.215 s** |

Probe histogram tails recorded alongside (max observed 1.98–2.22 s in the
capture phase). NOTE: the probe's rolling histogram is bounded at 300 samples
and saturates during long phases — the window-local metric is the
harness-built latest-sample series of the probe's own ticks (methodology in
the harness README + `driver.window_lag`); values quantize on the 0.5 s
watchdog tick lattice.

## 5. Executor saturation during the jobs no-limit load (P2 positive control)

Default executor: `ThreadPoolExecutor max_workers=12`. During the jobs_page
window (150 s, 287 sampler points): up to **5 concurrent threads** inside the
repositories statement family, 45/287 samples nonzero (a single `/api/jobs`
resolve occupies one worker for ~8 s; the badge's every-8s recent leg keeps
one nearly permanent). The verbatim faulthandler dumps
(`raw/evidence/faulthandler_jobs_page.txt`) show the expected chain inside
default-executor threads: **`work_resolver.py:1461 list_work → :1462 genexpr
→ _job_to_record → _lookup_instance → instance repository get`** (the
`1461→1847→2002→repository` family; exact line numbers per this tree).

## 6. `/api/jobs` badge cost decomposition (statements + TIME split)

From the per-request N4 attribution across the loaded windows:

| Statement family | statements/req | share of statement count | **share of statement TIME** |
|---|---|---|---|
| single-row `SELECT instances` (`_lookup_instance` — the 1250-orphan population) | ~1434 (recent) / ~1869 (page) | 99.6% | **92.3%** (13.11 s of 14.20 s over 5 page requests; 89.9% in the badge window) |
| `SELECT job_queue_items` (row hydration) | 4–20 | 0.3% | 5.3% |
| `SELECT task` | 1 | <0.1% | 2.4% |

**The orphan-id `_lookup_instance` lookups ARE the badge jobs cost** — the
page `limit=10` does not bound the enrichment (`list_work` resolves the whole
matching population, and every orphan misses the instance batch and falls to
a per-row lookup).

## 7. Wedge-capture stack pin (N2 — positive control at BASE; flips NEGATIVE at HEAD)

8 captures fired (7 `sustained_cpu`, 1 `thread_concentration`; knobs
tightened via the real `ENSEMBLE_WEDGE_CAPTURE_*` env). Verdicts:

* **family pin: 8/8 captures** show a thread section containing
  `daemon/routers/instances.py` or `daemon/repositories/instance/repository.py`
  over SQLAlchemy/psycopg frames — the loop-thread sync-DB instances path;
* **narrow pin: 4/8 captures** show `list_child_ids_permanent` AND
  `daemon/routers/instances.py` in the SAME thread section — e.g.
  `repository.py:433 in list_child_ids_permanent ← routers/instances.py:476
  in list_instances` (verbatim in `raw/evidence/*/stacks.txt`).

Both pins are the assertions that must flip to NEGATIVE at HEAD (shape A+C:
children round-trips O(rows)→O(1) and off-loop; the loop thread must never
sit in a sync-DB instances-path frame).

---

## Fences honored

- **Scratch PG only** — throwaway role+DB created and DROPPed by the harness
  (`db_rows_after=0 role_rows_after=0` recorded); child refuses non-scratch
  DB names; boot-time drift check `POSTGRES_DB == provisioned scratch db`.
- **Per-variable env scrub** — `run_all.sh` unsets each `POSTGRES_*`/PORT var
  BY NAME (the `env -u POSTGRES_*` glob idiom is a silent no-op, never used);
  `assert_clean_env()` refuses to run if anything survived; child env
  constructed explicitly in-process.
- **No live contact** — free port bind-verified ∉ {9797, 8079, 7979}, proven
  freed at teardown; `HOME` + `ENSEMBLE_DATA_DIR` under a harness mktemp tree
  (wedge-captures land there — the real boot's `from_env(data_dir=…)` WINS
  over `ENSEMBLE_WEDGE_CAPTURE_DIR`, documented in the harness README); MCP
  builtins disabled (no network); auth inert for the driver.
- **No `.env` edits anywhere** — the worktree `.env` is never read by the
  harness; `daemon/services/readiness.py` untouched; every daemon-enforced
  fence preserved.
- **N5** — 34/34 expected-collected-count fences green (every sample set,
  capture set, formula, and teardown asserted).
- **Commit-but-no-push** — branch commit only; no merge, no promote, no
  worktree deletion.

## Re-run instructions (the tester gate re-runs this at BASE and HEAD)

```bash
cd <checkout of the tree under test>
bash .agents/tester/harness/eventloop-phase0/run_all.sh --profile=smoke   # ~5 min, every path
bash .agents/tester/harness/eventloop-phase0/run_all.sh --profile=full    # ~18 min, prod-shaped
```

Additive-only: overlay at BASE via
`git checkout fix/eventloop-sync-db-hotpaths -- .agents/tester/harness/eventloop-phase0`.
Exit 0 = all fences green. Outputs: `raw/BASELINE.<profile>.json` (stable
schema; PROFILE-SUFFIXED — a smoke re-run cannot overwrite the committed
full baseline), `raw/run_<profile>.out`, `raw/evidence/`.

## Disclosures / open questions

- Harness request tagging uses an `X-Evloop-Tag` **header** (undeclared
  headers change no endpoint behaviour; chosen over a query param for the
  same reason). Endpoint URLs are FE-verbatim.
- The capture phase drives the SAME shapes at a tighter driver-side cadence
  (4s/2s vs FE 8s/10s) so the dump lands inside the sync-DB frames — a
  driver-side instrumentation choice, not an endpoint-shape change.
- The lag probe's histogram quantizes on the 0.5 s watchdog tick lattice
  (p50 ≈ 0.50 s everywhere is the lattice, not a finding); the informative
  numbers are p95/max (up to 2.2 s under the capture-phase storm).
- Defer-blocked formula refinement (`2 + non-paused holders`) documented in
  §1; the census's `2+holders` holds only when every holder is live.
- The wedge-capture `db_capture` census warnings (`c.n_live_tup does not
  exist`) seen in the log are the KNOWN pre-existing R-10 defect
  (wedge-capture store-census born-broken on real PG) — not caused by the
  harness, routed to the wedge-capture lane.
- **No production change was needed for any measurement.** No open questions
  blocked by fences.
