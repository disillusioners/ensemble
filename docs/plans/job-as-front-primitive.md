# Plan: Job-as-the-Front-Primitive (Collapse the Virtual-Job Facade)

| Field | Value |
|---|---|
| **Status** | PLANNING — architecture proposal, not yet started |
| **Scope** | LARGE — rewrite of fan-in (entry) + read (facade) layers; backend (message/Task/worker) unchanged |
| **Mode** | **Single migration, one release** (intentionally not phased — this is a simplification, not a feature) |
| **Supercedes** | The `feature/virtual-job-management-surface` WorkResolver facade (Task ∪ JobItem union) |
| **Related** | `docs/plans/decouple-execution-plan.md` (the prior single-dispatcher migration this builds on) |

---

## 1. Objective

Make **the Job the single public/tracked work primitive**. Every fan-in submission (HTTP, tool, telegram, scheduler) enters the system as a Job. The **message queue + Task + worker_pool stay** as the internal execution substrate a Job dispatches into. Internal system traffic (reports, nudges, `[JOB_EVENT]` delivery, compaction) keeps the raw message path — it is never exposed as "work".

End state: **one primitive, one lifecycle, one status authority.** The WorkResolver stops unioning two kinds; the bug classes the facade generated all session (missing `completed` for Task work, F10 drift guessing, active-orchestration status, work_id≠job_id linkage) dissolve because they only existed at the Task-vs-JobItem seam.

**Why one release, not phased:** the change is a *simplification* — the new layering is strictly cleaner than the old. Running both front layers in parallel for multiple releases reintroduces the exact duality we're removing. Cut over once, behind a flag, validate, delete the old path.

---

## 2. Current vs Target Layering

```
TODAY                                   TARGET
─────                                   ─────
fan-in:  message API (front)            fan-in:  Job (front) ──┐
         job_create (special)                    job_create /  │
                 │                               job_continue │
                 ▼                                            ▼
         MessageQueue + Task  ◄───────────────  JobItem  ──► dispatch ──► MessageQueue + Task
                 │                                                        │
                 ▼                                                        ▼
            worker_pool ──► instance                          worker_pool ──► instance

facade:  WorkResolver = Task ∪ JobItem (union, dedup, promotion)   facade: WorkResolver = JobItem only
internal msgs: raw message path                                  internal msgs: raw message path (unchanged)
```

The backend column (MessageQueue/Task/worker_pool/claim_pending_task/graph/SSE/dependency bus/child reports) is **untouched**. We rewrite the *entry* and *read* layers.

---

## 3. What Stays / What Changes

**Stays (internal execution substrate):**
- `message_queue`, `Task`, `worker_pool`, `task_processor`, `claim_pending_task` (per-instance serialization)
- Graph execution, SSE streaming, dependency bus, child-report lane
- The raw `enqueue_message` path — repurposed as **internal-only** (reports, nudges, `[JOB_EVENT]` delivery, compaction, system messages)

**Changes — front (entry layer):**
- The fan-in entry points that today enqueue a raw message switch to **creating a Job** (against new or existing instance) that dispatches a driving message. The full inventory:
  - `daemon/sources/registry.py:822` — **the single external-source chokepoint**. Every chat adapter (Slack, Telegram, Discord, …) routes here as `enqueue_message(source=f"{source_id}:{external_user_id}")`. Converting this one call covers *all* external chat sources at once; future adapters inherit the Job path automatically.
  - `daemon/sources/adapters/scheduler.py:720` — scheduler (`source="scheduler"`).
  - `daemon/routers/messages.py:129` — HTTP `POST /messages`.
  - `daemon/tools/instance.py:594` — the `send_message` tool (agent-to-agent).
  - `daemon/tools/job_queue.py:742` — `job_continue` (already job-shaped; aligns to the same model).
- Two creation modes, one verb: `job_create` (new instance) / `job_continue` (existing instance). Both produce JobItem + driving Task.

**Changes — facade (read layer):**
- `WorkResolver.list_work` / `resolve_work` → **JobItem-only**. Drop the Task union, the `(instance_id, message_id)` dedup, the active-orchestration promotion.
- F10 drift → trivially correct: a Job's driving Task already carries `work_id == job_id` (enforced on dispatch this session).

**Deletable after cutover:**
- `_kind_from_task_type` turn/report split in the facade, the Task branch of `list_work`, the promotion pass, the virtual-job dedup.

---

## 4. Key Design Decisions (must be settled before coding)

