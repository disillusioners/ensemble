# System Default Project Plan Review — 2026-04-24

## Key Findings

### Missed Touchpoints
1. `daemon/services/instance_lifecycle.py:104-106` — Converts "null"/"none"/"" → None (should → system default)
2. `daemon/tools/instance.py:315-317` — Auto-inherits project_id from parent; legacy None propagates to children
3. `daemon/services/retry_scheduler.py:181` — Filters OUT project_id=None jobs from retry (they're silently dropped)
4. `daemon/services/job_retry_engine.py:261` — `find_retryable_jobs(project_id=str=None)` accepts None param
5. `daemon/sources/adapters/scheduler.py:705` — Only routes through queue if project_id is set; otherwise falls to direct path (different from None-path but related)

### Plan Accuracy Issues
- api.py line references slightly off (project_repository created in manager.py, not api.py lifespan)
- Plan correctly identifies the 4 main service-layer touchpoints
- Phase dependency graph is correct

### Critical Edge Cases
- Existing jobs with project_id=NULL need migration BEFORE Phase 3
- Migration system exists (daemon/migrations/) — plan should provide SQL migration
- Scheduler adapter is safe (only enqueues if project_id is configured)
