# Architecture Recommendation: RAM-Based Auto-Termination — Phase 1 Design Decision Gate

Date: 2026-11-10 (aggregation completed 20:5x UTC) · **AMENDED 2026-11-10T23:5xZ (approver-iter-001) · 2026-11-11T0x:xxZ (approver-iter-002) · 2026-11-11T1x:xxZ (ESCALATION PASS — iteration 003 rejected at max iterations; leader disposition TrueAuto: Class A rulings + user rulings folded, then IMPLEMENTATION with no further approver calls)**
Architect instance: c15bdc7d (Architecture Controller — dispatch + aggregation only; no direct design)
Amendment instances: W-A `a2bdd4a3` (resilience-design) · W-B `b152ca58` (data-flow-design) · W-C `faba379e` (data-flow-design, iteration 002); the escalation pass is architect adjudication of **leader-framed** decisions with first-hand code/host verification (E11) — no open-ended design exploration remained to fan out.
Analysis instances: W1 `2ead7080` · W2 `acf4bd9d` · W3 `9796b69c` · W4 `4b079967` · W5 `3b7e355b` · COUNCIL governor `0153da35` + councilors `96da4747`/`b9471c96` (cycle 1)
Base: worktree `/home/nea/ensemble-src-wt-ram-auto-termination`, branch `feature/ram-auto-termination`, base `origin/latest@0efdf106f`
Status: **COMPLETE — 10/10 DQ rulings + R1 + sequencing verdict. Binding. AMENDED (iterations 001+002 in §14/§16; the ESCALATION PASS — user rulings + Class A + C9/C13 — in §17).**

> **PASS MARKER — `approver-iter-001` + `approver-iter-002` + `escalation-pass (iter-003 rulings)`.** This document is the **single** contract for the feature. Iteration 001 changed the sections in §14.3; iteration 002 those in §16.4; the escalation pass those in §17.5. **P1/P3/P4/C2 are closed (approver-credited) and are not re-opened.** No phase doc or test-plan was touched by any architect pass — the planner folds.

---

## 0. Executive summary

The commissioned feature — kill an agent-spawned bash/proc process tree that exceeds a RAM ceiling and return a typed error — is **architecturally sound and should proceed**.

Three findings dominated the cycle-1 aggregation:

1. **R1's stated premise was false, and the real situation was worse than the plan assumed.** The live unit has **no memory cap at all** (`MemoryMax=infinity`); the 7G/8G figures were a never-applied runbook recommendation; the Oct-30 incidents were **kernel OOM kills** (unit peak 7.3 GiB). **[SUPERSEDED at the escalation pass:** the council's "ship 3 GiB" resolution is **demoted to operator guidance** by USER RULING #2 — the default is now a **dynamic 70 % of detected total RAM** (§3.3a, §4.7); the 8 GiB-at-or-after-daemon-death analysis is RETAINED as the on-this-host honesty disclosure and is **accepted by the user's stated intent** (disaster prevention, not a constraint on legitimate heavy work). **]**
2. **The failure that actually happened is only partially coverable by this feature.** The killing memory was largely **orphaned children of a dead daemon incarnation** — invisible to any per-command guard. The guard is the **inner, preventive** layer; the **unit cap is the outer layer**; the kernel global OOM is today the *only* layer actually running.
3. **Summed RSS alone is unsafe as a kill metric — measured, not argued** (8.9× over-count on a 9-member fork tree). The trigger/confirm hybrid is **mandatory**.

**Amendment pass 1 (iteration 001):** bounded over-count re-arm; the durable event's owner/emitter/bridge; the 16-code reason vocabulary (P8 is post-kill); `RamGuardConfig`→root `Config`; verdict-publication ordering.

**Amendment pass 2 (iteration 002):** the TWO-thread guard (tick + execution lane); the 16-count correction; the narrowed `instance_id=None` claim; the T20 method/owner/fallback; the EventKind count fix.

**ESCALATION PASS (iteration 003 rulings) — binding:**

4. **USER RULING #2 (THE default):** when `RAM_GUARD_CEILING_MIB` is **UNSET**, the ceiling is **70 % of the detected effective total RAM** (dynamic, container/cgroup-aware), resolved once at boot; **explicit config overrides** and is validated `[64, 65536]` — **the clamp never applies to the derived value** (§3.3a, §4.7). USER RULING #1 (8 GiB confirmed) is superseded in its fixed figure; its **intent** (disaster prevention, not a constraint on legitimate heavy work) is carried into the clamp ruling and the §3.4 guidance.
5. **The 16-code vocabulary now has a sink:** `reason` is added to the SpawnRecord, the envelope key set, and the event payload (§17.1).
6. **Refusals reach a TERMINAL outcome** (or re-arm transiently); the bash lane gets a distinct, honest refusal surface; the 1270 ms wait never burns without a defined outcome (§17.2).
7. **The typed error is scoped to IN-FLIGHT calls** — post-return breaches surface via the durable event + the guard endpoint, disclosed; **no new agent-injection channel in v1** (routed follow-up) (§17.3).
8. **OFF beats validation:** `ENSEMBLE_RAM_GUARD=0` + an out-of-range ceiling **must not abort boot** — the mechanism is a conditional construction in `load_config` (§17.4).
9. **The claim token is EPISODE-scoped** — released when the episode resolves (KILLED, OVERCOUNTED re-arm, or REFUSED); no double-claim, no dead path; S13 is achievable (§17.6).

Everything below is binding on Phases 2–7 except where explicitly marked advisory.

---

## 1. Evidence base (all verified on-host / in-worktree)

| # | Fact | Source |
|---|---|---|
| E1 | `MemoryMax=infinity`, `MemoryHigh=infinity`, `MemoryCurrent` 2.69→3.31 GiB across the analysis window | `systemctl show ensemble-main.service` |
| E2 | No `Memory*` directive anywhere in the unit or its drop-ins | `/etc/systemd/system/ensemble-main.service.d/` |
| E3 | Oct-30 incidents: kernel OOM kills (`oom-kill`), unit peak 7.3 GiB | `journalctl -u ensemble-main.service` |
| E4 | Host: ~11.67 GiB RAM, 8,191 MiB swap; co-residents ≈1.79 GiB "others" | `free -m`, ps |
| E5 | The unit file is a **generated artifact**; a drop-in survives `adopt-unit.sh` regeneration | repo + RCA docs |
| E6 | Measured metric costs (250 pids): `/proc/<pid>/status` 0.056 ms/member; `smaps_rollup` ≈ `0.10 ms + 0.0115 ms/MiB × RSS`; full-table scan 9.49 ms median / 13.89 ms p95; over-count factor 8.9× (N≈9) | W1-r2 |
| E7 | 21-day prod aggregates: max legitimate live-sampled child footprint **364.5 MiB** | council |
| E8 | The Oct-30 killing memory was **untracked/orphaned**, not a live registered command | council |
| **E9** | **Iteration-001 anchors:** `event_bus.create_event` async + `to_thread` (`event_bus.py:155-181`); the house bridge precedent at `report_delivery_recovery.py:1499-1507`; `manager._get_event_loop()` hardened (`manager.py:3097-3195`); `manager._loop` at `:4425`; `MainLoopBridge.set_loop` only inside `PoolOrchestrator.setup()` which returns early when `USE_WORKER_POOL` is off (`pool_orchestrator.py:199-201, :235`); `self._event_bus` private (`manager.py:1945`); bash success-arm `mark_exit` at `bash.py:1110-1123` + `unregister` `:1132-1141`; `pid_registry.mark_exit` raises on unknown kinds (`:409-413`) and deletes the live row (`:423`); `proc_tools._drain_exit_code` ladder `:861-875`; `Config(**config_dict)` at `config.py:8795`; the yaml-beats-env trap `config.py:911-916`; the boot-abort precedent `api.py:2266-2278` | architect re-reads |
| **E10** | **Iteration-002 anchors:** `EventKind` = **16** members (`repositories/event/models.py:12-55`); the SQLite CHECK lists **8** (`migrations/versions/20260412_000002_create_event_table.sql:13-20`); the SQLModel column carries no CHECK (`models.py:74`); the house `/proc` reader is a plain blocking `read_text()` (`resource_monitor/proc_readers.py:127-133`) ⇒ the confirm deadline is a post-hoc check; the reaper's `_GRACE_POLL_INTERVAL_SECONDS = 0.25` (`bash_spawn_reaper.py:644`) yields one slice at a 250 ms grace; `resource_monitor/facade.py:9-12` (two threads, degrade-not-crash); `metrics_gate.py:335-341`; the detached-join form `api.py:4144-4152`; the reaper's slice-probed grace `bash_spawn_reaper.py:647-675` | architect re-reads |
| **E11** | **Escalation-pass anchors (architect-verified, 2026-11-11):** `MemTotal: 12239768 kB` = 11,952.9 MiB ⇒ **70 % = 8,367.0 MiB = 8.171 GiB (floor 8367 MiB)**; this daemon's cgroup = `0::/system.slice/ensemble-main.service` (v2; `memory.max` at `/sys/fs/cgroup/system.slice/ensemble-main.service/memory.max`); **anchor-drift corrections** — the bash `EXIT CODE:` line is at `bash.py:1156` (was cited `:1155`), the timeout-arm string at `:1160` (was `:1159`), `proc_run`'s `return process_id, None` at `proc_tools.py:1310` (was `:1307`), the reaper's starttime gate at `bash_spawn_reaper.py:859-866` (was `:851-857`), the bash S1 gate block at `bash.py:952-1006` (was `:951-1005`) | architect re-reads |

### 1.1 Plan claims falsified against the code

| # | Plan claim | Reality (verified) |
|---|---|---|
| P1 | "The live systemd unit ships `MemoryMax=8G`" | **No cap exists.** See E1–E3. |
| P2 | `pid_registry` closed set at `daemon/repositories/pid_registry/models.py:121-135` | Path does not exist. Real site: `daemon/services/resource_monitor/pid_registry.py:120-138`. |
| P3 | "the reaper's PID-ownership verification … `proc_tools.py:1039-1052`" | Two mechanisms conflated. The reaper's real gate is `verify_and_kill` (`bash_spawn_reaper.py:735-900`, starttime gate `:859-866`); `proc_tools.py:1037-1053` is a *different*, fail-open 1-signal environ check. |
| P4 | "the bash lane's ladder is gated by the ownership check" | The bash lane has **no ownership gate at all** (`bash.py:697-745`). |
| P5 | "pgid captured at spawn, never re-derived" (both lanes) | True for bash. **False for proc** (`proc_tools.py:1058`, `:1280`). |
| P6 | Risk #7: check the chart-tools legacy test before committing the envelope | Inapplicable (chart tools only). The real pin surface is in §6. |
| P7 | "`proc_readers.read_pss_rollup` exists, so the reader exists" | Self-only (the pid is hardcoded to `self`, `proc_readers.py:769`). |
| P8 | Phase 7: durable verdict ⇒ "migration-backed vocabulary extension" of `reap_reason` | `reap_reason` is plain `TEXT` — code-only. (Moot: DQ10.) |
| P9 | "`list_group_members` is the discovery primitive" | Correct but not injectable; per-spawn cost overruns the tick. |

