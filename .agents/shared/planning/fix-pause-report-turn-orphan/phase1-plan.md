# Phase 1: Fix Orphaned Active JobItem Resume Deadlock

> **Revision 2 (2026-08-01):** Applied council review amendments. CRITICAL: re-keyed all orphan correlation from `message_id` to `work_id` (prevents retry-cycle deadlock reproduction). Added W1-W4 (status_paused bind, exact-ID finalize threading, F1 race-window trade-off, expanded test matrix).

## Objective

Eliminate Bug A's permanent resume deadlock when `ask_questions` pauses a leader during a `PROCESS_REPORT` turn after its original `PROCESS_MESSAGE` Task has already reached a terminal state. Close both failure seams in a deliberately staged sequence: ship **Step A — guard hardening first** to restore forward progress for any fresh answer Task blocked by an orphaned `active` JobItem, then ship **Step B — resume routing correction** so this specific report-turn pause resumes through the root/checkpoint path and explicitly finalizes the orphaned JobItem (`active → done`) instead of relying on later cleanup.

The completed phase must preserve the F1 bifurcation behavior for genuinely in-flight work, preserve `PROCESS_REPORT` guard bypass, and keep `claim_pending_task` and `has_pending_tasks_blocked_by_busy_instance` logically identical on whether an active JobItem is a blocker.

## Scope

### In Scope

- Harden the cross-system admission guard in `TaskRepository.claim_pending_task` so an `active` JobItem whose backing `PROCESS_MESSAGE` Task is terminal no longer blocks a fresh `PROCESS_MESSAGE` answer Task. **The orphan exclusion MUST correlate via `task.work_id == job_queue_items.job_id` (NOT via `task.message_id == job_queue_items.metadata.message_id`)** — the `message_id` correlation re-introduces the deadlock on the retry path because `schedule_retry` reuses the parent's `message_id` while minting a fresh `work_id` for the retry child (`repository.py:1921, 1928`), so a `NOT EXISTS` keyed on `message_id` would find the live retry Task and block its own instance.
- Mirror the same orphan rule in `TaskRepository.has_pending_tasks_blocked_by_busy_instance`, preferably through a shared SQL helper so the P1/F11 invariant is structural rather than comment-only. The helper's bind set MUST include `status_paused` for the live-set subquery (the existing `has_pending_tasks_blocked_by_busy_instance` bind set at `repository.py:1506-1519` is incomplete — claim binds it at `:778`, busy-probe does not).
- Preserve the blocking behavior for an `active` JobItem backed by a `PENDING`, `RUNNING`, or `PAUSED` Task. `PAUSED` remains non-orphaned because pause cascade owns it and resume may re-arm/consume it.
- Add a repository primitive that recognizes the narrow report-turn-pause state: no resumable `PROCESS_MESSAGE` Task, most-recent `PROCESS_MESSAGE` Task terminal, and an active JobItem correlated to that Task by `job_queue_items.job_id == task.work_id` (NOT by `metadata.message_id == task.message_id`).
- Update `InstanceManager.resume_processing_job` to use that primitive as a fallback routing signal and take the existing root/checkpoint resume path.
- Thread the terminal Task's `work_id` through to `_process_resume_finalize` and use the exact-ID overload of `_get_processing_job_for_instance(instance_id, job_id=work_id)` to resolve the right JobItem when multiple historical rows exist for the same instance. `_process_resume_finalize` currently accepts a `job_id` parameter (`job_feedback_observer.py:1862`) but does not pass it through to the lookup at `:1945` — fix that threading.
- Verify `_process_resume_finalize` finalizes the correlated active JobItem and releases the admission slot without introducing a duplicate graph turn or a Task-with-no-JobItem answer artifact.
- Add focused repository/routing tests plus an E2E regression test named `test_pause_during_report_turn_then_resume`. **In particular, seed a retry-scenario regression where two Task rows share a `message_id` (parent CANCELLED + child PENDING retry) but have distinct `work_id`s** — this is the canonical regression test for the `message_id` → `work_id` re-keying.
- Run affected pause/resume, report-lane, cold-resume, and cross-system guard regression suites on PostgreSQL, with dual-driver coverage on SQLite where the test harness supports it.

### Out of Scope

- Changing the deliberate `PROCESS_REPORT` bypass of cross-system job coordination; report tasks remain governed only by pause and per-instance serialization gates.
- Broadening `find_paused_or_running_by_instance` to include `PROCESS_REPORT`; this would collapse the root-vs-child contract for normal report delivery and violate Report-Lane Decoupling.
- Adding schema columns or migrations. The fix should use existing `task.work_id`, `task.message_id`, `task.status/type`, `job_queue_items.admission_state`, `job_queue_items.job_id`, and instance fields. If implementation unexpectedly requires schema work, stop and re-plan with dual-driver migration handling and `_ensure_postgres_columns()`.
- Building a general orphan sweeper/reaper. Step A provides admission safety and Step B provides explicit cleanup for this incident path; lifecycle-wide garbage collection is a separate concern.
- Refactoring the entire resume/finalization pipeline or changing public message/JobItem creation semantics.
- Fixing unrelated pause/resume incidents or deferred risks outside Bug A.

## Sequencing and Dependencies

| Step | Name | Purpose | Ship Gate | Dependency |
|---|---|---|---|---|
| A | Harden orphan admission guard | Immediate deadlock unblock with minimal blast radius | Repository matrix passes on PostgreSQL and SQLite; P1/F11 predicates agree | None; ship first |
| B | Route report-turn pause through root resume | Structural cleanup: checkpoint resume and explicit JobItem finalization | Routing unit tests, observer race assertions, and E2E report-turn regression pass | Step A merged/deployed first |

**Release rule:** do not combine the rollout order even if both changes land in one development branch. Step A is independently releasable and must be validated before enabling/merging the hotter routing change. Step B assumes Step A remains as defense in depth for other active-orphan creation paths and for races that occur before explicit finalization.

