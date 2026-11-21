# DISCUSSION — multi-core CPU optimization for agents-ensemble (CPU throughput arc 2)

**Emperor directive (2026-11-20):** *"Any multiple core cpu optimization we can do now. Please investigate, we discuss."*

**This document is the synthesis the Emperor reads.** It merges three in-tree evidence bases and
adds **no new measurement**:

| Source | What it contributes | Where |
|---|---|---|
| **M1** — live CPU picture (captured 2026-11-21 03:39–03:49 UTC against the live v0.31.22 daemon, read-only) | how many cores are used, by which thread, while doing what | `.agents/shared/planning/cpu-throughput-arc2/M1-live-cpu-picture.md` |
| **M2** — GIL reality, frozen-MP hard gate, hot kernels (worktree venv + frozen-binary audit, no live contact) | *what can physically scale*, and what a process pool costs in this frozen build | `.agents/shared/planning/cpu-throughput-arc2/M2-gil-frozen-mp-kernels.md` |
| **Audit** — off-loop audit v0.31.10 (analysis-only, two read-only lanes) | the structural census of on-loop work + the ranked table whose **remaining** rows we fold here | `/home/nea/ensemble-src/.agents/shared/planning/sonic-team/off-loop-audit/HANDOFF.md` (+ `EVIDENCE.md`, `CODE-CENSUS.md`) |

Anchors re-checked at this lane's base **`bca2e046b`** (v0.31.22 + M1 + M2 merged); every line
number below is at that base. Audit cites are v0.31.10 and drift (one anchor moved **9539 → 9559**
inside `graph.py`); where I re-derived a count at this base I say so and give the number.

**Every claim carries its source.** `[M1 Qn]` / `[M2 §x]` / `[audit rN]` / `[src file:line]` /
`[mine]` = my own re-grep at `bca2e046b`. Where I extrapolate, the sentence starts with
**BOUNDED-ESTIMATE**.

---

## 1. HEADLINE ANSWERS

> **THE ANSWER IN ONE PARAGRAPH.** There is no large multi-core win available right now, and the
> measurements say why: the daemon uses **~0.6–0.75 of one core** steadily and **1.43 cores at peak**
> on a 16-vCPU box while **5–7 agent turns are in flight** (M1). The GIL is **standard** in both the
> venv and the shipped frozen binary, so threads cannot scale pure-Python CPU (**0.449×** with 4
> threads) — only processes can — and the frozen build's process pool needs a 4-line entry fix before
> it works at all, which M2 proved (and which we do **not** need yet, because CPU-bound demand is
> under one core). What *is* available is **overhead removal and de-contention**: a 200 ms full-`/proc`
> ram-guard scan eating ~17 % of the daemon's own core, a 4.3×-slower JSON codec that is already
> installed, a log f-string built before its level check (32 captured episodes, 46 s of blocked loop
> time), and one default asyncio executor shared by ~1.2k call sites of which **25 hold a worker for
> a whole LLM round-trip (watchover up to 90 s)**. Those four are §3's top rows and they are
> S-effort or less.

### 1.1 How many cores does the daemon actually use, and during what?

**It runs at ~0.6–0.75 of ONE core in steady state and touches ~1.4 cores at its peak. Fifteen of
the sixteen vCPUs are idle *with respect to this daemon*.** The machine-wide figure is higher only
because a foreign lane (a `vitest`/`tsc` frontend run) shared the box during window 2.

| Measurement | Value | Source |
|---|---|---|
| Daemon's own CPU (sum of per-thread `utime+stime` deltas, PID 1208513) | **63.3 % / 75.4 % / 71.4 % of one core** (W0 60 s / W1 60 s / W2 300 s) | [M1 Q1] |
| Peak observed sustained CPU (durable wedge captures, 97.07–143.05 %) | **1.43 cores max**, never more | [M1 Q1] |
| Machine-wide aggregate (`/proc/stat` jiffie deltas) | 6.05 / 13.02 / 16.72 % × 16 = **0.97 / 2.08 / 2.68 cores** — the excess over ~1 core is the foreign vitest lane (started mid-W2, gone by W2_b) | [M1 Q2] |
| Per-core spread | **no core pinned**: every core 13–22 % in W2; the loop spread it by OS scheduling | [M1 Q2] |
| Work in flight during measurement | `inflight_turns` 5 → 5 → 5 → 6 → 6 → **7**; the key capture's `db.json` shows `executing_turns_count=6` (2 explorer + 4 worker, all `process_message`) | [M1 Q1, Q5] |
| DB pools during measurement | checkpoint saver psycopg `{size 1, max 15, available 1, waiting 0}`, SQLAlchemy `{checkedout 0–1, size 15}` — **no pool contention** | [M1 Q4] |
| db_offload | **1 materialized thread of a 6 cap** (`db-offload_0`); memoir poignancy / reflection / plane_sync / sources all ~0 (dormant or lazy) | [M1 Q3] |

**"During what"** — the honest answer from thread stacks, not inference: *(a)* sync LLM streaming on
shared-pool threads, *(b)* checkpoint writes on the **main** thread, *(c)* a constant ram-guard
`/proc` scan at 200 ms, *(d)* wedge-capture + metrics sampling. It is not a benchmark and not idle
spin. [M1 Q1, Q3]

**Where the cycle actually goes** (share of one core, the three windows):

| Thread | W0 | W1 | W2 | Stack family | Source |
|---|---|---|---|---|---|
| `MainThread` (daemon) | 17.6 % | 17.7 % | **23.2 %** | `langgraph/checkpoint/postgres/aio.py:280 aput` → `daemon/checkpoint_adapter.py:838` | [M1 Q3] |
| `ram-guard-tick` | **16.9 %** | 15.7 % | **18.4 %** | `daemon/services/ram_guard/tree_reader.py:261 read_stat_entry` — a `read_text` of `/proc/<pid>/stat` for **every pid, every 200 ms** (`guard.py:180 poll_interval_ms=200`; `scan_proc` at `tree_reader.py:317` enumerates `os.listdir(/proc)` then reads each pid) | [M1 Q3; mine: `tree_reader.py:300-350`, `guard.py:486-493`] |
| `ThreadPoolExecutor-3_*` (shared default pool) | 4.4/2.6 % | **9.4/7.8 %** | 2.3/2.3 % | `llm_stream_watchdog.py:246 __iter__` → openai/httpx SSE → `graph.py _run_standard_with_retry` | [M1 Q3] |
| `WedgeCaptureWatchdog` | 3.5 % | 4.7 % | 4.0 % | wedge collector | [M1 Q3] |
| `metrics-trend-sampler` | 1.95 % | 2.05 % | 1.78 % | metrics sampler | [M1 Q3] |
| `ram-guard-lane` | 1.12 % | 1.20 % | 1.20 % | kill lane (idle) | [M1 Q3] |