---

## 2. DQ1–DQ10 — binding rulings

| DQ | Ruling | Rejected | Key evidence |
|---|---|---|---|
| **DQ1** — tree RAM metric | **(e) HYBRID, confirm MANDATORY**: trigger = `summed_rss_bytes` per tick; decision = one-shot **`summed_pss_bytes` confirm** at breach, 250 ms deadline, **fail-open ⇒ kill**. Overcounted trees re-arm at **`ceiling + min(shared_extra_bytes, RAM_GUARD_REARM_MAX_EXTRA_BYTES)`** with a growth governor (§4.1a). The confirm executes on the **execution-lane thread**. | pure RSS; pure PSS per tick; per-spawn cgroup; unit cgroup (cannot attribute) | E6; §4.1a |
| **DQ2** — loop home + cadence | **(b) TWO dedicated daemon threads — ONE TICK THREAD and ONE EXECUTION-LANE THREAD — started/stopped together on the FastAPI lifespan** (`app.state.ram_guard` owns both; OFF ⇒ **neither thread exists**). The **tick thread** owns registration, the ONE shared `/proc` scan, the RSS trigger, the peak/counter updates and the breach handoff; it **never** reads PSS, **never** signals, **never** sleeps beyond the poll. The **execution-lane thread** is the **only executor of the kill path**: the PSS confirm (250 ms deadline, fail-open ⇒ kill), then `ram_kill_ladder` (TERM → slice-probed grace → KILL → the P8 probe), then the verdict publication and the emitter call. The lane never samples and the tick never kills, so **a stalled confirm can never delay a tick**. Each thread has its own `while not stop` restart loop and its own `try/except` (counter, never log). Handoff: the claim token is taken under the registry lock **by the tick thread at observation** — **and is EPISODE-scoped** (§17.6); the spawn joins a **bounded pending set** (`RAM_GUARD_PENDING_MAX = 2`); overflow ⇒ the newest breach is **not claimed** (peak/counters still update; state stays WATCHING) and is re-offered next tick — **no confirm is ever skipped**. The ladder's grace is slice-probed at `RAM_GUARD_GRACE_POLL_SLICE_SECONDS = 0.025`, reimplemented in `ram_guard/ladder.py`. Poll default **200 ms**, bounds **[50, 2000] ms**. Lazy first-registration start **rejected**. Precedent: `resource_monitor/facade.py:9-12`; `metrics_gate.py:335-341`; shutdown = `.stop()` + detached join of both (`api.py:4144-4152`). | in-execution asyncio task; riding the 30 s sampler; **a supervisor thread** (watches/restarts another thread — the house accepts none for wedge-capture); **the lane is an EXECUTOR, not a supervisor** — the tick never signals and never writes a terminal state, so the claim token still has a single owner | W2, W4; E10; §16.1 |
| **DQ3** — tree discovery | **(c) HYBRID from ONE shared `/proc` scan per tick**: membership = **group relation ∪ bounded descendant closure**; zombies + kernel threads excluded; enumeration failure ⇒ drop the sample, never kill on no-evidence. | group-only; ppid-only; reusing `list_group_members` verbatim | E6 |
| **DQ4** — kill ladder + vocabulary | **(d) NEW sync short-grace ladder** shared by BOTH lanes: `ram_kill_ladder(pgid, leader_start_time, grace_seconds) -> (status, reason)`; TERM → slice-probed grace → KILL; zombie-aware skip; `os.killpg` from the **execution-lane thread**. **Grace default 250 ms, bounds [0, 2 000] ms, a SEPARATE knob** (the floor `0` is deliberate: a zero grace is a valid hard-quarantine mode — TERM then immediate KILL). Identity gate: **captured pgid + leader `starttime` re-verification** (fail-closed), environ check secondary. **Leader ruling Q2=(i):** the watch stays alive until the group member count reaches 0; identity = captured pgid ∧ surviving-member group membership; starttime gate applies to the leader only while alive; the watch is released only at group member count 0 — `already-dead:group-empty` fires **only at `group_member_count == 0`**; the complete 16-code vocabulary is in §4.2 — **and its `reason` value is recorded, enveloped and evented (§17.1)**. `exit_kind="ram_killed"` (constant, never a literal); proc gets `ram_killed: bool`, **status stays `"killed"`**; `reap_reason` untouched. | reusing `_kill_process`; routing through `_attempt_kill_signal`; a new proc status value | W2 §DQ4; §4.2; §17.1–17.2 |
| **DQ5** — substrate + seams | **(a) RAM-only in-process map**, keyed by `tracking_id`, both lanes. **The proc lane registers with the guard only — NO new durable write path.** The bash `bash_spawn_registry` row stays **OPEN** on a RAM kill. (c) stays illegal (fence). | durable extension; a proc write path; closing the row | W4; W3 |
| **DQ6** — typed error | **Mirror `_format_secret_envelope`; the envelope IS the entire bash return string**; numerics as **siblings before `message`**; `code="ram_limit_exceeded"`, `surface="bash.exec"\|"proc.exec"`, **+ `reason` (§17.1)**. **Proc placement differs structurally**: `proc_run` returns before any breach can exist — the proc verdict rides `proc_status` as a `ram_breach:` line + a flat `ram_killed: true`. **Consumer gate: SATISFIED by evidence** — the "22 consumers" figure is withdrawn; §6 carries the enumerated census; Phase 2 owns the artifact. Frozen evolution rule: additive keys allowed; any removal/rename/type-change requires a new `code` — **the escalation pass exercises this rule twice, additively** (`reason`; `ram_kill_refused:` lines, §17.2). No version field. | appending the envelope to output; numerics inside `message`; `Error:`-prefixing the proc envelope | W5; §6; §17 |
| **DQ7** — config surface | Bare **`ENSEMBLE_RAM_GUARD`** master kill-switch (reaper semantics; OFF = **complete no-op**; **OFF is honored BEFORE numeric validation** — §17.4) + **`RamGuardConfig(env_prefix="RAM_GUARD_")`**: `RAM_GUARD_CEILING_MIB` is **`Optional[int]`, default `None` ⇒ the DYNAMIC 70 % default (USER RULING #2, §4.7)**; explicit values validated `[64, 65536]` — **the clamp applies to explicit values ONLY**. `RAM_GUARD_POLL_INTERVAL_MS` 200 [50, 2000]; `RAM_GUARD_GRACE_MS` 250 [0, 2000] (floor 0 argued in DQ4); `RAM_GUARD_PSS_CONFIRM_DEADLINE_MS` 250 [50, 2000]. Depth/node caps, the §4.1a re-arm constants, `RAM_GUARD_PENDING_MAX`, `RAM_GUARD_GRACE_POLL_SLICE_SECONDS` and `WAIT_SLACK_MS` are **module constants, not knobs**. **Runtime injection**: `ceiling_bytes` is a parameter of `register()`; the **resolved** ceiling (derived or explicit) is the default. **SECURITY RULING: NO LLM-visible ceiling parameter.** **The class and the bare switch are wired into the root `Config`** (§4.6). | `RESOURCE_MONITOR_*`; agent-visible per-call ceiling | plan lean + W3; §4.6; §4.7 |
| **DQ8** — observability | `EventKind.RAM_LIMIT_EXCEEDED = "ram_limit_exceeded"` (**same string as the envelope `code`**). Payload = **the envelope key set + exactly four event-only keys** (`trigger`, `state`, `tracking_id`, `breach_at`) — **and `reason` rides both sets (§17.1)**. Producer-side allow-list enforced. Exactly **one event per breach** (driven by the `WATCHING→KILLED` transition, emitted by the lane) — **refusals emit NO event** (they are operational, not forensic: counters + the transition log line; §17.2); `instance_id=None` ⇒ counter + log only (defensive — §16.3). Counters — exact ordered list: `ram_kills, ram_breaches_detected, ram_kill_refusals_p0 … p7, ram_overcounted, ram_rearms, ram_confirm_failures, ram_events_emitted, ram_events_dropped, ownership_unverified, tree_incomplete` (no p8). Log prefix **`ram-guard:`**. Read surface: **new additive `GET /api/resources/ram-guard`** carrying the counters + the **eleven-field heartbeat** (§15 B23): `enabled, thread_alive, last_tick_at, missed_samples, ticks, registry_depth, lane_alive, lane_pending, last_lane_progress_at, resolved_ceiling_bytes, ceiling_source`. **not** in `/livez`/`readyz`; `/control-capabilities` untouched. | riding `/self-cost`; healthz coupling | W4; §4.5; §17.1 |
| **DQ9** — default-ON vs T4 | **Confirmed: entirely outside the T4 seam.** No `resource_limit_policy` read/write; both fences green; `/control-capabilities` byte-identical. The env-off switch is the v1 disable. | any T4 read path in v1 | W3, W4, W5 |
| **DQ10** — crash durability | **(a) in-process + the DQ8 event as the durable forensic record. No migration lands in v1 at all.** Events retain ~24 h. Rollback trivial. **The event has an owner, an emitter and a bridge (§4.5).** **Qualifying sentence (house debt; the ONLY surviving variant is B22 — the 14/10/`models.py:77` figures are WITHDRAWN falsified variants and must not appear in any pin cell):** *the SQLite `event.kind` CHECK constraint lists only 8 kinds while `EventKind` has 16 — it is already stale for 8 pre-existing kinds; the production deployment is PostgreSQL (`create_all`; the SQLModel column carries no CHECK — `models.py:74`), so `ram_limit_exceeded` (the 17th member) is unaffected.* | durable verdicts | W4; §4.5; E10 |

---

## 3. R1 — the Critical Interaction, resolved

### 3.1 Ceiling semantics: **ABSOLUTE per-command bytes** (council, unanimous)

`effective_ceiling = the RESOLVED ceiling (derived or explicit)`, compared against the ruling metric (DQ1). Relative/dynamic-to-current-usage ceilings rejected on five jointly-cited grounds (not computable; algebraically a constant; self-raising hazard; page-cache scale mismatch; untestable). **The 70 % dynamic DEFAULT (§3.3a) is a resolution rule for the ceiling's VALUE, not a dynamic-to-usage ceiling — it is resolved once at boot and is absolute thereafter.**

### 3.2 Layering and dependency direction

```
INNER  (preventive)  per-command RAM guard   — live in-flight command trees; typed retryable error; one command dies
OUTER  (containment) systemd unit MemoryMax  — daemon-internal leaks, aggregate children, ORPHANS ACROSS RESTARTS
LAST   (running today) kernel global OOM     — currently the ONLY layer actually enforcing anything
```

```
Unit survival:  X ≥ D_max + C + O + M_c        (the unit must host one full-ceiling command + the daemon)
Box survival:   X ≤ MemTotal − others − M_k    (the cgroup cap must fire before the kernel does)
```

**AMENDED:** the unit floor must host the **worst-case real footprint at the kill** (`F_max`, §3.4).

### 3.3 Shipped default — **SUPERSEDED AT THE ESCALATION PASS BY USER RULING #2**

**The council's resolution (RETAINED as operator guidance, superseded as the default):** the commissioned 8 GiB is unshippable on this 11.67 GiB host — at fire time box demand ≈ 4.79 residents + 8.0 = 12.79 GiB > 11.67 physical; it fires at-or-after daemon death in both Oct-30 incidents. The council shipped 3 GiB (empirical pre-emption: the fast-case tree at death was ≈4.0–4.3 GiB; 3 + 1.0 overshoot ≤ 4.3; max legitimate footprint 364.5 MiB ⇒ false-kill cost ≈ 0).

**USER RULING #1 (superseded in its fixed figure):** the default was user-confirmed at 8 GiB — **intent: disaster prevention, not a constraint on legitimate heavy work.** The fixed 8 GiB figure is superseded by Ruling #2; the INTENT is binding and is carried into the clamp ruling (§4.7c: the derived default is never upper-capped) and the §3.4 guidance.

**USER RULING #2 (THE default, binding):** when `RAM_GUARD_CEILING_MIB` is **UNSET**, the ceiling = **70 % of the detected effective total RAM** (dynamic); **explicit config overrides**. Mechanism, timing, clamp and tests are ruled in **§4.7**. On this host today (no unit cap): `0.7 × 11,952.9 MiB = 8,367 MiB = 8.171 GiB` (E11).

**Honest consequence (RETAINED, user-accepted):** at the derived default on this host the guard is a **disaster line that fires at-or-after daemon death** — at fire time demand ≈ 4.79 + 8.17 = **12.96 GiB > 11.67 physical** (the §3.3 8 GiB arithmetic applies verbatim, +0.17 GiB). This is accepted by the user's stated intent: the guard is disaster prevention, not a constraint; at this setting the **kernel global OOM operates as a peer layer**, and the operator guidance (below) is the lever for hosts where that is unacceptable.

**Operator guidance (the demoted 3 GiB evidence):** smaller hosts — or operators who want the guard to WIN rather than witness — lower the knob; **3072 MiB restores the iteration-001 arithmetic in full** (guard-wins below ≈0.49 GiB/s; a feasible 7G/8700M unit cap; §3.4). The knob is the operator's dial between "witness the disaster" (default) and "prevent it" (lowered).

**Raise-path:** VOID at any fixed figure smaller than the host can host — subsumed by the dynamic default (the ceiling now scales with the host; the layering arithmetic is §3.4's general form).

**Overshoot law (unchanged from iteration 002):**

```
Ω_typ   = R × 0.45 s    Ω_worst = R × 0.70 s    Ω_contended(2) = R × 1.21 s
```

### 3.4 Operator routing (OPTIONS + RECOMMENDATION — nothing executed by this lane) — **re-derived at the dynamic default**

**Arithmetic frame (frozen):**

```
F_max(R) = C + A + Ω(R)          (A = 160 MiB = 0.15625 GiB; Ω as §3.3)
floor(R) = D_max 3.5 + F_max(R) + O 1.0 + M_c 0.5 = C + 5.15625 + Ω(R)
box      = MemTotal T − others 1.79 − M_k 1.0      ⇒  X ≤ T − 2.79
```

| Scenario | C | floor @ R=1 (Ω_worst) | A feasible unit cap? |
|---|---|---|---|
| **Derived default on THIS host (T = 11.67 GiB)** | 8.171 GiB | **13.44 GiB** | **NO — at any R** (gap ≈ 4.45 GiB even at R = 0). The unit-cap question does not arise at the default on this host. |
| Knob lowered to 3072 MiB (the demoted guidance) | 3.0 GiB | 8.856 GiB | **YES** for R ≤ 1.034 GiB/s; the iteration-001 7G/8700M arithmetic applies verbatim |
| **General feasibility at the default** (C = 0.7·T) | 0.7·T | 0.7·T + 5.856 | **iff 0.7T + 5.156 + 0.70R ≤ T − 2.79 ⇔ T ≥ 26.5 + 2.33·R GiB** (≥ ~28.8 GiB at R = 1 GiB/s) |

- **The memo's unit-cap guidance (re-derived):** at the 70 % default, a feasible unit cap exists only on hosts ≥ ~26.5 GiB (R=0) / ~28.8 GiB (R=1). On smaller hosts the operator picks: **(a) ship without a unit cap** — the kernel global OOM is the outer layer (today's state; disclosed, not hidden); **(b) lower the ceiling knob** (3072 MiB restores the 7G/8700M pair and the guard-wins regime); or **(c) resize the host** (≥ ~29 GiB). The `7G / 8700M (8.50 GiB)` pair remains RECOMMENDED **only under option (b)**.
- **The truth statement (binding, replaces the withdrawn L110 claim):**

> "The guard completes before the unit cap fires **only while the tree's allocation rate R stays at or below the pair's headroom divided by the overshoot law** — at the lowered 3072 MiB knob and `8700M`, that is ≈0.49 GiB/s (worst frame; ≈0.76 typical; ≈0.28 contended). At the 70 % default no feasible unit cap exists on hosts below ~26.5 GiB, and on this host the derived default (8.17 GiB) fires at-or-after daemon death — the guard is then a **disaster line and witness**, which the user's ruling accepts. **'The guard always completes before a unit kill' must not appear in the memo, the runbook, or any acceptance criterion.** Restoring the preventive layering requires the knob lowered or the host resized — never a different default rule."

- **Vehicle / verify / rollback:** unchanged from iteration 001 (drop-in `30-memory-bounds.conf`, canary via `systemctl set-property --runtime`, symmetric rollback).
- **Also rejected:** `OOMScoreAdjust=-500` / `MemoryMin`; **`MemorySwapMax` stays unset**.

### 3.5 Honesty minimum (runbook + envelope must carry) — **+ items 14–15 (escalation pass)**

**Envelope:** `limit_bytes` = **the RESOLVED ceiling (derived or explicit)** — never a stale configured figure when the default is dynamic; observed peak as a **sampled lower bound** with the bound formula; kill outcome incl. tree completeness **and `reason`**; the metric's *defined* name + shared-page policy; runbook pointer. **Must not claim:** box protected, memory reclaimed instantly, other processes unaffected, or that the killed command was "the culprit".

**Runbook items (1)–(9b):** unchanged from iteration 002 — the overshoot law (typical/worst/contended); setsid escapees measured-not-killed; **orphans uncovered by the guard — only the unit cap covers that class**; per-tree scope; killed-legitimate-heavy-command cost; tree-RSS/PSS ≠ cgroup accounting; no unit cap by default; swap is not a safety net; watchdog liveness (a wedged lane stalls kills never measurement — `last_lane_progress_at` frozen is the signal); instance-less spawns are unguarded in v1.

- **(10)** the enforced-threshold disclosure (ratcheted, bounded re-arm, `F_max`, miss-risk) — **"the resolved ceiling"** replaces "the configured ceiling" throughout.
- **(11)** the fail-open confirm + **the deadline is a post-hoc elapsed check (the read is uninterruptible)**.
- **(12)** the proc-lane QUERY-ONLY verdict (verbatim; also in the `proc_status` docstring).
- **(13)** the daemon is not the reaper of a killed tree's grandchildren.
- **(14) The typed error is guaranteed only while the tool call is IN FLIGHT (NEW — escalation pass).** *"The typed `ram_limit_exceeded` envelope is delivered by the bash lane's return string only while the tool call is in flight. A breach that fires after the call has returned — the ordinary `cmd &` shape, where the shell exits with `EXIT CODE: 0` and a surviving grandchild balloons — reaches no caller: the tool has already returned. Such post-return kills surface via the DURABLE EVENT and the guard endpoint (`GET /api/resources/ram-guard`), which the agent does not automatically see. **There is NO agent-injection channel for post-return breaches in v1** (a routed follow-up); an agent that backgrounds work and never checks the endpoint will not learn of a post-return kill. The Objective and Success-Criterion-3 assertions are scoped accordingly: the typed error is an IN-FLIGHT guarantee."* — mirrors the proc-lane disclosure symmetry (item 12).
- **(15) The default ceiling is dynamic and host-derived (NEW — escalation pass).** *"When `RAM_GUARD_CEILING_MIB` is unset the ceiling is 70 % of the detected effective total RAM (the daemon's cgroup limit when finite, else host MemTotal), resolved once at boot; `limit_bytes` in every envelope is the RESOLVED value. Changing the host's RAM or the unit's memory limit does NOT re-derive a running guard — restart the daemon to re-resolve. On this host the resolved default is 8,367 MiB, at which the guard fires at-or-after daemon death (item above): operators who want prevention, not witness, lower the knob (3072 MiB restores the preventive arithmetic)."*

---

## 4. Frozen contracts (Phase 2 implements against these; any change is a version bump)

### 4.1 Tree-RAM reader contract (`daemon/services/ram_guard/tree_reader.py`)

```python
@dataclass(frozen=True) class ProcEntry:  pid:int; ppid:int; pgrp:int; state:str
@dataclass(frozen=True) class ProcTable:
    entries: dict[int, ProcEntry]; by_pgrp: dict[int, tuple[int,...]]; children: dict[int, tuple[int,...]]
    scan_complete: bool
@dataclass(frozen=True) class TreeReading:
    total_bytes:int; metric:str; member_count:int; member_pids:tuple[int,...]
    complete:bool; unreadable_pids:tuple[int,...]; zero_pids:tuple[int,...]; gone_pids:tuple[int,...]
    source:str

def scan_proc(proc_root: Path) -> ProcTable
def resolve_members(table, *, pgid:int, leader_pid:int, max_depth:int, max_nodes:int) -> MemberSet
def read_tree_rss(proc_root: Path, member_pids) -> TreeReading     # tick thread only
def read_tree_pss(proc_root: Path, member_pids) -> TreeReading     # execution-lane thread only
def exceeds(total_bytes:int, ceiling_bytes:int) -> bool            # total >= ceiling (inclusive)
```

- **Pure + injectable `proc_root`; no host `/proc` reads in unit tests.** No import of `proc_readers` or `bash_spawn_reaper` (the stat parse — `rfind(")")`, fields 5/22/24 — is reimplemented injectably).
- **Units:** the only `× 1024` lives in the parse helpers; everything downstream is **bytes**. Config is MiB; `× 1 048 576` once at resolution→`SpawnRecord`.
- **Member states:** READABLE / ZERO / GONE / UNREADABLE as ruled (iteration 001).
- **Breach semantics:** `total >= ceiling` (inclusive); the trigger fires on the CURRENT sample, not the peak; an incomplete reading above the ceiling proves a breach; below it is no evidence.
- **`tree_complete`** = scan ∧ closure ∧ no UNREADABLE member.
- **The membership carry-forward rule (Q2):** a pid joins the record's member set iff already-observed, or a bounded descendant of one, or shares the captured pgid and is not a pgid-leader replacement (§4.2 P5a).

### 4.1a The BOUNDED over-count re-arm (binding)

```
rearm_threshold_bytes = max(previous_rearm_threshold_bytes,
                             ceiling_bytes + min(shared_extra_bytes, RAM_GUARD_REARM_MAX_EXTRA_BYTES))
confirm_due = (summed_rss_bytes >= rearm_threshold_bytes)
              and (summed_rss_bytes - last_confirm_rss_bytes >= RAM_GUARD_REARM_MIN_GROWTH_BYTES)
RAM_GUARD_REARM_MAX_EXTRA_BYTES  = 33554432     (32 MiB)
RAM_GUARD_REARM_MIN_GROWTH_BYTES = 134217728    (128 MiB)
```

Rationale, governor, confirm-frequency arithmetic (lane-scoped), residual risk, and the sticky `OVERCOUNTED → OVERCOUNTED` idempotent update: unchanged from iteration 002 (§4.1a as amended there). **The claim-token lifecycle on this path is §17.6: the token is released at the re-arm — a later breach at the re-armed threshold is a NEW episode with a fresh claim (S13 achievable).**

### 4.2 Kill-ladder contract (`ram_guard/ladder.py`)

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

**16 codes — the enumeration IS the authoritative exact set: 9 kept/renamed + 7 added. Any count other than 16 anywhere in the plan set is a defect. `reason` is RECORDED on the verdict record, ENVELOPED and EVENTED (§17.1) — the four `killed:*` discriminations are observable in production and refusals do not collapse into the p0…p7 counter buckets.**

**Pre-kill gate P0–P7, P5a, P8-as-post-kill, the P5 three-way split (absent/alive-mismatch/unreadable):** unchanged from iteration 002 (§4.2 table). **Refusal classification (NEW — §17.2):** `refused:leader-unreadable` and `refused:group-unknown` are **TRANSIENT** (the episode re-arms to WATCHING; re-claimable on a later tick — tick-quantized, per-predicate counters make any spin visible; the sweep eventually releases dead records); all other refusals are **PERMANENT** (the record reaches the terminal `REFUSED` state and is released — for the safety rails `daemon-group`/`pgid-invalid`/`pgid-self`/`platform-unsupported`/`guard-disabled`/`not-registered`/`pgid-not-registered` this is the CORRECT outcome, not a failure; for `pgid-recycled` the captured group is gone; for `ownership-mismatch` the stamp check is deterministic — retrying cannot change it).

**The canonical Q2 predicate strings (char-for-char; unbolded, no insertions):**

```
the watch stays alive until the group member count reaches 0
identity = captured pgid ∧ surviving-member group membership
starttime gate applies to the leader only while alive
the watch is released only at group member count 0
group_member_count == 0
```

**The ladder is sync**, callable from the **execution-lane thread** with **no event loop** (and from a bare thread in tests); TERM → slice-probed grace (`RAM_GUARD_GRACE_POLL_SLICE_SECONDS = 0.025`) → KILL; KILL always reachable; zombie-aware skip; **idempotent**.

### 4.3 Typed-error contract (`ram_guard/errors.py`) — **+ `reason` and the refusal surface (escalation pass)**

- **Bash:** the envelope **is** the entire return string. **Proc:** a `ram_breach:` line + flat `ram_killed: true` inside `proc_status`. The proc lane's verdict is query-only by construction (`proc_run` returns `process_id, None` at `proc_tools.py:1310`).
- Key set (additive-evolution; the house 3-key prefix `ok, code, surface` byte-identical; numerics before `message`) — **`reason` inserted immediately after `surface` (escalation pass)**:
  `ok, code, surface, reason, limit_bytes, rearm_threshold_bytes, observed_peak_bytes, peak_is_lower_bound, poll_interval_ms, metric, trigger_metric, confirmed_pss_bytes, under_report_bound_bytes, pid, pgid, tree_complete, members_total, members_killable, members_unreachable, grace_ms, killed_at, ram_breach_observed, coincident_timeout, timeout_seconds, message`
  - `limit_bytes` = **the RESOLVED ceiling**; `reason` = the 16-code enum string (always set at terminal states).
- **The REFUSAL SURFACE (NEW — §17.2):** when the bash call composes its return while the record's episode resolved as a refusal (in-flight refusal, the command having completed), the return carries the command's ORDINARY output plus additive lines — `ram_kill_refused: <reason>` and `ram_breach_observed: true` — **output is never swallowed for a command that succeeded**, and the surface is distinct from `code="ram_limit_exceeded"` by construction (it never uses that code). The proc lane mirrors: a `ram_kill_refused: <reason>` line in `proc_status`. Sanctioned additively by the DQ6 evolution rule.
- **The wait-expiry surface (NEW — §17.2):** if the §4.3.1 wait expires while the record is non-terminal (a confirm genuinely still running), the return is the ORDINARY string plus `ram_breach_observed: true` and the peak — **the decision is reported as pending, never fabricated**; the outcome, when it lands post-return, is item 14's disclosure.
- **Negative space:** unchanged (no argv/env/stdout/stderr/secrets/per-member lists/paths/reprs; `message` fixed text, numeric interpolation only).
- **Message content:** unchanged from iteration 002, with **"the resolved ceiling"** replacing "the configured ceiling" in the enforced-threshold clause (B10 re-pinned).
- **Precedence (bash):** the RAM arm sits between `bash.py:1088` and `:1110` and preempts the success arm's `mark_exit`, the `EXIT CODE:` line (`:1156`), the 150 k truncation arm, and the timeout arm (`:1160`). **The bash tool's LLM-facing docstring gains one line mirroring the proc lane's** (the envelope's existence and shape — planner folds into Phase 3).
- **Coincidence:** claim-token, first-writer-wins (unchanged).