**Correlation-axis invariant (Revision 2):** Both Step A's carve-out and Step B's routing primitive MUST correlate Task ↔ JobItem via `task.work_id == job_queue_items.job_id` (direct column join). The previous `task.message_id == job_queue_items.metadata.message_id` correlation is **invalid** because `schedule_retry` (`repository.py:1793-1935`) reuses the parent's `message_id` while minting a fresh `work_id` for the retry child (`:1921` and `:1928`). A `NOT EXISTS` keyed on `message_id` would find the live retry Task and block its own instance — reproducing the deadlock via automatic retry path (`retry_scheduled=true`, retry cycle ~10 min). The `work_id`-keyed correlation is also SIMPLER than the `message_id` approach: it is a direct column-to-column join (`task.work_id = j.job_id`) with no JSON extraction — and it is the pattern already established at `repository.py:640-645` for the existing Part 2 queue-awareness guard.

## Tasks

### Step A — Guard Hardening (Ship First)

| # | Task | File / Location | Depends On | Effort | Acceptance |
|---|---|---|---|---|---|
| A1 | Encode the orphan definition as a reusable, dialect-aware SQL fragment: a `queued` or `active` JobItem is excluded from the blocking set when no Task with `task.work_id = job_queue_items.job_id` exists in `PENDING`, `RUNNING`, or `PAUSED`. **Correlation is via `task.work_id = j.job_id` (direct column join — NO JSON extraction)**, NOT via `task.message_id = json_extract(j.metadata, 'message_id')`. The fragment references only bound status/admission parameters (no JSON-extract params). Its docstring explicitly notes that `message_id`-keyed correlation would deadlock on the retry path because `schedule_retry` mints a fresh `work_id` per retry child while reusing `message_id`. Reuse the existing bind-set expansion pattern (PG `BOOLEAN` vs SQLite `INTEGER 0/1` — Python booleans are passed as bound parameters, matching the `schedule_retry` dual-driver pattern at `:1859-1870`). | `daemon/repositories/task/repository.py:817-958` (`_json_extract_text_sql`, `_admitted_task_carve_out_sql`; add/factor helper adjacent to these). The existing Part 2 guard at `:640-645` (`WHERE _qi.job_id = task.work_id`) is the canonical shape to mirror. | none | 0.5 day | One helper can be interpolated with either `j` or `j_running`; it references only bound status/admission parameters (no `_json_extract_text_sql` call) and uses the direct column join `task.work_id = j.job_id`. Its docstring distinguishes this terminal-backed orphan exclusion from the F1 bifurcated unified-dispatcher carve-out and explicitly documents why `work_id` (not `message_id`) is the correlation axis. |
| A2 | Replace the queued-only orphan exclusion in the atomic claim query with the broadened predicate for `admission_state IN (queued, active)` and Task statuses `PENDING/RUNNING/PAUSED`. The broadened subquery uses `WHERE _orphan_check.work_id = j.job_id AND _orphan_check.status IN (PENDING, RUNNING, PAUSED)` (no JSON extraction). Preserve `task_type != PROCESS_MESSAGE OR ...` so only fresh message Tasks use this carve-out; do not alter the report-lane bypass or per-instance RUNNING/pause gates. **Remove the `_orphan_json_extract` bind (no longer needed) — the new predicate is column-to-column.** | `daemon/repositories/task/repository.py:646-810`, specifically current orphan exclusion at `:757-763` and parameter binds at `:771-810` (the `_orphan_json_extract` bind was at `:480-488` and is now unused). | A1 | 0.5 day | A fresh pending `PROCESS_MESSAGE` answer is claimable when the same instance has an `active` JobItem correlated (via `job_id == work_id`) to only terminal (`COMPLETED`, `CANCELLED`, or `FAILED`) backing Task rows. The same candidate remains blocked if any correlated Task (via `work_id == job_id`) is `PENDING`, `RUNNING`, or `PAUSED`. `PROCESS_REPORT` behavior is unchanged. The `_orphan_json_extract` bind is no longer referenced anywhere in the file. |
| A3 | Mirror the broadened orphan exclusion in the busy-instance probe using the exact shared fragment. **Bind set MUST include `status_paused`** — the existing probe bind set at `repository.py:1506-1519` only binds `status_pending`, `status_running`, `status_waiting_children`, `status_queued_admission`, `status_active_admission`. The new shared helper requires `status_paused` for the `task.status IN (PENDING, RUNNING, PAUSED)` live-set subquery; without it the helper will raise `KeyError`/`MissingParameter` at execute time. The claim path already binds `status_paused` at `:778`, so this is a **required bind-set expansion on the busy-probe only**. Update comments/docstrings that currently describe only pending/running admission and ensure the method reports "not blocked" whenever `claim_pending_task` can admit the same candidate. | `daemon/repositories/task/repository.py:1408-1520`, especially blocker predicate around `:1465-1500`, the `_admitted_task_carve_out_sql` interpolation at `:1500`, and bind set at `:1506-1519` | A1 | 0.5 day | For every guard matrix fixture, `has_pending_tasks_blocked_by_busy_instance()` agrees with the claim query: active+terminal-backed returns false and permits claim; active+pending/running/paused-backed returns true and denies claim. The bind dict at `:1506-1519` now includes `status_paused`. No copied variant of the orphan SQL remains at the two call sites. |
| A4 | Add a parameterized repository regression matrix for JobItem admission state (`queued`, `active`) × backing Task state (missing, `PENDING`, `RUNNING`, `PAUSED`, `COMPLETED`, `CANCELLED`, `FAILED`) × candidate type (`PROCESS_MESSAGE`, `PROCESS_REPORT`). Include the incident-defining active+completed case and explicit F1 protections. **Add a retry-scenario regression row (see W4 case 1):** seed a CANCELLED parent Task and a PENDING retry child Task with the SAME `message_id` but DIFFERENT `work_id`s; the active JobItem's `job_id` matches the CANCELLED parent's `work_id`. Assert the carve-out correctly identifies the JobItem as an orphan (no Task with matching `work_id` is non-terminal) and admits the fresh answer Task. This is the KEY regression test for the `message_id` → `work_id` re-keying. Place tests with existing cross-system serialization/guard tests rather than creating an unrelated fixture stack. | Primary: `tests/test_message_job_serialization.py` (existing cross-system/FIFO guard coverage around `:279-700`); inspect/update `tests/test_report_lane_phase2.py:418-968` for report bypass/pause assertions | A2, A3 | 1 day | Assertions prove: (1) active+terminal-backed admits the fresh message; (2) active+running-backed blocks it; (3) active+paused-backed blocks it; (4) active+pending-backed blocks it; (5) queued orphan behavior does not regress; (6) reports still bypass the cross-system guard; (7) busy-probe output matches actual claim outcome; (8) **retry scenario (parent CANCELLED + retry child PENDING with same `message_id`, distinct `work_id`s) admits the fresh answer Task**; (9) **multiple Tasks with same `work_id` (defensive) treats any non-terminal match as "live"**; (10) **multi-JobItem-per-instance evaluates each JobItem independently (one orphan + one live → orphan excluded, live still blocks)**. Tests exercise real SQL rather than string matching. |
| A5 | Execute Step A's focused test matrix on PostgreSQL first, then SQLite. Run the broader report-lane and message serialization suites and inspect query plans/log output for parameter/bind failures. **Confirm that the `status_paused` bind added in A3 produces no `MissingParameter` errors on either driver.** | Test commands documented in PR/implementation notes; no production file | A4 | 0.5 day | All new tests pass on PostgreSQL and SQLite. Existing `tests/test_message_job_serialization.py` and `tests/test_report_lane_phase2.py` pass unchanged except intentional expectation additions. No SQLite-only SQL (`rowid`, unportable JSON operators) is introduced. |

