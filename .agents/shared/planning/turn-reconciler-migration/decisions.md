# Decisions Log: Turn-Reconciler & Named Transitions Migration

**Status:** Draft (Revision 1) — derived from the 2026-08-01 production incident (`docs/bugs/pause-during-report-turn-orphans-message-jobitem.md`); companion to `docs/plans/turn-reconciler-named-transitions.md`.
**Date:** 2026-08-01
**Author:** planner[v2] via plan-creation worker (Worker)
**Reviewer:** Council (governance) + lead developer
**Supersedes:** `defer-queue-and-job-task-seam-bugs.md` §1–§4 (this document formalizes the structural fix those deferred carve-outs foreshadowed)
**Related:** `.agents/shared/planning/fix-pause-report-turn-orphan/decisions.md` (D-REV-1 through D-REV-6) — the 5 point-fixes above are the *baseline* the migration makes redundant

---

## 1. Introduction

### 1.1 Purpose

This document is the **Architectural Decision Record (ADR)** for the 4-increment migration that converts the "cascade forgot a mirror" bug class — the same shape that fired on 2026-08-01 in two simultaneous forms, plus four prior incidents in the same family — from a weekly production occurrence into a *structurally impossible* one. It captures every binding design decision with its rationale, consequences, and which increment it ships in.

It is intended to be load-bearing: a future developer reviewing `daemon/services/turn_transitions.py` should find the answer to "why is it *this* shape?" by reading a `D-N` reference in the code comment, which points here.

### 1.2 Migration Overview

The migration introduces four structural changes, each independently shippable:

| Inc | Name | One-line effect |
|---|---|---|
| 1 | Turn reconciler | A `reconcile_turn_mirror(work_id)` routine is the *only* generator of the "mirrors must match the Task terminal state" truth. Additive — runs alongside existing guards with zero behavior change at first cut. |
| 2 | Carve-out deletion | `claim_pending_task`'s `NOT EXISTS` pile and `_admitted_task_carve_out_sql` (~100 lines of hand-tuned SQL) are deleted; the guard simplifies to ~5 lines because the reconciler guarantees there are no orphans to exclude. |
| 3 | Named transitions | Every lifecycle event becomes a `BEGIN_TURN` / `SUSPEND_TURN` / `RESUME_TURN` / `COMPLETE_TURN` / `ABORT_TURN` / `RETRY_TURN` operation with a declared, exhaustive mirror set, validated by a property test. Hand-written cascade SQL ceases to exist. |
| 4 | Turn suspension handle | `resume_processing_job` no longer infers root-vs-child from `Task.status`; it targets a `suspension_reason` + `resume_target_turn_id` handle on the authoritative turn. The routing gap that produced Bug A disappears. |

### 1.3 Read order

- **Section 2** — Decision Log (D1–D12). Each decision stands alone; cross-references use `D-N` notation.
- **Section 3** — Increment Sequencing. Dependency graph for ship order.
- **Section 4** — Risk Register. Top risks surviving the migration with mitigations.
- **Section 5** — Open Questions. Items deliberately left unresolved for council review.

### 1.4 Reader contract

A reader should be able to (a) understand the system without reading any other doc, (b) locate the source-of-truth file:line for any decision, and (c) find the increment in which a decision ships. If any of those three fails, the document is incomplete and must be revised.

---

## 2. Decision Log

### D1 — Reconciler must handle ALL 8 mirror tables, not the 5 in the design doc

**Status:** Accepted

**Context:**

The reference plan (`docs/plans/turn-reconciler-named-transitions.md` §4.1) enumerates five mirror tables the reconciler handles: `task`, `job_queue_items`, `message_queue`, `job_locks`, `dependency_watchers`. In production, the same logical fact — "this turn is in flight / paused / done" — is ALSO mirrored in three additional tables whose updates today are equally hand-written and equally fallible. If the reconciler ships missing any of those three, the next orphan bug class is structurally guaranteed.

**Decision:**

The reconciler owns mirror consistency for **all eight** tables below. The union of any transition's declared mirror set equals this full set (no transition can silently drop a mirror from its contract — D10).

| # | Table | State field | Values | Link key | In reference plan? | Notes |
|---|---|---|---|---|---|---|
| 1 | `task` | `status` | 6 values (`pending`, `running`, `paused`, `completed`, `cancelled`, `failed`) | `work_id` (UUID, **authority**) | ✅ Yes | The authority — Task is mutated first, mirrors follow. |
| 2 | `job_queue_items` | `admission_state` | 4 values (`queued`, `active`, `done`, `dead`) | `job_id` = `task.work_id` | ✅ Yes | Reconciled via `transition_to_done_by_work_id`. |
| 3 | `message_queue` | `status` | 6 values | `message_id` + `processing_task_id` | ✅ Yes | `processing_task_id` → `task.id` (not directly via `work_id`). |
| 4 | `job_locks` | (row existence = state) | n/a — ephemeral lease | `job_id` = `task.work_id` | ✅ Yes | `DELETE` on terminal Task; leave while in-flight. |
| 5 | `dependency_watchers` | `state` | 3 UPPER values (`PENDING`, `FIRED`, `CANCELLED`) | `source_task_id` → `task.id` | ✅ Yes | Triggered off, not always reconciled. |
| 6 | **`report_injections`** | `state` | 3 UPPER values | `report_message_id` → `message_queue.message_id` | ❌ **MISSING** | Same orphan class as `message_queue` — fires the `PROCESS_REPORT` lane. |
| 7 | **`instances`** | `status` | 10 values (tree-scoped) | `instance_id` = `task.instance_id` | ❌ **MISSING** | Cross-system pivot; cascades write to it. |
| 8 | **`job_watchers`** | (no `status`; uses `watch_events`) | n/a — listener rows | `job_id` (semantic = `work_id`) | ❌ **MISSING** | Dangling listener rows survive turn completion. |

**Rationale — why each missing table (6, 7, 8) must be included:**

#### D1.a — `report_injections` (table 6)

The `PROCESS_REPORT` lane (defined in `report-lane-decoupling.md`) writes a row to `report_injections` keyed on `report_message_id`. The resume cascade (Bug A from the 2026-08-01 incident) cancelled the `process_report` Task but did not transition the `report_injections` row. If a report arrives after pause, the row remains in `state='PENDING'` while the backing Task is `cancelled` — the exact mirror-orphan shape Bug B produced for `message_queue`, applied to a different table. The reference plan was authored before the report-lane decoupling landed; the table genuinely was 5-mirror back then. It is 6-mirror now, and the reconciler must follow.

#### D1.b — `instances` (table 7)

Every cascade in the codebase writes to `instances.status` (`_pause_cascade_db_sync`, `_resume_cascade_db_sync`, `_finalize_job_db_sync` all do). The reconciler must therefore observe `instances` to validate consistency. **However** `instances.status` is *tree-scoped* (a `running` instance can hold many turn lifecycles simultaneously), so the reconciler does NOT force-update it. See D11 for the soft reconciliation rule: verify-and-flag, let cascade or periodic sweep correct. The missing-tables list grew when the cascades started touching it.

#### D1.c — `job_watchers` (table 8)

`schedule_retry` (`task/repository.py:2119-2308`) mints a fresh `work_id` and migrates `job_watchers` rows from the parent to the child. If a turn is completed without first migrating its listeners, the rows remain tied to the parent `work_id`. The cascade finally has no transitive path to fire them — they silently drop. This bug is harder to spot than `message_queue` orphans because `job_watchers` has no boolean `state` column; the symptom is "the watched event never fires" weeks later. The reconciler must close it.

> **§ REVISION NOTE (R1, stale-citation fix 2026-08-01):** The pre-revision citation `repository.py:1793-1981` is stale (off by ~326 lines after subsequent repository refactors). Correct location: `task/repository.py:2119-2308`.

**Consequences:**

- Increment 1 ships with a reconciler that covers all 8 tables — not just 5. The reference plan's table list in §4.1 is updated in-place to reflect the 8-table scope. Estimated incremental work: +~400 lines (helpers for `report_injections`, soft-reconcile branch for `instances`, listener migration for `job_watchers`).
- The property test (D10) asserts coverage of all 8 — misses are caught at test time, not production.
- A future bug doc that says "the cascade forgot table N" cannot exist for N ∈ {1..8}.

**Related Increment:** Increment 1 (primary); D2, D10, D11.

---

### D2 — `work_id` is the authoritative correlation axis (NOT `message_id`)

**Status:** Accepted

**Context:**

Multiple migration candidates compete for the "key that ties a turn's mirrors together": `message_id`, `Task.id`, `work_id`, `instance_id`. The codebase already has four precedents, each with a different axis. The 2026-08-01 point-fixes (`.agents/shared/planning/fix-pause-report-turn-orphan/decisions.md` D-REV-1) made the binding call for THAT bug's authoring, but the migration needs a permanent, version-agnostic choice.

**Decision:**

**`work_id` (the `Task.work_id` UUID) is the authoritative correlation axis.** Every reconciler primitive keys on `work_id`. Cross-table predicates MUST express correlation as `Task.work_id = <mirror-foreign-key-to-work_id>`; they MUST NOT use `Task.message_id = <mirror-foreign-key-to-message_id>` paths when a `work_id` path exists.

