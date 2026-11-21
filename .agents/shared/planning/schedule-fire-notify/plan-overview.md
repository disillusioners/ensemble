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
- Identity-gated notify at BOTH dispatch sites — direct (`scheduler.py:2731`) AND at `_defer_delivery`'s DURABLE finalization (`FIRE_OUTCOME_DEFERRED_DURABLE`, `:2236`) — so a stranger-pinned reuse fire still reaches the creator (A1; ⚠️ F1: the hook point moved past the `:2140-2191` durable-evidence check — see "Notify-site list").
- Delivery via the existing `enqueue_message_job` lane with a NEW reserved `system:` source string.
- Best-effort, off the scheduler's critical path; cheap sent/dropped counters.
- Minimal agents-side note (Ari's tools_note only), tests, acceptance pack, PACKS.md registration.

**OUT (explicit):**
- Self-alarm-BOUND reuse schedules, where the wake lands in the creator itself — **no notify** (Req 7), enforced by the identity gate at BOTH sites (see "Notify-site list"). ⚠️ A1/DEC-9 AMENDED: the original blanket "skip the deferred-delivery site" is REVOKED — plain tool-created `reuse_instance` schedules are NOT bound to the caller at creation (only `self_alarm` binds, `_bind_schedule_to_caller`, `self_alarm.py:891-989`); on a first-fire mapping miss the mapper pins a FRESH STRANGER (`mapper.py:470-480`), so creator ≠ recipient on that lane and the creator WOULD have been silently skipped. Deferred stranger fires now NOTIFY.
- No per-schedule opt-out knob in v1 — deferred per DEC-2 (a single config-JSON key added later if real noise ever appears).
- No schedule body / brief echo in the notice (Req 1).
- No new table, no migration (recommended), no polling loop, no dependency-watcher arming, no spawn.
- No change to the fire path's dispatch semantics; no change to `execution_callback` contract.
- Manual-trigger fires (`scheduler.py:2901+`, `_execute_trigger` — "Manual triggers ALWAYS use immediate execution (no job queue)") bypass the job queue entirely and are outside notify scope.
- No live promote; no push; no changes to middleware/metrics/FE.

---

## Design

### Notify seam (exact sites)

The work handle exists at the two `enqueue_message_job` call sites. **Notify-site list (exact and complete — A1 AMENDED, both sites COVER with the SAME identity gate):**

| site | file:line | verdict | reason |
|---|---|---|---|
| direct dispatch | `daemon/sources/adapters/scheduler.py:2723` (result at `:2731`) | **COVER (identity-gated)** | fire becomes a job here; `result.job_id`/`result.message_id` in hand; recipient = the resolved target `instance_id` (`:2661`) |
| deferred delivery | `daemon/sources/adapters/scheduler.py:2109` (enqueue; result `:2116`) → hook at the **durable finalization** `:2236` (`FIRE_OUTCOME_DEFERRED_DURABLE`, block `:2196-2237`) | **COVER (identity-gated, durability-gated — ⚠️ F1)** | one hook covers BOTH of `_defer_delivery`'s callers uniformly (busy-caller `:2855` AND semaphore-timeout `:2358`); the hook fires AFTER the durable-evidence check (`:2140-2191`) — a taskless enqueue (real ids, NO pending Task) deletes the stranded row (`:2158-2188`), records FAILED, and re-arms with NO notify, so a VOID-HANDLE notice is impossible and no duplicate can race the real delivery; recipient = the resolved deferrable target; handle = `result.job_id`/`result.message_id` in hand |

**The SAME gate applies at BOTH sites:** notify iff a creator id is present AND `creator_instance_id != recipient instance_id`. A single fire takes exactly one path (direct XOR deferred) → at most ONE notice per fire; no double-notify is possible, and on the deferred path the gate skips precisely when recipient == creator (all self_alarm-bound schedules) — Req 7 preserved verbatim.

**Deferred-site tension resolved (⚠️ A1/DEC-9 AMENDMENT — SUPERSEDES the original blanket skip, retained as history):** the plan originally SKIPPED `:2109` on the premise "the deferred recipient IS the creator". That premise is true ONLY for self_alarm-bound schedules. The architect's decisive finding: plain tool-created `reuse_instance` schedules are **NOT bound to the caller at creation** — `create_schedule` uses `caller_instance_id` for audit logging only (`scheduling_service.py:710-715`, `:836-844`; zero `create_instance_mapping` calls in the service); only `self_alarm` binds (`_bind_schedule_to_caller`, `self_alarm.py:891-989` → `instance_mappings.agent_instance_id`, `models.py:115`). On first fire with a mapping miss and `force_new=False`, the mapper mints a **FRESH STRANGER instance** of the target agent and pins the mapping to it (`mapper.py:470-480`). So on the deferred path for a plain reuse schedule, creator ≠ recipient — the wake lands in the stranger and the creator gets nothing: the polling gap reborn (and prior art records deferred as the NORMAL shape for busy reuse callers). The blanket skip is therefore UNSAFE. Explorer-A's "cover both sites" was right for the wrong reason: coverage belongs to the identity gate, not to a blanket skip or a blanket cover. `new_instance` is unaffected: `_resolve_deferrable_target` returns None by construction (`:2428`, `:2437-2438`) → no deferral → the deferred hook is dead code for that mode.

**The gate is the IDENTITY rule (DEC-8, rationale CORRECTED per A4):** notify iff creator present AND `creator_instance_id != recipient instance_id`. For `new_instance` the recipient is a fresh instance of the target agent (`:2660`) → creator ≠ recipient → notify. For `reuse_instance` the recipient is the mapping-pinned instance: when the schedule is **self_alarm-BOUND** that pinned instance IS the creator → creator == recipient → skip (the wake IS the notify); when it is a **plain tool-created schedule** the pin is a first-fire STRANGER (`mapper.py:470-480`) → creator ≠ recipient → notify — exactly the commission's motivating gap. REST-created schedules have no creator → skip. NEVER an `instance_mode` string match (it would silence exactly the stranger-pinned case).

### Delivery call + message shape

A new helper `notify_schedule_fire(...)` (in `daemon/services/schedule_fire_notify.py` — ONE never-raise entrypoint, see F2 below) is invoked fire-and-forget at BOTH gated sites (A1, F1) — immediately after the successful direct enqueue (`scheduler.py:2731`) AND at `_defer_delivery`'s DURABLE finalization (`FIRE_OUTCOME_DEFERRED_DURABLE`, `:2236`, block `:2196-2237`) — i.e. AFTER the durable-evidence check (`:2140-2191`) has confirmed the delivery, so the handle is never void. It calls the existing lane:

```
manager.enqueue_message_job(
    instance_id=<creator_instance_id>,   # existing instance only — no mapper, no spawn
    message=<compact notice>,
    source="system:schedule_fire_notify",
    priority=0,                           # system
    metadata={"schedule_fire_notify": True},
)
```

**Dedup (A9 — recorded, NOT adopted):** recurring notices carry NO idempotency key; at-most-once-per-fire best-effort is accepted (a stable key would need per-fire identity `execution_id` + a DB round-trip on an informational notice; worst case is a duplicate tiny internal notice — harmless). Named opt-in if ever demanded: `idempotency_key=f"schedule_fire_notify:{execution_id}"`. The literal appears in exactly ONE mint site (the notify helper) — DEC-3 forgery-surface discipline.

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

`notify_schedule_fire` is scheduled with `asyncio.create_task(...)` and its own whole-body exception guard — off the awaited fire path, zero added latency, zero failure coupling. ⚠️ A2 (precedent CORRECTED): the mission-record mint (`scheduler.py:2713` → `mission_record_service.py:1334-1408`) is an **AWAITED `asyncio.to_thread` fail-open precedent — the fail-open POSTURE precedent, NOT a `create_task` one**; the `create_task` + strong-ref-set MECHANISM precedent is `warmup_pool.py:939` (module-level set + `add_done_callback(set.discard)`; adapter `:589`/`:699` are siblings). Cross-file posture precedent: `report_integrity_metrics.py:59-89` "a broken sink must never break the report path". Hold a strong reference to the created task so it is not garbage-collected mid-flight.

**Creator-status pre-check (A5 — DEC-1 mechanics):** INSIDE the off-path task body, BEFORE enqueue: read the creator row via `manager._instance_repository.get(creator_instance_id)` (`repositories/instance/repository.py:650`) wrapped in `asyncio.to_thread` (pattern `manager.py:3172-3188`) — the fire path pays neither the read nor the enqueue latency. Terminal (`COMPLETED|TERMINATED|ERROR|FAILED`) or absent row → skip delivery, one structured log line, `schedule_fire_notify_dropped_total`++ (mirrors `BoundCallerRefusal` doctrine: a confirmed-gone caller is refused, never revived — `mapper.py:89-129`, `:420-460`). PAUSED is NOT dead: its notice queues PENDING and is delivered on resume (`:2690-2699`). The enqueue lane auto-revives terminal targets by construction and has NO suppress flag (`enqueue_message_job` signature `:3131-3147`) — the pre-check is therefore the ONLY drop mechanism.

**Failure contract (A3 — LOAD-BEARING):** the guard must (i) log one structured line, (ii) increment `schedule_fire_notify_dropped_total`, (iii) **NEVER re-raise into `_route_via_job_queue`** — its `except … raise` (`:2750-2754`) would flip `_fire_outcome=FAILED` (`:2602-2604`), open the non-delivery episode (`:2892-2896`), and for a one-shot suppress consumption → 5s re-arm (`:823-834`): **a leaked notify exception would wedge the scheduler into a re-arm loop.** This clause is an explicit T2 exit criterion and gets its own test (A7-v). Accepted cost (architect): notify roughly doubles the fire's enqueue row-writes (MessageQueue + Task + Event + JobItem) — bounded by the execution-slot semaphore (`:583`, `:2837`), negligible at observed cadence. Daemon-restart loss of in-flight notices accepted (informational; the handle goes stale anyway).

**Never-raise ENTRYPOINT (F2 — reviewer condition; the stricter shape adopted under "simple always better"):** the A3 guard must not live only inside the notify coroutine. The adapter-side hook sites sit INSIDE the wedge regions — the direct site inside `_route_via_job_queue`'s `except…raise` (`:2750-2754`), the deferred site inside `_defer_delivery`'s failure ladder (`FIRE_OUTCOME_FAILED` legs at `:2100`, `:2126`, `:2182`) — so a SYNCHRONOUS raise from the identity gate or from `asyncio.create_task` itself would flip FAILED → 5s re-arm loop: the exact wedge A3 exists to prevent. The notify module therefore exposes **ONE never-raise entrypoint** — identity gate + `asyncio.create_task` + strong-ref registration + done-callback, WHOLE-BODY guarded; **nothing runs in the adapter outside it** (each adapter site is a single call into the entrypoint). Gate-time AND coroutine-time exceptions are both absorbed (A7-v extended per F2).

### Creator capture

At the tool layer the caller id is already available (`caller_instance_id` closure, `daemon/tools/scheduling.py:163`; forwarded to `create_schedule`, `:197-201`), but `create_schedule` (`scheduling_service.py:710-715`) uses it for audit logging only and drops it (`:836-844`). Persist it as a **config-JSON key `creator_instance_id`** in `_build_config_json` (`scheduling_service.py:422-454`) from `caller_instance_id`. Rationale (decisions.md DEC-4, ADOPTED): zero migration, GC-archive survival (`SourceConfigArchive` snapshots `config` verbatim as the sole reconstruction input, `models.py:221-230`, write `source/repository.py:829-841`), prior-art `_defer_*` config-JSONB carriage. **GC/dangling ruling (architect):** instance deletion is SOFT by default (`routers/instances.py:663-676`) → a dangling creator id hits the fail-closed `ValueError` → DEC-1 guard → dropped counter; no dangling-reference defect exists.

**Full-replace hazard — OVERSTATED in the original plan, re-framed as INSURANCE (A6):** `update_source_config` full-replaces at the repo layer (docstring at `daemon/repositories/source/repository.py:178-201`, NOT in `scheduling_service.py`), but BOTH update callers start from the existing config and **preserve unknown keys TODAY** — the REST PUT merges `{**existing.config, **update.config}` (`routers/schedules.py:381-387`); the tool path copies `dict(row.config or {})` then mutates named keys (`scheduling_service.py:1024`, call `:1129-1138`). An unknown `creator_instance_id` key is therefore preserved by both paths today; the re-thread (T1) is insurance + regression pin, not a bug fix — pinned anyway per A6 by EXTENDING `tests/test_scheduler_api.py::test_put_mask_sentinel_keeps_stored_secret` (`:340-383`, already asserts the exact config dict passed to `update_source_config`) plus the tool-path update test (no new test file). REST-created schedules (`daemon/routers/schedules.py:53-85`) carry no creator → key absent → skip notify.

### Counters (existing idiom)

NEW sibling module `daemon/services/schedule_fire_notify_metrics.py`, mirroring `report_integrity_metrics.py` exactly: lock-guarded module totals, `record_*` functions whose whole body is try/except (never raise, never block), `attach_sink(callable)`, `get_*` readers, test-only `reset_*`. Two strings: `schedule_fire_notify_sent_total` / `schedule_fire_notify_dropped_total`. **Fence note:** the metrics idiom places constant strings in `daemon/constants.py:1637`, but that file is content-SHA256 sealed (see Fences) — this plan defines the two strings as module-level constants in the NEW sibling file instead (fence-free, disjoint), and records the constants.py alternative as architect-gated in decisions.md item g.

### self_alarm skip rule

Skip notify when `creator_instance_id == recipient instance_id` (the identity rule above). ⚠️ A4 (rationale CORRECTED): the original premise — "for `reuse_instance` the mapper resolves the pinned caller (the creator)" — is true ONLY for self_alarm-bound schedules. Only `self_alarm` binds the mapping to the caller (`_bind_schedule_to_caller`, `self_alarm.py:891-989` → `instance_mappings.agent_instance_id`, `models.py:115`; read at fire by `_resolve_deferrable_target`, `scheduler.py:2442-2452`); plain `task_schedule` reuse schedules pin to a **first-fire STRANGER** (`mapper.py:470-480`). The identity rule is correct NOT because "reuse always wakes the creator" but because it discriminates exactly on **where the wake lands**: bound → skip (the wake IS the notify; Req 7); stranger-pinned → notify (the creator otherwise never hears — the commission's motivating gap, on BOTH the direct and deferred paths). The commission's named edge case — reuse targeting an instance OTHER than the creator — is covered correctly: identity mismatch → notify fires. String-match skip (by `instance_mode`) is REJECTED: it would silence exactly the stranger-pinned case and misfire on mode labels.

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
8. **Plan-anchor corrections (A8 — implementers grep at use time; do NOT transplant plan line numbers):** (1) the mission-record mint is `await asyncio.to_thread(resolve_or_mint_fail_open, …)` (`scheduler.py:2713`; `mission_record_service.py:1334-1408`) — an AWAITED fail-open precedent, NOT `create_task`; the `create_task` + strong-ref-set mechanism precedent is `warmup_pool.py:939`. (2) the child_reports dedup check is `child_reports.py:1349-1365` / `:4588-4601` (`:4877` is only the source-string mint). (3) the full-replace docstring lives at `daemon/repositories/source/repository.py:178-201`, not in `scheduling_service.py`. (4) the full-replace hazard is OVERSTATED — both update callers preserve unknown keys today (see Creator capture).

---

## Phases / tasks (small-medium lane; tasks kept in overview — no phase files needed)

Single-ownership invariant: every file edit is claimed by exactly ONE task. No forward symbol references — every symbol a task's exit criterion names is created in or before that task.

### Phase 1 — Implement
- **T1 (creator capture)** — `daemon/services/scheduling_service.py`: add `creator_instance_id` to `_build_config_json` (`:422-454`) from `caller_instance_id`; re-thread the key through the update path (⚠️ A6: INSURANCE, not a bug fix — both update callers preserve unknown keys today; pinned anyway per A6). `daemon/tools/scheduling.py`: ensure `task_schedule_update` preserves the key. Exit: creator id survives create+update round-trip (pinned by the A6 test extension in T5). (Owns both daemon files.)
- **T2 (notify helper)** — NEW `daemon/services/schedule_fire_notify.py`: ONE never-raise ENTRYPOINT `notify_schedule_fire(...)` (⚠️ F2) — whole-body-guarded adapter-facing call that performs the identity gate, `asyncio.create_task` of the inner worker, strong-ref registration + done-callback (`warmup_pool.py:939` mechanism precedent); NOTHING in the module may raise out of the entrypoint. The inner worker builds the exact message, stamps `system:schedule_fire_notify`, runs the DEC-1 creator-status pre-check (`_instance_repository.get` via `asyncio.to_thread` INSIDE the task body — terminal `COMPLETED|TERMINATED|ERROR|FAILED` or absent row → skip + one structured log line + dropped counter; PAUSED → normal queue), calls `manager.enqueue_message_job`, and is wrapped in a whole-body try/except + counters (⚠️ A3 LOAD-BEARING: never re-raise into the fire path). Exit: helper unit-testable with a mock manager; no spawn/mapper call; the failure contract (log line + dropped counter + never re-raise) holds at BOTH gate time and coroutine time (A7-v as extended by F2). (Owns the new file.)
- **T3 (metrics sibling)** — NEW `daemon/services/schedule_fire_notify_metrics.py`: lock-guarded sent/dropped totals, `record_*` no-op-safe, `attach_sink`, `get_*`, `reset_*`, module-level string constants. Exit: counters increment and never raise. (Owns the new file.)
- **T4 (seam hook)** — `daemon/sources/adapters/scheduler.py`: hook at BOTH sites (A1, ⚠️ F1) — (1) after the successful direct enqueue at `:2731`; (2) at `_defer_delivery`'s DURABLE finalization — the `FIRE_OUTCOME_DEFERRED_DURABLE` assignment at `:2236` (block `:2196-2237`), AFTER the durable-evidence check (`:2140-2191`) that deletes a taskless row (`:2158-2188`) and re-arms — where the recipient (the resolved deferrable target) and `result.job_id`/`result.message_id` are in hand AND durability is proven. Each site makes ONE call into the notify module's never-raise entrypoint (⚠️ F2) — NO inline identity gate and NO `asyncio.create_task` outside that guard (the direct site sits inside `_route_via_job_queue`'s `except…raise` `:2750-2754`; the deferred site inside the failure ladder with FAILED legs `:2100`/`:2126`/`:2182` — a synchronous raise at either would flip FAILED → 5s re-arm). Exit: new_instance fire → one notice; deferred DURABLE fire with stranger recipient → ONE notice; deferred/self-alarm fire with recipient == creator → ZERO notices; taskless/stranded enqueue (real ids, no pending Task) → NO notify (F1: no void-handle notice, no duplicate on the real delivery); REST (no creator) → zero notices. (Owns the adapter file.)
- Exit criterion uses symbols created in T1–T3 only (no forward refs).

### Phase 2 — Tests
- **T5 (unit)** — `tests/test_scheduler_adapter.py` (seam gating: cover/skip matrix incl. BOTH hook points per A1 and the F1 taskless leg), `tests/test_scheduler_api.py` (creator capture round-trip; ⚠️ A6: EXTEND the existing `test_put_mask_sentinel_keeps_stored_secret`, `:340-383`, to assert the creator key survives the PUT merge, plus the tool-path update test — no new test file for this), NEW `tests/unit/services/test_schedule_fire_notify.py` (message shape, source, no-spawn, counters, best-effort no-raise). **A7 required matrix (unit legs — helper/adapter level only):** (i) terminal creator → NO revive (status unchanged) + `dropped_total` +1 + no MessageQueue row; (ii) absent creator row → dropped counter +1; (iii) PAUSED creator → helper-level behavior ONLY (⚠️ F3: NO skip; `enqueue_message_job` invoked EXACTLY ONCE; no drop-counter increment — the `mock_manager` fixture stubs `enqueue_message` as AsyncMock (`:37`) and does NOT stub `enqueue_message_job` at all, so a mock cannot honestly produce the PENDING Task row or prove the status non-flip; those assertions live in T6's real-lane harness); (v) a raised notify exception NEVER escapes to the fire path (fire outcome stays DIRECT — the A3 re-arm-loop tripwire, LOAD-BEARING) — ⚠️ F2: assert GATE-TIME exceptions (raised by the identity gate or `create_task` itself, through the never-raise entrypoint) AND COROUTINE-TIME exceptions are BOTH absorbed; **F1 leg:** taskless/stranded deferred enqueue (real ids, row deleted `:2158-2188`, fire re-arms) → NO notify and NO void-handle notice (the hook fires only at the `FIRE_OUTCOME_DEFERRED_DURABLE` finalization `:2236`); deferred-gate unit legs: deferred DURABLE fire with STRANGER recipient → ONE notice; deferred fire with recipient == creator (self_alarm shape) → ZERO notices. Reuse `mock_manager` fixture (`test_scheduler_api.py:22-129`). (Owns these files.)
- **T6 (integration)** — `tests/integration/test_scheduled_tasks_e2e.py`: one stub seam (`spawn_instance_with_mcp`, `:20-26`), assert **exactly ONE** notice MessageQueue row per fire (`source=system:schedule_fire_notify`, `type=SYSTEM`, exact fields — A7-iv), no extra schedule row, zero dependency-watcher rows (DEC-6 regression), and the A7 deferred legs end-to-end: stranger-recipient deferred fire → ONE creator notice; recipient == creator deferred fire → ZERO notices. **⚠️ F3 real-lane leg (moved from T5):** PAUSED creator → PENDING Task row EXISTS and the instance status is NOT flipped (asserted against the `wire_manager_only` real lane — the only honest place for row-level and status assertions). (Owns this file.)
- **T7 (pack + registration)** — NEW `tests/packs/scheduler_fire_notify_acceptance.sh` (copy the 15-line shape of `scheduled_tasks_acceptance.sh`, `set -euo pipefail`, `ENSEMBLE_SELF_ENV=dev`, outer `timeout 600`); register in `.agents/tester/PACKS.md` (cross-check main checkout copy). (Owns these files.)

### Phase 3 — Docs / closeout
- **T8 (agents note)** — `agents/ari/tools_note.md`: MINIMAL two-line mention — on receiving a `system:schedule_fire_notify` notice, Ari may watch the work handle or ignore it. Justification: the notice introduces a new message type with an actionable handle; two lines in the unsealed tools_note is proportionate. Do NOT touch sealed `agents/ari/workflow.md`. ⚠️ Green (a): regenerate `agents/.prompt-artifacts.sha256` in the SAME commit as the tools_note.md edit — avoids a stage-time drift WARN. (Owns both files.)
- **T9 (closeout)** — staged release only; disclose candidate slot `v0.31.23` (W9 sequencing), no promote, `rollback_safe=true`, zero migrations. No file edits.

---

## Binding invariants (REJECT-grade)

1. **Never wedge the scheduler** — notify is fire-and-forget off the awaited fire path through ONE never-raise entrypoint (⚠️ F2: gate + `create_task` + strong-ref registration, whole-body guarded; nothing runs in the adapter outside it); failure logged/counted, never fatal, at gate time or coroutine time.
2. **Message-only, never spawn** — notice goes to an existing instance via `enqueue_message_job`; no mapper call, no create-instance branch.
3. **Non-user-origin everywhere** — `system:` source; fail-closed in `classify_user_origin` incl. upgrade-gate window; no `PendingWake`, no recorded wake-source touch.
4. **No body echo** — notice carries ONLY the five field groups; no schedule body/brief/`message` key.
5. **One hop** — no watcher arming, no re-arming, no schedule creation from the notice.
6. **No double-notify** — the IDENTITY gate (creator present AND `creator_instance_id == recipient` → skip) applies at BOTH hook sites (direct `:2731` AND `_defer_delivery`'s durable finalization `:2236` — ⚠️ F1: past the `:2140-2191` durable-evidence check, so no void-handle notice and no duplicate on the real delivery); a single fire takes exactly one path (direct XOR deferred), so at most ONE notice per fire. (⚠️ A1/DEC-9 amended from the original blanket deferred-site skip — the gate, not the site list, is what preserves Req 7.)
7. **Single ownership / no forward refs** — per the plan-contract invariants above.
8. **Fence discipline** — no edits to sealed `constants.py` / `agents/ari/workflow.md` without an explicit architect-gated re-pin; do not "fix" a fence by editing the fence test in-lane.

---

## Risks

- 🟡 **Implementation defect vector (HIGHEST — A3):** a notify exception escaping into `_route_via_job_queue` converts an informational notice into a **one-shot re-arm loop** (`except … raise` `:2750-2754` → `_fire_outcome=FAILED` `:2602-2604` → non-delivery episode `:2892-2896` → one-shot suppress consumption → 5s re-arm `:823-834`). The whole-body guard is LOAD-BEARING, not cosmetic; tested by A7-v (T5).
- **Revive churn on dead creators** (daily schedule reviving a dead creator daily) — RESOLVED by the DEC-1 ruling: drop+log+count with the creator-status pre-check inside the off-path task (A5); PAUSED still queues. Disclosed cost: a one-shot whose creator finished before the fire produces no notice — counted as dropped.
- **Recurrence noise** (daily-skill-system-watch at 05:00) — RESOLVED by the DEC-2 ruling: per-fire tiny SYSTEM notice (no attestation reset, no user-origin window); digest rejected; opt-out knob deferred, NOT built in v1.
- **Full-replace update hazard** loses creator key — RE-FRAMED per A6: OVERSTATED; both update callers preserve unknown keys today (`routers/schedules.py:381-387`, `scheduling_service.py:1024`); T1 re-thread is insurance + regression pin (extend `test_put_mask_sentinel_keeps_stored_secret` `:340-383`).
- **Stale SHA seals** on constants.py/workflow.md are pre-existing red; this lane must not add new edits to them.
- **Worktree PACKS.md lag** — cross-check main checkout before claiming registration.
- **Release-slot contention** with tool-metrics lane — W9 decides at stage time.
- 🟢 **Write amplification (architect-accepted):** notify ≈ doubles the fire's enqueue row-writes (MessageQueue + Task + Event + JobItem) — bounded by the execution-slot semaphore (`:583`, `:2837`); negligible at observed cadence.
- 🟢 **In-flight notice loss on daemon restart** — accepted (informational; the stale handle has no watch value).
- **Notice dedup (A9 — recorded, NOT adopted):** recurring notices carry NO idempotency key; at-most-once-per-fire best-effort; worst case a duplicate tiny internal notice (harmless). Named opt-in if ever demanded: `idempotency_key=f"schedule_fire_notify:{execution_id}"`.

---

## Success criteria

1. A `new_instance` schedule firing produces exactly ONE creator notice with the exact five field groups and a usable work handle, arriving without polling.
2. (⚠️ A1 re-scoped) A deferred fire whose recipient is a STRANGER (plain tool-created reuse schedule — busy caller `:2855` or semaphore-timeout `:2358`) produces exactly ONE creator notice; a fire whose recipient IS the creator (self_alarm-bound, direct or deferred) produces ZERO notices; REST-created (no creator) never notifies.
3. Notice source is `system:schedule_fire_notify`, classified non-user-origin in all gates; no spawn, no watcher, no re-arm.
4. Notification failure never affects the fire: a raised notify exception NEVER escapes to `_route_via_job_queue` and the fire outcome stays DIRECT — no FAILED flip, no re-arm loop (A3/A7-v); sent/dropped counters observable.
5. Creator id survives create + update round-trip (A6 insurance pin via the extended `test_put_mask_sentinel_keeps_stored_secret` + tool-path update test); REST-created schedules never notify.
6. All new tests pass (unit + integration-marked via pack); PACKS.md registered; staged release only, no promote.
7. (A7 / DEC-1) Terminal creator → NO revive (status unchanged) + `schedule_fire_notify_dropped_total` +1 + no MessageQueue row; absent creator row → dropped counter +1 (both unit-proven in T5); PAUSED creator → PENDING Task row exists, delivered on resume, no status flip (⚠️ F3: proven in T6's `wire_manager_only` real lane — a mock cannot honestly produce the row or prove the non-flip; T5 asserts helper-level behavior only).
8. (A7-iv / DEC-6) Exactly ONE notice MessageQueue row per fire (`source=system:schedule_fire_notify`, `type=SYSTEM`, exact fields); a taskless/stranded enqueue produces NO notice (⚠️ F1 — the hook fires only at the durable finalization `:2236`); zero new `source_configs` rows and zero dependency-watcher rows after a notice.
