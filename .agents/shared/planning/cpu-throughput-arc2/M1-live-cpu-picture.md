# M1 — Measured Live CPU Picture (agents-ensemble daemon, v0.31.22)

**Lane:** cpu-arc2 M1 (analysis-only, read-only vs live). **Captured:** 2026-11-21
03:39–03:49 UTC. **Host:** `ensemble-vm`, 16 vCPU (`nproc`=16, `/proc/cpuinfo`
processor count=16). **Branch base:** 0b5624dc9 (v0.31.22).

## Scope / fences honored

READ-ONLY throughout: `/proc` reads, `ps` reads, GET-only HTTP (no auth token
minted — that would be a live DB write, forbidden), read-only wedge-capture
harvest. No restarts, no config changes, no load injection, no `git add -A`.

### Live PID re-verification (done, not assumed)

- `ss -tlnp | grep 9797` → `LISTEN 0 2048 0.0.0.0:9797 ... ("ensemble-prod",pid=1208513,fd=6)`.
- `pgrep -af ensemble-prod` → process tree `launcher.sh(1208310) → ensemble-prod(1208450, 1 thread, supervisor) → ensemble-prod(1208513, 65–69 threads, the daemon)`.
- **Excluded:** demo instance `304598`/`304612` (`/home/nea/agents-ensemble-demo`,
  listens `127.0.0.1:7979`, a DIFFERENT instance); code-server `1209396`;
  context7-mcp children.
- `/api/health` → `{"version":"0.31.22","current_database":"postgres","uptime_seconds":6309}`
  (GET, public) — confirms the briefed version.

---

## Q1 — How many cores does the daemon actually use, and during what?

**Answer: the daemon consumes well under one core of real CPU (~0.6–0.75 of a
single core in steady state), briefly crossing one core only during LLM-stream
bursts (durable wedge captures: 97–143% of ONE core). It is effectively
single-core-bound — not a multi-core workload.** Machine-wide aggregate during
the windows was 0.97 → 2.68 core-equivalents, but the excess over ~1 core is
FOREIGN lanes (see "Foreign lanes"), not the daemon.

- Daemon's own CPU = sum of per-thread `utime+stime` deltas for PID 1208513:
  **63.3% (W0), 75.4% (W1), 71.4% (W2) of one core** (`m1_report.json` →
  `total_thread_cpu_pct`). At 100 Hz (`clk_tck=100`) that is ~0.6–0.75 core.
- Durable wedge captures (20, see Q5) report `process_cpu_pct` 97.069–143.046%
  of one core during `sustained_cpu` episodes — the daemon never exceeds ~1.4
  cores even at its peak.
- **"During what": agent turns were IN FLIGHT for the entire measurement.**
  Public `/readyz` `detail.inflight_turns` = 5 → 5 → 5 → 6 → 6 → 7 across the six
  boundary snapshots (`/tmp/m1_run/readyz_w{0,1,2}_{a,b}.txt`). The key wedge
  capture's `db.json` shows `executing_turns_count=6`: 2 `explorer` + 4 `worker`
  agents, all `task_type=process_message`. Stack families (Q3/Q5) attribute the
  CPU to: (a) sync LLM streaming on executor threads, (b) checkpoint writes on
  the main thread, (c) the 200 ms ram-guard `/proc` scan, (d) wedge capture +
  metrics sampling. So the honest "during what" is **live agent turns doing LLM
  I/O + checkpoint persistence, with a constant ram-guard/metrics background tax** —
  not a benchmark or idle spin.

---

## Q2 — Per-core utilization: `/proc/stat` two-read jiffie deltas (3 windows)

Method: read `/proc/stat` `cpu`/`cpu0..cpu15` fields user..steal (fields 2–9),
sleep, re-read; `busy = Δtotal − Δ(idle+iowait)`, `pct = busy/Δtotal`. Windows
60 s + 60 s + 300 s (`m1_sample.sh`, `clk_tck=100`, actual walls 60.00/60.00/300.00 s).

| core | W0 60s | W1 60s | W2 300s |
|---|---|---|---|
| cpu (aggregate/16) | 6.05% | 13.02% | 16.72% |
| cpu0 | 10.92% | 12.58% | 18.46% |
| cpu1 | 6.30% | 12.09% | 16.91% |
| cpu2 | 6.14% | 14.44% | 15.58% |
| cpu3 | 4.54% | 9.09% | 15.92% |
| cpu4 | 5.65% | 9.65% | 14.71% |
| cpu5 | 8.46% | 15.14% | 16.77% |
| cpu6 | 12.71% | 17.76% | 15.80% |
| cpu7 | 6.84% | 16.05% | 22.31% |
| cpu8 | 1.55% | 10.80% | 17.76% |
| cpu9 | 2.06% | 10.40% | 17.26% |
| cpu10 | 1.46% | 9.66% | 13.00% |
| cpu11 | 8.53% | 13.48% | 13.74% |
| cpu12 | 6.76% | 12.91% | 16.64% |
| cpu13 | 8.73% | 21.57% | 16.16% |
| cpu14 | 2.64% | 13.94% | 20.31% |
| cpu15 | 3.53% | 8.79% | 16.16% |

