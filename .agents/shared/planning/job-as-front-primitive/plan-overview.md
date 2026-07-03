# Plan Overview: Job-as-the-Front-Primitive (Collapse the Virtual-Job Facade)

## Objective
Make the **JobItem the single public/tracked work primitive**. Every fan-in submission (HTTP, tool, telegram, scheduler, external chat adapters) enters the system as a JobItem. The message queue + Task + worker_pool stay as the internal execution substrate a Job dispatches into. Internal system traffic (reports, nudges, `[JOB_EVENT]` delivery, compaction) keeps the raw message path — invisible to the facade.

## Scope Assessment
**LARGE** — rewrite of fan-in (entry) + read (facade) layers. The backend execution substrate (MessageQueue/Task/worker_pool/graph/SSE/dependency bus) is frozen and untouched. **Exception (RF1):** the cross-system guard inside `claim_pending_task` (`repository.py:607-646`) becomes load-bearing under universal message-JobItem traffic and may require optimization — this is explicitly scoped in Phase 0 and Phase 2, not treated as frozen. Net-negative diff expected (more deleted than added).

## Context
- Project: agents-ensemble
- Working Directory: `/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble`
- Architecture plan: `docs/plans/job-as-front-primitive.md`
- Prior migration: `docs/plans/decouple-execution-plan.md` (D11-D13 unified dispatch)

## ⚠️ Critical Discovery: Post-D13 / Job-as-Queue-Proxy Architecture State

The architecture has already been heavily refactored by D11-D13 (the "decouple-execution-plan" / Job-as-Queue-Proxy migration). The original architecture plan was written against a **pre-D13** codebase. Key findings:

| Pre-D13 (what the plan assumed) | Current Post-D13 Reality |
|---|---|
| `enqueue_message` had `dispatch_path` param ("workerpool" vs "jobqueue") | **REMOVED** — `enqueue_message` has a single unified path. No dispatch_path. |
| Messages could create JobItem rows via `dispatch_path="jobqueue"` | **Messages NEVER create JobItems.** `JobQueueService.enqueue(job_type="message")` raises `ValueError`. |
| 3-branch dispatch in `_process_next_job()` (legacy/observer/fallback) | **REMOVED** — JobProcessor only processes TASK-type dispatch-queue jobs. |
| `JobFeedbackObserver._admit_via_worker_pool()` existed | **REMOVED** — observer is now purely a lifecycle→finalize bridge. |
| `JobSystemConfig.use_legacy_jobqueue_dispatch` flag existed | **REMOVED**. |
| JobItem carried execution lifecycle state | **JobItem is now a pure queue proxy** with 4-value AdmissionState (queued/active/done/dead). Execution state lives on Instance. |
| Task was one of several execution authorities | **Instance is the sole execution authority.** Tasks and JobItems are queue tickets. |

### Post-D13 Invariants This Plan Must Respect
1. **JobItem = pure queue proxy** — `admission_state` is the sole write authority for queue gating. `status`/`started_at`/etc. are read from `Instance.status`.
2. **Instance = sole execution authority** — canonical execution state lives on the Instance row, not on Task or JobItem.
3. **Messages create Task-only (no JobItem)** — D13 invariant. This plan deliberately *reverses* that for public/external messages: `enqueue_message_job()` creates a JobItem alongside the Task. Internal messages (reports, nudges, `[JOB_EVENT]`) keep the Task-only D13 path.
4. **`dispatch_path` was REMOVED** — there is no workerpool-vs-jobqueue routing. `enqueue_message` always writes Task + MessageQueue and calls `worker_pool.notify_work()`.
5. **JobProcessor handles TASK jobs only** — message-Jobs are dispatched inline by `enqueue_message_job()`, never picked up by the poll loop.

### What This Means for the Plan
The original §4.1 concern ("a message-Job must NOT wait on `job_processor`'s poll loop") is **partially moot**: the current `enqueue_message` already dispatches instantly via `worker_pool.notify_work()`. The real work is:

1. **Creating a JobItem alongside the Task** — currently `enqueue_message` creates only MessageQueue + Task. We need it to ALSO create a JobItem (or use a thin wrapper that does).
2. **Ensuring the JobItem lifecycle is managed** — the JobFeedbackObserver must finalize the new message-JobItems.
3. **Collapsing the WorkResolver** — remove the Task union, dedup, and promotion code.

The **GO/NO-GO prototype** (§4.1) is now simpler: verify that creating a JobItem + Task in the same `enqueue_message` transaction doesn't add measurable latency vs creating Task-only.

## Phase Index

| Phase | Name | Objective | Dependencies | Coupling | Est. Time |
|-------|------|-----------|-------------|----------|-----------|
| 0 | GO/NO-GO Prototype | Verify message-Job dispatch has no latency regression | None | — | 2-4h |
| 1 | Feature Flag + Job-Message Bridge | Add `ENSEMBLE_MESSAGE_JOBS_ENABLED` flag and `enqueue_message_job()` that creates JobItem + Task atomically | None | — | 4-6h |
| 2 | Per-Instance Serialization + Retry Policy | Wire message-Jobs into per-instance queue serialization with retry=0 | Phase 1 | tight | 3-4h |
| 3 | Convert Fan-In Entry Points | Switch all 6 entry points to message-Job path behind flag | Phase 1, 2 | loose | 4-6h |
| 4 | Partial Facade Collapse (AD-6) | Delete turn-specific code; retain report Tasks. All 6 backend paths unchanged | Phase 3 | loose | 4-6h |
| 5 | Cutover + Cleanup | Flip flag default ON, run full E2E, delete old paths | Phase 4 | tight | 3-4h |

