# Decisions — schedule-fire-notify (Architect Question List)

These are **architect ruling items**. Each is framed with code-anchored options and churn/noise evidence, plus MY RECOMMENDATION. The architect rules; the planner does not settle these as done. All anchors verified against worktree `0b5624dc9`.

**RULING STATUS (2026-11-21, architect commit `30ca10c8e`, `architecture-recommendation.md`): COMPLETE — all nine items SETTLED.** Profile: 6 ADOPT (DEC-1, 2, 3, 4, 6, 7), 2 ADOPT-WITH-AMENDMENT (DEC-5, 8), 1 AMEND (DEC-9). Each DEC below carries a binding VERDICT line; the original option framing is retained as history and is NOT re-opened. Binding plan amendments A1–A9 are folded into plan-overview.md.

---

## DEC-1 — Creator-gone mechanism (revive-on-send vs durable-enqueue vs drop+log)

**Question:** The creator instance may be gone (COMPLETED/TERMINATED/ERROR/FAILED, or deleted) when a later fire wants to notify it. What happens?

**Options (code-anchored):**
- **(a) Revive-on-send.** Reuse `enqueue_message_job`'s built-in terminal→revive: status flipped to RUNNING in the same transaction (`instance_messaging.py:2714-2732`), a row-write with no revive budget (`manager.py:12299-12302`), checkpoint reused (`:2690-2694`). Pro: the notice always lands. Con: **churn** — a DAILY schedule revives a dead creator every day to deliver a notice it will ignore; each revive re-arms dependency watchers (`:2594`).
- **(b) Durable-enqueue without revive.** Queue a PENDING Task to the terminal/dead instance without status flip. Con: the enqueue lane's terminal branch auto-revives by construction (`:2714-2726`); suppressing it means a new code path, and a dead instance never claims the Task → silent pile-up. Complex and leaky.
- **(c) Drop + log + count.** Before enqueue, check the creator's status; if terminal/absent, skip delivery and increment `schedule_fire_notify_dropped_total`. Pro: no churn, one honest mechanism, no knobs. Con: a once-schedule whose creator died loses its notice — but a dead creator cannot act on it anyway.

**Churn/noise evidence:** Emperor's directive ("simple always better"); the motivating case (ari) is a live creator. `self_alarm` already refuses stranger-spawn for a confirmed-gone bound caller (`BoundCallerRefusal`, `mapper.py:89-129`, `:420-460`) — the system already treats "confirmed gone creator" as a refusal, not a revive target. PAUSED is a distinct, non-gone state (queues PENDING, claimed on resume, `:2695-2699`) and must NOT be dropped.

**RECOMMENDATION: (c) drop+log+count for terminal/absent creators; PAUSED still queues normally.** One mechanism, no per-schedule knob. If the architect wants once-schedules to revive their creator, that is the one narrow exception — but it reintroduces a knob, so default to (c).

**VERDICT (architect): ADOPT (c) — drop+log+count for terminal/absent; PAUSED queues. [MANDATORY disclosure recorded in architecture-recommendation.md]** Revive-on-send REFUTED as policy (structural churn: dead creator resurrected + watchers re-armed on every fire); durable-enqueue REFUTED as feasible (no suppress flag exists — the `enqueue_message_job` signature `:3131-3147` has none; the terminal branch auto-revives by construction). **Mechanics (A5):** the status read is `manager._instance_repository.get(creator_instance_id)` (`repositories/instance/repository.py:650`) wrapped in `asyncio.to_thread` (pattern `manager.py:3172-3188`), executed INSIDE the off-path `create_task` body — the fire path pays neither the read nor the enqueue latency. Terminal (`COMPLETED|TERMINATED|ERROR|FAILED`) or absent row → skip + one structured log line + `schedule_fire_notify_dropped_total`++. PAUSED → normal queue (the enqueue lane queues PENDING and delivers on resume; the PAUSED-reject semantics belong to the agent-tool send lane, which this design does NOT use). GC-safe: instance deletion is SOFT by default (`routers/instances.py:663-676`) → a dangling creator id hits the fail-closed `ValueError` → guard → dropped counter — no dangling-reference defect. Cover-matrix tests required (A7 i–iii).

