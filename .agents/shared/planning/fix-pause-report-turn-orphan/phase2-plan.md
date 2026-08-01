# Phase 2: Cascade Reconciliation + Completion-Guard Hardening for Orphaned `processing` `message_queue` Rows

> **Revision 3 (2026-08-01):** Approver rejection Iteration 2. 4 blocking issues resolved: (1) post-reconcile trigger mechanism specified (Task 17: re-fire completion reevaluation + Phase 2.5 hard-dependency statement), (2) CTE cross-DB snapshot divergence addressed via explicit `state.work_id <> ct.work_id` exclusion + cross-engine test (Task 18), (3) `processing_task_id` direct-path documented as **dead code** for `completion_report` (NULL fallback is the production reality) with defensive non-NULL test case, (4) unconsumed report content risk addressed via ReportInjection consumption check (Task 19 + Phase 2.5 verification). Non-blocking: guard-site reachability reframed (1 reachable + 3 dead-code fallbacks), "8 sites" reframed as "4 active + 4 audit-only."

> **Revision 2 (2026-08-01):** MAJOR revision per council review. 7 criticals fixed: (C1) SQL polarity inversion, (C2) no-Task row preservation, (C3) WriteGuardSession concurrency model, (C4) cascade-scoped UPDATE 4, (C5) restrict to completion_report, (C6) guard site audit, (C7) PostgreSQL test infra. 5 warnings addressed: (W5) UPDATE 4 placement, (W6) Task deletion fact fix, (W7) production-state test, (W8) shared predicate, (W9) cleanup script. CRITICAL: all correlation re-keyed from message_id to work_id.

Date: 2026-08-01
Author: planner[v2] via plan-creation worker
Status: Ready for Review (Iteration 2 → addressee)
Parent incident: `fix-pause-report-turn-orphan` (Bug B — leader stuck at `WAITING_CHILDREN` after pause-during-report-turn)

---

## Objective

Close Bug B with two complementary, defense-in-depth changes while preserving the planned follow-up relationship to Phase 1:

- **Phase 2.A — Cascade reconciliation (structural fix).** Reconcile only `completion_report` queue rows tied to Tasks that **this resume cascade** changes from `PAUSED` to `CANCELLED`. PostgreSQL performs Task cancellation and message reconciliation in one data-modifying CTE statement; SQLite captures `UPDATE ... RETURNING` rows and performs the scoped reconciliation as the next write in the same transaction. UPDATE 4 is immediately after UPDATE 2 and before JobItem activation (UPDATE 3). After commit, **re-fire the completion reevaluation** so the parent does not stay stuck waiting for a bus event that will never come (see §A5).
- **Phase 2.B — Completion-guard hardening (defense in depth).** Make parent own-queue completion guards count a queue row when it has no backing Task or any correlated non-terminal work attempt, and exclude it only when correlated work exists and all correlated attempts are terminal. The production target is `child_reports.py:1459`; **3 dormant bus-gated fallback sites** are hardened with the same shared SQLAlchemy predicate for future safety but are **dead code in production today** (the bus is always initialized in production; the fallbacks are gated behind `bus is None` early-returns or `RuntimeError` raises — see §B3 reframing).
- **Phase 2.5 — Production cleanup.** Provide a dry-run-first, one-shot remediation for **already-stuck instances** that have been orphaned since before this fix shipped. The cleanup is the **hard dependency** for unstick of historical instances — UPDATE 4 + Phase 2.B only prevent the issue going forward. The cleanup also performs a ReportInjection consumption check before dropping content (see §2.5.B).

**Required implementation order:** Phase 2.A, then Phase 2.B, then Phase 2.5/operator rollout. Phase 2 remains a planned follow-up to Phase 1. The defense-in-depth philosophy is unchanged.

---

## Scope

### In Scope

- `_resume_cascade_db_sync` Task `PAUSED → CANCELLED` returning contract and cascade-scoped `completion_report` reconciliation.
- One shared SQLAlchemy predicate for parent own-queue guards, driven by `Task.work_id` after resolving the queue-to-Task link.
- Post-reconcile completion-reevaluation re-fire (Task 17) — both inline-after-cascade and the `enqueue_message`-based fallback for instance tree roots.
- A complete reachability audit of all `pending_count` / `parent_pending` sites in `child_reports.py` and `error_reporting.py`, with accurate production-reachability labels.
- SQLite tests under `tests/unit/`, PostgreSQL tests under `tests/postgres/`, shared engine-agnostic scenario builders, and a two-connection PostgreSQL race test.
- A CI gate that explicitly runs both test trees.
- A dry-run-first cleanup utility or documented operator SQL for existing production orphans.
- **ReportInjection consumption check** in Phase 2.5 cleanup (PENDING = unconsumed → warn/re-arm; INJECTED/TASK_DELIVERED = consumed → safe to drop).

### Out of Scope

- Adding `message_queue.work_id` or a durable graph-consumption marker. Those would remove the NULL-pointer ambiguity but require a dual-driver schema migration and broader producer changes.
- **Re-arming/re-delivering cancelled report content for the cascade-scoped case (Phase 2.A).** Phase 2.A retains the reviewed **drop** decision with a documented justification (Task was RUNNING when cancelled → consumption in progress; see §A5.2). Phase 2.5 still offers re-arm/verification for historical orphans via ReportInjection state check.
- Reconciling `human`, `agent`, `system`, or `error_report` rows. A terminal Task does not prove their content reached the parent checkpoint.
- Changing child report-send decision semantics at `child_reports.py:623/637/1598/1610`; those queries answer a different question ("should the child SEND a report?" not "is the parent ready to complete?"). They are audited, not changed.
- Deleting DependencyBus fallback branches or changing DependencyBus initialization policy.
- Replacing the existing `process_message` stale cleanup in `manager.py:4963-5079`.
- **Populating `processing_task_id` on claim** in `claim_specific`/`dequeue` (open question §Open Q6). Adding that would make the direct path non-dead, but it is a separate producer-side change.

---

## Context and Corrected Invariants

### Production failure

The production leader reached `WAITING_CHILDREN` with two `completion_report` rows at `status='processing'`, `processing_task_id=NULL`; their `process_report` Tasks were `cancelled`. The parent own-queue guard at `child_reports.py:1459-1469` counted both forever (`docs/bugs/pause-during-report-turn-orphans-message-jobitem.md:120-145`).

Pause/resume currently transitions the Task `RUNNING → PAUSED → CANCELLED` but does not reconcile the queue row (`docs/bugs/pause-during-report-turn-orphans-message-jobitem.md:134-154`; `_resume_cascade_db_sync` at `instance_lifecycle.py:3293-3534`).

### Work identity and the only valid correlation paths

`MessageQueue` has no `work_id`. Its relevant fields are `message_id`, `type`, and `processing_task_id` (`message_queue/models.py:42-73`). `Task` has unique/indexed `work_id`, integer `id`, and non-unique `message_id` (`task/models.py:101-124`). `schedule_retry` deliberately reuses `message_id` while minting a fresh `work_id` (`task/repository.py:1892-1934`). Therefore:

1. **Direct path (currently dead code):** when `message_queue.processing_task_id IS NOT NULL`, resolve `Task.id = message_queue.processing_task_id` (with an explicit dialect-safe cast because the queue field is stored as text while `Task.id` is integer), then use that Task's `work_id` as the identity key. **No code in `daemon/` sets `processing_task_id` non-NULL for any message type today** — exhaustive grep confirms the column is only defined at `message_queue/models.py:72` (`Field(default=None)`) and read at `repository.py:440` (the `_row_to_message` serializer). The `claim_specific` method (`repository.py:203`) and `dequeue` method (`repository.py:170`) both transition READY→PROCESSING but only set `status`, `processing_started_at`, `last_activity_at` — they do NOT set `processing_task_id`. The direct path is retained as **future-proofing** for a producer-side improvement (Open Q6).
2. **NULL fallback (production reality):** when `processing_task_id IS NULL`, use `message_queue.message_id = task.message_id` only to discover candidate Tasks, then project their `work_id` values and evaluate status by those work IDs. **`message_id` is a locator, never the terminal/live identity key.** The correctness story for the production incident rests **solely on this fallback**.
3. **Ambiguous NULL fallback:** if the fallback discovers both terminal and non-terminal work IDs (for example, a cancelled attempt plus a fresh pending retry), preserve/count the queue row. Without a persisted queue `work_id`, the implementation must not guess which attempt owns it.
4. **No Task found:** preserve/count the queue row. This is an ambiguous or fresh state, not proof of an orphan.

