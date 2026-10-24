# Recurrence Evidence Matrix — wedge-capture commission (D3)

Commission: wedge-capture evidence instrumentation (incident 2026-10-22 follow-up).
Tree: `/home/nea/ensemble-src-wt-wedge-capture` @ base 639b7129b (branch `feature/wedge-capture-watchdog`). Code read-only for this document.
Companion documents: W1 implementation plan (owns instrument + final trigger defaults); diagnostics-lane addenda in shared meta KV.

---

## 1. Purpose + Scope

**Purpose:** turn the NEXT recurrence of the 2026-10-22 wedge family into a root-cause **verdict**: `H1` / `H2` / `BOTH` / `NEW`.

- **H1 (loop starvation by AutoContinue re-execution).** Boot-time AutoContinue re-executes large-history "poison" turns; the executing graph turn monopolizes the asyncio loop thread → sustained CPU peg on one core + event-loop lag + readyz sentinel timeouts. UNPROVEN — the instrument exists to falsify it if wrong.
- **H2 (checkpoint-store non-reclamation → saver-pool saturation).** ~47h without checkpoint cleanup (~96.5k rows / ~13GB observed 10-22); hard-coded 5-connection saver pool (`daemon/persistence.py:273-294`, `min_size=1, max_size=5`) saturates → saver ops stall → readyz sentinel timeouts + flaps; **no CPU peg**.
- **BOTH** — the two layers can stack (baseline calls this "mixed": e.g. a boot-scheduled resume executing while the store is bloated).
- **NEW** — anything else (GC/memory pressure, disk/IO, DB-side, foreign process). §7 is the escape hatch.

**Scope:** the verdict METHODOLOGY only. This document does not plan the instrument (W1 owns it); it consumes the instrument's captures as primary input and tells a responder exactly how to read them.

**The instrument (given, from W1):** an external daemon thread captures, at wedge time, into a file + metadata row: (1) all-thread Python stack dump; (2) per-thread CPU attribution; (3) event-loop lag samples; (4) GC stats + RSS + cgroup memory pressure; (5) saver-pool counters; (6) executing instance(s)/turn(s) + resume history + boot epoch; (7) that epoch's boot-time AutoContinue decisions; (8) checkpoint-store census; (9) tail of the `daemon.checkpoint_perf` stall ledger; (10) a readyz snapshot. Trigger reasons: sustained CPU ≥ ~95% of one core / sustained loop lag ≥ ~5s / per-thread CPU concentration. Field-level vocabulary: §9 (data dictionary).

---

## 1.1 Documented Baseline (embedding the binding diagnostics-lane facts, verbatim)

> **Chronic shape** = slow agets with NO boot at all (19:42–19:47Z 10-21, quadruple simultaneous on one thread, continuous daemon) — boot-epoch correlation is the SEPARATOR between chronic and S1.
>
> **S1 window** = exactly ONE slow aget (251s @ 05:03:51Z) alongside 95–107% single-core CPU and 3 boots scheduling 4/0/1 resumes with msg_counts up to 144.
>
> **Verdict rules (baseline):** CPU-peg + loop-lag + MANY pending resumes = H1 shape; lag/stall + pool-saturation counters + NO CPU peg (possibly no boot) = H2 shape; mixed = both layers stacked.

Supporting verified facts (this tree, base 639b7129b):

- Boot-continue decisions are LOG-ONLY today: `[BOOT_CONTINUE] …` lines from `daemon/services/auto_continue_boot_pass.py` — main pass emit sites :323-327 (OFF), :333-336 (no epoch), :361-365 (selection failed), :384 (multi-task skip), :403/:435-451 (per-candidate skips), :463 (success `instance=%s work_id=%s epoch=%s`), :477 (failure); orphan-sweep twin `sweep_orphaned_live_turns` :685-687 (OFF), :695 (no epoch), :718/:750/:765/:779/:823/:831-836/:846-849 + ALWAYS-EMITTED summary :865-879; caller summary at `daemon/api.py:2659` renders the `ContinueResult` dataclass (`"AutoContinue boot pass: %s"`). `ContinueResult` fields (auto_continue_boot_pass.py:164+): `candidates, scheduled, skipped_no_checkpoint, skipped_resume_refused, skipped_kill_switch, skipped_no_boot_epoch, skipped_multi_task_instance, errors, duration_seconds`.
- The W1 implementation ADDS an in-process boot record (effective auto-continue mode as the boot pass actually read it — never re-derived precedence — raw env value, `boot_epoch`, `ContinueResult` counters, `decided_at`), set in the pass INCLUDING the OFF early-return branch, exposed via a read-back surface. **That record is the PRIMARY structured source once live; log lines are the legacy/fallback path.**
- Per-task DB stamp: `mark_task_auto_continued(task_id, boot_epoch)` CAS at `daemon/repositories/task/repository.py:1412`; CLEARED on success by `clear_task_auto_continued` (:1493). **Gotcha: absence of the stamp does NOT mean "never resumed"** — a resumed task that later completed has its stamp cleared.
- Boot epoch: `daemon/services/boot_epoch.py:70-76` `get_boot_epoch()` — O(1) module global, naive-UTC, thread-safe; `None` is a real state. Correlation key = `boot_epoch` value; **naive-UTC discipline — never compare against `datetime.now(timezone.utc)`**.
- Live-observed lever flip: 05:24:18Z boot logged `[BOOT_CONTINUE] OFF … skipped_kill_switch=1`; 05:26:33Z boot "ran, scheduled=1" — `set-environment` flipped BETWEEN boots 2 minutes apart (volatile manager env; precedence `.env` > unit `Environment=` > `set-environment` > default). **Correlation keys on epoch, never wall-clock proximity alone.** Effective mode must be surfaced FROM THE RUNNING PROCESS, never re-derived.

