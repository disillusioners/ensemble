# Injection Bundle Fixture Drift + Coverage Rot (found 2026-08-25, pre-deploy @ 84fd8018)

## Symptom
4 deterministic failures in the `injection_unit_test` bundle (tests/test_injection_slot.py ×3, tests/test_injection_cleanup.py ×1), all `AttributeError: '_ManagerStub' object has no attribute '_deferred_watchover_terminate'` at daemon/manager.py:3488 (`_cleanup_instance_state`).

## Root Cause
`12378edb` (2026-08-06, feat(watchover)) added `self._deferred_watchover_terminate` to InstanceManager plus `.discard()` calls in `_cleanup_instance_state`, but never updated the `_ManagerStub` fixtures in the two injection test files (last touched 2026-07-22 / 2026-07-13). The stubs DO have the older companion attribute `_deferred_question_pause` — the new one was simply missed.

## Why it survived 20 days (coverage rot)
The `injection_unit_test` bundle had not been run since before 2026-08-06. 40 manager.py commits landed in between. Registered file-list packs rot silently if not scheduled; no gate covers "manager attribute added → stub companions updated".

## Evidence (attribution discipline)
- `git log -S "_deferred_watchover_terminate.discard" -- daemon/manager.py` → introducer 12378edb, ancestor of HEAD.
- Commit under validation (84fd8018) touched only graph.py + new test — not manager.py.
- Base re-run at parent f5e4b79a (worktree): identical `4 failed, 21 passed`, verbatim same 4 IDs + exception.

## Fix (2 lines, test-code only — not applied this session, validation-only mandate)
Add `_deferred_watchover_terminate: set[str] = set()` to `_ManagerStub` in tests/test_injection_slot.py AND tests/test_injection_cleanup.py. Then un-quarantine per 3×-clean protocol.

## Prevention
- When a commit adds an InstanceManager attribute consumed in `_cleanup_instance_state`, grep test stubs (`_ManagerStub`, `_ManagerStubLike`) for companion attributes — the stub-companion check takes seconds.
- Schedule the registered `injection_unit_test` bundle on any manager.py-touching change; file-list packs that never run accumulate exactly this drift.
- Watch for the "passes elsewhere" tell: `_deferred_question_pause.discard()` at the adjacent line passed — only the NEWER attribute's stub pair failed.

## Session context
Pre-deploy validation of 84fd8018 (graph tool-result placeholders). The 4 failures did NOT block the deploy: base-evidenced pre-existing, quarantined in QUARANTINE.md. Full report: RESULTS/2026-08-25-84fd8018-predeploy-graph-injection-validation.md
