# EVIDENCE.md — what actually blocks the asyncio loop in production

**Commission:** ANALYSIS-ONLY production-evidence harvest for the daemon off-loop audit.
**Measured:** 2026-11-19 18:45–19:35Z against the **LIVE** daemon (`ensemble-main`, systemd; `current -> releases/v0.31.10`), **v0.31.10**, `127.0.0.1:9797`, MainPID 540643, `NRestarts=0`, uptime at first probe ≈ 18 829 s.
**Code tree cited:** `/home/nea/ensemble-src-wt-offloop-census` = detached @ `01eb788b6` ("! New version 0.31.10") — the tag `v0.31.10` resolves to the annotated tag object `6cfd0fc4…` whose peeled commit is `01eb788b6`. **All `file:line` citations below are against THIS tree.**
**Writes:** exactly one — this file. No code changes, no source edits, no git mutations (no add/commit/stash/push), no restarts, no config changes, no installs.

---

## 0. Verdict in one paragraph

The event loop is blocked by **synchronous CPU work executed on the loop thread**, not by DB waits and not by HTTP handler latency. The single dominant blocker is **token counting (`tiktoken`) called from the 95 %-precall compaction gate** — `daemon/graph.py:8255` → `daemon/loader.py:1099` — with **pydantic/langchain model-schema construction** second (the one capture that *actually* fired the loop-lag trigger was caught inside `pydantic` `create_model`). Over the measured window the loop's healthy floor is **p50 = 0.9997 s ≈ one probe tick**, so the real blocking budget is the **excess over that floor**, not the raw lag. Across the **5 237 merged 1 Hz lag samples** the loop is blocked ≥ 1 s beyond the floor in **8.55 %** of samples and ≥ 5 s in **1.39 %** — i.e. **~1 282 s of genuine loop starvation in ~12.6 h of observed uptime**, concentrated in ~321 discrete episodes. The `/api/jobs` route leg measured by the prior lane (`instances-list-latency/ANALYSIS.md` §6/H3) is **real and complementary but NOT the primary blocker** — it never appears in any of the 20 production stack dumps; the loop is blocked by *agent-turn* work, not by *request-handling* work.

---

## 1. TOP-5 RANKED EVIDENCE ROWS

Ranking = frequency × duration, from the merged 1 Hz lag census (§3), attributed to the loop-thread stack captured at the nearest wedge-capture instant (§4).

