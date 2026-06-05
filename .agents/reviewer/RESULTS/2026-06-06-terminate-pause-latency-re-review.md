# Plan Re-Review: Terminate/Pause Latency — Revision 2

| Field | Value |
|---|---|
| **Reviewed** | 2026-06-06 |
| **Reviewer** | reviewer agent (direct cross-reference) |
| **Target** | `docs/plans/terminate-pause-latency.md` (363 lines, Revision 2) |
| **Previous Review** | `.agents/reviewer/RESULTS/2026-06-05-terminate-pause-latency-plan-review.md` |
| **Verdict** | ✅ **APPROVE WITH MINOR FIXES** — all 3 criticals resolved, all 7 warnings addressed; 1 new warning found |

---

## Verdict: ✅ APPROVE WITH MINOR FIXES

Revision 2 is a **substantial improvement** over the original. The plan correctly restructured itself around the previous review's findings: Fix 1 is deferred to a proper investigation, the pause-path scope issue is eliminated by scoping Fix A to terminate-only, and the cascade is parallelized. All three critical blockers from Revision 1 are resolved.

One new 🟡 warning was found during cross-referencing (W-NEW-1: incorrect attribute path for `_dispatch_bus`). This is a snippet-level fix, not a design issue.

---

## Scope Reviewed

- `docs/plans/terminate-pause-latency.md` — full plan (Revision 2, 363 lines)
- Cross-referenced against actual source code:
  - `daemon/services/instance_lifecycle.py:75-115` (class init), `:404-569` (terminate_instance), `:571-677` (pause cascade + `_pause_single`)
  - `daemon/services/dispatch_event_bus.py:108-125` (`notify_all`)
  - `daemon/services/job_queue_service.py:1170-1284` (complete_job idempotency)
  - `daemon/services/job_processor.py:140-169` (process loop), `:275-294` (orphan check)
  - `daemon/repositories/job_queue/repository.py:312-330` (find_jobs_by_instance)
  - `daemon/api.py:200-260` (service wiring)
  - `daemon/manager.py:591, 1157-1163` (manager attributes)

---

## Previous Critical Issues — Resolution Status

### ✅ C1 RESOLVED — Fix 2 (now Fix A) correctly scoped to terminate-only

**Previous issue:** Plan applied `await asyncio.wait_for(...)` to `_pause_single`, which is a sync function.

**Resolution:** Plan §4.1 explicitly scopes Fix A to the terminate path only. The scope rationale (§4.1, lines 66-68) correctly identifies `_pause_single` as sync at `instance_lifecycle.py:603` and explains that making it async would introduce 5s × N regression. Pause-path graph-task unwind is deferred to a follow-up PR (NG1, Q5). The plan does not touch `_pause_single` at all.

**Source verification:** Confirmed — `_pause_single` at `instance_lifecycle.py:603` is `def _pause_single(...) -> bool:` (sync). Plan does not propose any changes to this function. ✅

---

### ✅ C2 RESOLVED — Cascade parallelized, pause-path not regressed

**Previous issue:** Sequential cascade × 5s timeout = latency regression for trees with N children.

**Resolution:** Plan §4.1 (lines 86-94) uses `asyncio.gather(..., return_exceptions=True)` for terminate-path child cascade. §9 post-fix timeline (line 349) explicitly states: "terminate latency is max(per-child unwind) rather than sum." The pause path is explicitly left unchanged ("pause latency is unchanged (Fix A doesn't apply)"). Test plan (line 135) includes a parallel-cascade timing test.

**Source verification:** Current cascade at `instance_lifecycle.py:430-433` is sequential (`for child_id in list(meta.children): ... await self.terminate_instance(child_id)`). The plan's replacement with `asyncio.gather` is correct for the async `terminate_instance` function. ✅

---

### ✅ C3 RESOLVED — Fix 1 deferred to investigation

**Previous issue:** Fix 1's premise (step 7.6 doesn't catch MESSAGE jobs) was unverified, and Fix 1 was built on the assumption that 7.6 is broken.

**Resolution:** Fix 1 is entirely removed from this PR. §6 provides a detailed investigation plan with TRACE instrumentation for steps 7.5 and 7.6, a diagnostic decision matrix (§6.3), and three possible outcomes (§6.4). §8 explicitly defers Fix 1 to a follow-up PR gated on the §6 investigation. §10 Review Checklist item 1 makes the investigation a prerequisite.

**Source verification:** Step 7.6 at `instance_lifecycle.py:516-549` uses `find_jobs_by_instance(instance_id, job_type=None)` which at `repository.py:312-330` returns ALL job types (PENDING, PROCESSING, FAILED) — confirming that the MESSAGE job IS in scope if 7.6 runs. The investigation is the correct approach. ✅

---

## Previous Warnings — Resolution Status

### ✅ W1 RESOLVED — Q2 marked resolved, feature flag removed