Corpus-wide (20 durable captures): the hot thread is the LLM-stream pool thread in **14/20**,
`MainThread` (checkpoint write) in **5/20**, `ram-guard-tick` in **1/20**. [M1 Q5]

> **Read this table as the load-bearing fact of the whole arc:** measured CPU-bound *Python* work is
> **< 1 core**. The daemon is not short of cores; it is short of *non-blocking* work. Two of the three
> hottest consumers (checkpoint write on the loop, ram-guard full-`/proc` scan) are **recurring fixed
> costs**, and one (LLM SSE) is I/O wait that merely *occupies* threads.

### 1.2 GIL consequence map — what can actually scale, by work class

**The build is standard-GIL in both classes. There is no free-threading option on this release line.**

| Probe | Result | Source |
|---|---|---|
| Worktree venv interpreter 3.14.7: `Py_GIL_DISABLED` / `sys._is_gil_enabled()` / `sys.abiflags` | `0` / `True` / `""` (free-threaded builds report `t`) | [M2 A1] |
| CPU-bound 1 thread vs 4 threads (pure Python) | 1.381 s → 3.077 s = **0.449× (slower)** | [M2 A1] |
| Shipped frozen binary: CArchive runtime lib | **`libpython3.14.so.1.0`** — no `t` ⇒ not free-threaded; 4 771 PYZ modules, pyc magic `2b0e0d0a` == venv | [M2 A2] |

Consequences, work class by work class:

| Work class | Can threads help? | Evidence / number | Verdict |
|---|---|---|---|
| **I/O-bound (DB wait, HTTP/LLM wait, file, subprocess)** | **Yes** — the GIL is released across the wait | Structural: the GIL is not held during blocking I/O | threads fine; this is why `to_thread`/`db_offload` are the house pattern |
| **Pure-Python CPU-bound** (pydantic schema build, deep-size walks, chunk loops, `re` scans) | **No** — moves the stall, adds contention | 4 threads = **0.449×** [M2 A1]; `re` UUID scan 0.906× [M2 C3] | **only processes** (and see §4 — mostly don't) |
| **C-extension that releases the GIL — tiktoken BPE core** | **Yes, conditionally** | 4.960 → 1.577 s = **3.145×** on a pathological `a`-run; 3.192× end-to-end on that shape | scales on BPE-bound pathological shapes only |
| **C-extension that releases the GIL — tiktoken *regex pre-tokenizer*** | **No — actively harmful** | 0.531 → 7.043 s with 4 threads = **0.075× (13× SLOWER)** on normal prose; the pre-tokenizer thrashes the GIL on many small matches | **never thread normal-shape token counting** |
| **hashlib sha256** | Yes | 512 MB 1.715 s (298 MB/s); 4×128 MB = **2.80×** threaded | already GIL-releasing; nothing to do |
| **pydantic `model_validate`** | Not a bottleneck | 100 000 dicts → 0.345 s (≈290 k models/s) | skip |
| **stdlib `json`** | n/a — but a faster codec exists | see next row | — |
| **orjson** | n/a (single-call C) | **4.3× dumps**, **1.5× loads** on 28.6 MB / 30 000 items; **already installed transitively** (`langgraph-checkpoint-postgres`, `langgraph-sdk`, `langsmith`) and **already bundled in the shipped PYZ** | a *latency* win, not a parallelism win |
| **ormsgpack** (checkpoint blobs) | No | GIL-held per the audit's prior house evidence | **do not offload the decode**; algorithmic only [audit rX] |

**The three-line summary of the GIL:** *threads buy you I/O concurrency and nothing else; the only
CPU-parallel lever is a process pool; and the only GIL-releasing hot kernel we own (tiktoken BPE) is
usable only on pathological shapes whose pre-tokenizer is the part that thrashes.*

### 1.3 The frozen-multiprocessing HARD GATE — does a process pool even work here?

**Yes — but only with a 4-line entry change, and today it buys almost nothing.** The gate result:

| Spike binary | ctx | rc | `main` entries | worker tasks | `Pool(2)` | `ProcessPoolExecutor` | 50×64 KB pickling |
|---|---|---|---|---|---|---|---|
| **no `freeze_support`** | **spawn** | **124 (hang)** | **2 798** | **0** | never ran | never ran | never ran |
| no `freeze_support` | fork | 0 | 1 | 16 | 0.074 s | 0.021 s | 0.017 s |
| **with `freeze_support`** | **spawn** | **0** | **1** | **16** | **0.289 s** | **0.305 s** | **0.245 s** |
| with `freeze_support` | fork | 0 | 1 | 16 | 0.047 s | 0.020 s | 0.012 s |
| unfrozen venv baseline | spawn | 0 | 1 | 16 | 0.309 s | 0.281 s | 0.200 s |

[M2 §B]

Mechanism: `multiprocessing/spawn.py:83-95 get_command_line()` **special-cases `sys.frozen`** to
launch children with `argv == [exe, '--multiprocessing-fork', …]`; `freeze_support()` is the ONLY
thing that consumes that marker (`spawn.py:61 is_forking` → `spawn.py:67-75 spawn_main(...)`).
Without it the frozen bootloader simply **re-runs the entry script** — measured as 2 798 re-entries
in 45 s (the spike's own re-entry guard turned an unbounded fork bomb into a hang). [M2 §B]

Three facts that matter for a real daemon:

1. **`grep -rn freeze_support run_app.py daemon/ ensemble.spec` → rc=1, zero hits** — re-verified at
   `bca2e046b` [mine; M2 A2]. The entry is `run_app.py` (`ensemble.spec:5`), onefile, `console=True`.
2. **`multiprocessing` is fully bundled** (23 modules including `spawn`, `popen_spawn_posix`, `pool`,
   `resource_tracker`; `concurrent.futures.process` too) ⇒ **zero spec changes**; the missing piece is
   the entry-point bootstrap only. [M2 A2]
3. **Never enable without the entry fix.** In the real app each boot-loop child re-runs the `.env`
   block and `daemon.__main__._boot_db_preflight()` (`run_app.py` tail) — i.e. **DB preflight against
   the live database, thousands of times**. The DB-free spike understates the blast radius by a lot.
   [M2 §B] + [mine: `run_app.py`]

Cost model if enabled correctly: **spawn child boot ≈ 0.29 s** (vs 0.05 s fork), **≈ 0.25 s per
50 × 64 KB pickling roundtrip**, and each child imports from the PYZ (must be importable/picklable).
`fork` also works in the spike but is **unsafe in a multi-threaded process** (the daemon runs 60–69
threads) and **fork-in-multithreaded-frozen is UNTESTED** — treat fork as unavailable. [M2 §B, A2]

**Consequence for the topology:** a process pool is *permitted* (after 4 lines) but *not justified
today* — see §2.4 and §4.

---

## 2. TOPOLOGY PROPOSAL (sized for 16 vCPU)

### 2.1 What today shares the ONE default asyncio executor

`asyncio.to_thread` submits to the loop's **default** `ThreadPoolExecutor` — `min(32, cpu+4)` = **20
workers** on this host (computed; **runtime-unverified**, [audit §1b]) — and the daemon **never**
calls `asyncio.set_default_executor` (**zero hits at this base**, [mine]).

| Population on the default pool | Count at `bca2e046b` | Source |
|---|---|---|
| `asyncio.to_thread(` call lines | **1 209** raw `grep -rn` hits under `daemon/` (1 of them in `daemon/_archived/`, 0 under test dirs) → **1 208** real, → **1 181** after dropping pure comment / backtick-docstring lines. Audit counted **1 202** at v0.31.10; drift is expected across nine releases, and neither count is a distinct-call-site count | [mine; audit §1b] |
| `run_in_executor(` non-test lines | **28 lines → 25 real sites** at this base: **6 pass `None` (shared default)** — `graph.py:3282, 9801, 9859, 10266, 10295` + `manager.py:5085` — and **19 pass a dedicated executor**. Note the audit classified `mcp/connection_manager.py:215/496`, `sources/registry.py:601` and `tools/bash.py:1612` as SHARED at v0.31.10; at `bca2e046b` all four pass a named pool (`SECRET_RESOLVE_EXECUTOR`, `_executor`, `_DURABLE_INSERT_EXECUTOR`) — those sites were re-pointed since | [mine; audit §1c] |
| **LLM dispatches that HOLD a default-pool worker for the whole round-trip** | **25 code sites** (a 4-line-window scan returned 26 candidate lines; 1 — `graph.py:2024` — is a docstring mention): `graph.py:2078, 3282, 9801, 9859, 10266, 10295, 12497, 12624` (8); `compaction.py:4802`; `child_reports.py:1241, 1910`; `title_generation.py:145`; `room_naming.py:180`; `keyword_extraction.py:406`; `attestation_report_judge.py:411`; `watcher_context_builder.py:268`; `skill_evolution_service.py:2765`; `skill_search_service.py:1573, 1590`; `snapshot_search_service.py:862, 866`; `skill_embedding_service.py:368, 485`; `snapshot_embedding_service.py:445, 500` | [mine; audit §1b identifies the family] |
| Longest hold | **watchover check = `WATCHOVER_TIMEOUT_SECONDS_DEFAULT = 90 s`** (floor-clamped to ≥ 15 s), and it runs **once per tool call in a loop** (`graph.py:12461 for tc in tool_calls`). Scope honestly: it is **opt-in per instance** — the global kill-switch is `WATCHOVER_ENABLED` (default `true`, `graph.py:1251`) and the per-instance flag lives in `instance_metadata["watchover_enabled"]` (`manager.py:6961-6988`), so only *watched* instances pay. When they do, each check holds a default-pool worker for up to 90 s | [mine: `graph.py:11417`, `12009`-`12035`, `12461`, `12497`, `1251`; `manager.py:6961`] |
| Concurrency ceiling on turns | `limits.llm_concurrency = 10` semaphore around `graph.astream` (`manager.py:830`, `instance_messaging.py:5696`) | [mine] |

**The R-3 mechanism, quantified at this base:** 20 default-pool workers vs **up to 10 concurrent
main-turn LLM streams** (`llm_concurrency`) + **per-tool-call watchover checks of up to 90 s each on
any watched instance** + side lanes (title, room naming, keyword extraction, attestation judge,
watcher-context build, embeddings) ⇒ the pool can be **fully occupied by network waits**, and then
*every* DB `to_thread` bridge in the daemon (1 181 sites) queues behind them. Honest bound: the main
turn contributes at most 10 holders, so **the 20-worker pool is not saturated by turns alone** —
saturation requires the watchover + side lanes on top, and watchover is opt-in per instance. That is
why the audit's row 4 is *structural with a correlated sentinel* (5.4–8.1 s `aget` timeouts that the
audit declines to attribute causally) rather than a proven ceiling. What M1 does prove is the
*traffic*: the top hot thread in **14/20** captures is a shared-pool LLM-stream thread. [audit r4;
M1 Q5]

> **Two numbers in the brief vs this doc — reconciled, not quietly changed.** The commission brief
> cites "~202 `to_thread` sites" and "4 LLM-invoke sites". At `bca2e046b` the honest counts are:
> **202 is the audit's count of unbridged sync-DB *sites* (row 3's family), not the `to_thread`
> population** — that population is **1 181 non-comment lines** (raw: 1 209 hits, 1 archived; audit:
> 1 202 at v0.31.10). And the LLM-dispatch population on the default pool is **8 sites in
> `graph.py`** (`:2078` primary, `:3282` repair re-invoke, `:9801`/`:9859` post-repair, `:10266`/
> `:10295` post-compaction, `:12497` watchover, `:12624` summary) **plus 17 in services** = **25
> total** — the audit's "4" named only the primary-dispatch family, and all of its cited lines have
> drifted at this base (e.g. its `:12574` is now `:12624`; `:12574` today is a closing paren). The
> R-3 conclusion is unaffected — it is *worse*, not better, than the brief's framing. [mine]

