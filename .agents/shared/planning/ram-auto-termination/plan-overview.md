# Plan Overview: RAM-Based Auto-Termination for Agent-Spawned Shell Processes

Date: 2026-11-10 (planned from worktree base `0efdf106f3f66ccb400405f4137694592b72097d`, branch `feature/ram-auto-termination`, worktree `/home/nea/ensemble-src-wt-ram-auto-termination`)
Author: plan-creation worker (research by three partitioned explorers; all anchors re-verified against the worktree by this worker)
Status: **Phase 1 gate CLOSED** — architect adjudication COMPLETE (10/10 DQ rulings + R1 + sequencing verdict). Binding rulings live in the "Phase 1 Rulings" block below and in `architecture-recommendation.md`; the phase docs are rev 2 and reconcile against them.

> **rev 2 — the committed rev 1 body is superseded by this revision** (council cycle 1).
> Vehicle: **binding per-file edit list** (binding-order headers below). Where this
> document and `architecture-recommendation.md` disagree, **that document wins**.
> The architect's "Phase 1 Rulings" block is preserved **verbatim**.

## Applied edit list (binding order)

| Order | Edit | Class |
|---|---|---|
| 1 | Header: **gate closed** (was "blocked at Phase 1") | Class 3 |
| 2 | Objective + Scope: the shipped default is **3 GiB / 3 072 MiB**, not the commissioned 8 GiB (leader-accepted amendment) | Leader ruling |
| 3 | Routed follow-up 1: the unit-cap premise is **falsified** — **no cap exists today**; the recommendation is **7G / 8700M** (§3.4) | Class 3 |
| 4 | Phases table: P2 **absorbs the envelope helper + the consumer gate**; P5 **shrinks**; P7 **shrinks and re-weights** to the operator memo | Class 3 |
| 5 | Risk 1: rewritten against the **falsified** unit-cap premise | Class 3 |
| 6 | Risk 5: the **ownership-gate claim is falsified** (P3/P4) — the reaper's fail-closed 5-rule gate is not `proc_tools`' fail-open 1-signal check, and the bash lane has **no** ownership gate | Class 3 |
| 7 | Risk 7: the DQ6 gate is **arch doc §6's 22-consumer list**, not the chart-tools pack | Class 3 |
| 8 | Risk 8 + SC7: the 3 GiB default | Leader ruling |
| 9 | **SC3 re-aimed** at the **deciding value** | Class 2 |
| 10 | SC13 re-aimed at the §3.4 reality | Class 3 |
| 11 | Critical Interaction: rewritten as **RESOLVED** (absolute bytes / guard-inner / unit-cap-outer / 3 GiB) | Class 3 |
| 12 | Assumptions 3 + 7 re-aimed | Leader ruling |
| 13 | Open Questions → **Resolved in Phase 1**, with the two items that remain genuinely open | Class 3 |
| 14 | Knob names named in the plan body: `ENSEMBLE_RAM_GUARD` + `RAM_GUARD_*` (`CEILING_MIB` 3072, `POLL_INTERVAL_MS` 200, `GRACE_MS` 250, `PSS_CONFIRM_DEADLINE_MS` 250) | Class 3 |

---

## Objective

When a bash-tool or proc-tool execution's process tree exceeds a configurable RAM
ceiling (**default 3 GiB / 3 072 MiB = 3 221 225 472 bytes**), the daemon kills the
offending process tree promptly and returns a clear, honest, typed error to the
calling agent naming the **deciding** observed usage — preventing runaway agent
commands from ballooning the host and OOM-killing the daemon unit.

> **The default is AMENDED from the commissioned 8 GB** (leader-accepted). The
> commissioned number is **unshippable on this host**: at fire time box demand
> ≈ 4.79 residents + 8.0 = 12.79 GiB > 11.67 physical, and it fires
> **at-or-after daemon death** in both measured Oct-30 incidents. 8 GiB becomes
> correct on a ≥ ~14.5 GiB host. See the "Phase 1 Rulings" block and
> `architecture-recommendation.md` §3.3.

