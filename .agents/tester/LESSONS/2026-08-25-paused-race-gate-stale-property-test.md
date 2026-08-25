# Lesson: D13 guard widening outdates property-scenario expectations

**Date:** 2026-08-25 · **Gate:** fix/reconciler-paused-race-job-cancel e2e merge gate
**File:** tests/property/test_turn_state_machine.py (~:1406)

## Symptom

`TestDirectedScenarios::test_directed_pause_during_report_turn` failed `assert 'active' == 'done'` on `_read_job_item_admission` after final reconcile. Deterministic across 2 runs (not flaky — no quarantine path).

## Root cause

Branch commit `8c388e25` widened the reconciler's terminal JobItem guard in `daemon/repositories/task/repository.py` from `i.status = waiting_children` to `i.status IN (waiting_children, paused, running)` (D13: alive-but-transitioning instances suppress the `done` write). The property scenario seeds instance RUNNING and never drives it terminal → under new semantics JobItem correctly stays `active`. The branch updated 4 sibling unit tests in `tests/repositories/test_turn_reconciler.py` (re-seeded to COMPLETED) but missed the property-file scenario — **the 5th expectation site**.

## Fix (test code only, +23 lines, uncommitted — branch author must commit)

New `_force_instance_status` helper (mirrors existing `_force_task_status` raw-SQL pattern) + scenario step: drive instance → COMPLETED, re-reconcile, then assert DONE. Post-fix: 48P/0F/1deselect, exact baseline.

## Generalizable rules

1. **Semantic guard changes must sweep ALL expectation sites, not only the pack-local ones.** Property/directed-scenario files carry hidden copies of invariant expectations (`JobItem == done while instance alive`) that don't grep by function name. Sweep strategy: grep assertion strings (`== "done"`, `AdmissionState.DONE`) across `tests/property/` too.
2. **Deterministic FAIL after an intentional semantic change = stale test, not flake.** Retry budget is for pass/fail mixtures; a twice-identical failure with a known semantic delta root-causes by diff, not by retry.
3. **Quick-fix line budget is a guideline, not a gate:** 23-line mirror-pattern extension accepted; what matters is single-file, mirror-of-existing-pattern, deterministic verification, zero production-code edits.
4. Pack-script `set -euo pipefail` swallows the `RESULT: FAIL` banner on pytest failure — exit code + pytest summary are authoritative.