### Step B — Resume Routing Fix (Ship Second)

| # | Task | File / Location | Depends On | Effort | Acceptance |
|---|---|---|---|---|---|
| B1 | Define the repository primitive and its return contract. Recommended shape: `find_resume_root_candidate_by_active_job(instance_id) -> Task | None`, returning the terminal backing `PROCESS_MESSAGE` Task (including its stable `work_id`) rather than only a JobItem ID, because the existing root path consumes `Task.work_id`. **The query MUST correlate JobItem ↔ Task via `job_queue_items.job_id = task.work_id` (direct column join)**, NOT via `job_queue_items.metadata->>'message_id' = task.message_id` (JSON extraction). The active JobItem is identified by `job_queue_items.admission_state = 'active' AND job_queue_items.deleted_at IS NULL AND job_queue_items.job_id = task.work_id`. The query selects the most-recent `PROCESS_MESSAGE` Task only when it is terminal, no `PAUSED`/`RUNNING`/`CANCELLED` resumable message Task exists under the existing route semantics, and a non-deleted `active` JobItem for the instance has matching `job_id`. Resolve any ambiguity in `CANCELLED`: the existing method treats it as a resumable marker, so the fallback must not steal that case. | `daemon/repositories/task/repository.py:171-244` plus nearby query helpers; JobItem models/constants in `daemon/repositories/job_queue/models.py` | Step A shipped; design review of return contract | 0.5 day | Method has an explicit narrow contract, deterministic newest-first ordering, active-only admission filtering, `deleted_at IS NULL`, `job_id == work_id` correlation (no JSON extraction), and dual-driver SQL handling. It returns `None` for ordinary children, report-only histories, missing JobItems, queued JobItems, or any instance already recognized by `find_paused_or_running_by_instance`. Docstring explicitly explains why `work_id` is the correlation axis (retry path reuses `message_id`). |
| B2 | Implement the primitive using repository-layer SQL/SQLModel only. The SQL must use direct column joins (`task.work_id = job_queue_items.job_id`) and avoid any JSON extraction. Add indexed-field predicates before correlation where possible (`instance_id`, task type/status, JobItem admission state, `job_id`) and avoid schema changes. Document why it is a fallback detector for report-turn pauses rather than a replacement for the existing `PROCESS_MESSAGE` lookup. | `daemon/repositories/task/repository.py:171-244` and helper area `:817-958` | B1 | 1 day | Real-DB tests return the correlated terminal Task for the incident state and no row for all negative controls (queued/done/deleted JobItem, ordinary child, no JobItem, `CANCELLED` resumable Task). Query works on PostgreSQL and SQLite without `rowid` or backend-only syntax. **W4 case 2: multiple Tasks with same `work_id` (should not happen by design, but defensive) is handled gracefully — any non-terminal match counts as "live".** |
| B3 | Add repository unit tests for the routing primitive: terminal message Task + matching active JobItem (positive); latest Task running/paused/cancelled (existing route owns it); terminal Task + queued/done/deleted/nonmatching JobItem; active JobItem tied to an older terminal Task while a newer message Task exists; report Task as newest overall but terminal message Task as newest within `PROCESS_MESSAGE`; and ordinary child/no-JobItem (negative). **Add the W4 retry-scenario regression:** seed a CANCELLED parent and a PENDING retry child (same `message_id`, different `work_id`s). The active JobItem's `job_id` matches the CANCELLED parent's `work_id`. The primitive must select the CANCELLED parent as the candidate (because the active JobItem correlates to it via `work_id`) and return `None` for the retry child path (the retry child has no active JobItem). | `tests/unit/test_pause_resume_root.py` (existing root routing repository fixture), with focused helper additions | B2 | 1 day | Each state selects exactly one expected route candidate or `None`; ordering and `work_id == job_id` correlation are pinned; no test relies only on mocks for the SQL primitive. Retry-scenario regression passes deterministically. |
| B4 | Update `resume_processing_job` routing to perform the existing `find_paused_or_running_by_instance` lookup first, then invoke the new active-orphan fallback only when that lookup returns `None`. When fallback succeeds, feed its `work_id` into the established root branch and preserve deduplication, stale-message cleanup, checkpoint resume, and `_resume_processing_background` contracts. Introduce explicit structured logging that distinguishes `root_existing_task`, `root_active_orphan`, and `child` decisions. Do not enqueue `source="cascade_resume"` on the active-orphan route. | `daemon/manager.py:4821-4961` (routing and early child return), with root cleanup continuing through `:4963-5079` | B2, B3 | 1 day | Incident fixture chooses root route; `enqueue_message` is not called; `old_job_id` is the correlated terminal Task's stable `work_id`; the existing root background resume is scheduled once. Ordinary child and current paused/running/cancelled message paths retain prior behavior. |
| B5 | Pin the finalization/race contract. Trace the selected Task `work_id` through `_resume_processing_background` (already passes `old_job_id=task.work_id` at `manager.py:4956`) to `JobFeedbackObserver._process_resume_finalize`. **Threading fix:** `_process_resume_finalize` (`job_feedback_observer.py:1859`) currently accepts a `job_id` parameter (`:1862`) but does NOT pass it to the lookup at `:1945` (`_get_processing_job_for_instance(instance_id)` — the `job_id` argument is dropped). Change the lookup to `await self._get_processing_job_for_instance(instance_id, job_id=work_id)` so the F13 exact-ID overload at `job_feedback_observer.py:632-712` resolves the JobItem by exact ID. Without this, the observer might pick a DIFFERENT `active` JobItem (by freshest-`created_at` ordering) when multiple historical rows exist for the instance, leaving the orphaned JobItem un-transitioned. Verify the observer resolves and transitions the active JobItem associated with the instance/`work_id`, releases the slot, and remains idempotent when lifecycle-event finalization races it. Add or extend a unit test that makes both finalize paths contend and asserts one terminal transition, no failure, and no residual `active` admission. **Add a multi-JobItem-per-instance test (W4 case 3):** seed two `active` JobItems for the same instance, each with a different `work_id`; assert the exact-ID overload picks the right one and the other JobItem is left untouched. **Add a silent-cascade-resume interaction test (W4 case 4):** a cascade-resume for a child instance (not the report-turn-pause case) must still take the child branch — verify the fallback primitive returns `None` for ordinary children. **Add a `_graph_tasks` dedup test (W4 case 5):** if the report-turn-pause instance is already in `_graph_tasks` (another resume is in-flight), the fallback must deduplicate (return `already_resuming`) rather than start a second graph turn. | `daemon/manager.py:5292-5298` (`_process_resume_finalize` call site with `old_job_id` already threaded); `daemon/services/job_feedback_observer.py:1945` (the lookup to change); `_get_processing_job_for_instance` at `:632` (already accepts `job_id` overload); relevant tests in `tests/unit/test_pause_resume_root.py` and/or `tests/test_finalize_job_threading.py` | B4 | 1 day | Exactly one writer performs `active → done`/terminal transition; the losing finalize is a safe no-op via the existing conditional transition; instance lock/admission slot is released once; final instance/job state is consistent. Any mismatch between Task `work_id` and JobItem ID resolution is caught by the test rather than papered over. Multi-JobItem-per-instance test confirms the exact-ID overload selects the right JobItem; `_graph_tasks` dedup test confirms `already_resuming` is returned for an already-in-flight instance. |
| B6 | Add manager-level routing tests around `resume_processing_job` with mocked graph/queue dependencies but real repository results: active-orphan fallback selects root; no fallback selects child; silent child remains no-op; existing resumable Task takes precedence over fallback; concurrent `_graph_tasks` deduplication returns `already_resuming` with the correct work ID. | `tests/unit/test_pause_resume_root.py`; check compatibility with `tests/unit/test_cascade_pause_resume.py` and `tests/test_resume_gate.py` | B4 | 1 day | Root-vs-child contract is asserted by observable calls and return values, not logging alone. No Task-with-no-JobItem is created in the active-orphan case. Existing child behavior is unchanged. |
| B7 | Implement E2E regression `test_pause_during_report_turn_then_resume`. Drive a leader to `WAITING_CHILDREN`, ensure its original `PROCESS_MESSAGE` Task is terminal, trigger/detect a `PROCESS_REPORT` turn, fire `ask_questions` pause while that report turn is in flight, submit the answer, and wait for terminal completion. Capture intermediate DB/API state to prove the test exercised the intended seam rather than a normal message-turn pause. | `tests/e2e/test_e2e_workflows.py`, near existing `test_pause_after_spawn_then_resume` at `:1561`; helper additions local to E2E module as needed | B5, B6 | 1.5–2 days | Before resume: original message Task terminal, report Task in-flight/owned by pause path, JobItem active. After answer: route is root (observable via state/log or absence of fresh no-JobItem answer Task), JobItem reaches done/terminal admission, instance reaches expected terminal state within configured timeout, and no pending answer Task remains blocked. Test fails deterministically on pre-fix code. |
| B8 | Run the full regression set on PostgreSQL, then dual-driver repository tests on SQLite: new E2E, existing pause-after-spawn E2E, root pause/resume, cascade pause/resume, cold resume TTL, report lane, guard/serialization, and finalize threading. Repeat the new E2E enough times to expose timing races (minimum 10 consecutive runs in CI/stress mode). | `tests/e2e/test_e2e_workflows.py`; `tests/unit/test_pause_resume_root.py`; `tests/unit/test_cascade_pause_resume.py`; `tests/integration/test_cold_resume_ttl.py`; related suites above | B7 | 1 day plus CI runtime | PostgreSQL full set is green; SQLite repository/unit set is green; new E2E passes 10/10 consecutive runs with no orphaned active JobItem, blocked answer Task, duplicate resume, or double-finalize error. |