### 4.3.1 VERDICT-PUBLICATION ORDERING (binding; leader ruling 2)

1. **Terminal verdict first.** The verdict record reaches a terminal state (`KILLED` or **`REFUSED`-permanent** — §17.2 — with its `reason`, peak and timestamps) **before** any lane-side `mark_exit` or registry release. The verdict is published by the **execution-lane thread**.
2. **The lane waits, bounded.** After `await proc.wait()`, the lane reads the verdict record; if the episode is unresolved, it waits (25 ms poll, async, never blocking the loop) for a terminal state **or the deadline**:
   ```
   WAIT_SLACK_MS = 250        (one poll interval 200 ms + 50 ms scheduling/jitter margin — DECLARED, C9)
   wait_deadline_ms = RAM_GUARD_PENDING_MAX × T_service_worst_ms + WAIT_SLACK_MS
   T_service_worst_ms = PSS_CONFIRM_DEADLINE_MS + GRACE_MS + PROBE_MS
   wait_deadline_ms = 2 × 510 + 250 = 1270
   ```
   On expiry with a non-terminal record the return is the ORDINARY string + the additive pending lines (§4.3) — never a fabricated verdict. **Because `REFUSED` is terminal, a refusal exits the wait as soon as the ladder returns — the wait never burns its deadline without a defined outcome (§17.2).**