### readyz / stall-ledger semantics (store/pool correction — supersedes any earlier wording)

- The readyz **checkpoint-saver component is a write-once SENTINEL probe**: `aput_writes` into `checkpoint_writes`, then `await saver.aget_tuple(sentinel_read_config)` on a fixed synthetic thread, scheduled onto the loop via `run_coroutine_threadsafe` and blocked on with a ~0.9s wait (`daemon/services/readiness.py:580-743`, loop-bound leg :725-741; `CHECKPOINT_SAVER_PROBE_TIMEOUT_S = 1.0` at readiness.py:54, the leg waits `1.0 − 0.1`). It is **NOT** an `AsyncConnectionPool.check()` call; the reason string `"checkpoint_saver: AsyncConnectionPool.check() failed or timed out"` at readiness.py:337-339 is **STALE cosmetic** — do not read it as a pool check.
- Therefore a **sentinel timeout** means: the loop could not service the sentinel `aget_tuple` within ~1.0s — which is consistent with **loop starvation (H1) OR pool starvation (H2)**. The NEW pool counters (capture item 5) are what disambiguate.
- The `queue_max_age = None` overload lives on the **QUEUE probe** (`QUEUE_PROBE_TIMEOUT_S = 2.0`, readiness.py:59): probe timeout → `None` + degraded reason `"queue_freshness: queue probe timed out"` (readiness.py:525-530); empty/pre-epoch RUNNING set → `None` + FRESH (readiness.py:289-292 docstring region). `None` is overloaded — the accompanying degraded reason disambiguates.
- Pool counters will come from `psycopg_pool.AsyncConnectionPool.get_stats()` — sync, loop-free, touches no connections (pool constructed `daemon/persistence.py:273-294`, min1/max5; reachable via `manager.checkpointer.raw_saver.conn`). `get_stats()` field names are UNVERIFIED against pinned psycopg_pool 3.3.1; §9 uses descriptive canonical names (`pool_checked_out`, `pool_size_total`, `pool_waiting_clients`, `pool_checkout_timeouts`) — the implementation maps them at wiring time.
- Stall ledger: `[CheckpointPerf] op=aget` lines are pure logging (`daemon/checkpoint_perf.py:59-108`; format ``[CheckpointPerf] op=<name> thread=<8char> latency_ms=<int> bytes=<int> deleted=<int>``). The 304s/557s stalls were `op=aget` on the GET /messages path (`daemon/persistence.py:465`). `aget_tuple` is **UNTIMED in-tree today** — the sentinel probe is its only observable. The in-memory histogram (`daemon/checkpoint_metrics.py`) is thread-safe but its buckets cap at **10.0s** (checkpoint_metrics.py:61) — multi-minute stalls land in count/sum only.

---

## 2. Observables Table

Each observable: source → which hypothesis it discriminates and HOW (directional values). Instrument items are numbered as in §1/§9; "external" observables are responder-side.