## Coupling

- **Tight with Step A:** Step B relies on Step A remaining as a defensive admission escape hatch until/if explicit JobItem finalization completes. Both steps share the definition of "active JobItem backed only by terminal Task state." Both use the SAME correlation axis (`task.work_id == job_queue_items.job_id`) — if either step drifts back to `message_id`, the retry-cycle deadlock resurfaces.
- **Tight with JobFeedbackObserver:** The route's chosen `Task.work_id` must remain compatible with `_resume_processing_background` and `_process_resume_finalize`; changing the return type to a raw JobItem ID without adapting consumers risks skipped cleanup. Additionally, the W2 threading fix in B5 changes `_process_resume_finalize` to pass `job_id=work_id` into the F13 exact-ID overload of `_get_processing_job_for_instance` — this is a behavior change for the finalize path, not just for the active-orphan route (all callers of `_process_resume_finalize` benefit from the threading fix once applied). Reviewer should confirm this is intended and acceptable; if any caller relies on the freshest-`created_at` ordering, it should be flagged before merge.
- **Tight with F1 bifurcation:** `_admitted_task_carve_out_sql` intentionally distinguishes queued mirrors from active work. The new terminal-orphan exclusion must not transform every active+completed combination into global permission for arbitrary racing work; it applies inside the fresh `PROCESS_MESSAGE` candidate's cross-system guard and still treats `PENDING/RUNNING/PAUSED` backing Tasks as live. **The F1 race-window trade-off** (see Risk #11) is accepted to unblock the permanent deadlock; the window is bounded by Task → graph-stream teardown latency and is strictly less severe than the deadlock it prevents.
- **Loose with Report-Lane Decoupling:** Report tasks remain bypassed. The report turn is evidence for why the original message Task is no longer resumable, but it must not be added to the old message-task selector.
- **Independent of schema/migrations:** No data model change is expected.

