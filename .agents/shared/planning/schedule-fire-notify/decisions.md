# Decisions — schedule-fire-notify (Architect Question List)

These are **architect ruling items**. Each is framed with code-anchored options and churn/noise evidence, plus MY RECOMMENDATION. The architect rules; the planner does not settle these as done. All anchors verified against worktree `0b5624dc9`.

---

## DEC-1 — Creator-gone mechanism (revive-on-send vs durable-enqueue vs drop+log)

**Question:** The creator instance may be gone (COMPLETED/TERMINATED/ERROR/FAILED, or deleted) when a later fire wants to notify it. What happens?

**Options (code-anchored):**
- **(a) Revive-on-send.** Reuse `enqueue_message_job`'s built-in terminal→revive: status flipped to RUNNING in the same transaction (`instance_messaging.py:2714-2732`), a row-write with no revive budget (`manager.py:12299-12302`), checkpoint reused (`:2690-2694`). Pro: the notice always lands. Con: **churn** — a DAILY schedule revives a dead creator every day to deliver a notice it will ignore; each revive re-arms dependency watchers (`:2594`).
- **(b) Durable-enqueue without revive.** Queue a PENDING Task to the terminal/dead instance without status flip. Con: the enqueue lane's terminal branch auto-revives by construction (`:2714-2726`); suppressing it means a new code path, and a dead instance never claims the Task → silent pile-up. Complex and leaky.
- **(c) Drop + log + count.** Before enqueue, check the creator's status; if terminal/absent, skip delivery and increment `schedule_fire_notify_dropped_total`. Pro: no churn, one honest mechanism, no knobs. Con: a once-schedule whose creator died loses its notice — but a dead creator cannot act on it anyway.

**Churn/noise evidence:** Emperor's directive ("simple always better"); the motivating case (ari) is a live creator. `self_alarm` already refuses stranger-spawn for a confirmed-gone bound caller (`BoundCallerRefusal`, `mapper.py:89-129`, `:420-460`) — the system already treats "confirmed gone creator" as a refusal, not a revive target. PAUSED is a distinct, non-gone state (queues PENDING, claimed on resume, `:2695-2699`) and must NOT be dropped.

**RECOMMENDATION: (c) drop+log+count for terminal/absent creators; PAUSED still queues normally.** One mechanism, no per-schedule knob. If the architect wants once-schedules to revive their creator, that is the one narrow exception — but it reintroduces a knob, so default to (c).

---

## DEC-2 — Recurrence-noise strategy (digest vs per-fire vs opt-out)

**Question:** Daily/weekly schedules fire often (live case: `daily-skill-system-watch` at 05:00). How does the notice stay cheap and non-spammy?

**Options:**
- **(a) Per-fire notice.** One tiny SYSTEM message per fire. Pro: simplest, no state, no loop, handle arrives immediately. Con: one message/day for a daily schedule.
- **(b) Digest.** Accumulate fires and send periodically. Con: needs a timer/polling loop or accumulation state — directly against the prior-art architect ruling "NO new table / migration / daemon service / polling loop" (`scheduler-deferred-alarm-delivery` §16); higher complexity; stale handles by the time it lands.
- **(c) Opt-out flag.** A per-schedule `fire_notify_enabled` (config-JSON key) lets a creator silence a noisy schedule. Pro: user control. Con: a knob (Emperor prefers fewer); only worth it if per-fire proves noisy.

**Noise evidence:** the notice is `MessageType.SYSTEM`, so it does NOT reset the leader's fresh-episode attestation ledger (`msg_type==SYSTEM` fails the HUMAN conjunct, `instance_messaging.py:2733-2764`) and does NOT arm a user-origin window (`classify_user_origin` fail-closed). It is one cheap PROCESS_MESSAGE turn. A single small notice/day is not spam by any system measure.

**RECOMMENDATION: (a) per-fire, minimal fields, SYSTEM type.** Defer (c) as a follow-up knob only if real noise appears; reject (b) (polling-loop gravity).

---

## DEC-3 — Notification message shape (exact fields, source, no-spawn, non-user-origin)

**Question:** What exactly is in the notice, and how is it classified?

