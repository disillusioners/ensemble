# Critical Review: Job as Queue Proxy — Collapse Execution State onto Instance

| Field | Value |
|---|---|
| **Reviewer** | Strategic Planner (ensemble) |
| **Date** | 2026-06-28 |
| **Document reviewed** | `docs/plans/job-as-queue-proxy.md` (DRAFT, 327 lines) |
| **Cross-referenced** | `docs/plans/virtual-job-management-surface.md` (D14), `docs/architecture/completion-authority.md`, `daemon/services/job_feedback_observer.py`, `daemon/services/work_resolver.py`, `daemon/repositories/job_queue/models.py`, `daemon/services/instance_lifecycle.py`, `daemon/services/job_processor.py`, `daemon/services/job_queue_service.py`, `daemon/services/job_retry_engine.py`, `daemon/services/dead_letter_service.py` |
| **Verdict** | **Approve with required edits before execution.** The destination architecture and phasing are correct. The §8 risk register catches the real hazards. Five blocking edits required (§§2.1–2.5); three minor improvements (§§3.1–3.3). |

---

## 1. What's sound

### 1.1 The diagnosis is correct and the codebase is already self-confirming

The plan's central observation — "the execution state on `JobItem` is a mirror, not an authority" — is not just plausible, it is **demonstrated by the existing code**:

- The drift warning at `work_resolver.py:692-712` exists for no other reason than to admit the mirror desyncs. A warning that *only exists because the design is wrong* is the strongest possible signal that the design is wrong.
- `_finalize_job_db_sync` (`job_feedback_observer.py:2436-2491`) writes the instance terminal state first and the job status second, **deriving** the job status from the instance status via the explicit map at `:2209-2212`. You don't write a derivation table unless the derived column is downstream of an authority.
- `completion-authority.md` already documents Instance + DependencyBus as the completion authority. The plan correctly notes that this authority is exercised today *despite* the JobItem mirror, not because of it.

The mirror has cost the codebase concrete lines of code (the ~300 of `_finalize_job_db_sync` that exist only to keep the columns in lockstep) and concrete bugs (every status-drift divergence that the warning has fired on in production).

### 1.2 The KEEP/DROP analysis in §2 is precise and well-evidenced

The line-cited table in §2.1 (DROP) and §2.2 (KEEP) makes the plan reviewable rather than aspirational. Particular strengths:

- **`instance_id` classified correctly.** §2.3 explicitly separates "the pointer" (keep) from "the status/timing/result that used to sit beside it" (drop). This is the right call: every retry mints a new instance, so `instance_id` is a *current-attempt handle*, not a job-level concept, and it is exactly the delegation handle the proxy needs.
- **`retry_count` correctly kept on the job.** A retry counter that survives across instance deaths cannot live on any single instance. The plan recognizes this and explicitly cites it (§2.2 row `retry_count`).
- **`DeadLetterItem` correctly kept as a second-chance queue, not collapsed.** The DLQ row's `error_message`/`retry_count`/`failed_at` are a *frozen autopsy snapshot*. Treating them as live execution state (and moving them to Instance) would lose the property that DLQ entries survive the original job's purge. The plan's call is right.
- **The `JobLock` table is correctly identified as already doing the work.** "`status='processing'` is partly redundant with lock presence" (§2.2 row `JobLock`) is a sharp observation. The lock table is already the cross-process concurrency primitive; the `processing` column is a denormalization of it that can drift.

### 1.3 The admission state machine (§3) is the right vocabulary

The 4-value `AdmissionState` (`queued` / `active` / `done` / `dead`) is the smallest vocabulary that captures:

- Queue membership (`queued`),
- "Dequeued, instance spawned, lock held" (`active`),
- "Terminal, no retry pending" (`done`),
- "Dead-lettered, autopsy in DLQ" (`dead`).

Two specific design decisions in §3.2 deserve praise:

- **Making `paused` an Instance concern, not a job concern.** The plan correctly identifies that pause correctness is *already* gated on instance status in three places (`_process_next_job` pre-check, `start_job` second-line defense, `claim_pending_task` SQL guard), and that the dual-write to `job_queue_items.status='paused'` is therefore redundant. Removing it shrinks the cascade surface area.
- **Making `failed` not a resting state.** The retry decision becomes synchronous at finalize (`active → queued` for retry, `active → done` for no-retry, `active → dead` for DLQ). This eliminates the window where a job sits `failed` with no living instance — a real source of stuck-job bugs today.

### 1.4 The phased rollout (§5) is the plan's best feature

The order — read → additive dual-write → gate cutover → writer cutover → column drop → frontend → cleanup — is the right shape:

