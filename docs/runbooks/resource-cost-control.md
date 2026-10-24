# Resource Cost Control — Observability & Hygiene Runbook

Date: 2026-10-18 · Scope: resource-cost-control (P1–P8: sampler/attribution/anomaly/FE/control seam + O1/O3/O4/O8 + O2 ceremony-gated) ·
Plan: `.agents/shared/planning/resource-cost-control/plan-overview.md`.

---

## 1. Reading `/resources`

The FE page lives at the **`/resources`** route — a **lazy gear-menu entry**
(`frontend/src/app/app.ts` settings-menu `Resources` item; lazy
`loadComponent` route in `frontend/src/app/app.routes.ts`; landed P5). It has two
tabs plus a self-cost chip:

- **Live tab** — one request-time sample of the monitor, polled on a gated 8 s
  interval (skeleton on first fetch, retain-last-data on poll failure, single
  error branch). Shows the daemon block (pid/cpu/rss/threads/fds/uptime), the
  cgroup block, children, instances, the attribution summary, and overruns.
- **Timeline tab** — history as a dependency-free inline SVG with anomaly
  markers and click→window drill-down. Default window is **`1h`**
  (`DEFAULT_TIMELINE_WINDOW`, `timeline-tab.component.ts`); drill-down sends
  **Z-suffixed ISO** timestamps (UTC, `toISOString()` —
  `svg-timeline.component.ts`) to the traceback endpoint.
- **Self-cost chip** — reads `/api/resources/self-cost` (P1).

### The `/api/resources/*` surface (8 endpoints)

**LANDED (P1 — grep-verified against `daemon/routers/resources.py` at this
writing):**

| Endpoint | Notes |
|---|---|
| `GET /api/resources/live` | One live sample: `{ts, sample_level, daemon{pid,cpu_pct,rss_mb,threads,fds,uptime_s}, cgroup{memory{current_mb,peak_mb,anon_mb,file_mb,slab_mb}, cpu{usage_usec,throttled_usec,nr_throttled}}, children[], instances[], attribution{attributed_mb,unattributed_mb,coverage_pct}, overruns{count,last_ts}, self_cost{…}}`. Degrades to the empty shape, never 503. |
| `GET /api/resources/history` | `?window=15m\|1h\|24h\|7d` (default `15m`), `?resolution=auto\|raw\|minute\|hour`. Windows beyond raw retention (48 h) auto-fall to `minute`; the response carries `resolution_used` + `rollup` + `truncated`. |
| `GET /api/resources/self-cost` | The monitor's own cost: exact field set `{cpu_pct, cumulative_cpu_s, buffers_bytes, rss_budget_mb, threads, samples, ticks_skipped, flush:{batches,bytes,errors}, profiling:{active,capabilities}}`. `rss_budget_mb` reflects `RESOURCE_MONITOR_SELF_RSS_BUDGET_MB` (default 50). |

**FORWARD — ships with this release train, NOT yet in the merged tree at this
writing** (written from the plan spec; the router at HEAD carries an explicit
"P3 / P4 / P6 append their endpoints later" note):

| Endpoint | Owning phase | Planned shape |
|---|---|---|
| `GET /api/resources/traceback` | P3 | Traceback window for a time range — the timeline drill-down target. Expects Z-suffixed ISO t0/t1 from the FE. |
| `GET /api/resources/anomalies` | P4 | Anomaly findings: `shape: "spike"\|"ramp"\|"leak"\|"step"`, baseline method+value, observed, deviation metric vs threshold. `?window=` enum `{15m,1h,24h,7d}`, default `1h`. |
| `GET /api/resources/anomalies/{id}/evidence` | P4 | Per-finding evidence window backing the drill-down. |
| `GET /api/resources/control-capabilities` | P6 | Observe-only control-seam stub (capabilities enumeration). |
| `POST /api/resources/profile` | P6 | Opt-in on-demand profiling (`RESOURCE_MONITOR_PROFILING_ENABLED=false` by default; time-boxed). |

---

## 2. Honesty columns — what the numbers can and cannot tell you

Three honesty fields exist so the residual is **taught, not hidden**:

- **`unattributed_kb`** — the part of daemon anon RSS the attribution model
  could NOT charge to a named owner (instance, child process, MCP pool). In the
  storage tier (`resource_daemon_bucket` columns, flusher write set) it is
  carried as `unattributed_kb`; the `/live` summary surfaces the same quantity
  MB-rounded as `attribution.unattributed_mb`.