**Tests must use `processing_task_id=NULL` to match the production reality.** Add ONE test case with `processing_task_id` set non-NULL to prove the direct path works when populated (defensive — ensures the helper does not regress when a future change populates the column).

This is the strongest correct correlation possible without adding `message_queue.work_id`. Tests must lock this limitation in explicitly.

### Correct positive guard polarity (C1/C2)

For base statuses `PROCESSING` and `RETRYING`, the parent guard counts a row when:

```text
(no correlated work_id exists)
OR
(a correlated work_id has a Task in PENDING/RUNNING/PAUSED)
```

It excludes a row only when:

```text
(at least one correlated work_id exists)
AND
(no correlated work_id has a Task in PENDING/RUNNING/PAUSED)
```

`READY` rows always count regardless of Task history. `COMPLETED`/`FAILED` rows are outside the base status filter. This positive condition avoids both prior errors: inverted `NOT EXISTS` polarity and accidental removal of no-Task rows.

### Truth table — must be tested before implementation

| Queue status | Correlation path | Correlated Task attempts | Expected parent count | Expected UPDATE 4 eligibility |
|---|---|---|---:|---|
| `ready` | any | any / none | 1 | No |
| `processing` | direct (dead-code path) | terminal only | 0 | Yes, only if that Task is returned by this cascade and type is `completion_report` |
| `processing` | direct (dead-code path) | non-terminal | 1 | No |
| `processing` | NULL fallback (production reality) | terminal only | 0 | Yes, only if one terminal candidate is returned by this cascade |
| `processing` | NULL fallback (production reality) | non-terminal only | 1 | No |
| `processing` | NULL fallback (production reality) | terminal + non-terminal work IDs | 1 | No; ambiguous retry/mixed-attempt state |
| `processing` | either | no Task | 1 | No |
| `retrying` | either | terminal only | 0 | Same narrow cascade scope as `processing` |
| `completed` | any | any | 0 | No; already terminal |

The two "direct (dead-code path)" rows are exercised only by the defensive non-NULL test case. All production-row scenarios use `processing_task_id=NULL`.

---

## Phase 2.A — Cascade Reconciliation

### Design

#### A1. Scope UPDATE 4 to this transaction's cancellations

UPDATE 2 currently uses `UPDATE task ... WHERE instance_id IN :tree_ids AND status='paused' RETURNING id` (`instance_lifecycle.py:3392-3463`). Extend the returning projection to `id, work_id, message_id`; those returned rows are the **only** Tasks UPDATE 4 may use.

- **PostgreSQL:** replace standalone UPDATE 2 with one data-modifying CTE statement. `cancelled_tasks` performs UPDATE 2 and returns `id/work_id/message_id`; the following `UPDATE message_queue` consumes that CTE and returns reconciled message IDs. This makes Task selection and message scoping one database statement under READ COMMITTED.
- **SQLite:** execute UPDATE 2 with `RETURNING id, work_id, message_id`, materialize those rows in Python, then execute UPDATE 4 using only the returned identifiers before UPDATE 3. SQLite's single-writer behavior plus the transaction preserves all-or-nothing commit; the implementation must still use guarded predicates because `WriteGuardSession` itself is not a database mutex.

PostgreSQL shape (implementation sketch, not copy-paste SQL):

```sql
WITH cancelled_tasks AS (
    UPDATE task
       SET status = 'cancelled', ...
     WHERE instance_id IN (:tree_ids)
       AND status = 'paused'
     RETURNING id, work_id, message_id
),
reconciled_messages AS (
    UPDATE message_queue AS mq
       SET status = 'completed',
           completed_at = :now,
           last_activity_at = :now,
           processing_task_id = NULL
     WHERE mq.instance_id IN (:tree_ids)
       AND mq.type = 'completion_report'
       AND mq.status IN ('processing', 'retrying')
       AND EXISTS (
           SELECT 1
             FROM cancelled_tasks ct
            WHERE CAST(ct.id AS text) = mq.processing_task_id
               OR (
                    mq.processing_task_id IS NULL
                AND ct.message_id = mq.message_id
                AND NOT EXISTS (
                    SELECT 1
                      FROM task locator
                      JOIN task state ON state.work_id = locator.work_id
                     WHERE locator.message_id = mq.message_id
                       AND state.work_id <> ct.work_id          -- ★ exclude just-cancelled
                       AND state.status IN ('pending','running','paused')
                )
               )
       )
     RETURNING mq.message_id
)
SELECT ... FROM cancelled_tasks ... reconciled_messages;
```

The `★ exclude just-cancelled` clause is **load-bearing for cross-engine parity** (see §A2 below — addresses Blocking Issue 2 from approver).

The implementation may adjust the final projection to avoid a Cartesian result, but it must return both cancelled Task IDs (for watcher cleanup) and reconciled message IDs/count (for logging). State decisions remain keyed by `work_id`; `message_id` appears only in the documented NULL-pointer locator.

#### A2. Why the `state.work_id <> ct.work_id` exclusion is mandatory (cross-DB parity, Blocking Issue 2)

**Verified cross-engine divergence:**

| Engine | Snapshot semantics for data-modifying CTE sub-statements | Behavior on just-cancelled sibling |
|---|---|---|
| PostgreSQL (READ COMMITTED) | Data-modifying CTE sub-statements **share one snapshot** taken before the CTE's first UPDATE | The competing-live subquery re-reads `task` and sees the **PRE-update `PAUSED`** status of the just-cancelled attempt. The `cancelled_tasks` row says `cancelled`, but the competing-live subquery says "there is still a `PAUSED` Task at that `work_id`" → **false negative** blocks legitimate reconciliation. |
| SQLite | Subqueries in the same statement read **post-UPDATE** state (effectively `READ COMMITTED`-like snapshot of the updated rows) | The competing-live subquery sees the just-cancelled attempt as `cancelled` → no competing live → **permits** reconciliation. |

**Same input, different outcome.** Without the exclusion, an UPDATE 4 that reconciles a `processing` `completion_report` on PostgreSQL would silently fail to reconcile on SQLite, or vice versa. This is **unacceptable for dual-driver correctness**.

**The `state.work_id <> ct.work_id` exclusion is mandatory because:**

1. The `cancelled_tasks` CTE's RETURNING set is the **authoritative** source of "this cascade changed this `work_id` from `PAUSED` to `cancelled`."
2. The competing-live subquery is asking "is there ANOTHER work attempt at the same `work_id`?" — but after the same CTE already cancelled it, the subquery's re-read on PostgreSQL returns the **stale** `PAUSED` row.
3. By excluding `ct.work_id` from the competing-live subquery's join, we tell the subquery: "look at other work attempts with the same `message_id`, not the just-cancelled one." This eliminates the false negative.
4. On SQLite the exclusion is a no-op (the subquery already returns the correct set), so the clause is **safe and inert** there.

**Alternative approaches considered and rejected:**

- *Two-statement approach on PostgreSQL* — execute UPDATE 2 standalone, commit visibility, then UPDATE 4. Eliminates the divergence but sacrifices single-CTE atomicity; all-or-nothing commit is preserved via `WriteGuardSession` but the seam is wider and harder to reason about.
- *Defer to engine-specific SQL* — would force every test to assert behavior per-engine, doubling the test surface and inviting drift.

**Chosen approach:** Approach 1 (`state.work_id <> ct.work_id` exclusion) — surgical, single-source SQL, identical semantics on both engines. A **cross-engine parity test** (Task 18) seeds the exact scenario (two sibling attempts at the same `work_id`, one `PAUSED`, one terminal via a separate `work_id`; or — more directly — a single `processing_task_id=NULL` row whose only candidate Task is the just-cancelled `work_id`) and asserts both engines produce identical UPDATE 4 eligibility.

#### A3. Preserve no-Task and mixed-attempt rows

UPDATE 4 cannot match a no-Task row because eligibility begins from `cancelled_tasks`. In the NULL fallback, a competing non-terminal work attempt with the same message locator blocks reconciliation. This prevents a cancelled parent attempt from causing the shared queue row of a pending retry child to be finalized.

#### A4. Restrict drop semantics to `completion_report`