**Rationale:**

- **`work_id` is stable across retries.** `schedule_retry` (`task/repository.py:2119-2308`) mints a fresh `work_id` for the child but reuses the parent's `message_id`. A `message_id`-keyed `NOT EXISTS` finds the *fresh PENDING retry Task* and blocks it, reproducing the exact deadlock that motivated the point-fixes. The `work_id`-keyed correlation is a direct column-to-column join (no JSON extraction), established at the `Task.work_id = JobItem.job_id` join in `find_resume_root_candidate_by_active_job` (`task/repository.py:246-370`, the SQL fragment at line 363).

> **§ REVISION NOTE (R1, stale-citation fix 2026-08-01):** The pre-revision citations `repository.py:1793-1935` (schedule_retry) and `repository.py:640-645` (work_id_job_item_equality) are stale. Correct location of `schedule_retry`: `task/repository.py:2119-2308` (off by ~326 lines). The `work_id_job_item_equality` SQL fragment does not exist by that name; the actual work_id-keyed correlation is expressed as `.where(JobItem.job_id == Task.work_id)` at `task/repository.py:363` inside `find_resume_root_candidate_by_active_job`.
- **`work_id` links all five direct mirrors cleanly.** Tables 1 (`task`), 2 (`job_queue_items`), 4 (`job_locks`) join directly on `work_id` ↔ `job_id` equality. Tables 3 (`message_queue`) and 5 (`dependency_watchers`) use `processing_task_id` / `source_task_id`, both of which resolve to `Task.id → Task.work_id` in two hops via a single SQL fragment. Tables 6 and 8 derive from `work_id` via the message-id and the retry-migration helper respectively. Table 7 (`instances`) keys on `instance_id` — see D11.
- **`work_id` is the "virtual job" handle.** `docs/plans/virtual-job-management-surface.md` already establishes the `work_id` UUID as the public linkage handle. The reconciler strengthens that contract rather than introducing a new key.
- **Direct column-to-column joins are auditable.** JSON extraction paths (e.g. `payload->>'work_id'::text`) are not: they depend on schema shape, can't be indexed, and break on payload format changes. `work_id = work_id` is reviewable.

**Consequences:**

- Every helper method in `daemon/repositories/task/repository.py` that currently takes `task_id` and resolves to `work_id` keeps that signature (no API churn at the repository layer). New reconciler primitives take `work_id` directly.
- The point-fixes' `D-REV-1` correlation axis is now *codified*, not just an incident-specific decision. Code comments reference this ADR (D2).
- Any future mirror that wants to participate MUST declare a `work_id` foreign-key-shaped column. The schema migration review checklist gains a row: "Does this column join directly to `task.work_id` or via a 2-hop through `Task.id`? If via payload extraction, REJECT."

**Related Increment:** Increment 1 (axiomatic — every reconciler primitive depends on D2).

---

### D3 — Reconciler starts additive; carve-out deletion is a separate, later step

**Status:** Accepted

**Context:**

The reference plan proposes two simultaneous changes: (a) introduce the reconciler, (b) simplify the cross-system guard by deleting carve-outs. Combining them in one ship means a single regression reintroduces BOTH bugs (no reconciler running AND no orphan-exclusion) — a strictly worse state than either bug alone, and undetectable by either test class.

**Decision:**

- **Increment 1** ships the reconciler purely additive. It runs after every claim, resume, finalize, and timeout. It logs corrections but does NOT change the guard's behavior. The carve-outs (`queued`-orphan exclusion, `_admitted_task_carve_out_sql`) remain in place.
- **Increment 2** deletes the carve-outs. It does not run if Increment 1 telemetry shows the reconciler is not yet idempotent under load.

**Rationale:**

- **Regression baseline.** The 5 point-fixes from `.agents/shared/planning/fix-pause-report-turn-orphan/decisions.md` are the deployed-behavior baseline. All 404 tests must remain green across Increment 1. A green Increment 1 + green point-fixes means the system is at least as good as today; a broken Increment 1 with point-fixes means we lose nothing.
- **Risk monotonicity.** Additive changes can ONLY add safety. Removal changes can ONLY reduce it. Reversing the order (delete first, ship reconciler second) puts the system into a window where orphans exist AND are admitted — replicating Shape D from the design doc for the duration of the migration.
- **Telemetry gate.** Increment 1 ships with a metric: `reconciler_corrections_per_hour`. After 7 days in production, if it is non-zero (proof the reconciler is doing work) AND zero user-visible incidents occurred (proof the corrections were correct), Increment 2 is eligible. If either condition fails, Increment 2 is blocked and the reconciler must be hardened first.
- **Independent rollback.** If Increment 1 ships and a bug surfaces, it can be rolled back by removing the reconciler calls — point-fixes and existing guards remain. If Increment 1+2 ships combined and breaks, the rollback must re-add 100 lines of carved-out SQL — high-friction, error-prone.

**Consequences:**

- Increment 1 ships a single, easily rollback-able change.
- Increment 2 has a published gate (telemetry + incidents). Reviewers can audit the metric, not just the code.
- The total migration duration extends by ~7 days (the telemetry window) — accepted as the cost of safety.

**Related Increment:** Increment 1 (sets the constraint), Increment 2 (consumes the gate).

---

### D4 — 3 REPLACE, 2 COEXIST (point-fix disposition)

**Status:** Accepted

**Context:**

The 5 point-fixes from the 2026-08-01 incident (`fix-pause-report-turn-orphan/decisions.md`) are interim code with surgical SQL changes. The migration subsumes some of them directly; the others live at a different abstraction layer and should NOT be subsumed (they would couple the reconciler to predicates, which is the wrong layering).

**Decision:**

| Point-fix | File:Line | Disposition | Increment |
|---|---|---|---|
| `_terminal_orphan_active_sql` | `task/repository.py:1181-1253` | **REPLACE** | Increment 2 |
| `find_resume_root_candidate_by_active_job` | `task/repository.py:246-370` | **REPLACE** | Increment 4 |
| UPDATE 4 (136 lines, dialect-branched) | `instance_lifecycle.py:3664-4097` | **REPLACE** | Increment 1 |
| `predicates.py` (read-side) | `message_queue/predicates.py` | **COEXIST** | — |
| `_post_reconcile_completion_refire` | `instance_lifecycle.py:3302-3472` | **COEXIST** | — |

**Rationale — why each disposition:**

#### D4.a — Replace: `_terminal_orphan_active_sql` (`task/repository.py:1181-1253`)

This 73-line predicate is the explicit hand-rolled "find JobItems whose Task is terminal" check. The reconciler is exactly that — the *generalized* version of this predicate — and the property test (D10) asserts the reconciler's predicate is a superset. Keeping a hand-rolled orphan-aware filter while ALSO having the reconciler running creates two competing sources of truth for the same fact. That IS the bug class. Replace.

#### D4.b — Replace: `find_resume_root_candidate_by_active_job` (`task/repository.py:246-370`)

This 125-line primitive exists to handle the case "no `process_message` Task is paused but a `job_queue_items(message)` row is `active`." The bug doc's Option B (the answer gate routes directly through the handle) and D9 (Phase 3 — turn suspension handle) make this primitive obsolete: resume targets the handle, not the inferred active JobItem. The handle is in Increment 4; this replacement is part of that work.

#### D4.c — Replace: UPDATE 4 (`instance_lifecycle.py:3664-4097`)

UPDATE 4 reconciles only `completion_report` rows whose backing Task was cancelled by THIS resume cascade (per D-B-3 revised). It is the partial, scoped shadow of what `reconcile_turn_mirror` does globally. Replace the 136 lines with one call to `reconcile_turn_mirror(RETURNING work_id)`. The dialect-branching (PostgreSQL vs SQLite) collapses — the reconciler moves it once.

> **§ REVISION NOTE (R1, stale-citation fix 2026-08-01):** The pre-revision citation `instance_lifecycle.py:3664-4032` is stale (off by ~65 lines). Correct location: `instance_lifecycle.py:3664-4097` (UPDATE 4 spans through the `session.commit()` block at line 4096; the post-UPDATE-4 `pending_count` evaluation begins at line 4098).

#### D4.d — Coexist: `predicates.py` (`message_queue/predicates.py`)

Read-side predicates (e.g. "messages whose Task is paused" used for inbox rendering) live at a *different abstraction layer* than the reconciler. They project the mirror state into UI; they do not mutate it. The reconciler's job is mutation. Coupling them would force every UI read to route through the reconciliation path, which is the wrong performance characteristic. **They operate at the projection layer, not the lifecycle layer.** Coexist.

If a future bug shows the predicates desyncing from the reconciler (e.g. UI shows wrong rows after a pause), predicates are hardened *separately* — they call the reconciler first if needed, but the layering stays clean.

#### D4.e — Coexist: `_post_reconcile_completion_refire` (`instance_lifecycle.py:3302-3472`)

This 170-line routine handles the "instance completed but a child refires because of a retry queue event that landed in the same window" race. It is a *post-state* routine — the reconciler is *pre-state* (mutating). They are complementary, not redundant. Coexist.

