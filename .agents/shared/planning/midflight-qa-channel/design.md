# Design: Mid-Flight Question / Answer Channel

| Field | Value |
|---|---|
| **Status** | IMPLEMENTED on `feature/midflight-qa-channel` (2026-09-21) — H1–H7 approver touch-ups folded in below; see git history for the implementation commits. Originally DRAFT planning-only. |
| **Worktree** | `feature/midflight-qa-channel` @ `246b7325` (v0.13.9) |
| **Goal** | Make a mid-flight `ask_questions("...")` surface to the human via the existing push lanes, route the human's answer back to the paused asker, and emit non-blocking mid-flight reports on the same substrate. |
| **Scope** | MEDIUM-LARGE — ~9-12 daemon files (event schemas, work_notifier, watch_job, ask_questions, new `mid_flight_report` tool, two HTTP routes, one new TaskType for the wedge-guard one-shot wake). No polling introduced; no new services; no schema migration beyond additive. |
| **Hard constraints** | Event-driven only (no `asyncio.sleep` loops, no `EVENT_STREAM_POLL_INTERVAL` extensions). Reuse v0.13.9 lanes (EventBus, LiveEventHub, work_notifier → `[JOB_EVENT]` enqueue, mission liveness). Must not regress completed-event Result-body fix. Must not depend on fixing the two deferred defects (Task↔JobItem reconciliation; error-lane parent-before-report ordering). |
| **Out of scope** | Cascading watches through mission classes (already covered by `mission_terminal` opt-in, `work_notifier.py:135-188`). Frontend wiring of the human wizard beyond the SSE surface contract. |
| **Migration target** | At merge time, the durable copy should live at `docs/plans/midflight-qa-channel.md` (sibling of `report-lane-decoupling.md`). The 4-phase sub-plan referenced below is the implementation roadmap. |

---

## 1. Problem (recap)

Two live incidents on 2026-09-21:

1. **Lost question.** A leader called `ask_questions("demo-promote-path")` at a stop gate. The instance paused with `suspension_reason='awaiting_answer'`, the `question_pack` SSE was emitted fire-and-forget on `LiveEventHub` (`live_event_hub.py:384-426`), and nothing reached the job creator/watcher/human. When the human later replied, no agent-facing unpause existed: `job_continue` rejects PAUSED targets (`"Instance is paused — unpause it first"`); `job_inject` rejects paused; `send_message` is R-O1 reject. The HTTP `POST /api/instances/{id}/answer` route (`routers/instances.py:1053`) was never in the loop because the question never surfaced to the human — and it is instance-addressed, not job-addressed. The user worked around it by killing the job and starting fresh, losing the asker's context.

2. **Deep paused chain wedge.** Leader → wanderer → worker all paused (each with `awaiting_answer` handle). No auto-revival mechanism. The chain sat dead for 3h+ because nothing re-poked it. Today, only the human-initiated `POST /answer` (per-instance) can revive it.

### Why the existing surface failed (incident 1)

| Failure mode | Code path | Symptom |
|---|---|---|
| `question_pack` SSE is fire-and-forget | `live_event_hub.py:1-5` — "If no client is listening, events are dropped silently" | The wizard never opened on the FE because no agent-facing unpause exposed the question; even if the FE did open, the wizard UX would only know "the instance asked" without knowing which job to deliver the answer into. |
| Question is not a job event | `work_notifier.py:118-503` only fires for terminal / `in_progress` work statuses; `EventKind` (`repositories/event/models.py:12-23`) has no `QUESTION_REQUESTED` | `watch_job` does not deliver question events. Orchestrator (`job-orchestration/skill.md:163-188` parses `[JOB_EVENT]` headers) never sees the question. |
| Answer is instance-addressed | `routers/instances.py:1053-1305` — `POST /api/instances/{id}/answer` requires the asker's instance id, but the question never told anyone which instance that is | The orchestrator (job creator) has the `work_id`, not the `instance_id`. It cannot call the existing endpoint. |
| In-memory QuestionPack | `question_manager.py:11-13` "NOT persisted — lifetime of the daemon process"; handle is durable (`task.suspension_reason='awaiting_answer'`) | Daemon restart orphans the answer path: handle survives, pack is gone, POST /answer 404s (`routers/instances.py:1097-1109`). The wedge is silent because there is no event the watcher would see — just a row in `task` table. |

---

## 2. Target Architecture

Three event classes ride the **same push-only substrate** — the watcher pipeline that already exists for terminal job events — extended with non-terminal status fan-out. The orchestrator/jober subscribes via the existing `watch_job` tool; the event surfaces in its context as a `[JOB_EVENT]` line; the human answers via a new job-addressed HTTP route.

### 2.1 Substrate reuse (do NOT introduce parallel infrastructure)

```
asker tool                  ── emit ──▶ EventBus.create_event (kind=QUESTION_REQUESTED)  [lane 1]
                                              │
                                              ├─▶ LiveEventHub (FE SSE: question_pack_emit)  [lane 2]
                                              └─▶ work_notifier.notify_work_watchers (status="question_requested")
                                                          │   [FAN-OUT over live work_ids per MAJOR-1]
                                                          ▼
                                              job_watcher instance sees
                                              "[JOB_EVENT] Job {work_id}... question_requested"
                                              with the full payload in its assistant turn.
                                                          │
                                                          ▼ (orchestrator relays to chat source)
                                              human in chat replies
                                                          │
                                                          ▼
                                              POST /api/jobs/{work_id}/answer  [job-addressed; resolves work_id → instance_id]
                                                          │
                                                          ▼
                                              shared _answer_questions_via_instance helper:
                                                T1 set_answers CAS → (pack, transitioned)
                                                T1′ if transitioned=False → 200 already_delivered (no-op)
                                                T1″ if body.question_pack_id ≠ pack.id → 400 QUESTION_PACK_MISMATCH
                                                T3 if asker terminal → 410 ANSWER_TARGET_TERMINAL
                                                resume_processing_job → find_suspended_turn_for_answer
                                                → ResumeTurn → cascade resumes children
                                                Defect-3 fallback (enqueue_message as fresh user message)
                                                → asker continues with Q↔A HumanMessage in checkpoint

[Lane 4 — NotificationBroadcaster — fires ONLY at escalation_index=3 / wedge escalation, no polling]
```

**Four push lanes total** (no polling anywhere):
1. **EventBus** — `event_bus.create_event` persists + broadcasts to global subscribers (`event_bus.py:174-191`).
2. **LiveEventHub** — per-instance SSE queues for the FE (`live_event_hub.py:384-426`).
3. **work_notifier → [JOB_EVENT] enqueue** — non-terminal branch preserves watcher rows; CAS skips at `:417-447`.
4. **NotificationBroadcaster** — unconditional fan-out for `emission_index=3` wedge escalation + future question-targeted terminations (currently operator-facing only; not fired for normal question/report paths).

### 2.2 Three event classes

| Class | Pause? | Where emitted | Where delivered |
|---|---|---|---|
| **`question_requested`** | yes (asker pauses immediately after emission) | `daemon/tools/question_tools.py` `ask_questions()` step 3 | LiveEventHub (FE), work_notifier (orchestrator), EventBus (global) |
| **`midflight_report`** | no (informational) | New `mid_flight_report` tool (any agent) | LiveEventHub (FE), work_notifier (orchestrator, status=`in_progress`), EventBus (global) |
| **`stuck_awaiting_answer`** | n/a (diagnostic) | `_pause_cascade_db_sync` post-commit outbox, plus a one-shot future-dated Task row scheduled for `now + N` minutes (no loop) | work_notifier (orchestrator, status=`stuck_awaiting_answer`), EventBus (global); FE gets an optional banner via LiveEventHub |

### 2.3 Why no polling

- The existing `/jobs/{id}/events` SSE is a 2s poll (`jobs_streaming.py:362-363`, `constants.py:30`) — *do NOT extend this pattern*. New events ride push lanes.
- EventBus is push (`event_bus.py:155-191` — `create_event` persists + broadcasts to global subscribers + notifies per-instance asyncio.Event).
- LiveEventHub is push (per-connection asyncio.Queue).
- `notify_work_watchers` is push (`work_notifier.py:118-503` — terminal CAS for exactly-once; non-terminal preserves watcher rows).
- Wedge guard uses a **one-shot future-dated Task row** (substrate: `next_retry_at` per `stale_task_recovery.py:285` pattern, `worker_pool.py:982`) — NOT a periodic sweep. The one-shot fires once and disappears.

---

## 3. Event Schemas (mandatory §1)

### 3.1 Naming fit — three vocabularies to satisfy

| Vocabulary | Where | Current fit | What we add |
|---|---|---|---|
| `EventKind` enum (`repositories/event/models.py:12-23`, snake_case values) | Persisted `event` table + EventBus broadcast | `INSTANCE_LIFECYCLE`, `MESSAGE_RECEIVED`, etc. | **`QUESTION_REQUESTED`**, **`QUESTION_ANSWERED`**, **`MIDFLIGHT_REPORT`**, **`STUCK_AWAITING_ANSWER`**, **`CHILD_QUESTION_STILL_PENDING`** (5 EventKinds total — 4 also enter the work_notifier status map; the 5th is dispatch-only via EventBus + LiveEventHub) |
| `LiveEventHub` `event_type` strings (`live_event_hub.py:421` — `"event_type": "question_pack"`) | SSE per-instance | `"question_pack"` is the only relevant type today | `"midflight_report"`, `"stuck_awaiting_answer"`, `"answer_received"`, `"child_question_still_pending"` (best-effort FE banner; not load-bearing) |
| `work_notifier` status word (`work_notifier.py:101-107` — `"completed"`, `"settled"`, `"in_progress"`, `"paused"`) | `[JOB_EVENT] Job {work_id}... {status}` header | Terminal + `in_progress` | **`"question_requested"`**, **`"midflight_report"`**, **`"stuck_awaiting_answer"`**, **`"answer_received"`** (4 new non-terminal statuses — `child_question_still_pending` is excluded from this status map; see R1) |
| Source-prefix reservation (`constants.py:540-680`) | `enqueue_message(source=...)` carries the prefix | `internal_agent:job_event:{work_id}:{status}` for terminal/in_progress | Same `internal_agent:job_event:{work_id}:{status}` family extends to the new status words. No new reserved prefix needed. |

**Why `internal_agent:job_event:` not a new prefix:** the orchestrator's parser (`agents/_prompt_system/innate-skills/job-orchestration/skill.md:179`) keys off the `[JOB_EVENT]` header text, not the source prefix. Extending the status word inside the existing header (`... status={status_word}`) is byte-compatible with the parser; new status words are added to its action table (§7.3).

### 3.2 EventType names (additive)

```python
# daemon/repositories/event/models.py — EXTEND EventKind enum
class EventKind(str, enum.Enum):
    # ... existing values 1:1 ...
    QUESTION_REQUESTED = "question_requested"
    QUESTION_ANSWERED = "question_answered"
    MIDFLIGHT_REPORT = "midflight_report"
    STUCK_AWAITING_ANSWER = "stuck_awaiting_answer"
    CHILD_QUESTION_STILL_PENDING = "child_question_still_pending"   # §8.3
```

```python
# daemon/services/work_notifier.py — EXTEND _STATUS_DISPLAY_MAP
_STATUS_DISPLAY_MAP: dict[str, str] = {
    # existing 1:1 ...
    "question_requested": "question requested ❓",
    "answer_received":    "answer received ✓",
    "midflight_report":   "mid-flight report ⟳",
    "stuck_awaiting_answer": "stuck awaiting answer ⏳",
}
```

The icon set matches the existing glyph vocabulary (`✓ ✗ ⟳ ⏸ ❓ ⏳`).

### 3.3 Payload contracts

**`QUESTION_REQUESTED`** (emitted by `ask_questions`):

```json
{
  "instance_id":   "uuid-of-asker",
  "job_id":        "uuid-of-job-that-owns-the-asker",   // = Task.work_id
  "mission_id":    "uuid-of-mission-or-instance_id-if-root",
  "parent_id":     "uuid-of-parent-if-spawned",         // null for root askers
  "asker_agent_id":"leader",
  "asker_instance_name":"<instance_metadata.title or instance_name>",
  "question_pack_id":"uuid-from-QuestionManager-also-stamped-on-Task",
  "questions": [
    {"id": "uuid", "text": "Approach A or B?", "options": [...], "allow_custom": true, "required": true},
    ...
  ],
  "suspension_reason": "awaiting_answer",
  "paused_at":         "iso-8601",
  "child_count":       0   // for the wedge-guard reasoning
}
```

`question_pack_id` is the new durability hook (§5.4) — the same UUID4 stamped onto the Task row's `instance_metadata` JSONB so the answer route can find the pack even across daemon restarts. The live `QuestionManager._packs[instance_id]` stays in-memory as the canonical pre-answer store; the on-disk `instance_metadata['question_pack_id']` is the durable handle.

**Multi-work_id fan-out (incident-1 recurrence prevention).** `job_id` is an **array**, not a single field. The asker's question is fanned out to every work_id that currently maps to the asker — without this, a turn-2+ question on a long-running job silently notifies a work_id nobody watches (a real recurrence of incident 1). The asker's live work_ids are enumerated by reading:

- **JobItem side:** `JobItemRepository.get_active_by_instance(instance_id)` (`daemon/repositories/job_queue/repository.py:553+`) returns the live JobItem (QUEUED or ACTIVE admission state; excludes terminal + soft-deleted). For multi-JobItem instances (revive races, manual DB ops), fall back to `get_by_instance(instance_id)` (`repository.py:531-552`) which returns the most recent non-deleted JobItem deterministically.
- **Task side:** `TaskRepository.get_by_instance(instance_id)` (`daemon/repositories/task/repository.py:284-298`) returns all tasks newest-first. The runtime's current Task (the one whose `work_id` will appear in any resume-route computation) is the newest non-terminal row (`status='running' OR status='pending'`).

```json
{
  "instance_id":   "uuid-of-asker",
  "job_id":        ["uuid-of-each-live-work-id"],   // ARRAY (was singular — incident-1 recurrence)
  "mission_id":    "uuid-of-mission-or-instance_id-if-root",
  "parent_id":     "uuid-of-parent-if-spawned",     // null for root askers
  "asker_agent_id":"leader",
  "asker_instance_name":"<instance_metadata.title or instance_name>",
  "question_pack_id":"uuid-from-QuestionManager-also-stamped-on-Task",
  "questions": [
    {"id": "uuid", "text": "Approach A or B?", "options": [...], "allow_custom": true, "required": true},
    ...
  ],
  "suspension_reason": "awaiting_answer",
  "paused_at":         "iso-8601",
  "child_count":       0,                          // CONCRETE (H6): number of DIRECT children
                                                  //   (InstanceRepository.get_children(asker)) whose
                                                  //   instance status is NOT terminal
                                                  //   (completed/error/terminated/failed) — the live-
                                                  //   dependent count the wedge guard reasons about
  "fan_out_count":     2                           // CONCRETE (H6): len(job_id) — the number of live
                                                  //   work_ids fanned to (active JobItem, get_by_instance
                                                  //   fallback, + current Task), i.e. the number of
                                                  //   notify_work_watchers calls this emission makes
}
```

**Why fan-out is the DEFAULT, not an optimization:** the `instance_lifecycle.question_pack` SSE is fire-and-forget on `LiveEventHub`; the `[JOB_EVENT] Job {work_id}... question_requested ❓` line is delivered to every row in `job_watchers WHERE work_id IN (live_work_ids)`. Missing a work_id = the orchestrator watching the OTHER work_id never hears about the question — that IS incident 1. **Sanctioned fallback** (only if fan-out proves noisy at scale): document a first-turn-only scope + a new instance-routed event lane. Fan-out remains the default until measured otherwise.

**`QUESTION_ANSWERED`** (emitted by `POST /api/jobs/{work_id}/answer`):

```json
{
  "instance_id":   "uuid-of-asker",
  "job_id":        "uuid-of-job",
  "question_pack_id":"uuid",
  "answers":       { "<qid>": "<answer>", ... },
  "resume_route":  "answer_gate_existing_turn | enqueue_as_fresh_message",
  "delivery_ms":   123
}
```

`resume_route` reports which of the two branches in §5.3 fired (so a future operator can spot defect-3 fallback overuse).

**`MIDFLIGHT_REPORT`** (emitted by `mid_flight_report` tool):

```json
{
  "instance_id":   "uuid-of-reporter",
  "job_id":        "uuid-of-job-that-owns-the-reporter",
  "report_id":     "uuid",
  "summary":       "≤500 chars human-readable headline",
  "details":       "≤8KB optional longer text",
  "level":         "info | warning | decision",     // orchestrator can prioritize
  "paused":        false,                            // explicitly false — do NOT trigger pause
  "tools_used":    ["kb_search", "..."],             // best-effort: from the last tool batch
  "decision_required": false,                        // true would mean "stop and ask"
}
```

`level=decision_required` is the explicit "this is a question in disguise" affordance — but does NOT pause the reporter; the human relay still happens through the question lane. Mid-flight reports are by design non-blocking.

**`STUCK_AWAITING_ANSWER`** (emitted by `_pause_cascade_db_sync` post-commit + by a one-shot future Task row at `now + N min`):

```json
{
  "instance_id":      "uuid-of-asker",
  "job_id":           "uuid-of-job",
  "mission_id":       "uuid",
  "question_pack_id": "uuid",
  "asker_agent_id":   "leader",
  "paused_at":        "iso-8601",
  "waiting_for_seconds": 0,                          // at emission time
  "wedge_chain":      ["<root_paused_id>", "<mid_paused_id>", "<this_id>"],
  "child_count":      0
}
```

`wedge_chain` is the ancestry of paused-`awaiting_answer` instances from the root down to this one. Empty list means "this is the wedge root" (orchestrator should treat it as the actual question target).

---

## 4. Delivery Path (mandatory §2)

### 4.1 Question event: tool → bus → watcher pipeline

Concretely, in three synchronous steps inside `ask_questions` (`daemon/tools/question_tools.py:399-410`):

```text
[1] manager._question_manager.set_question_pack(instance_id, normalized)        # question_manager.py:214
    → returns QuestionPack (status=pending) with auto-gen id; OR None if duplicate pending
    → ALSO stamp instance_metadata['question_pack_id'] = pack.id   [DURABILITY HOOK — new]

[2] live_hub.stream_question_pack(instance_id, pack_to_dict(pack))              # live_event_hub.py:384
    → best-effort SSE: frontend wizard opens (existing behavior, unchanged)

[3] event_bus.create_event(instance_id, EventKind.QUESTION_REQUESTED, data=payload)   # NEW
    → persists row in `event` table (event_bus.py:174-181)
    → broadcast to all EventBus global subscribers (event_bus.py:187-191)

[4] notify_work_watchers(                                                     # NEW call site — FAN-OUT per incident-1 recurrence prevention
      work_id=<each-live-work-id>,                  # iterated: asker's active JobItem + current Task
      status="question_requested",
      instance_manager=manager,
      work_resolver=resolver,
      watcher_repo=watcher_repo,
      progress=None,
      result_summary=pack_to_dict(pack),   # the orchestrator parses the pack
    )
    → enqueues "[JOB_EVENT] Job {work_id}... question_requested ❓" into every watcher's instance
      for EVERY live work_id (NOT just the first — turn-2+ questions must reach watchers of any
      active JobItem or current Task). Single turn with N=1 work_ids → N notify calls.
    → status_display per work_notifier.py:_STATUS_DISPLAY_MAP (extended)
    → non-terminal: CAS claim is SKIPPED (work_notifier.py:182-188) — watcher rows survive

[5] manager.set_question_pause_requested(instance_id)                          # question_tools.py:415
    → (existing) flag set, post-tools conditional edge → question_pause_node → cascade pause
```

**Step [3] emits BEFORE [5]** because the pause cascade cancels the graph task (`instance_messaging.py:4406-4478`); any post-pause code on the tool-call side is moot (the F3 timing constraint from `live_event_hub.py:393-399`). The existing SSE emission at step [2] is already before [5] for the same reason.

**Step [1] durability hook:** write `instance_metadata['question_pack_id'] = pack.id` via `instance_repository.set_metadata(instance_id, key, value)` (additive — JSONB column already exists). This is the ONLY piece of durable state the answer route needs to find the pack after a daemon restart. On daemon restart, the pack is reconstructed from the metadata (see §5.4).

### 4.2 Report event: same lane, no pause

A new tool, `mid_flight_report`, replaces nothing — it complements `ask_questions`. The tool body is purely emit-and-return:

```text
mid_flight_report(summary, details="", level="info", decision_required=False):
    payload = {...}  # per §3.3
    await event_bus.create_event(instance_id, MIDFLIGHT_REPORT, payload)
    await live_hub.stream_report(instance_id, payload)         # NEW LiveEventHub method
    await notify_work_watchers(work_id, status="midflight_report",
                               progress=summary, ...)
    # NO pause flag set; NO post-tools-router side effect
    return f"Reported. {len(watchers)} watcher(s) notified."
```

The `work_notifier` path uses status `midflight_report` (non-terminal, per the existing non-terminal branch `work_notifier.py:182-188`), so watcher rows are preserved across the in-flight broadcast — a later terminal `[JOB_EVENT]` still fires.

### 4.3 Stuck/heartbeat event: transition-time emission + one-shot future wake

**Emission #1 — at pause time.** Inside `_pause_cascade_db_sync` (`instance_lifecycle.py:4873-4983`), AFTER the `session.commit()` at line 4983, for any task whose `effective_suspension_reason == 'awaiting_answer'`:

```python
# Post-commit outbox (mirrors the existing wakeup/SSE payloads the helper returns)
for work_id in suspended_work_ids:
    if _task_suspension_reason(work_id) == "awaiting_answer":
        await event_bus.create_event(
            instance_id=..., kind=EventKind.STUCK_AWAITING_ANSWER,
            data={...payload...}
        )
        await notify_work_watchers(
            work_id=work_id, status="stuck_awaiting_answer",
            progress=f"paused for {paused_at}s", ...
        )
```

This is **transition-time emission** — exactly what the constraint allows. It fires ONCE at pause, no loop.

**Emission #2 — one-shot future wake (the wedge guard).** To prevent silent 3h+ wedges, after emission #1 the pause path also schedules a **single** future-dated Task row via the asker-bound claim lane. The architectural decision: **asker-bound Task + type-scoped claim-gate carve-out** wins; the system-lane alternative (NULL-instance or mission-bound) was evaluated and rejected on five structural grounds (Task.instance_id is non-nullable per `task/models.py:178`; `NULL NOT IN (subquery)` is UNKNOWN; mission-bound hits the concurrency gate; JobItem future-dating rides the 60s `RetryScheduler` poll loop at `retry_scheduler.py:70,84,162`; no other restart-survivable one-shot substrate exists).

**Gate change — type-scoped carve-out at `daemon/repositories/task/repository.py:1931-1952`:**

```sql
-- WRAP the existing pause gate (precedent: the cross-system guard is already
-- type-scoped — task_type != :process_message_type OR ... — at
-- repository.py:1953-1957, bind-literal convention :1984).
AND (
    task.task_type = :heartbeat_emit_stuck
    OR instance_id NOT IN (
        SELECT instance_id FROM instances
        WHERE status IN (:status_paused, :status_terminated)
    )
)
```

- Bind the literal `"heartbeat_emit_stuck"` (same convention as `:process_message_type`).
- **Deliberately broad over TERMINATED**: the processor's terminal no-op IS the cleanup path; this makes invisible-row orphans impossible by construction.
- **Concurrency gate untouched** (`repository.py:1866-1930` — `status='running'`-only) — it is what makes the resume race deterministic.

**Row creation — Task has NO metadata column** (`task/models.py:160-260` shows only `result`/`error`/`suspension_reason`/etc., no `metadata`), and the real `create()` signature is `create(task_type, instance_id, message_id=None)` (`repository.py:237-262`) — no `status`, no `next_retry_at`, no `metadata` kwarg. Mint via direct `Task(...)` construction with `next_retry_at` (precedents: `c_revival` at `manager.py:8648-8657`; `RetryTurn` child INSERT at `repository.py:4281-4290`) OR a small new repo method.

**Context carrier**: `question_pack_id` rides the durable `instance_metadata['question_pack_id']` hook the design already defines (§4.1 step 1). **`emission_index` derives from persisted event history** — count prior `stuck_awaiting_answer` rows in the `event` table for this `question_pack_id` via `event_repo` (EventBus persists before broadcast at `event_bus.py:174-181`). This survives `StaleTaskRecovery`'s retry-child minting (`repository.py:4288-4301`), which drops unknown columns.

**Re-arm clause (single-emission chain, constraint-compliant — no scan, no loop):** when the processor claims a stuck-emission Task whose `emission_index < 3` AND the no-op predicate is FALSE (the wedge is still alive — `find_suspended_turn_for_answer` still finds the asker's `awaiting_answer` handle AND the asker's instance status is non-terminal), it mints **EXACTLY ONE** successor one-shot Task row at `now + STUCK_HEARTBEAT_AFTER_SECONDS` (1800s = 30 minutes per §4.4 constant) for the SAME asker instance. The successor inherits the type-scoped claim-gate bypass (§1.4) and runs the same emission flow with `emission_index` derived from persisted event history. **No successor is minted** when `emission_index == 3` (escalation) OR when the no-op predicate fires (asker already answered or terminated — the heartbeat self-cancels).

**Timing arithmetic — single source of truth = `STUCK_HEARTBEAT_AFTER_SECONDS = 1800` (30 min, per §4.4):**

| Emission | Trigger | Absolute time from pause | emission_index |
|---|---|---|---|
| #1 | Transition-time emission (post-commit outbox at pause) | t = 0 | 1 |
| #2 | First successor one-shot Task (re-arm after #1) | t = 1800s (30 min) | 2 |
| #3 | Second successor one-shot Task (re-arm after #2) | t = 3600s (60 min) | 3 |
| Escalation | Third successor NOT minted — processor terminates asker directly + fans out | t = 3600s (60 min total) | n/a |

**Total wedge-detection window: ~60 min, NOT 90 min.** The earlier "~90 min" wording was arithmetic drift — corrected.

**Processor**: one entry in the task dispatcher table (`task_processor.py:1285-1306`, `:1347`), default-lane fuel, wake latency ≤3s after `next_retry_at` via the existing condition-timeout claim loop (`worker_pool.py:371`) — no `notify_work` needed.