| # | Observable | Source | Discriminates | Directional reading |
|---|------------|--------|---------------|---------------------|
| O1 | Thread stacks at wedge | capture (1) `thread_stacks` | H1 vs H2 vs NEW | **H1:** loop thread inside LangGraph node / LLM-stream processing frames of a resumed turn (NOT parked in epoll/select, NOT in pool checkout). **H2:** loop thread parked (event-loop idle or awaiting pool checkout); optionally several threads waiting in pool `_getconn`. **NEW:** frames in GC/alloc paths, unexpected IO, third-party code. |
| O2 | Per-thread CPU attribution | capture (2) `thread_cpu_attribution` | H1 vs H2 vs NEW | **H1:** loop thread (native_id == main thread) concentrates ≥ ~80% of process CPU (PROPOSED). **H2:** flat/low profile — all threads blocked, nothing burning CPU. **NEW:** concentration on a non-loop, non-pool thread. |
| O3 | Event-loop lag over trigger window | capture (3) `loop_lag_histogram` | H1 vs H2 (shape, not cause) | **H1:** sustained ≥ ~5s lag WITH CPU peg (loop busy executing). **H2:** lag/stall WITHOUT peg (loop healthy but its awaited saver ops stall on pool). Lag alone is non-discriminating; always read jointly with O1/O2/O5. |
| O4 | GC stats + RSS + memory pressure | capture (4) `gc_rss_snapshot` | NEW (escape hatch) | RSS monotonic growth across captures + gen2 collection count climbing + `memory.pressure some` > 0 + no peg + pool healthy → memory/GC-pressure hypothesis (§7.1). Otherwise neutral. |
| O5 | Saver-pool counters | capture (5) `saver_pool_counters` | H2 vs H1 | **H2 signature:** `pool_checked_out == pool_size_total` (5/5) AND (`pool_waiting_clients` ≥ 1 OR `pool_checkout_timeouts` ≥ 1 in window). **H1-consistent:** pool healthy at capture (H1 can wedge with pool fine). Pool counters are the disambiguator for sentinel timeouts (§1.1). |
| O6 | Executing turns + resume history + epoch | capture (6) `executing_turns_resume_history` | H1 | Executing turn whose Task row carries `auto_continued_at == <capture boot_epoch>` → the turn IS a boot resume. msg_count ≥ ~100 (PROPOSED "large history"; S1 showed up to 144) strengthens. |
| O7 | Boot AutoContinue record | capture (7) `boot_autocontinue_record` (primary once live); fallback: `[BOOT_CONTINUE]` lines | H1 vs chronic-H2 | **H1 needs:** `effective_mode=on` AND `scheduled ≥ 1` at the capture epoch. **H1-killing:** `effective_mode=off` (`skipped_kill_switch=1`) or `scheduled=0` with zero stamps — wedge recurred with nothing re-executed. **Chronic-H2 marker:** slow agets during a continuous daemon with NO boot in window. |
| O8 | Checkpoint-store census | capture (8) `checkpoint_store_census` | H2 shaping | Elevated rows/bytes (PROPOSED: checkpoints > 50k rows or blobs > 8 GB; observed pathology ~96.5k / ~13 GB) + stale `maintenance_runs` `section=checkpoint-cleanup` (47h miss precedent) → H2 substrate. Modest counts weaken bloat hypothesis. |
| O9 | Stall-ledger tail | capture (9) `stall_ledger_tail`; extend via daemon log grep (§8) | H1 vs H2 shape | **S1 shape:** exactly ONE slow `op=aget` (251s; also 304s/557s on GET /messages, persistence.py:465) co-incident with peg. **Chronic shape:** triple/quadruple simultaneous ~181.5s agets completing within ~660ms of each other, no boot, no peg. Multi-minute sentinel stalls are visible only via the probe + pool counters (`aget_tuple` untimed; histogram caps at 10.0s — §1.1). |
| O10 | readyz snapshot at capture | capture (10) `readyz_snapshot`; external: probe history/uptime monitors | Disambiguation | `checkpoint_saver` timeout ⇒ loop could not service sentinel within ~1.0s (loop- OR pool-starved → read with O5). `queue_freshness` `None`: degraded reason `"queue probe timed out"` (readiness.py:525-530) = probe timeout; `None` + FRESH = empty/pre-epoch RUNNING set (readiness.py:289-292). Ignore the stale `'AsyncConnectionPool.check()'` wording (readiness.py:337-339). |
| O11 | Boot-epoch correlation of slow agets / wedges | external: daemon logs keyed by epoch (§8) | **THE separator (baseline)** | Wedge inside a boot epoch with `scheduled ≥ 1` → H1-compatible. Recurrent stalls under a CONTINUOUS daemon, no boot in window → chronic/H2 (baseline 19:42–19:47Z 10-21 quadruple). Never correlate by wall-clock proximity (lever-flip precedent, §1.1). |
| O12 | `pg_stat_activity` at wedge (if responder attached live) | external (§7.4) | H2 / DB-side NEW | Rows in `wait_event_type=Lock`/`IO` on checkpoint tables; saver connections holding `SELECT … FROM checkpoints/writes/blobs` for long durations → H2. Idle pool + idle loop → NEW. |
| O13 | Host/foreign CPU | external: `top -H -p <pid>`, cgroup CPU stat | NEW | Peg attributable to a thread whose stack shows neither graph frames nor pool checkout, or to a foreign process → NEW (§7.5). |

---

## 3. Verdict Matrix