### 2.2 Existing dedicated executors — KEEP AS IS

| Executor | Size | Gate / purpose | Source |
|---|---|---|---|
| `db_offload` | **6** (`DB_OFFLOAD_MAX_WORKERS_DEFAULT=6`, admission-gated, backpressure alarm) | sync DB off the loop | [M1 Q3 mapping table; `constants.py:1817`, `db_offload.py:342`] |
| memoir poignancy | 2 | LLM poignancy lane | `memoir_poignancy_worker.py:138` |
| memoir reflection | 1 | reflection lane | `memoir_reflection_worker.py:70` |
| memoir embedding | 2 (sequential embeds by design) | **explicitly never the shared pool** | `memoir_embedding_worker.py:213-217` |
| plane_sync | 2 (rate-limit cap) | fire-and-forget sync | `plane_sync_service.py:254` |
| sources | 4 | source callbacks | `sources/registry.py:50` |
| mcp proc-scan / mcp secret-resolve | 2 / 2 | named, dedicated | `mcp/stdio_wrapper.py:130`, `mcp/warmup_pool.py:116` |
| bash durable-insert | 2 | durable stamp | `tools/bash.py:155` |
| question-pause merge | 1 | RMW serialization | `question_pause_set.py:137` |

M1 confirms these are **cold at measurement time** (1 materialized `db-offload_0`, 1
`memoir-poignancy_0`, etc.) — they cost nothing idle, because `ThreadPoolExecutor` spawns lazily.
[M1 Q3]

### 2.3 Worker pool interaction (important, and counter-intuitive)

The job worker pool is **threads that hop onto the main loop**, not parallel executors:
`TaskProcessor.run_task()` builds a coroutine and calls `MainLoopBridge.run_async()` →
`asyncio.run_coroutine_threadsafe(coro, loop)` (`task_processor.py:1875-1880`,
`main_loop_bridge.py:83`). Therefore:

- **`limits.worker_pool_size` (default 5) is a CONCURRENCY knob, not a CPU-thread knob.** Raising it
  does not add cores of capacity; it adds **more simultaneous turns**, i.e. **more on-loop
  checkpoint writes, more token counting, more schema/tool work per second** on the *same* single
  loop. Given §1.1's finding (the loop is the constraint, not the cores), **raising worker slots
  currently trades throughput for loop lag**.
- The real concurrency ceiling for LLM work is the `llm_concurrency` semaphore (10), which is what
  caps simultaneous default-pool occupations.