### Coupling Assessment

| Phase Pair | Coupling | Rationale |
|---|---|---|
| 0 → 1 | independent | Prototype validates; Phase 1 builds the real thing |
| 1 → 2 | **tight** | Phase 2 wires queue serialization that Phase 1's JobItems must respect |
| 2 → 3 | **loose** | Phase 3 converts entry points that call Phase 1's API; no shared file edits |
| 3 → 4 | **loose** | Phase 4 deletes facade code; only depends on entry points being converted |
| 4 → 5 | **tight** | Phase 5 deletes the raw-message fallback only after facade is JobItem-only |

## Feature Flag Mechanism

**Flag**: `ENSEMBLE_MESSAGE_JOBS_ENABLED`
- **Location**: `JobSystemConfig` class in `daemon/config.py` (env prefix `ENSEMBLE_JOB_SYSTEM_`)
- **Field**: `message_jobs_enabled: bool = Field(default=False, ...)`
- **Env var**: `ENSEMBLE_JOB_SYSTEM_MESSAGE_JOBS_ENABLED=true`
- **Default**: `False` (raw message path remains default until cutover)
- **Accessed via**: `self._job_system_config.message_jobs_enabled` or a helper on the manager

## Risks & Mitigations

### Architecture Review Findings (RF1-RF3)

| Finding | Status | Coverage |
|---------|--------|----------|
| **RF1** — Cross-system guard (`claim_pending_task:607-646`) becomes load-bearing: today it fires on edge cases (TASK-type JobItems only); post-cutover it fires on **every** `process_message` claim because all public messages carry a JobItem | **NOT blocking** — owner ruled; plan MUST explicitly cover | Phase 0 load test + Phase 2 guard validation + conditional guard modification task |
| **RF2** — Report Task visibility: 6 backend code paths branch on `kind != "job"`; conceptual conflation between internal messages and report Tasks | **🔴 RED → mitigated by AD-6** | AD-6: partial collapse retains `kind="report"` Tasks. All 6 paths work unchanged. Phase 4 rewritten for partial collapse |
| **RF3** — D13 reversal / dual-record coupling: creating JobItem alongside Task reintroduces coupling | **🟡 YELLOW — RESOLVED** | JobItem is pure queue proxy (no execution state). Load concern only. Phase 0 Task 8 validates finalize throughput at chat scale |

### Implementation Risks

| Risk | Impact | Mitigation |
|------|--------|------------|
| **RF1: Guard query plan regression under universal load** | **high** | Phase 0 load-tests `claim_pending_task` under 100% message-JobItem traffic. If p99 claim latency regresses, Phase 2 scopes explicit guard modification (index optimization or query simplification) — NOT treated as frozen backend |
| JobItem creation adds latency to enqueue path | high | Phase 0 prototype measures this; atomic INSERT in same TX as Task |
| **RF1: Cross-system guard deadlock if message-JobItems block Task claiming** | high | The NULL-safe `_admitted_task_carve_out_sql` carve-out handles this by design (JobItem with `message_id` + matching Task = not blocking). Phase 0 task 5 validates; Phase 2 tests contention explicitly |
| Double-execution if serialization mis-wired | high | Explicit contended-instance test before cutover |
| **RF2: Report Task removal breaks 6 backend paths** | **high** | AD-6: partial collapse retains report Tasks. Phase 4 deletes turn-specific code only. All 6 paths audited |
| **RF2: Conceptual conflation — internal messages ≠ report Tasks** | medium | Phase 4 context section explicitly distinguishes the two concepts |
| Internal messages accidentally surfaced in facade | medium | Facade retains report Tasks; internal messages (transport) are invisible by construction |
| Retry/dead-letter fires on chat traffic | medium | Per-submission retry policy (retry=0 for message-Jobs) |
| JobFeedbackObserver can't finalize message-JobItems | high | Observer already handles `job_id=None` case; message-Jobs give it a real job_id — test lifecycle event flow |
| PostgreSQL JSON metadata stamp race | medium | `stamp_message_id` already uses dialect-aware atomic JSON UPDATE |

## Success Criteria

- [ ] Every public entry point creates a JobItem; no public path enqueues a raw message without a JobItem
- [ ] **All 6 entry points converted** (including PAUSED auto-resume cascade child path at `manager.py:3356`)
- [ ] `WorkResolver` returns JobItems + report Tasks only; turn-specific code (dedup, F10 drift, promotion) deleted
- [ ] **RF2**: `kind="report"` Task rows retained in `list_work`; all 6 backend paths verified report-safe
- [ ] Internal messages (reports/nudges/`[JOB_EVENT]`/compaction transport, `invoke_and_wait`) still use the raw path and are invisible to the facade
- [ ] **BLOCKING ISSUE 2**: Stuck `queued` JobItems finalized via finalize-on-completion fallback
- [ ] **BLOCKING ISSUE 3**: `list_pending_by_queue` filters `job_type="message"` — poll loop never picks up message-Jobs
- [ ] A parent mid-orchestration reports `processing` with no special-case code
- [ ] `job_continue` is the single continuation verb
- [ ] Full E2E + regression suite green; the diff is net-negative
- [ ] Latency E2E: message-Job dispatch startup ≤ raw-message dispatch startup + 5ms
- [ ] **RF3**: `_finalize_job` throughput validated at chat-message scale (multiple/sec)

## Tracking
- Created: 2026-07-02
- Last Updated: 2026-07-03 (3 blocking issues fixed: 6th entry point, stuck-queued recovery, poll-loop filter)
- Status: draft
