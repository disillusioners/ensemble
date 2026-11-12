# Phase 1: Design Decision Gate (DQ1–DQ10 Adjudication)

> **rev 3 — closure note (council cycle 1) + explicit self-disclaimer.**
> **SELF-DISCLAIMER (approver iteration 002):** this file is the **historical record
> of the Phase-1 gate's asks**, not a build contract. It is CLOSED and its rulings
> live in `architecture-recommendation.md` §2 and §3 (as amended by iterations 001
> and 002). **Nothing in this file is binding** — where it states a premise that a
> later pass falsified (the `MemoryMax=8G` premise in Task 11, the withdrawn
> consumer-count claim, the superseded verdict-wait deadline, the single-thread DQ2
> wording, the superseded vocabulary count), the **contract wins**; this file is
> retained only as the audit trail of what was asked. **The build contract is the
> phase docs' rev 4.** This phase is **CLOSED**: the
> architect returned **10/10 binding rulings + R1 + the sequencing verdict**
> (`architecture-recommendation.md`, §2 and §3). The task list below is retained
> as the **record of what was asked**; it is **not** the live contract. The live
> contract is the arch doc's frozen contracts (§4.1–§4.4) and the phase docs'
> rev 2.
>
> **One premise in this file was falsified and is corrected here:** Task 11's
> framing cited "the unit `MemoryMax=8G` / daemon band 5.3–6.0 GiB". **No unit
> memory cap exists** (`MemoryMax=infinity`, `MemoryHigh=infinity` — verified via
> `systemctl show` and by the absence of any `Memory*` directive in the unit or
> its drop-ins); the 7G/8G figures were a **never-applied** runbook
> recommendation, and the Oct-30 deaths were **kernel `oom-kill`** events, not
> cgroup-cap kills. The ruling that came back: the ceiling is **ABSOLUTE
> per-command bytes**, the guard is the **INNER** preventive layer, the unit cap
> is the **OUTER** layer covering the orphan class, and the shipped default is
> **3 GiB / 3 072 MiB** (arch doc §3.1–§3.3). The measured daemon band is
> **2.69 → 3.31 GiB**.
>
> **Two items remain genuinely open** and are carried forward, not closed here:
> the unit-cap drop-in is a **user action**, and the **3-vs-8 GiB confirmation** (DISCHARGED at the escalation pass — USER RULINGS #1 + #2: the default is now dynamic)
> must be surfaced in the final report and the Phase 7 operator memo.

## Objective

Convert the ten framed design questions (DQ1–DQ10 in
`plan-overview.md`) into **ten binding rulings**, each naming the chosen option, the
rejected options and why, the data the choice depends on, and the contract it
implies for the phases that follow. Also rule on the **Critical Interaction** (the
per-command ceiling vs the daemon-unit `MemoryMax`), which is a critical-path risk
(R1) and can each make the feature a no-op in production while every test passes.

**This phase writes no production code.** It produces a decision record plus the
frozen contracts that Phases 2–7 implement.

## Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 1 | **Ruling DQ1 — tree RAM metric.** Choose (a) summed RSS, (b) PSS, (c) per-spawn cgroup, (d) unit cgroup, or the trigger/confirm hybrid. If hybrid: specify the confirm-metric failure semantics (fail-open ⇒ kill anyway, or fail-closed ⇒ let it live) and the TOCTOU window | none | Ruling names the option, the **unit of the reported number** (bytes vs KiB), the **double-count semantics** (or its absence), and the exact definition that `observed_peak_bytes` must satisfy |
| 2 | **Ruling DQ2 — poll strategy + loop home.** Thread vs in-execution task vs sampler ride; plus the poll interval default and its bounds | Task 1 | Ruling names the mechanism, the interval default + `[min,max]`, the **breach-detection latency target**, and the overhead budget per tick per live spawn |
| 3 | **Ruling DQ3 — tree discovery.** pgid enumeration vs recursive ppid walk vs hybrid; the setsid-escapee coverage decision | none | Ruling names the discovery primitive, the **failure semantics** (enumeration-failed ⇒ fail-open vs fail-closed), and the setsid stance |
| 4 | **Ruling DQ4 — kill escalation + delivery.** The ladder shape, the grace length, the ownership re-verification, and the new vocabulary | Task 3 | Ruling names the ladder, the grace default + bounds, the delivery primitive per lane, and the **exact new vocabulary strings** (`ram_killed` candidates) in BOTH the `pid_registry` exit-kind set and the proc-tool status set |
| 5 | **Ruling DQ5 — integration seam + bookkeeping.** Which seams are touched in each lane; the substrate (RAM-only vs durable extension); the proc lane's missing durable write path | Tasks 1, 4 | Ruling names the substrate, the seams, and whether a **migration is required at all** (RAM-only ⇒ none) |
| 6 | **Ruling DQ6 — typed error contract.** After reading `tests/test_chart_tools_legacy_error_contract.py` and grepping the tool-output post-processors for consumers of `ok`/`code`/`surface`, fix the exact envelope shape | Task 4 | Ruling states the literal JSON shape for bash and for proc, the `code` and `surface` values, the field names, and the **list of consumers verified** |
| 7 | **Ruling DQ7 — config surface.** The knob family, names, units, bounds, the YAML comment-only rule, and **how the ceiling is threaded for runtime injection** (a hard requirement — the test harness cannot restart the daemon) | Tasks 1, 6 | Ruling names every knob, its unit and bounds, its env var, its default, and the **parameter path** from config → tool call → guard |
| 8 | **Ruling DQ8 — observability.** The event kind, the counter names, the log prefix, and the sanitization rule | Task 6 | Ruling names the event kind string, the payload keys, the counter names, the logger prefix, and confirms the event fires **once per breach** (never per tick) |
| 9 | **Ruling DQ9 — default-ON vs the T4 seam.** Confirm P1 sits outside `resource_limit_policy` and the `resource_pid_map` dormant columns; confirm `/control-capabilities` stays untouched | none | Ruling states the structural separation and names the fence test(s) that must keep passing **unmodified** |
| 10 | **Ruling DQ10 — crash durability.** In-process vs durable verdict; if durable, the closed-vocab extension (`reap_reason` in `bash_spawn_registry` is a CLOSED SET) and the write frequency (once per killed command, never per tick) | Task 5 | Ruling states the durability posture and, if durable, the migration requirement and the new vocabulary member |
| 11 | **Ruling — the Critical Interaction (R1).** The relationship between the per-command ceiling and the unit cap / daemon band. **[CLOSED — premise corrected: no unit memory cap exists (`MemoryMax=infinity`); the 7G/8G figures were a never-applied runbook recommendation and the Oct-30 deaths were kernel `oom-kill` events. The ruling returned: ABSOLUTE per-command bytes · guard-INNER preventive layer / unit-cap-OUTER containment layer · shipped default 3 GiB / 3 072 MiB. See the rev-2 closure note at the head of this file and arch doc §3.1–§3.3.]** | Task 7 | Ruling states the effective headroom formula, whether the shipped default changes, and what the operator runbook must say. **If the answer changes the default ceiling, the plan's default is amended here and the amendment is recorded in the decision record** |
| 12 | **Freeze the contracts.** Publish the tree-RAM reader contract (units, failure semantics, "unreadable" vs "zero"), the verdict-record shape, and the kill-ladder contract — so Phases 3 and 4 can proceed **in parallel** against a frozen interface | Tasks 1–4 | The three contracts are written down and referenced by later phases; any later change is a version bump, not a silent edit |

## Coupling

- **Tight with:** Phase 2 (the measurement substrate implements DQ1 + DQ3), Phase 3
  and Phase 4 (DQ2 + DQ4), Phase 5 (DQ6 + DQ7 + DQ8), Phase 7 (DQ10).
- **Loose with:** Phase 6 (the test suite is written against the rulings, but its
  content depends on them rather than feeding back).
- **Independent of:** nothing — this phase gates everything. That is its purpose.

## Risks

| Risk | Impact | Likelihood | Mitigation |
|---|---|---|---|
| A ruling is deferred rather than made ("let the implementer decide") | High | Medium | The exit criterion requires **10/10 rulings with a rejected-option list**. A deferred DQ fails the gate. |
| The Critical Interaction (R1) is waved through without an operator answer | High | Medium | Task 11 names it explicitly; the gate cannot close with R1 unresolved, because it decides the shipped default |
| The contracts are frozen too narrowly and Phase 3/4 need a change | Medium | Medium | Freeze the **interface**, not the implementation; contract changes are versioned and require a decision-record amendment |
| The architect rules a metric (e.g. per-spawn cgroup) that requires unavailable infrastructure (`systemd-run` unavailable; no delegation) | High | Low | The DQ1 table in `plan-overview.md` records the availability constraint inline, so an unavailable option is visibly unavailable at decision time |

## Exit Criterion

- **10/10 DQ rulings recorded**, each with: the chosen option, the rejected options
  and the reason each was rejected, the data it depends on, and the contract it
  implies.
- **The Critical Interaction (R1) ruled**, with the shipped default confirmed or
  amended (an amendment is recorded explicitly, with the previous value).
- **Three contracts frozen**: the tree-RAM reader contract, the verdict-record
  shape, and the kill-ladder contract.
- **Phase 2 is unblocked**: the reader's units, failure semantics, and discovery
  primitive are all specified, so the measurement substrate can be built with no
  further design questions.