**Escalation at `emission_index=3`**: call `manager.terminate_instance` **directly from the processor** (`manager.py:9401`). Do NOT route through task-ERROR → `JobFeedbackObserver` inference (drifts into the deferred error-lane defect, `error_reporting.py:222-296` — constraint respected). `terminate_instance_cascade` (design §8.7) does not exist by that name — the cascade lives inside `terminate_instance` itself. **In addition to work_notifier + EventBus**, the `emission_index=3` escalation fans out **unconditionally to NotificationBroadcaster** (`notification_broadcaster.py:19`; root-level precedent `instance_lifecycle.py:2060-2082`) so escalation reaches operators even when nobody is watching. **Drift-reconciler noise**: heartbeat rows sit PENDING >300s with NULL heartbeat → Pattern (a) WARNING per 300s cycle; safe but noisy. **Backlog note**: exclude the `heartbeat_emit_stuck` type from `list_pending_tasks_older_than` (`repository.py:1007-1046`) when convenient (registration in its type table).

**This is NOT polling.** Each link of the chain sits in the DB at `next_retry_at` ahead of time. The standard worker-claim loop wakes it once. The chain is finite (3 links) and each link is a one-shot, so the total work is bounded. There is no `asyncio.sleep`, no periodic state scan.

### 4.4 Concrete files touched (delivery path)