- **Phase 1 (read authority) before Phase 2 (additive dual-write)** means consumers stop reading the mirror *before* you start changing it. If Phase 2 has a bug, no production read is affected.
- **Phase 4 (writer cutover) before Phase 5 (column drop)** means you can observe both columns in production for at least one full release cycle before committing to the drop migration. This is genuinely reversible.
- **D14 (`WorkResolverService` / `WorkRecord`) as the read landing zone** is the load-bearing decision. It was already built for exactly this — a read facade that hides whether state lives on Task, Job, or Instance. The plan correctly treats D14 as the prerequisite, not a parallel effort.

### 1.5 The §8 risk register catches the real hazards

Three risks in particular are well-graded:

- **§8.1 (pause semantics)** correctly audits the three gates and identifies the integration test that must be green before Phase 5.
- **§8.2 (retry-without-instance window)** correctly identifies the failure mode (a finalize path that doesn't route through `maybe_retry`) before naming the mitigation.
- **§8.6 (DLQ snapshot integrity)** correctly sequences the snapshot capture before the column drop, so `DeadLetterItem` already has the data it needs by the time `JobItem.error_message` is gone.

---

## 2. Critical issues — required edits before execution

### 2.1 🔴 CRITICAL: The `active ⇔ JobLock` invariant is load-bearing but unenforced

**The plan says (§5 Phase 0, recommendation):** denormalize `admission_state` and note lock-presence as the invariant `admission_state='active'` must satisfy. **The plan says (§5 Phase 3):** "Worth an assertion/CI check."

This is the single most important invariant in the new model, and the plan relegates it to "worth an assertion." It is more than that — it is **the concurrency correctness invariant of the entire system.** If `admission_state='active'` but the lock is gone, the worker pool will double-dispatch. If the lock exists but `admission_state != 'active'`, the defer-gate and `count_active_jobs*` queries miscount.

**Required edit:**

Add an enforcement layer at the write boundary, not just an observability check in CI. Specifically:

- A `_assert_active_invariant(session, job_id)` helper called from `start_job_atomic` (after lock acquisition, before commit) and from `_finalize_job_db_sync` (after lock delete, before commit). On Postgres, this can be a CTE check; on SQLite, an explicit SELECT-and-raise.
- Promote §8 of the Definition of Done from "asserted in CI" (§10 item 8) to "enforced at the repository write boundary; CI test verifies the helper fails closed."

Without this, a single missing lock-release path during the Phase 4 cutover will produce a silent concurrency bug that is *not observable* from any existing test pack, because none of the existing tests assert cross-table consistency on this pair.

### 2.2 🔴 CRITICAL: `maybe_retry` must become non-optional, not audited

**The plan says (§8.2):** "Audit all finalize callers (`_finalize_job`, `complete_job`, `complete_job_sync`, `JobRecoveryService._fail_orphaned_job`) to ensure each routes through `maybe_retry`."

Auditing is necessary but not sufficient. A future PR that adds a new finalize path (recovery, manual fail, scheduler-cancel) will skip the audit unless the type system forces it.

**Required edit:**

Introduce a single terminal-write boundary — call it `_finalize_terminal(instance_id, decision)` — that:

1. Accepts `decision` as a required, non-default parameter of a closed enum: `Decision.NO_RETRY` / `Decision.RETRY` / `Decision.DEAD_LETTER`.
2. Internally computes the admission transition (`done` / `queued` / `dead`) from the decision and the retry policy.
3. Calls `maybe_retry` internally.
4. Rejects being called without a decision (no `decision=None` default).

Then every existing finalize caller (`_finalize_job`, `complete_job`, `complete_job_sync`, `_fail_orphaned_job`, `cancel_job`'s terminal branch) routes through `_finalize_terminal`. A future PR that adds a new terminal path fails typecheck or fails to instantiate the decision, not production.

This converts §8.2 from a checklist into a structural guarantee. It also has the side benefit of making the §4 `_finalize_job_db_sync` description accurate — today it routes through multiple ad-hoc helpers; after this edit there is one entry point.

### 2.3 🔴 CRITICAL: Phase 1's API compatibility shim is underspecified and spans four phases

**The plan says (§5 Phase 1):** "`jobs_crud._job_to_response` / `JobResponse` schema: join `instances` for `status`, timing, `result_summary`, `error`. Keep returning the legacy field names for API compatibility, sourced from the instance."

The frontend does not migrate until Phase 6. That means **Phases 1–5 ship an API whose field semantics have changed but whose field names have not.** A field named `result_summary` on `JobResponse` sourced from `instance.result_summary` may have different nullability, different update latency (it's now updated at instance terminal, not job terminal — which may or may not be the same instant), or different write semantics (it's now `task.result` for turn-kind jobs per `work_resolver.py:763`).

This is exactly the class of bug that produces "it works locally, breaks in production" reports — a frontend that reads `result_summary` and gets a subtly different value than it did pre-refactor.

**Required edit:**

Two options, in order of preference:

- **Option A (preferred):** Add a `JobResponseV2` schema with explicitly renamed fields (e.g., `execution_status`, `execution_started_at`, `execution_completed_at`) sourced from the instance, alongside the existing `JobResponse`. Frontend migrates to `JobResponseV2` in Phase 2 (not Phase 6 — earlier). Legacy `JobResponse` is removed in Phase 7.
- **Option B:** Keep field names but add a `schema_version: 2` field on every response in Phases 1–5. Frontend checks `schema_version` and treats its absence as v1. This forces frontend to explicitly opt in to the new semantics.

Either way, **Phase 6 frontend migration is too late.** The frontend needs to be reading from the new source from Phase 2 onward, not from Phase 6.

### 2.4 🟡 HIGH: The `_STATUS_CANONICAL_MAP` cleanup is described but not assigned

**The plan says (§5 Phase 5):** "`work_status.py`: `_STATUS_CANONICAL_MAP` loses the JobItem execution-status entries; only `admission_state → canonical` (for `dead`) and Instance-status mapping remain."

This is in the scope list (`work_status.py` is in the §0 file list) but no phase has an explicit bullet for the `processing` / `paused` / `cancelled` entries. A reader of the plan cannot tell which phase deletes which entry.

**Required edit:**

Add an explicit Phase 4 bullet:

> Phase 4 — `work_status._STATUS_CANONICAL_MAP`: delete the `processing` / `paused` / `cancelled` / `failed` entries that mapped `JobStatus.*`. Add the `admission_state='dead' → dead_letter` mapping. All other canonical statuses now resolve from `Instance.status`.

And a Phase 5 bullet (or merge into the existing Phase 5 bullet) confirming the file is fully consistent post-drop. Otherwise this is a guaranteed bug source — a stale map entry that fires on `JobStatus` values that no longer exist will raise on every job read.

### 2.5 🟡 HIGH: §8.5 (1:N instance-per-job history limitation) is acknowledged but the DLQ implication is missed

**The plan says (§8.5):** "A job retried N times points to N terminal instances over its life; only the *current* attempt's `instance_id` is on the row. Reading 'this job's history' requires joining by `(project_id, agent_id, message)` or a future attempts table — out of scope here, but flag it."

The punt is correct. But there is an immediate consequence the plan does not note: **successful retries are invisible.** The DLQ snapshot only captures failed attempts. A job that fails twice and succeeds on attempt 3 leaves no trace of attempts 1 and 2 in any audit-friendly table — only the current (successful) instance row, plus the deleted earlier-instance rows.

This is fine for now, but the plan should **explicitly document** this as an intentional limitation so the next contributor who asks "where are the past attempts for this retried job?" doesn't try to invent a half-baked history table inside this refactor.

**Required edit:**

Add to §8.5 (or §11 Out of Scope):

> Successful-retry history is intentionally lost in this design. Only the current attempt's instance is addressable from the job row; failed attempts are addressable via `DeadLetterItem`; successful intermediate attempts are not retained. This is a pre-existing limitation (today's single `instance_id` has it too). If/when a job-history view is needed, it is a separate plan and a separate table.

---

## 3. Minor improvements — should fix, not blocking

### 3.1 Phase 0 should produce a written invariants document, not just a vocabulary sign-off

**The plan says (§5 Phase 0):** "Confirm §3 admission-state vocabulary with a second reviewer. Inventory every `count_active_jobs*` / `list_pending*` / defer-gate query that filters `status IN (...)`."

The vocabulary sign-off is good but light. The §2.1 fix above (load-bearing invariant) and the §2.2 fix (single terminal entry point) both depend on a precise statement of the invariants the new model maintains. Without a written invariants doc, the Phase 0 sign-off will pass on vibes.

**Suggested addition:** Phase 0 exit criterion includes a `docs/architecture/job-as-queue-proxy-invariants.md` document listing:

- `admission_state='active'` ⇔ a `JobLock` row exists with `instance_id = JobItem.instance_id` (or no instance_id — to be defined).
- `admission_state IN ('queued', 'active')` ⇔ `deleted_at IS NULL`.
- `admission_state='done'` ⇒ `instance_id` references a terminal instance.
- `admission_state='dead'` ⇒ a `DeadLetterItem` row exists for this `job_id`.

This serves both as the design spec for §2.1 enforcement and as the reference for Phase 9 test design.

### 3.2 The "active ⇔ lock-held" CI check is underspecified for the failure mode it catches

**The plan says (§5 Phase 3, invariant):** "Worth an assertion/CI check."

The CI check should not be a periodic consistency sweep (those run after the damage). It should be a **per-write check** at the repository layer (see §2.1). For the CI sweep specifically, recommend:

- A daily integration test that selects a random sample of `active` jobs and asserts each has a `JobLock` row.
- A nightly test that selects a random sample of `JobLock` rows and asserts each has an `active` job.
- Both directions, not just one. Single-direction sweeps miss the "lock exists but job isn't active" case, which is the failure mode that breaks concurrency accounting silently.

### 3.3 Phase 7 cleanup is described as "remove the flag" but the resolver-OFF path is non-trivial

**The plan says (§5 Phase 7):** "Remove `use_virtual_job_resolver` flag (now always-on), legacy branches in `tools/job_queue.py`, dead SSE param, and the dual-write shim."

The legacy branches in `tools/job_queue.py` include `_job_item_to_work_record_shim` (`:1139-1174`, in scope) but also the `if use_virtual_job_resolver:` branches in `job_get` / `job_list` / `watch_job` / etc. These are not just a one-line flag toggle — they are 100–200 lines of dual-path code with non-trivial MCP-tool semantics (MCP tool result schemas, error mapping, retry-hint fields).

**Suggested addition:** Phase 7 should include an explicit "verify MCP tool result schema is byte-identical between legacy and resolver-ON paths for a sample of inputs" step. The MCP layer is consumed by external agents that may not survive a result-shape change.

---

## 4. Cross-cutting observations

### 4.1 The plan is well-aligned with prior art in the repo

This plan is the natural completion of the D11/D13 message decouple and the D14 virtual-job surface. It does not invent new abstractions; it collapses existing ones. The completion-authority doc (`docs/architecture/completion-authority.md`) already establishes Instance + DependencyBus as the completion authority; this plan removes the only structural obstacle to that authority being the sole authority.

### 4.2 The plan correctly identifies what *is not* changing

§11 (Out of Scope) is appropriately conservative: Task/Instance unification is separate, job-attempts table is separate, defer-queue on message/task is separate. This is the right discipline — a refactor that also tries to fix adjacent problems is a refactor that doesn't ship.

### 4.3 Test impact (§9) is large but mechanical — true, and worth budgeting for

The test files listed (`test_work_resolver.py`, `test_jobs_streaming_resolver.py`, `test_cascade_pause_resume.py`, `test_pause_resume_root.py`, `test_job_queue_tools.py`, `test_task_repository.py`, `test_finalize_job_h15.py`) span ~7 major test files. The "reseed from Instance/Task instead of JobStatus" sentence hides a non-trivial effort — many of these tests are seeded with fixture builders that know the JobStatus enum. Estimate Phase 5 as ~30–50% of the total engineering time of this refactor, not a tail-end cleanup.

### 4.4 The Definition of Done (§10) is strong but missing the new artifact

§10 items 1–10 are all good. Add item 11:

> `docs/architecture/job-as-queue-proxy-invariants.md` exists (per §3.1 above) and the invariants it lists are enforced at the repository write boundary.

---

## 5. Summary

The destination architecture — JobItem as queue ticket, Instance as execution authority, `WorkRecord` as read facade — is the right model. The phasing — read landing zone first, additive dual-write second, gate cutover third, writer cutover fourth, column drop last — is the right execution strategy. The risk register catches the real hazards (pause dual-write §8.1, retry sync §8.2, DLQ snapshot timing §8.6) rather than cosmetic ones.

**Required edits before execution:**

1. Enforce `active ⇔ lock-held` at the repository write boundary (§2.1).
2. Make `maybe_retry` structurally non-optional via a single `_finalize_terminal(instance_id, decision)` entry point with required `decision` parameter (§2.2).
3. Specify API compatibility shim with versioned schema or earlier frontend migration; Phase 6 is too late (§2.3).
4. Assign explicit Phase 4/5 bullets for `_STATUS_CANONICAL_MAP` cleanup (§2.4).
5. Document successful-retry history loss as intentional limitation (§2.5).

**Minor improvements:** written invariants doc (§3.1), bidirectional CI invariant sweep (§3.2), MCP schema-equivalence verification in Phase 7 (§3.3).

With these edits, the plan is ready to execute. Estimated effort: 6–8 weeks of focused engineering across the 7 phases, with Phase 5 (column drop + test reseed) as the largest single chunk.