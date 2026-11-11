# Plan Overview: RAM-Based Auto-Termination for Agent-Spawned Shell Processes

Date: 2026-11-10 (planned from worktree base `0efdf106f3f66ccb400405f4137694592b72097d`, branch `feature/ram-auto-termination`, worktree `/home/nea/ensemble-src-wt-ram-auto-termination`)
Author: plan-creation worker (research by three partitioned explorers; all anchors re-verified against the worktree by this worker)
Status: **Phase 1 gate CLOSED** — architect adjudication COMPLETE (10/10 DQ rulings + R1 + sequencing verdict), **AMENDED by approver-iter-001** (7 blockers + sweep ruled in §14–§15) and by the **escalation pass** (iter-003 blockers + the two **USER RULINGS**; the default ceiling is now **DYNAMIC** — 70 % of detected effective total RAM, §4.7). Binding rulings live in the "Phase 1 Rulings" block below and in `architecture-recommendation.md`; the phase docs are rev 5 and reconcile against them. **The default ceiling is now DYNAMIC** (70 % of detected effective total RAM, USER RULING #2 — §4.7); the fixed 3 GiB figure is **demoted to operator guidance**.

> **rev 5 — supersedes rev 4 (commit 0ce1bc8c8) per the escalation-pass (iter-003
> rulings + the two USER RULINGS).** Vehicle: **binding per-file edit list** (binding-order headers below).
> Where this document and `architecture-recommendation.md` disagree, **that document
> wins**. The architect's "Phase 1 Rulings" block is preserved **verbatim**.

## Applied edit list — rev 3 (binding order)

| Order | Edit | Blocker / source |
|---|---|---|
| 1 | Header: the phase docs are **rev 3**; the amendment is named | approver-iter-001 |
| 2 | **The §6 consumer-count claim is WITHDRAWN from the plan set** (5 sites) → the **§6 enumerated census** (the enumerated census — counts derived at fold time, never pinned; the table is the artifact) | DQ6 sweep |
| 3 | **DQ1 one-liner**: the re-arm clause → the **bounded** formula (§4.1a) | Blocker 2 |
| 4 | **DQ4 one-liner**: + the Q2=(i) semantics + the **16-code** vocabulary + the five canonical predicate strings | Blockers 1 & 6 |
| 5 | **DQ6 one-liner**: the census replaces the "22" claim; the artifact is owned by **Phase 2** | DQ6 sweep |
| 6 | **DQ8 one-liner**: the payload wording, the **exact counter set**, the `instance_id=None` rule | Blocker 6 |
| 7 | **DQ10 one-liner**: + the **event ownership** (§4.5) + the SQLite-CHECK qualifier | Blocker 3 |
| 8 | **Coupling Map**: the two replaced cells (§14.2) — P1↔P5 and P2↔P5 | §14.2 |
| 9 | **Phases table**: the P1 task-count ledger (10 → **12**); the P2/P5/P6/P7 roles and task counts updated for rev 3 | Sweep + rev 3 |
| 10 | **Critical Interaction** → the **§3.4 re-done** numbers (bounded frame, `F_max`, the rate-conditional truth) | Blocker 2 |
| 11 | **Risks**: + the guard-vs-cap race (🔴), + the bounded re-arm trade (🟡); Risk 7 re-aimed at the census | §9 |
| 12 | **Success Criteria**: + the bounded re-arm, the publication ordering, the config wiring, the event drop, the boot-abort rung | Blockers 2, 3, 4, 5 |
| 13 | **Configuration Surface**: + the **root-`Config` wiring** (§4.6) + the re-arm constants as **module constants** | Blocker 5 |
| 14 | **Assumptions**: updated for the bounded frame, the query-only proc verdict, and the event-drop posture | §14.2 |
| 15 | The **B10/B11 honesty + truth-statement pointers** and the **B14 coupling strings** land verbatim | §15 |

## Applied edit list — rev 4 (binding order, approver iteration 002)

| Order | Edit | Blocker / source |
|---|---|---|
| 1 | Header: the phase docs are **rev 4**; the amendment is named | iter-002 |
| 2 | **Vocabulary count 16 everywhere** — the superseded count was an arithmetic slip (9 kept/renamed + 7 added); the **B4 count-line** lands; **zero** survivors of the superseded count anywhere in the plan set | Blocker 1 |
| 3 | **DQ2 → the two-thread ruling** (B16 verbatim) in the scope item, the one-liner and the closure ledger | Blocker 3 |
| 4 | **Both coupling-map P2↔P5 cells** → B14's **renumber-proofed** string (the surviving `independent` cell replaced) | Blocker 5 |
| 5 | **§3.4 N = 2 contended row** + the contended overshoot frame + the amended truth statement (with the ≈0.28 GiB/s contended clause) | Blocker 3 |
| 6 | **Risks**: + the wedged execution-lane thread (🔴), + the N = 2 contended window (🔴) | §9 |
| 7 | **SC19** re-aimed at 16 codes (+ the count-line) · **SC21** (lane heartbeat) | Blockers 1 & 3 |
| 8 | **Assumption 14** — the `instance_id=None` narrowing (§16.3) | Notes sweep |

## Applied edit list — rev 5 (binding order, escalation pass: iter-003 + the USER RULINGS)

| Order | Edit | Blocker / source |
|---|---|---|
| 1 | Header: the phase docs are **rev 5**; the default is **DYNAMIC** | USER RULING #2 |
| 2 | **Objective + Scope**: the dynamic default; the **in-flight** scope of the typed error (A3, §17.3) | USER RULING #2, A3 |
| 3 | **Config surface**: `ceiling_mib: Optional[int]` (B8), the escalation-pass assertion set (B9), the message clause "resolved" (B10), the **eleven-field** heartbeat (B23), the dynamic-default contract (B24) | USER RULING #2 |
| 4 | **Critical Interaction** re-derived: the dynamic default, the general bound `T ≥ 26.5 + 2.33·R GiB`, the memo guidance (no-cap / lower-the-knob / resize), the retained user-accepted disclosure | §3.3a, §3.4 |
| 5 | **Risks**: + the derived-default risk (🔴, user-accepted); the stale default claims re-aimed | §9, §10 |
| 6 | **SCs**: SC3 (in-flight), SC7 (dynamic + OFF-no-abort), SC8 (C3 zombie re-aim), SC13 (**fifteen** items) | A3, C3, C4 |
| 7 | **Assumption 3** → the dynamic default; + the detection-failure fallback | USER RULING #2 |
| 8 | **Key sets**: `reason` added after `surface` in the envelope (B2) and the event payload (B3) | A1 |
| 9 | **E11 anchor sweep** — `bash.py:1156/:1160`, `proc_tools.py:1310`, `bash_spawn_reaper.py:859-866`, `bash.py:952-1006` | E11 |
| 10 | **C3** — the mid-string bold removed from predicate #1 (SC20-grep-safe) | C3 |
| 11 | The falsified EventKind variant (the earlier member-count / CHECK-count / column-line figures) is **withdrawn** — it appears in **no** pin cell | C13 |

## Applied edit list (binding order)

| Order | Edit | Class |
|---|---|---|
| 1 | Header: **gate closed** (was "blocked at Phase 1") | Class 3 |
| 2 | Objective + Scope: the shipped default is **3 GiB / 3 072 MiB**, not the commissioned 8 GiB (leader-accepted amendment) — **SUPERSEDED at the escalation pass**: the default is now **DYNAMIC** (USER RULING #2) | Leader ruling → superseded |
| 3 | Routed follow-up 1: the unit-cap premise is **falsified** — **no cap exists today**; the recommendation is **7G / 8700M** (§3.4) | Class 3 |
| 4 | Phases table: P2 **absorbs the envelope helper + the consumer gate**; P5 **shrinks**; P7 **shrinks and re-weights** to the operator memo | Class 3 |
| 5 | Risk 1: rewritten against the **falsified** unit-cap premise | Class 3 |
| 6 | Risk 5: the **ownership-gate claim is falsified** (P3/P4) — the reaper's fail-closed 5-rule gate is not `proc_tools`' fail-open 1-signal check, and the bash lane has **no** ownership gate | Class 3 |
| 7 | Risk 7: the DQ6 gate is **arch doc §6's enumerated census** (the §6 consumer-count figure is **withdrawn**), not the chart-tools pack | Class 3 |
| 8 | Risk 8 + SC7: the 3 GiB default — **SUPERSEDED at the escalation pass** (the default is now dynamic) | Leader ruling → superseded |
| 9 | **SC3 re-aimed** at the **deciding value** | Class 2 |
| 10 | SC13 re-aimed at the §3.4 reality | Class 3 |
| 11 | Critical Interaction: rewritten as **RESOLVED** (absolute bytes / guard-inner / unit-cap-outer / 3 GiB) | Class 3 |
| 12 | Assumptions 3 + 7 re-aimed | Leader ruling |
| 13 | Open Questions → **Resolved in Phase 1**, with the two items that remain genuinely open | Class 3 |
| 14 | Knob names named in the plan body: `ENSEMBLE_RAM_GUARD` + `RAM_GUARD_*` (`CEILING_MIB` 3072, `POLL_INTERVAL_MS` 200, `GRACE_MS` 250, `PSS_CONFIRM_DEADLINE_MS` 250) — **SUPERSEDED at the escalation pass** (`CEILING_MIB` is now `Optional[int]` ⇒ dynamic) | Class 3 → superseded |

---

## Objective

When a bash-tool or proc-tool execution's process tree exceeds a configurable RAM
ceiling — **default: DYNAMIC, 70 % of the detected effective total RAM** (USER
RULING #2, §4.7) — the daemon kills the offending process tree promptly and returns a
clear, honest, typed error to the calling agent naming the **deciding** observed
usage — preventing runaway agent commands from ballooning the host and OOM-killing
the daemon unit.

> **The default is now DYNAMIC (USER RULING #2).** `RAM_GUARD_CEILING_MIB` unset ⇒
> the ceiling is **70 % of the detected effective total RAM** (the daemon's cgroup
> limit when finite, else host `MemTotal`), **resolved once at boot**; an explicit
> value overrides and is validated `[64, 65536]` — **the clamp never applies to the
> derived value** (§4.7c). The previously-shipped fixed figure (3 GiB) is **demoted
> to operator guidance**; USER RULING #1's *intent* (disaster prevention, not a
> constraint on legitimate heavy work) is binding and is carried by the no-upper-clamp
> rule. On this host the derived default is **8,367 MiB (8.171 GiB)** (E11).
>
> **SCOPE (A3, leader-framed):** the typed `ram_limit_exceeded` error is guaranteed
> **only while the tool call is IN FLIGHT**. A breach that fires **after** the call
> has returned (the ordinary `cmd &` shape — the shell exits with `EXIT CODE: 0`
> while a surviving grandchild balloons) reaches **no caller**: the tool has already
> returned. Such post-return kills surface via the **durable event** and the **guard
> endpoint**, which the agent does not automatically see. There is **NO new
> agent-injection channel in v1** — adding one is a **routed follow-up** (Phase 7's
> queue). Disclosed as §3.5 item 14.

Testable completion sentence: an agent command whose process tree allocates past the
**resolved** ceiling is terminated (tree-wide, not just the direct child), the agent
receives a typed `ram_limit_exceeded` error whose **deciding value** clears the
ceiling **while the call is in flight**, and the daemon survives with no orphaned
descendants — while every command under the ceiling, and every lane with the
kill-switch OFF, behaves byte-identically to today.

**Enforcement scope is PER-COMMAND (process-tree scoped), NOT host-wide OOM management.**

### Motivating incidents (user-reported)

Systemd OOM restarts of the live daemon on 2026-10-30 at 10:43Z and 11:13Z, with
`python3` memory ballooners reaching 10–11 GB. Runaway agent commands are an
**observed crash class**, not a hypothetical.

### Commissioned error contract (user's words)

> "auto terminate the bash, proc command tool of our ensemble system if they use more
> than 8GB ram (sometime agent run wrong command and cost a lot of ram, cause resource
> leak and system unstable or crash)"

---

## Scope

### In Scope

1. **Measurement of the whole process tree** of each bash/proc execution (children
   included) against a configurable ceiling. **RULED (DQ1, hybrid):** a cheap
   `summed_rss_bytes` trigger per tick + a **mandatory one-shot `summed_pss_bytes`
   confirm** at breach (250 ms deadline, **fail-open ⇒ kill**). The confirm is the
   **deciding** value; pure RSS is falsified as a kill metric (measured **8.9×**
   over-count on a 9-member fork tree).
2. **A configurable ceiling** — **default DYNAMIC: 70 % of the detected effective
   total RAM** (USER RULING #2, §4.7); an explicit `RAM_GUARD_CEILING_MIB` overrides
   and is validated `[64, 65536]` (**the clamp applies to explicit values ONLY**;
   the derived value is floored at 64 MiB and **never upper-capped**). House-style
   knobs, with a **kill-switch default-ON** (`ENSEMBLE_RAM_GUARD`) and documented
   env-off (commission requirement; see DQ7/DQ9 and Dispatcher Ruling (c)).
3. **Fast-breach detection** — a poll interval chosen as an explicit
   latency/overhead trade-off (DQ2); **RULED: TWO dedicated daemon threads** (the
   tick thread and the execution-lane thread), poll **200 ms** [50, 2000].
4. **Breach handling**: terminate the process tree (escalation policy is DQ4), reap
   children so no zombies survive, and return a **typed error to the agent**
   carrying the **deciding** value and the honest-peak bound (contract shape is
   DQ6; **RULED**: the envelope **is** the bash return string; the proc verdict
   rides `proc_status` as a `ram_breach:` line + flat `ram_killed: true`).
5. **Observability** — a log line (prefix **`ram-guard:`**), a counter/metric in
   house idiom, and a durable event (`EventKind.RAM_LIMIT_EXCEEDED`); **no secrets
   and no argv** in any of them (§4.3 negative space).
6. **Tests** — real process trees that genuinely balloon, executed under a bounded
   harness with **small injected ceilings** so the test host is never OOM'd; under-limit
   commands unaffected; kill-switch OFF byte-identical; config ladder including
   validation-abort; negative paths.
7. **Agent-facing bash + proc command tools ONLY.**

### Out of Scope

| Excluded | Reason |
|---|---|
| MCP server processes | The commission names the bash + proc tools only. MCP servers are daemon-managed children with their own lifecycle; see Routed Follow-ups. |
| The daemon process itself | A daemon self-limit is a unit-level concern (`systemd` `MemoryMax`), not a per-command guard. The daemon is never a kill target of this feature. |
| Frontend / live-view | No UI surface in the commission. The typed error is an agent-facing contract, not an HTTP resource contract. |
| Host-wide OOM management (`systemd-oomd`, swap, `MemoryMax` tuning) | Explicitly a routed follow-up (below). This feature is a per-command guard, not a host policy. |
| MCP-server RAM limits | Routed follow-up (below). |
| Enforcement through the T4 `resource_limit_policy` table | **Fenced off by an existing test** — see "Fence That Constrains the Design" below. P1 is a config-driven enforcement port *outside* the T4 seam. |
| The `resource_pid_map` dormant columns (`action`/`action_by`/`acted_at`) | **Fenced off by an existing test** — see "Fence That Constrains the Design" below. |
| Rewriting the resource monitor's 30 s sampler or its `RESOURCE_MONITOR_*` config | Different subsystem, different cadence, different contract. The RAM guard is a separate fast-path service. |
| The `bash-tool-orphan-reaper` / `auto-kill-bg-processes` plans | Prior art, cited as boundary. Their decisions (event-driven cleanup, no perpetual polling, Tier-1/Tier-2 trees, instance-scoped not memory-scoped) are NOT re-decided here. |

### Routed Follow-ups (recorded, NOT expanded into this plan)

1. **Host-wide OOM posture** — **the live unit has NO memory cap at all**
   (`MemoryMax=infinity`, `MemoryHigh=infinity`; verified via `systemctl show` and
   by the absence of any `Memory*` directive in the unit or its drop-ins). The
   7G/8G figures this plan previously carried were a **never-applied runbook
   recommendation**, and the Oct-30 deaths were **kernel OOM kills**
   (`oom-kill`), not cgroup-cap kills. The recommended vehicle is now
   **`MemoryHigh=7G` / `MemoryMax=8700M` (8.50 GiB)** via a drop-in at
   `30-memory-bounds.conf` — operator-gated, restart-class, canary first. See
   `.agents/shared/planning/resource-cost-control/phase8-plan.md` §8.1 (the
   **never-applied** figures, superseded by this entry) and the Phase 7 operator
   memo `docs/runbooks/ram-guard-and-unit-bounds.md`.
2. **MCP server RAM limits** — the same guard applied to MCP stdio children
   (daemon-managed, different ownership, different kill authority).
3. **T4 ceremony path** — promoting this enforcement into the
   `resource_limit_policy` table (user-gated) once the durable port exists
   (cited adopted direction: `.agents/shared/planning/resource-cost-control/approach-comparison.md:75`).
4. **Dormant-column unfencing** — a deliberate, test-lifting change if
   `resource_pid_map.action`/`action_by`/`acted_at` are ever to carry the RAM-kill
   verdict (see Fence below).

### Fence That Constrains the Design (verified, non-obvious — read before choosing DQ5)

`tests/test_resource_control_seam.py` contains two **grep-based fences** that make
two otherwise-attractive DQ5 options **illegal** without a deliberate, test-lifting
change:

- `test_no_daemon_file_names_action_by_acted_at` (`:302`) — no `daemon/` file may
  name `action_by` / `acted_at` outside the model module.
- `test_no_resource_pid_map_string_names_action` (`:323`) — no `daemon/` file may
  combine the `resource_pid_map` string with the word `action`.

Consequence: **writing the RAM-kill verdict into `resource_pid_map`'s dormant
columns is fenced off today.** The guard's verdict substrate must be RAM-only
(a dedicated in-process registry) or a NEW table, unless the fence is consciously
lifted (which is itself a user-gated decision and belongs in a follow-up, not in
this commission). This is a hard constraint on DQ5, not a preference.

---

## Phases

| Phase | Name | Objective | Tasks | Coupling | Status |
|-------|------|-----------|-------|----------|--------|
| 1 | Design Decision Gate | Architect returns 10 binding rulings (DQ1–DQ10) + the metric contract + the kill ladder, before any code | 12 | gate — blocks Phases 2–7 | **CLOSED** — 10/10 ruled (arch doc) |
| 2 | Measurement Substrate | The reader (ONE shared scan/tick, injectable `proc_root`) + the **PSS confirm** (the deciding metric) + the **BOUNDED** OVERCOUNTED re-arm + the **envelope helper + the consumer-gate artifact** + the **event HOOK** + the guard thread + the kill ladder | 24 | tight with 3, 4, 5 | pending |
| 3 | Bash Lane Enforcement | bash executions breach → tree killed, the envelope returned, the **one-shot `mark_exit` ordering** AND the **verdict-publication ordering** honored, the durable row left **OPEN** | 16 | tight with 2, 5 | pending |
| 4 | Proc Lane Enforcement | proc executions breach → tree killed **by the guard thread** (**no 4th task** — leader ruling Q1), the verdict riding `proc_status`, consulted **before** `info.status` finalizes | 12 | tight with 2, 5 | pending |
| 5 | Config + Observability + Event Wiring | The `RAM_GUARD_*` knob family + the **root-`Config` wiring** + the **event EMITTER + its non-blocking bridge + the lifespan wiring** + the counters + the `ram-guard:` log + the **NEW** `GET /api/resources/ram-guard` + the T4 fence re-run | 17 | tight with 2, 3, 4 | pending |
| 6 | Bounded Test Suite | Real ballooning trees under small injected ceilings; kill-switch byte-identical; the §8 mandated additions; the S8-matching Q2 predicate task; the fixture split; mutation-proof | 24 | tight with 2, 3, 4, 5 | pending |
| 7 | Durability + Operator Memo + Follow-up Routing | The **operator memo** `docs/runbooks/ram-guard-and-unit-bounds.md` (PRIMARY) + the **operator docs** (knobs / env-off / counters / endpoint) + the §3.5 honesty set (items 10–13) + the §3.4 truth statement + the follow-up queue + the T4 hand-off note — **no migration** | 12 | loose with 3, 4 (parallel; self-contained criterion) | pending |

**Phase ownership (§15 B15, verbatim — supersedes the earlier arch §7 ownership sentence):**
```
Phase 2 owns the event HOOK, the DQ6 consumer-gate artifact (pins-only), the execution-lane thread, and the ceiling detector (ceiling.py).
Phase 5 owns the event EMITTER, the loop bridge, the lifespan wiring AND the lifespan construction (app.state.ram_guard), and the root-Config wiring of RamGuardConfig.
Phase 7 owns the operator documentation and the honesty set (fifteen items).
```

**Phase 1 is an explicit gate — and it is now CLOSED.** No code was written before
the architect returned the DQ rulings. The gate's outcome reshaped three phases:
**P2 grew** (it absorbs the envelope producer, the consumer gate, the PSS confirm
and the guard thread), **P5 shrank** to config + observability + the fence re-run,
and **P7 shrank and re-weighted** to the operator memo (no migration lands in v1).

---

## Coupling Map

| | P1 | P2 | P3 | P4 | P5 | P6 | P7 |
|---|---|---|---|---|---|---|---|
| **P1** Design Gate | — | tight (P2 implements DQ1+DQ3) | tight (P3 implements DQ2+DQ4) | tight (P4 implements DQ2+DQ4) | loose (DQ6's consumer-gate artifact now lives in P2; P5 implements DQ7 — the config surface) | loose | loose |
| **P2** Measurement | tight | — | tight (shared reader) | tight (shared reader) | tight (P5 consumes the envelope producer, the counters and the guard heartbeat from P2 — Phase 5's envelope-event and counter tasks depend on Phase 2's envelope-producer and guard-thread tasks) | tight | independent |
| **P3** Bash Lane | tight | tight | — | independent (separate lanes) | tight (error + knobs) | tight | loose |
| **P4** Proc Lane | tight | tight | independent | — | tight (error + knobs) | tight | loose |
| **P5** Contract/Config | tight | tight (P5 consumes the envelope producer, the counters and the guard heartbeat from P2 — Phase 5's envelope-event and counter tasks depend on Phase 2's envelope-producer and guard-thread tasks) | tight | tight | — | tight | independent |
| **P6** Tests | independent | tight | tight | tight | tight | — | independent |
| **P7** Durability | loose | independent | loose | loose | independent | independent | — |

**Tight couplings to guard against:**

- **P2 ↔ P3/P4** — both lanes consume ONE tree-RAM reader. If the reader's contract
  (units, failure semantics, "unreadable" vs "zero") changes, both lanes change.
  Freeze the reader contract in P2's exit criterion.
- **P3 ↔ P5** — the bash typed-error shape and the bash tool's observable behavior
  are one contract. A shape change in P5 lands in P3.
- **P3/P4 ↔ P6** — the test suite pins the error string and the exit_kind vocabulary.
  Test-first is cheaper here than retrofitting.
- **P5 ↔ P3/P4** — the knob names and defaults are read by both lanes.

**Independent:** P3 and P4 do not share code (different process managers, different
kill primitives). They may proceed in parallel after P2 lands — *if* the dispatcher
wants that; the skill's default sequential ordering is preserved here for reviewability.

---

## Risks

| # | Risk | Impact | Likelihood | Mitigation |
|---|------|--------|------------|------------|
| 1 | **FALSIFIED PREMISE (arch doc §1.1 P1) — the live unit ships NO memory cap**, so the previous "the unit cap trips first" reasoning described a counterfactual. The *real* risk is sharper: **the kernel global OOM is today the only layer actually enforcing anything**, and the Oct-30 killing memory was largely **orphaned children of a dead daemon incarnation** (E8) — invisible to any per-command guard | **High** | High | The guard is the **INNER, preventive** layer (live in-flight trees only); the **unit cap is the OUTER** layer that covers the orphan class; the kernel OOM is the LAST layer. The operator memo (Phase 7) is therefore **load-bearing, not optional hardening** — it carries the drop-in (`7G / 8700M`), the canary-first procedure, and the orphan finding. The plan never claims the guard protects the box from the orphan class |
| 2 | **False-positive kill** — summed RSS double-counts shared pages across tree members (a python tree with N children sharing an interpreter's libs can cross the ceiling while genuinely under it | **High** | Medium | DQ1 framing includes the cheap-trigger + expensive-confirm hybrid; make the metric choice and its double-count semantics EXPLICIT in the returned error (`observed_peak_bytes` is a **defined metric**, not "memory used"); keep the kill-switch default-ON but env-off as the fast operator escape. |
| 3 | **Watchdog starvation** — a memory balloon can starve the event loop; an `asyncio`-resident watchdog may never run, so the feature silently fails exactly when it matters | **High** | Medium | DQ2 frames the dedicated-daemon-thread home (wedge-capture precedent: *"A DEDICATED DAEMON THREAD — NEVER A COROUTINE"*, `daemon/services/wedge_capture/watchdog.py:1-49`); the thread performs `os.killpg` directly (no loop needed), so the kill survives a starved loop. |
| 4 | **Poll overhead** — a /proc tree scan per tick per live spawn is O(host pids) for pgid enumeration; a 200 ms poll across N live spawns could be measurable | Medium | Medium | DQ3 framing (pgid scan is O(host), not O(tree)); P2 must MEASURE the per-tick cost and P7/runbook must record the budget. Consider a shared-per-tick scan (one /proc walk serves all live spawns) rather than per-spawn walks. |
| 5 | **Killing the wrong tree** — PID recycling, or a `setsid` escapee that re-parents outside the group, could redirect the kill | **High** | Low | The guard supplies its **own** identity gate — the DQ4 ladder's **P0–P7** pre-kill predicates (captured pgid + leader `starttime` re-verification, **fail-closed** — with the three-way absent-vs-unreadable leader split; **P5a** refusing a recycled pgid; the environ check is **P6, secondary only**). **FALSIFIED (arch doc §1.1 P3/P4):** the previous text conflated two different mechanisms — the reaper's real gate is `verify_and_kill` (`bash_spawn_reaper.py:735-900`, 5-rule, DB-row-backed, fail-closed, starttime gate `:851-857`), whereas `proc_tools.py:1037-1053` is a **different, fail-open** 1-signal environ check; and the **bash lane has NO ownership gate at all** (`_kill_process`, `bash.py:697-745`, sends `killpg` with no environ/starttime check). This plan does **not** claim the bash lane's pre-existing path is gated |
| 6 | **Grace window defeats the purpose** — the existing bash ladder is SIGTERM → **5 s** → SIGKILL; a ballooning process keeps allocating for those 5 s and can push the unit past `MemoryMax` before the KILL lands | **High** | Medium | DQ4 framing: a RAM emergency needs a SHORT grace (the reaper's slice-probe ladder is the sync-friendly precedent). Make the grace a separate, shorter knob than the timeout grace. |
| 7 | **Tool contract break** — the agent-visible error string changes shape; a downstream consumer may assume the current form | Medium | Low | **The gate is SATISFIED by evidence, not by ritual:** `architecture-recommendation.md` §6 carries the **enumerated census** — **the enumerated census** (the counts are **derived at fold time, never pinned** — the table is the artifact) (the count is a derived convenience; **the table is the artifact**), owned by **Phase 2**. **AMENDED — the §6 consumer-count figure is WITHDRAWN** (no such list existed). **FALSIFIED (arch doc §1.1 P6):** `tests/test_chart_tools_legacy_error_contract.py` pins **chart tools only** (58 lines; imports neither bash nor proc) — it was the **wrong file**. The real pin surface is the `proc_status` substring assertions (`tests/tools/test_proc_tools.py:240, 257, 382, 1163, 1166-1167, 1233, 1831`) and the `ProcessInfo.status == "killed"` attribute pins (`:1614, 1655, 1692, 1944, 1992, 2038`) — satisfied by *adding* lines and keeping `status="killed"`. Four strings must never change (bash success, bash timeout, the `proc_status` block, the `proc_list` header) and are pinned by the Phase 6 suite — the bash **timeout string has ZERO test pins** and is therefore guaranteed byte-identical only when no breach was observed |
| 8 | **Test host OOM** — the harness genuinely allocates RAM; a mis-sized test could OOM the CI/dev box | **High** | Medium | P6 mandate: ceilings are **small and injected** (MiB-scale), the allocator sizes are **explicitly bounded in the test code**, and the harness asserts the host's free memory before spawning. Never use the **resolved production default** in a test — the derived default is host-sized (8,367 MiB on this host) and the explicit knob is the only test-safe path |
| 9 | **Fenced-off substrate** — reaching for `resource_pid_map`'s dormant columns (the obvious "enforcement audit" landing zone) breaks `tests/test_resource_control_seam.py` | Medium | Medium | The Fence section above states the constraint; DQ5 frames a RAM-only registry as the substrate; P7 records the unfencing as a user-gated follow-up, not an inline change. |
| 10 | **Coverage hole: `setsid` escapees** — a command that calls `setsid` leaves its process group, so neither the tree measurement nor the group kill reaches it (documented gap in BOTH lanes: `proc_tools.py:1677-1683`, `bash.py:590-600`) | Medium | Medium | DQ3 framing states the gap explicitly; note the orphan-reaper's Phase-4 env-stamp sweep already reaps such escapees at cleanup points, so the RAM guard is not the only net. Frame "setsid-aware tree discovery" as an explicit DQ3 sub-option. |
| 11 | **Boot-abort on a bad env value** — house rule: out-of-range env validated OUTSIDE the try/except at `load_config()` ABORTS BOOT (`config.py:911-916`, `api.py:2266-2278`) | Medium | Low | Clamp bounds generously (the ruled ceiling clamp is `ge=64, le=65536`), and P6 includes the config ladder case "out-of-range env aborts boot" as an explicit, asserted test. |
| 12 | **Two enforcement ports competing** — the RAM guard (default-ON, non-user-gated) lands beside the T4 seam (`enabled=false`, `user_gated=true`) and the observe-only fence test | Medium | Low | P1/DQ9 makes the separation structural: P1 lives OUTSIDE the T4 seam and does not read or write `resource_limit_policy`; the fence test must still pass unchanged. |
| 13 | **Overlap with the resource monitor's own child accounting** — `resource_pid_map` already tracks peak RSS at exit for registered children; two sources of truth could disagree | Low | Medium | Document the relationship: `resource_pid_map.peak_rss_kb` is the **exit-time floor** (a documented under-report — `bash.py:1096-1109`), the RAM guard's peak is the **watchdog's own sampled peak**. Different semantics; do not merge them. |
| 14 | 🔴 **NEW — the guard LOSES the race against the cgroup cap above ≈0.5 GiB/s on this host** (§3.4). The published pair's role is the **orphan class**; the guard-vs-cap race is winnable only below the measured-bottom rate, and the measured band is 1–3 GiB/s. Above it, **the daemon is the casualty**. Restoring the layering requires a ≥ ~14.5 GiB host, **not** a different ceiling | **High** | High | The §3.4 truth statement is reproduced **verbatim** in the memo, the runbook and the honest-peak envelope — **never papered over**. The operator drop-in is the mitigation (the cap becomes the containment layer). The **dynamic** default ships either way; this decides the **layering**, not the ceiling (arch doc §10 item 4) |
| 15 | 🟡 **NEW — the bounded re-arm trades containment for confirm frequency** (§4.1a): a smaller `CAP` means the trigger fires earlier, so the confirm runs more often (up to the tick rate ⇒ ≈17.5 % duty at a 250-pid host) and the tree's real footprint can reach `F_max` ≈ 3.86 GiB at R = 1 GiB/s | Medium | Medium | Both are **disclosed** in the §3.5 honesty items 10–11 (verbatim, in the memo and the envelope); the re-arm threshold is **reported** in the envelope as `rearm_threshold_bytes` beside the configured `limit_bytes`; the growth governor bounds the confirm rate |
| 16 | 🟡 **NEW — the durable event can be DROPPED** (§4.5): no live loop / loop closed / daemon shutdown ⇒ the event is dropped, and across a restart the event is the **only** durable artifact ⇒ a dropped event is a permanent loss of the forensic record | Medium | Low | The drop is **visible**: `ram_events_dropped++` on the endpoint plus the `WATCHING→KILLED` transition log line; the bridge is **non-blocking** (never `.result(timeout=…)`); the guard never imports the bus |
| 17 | 🟡 **NEW — `RamGuardConfig` declared but never wired** would make the whole env tier and the boot-abort rung dead code (approver blocker 5's root cause) | Medium | Low | The root field is declared in §4.6 and asserted by the §15 B9 shapes, which are unreachable without the wiring; the boot-abort rung is asserted, not disclosed |
| 18 | 🔴 **NEW — a WEDGED execution-lane thread stops the kills while the measurement continues** (iteration 002). A never-returning `smaps_rollup` read (the house reader is a plain blocking `read_text()` with no timeout, `proc_readers.py:127-133`) wedges the lane ⇒ **kills stop; measurement continues**. `lane_alive` stays **True** — the signal is the **progress heartbeat `last_lane_progress_at` frozen** plus `lane_pending` climbing, **NOT `Thread.is_alive()`** | **High** | Low | The **two-thread** shape (DQ2/B16) confines the wedge to the kill path — the tick thread keeps sampling, `last_tick_at` keeps advancing and the liveness sweep still runs (the §5 property, §15 B17). The residual (a wedged read holds the kill path until the daemon restarts) is **disclosed** in the §3.5 honesty set (item 11) and the unit cap is the backstop |
| 20 | 🔴 **NEW — at the 70 % dynamic default the guard fires at-or-after daemon death on this host** (§3.3) — **user-accepted by stated intent** (disaster prevention, not constraint). The operator levers are the knob and the host resize; no feasible unit cap exists at the default below a ~26.5 GiB host | **High** | High | The disclosure is **retained and user-accepted** (arch doc §10 item 4); the memo presents **no-cap / lower-the-knob / resize**; the derived default is never upper-capped (USER RULING #1's intent) |
| 19 | 🔴 **NEW — the N = 2 contended case empties the unit-cap window** (iteration 002): two simultaneous full-ceiling breaches ⇒ the unit-cap floor is 9.36625 GiB > the 8.88 GiB box ceiling ⇒ the window is **EMPTY above R ≈ 0.598 GiB/s**, and the guard wins at `8700M` only below R ≈ **0.28 GiB/s** | **High** | Medium | The N = 2 row is a **DISCLOSURE, not a design target**; the memo states that the published `7G / 8700M` pair is a **single-tree claim**; the contended overshoot `Ω_contended(2) = R × 1.21 s` appears in the honesty set and the memo |

### Critical-Path Risks (would invalidate the plan if realized)

- **R1** (unit cap interaction) and **R3** (watchdog starvation) are the two that can
  each make the feature a no-op in production while every test passes. Both must be
  answered in Phase 1 and re-proven in Phase 6, not deferred to a late phase.

---

## Success Criteria

| # | Criterion | How to Measure | Threshold |
|---|-----------|----------------|-----------|
| 1 | A bash command whose tree exceeds the injected ceiling is **terminated tree-wide** | P6 test: spawn `python3 -c` that forks children allocating past a 64 MiB ceiling; assert the direct child AND every grandchild are dead within the grace + poll window | All descendants of the spawned pgid dead; ≤ 5 s wall from breach detection to all-dead |
| 2 | The same holds for the proc tool | P6 test: same tree via `proc_run`; assert status is the RAM-kill status and the tree is dead | Same as #1 |
| 3 | The agent receives a **typed** error naming the **deciding** value — **guaranteed only while the tool call is IN FLIGHT** (A3, §17.3) | Assert the returned string parses as the `{ok:false, code, surface, reason, …}` envelope with `code="ram_limit_exceeded"`, the correct `surface`, a `limit_bytes` (= the **resolved** ceiling), a `reason`, and that the **DECIDING VALUE** — `confirmed_pss_bytes`, or `observed_peak_bytes` on a fail-open trigger — is **≥ the ceiling**. **Not** `observed_peak_bytes >= limit_bytes`: under the ruled hybrid the RSS peak and the deciding PSS value are deliberately **distinct** quantities. **Post-return kills are NOT claimed here** — they surface via the durable event + the guard endpoint (§3.5 item 14) | Envelope parses; the **deciding value** `>= limit_bytes`; the assertion is scoped to the **in-flight** call |
| 4 | The reported peak is **honest** — it is the watchdog's sampled peak, not the spawn-time floor | Assert the reported peak for a command that allocates a KNOWN amount exceeds that amount by a margin, and that it is > the spawn-time VmHWM floor | Reported peak ≥ 90 % of the known allocation |
| 5 | Under-limit commands are **unaffected** | P6 test: a command allocating 8 MiB under a 64 MiB ceiling → normal exit, normal output, `exit_kind=success`, no event, no counter increment | Exit code 0; output byte-identical to the pre-feature baseline |
| 6 | Kill-switch OFF is **byte-identical** | P6 test (mirroring the existing `test_bash_spawn_stamp.py` idiom `explicit_off_byte_identical_env_no_row`): with the switch OFF, the child env carries no stamp, no row/record is written, and the returned output is byte-identical to the ON-path output for an under-limit command | Byte-identical output; zero writes; zero extra syscalls observable in the env capture |
| 7 | The config ladder behaves per house rules | P6: **unset ⇒ dynamic** (`config.ram_guard.ceiling_mib is None`); an explicit env override applies; an out-of-range **explicit** value aborts boot — **asserted** (`pytest.raises(ValidationError)` around `load_config`); **OFF ⇒ NO ABORT** even with an out-of-range value set (B26); YAML literal does NOT silently beat the env tier (the yaml-literal trap) | Each rung behaves as specified |
| 8 | **No LIVE member remains** after a RAM kill (C3, §3.5 item 13) | Assert the direct child was reaped (the pre-existing `await proc.wait()`) and that **no LIVE member** remains in the group; a transient `Z` grandchild is **init's business**, not the guard's — the daemon is **not** the reaper of a killed tree's grandchildren (there is **no** subreaper in `daemon/`) | No live member in the killed group; the direct child is reaped; **no** grandchild-reaping claim is made |
| 9 | No secrets in any new log/event/record | P6: run a command whose env overlay carries a secret marker; assert the marker appears in NO log line, event payload, or persisted record | Zero occurrences of the marker value in logs/events/records |
| 10 | The feature is **inert** where it must be | P6: on a non-Linux platform (or when the platform guard says so), the guard performs NO measurement and NO kill; a command that would exceed the ceiling completes normally | Zero kills; zero measurement calls |
| 11 | The **existing fence tests still pass unmodified** | Run `tests/test_resource_control_seam.py` unchanged; run `tests/tools/test_bash.py`, `tests/tools/test_proc_tools.py`, `tests/unit/services/test_bash_spawn_reaper.py` | All pass, unmodified |
| 12 | The existing suites are **net-new-red free** | Full `tests/` run compared to the base commit | Net-new reds = 0 |
| 13 | The unit-cap interaction is **documented and actionable** | The Phase 7 operator memo `docs/runbooks/ram-guard-and-unit-bounds.md` states the **measured** state (`MemoryMax=infinity` — **no cap exists today**), the §3.4 interaction table (the dynamic-default regime, the general bound `T ≥ 26.5 + 2.33·R GiB`, the recommended/rejected rows), the drop-in vehicle + canary-first procedure + verify/rollback, and the §3.5 honesty set — **all fifteen items** (1–9, 9b, 10–15) | The memo contains the formula, the interaction table, and the worked example; the honesty set is reproduced verbatim — exactly the fifteen items |
| 14 | The DQ rulings are **recorded** | Phase 1 artifact names, for each of DQ1–DQ10, the chosen option, the rejected options, and the data it depends on | 10/10 rulings recorded with a rejected-option list |
| 15 | **The over-count re-arm is BOUNDED** | A fork tree whose summed RSS crosses the ceiling but whose summed PSS does not ⇒ NOT killed; the re-armed threshold is ≤ `ceiling + 32 MiB` and **ratchets** (never decreases); the confirm does not run on every tick (128 MiB growth governor) | Threshold ≤ `ceiling + RAM_GUARD_REARM_MAX_EXTRA_BYTES`; monotonic; confirm count ≤ growth/128 MiB |
| 16 | **The verdict is PUBLISHED before `mark_exit`** | A recording fake observes the call order: publication → `mark_exit` → guard-registry release; the success arm's `mark_exit` did **not** run | The observed order matches; the RAM verdict survives |
| 17 | **The config is wired into the root `Config`** | `load_config()` yields `config.ram_guard.ceiling_mib is None` by default (unset ⇒ dynamic) and `== 64` with `RAM_GUARD_CEILING_MIB=64`; out-of-range ⇒ `pytest.raises(ValidationError)`; **OFF ⇒ NO ABORT** even with an out-of-range value set (B26) | All three hold; the env tier is live, not dead code; the OFF branch does not abort |
| 18 | **A dropped event is visible** | No live loop ⇒ the event is dropped, `ram_events_dropped++`, and the transition log line is present; the tick does not block | Drop counted + logged; no tick stall |
| 19 | **The reason vocabulary is exact** | The ladder's reason set is asserted as an exact ordered list of **16 codes** (9 kept/renamed + 7 added); the status set is `{killed, already-dead, refused}`. **The enumeration above IS the authoritative exact set: 16 codes = 9 kept/renamed + 7 added. Any count other than 16 anywhere in the plan set is a defect.** | Exact set match; no drift; **zero** count sites other than 16 |
| 20 | **No plan-side text contradicts the amended contract** | A §15 char-for-char grep over the plan set returns the pinned strings; the withdrawn §6 consumer-count claim appears **nowhere** (and the withdrawn "guard always completes before a unit kill" claim appears only inside the verbatim truth statement that prohibits it) | Zero survivors for the withdrawn claims; every B-string present |
| 21 | **The lane heartbeat is a PROGRESS heartbeat** | A wedged execution-lane thread leaves `lane_alive` True but `last_lane_progress_at` frozen, with `lane_pending` climbing — **not** `Thread.is_alive()` | The endpoint reports the frozen progress heartbeat and the pending depth |

---

## Design Questions Framed for the Architect (DQ1–DQ10) — as framed, now RULED

These were **framed, not decided** when this plan was written. **All ten are now
RULED and binding** (arch doc §2; the one-line rulings are in the "Phase 1
Rulings" block above). The framing is **retained below as the decision record** —
each entry keeps its options, the data each option needs, the data each option
costs, and the lean recommendation the architect actually took. Read the **ruling**
for what is binding; read the **framing** for why the alternatives were rejected.

> ⚠️ **Falsified anchors inside the retained framing.** The framing below was written
> *before* the architect verified the code, and it asserts several things the arch
> doc then **falsified** (arch doc §1.1). Do **not** build on the framing's factual
> claims — build on the rulings. The falsified items are:
> **P2** the `pid_registry` path (`daemon/repositories/pid_registry/models.py` does
> **not** exist; the real site is `daemon/services/resource_monitor/pid_registry.py:120-138`)
> · **P3/P4** the "ownership gate" claims (the reaper's real gate is
> `verify_and_kill`, `bash_spawn_reaper.py:735-900`, 5-rule and fail-closed;
> `proc_tools.py:1037-1053` is a **different, fail-open** 1-signal check; and the
> **bash lane has no ownership gate at all**) · **P5** "proc captures pgid at spawn"
> (**false** — it is re-derived at kill time) · **P6** the chart-tools legacy test as
> the DQ6 gate (**inapplicable**; the gate is arch doc §6's **enumerated census** — the §6 consumer-count figure is withdrawn) ·
> **P7** "`read_pss_rollup` exists, so the reader exists" (**self-only** — it
> hardcodes `/proc/self/smaps_rollup`; a new per-pid reader is required) · **P8** the
> "migration-backed `reap_reason` extension" (**moot** — DQ10 rules no durable
> verdict, and `reap_reason` is plain TEXT with no CHECK constraint) · **P9**
> `list_group_members` as the injectable primitive (**hardcodes `/proc`**, and
> per-spawn invocation costs 9.49 ms/scan — it cannot serve as the hermetic seam) ·
> **Q1** the "**4th task**" proc-seam framing (arch doc §7's `(4th task)`
> parenthetical and the rev-1 "watchdog as the 4th task, mirroring `timeout_task`"
> shape — **superseded by leader ruling Q1: no 4th task**; the proc lane registers
> with the guard thread → status mapping → release, and the arch doc's §7
> parenthetical has since been struck so the two documents no longer contradict).

### DQ1 — Tree RAM metric

| Option | Mechanism | Data need | Cost / caveat |
|---|---|---|---|
| **(a) Summed RSS** | `VmRSS` from `/proc/<pid>/status` per tree member, summed | Per-pid `status` read — the existing `proc_readers.read_child_snapshot` (`proc_readers.py:737-758`) already does this, with an **injectable `proc_root`** (hermetic fixtures) | Cheap (O(tree) file reads). **Double-counts shared pages** across tree members → errs toward a false kill |
| **(b) PSS** | `/proc/<pid>/smaps_rollup` per tree member | Same, plus a `smaps_rollup` read per pid | Fair accounting. **Expensive** (kernel walks page tables). The sampler only affords it at **1/10 cadence** (`proc_readers.py:11-14`) — a 200–500 ms watchdog cannot |
| **(c) Per-spawn cgroup `memory.current`** | Kernel-accounted, exact | A cgroup per spawn | Exact. **Requires per-spawn cgroup creation — no delegation, `systemd-run` unavailable** (standing constraint); children share the daemon unit cgroup today |
| **(d) Daemon-unit cgroup number** | `memory.current` of the daemon's unit | Already exists (`proc_readers._read_cgroup_memory:191-210`) | Exact but **daemon-wide** — cannot attribute to a single command |

**Lean recommendation: (a) summed RSS as the trigger, with a (b) PSS confirm-on-breach
refinement offered as an explicit sub-option.** RSS over-counting is *fail-safe* for
the stated goal (kill the runaway), but it can false-kill a legitimate tree; a
one-time PSS read **at breach time only** (one expensive read, not per tick) would
make the kill decision on fair accounting. The PSS read's own failure semantics must
be specified (fail-open ⇒ kill anyway? fail-closed ⇒ let it live?). Note the
existing sampler reads PSS (`read_pss_rollup`) — but it is **self-only**
(hardcodes `/proc/self/smaps_rollup`), so a new **per-pid** reader is required
(arch doc §1.1 P7).

**Critical sub-question (see Critical Interaction below):** the metric choice
interacts with the unit cap.

### DQ2 — Poll strategy + loop home

| Option | Mechanism | Latency / overhead | Failure mode |
|---|---|---|---|
| **(a) In-execution watchdog task** racing the await seam (bash S2 `:1062-1065`; proc's `timeout_task` precedent `proc_tools.py:1241-1245`) | `asyncio` task per execution | Precise verdict; dies with the execution; zero cross-thread cost | **A starved event loop cannot run it** (wedge-capture docstring: *"A DEDICATED DAEMON THREAD — NEVER A COROUTINE"*). A memory balloon can starve the loop — the guard fails exactly when needed |
| **(b) Dedicated daemon thread** keyed to live spawns (wedge-capture precedent) | One `threading.Thread`; a RAM-only registry of live spawns | Survives a blocked/starved loop; needs the registry + cross-thread delivery | `os.killpg` is a plain syscall — **safe from any thread**, so the kill needs no loop. The coroutine only *observes* the verdict afterwards |
| **(c) Ride the 30 s sampler** | `ResourceMonitorConfig.sample_seconds` default 30, bounded [15,60] (`config.py:972-980`) | Zero new machinery | **Far too slow** for 10–11 GB ballooners (which can allocate multiple GB in seconds) |

**Lean recommendation: (b) a dedicated daemon thread**, with the per-execution
coroutine observing the verdict (the wedge-capture shape: thread samples and acts;
the coroutine reads the outcome). The counter-argument to state explicitly: (a) is
simpler and dies cleanly with the execution, and the starvation scenario is rare
but **is** the scenario this feature exists for. Data the architect needs: the
measured per-tick cost of the chosen reader (P2 produces it), the expected number of
concurrently-live spawns, and the target breach-detection latency.

**Poll interval** is a knob in its own right — house style is INT-only, so
milliseconds. Frame the trade-off: smaller ⇒ faster detection, higher overhead; the
architect should set the default and the bounds (candidate: default 200 ms,
bounds [50, 2000] ms).

### DQ3 — Tree discovery

| Option | Mechanism | Data need | Gap |
|---|---|---|---|
| **(a) pgid enumeration** | `bash_spawn_reaper.list_group_members:382-460` — Linux `/proc/*/stat` field 5 (`pgrp`); **fail-closed `None`** on enumeration failure; excludes zombies + kernel threads | Existing, tested primitive | **O(host pids)** per scan, not O(tree). **`setsid` escapees leave the group** — missed by both measurement AND group-kill |
| **(b) Recursive ppid walk** | No such walker exists. The boot sweep's ppid-chain walk (`boot_subprocess_sweep.py:421-453`, depth 64/512) walks **ancestors/descendants of self only** — not a general tree walker | New walker; must be bounded (depth + node cap) against a hostile /proc shape | Would catch group members' non-setsid children that escaped the group; more code, more surface |

**Lean recommendation: (a) pgid enumeration**, because it matches the kill delivery
unit (group kill) and reuses a fail-closed, tested primitive. Frame the
**setsid-escapee gap** explicitly (option (b) or a hybrid) and note that such
escapees are already reaped by the orphan reaper's env-stamp sweep — the RAM guard
is not the only net, but it would not *see* the balloon.

### DQ4 — Kill escalation + delivery

| Option | Mechanism | Fit |
|---|---|---|
| **(a) Reuse bash `_kill_process`** (`:697-745`) | `killpg(SIGTERM)` → wait **5 s** → `killpg(SIGKILL)`; pgid captured once (TOCTOU guard) | Reuses a proven ladder, but the **5 s grace** is long for a RAM emergency (the process keeps allocating), and it is `async` — awkward from a thread |
| **(b) Reuse reaper `_kill_ladder`** (`:678-731`) | `killpg(TERM)` → `_grace_wait_for_death` (0.25 s slices, `killpg(pgid,0)` probe) → `killpg(KILL)`; **zombie-aware skip** | **Sync**, slice-based, thread-friendly, already zombie-aware — the closest fit for a thread-resident guard |
| **(c) proc delivery via `_attempt_kill_signal`** (`:965-1086`) | 3-layer gate: status → liveness → **PID-ownership** (`/proc/<pid>/environ` exact-entry, `:1039-1052`; fail-open on unreadable) | The ownership gate is the correct anti-recycling defense for a kill decision |
| **(d) New short-grace ladder** | A RAM-specific ladder with a short grace (candidate [0.1, 5.0] s) | Best fit for the emergency semantics; new code, new tests |

**Lean recommendation: (d) a new short-grace sync ladder, built on (b)'s slice-probe
pattern and gated by (c)'s ownership check.** The architect must also rule the
**new vocabulary**: `exit_kind` for `pid_registry` (the closed set is
`success|timeout|cancelled|exit|unknown` — `pid_registry.py:121-135`) and the
proc-tool status set (`running|exited|killed|error` — `proc_tools.py:139-215`).
Candidate value: **`ram_killed`** in both, distinct from `timeout` so a RAM kill is
never miscounted as a timeout.

### DQ5 — Integration seam + bookkeeping

**Seams (verified):**
- bash — S1 capture block `:952-965` (pid + pgid known) · S2 the single lifetime
  await `:1062-1065` · S3 the kill arms `:1071` / `:1174-1214` · S4 result assembly
  `:1143-1160` · S5 register/unregister `:965` / `:481-486` · S6 `mark_exit`
  `:1110-1123`.
- proc — S7 post-spawn task set `:1226-1245` (the watchdog would be the **4th**
  task) · S9 the `timeout_task` pattern `:1241-1245` · S10 status mapping
  `:786-801` / `:855-875` · S11 `_attempt_kill_signal` for delivery.
- measurement — S12 `proc_readers.read_child_snapshot` (injectable `proc_root`
  `:16-18`) · S13 `list_group_members` (fail-closed `None`) · S14 `pid_registry`
  exit-kind vocabulary.

**Substrate options:**

| Option | Pros | Cons |
|---|---|---|
| **(a) RAM-only enforcement map** keyed by tracking id | Zero DB on the hot path (a DB write per tick is absurd); instant; testable | Verdict is lost on daemon restart (DQ10) |
| **(b) Extend `bash_spawn_registry`** with memory columns + a closed-vocab `reap_reason` extension | Durable, cross-restart, migration-backed | Vocabulary is a CLOSED SET (`:180-196`) ⇒ migration-backed extension; **proc_run writes NO registry row today** ⇒ the proc lane needs a new write path anyway |
| **(c) `resource_pid_map` dormant columns** (`action`/`action_by`/`acted_at`) | The "designed" enforcement-audit landing zone | **FENCED OFF** by `tests/test_resource_control_seam.py` (two grep fences). Would require a deliberate test-lifting change |

**Lean recommendation: (a) RAM-only registry as the enforcement substrate, with (b)
as the durable-verdict option if DQ10 rules for crash durability.** Note explicitly:
**the proc tool writes no registry row today** (only `bash.py:414` and
`mcp/stdio_wrapper.py:783` touch `pid_registry`; `proc_run` touches neither), so
covering the proc lane under option (b) requires a NEW durable write path — a real
scope item, not a detail.

### DQ6 — Typed error contract

The house pattern is `_format_secret_envelope` (`bash.py:662-678`): a JSON
`{"ok": false, "code": "<snake_case>", "surface": "<path>", "message": "…"}`
returned as a **string** at `:1215-1222`, before the generic `ERROR: …` arm. Tools
**never raise** — failures are returned strings ("ERROR: …" bash, "Error: …" proc;
pinned by `tests/tools/test_proc_tools.py:53-55`).

**Candidate shape:** the same envelope with `code="ram_limit_exceeded"`,
`surface="bash.exec"` / `"proc.exec"`, `message` carrying the human sentence, and
`limit_bytes` / `observed_peak_bytes` / `pid` / `pgid` as siblings of `message`.

**Data needs / checks before committing to the shape:**
1. `tests/test_chart_tools_legacy_error_contract.py` — read it before committing.
2. Any tool-output **post-processor** that parses tool return strings — grep for
   consumers of the `ok`/`code`/`surface` keys before changing the shape.
3. `observed_peak_bytes` MUST come from the watchdog's **own sampled peak**. The
   existing `peak_rss_kb` is the **spawn-time VmHWM floor** — the post-wait read
   almost always misses (`bash.py:1096-1109`) ⇒ an honest **under-report**. The new
   error must not inherit that lie.
4. Decide whether the numeric fields are siblings of `message` (parseable) or inside
   it (human-only) — and state which downstream consumers that breaks.

**Lean recommendation: mirror `_format_secret_envelope` exactly**, with the numeric
fields as siblings, and the observed peak sourced from the watchdog's sampler.

### DQ7 — Config surface

| Option | Family | Note |
|---|---|---|
| **(a) New `ENSEMBLE_*` boot-env family** (reaper precedent: `ENSEMBLE_BASH_ORPHAN_REAPER` — default-ON in all lanes, explicit `0/false/no/off` = OFF, **complete no-op** including zero DB queries, one INFO line at boot) | bare `ENSEMBLE_*`, read at import | Matches the kill-switch precedent the dispatcher named. Boot-time read ⇒ no restart-class surprise |
| **(b) A new config class** with its own `env_prefix` (per-class prefix families are the house rule — a bare `ENSEMBLE_*` does NOT bind to a prefixed class) | e.g. `RAM_AUTO_TERMINATION_*` | Gives typed bounds (`ge`/`le`) and validation. **YAML block must stay COMMENT-ONLY** — pydantic init-kwargs beat env (`config.py:911-916`), so a yaml literal silently kills the env tier |
| **(c) `ResourceMonitorConfig` field** | `RESOURCE_MONITOR_*` | Couples the fast guard to the 30 s monitor's config and its yaml trap; the cadences differ by 100× |

**Constraints the architect must honor:**
- **Sizes are INT-only** (house rule — `charged_bytes`/`rss_kb`/`self_rss_budget_mb`
  are all ints; no human-string parsing). Use MiB as the unit with `ge`/`le` bounds.
- **Validation happens OUTSIDE the try/except at `load_config()`** ⇒ an out-of-range
  env value **ABORTS BOOT** (house-approved; `config.py:911-916`, `api.py:2266-2278`).
- **The ceiling MUST be runtime-injectable per call / per test fixture** — a
  boot-env-only knob would force the test harness to restart the daemon with a
  64 MiB ceiling, which is unacceptable. This is a hard architectural requirement,
  not a preference: *the effective ceiling must be a parameter threaded from the
  config default down to the guard call.*
- The kill-switch OFF path must be a **complete no-op** (no measurement, no
  registry, no writes).

**Lean recommendation: (b) for the numeric knobs + (a) for the master kill-switch**,
with the numeric ceiling ALSO threaded as an explicit runtime parameter so tests
inject small values without a restart. The YAML block stays comment-only.

### DQ8 — Observability

**Candidate surfaces (house idioms):**
- A **logger line** with a module prefix (e.g. `ram-auto-termination: …`), following
  the `resource-monitor: …` / `[ResourceMonitor] started` convention.
- An **in-process counter** in the `SamplerCounters` shape (`sampler.py:243-284`),
  readable by a `/api/resources/...` endpoint.
- A **durable event** via `EventBus.create_event(instance_id, kind, data)`
  (`event_bus.py:155-191`) with an **additive** `EventKind` — the house pattern.
  Payload: `{code, surface, observed_peak_bytes, limit_bytes, pid, pgid}`.
- **Sanitization**: the persisted command must be the sanitized form —
  executable + argv-shape only, never the `env` overlay (which carries
  `__ENS_SECRET__`/`__KMS_REF__` markers). Reuse `_sanitize_command_for_registry`
  (defined `bash.py:251`; called at `bash.py:966-979`) — **that helper sanitizes for the REGISTRY row only**; the
  event and envelope payloads carry **usage numbers only** (§4.3 negative space), and
  the helper's presence here grants **no** permission to include argv anywhere.

**Data needs:** the architect rules on the event kind name, the counter names, and
whether the event is emitted per breach (once) — it must NOT be per poll tick.

### DQ9 — Default-ON vs the T4 user-gated seam

**Commission requirement (Dispatcher Ruling (c)):** the kill-switch is
**default-ON with a documented env-off**, planned **OUTSIDE the user-gated T4
seam**. The T4 `resource_limit_policy` table (`models/resource_monitor.py:321-378`)
has `enabled` default **FALSE** and `user_gated` NOT NULL default **TRUE**, and
**no writer exists**.

**The fence:** `daemon/routers/resources.py:1166-1179` states *"ZERO enforcement …
never kills"*; `_CONTROL_ACTION_NAMES` (`alert`, `throttle`, `kill`) is advertised
`available:false` (`:1223-1230`); `tests/test_resource_control_seam.py` pins both.

**Therefore:** P1 enforcement must be a **new port that does not read or write
`resource_limit_policy`** and does not touch the dormant columns. The
`/control-capabilities` response shape is pinned by the fence test ⇒ **do not
change it**. The T4 ceremony (a user-gated policy row) is the **future** path
(cited adopted direction: `resource-cost-control/approach-comparison.md:75`,
`architecture-recommendation.md:114`), not this commission.

**Open for the architect:** how the two surfaces coexist in the operator's mental
model — a default-ON hard guard beside a user-gated policy seam — and whether a
future user-gated *disable* is needed to suppress the guard (the env-off switch is
the v1 answer).

### DQ10 — Crash durability

| Option | Mechanism | Consequence |
|---|---|---|
| **(a) In-process only** | The RAM-only registry dies with the daemon | A RAM-killed command's verdict is lost on restart. Acceptable: the command died with the daemon anyway |
| **(b) Boot-reconcile awareness** | Durable rows already reconcile at boot (the reaper's boot lane; the `bash_spawn_registry` open-row lifecycle `open→reaped`) | A RAM-killed row should close with an **explicit reason** — but `reap_reason` is a **CLOSED SET** (`:180-196`) ⇒ a migration-backed vocabulary extension |

**Lean recommendation: (a) for v1 enforcement + (b) as a P7 vocabulary extension IF
DQ10 is ruled "durable"** — the durable write would occur **once per killed
command** (at exit), never per tick. Note the asymmetry: the bash lane already
writes a durable row per spawn (bounded 1.5 s, fail-open, `:1009-1059`); the proc
lane writes **none** — so durable verdicts for the proc lane need a new write path
(DQ5).


### Phase 1 Rulings (architect-adjudicated 2026-11-10 — BINDING; full detail + evidence in `architecture-recommendation.md`)

| DQ | Ruling (one line) |
|---|---|
| DQ1 | **(e) hybrid, confirm MANDATORY**: `summed_rss_bytes` trigger per tick + one-shot `summed_pss_bytes` confirm at breach (250 ms deadline, fail-open ⇒ kill); overcounted trees re-arm at **`ceiling + min(shared_extra_bytes, RAM_GUARD_REARM_MAX_EXTRA_BYTES)`** with a **growth governor** (128 MiB) — **AMENDED** (approver blocker 2: the previous unbounded `ceiling + (rss − pss)` rule is **withdrawn**). Measured over-count factor ≈ N (8.9× on a 9-member fork tree) — pure summed RSS false-kills fork-heavy legit jobs |
| DQ2 | **AMENDED (approver iteration 002, blocker B3) — the DQ2 ruling sentence (§15 B16), verbatim:** (b) TWO dedicated daemon threads — ONE TICK THREAD and ONE EXECUTION-LANE THREAD — started/stopped together on the FastAPI lifespan (app.state.ram_guard owns both; OFF ⇒ neither thread exists). The tick thread owns registration, the ONE shared /proc scan, the RSS trigger, the peak/counter updates and the breach handoff; it never reads PSS, never signals, never sleeps beyond the poll. The execution-lane thread is the only executor of the kill path: the one-shot PSS confirm (250 ms deadline, fail-open ⇒ kill), then ram_kill_ladder (TERM → slice-probed grace → KILL → the P8 probe), then the verdict publication and the emitter call. The lane never samples and the tick never kills, so a stalled confirm can never delay a tick. **The constants (§15 B16), verbatim:** RAM_GUARD_PENDING_MAX = 2 · RAM_GUARD_GRACE_POLL_SLICE_SECONDS = 0.025 The tick path never logs; no lock across /proc/signal/sleep; the pending set is bounded and an overflow is **re-offered**, never skipped |
| DQ3 | **(c) hybrid from ONE shared `/proc` scan per tick**: group ∪ bounded descendant-closure (per-spawn scans cost 9.49 ms each and overrun the tick); enumeration failure ⇒ drop the sample, never kill |
| DQ4 | **(d) NEW sync short-grace ladder** (TERM → slice-probe → KILL), grace 250 ms [0, 2000] as a SEPARATE knob; identity = captured pgid + leader `starttime` re-verify (fail-closed). **AMENDED (leader ruling Q2=(i) + approver blockers 1 & 6):** the watch stays alive until the group member count reaches 0; identity **re-anchors on the surviving group members** when the leader is gone; the leader `starttime` gate applies to the leader **only while it is alive**; `already-dead:group-empty` fires **ONLY at group member count 0**; the pre-kill gate is **P0–P7** (P8 is the post-kill probe ⇒ `killed:tree-incomplete`) and the vocabulary is **16 codes**. **Canonical predicate strings (verbatim):** `the watch stays alive until the group member count reaches 0` · `identity = captured pgid ∧ surviving-member group membership` · `starttime gate applies to the leader only while alive` · `the watch is released only at group member count 0` · `group_member_count == 0`. Vocabulary: `exit_kind="ram_killed"` (constant; the real closed set is `daemon/services/resource_monitor/pid_registry.py:120-138`, NOT the path cited in this plan); proc gets `ram_killed: bool`, status stays `"killed"` |
| DQ5 | **(a) RAM-only map**, both lanes; the proc lane registers with the guard ONLY (no new durable write path); the bash `bash_spawn_registry` row stays **OPEN** on a RAM kill so the reaper's escapee net stays armed; (c) stays fenced, no lift routed |
| DQ6 | Envelope mirrors `_format_secret_envelope`, numerics as siblings before `message`, **the envelope IS the entire bash return**; proc rides `proc_status` as a `ram_breach:` line + `ram_killed: true`. **AMENDED — the §6 consumer-count figure is WITHDRAWN** (no such list existed); the gate artifact is the **§6 enumerated census** (the enumerated census — counts derived at fold time, never pinned; the table is the artifact) and it is **owned by Phase 2** (Phase 5's duplicate claim collapses to a cross-reference). The chart-tools legacy test remains **inapplicable**. Additive-key evolution rule; no version field |
| DQ7 | Bare `ENSEMBLE_RAM_GUARD` master switch (reaper semantics, default-ON, complete no-op OFF, **OFF honored BEFORE numeric validation** — §17.4) + `RamGuardConfig(env_prefix="RAM_GUARD_")`: `CEILING_MIB` is **`Optional[int]`, default `None` ⇒ the DYNAMIC 70 % default** (USER RULING #2, §4.7); explicit values validated [64, 65536] — **the clamp applies to explicit values ONLY** · POLL_INTERVAL_MS 200 · GRACE_MS 250 · PSS_CONFIRM_DEADLINE_MS 250; YAML comment-only; out-of-range aborts boot **while ON**; per-call ceiling is an **internal/test seam — NEVER an LLM-visible tool parameter**. **AMENDED:** the class **and** the bare switch are wired into the **root `Config`**; the depth/node caps, the re-arm constants, `RAM_GUARD_PENDING_MAX`, `RAM_GUARD_GRACE_POLL_SLICE_SECONDS` and `WAIT_SLACK_MS` are **module constants, not knobs** |
| DQ8 | `EventKind.RAM_LIMIT_EXCEEDED` (= the envelope `code`); payload = **the envelope key set + exactly four event-only keys** (`trigger`, `state`, `tracking_id`, `breach_at`), producer-side allow-list, **no argv/env/stdout anywhere**; one event per breach — **refusals emit NO event** (counters + the transition log line, §17.2); the endpoint heartbeat is the **eleven-field B23 set** (`lane_alive`, `lane_pending`, `last_lane_progress_at`, `resolved_ceiling_bytes`, `ceiling_source` included); `instance_id=None` spawns ⇒ **counter + log only, no event**; counters (exact ordered set): `ram_kills, ram_breaches_detected, ram_kill_refusals_p0 … ram_kill_refusals_p7, ram_overcounted, ram_rearms, ram_confirm_failures, ram_events_emitted, ram_events_dropped, ownership_unverified, tree_incomplete` (**no** `ram_kill_refusals_p8` — P8 never refuses); log prefix `ram-guard:`; NEW additive `GET /api/resources/ram-guard` carries counters + the guard health heartbeat (NOT in livez/readyz)  — **defensive behavior only** (§16.3): in v1 an instance-less spawn is **not registered with the guard at all** (the bash seam's registration block is inside `if instance_id is not None and proc is not None:`, `bash.py:952-1006`), so such a tree is invisible to the guard and only the unit cap covers it|
| DQ9 | Confirmed: entirely outside the T4 seam; `/control-capabilities` byte-identical; both fence tests green unmodified; env-off is the v1 disable |
| DQ10 | **(a) in-process + the event as the durable forensic record; NO migration lands in v1 at all** (`reap_reason` is plain TEXT — the "migration-backed extension" premise was stale). **AMENDED (approver blocker 3):** the event now has an **owner, an emitter and a bridge** (§4.5) — **Phase 2 owns the HOOK, Phase 5 owns the EMITTER + the wiring**; the bridge is **non-blocking** (`run_coroutine_threadsafe` + a done-callback) on the **hardened** `manager._get_event_loop()` — **not** `MainLoopBridge`, whose loop is `None` on a no-worker-pool boot. **Qualifying sentence (house debt, out of scope; §15 B22, verbatim):** the SQLite `event.kind` CHECK constraint lists only 8 kinds while `EventKind` has 16 — it is already stale for 8 pre-existing kinds; the production deployment is PostgreSQL (`create_all`; the SQLModel column carries no CHECK — `models.py:74`), so `ram_limit_exceeded` (the 17th member) is unaffected|

**R1 — resolved, and the premise corrected:** the live unit has **NO memory cap** (`MemoryMax=infinity` — the 7G/8G figures were a never-applied runbook recommendation), and the Oct-30 deaths were **kernel OOM kills** (`oom-kill`), not cgroup-cap kills. Ceiling is **ABSOLUTE per-command bytes**; the guard is the INNER preventive layer, the unit cap the OUTER layer covering the orphan class (the Oct-30 killing memory was largely orphaned children of a dead incarnation — invisible to any per-command guard); **the unit cap adapts to the ceiling, never the reverse**. **Shipped default AMENDED: 3 GiB (3072 MiB), not 8 GiB** — 8 GiB fires at-or-after daemon death on this 11.9 GiB host in both measured incidents; raise-path is arithmetic (see §3.3 of the recommendation). Operator routing: drop-in `30-memory-bounds.conf`, recommended `MemoryHigh=7G / MemoryMax=8700M`, canary via `systemctl set-property --runtime` — user action, NOT this lane's.

**Amendments to plan text that Phases 2–7 must honour:** the breach fires on the **CURRENT sample, not the peak** (a released spike is reported, never killed); `observed_peak_bytes` stays the RSS-based sampled peak while the **deciding** value is `confirmed_pss_bytes` (Success Criterion 3 re-aimed accordingly); the plan's anchors at `daemon/repositories/pid_registry/models.py`, the "bash lane ownership gate", "proc captures pgid at spawn", "read_pss_rollup exists", and `list_group_members` as the injectable primitive are all **falsified** — see `architecture-recommendation.md` §1.1.

---

## Configuration Surface (RULED — arch doc DQ7)

The knob names are fixed here so every phase doc refers to the same identifiers.

| Knob | Default | Bounds | Notes |
|---|---|---|---|
| `ENSEMBLE_RAM_GUARD` | **ON** | — | Bare `ENSEMBLE_*` master switch; reaper semantics (`0/false/no/off` case-insensitive = OFF). OFF = a **complete no-op**: no thread, no registration, no measurement, zero DB queries, one INFO line at lifespan start |
| `RAM_GUARD_CEILING_MIB` | **unset ⇒ DYNAMIC** (70 % of detected effective total RAM) | **[64, 65536]** — **explicit values ONLY** | INT-only (house rule: no human-string parsing). `Optional[int]`, default `None`. The derived value is `max(67108864, int(0.7 × effective_total_bytes))` — floored at 64 MiB, **never upper-capped**. **Detection failure ⇒ the fixed fallback `3221225472` (3072 MiB) + one WARN — never aborts boot** (B24) |
| `RAM_GUARD_POLL_INTERVAL_MS` | **200** | [50, 2000] | The guard thread's tick |
| `RAM_GUARD_GRACE_MS` | **250** | [0, 2000] | The **short** TERM→KILL grace — a **separate knob** from the 5 s timeout grace, and a **precondition of the 3 GiB ceiling** |
| `RAM_GUARD_PSS_CONFIRM_DEADLINE_MS` | **250** | [50, 2000] | The one-shot PSS confirm's deadline; expiry ⇒ **fail-open ⇒ kill** |

**Class:** `RamGuardConfig(BaseSettings)`, `env_prefix="RAM_GUARD_"`. The YAML block
is **comment-only** (pydantic init-kwargs beat env — `config.py:911-916`). Bounds are
validated **outside** the try/except at `load_config()` ⇒ an out-of-range **explicit**
env value **aborts boot** (house-approved) — **but only while the guard is ON**: the
bare switch is parsed **FIRST** and OFF short-circuits validation (B26, below).
**Depth/node caps — the two §4.1a re-arm constants — `RAM_GUARD_PENDING_MAX`,
`RAM_GUARD_GRACE_POLL_SLICE_SECONDS` and `WAIT_SLACK_MS` are module constants, not
knobs.**

**AMENDED (approver blocker 5) — the ROOT-`Config` wiring** (§4.6). The hole this
closes: `daemon/config.py` had **zero** `ram_guard` references, the root `Config`
aggregates **explicit fields only** (`config.py:6802-6814`), and `load_config` ends
with `config = Config(**config_dict)` (`config.py:8795`) ⇒ the whole env tier and
the boot-abort rung were **dead code**; only the kill-switch would have worked. The
root field, verbatim: `ram_guard: RamGuardConfig = Field(default_factory=RamGuardConfig)`,
mirroring `resource_monitor` (`config.py:6812-6814`). **NOT** `| None`: OFF is a
*value*, not an absence. `default_factory` **is** the env tier ⇒ `load_config` needs
**no** change, provided `config.yaml` carries **no** `ram_guard:` literal block.
The bare `ENSEMBLE_RAM_GUARD` switch is **not** a pydantic field (pydantic would
raise on `""`/unknown values and abort boot, violating the reaper's "explicit
anything else ⇒ ON") — it is parsed by a dedicated function and carried on the root
`Config` as a **derived** value.

**OFF BEATS VALIDATION (B26, verbatim):** `load_config parses ENSEMBLE_RAM_GUARD FIRST; when OFF it passes config_dict["ram_guard"] = RamGuardConfig.model_construct(ceiling_mib=None, poll_interval_ms=200, grace_ms=250, pss_confirm_deadline_ms=250)`
— `model_construct` bypasses both validation and the settings sources, so **no
`ge`/`le` check runs while OFF** (env or yaml). When ON, construction is normal
(env tier + validation + abort on out-of-range). Enabling later with the bad value
still set aborts at that boot — a misconfigured guard cannot be enabled silently.

**The module constants (NOT knobs — §4.1a), verbatim:**
`RAM_GUARD_REARM_MAX_EXTRA_BYTES = 33554432` · `RAM_GUARD_REARM_MIN_GROWTH_BYTES = 134217728`.

**The message clause (§15 B10, verbatim):**
```
The enforced trigger threshold was {threshold_mib} MiB (the resolved ceiling is {limit_mib} MiB); when the summed-RSS metric over-counts shared pages the threshold is re-armed above the ceiling and the kill decision is always made on the shared-page total.
```

**The endpoint heartbeat (§15 B23, verbatim — the SINGLE eleven-field pin, superseding every six- or nine-field variant):**
```
enabled, thread_alive, last_tick_at, missed_samples, ticks, registry_depth, lane_alive, lane_pending, last_lane_progress_at, resolved_ceiling_bytes, ceiling_source
```

**The dynamic-default contract (§15 B24, verbatim):**
```
detect_effective_total_bytes(proc_root: Path, cgroup_root: Path) -> tuple[int, str]
resolved_ceiling_bytes = max(67108864, int(0.7 * effective_total_bytes))
effective_total_bytes = min(MemTotal_bytes, cgroup_limit_bytes) when the cgroup limit is finite, else MemTotal_bytes
ceiling_mib unset ⇒ derived; explicit ⇒ validated [64, 65536] — the clamp NEVER applies to the derived value
detection failure ⇒ resolved_ceiling_bytes = 3221225472 (3072 MiB) + one WARN; never aborts boot
limit_bytes = the RESOLVED ceiling (derived or explicit)
```

**The config wiring (§15 B8), verbatim:**

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

**The assertion forms (§15 B9), verbatim:**

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

**Runtime injection:** `ceiling_bytes` is a parameter of the guard's `register()`;
the config value is the default. **The per-call override is an internal/test seam
only — the LLM-facing bash/proc tool schemas gain NO ceiling parameter** (an
agent-visible knob would let the agent raise its own ceiling).

---

## Critical Interaction — RESOLVED in Phase 1

**The per-command ceiling and the daemon-unit cap are NOT independent numbers.** The
architect resolved this (arch doc §3); the premise underneath it was also
**corrected**: the live unit has **NO memory cap at all**.

| Quantity | Value | Source |
|---|---|---|
| Live unit `MemoryHigh` / `MemoryMax` | **`infinity` / `infinity`** — **no cap exists** | `systemctl show`; no `Memory*` directive in the unit or its drop-ins (E1–E2) |
| Daemon cgroup band (measured) | **2.69 → 3.31 GiB** across the analysis window | E1 |
| Host RAM / swap | **11 952 MiB RAM (~11.67 GiB)** / 8 191 MiB swap | E4 |
| Co-residents (PG 0.28 + demo 0.4 + OD 0.17 + OS ≈ 0.94) | ≈ **1.79 GiB** | E4 |
| **Shipped per-command ceiling** | **DYNAMIC — 70 % of the detected effective total RAM** (USER RULING #2, §4.7); on this host **8,367 MiB = 8.171 GiB** | arch doc §3.3a, §4.7, E11 |
| Recommended unit cap (operator-gated) | **`MemoryHigh=7G` / `MemoryMax=8700M` (8.50 GiB)** | arch doc §3.4 |
| Oct-30 incidents | **kernel `oom-kill`** (not cgroup-cap kills), unit peak 7.3 GiB | E3 |

**The three rulings (arch doc §3.1–§3.3):**

1. **ABSOLUTE per-command bytes** — `effective_ceiling = RAM_GUARD_CEILING_MIB ×
   1 048 576`, compared against the ruling metric. Relative/dynamic ceilings were
   rejected on five jointly-cited grounds (not computable today — `memory.max =
   max`; algebraically a constant; a self-raising hazard against the Oct-17 leak;
   a ~2.9 GiB scale mismatch — 74 % of `memory.current` is reclaimable page cache;
   and untestable without an operator host change).
2. **Layering** — the guard is the **INNER, preventive** layer (live in-flight
   trees); the unit cap is the **OUTER, containment** layer (daemon-internal leaks,
   aggregate children, and **orphans across restarts**); the kernel global OOM is
   the **LAST** layer and is today the only one actually enforcing anything. **The
   unit cap adapts to the ceiling, never the reverse.**
3. **The default is DYNAMIC (USER RULING #2) — the 3 GiB figure is DEMOTED to
   operator guidance.** With `RAM_GUARD_CEILING_MIB` **unset**, the ceiling is
   **70 % of the detected effective total RAM** (the daemon's cgroup limit when
   finite, else host `MemTotal`), **resolved once at boot**; an explicit value
   overrides and is validated `[64, 65536]` — **the clamp never applies to the
   derived value** (§4.7c). On this host the derived default is **8,367 MiB
   (8.171 GiB)** (E11). USER RULING #1's *intent* (disaster prevention, not a
   constraint on legitimate heavy work) is binding and is carried by the
   no-upper-clamp rule. **The on-this-host disclosure is RETAINED and user-accepted:**
   at the derived default the guard fires **at-or-after daemon death** on this
   11.9 GiB host, and the operator levers are the knob and the host resize.

**Coupling to DQ4 (hard precondition):** every second of (poll + grace) at 1 GiB/s
costs 1 GiB of ceiling. With the inherited 5 s grace, `C_max` collapses to
**0.68 GiB**; with the ruled ladder, the **corrected overshoot law** (the confirm
deadline is on the critical path) is `Ω_typ = R × (poll 0.200 + confirm 0.250) = R × 0.45 s`
and `Ω_worst = R × (poll 0.200 + confirm 0.250 + grace 0.250) = R × 0.70 s`. **The
short-grace ladder is a precondition of the ceiling, not a nicety.**

**AMENDED (approver blocker 2) — the §3.4 frame is RE-DONE with the bounded
numbers.** The unbounded re-arm rule made the effective kill threshold ≈4.825 GiB
and the unit-cap floor ≈9.825 GiB > the 8.88 GiB box ceiling ⇒ **no unit cap could
exist on the re-arm path at any allocation rate**, and *"the guard always completes
before a unit kill"* was false. The **bounded** re-arm restores a feasible cap:

```
F_max(R) = C_ceiling + A + Ω(R)                     (the tree's REAL footprint at the moment of the kill)
A        = RAM_GUARD_REARM_MAX_EXTRA_BYTES + RAM_GUARD_REARM_MIN_GROWTH_BYTES   (= 160 MiB = 0.15625 GiB)
floor(R) = D_max + F_max(R) + O + M_c               (D_max 3.5 worst · O 1.0 worst · M_c 0.5)
         = 8.15625 GiB + Ω(R)
box      = MemTotal 11.67 − others 1.79 − M_k 1.0   = 8.88 GiB  ⇒  X ≤ 8.88 GiB
```

| Rule | `A` | `F_max` @ R=1 | floor @ R=1 (Ω_worst) | A feasible unit cap? | Guard wins the race at the published pair? |
|---|---|---|---|---|---|
| Unbounded (**WITHDRAWN**) | 1.825 GiB | 4.825 GiB | **9.825 GiB** | **NO** — floor > 8.88 at every R | never |
| **BOUNDED (binding)** | **0.15625 GiB (160 MiB)** | 3.85625 GiB | **8.85625 GiB** | **YES** — R ≤ **1.034 GiB/s** (worst frame) / R ≤ 1.608 GiB/s (typical frame) | only for R ≤ **0.485 GiB/s** at `8700M` (worst frame) / R ≤ 0.755 GiB/s (typical frame) |
| **BOUNDED, contended N=2 (disclosed, iteration 002)** | 0.15625 GiB | 4.36625 GiB | **9.36625 GiB** | **ONLY for R ≤ 0.598 GiB/s** — the window is **EMPTY above that** | only for R ≤ **0.28 GiB/s** at `8700M` |

**The contended companion (N simultaneous breaches, iteration 002):** with the execution
lane servicing one breach at a time, the Nth tree's confirm waits behind N−1 services:

```
Ω_contended(N) = R × (poll 0.200 + (N−1)·T_service_worst + confirm 0.250 + grace 0.250)
T_service_worst = confirm_deadline 0.250 + grace 0.250 + probe ≈ 0.510 s
Ω_contended(2) = R × 1.21 s
```

Both the single-tree and the contended forms appear in the §3.5 honesty set and the
operator memo. At R = 1 GiB/s: Ω_worst ≈ 0.70 GiB; Ω_contended(2) ≈ 1.21 GiB.

**The N=2 row is a DISCLOSURE, not a design target** (iteration 002): two
simultaneous full-ceiling breaches ≈ 6 GiB of trees + a 3.5 GiB daemon ≈ 9.5 GiB of
unit demand on an 11.67 GiB box — that scenario is already past what per-tree
arithmetic rescues; the kernel OOM is the residual layer. **The published pair is a
single-tree claim and the memo must say so.**

**The general feasibility bound at the DYNAMIC default (C = 0.7·T), escalation pass:**

| Rule | `A` | `F_max` | floor | A feasible unit cap? |
|---|---|---|---|---|
| **General feasibility at the default** (C = 0.7·T) | 0.7·T | 0.7·T + 5.856 | **iff 0.7T + 5.156 + 0.70R ≤ T − 2.79 ⇔ T ≥ 26.5 + 2.33·R GiB** (≥ ~28.8 GiB at R = 1 GiB/s) |

**The memo's unit-cap guidance (re-derived):** at the 70 % default a feasible unit
cap exists only on hosts ≥ ~26.5 GiB (R = 0) / ~28.8 GiB (R = 1). On smaller hosts
the operator picks: **(a) ship without a unit cap** — the kernel global OOM is the
outer layer (today's state; disclosed, not hidden); **(b) lower the knob** — an
explicit `RAM_GUARD_CEILING_MIB` below the derived default; **(c) resize the host**.
**The 3 GiB evidence is demoted to operator guidance**, not to a default.

**The §3.4 truth statement (replacing the withdrawn "the guard always completes
before a unit kill" claim) — verbatim (arch doc §3.4):**

> "The guard completes before the unit cap fires **only while the tree's allocation rate R stays at or below ≈0.49 GiB/s** at the published pair `8700M` (worst frame; ≈0.76 GiB/s in the typical TERM-honouring frame; **≈0.28 GiB/s if a second tree is simultaneously breaching**). The measured band is **1–3 GiB/s** — so **above ≈0.5 GiB/s the cgroup cap fires first and the daemon, not the command, is the casualty** (the cap is then the containment layer, not the guard's backstop). For fork-heavy trees the same holds a fortiori: the kill decision waits on a PSS confirm whose cost scales with the tree's RSS. **'The guard always completes before a unit kill' must not appear in the memo, the runbook, or any acceptance criterion.** Restoring the layering requires a host of ≥ ~14.5 GiB, not a different ceiling."

**AMENDED (§3.3) — the raise-path is VOID on this host:** at C = 4 GiB the
unit floor is `D_max 3.5 + 4.0 + O 1.0 + M_c 0.5 = 9.0 GiB > 8.88 GiB` ⇒ the
X-window is **empty** and no unit cap can exist. Under the **dynamic** default the
generalized form applies instead: the preventive layering needs
**T ≥ ~26.5 + 2.33·R GiB** (§3.4's general bound), so the **knob** and the **host
resize** are the levers — there is no single shipped figure to raise.

**The interaction table** lives in arch doc §3.4 and is **re-published in the Phase 7
operator memo with the MEASURED R** (Phase 2 T19's obligation) — the arch doc's
table is computed at R = 1 GiB/s, so every rate-conditional number in it is
provisional until R lands.

---

## Research Insights (research findings that shaped this plan)

All anchors below were re-verified in the worktree by this worker.

1. **The bash lane has exactly ONE lifetime await** — `asyncio.wait_for(proc.wait(),
   timeout)` at `bash.py:1062-1065`. That single line is where the guard's verdict
   must be observed, and where a watchdog must race. Everything else in the bash
   tool is setup or teardown.
2. **`start_new_session=True`** (`bash.py:848-850`, `proc_tools.py:1179-1181`) ⇒
   **PGID == PID** for the direct child and the child is a new **session + process
   group**. Group kill therefore reaches non-setsid descendants. The `setsid`
   escapee gap is real and documented in both lanes (`bash.py:590-600`,
   `proc_tools.py:1677-1683`).
3. **The bash tool's `peak_rss_kb` is an honest UNDER-REPORT** — the post-wait
   `VmHWM` read almost always misses because `wait()` reaps the child first
   (`bash.py:1096-1109`). A true peak requires pre-reap polling — which is exactly
   what the RAM guard supplies. The new error's peak must come from the guard.
4. **The proc tool writes NO durable registry row** — only `bash.py:414` and
   `mcp/stdio_wrapper.py:783` touch `pid_registry`; `proc_run` touches neither the
   `bash_spawn_registry` nor `pid_registry`. Covering the proc lane under a durable
   substrate is a NEW write path.
5. **The proc tool already has a per-process watchdog precedent** — `info.timeout_task`
   is created alongside the reader and exit tasks (`proc_tools.py:1231-1245`),
   cancelled in the same cleanup tuple (`:1296-1301`). **SUPERSEDED by leader ruling
   Q1:** the RAM watchdog does **NOT** slot in there — the proc lane creates **no**
   fourth task; the **guard thread** owns the measurement, the breach decision, and
   the kill. The proc lane only registers, maps the verdict to status, and releases.
6. **A fail-closed tree enumerator already exists** —
   `bash_spawn_reaper.list_group_members:382-460` returns `None` on enumeration
   failure (vs `[]` for "group is dead"), excludes zombies and kernel threads, and
   is Linux-`/proc`-based with a documented macOS `ps` fallback.
7. **A tree-RAM reader seam already exists and is injectable** —
   `proc_readers.read_child_snapshot(proc_root, pid)` (`:737-758`) reads
   `VmRSS` from `/proc/<pid>/status` with an injectable `proc_root`
   (`:16-18`) ⇒ hermetic fixtures, no host reads.
8. **No per-spawn cgroup isolation exists** — agent shells are daemon children
   inside the daemon's systemd cgroup; `start_new_session` changes the session, not
   the cgroup; `systemd-run` is unavailable (standing constraint); there is **no**
   `rlimit`/`setrlimit` anywhere in `daemon/`. Hence DQ1(c) is unavailable without
   new infrastructure.
9. **The resource monitor is a THREAD, never asyncio** — one `threading.Thread`
   sampler + one flusher thread (`sampler.py:291-303`, `flusher.py:208-220`); the
   facade documents the posture (`:9-12`); the sampler **never touches the DB**
   (ring only, `sampler.py:14`); the flusher drains in **ONE** `engine.begin()` per
   window (`flusher.py:371-372`). The wedge-capture watchdog docstring is the
   canonical "dedicated thread, never a coroutine" precedent
   (`daemon/services/wedge_capture/watchdog.py:1-49`), including its two hard rules:
   **the tick path never logs** (a held logger lock could deadlock the watchdog)
   and **the lock is never held across a capture step**.
10. **The house kill-switch precedent is exactly what the dispatcher named** —
    `ENSEMBLE_BASH_ORPHAN_REAPER` (`bash_spawn_reaper.py:32-60`): default ON in all
    lanes when absent; explicit `0/false/no/off` (case-insensitive) = OFF
    everywhere; **OFF is a COMPLETE no-op** (no stamps, no writes, no sweeps, **zero
    DB queries**); one INFO line at boot. `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES`
    (`boot_subprocess_sweep.py:120-135`) is the sibling precedent
    (`DEFAULT_ENABLED = True`, `_DISABLE_VALUES` frozenset, grace clamped
    [0.5, 30.0]).
    **Not cited as a precedent:** `ENSEMBLE_REPORT_DELIVERY_RECOVERY_LANE_STUCK_WAKE`
    — it exists only in a config help-string (`config.py:2115`); the real mechanism
    is constructor kwargs.
11. **The yaml-literal trap is real and documented** — pydantic-settings
    init-kwargs beat env vars (`config.py:911-916`), so a literal in
    `config.yaml`'s `resource_monitor:` block silently kills the env tier. Any new
    config class's YAML block must stay **comment-only**.
12. **Boot-abort-on-bad-env is house-approved** — bounds are validated at
    `load_config()` **outside** the try/except (`api.py:2266-2278`), so an
    out-of-range env value aborts boot rather than silently clamping.
13. **The current tree's PG ensure list is NOT one transaction** (Dispatcher Ruling
    (b), verified against this tree): `_ensure_postgres_columns`
    (`daemon/manager.py:8396+`) uses **many separate `engine.begin()` blocks**
    (`:10036`, `:10073`, `:10164`, `:10282`, `:10375`, `:10583`), with a per-statement
    txn in the terminal_reason backfill (`:10162-10192`) and a fail-open checkpoint
    block (`:10575-10634`) — but there is still an **unguarded call site**
    (`:1057-1058`) where an exception aborts boot. Order is not containment.
    **Consequence for this plan:** a NEW table is **brand-new ⇒ NO ensure-list
    mirror** (the `bash_spawn_registry` precedent, `manager.py:1095-1099` boot
    order: registration → `create_all` → SQLite runner → ensure). The RAM guard's
    v1 substrate is RAM-only, so it needs **no migration at all** — a migration
    appears only if DQ10 rules for durable verdicts (P7).
14. **The existing "explicit OFF is byte-identical" test idiom already exists** —
    `tests/tools/test_bash_spawn_stamp.py` has `explicit_off_byte_identical_env_no_row`
    and `no_reaper_env` fixtures, plus a `_EnvCapture` spy on
    `asyncio.create_subprocess_shell`. P6 mirrors this idiom rather than inventing one.
15. **The fence that constrains DQ5(c)** — `tests/test_resource_control_seam.py`
    `:296-346` greps `daemon/` for `action_by`/`acted_at` and for the
    `resource_pid_map` string near the word `action`. Writing the RAM-kill verdict
    into those dormant columns **breaks the suite** (see the Fence section).
16. **Test fencing idioms in force** — `tests/tools/conftest.py` has an autouse
    `_reap_spawned_subprocesses` fixture (`:196-207`) that SIGKILLs leftover spawned
    pids after every test in that directory; `tests/unit/services/test_bash_spawn_reaper.py`
    is a **pure-predicate** pack with a `kill_fence` fixture that hard-raises on a
    real `os.killpg` — the right shape for the guard's kill-ladder tests.
17. **House fencing hazards that apply to THIS feature's tests** (must be baked
    into the harness, not rediscovered): pin `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0`
    **explicitly** (an `env -u` on it strips the ambient guard and the sweep kills
    live processes); scrub `POSTGRES_*` **per variable** (`env -u POSTGRES_*` is a
    silent NO-OP — bash does not glob env names); use a `mktemp` **FAKE_HOME** with
    an escape guard (never a live-shaped `HOME`-derived path); scrub the **full**
    `ENSEMBLE_AUTH_*` family **or none** (a partial scrub breaks collection).

---

## Assumptions (explicit)

1. **The enforcement target host is LINUX.** Per Dispatcher Ruling (a): the
   motivating incidents are systemd OOM restarts, and per-child attribution
   (`/proc/<pid>/environ` ownership verification) is Linux-only in the current code.
   **Darwin is graceful non-enforcement** (feature inert / observe-only), NOT the
   reverse. The plan must not assume macOS parity.
2. **The daemon's own consumption is NOT targetable by this feature.** The guard
   never signals the daemon, its own process group, `pgid == 1`, or `pgid == self`.
3. **The default ceiling is DYNAMIC — 70 % of the detected effective total RAM**
   (USER RULING #2, §4.7), resolved once at boot; an explicit
   `RAM_GUARD_CEILING_MIB` overrides and is validated `[64, 65536]` — **the clamp
   applies to explicit values ONLY**, and the derived value is **never
   upper-capped** (USER RULING #1's binding intent: disaster prevention, not a
   constraint on legitimate heavy work). The previously-shipped fixed figure
   (3 GiB) is **demoted to operator guidance**; the commissioned 8 GB is
   **superseded in its fixed figure** with its intent retained. On this host the
   derived default is **8,367 MiB (8.171 GiB)** (E11). **Detection failure ⇒ the
   fixed fallback `3221225472` (3072 MiB) + one WARN — never aborts boot.**
4. **The RAM guard is a NEW port**, not a T4 policy row, and does not read or write
   `resource_limit_policy` or the `resource_pid_map` dormant columns.
5. **`bash` and `proc` are separate lanes** with no shared process-management code;
   the ONLY shared artifact is the tree-RAM reader (P2).
6. **The resource monitor's 30 s sampler is not modified** and is not the enforcement
   path; the two subsystems may observe the same processes.
7. **The reported usage is a metric-defined quantity**, not "memory the command
   used". Under the ruled hybrid there are **two distinct quantities**:
   `observed_peak_bytes` (the RSS-based **sampled lower bound**) and
   `confirmed_pss_bytes` (the **deciding** value). The envelope must say which
   metric each number is, and the message must carry the lower-bound label
   (`peak_is_lower_bound`, `under_report_bound_bytes`). **AMENDED (§4.1a):** a third
   number, `rearm_threshold_bytes`, carries the **enforced trigger threshold**
   (the configured ceiling stays `limit_bytes`; `0` means no re-arm was armed).
8. **The kill-switch OFF path is a complete no-op** — no measurement, no registry
   entry, no event, no counter change, no observable difference in the child's
   environment or the returned output, **and no thread at all**.
9. **No new third-party dependency.** The tree reads are raw `/proc` (the house
   posture: `proc_readers.py:6-15` — *no psutil*).
10. **The proc lane's `MAX_PROCESSES_PER_INSTANCE = 10`** (`proc_tools.py:98`) and
    the bash lane's 1.5 s bounded durable write are unchanged by this feature.
11. **AMENDED — the proc lane's verdict is QUERY-ONLY by construction.** `proc_run`
    returns before any breach can exist, so the typed `ram_limit_exceeded` envelope
    is delivered by the **bash lane's return string only**. The proc lane surfaces
    the verdict via `proc_status` as a `ram_breach:` line plus a flat
    `ram_killed: true`. **The commission's phrase "returns … error" is scoped to the
    bash lane.** Three compensating disclosures are mandatory (§3.5 item 12).
12. **AMENDED — the durable event is the ONLY cross-restart forensic artifact.**
    The verdict record is RAM-only; a **dropped** event (no live loop / loop closed /
    daemon shutdown) is a permanent loss of that artifact — mitigated by
    `ram_events_dropped` and the transition log line (§4.5).
13. **AMENDED — the daemon is not the reaper of a killed tree's grandchildren.**
    There is **no** subreaper in `daemon/`; the guard kills a GROUP and init/PID 1
    reaps the re-parented grandchildren (§3.5 item 13).
14. **AMENDED (iteration 002, §16.3) — an instance-less spawn (`instance_id is None`)
    is NOT registered with the guard in v1.** The bash seam's registration block is
    inside `if instance_id is not None and proc is not None:` (`bash.py:952-1006`);
    widening that gate is a restructure this commission does not name. Such a tree is
    **invisible to the guard** and only the unit cap covers it (§3.5 item 9b);
    widening the gate is a **routed follow-up**. DQ8's `instance_id=None ⇒ counter +
    log only` sentence is **defensive behavior only**, not a v1 bash behavior.

---

## Open Questions — RESOLVED in Phase 1 (two items remain)

All ten design questions and the Critical Interaction were **adjudicated and are now
binding** (arch doc §2 and §3; the one-line rulings are in the "Phase 1 Rulings"
block above). They are restated here as a closure ledger so the dispatch can see
what was decided and where.

| # | Question | Resolution | Where |
|---|---|---|---|
| DQ1 | summed RSS vs PSS vs cgroup, and the trigger/confirm hybrid | **(e) hybrid; the PSS confirm is MANDATORY** (the deciding metric) | arch doc §2 |
| DQ2 | dedicated thread(s) vs in-execution task, and the poll interval | **AMENDED — (b) TWO dedicated daemon threads** (tick + execution lane; §15 B16 verbatim), poll 200 ms [50, 2000], `RAM_GUARD_PENDING_MAX = 2` | arch doc §2, §16.1 |
| DQ3 | pgid enumeration vs a tree walk; setsid coverage | **(c) hybrid from ONE shared `/proc` scan per tick** (group ∪ bounded descendant closure) | arch doc §2 |
| DQ4 | the ladder shape, the grace length, the new vocabulary | **(d) NEW sync short-grace ladder**; grace 250 ms [0, 2000]; `exit_kind="ram_killed"`; **AMENDED** — Q2=(i) leaderless semantics, the **16-code** vocabulary, the pre-kill gate is **P0–P7** (P8 = post-kill `killed:tree-incomplete`) | arch doc §2, §4.2 |
| DQ5 | the substrate, and the proc lane's missing registry write path | **(a) RAM-only**; the proc lane registers with the guard only — **no durable write path** | arch doc §2 |
| DQ6 | the exact envelope shape | Envelope **is** the bash return; the proc verdict rides `proc_status`; **AMENDED — the §6 consumer-count figure is withdrawn**; the gate artifact is the **§6 enumerated census**, owned by **Phase 2** | arch doc §2, §6 |
| DQ7 | the knob family, and how the ceiling is threaded | `ENSEMBLE_RAM_GUARD` + `RamGuardConfig(env_prefix="RAM_GUARD_")`; the per-call override is an **internal/test seam only**; **AMENDED** — wired into the **root `Config`** (§4.6); the re-arm constants are **module constants** | arch doc §2, §4.6 |
| DQ8 | the event kind, the counters, the log prefix | `EventKind.RAM_LIMIT_EXCEEDED`; the payload = the envelope key set + four event-only keys; the **exact counter set** (`ram_kill_refusals_p0 … p7`, **no p8**); prefix **`ram-guard:`**; **NEW** `GET /api/resources/ram-guard`; **AMENDED** — the event has an owner/emitter/bridge (§4.5) | arch doc §2, §4.5 |
| DQ9 | P1 vs the T4 seam | **Confirmed: entirely outside** it; `/control-capabilities` byte-identical | arch doc §2 |
| DQ10 | in-process vs durable verdicts | **(a) in-process + the event as the durable forensic record; NO migration in v1** | arch doc §2 |
| R1 | the unit-cap/ceiling relationship | **ABSOLUTE per-command bytes**; guard-inner / unit-cap-outer; **the default is DYNAMIC** (70 % of the detected effective total RAM — USER RULING #2, §4.7; the 3 GiB figure is demoted to operator guidance) | arch doc §3, §3.3a, §4.7 |

**Two items remain genuinely open — both are operator/user calls, not architect
calls:**

1. **The unit-cap drop-in is a user action** (`7G / 8700M` recommended; arch doc
   §3.4). This lane does not execute it. Without it, the guard is the **only**
   enforcement on the box and the **orphan class is uncovered** (proven Oct-30).
2. ~~**The 3-vs-8 GiB user confirmation** — the shipped default **amends the
   commissioned 8 GB**.~~ **DISCHARGED (USER RULINGS #1 + #2, escalation pass):** the
   default is now **DYNAMIC** (70 % of the detected effective total RAM, §4.7);
   USER RULING #1's *intent* is carried by the no-upper-clamp rule; the derived-default
   regime, the general feasibility bound (`T ≥ 26.5 + 2.33·R GiB`) and the three
   operator levers (no-cap / lower-the-knob / resize) are carried into the final report
   and the Phase 7 operator memo (pre-freeze gate (b), phase2 T21 / phase7 T12).