3. **Release after publication — CONDITIONAL on the claim.** A release of a CLAIMED record is DEFERRED TO THE LANE. The durable `bash_spawn_registry` row stays OPEN (DQ5).
4. **The success-path release is CONDITIONAL on the watch being finished:** when the bash call returns but `group_member_count > 0` (the `cmd &` shape), the guard entry is NOT released on the lane's exit paths; the tick thread's liveness sweep owns the release at `group_member_count == 0`. The `finally`-shaped release covers terminal/empty-watch records only.
5. **Exit kind.** `exit_kind=ram_killed` (the named constant) + the guard's sampled peak converted bytes→KB exactly once at S6.
6. **The proc lane.** `_drain_exit_code` (`proc_tools.py:786-875`) consults the verdict before finalizing `info.status`; `ram_killed` joins `timed_out`/`user_stopped` as the third authoritative input; `info.status` stays `"killed"`.
7. **Consequence for Phase 3 T5.** The ordering is asserted by a recording fake: publication observed before `mark_exit`; the success arm's `mark_exit(EXIT_KIND_SUCCESS)` did not run; the release happened after publication. **Phase 3's mandate includes the 1270 ms bounded wait itself (C9).**

### 4.4 The verdict record (`SpawnRecord`, RAM-only, keyed by `tracking_id`)

`tracking_id · instance_id · lane · pid · pgid · start_time_jiffies · ceiling_bytes (**the RESOLVED ceiling**) · poll_interval_ms · grace_ms · state (WATCHING→BREACHED→{OVERCOUNTED (sticky) | KILLED | REFUSED}→RELEASED; first-writer-wins on the terminal transitions) · **reason (the 16-code string; set at every terminal state — §17.1)** · observed_peak_bytes (monotonic) · last_total_bytes · samples · metric · trigger_metric · peak_is_lower_bound · under_report_bound_bytes · confirmed_pss_bytes · tree_complete · members_total/killable/unreachable · unreadable_pids · gone_pids · enumeration_failures · breach_at · killed_at · last_sample_at · trigger (rss_threshold | pss_threshold | pss_confirm_unavailable) · rearm_threshold_bytes · last_confirm_rss_bytes · rearm_count · confirm_count`

**Honest-peak bound (frozen):** per-sample under-report ≤ `R·Δt`, computed from the samples; total exposure = `R·(Δt + confirm_deadline + grace)`.

### 4.5 The durable event — OWNER, EMITTER, BRIDGE (binding)