Testable completion sentence: an agent command whose process tree allocates past the
configured ceiling is terminated (tree-wide, not just the direct child), the agent
receives a typed `ram_limit_exceeded` error whose **deciding value** clears the
ceiling, and the daemon survives with no orphaned descendants — while every command
under the ceiling, and every lane with the kill-switch OFF, behaves byte-identically
to today.

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
2. **A configurable ceiling**, default **3 GiB / 3 072 MiB** (leader-accepted
   amendment of the commissioned 8 GB), in house-style knobs, with a
   **kill-switch default-ON** (`ENSEMBLE_RAM_GUARD`) and documented env-off
   (commission requirement; see DQ7/DQ9 and Dispatcher Ruling (c)).
3. **Fast-breach detection** — a poll interval chosen as an explicit
   latency/overhead trade-off (DQ2); **RULED: one dedicated daemon thread**, poll
   **200 ms** [50, 2000].
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
| 1 | Design Decision Gate | Architect returns 10 binding rulings (DQ1–DQ10) + the metric contract + the kill ladder, before any code | 10 | gate — blocks Phases 2–7 | **CLOSED** — 10/10 ruled (arch doc) |
| 2 | Measurement Substrate | The reader (ONE shared scan/tick, injectable `proc_root`) + the **PSS confirm** (the deciding metric) + the OVERCOUNTED re-arm + the **envelope helper + the consumer-gate artifact** + the guard thread + the kill ladder | 22 | tight with 3, 4 | pending |
| 3 | Bash Lane Enforcement | bash executions breach → tree killed, the envelope returned, the **one-shot `mark_exit` ordering** honored, the durable row left **OPEN** | 14 | tight with 2, 5 | pending |
| 4 | Proc Lane Enforcement | proc executions breach → tree killed **by the guard thread** (**no 4th task** — leader ruling Q1), the verdict riding `proc_status` | 11 | tight with 2, 5 | pending |
| 5 | Config + Observability | The `RAM_GUARD_*` knob family + the event + the counters + the `ram-guard:` log + the **NEW** `GET /api/resources/ram-guard` + the T4 fence re-run | 15 | tight with 2, 3, 4 | pending |
| 6 | Bounded Test Suite | Real ballooning trees under small injected ceilings; kill-switch byte-identical; the §8 mandated additions; mutation-proof | 21 | tight with 2, 3, 4, 5 | pending |
| 7 | Durability + Operator Memo + Follow-up Routing | The **operator memo** `docs/runbooks/ram-guard-and-unit-bounds.md` (PRIMARY) + the §3.5 honesty set + the follow-up queue + the T4 hand-off note — **no migration** | 8 | loose with 3, 4 (parallel) | pending |

**Phase 1 is an explicit gate — and it is now CLOSED.** No code was written before
the architect returned the DQ rulings. The gate's outcome reshaped three phases:
**P2 grew** (it absorbs the envelope producer, the consumer gate, the PSS confirm
and the guard thread), **P5 shrank** to config + observability + the fence re-run,
and **P7 shrank and re-weighted** to the operator memo (no migration lands in v1).

---

## Coupling Map

