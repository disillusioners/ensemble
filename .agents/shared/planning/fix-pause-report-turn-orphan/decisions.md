# Decisions Log: Fix Pause-During-Report-Turn Orphan JobItem

> **Revision 2 (2026-08-01):** Major revision per active council review. Added D-REV-1 through D-REV-5 (work_id re-keying, F1 race-window acceptance, SQL polarity correction, cascade-scoped UPDATE 4, guard site audit). Revised D-B-1 (drop restricted to completion_report), D-B-3 (restricted to completion_report only), D-B-5 (RETURNING-scoped concurrency). Added hybrid bridge strategy (D-REV-6).

Date: 2026-08-01
Status: Ready for Review (Revision 2)

---

## D1 — Phase Sequencing: Bug A (deadlock) before Bug B (stuck terminal state)

**Decision:** Phase 1 (Bug A) ships before Phase 2 (Bug B).

**Rationale:** Bug A is a **deadlock** — the instance cannot make any forward progress at all. Bug B is a **stuck terminal state** — the instance completes its work but cannot reach `COMPLETED`. By criticality, the deadlock must be resolved first.

---

## D2 — Phase 1 Step Sequencing: Guard hardening (Step A) before routing fix (Step B)

**Decision:** Step A ships and validates BEFORE Step B.

**Rationale:** Step A has minimal blast radius (only predicates change) and fixes the deadlock for any code path. Step B mutates the resume routing (a hotter path). Step A remains as defense-in-depth even after Step B ships.

---

## D3 — Phase 2 Sub-step Sequencing: Cascade reconciliation (2.A) → guard hardening (2.B) → cleanup (2.5)

**Decision:** 2.A before 2.B before 2.5.

**Rationale:** 2.A closes the source; 2.B makes the guard robust; 2.5 cleans up existing stuck instances. All must land.

---

## D-REV-1 (CRITICAL) — Re-key ALL orphan correlation from `message_id` to `work_id`

**Decision:** All orphan detection correlates via `Task.work_id == JobItem.job_id`, NOT via `task.message_id`.

**Rationale:** `schedule_retry` (`repository.py:1793-1935`) mints a fresh `work_id` but reuses the parent's `message_id`. A `message_id`-keyed `NOT EXISTS` finds the fresh PENDING retry Task and blocks it — reproducing the exact deadlock via an automatic code path (retry cycle ~10 min). The `work_id`-keyed correlation is a direct column-to-column join (no JSON extraction), already established at `repository.py:640-645`.

**For Phase 2:** `message_queue` has no `work_id` column. Correlation uses two paths: (1) direct `processing_task_id → Task.id → work_id`, (2) `message_id` as candidate locator when `processing_task_id` is NULL, projected to `work_id`s with mixed states preserved.

**Supersedes:** D-A-2 (which described the `NOT EXISTS` via `message_id`).

---

## D-A-1 — Shared SQL predicate for claim guard and busy-probe (P1/F11 invariant)

**Decision:** Single alias-parameterized SQL helper interpolated at both `claim_pending_task` and `has_pending_tasks_blocked_by_busy_instance`.

**Rationale:** The P1/F11 invariant requires the two predicates to agree. A shared helper makes this structural. (Unchanged from Revision 1.)

---

## D-A-3 — Routing primitive returns Task (with `work_id`), not JobItem ID

**Decision:** `find_resume_root_candidate_by_active_job` returns the terminal backing `PROCESS_MESSAGE` Task (including `work_id`).

**Rationale:** The existing root path consumes `Task.work_id`. Returning a raw JobItem ID would cause skipped cleanup. (Unchanged from Revision 1.)

**Amendment (W2):** `work_id` must be threaded through to `_process_resume_finalize` via the exact-ID overload: `_get_processing_job_for_instance(instance_id, job_id=work_id)`. This avoids resolving the wrong JobItem when multiple historical rows exist.

---

## D-A-4 — Fallback routing only when existing lookup returns None

**Decision:** The new active-orphan fallback is invoked ONLY when `find_paused_or_running_by_instance` returns `None`.

**Rationale:** Preserves the root-vs-child contract for all normal cases. (Unchanged from Revision 1.)

---

## D-REV-2 (W3) — F1 race-window trade-off: EXPLICITLY ACCEPTED

**Decision:** The broadened carve-out accepts a narrow race window (<1s) where the parent might still be mid-`astream` (Task completed but graph stream not fully unwound).

**Rationale:** The window is bounded by Task→graph-stream teardown latency. It is strictly less severe than the permanent deadlock it prevents. Documenting this acceptance makes the trade-off explicit rather than implicit.

---

## D-B-1 (REVISED) — Re-arm vs drop for orphaned `completion_report` messages (RESOLVED: drop, RESTRICTED to completion_report)

**Decision:** Orphaned rows are marked `status='completed'` (drop). **Restricted to `completion_report` type only** (Revision 2 change — C5).

**Rationale:** Production evidence supports drop (reports already consumed by parent). However, C5 restriction is added because a terminal Task doesn't prove content was consumed for other message types. Restricting to `completion_report` prevents data loss for `human`/`error_report` messages.

---

## D-B-2 — Single SQL UPDATE with NOT EXISTS vs N+1 (RESOLVED: scoped single SQL)

**Decision:** Single batched UPDATE scoped via RETURNING from the Task-cancel statement.

**Rationale:** Atomic with the Task transition (all-or-nothing commit). One DB round-trip. `NOT EXISTS` is well-established in the codebase. (Amended to use RETURNING scope per C3/C4.)

---