**Injected emitter callable** (`RamBreachEmitter = Callable[[str, Mapping[str, Any]], bool]`, type-only `events.py` inside the package; the implementation + the **non-blocking** `run_coroutine_threadsafe` bridge live OUTSIDE at `daemon/services/ram_guard_events.py`, loop from `manager._get_event_loop()` — never `MainLoopBridge`). The **execution-lane thread** calls it **exactly once** at the `WATCHING→KILLED` transition, after the ladder, outside the registry lock, wrapped so it can never raise into the lane. **Refusals emit NO event** (§17.2). `instance_id=None` ⇒ no call (defensive). Drops visible: `ram_events_dropped++` + the transition log line.

**Event payload key set (ordered, exact) — `reason` inserted after `surface` (escalation pass):**

```
ok, code, surface, reason, limit_bytes, rearm_threshold_bytes, observed_peak_bytes, peak_is_lower_bound, poll_interval_ms, metric, trigger_metric, confirmed_pss_bytes, under_report_bound_bytes, pid, pgid, tree_complete, members_total, members_killable, members_unreachable, grace_ms, killed_at, ram_breach_observed, coincident_timeout, timeout_seconds, message, trigger, state, tracking_id, breach_at
```

**Producer-side allow-list:** `build_ram_event_payload(record)` is the ONLY producer; only the listed keys, in order; the bus persists `data` unsanitized. Negative space identical to the envelope's.

### 4.6 `RamGuardConfig` → root `Config` (binding)

1. **Class** — `class RamGuardConfig(BaseSettings)`, `env_prefix="RAM_GUARD_"`, with: **`ceiling_mib: Optional[int] = Field(default=None, ge=64, le=65536)`** — `None` ⇒ the dynamic 70 % default (§4.7); an explicit value is validated `[64, 65536]` — **the clamp applies to explicit values ONLY (§4.7c)**; `poll_interval_ms: int = Field(default=200, ge=50, le=2000)`; `grace_ms: int = Field(default=250, ge=0, le=2000)` (floor 0 argued in DQ4); `pss_confirm_deadline_ms: int = Field(default=250, ge=50, le=2000)`. Module constants (not knobs): the §4.1a pair, `RAM_GUARD_PENDING_MAX`, `RAM_GUARD_GRACE_POLL_SLICE_SECONDS`, `WAIT_SLACK_MS`, and §4.7's detector constants.
2. **Root field** — `ram_guard: RamGuardConfig = Field(default_factory=RamGuardConfig)` (mirroring `resource_monitor`, `config.py:6812-6814`); the yaml block comment-only. **AMENDED (escalation pass): `load_config` gains ONE branch** — the OFF short-circuit (item 4 below); nothing else.
3. **The master switch is NOT a pydantic field** — the reaper-semantics parser (absent/empty ⇒ ON; else `not in {"0","false","no","off"}` ⇒ ON), carried on the root `Config` as a derived value.
4. **Boot-abort rung — AMENDED (§17.4): OFF beats validation.** `load_config` parses the bare switch **FIRST**; when **OFF**, it passes `config_dict["ram_guard"] = RamGuardConfig.model_construct(ceiling_mib=None, poll_interval_ms=200, grace_ms=250, pss_confirm_deadline_ms=250)` — `model_construct` bypasses **both** validation and the settings sources, and pydantic v2 does not re-validate an exact-class instance passed into a field ⇒ **no `ge`/`le` check runs while OFF**: `ENSEMBLE_RAM_GUARD=0` + an out-of-range ceiling (env OR yaml) **boots fine** with the guard inert. When **ON**: normal construction (env tier + validation + **BOOT ABORTS** on out-of-range, per `api.py:2266-2278`). Enabling later with the bad value still set aborts at that boot — a misconfigured guard cannot be enabled silently.
5. **YAML-comment-only rule** — unchanged.
6. **OFF semantics** — a complete no-op (no threads, no registration, no measurement, zero DB queries, `app.state.ram_guard is None`, one INFO line) — **now structurally guaranteed to precede validation** (item 4).
7. **The assertion shapes (escalation-pass set):**
```
config = load_config(str(yaml_file)); assert config.ram_guard.ceiling_mib is None      # unset ⇒ dynamic
assert config.ram_guard.poll_interval_ms == 200; assert config.ram_guard.grace_ms == 250
assert config.ram_guard.pss_confirm_deadline_ms == 250
monkeypatch.setenv("RAM_GUARD_CEILING_MIB", "64"); config = load_config(str(yaml_file))
assert config.ram_guard.ceiling_mib == 64
with pytest.raises(ValidationError): load_config(str(yaml_file))                       # explicit out-of-range ⇒ ABORTS (ON)
monkeypatch.setenv("ENSEMBLE_RAM_GUARD", "0"); monkeypatch.setenv("RAM_GUARD_CEILING_MIB", "99999")
config = load_config(str(yaml_file))                                                    # OFF ⇒ NO ABORT (§17.4)
assert config.ram_guard_derived_switch is False
```
Plus the §4.7(f) detector tests (mocked roots; the derived value; the no-upper-clamp case; the fallback).

### 4.7 The DYNAMIC DEFAULT CEILING — USER RULING #2's mechanism (NEW — binding)

**(a) Detection (container-aware, by construction):**

```
detect_effective_total_bytes(proc_root: Path, cgroup_root: Path) -> tuple[int, str]
```
A **pure, injectable** function (the §4.1 discipline — no host reads in unit tests): `MemTotal` from `<proc_root>/meminfo`; the daemon's own cgroup limit from `<proc_root>/self/cgroup` → `<cgroup_root>/<path>/memory.max` (cgroup v2; the literal `max` or an absent file ⇒ unlimited) with the v1 `memory.limit_in_bytes` fallback; **`effective_total_bytes = min(MemTotal_bytes, cgroup_limit_bytes)` when the cgroup limit is finite, else `MemTotal_bytes`**. A constrained cgroup therefore never derives 70 % of the host. Non-Linux ⇒ INERT (unchanged). Lives in a new pure module `daemon/services/ram_guard/ceiling.py`; the guard resolves at start.

**(b) Timing — ONE boot-time snapshot** at guard start (lifespan): `resolved_ceiling_bytes` + `ceiling_source ∈ {meminfo, cgroup, fallback-fixed}`, stable for the daemon's lifetime; a restart re-derives. **Justification:** (i) `limit_bytes` stability — a per-execution read would make the envelope's `limit_bytes` vary within a session, breaking envelope honesty and `SpawnRecord` determinism; (ii) `MemTotal` is quasi-static (hotplug is exotic; the restart cadence covers balloon drivers); (iii) zero tool-path cost. The cgroup limit CAN move live (the operator canary) — **it does not re-derive a running guard; the runbook states a restart re-resolves** (§3.5 item 15). The resolved pair is exposed on the endpoint (B23).

**(c) THE CLAMP INTERACTION (load-bearing):** the `[64, 65536]` bounds **validate EXPLICIT values only**. The derived default = **`max(67108864, int(0.7 × effective_total_bytes))`** — floored at 64 MiB (a pathological-host guard), **NOT upper-capped**: capping at 64 GiB would make the guard kill a legitimate 100 GiB job on a 200 GiB host — **against USER RULING #1's binding intent** (disaster prevention, not a constraint on legitimate heavy work). On hosts > ~91 GiB the derived value legitimately exceeds 65536 MiB; that is correct, not a defect.

**(d) Envelope honesty:** `limit_bytes` = the RESOLVED ceiling (derived or explicit); `rearm_threshold_bytes` and all arithmetic are relative to it; the B10 message clause says **"the resolved ceiling"**.

**(e) §3.4 re-derived** (see there): on this host the derived default is **8,367 MiB (8.171 GiB)**; no feasible unit cap exists at the default on hosts < ~26.5 GiB; the 3 GiB figure is demoted to operator guidance; the memo's guidance re-derived (no-cap / lower-the-knob / resize).

**(f) Tests:** the unset-knob path derives from a **MOCKED detector** (injectable `proc_root`/`cgroup_root` fixtures: a plain host; a constrained cgroup — 70 % of the LIMIT, not the host; an unlimited cgroup — 70 % of MemTotal; a >91 GiB host — the derived value exceeds 65536 with NO abort, proving the no-upper-clamp ruling; a sub-64 MiB effective total — the floor); the **explicit** path is unchanged (env override; yaml trap; boot-abort on out-of-range while ON). **Detection failure ⇒ the fixed fallback `3221225472` (3072 MiB) + one WARN — never aborts boot** (a failed detector must not disarm or kill the daemon; the guard stays armed at the conservative fixed figure).

---

## 5. Failure-path rulings (Phase 6 must test every row)

| Failure | Ruling |
|---|---|
| **Tick thread dies / WEDGED** | Restart loop; a never-returning scan read is not recoverable (no supervisor) — `last_tick_at` frozen is the signal; the unit cap is the backstop. |
| **Execution-lane dies / WEDGED** | Restart loop / a never-returning `smaps_rollup` read stalls the kill path, never measurement — `last_lane_progress_at` frozen (NOT `Thread.is_alive()`) + `lane_pending` climbing are the signals. |
| **Registration leak** | `finally`-shaped release conditional on the watch being finished + the bounded non-lethal liveness sweep (tick thread); `group_member_count == 0` ⇒ release. |
| **Pre-existing leak (disclosed)** | The bash generic-exception arm (`bash.py:1223-1224`); Phase 3 adds the guard release; the rest is a routed follow-up. |
| **Double-kill race** | **EPISODE-scoped claim token** (§17.6): taken by the tick thread at observation, released when the episode resolves (KILLED / OVERCOUNTED re-arm / REFUSED); a new claim exists only after the previous episode's release under the registry lock — one owner at all times. `-9` is ambiguous by construction; the verdict record is the only discriminator. |
| **Pending-set overflow** | The newest breach is NOT claimed; re-offered next tick; no confirm skipped; visible via `lane_pending`. |
| **Peak under-report** | Sampled lower bound (§4.4). |
| **Event undeliverable** | DROPPED + counted + the transition log line (§4.5). |
| **Leader identity unverifiable** | `refused:leader-unreadable` (fail-closed) — **TRANSIENT**: the episode re-arms to WATCHING, re-claimable on a later tick (§17.2); the watch itself never drops. |
| **Refusal (permanent class)** | **§17.2:** terminal `REFUSED` + release; the in-flight refusal surface is the additive `ram_kill_refused: <reason>` lines; no event; counters + the transition log line. |
| **Degradation matrix** | non-Linux ⇒ INERT; `/proc` unreadable ⇒ no measurement, one WARN/spawn; enumeration failure ⇒ drop the sample; daemon group / `pgid≤1` / self ⇒ REFUSE (permanent); identity mismatch ⇒ REFUSE (`refused:pgid-recycled`, permanent); leader absent ⇒ the watch stays alive until the group member count reaches 0 and identity = captured pgid ∧ surviving-member group membership — starttime gate applies to the leader only while alive; the watch is released only at group member count 0 (no signal) ⇒ `already-dead:group-empty` ⇐ `group_member_count == 0`; |

