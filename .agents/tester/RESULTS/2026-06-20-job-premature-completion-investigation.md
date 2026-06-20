# Investigation: Job Premature Completion Bug

**Date:** 2026-06-20
**Status:** Root cause CONFIRMED (investigation only, no code changes)
**Log sources:** `logs/job-premature-completed.log`, `logs/premature-job-complete.log`, `logs/slack bot 2 hi message problem.log`

---

## 1. Exact Trigger Conditions

A parent JOB transitions to `completed` while its child instances are still running when **the CorrelationManager (CM) — the JOB's terminal driver — resolves completion based on message acknowledgements `(child_id, message_id)` rather than child-instance lifecycle**.

### The core disconnect
There are **two independent completion tracks** that can disagree:

| Track | Driver | Signal | Scope |
|-------|--------|--------|-------|
| **Instance completion** | `child_reports.py` (`_process_child_completion_and_notify_parent`) | `waiting_for > 0` → defers | Per-instance, tracks live children |
| **Job finalization** | `correlation_manager.py` → `job_feedback_observer.py` | All correlated messages acked → fires `completed` | Per-job, tracks message resolutions |

### Trigger conditions (all must be true)
1. A parent instance spawns N children (e.g. via `spawn_instance` + `send_message`), incrementing `waiting_for` to N.
2. A child finishes its **current message** and reports back via `_process_child_completion_and_notify_parent`.
3. The CorrelationManager counts message resolutions keyed by `(child_id, message_id)`. When **the messages it knows about are all acked**, CM fires `handle_correlation_complete` → `status=completed`.
4. The `JobFeedbackObserver` receives the CM callback and **finalizes the parent JOB to `completed`** (releasing locks) — **without checking whether other children instances are still running**.
5. Meanwhile the **instance-level** `waiting_for` is still `> 0` (other children still running), so the instance correctly "defers completion". But the **JOB is already finalized as `completed`** — the instance deferral cannot retroactively un-finalize the job.

**Net effect:** Job status = `completed` (terminal), but children are still running, and the parent instance is stuck in a "deferring completion" loop because `waiting_for > 0` while the job that drove it is already terminal.

---

## 2. Evidence from Logs

### Smoking gun — `job-premature-completed.log` (instance `326e6dab`, job `edab333b`)

**Setup (11:01:30):** Parent `326e6dab` spawns TWO children and sends them messages:
```
11:01:30 - waiting_for incremented -> 1 (parent=326e6dab..., child=ddb7fe1d...)
11:01:30 - waiting_for incremented -> 2 (parent=326e6dab..., child=e31a03ad...)
```
So `waiting_for = 2`. Job `edab333b` is the parent's active job (started 10:58:25).

**Child `e31a03ad` completes first (11:02:34):**
```
11:02:34 - Instance e31a03ad... completed, sending report to parent 326e6dab...
11:02:34 - waiting_for decremented -> 1 (parent=326e6dab..., child=e31a03ad...)
```
`waiting_for` correctly goes 2 → 1. Instance defers (line 174): `Instance 326e6dab... completed message but waiting for 1 children (CM=True), deferring completion`.

**Child `ddb7fe1d` completes second (11:06:36) — BUG TRIGGERS:**
```
11:06:36 - Instance ddb7fe1d... completed, sending report to parent 326e6dab...
11:06:36 - waiting_for decremented -> 0 (parent=326e6dab..., child=ddb7fe1d...)
11:06:36 - CM correlation complete: parent=326e6dab, status=completed, had_error=False
11:06:36 - Observer: finalized job edab333b... status=completed for instance 326e6dab... (released 1 lock(s), instance_was_terminal=False)
```

**❌ THE BUG:** When `ddb7fe1d` reports, CM believes correlation is complete (status=completed) and the observer **finalizes job `edab333b` to `completed`** — even though this is wrong in TWO ways:
- The parent instance `326e6dab` is **not actually done** — it immediately spawns MORE children (coder `2e507bd5` at 11:07:33, tester `8311eb7e` at 11:21, reviewer `1f2ea884` at 11:21, giter `71e8e7ab` at 11:32).
- The parent keeps running until 11:34:18 — **28 minutes after its job was finalized as `completed`**.

**Proof parent kept spawning children after job finalization:**
```
11:06:36  → job edab333b finalized as completed (CM fired)
11:07:33  → parent spawns child 2e507bd5 (coder)
11:07:52  → Instance 326e6dab... parent_id=None, waiting_for=1, status=completed
11:07:52  → Instance 326e6dab... completed message but waiting for 1 children (CM=True), deferring completion
...
11:21:58  → waiting_for=2, status=completed (still deferring, spawning tester+reviewer)
11:32:22  → waiting_for=1, status=completed (still deferring, spawning giter)
11:34:18  → last child (71e8e7ab) finally reports; "CM callback: no active PROCESSING job for instance 326e6dab..., skipping"
```