Rows = observable patterns; columns = verdict contribution. `—` = non-discriminating. Thresholds marked **[P]** are PROPOSED — the W1 implementation plan owns final trigger defaults; the instrument's trigger defaults (CPU ≥ ~95% of one core; lag ≥ ~5s) are the anchor.

| # | Observable pattern | CONFIRMS H1 | REFUTES H1 | POINTS H2 | POINTS NEW |
|---|--------------------|-------------|------------|-----------|------------|
| R1 | CPU peg at capture (≥95% of one core sustained ≥30s **[P]**; trigger default ~95%) | Pegged **and** loop thread concentrates (R2) **and** pending resumes ≥1 (R5) | Wedge/lag present with **NO peg** — execution-starvation mechanism absent | No peg (H2 signature includes NO CPU peg) | Pegged but loop thread IDLE in stack + non-loop/foreign thread concentrates |
| R2 | Per-thread CPU concentration | Loop thread ≥ ~80% of process CPU **[P]** | Concentration on saver-pool/aux thread, loop thread near-idle | No concentration — threads flat, all blocked on pool checkout | Concentration on a thread whose stack shows neither LangGraph frames nor pool checkout |
| R3 | Event-loop lag magnitude | Sustained ≥5s **[P]** WITH peg + pending resumes | Sustained lag with NO peg (loop blocked, not burning) | Lag/stall + sentinel timeout + pool saturated, no peg | Extreme lag with everything idle — loop blocked on something that is NOT the pool (read stack) |
| R4 | Stack-dump content on loop thread | LangGraph/LLM processing frames of a resumed turn ON the loop thread (not parked) | Loop thread parked in event-loop idle or pool-acquire; poison-turn frames absent | Loop thread(s) blocked awaiting pool checkout; multiple threads in `_getconn` wait | GC/alloc frames, unexpected IO, third-party code on the hot path |
| R5 | Pending-resume count at boot epoch | `scheduled ≥ 1` AND executing turn stamped `auto_continued_at == epoch`; MANY (≥2 **[P]**) strengthens | `scheduled = 0` AND zero stamps at that epoch while wedge recurs | Any value — H2 is independent of resumes (chronic baseline had NO boot at all) | Resumes present but stack shows execution parked elsewhere |
| R6 | Saver-pool counters at capture | NOT saturated — H1 can wedge with pool healthy (saturation does NOT confirm H1) | Saturated counters alongside sentinel timeouts explain the stall WITHOUT loop execution | `pool_checked_out == pool_size_total` (5/5) AND (`pool_waiting_clients` ≥1 **[P]** OR `pool_checkout_timeouts` ≥1 in window) | Pool healthy + sentinel timeout + loop idle → probe-path or DB-side issue |
| R7 | GC / RSS / memory.pressure | Large-history turn ⇒ big working set is CONSISTENT, not confirmatory | — | — (H2 is connection pathology, not memory) | RSS monotonic growth across ≥2 captures **[P]** + gen2 collections climbing + `memory.pressure some avg` > 0 + no peg + pool healthy (§7.1) |
| R8 | Checkpoint-store row/byte counts | — | — | checkpoints > 50k rows **[P]** or blobs > 8 GB **[P]** + stale `maintenance_runs` checkpoint-cleanup section (observed pathology ~96.5k / ~13 GB) | Modest counts → bloat hypothesis weak; look elsewhere |
| R9 | Stall-ledger shape | SINGLE slow aget ≥250s **[P]** co-incident with peg (S1 shape: ONE 251s aget) | Triple-simultaneous ~181.5s agets completing within ~660ms with NO peg (07:09–07:45Z chronic shape) | Triple/quadruple simultaneous slow agets, no boot in window, no peg; multi-minute sentinel stalls implied by probe+pool counters | No stalls at all while lag is reported → capture/instrumentation artifact or non-checkpoint stall |
| R10 | Boot-epoch correlation (THE separator) | Wedge onset within the boot window of an epoch whose record shows `scheduled ≥ 1`; reproduced across recurrences | Recurrent wedges with NO boot in window | Slow agets under a continuous daemon, no boot (chronic baseline) | — |
| R11 | readyz snapshot shape | Sentinel timeout + peg + pool HEALTHY (loop too starved to service ~0.9s sentinel `aget_tuple`) | Sentinel timeout + pool saturated + no peg (pool explains it without loop starvation) | Sentinel timeout with pool saturated; `queue_max_age=None` + degraded `"queue probe timed out"`; readyz 503 flaps | `database` component failing while checkpoint/queue green (DB_PROBE 0.5s timeouts, readiness.py:45) → DB-side NEW |
| R12 | Effective auto-continue mode at wedge epoch (boot record; O7) | `on` + `scheduled ≥ 1` | `off` (`skipped_kill_switch=1`) or `None`-epoch fast path — nothing re-executed | Any | — |