---

## DEC-2 — Recurrence-noise strategy (digest vs per-fire vs opt-out)

**Question:** Daily/weekly schedules fire often (live case: `daily-skill-system-watch` at 05:00). How does the notice stay cheap and non-spammy?

**Options:**
- **(a) Per-fire notice.** One tiny SYSTEM message per fire. Pro: simplest, no state, no loop, handle arrives immediately. Con: one message/day for a daily schedule.
- **(b) Digest.** Accumulate fires and send periodically. Con: needs a timer/polling loop or accumulation state — directly against the prior-art architect ruling "NO new table / migration / daemon service / polling loop" (`scheduler-deferred-alarm-delivery` §16); higher complexity; stale handles by the time it lands.
- **(c) Opt-out flag.** A per-schedule `fire_notify_enabled` (config-JSON key) lets a creator silence a noisy schedule. Pro: user control. Con: a knob (Emperor prefers fewer); only worth it if per-fire proves noisy.

**Noise evidence:** the notice is `MessageType.SYSTEM`, so it does NOT reset the leader's fresh-episode attestation ledger (`msg_type==SYSTEM` fails the HUMAN conjunct, `instance_messaging.py:2733-2764`) and does NOT arm a user-origin window (`classify_user_origin` fail-closed). It is one cheap PROCESS_MESSAGE turn. A single small notice/day is not spam by any system measure.

**RECOMMENDATION: (a) per-fire, minimal fields, SYSTEM type.** Defer (c) as a follow-up knob only if real noise appears; reject (b) (polling-loop gravity).

**VERDICT (architect): ADOPT (a) per-fire tiny SYSTEM notice; REJECT (b) digest; DEFER (c) opt-out knob — NOT built in v1.** Per-fire confirmed non-noise: `priority=0 AND msg_type=SYSTEM` fails BOTH conjuncts of the fresh-episode attestation reset (`instance_messaging.py:2765-2768`); `classify_user_origin` arms no user-origin window (`upgrade_journal.py:5406-5410`). Digest rejected twice over: accumulation state + a timer/polling flush (against the prior §16 ruling) AND stale handles — defeating the notice's purpose ("decide to watch NOW while the run is live"). Opt-out deferred: ~2 tiny notices/day across known live schedules is not noise by any system measure; a knob against a zero-observed problem violates the Emperor doctrine. If real noise appears, the knob is a single config-JSON key (`fire_notify_enabled`) with the same update-preservation property as DEC-4 — cheap to add later, expensive to remove once shipped.

---

## DEC-3 — Notification message shape (exact fields, source, no-spawn, non-user-origin)

**Question:** What exactly is in the notice, and how is it classified?

**Recommendation (framed as the proposed contract):**
- **Fields — EXACT, five groups, nothing else:** `label` (`SourceConfig.name`, `self.config.name`), `fired_at` (`metadata["scheduler.trigger_time"]`, `:2517-2539`), `target_agent` + `instance_mode` (`self._agent`, `self._instance_mode`), `recurrence` (`self._schedule_type` / config echo), work handle (`result.job_id`, `result.message_id`, resolved `instance_id` at `:2661`). **No schedule `message`/body echo** (bodies carry large briefs — Req 1).
- **Source:** `system:schedule_fire_notify` — a NEW reserved literal, minted only in daemon code. NEVER bare `"api"` (DF-4 forgery, `jobs_crud.py:509-516`); NEVER bare `"scheduler"` (that would anchor SOURCE-U and derive HUMAN — wrong semantics, `test_memoir_wake_refresh.py:524`).
- **No-spawn guarantee:** pass the known creator `instance_id` straight to `enqueue_message_job`; that lane addresses an EXISTING row and fails closed on a missing one (`instance_messaging.py:2488-2493`, backstop `:2700-2710`); no mapper, no create-instance branch (`research-messaging.md` §6b).
- **Non-user-origin classification:** `system:` → `is_reserved_source` True (`constants.py:1278`), `classify_user_origin` → `(False, "reserved-internal")` (checked BEFORE registry, `upgrade_journal.py:5406-5410`), SYSTEM msg type (`:1488`), stamped internal (`:1401`), no provenance-window inheritance (`manager.py:6382-6386`). Zero constant edits.