## Risks & Mitigations

| # | Risk | Impact | Likelihood | Mitigation |
|---|---|---|---|---|
| 1 | **F1 bifurcation regression:** active+completed is treated as orphan while a parent is still mid-`astream`, allowing a fresh message to race real in-flight work. | High | Medium | Scope the exclusion to incoming `PROCESS_MESSAGE` claim logic; require absence of correlated `PENDING/RUNNING/PAUSED` backing Tasks (via `work_id == job_id`); retain the existing bifurcated helper; add negative active+running and active+paused cases plus a child-report-race regression. Ship Step A independently and review generated SQL. |
| 2 | **Claim/busy-probe divergence (P1/F11 recurrence):** claim permits work while the worker believes it is busy, or vice versa. | High | Medium | Put the terminal-orphan predicate in one alias-parameterized helper and interpolate it at both sites. **Bind set on the busy-probe MUST include `status_paused`** — the existing bind set at `:1506-1519` is incomplete; without this addition the helper raises `KeyError`/`MissingParameter`. Parameterize tests to assert both methods for every state combination. |
| 3 | **Double-finalize race:** `_process_resume_finalize` and normal `JobFeedbackObserver._process_event` both finalize the same active JobItem. | High | Medium | Preserve the existing conditional/atomic terminal transition (`WHERE` on processing/active status), add a contention test, and assert one winner plus a safe loser and one lock release. Do not add a pre-check as an authoritative gate. |
| 4 | **Root-vs-child contract violation:** ordinary child instances or report-only instances are misclassified as roots and incorrectly resume a checkpoint. | High | Medium | Keep the existing message-task query first; make the fallback require terminal `PROCESS_MESSAGE` + matching active JobItem (correlated by `work_id == job_id`); add negative child, missing/mismatched JobItem, and report-only tests. Log the route reason explicitly. |
| 5 | **Work-ID identity mismatch:** fallback returns JobItem ID while downstream expects Task `work_id`, causing failure cleanup or finalization to skip silently. | High | Medium | Return the correlated Task from the primitive and preserve `old_job_id = task.work_id`; add an end-to-end assertion on active→done transition and unit coverage around consumer resolution. |
| 6 | **Paused ownership is misclassified as orphan:** excluding `PAUSED` from the live-state subquery admits an answer before cascade resume owns/re-arms the prior turn. | High | Low | Include `PAUSED` with `PENDING/RUNNING` in the orphan-check live set and pin a negative test for active+paused. **Bind `status_paused` in both the claim and busy-probe bind dicts** — busy-probe currently omits it (W1). |
| 7 | **Most-recent task ambiguity:** an older terminal message Task matches an active JobItem while a newer message operation exists, selecting the wrong checkpoint/root context. | High | Low | Define newest `PROCESS_MESSAGE` ordering explicitly and require correlation against that row; include multi-history tests. Consider a single SQL query/transactional snapshot rather than separate raceable reads. |
| 8 | **Cross-driver SQL incompatibility:** PostgreSQL JSONB and SQLite JSON extraction/status binds behave differently. | High | Medium | The new `work_id == job_id` correlation is a direct column-to-column join — NO JSON extraction is needed in Step A's carve-out or Step B's primitive, eliminating the JSONB/JSON portability concern entirely for the new code. The dual-driver concerns that remain (e.g., the existing `_admitted_task_carve_out_sql` at `:853-958`) are unchanged. Test PostgreSQL first and SQLite second. Do not use `rowid` or backend-only operators outside the existing helper. |
| 9 | **E2E timing flakiness:** test pauses before/after the report-turn seam and gives false confidence. | Medium | High | Add deterministic state polling/hooks for terminal original message Task + in-flight report Task + active JobItem before pausing; assert preconditions; run 10 consecutive repetitions. |
| 10 | **Step A masks Step B failure:** answer progresses through the child path, so E2E reaches completion while active JobItem cleanup/routing remains wrong. | High | Medium | E2E must assert root-route evidence, no new `cascade_resume` no-JobItem Task artifact, and explicit JobItem terminal state—not merely eventual instance completion. |
| 11 | **F1 race-window acceptance (Revision 2, W3):** the broadened carve-out admits a fresh `PROCESS_MESSAGE` Task when the active JobItem's backing Task is terminal. There is a narrow race window (<1s) where the parent might still be mid-`astream` (the Task completed but the graph stream hasn't fully unwound). **We ACCEPT this race-window risk to unblock the permanent deadlock.** The window is bounded by the Task → graph-stream teardown latency and is strictly less severe than the permanent deadlock it prevents. This trade-off is documented in `decisions.md` as Decision D-A-2's justification for the negative live-set, and is reaffirmed as Decision D-A-7 (race-window acceptance). | Medium | Low | The race window is bounded by Task → graph-stream teardown latency (typically <1s). The alternative (the current permanent deadlock) is strictly worse. The mitigation is the F1 bifurcation: the carve-out only fires for fresh `PROCESS_MESSAGE` claims, not for arbitrary racing work; PAUSED/RUNNING backing Tasks still block; and the report-lane bypass is preserved. Document this trade-off prominently in `decisions.md` so future reviewers don't reflexively "tighten" the carve-out and re-introduce the deadlock. |
| 12 | **`_process_resume_finalize` `job_id` parameter currently unused (Revision 2, W2):** the helper accepts a `job_id` parameter but does not pass it to the lookup. Threading it through changes finalize behavior for ALL callers (not just the active-orphan path). | Low | Low | The change is narrowly an improvement — using the exact-ID overload is strictly safer than freshest-`created_at` ordering when multiple historical rows exist. Confirm with reviewer that no caller relies on the legacy freshest ordering; if any do, flag them in PR description. The multi-JobItem-per-instance test (W4 case 3) will surface any regression. |