**Recommendation (framed as the proposed contract):**
- **Fields — EXACT, five groups, nothing else:** `label` (`SourceConfig.name`, `self.config.name`), `fired_at` (`metadata["scheduler.trigger_time"]`, `:2517-2539`), `target_agent` + `instance_mode` (`self._agent`, `self._instance_mode`), `recurrence` (`self._schedule_type` / config echo), work handle (`result.job_id`, `result.message_id`, resolved `instance_id` at `:2661`). **No schedule `message`/body echo** (bodies carry large briefs — Req 1).
- **Source:** `system:schedule_fire_notify` — a NEW reserved literal, minted only in daemon code. NEVER bare `"api"` (DF-4 forgery, `jobs_crud.py:509-516`); NEVER bare `"scheduler"` (that would anchor SOURCE-U and derive HUMAN — wrong semantics, `test_memoir_wake_refresh.py:524`).
- **No-spawn guarantee:** pass the known creator `instance_id` straight to `enqueue_message_job`; that lane addresses an EXISTING row and fails closed on a missing one (`instance_messaging.py:2488-2493`, backstop `:2700-2710`); no mapper, no create-instance branch (`research-messaging.md` §6b).
- **Non-user-origin classification:** `system:` → `is_reserved_source` True (`constants.py:1278`), `classify_user_origin` → `(False, "reserved-internal")` (checked BEFORE registry, `upgrade_journal.py:5406-5410`), SYSTEM msg type (`:1488`), stamped internal (`:1401`), no provenance-window inheritance (`manager.py:6382-6386`). Zero constant edits.

**RECOMMENDATION: adopt this contract verbatim.** It is the intersection of Req 1/5/6 and the research-verified zero-ceremony `system:` path.

---

## DEC-4 — Creator-capture persistence (column vs config-JSON key; opt-out storage)

**Question:** Where does the creating instance id live so a later fire can find it?

**Options:**
- **(a) Config-JSON key `creator_instance_id`** in `_build_config_json` (`scheduling_service.py:422-454`). Gains: zero migration (JSONB exists, `models.py:70-73`); survives source-GC archive/restore (config snapshot is sole reconstruction input, `:224-230`); matches `_defer_*` state-carriage precedent. Loses: no typing/queryability; **full-replace update hazard** — `update_source_config` replaces the whole dict, so `task_schedule_update` must re-thread the key.
- **(b) Additive column** `source_configs.creator_instance_id TEXT` — migration `20261121_HHMMSS_add_creator_instance_id_to_source_configs.sql` (autostart shape, `20260614_000001:8`), nullable, no backfill (identity never recorded), must mirror into `SourceConfigArchive` for GC round-trip. Gains: typed/queryable. Loses: a migration (against prior-art "no new migration" gravity), archive-mirror work, backfill impossible anyway.

**Fence evidence:** no exact-set pin on `source_configs` columns; no exact-dict pin on real `_build_config_json` output. The exact-dict pin at `test_self_alarm_tools.py:1191` is a hand-built `FakeRow`, not a live builder — neither option trips it. Both are legal.

**Opt-out storage (if DEC-2(c) is chosen):** also a config-JSON key (e.g. `fire_notify_enabled`), same full-replace re-thread caveat.

**RECOMMENDATION: (a) config-JSON key.** Zero migration, GC-safe, prior-art-aligned; the typing loss is acceptable because the only consumer is the adapter at fire time (reads config at `__init__`, `:326`). Mitigate the update hazard with a re-thread regression test. If the architect requires SQL queryability, (b) is the fallback with an additive-only migration.

---

## DEC-5 — Async best-effort delivery seam (create_task vs queue)

**Question:** How is the notify dispatched off the scheduler's critical path?

**Options:**
- **(a) `asyncio.create_task(notify_schedule_fire(...))`** with its own exception guard, immediately after the awaited enqueue at `:2731`. Pro: zero latency, zero failure coupling; exact in-file precedent (mission-record mint `create_task`, `scheduler.py:2710-2721`). Con: in-flight notice lost on daemon restart (acceptable — informational). Requires a strong task reference (module-level set + done-callback discard) to avoid GC.
- **(b) Work-queue the notify.** Pro: durable. Con: new job type / loop surface; heavier than a notice warrants; risks being mistaken for scheduled work (loop-safety smell).

**RECOMMENDATION: (a) `create_task` with exception guard + strong-ref set.** Matches Req 2 and both cited precedents (`report_integrity_metrics.py:59-89`).

---

## DEC-6 — Loop-safety enforcement

**Question:** How do we guarantee the notice cannot trigger scheduling or further notices (one hop)?

**Evidence:** message-triggered work is already bounded — graph loop breaker (`graph.py:214`, threshold 3), dependency-bus CAS one-shot (`dependency_bus/repository.py:14-27`, no re-arm of watchers on fire), child_reports dedup by exact source (`child_reports.py:4877`). A schedule is created ONLY by an explicit agent tool call (`daemon/tools/scheduling.py`); a reserved-source message produces exactly one PROCESS_MESSAGE turn.

**Options:** (a) enforce by construction (notify never touches watchers/schedules/mapper, uses `system:` source) + a regression test asserting zero new schedule rows and zero dependency-watcher rows after a notice; (b) add a dedicated loop-guard flag/constant. (b) is redundant with existing bounds.

**RECOMMENDATION: (a) by construction + regression test.** The three invariants (no watcher write, no re-arm, `system:` source) are the whole guarantee; a new guard adds a knob with no benefit.

---

## DEC-7 — Counter names in the existing idiom