**RECOMMENDATION: adopt this contract verbatim.** It is the intersection of Req 1/5/6 and the research-verified zero-ceremony `system:` path.

**VERDICT (architect): ADOPT verbatim.** All eight classification gates CONFIRMED (is_reserved_source `constants.py:1278`/`:1311-1335`; SYSTEM type `:1488` applied `instance_messaging.py:2386`; internal stamp `:1401` applied `:441-466`; `classify_user_origin` reserved-before-registry `upgrade_journal.py:5406-5410`; user-origin window cleared never armed `manager.py:6357-6358`; no inherited window for `system:*` `:6382-6386`; attestation reset fails the HUMAN conjunct `instance_messaging.py:2733-2768`; non-chat worker pool `constants.py:144` + `task/repository.py:203-233`). **Forgery surface ruled CLOSED:** enumerated every source-entry surface — HTTP `POST /api/jobs` body.source 422s reserved prefixes (`jobs_crud.py:528-545`); the messages router hardcodes `source="api"` (`messages.py:722`, no client param); the agent `send_message` tool has NO source parameter and always mints `internal_agent:{caller}` (`instance.py:4741`); REST schedules carry no source field. The filed R3/DF-4 defect (client-supplied `body.source` defaulting `"api"`, `jobs_crud.py:509-516`) is NOT enlarged by this design — a `system:` literal cannot ride that hole. Discipline requirement stands: **the literal appears in exactly ONE mint site (the notify helper).**

---

## DEC-4 — Creator-capture persistence (column vs config-JSON key; opt-out storage)

**Question:** Where does the creating instance id live so a later fire can find it?

**Options:**
- **(a) Config-JSON key `creator_instance_id`** in `_build_config_json` (`scheduling_service.py:422-454`). Gains: zero migration (JSONB exists, `models.py:70-73`); survives source-GC archive/restore (config snapshot is sole reconstruction input, `:224-230`); matches `_defer_*` state-carriage precedent. Loses: no typing/queryability; **full-replace update hazard** — `update_source_config` replaces the whole dict, so `task_schedule_update` must re-thread the key.
- **(b) Additive column** `source_configs.creator_instance_id TEXT` — migration `20261121_HHMMSS_add_creator_instance_id_to_source_configs.sql` (autostart shape, `20260614_000001:8`), nullable, no backfill (identity never recorded), must mirror into `SourceConfigArchive` for GC round-trip. Gains: typed/queryable. Loses: a migration (against prior-art "no new migration" gravity), archive-mirror work, backfill impossible anyway.

**Fence evidence:** no exact-set pin on `source_configs` columns; no exact-dict pin on real `_build_config_json` output. The exact-dict pin at `test_self_alarm_tools.py:1191` is a hand-built `FakeRow`, not a live builder — neither option trips it. Both are legal.

**Opt-out storage (if DEC-2(c) is chosen):** also a config-JSON key (e.g. `fire_notify_enabled`), same full-replace re-thread caveat.

**RECOMMENDATION: (a) config-JSON key.** Zero migration, GC-safe, prior-art-aligned; the typing loss is acceptable because the only consumer is the adapter at fire time (reads config at `__init__`, `:326`). Mitigate the update hazard with a re-thread regression test. If the architect requires SQL queryability, (b) is the fallback with an additive-only migration.