## Success Criteria

| # | Criterion | How to Measure | Threshold |
|---|---|---|---|
| 1 | `claim_pending_task` admits a fresh `PROCESS_MESSAGE` Task when the instance has an active JobItem whose correlated backing Task (via `work_id == job_id`) is terminal. | Real repository test on PostgreSQL and SQLite for `COMPLETED`, `CANCELLED`, and `FAILED`. | 100% of terminal-state cases return the candidate Task. |
| 2 | The guard still blocks when the active JobItem has live/resume-owned backing work. | Parameterized repository tests for `PENDING`, `RUNNING`, and `PAUSED`. | 100% return no claim for the fresh message candidate. |
| 3 | Claim and busy-probe predicates agree. | For each state in the admission/backing-task matrix, compare actual claimability with `has_pending_tasks_blocked_by_busy_instance`. | Zero mismatches across all matrix cases on both DB drivers. |
| 4 | Report-lane semantics remain unchanged. | Existing/new tests claim `PROCESS_REPORT` despite unrelated active JobItem, subject to pause/per-instance gates. | All report-lane regression tests pass; no new cross-system blocking of reports. |
| 5 | Report-turn-pause resume selects the root route. | Manager unit test and E2E precondition state with no resumable message Task but matching active JobItem + terminal latest message Task. | Root background resume called exactly once; `enqueue_message(source="cascade_resume")` not called. |
| 6 | Ordinary routing remains correct. | Unit cases for paused/running/cancelled message Task, child/no JobItem, and silent child. | Existing route selected in every control case with unchanged return contract. |
| 7 | Orphaned JobItem is explicitly finalized. | Inspect JobItem/admission state and lock after resume in unit/integration/E2E. | JobItem is no longer `active`; terminal/done state committed and slot/lock released within test timeout. |
| 8 | Finalization is race-safe and uses exact-ID resolution. | Concurrent `_process_resume_finalize` and lifecycle-event finalize test; multi-JobItem-per-instance test confirms the F13 exact-ID overload picks the right JobItem. | Exactly one state transition; zero exceptions; consistent terminal instance/job state; multi-JobItem test selects the right JobItem. |
| 9 | Incident is reproduced and fixed end to end. | `test_pause_during_report_turn_then_resume` with asserted intermediate seam state. | Passes 10/10 consecutive PostgreSQL runs; pre-fix implementation fails the deadlock/routing assertions. |
| 10 | No regressions in known pause/resume paths. | Run listed unit/integration/E2E suites. | 100% pass on primary PostgreSQL; repository/unit dual-driver suite passes on SQLite. |
| 11 | Retry-cycle deadlock does NOT re-emerge (Revision 2). | W4 case 1: seed parent CANCELLED + retry child PENDING with same `message_id`, distinct `work_id`s; active JobItem's `job_id` matches the CANCELLED parent's `work_id`. Assert carve-out admits the fresh answer Task. | 100% pass on both DB drivers. This is the canonical regression test for the `message_id` → `work_id` re-keying. |
| 12 | `status_paused` bind added to busy-probe (Revision 2, W1). | Direct unit test on `has_pending_tasks_blocked_by_busy_instance` with a `PAUSED` backing Task; assert no `MissingParameter` exception and correct return value. | Test passes; bind set contains `status_paused`. |

## Test Strategy

### 1. Repository Unit/Integration Tests — Guard Hardening

Use real database engines and persisted `Instance`, `Task`, and `JobItem` rows. Avoid mocking SQL results.

Minimum matrix:

| JobItem State | Backing Task | Fresh `PROCESS_MESSAGE` Claim | Busy Probe | Why |
|---|---|---|---|---|
| active | completed/cancelled/failed | admitted | not blocked | New orphan rule / incident case |
| active | pending/running/paused | blocked | blocked | Preserve live work and pause ownership |
| active | missing | admitted | not blocked | Active orphan with no backing Task |
| queued | missing or terminal | admitted | not blocked | Preserve queued-orphan/F1 behavior |
| active | running | `PROCESS_REPORT` admitted unless pause/running-instance gate applies | consistent | Preserve Report-Lane Decoupling |

Also test null/nonmatching `work_id`, deleted JobItems, and multiple Task histories. Verify SQL parameter completeness, especially `status_paused` in BOTH claim and busy-probe bind dicts.

**Revision 2 W4 additional test cases (5 new tests):**

