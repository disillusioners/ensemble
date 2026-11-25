# Phase 1 Addendum — the two measurements, contract rulings (architect, 2026-11-23 ~20:10Z+)

Companion to `architecture-recommendation-v3.md` (design of record). The Emperor's front door put two measurements
to this lane; both are FACTS that the build contract must reflect. Rulings below are mine, within my mandate.

## M1 — Execution-receipt contract: non-contradiction + stated assumption

**Measured facts (leader's lane):** for 47 of 51 fired schedule sources, every `schedule_executions` row carries
`instance_id = NULL`; `last_execution_status` reads `'queued'` for runs that RAN, runs that DID NOT, and the
2026-11-23 19:00Z reclaim test PROVEN to have never started a turn (instance created, task pending forever, message
`ready`, agent loop never began; pause/resume answered `no_active_job`). The schedule surface cannot tell an
operator whether a scheduled run actually ran.

**Ruling (binding on the listing contract):** the listing floor item 4 (last-fire + status) reads `schedule_executions`
AS STORED — single source, same discipline as next-fire. The listing MUST NOT synthesize an outcome (no client-side
ran/didn't-run inference from absence of data). **Stated assumption, verbatim for the code comment:** "last-fire and
status are rendered from `schedule_executions` as stored; outcome-truth (instance linkage + terminal status) is owned
by the execution-receipt lane (job d271db9d) — until that lands, `last_execution_status` is NOT evidence that a turn
ran." When the receipt side lands, the SAME listing fields become outcome-true with ZERO listing change — the listing
never fabricates, so it cannot contradict the receipt contract. No receipt-side work (writes, linkage, status
semantics) is in Phase 1 — explicitly out of this lane.

## M2 — Scheduler spawn path must honor the row's `project_id` (routed, not taken)

**Measured fact:** a scheduled lane whose schedule row carried `project_id = agents-ensemble` spawned in the SYSTEM
DEFAULT project and opened "Non-Scoped Mode — Project Selection Required"; its task sat pending/unclaimed with the
agent loop never starting.

**Ruling:** YES — the canonical contract REQUIRES the spawn path to honor the schedule row's `project_id`. A schedule
row's project scope is an operator-declared invariant; silently substituting the system default changes context
injection, memory/knowledge scoping, and provenance for EVERY project-scoped scheduled lane — a correctness defect,
wider blast radius than the listing defect. Invariants for the owning lane (unchanged from v3 §7): (a) a spawned task
inherits the row's project, never a substituted default; (b) the listing shows project scope (implemented in Phase 1
as W3); (c) a spawned task must not be left `pending`/unclaimed with its loop never started. **Fix belongs to the
leader's lane (job d271db9d) — this lane does not touch spawn/claim code.** Routed for the Emperor's visibility.

## M3 — Seeder re-arm semantics (5–13-day effective interval) needs an owner

**Established in v3 §0:** `_rearm_chain_row` (scheduler.py:950-1070) counts the next weekday-D occurrence from the
re-arm with `allow_today=False`, so the nominal "weekly" chain fires every **5–13 days**, not 7 (BR-1 sub-semantics).
An operator reading "weekly" is misled even after the label derivation lands (the derived label says "weekly
(windowed)"; the CALENDAR truth is a drifted interval).

**Ruling:** NOT a Phase 1 fix (the re-arm is load-bearing live machinery; touching it is its own commission with its
own pins). Owner: leader/PM ledger — the decision to make is EITHER honor a true weekly anchor (7-day cadence from a
stable reference, not from the re-arm instant) OR present the interval honestly (v3 §3.2 already names the sanctioned
alternative: the adapter's native `interval_seconds` primitive — one line, no second mechanism — if spread is ever
wanted once the chain marker retires). Phase 1's read-side derivation makes the WINDOW visible; the interval drift
stays maintenance-internal until owned.

## Phase 2 flip condition — restated for the record

Phase 2 (window as a first-class field, jitter kill-switch `ENSEMBLE_SCHEDULING_WINDOW_JITTER`, window-edge rules,
generic re-arm proof leg) builds ONLY on **Branch B: a MEASURED recurring collision** between the fixed slot and the
PG host's own recurring heavy work (autovacuum on the toasted relation, Longhorn snapshots on the PVC, PG maintenance
jobs, the house's recurring jobs) — v3 §2.2. Status: **unmeasured ⇒ Branch A (fixed slot; nothing built).** The
measurement is a cheap read-only devops probe over one weekly cycle (v3 §2.3/§4.9), NOT part of Phase 1. If anyone
judges Branch B has fired, it goes to the Emperor for the ruling — no lane starts Phase 2 on its own.

## v2 → v3 reconciliation note (for the builder lane)

The commission letter quotes v2's 4.1–4.3. v3 (five minutes older than the letter, same lane) is the design of record
and tightens Phase 1: floor item 5 (project scope) and §4.1 add "surface `project_id`"; §4.2 adds the live-preference
label-resolution hazard (mandatory companion to dead-label reuse); §4.3 upgrades v2's optional seeder tweak to a
CONDITIONAL work item (W6 in the brief). Where the letter and v3 differ, **v3 governs; the letter's items are the
floor.** `phase1-implementation-brief.md` in this directory is the binding build translation of both.