Core-equivalents = aggregate% × 16 / 100 → **0.97 / 2.08 / 2.68 cores machine-wide**.
No core is pinned hot to the daemon: utilization is spread across all 16 cores
(the main loop + OS scheduling spread it), and the W2 rise is dominated by the
foreign vitest lane (below). Raw deltas: `m1_report.json` `per_core` per window.

### Foreign-lane attribution (ps snapshots at every window boundary)

- `ps -eo pid,ppid,pcpu,... --sort=-pcpu` captured at all six boundaries
  (`/tmp/m1_run/ps_w{0,1,2}_{a,b}.txt`).
- **Foreign lane observed:** a `vitest`/`tsc` frontend test run in
  `/home/nea/Code/opensource-projects/llm-supervisor-proxy-wt-imggen-groups/pkg/ui/frontend/`
  — root PIDs `1281389` (npm exec vitest), `1281415` (vitest), `1281810`/`1281836`
  (tsc), workers `1281857`–`1282052`, esbuild `1281446`. It appeared ~03:44:50,
  MID-window-2, and was gone by the W2_b boundary (03:48:44). It inflated W2's
  machine-wide aggregate but is **excluded from daemon attribution** (the daemon
  number is the per-thread census of PID 1208513 only, unaffected).
- The brief's "tool-metrics bench lane" was **not** observed as a distinct
  python/pytest process in any boundary snapshot; the only concurrent foreign
  CPU load was the vitest lane above plus the sampler's own short-lived
  `python3`/`ps`/`sh` (visible at boundaries, transient).
- Demo `304612` shows a constant ~3.0% (lifetime ps average) — separate instance, excluded.

---

## Q3 — Daemon thread census: which NAMED threads are hot

Method: for every `/proc/1208513/task/<tid>/` read `comm` + `stat`
(`utime+stime`, fields 14–15) twice across each window; delta/CLK/wall = per-thread
CPU%. Cross-checked with `ps -T -p 1208513` at each boundary (68-line listing in
`pst_w0_a.txt`). Full names come from the wedge capture `threads.csv` (`name`
column; `/proc/comm` is truncated to 15 chars).

**Consistently hot threads (top of every window):**

| thread (full name) | W0 | W1 | W2 | stack family (from key capture `stacks.txt`) |
|---|---|---|---|---|
| `MainThread` (ensemble-prod) | 17.6% | 17.7% | 23.2% | checkpoint write: `langgraph/checkpoint/postgres/aio.py:280 aput` → `daemon/checkpoint_adapter.py:838` |
| `ram-guard-tick` | 16.9% | 15.7% | 18.4% | `daemon/services/ram_guard/tree_reader.py:261 read_stat_entry` (`pathlib.read_text` of `/proc/<pid>/stat`, 200 ms poll — `guard.py:180 poll_interval_ms=200`) |
| `ThreadPoolExecutor-3_*` (several) | 4.4/2.6/…% | 9.4/7.8/…% | 2.3/2.3/…% | sync LLM stream: `daemon/services/llm_stream_watchdog.py:246 __iter__` → openai/httpx SSE → `daemon/graph.py:11162 _run_standard_with_retry` |
| `WedgeCaptureWatchdog` | 3.5% | 4.7% | 4.0% | `daemon/services/wedge_capture/collector.py` |
| `metrics-trend-sampler` | 1.95% | 2.05% | 1.78% | `daemon/services/metrics_query/sampler.py:161` |
| `ram-guard-lane` | 1.12% | 1.20% | 1.20% | `daemon/services/ram_guard/guard.py:713 _lane_loop` |

`ThreadPoolExecutor-3_N` workers are the shared, **unprefixed** executor that
runs `current_llm.invoke` off-loop (`llm_stream_watchdog.py:11-12`). Their
contribution tracks the number of concurrent LLM streams (high in W1 when turns
were actively streaming, lower in W2 as turns interleaved).

### Mapping against known executors (source-confirmed)

