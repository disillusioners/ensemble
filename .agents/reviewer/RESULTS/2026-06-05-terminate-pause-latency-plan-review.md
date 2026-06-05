# Plan Review: Terminate/Pause Latency (docs/plans/terminate-pause-latency.md)

| Field | Value |
|---|---|
| **Reviewed** | 2026-06-05 |
| **Reviewer** | reviewer agent (Deep-Review, council mode) |
| **Target** | `docs/plans/terminate-pause-latency.md` (393 lines, 4 fixes) |
| **Verdict** | 🔴 **NEEDS-REVISION** (3 critical blockers before merge) |
| **Triggers** | Data Integrity, Complex Concurrency, Business-Critical, Cross-Cutting, Architecture Change |

---

## Verdict: 🔴 NEEDS-REVISION

The plan correctly diagnoses the symptoms (RC1–RC4) and the proposed fixes are architecturally sensible, but **three critical issues must be resolved before merging**:

1. **Fix 2 cannot be applied to the pause path** — `_pause_single` is a sync function; the proposed `await asyncio.wait_for(...)` will not compile.
2. **Fix 2 pauses regress badly** on trees with ≥5 children (5 s × N worst case), contradicting goal G1.
3. **Fix 1's premise (Q1) is unverified** — the code trace shows step 7.6 *should* already work; the 27 s delay may have a different root cause, meaning Fix 1 may be papering over a deeper bug.

Additionally, **Q2 is actually resolved** (idempotency exists via exception swallow), **Fix 3 duplicates existing functionality** (`notify_all`), and **Fix 4's repository-based child lookup is a no-op refactor** (both `meta.children` and `list_by_parent` query the same `InstanceHierarchy` table).

---

## Scope Reviewed

- `docs/plans/terminate-pause-latency.md` — full plan (sections 1–10, 393 lines)
- Cross-referenced against actual code:
  - `daemon/services/instance_lifecycle.py:418-569, 600-680`
  - `daemon/services/message_job_handler.py:220-292`
  - `daemon/services/job_queue_service.py:1140-1290`
  - `daemon/services/dispatch_event_bus.py:1-125`
  - `daemon/services/job_processor.py:177-336`
  - `daemon/services/job_state_machine.py:20-35`
  - `daemon/repositories/job_queue/repository.py:398-612`
  - `daemon/repositories/instance/repository.py:52-65, 139-143, 329-338`

---

## Session Used

- `ens review-deep` (council mode, single session)

---

## Findings

### 🔴 Critical

#### C1 — Fix 2 cannot be applied to the pause path (`_pause_single` is sync)
- **Files:** `daemon/services/instance_lifecycle.py:603` (signature), plan §4 Fix 2 lines 145–148
- **Issue:** The plan says "Apply identically in both places" (terminate at lines 438–442 AND pause at lines 629–631). However, the pause path's target function is `_pause_single`:
  ```python
  def _pause_single(target_id: str, prefetched_meta: Instance | None = None) -> bool:  # NOT async
  ```
  The proposed `await asyncio.wait_for(asyncio.shield(graph_task), timeout=5.0)` will fail to compile/run inside a sync function.
- **Fix:** Either (a) make `_pause_single` async (cascading change to caller at line 663–675, which is already async — straightforward), or (b) explicitly state Fix 2 is terminate-only and propose a separate, smaller change for pause. **The plan must pick one before review can proceed.**

#### C2 — Fix 2 pause-path regression: 5 s × N worst-case for trees with many children
- **Files:** `daemon/services/instance_lifecycle.py:662-675`, plan §3 G1, §9 post-fix timeline
- **Issue:** Goal G1 is "compress pause → job-row-cancelled from 30 s to < 5 s". But `pause_instance_cascade` iterates `tree_ids` and calls `_pause_single` (and the post-call `stream_status_change` await) **sequentially**. If Fix 2 makes each iteration await up to 5 s on `graph_task`, worst case is **5 s × N** for a tree of N instances. For a tree with 5+ children (common — `max_children_per_instance` is configurable), pause latency could **exceed the original 30 s**. The plan never acknowledges this; §9's "post-fix timeline" implicitly assumes a single instance.
- **Fix:** Either (a) apply Fix 2 in pause only to the **root** graph_task (children typically don't have in-flight LLM calls during pause cascade — verify), (b) bound the per-child wait to ~500 ms in pause (different from terminate's 5 s), or (c) parallelize the per-child waits with `asyncio.gather`. **The plan must pick one and update §9's timeline.**