**Question:** What counters, and where do their constant strings live?

**Recommendation:** `schedule_fire_notify_sent_total` / `schedule_fire_notify_dropped_total`, in a NEW sibling module `daemon/services/schedule_fire_notify_metrics.py` mirroring `report_integrity_metrics.py` (lock-guarded module totals `:38-39`, `record_*` whole-body try/except `:67-92`, `attach_sink` `:48-56`, `get_*`/`reset_*` `:95-105`). **Fence-driven placement:** the research idiom puts strings in `daemon/constants.py:1637`, but that file is content-SHA256 sealed by two tests (`test_chart_image_delivery_e2e.py:1488` enforced `:1610-1630`; `test_anomaly_report_duty_prompts.py:166` enforced `:511-525`), and its pins are ALREADY stale at base. Defining the strings in the new unsealed module avoids touching a sealed file and keeps file-disjoint from the tool-metrics lane. **Alternative (architect-gated):** put the strings in `constants.py` and re-pin BOTH seals in the same commit — a deliberate ceremony, not an inline edit. **RECOMMENDATION: strings in the new sibling module.**

---

## DEC-8 — self_alarm skip-notify rule

**Question:** `reuse_instance` already wakes the creator (the wake IS the message). How is the double-notify avoided?

**Evidence:** `self_alarm` binds a reuse schedule to its creator via `instance_mappings.agent_instance_id` (`self_alarm.py:40,143,867-886`; `models.py:115`; read at fire by `_resolve_deferrable_target`, `scheduler.py:2442-2452`). The deferred path (`_defer_delivery`, `:2109`) is the busy-caller variant delivering to that same pinned caller (`:2848-2863`).

**Options:** (a) skip by `instance_mode == reuse_instance` string; (b) skip by identity `creator_instance_id == resolved target instance_id`. (a) is brittle — it skips a reuse schedule targeting a DIFFERENT instance and could notify on a new_instance whose creator happens to match; (b) is exact and covers REST-created (no creator → skip) and new_instance (creator ≠ fresh target → notify).

**RECOMMENDATION: (b) identity rule** at the direct site (`:2723`): notify iff creator present AND creator ≠ resolved target. This subsumes the self_alarm/deferred overlap and implements Req 7 precisely.

---

## DEC-9 — Deferred-site tension resolution (the explorer-A conflict)

**Question:** Explorer A called `scheduler.py:2109` "mandatory coverage" for a fire-notify hook — is that right for THIS feature?

**Evidence (research-scheduler.md §Q1/Q3):**
- `_emit_scheduled_message` resolves a deferrable target READ-ONLY via `_resolve_deferrable_target()` (`:2805 → :2428`), which is **reuse_instance only** and returns `None` for new_instance by construction (`:2437-2438`).
- Deferral is taken only for "reuse + busy caller" (`:2855`); the deferred message is a durable PENDING delivery to the SAME pinned caller (`:2852-2855`), i.e. the creator for self-created reuse schedules.
- The scheduler's reuse branch is "the only stranger-spawn surface" (`scheduler.py:1964`, `:2433`, `:2804`) and is fenced by `BoundCallerRefusal` (`mapper.py:89-129`).

**Resolution:** Explorer A's "cover both sites" came from the prior-art `scheduler-deferred-alarm-delivery` lane, which built a generic *fired-echo* surface. For a *creator-notify* surface, the deferred recipient IS the creator — notifying it would be the very double-notify Req 7 forbids. The deferred site therefore needs **NO** notify. The notify-site list is exactly ONE entry: direct dispatch `scheduler.py:2723` (result at `:2731`), gated per DEC-8.

**RECOMMENDATION: SKIP `:2109`; cover only `:2723`.** Record this explicitly in the plan's notify-site table so a future reader does not "fix" it by adding a deferred notify. If the architect instead wants a "schedule fired" echo for the reuse/deferred lane, that is a SEPARATE feature with its own surface (and must respect the self-alarm overlap).

---

## Cross-cutting recommendation summary

| item | recommendation |
|---|---|
| DEC-1 creator-gone | drop+log+count for terminal/absent; PAUSED still queues |
| DEC-2 recurrence noise | per-fire minimal SYSTEM notice; defer opt-out; reject digest |
| DEC-3 message shape | exact 5 fields, `system:schedule_fire_notify`, no-spawn, non-user-origin |
| DEC-4 creator capture | config-JSON key `creator_instance_id`; re-thread on update; zero migration |
| DEC-5 async seam | `create_task` + exception guard + strong-ref set |
| DEC-6 loop safety | by construction + regression test; no new guard |
| DEC-7 counters | new sibling module strings; constants.py alternative is architect-gated re-pin |
| DEC-8 self_alarm skip | identity rule creator == target → skip |
| DEC-9 deferred site | SKIP `:2109`; cover only `:2723` |
