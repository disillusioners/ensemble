# Architecture Recommendation — schedule-fire-notify (Architect Ruling Pass)

**Date:** 2026-11-21T03:55Z
**Ruling instance:** architect (controller). Verification dispatched to two skill-equipped workers; all anchors below re-verified by them in THIS tree at ruling time:
- `architect-worker-notify-lane` (171958b0, `data-flow-design`) — messaging-lane semantics, classification gates, forgery surface, seam facts, binding question.
- `architect-worker-notify-resilience` (9ac52656, `resilience-design`) — loop safety, wedge contract, noise analysis, GC/update hazard, dedup risk.

**Tree:** `/home/nea/ensemble-src-wt-schedule-fire-notify`, branch `feature/schedule-fire-notify` @ plan commit `bfec4838` (base `0b5624dc9` == origin/latest). Ruling over `.agents/shared/planning/schedule-fire-notify/decisions.md` DEC-1..DEC-9.

---

## Status

**COMPLETE.** All nine DEC items ruled. Verdict profile: **6 ADOPT** (DEC-1, 2, 3, 4, 6, 7), **2 ADOPT-WITH-AMENDMENT** (DEC-5, 8), **1 AMEND** (DEC-9 — the planner's blanket deferred-site skip is unsafe; the identity gate applies at BOTH dispatch sites). Binding plan amendments A1–A9 below.

---

## Verification summary (what the workers established)

**Confirmed (load-bearing):**
1. The enqueue lane auto-revives terminal targets by construction — same-transaction status flip (`instance_messaging.py:2714-2732`), watcher re-arm (`:2594`), no revive budget (`manager.py:12299-12302`) — and **no suppress flag exists** (`enqueue_message_job` signature `:3131-3147`). Drop semantics therefore REQUIRE a creator-status pre-check.
2. PAUSED targets queue as PENDING Task rows, claimed on resume (`:2690-2699`, `:2980-2692 docstring`) — the enqueue lane does NOT reject PAUSED (that is the agent-tool send lane's semantics; this design does not use it). Missing rows fail closed before any write (`:2488-2493`, backstop `:2700-2710`).
3. All eight classification gates clear for `system:schedule_fire_notify` (anchors in DEC-3 below), and **the forgery surface is closed**: no user- or agent-reachable surface can mint a `system:` source.
4. `_route_via_job_queue` and `_defer_delivery` are reachable ONLY from the adapter wake loop (grep-exhaustive: defs `:2622`/`:1951`, route call `:2562`, defer calls `:2358` semaphore-timeout + `:2855` busy-caller; manual twin `_execute_trigger :2901` is operator-initiated, not message-reachable). No automatic re-entry path exists.
5. GC/config survival: `SourceConfigArchive` snapshots `config` verbatim as sole reconstruction input (`source/models.py:221-230`, write `source/repository.py:829-841`); instance delete is SOFT by default (`routers/instances.py:663-676`) → dangling creator id hits the fail-closed `ValueError` → guard → dropped counter.

**Corrected (plan anchors that drifted — implementers must grep at use time):**
- The mission-record mint is `await asyncio.to_thread(resolve_or_mint_fail_open, …)` (`scheduler.py:2713`; `mission_record_service.py:1334-1408`) — an AWAITED fail-open precedent, NOT `create_task`. The `create_task` + strong-ref-set mechanism precedent is `warmup_pool.py:939` (and adapter `:589`/`:699`).
- child_reports dedup check is `child_reports.py:1349-1365` / `:4588-4601` (`:4877` is the source-string mint).
- The full-replace docstring lives at `daemon/repositories/source/repository.py:178-201`, not in `scheduling_service.py`.
- **The full-replace hazard is OVERSTATED in the plan**: the repo layer full-replaces, but BOTH update callers start from the existing config — REST PUT merges `{**existing.config, **update.config}` (`routers/schedules.py:381-387`); the tool path copies `dict(row.config or {})` then mutates named keys (`scheduling_service.py:1024`, call `:1129-1138`). An unknown `creator_instance_id` key is **preserved by both paths today**. The re-thread is insurance, not a bug fix (pin it anyway — A6).

**Decisive new finding (drives DEC-8/DEC-9):** plain tool-created `reuse_instance` schedules are **NOT bound to the caller at creation**. `create_schedule` uses `caller_instance_id` for audit logging only (`scheduling_service.py:710-715`, `:836-844`; zero `create_instance_mapping` calls in the service). Only `self_alarm` binds (`_bind_schedule_to_caller`, `self_alarm.py:891-989` → `instance_mappings.agent_instance_id`, `models.py:115`). On first fire with a mapping miss and `force_new=False`, the mapper mints a **fresh stranger instance** of the target agent and pins the mapping to it (`mapper.py:470-480`). So for plain tool-created reuse schedules, creator ≠ pinned recipient in BOTH the direct and deferred paths — the wake never reaches the creator.

---

## DEC rulings

### DEC-1 — Creator-gone mechanism → **ADOPT (c) drop+log+count** (terminal/absent); PAUSED queues. [MANDATORY — disclosure paragraph below]

The planner's recommendation is confirmed on every axis and is the only option compatible with "simple always better":
- **(a) revive-on-send REFUTED as policy**: the churn argument is real and structural — the revive is a row-write with no budget, but it re-arms dependency watchers (`:2594`) and flips a dead instance to RUNNING on every fire; a daily schedule would resurrect a dead creator daily to deliver a notice it cannot act on.
- **(b) durable-enqueue-without-revive REFUTED as feasible**: the terminal branch auto-revives by construction (`:2714-2726`); suppressing it means a new code path against the lane's design, and a dead instance never claims the Task — silent pile-up. Exactly as the planner argued, now code-verified.
- **(c) adopted**: pre-check creator status; terminal (`COMPLETED|TERMINATED|ERROR|FAILED`) or absent → skip delivery, one structured log line, `schedule_fire_notify_dropped_total`++. This mirrors the system's own doctrine: `BoundCallerRefusal` treats a confirmed-gone caller as a refusal, not a revive target (`mapper.py:89-129`, `:420-460`).
- **PAUSED queues (planner rec CONFIRMED)**: the enqueue lane — the lane this design uses — queues PENDING and delivers on resume; the PAUSED-reject semantics belong to the agent-tool send lane, which is NOT in this design's path. PAUSED is a live-but-resting creator, not a gone one.
- **Mechanics (amended detail)**: the status read is `manager._instance_repository.get(creator_instance_id)` (`repositories/instance/repository.py:650`) wrapped in `asyncio.to_thread` (pattern `manager.py:3172-3188`), executed **inside the off-path `create_task` body** — the fire path pays neither the read nor the enqueue latency.

### DEC-2 — Recurrence-noise strategy → **ADOPT (a) per-fire tiny SYSTEM notice; REJECT (b) digest; DEFER (c) opt-out knob (not built in v1)**. [MANDATORY — disclosure paragraph below]

- Per-fire confirmed non-noise: `priority=0 AND msg_type=SYSTEM` fails BOTH conjuncts of the fresh-episode attestation reset (`instance_messaging.py:2765-2768`); `classify_user_origin` → `(False, "reserved-internal")` arms no user-origin window (`upgrade_journal.py:5406-5410`). One cheap PROCESS_MESSAGE turn per fire.
- Digest rejected twice over: it needs accumulation state + a timer/polling flush — squarely against the prior architect ruling "NO new table / migration / daemon service / polling loop" (`scheduler-deferred-alarm-delivery/architecture-recommendation.md` §16) — and it delivers **stale handles**, defeating the notice's entire purpose ("decide to watch NOW while the run is live").
- Opt-out knob deferred: ~2 tiny notices/day across known live schedules is not noise by any system measure; a knob against a zero-observed problem violates the Emperor doctrine. If real noise ever appears, the knob is a single config-JSON key (`fire_notify_enabled`) with the same update-preservation property as DEC-4 — cheap to add later, expensive to remove once shipped.

### DEC-3 — Message shape / source / no-spawn / non-user-origin → **ADOPT verbatim**

Five field groups (label, fired_at, target_agent + instance_mode, recurrence, work handle = job_id/message_id/target_instance_id), no body echo, `system:schedule_fire_notify`, priority 0, metadata `{"schedule_fire_notify": true}`, direct `instance_id` to `enqueue_message_job` (no mapper, no spawn). Every gate CONFIRMED:
1. `is_reserved_source` True via `"system:"` prefix (`constants.py:1278`, `:1311-1335`).
2. `MessageQueue.type` → `MessageType.SYSTEM` (`constants.py:1488`, applied `instance_messaging.py:2386`).
3. Internal stamp (`constants.py:1401`, applied `:441-466`).
4. `classify_user_origin` reserved-checked-BEFORE-registry → `(False, "reserved-internal")` (`upgrade_journal.py:5406-5410`).
5. User-origin window cleared, never armed (`manager.py:6357-6358`).
6. No inherited window for `system:*` (`manager.py:6382-6386`).
7. Attestation reset fails the HUMAN conjunct (`instance_messaging.py:2733-2768`).
8. Non-chat worker pool (`constants.py:144`, `task/repository.py:203-233`).

**Forgery surface (ruled CLOSED):** enumerated every source-entry surface — HTTP `POST /api/jobs` body.source 422s reserved prefixes (`jobs_crud.py:528-545`); the messages router hardcodes `source="api"` (`messages.py:722`, no client param); the agent `send_message` tool has NO source parameter and always mints `internal_agent:{caller}` (`instance.py:4741`); REST schedules carry no source field. The literal is mintable only inside daemon code, like every `system:*` precedent. The filed R3/DF-4 defect (client-supplied `body.source` defaulting `"api"`, `jobs_crud.py:509-516`) is NOT enlarged by this design — a `system:` literal cannot ride that hole. Discipline requirement stands: the literal appears in exactly ONE mint site (the notify helper).

### DEC-4 — Creator persistence → **ADOPT (a) config-JSON key `creator_instance_id`**

Zero migration; survives source-GC archive/restore (config is the sole reconstruction input, `models.py:221-230`); matches the `_defer_*` config-carriage precedent; consumer is the adapter alone. GC-safety ruled: instance deletion is soft by default, so a dangling id fail-closes into the DEC-1 dropped counter — no dangling-reference defect exists. **Hazard restated (plan correction):** the full-replace drop risk is NOT current behavior (both update callers preserve unknown keys — see Verification summary); treat the re-thread as insurance + regression pin, not an active defect. SQL-column alternative rejected: a migration + archive-table mirror for zero functional gain.

### DEC-5 — Async seam → **ADOPT (a) create_task + exception guard + strong-ref set**, with TWO amendments

- **A2 (precedent correction):** cite the AWAITED fail-open mission mint (`scheduler.py:2712-2721` → `mission_record_service.py:1334-1408`) as the fail-open *posture* precedent, and `warmup_pool.py:939` (module set + `add_done_callback(set.discard)`) as the `create_task` strong-ref *mechanism* precedent. The plan's "in-file create_task precedent" claim is wrong.
- **A3 (failure contract is LOAD-BEARING):** the guard must (i) log one structured line, (ii) increment `schedule_fire_notify_dropped_total`, (iii) **NEVER re-raise into `_route_via_job_queue`** — its `except … raise` (`:2750-2754`) would flip `_fire_outcome=FAILED` (`:2602-2604`), open the non-delivery episode (`:2892-2896`), and for a one-shot suppress consumption → 5s re-arm (`:823-834`): a leaked notify exception would wedge the scheduler into a re-arm loop. This clause gets its own test (A7). Accepted cost: notify roughly doubles the fire's enqueue row-writes (MessageQueue + Task + Event + JobItem) — bounded by the execution-slot semaphore (`:583`, `:2837`), negligible at observed cadence. Daemon-restart loss of in-flight notices accepted (informational; the handle goes stale anyway).

### DEC-6 — Loop safety → **ADOPT (a) by construction + ONE regression test; NO runtime guard**

Grep-exhaustive: the two fire functions are wake-loop-only (see Verification summary §4); a delivered reserved-source message yields exactly one PROCESS_MESSAGE turn; the loop breaker (`graph.py:214`), dependency-bus CAS one-shot (`dependency_bus/repository.py:802-871`), and child_reports exact-source dedup (`:1349-1365`, `:4588-4601`) bound the rest. A guard would catch nothing construction doesn't. **Honest nuance recorded:** a recipient agent holding scheduling tools (ari) may consciously call `task_schedule` in its turn — a fresh LLM decision bounded by the loop breaker, not an automatic fire→fire loop. The regression test (zero new `source_configs` rows, zero dependency-watcher rows after a notice) is the tripwire against the only realistic breach: a future edit wiring the notify helper into watcher/schedule surfaces.

### DEC-7 — Counters → **ADOPT sibling module; do NOT touch `constants.py`**

`constants.py` is content-SHA256 sealed by two tests with pins ALREADY stale at base — an edit forces a deliberate two-row re-pin ceremony for two strings. The sibling module (`daemon/services/schedule_fire_notify_metrics.py`, mirroring `report_integrity_metrics.py:38-105`) is fence-free, file-disjoint from the tool-metrics lane, and keeps the sealed-file status quo. The architect-gated constants.py alternative is explicitly NOT exercised.

### DEC-8 — self_alarm skip rule → **ADOPT (b) identity rule**, with the rationale CORRECTED (A4)

Rule: skip notify iff `creator_instance_id == resolved target instance_id`. **The plan's premise — "for reuse_instance the mapper resolves the pinned caller (the creator)" — is wrong for plain tool-created reuse schedules** (see the decisive finding): only `self_alarm` binds the mapping to the caller; plain `task_schedule` reuse schedules pin to a first-fire stranger. The identity rule is correct NOT because "reuse always wakes the creator" but because it discriminates exactly on where the wake lands: bound → skip (the wake IS the notify; Req 7), stranger-pinned → notify (the creator otherwise never hears — the commission's motivating gap). The edge case the commission named — reuse_instance targeting an instance OTHER than the creator — is covered correctly: identity mismatch → notify fires. String-match skip (option a) is REJECTED: it would silence exactly the stranger-pinned case and misfire on mode labels.

### DEC-9 — Deferred site → **AMEND: identity gate at BOTH sites; blanket SKIP `:2109` is UNSAFE**

The planner's blanket skip rests on "the deferred recipient IS the creator" — true ONLY for self_alarm-bound schedules. For a plain tool-created reuse schedule whose pinned stranger is BUSY, the fire defers (`:2848-2863`), delivery lands in the stranger, and the creator gets nothing — the polling gap reborn in the deferred subcase (and prior art records deferred as the NORMAL shape for busy reuse callers). **Ruling:**
- Hook the notify at BOTH enqueue sites with the SAME gate — notify iff creator present AND `creator != recipient`: (1) after the direct enqueue (`scheduler.py:2731`); (2) inside `_defer_delivery` after its successful enqueue (`:2109-2116`), where the recipient (the resolved deferrable target) and `result.job_id`/`result.message_id` are in hand. Hooking INSIDE `_defer_delivery` covers both of its callers (busy-caller `:2855` AND semaphore-timeout `:2358`) uniformly.
- No double-notify is possible: a single fire takes exactly one path (direct XOR deferred), and on the deferred path the gate skips precisely when recipient == creator (all self_alarm-bound schedules) — Req 7 preserved verbatim.
- `new_instance` is unaffected: `_resolve_deferrable_target` returns None by construction (`:2437-2438`) → no deferral → the deferred hook is dead code for that mode.
- One honest mechanism, two call sites — this also resolves the explorer-A "cover both sites" tension correctly: coverage belongs to the identity gate, not to a blanket skip or a blanket cover.

---

## Mandatory disclosures (DEC-1, DEC-2 — for the Emperor's report, verbatim-ish)

**DEC-1 — creator gone at fire time.** When a schedule fires and its creating instance is dead (completed/terminated/errored/failed) or its row no longer exists, the notice is **dropped, logged in one structured line, and counted** (`schedule_fire_notify_dropped_total`) — the dead creator is NOT revived. Rationale: the delivery lane revives terminal instances by construction, so a daily schedule would resurrect a dead creator every single day and re-arm its dependency watchers each time — churn with no payoff, since a dead instance cannot make the "watch now" decision the notice exists to enable. This mirrors the system's existing doctrine (a confirmed-gone bound caller is refused, never revived). The honest cost, disclosed: a one-shot schedule whose creator finished before the fire produces no notice — it is counted as dropped. A PAUSED creator is NOT dead: its notice queues and is delivered on resume.

**DEC-2 — recurring schedules and noise.** Every fire of a schedule with a recorded creator produces exactly ONE tiny system notice (label, fired-at, target agent + mode, recurrence, work handle — nothing else). No digest: digest machinery needs accumulation state and a polling/timer flush — machinery a prior architect ruling already forbade for this subsystem — and it would deliver stale work handles that defeat the notice's purpose ("decide to watch NOW"). No per-schedule opt-out knob is built now: at observed cadence (~two daily schedules live) this is ~2 small internal notices per day, they do not reset any attestation or user-origin gate, and each costs one cheap processing turn. If real noise ever materializes, the opt-out is a single config key added then — cheaper to add than to retract.

---

## Binding plan amendments

- **A1 (DEC-9, T4 rewrite):** notify hook at BOTH sites — after `scheduler.py:2731` AND inside `_defer_delivery` after `:2116` — each gated `creator present AND creator != recipient`. Update the plan-overview notify-site table (the "SKIP :2109" row is wrong); keep the DEC-9 record so no future reader "fixes" it back to blanket skip.
- **A2 (DEC-5):** correct the precedent citations (awaited fail-open mission mint = posture; `warmup_pool.py:939` = create_task + strong-ref mechanism).
- **A3 (DEC-5):** failure contract (log line + dropped counter + NEVER re-raise into `_route_via_job_queue`) is an explicit exit criterion; the re-arm-loop wedge vector goes into the plan's risk table as the top implementation defect vector.
- **A4 (DEC-8):** correct plan-overview's reuse-lane rationale ("the mapper resolves the pinned caller (the creator)" → true only for self_alarm-bound schedules; plain tool-created reuse schedules pin to a first-fire stranger).
- **A5 (DEC-1):** status pre-check mechanics — `_instance_repository.get` via `asyncio.to_thread` inside the off-path task; terminal-set + absent-row → dropped counter; PAUSED → normal queue. Cover-matrix tests required (A7).
- **A6 (DEC-4):** re-frame the update hazard as insurance (both update paths preserve unknown keys today); pin the create+update round-trip by EXTENDING `tests/test_scheduler_api.py::test_put_mask_sentinel_keeps_stored_secret` (`:340-383`, already asserts the exact config dict passed to `update_source_config`) plus the tool-path update test — no new test file needed for this.
- **A7 (test adequacy for the mandatory decisions):** add to T5/T6 — (i) terminal creator → no revive (assert status unchanged) + dropped counter +1 + no MessageQueue row; (ii) absent creator row → dropped counter +1; (iii) PAUSED creator → PENDING Task row exists, no status flip; (iv) exactly ONE notice MessageQueue row (`source=system:schedule_fire_notify`, `type=SYSTEM`, exact fields) per fire; (v) a raised notify exception never escapes to the fire path (fire outcome stays DIRECT); (vi) deferred fire with stranger recipient → ONE notice to creator; deferred fire with recipient == creator (self_alarm shape) → ZERO notices.
- **A8 (anchors):** the four drifted anchors in Verification summary §Corrected — implementers grep at use time; do not transplant plan line numbers.
- **A9 (dedup, recorded not adopted):** recurring notices carry NO idempotency key — at-most-once-per-fire best-effort is accepted (a stable key would need per-fire identity `execution_id` + a DB round-trip on an informational notice; worst case is a duplicate tiny internal notice, harmless). Named opt-in if ever demanded: `idempotency_key=f"schedule_fire_notify:{execution_id}"`.

---

## Risks

- 🟡 **Implementation defect vector (highest):** a notify exception escaping into `_route_via_job_queue` converts an informational notice into a one-shot re-arm loop (A3/A7-v). The whole-body guard is load-bearing, not cosmetic.
- 🟡 **Plan-anchor drift:** four stale/misattributed anchors in the plan set (A8) — a coder transplanting them writes against the wrong sites.
- 🟢 **Write amplification:** notify ≈ doubles per-fire enqueue row-writes; bounded by the execution-slot semaphore; negligible at observed cadence.
- 🟢 **In-flight notice loss on daemon restart** — accepted (informational; stale handle has no watch value).

## Gaps / unverified

- No live smoke was run (read-only ruling pass); runtime behavior of the not-yet-written helper is inferred from cited call sites. The T5/T6 test matrix (A7) is the compensating control.
- Semaphore timeout / `_max_concurrent` values not numerically read — the storm bound is structural (semaphore gates fires), not sized.
- Prior-art §16 "no polling loop" ruling was read from the sibling lane's architecture doc; no independent ruling record was cross-checked.