1. **Retry scenario — multiple Tasks with same `message_id` (KEY REGRESSION):** Seed a CANCELLED parent Task and a PENDING retry child Task with the SAME `message_id` but DISTINCT `work_id`s (mirroring `schedule_retry` at `repository.py:1921` and `:1928`). The active JobItem's `job_id` matches the CANCELLED parent's `work_id`. Assert the carve-out correctly identifies the JobItem as an orphan (because no Task with matching `work_id` is non-terminal) and admits the fresh answer Task. This is the KEY regression test for the `message_id` → `work_id` re-keying — under the OLD `message_id`-keyed predicate, this scenario would deadlock; under the NEW `work_id`-keyed predicate, it is admitted correctly.

2. **Multiple Tasks with same `work_id` (defensive, should not happen by design):** Assert the predicate handles it gracefully — treats any non-terminal match as "live". Even though `task.work_id` has a UNIQUE constraint (per `repository.py:1940` "reusing the parent's work_id would violate the UNIQUE constraint"), this test defends against future constraint violations or synthetic test fixtures.

3. **Multi-JobItem per instance (W4 case 3):** Seed two `active` JobItems for the same instance, each with a different `work_id`. Assert the carve-out evaluates each independently (one orphan + one live → the orphan is excluded from the blocking set, but the live one still blocks). This test catches any unintended `LIMIT 1` or `DISTINCT` collapsing in the carve-out subquery.

4. **Silent cascade-resume interaction (W4 case 4):** A cascade-resume for a child instance (not the report-turn-pause case) must still take the child branch. Verify the fallback primitive `find_resume_root_candidate_by_active_job` returns `None` for ordinary children (no resumable `PROCESS_MESSAGE` Task, no matching active JobItem).

5. **`_graph_tasks` dedup with fallback (W4 case 5):** If the report-turn-pause instance is already in `_graph_tasks` (another resume is in-flight), the fallback path in `resume_processing_job` must deduplicate (return `already_resuming`) rather than start a second graph turn. Verify the manager-level routing respects this even on the fallback path.

### 2. Repository Unit Tests — Routing Primitive

Build database states around the exact predicate, including:

- Positive: most-recent `PROCESS_MESSAGE` terminal; matching active JobItem (via `job_id == work_id`); optional report Task more recent/in-flight.
- Negative: existing paused/running/cancelled message Task (old route owns it).
- Negative: no message Task, no JobItem, queued/done/deleted JobItem, or mismatched `work_id == job_id`.
- Ordering: older matching terminal message Task must not win if the newest message Task represents another operation.
- Retry scenario (W4 case 1, see §1 above): seed parent CANCELLED + retry child PENDING with same `message_id`, distinct `work_id`s. Primitive must select the CANCELLED parent (because the active JobItem correlates to it via `work_id`); returns `None` for the retry-child path (retry child has no active JobItem of its own).
- Driver parity: execute on PostgreSQL and SQLite fixtures where available.

### 3. Manager Routing Unit Tests

Exercise `resume_processing_job` with controlled repositories and graph-task registry:

- Existing resumable Task → existing root route, no fallback needed.
- No existing Task + active-orphan fallback → root route, stable Task `work_id`, no `enqueue_message`.
- Neither lookup matches → unchanged child route.
- Silent child → unchanged no-enqueue result.
- In-progress graph task → deduplicated result (`already_resuming`).
- Multi-JobItem-per-instance (W4 case 3): the active-orphan route uses exact-ID resolution, not freshest-`created_at`, so the right JobItem is finalized.

Assert calls, IDs, result status, and absence/presence of queue artifacts. Route logs supplement but do not replace assertions.

### 4. Finalization Race Test

Use a real JobItem and lock with an observer wired to a zero-pending dependency bus. Trigger resume-finalize and lifecycle-event finalize concurrently or in both deterministic orderings. Assert one atomic terminal transition, final admission release, and no exception/no state regression from the second caller.

**Revision 2 (W2):** Additionally seed a multi-JobItem-per-instance fixture (two `active` JobItems for the same instance, distinct `work_id`s). The active-orphan path threads `work_id` to `_get_processing_job_for_instance` via the F13 exact-ID overload; assert the right JobItem is finalized and the other is left untouched.

### 5. E2E Regression — `test_pause_during_report_turn_then_resume`

The test must explicitly prove these stages:

1. Submit a leader workflow that spawns a child and later causes a report turn.
2. Wait until the original leader `PROCESS_MESSAGE` Task is terminal and the leader is/has been `WAITING_CHILDREN`.
3. Detect a `PROCESS_REPORT` Task in-flight, then cause `ask_questions` to pause within that report-driven graph turn.
4. Assert the original message JobItem remains active and is correlated to the terminal message Task via `work_id == job_id`.
5. Submit the answer/resume.
6. Assert the root route is used and no fresh orphan `cascade_resume` message Task is created.
7. Assert workflow reaches terminal success, JobItem leaves active admission, lock/slot is released, and no answer Task remains pending/blocked.
8. Assert no duplicate assistant turn or internal bus-message leak.
9. **Revision 2:** Confirm the active JobItem was finalized via the F13 exact-ID overload (verifiable via DB state — only the JobItem matching `work_id == job_id` transitioned, not any sibling active JobItem).

If deterministic tool timing cannot be guaranteed through the existing test agent, add a test-only synchronization hook/fixture at the report-turn boundary rather than relying on sleeps alone.

### 6. Regression Commands / Suites

Run at minimum:

- New focused guard/routing tests in `tests/test_message_job_serialization.py` and `tests/unit/test_pause_resume_root.py`
- `tests/test_report_lane_phase2.py`
- `tests/unit/test_cascade_pause_resume.py`
- `tests/integration/test_cold_resume_ttl.py`
- Relevant finalize race suite (`tests/test_finalize_job_threading.py` and observer tests)
- `tests/e2e/test_e2e_workflows.py::test_pause_after_spawn_then_resume`
- `tests/e2e/test_e2e_workflows.py::test_pause_during_report_turn_then_resume`