If the race condition is permanently eliminated by the reconciler (D2 + D1 eliminate the orphan class entirely), the refire routine becomes a no-op in practice but the code stays for defense-in-depth. Removing it would require proving no race exists; keeping it costs ~170 lines of safe code.

**Consequences:**

- Increment 1: removes 136 lines (`instance_lifecycle.py` UPDATE 4), adds ~400 (reconciler). Net +264.
- Increment 2: removes 73 lines (`_terminal_orphan_active_sql`). Net -73 cumulative.
- Increment 4: removes 125 lines (`find_resume_root_candidate_by_active_job`). Net -125 cumulative.
- `_post_reconcile_completion_refire` (170 lines) and `predicates.py` stay indefinitely.
- The migration shrinks ~205 lines of SQL overall — net code reduction in the lifecycle path.

**Related Increment:** Increment 1 (D4.c), Increment 2 (D4.a), Increment 4 (D4.b), D4.d/e are independent.

---

### D5 — Each increment independently shippable; Inc 3 ↔ Inc 2 interchangeable

**Status:** Accepted

**Context:**

Two valid orderings exist: (a) Reconciler → Carve-out deletion → Named transitions → Handle, or (b) Reconciler → Named transitions → Carve-out deletion → Handle. Both produce the same final system but differ in intermediate risk windows.

**Decision:**

**Default sequencing:** Inc 1 → Inc 2 → Inc 3 → Inc 4.

**Interchangeable pair:** Inc 2 and Inc 3 can ship in either order *after* Inc 1 lands.

**Rationale:**

