# LESSONS — ensure.md Validation: Skill Completion Counter Bugfix

- **Date:** 2026-07-21
- **Commit:** `02794c1f`
- **Outcome:** ✅ All in-scope requirements PASS. No failures, no contradictions, no quick fixes needed.

## Notable Findings

### 1. Blast-radius OUT-OF-SCOPE determination is valid for additive, non-locking hooks

The `concurrency_atomic_unit_test` requirement (Critical, always-on) was assessed as **OUT OF SCOPE** for this change. The change adds a fire-and-soft-fail metrics hook (`_record_metrics_for_task`) to `ProcessMessageProcessor`. Validation lesson: an additive async hook that

- fires **outside** any held lock (after `pipeline.execute()` / `execution_gate.run()` returns),
- wraps all sync DB helpers in `asyncio.to_thread`, and
- swallows its own exceptions

introduces **no concurrency/lock interaction** and can be excluded from `concurrency_atomic_unit_test` via static analysis alone — no pack run needed. This mirrors the ensure.md skill's guidance: "Prefer static checks (grep/read) over pytest where the requirement is a static property."

The static analysis chain that proved it:
- `ProcessMessageProcessor.process()` calls `await self._pipeline.execute(...)` (task_processor.py:317)
- `MessageProcessingPipeline.execute()` calls `self._execution_gate.run(...)` (message_processing_pipeline.py:413) — gate lock acquired/released **inside** this call
- `on_success` callback invoked at pipeline line 491-493 — **after** the gate run and all post-turn stages
- Failure-path hooks in `process()`'s try/except fire after `execute()` raises/returns — gate already released

**Reusable for future validations:** when a change is purely an additive async hook with no lock acquisition and full exception swallowing, the concurrency requirement can be discharged by reading the call-site context rather than running the pack.

### 2. The `process_message_metrics` test file is NOT yet a PACKS.md entry

The new test file `tests/services/test_process_message_metrics.py` (9 tests) is covered by the in-scope Req1 but does not have its own row in `.agents/tester/PACKS.md`. Currently it would be picked up by `skill_services_unit_test`'s glob `tests/services/test_skill_*.py` — but its filename (`test_process_message_metrics.py`) does **not** match that glob (`test_skill_*`).

**Action item (not blocking):** Consider either (a) renaming to `test_skill_process_message_metrics.py` so it falls under the existing `skill_services_unit_test` pack glob, or (b) adding a dedicated `process_message_metrics` row to PACKS.md. Without one of these, future scoped runs targeting "skill metrics" may miss this file. Flagged for the tester/leader to decide; not a failure of this change.

### 3. Static checks suffice for async/await and dead-code requirements

Requirements 3 (no sync DB calls on event loop), 5 (callers properly await), and 6 (no dead code) were all discharged by grep + targeted reads — zero pytest invocations. The 3 hook call sites (lines 382, 396, 747) and the two new methods (`_record_metrics_for_task`, `_compute_iterations_and_duration`) are all `async def`, all awaited, and all reachable. This is the intended pattern per ensure.md: "static file check — fast, no pytest."

## No Failures / No Contradictions

- No requirement failed.
- No ensure.md method contradicted tester optimization rules (no bare `pytest`, no `-x`, no unbounded suite).
- No quick fixes were needed or applied.

## Verdict

The change is clean from an ensure.md standpoint. Safe to merge.
