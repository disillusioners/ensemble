## E2E Test: Premature Job Completion Fix (MESSAGE jobs + WAITING_CHILDREN)

**Date**: 2026-05-24
**Commits**: 2bfe471, 9bc69f1

### Bug Description
MESSAGE jobs completed while instance was still WAITING_CHILDREN — premature job completion.

### Root Cause
`MessageJobHandler.handle()` did not check instance status before completing the job. It completed the MESSAGE job immediately after processing, even if the instance had spawned children and was waiting for their results.

### Fix Applied
Added WAITING_CHILDREN check in `message_job_handler.py`:
- After processing, check if instance status is WAITING_CHILDREN
- If yes, defer job completion (return early, don't call `complete_job`)
- `JobFeedbackObserver` will complete the deferred job when all children finish and instance transitions to completed

### E2E Verification
- Leader spawns coder → enters WAITING_CHILDREN
- Job completion is deferred (logged)
- Coder completes → report sent to parent
- Leader finishes → JobFeedbackObserver completes the deferred job
- Correct order confirmed through timestamp analysis

### Key Learning
- MESSAGE job lifecycle must respect instance state transitions
- `JobFeedbackObserver` is the correct place to complete deferred jobs (not MessageJobHandler)
- Log ordering is the most reliable way to verify timing bugs