## D-B-3 (REVISED) — Scope of UPDATE 4: completion_report only, THIS cascade only

**Decision:** UPDATE 4 reconciles ONLY `completion_report` rows whose backing Task was cancelled by THIS resume cascade (captured via RETURNING). NOT tree-wide, NOT all message types.

**Rationale:** C4 (scope to THIS cascade) prevents reconciling historical incidents. C5 (restrict to completion_report) prevents dropping unconsumed content for other message types.

**Supersedes:** Original D-B-3 (tree-wide, all message types).

---

## D-B-4 — Guard hardening at all reachable parent-completion sites

**Decision:** The shared positive-polarity predicate is applied at: `child_reports.py:1459` (reachable) + `:863`, `:2058`, `error_reporting.py:270` (bus-gated fallbacks). Child report-decision queries (`:623/637/1598/1610`) are audited but unchanged.

**Rationale:** C6 audit found 8 total sites. Only 1 is reachable in production (1459). 3 are bus-gated fallbacks (hardened as future-proofing, not deleted). 4 are child-report-decision logic (different concern, unchanged).

---

## D-B-5 (REVISED) — UPDATE 4 added to RESUME cascade, scoped via RETURNING CTE

**Decision:** UPDATE 4 is in `_resume_cascade_db_sync`, immediately after UPDATE 2 (Task cancel), before UPDATE 3 (JobItem activation). PostgreSQL uses a data-modifying CTE; SQLite captures RETURNING rows.

**Rationale:** The orphan is created when Task transitions `PAUSED → CANCELLED`. The RETURNING scope ensures atomicity within the CTE statement and prevents reconciling rows outside this cascade. WriteGuardSession provides all-or-nothing commit, NOT cross-connection serialization (C3 correction).

**Supersedes:** Original D-B-5 (claimed atomic serialization).

---

## D-B-6 — Guard hardening uses defensive "no-Task = count" semantics

**Decision:** The broadened guard only EXCLUDES a row when `EXISTS(terminal Task) AND NOT EXISTS(non-terminal Task)`. Rows with no Task are COUNTED.

**Rationale:** A row with no backing Task might be legitimate in-flight work. Counting it is conservative and correct. (Unchanged from Revision 1, but now expressed as positive polarity per C1.)

---

## D-REV-3 (C1) — SQL polarity CORRECTED: positive condition replaces inverted NOT EXISTS

**Decision:** The guard uses a POSITIVE condition: count a row when `(no Task exists) OR (a non-terminal Task exists)`. Exclude only when `(Task exists) AND (all Tasks terminal)`.

**Rationale:** The original `NOT EXISTS(Task IN PENDING/RUNNING/PAUSED)` was logically inverted — it included orphans in the count instead of excluding them. The positive condition is provably correct for all combinations (see truth table in Phase 2 plan).

---

## D-REV-4 (C2/C4) — UPDATE 4 requires EXISTS(terminal) AND NOT EXISTS(non-terminal), scoped via RETURNING

**Decision:** UPDATE 4 does NOT use `NOT EXISTS(non-terminal)` alone. It is scoped to Tasks returned by THIS cascade's UPDATE 2 RETURNING clause. This prevents finalizing rows with no backing Task (C2) and prevents reconciling historical incidents (C4).

---

## D-REV-5 (C3) — WriteGuardSession is NOT a mutex; concurrency via RETURNING scope

**Decision:** All documentation, risk descriptions, and code comments describe `WriteGuardSession` as providing all-or-nothing commit, NOT cross-connection serialization. Concurrency correctness comes from guarded row updates + the CTE's statement-local RETURNING set.

**Rationale:** Under PostgreSQL READ COMMITTED, other transactions CAN interleave between statements. The original plan's claim of "atomic serialization" was factually wrong. A two-connection PostgreSQL race test proves correctness.

---

## D-REV-6 — Hybrid Bridge Strategy: point-fixes ship now, reconciler is follow-up

**Decision:** Ship the corrected point-fixes (Phase 1 + Phase 2) as the immediate production unblock. Track the turn-reconciler-named-transitions migration as a planned follow-up. Point-fixes are NOT throwaway — they establish the `work_id` axis and shared patterns the reconciler subsumes.

**Rationale:** The reconciler is LARGE scope (~6 production files + property-test harness, multi-week timeline). The production deadlock needs an immediate fix. The point-fixes establish load-bearing artifacts (work_id correlation, positive-polarity orphan detection, shared predicate helper) that the reconciler will consume.

**See:** [follow-up-turn-reconciler.md](./follow-up-turn-reconciler.md)

---

## Explicitly Rejected Alternatives

### Option C — Reactive: streaming-error recovery (NOT in scope)
Investigates the proximate trigger (`'NoneType' object has no attribute 'get'`). Separate investigation. Does not block the structural fixes.

### General orphan sweeper/reaper (NOT in scope)
Lifecycle-wide garbage collection is a separate concern. The point-fixes provide admission safety and explicit cleanup for this incident path.

### Schema changes (NOT required)
Both phases use existing columns only. If schema work is needed in a follow-up, use `_ensure_postgres_columns()`.

### Re-arming orphaned messages (REJECTED for completion_report; DEFERRED for other types)
Drop semantics (mark `completed`) based on production evidence. Re-arming would inject duplicate content and violate the resume-driver contract. For non-completion_report types, re-arming is deferred until a durable consumption marker exists (C5).

### Extending UPDATE 4 to all message types (REJECTED per C5)
A terminal Task doesn't prove the message content was consumed. Restricting to `completion_report` prevents data loss.