- **`estimated`** — the row was NOT exactly measured. Per-instance in-process
  CPU is an estimate (Python-object counters, sampling — not per-thread
  accounting); rows carrying `estimated` must be read as approximations.
- **`coverage_pct`** — how much of the anon RSS the attribution model could
  explain at all. Python-object counters typically measure **≈40–80 %** of anon
  RSS (the rest is glibc arenas, shared objects, interpreter overhead that no
  in-process counter sees).

**Why per-instance truth is partial by physics.** All agents share ONE daemon
process. Per-byte, per-instance RAM attribution inside a shared CPython process
is impossible in principle — counters measure Python-object bytes, and in-process
CPU is estimated per instance. MCP server RAM is server-scoped, not per-agent.
There is **no fabricated per-instance truth** in this subsystem by design.

**Interpretation rule (binding).** Act on:

1. a **`coverage_pct` collapse** (the model suddenly explains much less than
   usual — something new and unattributed appeared),
2. **`children` (exact)** — subprocess rows ARE exactly measured (per-pid
   reads), and
3. **anomaly contributors** (§4),

— **never on the attributed number alone**, and never present the attribution
as ground truth.

---

## 3. Knob tuning

All knobs below are **restart-required** (config is read at boot). Knob
names/defaults/bounds are grep-verified against the merged code (citations in
the table).

### 3.1 `RESOURCE_MONITOR_*` (12 knobs — `daemon/config.py`, `ResourceMonitorConfig`)

| Knob | Default | Bounds | What it does |
|---|---|---|---|
| `RESOURCE_MONITOR_ENABLED` | `true` | — | Global kill-switch; `false` disables sampling at boot without a code revert. |
| `RESOURCE_MONITOR_SAMPLE_SECONDS` | `30` | clamp 15–60 | Sampler tick interval (s). |
| `RESOURCE_MONITOR_RETENTION_RAW_MINUTES` | `2880` (48 h) | floor 1440 (24 h) | Raw-tier retention; sweep is floor-gated. |
| `RESOURCE_MONITOR_RETENTION_MINUTE_HOURS` | `336` (14 d) | floor 168 (7 d) | Minute-rollup retention. |
| `RESOURCE_MONITOR_RETENTION_HOUR_DAYS` | `180` | floor 90 | Hour-rollup retention. |
| `RESOURCE_MONITOR_DETAIL_LEVEL` | `auto` | `auto`\|`full`\|`reduced`\|`minimal` | Sampler scan depth; `auto` lets the sampler decide per tick. |
| `RESOURCE_MONITOR_INSTANCE_CAP` | `50` | ≤ 64 | Max per-tick instance rows (top-N + overflow sentinel; §2.4 cardinality budget). |
| `RESOURCE_MONITOR_SELF_CPU_BUDGET_PCT` | `1.0` | 0–100 | EMA self-throttle trigger: tick cost > this % of the interval ⇒ interval doubles + deep-scan off. |
| `RESOURCE_MONITOR_SELF_RSS_BUDGET_MB` | `50` | ≥ 1 | **Runtime alarm/throttle knob** for the sampler's own RSS. DISTINCT from the regression-test `<20 MB` sampler-RSS-delta floor (that is the subsystem's self-cost footprint asserted by `tests/test_resource_monitor_self_cost.py`; this knob is what the live monitor enforces/exposes via `/self-cost`). |
| `RESOURCE_MONITOR_PROFILING_ENABLED` | `false` | — | Opt-in gate for on-demand profiling (LR4: off by default). |
| `RESOURCE_MONITOR_PROFILE_TIMEBOX_S` | `30` | clamp 10–120 | Profiler time-box per session (s). |
| `RESOURCE_MONITOR_FLUSH_BATCH_SECONDS` | `60` | ≥ 1 | Flusher batching window; bounded `executemany` writes ride it. |

Config-layer note: the yaml `resource_monitor:` block is kept free of literal
values — a yaml LITERAL would silently beat and kill the `RESOURCE_MONITOR_*`
env tier (pydantic-settings init-kwarg precedence). Flip knobs via env (or the
db-backed settings tier where registered), then restart.

### 3.2 P7 optimization knobs

