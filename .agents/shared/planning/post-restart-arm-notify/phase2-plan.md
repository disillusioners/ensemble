# Phase 2: Boot Sweep + Wake Delivery — `sweep_wake_records` + `enqueue_message`

> **⛔ HARD CONSTRAINT (inherited from Phase 2, governs every task below):**
> NEVER touch the live/production ensemble environment — it is the running environment of Ari and all live agents (~/agents-ensemble, port 9797, prod DB, ENSEMBLE_DEPLOY_LIVE are out of bounds; live pids must remain untouched). ALL work/testing/drills in dev and demo only. If any plan step would require touching live, mark it as USER-GATED and design it as an explicit user-confirmed action. Sandbox instances (own port + throwaway PG) are fine.

**ADR basis:** ADR-040 (Boot sweep + wake delivery primitive), ADR-041 (Routing preservation — re-stamp the user-origin window), ADR-042 (Terminal-state gating & abandonment policy), ADR-044 (Kill-switch & boot-never-wedge — the sweep-side gate). Cross-cutting invariants 2, 3, 6, 7, 9, 10, 11 of `architecture-recommendation.md` §8 are asserted by this phase.

**Scope of environment:** all implementation work targets **demo** (`~/agents-ensemble-demo`, :7979, `ensemble_demo`) and **sandboxes** (own port + throwaway PG). Live target paths exist in the scripts behind guards but their execution is **USER-GATED** and never performed by this initiative.

**Convention note:** any new Python module this phase adds uses `from __future__ import annotations` for Python 3.13 import safety. The sweep's wake branch is a new sub-routine of the existing boot pass; the existing pass is unchanged.

---

## Objective

Make the wake **deliverable**: extend `UpgradeJournalSweepService`
with a `sweep_wake_records()` sub-routine that fires on the existing
guaranteed pre-yield boot pass AND the 90s periodic tick, reads the
`pending_wakes` records minted by Phase 1, gates each on a terminal-class
journal event (D-FA4.1), re-stamps the user-origin window for the wake
turn (D-FA3.3), and calls `manager.enqueue_message` with the recorded
`source` (D-FA3.2). Delivery is **best-effort, never raises**; the
sweep is wrapped in `try/except Exception` at the api.py call site
(never aborts boot, D-FA5.4). Idempotency is structural (the
`status` field is the key; an unrelated later restart sees a CLEAN
dict and re-delivery is impossible).

**Exit in one sentence:** on demo, after an induced daemon restart
with a `pending_wakes` record + a terminal-class history event in
the journal, the boot pass delivers ONE wake to the recorded arming
instance with `source=<recorded>`, the wake's `MessageQueue.message_id`
is captured, the user-origin window is re-stamped for the wake turn,
and the record is removed from the dict — all proven by journal
reads + MessageQueue row inspection + the new unit pack (T2.1–T2.4,
T3.1–T3.4, T5.7–T5.11, T5.16) at 100% green.

---

## Verified Starting Point (do not re-derive)

- The wake primitive: `manager.enqueue_message(instance_id, message, source, priority, metadata, ...)` (`manager.py:7935-7996` →
  `instance_messaging.py:2108`) is THE existing internal wake
  primitive. It creates `MessageQueue` + `Task` rows in ONE
  transaction, notifies the worker pool. Revive semantics
  auto-flip terminal→RUNNING (`instance_messaging.py:1954-1976`);
  `PAUSED` is exempt (held until resume at `:2154-2161`,
  `:1925-1934`).
- The boot seam: `daemon/api.py:1477-1523` constructs and starts
  `UpgradeJournalSweepService` with a guaranteed pre-yield boot
  pass. Cadence: 90s tick + boot pass. The boot pass is the
  load-bearing path; the periodic tick is the long-downtime
  recovery path. The wake-detection sweep rides the SAME service.
- Worker pool boot order: `setup_worker_pool` (api.py:439) →
  `JobProcessor.start` (`:1371`) → `start_sources` (`:1416`) →
  `UpgradeJournalSweepService` (`:1477-1523`). The worker pool
  is up when the wake fires (R-10 verified).
- The user-origin window: `manager._user_origin_windows[instance_id]`
  (`manager.py:4239-4245`) and
  `manager._user_origin_last_stamp[instance_id]`
  (`manager.py:4230-4238`) are in-memory per-instance stamps,
  wiped at boot. The wake re-stamps the window for the wake turn
  via `manager.stamp_user_origin_window` so a follow-up
  `upgrade_status` call within the same wake turn can pass the
  3-factor gate's factor-2.