**VERDICT (architect): ADOPT (a) config-JSON key `creator_instance_id`.** Zero migration; survives source-GC archive/restore (config is the sole reconstruction input, `models.py:221-230`); matches the `_defer_*` config-carriage precedent; consumer is the adapter alone. GC-safety ruled: instance deletion is SOFT by default, so a dangling id fail-closes into the DEC-1 dropped counter — no dangling-reference defect exists. ⚠️ A6 hazard CORRECTION: the full-replace drop risk is **NOT current behavior** — both update callers preserve unknown keys today (REST PUT merges `{**existing.config, **update.config}`, `routers/schedules.py:381-387`; tool path copies `dict(row.config or {})` then mutates named keys, `scheduling_service.py:1024`, call `:1129-1138`; repo-layer docstring at `daemon/repositories/source/repository.py:178-201`). Treat the re-thread as **insurance + regression pin, not an active-defect fix** — pinned anyway per A6 by EXTENDING `tests/test_scheduler_api.py::test_put_mask_sentinel_keeps_stored_secret` (`:340-383`) plus the tool-path update test (no new test file). SQL-column alternative rejected: a migration + archive-table mirror for zero functional gain.

---

## DEC-5 — Async best-effort delivery seam (create_task vs queue)

**Question:** How is the notify dispatched off the scheduler's critical path?

**Options:**
- **(a) `asyncio.create_task(notify_schedule_fire(...))`** with its own exception guard, immediately after the awaited enqueue at `:2731`. Pro: zero latency, zero failure coupling; exact in-file precedent (mission-record mint `create_task`, `scheduler.py:2710-2721`). ⚠️ A2 (anchor CORRECTED — history retained): the mission-record mint is an AWAITED `asyncio.to_thread` fail-open precedent (`scheduler.py:2713` → `mission_record_service.py:1334-1408`), i.e. the fail-open POSTURE precedent; the `create_task` + strong-ref-set MECHANISM precedent is `warmup_pool.py:939` (module set + `add_done_callback(set.discard)`). Con: in-flight notice lost on daemon restart (acceptable — informational). Requires a strong task reference (module-level set + done-callback discard) to avoid GC.
- **(b) Work-queue the notify.** Pro: durable. Con: new job type / loop surface; heavier than a notice warrants; risks being mistaken for scheduled work (loop-safety smell).

**RECOMMENDATION: (a) `create_task` with exception guard + strong-ref set.** Matches Req 2 and both cited precedents (`report_integrity_metrics.py:59-89`).

**VERDICT (architect): ADOPT (a) — with TWO binding amendments. A2 (precedent correction):** cite the AWAITED fail-open mission mint as the fail-open *posture* precedent and `warmup_pool.py:939` as the `create_task` strong-ref *mechanism* precedent; the plan's original "in-file create_task precedent" claim was wrong. **A3 (failure contract LOAD-BEARING):** the guard must (i) log one structured line, (ii) increment `schedule_fire_notify_dropped_total`, (iii) NEVER re-raise into `_route_via_job_queue` — its `except … raise` (`:2750-2754`) would flip `_fire_outcome=FAILED` (`:2602-2604`), open the non-delivery episode (`:2892-2896`), and for a one-shot suppress consumption → 5s re-arm (`:823-834`): a leaked notify exception would wedge the scheduler into a re-arm loop. This clause is an explicit exit criterion with its own test (A7-v). Accepted cost: notify ≈ doubles the fire's enqueue row-writes — bounded by the execution-slot semaphore (`:583`, `:2837`), negligible at observed cadence; daemon-restart loss of in-flight notices accepted.

---

## DEC-6 — Loop-safety enforcement

**Question:** How do we guarantee the notice cannot trigger scheduling or further notices (one hop)?