#### C3 — Q1 unresolved: it is unclear why step 7.6 doesn't already handle MESSAGE jobs, and Fix 1's premise depends on it
- **Files:** `daemon/services/instance_lifecycle.py:505-549`, `daemon/services/job_processor.py:279-290`, plan §2 RC1, §7 Q1
- **Issue:** The plan states RC1 is "MESSAGE job `processing → cancelled` is never written in the terminate path", then acknowledges step 7.6 (lines 516–549) **does** call `complete_job(..., CANCELLED)` for processing jobs of any type. The plan flags this as Open Question Q1 but Fix 1 is built on the assumption that 7.6 is unreliable.
- **Trace of actual code:**
  1. 7.5 runs first (`cancel_message_job` → token signal only; row stays `processing`).
  2. 7.6 runs immediately after in the same coroutine: `find_jobs_by_instance(job_type=None)` returns PENDING+PROCESSING+FAILED rows (`repository.py:312-330`), so the MESSAGE job IS in scope.
  3. 7.6 checks `if remaining_job.status in ("completed", "cancelled", "dead_letter"): continue` at `instance_lifecycle.py:525-526`. The MESSAGE job is `processing`, so it does NOT skip.
  4. 7.6 calls `complete_job(remaining_job.job_id, demand_state=DemandState.CANCELLED, ...)` at line 536–540. This calls `terminate_job` → `atomic_transition(from=PROCESSING, to=CANCELLED)`. **This should succeed.**
- **The only way 7.6 wouldn't land the transition is if:**
  - The handler coroutine has already run between 7.5 and 7.6 and transitioned the row (then 7.6 skips at line 526) — **but that would mean RC1 doesn't exist.**
  - `complete_job` at 7.6 throws and is swallowed by the per-iteration `except Exception` at line 544–547 (logging-only).
  - `find_jobs_by_instance` doesn't return the MESSAGE job (e.g., row's `instance_id` is NULL — unlikely).
- **The plan's timeline shows the transition only fires 27 s later via `job_processor.py:281-289`** — that code path is the 30 s poll sweep, which fires `complete_job(CANCELLED)` only when it observes `instance.status == TERMINATED`. **This strongly suggests 7.6 either didn't run, ran but threw, or the MESSAGE job's `instance_id` column was mis-set.**
- **This is a blocker.** Without reproducing Q1, Fix 1 may be adding a duplicate write that adds complexity without solving the bug. The actual root cause might be something else entirely.
- **Fix:** Before merging Fix 1, instrument both 7.5 and 7.6 with `[TRACE]` logs showing job_id, status, and any exception text. Reproduce the 27 s delay. If 7.6 is reaching the `complete_job` call and the call is succeeding, Fix 1 is unnecessary and the plan should be restructured. If 7.6 is throwing, fix the underlying bug — don't paper over it with a duplicate write in 7.5.

---

### 🟡 Warnings

#### W1 — Q2 is RESOLVED: `complete_job` is idempotent via exception swallow (plan incorrectly lists Q2 as open)
- **Files:** `daemon/services/job_queue_service.py:1181-1183` (async), `:1265-1267` (sync), plan §7 Q2
- **Issue:** Q2 asks "Is `complete_job` / `terminate_job` idempotent against a job already in a terminal state? Required for Fix 1's safety." **It is already.** The code already does what the plan asks for:
  ```python
  # job_queue_service.py:1181-1183
  except (ValueError, InvalidTransitionError) as e:
      # Job state already changed — still need to release lock below
      logger.debug("Job %s already transitioned, skip: %s", job_id[:8], e)
  finally:
      # ... still releases the lock
  ```
- `atomic_transition` at `repository.py:434-439` raises `InvalidTransitionError` when `job.status != from_status`. The state machine at `job_state_machine.py:20-32` has **no transitions out of terminal states** (no `CANCELLED→CANCELLED`, no `COMPLETED→CANCELLED`). So the second writer's `terminate_job(from=PROCESSING, to=CANCELLED)` raises `InvalidTransitionError`, which is swallowed.
- Additionally, `_release_job_lock` at `job_queue_service.py:1187-1192` always runs in `finally`, and `release_by_job` at `lock_repository.py:41-54` is idempotent (returns False if no row found). **Lock double-release is safe.**
- **Fix:** Update the plan to mark Q2 resolved. Remove the "Action required" block at plan-lines 130–131 and the optional feature flag at plan-line 302 (unnecessary).

