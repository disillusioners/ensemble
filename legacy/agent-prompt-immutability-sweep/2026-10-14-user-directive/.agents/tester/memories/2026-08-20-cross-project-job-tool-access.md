# Knowledge: Cross-Project Job Tool Access (feature/job-tools-cross-project-access, 2026-08-20)

Verified-shipped behavior of the job tool ACL in `daemon/tools/job_queue.py`:

- `_check_job_access` (job_queue.py:375) is the single shared C2 access-check helper for the four
  visibility/write tools: `job_messages` (:1394), `job_tree` (:1515), `job_progress` (:1629),
  `job_inject` (:1740). It replaced 4 copy-pasted inline checks.
- Allow tiers: (1) caller.project_id == job.project_id (same-project); (2) caller.project_id ==
  SYSTEM_DEFAULT_PROJECT_ID (global operator tier — any-project access; this is the
  cross-project feature for chat-facing agents like Ari/Jober); deny otherwise with a
  logger.warning audit line naming caller, caller_project, job, job_project.
- Pre-bootstrap: SYSTEM_DEFAULT_PROJECT_ID None → strict deny for system-UUID callers;
  `caller.project_id=None` stays fail-open (deliberate legacy branch — legacy NULL-project rows
  rely on it; api.py backfills at :520/:542).
- `job_list` / `job_get` / `job_create` have NO C2 check by design (resolver/repo-level filtering
  semantics) — verified unchanged by the feature.
- SYSTEM_DEFAULT_PROJECT_ID (daemon/constants.py:89) is None until startup;
  tests/conftest.py:706 autouse fixture patches it to uuid5 71931ae0-… per test.
- job_continue (:858) was NOT migrated to the helper — its 4 failing tests (KeyError
  'instance_id' family) are pre-existing on parent 39f76dc7 and quarantined 2026-08-20.