**Evidence:** message-triggered work is already bounded — graph loop breaker (`graph.py:214`, threshold 3), dependency-bus CAS one-shot (`dependency_bus/repository.py:14-27`, no re-arm of watchers on fire), child_reports dedup by exact source (⚠️ A8 anchor corrected: the dedup check is `child_reports.py:1349-1365` / `:4588-4601`; `:4877` is only the source-string mint). A schedule is created ONLY by an explicit agent tool call (`daemon/tools/scheduling.py`); a reserved-source message produces exactly one PROCESS_MESSAGE turn.

**Options:** (a) enforce by construction (notify never touches watchers/schedules/mapper, uses `system:` source) + a regression test asserting zero new schedule rows and zero dependency-watcher rows after a notice; (b) add a dedicated loop-guard flag/constant. (b) is redundant with existing bounds.

**RECOMMENDATION: (a) by construction + regression test.** The three invariants (no watcher write, no re-arm, `system:` source) are the whole guarantee; a new guard adds a knob with no benefit.

**VERDICT (architect): ADOPT (a) by construction + ONE regression test; NO runtime guard.** Reachability grep-exhaustive: `_route_via_job_queue` and `_defer_delivery` are reachable ONLY from the adapter wake loop (defs `:2622`/`:1951`; route call `:2562`; defer calls `:2358` semaphore-timeout + `:2855` busy-caller; the manual twin `_execute_trigger :2901` is operator-initiated, not message-reachable) — no automatic re-entry path exists. **Honest nuance recorded:** a recipient agent holding scheduling tools (ari) may consciously call `task_schedule` in its turn — a fresh LLM decision bounded by the loop breaker, NOT an automatic fire→fire loop. The regression test is the tripwire against the only realistic breach: a future edit wiring the notify helper into watcher/schedule surfaces.

---

## DEC-7 — Counter names in the existing idiom

**Question:** What counters, and where do their constant strings live?

**Recommendation:** `schedule_fire_notify_sent_total` / `schedule_fire_notify_dropped_total`, in a NEW sibling module `daemon/services/schedule_fire_notify_metrics.py` mirroring `report_integrity_metrics.py` (lock-guarded module totals `:38-39`, `record_*` whole-body try/except `:67-92`, `attach_sink` `:48-56`, `get_*`/`reset_*` `:95-105`). **Fence-driven placement:** the research idiom puts strings in `daemon/constants.py:1637`, but that file is content-SHA256 sealed by two tests (`test_chart_image_delivery_e2e.py:1488` enforced `:1610-1630`; `test_anomaly_report_duty_prompts.py:166` enforced `:511-525`), and its pins are ALREADY stale at base. Defining the strings in the new unsealed module avoids touching a sealed file and keeps file-disjoint from the tool-metrics lane. **Alternative (architect-gated):** put the strings in `constants.py` and re-pin BOTH seals in the same commit — a deliberate ceremony, not an inline edit. **RECOMMENDATION: strings in the new sibling module.**

**VERDICT (architect): ADOPT sibling module; do NOT touch `constants.py`.** The architect-gated constants.py alternative is explicitly NOT exercised — an edit would force a deliberate two-row re-pin ceremony for two strings, on top of pins already stale at base. The sibling module is fence-free, file-disjoint from the tool-metrics lane, and keeps the sealed-file status quo.

---

## DEC-8 — self_alarm skip-notify rule

**Question:** `reuse_instance` already wakes the creator (the wake IS the message). How is the double-notify avoided? ⚠️ A4 (premise CORRECTED — history retained): that premise holds ONLY for self_alarm-bound schedules; plain tool-created `reuse_instance` schedules wake a first-fire stranger, not the creator.

**Evidence:** `self_alarm` binds a reuse schedule to its creator via `instance_mappings.agent_instance_id` (`self_alarm.py:40,143,867-886` — the binding itself via `_bind_schedule_to_caller`, `:891-989`; `models.py:115`; read at fire by `_resolve_deferrable_target`, `scheduler.py:2442-2452`). ⚠️ A4 correction: ONLY `self_alarm` binds — `create_schedule` uses `caller_instance_id` for audit logging only (`scheduling_service.py:710-715`, `:836-844`; zero `create_instance_mapping` calls in the service) — so a plain `task_schedule` reuse schedule pins to a **first-fire STRANGER** (`mapper.py:470-480`). The deferred path (`_defer_delivery`, `:2109`) delivers to that pinned recipient — stranger OR bound creator, whichever it is.