Use PostgreSQL as the authoritative result. SQLite passing is required for dual-driver repository support but is not a substitute for PostgreSQL validation.

## Files Touched

### Production (expected)

- `daemon/repositories/task/repository.py`
  - **Shared terminal-orphan SQL predicate correlated via `task.work_id = job_queue_items.job_id` (NOT via `task.message_id = json_extract(metadata, 'message_id')`).** Removes the `_orphan_json_extract` bind (no longer needed).
  - Broadened claim guard exclusion using the `work_id`-keyed predicate.
  - Mirrored busy-instance predicate/binds — **bind dict expanded to include `status_paused`** (current bind set at `:1506-1519` is incomplete; the claim path at `:778` already binds it).
  - New report-turn active-JobItem resume routing primitive correlated by `job_id == work_id` (no JSON extraction).
- `daemon/manager.py`
  - Fallback active-orphan lookup and root-route selection in `resume_processing_job`.
  - Route-reason logging and docstring updates.
- `daemon/services/job_feedback_observer.py`
  - **B5 / W2 fix:** `_process_resume_finalize` (`:1945`) must pass `job_id=work_id` to `_get_processing_job_for_instance` to use the F13 exact-ID overload. The helper at `:632-712` already accepts the `job_id` parameter; this is a one-line threading change that improves finalize safety for ALL callers, not just the active-orphan path. Update the docstring at `:1900-1920` to reflect the new behavior (no longer "for logging/fallback only" — the `job_id` is now authoritative when provided).

### Tests (expected)

- `tests/test_message_job_serialization.py`
  - Active/queued × backing-state guard and busy-probe matrix.
  - **Revision 2:** Retry-scenario regression (W4 case 1) — parent CANCELLED + retry child PENDING with same `message_id`, distinct `work_id`s.
  - **Revision 2:** Multi-JobItem-per-instance regression (W4 case 3).
- `tests/test_report_lane_phase2.py`
  - Preserve report bypass and pause/per-instance behavior; add regression assertion only if not covered by the serialization matrix.
- `tests/unit/test_pause_resume_root.py`
  - Routing primitive and manager root-vs-child tests.
  - **Revision 2:** Silent cascade-resume interaction (W4 case 4) — fallback returns `None` for ordinary children.
  - **Revision 2:** `_graph_tasks` dedup test (W4 case 5).
- `tests/unit/test_cascade_pause_resume.py`
  - Update/check expectations for fallback routing without changing normal cascade semantics.
- `tests/test_finalize_job_threading.py` and/or observer-specific test module
  - Double-finalize race and admission release.
  - **Revision 2:** Multi-JobItem-per-instance finalize test — confirms the F13 exact-ID overload (W2) picks the right JobItem.
- `tests/e2e/test_e2e_workflows.py`
  - New `test_pause_during_report_turn_then_resume`; retain `test_pause_after_spawn_then_resume`.
  - **Revision 2:** Add the exact-ID finalization assertion (step 9 of §5 above).
- `tests/integration/test_cold_resume_ttl.py`
  - Usually no code change; run as mandatory regression and update only if the new route requires an explicit expectation.

## Exit Criterion

Phase 1 is complete only when Step A has been validated/released before Step B, both root causes are covered by tests, the fresh answer Task is admissible under active+terminal-backed orphan state but blocked under active+running/paused state, the report-turn pause routes through the root checkpoint path, the correlated JobItem is explicitly finalized (via the F13 exact-ID overload, not freshest-`created_at`) and its slot released, the retry-scenario regression (parent CANCELLED + retry child PENDING with same `message_id`) passes deterministically on both DB drivers, and the new E2E passes 10 consecutive PostgreSQL runs alongside the existing pause/resume and report-lane suites.

## Assumptions and Open Questions

1. **Assumption:** The active JobItem's `job_id` remains the authoritative correlation to the terminal `PROCESS_MESSAGE` Task's `work_id` in this incident state. Verified at `repository.py:640-645` (existing Part 2 guard uses `WHERE _qi.job_id = task.work_id`) and `instance_messaging.py:1218-1222` (the linkage contract: `JobItem.job_id MUST equal Task.work_id`). Confirm against production incident rows before implementation.
2. **Assumption:** The existing root checkpoint remains resumable after the original message Task becomes terminal while a report-driven graph turn pauses. The E2E must prove this; if checkpoint ownership is report-turn-specific, the primitive/consumer contract may need to return more context than a Task.
3. **Open question:** Should terminal statuses for orphan detection be expressed positively (`COMPLETED/CANCELLED/FAILED`) or negatively as absence of `PENDING/RUNNING/PAUSED`? The proposed negative live-set is preferred because it covers terminal states uniformly, but reviewers should confirm no future non-terminal status can be introduced without joining the live set.
4. **Open question (resolved by W4 case 3):** Does `_get_processing_job_for_instance` inside `_process_resume_finalize` always resolve the same active JobItem when multiple historical rows exist? **Resolution (W2):** No — the current lookup uses freshest-`created_at` ordering. The fix threads `work_id` through to use the F13 exact-ID overload. The multi-JobItem-per-instance test is now part of the test matrix.
5. **Open question:** What deterministic hook best pauses specifically inside a report-driven `ask_questions` turn? Prefer an existing test agent/tool synchronization mechanism; avoid arbitrary sleep-based timing.
6. **Stop condition:** If Step B requires a new column, changes JobItem identity semantics, or cannot preserve old child routing, stop implementation and request a follow-up design review rather than expanding this phase silently.
7. **Open question (Revision 2, W2 side-effect):** Does any current caller of `_process_resume_finalize` rely on the freshest-`created_at` ordering for JobItem resolution? If so, threading `job_id=work_id` through would change their behavior. Survey all callers before merging B5; if any depend on the legacy ordering, either exclude them from the change or flag the behavior change explicitly in the PR description.