1. **Fast, streaming dispatch.** A message-Job must NOT wait on `job_processor`'s poll loop. The HTTP handler creates the JobItem, dispatches its driving message **inline** (the existing enqueue path), and streams the resulting Task execution directly. The processor poll remains for queue admission / recovery only. *Design: a "direct-dispatch" fast lane vs the background poll lane.*
2. **Internal messages stay raw.** Reports, nudges, `[JOB_EVENT]` watcher delivery, compaction, system messages keep `enqueue_message` with no JobItem. They are internal; the facade never surfaces them. The message path does not die — it becomes internal-only.
3. **Per-instance serialization via the job queue.** Today the worker pool serializes turns per instance. With message-Jobs, that serialization must be expressed through the job queue (per-instance `queue_id`, concurrency=1) so two message-Jobs on one instance never double-execute. Maps onto existing queue machinery — but must be wired and tested.
4. **Retry policy per submission type.** Chat/continuation Jobs: `retry=0`, no dead-letter. Orchestration Jobs: keep retry. The JobItem model already carries per-job retry config — this is a policy flag, not new structure.
5. **One submission = one JobItem = one driving Task = one turn.** A conversation yields N JobItems (one per submitted turn). Granularity is the same as today's per-turn Task rows, just at the Job level — and JobItem status is already instance-authoritative, so the parent-mid-flight `processing` case works for free.

---

## 5. Migration Order (single release, sequenced for safety)

Ordered so each step is independently testable; the whole sequence ships together.

1. **Enforce `work_id == job_id` everywhere a Job dispatches.** Already done on the dispatch path this session; extend to any remaining JobItem-bearing entry. This is the prerequisite that makes the facade collapse safe.
2. **Add the message-Job entry points behind a feature flag.** `POST /messages` and the `send_message` tool gain a "create a Job and dispatch" mode. Both modes (raw message / message-Job) coexist temporarily, selected per-route or per-flag. The Job fast-dispatch (inline enqueue + stream) lands here.
3. **Wire per-instance serialization + retry policy** for message-Jobs (decision §4.3, §4.4). Test the no-double-execution invariant.
4. **Convert each fan-in entry point** to the message-Job path, one at a time, each validated: the external-source chokepoint (`sources/registry.py` — covers Slack/Telegram/Discord/… in one conversion), the scheduler adapter, the HTTP `POST /messages` route, the `send_message` tool. (`job_continue` already aligns.)
5. **Flip the facade to JobItem-only** (`list_work` / `resolve_work`). Internal raw messages are invisible to it by construction.
6. **Cut over** (flag default on), run the full E2E + regression suite, monitor.
7. **Delete the old path**: raw-message fan-in code, the Task union in the resolver, the dedup, the promotion pass. This is the payoff — net deletion.

---

## 6. Risks & Mitigations

| Risk | Mitigation |
|---|---|
| Streaming latency regression (message-Job vs direct enqueue) | Fast-dispatch lane (§4.1) + a latency E2E that asserts sub-poll startup |
| Double-execution if per-instance serialization is mis-wired | Explicit no-double-execution test on a contended instance before cutover |
| Internal messages accidentally surfaced as jobs | Facade is JobItem-only by construction; add a test that a raw internal message never appears in `list_work` |
| Retry/dead-letter firing on chat traffic | Per-submission retry policy (§4.4); test that chat-Jobs never dead-letter |
| Migration too big to land cleanly | Single release but **sequenced** (§5) with a flag — each step shippable; the flag lets us revert the front without touching the backend |

---

## 7. Exit Criteria

- Every public entry point creates a Job; no public path enqueues a raw message. This includes the external-source chokepoint (`sources/registry.py` → Slack/Telegram/Discord/all adapters), the scheduler, HTTP `POST /messages`, and the `send_message` tool.
- `WorkResolver` is JobItem-only; the Task union, dedup, and promotion pass are deleted.
- Internal messages (reports/nudges/`[JOB_EVENT]`/compaction) still use the raw path and are invisible to the facade.
- A parent mid-orchestration reports `processing` with no special-case code (instance-authoritative status).
- `job_continue` is the single continuation verb for new and existing instances.
- Full E2E + regression suite green; the diff is net-negative (more deleted than added).

---

## 8. Out of Scope

- Redesigning the message queue / Task / worker_pool internals.
- The dependency bus or child-report lane.
- Changing graph execution or SSE semantics.
- Frontend rework beyond adapting to a job-only work list (the implementer decides how much UI churn is needed).

---

## 9. Notes for the Implementer

- This builds on the linkage fixes already landed this session (`work_id == job_id` on dispatch, instance-authoritative status). Audit those are solid before starting — they're the foundation the collapse rests on.
- The biggest unknown is §4.1 (fast streaming dispatch). Prototype that first; if the Job can't stream as cleanly as direct enqueue, the whole proposal needs revisiting before committing.
- Resist the urge to also "clean up" the backend while doing this. Keep the backend frozen so the diff stays focused on entry + facade.
