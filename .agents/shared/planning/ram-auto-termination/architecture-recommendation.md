# Architecture Recommendation: RAM-Based Auto-Termination — Phase 1 Design Decision Gate

Date: 2026-11-10 (aggregation completed 20:5x UTC) · **AMENDED 2026-11-10T23:5xZ (approver-iter-001) · AMENDED 2026-11-11T0x:xxZ (approver-iter-002 — FINAL before escalation)**
Architect instance: c15bdc7d (Architecture Controller — dispatch + aggregation only; no direct design)
Amendment instances: W-A `a2bdd4a3` (resilience-design) · W-B `b152ca58` (data-flow-design) · W-C `faba379e` (data-flow-design, iteration 002); fan-in adjudicated by the architect, rulings in §14 + §16
Analysis instances: W1 `2ead7080` (data-flow-design, replacement for wedged `ffc1b0ef`) · W2 `acf4bd9d` (resilience-design) · W3 `9796b69c` (security-design) · W4 `4b079967` (system-decomposition) · W5 `3b7e355b` (structural-design) · COUNCIL governor `0153da35` + councilors `96da4747` (agentic) / `b9471c96` (coding), skill `trade-off-analysis`
Base: worktree `/home/nea/ensemble-src-wt-ram-auto-termination` @ `b6cdb3cb9`, branch `feature/ram-auto-termination`, base `origin/latest@0efdf106f`
Status: **COMPLETE — 10/10 DQ rulings + R1 + sequencing verdict. Binding. AMENDED (approver iterations 001 + 002: 001's 7 blockers + notes ruled in §14; 002's B1 + B3 + notes ruled in §16).**

> **PASS MARKER — `approver-iter-001 amendment` + `approver-iter-002 amendment`.** This document is the **single** contract for the feature (Phase 2–7 build against it; where a phase doc and this document disagree, **this document wins**). Iteration 001 changed the sections listed in §14.3; iteration 002 changed the sections listed in §16.4. **Two leader rulings are binding and propagated** (Q2=(i) leaderless-tree semantics; verdict-publication ordering). No phase doc or test-plan was touched by either pass — the planner folds.

---

## 0. Executive summary

The commissioned feature — kill an agent-spawned bash/proc process tree that exceeds a RAM ceiling and return a typed error — is **architecturally sound and should proceed**, with **one commissioned number overturned**, **one layering correction**, and **a set of plan anchors corrected against the code**.

Three findings dominate:

