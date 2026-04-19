# Job Soft Delete — Approval Tracking

## Iteration 001 — 2026-04-19

### Verdict: APPROVED

### Evaluation Notes

1. **Execution path analysis**: Thorough and complete. All 11 repository methods that could lead to job execution are correctly identified. The decision to keep `get()` and `atomic_transition()` unfiltered is correct — they operate on explicit job IDs, not query-based discovery.

2. **`get_by_instance()` filtering**: Initially appeared risky (feedback observer + terminate_instance), but the plan correctly constrains soft-delete to terminal statuses only. PROCESSING jobs cannot be soft-deleted — they can only be cancelled. The race window where a job transitions to terminal AND gets soft-deleted before the observer processes the event is benign because the observer would encounter an InvalidTransitionError (already handled).

3. **Architecture decisions**: All 5 decisions in decisions.md are sound. Repository-level filtering (Decision 3) is the right approach for defense-in-depth.

4. **Minor observations** (non-blocking):
   - The restore endpoint could restore a PENDING deleted job, which would be immediately eligible for scheduling. This is likely intentional but should be documented.
   - The plan doesn't mention the `JobFeedbackObserver` explicitly in its execution path analysis — it's covered implicitly through `get_by_instance()`, but explicit mention would improve clarity.
   - `_job_to_response()` update is correctly captured in Phase 3 Task 2.
   - Hard-delete methods (`delete()`, `delete_completed()`, `delete_by_project()`) appear unused by any service/API code. Renaming to `hard_delete()` creates dead code — consider just removing them in a cleanup phase.

### Summary
Plan is complete, feasible, and safe. The critical risk (scheduler picking up deleted jobs) is addressed through systematic repository-level filtering with an index. No blocking issues found.
