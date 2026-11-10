# Phase 7: Durability, Operator Memo, and Follow-up Routing — rev 2

> **rev 2 — supersedes rev 1 (commit 36a9e9c0d) per council cycle 1.**
> Vehicle: **binding per-file edit list** (binding-order headers below).
> Binding source of truth: `architecture-recommendation.md` §3.3 (the amended
> default), §3.4 (operator routing), §3.5 (the honesty minimum — the **council
> union set**), DQ5 + DQ10 (durability), §8.5 (Phase-7 mandate). Where this file
> and that document disagree, **that document wins**.

## Applied edit list (binding order)

| Order | Edit | Class | Source |
|---|---|---|---|
| 1 | **T1 rewritten** — durability posture is now **in-process + the event as the durable forensic record**; **no migration** | Class 2 + Class 3 | DQ10; arch doc §8.5 |
| 2 | **T2 DELETED** — the "if durable ⇒ migration" branch is removed at **scope level** (the row is retained struck-through so the numbering stays continuous and the deletion is visible) | Class 2 | Council cycle 1 |
| 3 | **T3 repurposed** — the persistence facts a *future* table-bearing commission would need (3-site lockstep, brand-new-table ⇒ no ensure mirror, the unguarded ensure call site) | Class 3 | Arch doc §1.1; dispatcher ruling (b) |
| 4 | **T4 rewritten** — the **operator memo `docs/runbooks/ram-guard-and-unit-bounds.md` is the PRIMARY deliverable**, carrying the §3.4 routing and the §3.5 honesty set | Class 2 | Arch doc §3.4, §3.5, §8.5 |
| 5 | **T5 extended** — the pre-existing bash generic-arm leak is added to the follow-up list | Warning | Arch doc §5 ("Pre-existing leak") |
| 6 | **T6 kept, extended** — the T4 hand-off note, with the operator mental model | Class 3 | DQ9 |
| 7 | **Risk row 2 re-aimed** — the "8 GiB is safe" premise is replaced by the §3.4 reality (7G/8700M recommended; **no cap exists today**) | Class 3 | Arch doc §1.1 P1; §3.4 |
| 8 | **Exit Criterion rewritten** to the §8.5 deliverable set | Class 2 | Arch doc §8.5 |

## Objective

Close the loops the commission leaves open: the **operator memo** (the primary
deliverable of this phase), the **honesty set** the runbook and the envelope must
carry, the **durability posture**, and the **routed follow-ups** — including the
out-of-scope adjacents, which must be recorded rather than silently dropped.

**This phase runs in parallel with Phases 3 and 4** (arch doc §8.5).

## Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 1 | **Apply the DQ10 durability posture.** **(a) in-process** — the guard's registry is RAM-only; **the DQ8 event is the durable forensic record**. **No migration lands in v1 at all.** Rationale: the command died with the daemon anyway; the event answers *"why did my command die"* across restarts (one events query joins `event.pgid` ↔ the row's eventual `gc` close); events retain ~24 h (`EventRepository.cleanup_old`), sufficient for operator response windows and **stated in the runbook**. Rollback is trivial (remove the lifespan block + the lane seams; **zero schema residue**). The bash lane's `bash_spawn_registry` row is **left OPEN** on a RAM kill so the reaper's env-stamp escapee net stays armed; it closes later via the existing `ram-tier-dedup` path at terminal cleanup (DQ5) | Phase 1 Task 10 | The durability posture is **asserted** (in-process + event-only); **no** `.sql` file is added in v1; the durable row's OPEN-on-RAM-kill behaviour is pinned by the Phase 3 suite |
| 2 | ~~**If a migration is required, create it properly.** …~~ **DELETED (scope-level rating, council cycle 1).** DQ10 rules in-process + event; **no migration in v1.** Retained as a struck row so the numbering stays continuous and the deletion is visible | — | **Closed.** No migration branch exists in this plan |
| 3 | **Record the persistence facts a future table-bearing commission would need** (no action in v1). If a later commission adds a table: its own `daemon/migrations/versions/<timestamp>_create_<table>.sql` with `-- UP` / `-- DOWN`, idempotent `CREATE TABLE IF NOT EXISTS`, **no semicolons inside comments** (the runner splits on `;`), and index names byte-identical to the model's `__table_args__`; a **brand-new table ⇒ NO `_ensure_postgres_columns` mirror** (the `bash_spawn_registry` precedent); model registration happens **BEFORE** `create_all` (`manager.py:1095-1099`: registration → `create_all` → SQLite runner → ensure). **Current-tree truth about the ensure list:** `_ensure_postgres_columns` (`manager.py:8396+`) is **NOT** one transaction — it uses **many separate `engine.begin()` blocks** (`:10036`, `:10073`, `:10164`, `:10282`, `:10375`, `:10583`), with a per-statement txn in the terminal_reason backfill (`:10162-10192`) and a fail-open checkpoint block (`:10575-10634`), but there is still an **unguarded call site** (`:1057-1058`) where an exception **aborts boot**. Order is not containment | none | The facts are **recorded** in the phase report for the next commissioner; nothing is built |
| 4 | **Write the OPERATOR MEMO — `docs/runbooks/ram-guard-and-unit-bounds.md` — the PRIMARY deliverable of this phase** (arch doc §8.5). One page. It must contain: (a) **measured state** — `MemoryMax=infinity`, `MemoryHigh=infinity`, **no cap exists today**; (b) **both journal OOM deaths** (Oct-30 10:43Z and 11:13Z — *kernel* `oom-kill`, not a cgroup-cap kill, unit peak 7.3 GiB); (c) the **orphan finding** (E8: the killing memory was largely orphaned children of a dead incarnation — invisible to any per-command guard); (d) the **interaction table** (§3.4: C = 3 GiB; D_max 3.0–3.5; O 0.7–1.0; M_c 0.5; others 1.79; M_k 1.0 ⇒ X ∈ [8.0, 8.88]) with the **recommended `MemoryHigh=7G` / `MemoryMax=8700M` (8.50 GiB)** row and the rejected rows (9G, throttle-only, any X ≥ 12G, C = 8 GiB with any cap); (e) the **drop-in vehicle** — `/etc/systemd/system/ensemble-main.service.d/30-memory-bounds.conf` (next free slot; **not** `10-*`; survives `adopt-unit.sh` regeneration), applied on **restart** (restart-class), canary first via `systemctl set-property --runtime ensemble-main.service MemoryHigh=7G MemoryMax=8700M` (live, self-reverting), persist = drop-in + `daemon-reload` + restart at a maintenance window; (f) **verify/rollback** — `systemctl show … -p MemoryMax -p MemoryHigh -p NRestarts` (NRestarts stays 0), `memory.events` counters stay 0, journal grep for `memory-limit\|oom-kill` empty; rollback = remove drop-in + `daemon-reload` + restart (symmetric, no residue); (g) the **§3.5 honesty set** (below); (h) a **one-line supersession note** on `resource-cost-control.md` §8.1's **never-applied** 7G/8G figures; (i) the **3-vs-8 GiB user-confirmation** statement (pre-freeze gate (b)) | Phase 1 Task 11, Phase 2 Task 21 | The memo exists at the named path with **every** subsection (a)–(i) present; the §3.5 honesty set is reproduced **verbatim** from the arch doc; the recommended row and the rejected rows are both present; the drop-in path is the next free slot |
| 5 | **Record the routed follow-ups** as a numbered list with their entry points: (1) **host-wide OOM posture** — the unit drop-in (operator-gated; §3.4); (2) **MCP-server RAM limits** — the same guard applied to MCP stdio children (different ownership, different kill authority); (3) the **T4 ceremony path** — promoting this enforcement into `resource_limit_policy` (user-gated) once the durable port exists (`resource-cost-control/approach-comparison.md:75`); (4) the **dormant-column unfencing** — `resource_pid_map.action`/`action_by`/`acted_at` is **fenced** by `tests/test_resource_control_seam.py` and would require a deliberate, user-gated test-lifting change (NOT this lane); (5) **NEW — the pre-existing bash generic-exception-arm leak**: `bash.py:1223-1224` releases **neither** the `BashProcessRegistry` nor the `pid_registry`, and does **not** kill the child — and `MemoryError` lands exactly there during a RAM incident. Phase 3 adds the **guard** release there; the pre-existing registry leak is **disclosed, not fixed here**; (6) the **proc lane's missing escapee net** — the bash lane has the reaper's env-stamp sweep, the proc lane has **no** such net (disclosed in the honesty set) | none | Each follow-up names its owner-facing entry point, why it is out of scope here, and (for 4 and 5) that it is **deliberately deferred**, not forgotten |
| 6 | **Write the T4 hand-off note** — how the P1 default-ON guard coexists with the T4 user-gated seam (`enabled=false`, `user_gated=true`, no writer). The operator mental model (arch doc DQ9): the guard is a **safety net** (default-ON, in-process, config-driven — like the unit cap); T4 is a **future policy layer** (user-gated). The env-off switch is the v1 disable; a per-policy disable belongs to the future T4 ceremony. The note names every fence the future ceremony must consciously lift — the `/control-capabilities` `available:false` shape, the observe-only fence comment at `daemon/routers/resources.py:1166-1179`, and `tests/test_resource_control_seam.py` — and **lifts none of them now** | Phase 1 Task 9 | The note states the structural separation and names every fence; **no** fence is modified in this commission |
| 7 | **Reproduce the §3.5 honesty set** in the memo — the **council union set**, all nine items: (1) the **overshoot law** `≈ rate × (poll + grace)` with the shipped numbers (~0.45 GiB at 1 GiB/s); (2) **setsid escapees are *measured* (descendant closure) but NOT killable** by the group kill — the reaper's stamp sweep is the bash-lane backstop, and **the proc lane has no such net**; (3) **orphans from previous daemon incarnations are not covered — only the unit cap covers that class** (proven Oct-30); (4) **per-tree scope**: aggregate exposure = Σ live ceilings + daemon band; (5) **a killed legitimate heavy command is a real cost** — raise by arithmetic, never intuition; (6) **tree-RSS/PSS ≠ cgroup accounting** — never cross-compare with `memory.current`; (7) **no unit cap exists by default** (present tense + verify command + the REGENERATED-unit note — the unit file is a generated artifact, `adopt-unit.sh` never touches `*.service.d/`, so a drop-in survives regeneration); (8) **swap is not a safety net** — the daemon died with 203 MiB swap used *and* with 7.4 GiB used; swapped-out pages are invisible to an RSS metric (state the scope); (9) **watchdog liveness is load-bearing** — dedicated thread, never a coroutine | Task 4 | All nine items are present in the memo **and** the ones that the envelope must also carry (overshoot law, the lower-bound label, the guard-issued-kill statement) are cross-referenced to Phase 2/3 |
| 8 | **Carry the 3-vs-8 GiB user-confirmation statement** (pre-freeze gate (b)) — the memo must state plainly that the shipped default **amends the commissioned 8 GB**, give the **raise-path arithmetic** (§3.3: raise to 4 GiB by the same formula if the ladder lands ≤ 0.3 s total or measured legitimate workloads need > 3 GiB), and state the **host-resize consequence** (8 GiB becomes correct on a ≥ ~14.5 GiB host) | Task 4 | The statement is present in the memo and in the **final report** of this lane; the commissioned number is **not** silently replaced |

