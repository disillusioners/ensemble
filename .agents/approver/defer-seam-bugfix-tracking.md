# Tracking: Defer Queue + Job/Task Seam Bugfix

**Plan:** Defer Queue + Job/Task Seam Bugfix
**Files:** `.agents/shared/planning/defer-seam-bugfix/`
**Bug Document:** `docs/bugs/defer-queue-and-job-task-seam-bugs.md`

---

## Iteration 001 — APPROVED

**Date:** 2026-06-30 16:50 UTC
**Verdict:** APPROVED

### Evaluation Method
- Read all 6 plan files + full bug document (438 lines)
- Verified 10+ key code claims against actual source code via grep/read
- Spawned council session (2 councillors) to independently verify 5 deepest technical claims

### Council Verification Results (5/5 VERIFIED)
1. Phase 1 Tasks 6+7+9 atomicity — CORRECT (is_deferred must be wired for predicate to function)
2. AsyncMessageResult carries message_id — CORRECT (post-enqueue stamp feasible)
3. _finalize_terminal lock-release scope — CORRECT (canonical_project_id/queue_id available before finally)
4. F6 watcher migration exact-match — CORRECT (get_watchers_for_job uses strict equality)
5. WorkRecord lacks message_id field — CORRECT (needs adding for F1 dedup)

### Coverage Check
- 17/19 bugs assigned to 3 phases with correct dependencies
- F9 (PG-only re-arm trigger) + F16 (legacy fallback) deferred with documented rationale
- All 4 fix categories (A, B, C, D) map correctly to bug document recommendations

### Notes (non-blocking)
- Phase 3 reconciler registration site has some ambiguity ("find where StaleTaskRecovery is scheduled") — implementer should resolve during implementation
- Phase 2 Task 11 (`paused` → `active` ambiguity) acknowledges uncertainty about pause state representation — implementer should verify during implementation