**Assembly rule (baseline, operationalized):** score the rows. `R1∧R2∧R3∧R4∧R5` → **H1**. `R6∧R8(+R9 chronic shape / R10 no-boot)∧no-peg` → **H2**. Both clusters present in one recurrence → **BOTH** (report which layer led: peg-first ⇒ H1-led; stall-first ⇒ H2-led). No cluster completes and §7 hatch fires → **NEW**. A single REFUTES-H1 cell does not by itself close H1 family-wide — see §5.

---

## 4. H1 Confirmation Protocol

H1: "AutoContinue re-execution of large-history turns starves the asyncio loop." The pattern that PROVES it for one recurrence — ALL of C1–C6 required, C7 strengthening:

- **C1 — Mode+schedule:** the capture's `boot_epoch` E has a boot record (capture item 7) with `effective_mode=on` and `scheduled ≥ 1`. Fallback (legacy log path): the epoch's `[BOOT_CONTINUE]` success lines exist (grep G2, §8) and/or caller summary renders `ContinueResult(scheduled≥1)` (grep G4).
- **C2 — The executing turn IS a boot resume:** capture item 6 lists an executing instance/turn whose Task row carries `auto_continued_at == E` (DB check D1, §8). Remember the stamp gotcha: a completed turn's stamp is cleared (`clear_task_auto_continued`, repository.py:1493) — absence proves nothing; presence at capture time is the strong signal.
- **C3 — Stack:** `thread_stacks` shows the loop thread INSIDE that turn's execution — LangGraph node / LLM-stream / message-processing frames — not parked in epoll/select and not in pool checkout.
- **C4 — CPU:** the same loop thread concentrates ≥ ~80% of process CPU **[P]** (capture item 2), within a sustained ≥95%-of-one-core peg (trigger default).
- **C5 — Lag:** sustained event-loop lag ≥ ~5s **[P]** over the trigger window (capture item 3).
- **C6 — H2 excluded as sole cause:** pool counters (capture item 5) NOT saturated at capture, or demonstrably healthy at lag onset — otherwise BOTH must be considered.
- **C7 (strengthening):** the executing turn's history is large — msg_count ≥ ~100 **[P]** (S1 showed up to 144).

Interpretation: C1–C6 together = the poison turn's own Python frames are burning the core ON the loop thread while the loop cannot service the readiness sentinel — mechanism demonstrated end-to-end. Record `verdict=H1` for the recurrence, cite capture path + epoch E + the four line-anchors (stack, cpu, lag, boot record).

---

## 5. H1 Refutation Protocol

Any ONE of K1–K5 KILLS H1 **for that recurrence** (write `verdict=REFUTES-H1` + which killer). H1 as a family theory survives only if every recurrence's refutation checks come back negative.