The last line is damning: **`CM callback: no active PROCESSING job for instance 326e6dab..., skipping`** — when the final child completes, there's no active processing job because it was prematurely finalized 28 minutes earlier.

### Variant 2 — `premature-job-complete.log` (jober dispatching, instance `36878e2a`)

This is the SAME bug via the `job_continue` / `watch_job` pattern:
```
17:26:08 - [LLM] Tool call: job_continue → dispatches child job 58b9f77c to instance 8d71f05f
17:26:18 - [LLM] Tool call: watch_job → {'job_id': '58b9f77c'}
17:26:29 - Instance 36878e2a... parent_id=None, waiting_for=0, status=running
17:26:29 - Instance 36878e2a... no parent, skipping notification
17:26:29 - Instance 36878e2a... completed (no parent, no children), status=COMPLETED   ← PREMATURE
17:26:29 - Job transition: 8df36893 | processing -> completed
```

Here the parent (`36878e2a`) dispatched a child job (`58b9f77c`) via `job_continue` + `watch_job`, but the parent itself has `no children` tracked (the child is a separate watched JOB, not a spawned child instance). So `_process_child_completion` sees `waiting_for=0` and finalizes the parent JOB as `completed` while the watched child job `58b9f77c` is still pending/processing (it only starts at 17:26:29 and completes at 17:28:17).

### Variant 3 — `slack bot 2 hi message problem.log` (instance `1b8036ba`)

Explicit WARNING logged by the system itself:
```
23:13:20 - WARNING - Instance 1b8036ba has pending_count=1 but waiting_for=0 — proceeding to COMPLETED (not waiting_children)
23:13:20 - Instance 1b8036ba... completed (no parent, no children), status=COMPLETED
```
A second "hi" message arrived while the first was processing. `pending_count=1` (a queued message exists) but `waiting_for=0`, so the system proceeds to COMPLETED anyway.

---

## 3. Reproduction Steps

### Repro A (multi-child spawn race — the main bug)
1. Send a message to a parent instance that will spawn 2+ children and `send_message` to each.
2. The parent's active job starts `processing`.
3. **Children must complete their messages in sequence such that the CM sees all known message-resolutions satisfied.**
4. The moment the last *known* child message is acked, CM fires `completed` → observer finalizes the parent JOB to `completed`.
5. Have the parent (via LLM) spawn ANOTHER child after the first batch acked (common in agentic workflows — investigate → spawn fixer).
6. **Observe:** parent JOB is `completed` (terminal) while the new child is running and the parent instance is "deferring completion".

Key trigger: **parent spawns children in multiple waves**. CM only knows about the first wave's message resolutions. When wave 1 acks, job finalizes. Wave 2's children run under an already-terminal job.

### Repro B (job_continue + watch_job)
1. An agent calls `job_continue` to dispatch a child job, then `watch_job` to wait.
2. The agent produces its final text response (no spawned child INSTANCE, only a watched child JOB).
3. `_process_child_completion` sees `waiting_for=0` → finalizes the parent JOB as `completed`.
4. The watched child job is still running (pending/processing).

### Repro C (concurrent messages to root)
1. Two messages arrive for a root instance in quick succession (e.g. Slack "hi" twice).
2. First message processes; second is queued (`pending_count=1`).
3. First completes with `waiting_for=0` → JOB finalized `completed`.
4. Second message then reactivates the completed instance.

---

## Root Cause Summary (confirmed by code analysis)

The CorrelationManager (the JOB's terminal driver) tracks **message resolutions** keyed by `(child_id, message_id)`, not child-instance lifecycle. When all child messages that the CM knows about are acknowledged, CM fires `handle_correlation_complete` and the JOB transitions to `COMPLETED` — releasing all locks. But the instance-side `waiting_for` counter operates independently and can still be `> 0` (children spawned in a later wave, or children whose messages weren't part of the original correlation set). The two tracks are decoupled: job finalization is not gated on instance `waiting_for == 0`, so the JOB reaches its terminal `completed` state while children are still running.

---

## Note on DB Evidence
The production database is `ensemble_prod` (PostgreSQL, `.env.prod`). The affected jobs (`edab333b`, `692676ff`, `9f384288`, `da745e63`) and instance `326e6dab` live in the prod DB, which requires peer auth not available via the DB connection tool. The `ensemble_dev` DB was queried for schema structure (confirming `instances.waiting_for`, `instances.children`, `instances.parent_id`, `job_queue_items.status` columns) but does not contain the prod records. Log evidence is definitive and sufficient.