#### W2 — Fix 1 race with handler's normal COMPLETED path: error message is misleading
- **Files:** `daemon/services/job_queue_service.py:1174-1180` (CANCELLED path), `:1149-1155` (COMPLETED path), plan §4 Fix 1 line 117
- **Issue:** If the handler completes NORMALLY (`PROCESSING→COMPLETED` via `_repository.complete_job` at line 1151-1153) just before Fix 1 fires CANCELLED:
  1. Handler's `complete_job(COMPLETED)` succeeds: `atomic_transition(PROCESSING→COMPLETED)`.
  2. Fix 1's `complete_job(CANCELLED)` calls `terminate_job` → `atomic_transition(from=PROCESSING, to=CANCELLED)`. But the row is now COMPLETED. `job.status != from_status` raises `InvalidTransitionError`. Swallowed at line 1181–1183.
  3. Final row state: COMPLETED. **Correct outcome.**
- However, Fix 1's caller logs `error="Instance terminated during message processing"` (plan-line 117). If a future refactor moves the error message into the swallow log, it will be misleading ("terminated" when actually completed).
- **Fix:** Change Fix 1's error message to be conditional, or just use `"Cancelled by terminate cascade"` (factual, not presumptive).

#### W3 — Fix 3 is redundant with `notify_all` and the new method signature is misleading
- **Files:** `daemon/services/dispatch_event_bus.py:108-125` (`notify_all`), plan §4 Fix 3 lines 196–220
- **Issue:** `notify_terminated(instance_id)` as proposed does exactly what `notify_all()` already does: sets `self._global_event` and all project events. The `instance_id` parameter is **not used** in the proposed body — it's purely cosmetic.
- **Fix:** Either (a) drop Fix 3 entirely and call `self._dispatch_bus.notify_all()` from `terminate_instance` (one-line change, no new method), or (b) if the team wants a named entry-point for self-documenting code, make `notify_terminated` literally delegate to `notify_all` with a thin wrapper that logs the instance_id. Don't reinvent the loop body. Update plan §4.3 to reflect this.

#### W4 — Fix 4's RC4 hypothesis is unsupported: `meta.children` and `list_by_parent` read from the same source
- **Files:** `daemon/repositories/instance/repository.py:52-57` (`_load_children`), `:59-65` (`_enrich_instance`), `:139-143` (`get`), `:329-338` (`list_by_parent`), plan §2 RC4, §4 Fix 4
- **Issue:** The plan's RC4 theorizes "`meta.children` was already empty (possibly mutated by the pause that happened milliseconds earlier, or never populated for this parent/child pair)". But:
  - `meta = self._manager._instance_repository.get(instance_id)` at `instance_lifecycle.py:422` calls `get()` at `repository.py:139-143`, which calls `_enrich_instance` at line 143.
  - `_enrich_instance` at line 59–65 sets `instance.children = self._load_children(...)`.
  - `_load_children` at line 52–57 queries `select(InstanceHierarchy).where(parent_id == instance_id)`.
  - `list_by_parent` at line 329–338 queries the **same `InstanceHierarchy` table** with the **same predicate**.
- So `meta.children` IS `list_by_parent` — they return the same set on the same DB state. The "stale" or "mutated" hypothesis is **not supported by the code**. The `children: str = Field(default="[]")` column at `models.py:63` IS denormalized but is **never read** on the read path — `_enrich_instance` overwrites it on every `get()`.
- **The 4 s gap (RC4) is more likely from a different code path emitting the "Cascading terminate" log** (e.g., `manager.py:1059` or `routers/projects.py:818`), as the plan itself suggests. Fix 4's `trigger=DELETE` log tag will help diagnose this — but switching `meta.children` to `list_by_parent` won't fix anything because they're equivalent.
- **Fix:** Keep the `trigger=DELETE` log tag (high value, low cost). Drop or de-scope the repository-based child lookup — it's a no-op refactor. If the team wants belt-and-suspenders, leave both lookups in (as a sanity check) and log when they disagree.