| | P1 | P2 | P3 | P4 | P5 | P6 | P7 |
|---|---|---|---|---|---|---|---|
| **P1** Design Gate | — | tight (P2 implements DQ1+DQ3) | tight (P3 implements DQ2+DQ4) | tight (P4 implements DQ2+DQ4) | tight (P5 implements DQ6+DQ7) | loose | loose |
| **P2** Measurement | tight | — | tight (shared reader) | tight (shared reader) | independent | tight | independent |
| **P3** Bash Lane | tight | tight | — | independent (separate lanes) | tight (error + knobs) | tight | loose |
| **P4** Proc Lane | tight | tight | independent | — | tight (error + knobs) | tight | loose |
| **P5** Contract/Config | tight | independent | tight | tight | — | tight | independent |
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
| 5 | **Killing the wrong tree** — PID recycling, or a `setsid` escapee that re-parents outside the group, could redirect the kill | **High** | Low | The guard supplies its **own** identity gate — the DQ4 ladder's **P0–P8** predicates (captured pgid + leader `starttime` re-verification, **fail-closed**; the environ check is **P6, secondary only**). **FALSIFIED (arch doc §1.1 P3/P4):** the previous text conflated two different mechanisms — the reaper's real gate is `verify_and_kill` (`bash_spawn_reaper.py:735-900`, 5-rule, DB-row-backed, fail-closed, starttime gate `:851-857`), whereas `proc_tools.py:1037-1053` is a **different, fail-open** 1-signal environ check; and the **bash lane has NO ownership gate at all** (`_kill_process`, `bash.py:697-745`, sends `killpg` with no environ/starttime check). This plan does **not** claim the bash lane's pre-existing path is gated |
| 6 | **Grace window defeats the purpose** — the existing bash ladder is SIGTERM → **5 s** → SIGKILL; a ballooning process keeps allocating for those 5 s and can push the unit past `MemoryMax` before the KILL lands | **High** | Medium | DQ4 framing: a RAM emergency needs a SHORT grace (the reaper's slice-probe ladder is the sync-friendly precedent). Make the grace a separate, shorter knob than the timeout grace. |
| 7 | **Tool contract break** — the agent-visible error string changes shape; a downstream consumer may assume the current form | Medium | Low | **The gate is SATISFIED by evidence, not by ritual:** `architecture-recommendation.md` §6 lists **22 verified consumers, zero break**. **FALSIFIED (arch doc §1.1 P6):** `tests/test_chart_tools_legacy_error_contract.py` pins **chart tools only** (58 lines; imports neither bash nor proc) — it was the **wrong file**. The real pin surface is the `proc_status` substring assertions (`tests/tools/test_proc_tools.py:240, 257, 382, 1163, 1166-1167, 1831`) and the `ProcessInfo.status == "killed"` attribute pins — satisfied by *adding* lines and keeping `status="killed"`. Four strings must never change (bash success, bash timeout, the `proc_status` block, the `proc_list` header) and are pinned by the Phase 6 suite |
| 8 | **Test host OOM** — the harness genuinely allocates RAM; a mis-sized test could OOM the CI/dev box | **High** | Medium | P6 mandate: ceilings are **small and injected** (MiB-scale), the allocator sizes are **explicitly bounded in the test code**, and the harness asserts the host's free memory before spawning. Never use the production **3 GiB** default in a test |
| 9 | **Fenced-off substrate** — reaching for `resource_pid_map`'s dormant columns (the obvious "enforcement audit" landing zone) breaks `tests/test_resource_control_seam.py` | Medium | Medium | The Fence section above states the constraint; DQ5 frames a RAM-only registry as the substrate; P7 records the unfencing as a user-gated follow-up, not an inline change. |
| 10 | **Coverage hole: `setsid` escapees** — a command that calls `setsid` leaves its process group, so neither the tree measurement nor the group kill reaches it (documented gap in BOTH lanes: `proc_tools.py:1677-1683`, `bash.py:590-600`) | Medium | Medium | DQ3 framing states the gap explicitly; note the orphan-reaper's Phase-4 env-stamp sweep already reaps such escapees at cleanup points, so the RAM guard is not the only net. Frame "setsid-aware tree discovery" as an explicit DQ3 sub-option. |
| 11 | **Boot-abort on a bad env value** — house rule: out-of-range env validated OUTSIDE the try/except at `load_config()` ABORTS BOOT (`config.py:911-916`, `api.py:2266-2278`) | Medium | Low | Clamp bounds generously (the ruled ceiling clamp is `ge=64, le=65536`), and P6 includes the config ladder case "out-of-range env aborts boot" as an explicit, asserted test. |
| 12 | **Two enforcement ports competing** — the RAM guard (default-ON, non-user-gated) lands beside the T4 seam (`enabled=false`, `user_gated=true`) and the observe-only fence test | Medium | Low | P1/DQ9 makes the separation structural: P1 lives OUTSIDE the T4 seam and does not read or write `resource_limit_policy`; the fence test must still pass unchanged. |
| 13 | **Overlap with the resource monitor's own child accounting** — `resource_pid_map` already tracks peak RSS at exit for registered children; two sources of truth could disagree | Low | Medium | Document the relationship: `resource_pid_map.peak_rss_kb` is the **exit-time floor** (a documented under-report — `bash.py:1096-1109`), the RAM guard's peak is the **watchdog's own sampled peak**. Different semantics; do not merge them. |

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
| 3 | The agent receives a **typed** error naming the **deciding** value | Assert the returned string parses as the `{ok:false, code, surface, …}` envelope with `code="ram_limit_exceeded"`, the correct `surface`, a `limit_bytes`, and that the **DECIDING VALUE** — `confirmed_pss_bytes`, or `observed_peak_bytes` on a fail-open trigger — is **≥ the ceiling**. **Not** `observed_peak_bytes >= limit_bytes`: under the ruled hybrid the RSS peak and the deciding PSS value are deliberately **distinct** quantities | Envelope parses; the **deciding value** `>= limit_bytes` |
| 4 | The reported peak is **honest** — it is the watchdog's sampled peak, not the spawn-time floor | Assert the reported peak for a command that allocates a KNOWN amount exceeds that amount by a margin, and that it is > the spawn-time VmHWM floor | Reported peak ≥ 90 % of the known allocation |
| 5 | Under-limit commands are **unaffected** | P6 test: a command allocating 8 MiB under a 64 MiB ceiling → normal exit, normal output, `exit_kind=success`, no event, no counter increment | Exit code 0; output byte-identical to the pre-feature baseline |
| 6 | Kill-switch OFF is **byte-identical** | P6 test (mirroring the existing `test_bash_spawn_stamp.py` idiom `explicit_off_byte_identical_env_no_row`): with the switch OFF, the child env carries no stamp, no row/record is written, and the returned output is byte-identical to the ON-path output for an under-limit command | Byte-identical output; zero writes; zero extra syscalls observable in the env capture |
| 7 | The config ladder behaves per house rules | P6: default = **3 072 MiB (3 GiB)**; env override applies; out-of-range env aborts boot (asserted, or an **explicit untestable disclosure** if no daemon-boot harness exists); YAML literal does NOT silently beat the env tier (the yaml-literal trap) | Each rung behaves as specified |
| 8 | **No zombies** | After a RAM kill, assert the daemon reaped the direct child; assert no zombie remains in the group (the reaper's zombie-aware skip precedent) | `ps`/procfs shows no `Z`-state member of the killed group |
| 9 | No secrets in any new log/event/record | P6: run a command whose env overlay carries a secret marker; assert the marker appears in NO log line, event payload, or persisted record | Zero occurrences of the marker value in logs/events/records |
| 10 | The feature is **inert** where it must be | P6: on a non-Linux platform (or when the platform guard says so), the guard performs NO measurement and NO kill; a command that would exceed the ceiling completes normally | Zero kills; zero measurement calls |
| 11 | The **existing fence tests still pass unmodified** | Run `tests/test_resource_control_seam.py` unchanged; run `tests/tools/test_bash.py`, `tests/tools/test_proc_tools.py`, `tests/unit/services/test_bash_spawn_reaper.py` | All pass, unmodified |
| 12 | The existing suites are **net-new-red free** | Full `tests/` run compared to the base commit | Net-new reds = 0 |
| 13 | The unit-cap interaction is **documented and actionable** | The Phase 7 operator memo `docs/runbooks/ram-guard-and-unit-bounds.md` states the **measured** state (`MemoryMax=infinity` — **no cap exists today**), the §3.4 interaction table (recommended `7G / 8700M` **and** the rejected rows), the drop-in vehicle + canary-first procedure + verify/rollback, and the §3.5 honesty set (all nine items) | The memo contains the formula, the interaction table, and the worked example; the honesty set is reproduced verbatim |
| 14 | The DQ rulings are **recorded** | Phase 1 artifact names, for each of DQ1–DQ10, the chosen option, the rejected options, and the data it depends on | 10/10 rulings recorded with a rejected-option list |

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
> the DQ6 gate (**inapplicable**; the gate is arch doc §6's 22-consumer list) ·
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
  (`bash.py:966-979`) — **that helper sanitizes for the REGISTRY row only**; the
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
| DQ1 | **(e) hybrid, confirm MANDATORY**: `summed_rss_bytes` trigger per tick + one-shot `summed_pss_bytes` confirm at breach (250 ms deadline, fail-open ⇒ kill); overcounted trees re-arm at `ceiling + (rss − pss)`. Measured over-count factor ≈ N (8.9× on a 9-member fork tree) — pure summed RSS false-kills fork-heavy legit jobs |
| DQ2 | **(b) ONE dedicated daemon thread**, FastAPI-lifespan started/stopped; tick path never logs; no lock across /proc/signal/sleep; no PSS on the tick path; poll 200 ms [50, 2000] |
| DQ3 | **(c) hybrid from ONE shared `/proc` scan per tick**: group ∪ bounded descendant-closure (per-spawn scans cost 9.49 ms each and overrun the tick); enumeration failure ⇒ drop the sample, never kill |
| DQ4 | **(d) NEW sync short-grace ladder** (TERM → slice-probe → KILL), grace 250 ms [0, 2000] as a SEPARATE knob; identity = captured pgid + leader `starttime` re-verify (fail-closed). Vocabulary: `exit_kind="ram_killed"` (constant; the real closed set is `daemon/services/resource_monitor/pid_registry.py:120-138`, NOT the path cited in this plan); proc gets `ram_killed: bool`, status stays `"killed"` |
| DQ5 | **(a) RAM-only map**, both lanes; the proc lane registers with the guard ONLY (no new durable write path); the bash `bash_spawn_registry` row stays **OPEN** on a RAM kill so the reaper's escapee net stays armed; (c) stays fenced, no lift routed |
| DQ6 | Envelope mirrors `_format_secret_envelope`, numerics as siblings before `message`, **the envelope IS the entire bash return**; proc rides `proc_status` as a `ram_breach:` line + `ram_killed: true`. **Consumer gate CLOSED** (22 consumers verified; the chart-tools legacy test is inapplicable — this plan's Risk 7 named the wrong file). Additive-key evolution rule; no version field |
| DQ7 | Bare `ENSEMBLE_RAM_GUARD` master switch (reaper semantics, default-ON, complete no-op OFF) + `RamGuardConfig(env_prefix="RAM_GUARD_")`: CEILING_MIB **3072** [64, 65536] · POLL_INTERVAL_MS 200 · GRACE_MS 250 · PSS_CONFIRM_DEADLINE_MS 250; YAML comment-only; out-of-range aborts boot; per-call ceiling is an **internal/test seam — NEVER an LLM-visible tool parameter** |
| DQ8 | `EventKind.RAM_LIMIT_EXCEEDED` (= the envelope `code`); payload = the envelope keys + verdict extras, producer-side allow-list, **no argv/env/stdout anywhere**; one event per breach; counters `ram_kills`/`ram_breaches_detected`/per-predicate refusals/`ownership_unverified`/`tree_incomplete`; log prefix `ram-guard:`; NEW additive `GET /api/resources/ram-guard` carries counters + the guard health heartbeat (NOT in livez/readyz) |
| DQ9 | Confirmed: entirely outside the T4 seam; `/control-capabilities` byte-identical; both fence tests green unmodified; env-off is the v1 disable |
| DQ10 | **(a) in-process + the event as the durable forensic record; NO migration lands in v1 at all** (`reap_reason` is plain TEXT — the "migration-backed extension" premise was stale) |

**R1 — resolved, and the premise corrected:** the live unit has **NO memory cap** (`MemoryMax=infinity` — the 7G/8G figures were a never-applied runbook recommendation), and the Oct-30 deaths were **kernel OOM kills** (`oom-kill`), not cgroup-cap kills. Ceiling is **ABSOLUTE per-command bytes**; the guard is the INNER preventive layer, the unit cap the OUTER layer covering the orphan class (the Oct-30 killing memory was largely orphaned children of a dead incarnation — invisible to any per-command guard); **the unit cap adapts to the ceiling, never the reverse**. **Shipped default AMENDED: 3 GiB (3072 MiB), not 8 GiB** — 8 GiB fires at-or-after daemon death on this 11.9 GiB host in both measured incidents; raise-path is arithmetic (see §3.3 of the recommendation). Operator routing: drop-in `30-memory-bounds.conf`, recommended `MemoryHigh=7G / MemoryMax=8700M`, canary via `systemctl set-property --runtime` — user action, NOT this lane's.

**Amendments to plan text that Phases 2–7 must honour:** the breach fires on the **CURRENT sample, not the peak** (a released spike is reported, never killed); `observed_peak_bytes` stays the RSS-based sampled peak while the **deciding** value is `confirmed_pss_bytes` (Success Criterion 3 re-aimed accordingly); the plan's anchors at `daemon/repositories/pid_registry/models.py`, the "bash lane ownership gate", "proc captures pgid at spawn", "read_pss_rollup exists", and `list_group_members` as the injectable primitive are all **falsified** — see `architecture-recommendation.md` §1.1.

---

## Configuration Surface (RULED — arch doc DQ7)

The knob names are fixed here so every phase doc refers to the same identifiers.

| Knob | Default | Bounds | Notes |
|---|---|---|---|
| `ENSEMBLE_RAM_GUARD` | **ON** | — | Bare `ENSEMBLE_*` master switch; reaper semantics (`0/false/no/off` case-insensitive = OFF). OFF = a **complete no-op**: no thread, no registration, no measurement, zero DB queries, one INFO line at lifespan start |
| `RAM_GUARD_CEILING_MIB` | **3072** (3 GiB) | **[64, 65536]** | INT-only (house rule: no human-string parsing). `× 1 048 576` once at config → `SpawnRecord` ⇒ **3 221 225 472 bytes** |
| `RAM_GUARD_POLL_INTERVAL_MS` | **200** | [50, 2000] | The guard thread's tick |
| `RAM_GUARD_GRACE_MS` | **250** | [0, 2000] | The **short** TERM→KILL grace — a **separate knob** from the 5 s timeout grace, and a **precondition of the 3 GiB ceiling** |
| `RAM_GUARD_PSS_CONFIRM_DEADLINE_MS` | **250** | [50, 2000] | The one-shot PSS confirm's deadline; expiry ⇒ **fail-open ⇒ kill** |

**Class:** `RamGuardConfig(BaseSettings)`, `env_prefix="RAM_GUARD_"`. The YAML block
is **comment-only** (pydantic init-kwargs beat env — `config.py:911-916`). Bounds are
validated **outside** the try/except at `load_config()` ⇒ an out-of-range env value
**aborts boot** (house-approved). **Depth/node caps are module constants, not
knobs.**

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
| **Shipped per-command ceiling** | **3 GiB / 3 072 MiB = 3 221 225 472 bytes** | arch doc §3.3 (leader-accepted amendment) |
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
3. **Shipped default AMENDED to 3 GiB** — the commissioned 8 GiB is **unshippable
   on this host**: it fires **at-or-after daemon death** in both measured incidents
   (fast case: tree ≈ 4.0–4.3 GiB at death). It becomes correct on a ≥ ~14.5 GiB
   host. **Raise-path is arithmetic, never taste** (arch doc §3.3).

**Coupling to DQ4 (hard precondition):** every second of (poll + grace) at 1 GiB/s
costs 1 GiB of ceiling. With the inherited 5 s grace, `C_max` collapses to
**0.68 GiB**; with the ruled ladder (200 ms + 250 ms = 0.45 s), `C_max ≈ 3.85 GiB`
⇒ 3 GiB ships with margin. **The short-grace ladder is a precondition of the
ceiling, not a nicety.**

**The interaction table** (C = 3 GiB; D_max 3.0–3.5; O 0.7–1.0; M_c 0.5; others
1.79; M_k 1.0 ⇒ X ∈ [8.0, 8.88]) lives in arch doc §3.4 and is reproduced in the
Phase 7 operator memo.

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
3. **The shipped default is 3 GiB / 3 072 MiB (3 221 225 472 bytes)** — an
   **AMENDMENT of the commissioned 8 GB**, leader-accepted on measured grounds (the
   commissioned number is unshippable on this host; arch doc §3.3). The amendment,
   the raise-path arithmetic, and the host-resize consequence are carried into the
   final report and the Phase 7 operator memo.
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
   (`peak_is_lower_bound`, `under_report_bound_bytes`).
8. **The kill-switch OFF path is a complete no-op** — no measurement, no registry
   entry, no event, no counter change, no observable difference in the child's
   environment or the returned output.
9. **No new third-party dependency.** The tree reads are raw `/proc` (the house
   posture: `proc_readers.py:6-15` — *no psutil*).
10. **The proc lane's `MAX_PROCESSES_PER_INSTANCE = 10`** (`proc_tools.py:98`) and
    the bash lane's 1.5 s bounded durable write are unchanged by this feature.

---

## Open Questions — RESOLVED in Phase 1 (two items remain)

All ten design questions and the Critical Interaction were **adjudicated and are now
binding** (arch doc §2 and §3; the one-line rulings are in the "Phase 1 Rulings"
block above). They are restated here as a closure ledger so the dispatch can see
what was decided and where.

| # | Question | Resolution | Where |
|---|---|---|---|
| DQ1 | summed RSS vs PSS vs cgroup, and the trigger/confirm hybrid | **(e) hybrid; the PSS confirm is MANDATORY** (the deciding metric) | arch doc §2 |
| DQ2 | dedicated thread vs in-execution task, and the poll interval | **(b) ONE dedicated daemon thread**, poll 200 ms [50, 2000] | arch doc §2 |
| DQ3 | pgid enumeration vs a tree walk; setsid coverage | **(c) hybrid from ONE shared `/proc` scan per tick** (group ∪ bounded descendant closure) | arch doc §2 |
| DQ4 | the ladder shape, the grace length, the new vocabulary | **(d) NEW sync short-grace ladder**; grace 250 ms [0, 2000]; `exit_kind="ram_killed"` | arch doc §2, §4.2 |
| DQ5 | the substrate, and the proc lane's missing registry write path | **(a) RAM-only**; the proc lane registers with the guard only — **no durable write path** | arch doc §2 |
| DQ6 | the exact envelope shape | Envelope **is** the bash return; the proc verdict rides `proc_status`; **consumer gate CLOSED** (22 consumers, zero break) | arch doc §2, §6 |
| DQ7 | the knob family, and how the ceiling is threaded | `ENSEMBLE_RAM_GUARD` + `RamGuardConfig(env_prefix="RAM_GUARD_")`; the per-call override is an **internal/test seam only** | arch doc §2 |
| DQ8 | the event kind, the counters, the log prefix | `EventKind.RAM_LIMIT_EXCEEDED`; counters `ram_kills`/`ram_breaches_detected`/per-predicate refusals/`ownership_unverified`/`tree_incomplete`; prefix **`ram-guard:`**; **NEW** `GET /api/resources/ram-guard` | arch doc §2 |
| DQ9 | P1 vs the T4 seam | **Confirmed: entirely outside** it; `/control-capabilities` byte-identical | arch doc §2 |
| DQ10 | in-process vs durable verdicts | **(a) in-process + the event as the durable forensic record; NO migration in v1** | arch doc §2 |
| R1 | the unit-cap/ceiling relationship | **ABSOLUTE per-command bytes**; guard-inner / unit-cap-outer; **ship 3 GiB** | arch doc §3 |

**Two items remain genuinely open — both are operator/user calls, not architect
calls:**

1. **The unit-cap drop-in is a user action** (`7G / 8700M` recommended; arch doc
   §3.4). This lane does not execute it. Without it, the guard is the **only**
   enforcement on the box and the **orphan class is uncovered** (proven Oct-30).
2. **The 3-vs-8 GiB user confirmation** — the shipped default **amends the
   commissioned 8 GB**. The amendment, the raise-path arithmetic, and the
   host-resize consequence must be carried into the final report and the Phase 7
   operator memo (pre-freeze gate (b)).