| executor | configured | observed threads (census) | source |
|---|---|---|---|
| db_offload | **6 max** (`DB_OFFLOAD_MAX_WORKERS_DEFAULT=6`, `constants.py:1817`; pool built `db_offload.py:342`, prefix `db-offload`) | **1 materialized** (`db-offload_0`) — ThreadPoolExecutor spawns lazily; 6 is the cap, not the live count | `db_offload.py:100,342` |
| memoir poignancy | 2 (`memoir_poignancy_worker.py:138`) | `memoir-poignancy_0` (1 seen) | `api.py:3308-3346` |
| memoir reflection | 1 (`memoir_reflection_worker.py:70`) | `memoir-reflect_0` | `api.py:3269-3287` |
| memoir embedding | executor (`memoir_embedding_worker.py:216`) | `memoir-embed_0` | `api.py:3168` |
| mcp proc-scan | 2 (`stdio_wrapper.py:130-131`) | `mcp-procscan_0` | `stdio_wrapper.py:808` |
| mcp secret-resolve | 2 (`warmup_pool.py:116-117`) | `mcp-secret_0` | `warmup_pool.py` |
| plane_sync | 2 (`plane_sync_service.py:254`, no prefix) | no distinctly-named thread in census (dormant during windows) | `plane_sync_service.py:254` |
| sources | 4 (`sources/registry.py:50`, no prefix) | no distinctly-named thread in census (dormant) | `sources/registry.py:50` |
| bash durable-insert | executor (`bash.py:155`) | `bash-durable-stamp_0/1` | `bash.py:155` |
| worker pool | default + chat | `worker-default-worker-0..6`, `worker-chat-chat-worker-0/1`, `TaskHeartbeat-*` (9), `TimeoutMonitor-*` | `worker_pool.py:136,376,1449` |
| other | — | `iou-sqp-1208513`, `AnyIO worker th`, `resource-monitor-sampler/-flusher`, `metrics-listener/-census`, `llm-stream-watchdog`, `StaleTaskRecovery`, `StalePauseSweepService`, `ReportDeliveryRecoveryService`, `log-sumup-timer`, `keep-alive-handler:shard-None` | see stacks.txt |

**Key finding:** the hot CPU is NOT in the "headline" executors (db_offload,
memoir, plane_sync, sources are all ~0 in the census). It is in (1) the asyncio
main thread doing checkpoint writes, (2) the ram-guard tick thread's 200 ms
`/proc` scan, and (3) the shared ThreadPoolExecutor running sync LLM streams.

---

## Q4 — Pool occupancy history (GET-only doors)

**Door reachability (recorded, not assumed) — `m1_metrics_get.sh`:**

| door | result |
|---|---|
| `GET /readyz` (public) | 200 — pool gauges + inflight turns (used below) |
| `GET /api/health` (public) | 200 — version/uptime |
| `GET /livez` (public) | 200 — alive |
| `GET 127.0.0.1:9798/metrics` (loopback, no auth) | 200 but **0 bytes** — the exposition gate is OFF (`metrics_listener.py:526-530`: kill-switch OFF ⇒ valid empty body, zero series). Looked here; no bucket series. |
| `GET /api/resources/{live,history,self-cost,anomalies,evidence,ram-guard}` | **all 302 → `/api/auth/login?...`** — OAuth2-protected. Minting a bearer token requires a live DB write (`daemon/auth/cli.py`), forbidden by the READ-ONLY fence, so these were NOT fetched. |

**Pool gauges actually obtained** from public `/readyz` `detail.pool_state`
(`daemon/services/readiness.py:394-401,558-568,658-685`):

| boundary | inflight_turns | queue_max_age_s | sqlalchemy {checkedout,size,overflow} | psycopg {size,max,available,waiting} |
|---|---|---|---|---|
| W0_a 03:41:35 | 5 | 25.6 | {0,15,-1} | {1,15,1,0} |
| W0_b | 5 | 25.9 | {0,15,-1} | {1,15,1,0} |
| W1_a | 5 | 25.9 | {0,15,-1} | {1,15,1,0} |
| W1_b | 6 | 0.1 | {1,15,-1} | {1,15,1,0} |
| W2_a | 6 | 0.1 | {1,15,-1} | {1,15,1,0} |
| W2_b 03:48 | 7 | 3.5 | {1,15,-1} | {1,15,1,0} |

**Interpretation:** pool occupancy is LOW and stable — the checkpoint-saver
psycopg pool holds 1 connection of max 15 with 1 available and 0 waiting
clients throughout; SQLAlchemy checked-out connections 0–1 of size 15. No pool
starvation, no checkout queueing. The CPU is driven by LLM I/O + checkpoint
writes + ram-guard, NOT by pool contention. The key wedge capture's
`manifest.json.pool_counters` agrees: `pool_checked_out=1, pool_size_current=1,
pool_max=15, pool_waiting_clients=0` (`psycopg_pool.get_stats`).

**`resource_daemon_bucket` CPU history over the ring window:** the live bucket
history is served only by the auth-gated `/api/resources/history`, so it was not
directly retrievable under the fence. The DURABLE proxy is the wedge-capture
corpus (Q5): 18 `sustained_cpu` episodes over 2026-11-20 09:37 → 2026-11-21
03:16, each with `process_cpu_pct`, sustained seconds, executing-turn count and
top threads — a coarse but complete record of the daemon's CPU excursions. The
key capture's `metrics.json` also carries `rss_kb=604468`, `thread_count=60`,
GC stats, and cgroup memory (`current_mb=1819, peak_mb=2789`).

