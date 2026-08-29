# Instance-Tools Stability Backlog (post 2026-08-29 session)

Snapshot of the stability backlog captured on `latest` @ `4e10980b` (2026-08-29), immediately after six instance-tools batches landed this session (all gates PASS). Self-contained: causes, fix directions, effort/impact, and merge-commit traceability only — no implementation code.

**The running daemon predates all six batches — deploy strongly recommended** (see [Deployment note](#deployment-note)).

---

## Context

> **Status update (2026-08-29, post quick-wins #2):** Items 1–6 have **LANDED** — merge `23bcf1df` (send-gate terminal unblock, bus-fire TOCTOU re-purge, buffer mocks family RESOLVED, subtree_status queued/running columns, notify symmetry complete, hasattr probe). Remaining shelf: items 7–12, with item 7 (`system:*` boundary validation) now the recommended next pick, item 8 the next feature-grade item, item 11 still drill-env. Newly backlogged this batch: `messages.py:601-605` dict-attr 500 (pre-existing, masked by mocks); IN-list preservation test; grouped-SELECT query-plan check.

### Landed this session — six batches, all gates PASS

| # | Batch | Merge commit | Contents |
|---|-------|--------------|----------|
| 1 | agent-instance-tools | `5e98b06a` | `send_message` running-injection + terminal-revive; `subtree_messages` tool |
| 2 | instance-quick-wins | `ad1018e9` | `set_injection` source provenance; revive-once guard; planner/tester opt-in |
| 3 | waiting-children-watchdog | `22d03844` | hourly hang detection + wake notices |
| 4 | W1 serialization | `31981df5` | `injected_message`/`context_kind`/`source` through `serialize_message`; structured D12 |
| 5 | subtree_status | `2ba78315` | token-cheap subtree overview + orphan-guarded pending counts |
| 6 | reconciler wedge-fix | `4e10980b` | Pattern (d) per-work linkage; alive-guard; sub-shape (c) carrier revival; watchdog wedge backstop |

Reference: gate records in `.agents/tester/RESULTS/`, decisions in `.agents/shared/planning/agent-instance-tools/decisions.md` (full list under [References](#references)).

### Incident 2026-08-29 — why this backlog exists

During this session's gate, the **gate tester wedged for 2h44m**:

- **Incident root:** a child worker got stuck non-terminal **PRE-LLM** on DB QueuePool exhaustion — pool 5+10 saturated by a 75-worker fan-out, with 261 system warnings logged. This is backlog item 11.
- **Complication:** the subsequent terminate cascade hit a **bus-fire TOCTOU**, leaving a **stranded carrier** on the terminated instance. This stranded pair is the source of backlog items 1 and 2 below.

```mermaid
sequenceDiagram
    participant Caller as Caller (send_message)
    participant GR as Gate Runner (tester fan-out)
    participant CW as Child Worker (agent instance)
    participant QP as DB QueuePool (5 base + 10 overflow)
    participant TC as Terminate Cascade
    participant BUS as Dependency Bus
    participant MQ as DB Message Queue

    rect rgb(232, 244, 255)
        Note over Caller,MQ: Phase 1 - Fan-out and pool saturation
        GR->>CW: Fan out ~75 concurrent child workers
        CW->>QP: Request DB connections
        Note over QP: Pool saturates (5 base + 10 overflow)<br/>261 system warnings logged
        Note over CW: One worker wedges non-terminal PRE-LLM<br/>(never reaches LLM call)
        Note over GR: Gate tester wedges 2h44m
    end

    rect rgb(255, 235, 235)
        Note over Caller,MQ: Phase 2 - Terminate cascade vs TOCTOU race
        Note over TC: Terminate Cascade invoked on wedged instance
        TC->>MQ: Purge message queue
        BUS->>BUS: fire_for_terminated_target t=.404<br/>terminated-check is STALE
        BUS->>MQ: Enqueue message t=.437<br/>(after purge already removed queue)
        MQ->>MQ: Mint carrier task t=.448
        TC->>CW: Instance marked TERMINATED t=.453
        Note over MQ: Stranded: message + carrier pair<br/>on terminated instance
    end

    rect rgb(255, 250, 230)
        Note over Caller,MQ: Phase 3 - Revive attempt blocked (later)
        Caller->>MQ: send_message revive attempt
        MQ--xCaller: BLOCKED by send-gate - counts stranded carrier<br/>as in-progress ('Pending: 1, Processing: 0')
        Note over Caller,MQ: Revive blocked forever - strands backlog items:<br/>1 (send-gate), 2 (TOCTOU guard)
    end
```

---

## Backlog — ranked by ease × impact

Reading guide: Effort scale `XS < S < M < M+`; Impact ⭐⭐⭐ = highest. Priorities 1–3 carry medals as the session's top-ranked picks.

| Priority | Item | Cause | Fix direction | Effort | Impact |
|----------|------|-------|---------------|--------|--------|
| 1 🥇 | Send-gate: ignore carriers on terminal instances | Stranded pending carrier on a terminated instance blocks `send_message` (revive path) forever — reports `Pending: 1, Processing: 0`; never self-heals | Send-gate counts only carriers on non-terminal instances as in-progress | S | ⭐⭐⭐ protects revive capability after cascades |
| 2 🥈 | Bus-fire terminate-cascade TOCTOU guard | Dependency-bus fire enqueues after cascade purge removed the queue → stranded message+carrier rows (fire .404 → enqueue .437 → carrier .448 → terminated .453) | Re-check target-terminated inside `fire_for_terminated_target` (skip enqueue) or re-purge post-fire | S | ⭐⭐⭐ kills stranded-pair class at source |
| 3 🥉 | 53-test `buffer_response_header` mock fix | Base commit `85ae6e72` (X-LLM-Buffer-Response header) added production config reads without updating 5 test files' mocks → 53 quarantined failures, noise in every gate | Add attribute to the 5 mocks or `getattr`-harden prod reads (mechanical; ownership: CF-125s lineage) | XS | ⭐⭐ green-tree hygiene |
| 4 | subtree_status queued/running columns (Finding-3) | Pending column shows queued-only → busy RUNNING child renders 0, parents misread idle | Rename to `queued` + add `running` count (same batched query) | S | ⭐⭐ prevents wrong coordination decisions |
| 5 | Sub-shape (b) `notify_work` symmetry | `task_only_create` mints carrier without notify → delivery waits for next poll (delay not wedge; backstop compensates) | `notify_work` after commit, mirroring the c_revival pattern | S | ⭐⭐ faster wakeups |
| 6 | Mock-aware branches in `_has_live_carrier_task` | `except AttributeError → True` fallbacks shaped for test mocks → breadth in a wedge predicate | `hasattr` probe or proper fixture seam | S | ⭐⭐ cleanliness in correctness-critical predicate |
| 7 | `system:*` boundary validation (F2 family) | No prefix validation at HTTP boundary → forged `source='system:*'` gets internal-branch behavior; inert today (loopback) but blocks the live rung | Reject `system:*` / `internal_*` prefixes on external input | S | ⭐⭐⭐ when going live |
| 8 | WC-target HTTP waking path | User messages to WC-parked instances land in RAM FIFO, never wake parent (hourly watchdog only backstop) | Route WC-target HTTP sends through `enqueue_message` wake primitive | M | ⭐⭐⭐ best user-facing item |
| 9 | SSE-echo provenance | Live SSE bubbles construct `HumanMessage` without kwargs → no source/context flags while persisted view has them | Thread `entry_source` into echo `additional_kwargs` | S | ⭐⭐ UI parity |
| 10 | Structural paired-Task sync-cancel | Cancel writes JobItem terminal but leaves paired Task pending ≤5min (reconciler heals; read-side guard papers over) | `reconcile_terminal_task` synchronously in terminal-write paths | M | ⭐⭐ data hygiene |
| 11 | QueuePool sizing under fan-outs | Pool 5+10 saturated at ~75 concurrent workers → children wedge pre-LLM (the incident root) | Pool sizing + acquire-timeout + fan-out caps — needs load-test evidence to tune | M+ | ⭐⭐⭐ prevents incident class at scale (drill-env task) |
| 12 | Hardening-only minors | Pattern (a) per-instance symmetry (verified non-wedging); PAUSED-revival symmetry (verified benign); docstring nits | Symmetry cleanup + docstring fixes — polish only, both shapes verified non-wedging/benign | XS–S | ⭐ polish |

---

## Recommended next batch

```mermaid
flowchart TD
    %% Entry point — recommended before any backlog work, but independent of it
    Deploy["Deploy first — restart the running daemon to pick up all six landed batches (strongly recommended, independent of backlog)"]

    subgraph QW["Stability quick-wins #2 batch — all S/XS effort, wedge/stability seam, no schema changes, ~1 dev cycle + gate"]
        direction TB
        I1["1. Send-gate carriers"]
        I2["2. Bus-fire TOCTOU guard"]
        I3["3. 53-test mock fix"]
        I4["4. subtree_status columns"]
        I5["5. notify_work symmetry"]
        I6["6. _has_live_carrier_task cleanup"]
        I7["7. system:* prefix validation — optional, only if pre-closing the F2 gate"]
    end

    Deploy -->|"recommended order"| QW

    %% Two parallel follow-on tracks after the batch
    QW --> I8["Item 8: WC-target HTTP waking path — next feature-grade, user-facing, M effort"]
    QW --> I11["Item 11: QueuePool sizing under fan-outs — drill-env load-test track, needs load-test evidence, M+ effort"]

    %% Standalone polish item — attachable anywhere
    I12["Item 12: hardening-only minors — polish, fold into any batch"]
    I12 -.->|"fold into any batch"| QW
    I12 -.-> I8
    I12 -.-> I11

    classDef optional fill:#fff8e1,stroke:#f0a500,stroke-dasharray: 4 3;
    class I7 optional;
```

- **"Stability quick-wins #2" = items 1–6** (+ item 7 if pre-closing the F2 gate is wanted): all S/XS, all in the wedge/stability seam proven by this session, no schema changes, ~1 dev cycle + gate.
- **Item 8** — next feature-grade item (user-facing).
- **Item 11** — drill-env load-test task, not a quick win.

---

## Dropped — record, do not resurrect without user request

Numbering below refers to the session's proposal list, **not** the backlog priorities above.

| Proposal (session list) | Dropped because |
|--------------------------|-----------------|
| #6 broadcast send | user-dropped |
| #2 `resume_instance` tool | authority decision: operator-only |
| #9 mid-run progress taps | `subtree_messages` partially covers |

---

## Deployment note

The running daemon predates all six batches. Restart picks up:

- both watchdogs (hang + wedge)
- carrier revival — wedged parents self-heal ≤5min
- revive-once guard
- `subtree_status` / `subtree_messages`
- structured D12
- provenance
- paused-race fix `f5e4b79a`
- CF streaming fix

Optional 2-row cleanup if reviving wedged tester `77ab8ab2` is ever needed: task `25725` + `message_queue` row `f6ff733b` (daemon paused).

---

## References

- Gate records (`.agents/tester/RESULTS/`):
  - `2026-08-26-agent-instance-tools-phase1-gate.md`, `2026-08-27-agent-instance-tools-phase2-gate.md` — batch 1, agent-instance-tools (`5e98b06a`)
  - `2026-08-27-instance-quick-wins-gate.md` — batch 2, instance-quick-wins (`ad1018e9`)
  - `2026-08-27-waiting-children-watchdog-gate.md` — batch 3, waiting-children-watchdog (`22d03844`)
  - `2026-08-28-injection-marker-serialization-gate.md` — batch 4, W1 serialization (`31981df5`)
  - `2026-08-28-subtree-status-tool-gate.md` — batch 5, subtree_status (`2ba78315`)
  - `2026-08-29-reconciler-wedge-fix-gate.md` — batch 6, reconciler wedge-fix (`4e10980b`)
- Decisions: `.agents/shared/planning/agent-instance-tools/decisions.md`
---

## Newly documented this session

### `subtree_messages` final-message visibility

A child instance's FINAL report message (the verbatim report the dispatcher depends on) is not visible via the `subtree_messages` tool. Ref: agent-instance-tools `subtree_messages`; surfaced during the 2026-08-29 Pattern (f) batch.

### JobFeedbackObserver post-restart empty-queue anomaly

Investigation concluded 2026-08-29; **DISPOSITION: documented, not fixed.**

**Symptom**: After daemon restart, `JobFeedbackObserver` processes 0 events while the `events` table persists `instance_lifecycle` events in the same window. Live-log evidence (`~/agents-ensemble/data/logs/ensemble.log`): `JobFeedbackObserver: waiting for events...` repeated at 5-min cadence (matches `_health_check_interval=300s`, observer.py:324) for ~90 minutes before a brief activity burst at 19:42-19:50. From that point on, `JobFeedbackObserver: no events in 600s` fired at 5-min cadence continuously (~190× in the current log).

**Mechanism observed** (file:line evidence):

- `daemon/services/event_bus.py:155-191` — `EventBus.create_event(...)` persists to DB THEN awaits `_broadcast_to_global(...)`. The two operations share the same coroutine.
- `daemon/services/event_bus.py:286-307` — `subscribe_all` overwrites `_global_subscribers[subscriber_id]` with a fresh `asyncio.Queue`; old references become orphan.
- `daemon/services/event_bus.py:317-351` — `_broadcast_to_global` iterates `_global_subscribers.items()` via `list()` snapshot, `queue.put_nowait(event)` per subscriber. `QueueFull` swallowed with WARNING.
- `daemon/services/job_feedback_observer.py:501-516` — `start()` registers queue via `subscribe_all`, then `asyncio.create_task(self._event_loop())`.
- `daemon/services/job_feedback_observer.py:579-630` — `_event_loop` `await asyncio.wait_for(self._queue.get(), timeout=300)`.
- `daemon/services/event_publisher.py:38-73` — sole publisher of `kind=EventKind.INSTANCE_LIFECYCLE`; goes through `event_bus.create_event`.
- `daemon/manager.py:738` — `self._event_bus = EventBus(...)` set ONCE in `InstanceManager.__init__`; never reassigned in the source tree (verified across grep of `manager.py`, `api.py`).

**Why root cause is NOT clear**:

1. Diagnostic tests `tests/job_queue/test_job_feedback_observer_eventbus_pairing.py` (9 tests, all passing) exercise the real `EventBus` + real `JobFeedbackObserver` end-to-end. The mechanism is deterministic: events go to the queue every time.
2. The 14 persisted events are not reproducible from any other path — `stale_task_recovery.py:936` writes its own non-`instance_lifecycle` kinds; `message_processing_errors.py:266` is a `kind="error"` fallback for a missing bus.
3. The 90-minute zero-receipt window after restart is precisely the shape expected if a *second, untracked* `EventBus` were receiving the publishes — but no such second instantiation was found in the source tree.

**Suspected root causes (ranked)**:

1. **Hidden second `EventBus`** (highest): production deploy path may load `_archived/event_bus.py` or instantiate `EventBus` via reflection. Not visible in the static call graph.
2. **Bus recreation mid-flight**: nothing in source reassigns `manager._event_bus`, but a `_archived` copy or runtime import could.
3. **`event_repo.create_event` direct path with `kind="instance_lifecycle"`** — ruled out by source search; no path does this.
4. **Queue overflow** — would produce `WARNING f"Global subscriber job_feedback_observer queue full"` in the log; not seen.
5. **Slow startup race** — pre-loop backlog would still get drained on first `await self._queue.get()`; cannot explain 0 events over 90 minutes.

**What would disambiguate**:

1. `grep -E "Global subscriber job_feedback_observer queue full" ~/agents-ensemble/data/logs/ensemble*.log` → should return 0; if any hits, hypothesis 4 becomes the root cause.
2. `SELECT kind, count(*) FROM events WHERE created_at > '<restart time>' GROUP BY kind` — confirm the 14 events all have `kind = 'instance_lifecycle'`. If any other kind persists alongside, hypothesis 3 has merit.
3. Temporarily add a `logger.debug("_broadcast_to_global put: %s -> %s", subscriber_id, queue)` at `event_bus.py:347` on a canary and re-trigger the symptom; the log would name the exact queue the publish lands on and whether the observer holds it.
4. Reproduce in a unit test using the **full** `daemon.manager.InstanceManager` import graph (the current tests stub `event_bus=` — the diagnostic gap is exactly that we don't exercise the production wiring order).

**Risk**: A wrong fix here is worse than a documented gap. The observer's 5-site `_finalize_job_db_sync` canonical-writer role (job-queue repo finalize conventions, see critical notes "F9+F16" and "`_finalize_job_db_sync` owner") must not be disturbed by a speculative change to its event-source.

**Acceptance status**: Investigated; no fix proposed. Diagnostic test file: `tests/job_queue/test_job_feedback_observer_eventbus_pairing.py`.
