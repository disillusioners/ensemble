# Test Report: v0.13.9 result_summary / job-completed defect — LIVE REPRO on stable demo (pre-fix evidence)

Date: 2026-09-22 (02:21–02:25 UTC) · Worker: 5fa4ad92-dbb9-447a-9a38-54f4f7aca4db (`demo-repro-result-summary`, skill e2e-test)
Target: DEMO ONLY — http://127.0.0.1:7979, install /home/nea/agents-ensemble-demo, DB ensemble_demo (read-only)
Discipline held: ZERO contact with LIVE (9797 / ensemble_prod / ~/agents-ensemble); ambient POSTGRES_* (incl. POSTGRES_DB=ensemble_prod) scrubbed before DB work, demo-.env sourced, current_database()=ensemble_demo asserted; zero git; zero process kills; only mutation = the one test job. NO FIXES applied (repro-only mandate).

## Overall VERDICT: **DEFECT REPRODUCED — half-live state confirmed on current stable demo**
- (c) Premature-terminal arm: **PROVEN FIXED (again)** — exactly ONE terminal SSE event, co-incident with completed_at.
- (a) result_summary: **FAIL — null** in final job record AND terminal event, despite substantive LLM answer.
- (b) job-completed event row: **FAIL — does not exist** (0 completion-kind rows in the ENTIRE event table).
- Global notifications stream: **ZERO completion notifications** (banner + pings only).
- 🟠 NEW secondary observation: **double observer-finalize** (two `Observer: finalized job …` lines, first `instance_was_terminal=True`, second without) — candidate clue for the missing job-completed row.

## Pre-check (PASS — STABLE)
- livez HTTP 200: `{"status":"alive","uptime_seconds":36377.16466808319,"version":"0.13.9"}`
- readyz HTTP 200: components database/queue_freshness/services all true, draining=false (checked_at 2026-09-22T02:22:13.078939+00:00)
- Port 7979 owner: pid=422286 `/home/nea/agents-ensemble-demo/current/ensemble-prod`, boot Sep 21 16:15:46 2026 (~10.1h single lineage — same stable lineage as the 2026-09-21 re-run).
- Log scan (last ~10 min): only routine `reconcile_drift_states: reconciled=0` (5-min) + `JobFeedbackObserver: no events in 600s` warnings. No shutdown/recycle/mark-FAILED/Traceback.

## Job under test — `da13791e-1cab-4f06-9c8d-0ac6a677623e`
- Submit: `POST /api/jobs` 02:23:13.954Z → **HTTP 201**. Body: `{"agent_id":"kb-writer","message":"This is a smoke test. Write exactly two short substantive sentences describing what you do. Do not use any tools.","job_type":"task"}`
  (First attempt 02:23:07.187Z used `prompt` field → HTTP 422 missing `message`; schema-corrected. Not an env issue; not a retry of a failed job.)
- Lifecycle: pending → processing → **completed** in **15.32s** (created 02:23:13.984563, started 02:23:17.794252, completed_at 02:23:29.308207).
- Attribution: agent `kb-writer` (agent_dir releases/v0.13.9/agents/kb-writer), queue `system_fifo_queue` (queue_id `eaa19bd9-9335-4c5d-9054-7d226e4f4b65` in record), instance `4fd696d8-ebee-4341-a892-e9215c92a482`, message `93bb2855-ecb3-4c2b-8320-d2fed9b2989a`, source=api, job_type=task, project 71931ae0-0f25-5fbf-853b-2a78cc978d7e.

## (a) result_summary — FAIL (verbatim)
Final record via `GET /api/jobs/da13791e-…` (HTTP 200, 02:23:41.897Z) — key fields verbatim:
```
"status":"completed", "terminal_reason":"completed", "mission_terminal_reason":"completed",
"result_summary": null,
"outcome": null,
"error_message": null,
"queue_id":"eaa19bd9-9335-4c5d-9054-7d226e4f4b65",
"agent_id":"kb-writer"
```
Terminal SSE event (per-job `/api/jobs/{id}/events`, 3 events total: connected → status_update → completed):
```
event: completed
data: {"job_id": "da13791e-1cab-4f06-9c8d-0ac6a677623e", "status": "completed", "result_summary": null, "error_message": null, "queue_id": null, "job_type": "task", "mission_liveness": null, "mission_id": "4fd696d8-ebee-4341-a892-e9215c92a482", "mission_epoch": 1, "mission_terminal_reason": "completed", "outcome": null, "mission_ref": {"mission_id": "4fd696d8-ebee-4341-a892-e9215c92a482", "agent_id": "kb-writer", "liveness": "completed"}}
```
LLM HAD produced substantive content (log 02:23:28: `[LLM] Response: I receive knowledge text, analyze it for distinct domains, and split it into coh…`) — null is not "no answer", it is "answer not propagated". `queue_id: null` in SSE payloads also REPRODUCED (prior-run observation).