Use the model's actual column name: `message_queue.type` (`MessageQueue.type`, `message_queue/models.py:49`), filtered to `MessageType.COMPLETION_REPORT.value`. Do not reconcile `human`, `agent`, `system`, or `error_report` messages until a durable "consumed by checkpoint" marker exists.

The drop decision remains valid only for reports cancelled by this cascade: mark the queue row `completed`, retain `content` for audit, clear `processing_task_id`, and set activity/completion timestamps. A code comment must state that terminal Task status alone is not general consumption proof.

**Justification for the cascade-scoped drop (Blocking Issue 4, §A5.2):** the Task being `PAUSED → CANCELLED` while the message_queue row is `processing` means the `process_report` Task was actively driving the parent graph turn with this report's content when pause fired. By the time the message reaches `processing`, the `ReportInjection` row (if any) has been claimed by either the live agent-node drain (`INJECTED`) or the fallback task (`TASK_DELIVERED`) — see `graph.py:2577-2621` and `report_injection/models.py:42-58`. The "orphaned at `processing`" state observed in the bug report (lines 125-132) is consistent with consumption in progress (the report was already pulled into the turn) but where the parent checkpoint was lost mid-turn. The Phase 2.A drop preserves `content` in the row for audit; the Phase 2.5 cleanup adds an explicit ReportInjection consumption check (Task 19).

#### A5. Post-reconcile completion reevaluation (Blocking Issue 1)

**The trigger problem:** UPDATE 4 flips `message_queue` row status to `completed`, but **nothing naturally re-fires the completion cascade**. The DependencyBus only fires `_process_child_completion_db_sync` from a watcher's terminal event (`child_reports.py:1099-1106` inside `asyncio.to_thread`); the bus's `count_pending_for_target` queries `DependencyWatcher` rows, not `message_queue` rows (`dependency_bus/repository.py:301-340`). For already-stuck instances where **ALL children have already reported** (all bus watchers already FIRED), there is no pending watcher to re-fire the completion check — the instance is permanently stuck even after UPDATE 4 reconciles the orphaned rows.

**Verified code evidence:**

- `dependency_bus/repository.py:301-340` — `count_pending_for_target` does `select(func.count()).select_from(DependencyWatcher)` where `DependencyWatcher.state == PENDING`. **It does NOT count `message_queue` rows.**
- `child_reports.py:1099-1106` — the bus callback invokes `_process_child_completion_db_sync` only inside `asyncio.to_thread`, fired by `_emit_terminal_via_bus` (`child_reports.py:198-288`) when a child's terminal event emits. **No watcher fired = no callback = no completion reevaluation.**
- For historical stuck instances (the production incident), every child has already reported — there is no pending watcher to fire the bus callback. UPDATE 4 reconciles the queue rows but the parent instance remains at `WAITING_CHILDREN` because nothing re-checks `pending_count`.

**Required resolution — combined approach (recommended by approver):**

##### A5.1 — Inline post-cascade re-fire for **new** future incidents (defense-in-depth self-healing)

After UPDATE 4 commits, before returning from `_resume_cascade_db_sync`, check whether the instance is **now eligible for completion** using the **shared positive-polarity predicate** (Phase 2.B helper). If the predicate returns `pending_count == 0` for all reconciled rows and the instance has no other pending Task/JobItem, **synchronously call `_process_child_completion_db_sync(instance_id, completed_message_id=None, last_content="")` from the worker thread** (still inside `asyncio.to_thread`, after the cascade commit).

