# Architecture Recommendation: RAM-Based Auto-Termination — Phase 1 Design Decision Gate

Date: 2026-11-10 (aggregation completed 20:5x UTC)
Architect instance: c15bdc7d (Architecture Controller — dispatch + aggregation only; no direct design)
Analysis instances: W1 `2ead7080` (data-flow-design, replacement for wedged `ffc1b0ef`) · W2 `acf4bd9d` (resilience-design) · W3 `9796b69c` (security-design) · W4 `4b079967` (system-decomposition) · W5 `3b7e355b` (structural-design) · COUNCIL governor `0153da35` + councilors `96da4747` (agentic) / `b9471c96` (coding), skill `trade-off-analysis`
Base: worktree `/home/nea/ensemble-src-wt-ram-auto-termination` @ `36a9e9c0d`, branch `feature/ram-auto-termination`, base `origin/latest@0efdf106f`
Status: **COMPLETE — 10/10 DQ rulings + R1 + sequencing verdict. Binding.**

---

## 0. Executive summary

The commissioned feature — kill an agent-spawned bash/proc process tree that exceeds a RAM ceiling and return a typed error — is **architecturally sound and should proceed**, with **one commissioned number overturned**, **one layering correction**, and **a set of plan anchors corrected against the code**.

Three findings dominate:

1. **R1's stated premise is false, and the real situation is worse than the plan assumed.** The live unit has **no memory cap at all** (`MemoryMax=infinity`, `MemoryHigh=infinity`; verified via `systemctl show` and by the absence of any `Memory*` directive in the unit file and its drop-ins). The 7G/8G figures the plan treats as shipped are a **never-applied runbook recommendation** (`resource-cost-control/phase8-plan.md` §8.1 — a document whose own text says *"expect MemoryMax=infinity"*). The Oct-30 motivating incidents were **kernel OOM kills** (`Failed with result 'oom-kill'`, unit peak 7.3 GiB), not cgroup-cap kills. Consequence: the commissioned **8 GiB default is unshippable on this 11.9 GiB host** — it fires at-or-after daemon death in both measured incidents. **Ship 3 GiB.**
2. **The failure that actually happened is only partially coverable by this feature.** The council's measured decomposition of the Oct-30 incidents shows the killing memory was largely **orphaned children of a dead daemon incarnation** — invisible to any per-command guard, because the guard's registry dies with the daemon. The guard is the **inner, preventive** layer (in-flight trees); the **unit cap is the outer layer that covers the orphan class**; the kernel global OOM is today the *only* layer actually running. The operator memo is therefore **load-bearing, not optional hardening**.
3. **Summed RSS alone is unsafe as a kill metric — measured, not argued.** A fork-without-exec tree over-counts by **≈ the member count** (measured **8.9×** on a 9-member tree: 2,106 MiB summed RSS vs 237 MiB summed PSS vs ~250 MiB true footprint). With a 3 GiB ceiling, a 12-worker multiprocessing pool touching 250 MiB each would be **false-killed at a quarter of the ceiling**. The trigger/confirm hybrid (cheap RSS trigger per tick, one-shot PSS confirm at breach, fail-open to kill) is **mandatory**, and the PSS confirm is affordable exactly where it is needed (~35 ms median at a 3 GiB tree; p95 130–146 ms; unaffordable only above ~20 GiB of tree total — beyond this host's physical RAM).

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
| E8 | Incident decomposition: during the fast-case OOM window the daemon bucket's RSS *fell* 1,325→151 MiB (restart) while cgroup anon climbed 1,411→6,747 MiB with tracked children ≈3 MiB — the killing memory was **untracked/orphaned**, not a live registered command | council, coding councilor |

### 1.1 Plan claims falsified against the code (Phases 2–7 must not build on them)

| # | Plan claim | Reality (verified) |
|---|---|---|
| P1 | "The live systemd unit ships `MemoryMax=8G`" (R1 premise; Risks #1) | **No cap exists.** See E1–E3. |
| P2 | `pid_registry` closed set at `daemon/repositories/pid_registry/models.py:121-135` | Path **does not exist**. Real site: `daemon/services/resource_monitor/pid_registry.py:120-138` (`mark_exit` raises `ValueError` on unknown kinds, `:409-413`). |
| P3 | "the reaper's PID-ownership verification … `proc_tools.py:1039-1052`" | Two mechanisms conflated. The reaper's real gate is `verify_and_kill` (`bash_spawn_reaper.py:735-900`, 5-rule, DB-row-backed, fail-closed, starttime gate `:851-857`); `proc_tools.py:1037-1053` is a *different*, **fail-open** 1-signal environ check. |
| P4 | "the bash lane's ladder is gated by the ownership check" | The bash lane has **no ownership gate at all** (`_kill_process` `bash.py:697-745` sends `killpg` with no environ/starttime check). |
| P5 | "pgid captured at spawn, never re-derived" (both lanes) | True for bash (`:954-956`, reused `:1067/:1189`). **False for proc**: no pgid is captured; it is re-derived at kill time — `os.killpg(os.getpgid(proc.pid), sig)` (`proc_tools.py:1058`, `:1280`) — exactly the TOCTOU bash already fixed. |
| P6 | Risk #7: check `tests/test_chart_tools_legacy_error_contract.py` before committing the envelope | That test pins **chart tools only** (58 lines; imports neither bash nor proc) — **inapplicable**. The real pin surface is `proc_status` substring assertions (`tests/tools/test_proc_tools.py:240…1831`). |
| P7 | "`proc_readers.read_pss_rollup` exists, so the reader exists" (DQ1 lean) | It is **self-only** — hardcodes `/proc/self/smaps_rollup` (`proc_readers.py:769`). A new per-pid reader is required. |
| P8 | Phase 7: durable verdict ⇒ "migration-backed vocabulary extension" of `reap_reason` | `reap_reason` is plain `TEXT` with **no CHECK constraint** — a vocabulary extension is code-only. (Moot: DQ10 rules no durable verdict.) |
| P9 | "`list_group_members` is the discovery primitive (fail-closed, tested)" | It is correct but **not injectable** (hardcodes `/proc`) ⇒ cannot serve as the hermetic seam; and per-spawn invocation costs 9.49 ms/scan — N spawns overrun a 200 ms tick (see DQ3). |

---

## 2. DQ1–DQ10 — binding rulings

| DQ | Ruling | Rejected | Key evidence |
|---|---|---|---|
| **DQ1** — tree RAM metric | **(e) HYBRID, and the confirm is MANDATORY**: trigger = `summed_rss_bytes` (Σ `VmRSS` per member, kB×1024, compared per tick); decision = one-shot **`summed_pss_bytes` confirm** at breach (re-enumerate + Σ `Pss` from `smaps_rollup`), 250 ms deadline, **fail-open ⇒ kill** on confirm-failure/timeout. Overcounted trees (PSS < ceiling) enter `OVERCOUNTED` and re-arm at `ceiling + shared_extra_bytes` where `shared_extra = summed_rss − summed_pss` (additive — post-fork *private* growth moves both metrics 1:1). | pure RSS (false kill at factor ≈N, measured 8.9×); pure PSS per tick (17 % duty at 3 GiB/200 ms, scales with the tree); per-spawn cgroup (no delegation, `systemd-run` unavailable, no `setrlimit` in `daemon/`); unit cgroup (cannot attribute) | E6; W1-r2 §1 |
| **DQ2** — loop home + cadence | **(b) ONE dedicated daemon thread**, started/stopped on the **FastAPI lifespan** (wedge-capture + resource-monitor precedents; `app.state.ram_guard`; OFF ⇒ no thread at all). Wedge rules inherited structurally: tick path **never logs**; **no lock held across a /proc read, a signal, or a sleep**; **no PSS read on the tick path**; verdict writes RAM-only. Poll default **200 ms**, bounds **[50, 2000] ms**. Lazy first-registration start **rejected** (forks the health contract). | in-execution asyncio task (a starved loop cannot run it — the exact scenario the feature exists for); riding the 30 s sampler (100× too slow); a supervisor thread (two failure modes; house accepts none for wedge-capture) | W2, W4; `wedge_capture/watchdog.py:1-49` |
| **DQ3** — tree discovery | **(c) HYBRID from ONE shared `/proc` scan per tick**: `scan_proc(proc_root)` builds `pid → (state, ppid, pgrp)` once per tick; membership = **group relation (`pgrp == G`) ∪ descendant closure from the leader (bounded depth/node caps)**. Zombies + kernel threads excluded. Enumeration failure ⇒ **drop the sample** (peak keeps its value, no breach evaluated, `enumeration_failures++`) — never kill on no-evidence; the unit cap is the coarse net for "could not measure". Per-spawn scans **rejected** (9.49 ms × N overruns the tick). | group-only (misses setsid escapees ⇒ under-reports); ppid-only (misses orphans re-parented out of the closure but still in the group); reusing `list_group_members` verbatim (not injectable, per-spawn cost) | E6; W1-r2 §2 |
| **DQ4** — kill ladder + vocabulary | **(d) NEW sync short-grace ladder** shared by BOTH lanes: `ram_kill_ladder(pgid, leader_start_time, grace_seconds) -> (status, reason)`; TERM → slice-probed grace (reaper `_grace_wait_for_death` shape) → KILL; zombie-aware skip; `os.killpg` from the guard thread. **Grace default 250 ms, bounds [0, 2 000] ms, a SEPARATE knob** from the 5 s timeout grace. Identity gate: **captured pgid + leader `starttime` re-verification** (`/proc/<pgid>/stat` field 22, fail-closed; a missing leader ⇒ group empty ⇒ `already-dead`), environ check demoted to secondary. Vocabulary: `exit_kind="ram_killed"` (constant, never a literal — `mark_exit` raises); proc gets `ram_killed: bool` on `ProcessInfo`, **status stays `"killed"`**; `reap_reason` untouched. | reusing `_kill_process` (async + 5 s grace: at 3 GiB/s that is 15 GiB — the kernel OOM wins; C_max collapses to 0.68 GiB); routing through `_attempt_kill_signal` (re-derives pgid — P5; bash has no tracking id so its gate silently fail-opens); a new proc status value (breaks 3 test pins + the post-kill branch) | W2 §DQ4; council constraint #1 |
| **DQ5** — substrate + seams | **(a) RAM-only in-process map**, keyed by `tracking_id`, both lanes. **The proc lane registers with the guard only — NO new durable write path.** The guard writes nothing durable; the bash `bash_spawn_registry` row is left **OPEN** on a RAM kill so the reaper's env-stamp escapee net stays armed (it sweeps open rows only) and closes it via the existing `ram-tier-dedup` path at terminal cleanup. (c) stays illegal (fence); **no lift routed**. | durable extension (redundant with the event; a proc write path for zero enforcement value; closing the row silently disarms the escapee net) | W4; W3 critical interaction |
| **DQ6** — typed error | **Mirror `_format_secret_envelope`; the envelope IS the entire bash return string**; numeric fields are **siblings before `message`**; `code="ram_limit_exceeded"`, `surface="bash.exec"\|"proc.exec"`. **Proc placement differs structurally**: `proc_run` returns before any breach can exist — the proc verdict rides `proc_status` as a `ram_breach:` line + a flat `ram_killed: true` (added as lines, never replacing the block). **Consumer gate: SATISFIED by evidence** (22-consumer verified list; the feared legacy test is inapplicable) — it is a one-time gate artifact, not a per-phase ritual. Frozen evolution rule: additive keys allowed; any removal/rename/type-change requires a new `code`. No version field. | appending the envelope to output (neither JSON nor suffix-parseable; truncation arm could cut it); numerics inside `message`; `Error:`-prefixing the proc envelope (conflates "could not answer" with "answered: killed"; breaks `assert "Error:" not in result`) | W5 (full consumer list in §6) |
| **DQ7** — config surface | **(b)+(a) hybrid, as leaned**: bare **`ENSEMBLE_RAM_GUARD`** master kill-switch (reaper semantics: default ON when absent; `0/false/no/off` case-insensitive = OFF; OFF = **complete no-op** — no thread, no registration, no measurement, zero DB queries; one INFO line at lifespan start) + a prefixed class **`RamGuardConfig(env_prefix="RAM_GUARD_")`** for numerics (INT-only, `ge`/`le`, validated outside the try/except ⇒ out-of-range **aborts boot**; YAML block **comment-only**). Knobs: `RAM_GUARD_CEILING_MIB` default **3072** [64, 65536]; `RAM_GUARD_POLL_INTERVAL_MS` 200 [50, 2000]; `RAM_GUARD_GRACE_MS` 250 [0, 2000]; `RAM_GUARD_PSS_CONFIRM_DEADLINE_MS` 250 [50, 2000]. Depth/node caps are module constants, not knobs. **Runtime injection**: `ceiling_bytes` is a parameter of the guard's `register()` (the SpawnRecord already carries it); the config value is the default. **SECURITY RULING: the per-call override is an internal/test seam only — the LLM-facing bash/proc tool schema gains NO ceiling parameter** (an agent-visible knob would let the agent raise its own ceiling; cf. W3's registration-gated authority). | `RESOURCE_MONITOR_*` (couples a 200 ms guard to a 30 s monitor + its yaml trap); agent-visible per-call ceiling (containment bypass) | plan lean + E-anchors + W3 |
| **DQ8** — observability | `EventKind.RAM_LIMIT_EXCEEDED = "ram_limit_exceeded"` (**same string as the envelope `code`** — both are machine-facing verdict records of the same act; `exit_kind` stays a separate process-outcome taxonomy). Payload = the envelope key set + the verdict extras (`metric`, `trigger_metric`, `confirmed_pss_bytes`, `under_report_bound_bytes`, `members_total/killable/unreachable`) — **producer-side allow-list enforced** (the bus persists `data` unsanitized, `event_bus.py:176-183`). Exactly **one event per breach** (driven by the `WATCHING→KILLED` transition), never per tick; `instance_id=None` spawns ⇒ counter + log only. Counters (RAM-only, `SamplerCounters` shape): `ram_kills`, `ram_breaches_detected`, `ram_kill_refusals` per-predicate, `ownership_unverified`, `tree_incomplete`. Log prefix **`ram-guard:`** (matches `resource-monitor:`). Read surface: **new additive `GET /api/resources/ram-guard`** carrying the counters + the health heartbeat (`enabled, thread_alive, last_tick_at, missed_samples, ticks, registry_depth`) — **not** in `/livez`/`readyz` (an observability death must not gate traffic); `/control-capabilities` untouched (fence-pinned). | riding `/self-cost` (the monitor's own contract); healthz coupling | W4 |
| **DQ9** — default-ON vs T4 | **Confirmed: P1 lives entirely outside the T4 seam.** No read/write of `resource_limit_policy`; dormant columns untouched (both grep fences stay green unmodified); `/control-capabilities` byte-identical. Operator mental model: the guard is a **safety net** (default-ON, in-process, config-driven — like the unit cap), T4 is a **future policy layer** (user-gated, `enabled=false`, no writer). The env-off switch is the v1 disable; a per-policy disable belongs to the future T4 ceremony, not v1. | any T4 read path in v1 | W3, W4, W5 consumer #20 |
| **DQ10** — crash durability | **(a) in-process + the DQ8 event as the durable forensic record.** No migration lands in v1 **at all**. Rationale: the command died with the daemon anyway; the event answers "why did my command die" across restarts (one events query joins `event.pgid` ↔ the row's eventual `gc` close); events retain ~24 h (`EventRepository.cleanup_old`), sufficient for operator response windows and stated in the runbook. Rollback is trivial (remove the lifespan block + lane seams; zero schema residue). | durable verdicts (redundant with the event; a proc write path; and closing the row disarms the escapee net — see DQ5) | W4 |

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

### 3.3 Shipped default: **AMENDED — ship 3 GiB (3 072 MiB = 3 221 225 472 bytes), not the commissioned 8 GiB**

The commissioned 8 GiB is **unshippable on this host**: at fire time box demand ≈ 4.79 residents + 8.0 = 12.79 GiB > 11.67 physical; the kill completes at 13.8–14.8 GiB; empirically it **fires at-or-after daemon death in both Oct-30 incidents** (fast case: tree ≈ 4.0–4.3 GiB at death; slow case ≈ 8 RAM + 7.4 swap). It is over-budget by 1.17–2.52 GiB even at zero safety margin, and it is unshippable *with* a backstop too (needs `X ≥ 13 GiB`). 8 GiB becomes correct on a ≥ ~14.5 GiB host.

Council disagreement surfaced and resolved: `agentic` → 3 GiB (empirical pre-emption: C ≤ fast-case-tree-at-death − overshoot ≈ 4.3 − overshoot); `coding` → 4 GiB (box-survival arithmetic: C ≤ 4.48–5.18). **Resolution: 3 GiB**, because the binding constraint is the **intersection** and agentic's is tighter and incident-anchored: 4 GiB passes box-survival but **fails empirical pre-emption once overshoot is added** (4 + 0.7 = 4.7 > 4.3). 3 GiB passes both under every (latency + grace) on the table (3 + 1.0 = 4.0 ≤ 4.3). False-kill cost of 3 vs 4 ≈ 0 on measured evidence (max legitimate child footprint **364.5 MiB** over 21 days — both ceilings are ≥ 8× above it).

**Raise-path (arithmetic, never taste):** if the ladder lands ≤ 0.3 s total (it did: 0.45 s) *or* measured legitimate workloads need > 3 GiB, raise to 4 GiB by the same formula.

**Coupling to DQ4 (hard precondition):** every second of (poll + grace) at 1 GiB/s costs 1 GiB of ceiling. With the inherited 5 s grace, C_max collapses to **0.68 GiB**. With the ruled ladder (200 ms + 250 ms = 0.45 s), C_max ≈ **3.85 GiB** ⇒ 3 GiB ships with margin. **The short-grace ladder is a precondition of the ceiling, not a nicety.**

### 3.4 Operator routing (OPTIONS + RECOMMENDATION — nothing executed by this lane)

- **Artifact:** one-page options memo at `docs/runbooks/ram-guard-and-unit-bounds.md` (P7 deliverable): measured state, both journal OOM deaths, the orphan finding (E8), the interaction table, the drop-in, verify/rollback, the §3.5 honesty set, plus a one-line supersession note on `resource-cost-control.md` §8.1's never-applied 7G/8G figures.
- **Vehicle:** drop-in `/etc/systemd/system/ensemble-main.service.d/30-memory-bounds.conf` (next free slot; **not** `10-*`; verified to survive `adopt-unit.sh` regeneration). Disclose: `MemoryHigh`/`MemoryMax` apply only on restart — a **restart-class change**.
- **Interaction table** (C = 3 GiB default; D_max 3.0–3.5; O 0.7–1.0; M_c 0.5; others 1.79; M_k 1.0 ⇒ X ∈ [8.0, 8.88]):

| Option | MemoryHigh / MemoryMax | Verdict |
|---|---|---|
| **RECOMMENDED** | **7G / 8700M (8.50 GiB)** | unit slack 0.5 over the floor; box margin 0.38 under 8.88; the guard always completes before a unit kill; 7→8.5 throttle band gives reclaim pressure + runway |
| Conservative | 7G / 8G | valid, zero slack for daemon drift (phase8's numbers, re-purposed) |
| agentic's row | 7G / 9G | rejected at 9G (9 + 1.79 + 1.0 = 11.79 > 11.672) — recorded as the surfaced disagreement |
| Throttle-only | 8G / unset | rejected as primary: does **not** bound the orphan class |
| Any X ≥ 12G | — | invalid: the kernel OOMs the box before the cgroup cap fires |
| C = 8 GiB with any cap | — | invalid on this host (needs X ≥ 13) |

- **Also rejected:** `OOMScoreAdjust=-500` / `MemoryMin` (moves the outage onto PG/demo/OD co-residents). **Leave `MemorySwapMax` unset** — swap demonstrably buys the guard time on slow leakers (7.4 GiB absorbed at 11:13Z).
- **Verify/rollback:** canary first via `systemctl set-property --runtime ensemble-main.service MemoryHigh=7G MemoryMax=8700M` (live, self-reverting); persist = drop-in + `daemon-reload` + restart at a maintenance window; verify `systemctl show … -p MemoryMax -p MemoryHigh -p NRestarts` (NRestarts stays 0), `memory.events` counters stay 0, journal grep for `memory-limit|oom-kill` empty; rollback = remove drop-in + `daemon-reload` + restart (symmetric, no residue).

### 3.5 Honesty minimum (runbook + envelope must carry; council union set)

**Envelope:** configured ceiling bytes (in the metric's unit); observed peak as a **sampled lower bound**, explicitly labelled, with the bound formula; kill outcome incl. tree completeness; the metric's *defined* name + shared-page policy; runbook pointer. **Must not claim:** box protected, memory reclaimed instantly, other processes unaffected, or that the killed command was "the culprit" (a numeric kill may hit a legitimate heavy command).

**Runbook:** (1) overshoot law `≈ rate × (poll + grace)` with shipped numbers (~0.45 GiB at 1 GiB/s); (2) setsid escapees are *measured* (descendant closure) but **not killable** by the group kill — the reaper's stamp sweep is the bash-lane backstop, and the proc lane has **no** such net; (3) **orphans from previous daemon incarnations are not covered — only the unit cap covers that class** (proven Oct-30); (4) per-tree scope: aggregate exposure = Σ live ceilings + daemon band; (5) a killed legitimate heavy command is a real cost — raise by arithmetic, never intuition; (6) tree-RSS/PSS ≠ cgroup accounting — never cross-compare with `memory.current`; (7) no unit cap exists by default (present tense + verify command + REGENERATED-unit note); (8) **swap is not a safety net** — the daemon died with 203 MiB swap used *and* with 7.4 GiB used; swapped-out pages are invisible to an RSS metric (state the scope); (9) watchdog liveness is load-bearing — dedicated thread, never a coroutine.

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
def read_tree_rss(proc_root: Path, member_pids) -> TreeReading     # trigger metric
def read_tree_pss(proc_root: Path, member_pids) -> TreeReading     # confirm metric (kill path only)
def exceeds(total_bytes:int, ceiling_bytes:int) -> bool            # total >= ceiling (inclusive)
```

- **Pure + injectable `proc_root`; no host `/proc` reads in unit tests.** No import of `proc_readers` (self-only PSS reader, kB convention) and **no import of `bash_spawn_reaper`** — the stat parse (`rfind(")")`; field 5 = pgrp, 22 = starttime, 24 = rss_pages — verified) is reimplemented injectably, preserving the zombie/kernel-thread exclusions.
- **Units:** `/proc` reports kB (KiB); the **only** `× 1024` lives in the parse helpers; everything downstream sees **bytes**. Config is MiB; `× 1 048 576` once at config→`SpawnRecord`.
- **Member states:** READABLE → value; **ZERO** (status OK, no `VmRSS:` line — empirically zombies/kernel threads) → 0, still complete; **GONE** (ENOENT) → 0, still complete; **UNREADABLE** (read error / unparseable) → 0, `complete=False`, pid recorded.
- **Breach semantics:** `total >= ceiling` (inclusive); **the trigger fires on the CURRENT sample, not the peak** — a spike released before the next sample is *reported* (peak) but never killed. An **incomplete** reading that still `>= ceiling` **proves** a breach (a lower bound above the ceiling); an incomplete reading below the ceiling is *no evidence* — never "no breach".
- **`tree_complete`** = scan completed ∧ closure not truncated ∧ no UNREADABLE member. It does **not** mean "every member is killable" — that is `members_total / members_killable / members_unreachable` (escapees are *measured* but not group-killable).

### 4.2 Kill-ladder contract (`ram_guard/ladder.py`)

```
ram_kill_ladder(pgid, leader_start_time, grace_seconds) -> (status, reason)
status ∈ {killed, already-dead, refused}
reason  ∈ {killed:term-esrch, killed:group-dead-after-grace, killed:ladder,
           already-dead:group-empty, refused:pgid-invalid, refused:pgid-self,
           refused:daemon-group, refused:pgid-recycled, refused:platform-win32}
```
TERM → slice-probed grace → KILL; KILL always reachable; idempotent (safe to repeat); called **on the guard thread** in both lanes; ownership gate = captured pgid + leader starttime re-verification (fail-closed), environ check secondary. **Mandatory pre-kill predicates** (a kill fires iff ALL evaluate true; "unevaluable" ≠ true): P0 switch+Linux+registered · P1 pgid from this guard's registry · P2 `pgid > 1` · P3 `pgid != os.getpgid(os.getpid())` (**the daemon's group, not its pid**) · P4 `pid != os.getpid() ∧ pgid != os.getpid()` · P5 leader `starttime` match (unreadable ⇒ fail-closed ⇒ treat as exited) · P6 ownership stamp (mismatch ⇒ abort; unreadable ⇒ proceed + `ownership_unverified++`) · P7 group not dead · P8 post-kill probe ⇒ `tree_complete`.

### 4.3 Typed-error contract (`ram_guard/errors.py`)

- **Bash:** the envelope **is** the entire return string. **Proc:** a `ram_breach:` line + flat `ram_killed: true` inside `proc_status` (added, never replacing).
- Key set (additive-evolution; house 3-key prefix byte-identical; numerics **before** `message`): `ok, code, surface, limit_bytes, observed_peak_bytes, peak_is_lower_bound, poll_interval_ms, metric, trigger_metric, confirmed_pss_bytes, under_report_bound_bytes, pid, pgid, tree_complete, members_total, members_killable, members_unreachable, grace_ms, killed_at, ram_breach_observed, coincident_timeout, timeout_seconds, message`.
- **Negative space (forbidden, with rationale):** command/argv in any form incl. hashes (argv carries secrets; a hash is a brute-force oracle; the sanitized form still leaks the `--flag value` residual) · env keys or values (a live child's `/proc/<pid>/environ` holds **plaintext resolved secrets**, `secret_resolver.py:300-397`) · stdout/stderr · secret ids · per-member pid lists / RSS breakdowns · host paths/hostnames/workdir · exception reprs. `message` = **fixed text**, numeric interpolation only.
- **Message content (both templates):** the ceiling and observed peak with human units; `observed_peak_bytes` labelled a **sampled lower bound** of the named metric (+ shared-page policy); the statement that the **daemon's RAM guard** (not the kernel OOM killer) issued the kill; tree completeness; a recovery hint (a kill can land mid-write — re-run idempotently).
- **Precedence (bash):** the RAM arm sits between `bash.py:1088` and `:1110` and preempts, in order: the success arm's `mark_exit` (**one-shot** — `pid_registry.py:423` deletes the live row; if the success arm fires first the RAM verdict is *permanently erased*), the `EXIT CODE:` line (`:1155`), the 150 k truncation arm (`:1163-1171`), and the timeout arm (`:1159`). It also performs the registry unregister the success arm does (`:1128-1136`). The secret envelope (`:1215-1222`) is **mutually exclusive** with the RAM envelope (secret resolution raises before spawn ⇒ no child ⇒ no measurement).
- **Coincidence:** claim-token, first-writer-wins. Timeout wins ⇒ `code="timeout"` **plus** `ram_breach_observed:true` + the peak. Guard wins ⇒ `code="ram_limit_exceeded"` plus `coincident_timeout:true`.

### 4.4 The verdict record (`SpawnRecord`, RAM-only, keyed by `tracking_id`)

`tracking_id · instance_id · lane · pid · pgid · start_time_jiffies · ceiling_bytes · poll_interval_ms · grace_ms · state (WATCHING→BREACHED→OVERCOUNTED→KILLED→RELEASED, first-writer-wins) · observed_peak_bytes (monotonic) · last_total_bytes · samples · metric · trigger_metric · peak_is_lower_bound · under_report_bound_bytes · confirmed_pss_bytes · tree_complete · members_total/killable/unreachable · unreadable_pids · gone_pids · enumeration_failures · breach_at · killed_at · last_sample_at · trigger (rss_threshold | pss_threshold | pss_confirm_unavailable) · rearm_threshold_bytes`.

**Honest-peak bound (frozen):** per-sample under-report ≤ `R·Δt`, **computable from the samples themselves** as `max positive sample-to-sample delta` (`under_report_bound_bytes`); total exposure breach→all-dead = `R·(Δt + grace)`. Both appear in the envelope.

---

## 5. Failure-path rulings (Phase 6 must test every row)

| Failure | Ruling |
|---|---|
| **Watchdog thread dies** | No supervisor thread in v1 (two failure modes; house accepts none for wedge-capture). Tick body `try/except Exception` → counter, **never log**; outer `while not stop` restart loop. `last_tick_at` heartbeat + `missed_samples` + `thread_alive` exposed on `GET /api/resources/ram-guard`. The runbook states the unit cap is the only backstop if the guard is dead. |
| **Watchdog blocked** | Structural invariants (not discipline): no logger call on the tick path; locks only around µs-scale map mutations, never across a /proc read / signal / sleep; no PSS on the tick path; verdict writes RAM-only. |
| **Registration leak** | `finally`-shaped release on **all six** bash exit paths (success, timeout, RAM, cancel, generic-exception, spawn-failure) + a **bounded non-lethal liveness sweep** every ~25 ticks: dead pid ⇒ drop the entry; live-but-unreleased ⇒ **keep watching** (the safe direction — the runaway stays guarded). |
| **Pre-existing leak (disclosed, not fixed here)** | The bash generic-exception arm (`bash.py:1223-1224`) releases **neither** registry and does **not** kill the child — and `MemoryError` lands exactly there during a RAM incident. Phase 3 must add the *guard* release there; the pre-existing `BashProcessRegistry`/`pid_registry` leak on that arm is routed as a follow-up. |
| **Double-kill race** (reaper / bash timeout arm / proc timeout task) | **Claim token** under the registry lock, taken at observation (guard) or elapsation (timeout); the ladder runs outside the lock. First claimant owns the verdict; losers skip their signal and read the verdict; the loser is recorded `coincident`. Exit code `-9` is ambiguous by construction (guard KILL ≡ kernel OOM ≡ user kill ≡ `proc_stop(force)`) — **the verdict record is the only discriminator**. |
| **Peak under-report** | Sampled lower bound, bounded by §4.4; labelled in the envelope. |
| **Degradation matrix** | non-Linux ⇒ INERT; `/proc` unreadable ⇒ no measurement, no kill, one WARN/spawn, `missed_samples++`; enumeration failure ⇒ drop the sample; daemon group / `pgid≤1` / self ⇒ REFUSE; identity mismatch ⇒ REFUSE (`refused:pgid-recycled`); leader absent ⇒ `already-dead`; group empty ⇒ close, no signal. |

---

## 6. Consumer-gate evidence (the one-time gate artifact — DQ6)

22 consumers verified; **zero break**. Load-bearing: (1) the feared `test_chart_tools_legacy_error_contract.py` pins chart tools only — inapplicable; (2) no daemon-side `json.loads` consumes a bash/proc return string — the only machine consumer is the agent/LLM; (3) the real pin surface is `proc_status` substring assertions (`tests/tools/test_proc_tools.py:240, 257, 382, 1163, 1166-1167, 1831`) and `ProcessInfo.status == "killed"` attribute pins — satisfied by *adding* lines and keeping `status="killed"`; (4) `pid_registry.VALID_EXIT_KINDS` **must** be extended with the named constant or the RAM arm raises inside `mark_exit`; (5) four strings must never change: bash success `"STDOUT:\n…\n\nEXIT CODE: N"`, bash timeout `"ERROR: Command timed out after N seconds"` (byte-identical when no breach was observed), the `proc_status` block, the `proc_list` header.

---

## 7. Module decomposition + thread lifecycle

```
daemon/services/ram_guard/
  __init__.py     facade: get_ram_guard(), register/read_verdict/release
  tree_reader.py  scan_proc / resolve_members / read_tree_rss / read_tree_pss / exceeds   (pure, injectable)
  verdict.py      SpawnRecord, PeakRecord, breach predicate, OVERCOUNTED re-arm
  ladder.py       ram_kill_ladder + the P0–P8 pre-kill predicates
  guard.py        the thread + registry + GuardCounters + liveness sweep
  errors.py       format_ram_envelope (one producer, two surfaces)
```
Import direction: `tools/* → ram_guard` only; `ram_guard → stdlib` only (**no** `tools/*`, **no** `resource_monitor`, **no** repositories, and — superseding W4's "one sanctioned import" on W1-r2's evidence — **no** `bash_spawn_reaper`; the stat parse is reimplemented injectably because `list_group_members` hardcodes `/proc` and is per-spawn-priced). Thread lifecycle: **FastAPI lifespan** (`app.state.ram_guard`; own `try/except` + INFO; OFF ⇒ no thread, `app.state.ram_guard = None`; shutdown `.stop()` + join). Bash seams (verified): S1 `:952-965`, S2 `:1062-1065`, S3 `:1071` + `:1174-1214` (+ new RAM arm before `:1110`), S4 `:1143-1160`, S5 `:965/:483/:1073/:1133/:1196` (+ the generic arm `:1223`), S6 `:1110-1123` (note: `peak_rss_kb` is **KB** — one explicit bytes→KB conversion at this boundary or the two numbers disagree by 1024×). Proc seams (verified): S7 `:1231/:1235/:1241-1245` (4th task), cleanup tuples `:1296-1301` + `cleanup_instance`/`cleanup_all`, S10 `:786/:855-875` (`ram_killed` joins `timed_out`/`user_stopped` as the third authoritative input), S11 `:966-1086` (environ check demoted to secondary).

---

## 8. Sequencing verdict (7 phases — keep the shape, five amendments)

1. **Phase 2 absorbs the envelope helper + the consumer gate.** `errors.py` and the frozen envelope land with the substrate so Phases 3/4 build against them; the consumer-gate artifact is §6 of this document (reference it — do not re-derive). Phase 2 also adds the **per-tick cost measurement** and a **realistic allocation-rate measurement** (R is assumed ∈ [1, 3] GiB/s today; the grace default and the ceiling arithmetic depend on it).
2. **Phase 3 (bash first — the Oct-30 ballooners were bash `python3` commands) must additionally:** respect the **one-shot `mark_exit` ordering** (§4.3); add the guard release to the generic-exception arm; verify the `pgid = proc.pid` fallback invariant (`:955-957`) is test-pinned; perform the single bytes→KB conversion at S6.
3. **Phase 4 (proc) is COMMISSION-MANDATORY and unblocked** — strictly *less* coexistence risk (no durable row ⇒ the escapee-net question is bash-only). It must additionally: **capture pgid + start_time at spawn** (fixing P5 — the proc lane re-derives pgid at kill time today), add `ram_killed: bool` (surfaced **positively** in `proc_status` — `user_stopped` is never surfaced today, a real observability gap), and add `user_stopped: true` to `proc_status` while there (🟢 one line).
4. **Phase 5 shrinks** to the config class + observability surfaces + fence re-run (the envelope moved to Phase 2).
5. **Phase 7 shrinks and re-weights**: no migration at all; its primary deliverable becomes the **operator options memo** (§3.4) + the honesty set (§3.5) + follow-up routing. It can run in parallel with 3/4.

**Phase 6 additions:** dead-guard / blocked-tick cases (the plan's S1–S10 cover the substrate but have no "the thread died" case); the OVERCOUNTED re-arm path; the one-shot `mark_exit` ordering; the PSS-confirm fail-open path; mutation-proof per the existing house rule. **Test-plan amendment:** Success Criterion 3's `observed_peak_bytes >= limit_bytes` must become "**the deciding value** (`confirmed_pss_bytes`, or `observed_peak_bytes` on a fail-open trigger) `>= limit_bytes`" — under the hybrid the RSS peak and the deciding PSS value are deliberately distinct quantities.

---

## 9. Risks (aggregated, severity-ordered)

- 🔴 **The guard's own death is silent and fatal** (no unit cap exists; a dead guard ⇒ kernel OOM takes the daemon ⇒ Oct-30 repeats). Mitigation is *external* (the operator drop-in) + the health endpoint. Highest-severity operational item.
- 🔴 **The orphan class is out of reach** — orphans of dead incarnations are covered only by the unit cap (E8). The guard must never be described as protecting the box from that class.
- 🔴 **The PSS confirm is the only fairness gate** — made advisory, the guard false-kills fork-heavy legitimate jobs at factor ≈N; deadline set too tight, fail-open silently degrades it to pure-RSS.
- 🔴 **`mark_exit` is one-shot** — a wrong arm ordering permanently erases the RAM verdict.
- 🟡 PSS under-counts pages shared with processes *outside* the tree (direction: fewer kills; state in the runbook). Shared scan is O(host pids) per tick (9.49 ms @250 ⇒ ~40 ms @1000 — size the interval against the *worst* host pid count). `smaps_rollup` linearity beyond 725 MiB is inferred, not measured. Partial output is discarded on a RAM kill (accepted; stated in the message's recovery hint). Event-only durability loses the verdict if the daemon dies between kill and lane observation (the case DQ10(a) already accepts). The coincidence case changes the legacy bash timeout string when a breach was also observed (no test pins it; record it).
- 🟢 Naming collision: `bash.py:522` "RAM tier" means the RAM-resident registry bucket — the new concept must not reuse the phrase.

---

## 10. Decisions pending (for the dispatcher / user — I could not close these within my mandate)

1. **The 3 GiB default amends the commissioned 8 GiB.** The commission's words say 8 GB; the measured host cannot ship it (§3.3). I have ruled 3 GiB as the binding default **with an explicit raise-path**. If the user insists on 8 GiB, the honest consequence is: the feature ships as a tripwire that fires at-or-after daemon death on this host — i.e. cosmetic until the host is resized to ≥ ~14.5 GiB. *Under "keep 8": ship the knob at 8 GiB default and the memo states the host must grow first. Under "accept 3": nothing further.*
2. **The unit-cap drop-in is a user action** (`7G / 8700M` recommended; §3.4). This lane does not execute it. Without it, the guard is the *only* enforcement on the box and the orphan class is uncovered.
3. **Pre-freeze check (Phase 2 entry):** attribute today's unexplained **8.11 GiB unit peak** — if it was one command tree, 3 GiB catches it; if it was daemon page cache, it does not, and the D_max term in the interaction table shifts.

---

## 11. Gaps

- **W1 wedge:** the original data-flow worker (`ffc1b0ef`) wedged after 12 tool calls (1 h 53 m silent) and was terminated; the single permitted replacement (`2ead7080`) delivered the full assignment with measurements. Anomaly logged (`report_id=1d95b83b`). No coverage gap remains — DQ1/DQ3/reader-contract coverage is complete from the replacement's report.
- **Council quorum:** 2 of a possible 4 councilors (`agentic`, `coding`) — normal quorum, full-confidence synthesis; the models list was capped at 2 by the convening call.
- **Not verified by anyone:** `smaps_rollup` exposure for every pid beyond those probed; multi-TiB sparse-mapping cost; macOS behavior (the reader contract is Linux-only by design — Darwin stays inert per plan Assumption 1); executed test runs (all "no net-new reds" claims are argued from source, not run — Phase 6's job).

---

## 12. Plan reconciliation (applied to `plan-overview.md`)

A compact **"Phase 1 Rulings (architect-adjudicated, binding)"** block was inserted after DQ10 and before "## Critical Interaction", recording: the 10 rulings one-line each, the R1 resolution (absolute / guard-inner / 3 GiB / unit-cap routing), the trigger-on-current-sample amendment, the PSS-confirm mandate, and pointers to this document. No other plan text was altered — the plan council reconciles the phase files against this document.

---

## 13. Addendum — leader rulings (council cycle 1, appended by the plan writer)

Two rulings were returned by the **leader** after this document was completed. They
are **binding** and are recorded here so this document does not contradict itself.

### 13.1 Q1 — proc lane shape (LEADER-CONFIRMED, supersedes §7's "(4th task)" parenthetical)

> **LEADER RULING Q1 — CONFIRMED: reading (a). NO 4th task.**
> The proc lane is **register with the guard thread → status mapping → release**.
> The §7 seam list's "(4th task)" parenthetical is **superseded**: there is no
> asyncio watchdog task in the proc lane. The guard thread (DQ2) owns the
> measurement, the breach decision, the kill (through `ram_kill_ladder`), and the
> verdict record. The proc lane creates **exactly three** tasks — reader, exit,
> timeout — exactly as today. A **NEW pgid-capture task** (capture pgid + start_time
> at spawn, fixing the §1.1 P5 TOCTOU) is added per §8.3.

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

### 13.3 Scope note

Neither ruling alters §4.1 (the reader contract), §4.3 (the typed-error contract),
§4.4 (the verdict record), DQ1, DQ3, DQ5, DQ6, DQ7, DQ8, DQ9, DQ10, or the R1
resolution. Both are consumed by the phase docs' **rev 2** (`phase2-plan.md`,
`phase4-plan.md`, `phase3-plan.md`, `phase5-plan.md`, `phase6-plan.md`,
`phase7-plan.md`, `test-plan.md`, `plan-overview.md`).