§7 Q2 (line 291) is marked **RESOLVED** with a detailed explanation citing `job_queue_service.py:1181-1183` (async) and `:1265-1267` (sync) for the exception swallow. §5 (line 207) explicitly says "No feature flag."

**Source verification:** Confirmed — `job_queue_service.py:1181-1183` catches `(ValueError, InvalidTransitionError)` and logs at debug level. Lock release in `finally` at `:1184-1194` is also idempotent. ✅

---

### ✅ W2 RESOLVED (by deferral) — Fix 1 error message

Fix 1 is deferred entirely to §6. W2's concern about the misleading error message is moot until Fix 1 lands. ✅

---

### ✅ W3 RESOLVED — `notify_terminated()` dropped, `notify_all()` reused directly

§4.3 (lines 171-194) is titled "reuse `DispatchEventBus.notify_all()` (no new method)". No `notify_terminated()` method is proposed. The plan calls `self._manager._dispatch_bus.notify_all()` directly.

**Source verification:** `notify_all()` at `dispatch_event_bus.py:108-125` sets `self._global_event` and all project events — exactly what the wakeup needs. ✅

⚠️ See W-NEW-1 below for the attribute path issue.

---

### ✅ W4 RESOLVED — Fix 4's repository lookup dropped

No Fix 4 exists in Revision 2. The plan retains `meta.children` from `_instance_repository.get()` (matching current code at `instance_lifecycle.py:420-422`). §7 Q7 flags the denormalized `children` column as follow-up. ✅

---

### ✅ W5 RESOLVED — Terminate cascade parallelized

Fix A (lines 86-94) uses `asyncio.gather` with `return_exceptions=True`. Test plan (line 135) includes timing assertion for parallel cascade. ✅

---

### ✅ W6 RESOLVED — Ordering claim softened