---

## 6. Consumer-gate evidence (the one-time gate artifact — DQ6)

**The census below is the artifact.** All counts are derived conveniences, re-computed by grep at fold time and **never pinned**. Across the enumeration: ≈54–55 assertion sites in 10 test files; 1 inapplicable legacy pack; 0 daemon-side parsers. The "22 consumers" claim is withdrawn.

| # | Consumer surface | file:line | Breaks? |
|---|---|---|---|
| 1–7 | proc_status substrings + the attribute pin | `tests/tools/test_proc_tools.py:240, 257, 382, 1163, 1166-1167, 1233, 1831` | NO (additive; status stays `"killed"`) |
| 8–13 | `info.status == "killed"` group | `tests/tools/test_proc_tools.py:1614, 1655, 1692, 1944, 1992, 2038` | NO |
| 14 | proc_list header | `tests/tools/test_proc_tools.py:664` | NO |
| 15–29 | bash success-string group (`"EXIT CODE: N"`) | 8 files (≈30 sites) | NO — the RAM arm preempts the success arm entirely, only for a RAM-killed command |
| 30–33 | bash `"STDOUT:"` group | 4 files (≈10 sites) | NO |
| 34 | bash timeout string | **ZERO pins** (producer `bash.py:1160`) | NO pin exists; byte-identical only when no breach was observed |
| 35 | chart-tools legacy | `tests/test_chart_tools_legacy_error_contract.py` | INAPPLICABLE |
| 36 | daemon-side `json.loads` of a bash/proc return | NONE | — |
| 37 | daemon-side proc consumers | `proc_tools.py:2162`, `:2231` | NO |
| 38 | LLM/agent-facing | 7 agent docs; none parse the strings | NO (additive) |

**Load-bearing rules:** the four never-changing strings (bash success, bash timeout when no breach, the `proc_status` block, the proc_list header); `pid_registry.VALID_EXIT_KINDS` extended with the `ram_killed` constant. **Ownership: Phase 2 owns the artifact (pins-only); Phase 5 T1 cross-references.**

---

## 7. Module decomposition + thread lifecycle

```
daemon/services/ram_guard/
  __init__.py     facade: get_ram_guard(), register/read_verdict/release
  tree_reader.py  scan_proc / resolve_members / read_tree_rss / read_tree_pss / exceeds   (pure, injectable)
  ceiling.py      detect_effective_total_bytes + the 70% resolution (pure, injectable roots)   [NEW — §4.7]
  verdict.py      SpawnRecord, PeakRecord, breach predicate, OVERCOUNTED re-arm
  ladder.py       ram_kill_ladder + P0–P7 + the P8 post-kill probe + the slice-probed grace
  guard.py        the TICK thread + the EXECUTION-LANE thread + registry + pending set + GuardCounters + liveness sweep + the emitter call site + the boot-time ceiling resolution
  errors.py       format_ram_envelope (one producer, two surfaces) + build_ram_event_payload (the allow-list)
  events.py       RamBreachEmitter type alias ONLY
daemon/services/ram_guard_events.py   OUTSIDE the package: the bus-backed emitter impl + the loop bridge
```

**Import direction (frozen):** `tools/* → ram_guard` only; **`ram_guard → stdlib` only** — nothing from `daemon/` (no tools, no resource_monitor, no repositories, no event_bus, no main_loop_bridge, no bash_spawn_reaper); the event dependency is the INJECTED `RamBreachEmitter`, implemented outside the package, wired at the composition root.

**Thread lifecycle:** FastAPI lifespan (`app.state.ram_guard` owns BOTH threads; OFF ⇒ neither; shutdown `.stop()` + detached join — `api.py:4144-4152`). **The lifespan construction is Phase 5's task (the natural owner — the resource-monitor block's pattern).** Module constants: `RAM_GUARD_PENDING_MAX = 2` · `RAM_GUARD_GRACE_POLL_SLICE_SECONDS = 0.025` · `WAIT_SLACK_MS = 250`. Bash seams: S1 `:952-965`, S2 `:1062-1065`, S3 `:1071` + `:1174-1214` (+ the RAM arm before `:1110`), S4 `:1143-1160`, S5 (six paths), S6 `:1110-1123` (bytes→KB once). Proc seams: S7 `:1231/:1235/:1241-1245`, S10 `:786/:855-875`, S11 `:966-1086`.

**Phase ownership of the event wiring:** Phase 2 = the HOOK; Phase 5 = the EMITTER + the wiring + the lifespan construction.

---

## 8. Sequencing verdict (7 phases — keep the shape)

1. **Phase 2 absorbs the envelope helper + the consumer gate + the event HOOK + the execution-lane thread + the ceiling detector** (`ceiling.py`, §4.7 — pure, injectable, landed with the substrate). The R measurement must include the confirm deadline (typical/worst/contended).
2. **Phase 3 (bash first) must additionally:** respect the one-shot `mark_exit` ordering and the verdict-publication ordering **including the 1270 ms bounded wait** (C9 — `WAIT_SLACK_MS` declared in B20); add the guard release to the generic-exception arm; make the success-path release conditional on the watch being finished; test-pin the `pgid = proc.pid` fallback; the single bytes→KB conversion; **the LLM-facing docstring line**.
3. **Phase 4 (proc) must additionally:** capture pgid + start_time at spawn (a field capture, not a task); `ram_killed: bool` surfaced positively; `user_stopped: true` (🟢).
4. **Phase 5** = the config class (with the Optional ceiling + the OFF short-circuit, §4.6/§4.7) + observability surfaces (the eleven-field heartbeat, B23) + the fence re-run + the event emitter + the lifespan construction.
5. **Phase 7** = the operator options memo (§3.4 re-derived) + the honesty set (§3.5, **fifteen items**) + follow-up routing (including: the post-return agent-injection channel; the instance-less-spawn gate widening; the stale SQLite CHECK).

**Phase 6 additions:** per-thread death modes; pending overflow; the bounded re-arm; both orderings; the fail-open path; the event-drop path; the refusal terminality + the transient re-arm (§17.2); **the dynamic-default detector suite (§4.7f)**; mutation-proof per the house rule. **Success Criterion 3 re-aim:** the deciding value `>= limit_bytes` (the RESOLVED ceiling), in-flight scoped (§17.3).

---

## 9. Risks (aggregated, severity-ordered)

- 🔴 **The guard's own death is silent and fatal** — mitigated externally (the operator drop-in, when feasible) + the per-thread heartbeats.
- 🔴 **The orphan class is out of reach** — only the unit cap covers it.
- 🔴 **The PSS confirm is the only fairness gate** — the pending-overflow rule exists so lane congestion can never fire it.
- 🔴 **`mark_exit` is one-shot** — ordered explicitly (§4.3.1).
- 🔴 **At the 70 % default on this host the guard fires at-or-after daemon death** (§3.3) — **user-accepted by stated intent** (disaster prevention, not constraint); the operator levers are the knob and the host resize (§3.4); no feasible unit cap exists at the default below ~26.5 GiB hosts. Never papered over.
- 🟡 The bounded re-arm's confirm-frequency trade; the uninterruptible `/proc` reads (post-hoc deadline); event-only durability; the coincidence string; **the dynamic default means the ceiling varies BY HOST — the runbook must state the resolved value and its source** (§3.5 item 15).
- 🟢 Naming collision: `bash.py:522` "RAM tier".

---

## 10. Decisions pending

1. **RESOLVED BY USER RULINGS (recorded):** Ruling #1 (8 GiB confirmed; intent = disaster prevention) is superseded in its fixed figure by Ruling #2 (the 70 % dynamic default); the intent is binding and carried (§3.3, §4.7c). No user decision remains open on the default.
2. **The unit-cap drop-in is a user action** — and at the 70 % default on this host **no feasible pair exists** (§3.4); the memo presents the three options; the choice (no cap / lower the knob / resize) is the operator's.
3. **Pre-freeze check (Phase 2 entry):** unchanged from iteration 002 (method/owner/bounded fallback in §10 item 3 as amended).
4. **The host-resize decision** — generalized: at the default, the preventive layering needs T ≥ ~26.5 + 2.33·R GiB; the knob is the cheaper lever.

---

## 11. Gaps

Unchanged from iteration 002, plus: (g) the escalation pass was architect adjudication of leader-framed decisions with first-hand verification (E11) — no worker fan-out remained for fully-framed narrow rulings, and the mode is disclosed; (h) the §3.4 general-feasibility bound (`T ≥ 26.5 + 2.33R`) is derived from the worst-case frame and should be re-confirmed against Phase 2's measured R and probe cost; (i) the derived-default tests require the mocked-detector fixtures (§4.7f) — not yet written (Phase 2/5's job).

---

## 12. Plan reconciliation

Unchanged: the planner folds this document into the phase docs. **Escalation-pass supersessions for the fold:** the default-ceiling story (§3.3/§4.7), the `reason` key (§17.1), the refusal terminality + surfaces (§17.2), the in-flight scoping of the typed error (§17.3), OFF-beats-validation (§17.4), the episode-scoped token (§17.6), the eleven-field heartbeat (B23), and the planner-fold bases in §17.7.

---

## 13. Addendum — leader rulings (council cycle 1)

### 13.1 Q1 — proc lane shape (LEADER-CONFIRMED)
No 4th task; the guard threads own measurement/decision (tick) and kill/publication (lane); the proc lane creates exactly three tasks; the pgid capture is a field capture per §8 item 3. *(The dangling `§8.3` reference is corrected to §8 item 3.)*

### 13.2 Q2 — leaderless-tree failure mode (LEADER-RULED)
The watch stays alive until the group member count reaches 0; identity re-anchors on the surviving group members; the leader `starttime` gate applies only while alive. *(Propagated in §2 DQ4, §4.1, §4.2 (P5/P5a/P7), §5, §4.3.1; `refused:leader-unreadable` for the unreadable case; P5a added; the release-boundary consequence in §4.3.1 item 4.)*

### 13.3 Scope note
Neither ruling alters §4.1, §4.3, §4.4, DQ1, DQ3, DQ5–DQ10, or the R1 resolution's mechanics.