**Options:** (a) skip by `instance_mode == reuse_instance` string; (b) skip by identity `creator_instance_id == resolved target instance_id`. (a) is brittle — it skips a reuse schedule targeting a DIFFERENT instance and could notify on a new_instance whose creator happens to match; worse (per A4), it would silence exactly the stranger-pinned case, where the creator otherwise never hears. (b) is exact and covers REST-created (⚠️ B1(c) corrected premise: the REST route passes the audit sentinel `caller_instance_id="rest-api"` (`routers/schedules.py:205`), which T1 SUPPRESSES at capture — never persisted → key absent → identity gate fails on absent key → skip, no repo read, no drop counter), new_instance (creator ≠ fresh target → notify), self-alarm-bound reuse (creator == recipient → skip), and plain reuse (creator ≠ stranger → notify).

**RECOMMENDATION: (b) identity rule** at BOTH enqueue sites (A1): notify iff creator present AND creator ≠ recipient. This implements Req 7 precisely and covers the commission's named edge case (reuse targeting an instance OTHER than the creator → notify fires).

**VERDICT (architect): ADOPT (b) identity rule — with the rationale CORRECTED (A4).** The identity rule is correct NOT because "reuse always wakes the creator" but because it discriminates exactly on where the wake lands: bound → skip (the wake IS the notify; Req 7); stranger-pinned → notify (the creator otherwise never hears — the commission's motivating gap). String-match skip (option a) is REJECTED: it would silence exactly the stranger-pinned case and misfire on mode labels. Applies at BOTH sites per DEC-9/A1.

---

## DEC-9 — Deferred-site tension resolution (the explorer-A conflict)

**Question:** Explorer A called `scheduler.py:2109` "mandatory coverage" for a fire-notify hook — is that right for THIS feature?

**Evidence (research-scheduler.md §Q1/Q3):**
- `_emit_scheduled_message` resolves a deferrable target READ-ONLY via `_resolve_deferrable_target()` (`:2805 → :2428`), which is **reuse_instance only** and returns `None` for new_instance by construction (`:2437-2438`).
- Deferral is taken only for "reuse + busy caller" (`:2855`); the deferred message is a durable PENDING delivery to the SAME pinned caller (`:2852-2855`), i.e. the creator for self-created reuse schedules.
- The scheduler's reuse branch is "the only stranger-spawn surface" (`scheduler.py:1964`, `:2433`, `:2804`) and is fenced by `BoundCallerRefusal` (`mapper.py:89-129`).

**Resolution:** Explorer A's "cover both sites" came from the prior-art `scheduler-deferred-alarm-delivery` lane, which built a generic *fired-echo* surface. For a *creator-notify* surface, the deferred recipient IS the creator — notifying it would be the very double-notify Req 7 forbids. The deferred site therefore needs **NO** notify. The notify-site list is exactly ONE entry: direct dispatch `scheduler.py:2723` (result at `:2731`), gated per DEC-8. ⚠️ **A1 SUPERSEDED this resolution** (architect ruling 2026-11-21, commit `30ca10c8e`): the reasoning above rested on "the deferred recipient IS the creator", which holds ONLY for self_alarm-bound schedules — see the VERDICT below and the corrected notify-site table in plan-overview.md. History retained verbatim so no future reader mistakes the superseded skip for the settled ruling.

**RECOMMENDATION: SKIP `:2109`; cover only `:2723`. [SUPERSEDED by the VERDICT below — retained as history]** Record this explicitly in the plan's notify-site table so a future reader does not "fix" it by adding a deferred notify. If the architect instead wants a "schedule fired" echo for the reuse/deferred lane, that is a SEPARATE feature with its own surface (and must respect the self-alarm overlap).

**VERDICT (architect): AMEND — identity gate at BOTH sites; the blanket SKIP `:2109` is UNSAFE.** ⚠️ F1 (reviewer condition — anchor annotation on the ruling text, which is retained verbatim): the ruling's hook point "inside `_defer_delivery` after its successful enqueue (`:2109-2116`)" names the ENQUEUE site, where real ids exist but durability is NOT yet proven — the durable-evidence check (`scheduler.py:2140-2191`) runs after it and, on a taskless enqueue (real ids, NO pending Task), DELETES the stranded row (`:2158-2188`), records FAILED, and re-arms. A hook at the enqueue site would therefore emit a VOID-HANDLE creator notice on the stranded fire plus a SECOND notice on the real delivery. The implemented hook point is the `FIRE_OUTCOME_DEFERRED_DURABLE` finalization (`scheduler.py:2236`, block `:2196-2237`) — AFTER durability is confirmed; everything else in this verdict stands unchanged. The planner's blanket skip rests on "the deferred recipient IS the creator" — true ONLY for self_alarm-bound schedules. For a plain tool-created reuse schedule whose pinned stranger is BUSY, the fire defers (`:2848-2863`), delivery lands in the stranger, and the creator gets nothing — the polling gap reborn in the deferred subcase (prior art records deferred as the NORMAL shape for busy reuse callers). **Ruling:** hook the notify at BOTH enqueue sites with the SAME gate — notify iff creator present AND `creator != recipient`: (1) after the direct enqueue (`scheduler.py:2731`); (2) inside `_defer_delivery` after its successful enqueue (`:2109-2116`), where the recipient (the resolved deferrable target) and `result.job_id`/`result.message_id` are in hand. Hooking INSIDE `_defer_delivery` covers both of its callers (busy-caller `:2855` AND semaphore-timeout `:2358`) uniformly. No double-notify is possible: a single fire takes exactly one path (direct XOR deferred), and on the deferred path the gate skips precisely when recipient == creator (all self_alarm-bound schedules) — Req 7 preserved verbatim. `new_instance` is unaffected: `_resolve_deferrable_target` returns None by construction (`:2437-2438`) → no deferral → the deferred hook is dead code for that mode. One honest mechanism, two call sites — this resolves the explorer-A tension correctly: coverage belongs to the identity gate, not to a blanket skip or a blanket cover.

---

## Cross-cutting recommendation summary

| item | verdict (SETTLED 2026-11-21, `30ca10c8e`) |
|---|---|
| DEC-1 creator-gone | ADOPT drop+log+count for terminal/absent; PAUSED queues; pre-check inside the off-path task (A5) |
| DEC-2 recurrence noise | ADOPT per-fire minimal SYSTEM notice; digest rejected; opt-out knob NOT built in v1 |
| DEC-3 message shape | ADOPT verbatim: exact 5 fields, `system:schedule_fire_notify`, no-spawn, non-user-origin; forgery surface ruled CLOSED |
| DEC-4 creator capture | ADOPT config-JSON key `creator_instance_id`; re-thread = insurance + pin (A6); zero migration |
| DEC-5 async seam | ADOPT `create_task` + exception guard + strong-ref set, with A2 precedent fix + A3 load-bearing failure contract |
| DEC-6 loop safety | ADOPT by construction + ONE regression test; no runtime guard |
| DEC-7 counters | ADOPT new sibling module strings; do NOT touch `constants.py` (alternative NOT exercised) |
| DEC-8 skip rule | ADOPT identity rule (creator == recipient → skip) at BOTH sites; rationale CORRECTED (A4) |
| DEC-9 deferred site | AMENDED: identity gate at BOTH enqueue sites; blanket SKIP `:2109` UNSAFE (A1) |
| A9 notice dedup | recorded, NOT adopted: no idempotency key in v1; named opt-in `schedule_fire_notify:{execution_id}` |