| # | Blocker (on the event loop) | Evidence | Frequency | Duration | Rank score |
|---|---|---|---|---|---|
| **1** | **`tiktoken` token counting at the 95 %-precall compaction gate** — `daemon/graph.py:8255` (`_maybe_precall_compact_95` → `estimate_messages_tokens(full_messages)`) → `daemon/loader.py:1099` (`tiktoken` `encode`) | loop-thread stack in **7 of 20** live captures — **4 of them `v0.31.10`** (1 `0.31.2`, 2 `0.31.1`); **122 of 321** block episodes; **163 s** blocked | **122 episodes** | max lag **20.55 s** | **163 s** |
| **2** | **pydantic / langchain dynamic model-schema construction** — `pydantic/_internal/_model_construction.py:255` `__new__` → `complete_model_class` → `generate_schema` (reached via langchain's `create_schema_from_function` → `pydantic.v1.validate_arguments` → `create_model`) | **the one capture that actually fired `sustained_loop_lag`** (`20261119T170818Z`, **`v0.31.10`**) was caught **inside `pydantic` `create_model`** with the loop thread at **86.1 %** of process CPU; all pydantic/langchain schema work = **5 of 20** captures — **2 of them `v0.31.10`** (3 `create_model` = 2× `0.31.10` + 1× `0.31.1`; 1 `bind_tools` `0.31.2`; 1 `from_function` `0.31.1`), **32 episodes**, **51 s** blocked | **32 episodes** | max lag **23.16 s** (the worst observed) | **51 s** |
| **3** | **`tiktoken` token counting in the context-usage + compaction paths** — `daemon/services/instance_messaging.py:1227` (`_compute_context_usage`) and `daemon/compaction.py:3440` (`compact_state`) | **2 + 1** of 20 captures — **0 of them `v0.31.10`** (1 `0.31.2` + 1 `0.31.1`, and 1 `0.31.1`); **17 + 44 = 61** episodes; **80 s** blocked | **61 episodes** | max lag **17.46 s** | **80 s** |
| **4** | **Synchronous psycopg on the loop** — `daemon/repositories/instance/repository.py:647` `get()` (from `daemon/graph.py:989` `_is_project_already_injected`) and a session-close `ROLLBACK` from `daemon/services/child_reports.py:5713` | **2 of 20** captures — **0 of them `v0.31.10`** (1 `0.31.2` rollback + 1 `0.31.1` select); **48** episodes; **65 s** blocked | **48 episodes** | max lag **11.82 s** | **65 s** |
| **5** | **`logging.debug` f-string construction on the loop** — `daemon/graph.py:9539` (`logger.debug(f'[LLM][…] Invoking LLM …')`) — the f-string is built **before** the level check | **1 of 20** captures — **`v0.31.10`**; **32** episodes; **46 s** blocked | **32 episodes** | max lag **5.24 s** | **46 s** |

**Two structural facts behind the ranking:**

* **Every one of the top-5 blockers is synchronous CPU or sync-I/O executed directly on the loop thread.** There is **no** `await` in the hot path of any of them: `estimate_messages_tokens` is a plain `def` (`daemon/loader.py:1102`), `create_model` is a plain `def`, `repository.get` is a sync SQLModel call. The v0.27.14 `db_offload` bridge (`daemon/services/db_offload.py`, added `377f0b627` 2026-10-30) is applied to the **`/api/instances` read path only** — it does **not** cover agent-turn work, which is exactly where the loop is blocked.
* **The `sustained_cpu` trigger and the `sustained_loop_lag` trigger select DIFFERENT populations.** 19 of 20 surviving captures are `sustained_cpu` (process CPU ≥ 95 % of one core for ≥ 30 s); only **1** is `sustained_loop_lag`. Because CPU saturation is dominated by *worker* threads (`ThreadPoolExecutor-3_N`), **10 of 20** captures have the loop thread as the #1 CPU consumer. The single `sustained_loop_lag` capture — the only one *selected because the loop was starved* — caught the loop thread inside **pydantic model construction at 86.1 % CPU**. That is the highest-fidelity single datum in this report.
* **Version mix of the capture set — DISCLOSED.** The 20 surviving captures were **not all written by the version whose code this report cites**. Each manifest's `release_version` field reads: **6 × `0.31.2`, 7 × `0.31.1`, 7 × `0.31.10`** (per-boot epochs, oldest→newest: the 6 `0.31.2` captures are the 2026-11-18T23:xx–11-19T02:47 window; the 7 `0.31.1` captures the 05:50–11:50 window; the 7 `0.31.10` captures the 13:37–17:23 window). **The flagship `sustained_loop_lag` capture (`20261119T170818Z`, 23.16 s) IS `0.31.10`** — the highest-fidelity single datum in this report is on the cited version, verbatim. The `tiktoken` blocker recurs in **4 of the 7** `0.31.10` captures, so the #1 ranked row is not carried by the pre-`0.31.10` captures alone. The per-category × per-version split is given in §4.1. **Conclusions survive the mix**: the two dominant on-loop work classes (`tiktoken` token-counting, pydantic model-schema construction) appear in **all three** versions, i.e. they are **not** a regression introduced or fixed by the `0.31.1 → 0.31.10` line; they are a stable property of the agent-turn path. The lag-probe mechanics (`MAX_LAG_SAMPLES=300`, one-outstanding-post, 1-tick floor) are identical across the three versions. Where a specific line number is quoted, it is quoted against `v0.31.10` per the commission.

---

## 2. THE EVIDENCE BASE (what exists, what it contains, what reaches you)

### 2.1 Wedge-capture instrument — enumerated from source

Cited against the census tree:

| Element | Location | Fact |
|---|---|---|
| Triggers (3 canonical + manual) | `daemon/services/wedge_capture/triggers.py:54-65` | `sustained_cpu`, `sustained_loop_lag`, `thread_concentration`, `manual`. `evaluate()` (`:168`) is a **pure function** over a sample series; cooldown is per-reason (D13). |
| Sustain test | `daemon/services/wedge_capture/triggers.py:149-165` | **wall-time** from first to last consecutive over-threshold sample; a drop below threshold **resets** the run (`:131-147`). |
| Thread-concentration floor | `daemon/services/wedge_capture/triggers.py:69, 275` | ratio is evaluated **only** above an absolute 5 %-of-one-core floor (D7/F-06) — no FPs on a quiet daemon. |
| Probe (A1) | `daemon/services/wedge_capture/lag_probe.py` | probe state is `initializing`/`ok`/`unknown`/`stopped`; **at most one outstanding post** (`:222`); an unset probe is **never** evaluated as a number (`:35-39`). |
| Rolling window | `daemon/services/wedge_capture/lag_probe.py:59` | `MAX_LAG_SAMPLES = 300` — **bounded**, ≈ 5 min at 1 Hz. Percentiles are **nearest-rank**, no interpolation (`:95-108`). |
| Capture content (FIXED file set) | `daemon/services/wedge_capture/writer.py:64-73` | `manifest.json`, `stacks.txt`, `threads.csv`, `lag.json`, `metrics.json`, `db.json`, `readyz.json`, `checkpoint_perf_tail.json` — **and nothing else**. |
| **Stack dumps** | `daemon/services/wedge_capture/collector.py:54, 113-125` | `faulthandler.dump_traceback(file=handle, all_threads=True)` — **every thread's Python stack**, incl. the event-loop thread. |
| Thread CPU attribution | `daemon/services/wedge_capture/writer.py` (`threads.csv`) | per-thread `cpu_delta_s` + `share_pct`. |
| Storage | `daemon/repositories/wedge_captures/models.py:47-135` | **file-first, row-second**: the DB row holds a **path reference** + a few hundred bytes of metadata, NEVER the payload. Table `wedge_captures`; uniqueness on `capture_dir` + `capture_id` (A3). |
| Row write failure path | `daemon/services/wedge_capture/writer.py:87-88` | `.pending_row.json` sidecar; reconciler re-inserts with `ON CONFLICT (capture_dir) DO NOTHING`. |
| Retention | `daemon/services/wedge_capture/writer.py:297-378` | **directory-authoritative** (D23): count cap / age cap / disk cap, computed by **walking** the capture dir (mtime sort), never from the table. `boot-records/` and `.pending_row.json` are excluded from the prune. |
| Row prune coupling | `daemon/services/wedge_capture/watchdog.py:677-692` | pruned directories ⇒ `repository.delete_by_dirs(...)` — **the DB row count follows the directory count**. |
| Live config (effective) | `GET /api/wedge-captures/status` | `cpu_pct=95.0`, `cpu_sustain_s=30.0`, `lag_s=5.0`, `lag_sustain_s=15.0`, `thread_concentration_pct=85.0`, `sample_interval_s=1.0`, `cooldown_s=300.0`, `deadline_s=20.0`, `max_captures=20`, `max_age_hours=168`, `max_disk_mb=512`. |

**Read surfaces (all four enumerated):**

1. **HTTP GET** `daemon/routers/wedge_capture.py` — prefix `/wedge-captures`, mounted at **`/api/wedge-captures`**: `GET ""` (list, `:159-167`), `GET /status` (`:231-239`), `GET /{capture_id}` (full payload, `:335-344`), `POST /capture` (manual, `:268-279` — **not used in this harvest**). Path-traversal guard: id matched against `CAPTURE_ID_PATTERN` (`writer.py:77-81`).
2. **Agent tools** `daemon/tools/wedge_capture.py` — `wedge_capture_list`, `wedge_capture_get`; **privileged category**, allow-list gated (`:106`).
3. **Metrics family F3** `daemon/services/metrics_query/wiring/w1.py:157-186` — `ensemble_wedge_capture_total{reason=…}` (reads the durable table via `_WEDGE_CAPTURE_COUNT_SQL` at `:472-473`) and `ensemble_wedge_capture_write_total{outcome=…}`.
4. **Journal** — `daemon.wedge_capture.watchdog` INFO lines: `Wedge-capture: trigger fired reason=…` / `Wedge-capture: capture written dir=…`.

### 2.2 What was harvested (live, read-only)

* **20 wedge captures** — `GET /api/wedge-captures` → 20 entries; **all 20 full payloads** fetched via `GET /api/wedge-captures/{id}` (**HTTP payload band 39.2–66.2 KB**; the durable on-disk file-set per `manifest.json` is **35.6–64.5 KB** — the HTTP envelope adds the artifacts wrapper). Every one carries the **complete `stacks.txt`** (24–70 thread stacks) plus a **300-sample `lag.json`** series.
* **Live metrics** — `GET /api/metrics/snapshot?family=F1|F3|F4|F5|F11` and `GET /api/metrics/series?family=F1` (2 h window, 15 s interval, 168 points).
* **Journal** — `journalctl -u ensemble-main` read-only: **318 trigger fires over 2026-10-20 → 2026-11-18** (30 days), plus the 2026-11-18→19 tail (57 fires).

---

## 3. LOOP-LAG METRICS — the healthy floor, and the real blocking budget

### 3.1 Live snapshot (fresh, `last_publish_age_s ≈ 0.93 s`)

```
ensemble_loop_lag_seconds{stat=p50} = 0.9991     ensemble_loop_lag_samples = 300
ensemble_loop_lag_seconds{stat=p95} = 1.2897     ensemble_loop_lag_overflow_total = 0
ensemble_loop_lag_seconds{stat=max} = 4.2886     ensemble_loop_lag_probe_state{state=ok} = 1
```
This matches the prior lane's snapshot (p50 ≈ 1.00 / p95 ≈ 1.29 / max ≈ 4.00) — **reproduced independently**.

### 3.2 The critical interpretation: p50 ≈ 1.00 s is the PROBE FLOOR, not a block

The probe posts one callback per tick from the watchdog thread (`lag_probe.py:236`). Under the **one-outstanding-post** rule (A1(b), `:222-231`) a *healthy* loop answers on the **next** tick, so the recorded lag for a healthy daemon is **exactly one tick interval** (1.0 s at `sample_interval_s=1.0`). The source says so explicitly at `daemon/services/wedge_capture/config.py:28-36`:

> *"Under the D4(b) probe mechanics a HEALTHY daemon measures a lag of roughly ONE tick (the probe posted at tick T is executed at tick T+1) … a violating value would fire the lag trigger on a perfectly healthy daemon."*

**Independent confirmation from the harvested data:** the merged 1 Hz census of all 20 captures (5 237 samples; **dedup method, stated once here and used for every figure in §3: overlapping capture windows are de-duplicated by epoch-second, keeping the maximum lag seen at that second**) has **p50 = 0.9997 s** and **42.75 %** of all deduplicated samples fall in the tight band **[0.9 s, 1.1 s]** (the *unduplicated* 5 453 raw samples give 43.50 % — the difference is purely the dedup; both are reported so the choice is auditable). A daemon that is genuinely healthy 50 % of the time and blocked 50 % of the time would show a bimodal distribution with a large mass at ~0.0 s (a tick where the callback answered immediately). It does not: the mass is a **single lattice spike at 1 tick**. Conclusion: **raw `ensemble_loop_lag_seconds` must be read as `lag − 1.0 s`** to mean "the loop was blocked". The prior lane flagged this as a limitation (§9, "cannot distinguish a 1 s block from a probe-lattice artefact"); §3.3 resolves it in favour of the lattice artefact.

### 3.3 The merged census — excess over the floor

5 237 unique 1 Hz samples, merged across **5 boot epochs** (windows overlap; samples are de-duplicated by epoch-second, keeping the max):

| Threshold (raw lag) | samples | % | **excess (block) seconds** |
|---|---|---|---|
| ≥ 1.0 s (the floor itself) | 2 545 | 48.60 % | 0 |
| **≥ 2.0 s** (≥ 1 s of real block) | **447** | **8.54 %** | **1 282 s** |
| ≥ 3.0 s | 197 | 3.76 % | 933 s |
| **≥ 5.0 s** | **73** | **1.39 %** | **602 s** |
| ≥ 10 s | 22 | 0.42 % | 316 s |

* p95 = **2.638 s**, max = **23.164 s**.
* **321 distinct block episodes** (lag ≥ 2.0 s) across ~12.6 h of observed uptime (epoch spans 3.53 + 0.19 + 0.18 + 4.80 + 3.85 h).
* **Two distinct "blocked time" measures, used consistently below:** (a) **1 282 s** = the summed **excess over the 1.0 s floor** across all samples ≥ 2.0 s — this is *loop-unavailable time*, the load-bearing number; (b) **447 s** = the **wall-clock span** of the 321 episodes (the gap between the first and last blocked sample of each episode). (a) ≥ (b) because a blocked sample costs more than 1 s each.
* Per boot epoch, in the doc's epoch order (`2026-11-18T14:07`, `2026-11-19T04:46`, `06:27`, `07:02`, `13:31`), with the threshold stated explicitly:
  * **≥ 2.0 s episodes** — **62 / 68 / 16 / 17 / 158** = **321** ✓ (the section total). Max excess: 19.55 / 4.84 / 11.49 / 4.03 / 22.16 s.
  * **≥ 5.0 s episodes** — **18 / 3 / 4 / 1 / 10** = **36** (the rare, large blocks).
  * **1 Hz samples** — 1 800 / 600 / 328 / 625 / 1 884; spans 3.53 / 0.19 / 0.18 / 4.80 / 3.85 h.
  * An earlier draft of this section printed "16 / 3 / 4 / 1 / 10" against the 321-episode total — those were the **≥ 5.0 s** figures with the epoch-1 value off by 2 (correct ≥ 5.0 s epoch-1 is 18). **Corrected.**
* **Coverage caveat:** the census is a **union of 20 capture windows**, not continuous telemetry — it covers the periods around captures. Gaps between boot epochs are unobserved. It is a **lower bound** on total blocked time.
* **Reproduction (durable inputs only):** every figure in §3.3 re-derives from `/home/nea/agents-ensemble/data/wedge-captures/*/lag.json` + each sibling `manifest.json`'s `captured_at` — no `/tmp` scratch and no live contact required. The dedup rule is stated once, in §3.2.

### 3.4 The metrics trend ring is a 2-hour window, not a history

`GET /api/metrics/series?family=F1&window=6h|24h|72h` returns the **same** 168 points / 2 h window for all three (window is clamped server-side to `WINDOW_SECONDS`). The series is an **in-RAM ring** (`daemon/services/metrics_query/ring.py`), so it **dies with the process** and **cannot** answer "how much did the loop block last week". The wedge captures are the only durable evidence.

### 3.5 Per-pool occupancy — and a brief-premise correction

`daemon/services/metrics_query/wiring/vocab.py:118` — `POOL_NAMES = ("manager", "checkpoint")`. **There is no `repair` pool and no `checkpoint-adapter` pool** in the metrics vocabulary (grep for `repair` / `checkpoint_adapter` across `daemon/services/metrics_query/` → 0 hits). The brief's premise of "manager/repair/checkpoint-adapter attribution" is **not implementable as stated**; the shipped attribution is two pools only. Live values:

```
ensemble_db_pool_native{pool=manager,size}=15   {pool=manager,overflow}=0
ensemble_db_pool_native{pool=checkpoint,size}=1  {pool=checkpoint,max}=15  {pool=checkpoint,available}=1
ensemble_db_pool_checked_out{pool=manager}=0     {pool=checkpoint}=0
ensemble_db_pool_waiting_clients{pool=checkpoint}=0   (manager renders NO series — async-pool-only counter)
```
**Both pools were IDLE at every sampled instant** — including the 23.16 s loop-lag capture, whose own `manifest.json` records `pool_checked_out: 0`, `pool_waiting_clients: 0`, `pool_size_current: 1`. **The DB was NOT the constraint.** (F6 `ensemble_pg_backends` renders `live=0` — the PG census family is dark on this daemon; the capture's own `db.json` reports 32 backends: 1 active / 22 idle / 9 unknown, **0 idle-in-transaction**.)

---

## 4. WEDGE CAPTURES — the 20 production stack dumps

### 4.1 Loop-thread stack by capture (the event-loop thread is `ensemble-prod` / `MainThread`, tid 540796)

| capture_id | ver | loop-thread was in | #1 CPU thread | loop = #1 CPU? | max lag |
|---|---|---|---|---|---|
| 20261118T232113Z | 0.31.2 | **sync psycopg ROLLBACK** (`child_reports.py:5713`) | TPE-3_18 | no | 11.82 |
| 20261118T232617Z | 0.31.2 | **tiktoken** (`instance_messaging.py:1227`) | TPE-3_0 | no | 17.46 |
| 20261118T235416Z | 0.31.2 | idle (parked in `asyncio.run`) | **MainThread 47.7 %** | yes | 6.24 |
| 20261119T011440Z | 0.31.2 | idle | ram-guard-tick | no | 7.85 |
| 20261119T012522Z | 0.31.2 | **pydantic `bind_tools`** (`graph.py:11050`) | **MainThread 66.6 %** | yes | 8.86 |
| 20261119T024751Z | 0.31.2 | **tiktoken** (`graph.py:8255`) | **MainThread 58.2 %** | yes | 20.55 |
| 20261119T055000Z | 0.31.1 | **tiktoken** (`compaction.py:3440`) | TPE-3_11 | no | 5.81 |
| 20261119T055617Z | 0.31.1 | **sync psycopg SELECT** (`repository.py:647` ← `graph.py:989`) | **MainThread 5.4 %** | yes | 5.84 |
| 20261119T062826Z | 0.31.1 | **pydantic `create_model`** | **MainThread 74.6 %** | yes | 6.32 |
| 20261119T063846Z | 0.31.1 | **tiktoken** (`graph.py:8255`) | TPE-3_8 | no | 12.49 |
| 20261119T070319Z | 0.31.1 | **langchain `from_function`** (`skill_evolution_tools.py:357` ← `instance.py:7706` ← `instance_lifecycle.py:4944`) | **MainThread 80.7 %** | yes | 5.03 |
| 20261119T071846Z | 0.31.1 | **tiktoken** (`graph.py:8255`) | TPE-3_15 | no | 4.27 |
| 20261119T115039Z | 0.31.1 | **tiktoken** (`instance_messaging.py:1227`) | TPE-3_5 | no | 4.57 |
| 20261119T133725Z | **0.31.10** | **tiktoken** (`graph.py:8255`) | **MainThread 71.3 %** | yes | 4.18 |
| 20261119T134252Z | **0.31.10** | **tiktoken** (`graph.py:8255`) | **MainThread 31.8 %** | yes | 8.31 |
| 20261119T134848Z | **0.31.10** | **pydantic `create_model`** | TPE-3_3 | no | 7.00 |
| 20261119T135802Z | **0.31.10** | **tiktoken** (`graph.py:8255`) | **MainThread 45.2 %** | yes | 3.76 |
| 20261119T170654Z | **0.31.10** | **`logging.debug`** (`graph.py:9539`) | TPE-3_16 | no | 5.24 |
| **20261119T170818Z** | **0.31.10** | **pydantic `create_model`** ← **the `sustained_loop_lag` capture** | **MainThread 86.1 %** | yes | **23.16** |
| 20261119T172308Z | **0.31.10** | **tiktoken** (`graph.py:8255`) | TPE-3_11 | no | 5.88 |

**Category tally over 20 captures** (`ver` = the capture manifest's own `release_version`): `tiktoken` **10** (7× `graph.py:8255`, 2× `instance_messaging.py:1227`, 1× `compaction.py:3440`) · `pydantic create_model` **3** · `bind_tools` **1** · `langchain from_function` **1** · **sync psycopg** **2** · `logging.debug` **1** · **idle** **2**.

**Category × version split** (from the 20 manifests, oldest→newest boot epochs):

| category | total | 0.31.2 | 0.31.1 | 0.31.10 |
|---|---|---|---|---|
| `tiktoken` @ `graph.py:8255` (precall gate) | 7 | 1 | 2 | **4** |
| `tiktoken` @ `instance_messaging.py:1227` | 2 | 1 | 1 | 0 |
| `tiktoken` @ `compaction.py:3440` | 1 | 0 | 1 | 0 |
| **all `tiktoken` (row 1 + row 3 of the ranking)** | **10** | **2** | **4** | **4** |
| `pydantic create_model` | 3 | 0 | 1 | **2** |
| `pydantic bind_tools` (`graph.py:11050`) | 1 | 1 | 0 | 0 |
| langchain `from_function` | 1 | 0 | 1 | 0 |
| **all pydantic/langchain schema work (row 2)** | **5** | **1** | **2** | **2** |
| sync psycopg (rollback + select) | 2 | 1 | 1 | 0 |
| `logging.debug` (`graph.py:9539`) | 1 | 0 | 0 | 1 |
| idle | 2 | 2 | 0 | 0 |
| **TOTAL** | **20** | **6** | **7** | **7** |

* **Version conclusion:** both top classes recur in **all three** versions. `tiktoken` is **4/7 of the `0.31.10` captures**, pydantic schema work **2/7** — together **6 of the 7** `0.31.10` captures. The two exceptions on `0.31.10` are one `logging.debug` and the flagship `create_model` capture (which itself is pydantic). **There is no version at which the loop is idle-dominant.**

**Daemon frames seen ONLY on the loop thread** (never on a worker): `loader.py:1099` (×10), `graph.py:8255` (×7), `graph.py:9738` `agent_node` (×7), `loader.py:1259`/`loader.py:1283` (×6/×3), `instance_messaging.py:1227`/`:1258` (×2), `graph.py:11050` `build_instance_llms` → `bind_tools`, `graph.py:14366` `build_instance_graph`, `instance_lifecycle.py:5054` `_restore_instance`, `instance_lifecycle.py:4944` `create_instance_tools`, `instance.py:1826` `_resolve_instance_id`, `child_reports.py:5713`.

### 4.2 The one capture that actually proves a loop block

`20261119T170818Z-ensemble-vm-sustained_loop_lag-7d45acf6` — trigger detail: *"event-loop lag 23.16s sustained for 16.7s (threshold 5.00s / 15.0s)"*. Its `manifest.json` records `observed.lag_s = 23.164142`, `sustained_s = 16.716`, `lag_probe.stale = true`, `last_probe_age_s = 24.45`, `release_version = 0.31.10`, `boot_epoch = 2026-11-19T13:31:01.236066`, `pool_checked_out = 0`. The loop thread's stack at that instant:

```
File "pydantic/_internal/_generate_schema.py", line 865 in _model_schema
File "pydantic/_internal/_generate_schema.py", line 1023 in _generate_schema_inner
File "pydantic/_internal/_generate_schema.py", line 729 in generate_schema
File "pydantic/_internal/_model_construction.py", line 648 in complete_model_class
File "pydantic/_internal/_model_construction.py", line 255 in __new__
File "pydantic/deprecated/decorator.py", line 243 in create_model
File "pydantic/deprecated/decorator.py", line 143 in __init__
File "__init__.py", line ??? in __init__
```
`pydantic/deprecated/decorator.py:143` is `self.create_model(fields, takes_args, takes_kwargs, config)` inside the **v1-compat `validate_arguments`** shim; `:243` is `class DecoratorBaseModel(BaseModel)` with a `@field_validator` — i.e. **pydantic is building a fresh model class, schema and core-schema at runtime**. The caller chain in the sibling capture `20261119T070319Z` proves the entry point: `langchain_core/tools/base.py:381 create_schema_from_function` → `langchain_core/utils/pydantic.py:268 _create_subset_model_v2` → `create_model` — reached from **`daemon/tools/skill_evolution_tools.py:357` `create_skill_evolution_tools`** → `daemon/tools/instance.py:7706` `create_instance_tools` → `daemon/services/instance_lifecycle.py:4944` `_restore_instance` (an **`async def`**) → `daemon/services/instance_lifecycle.py:4615 get_instance`. The `bind_tools` sibling (`20261119T012522Z`) is the same class of work via `daemon/graph.py:11050` `build_instance_llms` → `langchain_openai/chat_models/base.py:1968 bind_tools` → `model_json_schema`.

**⇒ Tool/LLM schema construction is rebuilt from scratch, synchronously, on the event loop, every time an instance graph is assembled.** Nothing in that path is offloaded.

### 4.3 The tiktoken path — and why it is expensive

`daemon/loader.py:1087-1099`:

```python
def estimate_tokens(text: str) -> int:
    import tiktoken
    encoder = tiktoken.get_encoding("cl100k_base")
    return len(encoder.encode(text))
```
`daemon/loader.py:1102 estimate_messages_tokens(messages)` walks **every** message and calls `estimate_tokens(str(block))` per content block (`:1255-1259`), per tool-call name/args (`:1266-1272`), per message name (`:1275-1277`) and per `reasoning_content`/`thinking` blob (`:1282-1283`).

**Local microbenchmark (read-only, no live DB, no live daemon contact; run against the census tree's own `daemon/loader.py` with the release venv):**

| input | median `estimate_tokens` | throughput |
|---|---|---|
| 5 000 chars, varied words | 0.45 ms | 11.2 MB/s |
| 20 000 chars, varied words | 1.74 ms | 11.5 MB/s |
| 60 000 chars, varied words | 5.11 ms | 11.7 MB/s |
| 200 000 chars, varied words | 17.89 ms | 11.2 MB/s |
| 500 000 chars, varied words | 34.74 ms | 14.4 MB/s |
| 40-message agent list (92 000 chars, varied) | **7.5 ms** | — |
| **60 000 chars, ONE long unbroken word** | **2 607.81 ms** | 0.023 MB/s |
| 60 000 chars, base64-ish blob | 9.50 ms | — |
| 60 000 chars, JSON-ish repeated key | 28.61 ms | — |

**The decisive finding:** `tiktoken` is **linear (~11 MB/s) on ordinary prose but collapses to O(n²)-ish on a single long unbroken token run** (60 KB → 2.6 s; 200 KB → 26.6 s). One 40-message agent list containing one such block blocks the loop for **seconds**. And the *first* call in a process costs ~0.8–2.2 s (tiktoken encoder construction) — visible in the benchmark's trial 0 (2 162 ms → 236 ms steady state). **This is a content-dependent, unbounded, synchronous, on-loop cost.**

### 4.4 The `logging.debug` finding

`daemon/graph.py:9539`:
```python
logger.debug(f'[LLM][{instance_short}] Invoking LLM ({call_type}) with {len(full_messages)} messages (model={model_name}, transient_attempts={transient}, timeout_attempts={timeout}{vision_log})')
```
The f-string is built **before** the level check. It is cheap per call but is on the loop and shows up in 32 block episodes (46 s).

---

## 5. JOURNAL — 30 days of trigger history (read-only `journalctl -u ensemble-main`)

### 5.1 Trigger fires 2026-10-20 → 2026-11-18

**318 fires total: 244 `sustained_cpu` (76.7 %) + 74 `sustained_loop_lag` (23.3 %).** Zero `thread_concentration`, zero `manual`.

By day (fires): 10-26: 27 (4 lag) · 10-27: 28 (5 lag) · **10-28: 71 (25 lag)** · 10-29: 44 (3 lag) · **10-30: 75 (23 lag)** · 10-31: 11 (8 lag) · 11-02: 2 (2 lag) · 11-04: 2 · 11-06: 1 · 11-08: 1 · 11-09: 1 · 11-10: 2 · 11-12: 1 · 11-13: 6 · 11-14: 10 · 11-15: 3 · 11-16: 14 (3 lag) · 11-17: 19 (1 lag).

### 5.2 The 74 `sustained_loop_lag` fires — observed lag distribution

* median **20.45 s**, mean 21.03 s, min 5.25 s, **max 51.94 s**.
* ≥ 20 s: **62** · ≥ 25 s: 6 · ≥ 30 s: 3 · ≥ 40 s: 1.
* Worst: **2026-11-16T09:03:08Z lag 51.94 s sustained 47.3 s**; 2026-11-16T08:47:34Z 39.19 s / 36.1 s; 2026-10-28T23:08:21Z 34.88 s / 29.4 s; 2026-11-02T21:08:29Z 28.24 s / 21.0 s; 2026-10-30T18:56:51Z 25.90 s / 21.1 s.

**Inter-arrival analysis (the 2026-10-26 → 10-31 cluster):** **31 consecutive pairs** of the 74 lag fires are separated by a gap in **[300 s, 360 s]** (median **315 s** ≈ the `cooldown_s=300` cap).

* **Counting rule (stated explicitly):** a pair contributes its **later member** — the fire that *closed* the ≥300 s gap — and the band is **[20.00 s, 21.63 s]**.
* **Result: 23 of the 31 pairs** have their later member in that band. Sensitivity to the rule: **22 of 31** by the *first* member instead; **30 of 31** by *either* member. Widening the band to **[19.42 s, 22.22 s]** captures **27 of 31** later members.
* The later members' observed lag ranges **13.16 s – 34.88 s** overall; the first members' ranges **17.54 s – 24.76 s**.

That is the signature of a **periodic ~20 s stall** on a ~5-minute cadence, not of random load. The same shape persists on 10-31 (five consecutive fires at exactly **20.00–20.01 s**, 360 s apart) and reappears on 11-16 (39 s, 52 s). **The current regime (11-18→19) shows only 3 lag fires with a different shape** — 9.74 s, 9.05 s, 23.16 s — i.e. the *current* build is in a different (quieter) regime than the Oct-26→10-31 cluster. *(An earlier draft printed "26 of the 31"; 23/31 is the correct later-member count. Corrected.)*

### 5.3 The 2026-10-28 DB-starvation abort (the brief's "known past capture") — REPRODUCED FROM THE JOURNAL

`journalctl` over 10-20 → 11-18 contains **498** `current transaction is aborted, commands ignored until end of transaction block` lines and a matching `relation "tasks" does not exist` line, emitted by `daemon.wedge_capture.db_capture` at 5-minute intervals across the whole window (first seen 2026-10-28T22:50:13Z, i.e. **during the 25-fire lag cluster on 10-28**). The wedge capture's own DB sub-capture was failing to read the store census while the daemon's loop was stalled at 20–35 s. This is the DB-starvation/abort class the brief names — **but the surviving artifacts for that era are gone** (see §7).

---

## 6. SAMPLING DISCLOSURE — the instrument boundary

* **`py-spy` is ABSENT and was NOT installed.** Re-verified this run: `command -v py-spy` → not found; `/home/nea/ensemble-src/.venv/bin/py-spy` → absent; `/home/nea/ensemble-src-wt-offloop-census/.venv/bin/py-spy` → absent (the census worktree has **no `.venv` at all**); `find /home/nea -maxdepth 4 -name 'py-spy*'` → 0 hits; `python3 -c "import py_spy"` → ImportError. **No process attach, no ptrace, no profiler was used at any point.** The two `py-spy` mentions in the tree are **comments in unrelated files** (`daemon/util/log_redaction_filter.py:43`, `daemon/services/kms_lite.py:173`), not instrumentation.
* **The house sampling seam is `faulthandler`, and it lives inside the wedge-capture instrument only**: `daemon/services/wedge_capture/collector.py:54` (`import faulthandler`) and `:113-125` (`faulthandler.dump_traceback(file=handle, all_threads=True)`). A grep of `daemon/` for `faulthandler|py_spy|dump_stack|dump_traceback|sample_stack|loop.?monitor` returns **only** that collector plus the two prose comments. **There is no continuous stack sampler, no periodic loop monitor, and no per-request loop-lag attribution anywhere in the daemon.**
* **Consequence — the evidence base is exactly three things:** (a) the **20 wedge captures** (each a *single-instant* all-thread stack dump + a 300-sample lag window + per-thread CPU), (b) the **journal** trigger lines, (c) the **in-RAM metrics rings** (2 h, process-lifetime). **Nothing else.** There is no way, with the shipped instrument, to attribute a block to a request, a route, or a user.
* **Attribution method and its weakness.** Block episodes are attributed to the loop-thread stack of the **nearest** capture in time. Because captures are event-triggered (CPU/lag), a block episode between captures inherits the category of whatever fired next. This is **association, not proof**, for all 321 episodes. The **20 capture-instant stacks themselves are direct observations**; the 321-episode split is a derived attribution. The report's ranking leans on the direct observations (rows 1–5 all have ≥1 direct stack) and uses the 321-episode split only to order items with equal direct evidence.

---

## 7. WHAT WAS **NOT** REACHABLE (disclosure list)

1. **No live-DB queries** (per fence). The `wedge_captures` table was read **only** through the sanctioned metrics family F3 (`ensemble_wedge_capture_total` = `{sustained_cpu: 19, sustained_loop_lag: 1}`) and the HTTP GET list. **Consequence:** the DB table holds only the **20 surviving rows** — the writer deletes rows for pruned directories (`watchdog.py:677-692`), so the table is **not** a durable 30-day history either. **The durable evidence of 318 fires exists only in the journal.**
2. **The 2026-10-28 / 2026-10-29 / 2026-10-30 loop-lag capture artifacts are GONE.** The brief's "known past captures" (`20261029T101054Z` lag 20.06 s with the psycopg/`instance_lifecycle.py:5115` stack; the 2026-10-28 DB-starvation abort) are **not on disk anywhere on this host** — `find /home/nea -maxdepth 8 -name '20261029T101054Z*'` → 0 hits; no `202610*` capture directories exist under any wedge-captures root. They were evicted by `max_captures=20` / `max_age_hours=168` (`writer.py:297-378`). **Only their journal trigger lines survive.** This is itself a finding: **the capture retention preferentially evicts the loop-lag evidence**, because `sustained_cpu` fires 3.3× more often (244 vs 74 over 30 days) and the prune is **mtime-order, reason-blind** (`writer.py:362-366`).
3. **The 2026-10-31 → 2026-11-18 gap.** 74 lag fires are known from the journal but **zero** of their stack dumps survive; the 20 surviving captures are all from 2026-11-18 → 11-19. The ranking's frequency rows therefore rest on **~12.6 h of captured uptime** plus **30 days of trigger counts** — a **sampling** of the blocked population, not the whole of it.
4. **No request-level attribution.** The instrument cannot say *which* HTTP request (if any) coincided with a block. The prior lane's `/api/jobs` leg (3.8–8.5 s) is **not** visible in any stack dump here, so this report **cannot confirm or refute** that route's contribution from the capture evidence; it can only say the route is **not** the dominant *agent-turn* blocker.
5. **The `thread_concentration` trigger never fired** in 30 days — the "one thread holds the process's CPU" shape is **unobserved**, not disproved (the concentration floor at `triggers.py:69` may be suppressing it).
6. **No `pg_stat_activity`/`pg_stat_statements` census** (fence + role privilege). The `ensemble_pg_backends` family (F6) is **dark** on this daemon (`live=0`); backend state was read only from the capture's own `db.json`.
7. **The live daemon is a frozen PyInstaller binary** (`/home/nea/agents-ensemble/releases/v0.31.10/ensemble-prod`, 83 MB, `cwd=/home/nea/agents-ensemble`). Its `daemon/` tree is a **plugin slice**, not the full source, so the cited line numbers could **not** be byte-verified against the shipped runtime — only against the census tree (the designated citation tree), whose `graph.py`/`loader.py`/`compaction.py`/`instance_messaging.py`/`instance_lifecycle.py`/`instance.py` were confirmed present and read.
   **Line-granularity honesty (corrected — the original wording was overstated):** of the **21** line numbers this report enumerates for the loop thread, **16 land inside the named function** at that line in `v0.31.10`; **5 land at a call-site / owner boundary** instead: `loader.py:1259`/`1268`/`1283` → owner `_block_tokens` (called from `estimate_messages_tokens`); `instance_lifecycle.py:4944` → the `create_instance_tools(...)` **call site** inside `_restore_instance`; `instance.py:7706` → the `create_skill_evolution_tools(...)` **call site** inside `create_instance_tools`; `repository.py:647` → a **comment** — `def get()` is @650 and the `db_session.get(Instance, …)` @653; `skill_evolution_tools.py:357` → a **docstring** — `def create_skill_evolution_tools` is @209. Across **all** daemon frames in the 20 dumps (not just the loop thread) **145 / 1 605 (9.0 %)** land in a different function than a name-grep would suggest. **The described call chains and the causal claims ARE confirmed by direct source reading** — the frames' *order* and *function names* in the dumps are self-consistent; what fails is only the granularity of asserting that every line number sits inside its named function. Cite these anchors as *proximate to*, not *inside*, the named symbol.
8. **Two authenticated requests were made to `GET /api/wedge-captures`, `/status`, `/{id}`, `/api/metrics/snapshot`, `/api/metrics/series`** using the prior lane's pre-existing read-only operator session (`auth_sessions.id` → `Cookie: ensemble_sid=…`, read from the `chmod 600` file `/tmp/inst-lat-rJG6CI/sid` left by the `instances-list-latency` lane). **No token was minted, no auth was bypassed, no POST was issued** (the manual-capture POST was deliberately **not** called — it is a write). **Side effect (disclosed):** each authenticated request updates `auth_sessions.last_used_at` — identical to what the operator's open browser does.
9. **No loop-block was induced, reproduced, or load-tested.** Every number here is observational. The `tiktoken` microbenchmark in §4.3 is a **local, synthetic, read-only** measurement against the census tree's own `daemon/loader.py` in the release venv — it bounds the *shape* of the cost, it does **not** reproduce a production block.
10. **The `db_offload` seam was NOT exercised.** `daemon/services/db_offload.py` (6-worker executor, `thread_name_prefix="db-offload"`) appears in the capture thread lists as `db-offload_0/1/2` — all three idle at capture time. Its admission/backpressure behaviour under load is **not** measured here.

---

## 8. WHAT THIS DOES *NOT* CLAIM

* It does **not** claim the `/api/jobs` badge leg is harmless. The prior lane's H3 composite (3.8–8.5 s loop block, 3.7–4× inflation of every instances leg) stands as measured; this report simply finds that the **agent-turn** blockers are what the *production captures* actually caught. **Both can be true simultaneously** — and the captures caught only the agent-turn class because the instrument fires on CPU/lag, not per request.
* It does **not** claim p50 ≈ 1.00 s means "the loop is blocked 1 s". It means the **probe floor** is 1 tick (§3.2). Any downstream analysis quoting `ensemble_loop_lag_seconds{stat=p50}` as a stall indicator is reading an artefact.
* It does **not** claim the 321-episode attribution is causal proof (§6). The 20 capture-instant stacks are observations; the episode split is association.

---

## 9. CROSS-REFERENCE TO THE PRIOR LANE

`instances-list-latency/ANALYSIS.md` §6/H3 measured the `/api/jobs?status=completed,settled,…` leg at **3.833 s loaded p50 / 8.536 s max** and found it inflating every instances leg **3.7–4.0×**, with `ensemble_loop_lag_seconds` p50 ≈ 1.00 s. This report **does not re-derive** that measurement (per the brief). It **extends** it in three ways:

1. **It explains the p50 ≈ 1.00 s** the prior lane saw: it is the probe's **1-tick floor**, not a stall — so the prior lane's "consistent with the measured queueing amplification" (§9) should be **revised** to "consistent with, and fully explained by, the probe lattice; the load-bearing evidence remains the H3 composite".
2. **It adds the population the prior lane could not see**: the loop is blocked by **agent-turn** work (token counting, model-schema construction) in **~8.5 %** of 1 Hz samples, with a worst observed block of **23.16 s** — a different mechanism from HTTP-handler latency, requiring a different fix (offload the turn work, not just the route).
3. **It supplies the trigger cadence** the prior lane did not have: 74 `sustained_loop_lag` fires / 30 days; **31 consecutive pairs are spaced 300–360 s apart (median 315 s)**, of which **23 have their later member in the 20.00–21.63 s band** — a **periodic** stall, which is a much stronger lead than "it feels slow when my browser is open".

---

## 10. FILE INVENTORY (this harvest's evidence, all outside the repo except this file)

⚠️ **EPHEMERALITY WARNING.** Everything under `/tmp/offloop-ev/` is **scratch and will vanish** — it is NOT part of any deliverable and MUST NOT be treated as evidence. **Every headline number in this report re-derives from DURABLE sources alone**:
* **`/home/nea/agents-ensemble/data/wedge-captures/`** — 20 capture directories, each with the full 8-file set (`stacks.txt`, `threads.csv`, `lag.json`, `manifest.json`, `metrics.json`, `db.json`, `readyz.json`, `checkpoint_perf_tail.json`). Verified **manifest-for-manifest (20/20) against the `/tmp` copies** by the verifying lane. Re-running the merged census (§3.3) needs **only these directories** plus a timestamp sort.
* **The systemd journal** — `journalctl -u ensemble-main` (read-only) supplies every trigger-fire count, the 74 lag-fire values, and the inter-arrival cadence in §5.
* The **in-RAM metrics ring** (`/api/metrics/series`, 2 h) is the one genuinely non-durable input, and §3.4 discloses it as such. Its values (`F1` p50/p95/max at sample time) are restated as literals in §3.1, so nothing in this report depends on the ring surviving.

Scratch files under `/tmp/offloop-ev/` (enumerated for reproducibility; do **not** cite):

| file | size | what it is |
|---|---|---|
| `j_full.txt` | **1.1 GB** (1 092 137 053 B) | full `journalctl -u ensemble-main` harvest attempted back to 2026-10-07 (the 4 GB journal); **incomplete** — its write was still advancing when the window closed, and the wedge-capture era it was meant to cover is fully covered by `j_wedge_oct.txt` + `j_all.txt` instead |
| `j_all.txt` | 20 MB (20 032 152 B) | `journalctl` 2026-11-18 → (85 801 lines) |
| `j_wedge_oct.txt` | 422 KB | the wedge-line slice 2026-10-20 → 2026-11-18 (1 674 lines, 318 trigger fires) |
| `j_wedge.txt` | 24 KB | an earlier, superseded bounded wedge slice |
| `trig_all.txt` | 0 B | empty intermediate (the full-journal slice never completed) |
| `caps/*.json` | 20 files, 40–68 KB each | **all 20 full capture payloads** (stacks + threads.csv + lag.json + manifest + db.json + metrics.json + readyz.json + checkpoint_perf_tail.json), fetched read-only |
| `list.json` | 10 KB | the 20-capture list from `GET /api/wedge-captures` |
| `lagcap.json` | 43 KB | the `sustained_loop_lag` capture payload |
| `fams.json` | 39 KB | the metrics family catalog (36 families) |
| `series_6h.json` / `series_24h.json` / `series_72h.json` | ~17 KB each | the F1 trend series (all three clamped to the same 2 h window) |
| `cats.json` | 86 KB | intermediate per-capture classification |
| `loopthreads.json` | 137 KB | intermediate loop-thread extraction |
| `rows.json` | 4.9 KB | the per-capture classification table (category, top-CPU thread, loop-is-top flag, max lag) |
| `final_numbers.txt` | 1.1 KB | the merged-census outputs reproduced above |
| `bench_tok.py` / `bench_tok2.py` / `bench_tok3.py` / `bench_tok4.py` | 1.5–2 KB each | the read-only `tiktoken` microbenchmarks (§4.3) |

**No file outside `EVIDENCE.md` was written inside the repository. No git command that mutates state was executed (no add/commit/stash/push/checkout/reset).**