### 13.4 Amendment passes
Iteration 001 (7 blockers): §14. Iteration 002 (5 blockers): §16. **Iteration 003 (13 blockers, REJECTED at max iterations, ESCALATED): the leader's TrueAuto disposition — the Class A rulings + the user rulings are folded HERE (§17) and implementation proceeds with no further approver calls; P1/P3/P4/C2 are closed and not re-opened.**

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
| **`instance_id=None` spawns unguarded vs the DQ8 assumption** | **NARROWED at iteration 002 (§16.3):** the iteration-001 claim ("an `instance_id=None` spawn IS guarded") is **withdrawn** — it is not implementable without widening the bash seam's registration gate, which lives inside `if instance_id is not None and proc is not None:` (`bash.py:952-1006`), and that restructure is NOT commissioned. When instance identity is absent, the spawn is **not registered with the guard in v1** (today's unregistered path); a ballooning tree from an instance-less call is invisible to the guard and only the unit cap covers it — disclosed (§3.5 item 9b); widening the gate is a **routed follow-up**. DQ8's `instance_id=None ⇒ counter + log only` sentence remains as **defensive** behavior if a registered record ever carries a None instance_id. |
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

**B2 — the envelope key set (ordered, exact — `reason` added, escalation pass):**
```
ok, code, surface, reason, limit_bytes, rearm_threshold_bytes, observed_peak_bytes, peak_is_lower_bound, poll_interval_ms, metric, trigger_metric, confirmed_pss_bytes, under_report_bound_bytes, pid, pgid, tree_complete, members_total, members_killable, members_unreachable, grace_ms, killed_at, ram_breach_observed, coincident_timeout, timeout_seconds, message
```