## Coupling

- **Loose with:** Phases 3 and 4 (the memo describes behaviour; it does not define
  it). It can run **in parallel** with both.
- **Independent of:** Phase 2, Phase 5, Phase 6 (documentation; at most a recorded
  persistence fact).

## Risks

| Risk | Impact | Likelihood | Mitigation |
|---|---|---|---|
| A durable-verdict path adds a **DB write to the hot path** | Medium | Low | **Moot** — DQ10 rules no durable verdict; **no** migration lands (T1, T2). No write of any kind is added to the hot path |
| The memo states the unit-cap interaction but the operator reads it as "3 GiB is safe" | **High** | Medium | Task 4 requires the measured state (**no cap exists today**), the interaction table, and the §3.5 honesty set; Task 7 requires all nine honesty items verbatim |
| The operator applies the drop-in without the canary and the unit is killed at an awkward moment | **High** | Medium | The memo mandates the **canary first** path (`systemctl set-property --runtime`, live, self-reverting) and the restart-class disclosure |
| The follow-up list is written but never actioned | Low | Medium | Task 5 records each follow-up **with its entry point**, in the project's planning area, not only in prose |
| The commissioned 8 GB is silently replaced by 3 GiB and the user never learns | **High** | Medium | Task 8 (pre-freeze gate (b)) — the statement is in the memo **and** the final report |
| The pre-existing bash generic-arm leak is forgotten because Phase 3 fixes the guard release | Medium | Medium | Task 5 item (5) records it as a **deliberately deferred** pre-existing defect, distinct from the guard release Phase 3 adds |

## Exit Criterion

- The **operator memo exists** at `docs/runbooks/ram-guard-and-unit-bounds.md` with
  every subsection (a)–(i), the §3.4 interaction table (recommended **and** rejected
  rows), the drop-in vehicle at the next free slot with the canary-first procedure,
  and the verify/rollback steps.
- The **§3.5 honesty set** is reproduced in full (all nine items).
- The **3-vs-8 GiB statement** appears in the memo **and** in the final report.
- **No migration lands**; the durable-row-stays-OPEN behaviour is pinned by the
  Phase 3 suite.
- The **six routed follow-ups** are recorded with their entry points, including the
  **pre-existing bash generic-arm leak** and the **fenced dormant columns**.
- The **T4 hand-off note** names every fence a future ceremony must lift — and
  **lifts none of them now**.