#### W5 — Cascade `terminate_instance(child_id)` is sequential, not parallel (terminate latency regression)
- **Files:** `daemon/services/instance_lifecycle.py:430-433`, plan §4 Fix 4
- **Issue:** The cascade loop is:
  ```python
  if meta and meta.children:
      for child_id in list(meta.children):
          ...
          await self.terminate_instance(child_id)
  ```
  Each child termination is awaited sequentially. With Fix 2's 5 s bounded-await per graph_task, terminating a parent with N children = `5 s × N` worst case. Plan's §9 timeline implicitly assumes a single child.
- **Fix:** Use `await asyncio.gather(*[self.terminate_instance(child_id) for child_id in children], return_exceptions=True)`. Add this as a Fix 4 sub-item or note in §5.

#### W6 — Fix 3 ordering claim is too strong (early wakeup is benign)
- **Files:** `daemon/services/job_processor.py:177-336`, plan §4 Fix 3 lines 222–234
- **Issue:** The plan says "wakeup must happen after … DB status update. Otherwise the JobProcessor may wake, sweep, and find the instance still in a non-terminal state."
- **Trace of what happens if wakeup fires BEFORE the DB write at `instance_lifecycle.py:473`:**
  - JobProcessor wakes, calls `_process_next_job`.
  - For each queue, lists pending jobs. The terminating instance's MESSAGE job is still PROCESSING (not PENDING), so it's not in `list_pending_by_queue` results.
  - The orphan check at `job_processor.py:242-336` looks at PROCESSING jobs, fetches `instance.status`, sees it's still RUNNING (not TERMINATED yet), and continues (line 304–305: "Instance is alive and processing — not orphaned").
  - 30 s later, the polling loop runs again. By now status is TERMINATED. The orphan check completes the job.
- So early wakeup is **not unsafe** — it just doesn't help. The plan's "must happen after" is overly strong.
- **Fix:** Soften the language from "must" to "should, for effectiveness". The wakeup is still safe even if it fires slightly early.

#### W7 — Fix 1's PENDING branch silently duplicates step 7.6's PENDING handling
- **Files:** `daemon/services/message_job_handler.py:277-279`, `daemon/services/job_queue_service.py:541-543`, plan §4 Fix 1 lines 106–108
- **Issue:** Fix 1's plan-line 106–108 says for `pending` MESSAGE jobs: `await self._job_queue_service.cancel_message_job(msg_job.job_id)`. That calls `cancel_message_job` at `message_job_handler.py:267-292`, which for `pending` calls `self._job_repo.cancel_job` (line 279) — which is `atomic_transition(PENDING→CANCELLED)` at `repository.py:580-586`. ✓
- Then step 7.6 runs and tries to `cancel_job` for the same row — which is now CANCELLED. `cancel_job` at `job_queue_service.py:435-530` checks `can_transition(CANCELLED, CANCELLED)` at line 456–457 — returns False. **No exception, just returns False.** Benign but duplicated work.
- **Fix:** Add a comment to Fix 1's snippet noting that 7.6 will see the row as terminal and skip. Or, better: skip the `pending` branch in Fix 1 entirely and let 7.6 handle PENDING (it already does at line 543 via `cancel_job`). Only Fix 1's `processing` branch is actually needed.

---

### 🟢 Suggestions

#### S1 — Fix 1 may be largely unnecessary if Q1 resolves to "7.6 already works"
If Q1's investigation reveals that 7.6 does successfully transition the MESSAGE job, then Fix 1's only value is **microsecond-level latency** (7.5 fires slightly earlier than 7.6 in the same coroutine). If 7.6 is buggy, fix 7.6 directly rather than duplicating its logic in 7.5.

#### S2 — Parallelize child termination in Fix 4 (or as a sub-item)
For the terminate path, the cascade at `instance_lifecycle.py:430-433` is sequential. With Fix 2, each child's graph_task unwind adds up to 5 s. If `terminate_instance` is made to parallelize children (Fix 4 sub-item), the total terminate latency becomes `max(5 s)` instead of `sum(5 s)`.

#### S3 — Add a pause-cascade latency regression test
The plan's test plan (§4) doesn't include a pause-cascade latency test. Given C1/C2 above, this is a gap. Add a test that runs `pause_instance_cascade` against a tree of N≥5 mocks and asserts total time < some target.