- **Inc 1 is foundational.** Every later increment assumes the reconciler exists. Without it, none of the deleted carve-outs are safe to remove (Inc 2 depends on guarantees only the reconciler provides) and none of the named transitions are useful (they would just be hand-written cascades with renamed methods).
- **Inc 4 is last.** It introduces a new schema column (`suspension_reason`, `resume_target_turn_id` per D7) and rewrites resume routing. Both are invasive changes; landing them last lets them be tested against a stable reconciler and stable transitions.
- **Inc 2 ↔ Inc 3 interchangeability.** Inc 2 deletes code (carve-outs in the guard). Inc 3 introduces code (named transitions as wrappers around the reconciler + cascade logic). They touch DIFFERENT files (`task/repository.py` for Inc 2; new `services/turn_transitions.py` plus refactored `instance_lifecycle.py` for Inc 3). Shipping Inc 3 first slightly *worsens* the safety window (new code, transitions not yet battle-tested) but reduces the risk window of Inc 2 (less SQL surface area to reason about). Shipping Inc 2 first *narrows* the guard but relies on a wider code surface. Team picks based on which change has stricter review (the team's choice; this ADR does not bias).
- **No reason to interleave.** All four increments reach the same architecture; interleaving adds review complexity without buying anything.

**Dependency graph (canonical text form):**

```
        ┌──────────────┐
        │   Increment 1 │  (Reconciler)
        │   reconcile_   │
        │  turn_mirror() │
        └──────┬───────┘
               │
       ┌───────┴───────┐
       │               │
       ▼               ▼
  ┌────────────┐  ┌─────────────┐
  │ Increment 2 │  │ Increment 3 │  (Carve-out delete)
  │    OR        │ │    OR        │  (Named transitions)
  │ Increment 3 │  │ Increment 2 │
  └──────┬──────┘  └──────┬──────┘
         │                │
         └────────┬───────┘
                  │
                  ▼
          ┌──────────────┐
          │   Increment 4 │  (Suspension handle)
          └───────────────┘
```

- **Increment 5 (PostgreSQL invariant visibility, per the reference plan)** and the property tests ship independently — they have no dependency on increments 1–4 and can land with any of them.
- **Property tests** must ship with Increment 1 at the LATEST. Without them, Inc 2's deletion is unverified.

**Consequences:**

- Default ship order is documented; team can swap Inc 2/3 if review dictates.
- Increment 4's schema migration timing is decided with the council (see Open Questions §5).
- Property tests become a launch dependency for Inc 2 — they cannot be skipped.

**Related Increment:** All (defines sequencing constraints).

---

### D6 — Phase 5 invariant uses Python-side runtime check (Option a), not CI PostgreSQL container (Option b)

**Status:** Accepted

**Context:**

Two options for making the PostgreSQL-only constraint trigger (which enforces `admission_state='active' ⇔ JobLock` exists) test-visible in the SQLite-default dev/test stack:

- **(a) Mirror the trigger as a Python-side check inside `reconcile_turn_mirror`.** Enforce at runtime in every environment (dev, test, prod).
- **(b) Run cross-table invariant tests against PostgreSQL in CI.** Add a `pytest.mark.postgres_invariant` marker + Postgres service container.

**Decision:**

**Adopt Option (a).** `reconcile_turn_mirror` raises `InvalidTransitionError` on `active ⇔ no lock` mismatch. The PostgreSQL constraint trigger remains as defense-in-depth. Option (b) is rejected for this migration but is *available as a future enhancement* if team wants belt-and-suspenders.

**Rationale:**

- **Runtime cost.** Option (a) adds one boolean check per reconciler call (cheap, ~50ns). Option (b) requires a PostgreSQL service container in CI (typically +30s–2min per CI run depending on the container stack; also requires secrets management for the test DB; also breaks the test isolation paradigm).
- **Coverage.** Option (a) catches the invariant violation at every reconcile site (claim, resume, finalize, timeout, periodic sweep) in dev. Option (b) only catches it in CI (whenever CI runs, not when developers run the test locally). The bug class is "developers write code that violates the invariant and ship it" — only (a) catches it before merge.
- **PostgreSQL trigger stays.** Option (a) does NOT delete the PG trigger; it adds a Python-side mirror. The trigger catches violations from non-Python code paths (any admin SQL, any future direct DB access); the Python mirror catches violations from the reconciler path. Two enforcement layers, two different audiences.
- **CI cost reduction** is a desirable side-effect, not the motivation. The motivation is *every environment* gets the invariant, not just CI.

**Consequences:**

- The reconciler gains one more branch (`raise InvalidTransitionError`). Test surface for this branch must be added.
- The PG trigger is `IF`-not-modified; the comment gains a "do NOT delete — see ADR D6" note.
- CI Postgres service container is a possible follow-up (D5 next steps) but is not part of this migration.

**Related Increment:** Increment 1 (the reconciler that hosts the check). Also ships with Phase 5 of the reference plan; can land with Increment 1 as a single ship.

---

### D7 — New `task` columns require triple-registration (SQLModel + SQLite `.sql` + PostgreSQL `ALTER TABLE IF NOT EXISTS`)

**Status:** Accepted (inherited from existing constraint; re-affirmed here for the migration's scope)

**Context:**

Two prior production incidents established a binding rule: any new column on an existing table in a PostgreSQL-primary codebase MUST be registered in three places. Failure to do so produces a runtime `column does not exist` error in production that local SQLite tests happily ignore. (See critical notes — `Phase D enqueued_at column bug 2026-06-21` is the canonical incident.)

**Decision:**

Increment 4 introduces two new columns on `task`: `suspension_reason` (enum) and `resume_target_turn_id` (UUID, nullable). Each requires:

1. **SQLModel field** on the `Task` model class (`daemon/repositories/task/models.py`) with `Field(default=...)` and the `nullable` flag matching the column nullability.
2. **SQLite `.sql` migration file** in `daemon/migrations/sqlite/` named e.g. `2026_08_15_add_task_suspension_columns.sql`, containing `ALTER TABLE task ADD COLUMN suspension_reason TEXT; ALTER TABLE task ADD COLUMN resume_target_turn_id TEXT;`.
3. **PostgreSQL `ALTER TABLE IF NOT EXISTS`** in `_ensure_postgres_columns()` (the migration bootstrapper that runs at every daemon startup), using `ADD COLUMN IF NOT EXISTS suspension_reason TEXT, ADD COLUMN IF NOT EXISTS resume_target_turn_id TEXT;`.

**Rationale:**

- `.sql` migration files run against the SQLite test/dev DB only; the PostgreSQL migration bootstrapper runs against PG. They are independent code paths because the `ALTER TABLE IF NOT EXISTS` syntax is Postgres-specific and SQLite-flavored `ALTER TABLE` has different idempotency rules.
- **Skipping the SQLite `.sql`** makes local dev silently fail to add the column → all dev queries SELECT against a column that doesn't exist → opaque errors in development. The Phase D enqueued_at bug was exactly this: PG added the column via `_ensure_postgres_columns`, SQLite test DB didn't see the `.sql` migration file, and tests passed against a schema that didn't match production, hiding the bug.
- **Skipping the PG bootstrap** makes the daemon fail on first startup against a PG instance that pre-dated the migration. The trigger never fires; PG never gets the column.
- **Skipping the SQLModel field** makes the ORM silently ignore the column (SELECT reads return None; UPDATE writes silently drop the column). The model and the schema diverge, and a "why is the column blank?" debugging session follows weeks later.

**Consequences:**

- All three registrations are wired into a pre-merge checklist for Increment 4. The checklist references this ADR (D7).
- A developer forgetting any one will see the triple-checklist on the PR template before merge.

**Related Increment:** Increment 4 (only schema change in this migration). Also applies to any future column additions — this ADR is canonical for the project.

---

### D8 — `complete_task`, `cancel_task`, AND `fail_task` MUST route through `COMPLETE_TURN` / `ABORT_TURN` named transitions

**Status:** Accepted (amended 2026-08-01 to add `fail_task` as a third chokepoint; see § REVISION NOTE below)

> **§ REVISION NOTE (Council Review 2026-08-01):** This decision was originally framed for `complete_task` and `cancel_task` only. The council's review of Increment 3 (captured as blocker **B1** in `increment3-plan.md`) identified that `fail_task` (`task/repository.py:1492`) exhibits the **same dangerous split** as the other two: its SQL body touches ONLY the `task` row's `status` (set to `failed`) and forgets all 8 mirrors. It is called from **8 verified call sites**: `daemon/services/worker_pool.py:785,835`; `daemon/services/stale_task_recovery.py:262,329,468,514,583` (the wrapper at `:449-468` is itself a chokepoint bypass and must be re-routed through the named transition); `daemon/manager.py:5422`. The decision is **AMENDED**: `fail_task` is now a third chokepoint, routing through `ABORT_TURN(work_id, reason='failed')`. The full caller map (direct + indirect, including wrappers and `cancel_task_by_work_id` / `force_cancel_and_schedule_retry` / `force_complete_task` paths enumerated as B6 in `increment3-plan.md`) is documented in `increment3-plan.md` §6.5 and Appendix A of that plan.

**Context:**

`complete_task` (`task/repository.py:1437`) mutates ONLY the `task` row's `status` to `completed` and forgets every mirror. `cancel_task` (`task/repository.py:2386`) has the same shape. **`fail_task` (`task/repository.py:1492`) is the third member of this chokepoint family** — its body mutates ONLY the `task` row's `status` to `failed` and forgets every mirror. The 2026-08-01 incident root-cause analysis flagged these as the MOST DANGEROUS split of the bug class: the cascade never wrote to them, so they are not protected by any of the reconciler-less paths. They are the most-forgotten tables across the entire mirror set. The 2026-08-01 council review (B1 blocker in `increment3-plan.md`) identified that `fail_task` had been omitted from D8's original framing despite exhibiting the identical split — the amendment brings it into scope.

**Decision:**

After Increment 3 lands, **any code path that previously called `complete_task(...)`, `cancel_task(...)`, or `fail_task(...)` MUST call `COMPLETE_TURN(work_id)` / `ABORT_TURN(work_id)` / `ABORT_TURN(work_id, reason='failed')` respectively.** The legacy method bodies become thin wrappers around the named transitions (with a `DeprecationWarning` for 6 months, then hard removal — see OQ5 / OQ-INC3-2 for the resolved phased 4a/4b approach). The `ABORT_TURN` transition accepts a `reason` parameter: `'cancelled'` (from `cancel_task`) or `'failed'` (from `fail_task`); the `terminal_reason` discriminator on `job_queue_items` differs accordingly. Pre-amendment, `fail_task` callers could not preserve the `'failed'` discriminator because the named-transition contract did not exist.

**Rationale:**

- **`complete_task`/`cancel_task`/`fail_task` are the only direct mutators of `task.status`.** Every other lifecycle change goes through a cascade which (today) eventually calls one of these three methods. They are the chokepoint. The 2026-08-01 amendment adds `fail_task` because its SQL body is structurally identical to the other two — it is a third member of the same chokepoint family, not a separate concern.
- **They forget every mirror by design.** The bug class is "Author forgot to include table X in this cascade." If the chokepoint itself forgets, the chokepoint *is* the bug — every cascade is broken in the same way. With `fail_task` added, the chokepoint family now covers ALL three terminal transitions (success / cancel / fail); no terminal path is left to bypass the reconciler.
- **`COMPLETE_TURN` / `ABORT_TURN` are the only correct way.** They declare their mirror set explicitly (D10 verifies the union covers all 8 tables). A caller cannot bypass them without explicitly typing the wrong API. The `reason` parameter on `ABORT_TURN` preserves the `terminal_reason` discriminator (`'cancelled'` vs `'failed'`) that today is silently dropped on `task.status='failed'` rows because no code carries it through.
- **The `DeprecationWarning` window** catches late call-site migration. Grep-for-usage at the end of the window confirms zero stragglers; the wrappers are removed. With `fail_task` added, the grep scope extends to a third method — the deprecation window applies uniformly.

**Consequences:**

- Increment 3 introduces the wrappers; migration of call-sites is part of the same PR (no `DeprecationWarning` cost in the same release; warnings ship in the release *after* migration completes).
- The wrappers in the deprecation window are tested as the same function as the named transition (alias test). This catches "someone refactors the wrapper but not the underlying" bugs.
- **`ABORT_TURN` transition gains a `reason` parameter** (added by the 2026-08-01 amendment): accepts `'cancelled'` (from `cancel_task`) or `'failed'` (from `fail_task`). The `terminal_reason` discriminator on `job_queue_items` (`'cancelled'` vs `'failed'`) is now propagated correctly — pre-amendment, `fail_task` callers could not preserve the `'failed'` discriminator because the named-transition contract did not exist.
- **The 8 verified `fail_task` call sites are migrated in Increment 3's same PR** (per `increment3-plan.md` §2 B1, §6.5, Appendix A): `worker_pool.py:785,835`; `stale_task_recovery.py:262,329,468,514,583` (the wrapper at `:449-468` is itself a chokepoint bypass and must be re-routed); `manager.py:5422`. The integration test `tests/integration/test_complete_cancel_route_through_transitions.py` (per `increment3-plan.md` §8.5) is extended to cover `fail_task` directly.
- **Indirect callers are enumerated in `increment3-plan.md` §2 B6:** `cancel_task_by_work_id`, `force_cancel_and_schedule_retry`, `force_complete_task`, and `StaleTaskRecovery.fail_task` wrapper must all funnel into the named transitions. The property test (D10) covers the direct chokepoints; integration tests cover the indirect paths.

**Related Increment:** Increment 3 (named transitions — wraps `complete_task`, `cancel_task`, AND `fail_task`). Increment 1 also benefits (if a caller reaches any of the three before Increment 3 lands, they mutate Task but the reconciler corrects the mirrors — same safety guarantee). D11 (instances soft reconciliation) does NOT apply to the third chokepoint — `fail_task` does not have the WAITING_CHILDREN interaction (D9, D13) because failure does not produce a `waiting_children` instance state.

---

### D9 — `WAITING_CHILDREN` carve-out: RETAINED (Option a, per council review 2026-08-01)

**Status:** Accepted (RETAINED, Option a) — revised 2026-08-01

> **§ REVISION NOTE (Council Review 2026-08-01):** This decision was previously "Open (recommendation: remove; council pre-approval pending)." The council has reviewed and **rejected** the recommendation to remove. The accepted decision is to RETAIN the WAITING_CHILDREN carve-out (Option a in the pre-revision framing; Option B in this revised framing). The new D13 codifies the architectural reason. The "remove" recommendation in the pre-revision rationale below is preserved for historical reference but is no longer the accepted position.

**Context:**

`pending_count` guards (e.g. in `child_reports.py:1482-1560`) check "the parent has no in-flight `message_queue` rows." The reconciler marks `message_queue.status='completed'` when the backing Task is terminal (D1, table 3). After the reconciler is universal, any `message_queue` row counted as `processing` necessarily has a non-terminal backing Task — the semantic that `WAITING_CHILDREN` was *trying* to enforce is now mechanical.

> **§ REVISION NOTE (R1, stale-citation fix 2026-08-01):** The pre-revision citation `child_reports.py:1459-1519` is stale (off by ~40 lines). Correct location: `child_reports.py:1482-1560` (the `pending_count` guard sits inside the post-reconcile evaluation block at `:1482-1560`; the `# Single pending_count guard is sufficient.` invariant comment is at `:1540`).

**Two options (re-framed post-council):**

- **§ REVISION NOTE — Option (a) / RETAIN.** Keep the `WAITING_CHILDREN` carve-out at `repository.py:861` and `:1776`. Required because of D11 (soft reconciliation of `instances`) — see D13 for the architectural reason. The simplified predicate in Increment 2 folds the WAITING_CHILDREN clause into the new `_active_jobitem_with_inflight_task_sql` helper as its outermost conjunction.
- **§ REVISION NOTE — Option (b) / REMOVE (rejected).** The pre-revision recommendation. The simpler guard (`status='processing'` → backing Task non-terminal) would be tautologically true. **This option is REJECTED by the council** because it interacts badly with D11: the reconciler cannot force-update `instances.status`, so a `waiting_children` instance's active JobItem must stay `active` as a semaphore for the child-completion report path. Removing the carve-out would cause the simplified predicate to deadlock on the parent's in-flight `process_message` Task.

**Pre-revision "Rationale for (b) REMOVE" (preserved for historical reference; superseded):**

- **The semantic is now mechanical.** `processing` rows with terminal backing Tasks cannot exist by construction (the reconciler transitions them to `completed` everywhere the reconciler runs). The guard predicate is no longer useful.
- **Option (a) RETAIN's "defense" is an anti-pattern.** "Keep this check in case our check is bypassed" is structurally the same anti-pattern as the old carve-out pile (Shape D in the reference plan): papering over potential failures rather than making them impossible. The whole point of this migration is to *delete* that pattern.
- **However** there is ONE counter-argument: `WAITING_CHILDREN` guards transitions that happen *outside* the reconciler's call sites (e.g. a new feature added by a different team doesn't know to call the reconciler first). Keeping it provides a safety net for the unknown.

**Why the council reversed the recommendation (2026-08-01):**

The pre-revision rationale was internally consistent but failed to account for the **D11 interaction**. D11 (accepted, see §D11 below) makes `instances` soft-reconciliation only — the reconciler *verifies* instance↔Task consistency but does NOT *force-update* `instances.status`. This means:

1. A `waiting_children` instance cannot be transitioned to a different status by the reconciler.
2. The reconciler's `job_queue_items` rule (per the new D13) explicitly does NOT transition a `waiting_children` instance's active JobItem to `done` even if the Task is terminal — the JobItem is an intentional semaphore for the child-completion report path.
3. Without the WAITING_CHILDREN carve-out, the simplified `EXISTS(task WHERE work_id=job_id AND status IN (pending,running,paused))` predicate would see the parent's in-flight `process_message` Task and BLOCK the child-completion report Task from being claimed — reproducing the exact deadlock the original carve-out was designed to prevent.

The "anti-pattern" framing in the pre-revision rationale conflated two different concerns: the **mirror-lifecycle** concern (where the reconciler IS the right answer and carve-outs are anti-patterns) and the **instance-lifecycle** concern (where the reconciler is structurally unable to subsume the carve-out because D11 prevents force-update). The WAITING_CHILDREN carve-out is the latter, not the former.

**Decision (post-council):**

The WAITING_CHILDREN carve-out at `repository.py:861` and `:1776` is RETAINED. The simplified predicate in Increment 2 includes the WAITING_CHILDREN clause as its outermost conjunction (see `increment2-plan.md` §5, §6.4, and D13 for the architectural reason). The `status_waiting_children` bind stays. The `LEFT JOIN instances i ON j.instance_id = i.instance_id` joins stay.

**Consequences:**

- Increment 2 ships with the WAITING_CHILDREN carve-out intact. The simplified predicate is ~7 lines (vs. the pre-revision estimate of ~5 lines) — the "+2" is the retained WAITING_CHILDREN clause.
- A future increment MAY consider subsuming this carve-out if and only if D11 is revisited and `instances` becomes hard-reconciled. Until then, RETAIN.
- The "defense-in-depth" anti-pattern framing is rejected for this specific carve-out because the alternative (removing it) actively causes a deadlock class. Defense-in-depth is only an anti-pattern when it can be safely subsumed by the primary mechanism; here, it cannot.
- The "TODO with a sunset date" pre-revision suggestion is rejected: there is no automatic sunset trigger. Reevaluation of this decision is gated on revisiting D11.

**Related Increment:** Increment 2 (RETAIN decision); D11 (prerequisite — the reason RETAIN is mandatory); D13 (architectural reason codified); D10 verifies the property test would catch a regression in either direction.

---

### D10 — Property test MUST assert coverage of ALL 8 mirror tables

**Status:** Accepted

**Context:**

A property test (Hypothesis-style state-machine, see reference plan §9) asserts post-transition invariants. If the property test only checks the 5 reference-plan mirrors, it cannot catch a violation of mirrors 6, 7, or 8 (the tables D1 added).

**Decision:**

The property test's **mirror consistency invariant** reads:

> For every `task` row with terminal `status`, for every mirror table in the full 8-table set, the row(s) tied to that `task.work_id` are in a consistent terminal state — OR, for `instances` (D11), the consistency is verified without force-update.

Specifically:

| Mirror | Property assertion |
|---|---|
| `task` | (the authority; invariant is "status is some valid value") |
| `job_queue_items` | IF `task.status` ∈ terminal THEN `admission_state='done'` AND `terminal_reason` non-null |
| `message_queue` | IF `task.status` ∈ terminal AND `message_id` ∈ `task` THEN `status='completed'` AND `processing_task_id IS NULL` |
| `job_locks` | IF `task.status` ∈ terminal THEN no `job_locks` row with matching `job_id` exists |
| `dependency_watchers` | IF `task.status` ∈ terminal AND target Task is also terminal THEN `state='CANCELLED'` |
| `report_injections` | IF `task.status` ∈ terminal AND `task_type='process_report'` AND `report_message_id` is set THEN `state` not in pending values |
| `instances` | IF all Tasks for instance are terminal THEN `status` ∈ terminal — BUT flagged-not-failed (see D11) |
| `job_watchers` | IF `task.status` ∈ terminal THEN either no rows exist OR rows are migrated to the new `work_id` |

The unified assertion: **for every reachable state, after every sequence of transitions, all 8 mirrors are consistent with the authoritative `task`.**

**Rationale:**

- **Each missing row would re-open the bug class.** The Mirror 6 / 7 / 8 properties directly mirror the bug class shape (D1.a/b/c). Without them, the test is structurally incomplete and a regression to those bug classes would silently pass.
- **The property test is the static guarantee that D1's table-set is exhaustive.** If a future feature adds a NEW mirror (mirror 9), the property test setup function must be updated. That is the contract: a new mirror = a new property assertion = a new reconciler branch.
- **The `instances` row uses D11's pattern** — verify, not force-update. The property is "either consistent OR flagged via the drift-warning log channel." A force-update would be wrong because instance status has meaning beyond this turn.

**Consequences:**

- The property test's setup helper accepts a list of mirror tables; adding a ninth is a one-line change to setup PLUS a new assertion PLUS a reconciler branch (the triple registration, mirroring D7).
- Test runtime increases ~20% over a 5-mirror version. Acceptable.
- A violation of the property test is a P1 bug.

**Related Increment:** Increment 1 (mandatory pre-requisite for Increment 2's deletion of carve-outs).

---

### D11 — `instances` table uses soft reconciliation (verify-and-flag), not force-update

**Status:** Accepted

**Context:**

Table 7 (`instances.status`) is tree-scoped, not per-turn. A `running` instance may have many concurrent turn lifecycles (child processes, parallel tool calls, etc.). Force-updating instance status from the reconciler would cause: "this single Task went terminal → mark the instance terminated" — destroying sibling turns.

**Decision:**

The reconciler's `instances` branch:

1. **Reads** `instances.status` for `task.instance_id`.
2. **Computes** the "all tasks for this instance" aggregate status.
3. **If consistent with `task.status` change** → no-op.
4. **If inconsistent** (e.g. all Tasks for instance are terminal but instance is `running`) → logs a `drift` warning at WARN level, increments a metric counter, but DOES NOT update `instances`.
5. **The instance status is corrected by** the cascade (`_pause_cascade_db_sync` / `_resume_cascade_db_sync` / `_finalize_job_db_db_sync`) which is the proper authority, or by the periodic sweep in `JobRecoveryService`.

**Rationale:**

- **Tree-scoped ≠ per-turn.** `instances.status` semantics: "what's this *tree* doing?" It is not a 1:1 mirror of any single `task.status`. Forcing it through a per-turn reconciler creates a *new* bug class: a child turn's completion prematurely terminates the parent instance.
- **The reconciler is a verification layer, not an authority, for `instances`.** The cascade is the authority (it understands the tree shape); the reconciler only checks its work.
- **Verification is not no-op.** A `drift` warning that fires once per thousand cycles is fine. A `drift` warning that fires 100/minute is a production incident (and the metric surfaces it before the symptom). The reconciler's signal value comes from frequency, not from force-correcting.

**Consequences:**

- The `instances` reconciler branch is structurally different from the other 7 (verify, not mutate). The branch is gated on `if drift_log_only:` — easy to disable for debugging.
- Drift metric: `reconciler_instance_drift_count_per_minute`. Threshold: < 1/hr is healthy, 1–10/hr is degraded, > 10/hr is incident.
- A future feature that genuinely wants per-turn instance updates (rare) must declare it as a *named transition* (Increment 3) so the cascade-and-tree logic is correct.

**Related Increment:** Increment 1 (the verify branch). Carved-out — D8's complete-task / cancel-task routing does NOT include instances.

---

### D12 — Do NOT merge the mirror tables; this migration reinforces the three-table split

**Status:** Accepted (inherited from `defer-queue-and-job-task-seam-bugs.md` §1, re-affirmed)

**Context:**

The naive reader asks: "Why have three tables with hand-synced lifecycles? Merge them." The team has already evaluated and rejected merging (`defer-queue-and-job-task-seam-bugs.md` §1). This migration is the structural response: do NOT merge; instead, reconcile the three tables through one authority.

**Decision:**

The three tables (`task`, `job_queue_items`, `message_queue`) — plus the 5 derived/existence tables (`job_locks`, `dependency_watchers`, `report_injections`, `instances`, `job_watchers`) — stay as separate tables. The migration introduces one **authority** (the reconciler + named transitions) that owns their consistency, but the schema is unchanged.

**Rationale:**

The three primary tables carry distinct concerns:

| Table | Concern | Why not merge |
|---|---|---|
| `task` | Drives `graph.astream(thread_id)`. The dispatch row. | Has its own retry lifecycle, suspension state, fault domain per task type. |
| `job_queue_items` | Admission policy (queued/active/done/dead), slot allocation, DLQ, retry/visibility window. | Has DLQ semantics, retry budgets, slot leasing — none of which belong on a graph stream row. |
| `message_queue` | Payload delivery audit (ready/processing/completed). | Has payload (potentially JSONB-large), processing_task_id, retry counters — orthogonal to admission. |

The team quote (`defer-queue-and-job-task-seam-bugs.md` §1):

> *"The two-table split is a deliberate decoupling of queue-policy from execution … the merge-into-one-table alternative was evaluated and rejected (it would fold two orthogonal responsibilities into one object, creating a large hard-to-debug logic blob)."*

That judgement is correct for the **same reasons** that the reconciler is the right fix:

- **A merged table has its own bug class:** every SELECT now fans out across the union of fields; every UPDATE writes a wider row; every business rule is hidden behind a "where appropriate" WHERE clause. Debugging goes from "which table broke?" to "which subset of the row broke?"
- **The reconciler IS the merge, done correctly.** It produces one source of truth (Task) and three derived mirrors. The author sees one API; the database sees three tables. The team's correct prior refusal to merge the tables does NOT mean refusing to unify the lifecycle — it means refusing to unify the *storage*. We unify the lifecycle by introducing the authority; we keep the storage split for its orthogonal-concern benefits.

**Consequences:**

- The schema is unchanged. No migration of data, no ALTER TABLE … MERGE statements, no DDL risk.
- The reconciler is the *single point of control* for the lifecycle, even though the storage is still distributed.
- A future reader who asks "why three tables?" gets this ADR and the original `defer-queue-and-job-task-seam-bugs.md` §1 as the answers.

**Related Increment:** None directly (axiomatic — the migration's structure follows from D12). Every other decision assumes D12.

---

### D13 — `WAITING_CHILDREN` is an instance-lifecycle semantic state, not a mirror-consistency state. The reconciler cannot subsume it.

**Status:** Accepted (2026-08-01, council review of Increment 2)

> **§ REVISION NOTE (Council Review 2026-08-01):** This decision is **NEW** — added by the 2026-08-01 council review of Increment 2. It codifies the architectural reason that the WAITING_CHILDREN carve-out (D9) MUST be retained. D13 is the load-bearing rationale: it explains *why* the reconciler cannot subsume the carve-out even when the reconciler is universal, by tying the carve-out's necessity to D11 (soft reconciliation of `instances`). D13 + D11 form a single architectural pattern; the two are coupled. Without D13, a future reader of D9 might re-propose REMOVE on the same flawed reasoning the council rejected.

**Context:**

D11 makes `instances` soft-reconciliation only — the reconciler *verifies* instance↔Task consistency but does NOT *force-update* `instances.status`. This is correct for the `instances` table: instance status is tree-scoped, not per-turn, and force-updating would cause a child turn's completion to prematurely terminate the parent instance (see D11 rationale).

The WAITING_CHILDREN carve-out at `repository.py:861` and `:1776` interacts with this: a `waiting_children` instance's `process_message` Task has finished its turn (status: `completed`), but the instance is awaiting child-completion reports. The corresponding `job_queue_items` row is `active` and the `message_queue` row is `processing` — and the reconciler, per D11, MUST NOT touch the `instances.status` row or the active JobItem.

The pre-revision version of D9 recommended removing the WAITING_CHILDREN carve-out on the (valid but incomplete) reasoning that the reconciler would subsume it. The council's 2026-08-01 review identified that this reasoning fails the D11 interaction: removing the carve-out would cause the simplified predicate to deadlock on the parent's in-flight Task when the instance is in `waiting_children` state.

**Decision:**

The cross-system guard's WAITING_CHILDREN carve-out at `repository.py:861` and `:1776` is RETAINED. The reconciler's `job_queue_items` rule (Increment 1's `reconcile_turn_mirror`) explicitly does NOT transition a `waiting_children` instance's active JobItem to `done` even if the Task is terminal. The JobItem is an intentional semaphore for the child-completion report path. Increment 2's simplified predicate (`_active_jobitem_with_inflight_task_sql`) includes the WAITING_CHILDREN clause as its outermost conjunction.

Concretely, the reconciler's branch logic on `job_queue_items` is:

```python
# In reconcile_turn_mirror (Increment 1):
if i.status == InstanceStatus.WAITING_CHILDREN.value:
    # D13: JobItem is an intentional semaphore. DO NOT transition
    # to 'done' even if the Task is terminal. The cross-system
    # guard's WAITING_CHILDREN carve-out (D9 RETAINED) is what
    # makes the guard treat this JobItem as inert.
    pass  # leave JobItem as-is
else:
    # standard reconciler logic: transition JobItem to 'done' if
    # backing Task is terminal or absent
    ...
```

**Rationale:**

- **D11 makes the reconciler structurally unable to subsume the carve-out.** Force-updating `instances.status` would be wrong (D11); the alternative is to leave the `waiting_children` instance's JobItem `active` and rely on the guard's WAITING_CHILDREN clause to identify it as inert. The two are coupled: D11 + the carve-out form a single architectural pattern.
- **The "anti-pattern" framing of the pre-revision D9 was correct for mirror-lifecycle concerns but wrong for instance-lifecycle concerns.** The reconciler IS the right answer for `job_queue_items`, `message_queue`, `job_locks`, etc. (D1, D2, D10). It is NOT the right answer for `instances` (D11) or for the correlated `job_queue_items` row when the instance is in a state that the reconciler cannot transition out of.
- **The simplified predicate preserves the coupled invariant.** The `_active_jobitem_with_inflight_task_sql` helper is the single source of truth for the cross-system guard. It contains both the `EXISTS(task WHERE work_id=job_id AND status IN (pending,running,paused))` check (the reconciler-driven part) and the `AND (i.status IS NULL OR i.status != :status_waiting_children)` check (the D11-coupled carve-out). The two clauses are co-located so a future reader sees the architectural coupling.
- **Removing the carve-out is unsafe but adding a third clause is unnecessary.** The D9 v1 plan recommended REMOVE; the council rejected this. A future proposal to ADD a third clause (e.g., for another instance state) would need a separate decision (D14 or later) with the same architectural-justification bar.

**Consequences:**

- Increment 2 ships with the WAITING_CHILDREN carve-out intact. The simplified predicate is ~7 lines (vs. the pre-revision estimate of ~5 lines) — the "+2" is the retained WAITING_CHILDREN clause.
- The reconciler's `job_queue_items` rule (Increment 1) is now explicitly documented to NOT transition a `waiting_children` instance's active JobItem to `done`. The rule is enforced by:
  1. Code review (the branch logic above is the contract).
  2. The W4 retry-regression fixture at `tests/unit/test_pause_resume_root.py:861` (`def test_retry_scenario_parent_cancelled_retry_pending_returns_none`, docstring at `:864`), which exercises the `parent_cancelled + retry_pending + waiting_children_instance` matrix.
  3. The forward-compatibility test at `tests/integration/test_simplified_predicate_claimturn_parity.py` (C8, see `increment2-plan.md` §8.6), which asserts the simplified predicate produces identical results across claim paths.
- A future increment MAY consider subsuming this carve-out if and only if D11 is revisited and `instances` becomes hard-reconciled. Until then, the coupled D11 + D13 pattern is the accepted design.
- A future reader who asks "why is the WAITING_CHILDREN clause still here if the reconciler is supposed to subsume everything?" gets D11 + D13 as the answer. The two together explain the architectural coupling.

**Related Increment:** Increment 1 (reconciler SQL MUST implement the exception), Increment 2 (RETAIN decision); D9 (reclassified from "Open — recommend REMOVE" to "Accepted — RETAIN Option a"); D11 (prerequisite — the reason RETAIN is mandatory); D10 (verifies the property test would catch a regression).

> **§ REVISION NOTE v3 (Approver Review 2026-08-01):** The Approver's Issue 2 found that while D13 documents the WAITING_CHILDREN exception in the reconciler's branch logic (lines 559-571 above), the authoritative Increment 1 plan (`increment1-plan.md` §4, mirror table #2) OMITS the exception from the actual `job_queue_items` UPDATE SQL. The exception is now defined in Inc 1's SQL `WHERE` clause so Inc 2 can rely on it. Inc 1 v3 adds paired unit tests (waiting_children → JobItem stays active; non-waiting_children → JobItem transitions to done). Without this, Inc 1's reconciler would transition a `waiting_children` instance's active JobItem to `done`, breaking child-completion correlation.

---

### D14 — Claim-time reconciliation runs AFTER the cross-system guard (by design)

**Status:** Accepted (2026-08-01, Approver review of Increment 1, Issue 3)

**Context:**

The reconciler is called from `claim_pending_task` AFTER the `with engine.begin()` transaction commits (Inc 1, call site 2a). But the cross-system guard runs INSIDE the claim SQL at `repository.py:1060-1180` — BEFORE the reconciler fires. If an orphaned JobItem blocks the claim, the inner SELECT returns no rows, the claim returns None, and the reconciler never gets to run because there's no claimed Task to reconcile against.

**Decision:**

The current ordering (reconciler AFTER claim) is correct (Approver Option b). The reconciler at claim time CANNOT unblock a blocked claim — this is by design. Inc 2's carve-out deletion is ONLY safe because the reconciler runs on ALL other paths (resume, pause, finalize, timeout, periodic sweep) to ensure no orphans survive to claim time.

**Rationale:**

- Orphans are created at transition moments (pause, resume, finalize, timeout). The reconciler fires at EACH of these (call sites 2b-2e), correcting the orphan immediately.
- If a transition crashes before the reconciler fires, the periodic sweep (`reconcile_drift_states`, call site 2f) catches the orphan within N seconds.
- By the time a claim query runs, the orphan has already been corrected by the cascade's reconciler or the periodic sweep.
- The narrow window (crash → next sweep) produces a temporary stall, not a deadlock — the claim retries on the next `notify_work` cycle and succeeds once the sweep corrects the orphan.

**Consequences:**

- Inc 1 plan must document this ordering explicitly (Inc 1 v3 §5.2).
- Inc 2 readiness gate must verify the periodic sweep is active and running at the expected interval before carve-out deletion.
- The reconciler at claim time is still valuable — it catches any orphan that slipped through (defense in depth), even though it can't unblock the current claim.

**Related Increment:** Increment 1 (call site 2a ordering), Increment 2 (dependency: reconciler on all paths is the precondition for safe carve-out deletion).

---

## 3. Increment Sequencing

### 3.1 Canonical dependency graph

```
            ┌─────────────────────────────────────┐
            │             Foundation               │
            │  Property tests (reference plan §9)   │
            │  + PG invariant (Phase 5 / D6)       │
            │  + reconciler interface contract     │
            └────────────────────┬─────────────────┘
                                 │
                                 ▼
            ┌─────────────────────────────────────┐
            │        Increment 1 — Reconciler      │
            │  reconcile_turn_mirror(work_id)      │
            │  Handlers at claim/resume/finalize/  │
            │  timeout + periodic sweep.            │
            │  Soft-reconcile for instances (D11). │
            │  Property tests green.                │
            └────────────────────┬─────────────────┘
                                 │
                  ┌──────────────┴──────────────┐
                  │                             │
                  ▼                             ▼
       ┌───────────────────────┐   ┌──────────────────────────┐
       │  Increment 2 — Carve- │   │   Increment 3 — Named    │
       │  out deletion          │   │   transitions             │
       │  Delete queued-orphan  │   │   turn_transitions.py    │
       │  NOT EXISTS; delete    │   │   required mirror set     │
       │  _admitted_task_       │   │   declared per transition │
       │  carve_out_sql.         │   │   D8 — complete_task /   │
       │  ≥7d telemetry green.  │   │   cancel_task / fail_task │
       │  D9 decision lands.    │   │   route here (D8 amended  │
       │  (D9 RETAINED; see      │   │   2026-08-01).          │
       │  D13 for the           │   │   Migration of call-     │
       │                        │   │   sites in same PR.       │
       └──────────┬─────────────┘   └──────────────┬────────────┘
                  │                                 │
                  └──────────────┬──────────────────┘
                                 │
                                 ▼
            ┌─────────────────────────────────────┐
            │  Increment 4 — Suspension handle     │
            │  New task columns (D7 triple-reg).   │
            │  resume_processing_job reroutes     │
            │  through the handle.                │
            │  find_paused_or_running_by_instance │
            │  deleted; find_resume_root_         │
            │  candidate_by_active_job replaced.  │
            └─────────────────────────────────────┘
```

### 3.2 Inter-increment dependencies

- **Property tests / PG invariant** → **Inc 1** (test infrastructure must exist before deletions are defensible)
- **Inc 1** → {**Inc 2**, **Inc 3**} (both need the reconciler; both are safe to land after it)
- **Inc 2 ↔ Inc 3** — interchangeable; team chooses
- **{Inc 2, Inc 3}** → **Inc 4** (Inc 4's resume rerouting is safer against stable renamed cascades than against unstable hand-written cascades)

### 3.3 Release packaging

The reference plan suggests shipping **Increment 1 + Phase 5 + property tests** as a single release (the recommended "minimum viable safety" cut). This ADR endorses that packaging:

| Release | Contents |
|---|---|
| **v1 — Reconciler foundation** | Inc 1 + Phase 5 + Property tests |
| **v2 — Carve-out collapse** | Inc 2 (or Inc 3 if chosen as second) |
| **v3 — Transition unification** | Inc 3 (or Inc 2) |
| **v4 — Suspension handle** | Inc 4 |

Each release is independently revertable, ships behind no feature flag (releases are observable through telemetry), and is gated on the prior release's success criteria (D3 telemetry gate for v2).

### 3.4 Estimated effort

| Increment | Lines added (loose) | Lines removed | Wall-clock estimate |
|---|---|---|---|
| 1 | ~1200 (reconciler + property tests) | ~140 (UPDATE 4 + D4.c) | 4–6 weeks |
| 2 | ~50 (drift metric + named metric) | ~100 (carve-outs + WAITING_CHILDREN) | 1–2 weeks |
| 3 | ~600 (turn_transitions module) | ~400 (cascade SQL replaced) | 2–3 weeks |
| 4 | ~500 (handle + reroute) | ~125 (`find_resume_root_candidate`) | 3–4 weeks |

**Total:** ~2300 added, ~765 removed, ~10–15 weeks end-to-end. Allows one rollback week per increment; ~15–20 weeks with realistic slack.

---

## 4. Risk Register

The risks below assume the migration proceeds per Section 3. Mitigations are concrete, not aspirational.

| # | Risk | Impact | Likelihood | Mitigation |
|---|---|---|---|---|
| 1 | Increment 1 ships; reconciler has a latent bug that only fires under specific transition sequences. Telemetry gate (D3) catches user-visible impact, but property tests cover only modeled sequences. | High (orphan mirrors reintroduced in production) | Medium | **Property tests run randomized sequences for ≥10 minutes before merge.** Telemetry gate requires `reconciler_corrections_per_hour == 0 AND zero P2+ incidents for 7 days` before Inc 2 ships. If metric > 0 AND no incident, hardening sprint blocks Inc 2. |
| 2 | Increment 2 (carve-out deletion) ships while some transitions still bypass the reconciler. The "blocked if active+terminal-task" guard falls; orphans can be claimed and cause the original deadlock. | High (Bug A re-fires) | Medium | D3's telemetry gate requires the reconciler to actually run on the relevant paths (asserted in property tests). The reconciler reads `Task.work_id`; if `instances WHERE id IN (transitions-that-bypass)` is non-empty in production, Inc 2 does not ship. |
| 3 | Increment 3 (named transitions) introduces a new module (`turn_transitions.py`) that the team is unfamiliar with; call sites are migrated partially, leaving dual-write windows. | Medium (inconsistent state) | Medium | D8's `DeprecationWarning` window (6 months minimum, see OQ5 / OQ-INC3-2 phased 4a/4b) catches late migrations. Grep-for-usage at merge time of any new code that still calls `complete_task` / `cancel_task` / `fail_task` directly. Per the D8 amendment (2026-08-01), the grep scope extends to all three chokepoints so partial migration is caught at PR time. |
| 4 | Increment 4's schema migration (D7 triple-registration) breaks against an existing PG instance with old schema + no downtime window. | Medium (incidents at startup) | Low | All three registrations are wired into a pre-merge dry-run script that boots the daemon against a clean PG + SQLite instance and asserts all three read the same column. Migration is backwards-compatible (`ALTER TABLE IF NOT EXISTS`). |
| 5 | Property tests produce a Hypothesis health-check failure (e.g. too many examples failing indicates an invariant violation). | Medium (test infrastructure can't validate the migration) | Low | Health-check failures are themselves alarms. They signal an invariant is too strict (e.g. over-counted `processing` rows) and must be relaxed to match real-world async timing — those relaxations are reviewed separately and become permanent test config. |
| 6 | Increment 4's `resume_target_turn_id` column has a bug in the FK shape (e.g. doesn't actually reference `task.work_id` for cross-table consistency). | High (handle inconsistency) | Low | D7's PostgreSQL `ALTER TABLE IF NOT EXISTS` includes a NOT VALID foreign-key check that is validated post-migration in a separate script. Property test (D10) asserts the column is populated correctly for suspended turns. |
| 7 | A team member unfamiliar with the reconciler writes a new cascade that bypasses it (e.g. direct SQL in a service). | High (regression) | Medium | Code review checklist: "Does this PR introduce a direct SQL UPDATE/INSERT/DELETE on `task`, `job_queue_items`, `message_queue`, `job_locks`, `dependency_watchers`, `report_injections`, or `job_watchers`? If yes, it MUST go through the reconciler or a named transition." Linter rule is a follow-up but not part of this migration. |
| 8 | The reconciler + named transitions perform additional DB round-trips that raise p95 latency on hot paths (claim, resume). | Low (some operators care) | Medium | Performance benchmark run as part of Inc 1's acceptance. Threshold: p95 increase < 10%. If higher, the reconciler is extended with a fast-path (skip when no orphans detected) or batched transaction. |
| 9 | The reconciler's contention with the periodic sweep (`JobRecoveryService`) creates lock contention. | Medium (slowdowns + timeouts) | Low | Both call sites use `WriteGuardSession` and are ordered by `instance_id` + `work_id` (deterministic ordering). Lock timeout is observable and surfaced as a metric. |
| 10 | The migration is sized big enough to lose momentum if Inc 1 stalls. | High (abandoned migration = orphaned mirror problem forever) | Medium | Each increment is independently shippable; if Inc 1 stalls, the system is at LEAST as good as today (reconciler is additive). The team can pause the migration with no regression — only opportunity cost. |

---

## 5. Open Questions

Items below are unresolved by design. Each should be resolved in council or by the team before that increment lands.

### OQ1 — Property test runtime budget

Property tests (D10) cover the full state machine. Naive Hypothesis fuzzing can produce 100k+ examples per test run. The proposed target is "the test takes no more than 60 seconds in CI." If the configured example count is too low, invariants slip past; too high, CI time grows.

**Decision needed:** What is the team's tolerance for CI test runtime? Recommended: 5 minutes per incremental PR, 30 minutes for the v2/v3/v4 releases.

### OQ2 — `WAITING_CHILDREN` semantic (D9) — RESOLVED 2026-08-01

> **§ REVISION NOTE (Council Review 2026-08-01):** This open question is **RESOLVED**. D9 was previously OPEN with the recommendation to REMOVE. The council reviewed D9 (now RETAINED, Option a) and D13 (new — WAITING_CHILDREN is an instance-lifecycle semantic state, not a mirror-consistency state) and concluded that the carve-out is mandatory because of the D11 (soft reconciliation of `instances`) interaction. See D9 (with REVISION NOTE) and D13 for the full architectural reason. The "tripwire with sunset" option is **REJECTED** — there is no automatic sunset trigger; reevaluation is gated on revisiting D11.

D9 recommends REMOVE, counter-argued by "defense-in-depth for unknown future transitions." The team needs to make the call.

**Option A (recommended):** Remove. The named-transition chokepoint (D8) makes bypass structurally impossible.

**Option B:** Keep with a documented sunset. The carve-out becomes a "tripwire" — fires once and is then deleted.

**Resolution (2026-08-01):** RETAINED (D9, D13). Both D9 and D13 codify the architectural reason: removing the carve-out causes a deadlock when the simplified predicate sees a `waiting_children` instance's in-flight `process_message` Task. The cross-system guard retains ONE special case (WAITING_CHILDREN) beyond the simplified `EXISTS` predicate. This decision is not revisited unless D11 is changed.

### OQ3 — Increments 2 / 3 ordering

Section 3 documents that Inc 2 and Inc 3 are interchangeable. Which order does the team prefer?

**Option A:** Inc 2 first (carve-out deletion before rename). Simpler SQL surface; risk window is one big delete.

**Option B:** Inc 3 first (named transitions before carve-out deletion). More code, but the rename makes the deletions feel like cleanup of "now-redundant logic" rather than "removed defenses."

**Recommendation:** Option B (the migration feels like renaming things to see what's redundant, which fits the team's review style).

### OQ4 — Increment 4 schema migration timing

D7 requires triple-registration. The migration can run before or after the v4 release; CI tests vs. production rollout.

**Option A:** Migration lands in v4 (alongside the code). New column is unused in v1–v3; it's just a no-op until v4 reads it.

**Option B:** Migration lands in a separate small release between v3 and v4. Schema-only; no code change.

**Recommendation:** Option A — single release per increment.

### OQ5 — Deprecation window for `complete_task` / `cancel_task` / `fail_task`

D8 (amended 2026-08-01 to include `fail_task` as a third chokepoint, see § REVISION NOTE on D8) prescribes a 6-month window between "call-site migration lands" and "legacy method bodies are deleted." Some teams prefer 3 months; some 12. Increment 3's `increment3-plan.md` §13 OQ-INC3-2 documents the team's resolution: phased 4a/4b approach, no formal deprecation window for the initial refactor (call-site migration is part of the same PR); staged migration remains a council option if subsequent releases need it.

**Decision needed:** Confirm duration for the legacy wrapper `DeprecationWarning` (when wrappers enter the deprecation window in a follow-up release). Recommended: 6 months (long enough for two minor release cycles, short enough that the deprecation actually happens). The grep-for-usage scope now covers three method names: `complete_task`, `cancel_task`, `fail_task`.

### OQ6 — Drift metric threshold

D11 introduces `reconciler_instance_drift_count_per_minute` as a health signal. The thresholds proposed are < 1/hr healthy, 1–10/hr degraded, > 10/hr incident. The team should confirm or adjust.

**Decision needed:** What are the operational thresholds? Will PagerDuty integrate, or is monitoring-only?

### OQ7 — Code-review checklist vs linter rule

Risk 7 in Section 4 suggests "Does this PR introduce a direct SQL UPDATE/INSERT/DELETE on a mirror table? If yes, it MUST go through the reconciler or a named transition." This is currently proposed as a manual checklist item. A linter / pre-commit hook would catch it automatically.

**Decision needed:** Is the linter in scope for this migration, or a follow-up?

### OQ8 — Interaction with `report-lane-decoupling.md`'s carve-out

`PROCESS_REPORT` task type has its own carve-out logic (per `docs/plans/report-lane-decoupling.md`). The reconciler handles `report_injections` (table 6), but the *routing* of which mirror gets bypassed in `claim_pending_task` for the report lane is part of Phase 4's guard simplification.

**Decision needed:** Should the migration include the report-lane carve-out simplification, or leave it for a follow-up plan?

---

## Appendix A — Quick reference

| Decision | One-line summary | Increment |
|---|---|---|
| D1 | 8 mirror tables (not 5) | 1 |
| D2 | `work_id` is the correlation axis | 1 |
| D3 | Reconciler additive; carve-out delete later | 1, 2 |
| D4 | 3 REPLACE, 2 COEXIST | 1, 2, 4 |
| D5 | 4 increments independently shippable | all |
| D6 | Python-side invariant check (Option a) | 1 |
| D7 | Triple-registration for new columns | 4 |
| D8 | `complete_task` / `cancel_task` / `fail_task` → transitions (amended 2026-08-01) | 3 |
| D9 | `WAITING_CHILDREN` RETAIN (council-resolved 2026-08-01) | 2 |
| D10 | Property test covers all 8 tables | 1 |
| D11 | `instances` is soft reconciliation | 1 |
| D12 | No table merge | all |
| D13 | WAITING_CHILDREN is instance-lifecycle (D11-coupled carve-out) | 1, 2 |

## Appendix B — Cross-reference index

| Reference | Where it appears |
|---|---|
| `.agents/shared/planning/fix-pause-report-turn-orphan/decisions.md` D-REV-1, D-REV-6 | D4 (REPLACE dispos), D2 (correlation) |
| `docs/plans/turn-reconciler-named-transitions.md` §4.1, §4.4, §5.1, §6.2, §8.1 | D1, D5, D6, D8, D10 |
| `docs/bugs/defer-queue-and-job-task-seam-bugs.md` §1 | D12 (the merge-rejection canonical statement) |
| `docs/plans/virtual-job-management-surface.md` | D2 (the `work_id` precedent) |
| `docs/plans/report-lane-decoupling.md` | D4.d (PROCESS_REPORT coexistence), OQ8 |
| `docs/plans/unified-dispatcher.md` / `decouple-job-task-message-correlation.md` | Background only (assumed landed) |
| `docs/architecture/job-as-queue-proxy-invariants.md` §2d.5 (status-drift warning) | Inc 1 deletion; D11 reconciliation supersedes it |

## Appendix C — Glossary

- **Authority (table):** the single source of truth for a fact; all mirrors are derived from it.
- **Mirror (table):** a derived table whose rows reflect the authority's state plus an orthogonal concern (DLQ, locking, payload, etc.).
- **Reconciler:** `reconcile_turn_mirror(work_id)` — the routine that ensures mirrors match the authority.
- **Named transition:** a typed function (`SUSPEND_TURN`, `COMPLETE_TURN`, etc.) that mutates the authority AND declares the mirrors it touches.
- **Carve-out:** a `NOT EXISTS` subquery in a guard predicate that excludes a known orphan shape (legacy anti-pattern).
- **Soft reconciliation:** verify-and-flag, not force-update (D11).
- **Work_id:** the `Task.work_id` UUID; the authoritative correlation handle (D2).
- **Mirror set:** the union of mirror tables; today 8 tables (D1).
- **Suspension handle:** `task.suspension_reason` + `task.resume_target_turn_id` (Increment 4).

---

**End of document.**
