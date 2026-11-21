# Plan Overview — schedule-fire-notify

## Coordination verdict (read first — no overlap flag)

**NO task in this plan touches `middleware/`, `metrics/` (the tool-metrics shared family), or any `frontend/` file.** The in-flight tool-metrics lane (job `f470ca35`) owns middleware/metrics/FE; this plan owns `daemon/sources/adapters/scheduler.py`, the enqueue/messaging seam, and a **NEW sibling metrics module** (`daemon/services/schedule_fire_notify_metrics.py`). The new metrics module is **ADJACENT but file-disjoint** — a new file, not an edit to any existing metrics surface. Release-slot sequencing (candidate `v0.31.23`; tool-metrics may claim it first) is decided by W9 at stage time, not here.

**Process constraints (disclosure):** staged release at END of lane; candidate slot `v0.31.23` (contested — W9 decides); NO live promote (Emperor's ceremony); `rollback_safe=true` standing rule; any migration is additive-only (and this plan recommends ZERO migrations — see decisions.md item d).

---

## Objective

When a scheduled task **fires and its job is dispatched**, the **instance that created the schedule** receives a best-effort notification carrying the **work handle**, so the creator can decide to watch the run and gather info. Motivating evidence: `tool-metrics-build-kickoff` fired `2026-11-21T01:00:03Z`, job created `01:01:07Z`; creator (ari) learned only by polling at `03:00Z`. Today there is no path from firing back to the creator.

Emperor's directive (verbatim): *"When you (the instance that call schedule job-task). You should get a message inform when the scheduled task begin running. So you can decide to watch and get info."*

---

## Scope

**IN:**
- Capture the creating instance id at schedule creation and persist it with the schedule.
- On execution-start (fire → successful dispatch), notify the creator with a minimal fixed field set incl. the work handle.
- Delivery via the existing `enqueue_message_job` lane with a NEW reserved `system:` source string.
- Best-effort, off the scheduler's critical path; cheap sent/dropped counters.
- Minimal agents-side note (Ari's tools_note only), tests, acceptance pack, PACKS.md registration.

**OUT (explicit):**
- `reuse_instance` schedules where the wake already lands in the creator (self-alarm) — **no notify** (Req 7). The deferred-delivery site is exactly this lane — **skip** (see "Notify-site list").
- No schedule body / brief echo in the notice (Req 1).
- No new table, no migration (recommended), no polling loop, no dependency-watcher arming, no spawn.
- No change to the fire path's dispatch semantics; no change to `execution_callback` contract.
- No live promote; no push; no changes to middleware/metrics/FE.

---

## Design

### Notify seam (exact sites)

The work handle exists only at the two `enqueue_message_job` call sites. **Notify-site list (exact and complete):**

| site | file:line | verdict | reason |
|---|---|---|---|
| direct dispatch | `daemon/sources/adapters/scheduler.py:2723` (result at `:2731`) | **COVER** | fire becomes a job here; `result.job_id`/`result.message_id` in hand; this is the new_instance lane where the creator is uninvolved |
| deferred delivery | `daemon/sources/adapters/scheduler.py:2109` (result at `:2116`) | **SKIP** | `_defer_delivery` is **reuse-only** — `_resolve_deferrable_target` returns `None` for new_instance by construction (`:2428`, `:2437-2438`); the deferred message IS the wake into the pinned creator, which is the creator itself for self-created reuse schedules (self_alarm precedent, research-scheduler.md §Q3). Notifying would double-notify (violates Req 7). |

**Deferred-site tension resolved:** explorer A flagged `:2109` as "mandatory coverage", but the code evidence shows deferral requires a resolved deferrable target that is reuse-only; the deferred recipient IS the creator. Prior-art `scheduler-deferred-alarm-delivery` said "cover both sites" for a generic *fired-echo* surface — that does not apply to a *creator-notify* surface where the deferred path's recipient is already the creator. The plan therefore covers exactly ONE site (`:2723`).

Additionally, the direct site must be **gated**: notify only when a creator id is present AND `creator_instance_id != resolved target instance_id`. For `new_instance` the target is a fresh instance of the target agent (`:2660`), so creator ≠ target → notify. For `reuse_instance` the mapper resolves the pinned caller (the creator) → creator == target → skip. REST-created schedules have no creator → skip. This implements Req 7 by identity, not by a brittle `instance_mode` string check.

### Delivery call + message shape

A new helper `notify_schedule_fire(...)` (in `daemon/services/schedule_fire_notify.py`) is invoked fire-and-forget immediately after the successful direct enqueue at `scheduler.py:2731`. It calls the existing lane:

```
manager.enqueue_message_job(
    instance_id=<creator_instance_id>,   # existing instance only — no mapper, no spawn
    message=<compact notice>,
    source="system:schedule_fire_notify",
    priority=0,                           # system
    metadata={"schedule_fire_notify": True},
)
```

**Notice fields — EXACT, nothing else** (Req 1):
- `label` — `SourceConfig.name` (reachable as `self.config.name`; research-scheduler.md §Q1)
- `fired_at` — the fire's trigger time (from `metadata["scheduler.trigger_time"]`, built at `:2517-2539`)
- `target_agent` — `self._agent`; `instance_mode` — `self._instance_mode`
- `recurrence` — `self._schedule_type` / config `recurrence` echo
- work handle — `job_id`, `message_id` (`result.job_id`, `result.message_id`), and `target_instance_id` (the resolved `instance_id` at `:2661`)

**No schedule body, no brief, no `message` config key echo.** The compact one-line body is scrubbed for secrets for free by `scrub_enqueued_body` inside `_prepare_enqueued_message` (research-messaging.md cross-cutting notes).

### Source string + provenance

New reserved literal `system:schedule_fire_notify`, minted only inside daemon code. Because it starts with `system:`, it clears ALL classification gates with **zero constant edits / zero registration ceremony** (research-messaging.md §3, §4):
- `is_reserved_source` → True via the `"system:"` prefix family (`constants.py:1278`, pinned at `tests/unit/routers/test_source_reservation.py:108-140` which already contains `"system:"`).
- `MessageQueue.type` → `MessageType.SYSTEM` (`INTERNAL_SYSTEM_TYPED_SOURCE_PREFIXES` includes `"system:"`, `constants.py:1488`).
- `INTERNAL_STAMPED_SOURCE_PREFIXES` includes `"system:"` (`constants.py:1401`) → HumanMessage stamped internal; SOURCE-U anchor never moves.
- `classify_user_origin` → `(False, "reserved-internal")` (reserved checked first, `upgrade_journal.py:5409-5410`) → **non-user-origin everywhere incl. the upgrade-gate window**. No `PendingWake` row, no recorded wake-source touch.
- NEVER bare `"api"` (DF-4 forgery lesson). Do NOT add `"scheduler"` to the stamp set — `tests/unit/test_memoir_wake_refresh.py:524` pins bare `"scheduler"` as deliberately absent/HUMAN; our notice uses a distinct `system:` literal and leaves the fire message untouched.

### Best-effort wrapper (never wedge the scheduler)

`notify_schedule_fire` is scheduled with `asyncio.create_task(...)` and its own exception guard — off the awaited fire path, zero added latency, zero failure coupling. Any failure is logged (structured, one line) and counted as dropped; never re-raised into `_route_via_job_queue`. In-file precedent: the mission-record mint at `scheduler.py:2712-2721` (fail-open `create_task` wrapper); cross-file precedent: `report_integrity_metrics.py:59-89` "a broken sink must never break the report path". Hold a strong reference to the created task (module-level set with done-callback discard) so it is not garbage-collected mid-flight.

### Creator capture

At the tool layer the caller id is already available (`caller_instance_id` closure, `daemon/tools/scheduling.py:163`; forwarded to `create_schedule`, `:197-201`), but `create_schedule` (`scheduling_service.py:710-715`) uses it for audit logging only and drops it (`:836-844`). Persist it as a **config-JSON key `creator_instance_id`** in `_build_config_json` (`scheduling_service.py:422-454`) from `caller_instance_id`. Rationale (decisions.md item d): zero migration, GC-archive survival (`SourceConfigArchive` snapshots `config` verbatim, `models.py:224-230`), prior-art `_defer_*` config-JSONB carriage. **Full-replace hazard:** `update_source_config` replaces the whole config dict — `task_schedule_update` must re-thread the key or it is silently lost. REST-created schedules (`daemon/routers/schedules.py:53-85`) carry no creator → key absent → skip notify.

### Counters (existing idiom)

NEW sibling module `daemon/services/schedule_fire_notify_metrics.py`, mirroring `report_integrity_metrics.py` exactly: lock-guarded module totals, `record_*` functions whose whole body is try/except (never raise, never block), `attach_sink(callable)`, `get_*` readers, test-only `reset_*`. Two strings: `schedule_fire_notify_sent_total` / `schedule_fire_notify_dropped_total`. **Fence note:** the metrics idiom places constant strings in `daemon/constants.py:1637`, but that file is content-SHA256 sealed (see Fences) — this plan defines the two strings as module-level constants in the NEW sibling file instead (fence-free, disjoint), and records the constants.py alternative as architect-gated in decisions.md item g.

### self_alarm skip rule

Skip notify when `creator_instance_id == resolved target instance_id` (identity rule above). This is the reuse/self-alarm lane whose wake IS the message. No double-notify.

### Loop-safety guarantees (one hop, bounded)

1. The notice is **message-only by construction**: it calls `enqueue_message_job` with an existing `instance_id`; that lane has no create-instance branch and fail-closes on a missing target (`instance_messaging.py:2488-2493`, check-vs-commit backstop `:2700-2710`). No mapper call → zero spawn surface.
2. The notify helper **never** writes a dependency-watcher row, never re-arms anything, never creates a schedule. A scheduled task is created only by an explicit agent tool call.
3. `system:` source → downstream classifiers treat the notice as a terminal-traffic internal notice, not a user turn; one PROCESS_MESSAGE turn max. Graph loop breaker (`graph.py:214`, threshold 3) bounds any tool-looping in that turn. Dependency-bus CAS one-shot and child_reports dedup remain untouched.

---

## Fences That Constrain the Design (grep-fence check, applied 2026-11-21)

Applied the loaded `grep-fence-check-before-design-substrate` skill to every substrate decision. Verified against THIS worktree (`0b5624dc9`).

1. **`daemon/constants.py` is content-SHA256 SEALED** — `tests/test_chart_image_delivery_e2e.py:1488` (`SEALED_SHA_BASELINES`, enforced `:1610-1630`) and `tests/unit/test_anomaly_report_duty_prompts.py:166` (enforced `:511-525`). The tripwire's docstring (`tests/test_bind_retry.py:445`) states the file "is SEALED by content-sha256 pins". **Consequence:** adding metric constants there requires a deliberate re-pin of BOTH rows in the same lane (a ceremony, not a silent edit). Base HEAD sha is `6c1628b8…`, matching neither pin (`5ce5a419…`, `464a2459…`) — the pins are ALREADY stale at base, so this is a pre-existing condition; this lane must not make it worse. **Design response:** define the two metric strings in the NEW unsealed sibling module (no constants.py edit). Any constants.py edit is architect-gated and must re-pin both seals.
2. **`agents/ari/workflow.md` is content-SHA256 SEALED** — `tests/test_chart_image_delivery_e2e.py:1454` (`a0f5a382…`) and `tests/unit/test_anomaly_report_duty_prompts.py:153` (`26f68f44…`). `agents/ari/tools_note.md` is NOT sealed. **Design response:** the minimal agents-side note goes in `tools_note.md` only; do not touch the sealed `workflow.md`.
3. **`system:` source needs ZERO constant edits** (clean substrate): `"system:"` is already in `RESERVED_SOURCE_PREFIXES` (`constants.py:1278`), `INTERNAL_STAMPED_SOURCE_PREFIXES` (`:1401`), `INTERNAL_SYSTEM_TYPED_SOURCE_PREFIXES` (`:1488`), and the exact-set pin at `tests/unit/routers/test_source_reservation.py:108-140`. `is_reserved_source("system:xyz") is True` is pinned at `:155`. No fence trip.
4. **Do NOT edit the stamp sets**: `tests/unit/test_source_stamp_never_stamped_guards.py:100-101` and `tests/unit/test_memoir_wake_refresh.py:524` pin bare `"scheduler"` as never-stamped/HUMAN. Our notice uses a distinct `system:` literal; the fire message is untouched.
5. **Creator capture substrate is fence-free**: no exact-set pin on `source_configs` columns; no exact-dict pin on real `_build_config_json` output. The exact-dict pin at `tests/unit/tools/test_self_alarm_tools.py:1191` is on a hand-built `FakeRow` fixture, not a live config builder — adding a `creator_instance_id` key does not trip it. Both config-JSON key and additive column are legal; config-JSON key recommended (decisions.md d).
6. **PACKS.md is not content-sealed** — hits are docstring references only. Worktree copy is 1.65 MB (over grep tool cap); cross-check the MAIN checkout copy (1.48 MB) for section registration, per house rule; worktree copies branched pre-registration can lag.
7. **Resource-control seam fence is out of scope** — `tests/test_resource_control_seam.py:300` (`_NAME_RE = action_by|acted_at`) forbids naming those on `resource_pid_map`; our feature touches no resource-control surface.

---

## Phases / tasks (small-medium lane; tasks kept in overview — no phase files needed)

Single-ownership invariant: every file edit is claimed by exactly ONE task. No forward symbol references — every symbol a task's exit criterion names is created in or before that task.

### Phase 1 — Implement
- **T1 (creator capture)** — `daemon/services/scheduling_service.py`: add `creator_instance_id` to `_build_config_json` (`:422-454`) from `caller_instance_id`; re-thread the key through the update path so full-replace does not drop it. `daemon/tools/scheduling.py`: ensure `task_schedule_update` preserves the key. Exit: creator id survives create+update round-trip. (Owns both files.)
- **T2 (notify helper)** — NEW `daemon/services/schedule_fire_notify.py`: `notify_schedule_fire(...)` builds the exact message, stamps `system:schedule_fire_notify`, calls `manager.enqueue_message_job`, wraps all in try/except + counters; strong-ref task set. Exit: helper unit-testable with a mock manager; no spawn/mapper call. (Owns the new file.)
- **T3 (metrics sibling)** — NEW `daemon/services/schedule_fire_notify_metrics.py`: lock-guarded sent/dropped totals, `record_*` no-op-safe, `attach_sink`, `get_*`, `reset_*`, module-level string constants. Exit: counters increment and never raise. (Owns the new file.)
- **T4 (seam hook)** — `daemon/sources/adapters/scheduler.py`: after the successful direct enqueue at `:2731`, if creator present and creator ≠ target, `asyncio.create_task(notify_schedule_fire(...))` with strong ref. Do NOT touch `:2109`. Exit: fire on new_instance enqueues one notice; reuse/deferred/REST enqueue zero notices. (Owns the adapter file.)
- Exit criterion uses symbols created in T1–T3 only (no forward refs).

### Phase 2 — Tests
- **T5 (unit)** — `tests/test_scheduler_adapter.py` (seam gating: cover/skip matrix), `tests/test_scheduler_api.py` (creator capture round-trip), NEW `tests/unit/services/test_schedule_fire_notify.py` (message shape, source, no-spawn, counters, best-effort no-raise). Reuse `mock_manager` fixture (`test_scheduler_api.py:22-129`). (Owns these files.)
- **T6 (integration)** — `tests/integration/test_scheduled_tasks_e2e.py`: one stub seam (`spawn_instance_with_mcp`, `:20-26`), assert notice MessageQueue row with `source=system:schedule_fire_notify`, `type=SYSTEM`, exact fields, no extra schedule row. (Owns this file.)
- **T7 (pack + registration)** — NEW `tests/packs/scheduler_fire_notify_acceptance.sh` (copy the 15-line shape of `scheduled_tasks_acceptance.sh`, `set -euo pipefail`, `ENSEMBLE_SELF_ENV=dev`, outer `timeout 600`); register in `.agents/tester/PACKS.md` (cross-check main checkout copy). (Owns these files.)

### Phase 3 — Docs / closeout
- **T8 (agents note)** — `agents/ari/tools_note.md`: MINIMAL two-line mention — on receiving a `system:schedule_fire_notify` notice, Ari may watch the work handle or ignore it. Justification: the notice introduces a new message type with an actionable handle; two lines in the unsealed tools_note is proportionate. Do NOT touch sealed `agents/ari/workflow.md`. (Owns this file.)
- **T9 (closeout)** — staged release only; disclose candidate slot `v0.31.23` (W9 sequencing), no promote, `rollback_safe=true`, zero migrations. No file edits.

---

## Binding invariants (REJECT-grade)

1. **Never wedge the scheduler** — notify is fire-and-forget off the awaited fire path; failure logged/counted, never fatal.
2. **Message-only, never spawn** — notice goes to an existing instance via `enqueue_message_job`; no mapper call, no create-instance branch.
3. **Non-user-origin everywhere** — `system:` source; fail-closed in `classify_user_origin` incl. upgrade-gate window; no `PendingWake`, no recorded wake-source touch.
4. **No body echo** — notice carries ONLY the five field groups; no schedule body/brief/`message` key.
5. **One hop** — no watcher arming, no re-arming, no schedule creation from the notice.
6. **No double-notify** — skip reuse/self-alarm and deferred site (creator == target).
7. **Single ownership / no forward refs** — per the plan-contract invariants above.
8. **Fence discipline** — no edits to sealed `constants.py` / `agents/ari/workflow.md` without an explicit architect-gated re-pin; do not "fix" a fence by editing the fence test in-lane.

---

## Risks

- **Revive churn on dead creators** (daily schedule reviving a dead creator daily) — architect item (a); recommendation drop+log for terminal creators, PAUSED still queues.
- **Recurrence noise** (daily-skill-system-watch at 05:00) — architect item (b); recommendation per-fire tiny SYSTEM notice, no polling digest.
- **Full-replace update hazard** loses creator key — T1 mitigation (re-thread); regression test required.
- **Stale SHA seals** on constants.py/workflow.md are pre-existing red; this lane must not add new edits to them.
- **Worktree PACKS.md lag** — cross-check main checkout before claiming registration.
- **Release-slot contention** with tool-metrics lane — W9 decides at stage time.

---

## Success criteria

1. A `new_instance` schedule firing produces exactly ONE creator notice with the exact five field groups and a usable work handle, arriving without polling.
2. A `reuse_instance`/self-alarm fire and a deferred delivery produce ZERO notices.
3. Notice source is `system:schedule_fire_notify`, classified non-user-origin in all gates; no spawn, no watcher, no re-arm.
4. Notification failure never affects the fire (no exception escapes to `_route_via_job_queue`); sent/dropped counters observable.
5. Creator id survives create + update round-trip; REST-created schedules never notify.
6. All new tests pass (unit + integration-marked via pack); PACKS.md registered; staged release only, no promote.