#### S4 — Add `cascade_count` and `pause_children_count` to observability logs
Currently the proposed log has `children={len(children)}` but no pause-specific fields. For diagnosing pause latency regressions (C2), add a similar log at the end of `pause_instance_cascade`.

#### S5 — Document `resume_processing_job` interaction explicitly
The plan does not consider the `resume_processing_job` path (`manager.py:2022-2109`). That path queries `find_processing_message_jobs_by_instance` and operates on PROCESSING rows. If pause+resume+terminate happen quickly, the resume path may find a row that Fix 1 just cancelled. The resume path filters by `find_processing_message_jobs_by_instance` which only returns PROCESSING. So if Fix 1 fires between resume's `find` and resume's `enqueue_message`, the resume sees an empty list and falls into the "child instance" branch (`manager.py:2064+`). This is likely benign but should be called out in the plan.

#### S6 — Verify `tests/integration/test_terminate_cascade.py` actually exists
The plan references this file in multiple test plans, but it does not appear to exist. The actual cascade test is `tests/test_instance_cascade.py` (different focus: FK cascade in repo layer). Plan needs to either rename or add new tests.

---

## Q1–Q6 Resolution

| Q | Resolved? | Answer | Blocker? |
|---|---|---|---|
| **Q1** — Why doesn't 7.6 catch MESSAGE jobs? | **NO** — needs reproduction | The trace shows 7.6 *should* work. The 27 s delay suggests it doesn't, but the root cause is unverified. Possible causes: an exception being swallowed at `instance_lifecycle.py:544-547`, or the MESSAGE job's `instance_id` column not matching. | **YES — must resolve before merging Fix 1.** See Critical Issue C3. |
| **Q2** — Is `complete_job` idempotent? | **YES** | Idempotent via `except (ValueError, InvalidTransitionError): logger.debug(...)` at `job_queue_service.py:1181-1183` and `:1265-1267`. Lock release in `finally` is also idempotent (`release_by_job` returns False if already released). | No. Update the plan to mark resolved. |
| **Q3** — Bundle Fix 2 changes for project-cleanup? | **Team decision** | The plan's own recommendation (split) is fine. The C1 issue (sync vs async) does not apply to `routers/projects.py:818` — that handler is async. | No. |
| **Q4** — Make 5 s configurable? | **YES** | Don't make it configurable for v1. But **do** make it different for pause vs terminate (pause should be shorter, see C2). | No. |
| **Q5** — Is `meta.children` still needed? | **YES — it's not a denormalized cache, it's a runtime field** | `meta.children` is populated by `_enrich_instance` from `InstanceHierarchy` on every `get()`. The `children` column at `models.py:63` IS denormalized but **never read on read path** — `_enrich_instance` overwrites it. Removing `meta.children` from the Instance model would be safe; removing the `children` column would require checking all readers. | No — out of scope. |
| **Q6** — Add regression integration test? | **YES** | Strongly recommended. The test should also cover the pause cascade (C2). | No. |

---

## Plan Checklist Validation (§10)

| # | Item | Addressed? | How |
|---|---|---|---|
| 1 | Has Q1 been investigated and resolved? | **NO** | This is the biggest gap. Plan marks Q1 as open but builds Fix 1 on the assumption that 7.6 is broken. Must reproduce before merge. See C3. |
| 2 | Has Q2 been verified or guard added? | **YES, but plan doesn't say so** | Idempotency exists via exception swallow at `job_queue_service.py:1181-1183`. Plan should update Q2 to "resolved". See W1. |
| 3 | Does Fix 1's `complete_job(CANCELLED)` interaction with the handler produce the right final state? | **YES** | The loser of the race swallows `InvalidTransitionError` cleanly. The handler's normal COMPLETED path is also safe — the CANCELLED attempt is swallowed. Lock release is idempotent. |
| 4 | Does Fix 2's `asyncio.shield` correctly protect against outer-cancel? | **PARTIALLY** | Yes for the terminate path. **Not assessed for pause** because the target function is sync (C1). The shield+cancel+wait_for semantics themselves are correct. |
| 5 | Does Fix 3's wakeup happen after Fix 1's DB write and the DB status update? | **YES by placement, but ordering claim is too strong** | Plan places the call at end of `terminate_instance`. Even if reordered earlier, the failure mode is benign — see W6. |
| 6 | Does Fix 4's `list_by_parent` return the same set as `meta.children`? | **YES — they are equivalent** | Both query `InstanceHierarchy` with `parent_id == instance_id`. The RC4 hypothesis is unsupported. See W4. |
| 7 | Are existing tests updated or still passing? | **NOT VERIFIED** | Plan references `tests/integration/test_terminate_cascade.py` — this file does not exist. The actual cascade test is `tests/test_instance_cascade.py` (different focus). Plan needs to either rename or add new tests. |
| 8 | Is the observability log consistent with daemon's logging style? | **PARTIALLY** | Existing code uses `[TRACE]` prefix liberally (e.g., `job_processor.py:140, 192`). Plan says "no `[TRACE]` prefix" — this contradicts observed code style. Pick one convention. |