| File | Change | Lines (approx) |
|---|---|---|
| `daemon/repositories/event/models.py` | Extend `EventKind` (5 new enum members — the 4 lane kinds + `CHILD_QUESTION_STILL_PENDING`) | +5 lines |
| `daemon/repositories/job_queue/watcher_models.py` | **(H1)** Register the 4 new status words in `ALL_WATCHABLE_EVENTS` (:16) — without this, `watch_job`'s events validation (`job_queue.py:1693-1701`) REJECTS them and `notify_work_watchers`' per-watcher `status in watch_events` filter silently drops every non-terminal notification. The mission-terminal set (`ALL_MISSION_TERMINAL_WATCHABLE_EVENTS`, :33-35) is NOT extended — new kinds are non-mission-terminal | +6 lines |
| `daemon/repositories/task/models.py` | **(H4)** Add `HEARTBEAT_EMIT_STUCK` to `TaskType` AND `PAUSED_BY_PARENT` to `SuspensionReason` (both pure-Python members — `suspension_reason`/`task_type` are TEXT columns, zero SQL migration) | +2 lines |
| `daemon/services/work_notifier.py` | Extend `_STATUS_DISPLAY_MAP` | +4 lines |
| `daemon/services/live_event_hub.py` | New `stream_midflight_report()`, `stream_stuck_awaiting_answer()`, `stream_child_question_still_pending()`, `stream_answer_received()` | +60 lines |
| `daemon/tools/question_tools.py` | Add the `notify_work_watchers` + `event_bus.create_event` calls after the existing SSE emission (steps [3]+[4] of §4.1); stamp `question_pack_id` into `instance_metadata`; **fan-out iteration over live work_ids** (JobItemRepository.get_active_by_instance + TaskRepository.get_by_instance, per MAJOR-1) | +40 lines |
| `daemon/services/instance_lifecycle.py` | Inside `_pause_cascade_db_sync` post-commit: emit stuck/heartbeat event #1 for `awaiting_answer` tasks (stamping `paused_by_parent` distinct suspension_reason on cascade-inherited children — leader decision 2); schedule the one-shot future Task row via direct `Task(...)` construction. Inside `_resume_cascade_db_sync` post-commit: emit `child_question_still_pending` event for each resumed instance with a still-pending pack. Clear `question_pack_payload`/`question_pack_id` from `instance_metadata` on answer consumption (per R3 fix) | +70 lines |
| `daemon/services/task_processor.py` | **(H7)** Register `HeartbeatEmitStuckProcessor(instance_manager, task_repo, event_repo)` for the literal `heartbeat_emit_stuck` in the dispatcher table (`task_processor.py:1285-1306, :1347`); signature mirrors `SendReportProcessor`/`CleanupProcessor`. Concurrency-gate interaction: the row's `instance_id` IS the asker — when the asker has RESUMED and another of its tasks is RUNNING, the per-instance concurrency gate (`claim_pending_task`'s `status='running'`-only guard, UNTOUCHED by the carve-out) holds the heartbeat PENDING for that turn's duration (bounded ~ms interference, accepted per OQ-4 #3); the processor consumes no asker slot beyond its own claimed row. Processor calls `manager.terminate_instance` directly at `emission_index=3`; the durable no-op predicate uses `find_suspended_turn_for_answer` (NOT in-RAM pack status) | +140 lines |
| `daemon/repositories/task/models.py` | Add `HEARTBEAT_EMIT_STUCK` to `TaskType` enum | +1 line |
| `daemon/repositories/task/repository.py` | **(a)** Add type-scoped disjunct at `:1931-1952` (wrap the pause gate — exactly the §1.4 spec). **(b)** Add a new repo method (e.g. `create_one_shot_heartbeat(task_type, instance_id, next_retry_at)`) OR document the direct `Task(...)` construction path (no `metadata` kwarg on `create()`). **(c)** Register `heartbeat_emit_stuck` for exclusion in `list_pending_tasks_older_than` (`:1007-1046`) to suppress drift-reconciler noise | +30 lines |
| `daemon/services/question_manager.py` | **(a)** `set_answers` returns `(pack \| None, transitioned: bool)` tuple under existing lock (CAS — pending→answered exactly-once, no overwrite on `status='answered'`). **(b)** New `_get_pending_packs_for_instances(tree_ids)` read API under lock, used by `_resume_cascade_db_sync` for the OQ-2 child-question detection. **(c)** Clear `instance_metadata['question_pack_payload']` / `['question_pack_id']` on `status='answered'` (R3 fix — moved off `set_question_pack`). **(d)** Boot-time rehydration pass in `manager.__init__` reads `idx_task_status_type_created` `status='paused'` prefix with Python post-filter on `suspension_reason` (no index change) | +80 lines |
| `daemon/routers/jobs.py` | New `POST /api/jobs/{work_id}/answer` route (~50 lines): resolves `work_id → instance_id` via `WorkResolver.resolve_work` (`work_resolver.py:1000-1053`), delegates to shared `_answer_questions_via_instance` helper | +50 lines |
| `daemon/routers/instances.py` | Extract the existing answer-route body (`:1053-1305`) into a shared `_answer_questions_via_instance` helper; both routes delegate to it. Helper enforces the new guards: T1 CAS, T1′ duplicate, T1″ pack correlation, T3 status pre-check, T7 missing-pack → `QUESTION_PACK_LOST` (fixes today's mislabeled `INSTANCE_NOT_FOUND` at `:1097-1109`) | +20 lines net (extraction + shared logic, -10 lines duplication) |
| `daemon/services/mission_resolver.py` | No change (read-only consumer) | 0 lines |
| `daemon/tools/midflight_report.py` | NEW file: `mid_flight_report(summary, details, level, decision_required)` tool — non-blocking emit-and-return | +60 lines |
| `daemon/tools/_tool_registry.py` | Register `"midflight": "daemon.tools.midflight_report"` category | +1 line |
| `daemon/tools/instance.py` | Wire `create_midflight_tools()` into `create_instance_tools()` | +5 lines |
| `agents/<leader,developer,coder,tester,devops>/meta.json` | Add `"midflight"` to `tools.allow` for the agents that get the new tool | ~5 lines per agent |
| `agents/_prompt_system/innate-skills/job-orchestration/skill.md` | Extend status action table with the 5 new statuses (parser unchanged — already extracts `status`, `Result:`, `Error:` lines) | +30 lines |
| `daemon/services/notification_broadcaster.py` | New `emit_question_escalation(...)` helper invoked at `emission_index=3` (parallel to `emit_root_completion`) | +20 lines |
| `daemon/constants.py` | Add `STUCK_HEARTBEAT_AFTER_SECONDS = 1800`; add error code constants for `NO_PENDING_QUESTION`, `QUESTION_PACK_LOST`, `ANSWER_TARGET_TERMINAL`, `QUESTION_PACK_MISMATCH` | +10 lines |

---

## 5. Answer Addressing + Delivery (mandatory §3)

### 5.1 Why `POST /api/instances/{id}/answer` didn't help in incident 1

Two reasons, both load-bearing:

1. **It is instance-addressed, not job-addressed.** The orchestrator holds the `work_id` (a `Task.work_id` / `JobItem.job_id`), not the `instance_id`. When the question never surfaced as a job event, the orchestrator never had the `instance_id` to call the endpoint with. Today, even if a human pasted an instance id into chat, the FE wizard has no UI for `POST /api/instances/{id}/answer` outside the existing per-instance SSE flow — and even that flow is gated on the in-memory `QuestionManager._packs[instance_id]` which is empty if the FE never opened the wizard.
2. **The pack is in-memory; the handle is durable.** `QuestionManager._packs[instance_id]` is RAM-only (`question_manager.py:11-13`); the Task row's `suspension_reason='awaiting_answer'` + `resume_target_turn_id` is durable (`manager.py:5062-5073`). Daemon restart → handle survives, pack is gone → `POST /answer` 404s with "No question pack for instance..." (`routers/instances.py:1097-1109`). The wedge is silent: no event, no UI, no recovery except human eyes on logs.

### 5.2 New job-addressed HTTP route

```
POST /api/jobs/{work_id}/answer
body: { answers: dict, resume_message: str | None = None }
200: { status: "answer_received", work_id, instance_id, question_pack_id, resume_route }
404: { code: "JOB_NOT_FOUND" }                       # no such work_id
404: { code: "NO_PENDING_QUESTION" }                # no awaiting_answer handle on this job's instance
410: { code: "QUESTION_PACK_LOST" }                  # durable handle but in-memory pack gone (see §5.4)
503: { code: "WRITE_PAUSED" }                        # migration
```

**Why a separate route and not just an `X-Job-Id` header on the existing one:** the existing instance-addressed route is the canonical FE wizard path (chat surface knows the `instance_id`). The job-addressed route is the orchestrator-relay path (chat source knows the `work_id`). Two surfaces, two callers, two IDs — separating them keeps each contract tight and avoids header-driven branching inside the existing endpoint. Both share the same downstream `_question_manager.set_answers` + `resume_processing_job` fan-out; the only difference is the lookup direction (work_id → instance_id → pack).

### 5.3 Routing back to the paused asker

The new route mirrors `POST /api/instances/{id}/answer` (`routers/instances.py:1053-1305`, ~250 lines — the existing handler body) but resolves `work_id → instance_id` first:

```python
# daemon/routers/jobs.py — NEW endpoint (jobs router already exists)
@router.post("/{work_id}/answer")
async def answer_questions_job(work_id: str, body, request):
    # 1. Resolve work_id → instance_id (WorkResolver, single source of truth —
    #    work_resolver.py:1000-1053: 2 indexed SELECTs over Task.work_id and
    #    JobItem.job_id, read-only).
    record = await work_resolver.resolve_work(work_id)
    if record is None:
        raise HTTPException(404, detail={"code": "JOB_NOT_FOUND", "work_id": work_id})
    instance_id = record.instance_id

    # 2. Delegate to the shared _answer_questions_via_instance helper.
    #    Extracted from rounters/instances.py:1053-1305 — single source of
    #    truth for "set_answers + cascade + fallback + new T1/T1'/T1''/T3/T7
    #    guards". The existing instance route also delegates to it.
    return await _answer_questions_via_instance(
        instance_id=instance_id, body=body, request=request,
    )
```

The `_answer_questions_via_instance` helper is extracted from `routers/instances.py:1053-1305` and reused — ~90% of the existing route is shared logic, extraction is mechanical. The jobs router already ships **six** per-job POST actions (`/{job_id}/cancel|restore|retry|cleanup|resend-foreground`, `defer-holders/{id}/force-complete` — `jobs_management.py:225,353,438,832,951,1021`), so a new `{job_id}/answer` slot is consistent with established precedent.

**Error codes (live in the shared helper, so both surfaces inherit — also fixes today's mislabeled `INSTANCE_NOT_FOUND` 404 at `instances.py:1097-1109`):**

| Code | HTTP | Meaning |
|---|---|---|
| `INSTANCE_NOT_FOUND` *(fix pass NIT-10 — aligns the table to the implemented behavior)* | 404 | Instance unknown to the manager. Raised by the shared helper; the jobs surface pre-empts it with `JOB_NOT_FOUND` at work_id resolve time, before delegating. |
| `NO_PENDING_QUESTION` *(new)* | 404 | Instance exists but has no pack in `status='pending'`. (Was mislabeled `INSTANCE_NOT_FOUND` before.) |
| `QUESTION_PACK_LOST` *(new)* | 410 | Pack gone after restart, `instance_metadata['question_pack_payload']` also missing. |
| `QUESTION_PACK_MISMATCH` *(new)* | 400 | Body `question_pack_id` ≠ current pack id (stale-answers hijack guard). |
| `ANSWER_TARGET_TERMINAL` *(new)* | 410 | Asker is COMPLETED/TERMINATED — answer rejected (leader decision 1). |
| `ALREADY_DELIVERED` (no error code; 200 with `resume_route:"already_delivered"`) | 200 | CAS at T1′ lost — pack is already answered; no-op. |
| `WRITE_PAUSED` *(unchanged)* | 503 | Daemon migration mode. |

### 5.4 Durability: pack reconstruction after daemon restart

The pack is gone from RAM after restart; the handle is gone if `set_answers` succeeds (the `ResumeTurn` at `instance_lifecycle.py:5545` nulls `suspension_reason` + `resume_target_turn_id`). To survive a restart between emission and answer:

1. **`instance_metadata['question_pack_id']`** stamped at step [1] of §4.1 carries the `pack.id`. **NEW** — one-line write to `instance_metadata` (JSONB; no schema migration).
2. **Pack state also written to `instance_metadata['question_pack_payload']`** (`metadata["question_pack_payload"]` = `pack_to_dict(pack)`, ≤ 8KB typically). This is the JSONB-as-blob pattern already used for `mcp_tool_names` etc. (`instance_lifecycle.py:1987`).
3. **Metadata clear on answer consumption (R3 fix — moved off `set_question_pack`):** `set_answers` (and the no-op duplicate branch) deletes both `question_pack_payload` and `question_pack_id` from `instance_metadata`. Clearing in `set_question_pack` (the create site) would create a stale-rehydration window where a rehydrated answered pack can re-trigger a re-emission. Clearing in `set_answers` is the only safe site.
4. **On daemon startup, a one-shot hydration pass** in `manager.__init__` rehydrates packs. Per OQ-1 (architect recommendation §2): **NO index change.** Existing index `idx_task_status_type_created` on `(status, task_type, created_at)` (migration `20260906_192100_add_task_claim_wake_lane_index.sql:58`) covers the boot scan via its `status='paused'` prefix. The scan is:
   ```sql
   SELECT instance_id FROM task WHERE status = 'paused'
   ```
   Python post-filter on `suspension_reason == 'awaiting_answer'`. Cost is O(paused tasks) — bounded by active orchestration depth (tens, not thousands), once at boot, milliseconds. **Never scan by `suspension_reason` alone** — would force a full table scan. The hydration pass reads each pack payload from `instance_metadata`, reconstructs the `QuestionPack`, and inserts into `QuestionManager._packs` under the lock. Idempotent: only patches packs whose in-memory state is empty.
5. The HTTP route's `410 QUESTION_PACK_LOST` is returned when `set_answers` returns `(None, _)` AND `instance_metadata['question_pack_payload']` is also missing. (The route resolves `work_id → instance_id`, then attempts to use the existing `_answer_questions_via_instance`; if that returns 404 on "No question pack", the new route catches and converts to 410.)

This addresses durability without changing `QuestionManager`'s in-memory design.

### 5.5 Defect-3 fallback

Reuse verbatim. `routers/instances.py:1188-1211` already has the enqueue-as-fresh-message fallback for the rare case where `find_suspended_turn_for_answer` finds no `awaiting_answer` handle. The job-addressed route inherits this. The `resume_route` field in the `QUESTION_ANSWERED` event payload (§3.3) reports which branch fired.

---

## 6. Resume Semantics (mandatory §4)

### 6.1 PAUSED → RUNNING transition

For the asker:

```text
answer arrives (HTTP POST /api/jobs/{work_id}/answer)
    │
    ├─ question_manager.set_answers → (pack, transitioned) tuple under lock  # CAS — T1
    │     pending → answered exactly-once; status='answered' on entry → (pack, False) without overwrite
    │     moved per OQ-3: the prior non-atomic set_answers (question_manager.py:307-338) was overwrite-idempotent
    │
    ├─ if transitioned=False: return 200 {resume_route: "already_delivered"}     # T1′ no-op path
    │     skips everything below — SSE, events, resume, Defect-3
    │
    ├─ if body.question_pack_id present AND ≠ pack.id: return 400 QUESTION_PACK_MISMATCH  # T1″ stale-pack guard
    │
    ├─ live_hub.stream_question_pack(instance_id, {status: "answered"})   # best-effort
    │
    ├─ event_bus.create_event(instance_id, EventKind.QUESTION_ANSWERED, data)
    │
    ├─ notify_work_watchers(work_id, status="answer_received", result_summary=<pack>)
    │     # non-terminal fan-out so orchestrator knows the answer landed BEFORE the asker continues
    │
    └─ resume_processing_job(instance_id, message=answer_msg, route_outcome="answer_gate_existing_turn")
          │
          ├─ T3: if asker status in (COMPLETED, TERMINATED) → 410 ANSWER_TARGET_TERMINAL
          │     (T2a / T2b / T2b′ are not even tried; the answer is rejected outright per leader decision 1)
          │
          ├─ T3b: if asker status in (ERROR, FAILED) → REVIVE + deliver the answer (no revive-budget consumption —
          │     mirror user-API revive semantics; leader decision 1). Return resume_route:"revived_error_target".
          │
          ├─ find_suspended_turn_for_answer(instance_id)            # task_repository.py:407-473
          │     SELECT work_id, resume_target_turn_id FROM task
          │     WHERE instance_id=? AND status='paused'
          │       AND suspension_reason='awaiting_answer'
          │       AND resume_target_turn_id IS NOT NULL
          │     (filters on awaiting_answer only — cascade-inherited children stamped paused_by_parent
          │      via leader decision 2 cannot be consumed by an answer aimed at the parent)
          │
          ├─ ResumeTurn(work_id, reason=None, ...)                    # turn_transitions.py
          │     ONE atomic SQL: status='paused' → 'pending', suspension_reason=NULL,
          │                     resume_target_turn_id=NULL,
          │                     last_handle_cleared_at=now
          │
          ├─ _resume_processing_background (manager.py:10469)         # asyncio.create_task
          │     re-claims the just-PENDING task; Driver A: is_retry=True under ExecutionGate
          │
          └─ Defect-3 fallback (if find_suspended_turn_for_answer returns None):
                enqueue_message(instance_id, message=answer_msg, source="api_answer_fallback")
                → fresh PROCESS_MESSAGE Task; cascade still runs but the new Task drives the turn
```

For the children paused mid-cascade (every instance in `tree_ids` with `status='paused'` and a RUNNING task that got `SuspendTurn`'d with `reason='awaiting_answer'` — note: in the current code, `pause_instance_cascade` uses a single `effective_suspension_reason` for ALL paused tasks in the tree, `instance_lifecycle.py:4928-4930`):

```text
resume_instance_cascade(instance_id)
    │
    ├─ for each paused instance in tree:
    │     ResumeTurn(work_id, reason=None)  → PAUSED → PENDING
    │     _resume_processing_background for each
    │
    ├─ watcher re-arm: find FIRED-but-unstamped watcher rows for any instance in tree
    │     (existing, instance_lifecycle.py:3392-3515) and re-stamp them
    │
    └─ post-commit: SSE status_change PAUSED → RUNNING for each instance in tree
```

**Idempotency guard (three layers, all required for the design's exactly-once contract):**

1. **CAS at T1 (architect OQ-3):** `set_answers` returns `(pack, transitioned: bool)` under `QuestionManager._lock`. A duplicate answer loses the CAS (`transitioned=False`) and the helper short-circuits to `200 resume_route:"already_delivered"` — SSE, events, resume, and Defect-3 are all skipped.
2. **`find_suspended_turn_for_answer` filter:** the finder requires `status='paused' AND suspension_reason='awaiting_answer' AND resume_target_turn_id IS NOT NULL` (`task/repository.py:407-473`). After `ResumeTurn` clears the handle (`turn_transitions.py:285-292`), the finder returns `None` — the T1 winner's path is now safe from concurrent re-entry.
3. **Defect-3 fallback (only on T1 winner):** if the finder returns `None` for any reason (handle lost via crash, etc.), the T1 winner's path falls through to `enqueue_message(..., source="api_answer_fallback")` per `routers/instances.py:1188-1228` and delivers the answer as a fresh user message. **T1 losers do NOT hit this fallback** — they're short-circuited at layer 1.

### 6.2 Children handling

When a child of the asker is itself paused awaiting an answer, the cascade logic in `resume_instance_cascade` re-resumes them silently (no Q↔A message, `silent=True` per the `messages.py:198-249` PAUSED-branch pattern, mirrored at `instances.py:1156-1162`). Each child continues from its own LangGraph checkpoint with no new tool call. This is the existing behavior; the design does not change it.

### 6.3 Pending reports while paused

Mid-flight reports emitted while an instance is paused (e.g., a worker reports progress to the leader just before the leader pauses awaiting an answer) sit in `message_queue` with their PROCESS_MESSAGE Task in PENDING. The pause protection from `report-lane-decoupling.md` §1.6 (added to `claim_pending_task`'s WHERE: `instance_id NOT IN (SELECT instance_id FROM instances WHERE status IN ('paused','terminated'))`) keeps them PENDING. On answer + resume, `notify_work()` is fired (`report-lane-decoupling.md` §1.6 final bullet) and they are claimed in created_at order. **No new code** — design inherits the explicit pause gate from the report-lane work.

### 6.4 Watcher re-arm

Existing `resume_instance_cascade` already re-arms FIRED-but-unstamped watcher rows for instances in the tree (`instance_lifecycle.py:3392-3515`). No new code.

---

## 7. Agent-Side Tool Contract (mandatory §5)

### 7.1 `ask_questions` additions (today: SSE only, pre-pause)

Today `ask_questions` (`question_tools.py:315-439`) does three things: (1) store pack, (2) emit SSE, (3) set pause flag. After this design it does five things: (1) store pack + stamp `question_pack_id` into `instance_metadata`, (2) emit SSE, **(3) emit `EventKind.QUESTION_REQUESTED` via EventBus**, **(4) `notify_work_watchers` with `status="question_requested"`**, (5) set pause flag.

Steps (3) and (4) are the load-bearing additions. The tool's external contract (signature, return value, paused-after behavior) is unchanged — agents still call `ask_questions([...])` and see the same `"Asked the user: Q1: ... The instance will pause until the user answers."` echo.

### 7.2 New `mid_flight_report` tool

```python
# daemon/tools/midflight_report.py — NEW
@register_tool_category("midflight")
@tool
async def mid_flight_report(
    summary: str,                                    # ≤500 chars, required
    details: str = "",                               # ≤8KB, optional
    level: Literal["info","warning","decision"] = "info",
    decision_required: bool = False,
) -> str:
    """Surface progress or a decision to the job watcher without pausing.

    Fires a non-blocking MIDFLIGHT_REPORT event:
      - via LiveEventHub to the FE (banner; non-modal)
      - via work_notifier to any job watcher (jober, orchestrator) — the
        watcher sees "[JOB_EVENT] Job {work_id}... midflight_report ⟳"
        in its context, can relay to the human, and is NOT blocked on a reply.
    Does NOT pause. For blocking, use ask_questions instead.
    Returns: "Reported. N watcher(s) notified."
    """
```

This is a NEW tool — registration in `_tool_registry.py` `"midflight": "daemon.tools.midflight_report"`, wired in `daemon/tools/instance.py:create_instance_tools`. **Not** in `INNATE_SKILL_TOOL_CATEGORIES` — opt-in via `tools.allow` in each agent's `meta.json`. Initially enabled on: `leader`, `developer`, `coder`, `tester`, `devops` (the agents most likely to surface progress during long-running work).

### 7.3 Orchestrator agents receiving + relaying

The orchestrator (`job-orchestration/skill.md`) already parses `[JOB_EVENT] Job {work_id}... {status}` headers. This design **extends the status action table** (the table at `skill.md:148-158`):

| Status | Action (existing) | Action (extended) |
|---|---|---|
| `completed ✓` | Record result, proceed | unchanged |
| `failed ✗` | Check error type, retry | unchanged |
| `paused ⏸` | Decide resume / terminate / cancel | unchanged |
| `in_progress ⟳` | (existing) Progress line | unchanged |
| **`question_requested ❓`** | (NEW) Extract `question_pack` payload from `Result:` line (orchestrator-side parser unchanged — already extracts `Result:` per `skill.md:194-204`; the pack dict is the result_summary field of the non-terminal `[JOB_EVENT]`), relay to human in chat (chat source already routes to the user), wait for human reply, then call `POST /api/jobs/{work_id}/answer` with the answers (job-id extracted from the `Job {work_id}...` header; `question_pack_id` echoed in the body for the T1″ correlation guard). |
| **`answer_received ✓`** | (NEW) Log; the asker is now resuming. **Informational only — the watcher row SURVIVES this notification** (preserved for the eventual terminal `[JOB_EVENT]`). The non-terminal branch in `work_notifier.py:417-447` does NOT call the atomic CAS DELETE...RETURNING (`work_notifier.py:420-424`) — that CAS only fires on terminal statuses. The rows persist on disk; the next terminal status change will find them still there. (The plan's earlier reference to `work_notifier.py:162-173` was a docstring, not the code — corrected.) |
| **`midflight_report ⟳`** | (NEW) Forward to chat source as a non-modal update (the chat adapter decides display). Do NOT block; do NOT call `/answer`. |
| **`stuck_awaiting_answer ⏳`** | (NEW) Log at WARNING; if the orchestrator is the question's author (i.e., this is the orchestrator's own paused instance, see `wedge_chain`), it relays to the human AGAIN with a `(_reminder)` suffix; otherwise it forwards to its own chat source as a visible ping. |
| **`child_question_still_pending ⚠`** | (NEW) A child of a just-resumed parent still has its own pending question pack. Orchestrator may choose to (a) relay the child's question to the human as a separate concern, (b) ignore (the child is RUNNING and reachable via `POST /api/jobs/{child_work_id}/answer`), or (c) terminate the child and restart with the parent's new context. Default: surface to chat; do not block. |

The skill.md action table is updated to include the **four new work_notifier statuses** (plus the fifth EventKind `CHILD_QUESTION_STILL_PENDING` which is dispatched directly via EventBus + LiveEventHub but never enters the work_notifier status map). The parser is unchanged — it already extracts `job_id`, `status`, `Result:`, `Error:` lines (`skill.md:194-204`).

**Why `watch_job`-style subscription is enough:** the orchestrator calls `watch_job(work_id, events=[...])` at job creation. Today `events` filters terminal events (`watch_job` tool at `daemon/tools/job_queue.py:1659-1793`). We extend the accepted-events vocabulary to include `question_requested`, `midflight_report`, `stuck_awaiting_answer`, `answer_received`. The `notify_watchers` filter (`watcher_models.ALL_WATCHABLE_EVENTS`) is the only knob.

**Why also EventBus:** orchestrators that didn't `watch_job` (e.g., chat adapters translating events back to the source) need a global hook. `EventBus.create_event` broadcasts to global subscribers (`event_bus.py:317-352`); an external-source adapter can subscribe via `event_bus.subscribe_all(adapter_id)`.

---

## 8. Failure Modes (mandatory §6)

### 8.1 Watcher gone / terminated before answer

The answer path is **watcher-independent**: the pack lives on the **asker**, not the watcher; the new job-addressed route resolves `work_id → instance_id` via `WorkResolver.resolve_work` (`work_resolver.py:1000-1053`), which is Task-by-work_id + JobItem-by-PK — purely read-only, independent of any watcher.

If the orchestrator's instance is terminated between emission and answer:

- The watch row is in `job_watchers` but the watching instance is `TERMINATED`.
- `notify_work_watchers` iterates watchers and calls `enqueue_message` per watcher (`work_notifier.py:477-481`). For a TERMINATED instance, `enqueue_message` **revives** the instance (`instance_messaging.py:1904-1915`; attestation reset only for user/HUMAN-origin sources, `:1934-1975`). Only a **deleted** watcher row strands (warning `:1983`); a TERMINATED instance is brought back to life by the message.
- `reconcile_terminal_watches` runs at next startup (`job_queue_service.py:460-523`, boot in `api.py:1128`) but only for **terminal** work units — question rows are held by design (`work_notifier.py:164-172`), so a non-terminal `question_requested` row is never garbage-collected.

For our case, the question still has `instance_id` of the asker (the L2 asker, not the watcher). The answer endpoint resolves `work_id → instance_id` independently. The watcher being gone does not affect answer delivery.

**Escalation reach when nobody watches:** at `emission_index=3` the wedge-guard fan-out includes `NotificationBroadcaster` **unconditionally** (sibling of root-completion at `instance_lifecycle.py:2060-2082`), so the escalation reaches the operator's UI even when every `watch_job` row has been GC'd. Without this fan-out, escalation is logs-only.

**Minimal new guard:** none — the existing watcher revival + restart reconciliation handles it.

### 8.2 Multiple simultaneous questions

`QuestionManager.set_question_pack` already rejects a second pending pack (`question_manager.py:240-246` — returns `None`, tool returns the verbatim error). **No two pending packs for the same instance.** Across instances, each emits its own event; orchestrators handle multiple events on the same work_id as separate deliveries (existing pattern at `work_notifier.py:135-188`).

### 8.3 Question while children running

`pause_instance_cascade` cascades to all running/idle/waiting_children instances in the tree (`instance_lifecycle.py:4933-4951`). Children pause with `effective_suspension_reason='awaiting_answer'` (same as parent — `instance_lifecycle.py:4927-4930`). On answer, all are resumed silently (`messages.py:198-249` pattern, `instances.py:1156-1162`).

**Per OQ-2 (architect recommendation §2 / leader decision 2):** the mechanism that forces the decision is that `resume_instance_cascade`'s db-sync selects **every** paused task in the tree — `WHERE instance_id IN tree_ids AND status='paused'`, no suspension-reason filter (`instance_lifecycle.py:5486-5535`) — and `ResumeTurn` nulls `suspension_reason` + `resume_target_turn_id` on each (`turn_transitions.py:329-330,373-374,417-418`). A child paused for **its own** question (its own `awaiting_answer` handle, own pack in `QuestionManager._packs`) would be resumed alongside the parent and **its handle is WIPED while its pack stays `pending` in RAM**: task row unfindable by `find_suspended_turn_for_answer`, pack unrejectable by the duplicate-pending guard (`question_manager.py:240-246`), orchestrator unaware.

**Decision (B):** emit `CHILD_QUESTION_STILL_PENDING` post-commit in `_resume_cascade_db_sync` (after the commit at `instance_lifecycle.py:5582`), for each resumed instance whose pack is still `pending`. Detection requires a new `QuestionManager._get_pending_packs_for_instances(tree_ids)` read API under the existing `_lock` (`question_manager.py:212`); for each match, emit one event on the same **four push lanes** as `stuck_awaiting_answer` (EventBus — `event_bus.py:155-191`; LiveEventHub SSE — `live_event_hub.py:384-426`; `notify_work_watchers` non-terminal — `work_notifier.py:417-447`; NotificationBroadcaster unconditional — `notification_broadcaster.py:19`, only when escalated). For the standard non-escalated path, three lanes fire (NotificationBroadcaster is reserved for `emission_index=3`):

```json
{
  "child_instance_id":    "uuid",
  "child_job_id":         "uuid",
  "child_asker_agent_id": "leader",
  "question_pack_id":     "uuid",
  "paused_at":            "iso-8601",
  "wedge_chain":          ["<parent_id>", "<this_id>"],
  "paused_by_parent":     true
}
```

Informational only — no pause. Recovery is well-defined even with the handle wiped: the child is RUNNING, so `POST /api/jobs/{child_work_id}/answer` falls to the Defect-3 fresh-message branch and the answer reaches the child's graph normally.

**Rejected alternatives** (architect §2):
- **(A) Silent** — the orphan leak above. Task row unfindable, pack unrejectable, orchestrator unaware.
- **(C) Refuse parent resume** until child questions answered — over-blocking; the parent's stop-gate answer should not be hostage to a child's question.
- **Preserve-handle variant** (skip `ResumeTurn` for reason-mismatched tasks) — sounder semantics but risks re-wedging the parent on `waiting_children` when it resumes expecting children to progress, and deepens changes to shared cascade code. Revisit only if (B) proves insufficient.

**Complementary hardening (leader decision 2 — adopt now):** cascade-inherited children get a distinct `suspension_reason = 'paused_by_parent'` at pause time. The cascade-stamping site at `_pause_cascade_db_sync` passes a different `effective_suspension_reason` for cascade artifacts (children whose own turn was not directly paused by the asker's question). This means `find_suspended_turn_for_answer` (which filters only `suspension_reason='awaiting_answer'`) **cannot consume a cascade artifact mid-pause** — an answer aimed at the parent can never partially resume a child via T2a. Cascade resume is reason-agnostic so children still resume on answer. Modest scope: one new `SuspensionReason` enum value + one stamping site.

**R3 (metadata clear) — moved off `set_question_pack`:** `set_answers` (CAS branch AND `transitioned=False` no-op branch) and `clear_question_pack` both delete `instance_metadata['question_pack_payload']` + `['question_pack_id']`. Clearing in `set_question_pack` (the create site) leaves a stale-rehydration window where an answered pack can be resurrected on boot.

### 8.4 Late answer after instance reached a non-running terminal state

The flow for a human answer that arrives AFTER the asker already reached COMPLETED / TERMINATED / ERROR / FAILED — **per leader decision 1 (binding)** — is:

```text
Pre-T2b status check (new guard in shared helper):
    │
    ├─ asker.status == COMPLETED → 410 ANSWER_TARGET_TERMINAL
    │     rationale: stale pack; the job is finished; reviving to deliver would be incorrect
    │     (today's `enqueue_message` to a COMPLETED instance does silently revive —
    │     `instance_messaging.py:1904-1915` — which is a separate existing misbehavior
    │     this design intentionally tightens)
    │
    ├─ asker.status == TERMINATED → 410 ANSWER_TARGET_TERMINAL
    │     rationale: identical — TERMINATED is a deliberate stop, not a recoverable crash
    │     (today's behavior revives; tightened intentionally)
    │
    ├─ asker.status in (ERROR, FAILED) → REVIVE + deliver the answer
    │     rationale: ERROR/FAILED is the genuine recovery affordance; the handle is gone
    │     anyway (task.repository.py:435-439: failed/errored tasks clear suspension_reason)
    │     so the answer lands via the Defect-3 fresh-message branch (T2b)
    │     response: 200 resume_route:"revived_error_target" — FE/operator sees what happened
    │     NO revive-budget consumption (mirrors user-API revive semantics; answer path is
    │     user-origin, not a self-orchestration revive)
    │
    └─ asker.status in (RUNNING, IDLE, PAUSED, WAITING_CHILDREN) → existing T2a/T2b flow
```

**Why the COMPLETED/TERMINATED tightening is intentional:** today's behavior silently restarts a finished asker (`instance_messaging.py:1904-1915`) when an answer arrives after the job was already wrapped. This is a latent data-loss hazard — the new instance has no LangGraph checkpoint, so it runs from scratch. Tightening to explicit `410 ANSWER_TARGET_TERMINAL` prevents this. The existing instance-addressed route inherits the tightening via the shared `_answer_questions_via_instance` helper.

**FE dependency finding (MAJOR-3, grep 2026-09-21):** no frontend or wizard code relies on the silent-revive. The wizard (`frontend/src/app/components/question-wizard/question-wizard.component.ts`) is NON-OPTIMISTIC — `submit()` waits on the API, hides via the `question_pack` SSE (`status='answered'`), and on ANY HTTP error surfaces `err?.error?.message` in a toast while keeping the wizard open for retry; the FE's only answer surface is `api.service.ts:answerQuestions` → `POST /api/instances/{id}/answer` (no job-addressed call existed). The new `410 ANSWER_TARGET_TERMINAL` / `400 QUESTION_PACK_MISMATCH` bodies therefore render through the wizard's EXISTING error handler with zero FE changes; the `already_delivered` 200 is a success shape the wizard already tolerates. Released in the changelog entry (see release notes).

**Idempotency:** the CAS at T1 closes §8.5's race before this guard runs — a duplicate answer is rejected at T1′ (`already_delivered`) before status checks.

### 8.5 Answer while asker already resumed (race) — CAS at T1 closes it

Two parallel answer requests, **with the OQ-3 CAS in place**:

```text
Request A:  set_answers → pending → answered (CAS wins); transitioned=True; T1 emits events
            find_suspended_turn_for_answer → None (the CAS already consumed via T2a)
            Defect-3 fallback fires: enqueue_message as fresh message with the same answer payload
            → response: resume_route="enqueue_as_fresh_message" OR "answer_gate_existing_turn"

Request B:  set_answers → status is already 'answered'; (pack, transitioned=False) under lock
            shared helper SKIPS everything (SSE, events, resume, Defect-3)
            → response: 200 {resume_route: "already_delivered"}
```

**Exactly-once is now the contract.** The CAS under `QuestionManager._lock` is the single arbitrating primitive: the second caller sees `transitioned=False` and gets the no-op 200. The T1″ pack-correlation guard (when body carries `question_pack_id`) closes the stale-answers hijack (T6): an old-answers retry landing after resume+re-ask would otherwise stamp the NEW pack `answered` with OLD answers.

**Backward compat:** the existing `POST /api/instances/{id}/answer` route gains the same CAS via the shared `_answer_questions_via_instance` helper. Today's behavior of returning `200 resume_route:"answer_gate_existing_turn"` for the first answer and the silent fallthrough for the second is replaced by the cleaner `already_delivered` for the second — strictly an improvement.

The deeper conditional `UPDATE paused→resuming … RETURNING` inside `find_suspended_turn_for_answer` is **optional hardening** — recommended but not required, since the entry-CAS makes the TOCTOU unreachable via the answer routes.

### 8.6 Event emission failure must not break the asker

Every emit step in §4.1 / §4.2 / §4.3 is wrapped:

- **SSE (`live_hub.stream_*`)** — already best-effort; `live_event_hub.py:_stream_to_connections` logs and drops on full queue (`live_event_hub.py:104-110`).
- **`event_bus.create_event`** — wraps `_event_repo.create_event` in `asyncio.to_thread` (`event_bus.py:174-181`); raises are caught in `notify_watchers` style at the call site (`work_notifier.py:486-500`). **New call sites must wrap in try/except + log at WARNING.** Pattern: `try: await event_bus.create_event(...) except Exception as e: logger.warning(...)`.
- **`notify_work_watchers`** — already exception-safe (`work_notifier.py:486-500`); worst case is "watcher missed the event" which is reconciled at next startup.

**Crucially:** the EventBus + work_notifier calls in §4.1 happen BEFORE the pause flag is set (§4.1 step [5]). If they all fail, the question is still stored, the SSE already fired, the pause still happens. The asker pauses normally; the orchestrator may miss the event; the human can still answer via the FE wizard (which has the SSE-fed pack).

### 8.7 Deep paused chains — wedge guard

The `stuck_awaiting_answer` event + one-shot future wake (per §4.3) is the wedge guard. Trigger conditions:

| Condition | Detection | Action |
|---|---|---|
| Single instance paused awaiting answer > 30 min | Successor one-shot Task row at `now + 1800s` (default `STUCK_HEARTBEAT_AFTER_SECONDS` per §4.4) — **re-arm clause** fires ONLY when the no-op predicate is false (asker still has the `awaiting_answer` handle AND asker instance is non-terminal); if no-op predicate fires (handle already cleared, or asker terminal) the heartbeat self-cancels without minting a successor | Emit `stuck_awaiting_answer` event with `emission_index=2`. Orchestrator logs at WARNING; relays to chat with `_reminder` suffix. |
| Chain depth ≥ 2 paused awaiting answers | Each pause emits its own stuck event with `wedge_chain=[root, ..., this]` (computed at emission time via `instance_repository.get_cascade_tree_ids` of each paused ancestor — adds ONE DB read per emission, cheap). Orchestrator surfaces the wedge chain in chat. | Same as above; orchestrator may choose to cancel and re-issue at a higher level. |
| 3 successive emissions (`emission_index=3`, **~60 min total** — t=0 + 1800s + 3600s per §4.3 arithmetic) | `HeartbeatEmitStuckProcessor` (a) emits `stuck_awaiting_answer` with `emission_index=3`, (b) calls **`manager.terminate_instance` (`manager.py:9401`) DIRECTLY**, (c) **mints NO successor** (chain terminated — re-arm clause short-circuits when `emission_index >= 3`). In addition to `work_notifier` + `EventBus`, the escalation fans out **unconditionally to `NotificationBroadcaster`** (`notification_broadcaster.py:19`; root-level precedent `instance_lifecycle.py:2060-2082`) so operators see it even when nobody is watching. NOT via the observer's finalize framing (do not make `JobFeedbackObserver` inference load-bearing — that drifts into the deferred error-lane defect, `error_reporting.py:222-296`). The wedge's topmost instance is terminated; the cascade lives inside `terminate_instance` itself (no separate `terminate_instance_cascade` exists). |

**No polling, no timers.** Every emission is a one-shot Task row with `next_retry_at`. The worker claim picks each up exactly once.

### 8.8 EventBus outbox durability

`event_bus.create_event` persists to the `event` table before broadcasting (`event_bus.py:174-181`). On daemon restart, the `event` table is the durable trail — orchestrators can `SELECT * FROM event WHERE instance_id=? AND kind='question_requested' ORDER BY created_at DESC` if they need to re-derive (not a normal path; recovery uses `[JOB_EVENT]` re-arming via `work_notifier`'s reconcile). **No new durability machinery needed.**

---

## 9. Test Strategy (mandatory §7)

### 9.1 Acceptance list — mapped to tests

| ID | Acceptance | Test |
|---|---|---|
| **(a)** | Question surfaces as event to watcher | `test_midflight_qa.py::test_question_surfaces_to_watcher` — leader calls `ask_questions`; jober has `watch_job(work_id)` registered; jober's context receives a `[JOB_EVENT] Job ... question_requested ❓` line within 100ms; the `Result:` line carries the `question_pack_payload`. |
| **(b)** | Answer resumes asker with answer in-context | `test_midflight_qa.py::test_answer_resumes_asker` — same setup; HTTP `POST /api/jobs/{work_id}/answer` with answers; assert asker's instance transitions PAUSED → RUNNING within 200ms; assert the next assistant turn echoes the answer text (F7 compaction safety). **Delegated coverage (fix pass MINOR-4):** the real PAUSED→RUNNING resume chain (awaiting-answer handle consumption + graph re-entry) is covered by `tests/unit/test_answer_gate_resume_chain.py` plus the tester-phase integration check; in THIS suite `resume_processing_job` is an intentional `AsyncMock` — unit isolation, the helper's routing/guard logic is the unit under test, not the manager's resume machinery. |
| **(c)** | Report event non-blocking | `test_midflight_qa.py::test_report_non_blocking` — leader calls `mid_flight_report(summary="50% done")`; instance stays RUNNING (no pause flag set); jober receives `[JOB_EVENT] Job ... midflight_report ⟳` line; orchestrator can call another tool in the same turn. |
| **(d)** | No polling introduced | **Real test functions** (NIT-16 — implemented in `tests/job_queue/test_midflight_qa.py::TestNoPollingIntroduced`): (1) no `asyncio.sleep` in `daemon/services/midflight_qa.py` / `daemon/tools/midflight_report.py` / `daemon/tools/question_tools.py`; (2) the `HeartbeatEmitStuckProcessor` CLASS BODY (via `inspect.getsource`) has no `asyncio.sleep` / `time.sleep` / `while True`; (3) `midflight_qa.py` has no `while True`; (4) `EVENT_STREAM_POLL_INTERVAL` consumers pinned to the pre-change set (on this lineage the constant lives only in `constants.py` — `jobs_streaming.py` hardcodes the 2s literal); (5) the `task/repository.py` carve-out site asserts the type-scoped disjunct + bind literal are present and the claim SQL contains no `while True`; (6) the instance_lifecycle stuck-mint region has no loop construct. |
| **(e)** | Completed-event Result bodies still work | `test_midflight_qa.py::test_completed_event_regression` — full job lifecycle: jober creates job → leader runs → completes; assert the terminal `[JOB_EVENT] Job ... completed ✓` carries the `Result:` line with the leader's final assistant text. Re-uses the v0.13.9 release-gate fixture. |

### 9.2 Failure-mode tests

| Failure | Test |
|---|---|
| Watcher gone before answer | `test_watcher_terminated_mid_flight` — orchestrator terminates; human still answers via FE wizard; asker resumes. |
| Multiple simultaneous questions | `test_duplicate_pending_pack_rejected` — leader calls `ask_questions` twice; second call returns `"Already have a pending question pack..."`. |
| Question while children running | `test_question_with_running_children` — leader pauses; 2 children are paused via cascade; answer resumes all 3 silently for children + answer for leader. |
| Duplicate / late answer | `test_duplicate_answer_cas_noop` — POST /answer twice in rapid succession; **first call wins the T1 CAS** (`transitioned=True`), emits `QUESTION_ANSWERED` event, resumes asker normally with `resume_route="answer_gate_existing_turn"` or `"enqueue_as_fresh_message"`; **second call loses the CAS** (`transitioned=False`), helper short-circuits to `200 {resume_route:"already_delivered"}` — NO SSE emitted, NO `QUESTION_ANSWERED` event, NO `resume_processing_job`, NO Defect-3 fallback. Assert the asker receives the answer exactly ONCE. |
| **CAS-winner / lost-handle race** | `test_cas_winner_lost_handle` — POST /answer A wins CAS + begins resume; the `_resume_processing_background` task (`manager.py:10469`) is mid-flight (asyncio.create_task scheduled, but `ResumeTurn` has not yet completed on the asker Task row). POST /answer B arrives during this window. Assert B's `find_suspended_turn_for_answer` returns `None` (the handle was cleared by A's `ResumeTurn`); B falls through to the T1 CAS path (B already lost at the entry CAS before reaching the finder — the T1 CAS short-circuits to `already_delivered`). Asker receives A's answer exactly once; B is a no-op. Confirms the entry-CAS closes the TOCTOU window that `find_suspended_turn_for_answer`'s read-not-claim contract would otherwise leave open. |
| Event emission failure | `mock_event_bus.create_event.side_effect = RuntimeError` — asker still pauses; SSE still fires; warning logged; orchestrator may miss event but FE has it. |
| Wedge guard | `test_wedge_guard_one_shot` — fake `now + 1800s`; assert the one-shot Task row fires and emits `stuck_awaiting_answer`; orchestrator log shows `_reminder` relay. |
| Durability after daemon restart | `test_daemon_restart_keeps_pack` — pause instance; kill daemon; restart; assert `QuestionManager._packs[instance_id]` is rehydrated from `instance_metadata`; answer endpoint succeeds (returns 200 instead of 404). |

### 9.3 Manual verification checklist

- Chat source adapters (Slack, Discord, Telegram): orchestrator can relay `question_requested ❓` to the chat source and the chat adapter accepts the human reply as the answer body.
- `[JOB_EVENT]` parser in orchestrator agent: confirmed to extract the pack payload from `Result:` line (manual inspect of one transcript).

---

## 10. Risks / Open Questions

### Hard risks

- **R1 — `notify_work_watchers` non-terminal path preserves watcher rows.** Currently the only non-terminal status is `in_progress` (used for long-running jobs). Adding **four new non-terminal work_notifier statuses** (`question_requested` ❓, `answer_received` ✓, `midflight_report` ⟳, `stuck_awaiting_answer` ⏳) means orchestrators receive multiple `[JOB_EVENT]` lines per job. The fifth EventKind, `CHILD_QUESTION_STILL_PENDING` ⚠, is emitted only via EventBus + LiveEventHub (NOT through the work_notifier status map — it surfaces through the same watcher's queue but with `notify_work_watchers` called separately, OR is dispatched directly via `instance_lifecycle` post-commit; the `work_notifier` non-terminal branch at `:417-447` handles it identically when routed through that path). **Mitigation:** the orchestrator's skill.md action table treats each new status as a single fire-and-forget (no state); long-running jobs already get `in_progress` events without issue. **Verified:** the notifier's non-terminal branch (`work_notifier.py:417-447`) does not claim — rows persist until the terminal event, which is the desired semantics here.
- **R2 — Heartbeat Task row could be claimed by a worker that's not the asker.** The Task row's `instance_id` IS the asker; `claim_pending_task` excludes PAUSED instances (`repository.py:1931-1952` — the pause gate; the prior `:1248-1254` cite was stale line numbering in the prior design.md revision). But the heartbeat Task's purpose IS to observe the paused asker — it must be claimable while the asker is PAUSED. **Mitigation (per architect §1.4):** wrap the existing pause gate with a type-scoped disjunct:
  ```sql
  AND (
      task.task_type = :heartbeat_emit_stuck
      OR instance_id NOT IN (
          SELECT instance_id FROM instances
          WHERE status IN (:status_paused, :status_terminated)
      )
  )
  ```
  Bind the literal `"heartbeat_emit_stuck"` (same convention as `:process_message_type` at `:1984`). **Deliberately broad over TERMINATED** — the processor's terminal no-op IS the cleanup path; makes invisible-row orphans impossible by construction. **Concurrency gate untouched** (`repository.py:1866-1930` — `status='running'`-only) — it is what makes the resume race deterministic.
  **System-lane alternative rejected** on five grounds (architect §1.3): `Task.instance_id` non-nullable (`task/models.py:178`); `NULL NOT IN (subquery)` is UNKNOWN; mission-bound row couples to the concurrency gate (orchestrator busiest when wedges happen); JobItem future-dating rides the 60s `RetryScheduler` poll (`retry_scheduler.py:70,84,162`); no other restart-survivable one-shot substrate exists. **Asker-bound Task + type-scoped claim-gate carve-out is the only structurally legal mechanism.**
- **R3 — `instance_metadata` JSONB payload grows.** Each question pack embeds in metadata; a long-long-running session could accumulate many answered packs. **Mitigation (corrected per architect §5/R3 fix — architect amendment §5):** the metadata clear happens on **answer consumption** (`set_answers` / `clear_question_pack`), NOT on `set_question_pack` (the create site). Clearing in the create site leaves a stale-rehydration window where an answered pack could be resurrected on boot and re-trigger emissions. The pack payload is short-lived — written at ask time, deleted at answer time, regardless of T1′ outcome.
- **R4 — EventBus global subscribers see ALL events.** Adding five new `EventKind` values means every chat adapter must filter. **Mitigation:** existing adapters already filter by `event_type` (the `kind` field is propagated as `event_type` in `event_bus._broadcast_to_global:337`). New kinds are explicitly ignored unless the adapter adds a handler. The FE frontend gets the same filtering via `LiveEventHub` (which is per-instance). **Open question OQ-5:** should the chat adapter team be given a heads-up doc?
- **R5 — Pre-fix DEFECT A (error-lane parent-before-report ordering) is unchanged.** This design emits `QUESTION_REQUESTED` BEFORE the pause, similar to the existing SSE. If the parent's report ordering regresses, the question event ordering does NOT regress — events go through `event_bus.create_event` which is a separate row from `child_reports`. **No new coupling.**

### Open questions

- **OQ-1 — Pack rehydration scan.** Resolved per architect §2: **NO index change.** Boot scan uses `idx_task_status_type_created` (`20260906_192100_add_task_claim_wake_lane_index.sql:58`) — its `(status, task_type, created_at)` composite covers `WHERE status='paused'` via the index prefix; Python post-filter on `suspension_reason='awaiting_answer'`. Cost is O(paused tasks) — bounded by active orchestration depth (tens, not thousands), once at boot, milliseconds. **Never scan by `suspension_reason` alone** — would force a full table scan. Adding `suspension_reason` to the composite would dilute selectivity for the claim path (the index's real job); a partial index on `suspension_reason='awaiting_answer'` is redundant with the status prefix. A one-shot boot pass is not a periodic scan — constraint-compliant.
- **OQ-2 — Child question still pending.** Resolved per architect §2 / leader decision 2: **(B) Emit `CHILD_QUESTION_STILL_PENDING`** post-commit in `_resume_cascade_db_sync` (full spec in §8.3 + §6.1). Detection requires a new `QuestionManager._get_pending_packs_for_instances(tree_ids)` read API under lock. Rejected alternatives documented: (A) silent — orphan leak via handle-wipe at `instance_lifecycle.py:5486-5535` + `turn_transitions.py:329-330`; (C) refuse parent resume — over-blocking; preserve-handle variant — re-wedge on parent's `waiting_children`. **Complementary hardening (leader decision 2):** cascade-inherited children stamped `paused_by_parent` distinct reason so `find_suspended_turn_for_answer` (filters only `awaiting_answer`) cannot consume a cascade artifact mid-pause.
- **OQ-3 — Exactly-once CAS.** Resolved per architect §2 / leader decision (implicit via OQ-3 promotion to requirement): **CAS at `set_answers` is now a REQUIREMENT, not a defer.** Tuple return `(pack | None, transitioned: bool)` — `pending→answered` exactly-once, no overwrite. Loser (T1′) gets `200 resume_route:"already_delivered"`; helper skips everything (SSE, events, resume, Defect-3). Optional `question_pack_id` echo in body → `400 QUESTION_PACK_MISMATCH` on mismatch (closes the stale-answers hijack T6). The deeper conditional `UPDATE paused→resuming … RETURNING` inside `find_suspended_turn_for_answer` is optional hardening — the entry-CAS makes TOCTOU unreachable via the answer routes.
- **OQ-4 — Heartbeat Task row claim semantics.** Resolved per architect §1.5: (1) **exactly-once under concurrent claim** — the atomic claim arbitrates (scalar subquery + outer UPDATE row-lock; loser gets 0 rows, `repository.py:1608-1618`). Emission happens only in the claimed processor → once. (2) **No-op predicate (durable, not RAM)**: `find_suspended_turn_for_answer(asker)` returns `None` once the handle is consumed (`turn_transitions.py:285-292` clears `suspension_reason`/`resume_target_turn_id`) — this is the durable "answer already landed" check. **Do NOT use the in-RAM QuestionManager pack status** — gone on restart (`question_manager.py:11-13`). Also read asker instance status: terminal → silent `complete_task`. (3) **Resume race (answer at t≈1800s)**: `ResumeTurn` flips the asker's task PAUSED→PENDING atomically (`turn_transitions.py:261-330`); both rows PENDING → claim order is PROCESS_REPORT tier first, then `created_at ASC` (`repository.py:1620-1625`) → the asker's older message task claims first, goes RUNNING, and the concurrency gate blocks the heartbeat for the turn's duration; the heartbeat claims later, finds no handle, no-ops. **Deterministic, exactly-once, zero new machinery.** (Bounded interference: a claimed heartbeat can delay the asker's resumed claim by the emission duration, ~ms. Accepted.) (4) **Crash between claim and emit**: `StaleTaskRecovery` force-cancels and mints a retry child preserving `task_type`/`instance_id`/`next_retry_at` (`repository.py:4158-4310`, child kwargs `:4288-4301`) → re-claim → no-op check re-runs. **At-least-once in the crash window, exactly-once normally** — same contract as PROCESS_REPORT delivery. (5) **Re-arm clause** (per MAJOR-2): when `emission_index < 3` AND the no-op predicate is false, the processor mints exactly ONE successor one-shot Task at `now + STUCK_HEARTBEAT_AFTER_SECONDS` (1800s); when `emission_index == 3` (escalation) OR the no-op predicate fires (handle cleared, asker terminal), NO successor is minted. (6) **No cancellation hook needed** when the answer lands early: the row fires at +30min, re-checks the handle, and self-cancels. A cancel hook would be a racy new coordination point for zero benefit.
- **OQ-5 — Chat adapter heads-up doc.** R4 mitigation needs a one-paragraph note in each chat adapter's skill.md explaining the new event types it may receive and that the default behavior is "ignore if no handler". A short CR-equivalent doc would suffice.

---

## 11. References

### Internal research

- Explorer REPORT 1: `ask/answer surface` (plan-explorer-asktool, HIGH confidence) — incident 1 root cause map. Provides file:line citations for `question_tools.py:315-439`, `manager.py:962-973`, `instance_messaging.py:4406-4478`, `routers/instances.py:1053-1305`.
- Explorer REPORT 2: `event lanes + v0.13.9 Result-body fix` — LiveEventHub + work_notifier + EventBus pipeline. Provides file:line citations for `live_event_hub.py:384-426`, `work_notifier.py:101-107, 118-503`, `event_bus.py:155-191, 286-352`, `repositories/event/models.py:12-23`.
- Explorer REPORT 3: `messaging lanes + pause/resume` — `messages.py:198-249` PAUSED-branch fan-out, `instance_lifecycle.py:4873-4983` `_pause_cascade_db_sync`, `manager.py:9222-9226, 9540-9640` `find_suspended_turn_for_answer` + `ResumeTurn` chain.

### Adjacent plans

- `docs/plans/report-lane-decoupling.md` — §1.6 explicit pause gate in `claim_pending_task` (R2 mitigation depends on this). §1.1 wake workers on report creation (also reused for the wedge-guard wake).
- `.agents/shared/planning/question-tool/plan-overview.md` — Phase 1 (Backend Core) and Phase 2 (Backend API + SSE) — the question surface that this design extends.
- `.agents/shared/planning/watchover/plan-overview.md` — AD-3 (3-strikes deferred marker), AD-6 (bifurcated failure handling) — patterns reused for the wedge-guard one-shot future wake and event-emission-failure tolerance.
- `.agents/shared/planning/job-as-front-primitive.md` — the Front-Primitive plan; the answer route uses `WorkResolver.resolve_work(work_id)` for the job-addressed lookup, consistent with this plan's direction.
- `agents/_prompt_system/innate-skills/job-orchestration/skill.md:148-204` — orchestrator status action table + `[JOB_EVENT]` parser (extended in §7.3).

### Code anchors

- `daemon/repositories/event/models.py:12-23` — `EventKind` enum (extend with 4 new values: `QUESTION_REQUESTED`, `QUESTION_ANSWERED`, `MIDFLIGHT_REPORT`, `STUCK_AWAITING_ANSWER`, plus `CHILD_QUESTION_STILL_PENDING` from §8.3).
- `daemon/repositories/task/repository.py:1931-1952` — pause gate in `claim_pending_task` (the seam for the §1.4 type-scoped carve-out).
- `daemon/repositories/task/repository.py:1953-1957, :1984` — cross-system guard already type-scoped (`task_type != :process_message_type OR …`); bind-literal convention reference.
- `daemon/repositories/task/repository.py:407-473` — `find_suspended_turn_for_answer` (filters `paused` + `awaiting_answer` + `resume_target_turn_id` non-null; >1 row → ValueError; durable "answer already landed" predicate per OQ-4).
- `daemon/repositories/task/repository.py:237-262` — `create(task_type, instance_id, message_id=None)` (NO `status` / `next_retry_at` / `metadata` kwargs — Task has no metadata column, `task/models.py:160-260`).
- `daemon/repositories/task/repository.py:1608-1618, :1620-1625` — atomic claim arbitration + ordering (PROCESS_REPORT tier first, then `created_at ASC`).
- `daemon/repositories/task/repository.py:4158-4310` — `StaleTaskRecovery` retry-child minting (child kwargs `:4288-4301`); at-least-once crash window for the heartbeat row.
- `daemon/repositories/task/repository.py:1007-1046` — `list_pending_tasks_older_than` (drift-reconciler noise; backlog = exclude `heartbeat_emit_stuck`).
- `daemon/repositories/task/models.py:160-260` — `Task` model (no `metadata` column; future-dating via `next_retry_at`).
- `daemon/services/work_notifier.py:417-447` — non-terminal notify branch (CAS is SKIPPED here; watcher rows are preserved for the eventual terminal event).
- `daemon/services/work_notifier.py:357-389` — `mission_live` derivation (read-model; new kinds not added to `ALL_MISSION_TERMINAL_WATCHABLE_EVENTS`).
- `daemon/services/turn_transitions.py:261-330, :285-292, :329-330, :373-374, :417-418` — `ResumeTurn` atomic flip + handle-clear; the §8.3 mechanism that wipes the child's `awaiting_answer` handle on cascade.
- `daemon/routers/instances.py:1053-1305` — existing instance-addressed answer route (~250 lines; extract into shared `_answer_questions_via_instance` helper).
- `daemon/routers/instances.py:1097-1109` — today's mislabeled `INSTANCE_NOT_FOUND` on missing-pack (fix: helper returns proper `NO_PENDING_QUESTION` / `QUESTION_PACK_LOST`).
- `daemon/routers/instances.py:1188-1228` — Defect-3 fallback (enqueue as fresh message); unchanged semantically, now gated by T1 CAS + T1′ no-op + T3 status pre-check.
- `daemon/services/instance_lifecycle.py:4873-4983` — `_pause_cascade_db_sync` (post-commit outbox hooks for stuck event + one-shot future Task + `paused_by_parent` stamping).
- `daemon/services/instance_lifecycle.py:5486-5535` — pause-cascade db-sync WHERE (`status='paused'`, no suspension-reason filter — the §8.3 mechanism that wipes cascade-inherited child handles).
- `daemon/services/instance_lifecycle.py:5582` — `_resume_cascade_db_sync` post-commit point (where §8.3 emits `CHILD_QUESTION_STILL_PENDING`).
- `daemon/services/instance_lifecycle.py:2060-2082` — root-level `NotificationBroadcaster` precedent for `emit_question_escalation` (wedged `emission_index=3`).
- `daemon/services/work_resolver.py:1000-1053` — `WorkResolver.resolve_work` (2 indexed SELECTs over Task.work_id + JobItem.job_id; read-only; the new job-route's lookup seam).
- `daemon/services/job_recovery_service.py:926, :1488-1516` — `reconcile_drift_states` + alive-instance P1 pattern (heartbeat rows sit PENDING >300s, drift-reconciler WARNINGS; backlog = exclude type).
- `daemon/services/error_reporting.py:222-296` — deferred error-lane defect (reason NOT to route wedge-escalation through `JobFeedbackObserver` finalize framing).
- `daemon/manager.py:9401` — `terminate_instance` (escalation target for `emission_index=3` — call directly from processor, NOT via observer).
- `daemon/manager.py:8648-8657` — `c_revival` precedent for direct `Task(...)` construction with `next_retry_at` (heartbeat minting pattern).
- `daemon/manager.py:5062-5073` — durable handle columns `task.suspension_reason` + `task.resume_target_turn_id` + their role in restart survival.
- `daemon/manager.py:10469` — `_resume_processing_background` (asyncio.create_task; survives the cascade resume).
- `daemon/tools/question_tools.py:315-439` — `ask_questions` tool (extend with steps [3]+[4] of §4.1).
- `daemon/tools/job_queue.py:1659-1793` — `watch_job` (extend accepted events vocabulary; new `ALL_WATCHABLE_EVENTS` members are non-terminal by definition so the claim-skip path is automatic).
- `daemon/services/stale_task_recovery.py:285` — `next_retry_at` wake pattern (precedent for §4.3 wedge-guard substrate).
- `daemon/services/event_bus.py:155-191, :286-352` — `create_event` + `subscribe_all` / `_broadcast_to_global` (persists before broadcast at :174-181; the durability seam for `emission_index` derivation).
- `daemon/services/live_event_hub.py:384-426` — `stream_question_pack` (precedent for `stream_midflight_report`, `stream_stuck_awaiting_answer`, `stream_child_question_still_pending`, `stream_answer_received`).
- `daemon/services/notification_broadcaster.py:19, :88-125, :127-146` — `emit` / `emit_root_completion` (the pattern `emit_question_escalation` mirrors).
- `daemon/services/worker_pool.py:302, :371, :982` — claim seam (`claim_pending_task`), condition-timeout claim loop (≤3s wake latency), `schedule_retry` future-dating precedent.
- `daemon/services/instance_messaging.py:1904-1915` — `enqueue_message` to TERMINATED instance REVIVES it (the §8.1 mechanism; §8.4 intentionally tightens for the answer path).
- `daemon/services/instance_messaging.py:1934-1975` — attestation reset (user/HUMAN-origin only; §8.1).
- `daemon/repositories/job_queue/watcher_models.py:33-35` — `ALL_MISSION_TERMINAL_WATCHABLE_EVENTS` (do NOT add the new kinds here; new kinds are non-mission-terminal — mission liveness derives from the stored WorkRecord state, never the input status).
- `daemon/constants.py:30, :540-680` — `EVENT_STREAM_POLL_INTERVAL` (do NOT extend; new lanes are push), `RESERVED_SOURCE_PREFIXES` (no new prefix needed).
- `daemon/retry_scheduler.py:70,84,162` — 60s poll loop (one of the §1.3 grounds for rejecting the JobItem future-dating alternative).

### Out-of-band invariants the design respects

- **C2 marker-window** (`graph.py:8600-8680` + `manager.py:4263`) — the deferred-pause pattern: `question_pause_node` sets the marker; the actual cascade fires from the post-graph completion path. The §4.1 event emits happen at tool-call time, BEFORE the conditional edge routes to the pause node. No new coupling to the marker.
- **DEFECT A (error-lane parent-before-report ordering)** — not on the path of this design. Errors propagate via `error_reporting.py:538` (`_emit_terminal_via_bus`); questions are unrelated.
- **S3 invariants (claim eligibility)** — `claim_pending_task`'s explicit pause-exclusion (`report-lane-decoupling.md` §1.6, mirrors `job_processor.py:633` skip) keeps the heartbeat Task row PENDING until the asker resumes or the worker bypasses for heartbeat-typed rows (R2 mitigation).
- **v0.13.9 completed-event Result-body fix** — `report-lane-decoupling.md` §1.5 (verify single finalize path) + the `_finalize_job` Result body carried by `notify_watchers` (`work_notifier.py:466-468`) are not modified by this design. The **four new non-terminal work_notifier statuses** do NOT enter the finalize path; the fifth EventKind (`CHILD_QUESTION_STILL_PENDING`) is also non-terminal and out of the finalize path.

### Migration

- At merge time, copy this file to `docs/plans/midflight-qa-channel.md` (sibling of `report-lane-decoupling.md`). The implementation phases are intentionally left out of this design doc — they belong in a separate `phase{N}-plan.md` series under `.agents/shared/planning/midflight-qa-channel/`, mirroring `report-lane-decoupling.md`'s 1.1–1.8 + 2.1–2.4 + 3 (deferred). The design's §4.4 + §6 enumeration is sufficient scoping for the planner to write those phase files.

---

## 12. Glossary

| Term | Definition |
|---|---|
| **asker** | The instance that called `ask_questions`. Pauses awaiting an answer. |
| **watcher** | An instance that called `watch_job(work_id)` on the asker's work_id. Receives `[JOB_EVENT]` lines. |
| **orchestrator** | A watcher with mission-coordination tools (`job-orchestration` skill); relays `[JOB_EVENT]` lines to a chat source. |
| **answer route** | HTTP endpoint that accepts the human's answer (`POST /api/instances/{id}/answer` existing; `POST /api/jobs/{work_id}/answer` NEW). |
| **question pack** | A bundle of questions stored in `QuestionManager._packs[instance_id]` (in-memory) + `instance_metadata['question_pack_payload']` (durable JSONB shadow). |
| **stuck / heartbeat event** | `STUCK_AWAITING_ANSWER` event emitted at pause time + on a one-shot future-dated Task row, indicating the asker is paused awaiting an answer for `waiting_for_seconds`. |
| **wedge** | A chain of paused-`awaiting_answer` instances with no human reply. The wedge guard emits escalating stuck events. |
| **paused_by_parent** | Distinct `suspension_reason` value stamped on cascade-inherited children at pause time (leader decision 2). `find_suspended_turn_for_answer` filters only `awaiting_answer`, so cascade artifacts can never be consumed by an answer aimed at the parent. |
| **child_question_still_pending** | `CHILD_QUESTION_STILL_PENDING` event emitted post-commit in `_resume_cascade_db_sync` for each resumed instance whose pack is still `pending` (architect OQ-2 / decision (B)). |
| **CAS (in this design)** | The atomic state transition under `QuestionManager._lock`: `pending → answered` exactly-once. `set_answers` returns `(pack, transitioned: bool)`; loser (`transitioned=False`) is the `already_delivered` no-op path. |

---

## 13. Decisions Log (binding)

All open architectural questions and pending leader decisions are now resolved. Each row records the verdict + the one-line rationale + its source. Date: 2026-09-21.

| # | Topic | Decision | Rationale | Source |
|---|---|---|---|---|
| 1 | R2 — Heartbeat attach-point | **Asker-bound Task + type-scoped claim-gate carve-out.** Broad over PAUSED+TERMINATED. Concurrency gate untouched. | The system-lane alternative fails on five independent grounds: `Task.instance_id` non-nullable (`task/models.py:178`); `NULL NOT IN (subquery)` evaluates UNKNOWN; mission-bound couples to the concurrency gate (orchestrator busiest during wedges); JobItem future-dating rides the 60s `RetryScheduler` poll (`retry_scheduler.py:70,84,162`); no other restart-survivable one-shot substrate exists. | Architect recommendation §1.1–1.3 (date 2026-09-21) |
| 2 | OQ-1 — Rehydration scan | **NO index change.** Boot scan uses `idx_task_status_type_created` `status='paused'` prefix + Python post-filter on `suspension_reason='awaiting_answer'`. Never scan by `suspension_reason` alone. | Composite `(status, task_type, created_at)` covers the boot scan via its `status` prefix; adding `suspension_reason` would dilute selectivity for the claim path (the index's real job). Boot scan is one-shot, not periodic — constraint-compliant. | Architect recommendation §2/OQ-1 (date 2026-09-21) |
| 3 | OQ-2 — Child question still pending | **(B) Emit `CHILD_QUESTION_STILL_PENDING`** post-commit in `_resume_cascade_db_sync`. Rejected (A) silent (orphan leak via handle-wipe), (C) refuse parent resume (over-blocking), preserve-handle variant (parent re-wedge on `waiting_children`). | The mechanism that forces the decision is that `resume_instance_cascade`'s db-sync wipes every paused task's handle regardless of suspension reason (`instance_lifecycle.py:5486-5535` + `turn_transitions.py:329-330`); a child's own pending question would become unrecoverable. | Architect recommendation §2/OQ-2 (date 2026-09-21) |
| 4 | OQ-3 — Exactly-once CAS | **PROMOTED to REQUIREMENT.** `set_answers` returns `(pack, transitioned: bool)` tuple; `pending→answered` exactly-once; loser gets `200 resume_route:"already_delivered"` and the helper skips everything (SSE, events, resume, Defect-3). Optional `question_pack_id` echo → `400 QUESTION_PACK_MISMATCH`. | The prior non-atomic `set_answers` (`question_manager.py:307-338`) was overwrite-idempotent (second call **clobbered** first); `find_suspended_turn_for_answer` is a read, not a claim — two racers both saw the handle (TOCTOU). Acceptance of double-delivery is revoked. | Architect recommendation §2/OQ-3 (date 2026-09-21) |
| 5 | OQ-4 — Heartbeat-claim semantics | **Exactly-once via atomic claim** (`repository.py:1608-1618`). **Durable no-op predicate** = `find_suspended_turn_for_answer` returns `None` OR asker instance status is terminal. **Never use in-RAM pack status** (gone on restart). **Fire-and-no-op lifecycle** (no cancellation hook). **At-least-once crash window** via `StaleTaskRecovery` retry-child (`repository.py:4288-4301`) — same contract as PROCESS_REPORT delivery. | The atomic claim arbitrates between concurrent workers (scalar subquery + outer UPDATE row-lock). The handle-clear by `ResumeTurn` (`turn_transitions.py:285-292`) is the durable "answer already landed" signal — survives daemon restart while the in-RAM pack does not. Crash between claim and emit is recovered by the existing `StaleTaskRecovery` retry-child minting (drops unknown columns, preserves `task_type`/`instance_id`/`next_retry_at`). | Architect recommendation §1.5 (date 2026-09-21) |
| 6 | Trade-off — Answer surface | **A — `POST /api/jobs/{work_id}/answer`** (the plan's original pick). Confirmed. Dominant axes: maintainability (single shared helper, two thin routes, typed 404 for free, fixes today's mislabeled `INSTANCE_NOT_FOUND`) and contract fit (orchestrator's identity in this pipeline is `work_id`, jobs router already establishes `/{job_id}/<action>` convention with 6 precedents). | Every B variant either forces client-side `work_id→instance` resolution onto an LLM or degenerates into A behind an instance-scoped URL. B's only real advantage (smaller diff) evaporates because shared-helper extraction is paid identically in both. | Architect recommendation §6 (date 2026-09-21) |
| 7 | **Leader decision 1** — T3 revive-on-answer policy | Asker in COMPLETED/TERMINATED → `410 ANSWER_TARGET_TERMINAL`. Asker in ERROR/FAILED → REVIVE + deliver the answer; NO revive-budget consumption (mirror user-API revive semantics; answer path is user-origin); response flag `resume_route:"revived_error_target"`. | COMPLETED/TERMINATED = deliberate stops; today's `enqueue_message` silently revives (`instance_messaging.py:1904-1915`) which is a latent data-loss hazard (no LangGraph checkpoint on the revived instance). ERROR/FAILED = genuine recovery affordance (handle is gone anyway — `task/repository.py:435-439`). Tightening is intentional; existing instance-addressed route inherits via shared helper. | Leader decision 1 (date 2026-09-21, binding) |
| 8 | **Leader decision 2** — `paused_by_parent` distinct reason | **ADOPT NOW.** New `SuspensionReason.PAUSED_BY_PARENT` value; stamped on cascade-inherited children at the pause cascade site (`_pause_cascade_db_sync`); feeds OQ-2 `child_question_still_pending` detection; prevents cascade-inherited child handles being answer-consumed by an answer aimed at the parent. | `find_suspended_turn_for_answer` already filters only `awaiting_answer`; the new value just ensures cascade artifacts never match. Cascade resume is reason-agnostic so children still resume on parent answer. Modest scope: one enum value + one stamping site. | Leader decision 2 (date 2026-09-21, binding) |
| 9 | **Leader decision 3** — Drift-reconciler exclusion | **Exclude `heartbeat_emit_stuck`** from `list_pending_tasks_older_than` (`daemon/repositories/task/repository.py:1007-1046`). | Heartbeat rows sit PENDING >300s with NULL heartbeat — Pattern (a) WARNING per 300s cycle; safe but noisy. Backlog-acceptable. Registration in the drift-reconciler's type table. | Leader decision 3 (date 2026-09-21, binding) |
| 10 | Implementation phasing | Plan-out-of-scope for design.md; 4-phase sub-plan to be authored separately as `phase{N}-plan.md` files mirroring `report-lane-decoupling.md`'s shape (1.1–1.8 ship-first + 2.1–2.4 hardening + 3 deferred). | This design doc is the source of truth for the WHAT and WHY; phase plans are the HOW + ordered work items. | Planner discretion |
| 11 | Review remediation — MAJOR-1: watcher fan-out | **Fan-out over EVERY live work_id** — active JobItem + current Task. Removed the invented `task.work_id_for(instance_id)` API; replaced with verified `JobItemRepository.get_active_by_instance` (`repository.py:553+`) + `get_by_instance` (`:531-552`) + `TaskRepository.get_by_instance` (`task/repository.py:284-298`). The fan-out is the DEFAULT; the first-turn-only fallback is documented but requires a separate instance-routed event lane. | A turn-2+ question on a long-running job would otherwise silently notify a work_id nobody watches — that IS incident 1 recurring. Sanctioned fallback exists if fan-out proves noisy at scale. | Review verdict APPROVED_WITH_NOTES, edit MAJOR-1 (date 2026-09-21, binding) |
| 12 | Review remediation — MAJOR-2: wedge-guard re-arm clause | **Add the missing minting step.** Each claimed stuck-emission with `emission_index < 3` AND no-op predicate false mints EXACTLY ONE successor one-shot Task at `now + STUCK_HEARTBEAT_AFTER_SECONDS` (1800s) — single-emission chain, constraint-compliant. Fixed arithmetic: t=0 + 1800s + 3600s = **60 min total** (NOT 90 min as the prior version stated). At `emission_index == 3` escalation fires + NO successor is minted. | The re-arm clause was an implicit assumption; making it explicit closes the "silent after 1 emission" wedge window. Arithmetic drift (`~90 min`) would have been operator-visible (test fixtures and SLOs would misalign). | Review verdict APPROVED_WITH_NOTES, edit MAJOR-2 (date 2026-09-21, binding) |
| 13 | Review remediation — MINOR-10: duplicate/late-answer test | **Aligned with OQ-3 CAS.** Duplicate answer → `200 {resume_route:"already_delivered"}` (NOT an error); helper skips SSE, events, resume, Defect-3. Renamed test to `test_duplicate_answer_cas_noop`. Added `test_cas_winner_lost_handle` for the CAS-winner mid-resume race: POST /answer B arrives while A's `_resume_processing_background` is mid-flight; B's `find_suspended_turn_for_answer` returns `None` AND B already lost the entry CAS, so the helper short-circuits to `already_delivered`. Confirms TOCTOU window is closed at the entry CAS, not at the finder. | The prior `test_late_answer_after_resume` accepted double-delivery as the expected behavior — that acceptance was revoked by OQ-3. The new test asserts exactly-once. | Review verdict APPROVED_WITH_NOTES, edit MINOR-10 (date 2026-09-21, binding) |
| 14 | Review remediation — MINOR-12: stale cite + EventKind count + SSE lane consistency | **(a)** Refreshed `manager.py:6494` → `manager.py:10469` for `_resume_processing_background` (verified). **(b)** Refreshed `retry_scheduler.py:94,152-162` → `retry_scheduler.py:70,84,162` (verified: class at `:70`, `_poll_interval` field at `:84`, poll sleep at `:162`). **(c)** EventKind count clarified to **5 EventKinds total, 4 in work_notifier status map** (`CHILD_QUESTION_STILL_PENDING` excluded from the status map); R1 wording updated to match. **(d)** SSE lane count clarified to **4 push lanes** (EventBus, LiveEventHub, work_notifier→[JOB_EVENT], NotificationBroadcaster); §2.1 diagram updated. | Stale cites misdirect the implementer; EventKind/SSE-lane ambiguity creates incorrect "5 vs 4" mismatches across the doc; clarifying both resolves the drift. | Review verdict APPROVED_WITH_NOTES, edit MINOR-12 (date 2026-09-21, binding) |