- BOUNDED-ESTIMATE: with 5 + 2 worker threads and 5–7 turns in flight (M1's own observation), the
  pool is not the binding constraint *today*; it becomes one only under a burst larger than the
  shared-default-pool headroom (§2.1).

### 2.4 Proposed per-class topology

```
                    ┌──────────────────────────────────────────────────────────────┐
                    │  MAIN EVENT LOOP (1 thread) — must own NO CPU work > ~1 ms   │
                    └───────┬──────────────────────────────────────────────────────┘
   ┌────────────────────────┼─────────────────────────┬──────────────────────────┐
   │                        │                         │                          │
 [A] DB pool           [B] NETWORK/LLM pool      [C] SHORT-IO pool        [D] (later) PROCESS pool
 db_offload, 6→8-10    NEW, 24-32 workers        NEW, 4 workers           bounded, ≤ 8, spawn
 sync DB only          LLM/watchover/embed       file reads, subprocess,  ON-DEMAND, coarse jobs
 (PG manager pool 15)  (pure wait, GIL free)     git, small hashing       GIL-bound residuals only
```

| Pool | Size | Why this size | Evidence |
|---|---|---|---|
| **A. DB** (`db_offload`, existing) | **6 → 8–10** | PG manager pool is 15 (`readyz` `size 15`), checkpoint pool max 15; 8–10 leaves headroom; today only **1** thread materializes | [M1 Q4; audit r4] |
| **B. Network/LLM** (new, dedicated) | **24–32** | **25** identified LLM-dispatch sites (§2.1). Sized from the *observed* occupancy, not a formal bound: M1 saw **5–7 turns in flight** against a `llm_concurrency` ceiling of **10** [M1 Q1, mine], plus watchover (one holder per tool call on any *watched* instance, ≤90 s each) plus the side lanes. BOUNDED-ESTIMATE, and deliberately generous: each hold is a pure wait (GIL released), so over-provisioning a waiting pool is cheap, while under-provisioning restores the R-3 queue. If the new pool never exceeds ~60 % utilization under the post-L1 load picture, shrink it — do not add a pool you cannot observe (hence the metrics requirement in the same row) | [M1 Q1; mine §2.1; audit r4] |
| **C. Short-IO** (new, small) | **4** | file/subprocess helpers currently mixed into the default pool (`git_diff_service.py:87` 1 MiB `read_text` unbridged; `routers/agents.py:131/150` `open()` + `rmtree`; `checkpoint_prune`/`maintenance` `engine.dispose()`); keeps DB+LLM pools unpolluted by slow FS | [audit r3, r10; mine] |
| **D. Process pool** | ≤ 8, spawn-on-demand, **coarse jobs only** | Two hard constraints: **`freeze_support()` at the first entry line** (mandatory for spawn) and **pickling ≥ 0.25 s per 50×64 KB**, so per-request chatter is out; checkpoint blobs must NEVER cross it (ormsgpack is GIL-held, and blobs are ~42.6 GB table-wide / 2 758 rows — [M1 Q5]) | [M2 §B, §A2; audit r1] |

**Honest gate on D:** it *works* (M2 measured it) and it *costs* ~4 lines + ~0.29 s per child boot,
but **it buys almost nothing today** because measured CPU-bound Python mass is **< 1 core** (§1.1).
It becomes worth building when (a) algorithmic/caching fixes have removed the wasted work, and
(b) a residual GIL-bound unit is shown to exceed ~0.2 core sustained. Ranked **low priority** (§3).

---

## 3. ONE RANKED TABLE — everything, by measured impact per effort

Effort scale: **XS < 2 h · S 2–4 h · M 6–12 h · L 12 h+** (hours given per row).
"Measured" = M1 capture/wedge evidence or M2 bench; "structural" = audit census, no current-capture
weight; "BOUNDED-ESTIMATE" = my extrapolation, explicitly flagged.

**Rows 1 / 2 / 7 of the audit table are ALREADY LANDED in arc 1** and are excluded from the ranking
(recorded for continuity): row 1 tiktoken (de-dup + shape-aware chunked counting) → **v0.31.16**;
row 2 pydantic schema construction (registry + boot warm) → **v0.31.16**, factory-local input models
hoisted → **v0.31.17**; row 7 `/api/instances` payload → **v0.31.12** (Option A: drop
`mcp_tool_names`) and **v0.31.13** (Option D: prefs off-load + no re-validation). [audit r1, r2, r7;
`CHANGELOG.md` v0.31.12–.17]

**Correction to that exclusion — Option F (response gzip) did NOT land.** The audit's row 7 named
`api.py:5977` as "the gzip slot"; that line is the **CORS** middleware registration, and there is
**no `GZipMiddleware` anywhere in `daemon/`** at this base [mine: `grep -rn GZipMiddleware daemon/`;
`api.py:5977-6006` read]. So response compression on the list routes is still **available work**, and
it is folded into the ranking below as row **2b** rather than assumed done.

**One premise correction found while verifying:** audit **row 5** (Option E badge leg) is *also*
largely landed — **v0.31.15** bounded the badge-leg fetch (`TIE_PAD=8` pushdown,
`work_resolver.py:91, 1453-1456`), batched the per-row N+1s (`_get_queue_positions`,
`get_dlq_by_job_ids`, [mine: `jobs_crud.py:943, 956`]), and moved the three watch reads off the loop
(`await asyncio.to_thread(self._watcher_repo.get_active_watches)`, `job_queue_service.py:556/711/844`)
onto a distinctly-named reader (`watcher_repository.py:482`). **Caveat, stated plainly:** that reader
is a distinct *name* but still a **full-table `select(JobWatcher)` with no LIMIT and no filter** —
its own docstring says so ("the same (currently unfiltered) read") [mine]. So the *loop* is
protected; the *read* is not bounded. Row 5 is therefore **verify-and-close (XS)** for the loop
question, with a small residual (bound the watch read, trim/encode the response) if the Emperor
wants the payload leg too.

| # | What | Measured-or-bounded impact | Effort (h) | Risk | Dependencies |
|---|---|---|---|---|---|
| **1** | **ram-guard tick cost** — `scan_proc` does `os.listdir(/proc)` + one `read_text` per pid **every 200 ms** (`guard.py:180,486-493`; `tree_reader.py:300-350`). Fix family: cache stat reads keyed on `starttime`+`mtime`, or back the poll off when the tracked set is unchanged, or gate the scan on a cheap `/proc/stat`-level signal first. **Do NOT** lengthen the confirm deadline (that is the kill decision's safety margin). | **MEASURED, the largest single steady consumer after checkpoint**: `ram-guard-tick` = 16.9 % / 15.7 % / 18.4 % of one core [M1 Q3] ⇒ **~0.17 core of a 0.6–0.75-core total (~25 % of daemon CPU)**, continuously, and the scan cost scales with total host pids (256–270 observed across two checks in this lane, [mine: an `ls /proc` count filtered to numeric entries]) — i.e. with *foreign* process churn, not with this daemon's own work. Payoff is pure overhead removal — it changes no decision. | **S (2–4 h)** | **Medium** — this is a *safety* surface (the RAM guard kills wedges). Any cadence change must be pinned by the existing ram-guard test surface; a missed sample must remain fail-closed. | none; independent of every other row |
| **2** | **orjson swap for large (de)serialization** — replace `json.dumps/loads` on the *big* payloads only (236 dumps / 102 loads exist repo-wide [mine]; target the envelope/metrics/checkpoint-adjacent and any ≥100 KB path, not all 338). `orjson` is already transitive **and already in the shipped PYZ** ⇒ zero new dependency, zero spec change. | **MEASURED on the kernel**: **4.3× dumps**, **1.5× loads** on 28.6 MB / 30 000 items [M2 C2]. End-to-end is **BOUNDED-ESTIMATE small** — no single measured site owns > a few % of the 0.6–0.75-core total; this is a latency/p99 win, not a throughput win. | **S (2–4 h)** | **Low–Medium** — semantics differ and **I measured the three that matter** at this base's venv (orjson 3.11.7): (1) `default=` **does** exist — M2's phrase "no custom `default=` hooks" reads as a capability gap, but the gap is in the *call sites* (they rely on stdlib's implicit coercions), not in orjson: `orjson.dumps({'d': date(...)}, default=str)` returns `b'{"d":"2026-01-01"}'`, (2) **NaN/Infinity are emitted as `null`** where stdlib emits `NaN` — a real behaviour change on any float-bearing payload, (3) **non-`str` dict keys raise `TypeError`** (stdlib coerces). Every swapped site needs a value-parity test; keep stdlib where floats-with-NaN or int-keyed dicts can appear. `OPT_SORT_KEYS`/`OPT_INDENT_2` exist, so the `sort_keys=True` sites used for fingerprints are reachable. (Probed in the M2 worktree venv, orjson 3.11.7 — the same version the frozen binary bundles.) [mine] | independent; good companion to #1 (same "cheap overhead" lane) |
| **2b** | **Response compression on the big list routes** (audit r7 Option F, NOT landed — see the correction above). `starlette.middleware.gzip.GZipMiddleware` at app construction, `minimum_size` tuned so small JSON is untouched. | **BOUNDED-ESTIMATE**: the prior lane measured `GET /api/instances` at **6.06 MB/poll @10 s** before Option A; after Option A's ~57 % projection it is ~2.6 MB [audit r7, CHANGELOG v0.31.12]. Compression moves bytes, not CPU — its CPU win is *fewer bytes to encode/parse on both ends*, and it is the only remaining lever on that route after A+D landed. | **XS (1–2 h)** | **Low** — mechanical, but it changes the wire contract for every client (FE must accept `Content-Encoding`); pin the wire-shape packs. | independent |
| **3** | **R-3 executor split** — dedicated **network/LLM pool** (24–32) for the **25** LLM-holding sites; grow `db_offload` 6 → 8–10; optional 4-worker short-IO pool; extend the metrics vocabulary so each pool has occupancy series (today only `manager`/`checkpoint` [audit r4]). | **MEASURED attribution + structural**: LLM-stream pool thread is the **#1 hot thread in 14/20 captures** [M1 Q5]; 25 holding sites verified at this base [mine]; the 20-worker default pool is shared by 1 181 `to_thread` sites [mine]. Ceiling mechanism, stated with its own limits (§2.1): 10 main turns × 30–300 s cannot alone fill 20 workers, so saturation needs watchover (≤90 s per tool call, opt-in per instance) **plus** the side lanes; the audit itself calls this correlated, not proven. Impact on **time-to-readyz / lag tails** is BOUNDED-ESTIMATE. | **M (6–12 h)** | **Medium** — executor sprawl; per-pool admission needs the same discipline `db_offload` already has (gate + backpressure alarm + drain). Land **after** #1/#2 so the post-fix load picture is the one you size against. | needs #1/#2 landed first (or at least concurrent) to size correctly |
| **4** | **Hot bridges, sliced (audit r3)** — 202 unbridged sync-DB sites; highest-value slices: `tools/job_queue.py` watcher calls (32 in v0.31.10; **12 `count_watches_for_instance`/`add_watch`/`remove_watch` sites re-verified still unbridged** at `bca2e046b` — `:1601, 1606, 2695, 2711, 2795, 2852, 2870, 2986, 3036, 3067, 4375, 4441`, none of them on a `to_thread` line nor within 4 lines of one [mine]), `child_reports.py` completion path (the two `_should_send_completion_report`/`_update_parent_on_child_complete` bodies are **no longer called from the live path** — the live path runs the whole `WriteGuardSession` block through `asyncio.to_thread(self._process_child_completion_db_sync, …)` at `child_reports.py:3465/3472` and inlines the idempotency checks there, [mine]), `manager._reconcile_deferred_report_async` (audit: a 19-statement write tx; at this base the function spans `manager.py:13530-14023` and its ~494-line body contains exactly **one** `to_thread` — `:13683` — so the sync transaction work is still on the loop [mine]), router prefs/skills-trigger CRUD (`routers/skills.py:661/692/724/761` unbridged [mine]), `tools/knowledge_tools.py:851/901` (`explore()`/`experience()` repo round-trip on the loop [mine]). | **Structural** — **zero v0.31.22-capture weight**: the audit's 2 sync-psycopg captures were 1× v0.31.1 + 1× v0.31.2 [audit r3], and M1's corpus-wide top-1 attribution is LLM-SSE **14/20**, `MainThread` in checkpoint `aput` **5/20**, ram-guard **1/20** — no capture's hottest thread is a sync-psycopg loop stack [M1 Q5]. Cost is loop occupancy, not DB throughput (pools idle: `waiting 0` [M1 Q4]). | **M (6–12 h)** hot slices; **L** for the whole family | **Medium** — `WriteGuardSession`/transaction boundaries across an offload hop (precedents exist: `instance_lifecycle.py:2943`, `child_reports.py:3465`); one pack per slice. | independent surfaces (tools/services/routers); can run parallel to #3 |
| **5** | **`logging.debug(f"…")` level guard** (audit r10) — the f-string is built **before** the level check. Verified live at this base: `graph.py:9559` (audit's `:9539` — anchor drifted 20 lines) has **no `isEnabledFor` guard**, and the repo has zero `isEnabledFor` uses in `graph.py` [mine]. | **MEASURED**: 1/20 captures, **32 episodes / 46 s blocked**, all on v0.31.10 [audit EVIDENCE r5]. Cheapest measurable win on the board. | **XS (0.5–1 h)** | **Very low** — mechanical; behaviour identical. | none; rides with any lane |
| **6** | **R-2 BFS batching** (audit r6) — `repository.get_tree_ids_permanent` (`:844-908`) issues **1 SELECT per frontier node per level** with a pinned session; the batched shape already exists 780 lines away (`:1616-1662`, one `IN` per level) [mine: read both]. Also batch the per-node `repo.get` loop in `instance_lifecycle.py:3245` (inside `terminate_instance`, 2638) [mine]. | **Structural** — no capture weight; worst case O(nodes) round-trips with a pinned session; currently reached on-loop via subtree authz + cascade pause/resume/hard-delete. | **S–M (3–8 h)** | **Low–Medium** — traversal-semantics parity (permanent `parent_id` lineage vs transient `instance_hierarchy`); the existing 256-depth cap + visited-set must survive. | rides with #4's repository slice or standalone |
| **7** | **Periodic ~20 s stall — diagnostic only** (audit r9) — correlate fire instants against sweep cadences. **New, cheap leads at this base:** six unrelated services default to **300 s** intervals (`job_feedback_observer.py:386` health check, `mission_strand_healer.py:175`, `report_delivery_recovery.py:442`, `stale_pause_sweep.py:129`, `watch_reconcile_sweep.py:88`, memoir latch ticks (`memoir_poignancy_worker.py:164`, `memoir_embedding_worker.py:170`)) — a superset of candidates for a 300–360 s spacing [mine]. | **Journal-derived**: 31 consecutive fire pairs at 300–360 s (median 315), 23/31 in the 20.00–21.63 s band (Oct 26→31) [audit r9]. **Regime check (M1):** the current corpus is 18 `sustained_cpu` + 2 `sustained_loop_lag` over ~18 h [M1 Q5] — *a different, quieter shape* ⇒ this may be an already-mitigated past regime. | **S–M (3–6 h)** | **Low** — read-only diagnostic; risk is wasted effort if the regime is gone (the six-lead list above makes it cheaper to check) | none |
| **8** | **Row 5 / Option E — VERIFY AND CLOSE** (premise correction above) | **Already landed in v0.31.15**: bounded fetch, batched N+1s, bridged watch reads [mine: `work_resolver.py:91`, `jobs_crud.py:943/956`, `job_queue_service.py:556/711/844`]. Residual = *measure* that the measured 3.8–8.5 s leg actually moved (the v0.31.15 CHANGELOG itself discloses the fleet-scale inflation was **not** re-measured at lane time). | **XS (0.5–2 h)** — verification, not code | **Low** | the v0.31.15 harness `.agents/tester/harness/jobs-badge-cost/` |
| **9** | **`freeze_support()` enablement** (new, M2) — `multiprocessing.freeze_support()` as the **literal first statement** of `run_app.py` (before the `.env` block and `_boot_db_preflight()`). **Enabling-but-not-sufficient**: it buys no CPU by itself; it makes D possible and prevents a catastrophic boot loop if anything later spawns. | **MEASURED**: without it, spawn → **2 798 entry re-runs / 45 s, 0 tasks** [M2 §B]; with it → 1 entry, 16 tasks, `Pool(2)` 0.289 s. **Zero spec changes** (all mp modules bundled) [M2 A2]. | **XS–S (1–3 h)** incl. a frozen-boot gate | **Low** if done as the first statement; **CRITICAL** if done anywhere later (child re-runs DB preflight). Needs a frozen-binary boot smoke — and that smoke **must pin `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` explicitly** (house rule) | independent; prerequisite for #10 |
| **10** | **Bounded process pool** (new, M2) — ≤ 8 workers, spawn-on-demand, coarse jobs only (never checkpoint blobs / per-request chatter). | **MEASURED feasibility, BOUNDED-ESTIMATE benefit**: works, 0.29 s child boot, 0.25 s per 50×64 KB roundtrip [M2 §B]; but measured CPU-bound mass is **< 1 core** ⇒ **low priority, likely zero payoff today**. Revisit only if a residual unit is proven > ~0.2 core. | **M–L (12 h+)** | **Medium–High** — fork unsafe (60–69 threads); spawn semantics; pickling surface; child boot must never touch the DB | #9; and only after #1–#4 |
| **11** | **Wedge-capture retention is reason-blind** (audit r8 — *not* on this brief's fold list, but still open and it gates the NEXT arc's evidence): `prune_captures` (`wedge_capture/writer.py:297-378`) prunes oldest-first by mtime with **no reason awareness** [mine: read in full], while `sustained_cpu` fires ~3.3× more often than `sustained_loop_lag` [audit r8] — so the *diagnostic* captures are evicted first. Fix: per-reason quota, or a separate lag quota. | **PROVEN bias**: 318 journal fires vs **20 surviving captures** over a month [audit r8]; M1's harvest sees exactly that survivor set, and **only 1 of 20 is on the live version** [M1 Q5]. Without this, arc 3 will again be reasoning from a v-mixed, CPU-bias-selected sample. | **XS–S (1–3 h)** | **Low** — retention only; keep the directory-as-authority contract + the prune pins | none; do it **before** any lane that expects new evidence |
| **—** | **Cross-ref, owned elsewhere:** checkpoint read-path `_observe_read` O(payload) walk **on the loop** (`checkpoint_adapter.py:390`, docstring admits it) + ormsgpack decode on the **default** pool (`langgraph/checkpoint/postgres/aio.py:286, 328`) ⇒ owned by the checkpoint-amplification lane. M1 raises its weight: `MainThread` in `aput` is 17.6–23.2 % of one core [M1 Q3]. | audit rX [EVIDENCE §] | n/a here | n/a | — |

**Why this order.** #1 and #2 are the only rows with *measured* numbers on *both* sides (a measured
cost and a measured mechanism), and both are S. #3 is next because its attribution is measured even
though its gain is bounded-estimate. #4/#6 are real but carry **no** capture weight at the current
version — they are correctness-of-topology work, not lag relief. #5 is a 1-hour coupon. #8 is free
information. #9/#10 are engineering for a future that the measurements do not yet justify.

**Audit-row crosswalk** (so nothing from the remaining audit rows is silently dropped):
audit **r3** (202 hot-bridge sites) → row **#4**; audit **r4** (R-3 executor split) → **#3**; audit
**r5** (Option E) → **#8** (+ its residual folded into **#2b**/**#4**); audit **r6** (BFS batching)
→ **#6**; audit **r8** (instrument gaps / reason-blind retention — not on the brief's fold list, but
it gates the next arc) → **#11**; audit **r9** (periodic stall) → **#7**; audit **r10** (micro XS) →
split: the `logging.debug` f-string is **#5**, and the rest of that family (`routers/agents.py:131`
`open()` + `:150` `rmtree`, `git_diff_service.py:87` 1 MiB `read_text`, `maintenance.py:2903` /
`checkpoint_prune.py:612,786` `engine.dispose()`) is the **short-IO pool worklist inside #3** — all
re-verified still unbridged/unmoved at this base [mine]. **New rows contributed by M1**: **#1**
(ram-guard tick — *absent from the audit's table entirely*; it is the single largest steady CPU
consumer M1 found). **New rows contributed by M2**: **#2** (orjson — M2's own correction:
transitively installed *and* already in the shipped PYZ), **#9**, **#10**. **#2b** is the audit's
Option F, which the "already landed" assumption had wrongly covered.