---

## Additional Cross-Cutting Concerns

### Lock Contention (Fix 1 + handler's lock) — SAFE
The handler's `complete_job(COMPLETED)` releases the lock via `_release_job_lock` at `job_queue_service.py:1187-1192`. Fix 1's `complete_job(CANCELLED)` would also try to release — but `release_by_job` at `lock_repository.py:41-54` is idempotent (returns False if already released). No deadlock, no double-free. ✅

### Concurrent message enqueue TOCTOU — UNADDRESSED
The plan does not address this. Between `find_jobs_by_instance` at step 7.5/7.6 and the loop body, a new MESSAGE job could be enqueued. After Fix 1, that new job would be in PENDING, and step 7.6's `find_jobs_by_instance` would re-read and catch it. **The double-read at 7.5 and 7.6 partially mitigates this** but a job enqueued after 7.6's read but before terminate completes would survive. Acceptable in practice (terminate is fast), but the plan should call this out.

### `resume_processing_job` interaction — BENIGN BUT UNDOCUMENTED
`manager.py:2051-2054` uses `find_processing_message_jobs_by_instance` which only returns PROCESSING rows. If Fix 1 cancels the row between resume's check and resume's enqueue, resume sees an empty list and treats the instance as a child (`manager.py:2064+`). This is benign but the plan should document it.

---

## Recommendations

**Do not merge as-is.** Required actions before next review iteration:

### Must-fix (blocks merge)
1. **Resolve Q1 (C3):** Reproduce the 27 s delay locally with `[TRACE]` logging in steps 7.5 and 7.6. If 7.6 is succeeding, restructure Fix 1 (or drop it). If 7.6 is throwing, fix the underlying bug.
2. **Rework Fix 2's pause-path scope (C1 + C2):** Either make `_pause_single` async (and adjust cascade), or scope Fix 2 to terminate-only with a separate, smaller change for pause. Add latency budget analysis for N-child pause cascade.
3. **Verify Fix 3's necessity (W3):** Decide whether to drop Fix 3 in favor of `notify_all()`, or keep with a thin wrapper.

### Should-fix (before merge)
4. **Update Q2 to resolved** (W1); remove the now-unnecessary feature flag (plan-line 302).
5. **Reconsider Fix 4's repository-based lookup** (W4) — it's a no-op refactor since `meta.children` and `list_by_parent` query the same source. Keep the `trigger=DELETE` log tag (high value).
6. **Address W5 (sequential cascade)** — parallelize child termination with `asyncio.gather`, or accept the documented latency budget.
7. **Verify test file existence** (Checklist #7) — `tests/integration/test_terminate_cascade.py` doesn't appear to exist.

### Nice-to-have
8. Update error message in Fix 1 to be factual (W2).
9. Add pause-cascade latency regression test (S3).
10. Document `resume_processing_job` interaction (S5).

---

## Strengths of the Plan

For balance, several aspects are well-handled:
- **Root cause analysis (§2)** is thorough and identifies the right code paths.
- **Goals/non-goals (§3)** are clearly scoped.
- **Rollout ordering (§5)** is sound — separating commits by risk profile is correct.
- **Observability additions (§6)** are pragmatic and useful.
- **Open questions (§7)** demonstrate intellectual honesty.
- **Appendix timeline (§9)** is a great pattern for correlating logs to fixes.

The plan is a high-quality starting point — it just needs the three critical issues resolved before it can be implemented safely.