- The terminal-state predicate (CORRECTED per architecture delta
  #1): `_TERMINAL_EVENTS` at `upgrade_journal.py:983` is SIX
  members — `(commit, rollback, halt, sweep_rollback, sweep,
  quarantine)` — and contains **NO `"restart"`** (the exclusion is
  deliberate: the PROMOTE-only reconcile at `:1016` depends on it).
  The wake is therefore gated on the SIBLING constant
  `WAKE_TERMINAL_EVENTS = _TERMINAL_EVENTS + ("restart",)` +
  a wake-owned reader defined in THIS phase (T13) — never on a
  mutated `_TERMINAL_EVENTS`. A new event name added to
  `_TERMINAL_EVENTS` propagates through the `+` concat (R-18).
- The reconcile: `reconcile_pending_op` is PROMOTE-kind only
  (`upgrade_journal.py:1002-1067`). The wake sweep is RESTART-kind
  aware and is the only consumer that knows the wake record exists
  — it does not depend on the reconcile for the wake path.
- The existing refusal vocabulary: the arm-side refusal tokens
  (`pipeline-busy`, `journal-unavailable`, `live-restart-refused`,
  etc.) are unchanged (Phase 2 cross-cutting invariant from the
  parent plan's non-goals). The wake record is written only on
  a successful arm; refused arms produce no record.

---

## Design Decisions (this phase)

**D1 — Boot sweep is a NEW sub-routine of the existing boot pass
(D-FA3.1, ADR-040).** The new method is
`UpgradeJournalSweepService.sweep_wake_records() -> WakeSweepResult`,
called from the existing boot pass immediately after
`_boot_uj.reconcile_pending_op(upgrade_install_dir)` at api.py:1500
(wrapped in `try/except Exception` that logs WARNING and never
aborts boot, D-FA5.4 / invariant 6). The 90s periodic tick also
carries the wake sweep (D-FA3.4) — the same sub-routine, called from
the existing tick handler. The sweep is in-process, single-threaded;
the boot-pass-vs-tick race is impossible by construction (the
service is an asyncio task, not a multi-process pool — D-FA4.3).

**D2 — Wake delivery is `enqueue_message` with re-stamped source
(D-FA3.2, ADR-040 + ADR-041).** The wake's `source` is the recorded
arm-time source; the agent's response routes back to the original
chat (AC3, invariant 10). The body is a **self-describing pointer**
(D-FA3.2): the wake says "call `upgrade_status(run_id=...)` and
report back to the user" — the LLM in the agent's first turn does
the work, no LLM in the critical path (invariant 9). The metadata
carries `{"system_context": {"kind": "post_restart_arm_notify", ...},
"delivery": {"channel": "post_restart_arm_notify"}}` (mirrors the
WC watchdog pattern at `manager.py:7984`). The
`manager.stamp_user_origin_window` re-stamp is the FIRST step of
delivery, so a follow-up `upgrade_status` call within the same wake
turn passes the 3-factor gate's factor-2 (ADR-041). ⟪Architecture
delta #8 (SHOULD): the pre-enqueue stamp is DEFENSIVE/REDUNDANT —
the BINDING stamp is the natural one at `manager.py:8112`
(`_process_message_with_tracking` stamps every processed message
with its own source), which fires for the wake turn even if the
daemon restarts between enqueue and processing; window lifetime is
`NONCE_TTL_S = 3600s` (`manager.py:4220`), NOT turn-bound;
`discord` is whitelisted via `USER_ORIGIN_CHAT_SOURCE_TYPES`
(`upgrade_journal.py:1944-1946`); the window clear on the next
non-user-origin dispatch (`manager.py:4247`) does not endanger the
wake turn. Retained as belt-and-braces; T3.3 asserts "window is
set after wake delivery", never "exactly one stamp call" (ADR-041
addendum in `decisions.md`).⟫

**D3 — Terminal-state gating predicate is the wake's
load-bearing AC4 guarantee (D-FA4.1, ADR-042; architecture delta
#1).** The wake is delivered only when the WAKE-OWNED terminal
reader — `wake_terminal_event_after(journal, armed_at)` built on
the sibling constant `WAKE_TERMINAL_EVENTS = _TERMINAL_EVENTS +
("restart",)` (T13, defined in `upgrade_journal.py` by THIS
phase) — returns a non-`None` event name. The reader is
armed_at-scoped, run_id-matched, and restart-kind aware; it
wraps the Phase 1 parameterized walker `latest_matching_event`.
`_TERMINAL_EVENTS` itself is NEVER mutated (the PROMOTE-only
reconcile's semantics at `:1016` depend on the 6-member set).
Without this sibling constant the wake would NEVER fire for
intentional restarts — the dominant case (`restart.sh:262`
journals `"restart"`, which `_TERMINAL_EVENTS` rejects). Pending
wakes (no terminal event yet) are HELD — the next tick retries.
The grace is `PENDING_WAKE_GRACE_S = 600` (default, tunable via
`.env`). Past the grace, the wake is marked `abandoned` (D-FA1.2)
and a `wake_abandoned` history event is journaled for forensics.

**D4 — One-shot delivery via status lifecycle (D-FA1.2 + D-FA5.3,
invariant 7).** The CAS transition `pending → delivering`
acquires the journal lock; a subsequent tick observing a
`delivering` record is the CAS-loser case — the tick skips it
(it is in flight). The unconditional `delivering → delivered`
write (after `enqueue_message` returns the `message_id`) REMOVES
the record from the `pending_wakes` dict (the structural
removal is the idempotency key). An unrelated later restart
observes a CLEAN dict and re-delivery is impossible.

**D5 — Best-effort, never wedges (D-FA5.4, ADR-044, invariant 6).**
The sweep is wrapped at TWO levels: (a) the per-wake
`try/except Exception` inside `_deliver_wake` increments a
`WakeSweepResult.errors` counter and continues to the next wake;
(b) the sweep-level `try/except Exception` in
`sweep_wake_records` returns a clean result and logs WARNING. The
api.py call site wraps the boot pass call in
`try/except Exception` and never aborts boot. A `JournalTorn` from
`journal_read` is caught at the sweep level — the sweep returns
a result with `errors=1` and continues to the next tick.

**D6 — Kill-switch is `ENSEMBLE_POST_RESTART_ARM_NOTIFY=0`,
default ON (D-FA6.2, ADR-044, invariant 11).** The sweep's
`_is_enabled()` short-circuits the entire wake branch. Phase 1's
arm-side write is also gated on the same env. The default is
ON — the feature is the zero-user-action deliverable; the
kill-switch exists for operator opt-out, not for default-off.

**D7 — Periodic tick covers the long-downtime case (D-FA3.4).**
The 90s periodic tick fires the same `sweep_wake_records()`. This
covers the case where the daemon was down for an entire arm +
completion cycle and the boot pass fired BEFORE the terminal event
was journaled (e.g. the pipeline runner journaled the terminal
event during the next boot's first tick, not the boot pass
itself). The periodic tick is the retry path; the boot pass is
the fast path.

---

## Components (file-level touch list)

### Source files modified

| File | What changes | Mandate (architecture section + ADR) |
|---|---|---|
| `daemon/services/upgrade_journal_sweep.py` | (a) Add `sweep_wake_records(self) -> WakeSweepResult` method on `UpgradeJournalSweepService`; (b) add `_is_enabled(self) -> bool` (kill-switch gate); (c) add `_resolve_wake_targets(self, pending: list[PendingWake]) -> Iterable[tuple[PendingWake, str]]` (terminal-state predicate per wake, yielding only wakes with a terminal event); (d) add `_deliver_wake(self, install_dir, wake, terminal_outcome)` inner method (the `enqueue_message` call + re-stamp + metadata shape); (e) define `WakeSweepResult` dataclass (`pending_at_start`, `pending_at_end`, `delivered`, `abandoned`, `errors`, `coalesce_overflows`); (f) call `sweep_wake_records()` from the existing 90s tick handler | D-FA3.1, D-FA3.2, D-FA3.3, D-FA3.4, D-FA4.1, D-FA5.3, D-FA5.4, D-FA6.2 → ADR-040, ADR-041, ADR-042, ADR-044 |
| `daemon/api.py` | (a) Extend the existing boot pass at `:1500` with a wrapped call: `try: wake_result = await upgrade_journal_sweep.sweep_wake_records(); logger.info(...) except Exception as boot_exc: logger.warning(...)`. The new call is immediately after `_boot_uj.reconcile_pending_op(upgrade_install_dir)`. (b) The lifespan order is unchanged (worker pool → job processor → sources → sweep service); the new call rides the existing service's boot pass | D-FA3.1, D-FA5.4, R-10 → ADR-040, ADR-044 |

### Test files created

| File | What it covers | Test-strategy case IDs |
|---|---|---|
| `tests/unit/services/test_post_restart_arm_notify_sweep.py` | The sweep's boot pass enqueues a wake; the empty-case fast path; pending wakes are held; mixed batch delivers only the terminal ones; missing-instance ari fall-back; missing-instance no-ari journals notice; coalesce by instance; separate instances get separate wakes; coalesce cap drops overflow; coalesce body format; idempotent (skip delivered); CAS-loser skip; `JournalTorn` skip; enqueue failure continue; kill-switch short-circuits; abandon-on-switch-off one-time pass; CAS wait_s≈30 contention; delivered-mark failure leaves `delivering` (at-least-once); malformed-record skip; `install_dir=None` no-op | T2.1, T2.2, T2.3, T2.4, T5.1, T5.2, T5.3, T5.4, T5.5, T5.6, T5.7, T5.8, T5.9, T5.10, T5.11, T5.16 |
| `tests/job_queue/test_post_restart_arm_notify_routing.py` | Source = recorded source; full routing path with stub source adapter (the stub captures BOTH `dispatch_message` AND `dispatch_completed`); user-origin window re-stamped; empty source uses `"api"` sentinel. (Phase 3 extends this pack with T3.5 — the Site 1 progressive-dispatch test, architecture delta #6) | T3.1, T3.2, T3.3, T3.4 |

### Files NOT touched (explicit non-modification)

- `daemon/manager.py` — no new public method. The wake uses the
  existing `manager.enqueue_message(...)` (manager.py:7935) and
  the existing `manager.stamp_user_origin_window(...)`
  (manager.py:4230-4245). No new surface (invariant 2, ADR-040).
- `daemon/tools/upgrade_journal.py` — Phase 1 added the lifecycle
  helpers and the parameterized walker; Phase 2 touches this file
  ONLY for architecture delta #1 (T13: the sibling constant
  `WAKE_TERMINAL_EVENTS = _TERMINAL_EVENTS + ("restart",)` + the
  wake-owned reader `wake_terminal_event_after` wrapping the Phase 1
  walker). `_TERMINAL_EVENTS` itself is NOT mutated. The CAS, the
  structural removal, and the walker are all from Phase 1.
- `daemon/api.py` URL router — no new HTTP endpoint (invariant 3).
  The wake is internal to the daemon; the wake's user-facing
  surface is the agent's first turn (which is a normal
  `MessageQueue` row).
- `daemon/instance_messaging.py` — no change. The wake reuses the
  existing `enqueue_message` (line 2108) and the existing
  terminal→RUNNING revive (lines 1954-1976) and the existing
  PAUSED-exempt claim gate (lines 2154-2161, 1925-1934).
- `daemon/migrations/` — no new migration; Phase 1 already
  established the journal-section approach (ADR-039, no new table).

---

## Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| **T1** | **Add `WakeSweepResult` dataclass to `daemon/services/upgrade_journal_sweep.py`** — fields: `pending_at_start: int`, `pending_at_end: int`, `delivered: int`, `abandoned: int`, `errors: int`, `coalesce_overflows: int`. Default-constructible; the sweep returns one per tick. Mirrors the shape of the existing sweep's results (the `reconcile_pending_op` boot note is a similar small struct) | none | Unit: `WakeSweepResult()` defaults to all zeros; the dataclass is JSON-serializable for the structured log line |
| **T2** | **Add `_is_enabled(self) -> bool` on `UpgradeJournalSweepService`** — reads `os.environ.get("ENSEMBLE_POST_RESTART_ARM_NOTIFY", "1") != "0"`. Returns `True` when env unset / `"1"` / any other value; returns `False` only when env explicitly `"0"`. Cached at service construction is NOT done — the env is read per tick (operator can flip without restart) | none | Test: `monkeypatch.setenv("ENSEMBLE_POST_RESTART_ARM_NOTIFY", "0")` → `_is_enabled()` returns `False`; unset / `"1"` / `"true"` → returns `True` |
| **T3** | **Add `_resolve_wake_targets(self, pending: list[PendingWake]) -> list[tuple[PendingWake, str]]`** — for each pending wake, calls the wake-owned terminal reader (`wake_terminal_event_after`, T13) with the wake's `run_id` + `armed_at`; if the reader returns a non-`None` event name, yields `(wake, event_name)`; if `None`, holds the wake (the record stays in the dict, the sweep moves on). The order of the output matches the input order (preserves any upstream sort; the Phase 3 coalesce runs on the output) | Phase 1 T7 + T13 (this phase) | Unit: a mixed batch (one terminal, one pending) yields only the terminal one; a JournalTorn during predicate is caught per-wake and the wake is held (the sweep continues) |
| **T4** | **Add `_coalesce_wakes(self, terminal_wakes: list[tuple[PendingWake, str]]) -> dict[str, tuple[list[PendingWake], list[str]]]`** — groups by `arming_instance_id`; per-group, sorts by `armed_at` DESC (newest first); caps each group at `PENDING_WAKE_COALESCE_MAX = 16` (default, tunable). Overflow: the dropped records' `run_id`s are accumulated into a `coalesce_overflows: list[str]`; a `wake_coalesce_overflow` history event is journaled via `journal_history_append` (D-FA5.2) | T3 | Unit: 17 wakes for the same instance → 1 group of 16 + 1 dropped; the dropped `run_id` is in the overflow list; the journal `history` ends with a `wake_coalesce_overflow` event naming the dropped `run_id` |
| **T5** | **Add `_format_wake_body(self, wake, terminal_outcome) -> str` and `_format_coalesced_wake_body(self, wakes, terminal_outcomes) -> str`** — single-wake body: "Post-restart arm-notify. Your armed `<kind>` `<run_id>` completed during the daemon downtime. Outcome: `<terminal_outcome>`. Target: `<target_version or "n/a">`. Armed at: `<armed_at>`. Wake at: `<now>`. This is an auto-wake — the daemon restarted, the pipeline is terminal, and you (the arming instance) are being notified so you can call `upgrade_status(run_id="<run_id>")` and report back to the user. The report will route to the same channel where the arm was confirmed." Coalesced body: "Post-restart arm-notify (coalesced — N arms pending for this instance). The most recent armed `<kind>` `<run_id>` completed during the daemon downtime. Outcomes (newest first): `- <run_id_a>: <outcome_a> at <armed_at_a>`; ... This is an auto-wake — call `upgrade_status(run_id=...)` on each, or `upgrade_status()` with no args to enumerate all." (D-FA3.2 + D-FA5.2) | T4 | Unit: the body format is byte-exact vs the documented format; a coalesced body for 3 wakes contains all 3 `run_id`s and `terminal_outcome`s; the body stays under 64KB even for the 16-coalesce cap (~1280 chars documented in R-13) |
| **T6** | **Add `_deliver_wake(self, install_dir, wake, terminal_outcome) -> str \| None`** — the inner delivery method. Step 1: re-stamp the user-origin window if `wake.source` is non-empty (`manager.stamp_user_origin_window(wake.arming_instance_id, source=wake.source, message_id=wake.message_id)`). Step 2: format the body (T5). Step 3: call `await manager.enqueue_message(instance_id=wake.arming_instance_id, message=body, source=wake.source or "api", priority=2, metadata={"system_context": {"kind": "post_restart_arm_notify", "run_id": wake.run_id, "arm_kind": wake.kind, "terminal_outcome": terminal_outcome, "target_version": wake.target_version, "armed_at": wake.armed_at, "wake_at": now_iso()}, "delivery": {"channel": "post_restart_arm_notify"}})`. Step 4: return `result.message_id` on success; log WARNING + return `None` on any exception. The method is `async` because `enqueue_message` is `async`. ⟪Task note (architecture delta #8): the Step-1 re-stamp is DEFENSIVE/REDUNDANT — the natural stamp at `manager.py:8112` is binding and fires for the wake turn regardless; do NOT assert "exactly one stamp call" anywhere; see D2 and the ADR-041 addendum in `decisions.md`.⟫ (D-FA3.2 + D-FA3.3 + D-FA5.4 + ADR-041) | T5 | Unit (with `AsyncMock` manager): the call is made with the documented args; on `AsyncMock` returning a fake `message_id`, the method returns the `message_id`; on `AsyncMock` raising, the method returns `None` and logs WARNING |
| **T7** | **Add `sweep_wake_records(self) -> WakeSweepResult` public method on `UpgradeJournalSweepService`** — the boot + periodic entry point. Order: (a) `_is_enabled()` → if false, return `WakeSweepResult()`; (b) read `install_dir` (service field; `None` → return clean result); (c) `try: data = journal_read(install_dir); except JournalTorn: log + return WakeSweepResult(errors=1)`; (d) `pending = list_pending_wakes(install_dir)`; (e) record `pending_at_start = len(pending)`; (f) `terminal_wakes = self._resolve_wake_targets(pending)`; (g) `coalesced = self._coalesce_wakes(terminal_wakes)`; (h) for each `(wakes, outcomes)` in `coalesced.items()`: try-block per group — `mark_wake_delivering(install_dir, wakes[0].run_id)` (CAS, one per group; the others in the group are marked delivered in the same call's structural sweep after delivery — see T9); `message_id = self._deliver_wake(install_dir, wakes[0], outcomes[0])`; on success, `mark_wake_delivered(install_dir, wakes[0].run_id, message_id)` for ALL wakes in the group (single dict-removal pass); on failure, hold the wakes (do not mark abandoned yet — wait for the next tick); (i) record `pending_at_end = len(list_pending_wakes(install_dir))`; (j) handle overflow: `journal_history_append(install_dir, "wake_coalesce_overflow", ...)` for the coalesce-cap drops; (k) sweep-level `try/except Exception` that logs WARNING and returns a result with `errors += 1` (D-FA5.4). All work is in-process; no new locks beyond the journal lock. (D-FA3.1 + D-FA3.4 + D-FA4.1 + D-FA5.3 + D-FA5.4) | T1, T2, T3, T4, T5, T6 | Unit: a `tmp_path` journal with one `pending` wake + matching terminal event → `WakeSweepResult(delivered=1, ...)`; the wake is removed from the dict; a `JournalTorn` (simulated) → `WakeSweepResult(errors=1, ...)`; the kill-switch on → clean `WakeSweepResult()` with no `enqueue_message` call |
| **T8** | **Wire the sweep into the existing 90s tick handler in `UpgradeJournalSweepService`** — find the existing periodic-tick loop (the same loop that already calls the reconcile / GC / executor reaper). Add `await self.sweep_wake_records()` immediately after the existing sub-routines in the same try-block. The new sub-routine never raises (T7's sweep-level catch); a failure increments `errors` and logs WARNING. (D-FA3.4) | T7 | Unit: a sweep test that calls the tick handler (with the other sub-routines mocked) verifies `sweep_wake_records` is invoked; on a tick after a torn journal, the tick continues (the next sub-routine still runs) |
| **T9** | **Wire the sweep into the boot pass in `daemon/api.py:1477-1523`** — immediately after the existing `boot_note = _boot_uj.reconcile_pending_op(upgrade_install_dir)` call, add a wrapped call: `try: wake_result = await upgrade_journal_sweep.sweep_wake_records(); logger.info("UpgradeJournalSweepService wake boot sweep: %s", wake_result) except Exception as boot_exc: logger.warning("UpgradeJournalSweepService wake boot sweep failed: %s", boot_exc)`. The lifespan order is unchanged. (D-FA3.1 + D-FA5.4 + R-10) | T7, T8 | Demo: after a `system_restart(target=demo)` arm + an induced daemon restart (kill the daemon, then restart via launcher.sh), the boot pass logs a `wake boot sweep` INFO line with the delivered count; the wake is in the MessageQueue; the `pending_wakes` dict is empty post-sweep |
| **T10** | **Phase-2 unit test pack at `tests/unit/services/test_post_restart_arm_notify_sweep.py`** — sync + `pytest-asyncio`; `tmp_path` fixtures; `monkeypatch` for the kill-switch env; `unittest.mock.AsyncMock` for `manager.enqueue_message`, `manager.stamp_user_origin_window`; `MagicMock` for the source-adapter stub; convention precedent is `tests/unit/services/test_maintenance_run_lock_and_capture.py`. All test IDs T2.1, T2.2, T2.3, T2.4, T5.1, T5.2, T5.3, T5.4, T5.5, T5.6, T5.7, T5.8, T5.9, T5.10, T5.11, T5.16 (T13–T17 coverage). Every test docstring carries the AC + ADR-040/041/042/044 reference | T1–T9, T13–T17 | `pytest tests/unit/services/test_post_restart_arm_notify_sweep.py -v` exits 0 with all T2.* + T5.1–T5.11 + T5.16 GREEN |
| **T11** | **Phase-2 job-queue test pack at `tests/job_queue/test_post_restart_arm_notify_routing.py`** — end-to-end with a stub source adapter; convention precedent is `tests/job_queue/test_a2_autopromote_notify.py` (autopromote end-to-end) and `tests/job_queue/test_idempotent_enqueue.py` (idempotency). All test IDs T3.1, T3.2, T3.3, T3.4 | T9, T10 | `pytest tests/job_queue/test_post_restart_arm_notify_routing.py -v` exits 0 with all T3.* GREEN; the response-routing assertion (T3.2) uses a stub `deliver_response` capture, not a live chat adapter |
| **T12** | **Non-regression check: existing `test_upgrade_journal.py` + `test_upgrade_tools.py` + `test_release_journal.sh` + `test_maintenance_run_lock_and_capture.py` packs remain green** — Phase 2 adds a new method on the existing sweep service and a new line in `api.py`'s boot pass; neither change touches the existing reconcile / GC / executor-reaper / `enqueue_message` / `stamp_user_origin_window` surface. The lifespan order is unchanged | T1–T11 | Full unit + job-queue packs exit 0; the existing `reconcile_pending_op` boot note still appears in the boot pass log; live pids verified unchanged on demo |
| **T13** | **Add the wake terminal event-set + wake-owned reader to `upgrade_journal.py` (architecture delta #1, MUST)** — define the SIBLING constant `WAKE_TERMINAL_EVENTS = _TERMINAL_EVENTS + ("restart",)` and the wake-owned reader `wake_terminal_event_after(journal, armed_at)` that wraps the Phase 1 parameterized walker `latest_matching_event(journal, run_id, armed_at, WAKE_TERMINAL_EVENTS)`. The reader is armed_at-scoped, run_id-matched, and restart-kind aware (mirrors `_terminal_event_after` `:986` but is wake-owned). **MUST NOT mutate `_TERMINAL_EVENTS` itself** — the PROMOTE-only reconcile (`reconcile_pending_op` `:1016` returns `None` for restart-kind; comment `:980-981`) depends on the 6-member set. Without the `"restart"` member the wake would NEVER fire for intentional restarts — the dominant case (`restart.sh:262` journals `"restart"`). Phase 3's T4.8 mutation guard pins both directions | Phase 1 T7 | Unit: `WAKE_TERMINAL_EVENTS` contains all 6 `_TERMINAL_EVENTS` members PLUS `"restart"`; `_TERMINAL_EVENTS` is unchanged (6 members); a journal whose history ends with `{"name": "restart", "run_id": "r-x", "ts": ≥ armed_at}` returns `"restart"` from the reader; a pre-`armed_at` restart event returns `None`; a `JournalTorn` returns `None` |
| **T14** | **Add abandon-on-switch-off semantics to the sweep (architecture delta #2, MUST)** — when `sweep_wake_records` observes `ENSEMBLE_POST_RESTART_ARM_NOTIFY=0` AND `pending_wakes` records are present: mark each `abandoned` with `reason=kill_switch_off` (via `mark_wake_abandoned`, which journals ONE `wake_abandoned` history event) — a ONE-TIME pass (an already-abandoned/empty dict produces no repeated history events). The arm-side short-circuit (no NEW records while OFF, Phase 1 T2) is unchanged. Rejected alternatives (recorded in ADR-044): hold (operator footgun, manual purge) and late-deliver (stale-wake flood on re-enable — must be impossible; Phase 3 T5.17 pins it). The `WakeSweepResult` gains an `abandoned_kill_switch` counter (or folds into `abandoned`) so the structured log line distinguishes the pass | T2, T7 (kill-switch gate reorders: abandon-pass THEN return) | Unit (T5.16): env off + 2 pending records → both marked `abandoned` with `reason=kill_switch_off`; exactly ONE `wake_abandoned` history event per record; a second tick with the dict empty journals NOTHING new; no `enqueue_message` call is ever made while OFF |
| **T15** | **Sweep CAS writes use `lock_acquire(wait_s≈30)` + single retry (architecture delta #4, SHOULD)** — the `pending → delivering` (and the delivered/abandoned writes that follow) acquire the journal lock with `wait_s≈30` instead of the `lock_acquire` default `wait_s=0.0` (which fails fast under concurrent shell-writer contention). On a failed acquisition: ONE retry, then skip the wake this tick (it stays `pending` for the next tick). Deadlock with `launcher._journal_sweep` is structurally absent — the launcher runs pre-daemon, so no lock ordering cycle exists | T3, T7 | Unit: with the journal lock pre-held by a mock shell writer, the sweep's CAS blocks up to `wait_s≈30` (mocked clock) and succeeds on release; a still-contended second attempt logs once and leaves the wake `pending` (no `delivering` flapping); the next tick delivers normally |
| **T16** | **Specify `mark_wake_delivered` failure semantics (architecture delta #5, SHOULD)** — if the delivered-mark write fails after a successful `enqueue_message`: retry ONCE; if the retry also fails, LEAVE the record in `delivering` for the next boot/tick. This is deliberate AT-LEAST-ONCE delivery: the next pass re-enqueues (a harmless duplicate wake; the consumer is idempotent — `upgrade_status` reads live `state.json`, so a repeated pointer costs one redundant tool call, not a wrong answer). Do NOT mark `abandoned` and do NOT crash — log WARNING with the `run_id` | T7 | Unit: `mark_wake_delivered` raising (mocked journal failure) once → one retry observed; second failure → record remains `status=delivering` in the dict, sweep logs WARNING, `errors` counter +1, boot not wedged; a subsequent tick re-delivers (duplicate enqueue asserted — the at-least-once contract) |
| **T17** | **Sweep hardening: malformed-record filter + torn/OSError catch + `install_dir=None` no-op (architecture delta #10, SHOULD)** — (a) each `pending_wakes` value goes through a `PendingWake.from_json` field-filter (mirroring `PendingOp.from_json` `upgrade_journal.py:735-742`) so a malformed record is logged + SKIPPED, never crashed on; (b) the sweep's journal access catches `JournalTorn` AND `OSError` (precedent `:1013`/`:1039` — the existing reconcile's catch family), returning `errors += 1` and continuing; (c) `install_dir=None` (dev environments with no install dir) → clean no-op sweep (precedent `UpgradeJournalSweepService.__init__ :99-103`) | T7 | Unit: one malformed + one well-formed record → only the well-formed is processed, the malformed is logged + skipped (no exception escapes); `JournalTorn` and `OSError` both → `errors=1` + clean result; `install_dir=None` → `WakeSweepResult()` all-zeros, zero journal reads attempted |

---

## Coupling

- **Tight with Phase 1 (ADR-039)** — Phase 2 consumes the Phase 1
  helpers: `list_pending_wakes`, `arm_pending_wake`,
  `mark_wake_delivering`, `mark_wake_delivered`,
  `mark_wake_abandoned`, `latest_matching_event` (wrapped by the
  Phase 2 wake-owned reader), `PendingWake`. If
  Phase 1's signatures or semantics change, Phase 2 must follow.
  ⟪SEAM (architecture delta #1): the wake terminal reader is
  grounded on the sibling constant `WAKE_TERMINAL_EVENTS =
  _TERMINAL_EVENTS + ("restart",)` — NEVER on a mutated
  `_TERMINAL_EVENTS` (PROMOTE-only reconcile semantics at
  `upgrade_journal.py:1016` depend on the 6-member set); Phase 3's
  T4.8 mutation guard pins both directions.⟫
- **Tight with `manager.enqueue_message` (manager.py:7935)** — the
  wake is the same primitive every other wake uses (WC watchdog,
  nudges, [JOB_EVENT] delivery, compaction, system messages). The
  metadata shape is the same channel; the response routing is the
  same path. **No new messaging subsystem** (invariant 2, ADR-040).
- **Tight with `manager.stamp_user_origin_window` (manager.py:4230-4245)**
  — the re-stamp is the same in-memory API the arm path uses. The
  recorded source has already passed the F2 surface on the
  verified-arm path; the re-stamp is safe by inheritance
  (D-FA3.3, ADR-041, R-3).
- **Tight with the api.py boot pass (api.py:1477-1523)** — the
  sweep's wake branch is a new sub-routine of the existing boot
  pass; the lifespan order is unchanged. A change to the boot pass
  shape (e.g. the reconcile moving to a separate service) must
  coordinate the wake branch too.
- **Loose with `enqueue_message` priority / `MessageQueue.message_metadata`**
  — the wake uses `priority=2` (above user `1`); the metadata
  shape is a documented small JSON dict. No new column, no new
  index (R-16).
- **Independent of** `restart.sh` / `promote.sh` / `lib.sh` — the
  executor is unchanged; the wake is a post-pipeline concern.

---

## Per-Phase Verification (test-strategy.md mapping)

| Test ID | Description | Where | Verifies |
|---|---|---|---|
| **T2.1** | Boot pass enqueues a wake — `AsyncMock` manager, one pending wake with terminal event, one `enqueue_message` call with the documented args | `tests/unit/services/test_post_restart_arm_notify_sweep.py` | AC2 (boot delivery); invariant 9 (no LLM in critical path); R-10 (boot order) |
| **T2.2** | Empty-case fast path — no pending wakes, no `enqueue_message` call, clean `WakeSweepResult()` | same | Boot pass is cheap on a clean journal |
| **T2.3** | Pending wake held — terminal-state predicate returns `None`, wake remains `pending`, no delivery | same | AC4 (no wake while pipeline pending); ADR-042 |
| **T2.4** | Mixed batch — terminal wakes delivered; pending wakes remain | same | AC4 in a multi-wake scenario |
| **T5.1** | Missing instance → ari fall-back — `enqueue_message` raises `InstanceNotFound`, sweep finds ari in project, delivers to ari with annotation | same | AC5 + D-FA5.1 + ADR-043 |
| **T5.2** | Missing instance, no ari → journals `arm_notify_no_instance` history event; record marked abandoned | same | AC5 + D-FA5.1 + ADR-043 |
| **T5.3** | Coalesce by instance — 3 wakes for same instance → 1 coalesced wake, run-list newest-first | same | AC5 + D-FA5.2 + ADR-043 |
| **T5.4** | Separate instances — 3 wakes for 3 instances → 3 separate wakes, no cross-instance coalesce | same | Coalesce is by instance, not global |
| **T5.5** | Coalesce cap drops overflow — 17 wakes for same instance → 1 coalesced of 16 + 1 dropped; `wake_coalesce_overflow` history event | same | D-FA5.2 cap + R-13 mitigation |
| **T5.6** | Coalesce body format — body byte-exact vs documented format | same | D-FA5.2 body format |
| **T5.7** | Idempotency — stale `status=delivered` record (defensive) is skipped | same | AC5 one-shot; invariant 7; R-9 mitigation |
| **T5.8** | CAS loser skip — two ticks race on the same wake, second tick's `mark_wake_delivering` returns `None`, second tick skips | same | D-FA1.2 CAS + R-11 |
| **T5.9** | `JournalTorn` skip — `journal_read` raises, sweep returns `errors=1` and continues | same | D-FA5.4 + R-20 mitigation |
| **T5.10** | `enqueue_message` failure continue — exception caught per-wake, sweep increments `errors` and continues | same | D-FA5.4 boot-never-wedge; R-20 |
| **T5.11** | Kill-switch on sweep — `ENSEMBLE_POST_RESTART_ARM_NOTIFY=0` → `_is_enabled()` returns `False`; the delivery branch is a no-op | same | D-FA6.2 + ADR-044; invariant 11 |
| **T5.16** | Abandon-on-switch-off (architecture delta #2) — env OFF + records present → each marked `abandoned` with `reason=kill_switch_off` + exactly one `wake_abandoned` history event each (one-time pass); NO `enqueue_message` call while OFF | same | MUST delta #2 + ADR-044 persisted-record semantics; R-21 mitigation (stale-flood prevention) |
| **T3.1** | Wake's `source` = recorded source — arm with `source=discord:user123`, recorded `pending_wakes.source` matches, `enqueue_message` called with same | `tests/job_queue/test_post_restart_arm_notify_routing.py` | AC3 routing; invariant 10; ADR-041 |
| **T3.2** | Full routing — arming turn via stub `discord:user123` adapter, daemon restart, wake delivered, agent's response delivered to `discord:user123` via the stub | same | AC3 full path |
| **T3.3** | User-origin window set after wake delivery (REWORDED per architecture delta #7) — after the wake is delivered, `manager._user_origin_windows[arming_instance_id]` has the entry with the recorded source; a follow-up `upgrade_status` call within the same wake turn passes the 3-factor gate's factor-2. The assertion is "window is SET after delivery" — NEVER "exactly one stamp call" (double-stamping — the defensive pre-enqueue stamp + the natural `manager.py:8112` stamp — is structural) | same | AC3 + ADR-041 (+ addendum); R-3 mitigation |
| **T3.4** | Empty source → `"api"` sentinel — wake with `source=""`, `enqueue_message` called with `source="api"` | same | D-FA3.2 fall-back; D-FA5.1 missing-routing |

**Pre-Phase-2 baseline:** the Phase 1 pack at
`tests/unit/tools/test_post_restart_arm_notify_journal.py` (T1.* +
T4.1–T4.6 + T5.12) remains green (T12).
**Post-Phase-2 invariant:** every new test docstring carries the
AC + ADR-040/041/042/044 reference (test-strategy.md §4.5).

---

## Risks (phase-specific — full register: sibling `risk-register.md`)

| # | Risk | Impact | Mitigation |
|---|------|--------|------------|
| R2.1 | The boot pass fires `sweep_wake_records` BEFORE the worker pool is up — wake is held in PENDING and misses the boot window | Low | Worker pool is up at api.py:439; sweep service starts at api.py:1477-1523 — boot order is verified (R-10); T2.1 asserts the wake is enqueued, not held |
| R2.2 | A re-stamp of the user-origin window enables a forged live arm | Medium (low likelihood) | Re-stamp source is the recorded arm-time source, which already passed the F2 surface on the verified-arm path (D-FA3.3, ADR-041, R-3 four-layer mitigation); T3.3 asserts the re-stamp shape |
| R2.3 | The sweep's `enqueue_message` call hangs on a stuck instance revive (terminal→RUNNING) | Low | `enqueue_message` has its own bounded wait; the per-wake `try/except Exception` (T6) catches any hang-class exception and continues; R-15 (paused instance) is the existing claim-side gate, unchanged |
| R2.4 | Coalesce overflow at 17 wakes creates a body too large for `MessageQueue.message` | Low | The cap is 16; the body is a self-describing pointer (~80 chars per entry → ~1280 chars for 16); well under the 64KB cap (R-13) |
| R2.5 | The kill-switch flips during a sweep — half the wakes are delivered, half are not | Low | The kill-switch is read per tick; an in-flight sweep completes its current wake then returns; the next tick short-circuits. No mid-sleep state to worry about. The flip is a quiet operator action, not a race |
| R2.6 | The sweep's `_coalesce_wakes` marks only `wakes[0]` as `delivering` / `delivered` but the others in the group are removed by structural sweep — the order matters | Medium | The structural sweep in T7 step (h) marks ALL wakes in the group as delivered in a single dict-removal pass; the test T5.3 asserts the dict is empty post-coalesced-delivery |

---

## Rollback / Abandonment Notes

**If Phase 2 ships and Phase 3 does not:** the wake is delivered
on terminal events with the routing preserved, but the front-door
ari fall-back, the long-downtime double-arm coalesce, and the
banner updates are not in place. The recovery for a missing
instance is a `pipeline-busy` refusal; the user can re-query
interactively. **No live impact.**

**Abandonment (kill-switch):** the same env-var flip as Phase 1
(`ENSEMBLE_POST_RESTART_ARM_NOTIFY=0`) disables the sweep's wake
branch AND the arm-side write. Operators opt out with one
env-var flip; no code change required.

**Code rollback (if a Phase 2 commit lands and is found broken):**
the change is local to `daemon/services/upgrade_journal_sweep.py`
(new `WakeSweepResult` + new method) + `daemon/api.py` (one new
wrapped call in the boot pass). A `git revert` of the commit is
the rollback; the sweep service returns to the pre-Phase-2
behavior (no wake branch, no impact on the existing reconcile /
GC / executor-reaper). The Phase 1 arm-side writes still produce
the durable record but it is never consumed — the records
accumulate up to `abandon_after` and are structurally removed
at that point (the wake sweep's abandonment path is also
disabled, so the records are bounded by the journal-validity
window).

**What does NOT work as a rollback:** deleting the
`sweep_wake_records` method while leaving the boot-pass call in
`api.py` — the boot pass would log a `WakeSweepResult` for a
non-existent method and the call site would `AttributeError`.
The correct rollback is the code revert, not a partial
in-place edit.

---

## Exit Criterion

**All of the following, objectively verifiable:**

1. **Boot delivery:** T2.1 + T2.2 GREEN; the boot pass enqueues
   a wake on a `pending` record with a terminal event; the
   empty-case fast path is clean.
2. **Terminal-state gating:** T2.3 + T2.4 GREEN; pending wakes
   are held; mixed batches deliver only the terminal ones.
3. **Routing:** T3.1, T3.2, T3.3, T3.4 GREEN; the wake's source
   matches the recorded source; the response routes back to the
   original chat; the user-origin window is re-stamped; the
   empty-source fall-back uses the `"api"` sentinel.
4. **Edge cases (Phase 2 subset):** T5.1, T5.2, T5.3, T5.4, T5.5,
   T5.6 GREEN; missing-instance ari fall-back + notice;
   coalesce by instance with cap + overflow.
5. **Idempotency + never-wedge:** T5.7, T5.8, T5.9, T5.10 GREEN;
   delivered records are skipped; CAS loser is skipped;
   `JournalTorn` is caught; `enqueue_message` failure is caught
   per-wake.
6. **Kill-switch on the sweep side:** T5.11 GREEN (delivery
   branch no-op) + T5.16 GREEN (abandon-on-switch-off: records
   present while OFF → `abandoned` with `reason=kill_switch_off`
   + one history event each, one-time pass) when
   `ENSEMBLE_POST_RESTART_ARM_NOTIFY=0`.
7. **Demo end-to-end:** T9 acceptance; after a `system_restart`
   arm on demo + an induced daemon restart + restart via
   `launcher.sh`, the boot pass logs the wake delivery, the
   `MessageQueue` has the wake row, the `pending_wakes` dict is
   empty post-sweep.
8. **Non-regression:** T12 GREEN; the existing
   `tests/unit/tools/test_upgrade_journal.py` (53-line pack) +
   `tests/unit/tools/test_upgrade_tools.py` (135-test pack) +
   `tests/unit/services/test_maintenance_run_lock_and_capture.py`
   + `tests/test_release_journal.sh` all pass byte-exact.
9. **Live untouched:** no live pid verified-touched (sandbox-only
   work + demo pids verified at the demo acceptance step).

The Phase 2 commit is mergeable when 1–9 are green.
Phase 3 starts on the same `feature/post-restart-arm-notify`
branch; the commit that closes Phase 3 is the one that
adds the long-downtime double-arm coalesce test (T5.13) and
the rest of the edge-case coverage.
