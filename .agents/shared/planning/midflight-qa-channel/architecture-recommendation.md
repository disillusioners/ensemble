# Architecture Recommendation: Mid-Flight Question / Answer Channel — Enrichment

| Field | Value |
|---|---|
| **Status** | COMPLETE — all blockers resolved with source evidence |
| **Enriches** | `design.md` (planner-authored, 693 lines, DRAFT) in this directory |
| **Worktree** | `feature/midflight-qa-channel` @ `246b7325` (v0.13.9) |
| **Method** | 3 parallel design analysts (structural / data-flow / resilience), evidence adjudicated and synthesized by architect |
| **Verdict summary** | Planner's architecture is directionally sound and lane-safe. R2 resolves in favor of the planner's asker-bound Task + claim-gate bypass (the system-lane alternative is structurally illegal). All 4 OQs decided. **5 factual defects in design.md must be fixed before implementation.** Answer surface: **A (job-addressed route) — confirmed.** |

---

## 1. R2 RESOLUTION (blocker) — heartbeat attach-point

### 1.1 Verdict

**§10/R2 and §11-S3 are CORRECT; the §4.3 inline note ("no claim guard change needed") is factually WRONG. The planner's Approach 1 — one-shot Task row bound to the asker + a type-scoped carve-out in the claim gate — is the winning mechanism.** The leader's suggested alternative (attach to the system lane / mission layer so no claim-gate change is needed) was evaluated seriously and **rejected on three independent structural grounds** (§1.3).

The pause gate exists verbatim in `claim_pending_task`'s atomic claim (`daemon/repositories/task/repository.py:1931-1952` in this revision — the plan's `:1248-1254` cite is stale line numbering):

```sql
AND instance_id NOT IN (
    -- Phase 1 (2026-06-24, report-lane decoupling): Pause gate.
    -- Excludes instances whose status is PAUSED or TERMINATED
    -- for ALL task types.
    SELECT instance_id FROM instances
    WHERE status IN (:status_paused, :status_terminated)
)
```

"For ALL task types" is the wedge, verbatim: an asker-bound Task row is **never claimable while the asker is paused** — exactly the state the heartbeat exists to observe. Without a change, the wedge guard cannot fire.

### 1.2 Why the bypass is safe and precedented

- **Precedent in the same WHERE cascade**: the cross-system guard is already type-scoped — `task_type != :process_message_type OR instance_id NOT IN (...)` ("Report tasks … bypass the guard entirely", `repository.py:1953-1957`, bind at `:1984`). Adding one more type-scoped disjunct is the **established extension shape**, not novel surgery on a hot path.
- **Sole claim seam**: `TaskProcessor.claim_task` → `task_repo.claim_pending_task` (`task_processor.py:1308-1332`), called from both worker pools (`worker_pool.py:302`). Blast radius is one WHERE clause, exercised by every claim — but composed exactly like the 7 existing gates.
- **All other gates are non-factors** for the heartbeat row: per-instance concurrency gate is `status='running'`-only (S3 invariant preserved, `repository.py:1866-1930`); defer/background gates bypass non-deferred rows (`:1762-1818`); queue-awareness passes (heartbeat `work_id` has no JobItem, `:1844-1849`); chat-lane gate passes (`message_id IS NULL`, `:1850-1865`). **Exactly one gate blocks; one clause fixes it.**

### 1.3 Why the system-lane alternative is REJECTED (evidence)

The leader asked for an explicit trade-off evaluation of attaching the one-shot task to the system lane / mission layer:

1. **`Task.instance_id` is non-nullable at the model** (`task/models.py:178` — `str = Field(index=True)`). A system-owned NULL-instance Task is schema-illegal.
2. **Even if nullable, `NULL NOT IN (subquery)` evaluates UNKNOWN** whenever any paused/terminated instance exists — the row would be claimable *only when no wedge exists*. Double-dead.
3. **Binding to a non-asker instance (e.g., the orchestrator) passes the pause gate but couples to the per-instance concurrency gate** (`repository.py:1866-1930`): the orchestrator is busiest exactly when wedges happen, and a RUNNING heartbeat row would block the orchestrator's own next claim.
4. **The JobItem side violates the event-driven constraint**: job-side future-dating rides `RetryScheduler`'s **60s poll loop** (`retry_scheduler.py:94,152-162`); `JobProcessor._process_loop` is a 30s poll (`job_processor.py:87-90`). JobItems also have no arbitrary-callback handler (`repository.py:1979-1983` — spawn+enqueue semantics only).
5. **No other one-shot substrate exists**: `TimeoutMonitor` is an in-memory thread, not restart-survivable (`timeout_monitor.py:43-77`); `mission_resolver` is a read-model (`mission_resolver.py:5,424`); the MessageQueue lane feeds the graph-turn pipeline, which pause-checks and skips (`message_processing_pipeline.py:464-476,520`) — a foreign body. The only true future-dated Task precedent (usage-limit deferral, `worker_pool.py:982` via `schedule_retry`) never hits the pause gate because its instance is RUNNING — which is precisely why this conflict was never exercised before.

