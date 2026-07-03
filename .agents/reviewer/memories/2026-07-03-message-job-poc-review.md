# Message-Job POC Review (commit 7d42f6b5)

## Deep-Review triggered
Data Integrity, Concurrency, Business-Critical, Architecture changes.

## Key Findings
- 🔴 C1: `_get_processing_job_for_instance` matching `queued` can finalize never-processed messages
- 🔴 C2: Fire-and-forget activation race (`run_async_no_wait`) — observer can finalize before activation commits
- 🟡 Non-atomic three-write (Task + JobItem + stamp) — degrades gracefully but creates noise
- 🟡 JobRepository.create redundant default_factory (job_id param shadows model default)
- ✅ Feature flag OFF = zero behavior change (verified)
- ✅ AsyncMessageResult single definition in messaging_types.py (verified)
- ✅ list_pending_by_queue excludes message jobs correctly
- ✅ VALID_TRANSITIONS includes (queued, done)

## Lessons
- Informational mirrors that follow an authoritative primitive MUST complete their state transitions BEFORE the primitive becomes visible to other consumers.
- OR the observer/consumer must JOIN against the authoritative primitive's status.
- Matching `queued` (a pre-processing state) in finalize logic is inherently dangerous.