This is safe because:
- `_process_child_completion_db_sync` opens its own `WriteGuardSession` (the original cascade's session has committed and is closed).
- The function has explicit idempotency guards (`instance.status in (COMPLETED, ERROR, PAUSED)` short-circuit at `child_reports.py:1212-1219`).
- The function's `pending_count` query at `child_reports.py:1459` is **what we are intentionally re-firing** — if the predicate is correct, the result is consistent.
- `completed_message_id=None` is supported — the function excludes `message_id != completed_message_id`, which becomes `message_id != None` (always true, so no message is excluded). **Note:** this means the `pending_count` check sees ALL messages, not just the reconciled ones. The shared predicate helper (Phase 2.B) is the authoritative check, not the legacy one in `child_reports.py:1459` — see Open Q7 about whether to also fix the legacy check.

For **non-root instances** (those with a `parent_id`), the re-fire path additionally calls `bus.emit_terminal_for_child_instance` if the instance has changed to a terminal state, propagating the completion up the tree. The bus's own completion logic then fires the parent's bus callback naturally.

##### A5.2 — Phase 2.5 is the **hard dependency** for historical stuck instances

UPDATE 4 + A5.1 self-heal **new** future incidents. **Historical stuck instances** (those orphaned before this fix shipped) have no in-flight resume cascade to attach A5.1 to — they were stuck in their last state. For these, **Phase 2.5's operator cleanup is the only unstick path**, and it is explicit about that:

- The cleanup script (`scripts/remediate_pause_report_orphans.py`) reconciles the orphaned `message_queue` rows AND transitions the instance `WAITING_CHILDREN → COMPLETED` in one operator-approved transaction.
- The script emits a warning that it is the manual unstick path; **no natural event re-fires completion for already-stuck instances** because there is no resume cascade to attach to.

##### A5.3 — Operator runbook ordering

1. Ship Phase 2.A + Phase 2.B.
2. CI green (SQLite + PostgreSQL, two-connection race test, cross-engine parity test).
3. Operator runs Phase 2.5 dry-run on the stuck production instance, confirms the candidate rows.
4. Operator runs Phase 2.5 apply, which reconciles the queue rows and transitions the instance to `COMPLETED`.
5. Future incidents self-heal via A5.1's inline re-fire.

#### A6. Correct placement and concurrency wording

The effective order is:

1. UPDATE 1: instances `PAUSED → RUNNING`.
2. UPDATE 2 + UPDATE 4: cancel selected Tasks and reconcile only their `completion_report` rows (one CTE statement on PostgreSQL; adjacent statements on SQLite).
3. UPDATE 3: JobItems to canonical `active` admission state.
4. Commit.
5. **A5.1 re-fire** (post-commit, inside the same `asyncio.to_thread` wrapper that called `_resume_cascade_db_sync`, on the worker thread): call `_process_child_completion_db_sync(instance_id, completed_message_id=None, last_content="")` if the post-UPDATE-4 predicate returns `pending_count == 0`. For non-roots, also call `bus.emit_terminal_for_child_instance` if the instance reached terminal.

The shared transaction provides **all-or-nothing commit**, not cross-connection serialization. Under PostgreSQL READ COMMITTED, unrelated transactions can interleave between UPDATE 1, the UPDATE 2/4 CTE, and UPDATE 3. Correctness comes from guarded row updates plus the CTE's statement-local returned set, not from `WriteGuardSession`. Do not claim a mutex, global atomic serialization, or "no other code path can interleave."

### Exit Criterion

A resume cascade reconciles only the `completion_report` rows associated with Tasks it cancelled, preserves no-Task/mixed-attempt rows, performs reconciliation before JobItem activation, **re-fires the completion reevaluation post-commit so the parent does not stay stuck at `WAITING_CHILDREN`**, and passes SQLite plus PostgreSQL concurrency tests including the **cross-engine CTE parity test** (Task 18). Historical stuck instances are unstickable via Phase 2.5; future stuck instances self-heal via A5.1.

---

## Phase 2.B — Completion-Guard Hardening

### B1. Shared predicate

Create one alias-safe SQLAlchemy helper in `daemon/repositories/message_queue/predicates.py` (new), for example `message_queue_counts_as_pending(message_alias, task_alias_factory=...)`. It must:

- Preserve the caller's base filters and completed-message exclusion.
- Return true unconditionally for `READY`.
- **Resolve direct `processing_task_id → Task.id → Task.work_id` when the pointer exists.** (Currently dead code in production — see Context §Direct path; the helper still implements this branch as future-proofing.)
- Use `message_id` only as the NULL-pointer locator, project candidate `work_id`s, and evaluate non-terminal status through those work IDs.
- Return true when no correlated Task exists.
- Return true when any correlated work attempt is `PENDING`, `RUNNING`, or `PAUSED`.
- Return false only for `PROCESSING`/`RETRYING` rows with correlated work and terminal-only attempts.
- Use enum values, correlated aliases, and constructs that compile on SQLite and PostgreSQL.

### B2. Reachability audit (C6) — REFRAMED for accuracy

**Important reframing:** the previous plan labeled the parent-completion sites as "1 live + 3 fallbacks" but the headline still implied "4 production code changes." For accuracy, this section separates **reachable production sites** from **dead-code fallbacks** explicitly. There are also **child report-send decision sites** that are NOT parent-completion guards and are audited but unchanged.

| Site | Category | Production reachability | Behavior in production | Revision decision |
|---|---|---|---|---|
| `child_reports.py:1459` in `_process_child_completion_db_sync` | **(a) Parent completion guard; primary reachable production site** | **REACHABLE in normal production** | Always evaluated on every child completion | **Apply shared predicate** — this is the **only** site whose guard semantics change in production |
| `child_reports.py:863` in `_update_parent_on_child_complete` | **(b) Parent completion guard; bus-off fallback** | **Dead code in production.** Bus-active return at `child_reports.py:851-860` skips this entire block in production (the bus is always initialized — verified by `RuntimeError` raise elsewhere). | Returned early in production; this `parent_pending` query is **never executed** | Harden with shared predicate as **future-proofing**; do not delete in this phase |
| `child_reports.py:2058` in `_process_child_completion_db_sync` legacy parent cascade | **(b) Parent completion guard; bus-off fallback** | **Dead code in production.** Bus-active branch at `child_reports.py:2049-2055` skips it. | Returned early in production | Harden with shared predicate as **future-proofing**; do not delete |
| `error_reporting.py:270` | **(b) Parent completion guard; bus-off fallback** | **Dead code in production.** Bus-active branch at `error_reporting.py:257-264` skips it. | Returned early in production | Harden with shared predicate as **future-proofing**; do not delete |
| `child_reports.py:623/637` in `_should_send_completion_report` | **(c) Child report-send decision** | REACHABLE | Decides whether the child SENDS a report (different question: "is there content worth sending?" not "is the parent ready to complete?") | **Audit only — no semantic change.** Add audit regression assertions only. |
| `child_reports.py:1598/1610` inline mirror | **(c) Child report-send decision** | REACHABLE | Same different concern as `:623/637` | **Audit only — no semantic change.** Add parity assertion with `:623/637`. |

**Production behavior summary:**

- **1 reachable production guard site** (`child_reports.py:1459`) gets the new shared predicate. This is the **only site where the predicate change has observable effect in production today**.
- **3 parent-completion fallback sites** (`child_reports.py:863`, `:2058`, `error_reporting.py:270`) are hardened with the same predicate as **future-proofing** — the production bus path bypasses them entirely, but if a future code path bypasses the bus (e.g. a test fixture without CM wired, a legacy code path), the predicate will already be correct.
- **4 child report-send decision sites** (`child_reports.py:623/637/1598/1610`) answer a different question. They are **not** parent-completion guards. Audit them with parity tests; do not change semantics.

**Headline reframing (Non-Blocking Note 2):** the plan now reports **"4 active parent-completion + 4 audit-only child-decision sites"** — not the prior misleading "8 sites." Of the 4 parent-completion sites, **1 is reachable in production and 3 are dead-code fallbacks**. Of the 4 child-decision sites, **none change semantics** — they are audited, not modified.

### B3. Exit Criterion

The **1 reachable production guard** and **3 retained parent-completion fallbacks** use one shared, positive-polarity predicate; the 4 child report-send decision sites are audited with parity tests but their semantics are unchanged.

---

## Phase 2.5 — Production Cleanup for Existing Stuck Instances

Create `scripts/remediate_pause_report_orphans.py` (or an equivalently reviewed operator SQL artifact) with **dry run as the default** and an explicit `--apply` flag. Require `--instance-id`; never scan/update every project implicitly.

### Dry run

Print candidate queue rows, direct/fallback Task IDs, resolved `work_id`s, Task statuses, message type, **ReportInjection state** (PENDING/INJECTED/TASK_DELIVERED/none), and the reason each row is eligible or preserved. Eligibility mirrors Phase 2.B for historical data:

- instance matches the operator-supplied ID;
- `type='completion_report'`;
- queue status is `processing` or `retrying`;
- at least one terminal correlated work attempt exists;
- no correlated work attempt is `pending`, `running`, or `paused`;
- no-Task and mixed terminal/non-terminal rows are printed as **preserved**, never updated;
- **ReportInjection state for each candidate**: PENDING = unconsumed (warn and require explicit `--force-rearm` to drop), INJECTED/TASK_DELIVERED = consumed (safe to drop).

### Apply transaction — mirror the incident remediation (with ReportInjection check, Task 19)

The apply path must:

1. **For each candidate row, check the corresponding `ReportInjection` row** (key by parent instance id + message id). If a `PENDING` ReportInjection exists for the row being dropped, **abort with a per-row error** unless `--force-rearm` is supplied. The operator can then re-arm the ReportInjection row (it stays PENDING and will be drained on the next graph turn) or `--force-drop` the orphaned content with an explicit acknowledgment.
2. **For rows where ReportInjection is INJECTED or TASK_DELIVERED** (or absent), emit and execute the reviewed equivalent of the production SQL from `docs/bugs/pause-during-report-turn-orphans-message-jobitem.md:285-300`, narrowed by the new `completion_report` and positive correlation guards:

```sql
BEGIN;
UPDATE message_queue
   SET status = 'completed',
       completed_at = NOW(),
       last_activity_at = NOW(),
       processing_task_id = NULL,
       error_message = COALESCE(error_message,'') ||
         'manual-unstick: orphaned completion_report; terminal backing work after pause/resume'
 WHERE instance_id = :instance_id
   AND type = 'completion_report'
   AND status IN ('processing','retrying')
   AND EXISTS (/* correlated terminal Task, status evaluated by work_id */)
   AND NOT EXISTS (/* correlated PENDING/RUNNING/PAUSED work_id */)
RETURNING message_id;
COMMIT;
```

3. **Print the ReportInjection state for every reconciled row** in the operator log — even for INJECTED/TASK_DELIVERED rows (audit trail).
4. **After reconciliation, print a second dry-run decision for the instance transition.** If the operator explicitly approves completion and the instance still meets the incident's conditions, include the reviewed equivalent of lines 304-313:

```sql
BEGIN;
UPDATE instances
   SET status = 'completed',
       updated_at = NOW()::text,
       last_activity_at = NOW(),
       version = COALESCE(version, 1) + 1
 WHERE instance_id = :instance_id
   AND status = 'waiting_children';
COMMIT;
```

5. Before offering that transition, the utility must report remaining countable queue work, non-terminal Tasks, and pending DependencyBus watchers. The direct status write is an operator remediation and does not emit normal completion side effects; the script must print that caveat and the affected row counts.

**Data-loss risk acknowledgment (Phase 2.5 specific):**

- For rows with **absent** ReportInjection row (or unknown): the cleanup prints `report_injection_state: unknown` and proceeds only with `--force-drop`. Default is refuse.
- For rows with **PENDING** ReportInjection: refuse by default. The operator must explicitly request re-arm (which preserves the PENDING row and the message_queue row, leaving them to be drained naturally on the next graph turn) or `--force-drop` (acknowledging data loss).
- For rows with **INJECTED/TASK_DELIVERED**: the report was already consumed by the parent's graph turn (INJECTED via live agent-node drain at `graph.py:2577-2621`, or TASK_DELIVERED via the fallback `PROCESS_REPORT` task). Safe to drop.

### Exit Criterion

Operators can inspect exact candidates (with ReportInjection state per row) without writes, apply remediation to one approved instance, see all changed IDs and per-row ReportInjection states, and optionally perform the guarded `WAITING_CHILDREN → COMPLETED` transition with explicit warning about skipped side effects. PENDING ReportInjection rows trigger a refusal unless `--force-rearm` or `--force-drop` is supplied.

---

## Tasks

| # | Task | Depends On | Acceptance | Key Files |
|---|---|---|---|---|
| 1 | Write the positive-polarity truth-table tests **before implementation** | none | All combinations in the table above are represented, including no Task, terminal-only, live-only, mixed terminal/live attempts, direct pointer, NULL fallback, READY, and the defensive non-NULL `processing_task_id` case. **All production-row scenarios use `processing_task_id=NULL`**. Tests initially fail against the old logic. | `tests/unit/test_message_queue_pending_predicate.py` (new) |
| 2 | Implement the shared work-ID predicate helper | 1 | One alias-safe helper passes Task 1 on SQLite; `message_id` is used only to locate candidates when `processing_task_id` is NULL; the direct-path branch is exercised only by the defensive non-NULL test case. | `daemon/repositories/message_queue/predicates.py` (new) |
| 3 | Refactor UPDATE 2 and add cascade-scoped UPDATE 4 | 1 | PostgreSQL uses a data-modifying CTE returning `id/work_id/message_id`; SQLite captures the same returned fields and reconciles only that set. No historical tree-wide sweep is possible. **The competing-live subquery explicitly excludes `ct.work_id`** (`state.work_id <> ct.work_id`) to neutralize the PostgreSQL snapshot divergence. | `daemon/services/instance_lifecycle.py:3392-3519` |
| 4 | Restrict and position UPDATE 4 correctly | 3 | Only `type='completion_report'` and `status IN ('processing','retrying')` match; UPDATE 4 is immediately after Task cancellation and before JobItem activation. | `daemon/services/instance_lifecycle.py` |
| 5 | Correct lifecycle documentation and add structured logging | 3, 4 | Docstrings say all-or-nothing commit but not serialization; logs include cancelled Task work IDs/count, reconciled message IDs/count, direct vs fallback counts, and skipped ambiguous count after commit. | `daemon/services/instance_lifecycle.py` |
| 6 | Harden the **1 reachable production** parent guard | 2 | `child_reports.py:1459` uses the shared positive predicate and preserves its `message_id != completed_message_id` filter. **This is the only site with observable production effect.** | `daemon/services/child_reports.py:1446-1519` |
| 7 | Harden the **3 dead-code fallback** parent-completion sites (future-proofing) | 2 | `child_reports.py:863`, `child_reports.py:2058`, and `error_reporting.py:270` use the same helper; control flow is otherwise unchanged. These are unreachable in production but hardened so that any future code path bypassing the bus gets correct semantics automatically. | `daemon/services/child_reports.py`, `daemon/services/error_reporting.py` |
| 8 | Lock the guard-site reachability audit in tests/comments | 6, 7 | Tests identify the **1 reachable production site** by name (`child_reports.py:1459`), verify bus-active paths bypass the 3 fallbacks (via `RuntimeError`/`bus is not None` early-return), and verify the 4 child report-decision sites (`:623/637/1598/1610`) remain semantically unchanged. Comments in `child_reports.py` and `error_reporting.py` label each site with `(reachable in production)` or `(dead-code fallback — bus-active path bypasses)`. | `tests/unit/test_message_queue_pending_predicate.py`, service comments |
| 9 | Extend SQLite cascade tests using shared builders | 3, 4, 14 | Covers direct pointer (non-NULL `processing_task_id`, defensive), NULL fallback (production reality, `processing_task_id=NULL`), no Task, mixed retry work IDs, non-`completion_report`, READY/already-terminal rows, multi-node scope, rollback, idempotency, and UPDATE 4-before-UPDATE 3 ordering. | `tests/unit/test_cascade_pause_resume.py`, `tests/helpers/pause_report_orphan_scenarios.py` (new) |
| 10 | Test all parent guard sites on SQLite | 6, 7, 14 | The truth table passes at the **1 reachable production site** and the 3 dead-code fallbacks; no-Task rows count and terminal-only rows do not. | `tests/unit/test_pause_cascade_message_queue_orphan.py` (new) |
| 11 | Add PostgreSQL CTE and two-connection race coverage | 3, 4, 14 | Tests live under `tests/postgres/`, use `pg_engine` and `pg_two_connections`, and prove no interleaving yields a finalized queue row with live correlated work or reconciliation outside this cascade's returned set. | `tests/postgres/test_pause_report_orphan_reconciliation_pg.py` (new) |
| 12 | Add full pause-during-report-turn end-to-end regression | 9, 10, 11 | Real pause/resume flow reaches `COMPLETED`; no orphaned scoped completion reports remain; assertions include Task work IDs and JobItem state, not just eventual status. | `tests/integration/test_pause_during_report_turn_reaches_completed.py` (new) plus PostgreSQL counterpart if DB-specific |
| 12b | Seed and reconcile the exact production state | 9, 10 | Leader is `WAITING_CHILDREN`; two `completion_report` rows are `processing` with `processing_task_id=NULL`; backing Tasks are already `cancelled`. Test the UPDATE 4 primitive directly by supplying those Tasks' `id/work_id/message_id` as the explicit captured-cancellation input, then trigger completion reevaluation and assert both rows reconcile and the leader reaches `COMPLETED`. Also add a resume-seam variant that starts Tasks as `PAUSED`, pauses immediately after UPDATE 2 captures/returns them (where the DB now exactly matches the observed cancelled/orphan state), then releases UPDATE 4. **Do not** claim that a fresh unmodified resume can rediscover already-CANCELLED Tasks: that would violate C4 because UPDATE 2 returns only Tasks cancelled by this transaction. | SQLite unit/integration file plus `tests/postgres/test_pause_report_orphan_reconciliation_pg.py` |
| 13 | Prove defense in depth with cascade reconciliation disabled | 10 | With UPDATE 4 bypassed, terminal-only historical reports do not block the production parent guard; no-Task and mixed-live rows still block completion. | `tests/integration/test_pause_during_report_turn_reaches_completed.py` |
| 14 | Create engine-agnostic scenario builders | none | Builders accept any SQLAlchemy Engine, seed instances/Tasks/messages/JobItems/ReportInjections, return IDs/work IDs, and are imported by both unit and PostgreSQL tests; no fake dual-DB parametrized fixture is claimed. | `tests/helpers/__init__.py`, `tests/helpers/pause_report_orphan_scenarios.py` |
| 15 | Add the dual-tree CI gate | 9-14, 17-19 | CI runs SQLite/unit tests and serial PostgreSQL tests separately; a missing/unreachable PostgreSQL service fails the required PG job rather than silently satisfying the gate via skip. **The CI gate must include the cross-engine parity test (Task 18) and the re-fire test (Task 17).** | Project CI/test scripts; documented commands below |
| 16 | Implement Phase 2.5 cleanup | 3, 10, 19 | Default dry run, explicit single instance, work-ID-aware eligibility, ReportInjection consumption check per row (PENDING → refuse unless `--force-rearm`/`--force-drop`; INJECTED/TASK_DELIVERED → safe), reviewed UPDATE/RETURNING output, optional guarded instance transition, and operator caveat are tested. | `scripts/remediate_pause_report_orphans.py`, script tests/docs |
| **17** | **Implement post-reconcile completion re-fire (A5.1)** | 2, 3, 4 | After `_resume_cascade_db_sync` commits UPDATE 4, evaluate `pending_count` via the shared predicate for the affected instance tree. If `pending_count == 0` and the instance is otherwise ready to complete, **synchronously call `_process_child_completion_db_sync(instance_id, completed_message_id=None, last_content="")`** on the worker thread (still inside the `asyncio.to_thread` wrapper). For non-root instances that reach terminal, additionally call `bus.emit_terminal_for_child_instance` to propagate completion up the tree. Verify the existing idempotency guards at `child_reports.py:1212-1219` make this safe. | `daemon/services/instance_lifecycle.py:_resume_cascade_db_sync`, post-commit tail |
| **18** | **Add cross-engine CTE parity test (A2, Blocking Issue 2)** | 3, 14 | New test seeds the exact PostgreSQL divergence scenario on BOTH engines: one `processing_task_id=NULL` row whose only candidate Task is the just-cancelled `work_id`. Assert both SQLite and PostgreSQL produce identical UPDATE 4 eligibility (both reconcile, both skip, or both preserve — but never one reconcile and one skip on the same input). Test name: `test_cte_work_id_exclusion_cross_engine_parity`. Place in both `tests/unit/test_cascade_pause_resume.py` (SQLite variant) and `tests/postgres/test_pause_report_orphan_reconciliation_pg.py` (PG variant), sharing scenario builders from Task 14. | `tests/unit/test_cascade_pause_resume.py`, `tests/postgres/test_pause_report_orphan_reconciliation_pg.py` |
| **19** | **Implement Phase 2.5 ReportInjection consumption check (A5.2, Blocking Issue 4)** | 16 | Before any `message_queue` UPDATE in Phase 2.5 apply path, query `report_injection` for rows matching `(parent_instance_id, message_id)`. If state is `PENDING`, refuse unless `--force-rearm` (preserve message_queue row + ensure ReportInjection will be drained on next graph turn) or `--force-drop` (explicit data-loss acknowledgment). If state is `INJECTED` or `TASK_DELIVERED`, safe to drop — print state in audit log. If no ReportInjection row exists, print `unknown` and refuse unless `--force-drop`. Test with three ReportInjection states and the no-row case. | `scripts/remediate_pause_report_orphans.py`, `daemon/repositories/report_injection/repository.py` (read-only query helper if needed) |

---

## PostgreSQL Concurrency Protocol and Race Test

### Protocol choice: Option B (`RETURNING` scope), justified

Use Option B rather than a separate `SELECT ... FOR UPDATE` candidate scan. The returned set from the Task cancellation is narrower: it identifies exactly what this cascade changed, avoids sweeping historical incidents, and on PostgreSQL can feed UPDATE 4 within the same data-modifying CTE statement. A preliminary row-lock scan would still need a reliable queue-to-work identity and would expand lock scope across the tree.

`WriteGuardSession` remains useful for process-level pause control and transaction ownership, but it does not serialize other PostgreSQL connections. Row-level DML locks, guarded status predicates, and the statement-local CTE provide database concurrency correctness.

### Required two-connection PostgreSQL test

Use `tests/postgres/conftest.py:244-265` (`pg_two_connections`) and a barrier/thread pair:

1. Seed one paused Task, a processing `completion_report`, and its queue-to-Task correlation.
2. Connection A runs the resume cancellation/reconciliation statement while connection B concurrently attempts a conflicting Task/message transition.
3. Exercise both race orders: B wins before A's guarded UPDATE; A wins and B observes the terminal result.
4. Repeat with `processing_task_id=NULL` and a second PENDING retry Task sharing `message_id` but owning a fresh `work_id`.
5. Assert only valid outcomes: either A returns the cancelled `work_id` and reconciles its exact report, or A returns no eligible Task/message and leaves concurrent live work countable. Forbidden outcomes are: historical unrelated row reconciled, no-Task row reconciled, mixed-attempt NULL-fallback row reconciled, or queue row `completed` while its resolved live work remains the owner.
6. Run under PostgreSQL READ COMMITTED explicitly and record transaction boundaries in the test comments.

### Cross-engine CTE parity test (Task 18)

A separate test (`test_cte_work_id_exclusion_cross_engine_parity`) seeds the PostgreSQL-specific divergence scenario on both engines:

- One `processing` `completion_report` row with `processing_task_id=NULL`, `message_id=X`.
- One Task `A` at `work_id=WA`, `message_id=X`, status `paused` (this is the just-cancelled candidate).
- No other Tasks with `message_id=X`.
- After UPDATE 2 (which sets Task `A` to `cancelled`) + UPDATE 4, the `processing_task_id=NULL` row should reconcile (the only candidate `work_id` is the just-cancelled `WA`, and there is no competing live work).

Without the `state.work_id <> ct.work_id` exclusion, the PostgreSQL competing-live subquery re-reads Task `A` as `PAUSED` (pre-update snapshot) and falsely concludes "competing live work exists" → reconciliation blocked (false negative). With the exclusion, the subquery returns no competing live work (Task `A`'s `work_id` is excluded) → reconciliation permitted. SQLite is unaffected by the exclusion but also passes.

Assert both engines produce identical UPDATE 4 RETURNING sets on the same input.

---

## Test Infrastructure and CI

### Layout

| Tree | Engine/fixture | Coverage |
|---|---|---|
| `tests/unit/` | Existing in-memory SQLite engine (`StaticPool`, FK pragma) | Predicate truth table (Task 1, including defensive non-NULL `processing_task_id` case), cascade behavior, fallback guards, ordering, rollback, **cross-engine parity (SQLite variant)** |
| `tests/postgres/` | Existing `pg_engine`; `pg_two_connections` for races | Data-modifying CTE, dialect casts, READ COMMITTED interleavings, exact production state, **cross-engine parity (PG variant)** |
| `tests/helpers/` | Engine-agnostic builders only | Shared seed/read/assert primitives imported by both trees |
| `tests/integration/` | Existing integration harness | Full graph/pause/resume completion behavior, including post-reconcile re-fire |

`test_cascade_pause_resume.py` is SQLite-only (`tests/unit/test_cascade_pause_resume.py:1-40`). No dual-driver parametrized fixture currently exists. Do not retrofit an imaginary fixture. Create reusable builders and separate tests in the established PostgreSQL tree (`tests/postgres/conftest.py:1-7,159-178`).

### CI commands/gate

The implementation PR must provide two required checks, for example:

```bash
# SQLite/unit and focused integration
.venv/bin/pytest tests/unit/test_message_queue_pending_predicate.py \
  tests/unit/test_cascade_pause_resume.py \
  tests/unit/test_pause_cascade_message_queue_orphan.py \
  tests/integration/test_pause_during_report_turn_reaches_completed.py \
  --override-ini="addopts=" -q

# PostgreSQL, serial (xdist is explicitly unsupported for this tree)
.venv/bin/pytest tests/postgres/test_pause_report_orphan_reconciliation_pg.py \
  --override-ini="addopts=" -m postgres -q
```

The PostgreSQL CI job must provision `ensemble_test` and verify connectivity before pytest so fixture skip cannot produce a false green. Broader existing pause/resume, report-lane, and child-completion suites must also pass before merge. The **cross-engine parity test (Task 18)** must run on both engines and assert identical outcomes.

---

## Key Files / Files Touched

### Production modifications

- `daemon/services/instance_lifecycle.py:3293-3534` — UPDATE 2 returning contract, PostgreSQL CTE / SQLite branch, UPDATE 4 placement with `state.work_id <> ct.work_id` exclusion, **post-commit re-fire (Task 17)**, logging, corrected concurrency comments.
- `daemon/repositories/message_queue/predicates.py` — new shared positive-polarity SQLAlchemy predicate.
- `daemon/services/child_reports.py:1459` — **1 reachable production** parent-completion guard.
- `daemon/services/child_reports.py:863,2058` — **3 dead-code fallbacks** (bus-active path bypasses), hardened as future-proofing.
- `daemon/services/error_reporting.py:270` — **1 dead-code fallback** (bus-active path bypasses), hardened as future-proofing.
- `scripts/remediate_pause_report_orphans.py` — dry-run-first Phase 2.5 cleanup with ReportInjection consumption check (Task 19).
- `daemon/services/child_reports.py:851-860, 1212-1219, 2049-2055` (comments only) — label bus-active early-return paths as `(dead-code fallback — bus-active path bypasses)`.

### Tests/infrastructure

- `tests/helpers/pause_report_orphan_scenarios.py` (new shared builder; extend with ReportInjection seeding helpers).
- `tests/unit/test_message_queue_pending_predicate.py` (new; includes defensive non-NULL `processing_task_id` case).
- `tests/unit/test_cascade_pause_resume.py` (extend; SQLite only; add `test_cte_work_id_exclusion_cross_engine_parity` SQLite variant).
- `tests/unit/test_pause_cascade_message_queue_orphan.py` (new).
- `tests/postgres/test_pause_report_orphan_reconciliation_pg.py` (new; `pg_engine` / `pg_two_connections`; add `test_cte_work_id_exclusion_cross_engine_parity` PG variant).
- `tests/integration/test_pause_during_report_turn_reaches_completed.py` (new; add post-reconcile re-fire assertion).
- CI/test script configuration for required SQLite and PostgreSQL jobs, including cross-engine parity and re-fire tests.

### Audited, no semantic change (4 audit-only child-decision sites)

- `daemon/services/child_reports.py:623/637/1598/1610` — child report-send decision logic. Parity tests added; semantics unchanged.

### Verified read-only (no code change)

- `daemon/repositories/message_queue/models.py:42-73` — correlation evidence (only `processing_task_id=NULL` is observed in production).
- `daemon/repositories/task/models.py:101-124` — correlation evidence; no schema change.
- `daemon/repositories/report_injection/models.py:42-58, 156` — PENDING/INJECTED/TASK_DELIVERED states; read by Phase 2.5 cleanup.
- `daemon/graph.py:2577-2621` — agent-node drain path that transitions PENDING → INJECTED.
- `daemon/manager.py:4963-5079` — existing `process_message` cleanup (out of scope, retained).

---

## Risks

| # | Risk | Impact | Likelihood | Mitigation |
|---|---|---|---|---|
| 1 | NULL `processing_task_id` cannot identify one work attempt exactly. | High — wrong attempt selection could drop retry work. | Medium; observed in production. | Use message ID only as a candidate locator, evaluate by projected work IDs, preserve mixed terminal/live states, and scope UPDATE 4 to Tasks returned by this cascade. **The direct-path branch is retained as future-proofing but not exercised by production data.** |
| 2 | SQL polarity drifts back to "NOT EXISTS live" and excludes no-Task/mixed rows incorrectly. | High. | Medium. | Truth-table test precedes implementation; one shared helper is used at all parent guards. The truth table includes a row for the defensive non-NULL `processing_task_id` case. |
| 3 | `WriteGuardSession` is mistaken for a cross-connection mutex. | High under PostgreSQL READ COMMITTED. | Medium. | Documentation forbids serialization claims; PostgreSQL uses the UPDATE 2/4 CTE returned set; two-connection tests cover both race orders. |
| 4 | UPDATE 4 reconciles historical incidents outside the current cascade. | High — ownership violation. | Low after revision. | Eligibility starts from `cancelled_tasks RETURNING`; tests seed unrelated historical orphans and assert they remain unchanged. |
| 5 | Dropping a report that the graph never consumed loses child output. | Critical. | **Low but not provably zero without a consumed marker.** | **Combined mitigation chain (Blocking Issue 4):** (a) restrict to `completion_report` cancelled by this cascade; (b) Phase 2.A drop is justified because the Task was RUNNING = consumption in progress (the parent's graph turn was actively driving with this report); (c) Phase 2.5 cleanup adds a ReportInjection consumption check — PENDING refuses by default, INJECTED/TASK_DELIVERED safely drops, unknown refuses unless `--force-drop`; (d) preserve `content` in the row for audit. **Document residual risk explicitly.** |
| 6 | A pending retry shares `message_id` with a cancelled parent but has a fresh `work_id`. | High — message-id-only logic can misclassify it. | Medium; `schedule_retry` explicitly does this. | Re-key state evaluation to work ID; NULL fallback mixed attempts remain countable and unreconciled; add dedicated tests. |
| 7 | Task deletion creates no-Task queue rows. | Medium — guard intentionally preserves them and may leave a stuck row. | Low during pause/resume. | Correct fact: Tasks **are deleted** during `_terminate_instance_db_sync` together with queue rows (`instance_lifecycle.py:2844-2871`), but are **not deleted** during pause/resume cascades. The incident occurs in pause/resume where Tasks become terminal. No-Task rows remain preserved because teardown/partial-failure provenance is ambiguous. |
| 8 | Direct pointer cast differs between SQLite and PostgreSQL (`processing_task_id` text vs `Task.id` integer). | High. | Medium. | Isolate dialect-safe cast in helper/SQL builder; compile and execute in both trees. **The cast is exercised only by the defensive non-NULL test case in production today.** |
| 9 | UPDATE 3 exposes contradictory active JobItem/orphan queue state. | High. | Low after placement fix. | UPDATE 4 runs immediately after UPDATE 2 and before UPDATE 3; ordering test inspects emitted SQL/failure boundary. |
| 10 | Hardened fallback code hides DependencyBus initialization failures. | Medium. | Low. | Do not alter bus gates or error policy; only reuse the predicate if a fallback is reached. Label the 3 fallbacks as `dead-code fallback — bus-active path bypasses` in code comments. |
| 11 | Child report-send decision queries are accidentally changed with parent guards. | High — premature/missing completion reports. | Medium. | Explicitly categorize `:623/637/1598/1610` as audit-only (not parent guards); parity regression tests. |
| 12 | PostgreSQL job silently skips because DB is unavailable. | High — SQLite-only false confidence. | Medium in CI. | Provision/probe DB before pytest and make probe failure fail the CI job; run PG serially. |
| 13 | Cleanup script updates too broadly or completes an instance with remaining work. | Critical. | Low with gating. | Default dry run; mandatory single instance; positive correlation; completion-report filter; show remaining queue/tasks/watchers; explicit apply and completion confirmations; transaction + RETURNING audit; **ReportInjection consumption check (Task 19)**. |
| 14 | All-or-nothing commit is broken during dialect branching. | High. | Low. | Keep UPDATE 1, UPDATE 2/4, UPDATE 3, and commit in one Session transaction; inject failures after each stage and assert rollback on both engines. |
| 15 | Reconciliation query impacts resume latency. | Medium. | Low. | Returned cancellation set bounds UPDATE 4; use indexed `Task.work_id`, `Task.message_id`, queue status/instance filters; inspect PostgreSQL EXPLAIN for representative tree sizes. |
| **16** | **Post-reconcile re-fire (Task 17) silently double-completes an instance.** | High. | Low (idempotency guards exist at `child_reports.py:1212-1219`). | Re-fire calls `_process_child_completion_db_sync(instance_id, completed_message_id=None, last_content="")`. The function's terminal-state short-circuit (`instance.status in (COMPLETED, ERROR, PAUSED)`) makes re-entry safe. Integration test (Task 17 acceptance) verifies that a cascade-reconciled instance reaches `COMPLETED` and a second re-entry short-circuits. |
| **17** | **PostgreSQL CTE snapshot divergence silently breaks dual-driver parity (Blocking Issue 2).** | Critical — same input, different outcomes. | Medium without exclusion; eliminated with `state.work_id <> ct.work_id`. | The exclusion is **load-bearing** in the CTE sketch (see §A2). Task 18 cross-engine parity test seeds the exact divergence scenario on both engines and asserts identical UPDATE 4 RETURNING sets. CI gate must run both variants. |
| **18** | **Phase 2.5 drops unconsumed PENDING ReportInjection content (Blocking Issue 4).** | Critical — silent data loss. | Medium without per-row check. | Phase 2.5 apply path queries `report_injection` state for every candidate row (Task 19). PENDING refuses unless `--force-rearm` or `--force-drop` is explicitly supplied. INJECTED/TASK_DELIVERED safely drops. Absent row refuses unless `--force-drop`. |
| **19** | **Post-reconcile re-fire fires while other writers are mid-cascade (TOCTOU).** | Medium. | Low. | Re-fire happens AFTER the cascade commit. Subsequent cascade writes see the reconciled rows as `completed`. The idempotency short-circuit at `child_reports.py:1212-1219` protects against double-finalization if the re-fire races with a parallel completion event. |

---

## Success Criteria

| # | Criterion | How to Measure | Threshold |
|---|---|---|---|
| 1 | Correct positive guard polarity | Truth-table tests on SQLite and PostgreSQL, **including the defensive non-NULL `processing_task_id` case** | 100% cases pass |
| 2 | No-Task rows are preserved/countable | Unit + PG tests | 0 no-Task rows reconciled or excluded |
| 3 | Retry attempts are distinguished by `work_id` | Cancelled parent + pending retry child test | Queue row remains countable/retriable in every mixed-attempt case |
| 4 | UPDATE 4 is scoped to this cascade | Seed returned and historical terminal Tasks together | 0 historical rows changed |
| 5 | Only completion reports are dropped | Seed every `MessageType` with identical Task state | Only `completion_report` changes; 0 other types changed |
| 6 | UPDATE 4 precedes UPDATE 3 | Ordering/rollback instrumentation | No path activates JobItem before reconciliation stage |
| 7 | PostgreSQL race correctness | Two connections, both race orders, READ COMMITTED | 0 forbidden final states across repeated runs |
| 8 | Primary production guard completes correctly | Exact production-state test (Task 12b) | Two orphans reconciled/excluded and instance reaches `COMPLETED` |
| 9 | Parent fallbacks do not drift | Same matrix at `:863`, `:2058`, and error `:270` | Identical predicate behavior across all 4 parent-completion sites |
| 10 | Child report-decision behavior is unchanged | Audit regression at `:623/637/1598/1610` | Existing expectations unchanged; parity assertions pass |
| 11 | Full pause/report/resume flow recovers | End-to-end test | Final instance `COMPLETED`; no scoped orphan rows |
| 12 | Transaction rollback remains all-or-nothing | Inject error at UPDATE 4 and UPDATE 3 | Instances, Tasks, messages, JobItems all revert |
| 13 | Dual-driver CI is real | Required SQLite and provisioned PG jobs | Both pass; PG job cannot green via skip |
| 14 | Existing production incidents can be remediated safely | Cleanup dry run + apply tests | Dry run performs 0 writes; apply changes only printed eligible rows |
| 15 | Documentation is factually accurate | Review checklist | No claim that WriteGuardSession serializes connections or Tasks are never deleted; "8 sites" reframed as "4 active + 4 audit-only" with 1 reachable + 3 dead-code fallbacks in the active set |
| **16** | **Post-reconcile re-fire self-heals new incidents** | Integration test (Task 17) | Pause → resume → reconcile → re-fire; instance reaches `COMPLETED` without operator action |
| **17** | **Cross-engine CTE parity** | `test_cte_work_id_exclusion_cross_engine_parity` on both engines | Identical UPDATE 4 RETURNING sets on the same input |
| **18** | **Re-fire does not double-complete** | Idempotency integration test | Second re-fire on a `COMPLETED` instance short-circuits via `child_reports.py:1212-1219` |
| **19** | **Phase 2.5 refuses to drop PENDING ReportInjection content** | Cleanup test (Task 19) | Default apply refuses PENDING; `--force-rearm` re-arms; `--force-drop` proceeds with warning; INJECTED/TASK_DELIVERED safely drops |
| **20** | **Defensive non-NULL `processing_task_id` path works** | Unit + PG test (Task 1 row + Task 2 acceptance) | Direct-path branch returns correct positive-polarity answer when `processing_task_id` is populated (defensive future-proofing) |

---

## Research Insights

- `message_queue` exposes `type` and `processing_task_id` but no `work_id` (`daemon/repositories/message_queue/models.py:42-73`). The `processing_task_id` column is `default=None` and **no producer in `daemon/` populates it today** — verified by exhaustive grep (only `models.py:72` definition and `repository.py:440` serializer; `claim_specific`/`dequeue` at `repository.py:170/203` do not set it).
- `Task.work_id` is unique/indexed while `Task.message_id` is non-unique (`daemon/repositories/task/models.py:101-124`).
- `schedule_retry` reuses `message_id` and creates a fresh `work_id` (`daemon/repositories/task/repository.py:1892-1934`).
- UPDATE 2 already uses `RETURNING id`, providing the seam for cascade-scoped identity (`daemon/services/instance_lifecycle.py:3425-3463`).
- UPDATE 3 currently follows UPDATE 2 (`daemon/services/instance_lifecycle.py:3465-3519`); revised UPDATE 4 belongs between them.
- The **only reachable production parent-completion guard** is `child_reports.py:1459`. Bus-active gates bypass `child_reports.py:863/2058` and `error_reporting.py:270` (verified by the `RuntimeError` raise at `child_reports.py:1252` and `bus is not None` early-returns at `child_reports.py:851-860` and `child_reports.py:2049-2055`).
- DependencyBus `count_pending_for_target` queries `DependencyWatcher` rows, NOT `message_queue` rows (`daemon/repositories/dependency_bus/repository.py:301-340`). The bus callback that triggers `_process_child_completion_db_sync` fires only from `_emit_terminal_via_bus` (`child_reports.py:198-288`) — **no watcher pending = no callback = no completion reevaluation.** This is the root cause of Blocking Issue 1.
- PostgreSQL fixtures are isolated under `tests/postgres/`; `pg_engine` and `pg_two_connections` already exist (`tests/postgres/conftest.py:159-178,244-265`). The existing cascade test is explicitly SQLite-only (`tests/unit/test_cascade_pause_resume.py:1-40`).
- Full teardown deletes `message_queue` and Task rows in one transaction (`daemon/services/instance_lifecycle.py:2844-2871`); pause/resume does not delete Tasks.
- The production manual remediation SQL and direct completion transition are documented at `docs/bugs/pause-during-report-turn-orphans-message-jobitem.md:285-316`.
- ReportInjection states: `PENDING` (initial), `INJECTED` (live agent-node drain at `graph.py:2577-2621`), `TASK_DELIVERED` (fallback `PROCESS_REPORT` task won claim). Defined at `daemon/repositories/report_injection/models.py:42-58, 156`. State transitions use guarded `WHERE state = 'PENDING'` UPDATEs.
- PostgreSQL data-modifying CTE sub-statements share one snapshot taken before the CTE's first UPDATE; SQLite reads post-UPDATE state. **This is the cross-engine divergence that the `state.work_id <> ct.work_id` exclusion neutralizes.**
- `_process_child_completion_db_sync` has explicit terminal-state short-circuit at `child_reports.py:1212-1219` (`COMPLETED`/`ERROR`/`PAUSED`), making post-cascade re-fire safe.

---

## Open Questions / Follow-ups

1. **Durable queue work identity:** should a later migration add `message_queue.work_id` and populate it at task creation/claim? This would eliminate the NULL fallback ambiguity. It is not required for this narrowly scoped fix.
2. **Durable consumption marker:** until the graph/checkpoint records that report content was consumed, UPDATE 4 must remain restricted to reviewed `completion_report` cases. Phase 2.5's ReportInjection check is a partial proxy (PENDING/INJECTED/TASK_DELIVERED states track drain/drain-or-task-delivery, but do not prove the report content reached the parent's final response).
3. **Fallback deletion:** the 3 DependencyBus-off parent guards are hardened, not removed. A separate cleanup can delete them only after tests and initialization policy no longer depend on fallback behavior.
4. **Operator completion API:** Phase 2.5 mirrors the manual direct status write. A future "reevaluate completion" service/API should emit normal SSE and CompletionRegistry side effects instead of requiring SQL. The Task 17 post-reconcile re-fire is a step in this direction (it uses the existing `_process_child_completion_db_sync` which emits the normal side effects).
5. **Decisions/overview synchronization:** after approval, update `decisions.md` and `plan-overview.md`, which still describe the rejected message-ID `NOT EXISTS` design. This revision intentionally changes only the requested Phase 2 file.
6. **`processing_task_id` population on claim (Open Question, Blocking Issue 3):** should `claim_specific` and `dequeue` in `message_queue/repository.py:170/203` be modified to set `processing_task_id=Task.id` when transitioning READY→PROCESSING? This would activate the direct-path correlation branch, eliminate the NULL-fallback ambiguity, and simplify the predicate helper. **Recommended as a follow-up PR** — it is a producer-side change with broad impact (every message claim path) and is independent of the Phase 2 fix. The Phase 2 helper already implements the direct-path branch, so this follow-up is purely additive.
7. **Legacy `pending_count` query at `child_reports.py:1459` after Task 17 re-fire:** the re-fire calls `_process_child_completion_db_sync` with `completed_message_id=None`, which makes the `message_id != completed_message_id` filter match ALL messages (no exclusion). The shared predicate helper (Phase 2.B) is the authoritative check, not this legacy query. After Task 17 ships, the legacy query is effectively dead code in the re-fire path but still primary in the normal child-completion path. A separate cleanup could route both paths through the shared predicate helper.

---

## Exit Criterion

All Tasks 1-19, including Task 12b, Tasks 17-19, are complete. Phase 2.A reconciles only cascade-returned `completion_report` work using PostgreSQL CTE/SQLite returned-row scoping before UPDATE 3, **with the `state.work_id <> ct.work_id` exclusion that neutralizes the PostgreSQL snapshot divergence**, and **post-commit re-fires the completion reevaluation** (Task 17) so new incidents self-heal. Phase 2.B uses one positive, work-ID-based predicate at the **1 reachable production guard** and the **3 dead-code fallbacks** (with accurate reachability labels) while preserving child report-decision semantics at the 4 audit-only sites. Phase 2.5 ships a dry-run-first single-instance cleanup with a **per-row ReportInjection consumption check** (Task 19) that refuses to drop unconsumed PENDING content by default. SQLite and provisioned PostgreSQL CI jobs pass, the two-connection READ COMMITTED race test has no forbidden outcomes, the **cross-engine CTE parity test (Task 18) has identical UPDATE 4 RETURNING sets on both engines**, and the exact production state reaches `COMPLETED` without dropping no-Task, mixed-retry, or non-`completion_report` work.