---

## 4. WHAT NOT TO PARALLELIZE (honest list)

1. **Threaded normal-prose token counting.** **13× SLOWER** with 4 threads (0.531 s → 7.043 s) — the
   `regex` pre-tokenizer thrashes the GIL on many small matches; the BPE core's GIL release cannot
   rescue it because the pre-tokenizer dominates on normal text. Only **BPE-bound pathological
   shapes** scale (3.145×/3.192×). [M2 C1] *After arc 1 (v0.31.16 2C) the chunked path means the
   exact `estimate_tokens` route is already bounded — do not "make it parallel" on top.*
2. **Checkpoint decode offload.** `ormsgpack` is **GIL-held** (audit, prior house evidence) — moving
   the decode to a thread relocates the stall to "loop starved **and** worker starved". **Declined on
   evidence**; the algorithmic fix (the O(payload) `_observe_read` walk) is the only lever, and it
   belongs to the checkpoint-amplification lane. [audit rX; M2 C2 mechanism]
3. **Parallelism cannot fix wasted work.** The measured top costs are *recurring, avoidable or
   deferrable* work: a 200 ms full-`/proc` scan whose result rarely changes (#1), a 4.3×-slower JSON
   codec (#2), an f-string built before its level check on 32 captured episodes totalling 46 s of
   blocked loop time (#5), a checkpoint write at every node boundary. Distributing them across 16
   cores would still cost the same total CPU. **Eliminate/cache first, parallelize last** — this is
   the audit's own framing and M1's numbers confirm it (< 1 core of demand).
4. **A process pool for a < 1-core workload is pure overhead.** 0.29 s per child boot + 0.25 s per
   50×64 KB crossing, against a measured CPU-bound Python mass under one core ⇒ expected net
   negative. [M2 §B vs M1 Q1]
5. **Turning up `worker_pool_size` / `chat_worker_pool_size` is not a CPU optimization.** It is a
   concurrency knob (`MainLoopBridge.run_async`, §2.3); more turns means more on-loop fixed cost per
   second on one loop. Treat it as throughput-vs-latency, never as "use more cores". [mine: §2.3]
6. **Remaining serial work that is already cheap stays serial.** pydantic `model_validate` (290 k
   models/s), hashlib sha256 (298 MB/s single-threaded, already GIL-releasing), the ≤ 16 KB exact
   token path, the chunker itself (0.3 % of shipped-path time). Optimizing these is motion without
   movement — and note hashlib is the one kernel that *would* scale, which is precisely why spawning
   infrastructure for it would be paying 0.29 s per task to save milliseconds. [M2 C3, C1]

---

## 5. RECOMMENDED SEQUENCE + release-lane sharing

**Lane sharing principle** (from audit §sequence, preserved): share a lane when the *surfaces are
disjoint*; never share when the change is config-sensitive or when the next lane's sizing depends on
the previous lane's result.

| Lane | Rows | Why they share | Est. effort |
|---|---|---|---|
| **L0 — verify, no code** | #8 (Option E close), #7 diagnostic read, **#11 retention fix first** | #11 is cheap and it is the precondition for anything that later wants evidence; #8/#7 are pure measurement and can run in parallel with anything | XS–S |
| **L1 — cheap overhead** | **#1 ram-guard tick** + **#2 orjson** + **#5 logging guard** + **#2b response gzip** | four disjoint, independently-pinnable, individually-small wins; **highest measured impact per hour on the board**; no topology change | **S + S + XS + XS ≈ 6–11 h** |
| **L2 — topology** | **#3 R-3 executor split** | must size against the post-L1 load picture; touches executor config + metrics vocab → **must not share** (config-sensitive) | M 6–12 h |
| **L3 — bridges** | **#4 hot bridges (slices)** + **#6 BFS batching** | disjoint surfaces (tools/services/routers vs repository); can run parallel to L2 by a second agent if desired | M + S–M |
| **L4 — process-pool readiness (deferred)** | **#9 freeze_support** then **#10 pool** | #9 is safe and standalone (XS–S); **#10 is gated on evidence that does not exist yet** — hold until L1–L3 land and a residual GIL-bound unit is measured > ~0.2 core | XS–S now, M–L later |

**Ordering rationale, in one line each:**
- **L0 first** because #11 is a 1–3 h fix that decides whether arc 3 gets *evidence* at all (the
  current 20-capture corpus is a reason-biased survivor set from 318 fires [audit r8]), and because
  #8 is free information and #7 may have aged out (the current capture regime is quieter and
  differently shaped [M1 Q5]).
- **L1 before L2** because the executor split must be sized on a load picture that already excludes
  the two largest avoidable steady costs.
- **L3 is genuinely independent** — no executor, no loop-topology change — so it can ship whenever a
  lane is free.
- **L4 is honestly parked.** Building a process pool now would be building capacity for a workload
  that measures under one core (§1.1) — the honest answer to the Emperor's question is *"the
  multi-core lever exists and is proven, but the system does not yet have the CPU-bound demand to
  make it pay; the wins available now are overhead removal and de-contention, not parallelism."*

---

## 6. Provenance, caveats, and what is still unmeasured

- **Version mix, disclosed twice over.** M1's 20 durable captures are **19 × v0.31.10 + 1 ×
  v0.31.22** — only ONE capture is on the live version. Arc 1 (v0.31.16/.17) landed *after* the
  v0.31.10 captures, so **M1 cannot confirm that the tiktoken/pydantic on-loop classes are gone**;
  it can only show where the cycle goes now in the thread census (LLM SSE, checkpoint write,
  ram-guard). The audit's own mix was 6 × v0.31.2 / 7 × v0.31.1 / 7 × v0.31.10. [M1 Q5; audit
  EVIDENCE §1]