- **K1 — Nothing to re-execute:** wedge recurs at epoch E with boot record `scheduled = 0` AND zero Task rows stamped `auto_continued_at == E` (D1). No resumes ⇒ no re-execution ⇒ not H1.
- **K2 — Mode OFF:** boot record `effective_mode=off` at E (`skipped_kill_switch=1`). Fallback: `[BOOT_CONTINUE] OFF:` line for that boot (grep G3). Nothing was resumed by design.
- **K3 — Loop thread is not the burner:** stack shows the loop thread parked (event-loop idle / pool-checkout wait) while a NON-loop thread concentrates CPU (capture items 1+2 disagree with H1's mechanism).
- **K4 — No peg:** sustained lag/stall WITHOUT the CPU peg (loop is blocked, not busy) — execution-starvation mechanism absent; switch to H2/new analysis.
- **K5 — Chronic shape reproduces:** wedge recurs under a continuous daemon with NO boot at all in the window (the 19:42–19:47Z 10-21 baseline shape) — boot-resume causality cannot apply.

Correlation discipline for K1/K2: key on `boot_epoch`, never wall-clock proximity — the 05:24:18Z OFF / 05:26:33Z ON lever flip 2 minutes apart (§1.1) is the standing counterexample. And read the OFF boot's PAIR of lines: the OFF emit (auto_continue_boot_pass.py:324-327) plus the caller summary rendering `ContinueResult(... skipped_kill_switch=1 ...)` (daemon/api.py:2659).

---

## 6. H2 Signature Section

**Mechanism under test:** checkpoint-store non-reclamation (baseline: ~47h without cleanup, ~96.5k rows / ~13GB) → saver operations slow → 5-connection saver pool (`daemon/persistence.py:273-294`, min1/max5, hard-coded) saturates → the readyz sentinel `aget_tuple` (readiness.py:580-743, ~1.0s budget, loop leg waits ~0.9s) cannot be serviced → readyz 503 flaps; queue probe (2.0s, readiness.py:59) times out → `queue_max_age=None` + degraded. **Defining negative: NO CPU peg.**

**Signature checklist:**

| Check | H2-expected value |
|-------|-------------------|
| Pool counters (capture 5) | `pool_checked_out == pool_size_total` (5/5); `pool_waiting_clients` ≥ 1 **[P]**; `pool_checkout_timeouts` ≥ 1 **[P]** in window |
| readyz (capture 10) | `checkpoint_saver` timeout with the pool saturated; `queue_freshness=None` + reason `"queue probe timed out"` (NOT the FRESH-empty `None`, readiness.py:289-292) |
| CPU / thread profile (captures 1-3) | No peg; loop thread parked or low-CPU; lag/stall present without burn |
| Store census (capture 8) | Elevated rows/bytes (PROPOSED gate: >50k rows or >8GB blobs; observed ~96.5k / ~13GB) + `maintenance_runs` shows no recent `section=checkpoint-cleanup` run (47h miss precedent) |
| Boot correlation (O11) | Slow agets under a continuous daemon, NO boot in window (chronic) — OR any boot window where pool counters explain the stall without peg |
| Stall ledger (capture 9) | **Chronic shape A:** triple-simultaneous ~181.5s `op=aget` completing within ~660ms of each other (07:09–07:45Z 10-22). **Shape B (single, ambiguous):** one 251s / 304s / 557s `op=aget` — on GET /messages path (persistence.py:465); a single slow aget alongside peg belongs to the S1/H1 pattern instead (§4/§5) |
| Limitations (record in the verdict) | `aget_tuple` untimed in-tree — the sentinel probe is its only observable; histogram buckets cap at 10.0s (checkpoint_metrics.py:61) so multi-minute stalls appear only as count/sum drift; the `'AsyncConnectionPool.check()'` reason string is stale cosmetic (readiness.py:337-339) |

**Mixed/BOTH:** pool saturated AND a boot resume executing with peg — record BOTH; order the layers by onset (capture lag window vs pool-counter window) as H1-led or H2-led.

---

## 7. "Points Somewhere New" Escape Hatch

Fire when no H1/H2 cluster completes (§3 assembly rule). Each shape below: how it looks + the ONE-OFF commands that grab extra evidence. These are responder-side diagnostics — **none of them scope-creeps the instrument** (W1's capture set stays as planned).

### 7.1 GC / memory-pressure shape
**Looks like:** RSS monotonic growth across ≥2 captures **[P]** + `gc.get_stats()` gen2/gen1 collection counts climbing + `memory.pressure` (`some` or `full` avg) > 0 + NO peg + pool healthy. Candidate substrate: blob-table bloat — live facts: blob table ~14GB live, ~1.5MB avg/blob, post-relief row counts 8754/9793/16785.
**One-off evidence:**
```bash
cat /sys/fs/cgroup/system.slice/ensemble-main.service/memory.pressure   # cgroup line (path per host layout)
grep -E 'VmRSS|VmHWM' /proc/<pid>/status ; cat /proc/<pid>/smaps_rollup | head -20
grep -E '^ru_maxrss' /proc/<pid>/status 2>/dev/null || true            # ru_maxrss rides wait/status; use resource in-process if needed
```
```sql
SELECT pg_size_pretty(pg_total_relation_size('checkpoint_blobs'));
SELECT count(*) FROM checkpoint_blobs;
```

### 7.2 Disk / IO shape
**Looks like:** loops parked in D-state/IO wait; high `write_bytes` deltas; stall ledger slow across ALL ops.
```bash
cat /proc/<pid>/io            # read_bytes/write_bytes deltas across two samples
iostat -x 1 5                 # device util/await on the PG volume
ps -eo pid,stat,wchan:32,cmd | grep -E '<pid>|postgres' | head
```

### 7.3 DB-side shape
**Looks like:** `database` readyz component failing (DB_PROBE_TIMEOUT_S=0.5, readiness.py:45) while checkpoint/queue green; or `pg_stat_activity` shows lock/IO waits on checkpoint tables.
```sql
SELECT pid, state, wait_event_type, wait_event, now()-query_start AS dur,
       left(query, 120) AS query
FROM pg_stat_activity
WHERE datname = current_database() AND state <> 'idle'
ORDER BY dur DESC NULLS LAST LIMIT 20;
```

### 7.4 Foreign / host shape
**Looks like:** peg attributable to a thread with neither graph nor pool frames (capture 1+2), or system-wide CPU from another process/cgroup.
```bash
top -H -b -n 1 -p <pid> | head -30      # per-thread CPU inside the daemon
systemd-cgtop -1                         # sibling cgroups stealing the core
```

### 7.5 Instrumentation-artifact shape
**Looks like:** lag reported but stacks idle, pool healthy, no peg, no store growth — suspect the capture path itself; file a defect against W1's instrument with the capture attached. Do not silently reinterpret as H1/H2.

---

## 8. Correlation Procedure (step-by-step runbook)

All paths below are on the affected daemon's log store; capture file + metadata row come from the instrument (W1 defines exact paths — substitute when W1 lands). Read-only throughout.

**Step 1 — Locate the capture.** From the metadata row read: `captured_at`, `trigger_reason` (`sustained_cpu | sustained_loop_lag | thread_concentration`), and `boot_epoch` (naive-UTC). Everything keys on `boot_epoch`.

**Step 2 — Read the boot record for that epoch (PRIMARY once live).** Query the W1 read-back surface for `boot_epoch == E`: `effective_mode`, `raw_env_value`, all `ContinueResult` counters, `decided_at`. Effective mode is surfaced FROM the running process — never re-derived from env files (precedence `.env` > unit `Environment=` > `set-environment` > default; set-environment is volatile).
*Legacy fallback (pre-instrument boots or if the record is absent):* steps 3–4.

**Step 3 — Pull `[BOOT_CONTINUE]` lines for the epoch (grep G1/G2/G3/G4):**
```bash
# G1: every boot-continue line in the window (main pass + orphan sweep)
grep -nF '[BOOT_CONTINUE]' ensemble.log
# G2: success lines with their epoch (ids printed as 8-char prefixes; epoch via isoformat())
grep -E '\[BOOT_CONTINUE\] instance=[0-9a-f]{8} work_id=[0-9a-f]{8} epoch=' ensemble.log
#    then filter to the epoch (isoformat uses 'T'):
grep -F 'epoch=2026-10-22T05:26' ensemble.log
# G3: OFF boots (kill-switch fast path, auto_continue_boot_pass.py:324-327)
grep -F '[BOOT_CONTINUE] OFF:' ensemble.log
# G4: caller summary rendering ContinueResult (daemon/api.py:2659)
grep -F 'AutoContinue boot pass:' ensemble.log
# G5: orphan-sweep always-emitted summary (auto_continue_boot_pass.py:866-879)
grep -F '[BOOT_CONTINUE] orphan-sweep summary:' ensemble.log
```
Read G3 and G4 as a PAIR for OFF boots (`... skipped_kill_switch=1`).

**Step 4 — Slice the daemon log to the epoch window:** boot banner → `[BOOT_CONTINUE]` lines → first stall-ledger lines after them. Note the boot's epoch string from the banner/record; correlate consecutive boots by epoch, never by wall-clock proximity (lever-flip precedent, §1.1).

**Step 5 — Task stamps (DB check D1):**
```sql
-- D1: which tasks were resumed by epoch E (stamp presence = resumed and NOT yet completed)
SELECT id, instance_id, auto_continued_at FROM task
WHERE auto_continued_at = :epoch;   -- bind naive-UTC datetime, matching the epoch dtype
-- gotcha: cleared on success (clear_task_auto_continued, repository.py:1493);
-- zero rows does NOT prove "no resumes" — cross-check G2/G4 counts.
```

**Step 6 — Stall-ledger tail:** already inside the capture (item 9); extend from the log when needed:
```bash
# G6: all slow saver ops (format verified: checkpoint_perf.py:103-109)
grep -E '\[CheckpointPerf\] op=(aget|aput|adelete) ' ensemble.log
# G7: only the heavy ones (latency_ms >= 250000 ~ S1's 251s class)
grep -E '\[CheckpointPerf\] op=' ensemble.log | awk '{for(i=1;i<=NF;i++) if($i~/^latency_ms=/){split($i,a,"="); if(a[2]+0>=250000) print}}'
# G8: GET /messages slow-path line (persistence.py:465 aget source)
grep -F '[/Messages] ' ensemble.log
```

**Step 7 — readyz history:** capture-time snapshot (item 10) + any external monitor; classify `None` readings by their degraded reason (§2 O10).

**Step 8 — Assemble the verdict:** score §3 rows → H1 / REFUTES-H1 / H2 / BOTH / NEW. Record: capture path, epoch E, the decisive rows, and any [P] threshold that was load-bearing.

---

## 9. Data Dictionary — Capture Payload Fields

Canonical names shared by this matrix and the W1 implementation. Instrument items 1–10 in order.

| Field | Instrument item | Source | Meaning / notes |
|-------|-----------------|--------|-----------------|
| `thread_stacks` | 1 | faulthandler-style all-thread self-dump, taken from the external watchdog thread | Frames per thread keyed by `native_id` + thread name. Loop thread = the thread running the asyncio loop (match `native_id` to the main thread). H1's key evidence (§4 C3). |
| `thread_cpu_attribution` | 2 | `/proc/self/task/<tid>/stat` deltas over the trigger window, mapped to Python threads via `threading.native_id` | Per thread: `native_id`, `name`, `cpu_delta_s`, `share_pct`; plus derived `loop_thread_native_id`. Discriminates loop-thread concentration (R2). |
| `loop_lag_histogram` | 3 | rolling histogram of `call_soon_threadsafe` heartbeat lag over the trigger window | Bucketed samples + p50/p95/max. Trigger default ≥ ~5s. Note: distinct from `checkpoint_metrics.py`'s saver-op histogram (buckets cap 10.0s — that one measures saver ops, not loop lag). |
| `gc_rss_snapshot` | 4 | `gc.get_stats()` + cheap object counts + `/proc/self/status` `VmRSS` + `ru_maxrss` + cgroup `memory.pressure` line | Memory-pressure interface wired on the ensemble-main cgroup. Feeds §7.1 only; neutral for H1/H2 otherwise. |
| `saver_pool_counters` | 5 | `psycopg_pool.AsyncConnectionPool.get_stats()` — sync, loop-free, touches no connections (pool: `daemon/persistence.py:273-294` min1/max5; reachable `manager.checkpointer.raw_saver.conn`) | Canonical descriptive fields: `pool_checked_out`, `pool_size_total`, `pool_waiting_clients`, `pool_checkout_timeouts`. `get_stats()` field names UNVERIFIED against pinned psycopg_pool 3.3.1 — the implementation maps real field names to these canonicals at wiring time (binding T4: these counters DO NOT exist today; `queue_max_age` renders `None` exactly when the probe path times out — see O10). |
| `executing_turns_resume_history` | 6 | in-process registry + Task rows | Per executing turn: `instance_id`, `work_id`/thread id, `msg_count`, resume chain (which boot scheduled it), `boot_epoch`. Joins to D1 stamps. |
| `boot_autocontinue_record` | 7 | W1's in-process boot record (set in the pass INCLUDING the OFF early-return branch), exposed via read-back surface | `effective_mode` (what the boot pass actually read — NEVER re-derived precedence), `raw_env_value`, `boot_epoch`, `ContinueResult` counters (`candidates, scheduled, skipped_no_checkpoint, skipped_resume_refused, skipped_kill_switch, skipped_no_boot_epoch, skipped_multi_task_instance, errors, duration_seconds`), `decided_at`. PRIMARY structured source; `[BOOT_CONTINUE]` log lines are legacy/fallback (§1.1). |
| `checkpoint_store_census` | 8 | cheap `count(*)` or `reltuples` estimate (+ optional `n_live_tup` drift only) | `checkpoints_rows`, `checkpoint_blobs_rows`, `checkpoint_blobs_bytes`. Query-plan capture is OUT of scope (addendum 2). Observed pathology reference: ~96.5k rows / ~13GB. |
| `stall_ledger_tail` | 9 | tail of `daemon.checkpoint_perf` log lines | Lines of form `[CheckpointPerf] op=<name> thread=<8char> latency_ms=<int> bytes=<int> deleted=<int>` (checkpoint_perf.py:103-109). `aget_tuple` untimed — sentinel stall visibility comes from O10 + `saver_pool_counters`, not this ledger. |
| `readyz_snapshot` | 10 | the cached `ReadinessComposite` served by `/readyz` (daemon/api.py:4630) | `database`, `queue_freshness`, `checkpoint_saver` booleans + degraded reasons + `queue_max_age_seconds` + `inflight_turns`. `checkpoint_saver` budget `CHECKPOINT_SAVER_PROBE_TIMEOUT_S=1.0` (readiness.py:54); queue 2.0s (:59); DB 0.5s (:45). The `'AsyncConnectionPool.check()'` reason text is stale cosmetic (readiness.py:337-339). |
| `trigger_reason` | metadata | watchdog | One of `sustained_cpu` (≥ ~95% of one core), `sustained_loop_lag` (≥ ~5s), `thread_concentration`. Final defaults owned by W1. |
| `captured_at` / `boot_epoch` | metadata | watchdog / `get_boot_epoch()` (boot_epoch.py:70-76) | `captured_at` wall-clock; `boot_epoch` = the O(1) process-global naive-UTC epoch (`None` is a real state). `boot_epoch` is THE correlation key — naive-UTC discipline everywhere (never `datetime.now(timezone.utc)` comparisons). |

---

## Appendix A: diagnostics-lane live baseline (to be appended)

*RESERVED — the diagnostics lane appends its live numbers here after deployment. Do not edit this placeholder from other lanes.*

(Expected content: measured trigger-window baselines for each §3 row; calibrated values replacing PROPOSED [P] thresholds where evidence supports; any additional baseline shapes observed on live traffic.)