1. **R1's stated premise is false, and the real situation is worse than the plan assumed.** The live unit has **no memory cap at all** (`MemoryMax=infinity`, `MemoryHigh=infinity`; verified via `systemctl show` and by the absence of any `Memory*` directive in the unit file and its drop-ins). The 7G/8G figures the plan treats as shipped are a **never-applied runbook recommendation** (`resource-cost-control/phase8-plan.md` §8.1 — a document whose own text says *"expect MemoryMax=infinity"*). The Oct-30 motivating incidents were **kernel OOM kills** (`Failed with result 'oom-kill'`, unit peak 7.3 GiB), not cgroup-cap kills. Consequence: the commissioned **8 GiB default is unshippable on this 11.9 GiB host** — it fires at-or-after daemon death in both measured incidents. **Ship 3 GiB.**
2. **The failure that actually happened is only partially coverable by this feature.** The council's measured decomposition of the Oct-30 incidents shows the killing memory was largely **orphaned children of a dead daemon incarnation** — invisible to any per-command guard, because the guard's registry dies with the daemon. The guard is the **inner, preventive** layer (in-flight trees); the **unit cap is the outer layer that covers the orphan class**; the kernel global OOM is today the *only* layer actually running. The operator memo is therefore **load-bearing, not optional hardening**.
3. **Summed RSS alone is unsafe as a kill metric — measured, not argued.** A fork-without-exec tree over-counts by **≈ the member count** (measured **8.9×** on a 9-member tree: 2,106 MiB summed RSS vs 237 MiB summed PSS vs ~250 MiB true footprint). With a 3 GiB ceiling, a 12-worker multiprocessing pool touching 250 MiB each would be **false-killed at a quarter of the ceiling**. The trigger/confirm hybrid (cheap RSS trigger per tick, one-shot PSS confirm at breach, fail-open to kill) is **mandatory**, and the PSS confirm is affordable exactly where it is needed (~35 ms median at a 3 GiB tree; p95 130–146 ms; unaffordable only above ~20 GiB of tree total — beyond this host's physical RAM).

**Amendment pass 1 (approver iteration 001) — five structural corrections, all binding:**

4. **The over-count re-arm is now BOUNDED** (§3.4, §4.1a): the unbounded `ceiling + shared_extra` rule made the effective kill threshold ≈4.82 GiB and the §3.4 unit-cap floor ≈9.82 GiB > the 8.88 GiB box ceiling ⇒ **no feasible unit cap existed at all** on the re-arm path, and *"the guard always completes before a unit kill"* was false. The bound restores a feasible unit cap and the L110 claim is replaced with a **rate-conditional truth** (§3.4).
5. **The durable event now has an owner, an emitter and a bridge** (§7, §4.5): the guard never imports the bus — an **injected emitter callable** (stdlib-pure guard) is wired at the composition root; the thread→loop hop is **non-blocking** and uses the **hardened manager accessor** (never `MainLoopBridge`, whose loop is set only inside `PoolOrchestrator.setup()` — `pool_orchestrator.py:199-201, :235` — and is therefore `None` on a no-worker-pool boot).
6. **The reason vocabulary is complete** (§4.2): **16 codes** (9 kept/renamed + 7 added — the iteration-001 text said "18", an arithmetic slip corrected in iteration 002, §16); **P8 is a post-kill verification step, not a pre-kill predicate** (the frozen list mislabelled it), so the pre-kill gate is **P0–P7**.
7. **`RamGuardConfig` is wired to the root `Config`** (§4.6): a root field + a non-pydantic bare-switch parser; out-of-range values **abort boot** and the rung **is assertable today** (the Phase-5 "untestable disclosure" escape is deleted).
8. **Verdict publication is ordered before `mark_exit` / registry release** (§4.3.1) — the leader's ruling; Phase 3 T5's acceptance becomes achievable as designed.

**Amendment pass 2 (approver iteration 002) — two structural corrections, all binding:**

9. **The guard is now TWO dedicated daemon threads** (§2 DQ2, §16.1): ONE **tick thread** (registration, the shared scan, the RSS trigger, the handoff — never PSS, never signals) + ONE **execution-lane thread** (the PSS confirm, the kill ladder, the verdict publication, the emitter call — the only executor of the kill path). A stalled `smaps_rollup` read can therefore **never delay a tick** (test-plan N8 / phase6 T20 become satisfiable and their assertion shapes are pinned in §15 B18–B19). The house precedent is DQ2's own cited family: `resource_monitor` runs exactly this split (sampler + flusher, `facade.py:9-12`: *"BOTH threads are `threading.Thread(daemon=True)` … Boot failure of either thread DEGRADES the subsystem"*).
10. **The vocabulary count is corrected to the enumeration** (§16.2): every enumeration has **16** codes; the "18" was a propagated arithmetic slip (W-A's draft header, not recounted at iteration 001). The enumeration is authoritative.

Everything below is binding on Phases 2–7 except where explicitly marked advisory.

---

## 1. Evidence base (all verified on-host / in-worktree, 2026-11-10)

| # | Fact | Source |
|---|---|---|
| E1 | `MemoryMax=infinity`, `MemoryHigh=infinity`, `MemoryCurrent` 2.69→3.31 GiB across the analysis window | `systemctl show ensemble-main.service` |
| E2 | No `Memory*` directive anywhere in the unit or its drop-ins (`10-zero-env-reset.conf`, `20-core-capture.conf`) | `/etc/systemd/system/ensemble-main.service.d/` |
| E3 | Oct-30 incidents: *"A process of this unit has been killed by the OOM killer"* → *"Failed with result 'oom-kill'"* → *"7.3G memory peak, 203.2M memory swap peak"* (10:42→10:43 and 11:13). A cgroup-cap kill reports `'memory-limit'`, not `'oom-kill'` | `journalctl -u ensemble-main.service` |
| E4 | Host: 11,952 MiB RAM (~11.67 GiB), 8,191 MiB swap; co-residents PG 0.28 + demo 0.4 + OD 0.17 + OS ≈0.94 ⇒ ~1.79 GiB "others" | `free -m`, ps |
| E5 | The unit file is a **generated artifact** (adopt-unit.sh pipeline); `adopt-unit.sh` never touches `*.service.d/` ⇒ a drop-in survives regeneration | repo + RCA docs |
| E6 | Measured metric costs (this host, 250 pids): `/proc/<pid>/status` 0.056 ms/member; `smaps_rollup` 0.120 ms/member, cost ≈ `0.10 ms + 0.0115 ms/MiB × RSS`; full-table scan 9.49 ms median / 13.89 ms p95; fork-tree RSS over-count factor 8.9× (N≈9) | W1-r2 bounded measurements |
| E7 | 21-day prod aggregates (read-only): max legitimate live-sampled child footprint **364.5 MiB**; exit-time floor 64.4 MiB | council, coding councilor |
| E8 | Incident decomposition: during the fast-case OOM window the daemon bucket's RSS *fell* 1,325→151 MiB (restart) while cgroup anon climbed 1,411→6,647 MiB with tracked children ≈3 MiB — the killing memory was **untracked/orphaned**, not a live registered command | council, coding councilor |
| **E9** | **Iteration-001 anchors (architect-verified):** `event_bus.create_event` is `async` + `await asyncio.to_thread(...)` (`event_bus.py:155-181`); house bridge precedent `asyncio.run_coroutine_threadsafe(...).result(timeout=8.0)` at `report_delivery_recovery.py:1499-1507`; `manager._get_event_loop()` = hardened accessor, raises `RuntimeError` when no live loop, **never** creates one (`manager.py:3097-3195`); `manager._loop` set at `manager.py:4425`; `MainLoopBridge.set_loop(...)` only inside `PoolOrchestrator.setup()` which **returns early** when `USE_WORKER_POOL` is false/0/no (`pool_orchestrator.py:199-201, :235`); `self._event_bus` is private (`manager.py:1945`); bash success arm `mark_exit` at `bash.py:1110-1123` + `unregister` at `:1132-1141`; `pid_registry.mark_exit` raises on an unknown kind (`:409-413`) and **deletes** the live row (`:423`); `proc_tools._drain_exit_code` ladder at `:861-875`; `proc_run` returns `process_id, None` at `proc_tools.py:1307`; `Config(**config_dict)` at `config.py:8795`; the YAML-literal-beats-env trap at `config.py:911-916`; the boot-abort precedent at `api.py:2266-2278` | architect re-reads in the worktree |
| **E10** | **Iteration-002 anchors (architect-verified, 2026-11-11):** `EventKind` has **16** members (`repositories/event/models.py:12-55`) while the SQLite `event.kind` CHECK lists only **8** (`migrations/versions/20260412_000002_create_event_table.sql:13-20`) and the SQLModel column carries **no** CHECK (`models.py:74`); the house `/proc` reader is a plain blocking `path.read_text()` with **no timeout** (`resource_monitor/proc_readers.py:127-133`) ⇒ the 250 ms confirm deadline is a **post-hoc elapsed check** and a never-returning read wedges whichever thread ran it; the reaper's grace slice `_GRACE_POLL_INTERVAL_SECONDS = 0.25` (`bash_spawn_reaper.py:644`; default grace 5.0 s at `:120`) — copied verbatim to a 250 ms grace it yields **one slice and no early exit** (the full window always burns); `resource_monitor/facade.py:9-12`: *"BOTH threads are `threading.Thread(daemon=True)` — NEVER asyncio tasks. Boot failure of either thread DEGRADES the subsystem"*; `metrics_gate.py:335-341`: *"A read that blocks … stalls THIS thread only"*; the house **detached-join** shutdown form at `api.py:4144-4152`; the reaper's slice-probed grace shape (arithmetic convergence, early exit on ESRCH) at `bash_spawn_reaper.py:647-675` | architect re-reads in the worktree |

### 1.1 Plan claims falsified against the code (Phases 2–7 must not build on them)

| # | Plan claim | Reality (verified) |
|---|---|---|
| P1 | "The live systemd unit ships `MemoryMax=8G`" (R1 premise; Risks #1) | **No cap exists.** See E1–E3. |
| P2 | `pid_registry` closed set at `daemon/repositories/pid_registry/models.py:121-135` | Path **does not exist**. Real site: `daemon/services/resource_monitor/pid_registry.py:120-138` (`mark_exit` raises `ValueError` on unknown kinds, `:409-413`). |
| P3 | "the reaper's PID-ownership verification … `proc_tools.py:1039-1052`" | Two mechanisms conflated. The reaper's real gate is `verify_and_kill` (`bash_spawn_reaper.py:735-900`, 5-rule, DB-row-backed, fail-closed, starttime gate `:851-857`); `proc_tools.py:1037-1053` is a *different*, **fail-open** 1-signal environ check. |
| P4 | "the bash lane's ladder is gated by the ownership check" | The bash lane has **no ownership gate at all** (`_kill_process` `bash.py:697-745` sends `killpg` with no environ/starttime check). |
| P5 | "pgid captured at spawn, never re-derived" (both lanes) | True for bash (`:954-956`, reused `:1067/:1189`). **False for proc**: no pgid is captured; it is re-derived at kill time — `os.killpg(os.getpgid(proc.pid), sig)` (`proc_tools.py:1058`, `:1280`) — exactly the TOCTOU bash already fixed. |
| P6 | Risk #7: check `tests/test_chart_tools_legacy_error_contract.py` before committing the envelope | That test pins **chart tools only** (58 lines; imports neither bash nor proc) — **inapplicable**. The real pin surface is `proc_status` substring assertions (`tests/tools/test_proc_tools.py:240…1831`, **13 sites in that file** — see §6). |
| P7 | "`proc_readers.read_pss_rollup` exists, so the reader exists" (DQ1 lean) | It is **self-only** — the `proc_root` is injectable, but the *pid* is hardcoded to `self` (`/proc/self/smaps_rollup`, `proc_readers.py:769`). A new per-pid reader is required. |
| P8 | Phase 7: durable verdict ⇒ "migration-backed vocabulary extension" of `reap_reason` | `reap_reason` is plain `TEXT` with **no CHECK constraint** — a vocabulary extension is code-only. (Moot: DQ10 rules no durable verdict.) |
| P9 | "`list_group_members` is the discovery primitive (fail-closed, tested)" | It is correct but **not injectable** (hardcodes `/proc`) ⇒ cannot serve as the hermetic seam; and per-spawn invocation costs 9.49 ms/scan — N spawns overrun a 200 ms tick (see DQ3). |

---

## 2. DQ1–DQ10 — binding rulings

| DQ | Ruling | Rejected | Key evidence |
|---|---|---|---|
| **DQ1** — tree RAM metric | **(e) HYBRID, and the confirm is MANDATORY**: trigger = `summed_rss_bytes` (Σ `VmRSS` per member, kB×1024, compared per tick); decision = one-shot **`summed_pss_bytes` confirm** at breach (re-enumerate + Σ `Pss` from `smaps_rollup`), 250 ms deadline, **fail-open ⇒ kill** on confirm-failure/timeout. Overcounted trees (PSS < ceiling) enter `OVERCOUNTED` and re-arm at **`ceiling + min(shared_extra_bytes, RAM_GUARD_REARM_MAX_EXTRA_BYTES)`** with a **growth governor** — see §4.1a (AMENDED, approver blocker 2: the previous unbounded `ceiling + shared_extra` rule is **withdrawn**). The confirm executes on the **execution-lane thread** (DQ2, iteration 002). | pure RSS (false kill at factor ≈N, measured 8.9×); pure PSS per tick (17 % duty at 3 GiB/200 ms, scales with the tree); per-spawn cgroup (no delegation, `systemd-run` unavailable, no `setrlimit` in `daemon/`); unit cgroup (cannot attribute) | E6; W1-r2 §1; §4.1a |
| **DQ2** — loop home + cadence | **(b) TWO dedicated daemon threads — ONE TICK THREAD and ONE EXECUTION-LANE THREAD — started/stopped together on the FastAPI lifespan** (`app.state.ram_guard` owns both; OFF ⇒ **neither thread exists**). **AMENDED (approver iteration 002, blocker B3 — supersedes the iteration-001 "ONE dedicated daemon thread" wording).** The **tick thread** owns registration, the ONE shared `/proc` scan, the RSS trigger, the peak/counter updates and the breach handoff; it **never** reads PSS, **never** signals, **never** sleeps beyond the poll. The **execution-lane thread** is the **only executor of the kill path**: the one-shot PSS confirm (250 ms deadline, fail-open ⇒ kill), then `ram_kill_ladder` (TERM → slice-probed grace → KILL → the P8 probe), then the verdict publication and the emitter call. The lane never samples and the tick never kills, so **a stalled confirm can never delay a tick**. Each thread has its own `while not stop` restart loop and its own `try/except` (counter, never log). Handoff: the claim token is taken under the registry lock **by the tick thread at observation**; the spawn joins a **bounded pending set over the registry** (`RAM_GUARD_PENDING_MAX = 2`); if the pending set is full, the newest breach is **not claimed** (its peak and `ram_breaches_detected` still update; the state stays WATCHING) and is re-offered next tick — **no confirm is ever skipped**, so `pss_confirm_unavailable` is produced only by a genuine confirm failure/timeout. The ladder's grace is slice-probed with the guard's OWN slice constant (`RAM_GUARD_GRACE_POLL_SLICE_SECONDS = 0.025` — 10 slices at the 250 ms default, early exit live; the reaper's 0.25 s slice yields one slice and no early exit at a 250 ms grace — E10), **reimplemented in `ram_guard/ladder.py`** (the `bash_spawn_reaper` import stays forbidden). Poll default **200 ms**, bounds **[50, 2000] ms**. Lazy first-registration start **rejected** (forks the health contract). Precedent: `resource_monitor/facade.py:9-12` (one sampler thread + one flusher thread, degrade-not-crash); `metrics_gate.py:335-341` (a blocked read stalls that thread only); shutdown = `.stop()` + **detached join of both threads** (the house form at `api.py:4144-4152`). | in-execution asyncio task (a starved loop cannot run it); riding the 30 s sampler (100× too slow); **a supervisor thread** (watches/restarts another thread — two failure modes; the house accepts none for wedge-capture, `wedge_capture/watchdog.py:22-23`: exactly ONE capture executor so no second concurrent-writer class can exist); **the lane is an EXECUTOR, not a supervisor** — it never watches the tick and the tick never watches it; the tick thread never signals and never writes a terminal state, so the claim token still has a single owner | W2, W4; `wedge_capture/watchdog.py:1-49`; E10; §16.1 |
| **DQ3** — tree discovery | **(c) HYBRID from ONE shared `/proc` scan per tick**: `scan_proc(proc_root)` builds `pid → (state, ppid, pgrp)` once per tick; membership = **group relation (`pgrp == G`) ∪ descendant closure from the leader (bounded depth/node caps)**. Zombies + kernel threads excluded. Enumeration failure ⇒ **drop the sample** (peak keeps its value, no breach evaluated, `enumeration_failures++`) — never kill on no-evidence; the unit cap is the coarse net for "could not measure". Per-spawn scans **rejected** (9.49 ms × N overruns the tick). | group-only (misses setsid escapees ⇒ under-reports); ppid-only (misses orphans re-parented out of the closure but still in the group); reusing `list_group_members` verbatim (not injectable, per-spawn cost) | E6; W1-r2 §2 |
| **DQ4** — kill ladder + vocabulary | **(d) NEW sync short-grace ladder** shared by BOTH lanes: `ram_kill_ladder(pgid, leader_start_time, grace_seconds) -> (status, reason)`; TERM → slice-probed grace (the reaper's `_grace_wait_for_death` shape, at the guard's own 25 ms slice) → KILL; zombie-aware skip; `os.killpg` from the **execution-lane thread** (the only thread that ever signals — iteration 002). **Grace default 250 ms, bounds [0, 2 000] ms, a SEPARATE knob** from the 5 s timeout grace. Identity gate: **captured pgid + leader `starttime` re-verification** (fail-closed), environ check demoted to secondary. **AMENDED (leader ruling Q2=(i) + approver blocker 1):** the watch stays alive until the group member count reaches 0; identity = captured pgid ∧ surviving-member group membership (the identity re-anchors on the surviving group members when the leader is gone); starttime gate applies to the leader only while alive; the watch is released only at group member count 0 — `already-dead:group-empty` fires **only at `group_member_count == 0`**; the complete 16-code vocabulary is in §4.2. `exit_kind="ram_killed"` (constant, never a literal — `mark_exit` raises); proc gets `ram_killed: bool` on `ProcessInfo`, **status stays `"killed"`**; `reap_reason` untouched. | reusing `_kill_process` (async + 5 s grace: at 3 GiB/s that is 15 GiB — the kernel OOM wins; C_max collapses to 0.68 GiB); routing through `_attempt_kill_signal` (re-derives pgid — P5; bash has no tracking id so its gate silently fail-opens); a new proc status value (breaks 3 test pins + the post-kill branch) | W2 §DQ4; council constraint #1; §4.2 |
| **DQ5** — substrate + seams | **(a) RAM-only in-process map**, keyed by `tracking_id`, both lanes. **The proc lane registers with the guard only — NO new durable write path.** The guard writes nothing durable; the bash `bash_spawn_registry` row is left **OPEN** on a RAM kill so the reaper's env-stamp escapee net stays armed (it sweeps open rows only) and closes it via the existing `ram-tier-dedup` path at terminal cleanup. (c) stays illegal (fence); **no lift routed**. | durable extension (redundant with the event; a proc write path for zero enforcement value; closing the row silently disarms the escapee net) | W4; W3 critical interaction |
| **DQ6** — typed error | **Mirror `_format_secret_envelope`; the envelope IS the entire bash return string**; numeric fields are **siblings before `message`**; `code="ram_limit_exceeded"`, `surface="bash.exec"\|"proc.exec"`. **Proc placement differs structurally**: `proc_run` returns before any breach can exist — the proc verdict rides `proc_status` as a `ram_breach:` line + a flat `ram_killed: true` (added as lines, never replacing the block). **Consumer gate: SATISFIED by evidence — AMENDED:** the "22 consumers" figure is **withdrawn** (no such list exists); §6 now carries an **enumerated, file:line-anchored census** and the gate artifact is owned by **Phase 2** (Phase 5's duplicate claim collapses to a cross-reference). Frozen evolution rule: additive keys allowed; any removal/rename/type-change requires a new `code`. No version field. | appending the envelope to output (neither JSON nor suffix-parseable; truncation arm could cut it); numerics inside `message`; `Error:`-prefixing the proc envelope (conflates "could not answer" with "answered: killed"; breaks `assert "Error:" not in result`) | W5; §6 |
| **DQ7** — config surface | **(b)+(a) hybrid, as leaned**: bare **`ENSEMBLE_RAM_GUARD`** master kill-switch (reaper semantics: default ON when absent; `0/false/no/off` case-insensitive = OFF; OFF = **complete no-op** — no thread, no registration, no measurement, zero DB queries; one INFO line at lifespan start) + a prefixed class **`RamGuardConfig(env_prefix="RAM_GUARD_")`** for numerics (INT-only, `ge`/`le`, validated outside the try/except ⇒ out-of-range **aborts boot**; YAML block **comment-only**). Knobs: `RAM_GUARD_CEILING_MIB` default **3072** [64, 65536]; `RAM_GUARD_POLL_INTERVAL_MS` 200 [50, 2000]; `RAM_GUARD_GRACE_MS` 250 [0, 2000]; `RAM_GUARD_PSS_CONFIRM_DEADLINE_MS` 250 [50, 2000]. Depth/node caps — **the two §4.1a re-arm constants, `RAM_GUARD_PENDING_MAX`, and `RAM_GUARD_GRACE_POLL_SLICE_SECONDS`** — are **module constants, not knobs**. **Runtime injection**: `ceiling_bytes` is a parameter of the guard's `register()` (the SpawnRecord already carries it); the config value is the default. **SECURITY RULING: the per-call override is an internal/test seam only — the LLM-facing bash/proc tool schema gains NO ceiling parameter** (an agent-visible knob would let the agent raise its own ceiling; cf. W3's registration-gated authority). **AMENDED (approver blocker 5):** the class **and** the bare switch are wired into the **root `Config`** — see §4.6. | `RESOURCE_MONITOR_*` (couples a 200 ms guard to a 30 s monitor + its yaml trap); agent-visible per-call ceiling (containment bypass) | plan lean + E-anchors + W3; §4.6 |
| **DQ8** — observability | `EventKind.RAM_LIMIT_EXCEEDED = "ram_limit_exceeded"` (**same string as the envelope `code`** — both are machine-facing verdict records of the same act; `exit_kind` stays a separate process-outcome taxonomy). Payload = **the envelope key set + exactly four event-only keys** (`trigger`, `state`, `tracking_id`, `breach_at`). **producer-side allow-list enforced** (the bus persists `data` unsanitized, `event_bus.py:174-181`). Exactly **one event per breach** (driven by the `WATCHING→KILLED` transition, emitted by the **execution-lane thread**), never per tick; `instance_id=None` ⇒ **counter + log only, no event** (the `event.instance_id` column is `NOT NULL` — and see §16.3: no v1 lane registers an instance-less spawn anyway; the sentence is defensive). Counters (RAM-only, `SamplerCounters` shape) — exact ordered list: `ram_kills, ram_breaches_detected, ram_kill_refusals_p0 … ram_kill_refusals_p7, ram_overcounted, ram_rearms, ram_confirm_failures, ram_events_emitted, ram_events_dropped, ownership_unverified, tree_incomplete` (there is **no** `ram_kill_refusals_p8` — P8 never refuses; see §4.2). Log prefix **`ram-guard:`** (matches `resource-monitor:`). Read surface: **new additive `GET /api/resources/ram-guard`** carrying the counters + the health heartbeat (`enabled, thread_alive, last_tick_at, missed_samples, ticks, registry_depth, lane_alive, lane_pending, last_lane_progress_at`) — **not** in `/livez`/`readyz` (an observability death must not gate traffic); `/control-capabilities` untouched (fence-pinned). | riding `/self-cost` (the monitor's own contract); healthz coupling | W4; §4.5; §16.1 |
| **DQ9** — default-ON vs T4 | **Confirmed: P1 lives entirely outside the T4 seam.** No read/write of `resource_limit_policy`; dormant columns untouched (both grep fences stay green unmodified); `/control-capabilities` byte-identical. Operator mental model: the guard is a **safety net** (default-ON, in-process, config-driven — like the unit cap), T4 is a **future policy layer** (user-gated, `enabled=false`, no writer). The env-off switch is the v1 disable; a per-policy disable belongs to the future T4 ceremony, not v1. | any T4 read path in v1 | W3, W4, W5 consumer #20 |
| **DQ10** — crash durability | **(a) in-process + the DQ8 event as the durable forensic record.** No migration lands in v1 **at all**. Rationale: the command died with the daemon anyway; the event answers "why did my command die" across restarts (one events query joins `event.pgid` ↔ the row's eventual `gc` close); events retain ~24 h (`EventRepository.cleanup_old`), sufficient for operator response windows and stated in the runbook. Rollback is trivial (remove the lifespan block + lane seams; zero schema residue). **AMENDED (approver blocker 3):** the event now has an owner, an emitter and a bridge (§4.5). **Qualifying sentence (house debt, out of scope — CORRECTED at iteration 002; phase7 T1 pins this verbatim):** *the SQLite `event.kind` CHECK constraint lists only 8 kinds while `EventKind` has 16 — it is already stale for 8 pre-existing kinds; the production deployment is PostgreSQL (`create_all`; the SQLModel column carries no CHECK — `models.py:74`), so `ram_limit_exceeded` (the 17th member) is unaffected. On a hypothetical SQLite-created DB the insert would fail; that failure surfaces as an emitter error log + `ram_events_dropped++` (never silent). No migration is added to fix the stale CHECK (pre-existing house debt, a separate commission).* | durable verdicts (redundant with the event; a proc write path; and closing the row disarms the escapee net — see DQ5) | W4; §4.5; E10 |

---

## 3. R1 — the Critical Interaction, resolved

### 3.1 Ceiling semantics: **ABSOLUTE per-command bytes** (council, unanimous)

`effective_ceiling = RAM_GUARD_CEILING_MIB × 1 048 576`, compared against the ruling metric (DQ1). Relative/dynamic ceilings rejected on five jointly-cited grounds: **not computable today** (`memory.max = max` — nothing to subtract); **algebraically a constant** (the tree term cancels: `X − (unit_current − tree) = X − daemon_baseline`); **self-raising hazard** (the daemon's attributed bytes peaked at 6 652 MiB during the Oct-17 leak — a lagged sampler would have handed that leak a higher ceiling); **~2.9 GiB scale mismatch** (74 % of today's `memory.current` is reclaimable page cache, which tree-RSS excludes); **untestable** without an operator host change.

### 3.2 Layering and dependency direction

```
INNER  (preventive)  per-command RAM guard   — live in-flight command trees; typed retryable error; one command dies
OUTER  (containment) systemd unit MemoryMax  — daemon-internal leaks, aggregate children, ORPHANS ACROSS RESTARTS
LAST   (running today) kernel global OOM     — currently the ONLY layer actually enforcing anything
```

**The unit cap adapts to the ceiling, never the reverse.** The plan's R1 no-op logic was right-for-the-wrong-reason: it is live only *conditional on a mis-sized cap being applied* — which the interaction table below prevents.

```
Unit survival:  X ≥ D_max + C + O + M_c        (the unit must host one full-ceiling command + the daemon)
Box survival:   X ≤ MemTotal − others − M_k    (the cgroup cap must fire before the kernel does)
```

**AMENDED (§3.4):** the *unit* survival floor must additionally host the **worst-case real footprint at the moment of the kill** (`F_max`, §3.4), not merely the configured ceiling — the tree keeps growing between detection and KILL.

### 3.3 Shipped default: **AMENDED — ship 3 GiB (3 072 MiB = 3 221 225 472 bytes), not the commissioned 8 GiB**

The commissioned 8 GiB is **unshippable on this host**: at fire time box demand ≈ 4.79 residents + 8.0 = 12.79 GiB > 11.67 physical; the kill completes at 13.8–14.8 GiB; empirically it **fires at-or-after daemon death in both Oct-30 incidents** (fast case: tree ≈ 4.0–4.3 GiB at death; slow case ≈ 8 RAM + 7.4 swap). It is over-budget by 1.17–2.52 GiB even at zero safety margin, and it is unshippable *with* a backstop too (needs `X ≥ 13 GiB`). 8 GiB becomes correct on a ≥ ~14.5 GiB host.

Council disagreement surfaced and resolved: `agentic` → 3 GiB (empirical pre-emption: C ≤ fast-case-tree-at-death − overshoot ≈ 4.3 − overshoot); `coding` → 4 GiB (box-survival arithmetic: C ≤ 4.48–5.18). **Resolution: 3 GiB**, because the binding constraint is the **intersection** and agentic's is tighter and incident-anchored: 4 GiB passes box-survival but **fails empirical pre-emption once overshoot is added** (4 + 0.7 = 4.7 > 4.3). 3 GiB passes both under every (latency + grace) on the table (3 + 1.0 = 4.0 ≤ 4.3). False-kill cost of 3 vs 4 ≈ 0 on measured evidence (max legitimate child footprint **364.5 MiB** over 21 days — both ceilings are ≥ 8× above it).

**Raise-path (AMENDED — arithmetic, never taste):** the previous text said "raise to 4 GiB by the same formula". **Under this document's own §3.4 table that raise is VOID on this host**: at C = 4 GiB the unit floor is `D_max 3.5 + 4.0 + O 1.0 + M_c 0.5 = 9.0 GiB > 8.88 GiB` ⇒ the X-window is **empty** and no unit cap can exist. The raise-path therefore becomes: **raise to 4 GiB only after the host reaches ≥ ~14.5 GiB** (or after Phase 2 Task 19 measures a materially lower allocation rate R, which would have to lower the overshoot term below the point that makes the window non-empty). If Phase 2 T19 measures R ≤ 1.0 GiB/s the shipped 3 GiB ceiling stays and the memo publishes the measured rate.

**Overshoot law (AMENDED — the confirm deadline is on the critical path; the contended companion added at iteration 002):** the original law `≈ rate × (poll + grace)` (0.45 s) **omitted the PSS confirm**, which sits between detection and the kill signal. The corrected law (single-tree, N = 1):

```
Ω_typ   = R × (poll 0.200 + confirm 0.250)              = R × 0.45 s   (TERM-honouring trees)
Ω_worst = R × (poll 0.200 + confirm 0.250 + grace 0.250) = R × 0.70 s   (TERM-ignoring trees)
```

**Contended companion (N simultaneous breaches, iteration 002):** with the execution lane servicing one breach at a time, the Nth tree's confirm waits behind N−1 services:

```
Ω_contended(N) = R × (poll 0.200 + (N−1)·T_service_worst + confirm 0.250 + grace 0.250)
T_service_worst = confirm_deadline 0.250 + grace 0.250 + probe ≈ 0.510 s
Ω_contended(2) = R × 1.21 s
```

Both the single-tree and the contended forms appear in the §3.5 honesty set and the operator memo. At R = 1 GiB/s: Ω_worst ≈ 0.70 GiB; Ω_contended(2) ≈ 1.21 GiB.

### 3.4 Operator routing (OPTIONS + RECOMMENDATION — nothing executed by this lane) — **RE-DONE WITH BOUNDED NUMBERS**

**Arithmetic frame (frozen by this amendment):**

```
F_max(R) = C_ceiling + A + Ω(R)                     (the tree's REAL footprint at the moment of the kill)
A        = RAM_GUARD_REARM_MAX_EXTRA_BYTES + RAM_GUARD_REARM_MIN_GROWTH_BYTES   (= 160 MiB = 0.15625 GiB)
floor(R) = D_max + F_max(R) + O + M_c               (D_max 3.5 worst · O 1.0 worst · M_c 0.5)
         = 8.15625 GiB + Ω(R)
box      = MemTotal 11.67 − others 1.79 − M_k 1.0   = 8.88 GiB  ⇒  X ≤ 8.88 GiB
```

With `C_ceiling = 3.0 GiB`, `D_max = 3.5`, `O = 1.0`, `M_c = 0.5`:

| Rule | `A` | `F_max` @ R=1 | floor @ R=1 (Ω_worst) | A feasible unit cap? | Guard wins the race at the published pair? |
|---|---|---|---|---|---|
| **Unbounded (WITHDRAWN — the approver's blocker 2)** | 1.825 GiB (measured 1 869 MiB) | 4.825 GiB | **9.825 GiB** | **NO** — floor > 8.88 at every R ⇒ **no unit cap can exist at all** ⇒ the orphan class loses its only backstop | never (the cap always fires first) |
| **BOUNDED, single-tree N=1 (binding)** | **0.15625 GiB (160 MiB)** | 3.85625 GiB | **8.85625 GiB** | **YES** — for R ≤ **1.034 GiB/s** (worst frame) / R ≤ 1.608 GiB/s (typical frame) | only for R ≤ **0.485 GiB/s** at `8700M` (worst frame) / R ≤ 0.755 GiB/s (typical frame) |
| **BOUNDED, contended N=2 (disclosed, iteration 002)** | 0.15625 GiB | 4.36625 GiB | **9.36625 GiB** | **ONLY for R ≤ 0.598 GiB/s** — the window is **EMPTY above that** | only for R ≤ **0.28 GiB/s** at `8700M` |

- **The N=2 row is a DISCLOSURE, not a design target** (iteration 002): two simultaneous full-ceiling breaches ≈ 6 GiB of trees + a 3.5 GiB daemon ≈ 9.5 GiB of unit demand on an 11.67 GiB box — that scenario is already past what per-tree arithmetic rescues; the kernel OOM is the residual layer. The published pair is a **single-tree claim** and the memo must say so.
- **Artifact:** one-page options memo at `docs/runbooks/ram-guard-and-unit-bounds.md` (P7 deliverable): measured state, both journal OOM deaths, the orphan finding (E8), the interaction table **as re-done above**, the drop-in, verify/rollback, the §3.5 honesty set, plus a one-line supersession note on `resource-cost-control.md` §8.1's never-applied 7G/8G figures.
- **Vehicle:** drop-in `/etc/systemd/system/ensemble-main.service.d/30-memory-bounds.conf` (next free slot; **not** `10-*`; verified to survive `adopt-unit.sh` regeneration). Disclose: `MemoryHigh`/`MemoryMax` apply only on restart — a **restart-class change**.
- **Interaction table (C = 3 GiB default; D_max 3.0–3.5; O 0.7–1.0; M_c 0.5; others 1.79; M_k 1.0 ⇒ X ∈ [8.0, 8.88]):**

| Option | MemoryHigh / MemoryMax | Verdict |
|---|---|---|
| **RECOMMENDED** | **7G / 8700M (8.50 GiB)** | **feasible** under the bounded re-arm (N=1): floor 8.15625 + Ω ≤ 8.496 ⇒ the guard wins the race only below R ≈ 0.49 GiB/s (worst frame); the pair's real job is the **orphan class**, not the guard-vs-cap race — see the truth statement below. Box margin 0.384 GiB. 7→8.5 throttle band gives reclaim pressure + runway. **A single-tree claim (see the N=2 row above).** |
| Conservative | 7G / 8G (8.00 GiB) | **REJECTED as primary** — 8.00 GiB < the bounded floor 8.15625 GiB ⇒ the cap fires before the tree's kill even at R = 0. It is strictly worse than 8700M and buys nothing |
| agentic's row | 7G / 9G | rejected at 9G (9 + 1.79 + 1.0 = 11.79 > 11.672) — recorded as the surfaced disagreement |
| Throttle-only | 8G / unset | rejected as primary: does **not** bound the orphan class |
| Any X ≥ 12G | — | invalid: the kernel OOMs the box before the cgroup cap fires |
| C = 8 GiB with any cap | — | invalid on this host (needs X ≥ 13) |
| **C = 4 GiB (the raise-path)** | — | **VOID on this host** — floor 9.0 GiB > 8.88 ⇒ empty window (§3.3) |

- **The L110 claim is WITHDRAWN and replaced by the truth statement (AMENDED — approver blocker 2 + iteration 002):**

> "The guard completes before the unit cap fires **only while the tree's allocation rate R stays at or below ≈0.49 GiB/s** at the published pair `8700M` (worst frame; ≈0.76 GiB/s in the typical TERM-honouring frame; **≈0.28 GiB/s if a second tree is simultaneously breaching**). The measured band is **1–3 GiB/s** — so **above ≈0.5 GiB/s the cgroup cap fires first and the daemon, not the command, is the casualty** (the cap is then the containment layer, not the guard's backstop). For fork-heavy trees the same holds a fortiori: the kill decision waits on a PSS confirm whose cost scales with the tree's RSS. **'The guard always completes before a unit kill' must not appear in the memo, the runbook, or any acceptance criterion.** Restoring the layering requires a host of ≥ ~14.5 GiB, not a different ceiling."

- **Also rejected:** `OOMScoreAdjust=-500` / `MemoryMin` (moves the outage onto PG/demo/OD co-residents). **Leave `MemorySwapMax` unset** — swap demonstrably buys the guard time on slow leakers (7.4 GiB absorbed at 11:13Z).
- **Verify/rollback:** canary first via `systemctl set-property --runtime ensemble-main.service MemoryHigh=7G MemoryMax=8700M` (live, self-reverting); persist = drop-in + `daemon-reload` + restart at a maintenance window; verify `systemctl show … -p MemoryMax -p MemoryHigh -p NRestarts` (NRestarts stays 0), `memory.events` counters stay 0, journal grep for `memory-limit|oom-kill` empty; rollback = remove drop-in + `daemon-reload` + restart (symmetric, no residue).

### 3.5 Honesty minimum (runbook + envelope must carry; council union set) — **AMENDED (4 additions + 2 iteration-002 clauses)**

**Envelope:** configured ceiling bytes (in the metric's unit); observed peak as a **sampled lower bound**, explicitly labelled, with the bound formula; kill outcome incl. tree completeness; the metric's *defined* name + shared-page policy; runbook pointer. **Must not claim:** box protected, memory reclaimed instantly, other processes unaffected, or that the killed command was "the culprit" (a numeric kill may hit a legitimate heavy command).

**Runbook:** (1) overshoot law `≈ rate × (poll + confirm + grace)` — typical `R × 0.45 s`, worst `R × 0.70 s`, contended `R × 1.21 s` at two simultaneous breaches, with shipped numbers (≈0.45–1.21 GiB at 1 GiB/s); (2) setsid escapees are *measured* (descendant closure) but **not killable** by the group kill — the reaper's stamp sweep is the bash-lane backstop, and the proc lane has **no** such net; (3) **orphans from previous daemon incarnations are not covered — only the unit cap covers that class** (proven Oct-30); (4) per-tree scope: aggregate exposure = Σ live ceilings + daemon band; (5) a killed legitimate heavy command is a real cost — raise by arithmetic, never intuition; (6) tree-RSS/PSS ≠ cgroup accounting — never cross-compare with `memory.current`; (7) no unit cap exists by default (present tense + verify command + REGENERATED-unit note); (8) **swap is not a safety net** — the daemon died with 203 MiB swap used *and* with 7.4 GiB used; swapped-out pages are invisible to an RSS metric (state the scope); (9) watchdog liveness is load-bearing — dedicated threads, never coroutines; **a wedged lane (a `/proc` read that never returns) stalls the kill path, never the measurement path — `last_lane_progress_at` frozen is the signal, the unit cap is the backstop** *(iteration 002)*; (9b) **an instance-less spawn (`instance_id is None`) is not registered with the guard in v1** — a ballooning tree from such a call is invisible to the guard and only the unit cap covers it *(iteration 002, §16.3)*.

**ADDED in the amendment passes:**

- **(10) The enforced threshold is not the configured ceiling.** *"The ENFORCED trigger threshold is not the configured ceiling. An over-counted tree (summed RSS over the threshold, summed PSS under the ceiling) is re-armed at `ceiling + min(summed_rss − summed_pss, 32 MiB)` — and the threshold is RATCHETED (it never decreases) — and it re-confirms only after a further 128 MiB of growth. The tree's real (shared-page) footprint can therefore reach `F_max` — ≈3.86 GiB at R = 1 GiB/s, i.e. **≈0.86 GiB (≈29 %) above the configured 3 GiB** — before the kill completes. The envelope reports BOTH numbers: `limit_bytes` (configured) and `rearm_threshold_bytes` (enforced trigger). A bounded re-arm can also MISS a genuinely-bad tree: if the shared double-count shrinks, summed RSS under-reports the real growth and the tree can exceed the configured ceiling before the confirm runs."*
- **(11) The PSS confirm is FAIL-OPEN.** *"If the confirm fails or exceeds its 250 ms deadline the tree is KILLED on the RSS evidence alone, so a legitimate fork-heavy command can be killed on a confirm timeout. The confirm rate is bounded (one confirm per 128 MiB of growth) to bound that exposure. On this host the confirm costs ≈35 ms median / 130–146 ms p95 at a 3 GiB tree, so a timeout is not expected below ≈20 GiB of tree total — beyond this host's physical RAM. The deadline is a POST-HOC elapsed check — the underlying `/proc` read is uninterruptible (`proc_readers.py:127-133` shape) — so a read that never returns holds the kill path until the daemon restarts; it never holds the tick."*
- **(12) The proc-lane verdict is QUERY-ONLY.** *"The proc lane's RAM verdict is QUERY-ONLY: `proc_run` returns a process handle before any breach can exist, so the typed `ram_limit_exceeded` envelope is delivered by the BASH lane's return string only. In the proc lane the verdict is surfaced by `proc_status` as a `ram_breach:` line plus a flat `ram_killed: true` (added as lines, never replacing the block). An agent that never calls `proc_status` after a proc run will NOT see the RAM verdict in the `proc_run` result — it must poll `proc_status` (the same obligation it already has for `timed_out` and `user_stopped`)."* — this sentence must also appear in the `proc_status` LLM-facing docstring.
- **(13) The daemon is not the reaper of a killed tree's grandchildren.** *"The guard kills a GROUP. The daemon reaps only its DIRECT child (the pre-existing `await proc.wait()`). Grandchildren are re-parented to init/PID 1, which reaps them; a transient `Z` member of a grandchild is init's business, not the guard's. The guard never forks and never races the direct-child reap."*

---

## 4. Frozen contracts (Phase 2 implements against these; any change is a version bump)

### 4.1 Tree-RAM reader contract (`daemon/services/ram_guard/tree_reader.py`)

```python
@dataclass(frozen=True) class ProcEntry:  pid:int; ppid:int; pgrp:int; state:str
@dataclass(frozen=True) class ProcTable:
    entries: dict[int, ProcEntry]; by_pgrp: dict[int, tuple[int,...]]; children: dict[int, tuple[int,...]]
    scan_complete: bool                      # False ⇒ the scan aborted
@dataclass(frozen=True) class TreeReading:
    total_bytes:int; metric:str; member_count:int; member_pids:tuple[int,...]
    complete:bool; unreadable_pids:tuple[int,...]; zero_pids:tuple[int,...]; gone_pids:tuple[int,...]
    source:str

def scan_proc(proc_root: Path) -> ProcTable                   # ONE shared scan per tick
def resolve_members(table, *, pgid:int, leader_pid:int, max_depth:int, max_nodes:int) -> MemberSet
def read_tree_rss(proc_root: Path, member_pids) -> TreeReading     # trigger metric (tick thread only)
def read_tree_pss(proc_root: Path, member_pids) -> TreeReading     # confirm metric (execution-lane thread only)
def exceeds(total_bytes:int, ceiling_bytes:int) -> bool            # total >= ceiling (inclusive)
```

- **Pure + injectable `proc_root`; no host `/proc` reads in unit tests.** No import of `proc_readers` (self-only PSS reader, kB convention) and **no import of `bash_spawn_reaper`** — the stat parse (`rfind(")")`; field 5 = pgrp, 22 = starttime, 24 = rss_pages — verified) is reimplemented injectably, preserving the zombie/kernel-thread exclusions.
- **Units:** `/proc` reports kB (KiB); the **only** `× 1024` lives in the parse helpers; everything downstream sees **bytes**. Config is MiB; `× 1 048 576` once at config→`SpawnRecord`.
- **Member states:** READABLE → value; **ZERO** (status OK, no `VmRSS:` line — empirically zombies/kernel threads) → 0, still complete; **GONE** (ENOENT) → 0, still complete; **UNREADABLE** (read error / unparseable) → 0, `complete=False`, pid recorded.
- **Breach semantics:** `total >= ceiling` (inclusive); **the trigger fires on the CURRENT sample, not the peak** — a spike released before the next sample is *reported* (peak) but never killed. An **incomplete** reading that still `>= ceiling` **proves** a breach (a lower bound above the ceiling); an incomplete reading below the ceiling is *no evidence* — never "no breach".
- **`tree_complete`** = scan completed ∧ closure not truncated ∧ no UNREADABLE member. It does **not** mean "every member is killable" — that is `members_total / members_killable / members_unreachable` (escapees are *measured* but not group-killable).
- **AMENDED (leader ruling Q2=(i)) — the membership carry-forward rule.** `resolve_members` keeps its signature (the reader contract is not re-shaped), but the **guard** applies the members it resolves through a **record carry-forward**: a pid joins the record's member set iff (a) it was already an observed member of this record, **or** (b) it is a bounded descendant of an already-observed member, **or** (c) it shares the captured pgid **and** is not a pgid-leader replacement (see §4.2 P5a). Without the carry-forward, the re-anchor would re-adopt a recycled pgid's members.

### 4.1a The BOUNDED over-count re-arm (binding; approver blocker 2)

**The withdrawn rule:** `rearm_threshold_bytes = ceiling_bytes + shared_extra_bytes` (unbounded). Consequence (measured): effective threshold ≈ 4.825 GiB, §3.4 unit-cap floor 9.825 GiB > the 8.88 GiB box ceiling ⇒ **no unit cap could exist on the re-arm path at any allocation rate** ⇒ the orphan class (E8) lost its only backstop, and the envelope reported `limit_bytes = 3 GiB` while ≈4.8 GiB was enforced — a §3.5 honesty violation.

**The binding rule:**

```
rearm_threshold_bytes = max(previous_rearm_threshold_bytes,
                             ceiling_bytes + min(shared_extra_bytes, RAM_GUARD_REARM_MAX_EXTRA_BYTES))
confirm_due = (summed_rss_bytes >= rearm_threshold_bytes)
              and (summed_rss_bytes - last_confirm_rss_bytes >= RAM_GUARD_REARM_MIN_GROWTH_BYTES)
```

**Module constants (NOT knobs — DQ7 precedent "depth/node caps are module constants"):**

```
RAM_GUARD_REARM_MAX_EXTRA_BYTES  = 33554432     (32 MiB)   — cap on the shared-double-count allowance
RAM_GUARD_REARM_MIN_GROWTH_BYTES = 134217728    (128 MiB)  — confirm governor: bytes of growth between confirms
A = 167772160 (160 MiB = 0.15625 GiB)
```

**Why the additive form survives (bounded):** the unbounded form was *correct* in intent — re-arming at `ceiling + shared_extra` delays the trigger until the **real** footprint crosses the ceiling, because `summed_rss = PSS + shared_extra`. The bound keeps that property up to `CAP`; beyond `CAP` the trigger fires earlier, so the confirm runs more often (CPU cost) but the tree's real footprint can never exceed the ceiling by more than `CAP` when the shared double-count shrinks. **Containment is bought with confirm frequency, never with ceiling.**

**The growth governor.** After an over-counted confirm the threshold is **ratcheted** (non-decreasing) — so the trigger condition stays true — and the governor is what stops the confirm from running on **every tick**: the next confirm requires a further `RAM_GUARD_REARM_MIN_GROWTH_BYTES` of summed growth. Its cost is an additional `128 MiB` of real-footprint allowance (counted in `A`); its benefit is that the confirm rate is bounded at `min(tick_rate, R / 128 MiB)`.

**Confirm-frequency arithmetic (re-scoped at iteration 002 — the confirm runs on the LANE, not the tick):** at R = 1 GiB/s the governor does **not** bind (R / 128 MiB = 8/s > the 5/s tick rate) ⇒ the confirm runs at the tick-detection rate ⇒ **lane** duty ≈ 5 × 35 ms = 175 ms/s ≈ 17.5 % of one core, and **the tick is never involved at all** (the confirm executes on the execution-lane thread — DQ2). The lane's throughput bound is `1 / T_service` ≈ 2–12 confirms/s. The real limits are (i) the tick-side scan overrun at large host pid counts (Phase 2 T18's sizing job — the scan is 9.49 ms at 250 pids, ≈40 ms at 1000, and the tick overruns beyond ≈1 250 pids) and (ii) the **number of fail-open opportunities** (each confirm that exceeds its 250 ms deadline kills on RSS evidence alone). The governor binds below R ≈ 0.64 GiB/s — exactly the slow, legitimately-heavy fork job, where it cuts the confirm rate by 4–8×.

**Residual over-count risk ACCEPTED (disclosed in §3.5 item 10):** a tree whose shared double-count shrinks can consume up to `F_max` (≈3.86 GiB at R = 1 GiB/s) before the next confirm; a genuinely-bad tree can therefore exceed the configured ceiling by ≈0.86 GiB; and the fail-open confirm can kill a legitimate fork-heavy command on a confirm timeout. These are the prices of the hybrid metric and they are stated, not hidden.

**The `OVERCOUNTED` state becomes STICKY:** the record's state machine keeps the shape `WATCHING → BREACHED → OVERCOUNTED → KILLED → RELEASED`, but **first-writer-wins applies to the terminal transitions** (`→KILLED`, `→RELEASED`) and to `WATCHING→BREACHED`; **`OVERCOUNTED → OVERCOUNTED` is an idempotent in-place update** of `rearm_threshold_bytes` (ratcheted), `last_confirm_rss_bytes`, `rearm_count`, `confirm_count`, `samples`, `last_sample_at` — **no state re-entry, no `BREACHED` re-entry**. The `OVERCOUNTED` state is the only state in which a further confirm may fire, and it is released (not killed) at `group_member_count == 0`.

### 4.2 Kill-ladder contract (`ram_guard/ladder.py`) — **AMENDED (leader ruling Q2=(i) + approver blockers 1 & 6 + iteration 002)**

```
ram_kill_ladder(pgid, leader_start_time, grace_seconds) -> (status, reason)
status ∈ {killed, already-dead, refused}
reason  ∈ {killed:term-esrch, killed:group-dead-after-grace, killed:ladder, killed:tree-incomplete,
           already-dead:group-empty,
           refused:guard-disabled, refused:not-registered, refused:platform-unsupported,
           refused:pgid-not-registered, refused:pgid-invalid, refused:pgid-self,
           refused:daemon-group, refused:pgid-recycled, refused:leader-unreadable,
           refused:ownership-mismatch, refused:group-unknown}
```

**16 codes — the enumeration above IS the authoritative exact set (iteration 002, §16.2): 9 kept/renamed from the frozen cycle-1 set (3 `killed:*` + `already-dead:group-empty` + 5 `refused:*`, with `refused:platform-win32` renamed to `refused:platform-unsupported`) + 7 added (`killed:tree-incomplete`, `refused:guard-disabled`, `refused:not-registered`, `refused:pgid-not-registered`, `refused:leader-unreadable`, `refused:ownership-mismatch`, `refused:group-unknown`). REMOVED: none. The `status` set is unchanged.**

**Mandatory PRE-KILL predicates — a kill fires iff ALL evaluate true; "unevaluable" ≠ true. The gate is P0–P7** (AMENDED: the previous list wrongly included P8 among the pre-kill predicates — a post-kill probe cannot gate a kill):

| Pred | Meaning | FALSE ⇒ | UNREADABLE ⇒ |
|---|---|---|---|
| **P0** | switch ON ∧ Linux ∧ registered in this guard's registry | `refused:guard-disabled` / `refused:not-registered` / `refused:platform-unsupported` | — |
| **P1** | the pgid is the one captured by **this guard's** registry | `refused:pgid-not-registered` | — |
| **P2** | `pgid > 1` | `refused:pgid-invalid` | — |
| **P3** | `pgid != os.getpgid(os.getpid())` (**the daemon's group, not its pid**) | `refused:daemon-group` | — |
| **P4** | `pid != os.getpid() ∧ pgid != os.getpid()` | `refused:pgid-self` | — |
| **P5** | **leader-identity gate — three outcomes (AMENDED):** `leader alive ∧ starttime matches` ⇒ TRUE; `leader alive ∧ starttime mismatch` ⇒ `refused:pgid-recycled`; `leader absent (ENOENT)` ⇒ **NOT APPLICABLE** — the identity re-anchors on the surviving group members and the kill may proceed on group membership; `leader stat unreadable (read error / unparseable)` ⇒ `refused:leader-unreadable` (**fail-closed**) | as listed | `refused:leader-unreadable` |
| **P5a** | **pgid-recycle check (required by the re-anchor):** `pgid recycled ⇔ a live process with pid == captured_pgid has a starttime ≠ captured leader_start_time` | `refused:pgid-recycled` | `refused:pgid-recycled` (fail-closed) |
| **P6** | ownership stamp | mismatch ⇒ `refused:ownership-mismatch` | unreadable ⇒ **PROCEED** + `ownership_unverified++` (deliberate fail-open, unchanged) |
| **P7** | group liveness: `group_member_count > 0` | `group_member_count == 0` ⇒ `already-dead:group-empty` (**release, no signal**) | `refused:group-unknown` |

**P8 — POST-KILL verification step (not a pre-kill predicate):** after the KILL, the ladder probes the tree; `tree_complete` true ⇒ the existing `killed:*` code stands; incomplete/unknown ⇒ `killed:tree-incomplete` (status stays `killed`, and `tree_incomplete` is incremented). **There is no `ram_kill_refusals_p8`** — P8 never refuses.

**Leader ruling Q2=(i) — the canonical predicate strings (these MUST appear character-for-character in §2 DQ4, §5, phase2 T10/T11 and test-plan S8/B13):**

```
the watch stays alive until the group member count reaches 0
identity = captured pgid ∧ surviving-member group membership
starttime gate applies to the leader only while alive
the watch is released only at group member count 0
group_member_count == 0
```

`already-dead:group-empty` fires **ONLY** at `group_member_count == 0` — it is **not** produced merely because the leader is gone. (Kept-and-redefined rather than deleted: it is the only code that carries the "nothing to signal, release the watch" verdict; killing it would collapse a release into a generic refusal.)

**Ownership gate:** captured pgid + leader starttime re-verification (fail-closed), environ check secondary. **The ladder is sync**, callable from the **execution-lane thread** with **no event loop** (and from a bare thread in tests — S16); TERM → slice-probed grace (at `RAM_GUARD_GRACE_POLL_SLICE_SECONDS = 0.025`) → KILL; KILL always reachable; zombie-aware skip; **idempotent**.

### 4.3 Typed-error contract (`ram_guard/errors.py`) — **AMENDED**

- **Bash:** the envelope **is** the entire return string. **Proc:** a `ram_breach:` line + flat `ram_killed: true` inside `proc_status` (added, never replacing). **AMENDED:** the proc lane's verdict is **query-only** by construction (`proc_run` returns `process_id, None` at `proc_tools.py:1307`, before any breach can exist) — the honesty set and the runbook must carry the §3.5 item-12 sentence, and the `proc_status` LLM-facing docstring must gain the matching line.
- Key set (additive-evolution; house 3-key prefix byte-identical; numerics **before** `message`) — **AMENDED** (`rearm_threshold_bytes` inserted immediately after `limit_bytes`, so the configured ceiling and the enforced trigger threshold read together):
  `ok, code, surface, limit_bytes, rearm_threshold_bytes, observed_peak_bytes, peak_is_lower_bound, poll_interval_ms, metric, trigger_metric, confirmed_pss_bytes, under_report_bound_bytes, pid, pgid, tree_complete, members_total, members_killable, members_unreachable, grace_ms, killed_at, ram_breach_observed, coincident_timeout, timeout_seconds, message`
  - `limit_bytes` **stays the configured ceiling**; `rearm_threshold_bytes` carries the **enforced trigger threshold**; value convention **integer bytes, `0` = no re-arm was armed** (never `null`). Both appear in the envelope **and** in the event payload (§4.5).
- **Negative space (forbidden, with rationale):** command/argv in any form incl. hashes (argv carries secrets; a hash is a brute-force oracle; the sanitized form still leaks the `--flag value` residual) · env keys or values (a live child's `/proc/<pid>/environ` holds **plaintext resolved secrets**, `secret_resolver.py:300-397`) · stdout/stderr · secret ids · per-member pid lists / RSS breakdowns · host paths/hostnames/workdir · exception reprs. `message` = **fixed text**, numeric interpolation only.
- **Message content (both templates) — AMENDED:** the ceiling and observed peak with human units; `observed_peak_bytes` labelled a **sampled lower bound** of the named metric (+ shared-page policy); the statement that the **daemon's RAM guard** (not the kernel OOM killer) issued the kill; tree completeness; a recovery hint (a kill can land mid-write — re-run idempotently); **and the enforced-threshold clause** — *"The enforced trigger threshold was {threshold_mib} MiB (the configured ceiling is {limit_mib} MiB); when the summed-RSS metric over-counts shared pages the threshold is re-armed above the ceiling and the kill decision is always made on the shared-page total."* — a **single interpolation** (no third template; `threshold_mib` = `rearm_threshold_bytes` when armed, else `limit_bytes`).
- **Precedence (bash):** the RAM arm sits between `bash.py:1088` and `:1110` and preempts, in order: the success arm's `mark_exit` (**one-shot** — `pid_registry.py:423` deletes the live row; if the success arm fires first the RAM verdict is *permanently erased*), the `EXIT CODE:` line (`:1155`), the 150 k truncation arm (`:1163-1171`), and the timeout arm (`:1159`). It also performs the registry unregister the success arm does (`:1128-1136`). The secret envelope (`:1215-1222`) is **mutually exclusive** with the RAM envelope (secret resolution raises before spawn ⇒ no child ⇒ no measurement).
- **Coincidence:** claim-token, first-writer-wins. Timeout wins ⇒ `code="timeout"` **plus** `ram_breach_observed:true` + the peak. Guard wins ⇒ `code="ram_limit_exceeded"` plus `coincident_timeout:true`.

### 4.3.1 VERDICT-PUBLICATION ORDERING (binding; leader ruling 2) — **deadline re-derived at iteration 002**

**The rule:** on guarded paths, the kill ladder's verdict is **PUBLISHED before any `mark_exit` / registry release**, and `mark_exit` for a guarded command **waits on guard completion**. `mark_exit` is one-shot (`pid_registry.py:409-413` raises on an unknown kind; `:423` deletes the live row) — an ordering that loses the race erases the verdict permanently.

1. **Terminal verdict first.** The verdict record reaches a terminal state (`KILLED` with its reason, peak and timestamps) **before** any lane-side `mark_exit` or registry release runs. The verdict is published by the **execution-lane thread** (DQ2).
2. **The lane waits, bounded.** After `await proc.wait()` (bash S2 / proc exit task), the lane reads the verdict record. If the record is non-terminal (`BREACHED` / `OVERCOUNTED` — the guard may still be running the ladder, or the record may be queued behind another breach), the lane **waits** for a terminal state. The wait is a **bounded polling wait on the RAM-only record** (25 ms poll), `async`, **never blocking the event loop**. **Wait deadline (re-derived at iteration 002 for the two-thread guard):**
   ```
   wait_deadline_ms = RAM_GUARD_PENDING_MAX × T_service_worst_ms + WAIT_SLACK_MS
   T_service_worst_ms = PSS_CONFIRM_DEADLINE_MS + GRACE_MS + PROBE_MS  (= 250 + 250 + ~10 = 510)
   ⇒ 2 × 510 + 250 = 1270 ms
   ```
   On deadline expiry the lane proceeds with the record's actual state and the envelope reports it — it never fabricates "no breach".
3. **Release after publication — CONDITIONAL on the claim.** The guard-registry release and the `BashProcessRegistry.unregister` (`bash.py:1132-1141`) happen **after** the verdict is published. **A release of a CLAIMED record is DEFERRED TO THE LANE** (the lane releases after publishing) — a lane-side release racing the lane's own publication is the §4.3.1 violation this clause closes. The durable `bash_spawn_registry` row stays **OPEN** (DQ5 — unchanged).
4. **The success-path release is CONDITIONAL on the watch being finished (iteration 002 — the basis for the planner's blocker-B2 fold):** when the bash call returns but the record's watch is not finished (`group_member_count > 0` — the ordinary `cmd &` leaderless shape), the guard entry is **NOT released on the bash lane's exit paths**; the tick thread's liveness sweep owns the release at `group_member_count == 0`. The `finally`-shaped release on the six bash exit paths covers the lane's own bookkeeping for terminal/empty-watch records only — **it never ends a live watch**.
5. **Exit kind.** A RAM-killed command is marked with `exit_kind=ram_killed` (the **named constant**, never a literal) and the guard's sampled peak converted **bytes → KB exactly once** at S6.
6. **The proc lane.** `_drain_exit_code` (`proc_tools.py:786-875`) consults the verdict **before** finalizing `info.status`; `ram_killed` joins `timed_out`/`user_stopped` as the **third authoritative input** — the existing ladder at `:861-875` is preserved and the RAM verdict is consulted first, because it is the only signal that says *who* killed the tree. `info.status` stays `"killed"`; `ram_killed` is `True` whenever the guard's record says `KILLED` (independently of the timeout claim — the envelope's `code` follows the claim-token rule, so a coincidence shows `code="timeout"` + `coincident_timeout:true` + `ram_killed:true`).
7. **Consequence for Phase 3 T5.** Its acceptance becomes achievable as designed: the ordering is asserted by a **recording fake** (not by inspection) — the verdict publication is observed **before** `mark_exit` is called; the success arm's `mark_exit(exit_kind=EXIT_KIND_SUCCESS)` did **not** run; the guard-registry release happened **after** the publication.

### 4.4 The verdict record (`SpawnRecord`, RAM-only, keyed by `tracking_id`)

`tracking_id · instance_id · lane · pid · pgid · start_time_jiffies · ceiling_bytes · poll_interval_ms · grace_ms · state (WATCHING→BREACHED→OVERCOUNTED→KILLED→RELEASED; first-writer-wins on the terminal transitions; OVERCOUNTED is a sticky idempotent update — §4.1a) · observed_peak_bytes (monotonic) · last_total_bytes · samples · metric · trigger_metric · peak_is_lower_bound · under_report_bound_bytes · confirmed_pss_bytes · tree_complete · members_total/killable/unreachable · unreadable_pids · gone_pids · enumeration_failures · breach_at · killed_at · last_sample_at · trigger (rss_threshold | pss_threshold | pss_confirm_unavailable) · rearm_threshold_bytes · last_confirm_rss_bytes · rearm_count · confirm_count` (the last three are NEW — required by the bounded re-arm).

**Honest-peak bound (frozen, AMENDED):** per-sample under-report ≤ `R·Δt`, **computable from the samples themselves** as `max positive sample-to-sample delta` (`under_report_bound_bytes`); total exposure breach→all-dead = `R·(Δt + confirm_deadline + grace)` — the confirm term is **added** (the previous form omitted it). Both appear in the envelope.

### 4.5 The durable event — OWNER, EMITTER, BRIDGE (binding; approver blocker 3)

**The hole:** DQ10's only durability mechanism was the DQ8 event, but nothing produced it — the §7 freeze (`ram_guard → stdlib only`) forbids the guard from touching the bus; phases 3/4 referenced no event; the bus is `async` (`event_bus.create_event` → `await asyncio.to_thread(...)`, `event_bus.py:155-181`) while the guard's threads must work with **no running loop**; and there is **no** `get_event_bus()` accessor (`manager.py:1945` holds a private `self._event_bus`).

**The binding shape — an INJECTED emitter callable (constructor injection):**

```
daemon/services/ram_guard/events.py      RamBreachEmitter = Callable[[str, Mapping[str, Any]], bool]
```

- The guard holds `emitter: RamBreachEmitter | None` (default `None` ⇒ no event, counter only). At the `WATCHING→KILLED` transition — **after** the ladder returns and **outside** the registry lock (the claim token is taken under the lock **by the tick thread at observation**; the ladder runs outside it, §5 "Double-kill race") — the **execution-lane thread** calls `emitter(instance_id, build_ram_event_payload(record))` **exactly once**. The call is wrapped in `try/except Exception` ⇒ it can never raise into the lane's execution path; a raising emitter ⇒ `ram_events_dropped++`.
- The emitter returns `True` iff the event was **scheduled** for delivery, `False` iff it was **dropped**; `ram_events_emitted` / `ram_events_dropped` count the two outcomes.
- `instance_id is None` ⇒ **no call at all** (counter + log only) — the `event.instance_id` column is `NOT NULL`. (Defensive only — §16.3: no v1 lane registers an instance-less spawn.)

**The bridge (lives OUTSIDE the package — `daemon/services/ram_guard_events.py`):**

```
loop = manager._get_event_loop()        # manager.py:3097 — hardened: raises RuntimeError if no live loop; NEVER creates one
coro = event_bus.create_event(instance_id=..., kind=EventKind.RAM_LIMIT_EXCEEDED, data=dict(payload))
fut = asyncio.run_coroutine_threadsafe(coro, loop)   # NON-BLOCKING — never .result(timeout=…)
fut.add_done_callback(_log_exception)                # logs; never re-raises into the lane
```

**Why NOT the two obvious alternatives — both ruled out with evidence:**

- **Not `MainLoopBridge`.** `MainLoopBridge.set_loop(manager._loop)` is called **only** inside `PoolOrchestrator.setup()`, and that method **returns early** when `USE_WORKER_POOL` is `false`/`0`/`no` (`pool_orchestrator.py:199-201`, `:235`) ⇒ on such a boot `MainLoopBridge._loop` is `None` ⇒ **every RAM event would be silently dropped**. The manager's hardened accessor (`manager._get_event_loop()`, `manager.py:3097-3195`) is used instead — it returns `self._loop` (set unconditionally at `manager.py:4425`) when live, else the thread's live loop, else **raises** `RuntimeError` (it never fabricates a loop).
- **Not the blocking `.result(timeout=8.0)` precedent.** `report_delivery_recovery.py:1499-1507` blocks for **8 s = 40× the 200 ms poll interval**. A thread blocked 8 s is the 🔴 "silent and fatal" failure mode (§5), and the wedge-capture precedent forbids it outright. The guard's hop is therefore **schedule-and-forget** with a done-callback.

**Drop semantics — visible, never silent:** no live loop / loop closed / daemon shutdown ⇒ the event is **DROPPED** (never raised, never retried) and the loss is visible: `ram_events_dropped++` on `GET /api/resources/ram-guard`, plus the `WATCHING→KILLED` **transition log line** (the tick path never logs; the transition does), which carries the same allow-listed key set as the journal fallback.

**Honest limit on durability:** §4.4 is RAM-only, so it does **not** survive a restart; across a restart the **event is the only durable artifact** (DQ10(a)) ⇒ a dropped event is a permanent loss of the forensic record. The drop counter, the transition log line and the ≈24 h event retention are the compensations.

**Event payload key set (ordered, asserted as an exact list):**

```
ok, code, surface, limit_bytes, rearm_threshold_bytes, observed_peak_bytes, peak_is_lower_bound, poll_interval_ms, metric, trigger_metric, confirmed_pss_bytes, under_report_bound_bytes, pid, pgid, tree_complete, members_total, members_killable, members_unreachable, grace_ms, killed_at, ram_breach_observed, coincident_timeout, timeout_seconds, message, trigger, state, tracking_id, breach_at
```

= the **envelope key set** + exactly four **event-only** keys (`trigger` — `rss_threshold|pss_threshold|pss_confirm_unavailable`, the fail-open discriminator; `state` — the transition, `KILLED`; `tracking_id` — the within-run correlation id; `breach_at` — crossing time vs kill time).

**Producer-side allow-list (literal):** *"`build_ram_event_payload(record)` is the ONLY producer of the event payload. It projects ONLY the keys in the ordered list above, in that order, from the `SpawnRecord`; a key not in the list is never copied, and no code path may mutate the returned dict before it reaches `EventBus.create_event`. The bus persists `data` **unsanitized** (`event_bus.py:174-181`), so the allow-list MUST be enforced at the producer."* The negative space is identical to the envelope's (§4.3).

### 4.6 `RamGuardConfig` → root `Config` (binding; approver blocker 5)

**The hole:** `daemon/config.py` had **zero** `ram_guard` references; the root `Config` aggregates **explicit fields only** (`config.py:6802-6814`) and `load_config` ends with `config = Config(**config_dict)` (`config.py:8795`) ⇒ the whole env tier and the boot-abort rung were dead code; only the kill-switch would have worked.

**The binding statement:**

1. **Class** — `class RamGuardConfig(BaseSettings)` in `daemon/config.py`, mirroring `ResourceMonitorConfig` (the house peer at `config.py:920+`): `model_config = SettingsConfigDict(env_prefix="RAM_GUARD_")` with exactly four **INT-only** fields — `ceiling_mib: int = Field(default=3072, ge=64, le=65536)` · `poll_interval_ms: int = Field(default=200, ge=50, le=2000)` · `grace_ms: int = Field(default=250, ge=0, le=2000)` · `pss_confirm_deadline_ms: int = Field(default=250, ge=50, le=2000)`. Depth/node caps, the §4.1a re-arm constants, `RAM_GUARD_PENDING_MAX` and `RAM_GUARD_GRACE_POLL_SLICE_SECONDS` are **module constants, not knobs**.
2. **Root field** — `ram_guard: RamGuardConfig = Field(default_factory=RamGuardConfig)`, mirroring `resource_monitor` (`config.py:6812-6814`). **NOT** `| None`: OFF is a *value*, not an absence. `default_factory` **is** the env tier ⇒ `load_config` needs **no** change, provided `config.yaml` carries **no** `ram_guard:` literal block (the house shape at `config.yaml:283-305`).
3. **The master switch is NOT a pydantic field.** A bare `ENSEMBLE_*` name does not bind to a prefixed class; `ENSEMBLE_RAM_GUARD` is parsed by a dedicated function mirroring the reaper's (`bash_spawn_reaper.py:139-156`): absent ⇒ **ON**; empty ⇒ **ON**; else `raw.strip().lower() not in {"0","false","no","off"}` ⇒ **ON**. It must **not** be a pydantic `bool` (pydantic would raise on `""`/unknown values and abort boot, violating the reaper's "explicit anything else ⇒ ON"). The resolved flag is carried on the root `Config` as a **derived** value so the lifespan reads it without re-parsing the env.
4. **Boot-abort rung** — the `ge`/`le` bounds fire at `Settings` instantiation inside `load_config()` ⇒ `config = Config(**config_dict)` (`config.py:8795`) raises `ValidationError` ⇒ it propagates out of `load_config()` (`api.py:614`), which runs **before and outside** the lifespan's `try/except` ⇒ **BOOT ABORTS** (house precedent documented at `api.py:2266-2278`). **Do not clamp.**
5. **YAML-comment-only rule** — the `ram_guard:` block carries **comments only** (pydantic init-kwargs beat env: `config.py:911-916`).
6. **OFF semantics** — a complete no-op: no threads, no registration, no measurement, **zero DB queries**, no `/proc` reads, `app.state.ram_guard is None`, one INFO line at lifespan start. OFF is checked **before** the knob-validation path so an out-of-range ceiling cannot matter while the guard is off.

**The assertion shapes (the house idiom — `tests/unit/test_auth_config.py:371-377`):**

```
config = load_config(str(yaml_file)); assert config.ram_guard.ceiling_mib == 3072
assert config.ram_guard.poll_interval_ms == 200; assert config.ram_guard.grace_ms == 250
assert config.ram_guard.pss_confirm_deadline_ms == 250
assert config.ram_guard.ceiling_mib * 1_048_576 == 3_221_225_472
monkeypatch.setenv("RAM_GUARD_CEILING_MIB", "64"); config = load_config(str(yaml_file))
assert config.ram_guard.ceiling_mib == 64
with pytest.raises(ValidationError): load_config(str(yaml_file))      # out-of-range ⇒ BOOT ABORTS
```

**CONSEQUENCE (discharges approver blocker 7's C3 half):** the boot-abort rung **is assertable today** — `load_config()` itself raises, so no daemon-boot harness is needed. **Phase 5's "explicit untestable disclosure" escape is DELETED.**

---

## 5. Failure-path rulings (Phase 6 must test every row) — **AMENDED (iteration 002: two-thread death modes)**

| Failure | Ruling |
|---|---|
| **Tick thread dies** (raises) | Its own `while not stop` restart loop revives it (phase2 T14's pattern); `thread_alive` False→True, `missed_samples++`. Heartbeat on `GET /api/resources/ram-guard`. |
| **Tick thread WEDGED** (a never-returning `/proc` read — the scan shares the uninterruptible `read_text` shape, E10) | **Not recoverable — no supervisor exists** (DQ2 rejects supervisors; the house accepts none for wedge-capture). Measurement stops; `last_tick_at` **frozen is the only signal** (`missed_samples` does NOT climb — the tick never returns to count a miss). The runbook states the unit cap is the only backstop. Disclosed, not fixed. |
| **Execution-lane thread dies** (raises) | Its own restart loop revives it; `lane_alive` False. Pending breaches resume on revival. |
| **Execution-lane thread WEDGED** (a never-returning `smaps_rollup` read — the exact failure the feature exists to prevent) | **Kills stop; measurement continues.** Breaches are still detected on schedule by the tick thread and claimed into the pending set, but never executed. **`lane_alive` stays True** (a wedged thread is alive) ⇒ the signal is the **progress heartbeat `last_lane_progress_at` frozen**, plus `lane_pending` climbing. NOT `Thread.is_alive()` — that reports a wedged lane green. The runbook: the unit cap is the backstop; the residual (a wedged read holds the kill path until the daemon restarts) is disclosed (§3.5 item 11). |
| **Registration leak** | `finally`-shaped release on the bash exit paths — **conditional on the watch being finished** (§4.3.1 item 4: a live watch is NEVER ended by a lane exit; the sweep owns its release) — + a **bounded non-lethal liveness sweep** every ~25 ticks **on the tick thread**: dead pid ⇒ drop the entry; live-but-unreleased ⇒ **keep watching** (the safe direction — the runaway stays guarded); `group_member_count == 0` ⇒ release. |
| **Pre-existing leak (disclosed, not fixed here)** | The bash generic-exception arm (`bash.py:1223-1224`) releases **neither** registry and does **not** kill the child — and `MemoryError` lands exactly there during a RAM incident. Phase 3 must add the *guard* release there; the pre-existing `BashProcessRegistry`/`pid_registry` leak on that arm is routed as a follow-up. |
| **Double-kill race** (reaper / bash timeout arm / proc timeout task) | **Claim token** under the registry lock, taken by the **tick thread at observation** or by elapsation (timeout); the ladder runs outside the lock on the **execution-lane thread**. First claimant owns the verdict; losers skip their signal and read the verdict; the loser is recorded `coincident`. Exit code `-9` is ambiguous by construction (guard KILL ≡ kernel OOM ≡ user kill ≡ `proc_stop(force)`) — **the verdict record is the only discriminator**. |
| **Pending-set overflow** (a breach arrives while `RAM_GUARD_PENDING_MAX` records are pending — NEW, iteration 002) | The newest breach is **NOT claimed** (the state stays WATCHING; the peak and `ram_breaches_detected` still update) and is **re-offered next tick**. **No confirm is ever skipped** and no kill fires without one ⇒ `pss_confirm_unavailable` remains reserved for a genuine confirm failure/timeout. Congestion is visible: `lane_pending` on the endpoint. |
| **Peak under-report** | Sampled lower bound, bounded by §4.4; labelled in the envelope. |
| **The event cannot be delivered** | No live loop / loop closed / daemon shutdown ⇒ the event is **DROPPED**, never raised into the lane; `ram_events_dropped++`; the `WATCHING→KILLED` transition log line is the journal fallback (§4.5). The verdict record remains the in-process source of truth; across a restart the event is the only durable artifact (DQ10). |
| **The leader's identity is unverifiable** | Leader `stat` **unreadable** (read error / unparseable — *not* ENOENT) ⇒ `refused:leader-unreadable` (**fail-closed**: an unreadable identity is not a confirmed-dead leader). The **watch stays alive** (measurement continues); only the kill is refused while identity is unverifiable. The §3.5 honesty set carries this exposure; the unit cap is the backstop. |
| **Degradation matrix (AMENDED — leader ruling Q2=(i))** | non-Linux ⇒ INERT; `/proc` unreadable ⇒ no measurement, no kill, one WARN/spawn, `missed_samples++`; enumeration failure ⇒ drop the sample; daemon group / `pgid≤1` / self ⇒ REFUSE; identity mismatch ⇒ REFUSE (`refused:pgid-recycled`); leader absent ⇒ the watch stays alive until the group member count reaches 0 and identity = captured pgid ∧ surviving-member group membership — starttime gate applies to the leader only while alive (leader alive + `starttime` mismatch ⇒ REFUSE `refused:pgid-recycled`; leader `stat` unreadable ⇒ REFUSE `refused:leader-unreadable`); a live process holding the captured pgid with a different `starttime` ⇒ REFUSE `refused:pgid-recycled`; the watch is released only at group member count 0 (no signal) ⇒ `already-dead:group-empty` ⇐ `group_member_count == 0`; |

---

## 6. Consumer-gate evidence (the one-time gate artifact — DQ6) — **AMENDED: the "22" is withdrawn; counts are derived, never pinned**

**The census below is the artifact.** It enumerates every machine-pinned consumer surface as file:line rows — individually-cited pins plus three group families (the `ProcessInfo.status == "killed"` attribute pins; the bash success-string pins; the bash `STDOUT:` pins). **All counts — per-group tallies and any headline total — are derived conveniences, re-computed by grep at fold time and NEVER pinned** (a ±1 grep-count drift between passes is a fold-time note, not a contract violation). Across the enumeration: ≈54–55 assertion sites in 10 test files; 1 inapplicable legacy pack; 0 daemon-side parsers. The previous "22 consumers verified" claim is **withdrawn** — no such list existed; the original section enumerated 5 *facts*, not 22 consumers.

| # | Consumer surface | file:line | Asserts | Breaks on the envelope? |
|---|---|---|---|---|
| 1 | proc_status substring | `tests/tools/test_proc_tools.py:240` | `"status: running" in status` | NO (additive) |
| 2 | proc_status substring | `tests/tools/test_proc_tools.py:257` | `"status: killed" in final_status` | NO |
| 3 | proc_status substring | `tests/tools/test_proc_tools.py:382` | `"status: exited" or "status: killed"` | NO |
| 4 | proc_status substring | `tests/tools/test_proc_tools.py:1163` | `"status: killed" in status` | NO |
| 5 | proc_status substring | `tests/tools/test_proc_tools.py:1166-1167` | `"timed_out: true" in status` | NO |
| 6 | proc_status substring — **not previously cited** | `tests/tools/test_proc_tools.py:1233` | `"status: exited" or "status: killed"` | NO |
| 7 | `ProcessInfo` attribute | `tests/tools/test_proc_tools.py:1831` | `info.status in ("exited","killed")` | NO (status stays `"killed"`) |
| 8–13 | `ProcessInfo` attribute group — **not previously cited** | `tests/tools/test_proc_tools.py:1614, 1655, 1692, 1944, 1992, 2038` | `info.status == "killed"` | NO |
| 14 | proc_list header | `tests/tools/test_proc_tools.py:664` | `"process_id \| status \| command \| uptime"` | NO |
| 15–29 | bash success-string group (`"EXIT CODE: N"`) — ≈30 sites in 8 files | `tests/tools/test_bash_spawn_stamp.py` · `tests/test_tools.py` · `tests/tools/test_bash_secret_env.py` · `tests/integration/test_secrets_v1_endtoend.py` · `tests/test_resource_pid_registry.py` · `tests/tools/test_auto_kill_integration.py` · `tests/integration/test_compaction_e2e.py` · `tests/manual/question_pause_strand_repro.py` | the success string shape | NO — the RAM arm preempts the success arm **entirely** and only for a RAM-killed command; every other command's string is byte-identical |
| 30–33 | bash `"STDOUT:"` group — ≈10 sites in 4 files | `tests/tools/test_bash_secret_env.py` · `tests/integration/test_secrets_v1_endtoend.py` · `tests/tools/test_auto_kill_integration.py` · `tests/unit/test_mcp_quote_sanitization.py` | `STDOUT:\n<value>\n\nEXIT CODE: N` | NO (same argument) |
| 34 | bash **timeout string** | **ZERO pins** (`grep -rn "Command timed out" tests/` ⇒ nothing; the only occurrence is the producer `daemon/tools/bash.py:1160`) | — | **NO pin exists** — the previous §6 item (5) claim that it "must never change" is corrected: it is guaranteed byte-identical **only when no breach was observed** (§4.3 precedence; §9) |
| 35 | chart-tools legacy contract | `tests/test_chart_tools_legacy_error_contract.py` (58 lines) | `generate_chart` only — imports neither bash nor proc | **INAPPLICABLE** (arch §1.1 P6) |
| 36 | daemon-side `json.loads` of a bash/proc return string | **NONE** | — | — |
| 37 | daemon-side consumers of the proc surface | `daemon/tools/proc_tools.py:2162` (`proc_status` → `manager.get_status`), `:2231` (`proc_list`) | — | NO |
| 38 | LLM/agent-facing consumption | `agents/aegis/workflow.md:37` · `agents/planner/tools_note.md:75` · `agents/planner[v2]/tools_note.md:75` · `agents/devops/tools_note.md:70,87` · `agents/coder/soul.md:87` · `agents/worker/soul.md:78` — **none parse the strings** | the tool result string reaches the model verbatim | NO (additive lines only) |

**Load-bearing rules (unchanged):** (1) the feared `test_chart_tools_legacy_error_contract.py` pins chart tools only — inapplicable; (2) no daemon-side `json.loads` consumes a bash/proc return string — the only machine consumer is the agent/LLM; (3) the real pin surface is the `proc_status` substring assertions and the `ProcessInfo.status == "killed"` attribute pins — satisfied by *adding* lines and keeping `status="killed"`; (4) `pid_registry.VALID_EXIT_KINDS` **must** be extended with the named `ram_killed` constant or the RAM arm raises inside `mark_exit` (`pid_registry.py:409-413`); (5) the bash success string, the bash timeout string (byte-identical when no breach was observed), the `proc_status` block and the `proc_list` header must never change.

**Ownership (AMENDED — the gate artifact was orphaned in Phase 2 and double-claimed in Phase 5):** **Phase 2 owns the artifact** as a **pins-only** artifact (no tool wiring — Phase 2's objective forbids tool wiring); Phase 5's T1 collapses to a one-line cross-reference ("reference the Phase-2 artifact; do not record a second artifact and do not re-derive it"). The "22 consumers" claim is deleted from **arch §6, plan-overview and phase5**; every occurrence is replaced by "the §6 enumerated census".

---

## 7. Module decomposition + thread lifecycle — **AMENDED (both passes)**

```
daemon/services/ram_guard/
  __init__.py     facade: get_ram_guard(), register/read_verdict/release
  tree_reader.py  scan_proc / resolve_members / read_tree_rss / read_tree_pss / exceeds   (pure, injectable)
  verdict.py      SpawnRecord, PeakRecord, breach predicate, OVERCOUNTED re-arm (§4.1a)
  ladder.py       ram_kill_ladder + the P0–P7 pre-kill predicates + the P8 post-kill probe + the slice-probed grace
  guard.py        the TICK thread + the EXECUTION-LANE thread + registry + pending set + GuardCounters + liveness sweep + the emitter call site
  errors.py       format_ram_envelope (one producer, two surfaces) + build_ram_event_payload (the allow-list)
  events.py       RamBreachEmitter type alias ONLY (stdlib typing; no emitter implementation here)
daemon/services/ram_guard_events.py   OUTSIDE the package: the bus-backed emitter impl + the loop bridge
```

**Import direction (AMENDED — the literal replacement for the frozen sentence):**

> Import direction: `tools/* → ram_guard` only; **`ram_guard → stdlib` only. `ram_guard` imports NOTHING from `daemon/` — no `tools/*`, no `resource_monitor`, no `repositories`, no `event_bus`, no `main_loop_bridge`, no `bash_spawn_reaper`. The event dependency is satisfied by an INJECTED callable (`RamBreachEmitter`), whose implementation lives OUTSIDE the package (`daemon/services/ram_guard_events.py`) and is wired at the composition root (the FastAPI lifespan).** The stat parse is reimplemented injectably because `list_group_members` hardcodes `/proc` and is per-spawn-priced.

**Thread lifecycle:** **FastAPI lifespan** (`app.state.ram_guard` owns BOTH threads; own `try/except` + INFO; OFF ⇒ **neither thread**, `app.state.ram_guard = None`; shutdown `.stop()` + **detached join of both threads** — the house form at `daemon/api.py:4144-4152`). **New module constants (iteration 002):** `RAM_GUARD_PENDING_MAX = 2` · `RAM_GUARD_GRACE_POLL_SLICE_SECONDS = 0.025`. Bash seams (verified): S1 `:952-965`, S2 `:1062-1065`, S3 `:1071` + `:1174-1214` (+ new RAM arm before `:1110`), S4 `:1143-1160`, S5 `:965/:483/:1073/:1133/:1196` (+ the generic arm `:1223`), S6 `:1110-1123` (note: `peak_rss_kb` is **KB** — one explicit bytes→KB conversion at this boundary or the two numbers disagree by 1024×). Proc seams (verified): S7 `:1231/:1235/:1241-1245`, cleanup tuples `:1296-1301` + `cleanup_instance`/`cleanup_all`, S10 `:786/:855-875` (`ram_killed` joins `timed_out`/`user_stopped` as the third authoritative input), S11 `:966-1086` (environ check demoted to secondary).

**Phase ownership of the event wiring:**

| # | Phase | Owns | Seam site |
|---|---|---|---|
| (i) **the HOOK** | **Phase 2** (new task) | the guard's `emitter` ctor arg (default `None` = no-op), the call site at the `WATCHING→KILLED` transition, and `build_ram_event_payload` | `daemon/services/ram_guard/guard.py`, `errors.py` |
| (ii) **the EMITTER + the WIRING** | **Phase 5** (new task) | `EventKind.RAM_LIMIT_EXCEEDED`; `daemon/services/ram_guard_events.py`; the non-blocking bridge; the lifespan wiring | the FastAPI lifespan, next to the resource-monitor block (`daemon/api.py:2440-2491`) — `manager` and `manager._loop` are both in scope there |

---

## 8. Sequencing verdict (7 phases — keep the shape, five amendments)

1. **Phase 2 absorbs the envelope helper + the consumer gate + the event HOOK.** `errors.py` and the frozen envelope land with the substrate so Phases 3/4 build against them; the consumer-gate artifact is §6 of this document (reference it — do not re-derive) and is owned by **Phase 2** (Phase 5's duplicate claim collapses to a cross-reference). Phase 2 also adds the **per-tick cost measurement** and a **realistic allocation-rate measurement** (R is assumed ∈ [1, 3] GiB/s today; the grace default and the ceiling arithmetic depend on it) — and, per §3.4, the **overshoot law it measures must include the confirm deadline** (`R × 0.45 s` typical / `R × 0.70 s` worst / `R × 1.21 s` contended at N=2). The event **hook** lands here; the event **emitter** does not (Phase 5). **The execution-lane thread lands here** (DQ2's two-thread shape is substrate, not lane work).
2. **Phase 3 (bash first — the Oct-30 ballooners were bash `python3` commands) must additionally:** respect the **one-shot `mark_exit` ordering** and the **verdict-publication ordering** (§4.3.1); add the guard release to the generic-exception arm; **make the success-path release conditional on the watch being finished** (§4.3.1 item 4 — the planner's blocker-B2 fold basis); verify the `pgid = proc.pid` fallback invariant (`:955-957`) is test-pinned; perform the single bytes→KB conversion at S6.
3. **Phase 4 (proc) is COMMISSION-MANDATORY and unblocked** — strictly *less* coexistence risk (no durable row ⇒ the escapee-net question is bash-only). It must additionally: **capture pgid + start_time at spawn** (fixing P5 — the proc lane re-derives pgid at kill time today), add `ram_killed: bool` (surfaced **positively** in `proc_status` — `user_stopped` is never surfaced today, a real observability gap), and add `user_stopped: true` to `proc_status` while there (🟢 one line).
4. **Phase 5 shrinks** to the config class + observability surfaces + fence re-run (the envelope moved to Phase 2) **+ the event emitter and its wiring** (§4.5, §7) **+ the root-`Config` wiring of `RamGuardConfig`** (§4.6).
5. **Phase 7 shrinks and re-weights**: no migration at all; its primary deliverable becomes the **operator options memo** (§3.4) + the honesty set (§3.5) + the follow-up routing. It can run in parallel with 3/4. **The operator documentation the plan set promises is owned here**: the knob table, the env-off procedure, the counter names and the endpoint contract (`GET /api/resources/ram-guard`).

**Phase 6 additions:** dead-guard / blocked-tick cases — now **per-thread** (tick dead, tick wedged, lane dead, lane wedged, pending-overflow); the OVERCOUNTED re-arm path (with the **bounded** formula); the one-shot `mark_exit` ordering **and** the verdict-publication ordering; the PSS-confirm fail-open path; the event-drop path (no live loop ⇒ dropped + counted); mutation-proof per the existing house rule. **Test-plan amendment:** Success Criterion 3's `observed_peak_bytes >= limit_bytes` must become "**the deciding value** (`confirmed_pss_bytes`, or `observed_peak_bytes` on a fail-open trigger) `>= limit_bytes`" — under the hybrid the RSS peak and the deciding PSS value are deliberately distinct quantities.

---

## 9. Risks (aggregated, severity-ordered)

- 🔴 **The guard's own death is silent and fatal** (no unit cap exists; a dead guard ⇒ kernel OOM takes the daemon ⇒ Oct-30 repeats). Mitigation is *external* (the operator drop-in) + the health endpoint (now per-thread heartbeats — a wedged lane is caught by `last_lane_progress_at`, a wedged tick by a frozen `last_tick_at`). Highest-severity operational item.
- 🔴 **The orphan class is out of reach** — orphans of dead incarnations are covered only by the unit cap (E8). The guard must never be described as protecting the box from that class.
- 🔴 **The PSS confirm is the only fairness gate** — made advisory, the guard false-kills fork-heavy legitimate jobs at factor ≈N; deadline set too tight, fail-open silently degrades it to pure-RSS. **(Iteration 002: the pending-set overflow rule exists precisely so lane congestion can NEVER fire the fairness gate — `pss_confirm_unavailable` is reserved for genuine confirm failures.)**
- 🔴 **`mark_exit` is one-shot** — a wrong arm ordering permanently erases the RAM verdict. **Now ordered explicitly** (§4.3.1): publication before any `mark_exit` / registry release, and the lane-side wait is bounded at 1270 ms (§4.3.1 item 2).
- 🔴 **The guard loses the race against the cgroup cap above ≈0.5 GiB/s on this host** (§3.4; **≈0.28 GiB/s if a second tree is simultaneously breaching** — iteration 002). The published pair's role is the **orphan class**; the guard-vs-cap race is winnable only below the measured-bottom rate, and the measured band is 1–3 GiB/s. Above it, the daemon is the casualty. Restoring the layering requires a ≥ ~14.5 GiB host, not a different ceiling. **This must be stated in the memo, the runbook and the honest-peak envelope — never papered over.**
- 🟡 PSS under-counts pages shared with processes *outside* the tree (direction: fewer kills; state in the runbook). Shared scan is O(host pids) per tick (9.49 ms @250 ⇒ ~40 ms @1000 — size the interval against the *worst* host pid count). `smaps_rollup` linearity beyond 725 MiB is inferred, not measured. Partial output is discarded on a RAM kill (accepted; stated in the message's recovery hint). Event-only durability loses the verdict if the daemon dies between kill and lane observation (the case DQ10(a) already accepts) **and** if the event cannot be delivered (§4.5 — dropped + counted + logged). The coincidence case changes the legacy bash timeout string when a breach was also observed (no test pins it; record it).
- 🟡 **The bounded re-arm trades containment for confirm frequency** (§4.1a): a smaller `CAP` means the trigger fires earlier, so the confirm runs more often (up to the tick-detection rate ⇒ ≈17.5 % lane duty at a 250-pid host) and the tree's real footprint can reach `F_max` ≈ 3.86 GiB at R = 1 GiB/s. Both are disclosed in §3.5 items 10–11.
- 🟡 **The `/proc` reads are uninterruptible** (iteration 002, E10): the 250 ms deadline is a post-hoc elapsed check; a never-returning read wedges the reading thread. On the lane this stalls the kill path only (measured on schedule, disclosed, `last_lane_progress_at` exposes it); on the tick it stalls measurement entirely (disclosed — the frozen `last_tick_at` is the signal; no supervisor exists by ruling).
- 🟢 Naming collision: `bash.py:522` "RAM tier" means the RAM-resident registry bucket — the new concept must not reuse the phrase.

---

## 10. Decisions pending (for the dispatcher / user — I could not close these within my mandate)

1. **The 3 GiB default amends the commissioned 8 GiB.** The commission's words say 8 GB; the measured host cannot ship it (§3.3). I have ruled 3 GiB as the binding default **with an explicit raise-path**. If the user insists on 8 GiB, the honest consequence is: the feature ships as a tripwire that fires at-or-after daemon death on this host — i.e. cosmetic until the host is resized to ≥ ~14.5 GiB. *Under "keep 8": ship the knob at 8 GiB default and the memo states the host must grow first. Under "accept 3": nothing further.*
2. **The unit-cap drop-in is a user action** (`7G / 8700M` recommended; §3.4). This lane does not execute it. Without it, the guard is the *only* enforcement on the box and the orphan class is uncovered.
3. **Pre-freeze check (Phase 2 entry) — AMENDED with the method / owner / bounded fallback (approver iteration 002):** attribute today's unexplained **8.11 GiB unit peak**. **Method (read-only, ONE measurement session; no load injection, no repro attempt, no daemon restart):** (i) the unit's cgroup `memory.stat` (anon vs file) + `MemoryCurrent`/`memory.peak` — separates daemon anon from page cache; (ii) a `ps` rollup of the unit's processes vs the daemon's tracked children (resource-monitor tables + open `bash_spawn_registry` rows) — attributes the footprint between daemon baseline, tracked children, and untracked/orphaned; (iii) the Oct-30 journal OOM windows (E3/E8) for the incident-side attribution. **Owner:** Phase 2 Task 20 executes it and records the outcome in the phase report; Phase 7 carries it into the operator memo. **Bounded fallback:** if the attribution is inconclusive within the session, the gate resolves **CONSERVATIVELY** — the unattributed residue is treated as daemon-side (`D_max` shifts up by `min(residue, 1.0 GiB)`) and the §3.4 table is re-published with the conservative `D_max`; **the freeze is NEVER blocked on inconclusiveness** (no branch blocks it: a command-tree attribution is caught by the 3 GiB ceiling; a page-cache attribution shifts `D_max`; an orphan attribution is the unit cap's class by construction).
4. **The host-resize decision is now load-bearing.** The guard-vs-cap race is lost above ≈0.5 GiB/s on this host (§3.4; ≈0.28 GiB/s contended). The user owns the choice: accept the daemon as the casualty above that rate (the cap is the containment layer), or grow the host to ≥ ~14.5 GiB to restore the layering. **The feature still ships at 3 GiB either way** — this decides the *layering*, not the ceiling.

---

## 11. Gaps

- **W1 wedge:** the original data-flow worker (`ffc1b0ef`) wedged after 12 tool calls (1 h 53 m silent) and was terminated; the single permitted replacement (`2ead7080`) delivered the full assignment with measurements. Anomaly logged (`report_id=1d95b83b`). No coverage gap remains — DQ1/DQ3/reader-contract coverage is complete from the replacement's report.
- **Council quorum:** 2 of a possible 4 councilors (`agentic`, `coding`) — normal quorum, full-confidence synthesis; the models list was capped at 2 by the convening call.
- **Not verified by anyone:** `smaps_rollup` exposure for every pid beyond those probed; multi-TiB sparse-mapping cost; macOS behavior (the reader contract is Linux-only by design — Darwin stays inert per plan Assumption 1); executed test runs (all "no net-new reds" claims are argued from source, not run — Phase 6's job).
- **Amendment-pass gaps:** (a) iterations 001/002 were produced by **workers + architect adjudication, not a council** — the amendments are surgical and reversible, and the structural decisions were already council-reviewed; (b) the **R measurement** (Phase 2 T19) is still an assumption — every rate-conditional number in §3.4 is provisional until it lands, and the §3.4 table must be re-published with the measured R; (c) the **consumer census is repo-grounded only** — no external (non-repo) consumer of the bash/proc strings was checked; (d) the **SQLite `event.kind` CHECK** remains stale for 8 pre-existing kinds (16 members − 8 listed — corrected at iteration 002) — house debt, deliberately not fixed here; (e) no test was executed in either pass; (f) the contended-overshoot numbers (§3.4 N=2) are derived from `T_service_worst = 510 ms`, which includes an estimated ~10 ms probe cost — Phase 2's measurements should confirm the probe term.

---

## 12. Plan reconciliation (applied to `plan-overview.md`)

A compact **"Phase 1 Rulings (architect-adjudicated, binding)"** block was inserted after DQ10 and before "## Critical Interaction", recording: the 10 rulings one-line each, the R1 resolution (absolute / guard-inner / 3 GiB / unit-cap routing), the trigger-on-current-sample amendment, the PSS-confirm mandate, and pointers to this document. No other plan text was altered — the plan council reconciles the phase files against this document.

**Amendment-pass reconciliation (the planner folds):** `plan-overview.md`'s Phase-1-Rulings one-liners for **DQ1** (unbounded re-arm), **DQ2** (one thread → two), **DQ4** (vocabulary + leaderless semantics + the 16-count), **DQ6** ("22 consumers"), **DQ8** (payload wording + counters + the per-thread heartbeat) and the **Coupling Map** (P1↔P5 and **both** P2↔P5 cells — iteration 002's blocker B5) are superseded by this document — see §14.2, §16.

---

## 13. Addendum — leader rulings (council cycle 1, appended by the plan writer)

Two rulings were returned by the **leader** after this document was completed. They
are **binding** and are recorded here so this document does not contradict itself.

### 13.1 Q1 — proc lane shape (LEADER-CONFIRMED, supersedes §7's "(4th task)" parenthetical)

> **LEADER RULING Q1 — CONFIRMED: reading (a). NO 4th task.**
> The proc lane is **register with the guard → status mapping → release**.
> The §7 seam list's "(4th task)" parenthetical is **superseded**: there is no
> asyncio watchdog task in the proc lane. The guard threads (DQ2) own the measurement and the breach decision (tick thread) and the kill (through `ram_kill_ladder`) and the verdict publication (execution-lane thread). The proc lane creates **exactly three** tasks — reader, exit,
> timeout — exactly as today. A **NEW pgid-capture task** (capture pgid + start_time
> at spawn, fixing the §1.1 P5 TOCTOU) is added per **§8 item 3**.

*(AMENDED: the cross-reference "`§8.3`" was dangling — §8 has no subsections — and the "task" wording collided with Phase 4's "exactly three tasks". The pgid capture is a **field capture inside the existing spawn path**, not a fourth task and not a new asyncio task; the reference is **§8 item 3**. Iteration 002: "the guard thread" wording updated for the two-thread guard.)*

### 13.2 Q2 — leaderless-tree failure mode (LEADER-RULED, §4.2-P5 contract delta)

> **LEADER RULING Q2 — ruling (i): the watch STAYS ALIVE until the group member
> count reaches 0, and the identity RE-ANCHORS on the surviving group members.**
> When the group leader exits while a grandchild is still ballooning (the ordinary
> `cmd &` shape), the guard does **not** drop the watch. This is a **§4.2-P5
> contract change**, **not** an accepted limitation: P5's leader-`starttime` gate
> governs the **kill** decision, but the **watch** itself is anchored on group
> membership and survives the leader's exit. The re-anchored identity is the
> surviving group members; the watch is released **only** at member count 0.
> Wired into `phase2-plan.md` (T10) and asserted by a test that backgrounds a
> ballooning grandchild and lets the shell exit.

*(AMENDED by the approver-iter-001 pass: Q2=(i) stands and the §4.2 freeze is **LIFTED for its propagation**. The propagation is now complete in §2 DQ4, §4.1, §4.2 (P5/P5a/P7), §5 and §4.3.1; the canonical predicate strings are listed in §4.2. The leader's text did not address an **unreadable** (as distinct from absent) leader stat — resolved here as `refused:leader-unreadable` (fail-closed), and the pgid-recycle check **P5a** was added because the re-anchor would otherwise be able to kill a recycled pgid. Iteration 002 added the release-boundary consequence: a live watch is never ended by a lane exit — §4.3.1 item 4.)*

### 13.3 Scope note

Neither ruling alters §4.1 (the reader contract), §4.3 (the typed-error contract), §4.4 (the verdict record), DQ1, DQ3, DQ5, DQ6, DQ7, DQ8, DQ9, DQ10, or the R1 resolution. Both are consumed by the phase docs' **rev 2** (`phase2-plan.md`, `phase4-plan.md`, `phase3-plan.md`, `phase5-plan.md`, `phase6-plan.md`, `phase7-plan.md`, `test-plan.md`, `plan-overview.md`).

### 13.4 Amendment passes

The approver **rejected** this plan set at iteration 001 (7 blockers + ~18 notes — ruled in §14) and at iteration 002 (5 blockers — B1/B3 ruled in §16; B2/B4/B5 are planner folds whose contract bases are §4.3.1 item 4, §14.1 blocker 7's rule, and §15 B14 verbatim). The leader's two rulings (Q2=(i) propagation and the verdict-publication ordering) are propagated throughout.

---

## 14. AMENDMENT LEDGER — approver iteration 001 (7 blockers + sweep), ruled by the architect

### 14.1 Blocker → resolution

| # | Approver blocker | Resolution (binding) | Where |
|---|---|---|---|
| **1** | **LEADERLESS-TREE CONTRACT CONTRADICTION** | **Q2=(i) propagated, single semantics.** The watch stays alive until the group member count reaches 0; identity = captured pgid ∧ surviving-member group membership; starttime gate applies to the leader only while alive; `already-dead:group-empty` fires **only** at member count 0 (kept-and-redefined, not deleted); the canonical predicate strings are pinned; the leaderless case produces **no** reason code. **Two refinements added**: the leader's stat is split into **absent (ENOENT ⇒ re-anchor)** vs **unreadable (⇒ `refused:leader-unreadable`, fail-closed)**, and a new **P5a pgid-recycle check** was added because the re-anchor would otherwise be able to kill a recycled pgid. | §4.1, §4.2, §5, §2 DQ4, §13.2 |
| **2** | **OVERCOUNT RE-ARM UNBOUNDED** | **Bounded re-arm + growth governor** (§4.1a): `rearm_threshold_bytes = max(prev, ceiling + min(shared_extra, 32 MiB))`, confirm gated by 128 MiB of growth; both constants are **module constants** (not knobs). **§3.4 redone** with the corrected overshoot law (`R × (poll + confirm + grace)` = `R × 0.70 s` worst) and the new `F_max`/`floor` frame ⇒ the bounded rule restores a **feasible** unit cap (R ≤ 1.034 GiB/s worst frame) where the unbounded rule had **none**. **L110 withdrawn** and replaced by the rate-conditional truth statement. `rearm_threshold_bytes` added to **both** key sets. Residual over-count risk accepted and disclosed (§3.5 items 10–11). | §3.3, §3.4, §3.5, §4.1a, §4.2, §4.3, §4.4, §4.5 |
| **3** | **DURABLE EVENT OWNERLESS** | **Injected emitter callable** (stdlib-pure guard) + a **non-blocking** bridge on the **hardened manager accessor** (`manager._get_event_loop()`, `manager.py:3097` — **not** `MainLoopBridge`, whose loop is `None` on a no-worker-pool boot). §7's import freeze **amended** (the emitter's implementation lives outside the package). Drop semantics = **visible** (`ram_events_dropped` + the transition log line). Ownership: **Phase 2 = the hook, Phase 5 = the emitter + the wiring**. | §4.5, §7, §2 DQ8/DQ10, §8 |
| **4** | **VERDICT-PUBLICATION RACE** | **Leader ruling 2 encoded**: publication BEFORE any `mark_exit`/registry release; the lane-side wait is bounded (deadline re-derived at iteration 002 to **1270 ms**, §4.3.1 item 2); the release follows the publication and is **conditional on the claim** (a claimed record's release is deferred to the lane); the proc lane consults the verdict before finalizing `info.status`. **Phase 3 T5's acceptance is re-written** to be achievable and observable (a recording fake, not inspection). | §4.3.1, §4.2, §8 item 2 |
| **5** | **`RamGuardConfig` UNWIRED** | **Wired**: `RamGuardConfig(BaseSettings)` + the root field `ram_guard: RamGuardConfig = Field(default_factory=RamGuardConfig)` (mirroring `resource_monitor`); the bare `ENSEMBLE_RAM_GUARD` switch is a **non-pydantic** reaper-semantics parser carried as a derived value; out-of-range ⇒ **boot aborts** (`Config(**config_dict)` at `config.py:8795` is outside the try/except); YAML **comment-only**; OFF = complete no-op. The **assertion shapes** are pinned — and the boot-abort rung **is assertable today**, so Phase 5's "untestable disclosure" escape is **deleted**. | §4.6, §2 DQ7, §8 item 4 |
| **6** | **REASON VOCABULARY INCOMPLETE** | **Root cause found: P8 is not a pre-kill predicate** (the frozen list mislabelled it) ⇒ the pre-kill gate is **P0–P7** and P8 is the post-kill verification step (`killed:tree-incomplete`). The vocabulary is completed to **16 codes** (the enumeration is authoritative — §16.2) with one rename (`refused:platform-win32` → `refused:platform-unsupported`); the counter set is pinned exactly (**`ram_kill_refusals_p0 … p7`, no p8**) plus the new `ram_overcounted`/`ram_rearms`/`ram_confirm_failures`/`ram_events_emitted`/`ram_events_dropped`. | §4.2, §2 DQ8 |
| **7** | **PROSE-SATISFIABLE ACs** | **The phase5 T5 escape is DELETED** (the rung is assertable via `pytest.raises(ValidationError)` around `load_config` — §4.6). The remaining half is a **plan-level rule the contract now states**: *every acceptance criterion that asserts an absence must carry a counter-mutation row in the §4 mutation table* — absence-only and raw-text pins are prose-satisfiable. Plus the fixture split: **P0–P7 refusal legs use `kill_fence`; the ladder + P8 legs use `kill_census`** (`kill_fence` hard-raises on any non-zero signal and therefore cannot host the ladder's own TERM) — see the sweep below. | §4.6, §15, §5 |

### 14.2 Sweep items (approver notes) — resolutions

| Note | Resolution |
|---|---|
| **Coupling-matrix P2↔P5 contradiction** | The Coupling Map's P2↔P5 cells (BOTH directions) said `independent`, while the phases table (Phase-5 row) says `tight with 2, 3, 4` and Phase 5's own tasks depend on Phase 2's envelope/counter/heartbeat producers. **The Coupling Map cells are wrong; the phases table is right.** Replace BOTH cells with the B14 string (§15) — **renumber-proofed at iteration 002: it cites the dependency semantically, not by task number, so a renumber cannot stale it**. **Note:** this contradiction is resolved by the phase dependency columns, **not** by the leader's Q2 (which is orthogonal to the phase graph). |
| **Stale P1↔P5 annotation** | The P1 row's P5 cell read `tight (P5 implements DQ6+DQ7)` — the **DQ6** half is **stale** (the consumer-gate artifact moved to Phase 2). Replace with `loose (DQ6's consumer-gate artifact now lives in P2; P5 implements DQ7 — the config surface)`. |
| **DQ6 consumer-gate double-ownership + the "22-consumer" citation** | **"22 consumers" is withdrawn** — no such list exists; §6 now carries the **enumerated census** (the table IS the artifact; **counts are derived at fold time and never pinned** — iteration 002). **Phase 2 owns the artifact** (pins-only); **Phase 5 T1 collapses** to a cross-reference. Two §6 corrections fall out: the previously uncited pins (`test_proc_tools.py:1233` and the six `info.status == "killed"` attribute pins) must be pinned too, and the bash **timeout string has ZERO test pins** — it is guaranteed byte-identical only "when no breach was observed". |
| **Proc-lane verdict queryable-only asymmetry** | **Query-only is ACCEPTED with disclosure** — the proc lane must **not** gain a typed error surface (the return value is fixed by `proc_run`'s contract, and appending to a process-id string is the shape DQ6 rejected). Three compensating disclosures are **mandatory**: the §3.5 item-12 sentence in the runbook **and** the envelope honesty set; the matching line in the `proc_status` LLM-facing docstring; and Phase 4's existing `ram_breach:` / `ram_killed: true` pins kept. The commission's phrase "returns … error" is **scoped to the bash lane** in the plan set. |
| **P8 needs a kill_census fixture, not kill_fence** | **Confirmed and ruled.** `kill_fence` hard-raises on ANY non-zero signal (`tests/unit/services/test_bash_spawn_reaper.py:64-81`) ⇒ it can only host legs where **no signal may fire** (the P0–P7 **refusal** legs). `kill_census` **records** `(pgid, sig)` without delivering (`:84-108`) ⇒ it hosts the **ladder + P8** legs. P8's probe additionally needs an injectable **fake `proc_root`** whose member stat lines transition alive→gone at the recorded KILL — the census alone cannot make the probe observe a dead tree. Test-name shape: `test_p<n>_<case>_<expected_status>`. |
| **"reap children / no zombies" has no owning task; no subreaper exists; is B5 vacuous?** | **B5 IS VACUOUS as written and is ruled out.** Verified: **no** `PR_SET_CHILD_SUBREAPER` / subreaper / `SIGCHLD` handler anywhere in `daemon/`; the only `waitpid` use is the dedicated upgrade-journal reaper (`upgrade_journal_sweep.py:3170-3176`); the bash lane's direct child is reaped by the pre-existing `await proc.wait()` (`bash.py:1065`). The guard kills a **GROUP**; grandchildren are re-parented to init/PID 1, which reaps them; **the daemon is not the reaper of a killed tree's grandchildren**. **B5 is rewritten** to what the guard actually owns (the guard never forks; the direct child is reaped by the pre-existing `proc.wait()`; after the kill the group has no LIVE member; a transient `Z` grandchild is init's business), **Phase 3 owns the ordering**, **Phase 6 owns the assertion**, and the runbook carries the §3.5 item-13 statement verbatim. |
| **`instance_id=None` spawns unguarded vs the DQ8 assumption** | **NARROWED at iteration 002 (§16.3):** the iteration-001 claim ("an `instance_id=None` spawn IS guarded") is **withdrawn** — it is not implementable without widening the bash seam's registration gate, which lives inside `if instance_id is not None and proc is not None:` (`bash.py:951-1005`), and that restructure is NOT commissioned. When instance identity is absent, the spawn is **not registered with the guard in v1** (today's unregistered path); a ballooning tree from an instance-less call is invisible to the guard and only the unit cap covers it — disclosed (§3.5 item 9b); widening the gate is a **routed follow-up**. DQ8's `instance_id=None ⇒ counter + log only` sentence remains as **defensive** behavior if a registered record ever carries a None instance_id. |
| **Operator docs promised but unowned** | **Phase 7 owns them** (§8 item 5): the knob table, the env-off procedure, the counter names and the `GET /api/resources/ram-guard` endpoint contract. |
| **`event.kind` SQLite CHECK is stale** | **Qualifying sentence added to DQ10** (§2), **corrected at iteration 002**: 16 members / 8 listed / stale-for 8 (E10). PostgreSQL (production) is unaffected (the SQLModel column carries no CHECK, `models.py:74`); the CHECK is **pre-existing house debt** and is **not** fixed by this feature (DQ10: no migration in v1). |
| **arch §13.1's "NEW pgid-capture task" collides with Phase 4's "exactly three tasks"** | Fixed in §13.1: the pgid capture is a **field capture inside the existing spawn path** — not a task, not a fourth asyncio task; the dangling "`§8.3`" reference is corrected to **§8 item 3**. |
| **arch §1.1 P7 wording imprecision** | Fixed: the `proc_root` is injectable; only the *pid* is hardcoded to `self` (`proc_readers.py:769`). |
| **Anchor drift in the house precedent** | Recorded in E9: the house bridge precedent is at `report_delivery_recovery.py:1499-1507` (**not** `:1511-1515`); `manager.py:8586-8596`/`:9118` moved to `:12004`/`:12638`. The stale docstring at `report_delivery_recovery.py:106`/`:131` is house doc debt, not this feature's. |

### 14.3 Changed anchors (iteration 001, for the diff)

| Old line (cycle-1 doc) | Change |
|---|---|
| L19 (exec summary) | + amendment-pass summary block (items 4–8; item 6's count corrected at iteration 002) |
| L46 (P6 row) | + the uncited proc_status pins |
| L48 (P7 row) | wording: the root is injectable, only the pid is hardcoded |
| L58 (DQ1) | re-arm clause → the bounded formula (§4.1a); + the lane execution context (iteration 002) |
| L61 (DQ4) | + Q2=(i) semantics + the 16-code vocabulary pointer |
| L63 (DQ6) | "22-consumer" claim → the enumerated census; + the Phase-2 ownership |
| L64 (DQ7) | + the root-Config wiring (§4.6); + the re-arm/pending/slice constants are module constants |
| L65 (DQ8) | payload wording; the counter list; the per-thread heartbeat (iteration 002) |
| L67 (DQ10) | + the event ownership (§4.5) + the SQLite CHECK qualifier (corrected counts at iteration 002) |
| L88–L90 (3.2) | + the `F_max` term in the unit-survival floor |
| L94–L100 (3.3) | + the corrected overshoot law + the contended companion (iteration 002); the raise-path is VOID on this host |
| L106–L117 (3.4) | the table re-done with bounded numbers + the N=2 disclosure row (iteration 002); L110 withdrawn and replaced |
| L122–L124 (3.5) | + honesty items 10–13 + items 9/9b (iteration 002) |
| L142–L153 (4.1) | + the membership carry-forward rule; + the per-function thread attribution (iteration 002) |
| NEW 4.1a | the bounded re-arm (confirm-frequency paragraph re-scoped at iteration 002) |
| L157–L164 (4.2) | the 16-code vocabulary; P0–P7 pre-kill; P8 post-kill; P5 three-way split; + P5a; + the canonical predicate strings |
| L166–L173 (4.3) | + `rearm_threshold_bytes`; + the message clause; + the proc-lane query-only note |
| NEW 4.3.1 | the verdict-publication ordering (deadline re-derived to 1270 ms at iteration 002; + the conditional releases) |
| L177 (4.4) | + `last_confirm_rss_bytes` / `rearm_count` / `confirm_count`; the sticky OVERCOUNTED state; the honest-peak bound + the confirm term |
| NEW 4.5 | the event owner / emitter / bridge (thread attribution updated at iteration 002) |
| NEW 4.6 | the `RamGuardConfig` → root `Config` wiring |
| L187–L193 (5) | the degradation row replaced; + the per-thread death modes + the pending-overflow row (iteration 002) |
| L197–L199 (§6) | the census replaces the "22" claim; + the ownership; counts never pinned (iteration 002) |
| L205–L214 (§7) | + `events.py` + `ram_guard_events.py`; the import-freeze sentence replaced; + the two-thread lifecycle + the new constants (iteration 002) |
| L218–L226 (§8) | + the Phase-2 hook / Phase-5 emitter split; + the config wiring; + the Phase-7 operator-docs ownership; + the B2 fold basis (iteration 002) |
| L230–L237 (§9) | + the race risk; + the re-arm trade; + the uninterruptible-read risk (iteration 002) |
| L241–L245 (§10) | + the host-resize decision; + the T20 method/owner/fallback (iteration 002) |
| L249–L253 (§11) | + the amendment-pass gaps (counts corrected at iteration 002) |
| L263–L298 (§13) | + §13.4; Q1's cross-reference fixed; Q2's propagation note (+ the release-boundary note at iteration 002) |
| NEW §14, §15, §16 | the ledgers + the string sets |

---

## 15. STRINGS TO PIN CHARACTER-FOR-CHARACTER (the planner's binding list)

**B1 — the bounded re-arm (§4.1a):**
```
rearm_threshold_bytes = max(previous_rearm_threshold_bytes, ceiling_bytes + min(shared_extra_bytes, RAM_GUARD_REARM_MAX_EXTRA_BYTES))
confirm_due = (summed_rss_bytes >= rearm_threshold_bytes) and (summed_rss_bytes - last_confirm_rss_bytes >= RAM_GUARD_REARM_MIN_GROWTH_BYTES)
RAM_GUARD_REARM_MAX_EXTRA_BYTES = 33554432
RAM_GUARD_REARM_MIN_GROWTH_BYTES = 134217728
```

**B2 — the envelope key set (ordered, exact):**
```
ok, code, surface, limit_bytes, rearm_threshold_bytes, observed_peak_bytes, peak_is_lower_bound, poll_interval_ms, metric, trigger_metric, confirmed_pss_bytes, under_report_bound_bytes, pid, pgid, tree_complete, members_total, members_killable, members_unreachable, grace_ms, killed_at, ram_breach_observed, coincident_timeout, timeout_seconds, message
```

**B3 — the event payload key set (ordered, exact):**
```
ok, code, surface, limit_bytes, rearm_threshold_bytes, observed_peak_bytes, peak_is_lower_bound, poll_interval_ms, metric, trigger_metric, confirmed_pss_bytes, under_report_bound_bytes, pid, pgid, tree_complete, members_total, members_killable, members_unreachable, grace_ms, killed_at, ram_breach_observed, coincident_timeout, timeout_seconds, message, trigger, state, tracking_id, breach_at
```

**B4 — the reason set (ordered, exact), the status set, and the authoritative count (iteration 002):**
```
killed:term-esrch, killed:group-dead-after-grace, killed:ladder, killed:tree-incomplete, already-dead:group-empty, refused:guard-disabled, refused:not-registered, refused:platform-unsupported, refused:pgid-not-registered, refused:pgid-invalid, refused:pgid-self, refused:daemon-group, refused:pgid-recycled, refused:leader-unreadable, refused:ownership-mismatch, refused:group-unknown
killed, already-dead, refused
```
**The enumeration above IS the authoritative exact set: 16 codes = 9 kept/renamed + 7 added. Any count other than 16 anywhere in the plan set is a defect.**

**B5 — the counter set (ordered, exact):**
```
ram_kills, ram_breaches_detected, ram_kill_refusals_p0, ram_kill_refusals_p1, ram_kill_refusals_p2, ram_kill_refusals_p3, ram_kill_refusals_p4, ram_kill_refusals_p5, ram_kill_refusals_p6, ram_kill_refusals_p7, ram_overcounted, ram_rearms, ram_confirm_failures, ram_events_emitted, ram_events_dropped, ownership_unverified, tree_incomplete
```

**B6 — the Q2 canonical predicate strings (must appear verbatim in §2 DQ4, §4.2, §5, phase2 T10/T11 and test-plan S8/B13):**
```
the watch stays alive until the group member count reaches 0
identity = captured pgid ∧ surviving-member group membership
starttime gate applies to the leader only while alive
the watch is released only at group member count 0
group_member_count == 0
```

**B7 — the event emitter interface and the §7 import-freeze sentence:**
```
RamBreachEmitter = Callable[[str, Mapping[str, Any]], bool]
```
```
Import direction: `tools/* → ram_guard` only; `ram_guard → stdlib` only. `ram_guard` imports NOTHING from `daemon/` — no `tools/*`, no `resource_monitor`, no `repositories`, no `event_bus`, no `main_loop_bridge`, no `bash_spawn_reaper`. The event dependency is satisfied by an INJECTED callable (`RamBreachEmitter`), whose implementation lives OUTSIDE the package (`daemon/services/ram_guard_events.py`) and is wired at the composition root (the FastAPI lifespan).
```

**B8 — the config wiring:**
```
ram_guard: RamGuardConfig = Field(default_factory=RamGuardConfig)
model_config = SettingsConfigDict(env_prefix="RAM_GUARD_")
ceiling_mib: int = Field(default=3072, ge=64, le=65536)
poll_interval_ms: int = Field(default=200, ge=50, le=2000)
grace_ms: int = Field(default=250, ge=0, le=2000)
pss_confirm_deadline_ms: int = Field(default=250, ge=50, le=2000)
ENSEMBLE_RAM_GUARD
0, false, no, off
```

**B9 — the assertion forms:**
```
with pytest.raises(ValidationError): load_config(str(yaml_file))
assert config.ram_guard.ceiling_mib == 3072
assert config.ram_guard.poll_interval_ms == 200
assert config.ram_guard.grace_ms == 250
assert config.ram_guard.pss_confirm_deadline_ms == 250
assert config.ram_guard.ceiling_mib * 1_048_576 == 3_221_225_472
```

**B10 — the honesty-set sentences (verbatim, runbook + envelope):** the §3.5 items **10, 11, 12, 13** blocks, and the envelope message clause:
```
The enforced trigger threshold was {threshold_mib} MiB (the configured ceiling is {limit_mib} MiB); when the summed-RSS metric over-counts shared pages the threshold is re-armed above the ceiling and the kill decision is always made on the shared-page total.
```

**B11 — the §3.4 truth statement (replacing L110):** the rate-conditional paragraph quoted in §3.4, verbatim (including the contended clause added at iteration 002).

**B12 — the fixture split and the test-name shape:**
```
kill_fence      (the P0–P7 REFUSAL legs only)
kill_census     (the ladder and the P8 legs)
test_p<n>_<case>_<expected_status>
```

**B13 — the §6 census:** the enumerated table in §6, verbatim (the table is the artifact; counts are re-derived at fold time, never pinned).

**B14 — the coupling-map replacements (renumber-proofed at iteration 002):**
```
tight (P5 consumes the envelope producer, the counters and the guard heartbeat from P2 — Phase 5's envelope-event and counter tasks depend on Phase 2's envelope-producer and guard-thread tasks)
loose (DQ6's consumer-gate artifact now lives in P2; P5 implements DQ7 — the config surface)
```

**B15 — the phase-ownership lines:**
```
Phase 2 owns the event HOOK (the emitter injection, the WATCHING→KILLED call site, build_ram_event_payload) and the DQ6 consumer-gate artifact (pins-only).
Phase 5 owns the event EMITTER (EventKind.RAM_LIMIT_EXCEEDED, daemon/services/ram_guard_events.py, the non-blocking loop bridge, the lifespan wiring) and the root-Config wiring of RamGuardConfig.
Phase 7 owns the operator documentation (knob table, env-off procedure, counter names, the GET /api/resources/ram-guard endpoint contract).
```

**B16 — the DQ2 two-thread ruling sentence (iteration 002 — replaces the iteration-001 "ONE dedicated daemon thread" wording):**
```
(b) TWO dedicated daemon threads — ONE TICK THREAD and ONE EXECUTION-LANE THREAD — started/stopped together on the FastAPI lifespan (app.state.ram_guard owns both; OFF ⇒ neither thread exists). The tick thread owns registration, the ONE shared /proc scan, the RSS trigger, the peak/counter updates and the breach handoff; it never reads PSS, never signals, never sleeps beyond the poll. The execution-lane thread is the only executor of the kill path: the one-shot PSS confirm (250 ms deadline, fail-open ⇒ kill), then ram_kill_ladder (TERM → slice-probed grace → KILL → the P8 probe), then the verdict publication and the emitter call. The lane never samples and the tick never kills, so a stalled confirm can never delay a tick.
RAM_GUARD_PENDING_MAX = 2
RAM_GUARD_GRACE_POLL_SLICE_SECONDS = 0.025
```

**B17 — the §5 "blocked" invariant and the phase2 risk-row property (iteration 002):**
```
no PSS read and no signal on the tick path — both live on the execution-lane thread
```
```
The confirm and the kill ladder run on the execution-lane thread, never on the tick thread, so a stalled smaps_rollup read can delay a kill but can never delay a tick, a measurement or the liveness sweep.
```

**B18 — test-plan N8's replacement row (iteration 002):**
```
| N8 | A stalled smaps_rollup read on the execution-lane thread | The confirm's 250 ms deadline fires ⇒ fail-open ⇒ kill; the tick path is never blocked — the tick keeps sampling on schedule, last_tick_at keeps advancing and the liveness sweep still runs while the lane is stalled; a breach detected during the stall is detected on schedule (its detection is not deferred by the stall). The residual (a stalled confirm holds the kill path until the deadline; a read that never returns holds it until the daemon restarts) is disclosed, not silently accepted |
```

**B19 — phase6 T20's replacement row (iteration 002):**
```
| 20 | smaps_rollup stalled-read → head-of-line blocking. Test the confirm's deadline path (T14) with a stalled confirm on the execution-lane thread and assert the tick path is never blocked: last_tick_at keeps advancing at the poll cadence, the shared scan keeps running, the liveness sweep still fires, and a second breach detected during the stall is detected on schedule. Record the residual in the phase report as disclosed | Task 14 | A simulated stalled confirm does not delay a tick; the deadline fires; the tick heartbeat advances during the stall; the residual (a wedged read holds the kill path until restart) is disclosed |
```

**B20 — the verdict-wait deadline (iteration 002, replaces the iteration-001 750 ms):**
```
wait_deadline_ms = RAM_GUARD_PENDING_MAX × T_service_worst_ms + WAIT_SLACK_MS
T_service_worst_ms = PSS_CONFIRM_DEADLINE_MS + GRACE_MS + PROBE_MS
wait_deadline_ms = 2 × 510 + 250 = 1270
```

**B21 — the conditional-release clauses (iteration 002):**
```
A release of a CLAIMED record is deferred to the lane (the lane releases after publishing).
A live watch is never ended by a lane exit; the liveness sweep owns the release at group_member_count == 0.
```

**B22 — the EventKind qualifying sentence (corrected counts, iteration 002 — phase7 T1 pins this verbatim):**
```
the SQLite `event.kind` CHECK constraint lists only 8 kinds while `EventKind` has 16 — it is already stale for 8 pre-existing kinds; the production deployment is PostgreSQL (`create_all`; the SQLModel column carries no CHECK — `models.py:74`), so `ram_limit_exceeded` (the 17th member) is unaffected
```

---

## 16. ITERATION-002 AMENDMENT LEDGER (B1 + B3 + notes), ruled by the architect

### 16.1 Ruling 1 — B3: PSS-confirm execution context → **(a) TWO dedicated daemon threads (tick + execution lane)**

**The defect:** DQ2 mandated ONE dedicated daemon thread while "no PSS on the tick path" was asserted binding — with a single execution context, the sequential breach→confirm→TERM→grace→KILL on that thread makes an inline stalled confirm **delay ticks** and head-of-line block the measurement of other spawns; test-plan N8 and phase6 T20 were unsatisfiable; phase2's risk row asserted an impossible property.

**The ruling (binding):** option **(a)** — DQ2's replacement sentence is §15 **B16**; the §5 invariant is §15 **B17**; N8/T20 are §15 **B18/B19**. Three amendments over the architect's provisional shape (W-C's analysis, adopted):
- **No starvation fail-open.** A bounded **pending set over the registry** (`RAM_GUARD_PENDING_MAX = 2`) replaces the FIFO; overflow ⇒ the newest breach is **not claimed** (re-offered next tick) — **no confirm is ever skipped**, so `pss_confirm_unavailable` stays reserved for genuine confirm failures/timeouts and **lane congestion can never fire the fairness gate** (§9 🔴).
- **The lane heartbeat is a PROGRESS heartbeat.** `lane_alive` alone would report a wedged-but-alive lane green; `last_lane_progress_at` + `lane_pending` on `GET /api/resources/ram-guard` are the signals (DQ8).
- **The grace slice is the guard's own constant.** The reaper's `_GRACE_POLL_INTERVAL_SECONDS = 0.25` yields ONE slice and NO early exit at a 250 ms grace (the reaper's own grace is 5.0 s — its slice is 1/20 of it); the guard uses `RAM_GUARD_GRACE_POLL_SLICE_SECONDS = 0.025` (10 slices, early exit live), **reimplemented in `ram_guard/ladder.py`** (the `bash_spawn_reaper` import stays forbidden), preserving the arithmetic-convergence property for mocked-time tests.

**Second-order effects (all ruled):**
- **Failure-path table** (§5): four distinct death modes — tick dies (restart loop) / tick WEDGED (a never-returning scan read — not recoverable, `last_tick_at` frozen is the signal, disclosed) / lane dies (restart loop, pending resumes) / lane WEDGED (a never-returning `smaps_rollup` read — **kills stop, measurement continues**, `last_lane_progress_at` frozen is the signal). The liveness sweep stays on the tick thread.
- **Why the deadline cannot bound a stalled read (disclosed):** the house `/proc` reader is a plain blocking `read_text()` with no timeout (`proc_readers.py:127-133`, E10) — the 250 ms deadline is a **post-hoc elapsed check**; a never-returning read wedges whichever thread ran it. Under (a) that is the lane (kill path only); under (b) it would have been the whole guard. This asymmetry decided the ruling as much as the head-of-line argument.
- **Overshoot arithmetic:** the confirm no longer serializes with the tick; it serializes with the **lane**. Contended companion added (§3.3/§3.4): `Ω_contended(2) = R × 1.21 s`; the N=2 unit-cap window is empty above R ≈ 0.598 GiB/s and the guard wins at `8700M` only below R ≈ 0.28 GiB/s — both published as the **N=2 disclosure row**; the recommended pair is explicitly a **single-tree claim**.
- **Kill-ladder timing:** the ladder runs on the lane; `T_service_worst = confirm 250 + grace 250 + probe ≈ 510 ms` bounds one service; the §4.3.1 bash-lane wait is re-derived to **1270 ms** (§15 B20) — the iteration-001 750 ms did not cover a queued record.
- **Conditional release (§4.3.1 items 3–4, §15 B21):** a claimed record's release is deferred to the lane (a lane-side release racing the lane's publication is the ordering violation), and the success-path release is conditional on the watch being finished — **this is the contract basis for the planner's blocker-B2 fold** (a live watch is never ended by a lane exit).
- **House consistency:** DQ2's own precedent family runs exactly this split — `resource_monitor/facade.py:9-12` ("BOTH threads are `threading.Thread(daemon=True)` … Boot failure of either thread DEGRADES the subsystem"), `metrics_gate.py:335-341` (a blocked read stalls that thread only), the detached-join shutdown at `api.py:4144-4152`. The wedge-capture "no second thread" rejection bans a second **concurrent writer** and a **supervisor** — the lane is neither (the tick never signals and never writes a terminal state; the claim token still has a single owner). Option (b) was rejected: it makes the only measurement path block behind one tree's kill path for up to 510 ms, deletes the invariant phase2's risk row and N8 rely on, and turns a stalled read into total subsystem death.

### 16.2 Ruling 2 — B1: the vocabulary count → **the enumeration is authoritative: 16 codes**

Every enumeration (§4.2, §15 B4, phase2 T12, test-plan S18) contains exactly **16** codes; the "18 (was 9)" was an arithmetic slip that originated in the iteration-001 worker's draft header and was propagated without a recount. **The enumeration is the authoritative exact set: 16 = 9 kept/renamed + 7 added** (the arithmetic is stated in §4.2 and §15 B4). No additional codes were intended: every P0–P7 outcome class, P5a, and the ladder/P8 outcomes are covered by the 16 (the pending-overflow path of §16.1 deliberately adds none — the overflow re-offers rather than refusing). All count sites in THIS document now say 16; **the planner propagates the count and the B4 count-line to every plan-set site that says otherwise.**

### 16.3 Ruling 3 — the two notes

- **`instance_id=None` — NARROWED (no restructure commissioned).** The iteration-001 claim ("an `instance_id=None` spawn IS guarded … the guard's `register()` therefore accepts a `None` `instance_id`") is **withdrawn**: the bash seam's registration/capture block lives inside `if instance_id is not None and proc is not None:` (`bash.py:951-1005`), and widening that gate is a restructure this commission does not name. **The narrow claim:** when instance identity is absent, the spawn is **not registered with the guard in v1**; a ballooning tree from an instance-less call is invisible to the guard and only the unit cap covers it — disclosed as §3.5 item 9b; widening the gate (capture pgid/starttime for `proc is not None` only; register keyed by `tracking_id`) is a **routed follow-up**. DQ8's `instance_id=None ⇒ counter + log only` sentence stays as **defensive** behavior (§4.5), not a v1 bash behavior.
- **Phase-2 T20 pre-freeze gate (a) — method / owner / bounded fallback named (§10 item 3):** method = one read-only session (cgroup `memory.stat` anon-vs-file + `MemoryCurrent`; a `ps` rollup of the unit vs the tracked children; the Oct-30 journal windows); owner = Phase 2 T20 executes and records, Phase 7 carries it into the memo; bounded fallback = inconclusive ⇒ **conservative** D_max shift (`+ min(residue, 1.0 GiB)`) and the §3.4 table re-published — **the freeze is never blocked on inconclusiveness**.

### 16.4 Changed anchors (iteration 002, for the diff)

| Iteration-001 line | Change |
|---|---|
| :34 (§0 item 6) | "18 codes" → "16 codes … corrected in iteration 002, §16" |
| :54 (E9) | EventKind 14 → 16 (moved to E10 with the full corrected set); `models.py:77` → `:74` |
| :77 (DQ2) | the full two-thread replacement (B16) + the pending-set/slice constants + the supervisor/executor distinction |
| :78 (DQ3) | unchanged |
| :79 (DQ4) | `os.killpg` from the execution-lane thread; the Q2 strings unbolded to verbatim; 16-code pointer |
| :84 (DQ8) | + the lane-side emission + the per-thread heartbeat fields + the defensive `instance_id=None` note |
| :85 (DQ10) | the qualifying sentence corrected (16 / 8 / 8; `models.py:74`; the 17th member) |
| :93–:96 (§3.3) | + the contended companion `Ω_contended(N)` |
| :117–:154 (§3.4) | + the N=2 disclosure row + the single-tree claim note + the contended clause in the truth statement |
| :160–:165 (§3.5) | + items 9/9b; item 10 gains the ratchet clause; item 11 gains the post-hoc-deadline/uninterruptible-read clause |
| :186–:215 (§4.1a) | the confirm-frequency paragraph re-scoped to the lane |
| :217–:290 (§4.2) | the ladder runs on the execution-lane thread; the 16-count + arithmetic; the slice constant |
| :292–:314 (§4.3) | unchanged (message clause, key set) |
| :316–:330 (§4.3.1) | wait deadline 750 → 1270 (B20); + items 3–4 conditional releases (B21) |
| :355–:380 (§4.5) | the emitter call attributed to the lane; the defensive `instance_id` note |
| :385–:400 (§5) | the four death-mode rows + the pending-overflow row; the blocked invariant (B17); the degradation row's verbatim strings |
| :402–:428 (§6) | counts-never-pinned framing |
| :430–:460 (§7) | guard.py = both threads; the detached-join lifecycle; the two new constants |
| :462–:485 (§8) | + the lane lands in Phase 2; + the B2 fold basis in Phase 3's additions |
| :487–:497 (§9) | + the contended race numbers; + the uninterruptible-read risk; the fairness-gate/pending-overflow note |
| :499–:503 (§10) | item 3 gains the method/owner/fallback |
| :500 (§11) | the CHECK gap corrected to 8; + gap (f) |
| :516–:523 (§13.1) | the two-thread wording |
| §14.1/§14.2 | blocker 6's count corrected; the instance_id row narrowed; B14 renumber-proofed; the census framing |
| §15 | + B16–B22; B4 gains the authoritative count line |
| NEW §16 | this ledger |