§4.3's code comment (line 183) explicitly states: "Safe to call even if the DB writes haven't fully settled — early wakeup is benign (JobProcessor's orphan-check... will just see RUNNING and skip, then catch TERMINATED on its next pass)." No "must" language. ✅

---

### ✅ W7 RESOLVED (by deferral) — Fix 1 PENDING branch

Fix 1 is deferred entirely. No PENDING branch exists in Revision 2. ✅

---

## Previous Suggestions — Resolution Status

| # | Suggestion | Status |
|---|---|---|
| S1 | Fix 1 may be unnecessary if Q1 resolves | ✅ Addressed — §6 investigation is exactly this |
| S2 | Parallelize child termination | ✅ Implemented — Fix A uses `asyncio.gather` |
| S3 | Pause-cascade latency regression test | ✅ Acknowledged — deferred to follow-up PR (Q5), explicitly noted as non-goal NG1 |
| S4 | Add pause-specific observability logs | ✅ Acknowledged — deferred to follow-up PR |
| S5 | Document `resume_processing_job` interaction | ✅ Addressed — §7 Q8 documents this interaction explicitly |
| S6 | Verify test file existence | ✅ Addressed — §7 Q9 acknowledges the file doesn't exist and recommends new locations |

---

## New Finding

### 🟡 W-NEW-1 — Wakeup code uses incorrect attribute path for `_dispatch_bus`

- **Plan:** §4.3, lines 184-185
- **Code snippet:**
  ```python
  if hasattr(self._manager, '_dispatch_bus') and self._manager._dispatch_bus is not None:
      self._manager._dispatch_bus.notify_all()
  ```
- **Issue:** `self._manager` is an `InstanceManager` (`instance_lifecycle.py:88, :101`). The `InstanceManager` does **not** have a `_dispatch_bus` attribute. The dispatch bus is wired onto:
  1. `job_queue_mgmt_service._dispatch_bus` (`api.py:210`)
  2. `job_queue_service` via `set_dispatch_bus()` (`api.py:225`)
  
  Neither of these is `self._manager._dispatch_bus`. The `hasattr` guard prevents a crash, but it means `notify_all()` will **never fire** — the wakeup code is unreachable.

- **Fix:** Use the correct attribute path. The simplest option:
  ```python
  if hasattr(self._manager, '_job_queue_mgmt_service') and self._manager._job_queue_mgmt_service is not None:
      if hasattr(self._manager._job_queue_mgmt_service, '_dispatch_bus'):
          self._manager._job_queue_mgmt_service._dispatch_bus.notify_all()
  ```
  
  Alternatively, add a `dispatch_bus` property or setter to `InstanceManager` during wiring in `api.py`. Or access via `self._job_queue_service` if that service stores the dispatch bus reference.

- **Severity:** 🟡 Warning (the code won't crash due to `hasattr`, but the RC2 fix won't actually work — JobProcessor will continue to rely on the 30s poll timeout for terminate-induced wakeups).

---

## Checklist Validation (§10 of plan)

| # | Item | Status | Notes |
|---|---|---|---|
| 1 | Q1 investigation completed before Fix 1 | ✅ Correctly deferred | §6 provides thorough investigation plan; Fix 1 is gated on it |
| 2 | Fix A's `await` on sync context | ✅ Correct | `terminate_instance` is `async def` (line 404). `_pause_single` is not touched. |
| 3 | `asyncio.shield` correctness | ✅ Correct | Protects inner task from outer-cancel. Plan's explanation (line 124) is accurate. |
| 4 | `asyncio.gather(..., return_exceptions=True)` correctness | ✅ Correct | Aggregates exceptions, doesn't propagate. Reviewer checklist item 5 is right. |
| 5 | Pause-path latency budget acceptable | ✅ Documented | Not regressed — Fix A doesn't apply. §9 acknowledges this. |
| 6 | Existing tests in `test_instance_cascade.py` still passing | ✅ Acknowledged | Plan correctly references this file (line 136) |
| 7 | New test files in `tests/integration/` per Q9 | ✅ Acknowledged | Q9 recommends `tests/services/test_instance_lifecycle_terminate.py` |
| 8 | `[TRACE]` prefix matches daemon style | ✅ Correct | References `job_processor.py:140` and `instance_lifecycle.py:555` |
| 9 | `notify_all()` reuse appropriate | ✅ Correct (design) / ⚠️ Implementation path wrong | See W-NEW-1 |

---

## Cross-Cutting Concerns

### asyncio.shield + wait_for semantics — ✅ CORRECT
The plan's `await asyncio.wait_for(asyncio.shield(graph_task), timeout=5.0)` is the correct pattern:
- `graph_task.cancel()` is called first (line 107)
- `asyncio.shield` prevents the inner task from being cancelled if the outer coroutine is cancelled
- `asyncio.wait_for` raises `TimeoutError` after 5s
- Both `TimeoutError` and `CancelledError` are caught (lines 114-120)
- If timeout fires, the graph task continues unwinding in the background (safe — it's been popped from `_graph_tasks` at line 105)

### Parallel cascade error handling — ✅ ACCEPTABLE
`asyncio.gather(..., return_exceptions=True)` captures child termination exceptions without propagating. Since `terminate_instance` wraps most operations in try/except and returns True/False, exceptions are unlikely. The plan's post-gather log loop (lines 95-99) logs all children uniformly, which could mask a child failure — but this is the same behavior as the current sequential code (which would just proceed to the next child after a logged exception in the inner try/except).

### Cascade log placement change — 🟢 MINOR OBSERVATION
The plan moves the "Cascading terminate to child" log to AFTER the gather (line 95-99), whereas the current code logs BEFORE each child termination (line 432). The new placement means the log reflects completion, not initiation. This is actually better for observability (you know the child was processed), but the log message "Cascading terminate to child" reads as initiation. Consider changing to "Cascade complete for child" or adding a pre-gather "Cascading to N children (trigger=DELETE)" log.

---

## Summary

| Category | Count | Details |
|---|---|---|
| 🔴 Critical (new) | 0 | — |
| 🟡 Warnings (new) | 1 | W-NEW-1: wrong attribute path for `_dispatch_bus` |
| 🟢 Suggestions (new) | 1 | Cascade log message semantics |
| ✅ Previous Critical resolved | 3/3 | C1, C2, C3 all resolved |
| ✅ Previous Warnings resolved | 7/7 | W1–W7 all resolved or moot by deferral |
| ✅ Previous Suggestions addressed | 6/6 | S1–S6 all acknowledged or implemented |

---

## Recommendations

**Approve for implementation** with the following minor fix required:

1. **[Must]** Fix W-NEW-1: Correct the `_dispatch_bus` attribute path in §4.3's code snippet. Verify the correct path by checking which service object holds the reference at runtime.

**Nice-to-have:**
2. Clarify the cascade log message to reflect post-completion semantics (line 95-99).
3. Add a `t0 = time.monotonic()` placement note in Fix B.2's implementation guidance (the summary log references `t0` and `graph_unwind_ms` but doesn't show where they're initialized).

---

## Strengths of Revision 2

The revision demonstrates excellent responsiveness to feedback:
- **Fix 1 deferral** is the right call — investigating root cause before adding a fix is sound engineering
- **Scope rationale** (§4.1 lines 66-68) is precise and correctly traces the sync/async boundary
- **Investigation plan** (§6) is thorough with a decision matrix that maps diagnostic outcomes to resolutions
- **No feature flag** (§5) is correct — the changes are small, testable, and revertible
- **Post-fix timeline** (§9) now correctly models parallel cascade and acknowledges the MESSAGE job delay is deferred
- **Open questions** (§7) are well-scoped and demonstrate intellectual honesty, especially Q8 (resume interaction) and Q9 (test file path)