### 1.4 Exact mechanism spec (supersedes §4.3's sketch)

**Gate change** — `repository.py:1931-1952`, wrap the pause gate:

```sql
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
- **Do NOT touch the concurrency gate** — it is what makes the resume race deterministic (§1.5).

**Row creation — §4.3's `task_repo.create(...)` sketch is invalid** (factual defect #1): the real signature is `create(task_type, instance_id, message_id=None)` — no `status`, no `next_retry_at`, **no `metadata` kwarg** (`repository.py:237-262`), and the Task model has **no metadata column at all** (`task/models.py:160-260`). Mint via direct `Task(...)` construction with `next_retry_at` (precedents: c_revival `manager.py:8648-8657`; RetryTurn child INSERT `repository.py:4281-4290`) or add a small repo method.

**Context carrier**: `question_pack_id` rides the durable `instance_metadata` hook the design already defines (§4.1 step 1); `emission_index` **derives from persisted event history** (EventBus persists before broadcast, `event_bus.py:174-181` — count prior `stuck_awaiting_answer` rows for the pack). This survives `StaleTaskRecovery`'s retry-child minting, which drops unknown columns.

**Processor**: one entry in the task dispatcher table (`task_processor.py:1285-1306`, `:1347`), default-lane fuel, wake latency ≤3s after `next_retry_at` via the existing condition-timeout claim loop (`worker_pool.py:371`) — no `notify_work` needed.

**Escalation at `emission_index=3`**: call `manager.terminate_instance` **directly from the processor** (`manager.py:9401`). Do NOT route through task-ERROR → `JobFeedbackObserver` inference (drifts into the deferred error-lane defect, `error_reporting.py:222-296` — constraint respected). Note: `terminate_instance_cascade` (design §8.7) **does not exist by that name** (factual defect #2).

### 1.5 OQ-4 DECISION — heartbeat-claim semantics

1. **Exactly-once under concurrent claim**: the atomic claim arbitrates (scalar subquery + outer UPDATE row-lock; loser gets 0 rows, `repository.py:1608-1618`). Emission happens only in the claimed processor → once.
2. **No-op detection** (first statement after claim): `find_suspended_turn_for_answer(asker)` returns `None` once the handle is consumed (`turn_transitions.py:285-292` clears `suspension_reason`/`resume_target_turn_id`) — this is the durable "answer already landed" predicate, cross-confirmed by both other analysts (`task/repository.py:407-473`). Also read asker instance status: terminal → silent `complete_task`. **Do NOT use QuestionManager pack status** — in-RAM, gone on restart (`question_manager.py:11-13`).
3. **Resume race (answer at t=1790s)**: `ResumeTurn` flips the asker's task PAUSED→PENDING atomically (`turn_transitions.py:261-330`); both rows PENDING → claim order is PROCESS_REPORT tier first, then `created_at ASC` (`repository.py:1620-1625`) → the asker's older message task claims first, goes RUNNING, and the concurrency gate blocks the heartbeat for the turn's duration; the heartbeat claims later, finds no handle, no-ops. **Deterministic, exactly-once, zero new machinery.** (Bounded interference: a claimed heartbeat can delay the asker's resumed claim by the emission duration, ~ms. Accepted.)
4. **Crash between claim and emit**: `StaleTaskRecovery` force-cancels and mints a retry child preserving `task_type`/`instance_id`/`next_retry_at` (`repository.py:4158-4310`, child kwargs `:4288-4301`) → re-claim → no-op check re-runs. At-least-once in the crash window, exactly-once normally — same contract as PROCESS_REPORT delivery.
5. **No cancellation hook needed** when the answer lands early: the row fires at +30min, re-checks the handle, and self-cancels. A cancel hook would be a racy new coordination point for zero benefit.

---

## 2. OQ DECISIONS (blockers)

### OQ-1 — Rehydration scan: **NO index change. One-shot boot scan as-specified.**

Existing indexes on `task`: `idx_task_resume_target (resume_target_turn_id, suspension_reason)`, `idx_task_status_created (status, created_at)`, `idx_task_status_type_created (status, task_type, created_at)` (`20260906_192100_add_task_claim_wake_lane_index.sql:58`), `idx_task_instance`, `idx_task_work_id` (UNIQUE). No `(status, suspension_reason)` index — and none needed:

- The scan `SELECT instance_id FROM task WHERE status='paused' AND suspension_reason='awaiting_answer'` uses `idx_task_status_type_created`'s `status` prefix with a residual post-filter; cost is O(paused tasks) — bounded by active orchestration depth (tens, not thousands), once at boot. Milliseconds.
- Adding `suspension_reason` to that composite would **dilute selectivity for the claim path** (the index's real job); a partial index on `suspension_reason='awaiting_answer'` is redundant with the status prefix.
- **Implementation note**: filter `status='paused'` in SQL (index-served), post-filter `suspension_reason` in Python. Do NOT scan by `suspension_reason` alone.
- A one-shot boot pass is not a periodic scan — constraint-compliant.

### OQ-2 — Child question still pending: **(B) Emit `child_question_still_pending` at parent-resume post-commit.** (Overturns the plan's (A) recommendation.)

**Mechanism that forces the decision**: `resume_instance_cascade`'s db-sync selects **every** paused task in the tree — `WHERE instance_id IN tree_ids AND status='paused'`, no suspension-reason filter (`instance_lifecycle.py:5486-5535`) — and `ResumeTurn` nulls `suspension_reason` + `resume_target_turn_id` on each (`turn_transitions.py:329-330,373-374,417-418`). A child paused for **its own** question (its own `awaiting_answer` handle, own pack in `QuestionManager._packs`) is resumed alongside the parent and **its handle is WIPED while its pack stays `pending` in RAM**: task row unfindable by `find_suspended_turn_for_answer`, pack unrejectable by the duplicate-pending guard (`question_manager.py:240-246`), orchestrator unaware. Silent option (A) leaks an orphan the human cannot act on.

**Spec**: post-commit in `_resume_cascade_db_sync` (after the commit at `instance_lifecycle.py:5582`), for each resumed instance whose pack is still `pending` (add `QuestionManager._get_pending_packs_for_instances(tree_ids)` read API under its lock, `question_manager.py:212`): emit one `child_question_still_pending` on the same three lanes as `stuck_awaiting_answer` (EventBus + LiveEventHub banner + `notify_work_watchers` non-terminal). Payload mirrors the stuck schema: `{child_instance_id, child_job_id, question_pack_id, asker_agent_id, paused_at, wedge_chain=[parent_id, child_id]}`. Informational only — no pause. Recovery is well-defined even with the handle wiped: the child is RUNNING, so `POST /api/jobs/{child_work_id}/answer` falls to the Defect-3 fresh-message branch and the answer reaches the child's graph normally.

**Rejected alternatives**: (A) silent — the orphan leak above; (C) refuse parent resume until child questions answered — over-blocking, the parent's stop-gate answer shouldn't be hostage to a child; "preserve the child's handle on cascade resume" (skip reason-mismatched tasks) — sounder semantics but risks re-wedging the parent on `waiting_children` when it resumes expecting children to progress, and deepens changes to shared cascade code. Revisit only if (B) proves insufficient.

**Complementary hardening (recommended, 🟡)**: give cascade-inherited child handles a distinct `suspension_reason` (e.g. `paused_by_parent`) at pause time, so `find_suspended_turn_for_answer` (which filters `awaiting_answer`) can never consume a cascade artifact mid-pause — i.e., an answer aimed at the parent can never partially resume a child. Cascade resume is reason-agnostic so children still resume on answer. Modest scope; see Decisions Pending.

### OQ-3 — Exactly-once CAS: **PROMOTED from "defer" to REQUIREMENT.** CAS at `set_answers` entry + pack correlation.

Worker C found **no CAS anywhere on the answer path** (🔴): `set_answers` is overwrite-idempotent (second call **clobbers** the first's answers, `question_manager.py:307-338`), and `find_suspended_turn_for_answer` is a **read, not a claim** — two racers can both see the handle (TOCTOU). The plan's own §8.5 accepts double-delivery; that acceptance is revoked.

**Decision**:
1. `set_answers` → tuple return `(pack | None, transitioned: bool)` under the existing lock: `pending→answered` exactly-once; on `status=='answered'` return `(pack, False)` **without overwriting answers**.
2. Both answer routes (shared helper) skip everything — SSE, events, `resume_processing_job`, Defect-3 — when `transitioned=False`, returning `200 {resume_route: "already_delivered"}`. This preserves the FE's idempotent-retry contract (409 explicitly rejected).
3. The entry-CAS makes the T2a finder TOCTOU **unreachable via the answer routes** (only the CAS winner reaches the finder). The deeper conditional `UPDATE paused→resuming … RETURNING` inside the finder is therefore optional hardening, not required.
4. **Pack correlation (closes the stale-answer hijack, T6)**: the answer body may carry `question_pack_id` (the orchestrator/FE has it from the `question_requested` payload); when present and ≠ current pack id → `400 QUESTION_PACK_MISMATCH`. Strict-check-when-present; absent field = today's behavior. Without this, an old-answers retry landing after resume+re-ask stamps the NEW pack `answered` with OLD answers and resumes with "(no answer)" placeholders.

### OQ-4 — Heartbeat-claim semantics: **decided in §1.5** (exactly-once via atomic claim; durable no-op predicate = `find_suspended_turn_for_answer` None-or-terminal; fire-and-no-op lifecycle; at-least-once crash window via retry-child).

---

## 3. Lane-Correctness Audit — v0.13.9 result bodies are NOT at risk

All three push lanes verified against source. **Every chain link SAFE.**

| Chain link | Verdict | Evidence |
|---|---|---|
| New statuses take the non-terminal branch; watcher rows PRESERVED | SAFE | Branch keys on `_is_terminal(status)` (`work_notifier.py:417-447`); `_TERMINAL_STATUSES = {completed, settled, failed, cancelled, dead_letter}` (`work_status.py:145-147`); none of the 4 new words are members → CAS (DELETE…RETURNING, `:420-424`) never fires; rows persist for the eventual terminal event |
| Non-terminal notify writes job/Task/JobItem rows or triggers completion re-eval | SAFE | Only DB write is `enqueue_message` to the WATCHER (`work_notifier.py:477-481`); `watcher_repo` + `work_resolver` are read-only on this path |
| New kinds re-enter `JobFeedbackObserver` | SAFE | `_process_event` hard-filters `event_type != "instance_lifecycle"` → return (`job_feedback_observer.py:998-999`); new kinds never reach `atomic_transition` / `_root_completion_gate` |
| `[JOB_EVENT]` enqueue fires completion for the wrong job | SAFE | Watcher's own task only triggers notify on terminal `completed`/`settled` (`task_processor.py:990`); the enqueue is `is_completion_report`-treated (`message_processing_pipeline.py:727`) — context-gate skip, no side-effects |
| Resume path intersects `_root_completion_gate` | SAFE | Gate is root-completion triggered by `child_completed` (`child_reports.py:1969, 2027-2048`); the answer resume is a per-instance PAUSED→PENDING flip, identical surface to the existing instance route — no new intersection |
| New kinds leak into mission-terminal classification | SAFE | `mission_live` derives from the WorkRecord's stored state, not the input status (`work_notifier.py:357-389`); new kinds are NOT added to `ALL_MISSION_TERMINAL_WATCHABLE_EVENTS` (`watcher_models.py:33-35`) |
| EventBus consumers break on new enum values | SAFE | Single production global subscriber (`JobFeedbackObserver`, `job_feedback_observer.py:604`) default-ignores; all other consumers are producers or explicit allowlists (`migration.py:266`, `notification_broadcaster.py:201`); no exhaustive match anywhere |
| Source-prefix family extension | SAFE | `internal_agent:job_event:{work_id}:{status}` byte-compatible: reserved-prefix match is `startswith` (`constants.py:694,711-713`); parser keys on header text (`job-orchestration/skill.md:179`); `<meta>` carve-out (`instance_messaging.py:2719`) and completion-report treatment (`:2854`) inherit to all family members |
| 2s jobs-SSE poll extended | SAFE (unchanged) | No new consumer of `jobs_streaming.py:362-363`; all new lanes are push |

**Invariant to preserve during implementation** (from the blueprint's terminal-token contract): no new status token may ever flow into `JobFeedbackObserver`'s accepted set (`job_feedback_observer.py:316` accepts only `completed|error|failed`) — the wedge-guard escalation deliberately bypasses the observer entirely (§1.4).

**Self-watch edge (benign)**: if the watcher instance IS the asker (orchestrator asked its own question), the `[JOB_EVENT]` line lands in the asker's own queue — but the asker is PAUSED, so the claim gate holds it PENDING until answer-resume, exactly like any queued message. No auto-resume loop.

---

## 4. Answer State Machine (pack + delivery)

States: `PACK_PENDING` → `ANSWERED` (CAS) → `DELIVERED_EXISTING_TURN` | `DELIVERED_FRESH_MESSAGE` (Defect-3) | `REJECTED_TERMINAL` (410) ; plus `ALREADY_DELIVERED` (200 no-op) and `LOST` (410 after restart without durable payload).

| # | Transition | Guard (file:line) | CAS point | HTTP result |
|---|---|---|---|---|
| T0 | ask → `PACK_PENDING` | duplicate-pending rejection `question_manager.py:240-246` | in-RAM lock + `instance_metadata` stamp (§4.1 hook) | n/a (tool echo) |
| T1 | POST → `ANSWERED` | `set_answers` **with new CAS** (OQ-3): `(pack, transitioned)`; pack-id correlation when body carries `question_pack_id` | **CAS under `QuestionManager._lock`** (`question_manager.py:212,307-338`) | 200 `answered` |
| T1′ | duplicate POST | CAS fails (`transitioned=False`) → **skip resume, skip Defect-3** | same CAS | **200 `resume_route:"already_delivered"`** |
| T1″ | stale pack correlation | body `question_pack_id` ≠ current pack | pre-CAS check | **400 `QUESTION_PACK_MISMATCH`** |
| T2a | → `DELIVERED_EXISTING_TURN` | `find_suspended_turn_for_answer` (`task/repository.py:407-473`; filters `paused` + `awaiting_answer` + `resume_target_turn_id` non-null; >1 row → ValueError) | reachable only by the CAS winner → TOCTOU closed for this path | 200 `answer_gate_existing_turn` |
| T2b | → `DELIVERED_FRESH_MESSAGE` | finder None → Defect-3 enqueue (`instances.py:1188-1228`, `source="api_answer_fallback"`) | none (creates MessageQueue + Task) | 200 `enqueue_as_fresh_message` |
| T3 | answer to COMPLETED/TERMINATED asker | **NEW guard before T2b** (see below — currently the enqueue REVIVES, `instance_messaging.py:1904-1915` incl. TERMINATED) | guard in shared helper | **410 `ANSWER_TARGET_TERMINAL`** (proposed) |
| T4 | answer while resumed (race) | closed by T1′ CAS — was §8.5's accepted double-delivery | T1′ | 200 `already_delivered` |
| T5 | unknown work_id | `WorkResolver.resolve_work` None (`work_resolver.py:1000-1053`; `work_id` UUID4-per-row, never reused; 2 indexed SELECTs, read-only) | n/a | 404 `JOB_NOT_FOUND` |
| T6 | stale answers vs newer pack | closed by T1″ correlation | T1″ | 400 `QUESTION_PACK_MISMATCH` |
| T7 | pack gone after restart, no durable payload | `set_answers` → None AND `instance_metadata['question_pack_payload']` missing | n/a | 410 `QUESTION_PACK_LOST` |

**T3 guard (decision required — flagged to leader)**: design §8.4 claims TERMINATED → `enqueue_message` raises → 503. **The code revives instead** (`instance_messaging.py:1904-1915`; attestation reset only for user/HUMAN messages, `:1934-1975`). Today a stale answer **silently restarts a finished asker**. Recommendation: in the shared helper, pre-T2b status check → `410 ANSWER_TARGET_TERMINAL` for COMPLETED/TERMINATED; for ERROR/FAILED allow the revive (genuine recovery affordance — the handle is gone anyway, `repository.py:435-439`, so the answer lands as a fresh message) but return an explicit `resume_route:"revived_error_target"` so the FE/operator sees what happened. This tightens the existing route's stale-answer behavior intentionally — leader should confirm (Decisions Pending).

**Also fixed on both surfaces**: today's missing-pack 404 is mislabeled `INSTANCE_NOT_FOUND` (`instances.py:1097-1109`); applying `NO_PENDING_QUESTION` / `QUESTION_PACK_LOST` in the shared helper fixes the labeling defect for the existing route too.

---

## 5. Failure-Mode Hardening

| Mode | Current behavior (evidence) | Verdict / guard |
|---|---|---|
| **Watcher gone before answer** | Answer path is watcher-independent: pack lives on the ASKER; route resolves work_id→instance via Task/JobItem tables (`work_resolver.py:1000-1053`). Design §8.1's "undrained row for TERMINATED watcher" is **stale** — enqueue to a terminal instance REVIVES it (`instance_messaging.py:1904-1915`); only a deleted row strands (warning `:1983`). `reconcile_terminal_watches` (`job_queue_service.py:460-523`, boot `api.py:1128`) reconciles terminal work only — question rows are held by design (`work_notifier.py:164-172`) | **Accept watcher-independence.** Escalation reach when nobody watches: `emission_index=3` fans out to **NotificationBroadcaster unconditionally** (`notification_broadcaster.py:19`; root-level precedent `instance_lifecycle.py:2060-2082`) in addition to work_notifier + EventBus — otherwise logs-only |
| **Question while children running** | `_pause_cascade_db_sync` is one guarded session: instances UPDATE running/idle/waiting_children→paused (`instance_lifecycle.py:4933-4951`), per-task `SuspendTurn` with a single tree-wide `effective_suspension_reason` (`:4927-4930`), commit, post-commit reconcile. Graph cancel at node-boundary checkpoint; children's queued tasks held by claim gate. Watchdog double-protected: paused parents skipped (`waiting_children_watchdog.py` step 2a) AND paused children deliberately excluded from hang detection (`instance/repository.py` `list_hung_children_for_parent`) | **Safe as-is.** Residual: child handles are cascade artifacts answer-consumable via the child's own route → adopt `paused_by_parent` distinct reason (OQ-2 hardening) |
| **Daemon restart between Q and A** | Survives: task handle (`suspension_reason` + `resume_target_turn_id`), PAUSED instance, event rows, watcher rows, future-dated wake row (claimed only when due, `repository.py:1753`; boot sweeps target dead-instance/terminal-JobItem rows only, `job_recovery_service.py:926,1317-1400`). Lost: RAM packs (→ OQ-1 rehydration from `instance_metadata`), SSE connections | Two guards: (1) HeartbeatEmitStuck re-verifies the handle at emit (OQ-4 #2) — prevents spurious stuck events after early answers; (2) **move the metadata clear out of R3's site**: clear `question_pack_payload`/`question_pack_id` when the pack is ANSWERED (in `set_answers`/`clear_question_pack`, `manager.py:1909`), NOT in `set_question_pack` (which *creates* packs) — R3 as written leaves a stale-rehydration window that can resurrect an answered pack |
| **Multiple simultaneous askers** | Each asker = own Task turn = own unique `work_id`; resolver maps work_id→exactly one instance (JobItem-first on dual-backed rows, `work_resolver.py:1023-1032`); per-watcher delivery FIFO by `created_at ASC`; non-terminal notify never claims | **Accept.** Payload carries `instance_id` + asker name so the orchestrator disambiguates. Ad-hoc askers with no job backing have no work_id → job route 404s (correct contract); instance route remains the non-job fallback |
| **Drift-reconciler noise** | Heartbeat rows sit PENDING >300s with NULL heartbeat → Pattern (a) WARNING per 300s cycle; alive-instance branch is **log-only**, no mutation (`job_recovery_service.py:1488-1516`) | Safe but noisy. Backlog: exclude the type in `list_pending_tasks_older_than` (`repository.py:1007-1046`) |

---

## 6. Answer Surface — Trade-off Matrix (A vs B) and Recommendation

Evidence base (Worker C): the existing handler spans `instances.py:1053-1305` (~250 lines — the plan's "1053-1228" underspans by ~77); ~90% is shared logic, extraction is mechanical; the jobs router already ships **six** per-job POST actions (`/{job_id}/cancel|restore|retry|cleanup|resend-foreground`, `defer-holders/{id}/force-complete` — `jobs_management.py:225,353,438,832,951,1021`); `WorkResolver` is 2 indexed SELECTs, already invoked on every notify; auth is app-level and identical on both surfaces (`instances.py:1697-1699`) — the new route adds no F2-style forging surface; today's instance-route 404 is mislabeled for the no-pack case.

| Axis | A: new `POST /api/jobs/{work_id}/answer` | B: extend `POST /api/instances/{id}/answer` visibility |
|---|---|---|
| **Complexity** | Low-Med — one thin route + resolver step over the shared helper; matches 6 existing precedents | Med — no precedent for header-aliasing; a second delegating route is then just "A with an instance-scoped URL"; without server-side resolution the ORCHESTRATOR must resolve work_id→instance itself (pushes an LLM into doing registry lookups) |
| **Scalability** | Good — job-addressed contract scales with jobs/watchers, not instance identity | Neutral — same downstream path, but contract keyed on an ID the chat surface doesn't natively hold |
| **Maintainability** | **Best** — single shared `_answer_questions_via_instance`, two thin routes, typed 404 for free, fixes the mislabeled 404 on both surfaces | Worse — aliasing/branching in a live handler, or a near-duplicate route; drift risk between the two entry semantics |
| **Risk** | Slightly higher new-endpoint surface (auth parity neutral; no F2 interaction) | Lower new surface but higher contract-confusion risk (misrouted answers; header semantics an LLM can get wrong) |
| **Cost** | Comparable — extraction is mechanical either way; A adds one route + one resolver call | Comparable — but B's "cheap" variants collapse into A's shape anyway |

**Recommendation: A — the planner's pick is confirmed.** Dominant axes: Maintainability and contract fit — the orchestrator's identity in this pipeline is the `work_id` (it holds `[JOB_EVENT] Job {work_id}...`, it parses the job id from that header), the jobs router already establishes the `/{job_id}/<action>` convention, and B's every concrete variant either forces client-side resolution onto an LLM or degenerates into A behind an instance-scoped URL. B's only real advantage — smaller diff — evaporates because the shared-helper extraction is required (and paid) identically in both.

**Confidence: High.** Flipping assumption: if the FE wizard were the ONLY answer source (no chat/orchestrator relay ever), B's single-address-space would win — but that contradicts both the incident record and the design's own orchestrator-relay flow.

---

## 7. Edits design.md needs (section → change)

1. **§4.3 (emission #2 sketch)** — Replace "no claim guard change needed" with the verified gate quote + the §1.4 carve-out spec (type-scoped disjunct, **broad over paused+terminated**, bind literal, concurrency gate untouched). Fix the `task_repo.create(...)` sketch (real signature `create(task_type, instance_id, message_id=None)`, `repository.py:237-262`; no `metadata` kwarg — Task has **no metadata column**, `task/models.py:160-260`): mint via direct `Task(...)` construction or a new repo method; `emission_index` derives from persisted event history; `question_pack_id` rides `instance_metadata`.
2. **§4.4 (files-touched table)** — `task/repository.py` row "0 lines / create() already supports next_retry_at" is **wrong**: new repo method (or documented direct construction) + the claim-gate carve-out at `:1931-1952`. Add dispatcher registration (`task_processor.py:1285-1347`). `instance_lifecycle.py` row: also emit `child_question_still_pending` (OQ-2) and move the metadata clear (edit 8).
3. **§5.3** — Correct the extraction span: `instances.py:1053-1305` (~250 lines, not 1053-1228). State that the new error codes (`NO_PENDING_QUESTION`, `QUESTION_PACK_LOST`, `ANSWER_TARGET_TERMINAL`, `QUESTION_PACK_MISMATCH`) live in the shared helper so both surfaces inherit (also fixes today's mislabeled `INSTANCE_NOT_FOUND` at `instances.py:1097-1109`).
4. **§5.4 + §10 OQ-1** — Record the decision: **no index change**; boot scan = `idx_task_status_type_created` `status='paused'` prefix + Python post-filter on `suspension_reason`; never scan by `suspension_reason` alone.
5. **§6.1 / §8.5 / §10 OQ-3** — Replace "accept the duplicate / defer CAS" with the **requirement**: tuple-return CAS at `set_answers` (pending→answered exactly-once, no overwrite), skip resume+fallback on `transitioned=False`, 200 `resume_route:"already_delivered"`; optional `question_pack_id` echo in the body with `400 QUESTION_PACK_MISMATCH` on mismatch.
6. **§7.3** — Fix the `answer_received` rationale (doubly wrong): `work_notifier.py:162-173` is a **docstring**; the real branch is `:417-447`; the CAS does NOT fire — the watcher row is **PRESERVED**. Re-rationale: "informational; the row survives for the eventual terminal event."
7. **§8.1** — Update watcher-gone: enqueue to a TERMINATED watcher **revives** it (`instance_messaging.py:1904-1915`), no stranded row (only deleted-instance rows strand); answer path is watcher-independent; escalation at `emission_index=3` fans out to NotificationBroadcaster unconditionally.
8. **§8.3 / §10 OQ-2** — Record decision **(B)**: post-commit `child_question_still_pending` emission in `_resume_cascade_db_sync` (spec in §2/OQ-2 above, incl. the `QuestionManager` read API); note rejected alternatives (A = orphan leak via handle-wipe at `instance_lifecycle.py:5486-5535` + `turn_transitions.py:329-330`; C = over-blocking; preserve-handle variant = parent waiting-children re-wedge). Add the `paused_by_parent` hardening note. **R3** — move the metadata clear from `set_question_pack` to answer consumption (`set_answers`/`clear_question_pack`).
9. **§8.4** — Replace the "TERMINATED → enqueue raises → 503" claim (**factually wrong — the code revives**): new `410 ANSWER_TARGET_TERMINAL` guard for COMPLETED/TERMINATED in the shared helper; ERROR/FAILED revive allowed with explicit `resume_route:"revived_error_target"`; mark the intentional behavior change for leader confirmation.
10. **§8.7** — `terminate_instance_cascade` doesn't exist by that name — escalation calls `manager.terminate_instance` (`manager.py:9401`) directly from the processor; **drop the JobFeedbackObserver "had_parent_error-equivalent" finalize framing** (do not make observer inference load-bearing — deferred error-lane defect, `error_reporting.py:222-296`); add the NotificationBroadcaster fan-out; note the drift-reconciler log-noise + optional type exclusion backlog.
11. **§10 R2** — Keep the verdict; upgrade "single-line WHERE clause change" to the §1.4 spec (broad bypass; concurrency gate untouched; bind-literal convention `repository.py:1984`); refresh the stale gate cite (`:1248-1254` → `:1931-1952` this revision). Record the system-lane alternative as evaluated-and-rejected (§1.3's five grounds).
12. **§10 OQ-4** — Record the decision (§1.5): exactly-once via atomic claim; no-op predicate = `find_suspended_turn_for_answer` None-or-terminal (never RAM pack status); fire-and-no-op lifecycle; at-least-once crash window via StaleTaskRecovery retry-child (`repository.py:4288-4301`).
13. **§11 code anchors** — Refresh drifted line numbers (claim gate `repository.py:1931-1952`; answer handler `instances.py:1053-1305`; work_notifier branch `:417-447`); add `work_resolver.py:1000-1053`, `job_recovery_service.py:926,1488-1516`, `manager.py:9401`.

---

## 8. Decisions Pending (leader)

1. **T3 revive-on-answer policy** — confirm `410 ANSWER_TARGET_TERMINAL` for COMPLETED/TERMINATED (tightens the existing route's stale-answer behavior from silent-revive to explicit rejection) and the allowed-but-flagged ERROR/FAILED revive. Alternative: keep revive everywhere and only flag it in the response.
2. **`paused_by_parent` distinct suspension-reason** — recommended (prevents cascade-inherited child handles being answer-consumed); small blast radius in `_pause_cascade_db_sync` + `find_suspended_turn_for_answer` filtering already matches only `awaiting_answer`. Adopt now or backlog.
3. **Drift-reconciler exclusion** for `heartbeat_emit_stuck` in `list_pending_tasks_older_than` — log-noise hygiene; backlog-acceptable.

## 9. Open Questions (non-blocking)

- **OQ-5** (chat-adapter heads-up note): keep as a one-paragraph CR note per adapter — default-ignore is enforced at the single production subscriber; low urgency.
- Whether the FE ever needs `answer_received` as a load-bearing SSE type (currently best-effort banner) — frontend wiring is out of scope per design header.

## 10. Confidence & Gaps

- **Confidence: High** on R2 (three independent structural grounds for rejecting the alternative, gate quoted verbatim), lane audit (every link SAFE with file:line), OQ-1..OQ-4, and surface A.
- One verification was closed by cross-report agreement rather than a single worker's direct read: `find_suspended_turn_for_answer`'s `awaiting_answer` filter is cited identically by two independent analysts (`task/repository.py:407-473`); implementation should re-confirm the exact WHERE on first touch.
- No implementation was performed; all mechanism specs are design-level with file:line anchors for the planner's phase files.