- **`lag − 1.0 s`.** `ensemble_loop_lag_seconds` p50 = 0.9997 s is the **probe's 1-tick floor**, not
  a stall; read the excess. Never quote raw p50 as blocking. [audit EVIDENCE §3.2]
- **My own counts differ from the audit's** and that is expected: audit 1 202 `to_thread` sites at
  v0.31.10 vs **1 181 non-comment lines at `bca2e046b`**; `run_in_executor` 25 sites reproduced
  exactly; the `logging.debug` anchor moved 9539 → **9559**. Line numbers are proximate-to-symbol;
  re-grep at use time. [mine]
- **Still unmeasured (do not pretend otherwise):** no per-request loop attribution exists (the
  instrument fires on CPU/lag, never per request); the metrics ring is 2 h in-RAM [audit r8]; the
  5 `/api/resources/*` bodies were unreachable under M1's read-only fence (auth-gated; minting a
  token = a live DB write) and the Prometheus exposition gate is OFF (`0 bytes`) [M1 Q4]; the
  default-pool size (20) is **computed, not observed** [audit §1b]; and the impact figures in rows
  #2/#3/#4 are **BOUNDED-ESTIMATE**, not measured end-to-end.
- **Retention bias is still live** and is now a ranked row (**#11**), not just a caveat:
  `prune_captures` prunes oldest-first by mtime with **no reason awareness**, so `sustained_cpu`
  fires evict the rarer `sustained_loop_lag` captures [audit r8; mine: `writer.py:297-378` read in
  full]. It is XS–S and it is what makes the *next* arc's evidence trustworthy.
- **M2's own disclosed defects**: two bench attempts accidentally fed a single-piece 512 KB run to
  the raw BPE core (the O(n²) pathology) and timed out — corrected to the shipped 4 KB grid; all
  reported numbers come from the corrected scripts. The `_MEIxxxxx` onefile teardown race appears only
  in the nofs+spawn boot-loop case (a symptom, not a product defect). [M2 §Data integrity]