## (b) job-completed event row — FAIL (verbatim, guard proof)
Guard sequence: ambient env carried `POSTGRES_DB=ensemble_prod` (+host/user/password) → unset all POSTGRES_*/DATABASE_URL → sourced `/home/nea/agents-ensemble-demo/.env` → `POSTGRES_DB=ensemble_demo` → `SELECT current_database();` = `ensemble_demo` (assertion PASSED) — only then queries ran (read-only).

Event rows for this job (all rows for instance 4fd696d8 since 02:23:13.984563):
```
id  created_at                kind                data (LEFT 400)
25  2026-09-22 02:23:17.762441  message_received     {"message_id":"93bb2855-…","role":"user","content":"This is a smoke test. Write exactly two short substantive sentences describing what you do. Do not use any tools.","source":"api","created_at":"2026-09-22T02:23:17.762307+00:00"}
26  2026-09-22 02:23:29.163110  instance_lifecycle   {"instance_id":"4fd696d8-…","status":"completed","error":null,"parent_id":null}
```
Whole-table distinct kinds: `instance_lifecycle`=5, `message_received`=5 (10 rows total).
Completion-kind sweep: `SELECT … WHERE kind ILIKE '%job%' OR kind ILIKE '%completion%' OR kind ILIKE '%complete%'` → **(0 rows)** across ALL time.
Explicit: (i) job-completed row exists? **NO.** (ii) does any completed event carry a Result body? **NO** — the instance_lifecycle row carries instance status only; no result/summary/job payload.

## (c) Premature-terminal arm — PROVEN FIXED
Exactly ONE terminal SSE event, at ~02:23:29, co-incident with completed_at=02:23:29.308. Timing chain (daemon log, grep -a):
- 02:23:28 graph: LLM response logged
- 02:23:29 child_reports: `_process_child_completion_and_notify_parent called` → `Instance … completed (no parent, no children), status=COMPLETED`
- 02:23:29.163 DB row id=26 (instance_lifecycle) persisted
- 02:23:29 Observer: `finalized job da13791e… status=completed … (released 1 lock(s), instance_was_terminal=True)` **and a SECOND line without the flag (double-finalize — see below)**
- 02:23:29.308 API: `Work da13791e completed with status: completed` (completed_at stamped)
No emission before the child report; no duplicate terminal events.

## Global notifications stream — ZERO completions
Background capture 02:23:26.058Z → 02:24:55Z (89s, covers completion window): only `event: connected {"status":"connected"}` + 3 ping heartbeats. grep: completion_events_in_global_stream=0.

## 🟠 NEW: double observer-finalize
Two `Observer: finalized job da13791e…` lines within the same second — first `(released 1 lock(s), instance_was_terminal=True)`, second `(released 1 lock(s))` without the flag. SSE correctly emitted ONE terminal event and no extra DB row appeared, but the observer finalize path fired twice. Candidate clue for the fix commission: the seam that should stamp result_summary / persist job-completed may be racing or duplicated on this path. Not diagnosed (read-only mandate).

## Conclusion (emission surface)
On the current stable demo (v0.13.9, pid 422286, single lineage since Sep 21 16:15:46), a clean public-API `job_type=task` job (kb-writer, system_fifo_queue) completes with exactly one correctly-timed terminal SSE event — the premature-terminal arm of v0.13.9 is fixed — but the result_summary/job-completed arm is NOT live on the real path: `result_summary` and `outcome` are null in BOTH the terminal event and the final job record despite a substantive LLM answer; the DB `event` table contains NO job-completed (or any completion-kind) row for this job — or ever (0 rows table-wide); and the global notifications stream delivers zero completion notifications. The observer's finalize fires (twice — new double-finalize observation) yet nothing on the child_reports→observer→router path stamps the result body or persists a job-completed event. Same shape as 2026-09-21; fresh verbatim evidence secured pre-fix.

## Raw evidence
`/tmp/demo-repro-20260922/`: 01-precheck.txt, 02-submit-response.txt, 03-sse-per-job.txt, 04-sse-global.txt, 05-final-job-record.txt, 06-events-list.txt, REPORT-evidence.md, job_id.txt, timestamps.txt

## Action Needed
- [ ] Fix commission: wire result_summary stamping + job-completed event persistence into the real observer finalize path; investigate the double-finalize (instance_was_terminal=True then repeat) as the likely seam.
- [ ] Extend `job_completion_acceptance_test` to assert on the REAL emission surface (SSE payload + event-table row for a public-API task job) — LESSONS 2026-08 drift rule applies.
- [ ] Optional: queue_id in SSE event payloads is null while the record carries it — cosmetic schema gap, note for the fix.
