# Job Auto-Inject project_id Implementation

## Date: 2026-04-18

## Problem
When jobs were processed, `project_id` stored on `JobItem` was not passed to `spawn_instance()`. The agent receiving the job message had no automatic project context — relying on fragile keyword extraction instead.

## Solution
Added `project_id=job.project_id` to both `spawn_instance()` calls in `daemon/services/job_processor.py`:
- Main job processing path (~line 191)
- Orphan job fallback path (~line 242)

## Architecture
The system already had full infrastructure for project context injection:
1. `spawn_instance(project_id=...)` stores it in `instance_metadata["project_id"]`
2. `dispatch_message()` reads `instance_metadata["project_id"]` on first message
3. `format_project_context()` prepends project JSON to the message
4. Sets `project_injected=True` flag to prevent re-injection

The fix was a single-line parameter addition to connect existing infrastructure.

## Key Files
- `daemon/services/job_processor.py` — Job execution, spawn calls
- `daemon/manager.py:572-697` — spawn_instance() with project_id storage
- `daemon/manager.py:1078-1151` — Project context injection on first message
- `daemon/repositories/job_queue/models.py:117` — JobItem.project_id field

## Commit: 1d65cc0 on branch feature/job-autoinject-project-id