**B3 — the event payload key set (ordered, exact — `reason` added, escalation pass):**
```
ok, code, surface, reason, limit_bytes, rearm_threshold_bytes, observed_peak_bytes, peak_is_lower_bound, poll_interval_ms, metric, trigger_metric, confirmed_pss_bytes, under_report_bound_bytes, pid, pgid, tree_complete, members_total, members_killable, members_unreachable, grace_ms, killed_at, ram_breach_observed, coincident_timeout, timeout_seconds, message, trigger, state, tracking_id, breach_at
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

**B8 — the config wiring (AMENDED — escalation pass):**
```
ram_guard: RamGuardConfig = Field(default_factory=RamGuardConfig)
model_config = SettingsConfigDict(env_prefix="RAM_GUARD_")
ceiling_mib: Optional[int] = Field(default=None, ge=64, le=65536)
poll_interval_ms: int = Field(default=200, ge=50, le=2000)
grace_ms: int = Field(default=250, ge=0, le=2000)
pss_confirm_deadline_ms: int = Field(default=250, ge=50, le=2000)
ENSEMBLE_RAM_GUARD
0, false, no, off
```

**B9 — the assertion forms (escalation-pass set — supersedes the iteration-001 set):**
```
config = load_config(str(yaml_file)); assert config.ram_guard.ceiling_mib is None      # unset => dynamic
assert config.ram_guard.poll_interval_ms == 200; assert config.ram_guard.grace_ms == 250
assert config.ram_guard.pss_confirm_deadline_ms == 250
monkeypatch.setenv("RAM_GUARD_CEILING_MIB", "64"); config = load_config(str(yaml_file))
assert config.ram_guard.ceiling_mib == 64
with pytest.raises(ValidationError): load_config(str(yaml_file))                       # explicit out-of-range => ABORTS (ON)
monkeypatch.setenv("ENSEMBLE_RAM_GUARD", "0"); monkeypatch.setenv("RAM_GUARD_CEILING_MIB", "99999")
config = load_config(str(yaml_file))                                                    # OFF => NO ABORT
assert config.ram_guard_derived_switch is False
```
**B10 — the message clause (AMENDED — "resolved", escalation pass):**
```
The enforced trigger threshold was {threshold_mib} MiB (the resolved ceiling is {limit_mib} MiB); when the summed-RSS metric over-counts shared pages the threshold is re-armed above the ceiling and the kill decision is always made on the shared-page total.
```

**B11 — the §3.4 truth statement:** as written in §3.4 (the escalation-pass version — the derived-default regime + the knob/host levers).

**B12 — the fixture split and the test-name shape:**
```
kill_fence      (the P0–P7 REFUSAL legs only)
kill_census     (the ladder and the P8 legs)
test_p<n>_<case>_<expected_status>
```

**B13 — the §6 census:** the table, verbatim; counts never pinned.

**B14 — the coupling-map replacements (renumber-proofed at iteration 002):**
```
tight (P5 consumes the envelope producer, the counters and the guard heartbeat from P2 — Phase 5's envelope-event and counter tasks depend on Phase 2's envelope-producer and guard-thread tasks)
loose (DQ6's consumer-gate artifact now lives in P2; P5 implements DQ7 — the config surface)
```

**B15 — the phase-ownership lines (AMENDED — + the lifespan owner):**
```
Phase 2 owns the event HOOK, the DQ6 consumer-gate artifact (pins-only), the execution-lane thread, and the ceiling detector (ceiling.py).
Phase 5 owns the event EMITTER, the loop bridge, the lifespan wiring AND the lifespan construction (app.state.ram_guard), and the root-Config wiring of RamGuardConfig.
Phase 7 owns the operator documentation and the honesty set (fifteen items).
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

**B20 — the verdict-wait deadline (AMENDED — `WAIT_SLACK_MS` DECLARED, C9):**
```
WAIT_SLACK_MS = 250
wait_deadline_ms = RAM_GUARD_PENDING_MAX × T_service_worst_ms + WAIT_SLACK_MS
T_service_worst_ms = PSS_CONFIRM_DEADLINE_MS + GRACE_MS + PROBE_MS
wait_deadline_ms = 2 × 510 + 250 = 1270
```
*Derivation of the slack (so the 250 is recoverable without back-substitution): one poll interval (200 ms — the tick could hand off just after a sample) + 50 ms scheduling/jitter margin.*

**B21 — the release clauses (AMENDED — the episode-scoped token + the release triggers):**
```
A release of a CLAIMED record is deferred to the lane (the lane releases after publishing).
A live watch is never ended by a lane exit; the liveness sweep owns the release at group_member_count == 0.
The claim token is EPISODE-scoped: taken at observation, released when the episode resolves (KILLED, OVERCOUNTED re-arm, or REFUSED); OVERCOUNTED publishes no verdict — the re-armed threshold IS the episode's recorded outcome.
The release triggers: (i) group_member_count == 0 (the tick-thread sweep); (ii) terminal resolution with an empty group (the lane); (iii) the post-kill probe confirming the group empty on the RAM path (the lane).
```

**B22 — the EventKind qualifying sentence (C13: the ONLY surviving pin variant; the 14/10/`models.py:77` figures are WITHDRAWN falsified variants and must not appear in any pin cell):**
```
the SQLite `event.kind` CHECK constraint lists only 8 kinds while `EventKind` has 16 — it is already stale for 8 pre-existing kinds; the production deployment is PostgreSQL (`create_all`; the SQLModel column carries no CHECK — `models.py:74`), so `ram_limit_exceeded` (the 17th member) is unaffected
```

**B23 — the endpoint payload (NEW — escalation pass; supersedes every six- or nine-field variant in the plan set):**
```
enabled, thread_alive, last_tick_at, missed_samples, ticks, registry_depth, lane_alive, lane_pending, last_lane_progress_at, resolved_ceiling_bytes, ceiling_source
```
(plus the B5 counter set; `ceiling_source ∈ {meminfo, cgroup, fallback-fixed}`)

**B24 — the dynamic-default contract (NEW — USER RULING #2):**
```
detect_effective_total_bytes(proc_root: Path, cgroup_root: Path) -> tuple[int, str]
resolved_ceiling_bytes = max(67108864, int(0.7 * effective_total_bytes))
effective_total_bytes = min(MemTotal_bytes, cgroup_limit_bytes) when the cgroup limit is finite, else MemTotal_bytes
ceiling_mib unset ⇒ derived; explicit ⇒ validated [64, 65536] — the clamp NEVER applies to the derived value
detection failure ⇒ resolved_ceiling_bytes = 3221225472 (3072 MiB) + one WARN; never aborts boot
limit_bytes = the RESOLVED ceiling (derived or explicit)
```

**B25 — the refusal and pending surfaces (NEW — escalation pass):**
```
ram_kill_refused: <reason>
ram_breach_observed: true
```
*(additive lines in the bash return / `proc_status`; output is never swallowed; distinct from `code="ram_limit_exceeded"` by construction; the wait-expiry surface is the ORDINARY return + these lines — a pending decision is reported as pending, never fabricated.)*

**B26 — the OFF short-circuit (NEW — escalation pass):**
```
load_config parses ENSEMBLE_RAM_GUARD FIRST; when OFF it passes config_dict["ram_guard"] = RamGuardConfig.model_construct(ceiling_mib=None, poll_interval_ms=200, grace_ms=250, pss_confirm_deadline_ms=250)
```

**B27 — the refusal classification (NEW — escalation pass):**
```
transient (re-arm to WATCHING; re-claimable on a later tick): refused:leader-unreadable, refused:group-unknown
permanent (terminal REFUSED; released): refused:guard-disabled, refused:not-registered, refused:platform-unsupported, refused:pgid-not-registered, refused:pgid-invalid, refused:pgid-self, refused:daemon-group, refused:pgid-recycled, refused:ownership-mismatch
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

- **`instance_id=None` — NARROWED (no restructure commissioned).** The iteration-001 claim ("an `instance_id=None` spawn IS guarded … the guard's `register()` therefore accepts a `None` `instance_id`") is **withdrawn**: the bash seam's registration/capture block lives inside `if instance_id is not None and proc is not None:` (`bash.py:952-1006`), and widening that gate is a restructure this commission does not name. **The narrow claim:** when instance identity is absent, the spawn is **not registered with the guard in v1**; a ballooning tree from an instance-less call is invisible to the guard and only the unit cap covers it — disclosed as §3.5 item 9b; widening the gate (capture pgid/starttime for `proc is not None` only; register keyed by `tracking_id`) is a **routed follow-up**. DQ8's `instance_id=None ⇒ counter + log only` sentence stays as **defensive** behavior (§4.5), not a v1 bash behavior.
- **Phase-2 T20 pre-freeze gate (a) — method / owner / bounded fallback named (§10 item 3):** method = one read-only session (cgroup `memory.stat` anon-vs-file + `MemoryCurrent`; a `ps` rollup of the unit vs the tracked children; the Oct-30 journal windows); owner = Phase 2 T20 executes and records, Phase 7 carries it into the memo; bounded fallback = inconclusive ⇒ **conservative** D_max shift (`+ min(residue, 1.0 GiB)`) and the §3.4 table re-published — **the freeze is never blocked on inconclusiveness**.

### 16.4 Changed anchors (iteration 002, for the diff)

| Iteration-001 line | Change |
|---|---|
| :34 (§0 item 6) | "18 codes" → "16 codes … corrected in iteration 002, §16" |
| :54 (E9) | EventKind count corrected — the 14/:77 figures are WITHDRAWN falsified variants (E10/B22 carry the only surviving pin: 16/8/:74) |
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
## 17. ESCALATION-PASS LEDGER — iteration 003 (user rulings + Class A + C9/C13)

### 17.0 The user rulings (binding, folded)

**RULING #1 (superseded in its fixed figure):** the default was user-confirmed at 8 GiB; **intent: disaster prevention, not a constraint on legitimate heavy work.** The fixed 8 GiB is superseded; the intent is binding and is carried into §4.7c (the derived default is never upper-capped) and §3.4's guidance.
**RULING #2 (THE default):** unset knob ⇒ ceiling = **70 % of the detected effective total RAM** (dynamic); explicit config overrides. Mechanism §4.7: (a) `detect_effective_total_bytes` (meminfo + the daemon's own cgroup limit, `min` when finite — container-aware by construction; this host's cgroup is `0::/system.slice/ensemble-main.service`, E11); (b) **one boot-time snapshot** (stability of `limit_bytes`, `SpawnRecord` determinism, zero tool-path cost; a restart re-derives — §3.5 item 15); (c) **the clamp validates explicit values only** — the derived value is floored at 64 MiB and **never upper-capped** (capping would over-constrain legitimate heavy work on big hosts, against Ruling #1's intent); (d) `limit_bytes` = the RESOLVED ceiling (B10 re-pinned with "resolved"); (e) §3.4 re-derived — on this host the default is **8,367 MiB (8.171 GiB)**; no feasible unit cap at the default below ~26.5 GiB hosts; the 3 GiB evidence demoted to operator guidance; the memo presents no-cap / lower-the-knob / resize; the on-this-host fires-at-or-after-daemon-death disclosure RETAINED and user-accepted; (f) the unset path derives from a **mocked detector**; the explicit path unchanged.

### 17.1 Class A1 — the reason sink
`reason` (the 16-code enum string) is added to: the **SpawnRecord** (§4.4, set at every terminal state), the **envelope key set** (§4.3/B2, immediately after `surface` — the 3-key house prefix stays byte-identical), and the **event payload** (§4.5/B3, same position). The four `killed:*` discriminations are now observable in production; refusals no longer collapse into the p0…p7 buckets (the counters remain as aggregates). DQ6-additive; no existing string changes.

### 17.2 Class A2 — refused / deadline-expiry terminality
- **`REFUSED` is a terminal state**: `WATCHING → BREACHED → REFUSED` (first-writer-wins), carrying `reason`. **Transient refusals** (`refused:leader-unreadable`, `refused:group-unknown` — B27) re-arm to WATCHING (re-claimable on a later tick; tick-quantized; counters make spin visible; the sweep releases dead records); **permanent refusals** (the other nine) terminate and release — for the safety rails this is the CORRECT outcome, not a failure.
- **The in-flight refusal surface (B25):** the bash return = the command's ordinary output + `ram_kill_refused: <reason>` + `ram_breach_observed: true` — additive lines, output never swallowed (the command succeeded), distinct from `code="ram_limit_exceeded"` by construction; the proc lane mirrors the line in `proc_status`.
- **The wait-expiry surface:** the §4.3.1 1270 ms expiry on a non-terminal record composes the ORDINARY return + `ram_breach_observed: true` + the peak — pending is reported as pending, never fabricated; the post-return outcome is §17.3's disclosure.
- **No burn without outcome:** because `REFUSED` is terminal, the bash wait exits the moment the ladder refuses; the full 1270 ms burns only while a confirm is genuinely still running (and then the surface above applies). **Refusals emit NO DQ8 event** (operational, not forensic: counters + the transition log line).

### 17.3 Class A3 — post-return typed error (leader framing, worded)
**The v1 scope is honest: the typed `ram_limit_exceeded` error is guaranteed only while the tool call is IN FLIGHT.** A breach that fires after the call has returned (the `cmd &` shape — the shell exits with `EXIT CODE: 0`, the surviving grandchild balloons, the watch outlives the call by ruling Q2) reaches no caller: the tool has already returned. Such post-return kills surface via the **durable event** and the **guard endpoint**, which the agent does not automatically see. **There is NO new agent-injection channel in v1** — adding one is a **routed follow-up** (Phase 7's queue). This is disclosed as §3.5 item 14, mirroring the proc-lane disclosure symmetry (item 12); the Objective and Success-Criterion-3 assertions are scoped to in-flight calls. **Explicit ACK (the approver's note):** the commission's "returns … error" clause is read **lane- and phase-scoped** — guaranteed on the bash lane in flight; the proc lane is query-only **as an acknowledged structural scope concession** (`proc_run` returns `process_id, None` at `proc_tools.py:1310` before any breach can exist), not merely a disclosure.

### 17.4 Class A4 — OFF beats validation
`ENSEMBLE_RAM_GUARD=0` + an out-of-range ceiling **must not abort boot**. **Mechanism (B26):** `load_config` parses the bare switch FIRST; when OFF it passes a `RamGuardConfig.model_construct(<defaults>)` instance into `config_dict` — `model_construct` bypasses both validation and the settings sources, and pydantic v2 does not re-validate an exact-class instance, so **no `ge`/`le` check runs while OFF** (env or yaml). When ON, construction is normal (env tier + validation + abort on out-of-range). §4.6 items 4 & 6 are amended accordingly; the assertion is in §4.6 item 7; the phase5 wiring carries the branch. Enabling later with the bad value still set aborts at that boot — a misconfigured guard cannot be enabled silently.

### 17.5 Class A5 — the episode-scoped claim token
The token is taken by the tick thread at observation and **released when the EPISODE resolves** — resolution = the confirm's verdict: `KILLED` (terminal), **`OVERCOUNTED` re-arm (the token is RELEASED; the re-armed threshold is the episode's recorded outcome — OVERCOUNTED publishes no verdict)**, or `REFUSED` (permanent ⇒ terminal+release; transient ⇒ re-arm to WATCHING). A later breach at the re-armed threshold is a **NEW episode with a fresh claim** — exactly one owner at all times (a new claim exists only after the previous release, under the registry lock), no double-claim race, no dead path: **test-plan S13 is achievable** (overcount → re-arm → later breach → new episode → confirm → kill). B21 carries the full clause set including the three release triggers.

### 17.6 C9 / C13
- **C9:** `WAIT_SLACK_MS = 250` is DECLARED (B20) with its derivation (one poll interval 200 ms + 50 ms jitter margin — recoverable without back-substitution), and the **1270 ms bounded wait is added to §8 item 2's Phase-3 mandate** (§4.3.1 item 2 / §8).
- **C13:** the **only** surviving EventKind pin variant is B22 (16 / 8 / `models.py:74`, repo-verified); the **14/10/`models.py:77` figures are WITHDRAWN falsified variants** and must not appear in any pin cell (stated in DQ10 and B22's header; the §16.4 historical row now marks them as withdrawn rather than reciting them as facts).

### 17.7 Planner-fold bases (the Class B / C blockers — one fold, no further approver calls)

| Approver blocker | Contract basis (this document) |
|---|---|
| B1 (phase3 S5 "ALL SIX" residue) | §4.3.1 item 4 + B21 clause 2 — the S5 seam row must say *conditional on the watch being finished* |
| B2 (phase6 SURVIVES-success sub-leg unowned) | §4.3.1 item 4 + §5's registration-leak row — Phase 6 gains a phase-owned row/exit criterion for the sub-leg |
| B3 (heartbeat six vs nine fields) | **B23** — the eleven-field set is the single pin (the nine-field set + `resolved_ceiling_bytes` + `ceiling_source`); O6/T7 re-pin to B23 |
| C2 (plan-overview mid-string bold) | **B6** — the five strings verbatim, unbolded |
| C3 (zombie SC8 residue) | **§3.5 item 13** — the init-reaps-grandchildren statement; SC8/scope re-aimed at "no LIVE member remains; a transient Z is init's business" |
| C4 (honesty-scope three-way) | **§3.5 items 1–15** — the full fifteen-item set (1–9, 9b, 10–15); the phase7 exit criterion enumerates exactly these |
| C5 (phase7 T1 dual EventKind variants) | **B22 + §17.6** — the falsified variant is withdrawn; only the repo-verified sentence is pinned |
| Notes: S17/O11 row mis-aiming | §5's tick-wedged row (S17 = tick-path silence; O11 = dead-guard) — re-aim the counter-mutation rows at the POSITIVE shapes |
| Note: anchor drift | **E11** — the corrected cites (`bash.py:1156/:1160`, `proc_tools.py:1310`, `bash_spawn_reaper.py:859-866`, `bash.py:952-1006`, stdio_wrapper under `daemon/mcp/`) |

### 17.8 Changed anchors (escalation pass, for the diff)

| Where | Change |
|---|---|
| Header / §0 | + the escalation-pass marker; item 1 superseded-in-part (the 3 GiB default); items 4–9 (the user + Class A rulings) |
| E11 (NEW) | the host facts (MemTotal / 70 % / the cgroup path) + the anchor-drift corrections |
| DQ4 / DQ6 / DQ7 / DQ8 / DQ10 | the reason sink; the additive-rule exercise; the Optional ceiling + the clamp ruling + OFF-beats-validation; the eleven-field heartbeat; the B22-only variant |
| §3.1 / §3.3 / §3.4 / §3.5 | the resolution-rule note; USER RULINGS #1/#2 + the supersession; the re-derived table + the general bound + the truth statement; items 14–15 + "resolved" wording |
| §4.2 | the refusal classification (B27) + the reason sink note |
| §4.3 | `reason` in the key set; the refusal + pending surfaces; the docstring requirement; the corrected bash cites |
| §4.3.1 | `WAIT_SLACK_MS` declared; the expiry surface; REFUSED terminality in the wait |
| §4.4 | `REFUSED` in the state machine; `reason`; the resolved ceiling |
| §4.5 | `reason` in the payload; refusals emit no event |
| §4.6 | the Optional ceiling; the OFF short-circuit (item 4 rewritten); the assertion set (item 7) |
| §4.7 (NEW) | the dynamic-default mechanism (a)–(f) |
| §5 / §7 / §8 / §9 / §10 | the refusal rows; the episode-scoped token; `ceiling.py` + the lifespan owner; the derived-default risk; the resolved decisions |
| §15 | B2/B3 (+reason); B8 (Optional); B9 (superseded pointer); B10 ("resolved"); B15 (+lifespan owner); B20 (+the declaration); B21 (+the token + triggers); B22 (withdrawal note); **B23–B27 (NEW)** |
| §17 (NEW) | this ledger |