| Knob | Default | Bounds | What it does |
|---|---|---|---|
| `LIMITS_INSTANCE_CACHE_TTL_HOURS` | `6` | ≥ 0 | Post-terminal TTL (h) for the in-memory instance-graph cache (O3 — narrowed from 24 h). Non-active graphs older than TTL are released on the periodic sweep; active instances are never evicted. User-visible: instance graphs evict 6 h after terminal instead of 24 h. (`config.py` `LimitsConfig.instance_cache_ttl_hours`.) |
| `LIMITS_INSTANCE_CACHE_MAX_ENTRIES` | `64` | ≥ 1 | LRU cap on the same cache — least-recently-ACCESSed non-active entries evict beyond the cap. |
| `MCP_POOL_IDLE_REAP_SECONDS` | `1800` (30 min) | ≥ 0 (`0` disables) | Reap idle pooled STDIO MCP connections idle beyond this (O4-B; floor-preserving — `McpPoolConfig.default_pool_size=1` floor kept, in-use connections never reaped). Kills the plane MCP retry-storm churn (the computer-use builtin it also covered was removed 2026-10-24). (`config.py` `McpPoolConfig.idle_reap_seconds`; consumed in `daemon/mcp/warmup_pool.py`.) |
| `LIMITS_CHART_RENDER_CONCURRENCY` | `2` | effective clamp [1, 4] | Max concurrent chromium renders daemon-wide (O2). **CEREMONY-GATED: the knob is in the tree but takes LIVE effect only at the user's promote nonce ceremony.** NOTE: earlier plan text spelled it `CHART_RENDER_MAX_CONCURRENCY` — the MERGED name is `LIMITS_CHART_RENDER_CONCURRENCY` (`config.py` `LimitsConfig.chart_render_concurrency`; consumed by the render gate in `daemon/tools/chart_tools.py`). |
| `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES` | lane-conditional | explicit value overrides | Boot-time stale-subprocess sweep (O8a): default **ON only in the prod lane** (frozen binary, `sys.frozen`), OFF in dev/test/manual; the env var overrides both ways. Reaps only children provably orphaned by prior incarnations (parent-dead / start-time predating current boot), each through the PID-recycle guard (starttime field-22 + pgid). Companion knob `ENSEMBLE_BOOT_SWEEP_TERM_GRACE_SECONDS` (TERM→KILL grace). (`daemon/services/boot_subprocess_sweep.py`; wired in `daemon/__main__.py`.) |

### 3.3 `MALLOC_ARENA_MAX=2` operator note (O1)

The arena cap is inserted at all four boot surfaces (grep-verified):
`scripts/systemd/ensemble-daemon.service:106` (static
`Environment=MALLOC_ARENA_MAX=2`), `launcher.sh:1658` (default-only
`": ${MALLOC_ARENA_MAX:=2}"` + export — a staged/`.env` value WINS),
`daemon/__main__.py:48` (module-level `os.environ.setdefault`), plus the
never-raises `malloc_trim(0)` tuner on the existing hourly/idle-gated
maintenance loop (`daemon/services/malloc_tuner.py::trim_malloc_heap`, wired via
`Manager._run_malloc_trim`). The INSTALLED live unit predates O1 — the line
reaches the live host at the next adoption/regeneration, or sooner via the
drop-in in the hygiene checklist item (f).

**O1 measurement protocol (read-only).** Compare daemon RSS via
`GET /api/resources/history` (hour resolution) at **T+24 h** and **T+72 h**
post-promote vs the recorded baseline **1.68–1.72 GB flat**. Expected:
−10~30 % anon. **Decision rule: real reduction < 5 % ⇒ O3 (instance-cache
eviction) becomes the dominant lever** — record the finding, correct the saving
ledger, keep O1 (harmless one-line revert).

---

## 4. Anomaly `shape` glossary

The anomaly layer (P4, forward) classifies findings into four shapes. What each
implies and where to drill:

| Shape | What it implies | Drill-down |
|---|---|---|
| `spike` | A short, sharp excursion above baseline and back — typically a transient (a burst of renders, a checkpoint storm, a GC cliff). | Open `/anomalies` → pick the finding → `/anomalies/{id}/evidence` → the traceback window around the peak. |
| `ramp` | A sustained upward drift over the window — load growth, cache filling toward a cap, or the leading edge of a leak. | Evidence window first; if the ramp continues across windows, correlate with `/history` over `24h` and check the instance-cache/MCP-pool knobs (§3.2). |
| `leak` | Monotonic growth that never returns to baseline — the classic leak signature (the Oct-17 class). | Evidence window + traceback; confirm against `/history` over `7d` (raw retention only holds 48 h — use minute rollup beyond it). |
| `step` | An abrupt level shift that then holds — a config flip, a worker-count change, a new resident child. | Evidence window; check what changed at the step edge (deploys, knobs §3, systemd bounds). |

**Conditioned-baseline caveat (critical).** Baselines are conditioned on
`(n_active_workers, day-of-week, hour-of-day)`. **5 workers ⇒ higher RSS is NOT
an anomaly.** Without the conditioning, every morning GC spike would be a false
positive — read findings against the conditioned baseline, not a global mean.