---

## Q5 — Durable wedge captures (READ-ONLY harvest)

`m1_wedge_harvest.sh` over `/home/nea/agents-ensemble/data/wedge-captures`:
**20 captures — 18 `sustained_cpu`, 2 `sustained_loop_lag`.**

**Version mix (POK `offloop-audit-01` caveat honored — disclosed, not hidden):**
`{'0.31.10': 19, '0.31.22': 1}`. Only ONE capture is v0.31.22-era (the live
version); the other 19 are v0.31.10. Any version-comparison claim must account
for this mix. Census is re-derivable from these durable files (bit-exact from
the capture dir, per the POK row).

**Key v0.31.22 capture — `20261121T031643Z-ensemble-vm-sustained_cpu-e3292355`**
(captured 03:16:43Z, 20 s deadline, `manifest.json`):

- `summary`: "process CPU **125.8%** of one core sustained for **31.0s**
  (threshold 95.0% / 30.0s)"; `observed.process_cpu_delta_s=39.0`,
  `sustain_s=30.0`, `sustained_s=30.987`.
- `metrics.json.process`: `pid=1208513, thread_count=60,
  observer_native_id=1209394` (WedgeCaptureWatchdog).
- `readyz.json`: `inflight_turns=6, queue_max_age_seconds=4.31, ready=true`.
- `db.json`: `executing_turns_count=6` (2 explorer + 4 worker, all
  `process_message`); checkpoint store `checkpoints_rows=4155`,
  `checkpoint_blobs_rows=2758` (~42.6 GB blobs), PG activity 3 active / 18 idle
  / 1 idle-in-transaction / 32 total backends.
- `lag.json`: 300 samples, loop latency p50≈1.00 s, p95≈1.64 s, max 2.54 s,
  `probe_state=ok`, `never_fired=false` — the event loop stayed responsive
  despite the CPU load.
- `threads.csv` top (full names): `ThreadPoolExecutor-3_6` 0.34 s (18.2%),
  `ThreadPoolExecutor-3_10` 0.29 s (15.5%), `MainThread` 0.28 s (15.0%),
  `ram-guard-tick` 0.09 s, `WedgeCaptureWatchdog` 0.09 s,
  `ThreadPoolExecutor-3_7` 0.08 s, `ReportDeliveryRecoveryService` 0.06 s.
- `stacks.txt` (59 thread stacks) stack families: 4× ThreadPoolExecutor in
  `llm_stream_watchdog.py:246` LLM SSE; `MainThread` in
  `checkpoint_adapter.py:838 aput`; `ram-guard-tick` in
  `tree_reader.py:261`; `resource-monitor-flusher` in psycopg; the rest parked
  in `threading.py:373 wait` (TaskHeartbeat/worker/TimeoutMonitor/memoir/etc.).

**Corpus-wide pattern (top-1 hot thread per capture):** the hot thread is
`ThreadPoolExecutor-3_*` (LLM stream) in **14/20** captures, `MainThread`
(checkpoint write) in **5/20**, `ram-guard-tick` in **1/20** — consistent with
Q3. Two captures (`153827Z`, `185206Z`) have
`process_cpu_pct=null` but are `sustained_loop_lag` triggers with short sustain
(15 s / 18.6 s); disclosed rather than dropped.

---

## Method / reproduction / honesty

- Probe scripts (committed): `benches/m1_sample.sh` (per-core + thread census +
  boundary ps/readyz), `benches/m1_metrics_get.sh` (door reachability),
  `benches/m1_wedge_harvest.sh` (durable harvest). Raw output: `/tmp/m1_run/`
  (`m1_report.json`, `ps_*`, `pst_*`, `readyz_*`, `health_*`), `/tmp/m1_metrics/`.
- **Untested / not retrievable under fence:** the five `/api/resources/*` JSON
  bodies (auth-gated) and the live Prometheus series (gate OFF, empty body) —
  substituted with public `/readyz` + durable wedge captures, as documented
  above. No claim is made from those unreachable doors.
- **Caveats:** (1) the sampler's own `python3`/`ps`/`sh` appear transiently in
  boundary ps snapshots but are excluded from the per-thread daemon number;
  (2) the foreign vitest lane inflated W2's machine-wide aggregate only;
  (3) version mix is 19×v0.31.10 + 1×v0.31.22; (4) `db_offload` shows 1 live
  thread vs 6 configured (lazy spawn); (5) agent turns WERE in flight, so the
  "during what" is backed by direct evidence, not inferred from no-turn windows.