---

## 5. Known non-issues (verified NO-OP ledger — do NOT re-open)

Recorded verbatim from phase7-plan D11; these sites were investigated and
verified bounded/no-op. Do not re-open them:

- `PYTHONASYNCIODEBUG` (default off);
- `CompletionRegistry` (pop-on-completion + sweeper, bounded);
- the three tiny monotonic dicts (`manager.py:1146`,
  `skill_injection_service.py:225`, `compaction.py:2300` — ≤200 KB total);
- the 21 poll loops (CPU < 1/8 core — the latency cost cannot be tuned away).

---

## 6. systemd memory bounds + swap + tripwire reading (2026-10-18 OOM delta)

**Recommendation (operator-gated — agents never execute against live).** The
unit ran `MemoryMax=infinity` and the box demonstrably died without bounds:
3 kernel OOM deaths the night of Oct 17, daemon killed **23:19:14Z**, unit peak
**8.4 GB**, box at **6.7 GB** at recovery, zero swap. Recommended starting
bounds for `ensemble-main.service`: **`MemoryHigh=7G`** (soft throttle; 1 G
headroom over the 5.3–6.0 G steady cgroup band incl. reclaimable page cache) /
**`MemoryMax=8G`** (hard cap; converts box-death into a unit-scoped kill —
figures are approximate working values, tunable once real headroom is observed
via `/api/resources`). **Exact commands: hygiene checklist item (a)**
(`docs/runbooks/resource-hygiene-operator-checklist.md`).

**Swap = USER DECISION** — zero swap turns any transient spike into a direct
kernel OOM kill (the demonstrated mechanism); a modest swapfile is cheap
pressure-valve insurance, and some operators legitimately prefer strict
no-swap + hard cgroup bounds. The call belongs to the user; if opted in, the
example commands are checklist item (b).

**Reading the tripwire.** `/system.slice` `memory.peak` ≈ **9.6 GB ≈ 97% of
the 9.9 GB box** since the Sep-26 boot. Before Oct 17 this read as a
hypothetical near-full event; after the demonstrated OOM (3 deaths, daemon
killed **23:19:14Z**, unit peak **8.4 GB**, box at **6.7 GB** at recovery) it
is a **DEMONSTRATED kill mechanism**, not a hypothetical. Treat
`memory.peak` approaching **~90 % of MemTotal as an actionable alarm**, not
background noise. Per-unit cross-check:
`systemd-cgtop -m` and
`cat /sys/fs/cgroup/system.slice/ensemble-main.service/memory.peak`.

**Code-side counterpart (LANDED, P7):** **O8** — the boot-time stale-subprocess
sweep (`daemon/services/boot_subprocess_sweep.py`, prod-lane default, env
kill-switch `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES`) reaps the
leaked-across-restarts child class that contributed to the Oct-17 event, and
the read-only KillMode audit
(`docs/investigations/2026-10-18-o8-stale-subprocess-sweep-and-killmode-audit.md`)
verified the unit's stop-phase kill semantics (verdict: **no unit-file change
warranted** — `KillMode=mixed` escalates to a whole-cgroup kill on
stop/restart; the observed survivor class was boot-time orphans, closed by the
sweep, not by KillMode).

---

## 7. Operator hygiene checklist (O7)

The companion document **`docs/runbooks/resource-hygiene-operator-checklist.md`**
carries the exact-command surface for the items this runbook describes: systemd
memory bounds (a), swap (b, USER DECISION), the pattern-gated test-database
cleanup (c), the orphaned demo context7-mcp kill (d), the demo-daemon keep/kill
(e, USER DECISION), the `MALLOC_ARENA_MAX` on the installed unit (f), and the
O1 verification protocol (g).

**Operator usage rules (binding):**

- **Run the DRY-RUN FIRST** — the checklist's destructive items have a mandatory
  list-what-matches ordered step before any drop/kill; never skip it, never
  paste the dry-run and the destructive step as one block.
- **Respect the pattern + age gates** — the test-DB cleanup matches ONLY the
  four prefixes (`ensemble_blob_prune_*` / `check_score_*` / `debug_*` /
  `repro_*`) and only databases older than 7 days.
- **`USER DECISION` items are the user's alone** — swap provisioning and the
  demo-daemon keep/kill are framed there, never forced.
- **Agents never execute any of it against live** — the checklist is executed
  by the human operator ONLY (LR5); this commission performed zero live
  mutations.
