# Architecture Recommendation — Post-Restart Arm-Notify

> **⚠ SUPERSESSION NOTICE:** this design **supersedes D-FA1.2** of
> `.agents/shared/planning/self-restart-upgrade-phase2/architecture-recommendation.md`
> (the ratified pull-model-only ruling). See `supersession-record.md`
> for the explicit ruling, the unchanged-affected ADRs, and the
> rationale. All other D-FA1.* decisions (D-FA1.1 pending-op record,
> D-FA1.3 daemonized executor, D-FA1.4 post-turn trigger, D-FA1.5
> in-flight semantics) remain **in force** — the wake is an
> additive concern layered on top of the same durable surface.

- **Date:** 2026-10-03 · **Author:** architect (controller) — feature analysis
- **Input:** verified research findings (3 explorers, this branch — see §0)
- **Status:** **DECIDED** — every focus area ends in one implementable recommendation. Where I deviate from a research finding, it is marked **⟲ OVERRIDE** with rationale.
- **Scope:** planning only. No code was written or run. Read-only repo inspection.

> **⛔ HARD CONSTRAINT (inherited verbatim from Phase 2, governs every decision below):**
> NEVER touch the live/production ensemble environment — it is the
> running environment of Ari and all live agents (~/agents-ensemble,
> port 9797, prod DB, ENSEMBLE_DEPLOY_LIVE are out of bounds; live
> pids must remain untouched). ALL work/testing/drills in dev and
> demo only. If any plan step would require touching live, mark it
> as USER-GATED and design it as an explicit user-confirmed action.
> Sandbox instances (own port + throwaway PG) are fine.

**Constraint compliance:** every LIVE execution path designed here is
**refusal-tested only** — never exercised by this feature. Live
`system_restart` is outright-refused before any journal write
(`upgrade_tools.py:2051-2058`); live `system_upgrade` writes a wake
record only on a verified-arm path (the same F2 surface the
3-factor gate already verifies). The wake record therefore cannot
originate from a live-only path; live-wake remains USER-GATED
(hard constraint of Phase 2).

---

## 0. Executive Summary

| FA | Decision (one line) |
|----|---------------------|
| FA1 | Wake record = new `pending_wakes: dict[str, PendingWake]` keyed by `run_id`, atomic with the existing `pending_op` write; lives on the same `releases/state.json` surface (no second file — D-FA1.1 ruling). **NOT** cleared by `clear_pending_op` or by `restart.sh`'s post-completion clearing. |
| FA2 | Arm-side write rides the existing arm journal-write call (one atomic `journal_write`, so the wake record is structurally inseparable from the arm — a crash between the two writes is impossible by construction). The new fields are added to the `PendingWake` dataclass (with `from_json` field-filter discipline — `upgrade_journal.py:738` — keeping it forward-compatible). |
| FA3 | Boot delivery rides the existing `UpgradeJournalSweepService` (api.py:1477-1523). The wake branch is a NEW sub-routine of the existing boot pass + the periodic 90s tick. Wake primitive = `manager.enqueue_message(..., source=<recorded>, metadata={<SYSTEM CONTEXT>})`. The recorded source is re-stamped into the user-origin window for the wake turn (so a follow-up `upgrade_status` call within the same wake turn passes the 3-factor gate). |
| FA4 | Terminal-state gating = wake delivered only when the journal has a terminal-class history event for this `run_id` (`_TERMINAL_EVENTS` at `upgrade_journal.py:983`). Pending-wake records for still-running pipelines are **deferred** to the next sweep tick. After `expires_at + grace` (default 600s) without a terminal event, the record is marked `abandoned` and a `front-door-ari-notice` is the recovery — see FA5. |
| FA5 | Edge cases: missing instance → fall-back to a "front-door" ari instance in the same project (or surfaced `arm-notify-missing-instance` notice in the wake branch's structured log); multiple records for the same instance → coalesce into ONE wake with a run-list payload; idempotent one-shot delivery via `status` lifecycle (pending→delivering→delivered, terminal→cleared); boot never wedges (best-effort try/except in the sweep, log + continue, no abort); live-refused arms → structurally no record. |
| FA6 | No new messaging subsystem, no new HTTP endpoint, no new SQLModel table. The wake rides `enqueue_message` (manager.py:7935), the periodic sweep (api.py:1477), the durable journal (`releases/state.json`). The kill-switch is `ENSEMBLE_POST_RESTART_ARM_NOTIFY=0` (default ON) — the sweep's wake branch is gated on this env, so the entire feature can be disabled without code changes. |

**New ADRs to mint in `decisions.md`:** **ADR-039** wake record
schema + write atomicity · **ADR-040** boot sweep + wake delivery
primitive · **ADR-041** routing preservation (re-stamp the
user-origin window) · **ADR-042** terminal-state gating &
abandonment policy · **ADR-043** edge cases & coalescing ·
**ADR-044** kill-switch & boot-never-wedge.

---

## FA1 — The Wake Record (D-FA1.x — durable schema)

> **Naming note (to avoid confusion with the parent doc's D-FA1.x):**
> the parent's `.agents/shared/planning/self-restart-upgrade-phase2/architecture-recommendation.md`
> uses D-FA1.1 / D-FA1.2 / D-FA1.3 / D-FA1.4 / D-FA1.5 to label its
> FA1 focus area (arm-and-outcome for the self-restart-upgrade
> initiative). **This document** uses D-FA1.x for its own FA1 focus
> area (the wake record). The two are unrelated; cross-references
> always carry the file path. The parent's D-FA1.2 is the pull-model
> ruling SUPERSEDED by this design (see `supersession-record.md`).

### D-FA1.1 — Schema: `pending_wakes: dict[str, PendingWake]` on the existing journal ✅ DECIDED

**Where it lives:** `releases/state.json` (the same single-writer
atomic surface as `pending_op` / `pending_restart` / `pending_actions`
/ `history`). `ensure_extensions` (`upgrade_journal.py:332-352`)
gains one new key:

```jsonc
// releases/state.json — NEW key, additive extension
"pending_wakes": {              // {} when idle; keyed by run_id (the cross-death join key)
  "r-<utcstamp>-<4hex>": {      // one record per armed pipeline
    "run_id": "r-<utcstamp>-<4hex>",
    "kind": "restart" | "promote",  // mirrors the armed PendingOp.kind
    "env": "demo" | "dev" | "sandbox",  // mirrors PendingOp.env (live never appears)
    "arming_instance_id": "<uuid>",   // from PendingOp.armed_by_instance — the wake target
    "arming_agent_id": "ari" | "jober" | ...,   // from instance repo lookup at arm time (graceful miss = null)
    "source": "discord:<external_user_id>" | "api" | ...,   // recorded user-origin source (the wake's source)
    "message_id": "<uuid>",            // the message_id of the arm-time user turn (audit/forensics)
    "message_metadata": {...},         // verbatim snapshot of MessageQueue.message_metadata for the arming turn (audit)
    "target_version": "1.2.3" | null,  // promote only; restart = null
    "mode": "graceful-now" | null,     // restart only; promote = null
    "armed_at": "<iso>",               // mirrors PendingOp.armed_at
    "expires_at": "<iso>",             // arming expiry (same as PendingOp.expires_at, 1800s restart / 600s promote)
    "abandon_after": "<iso>",          // expires_at + 600s (FA4 grace)
    "status": "pending" | "delivering" | "delivered" | "abandoned",
    "delivered_at": "<iso>" | null,
    "delivered_message_id": "<uuid>" | null
  }
}
```

**Why on the journal, not a new table:**

* **Single-writer atomicity.** A second file (or a DB row in
  addition to the journal) introduces a second atomicity surface —
  D-FA1.1 (self-restart-upgrade-phase2 plan) UNANIMOUSLY rejected
  that direction. We extend, we do not parallel.
* **Read coupling.** The wake is read alongside the terminal-state
  check (FA4). Both reads are on the same file; the cost is one
  `journal_read` per sweep, not a join.
* **Write coupling.** The wake is written in the SAME
  `journal_write` call as the arm. See D-FA1.2 (below).
* **Migration / schema-evolution discipline.** The
  `PendingWake.from_json` constructor uses the same
  `kwargs = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}`
  discipline as `PendingOp.from_json` (`upgrade_journal.py:738`).
  Adding a field is forward-compatible; an old binary reading a
  new journal drops the unknown field silently — but the old
  binary never had a wake path, so this is moot (a new-binary-only
  read).

**Why keyed by `run_id` (not by `arming_instance_id`):**

* `run_id` is the cross-death join key already used by
  `pending_op` / `pending_restart` / `pending_actions`. Reusing
  it makes the wake record a sibling of the arm record — the
  same atomic surface, the same key space.
* The same `run_id` may correspond to ONE arming instance (the
  common case) OR — in the rare co-arm case — multiple arming
  instances if two Aries race-armed the same run (the journal
  lock prevents that, but defensively keyed-by-run is correct).
  Coalescing happens at FA5.2, not at the schema.

**Why `arming_agent_id` is a SEPARATE field from `arming_instance_id`:**

* The instance id is durable (revive-safe) but the agent id is
  looked up at arm time from the instance repo. The wake's
  *target* is the instance id; the agent id is **informational**
  for the agent's self-introspection (the agent's first turn
  after the wake can verify "yes, I am ari" against its own
  identity). A miss (instance deleted between arm and wake) is
  tolerated — `null` is a valid value (FA5.1).
* This separation is consistent with the existing
  `manager._user_origin_windows[instance_id]` shape
  (manager.py:4239-4245) — keyed by instance, not by agent.

**`source` format & value-domain:**

* The recorded `source` MUST be one of the
  `classify_user_origin`-arms-the-gate set (verified-arm path
  only): exact `"api"`, or any `"<segment>:<id>"` whose
  `SourceType.value` is in
  `USER_ORIGIN_CHAT_SOURCE_TYPES = {telegram, slack, discord, whatsapp}`
  (`upgrade_journal.py:1944-1946`). The recorded source has
  **already** passed the live-gate on the verified arm path
  (the 3-factor gate at `upgrade_tools.py:2494-2660` requires
  the same window); the wake surface is therefore **safe by
  inheritance**.
* The `non-live arming` path (`system_restart` non-live OR
  `system_upgrade` non-live dry-run) does NOT require a
  user-origin window. The recorded `source` is whatever was
  the arm-time user-origin (may be the empty-string sentinel
  `""` if the user did not stamp, or a non-user-origin like
  `scheduler:`). When the wake is delivered, an empty `source`
  is treated as **missing routing** (FA5.1 fall-back).

**`message_metadata` is a verbatim snapshot, not a free-form dict:**

* The existing `MessageQueue.message_metadata` is a JSONB column
  (`instance_messaging.py:1793-1795`). The wake record mirrors
  the arming turn's metadata for forensics — so a tester can
  reproduce the arming turn's exact context from the journal
  alone (no DB read required).
* This is **informational** — the wake does not re-stamp the
  message_metadata for the wake turn; the wake's own metadata
  is constructed in FA3.2.

### D-FA1.2 — Lifecycle (the state machine) ✅ DECIDED

```
                ┌───────────┐
arm-time write  │  pending  │  status=pending, run_id, source, etc.
─────────────────────────────►│
                └─────┬─────┘
                      │
                      │ boot sweep: terminal-event observed (FA4)
                      │    or periodic tick: same
                      ▼
                ┌────────────┐
                │ delivering │  status=delivering, delivered_at=now
                └─────┬──────┘
                      │
        ┌─────────────┴─────────────┐
        │                           │
        ▼                           ▼
  ┌───────────┐              ┌─────────────┐
  │ delivered │              │  abandoned  │  status=abandoned, no delivery
  │ (terminal │              │  (grace     │  reason=expired/no-terminal
  │  cleared) │              │   expired)  │
  └───────────┘              └─────────────┘
```

**The status transitions are exactly the four shown above. There is NO `failed` state — a delivery failure logs and continues (boot-never-wedge, FA5.4); the wake record remains `pending` and is retried on the next tick.**

**Transitions in code (D-FA1.2 helpers, all in `upgrade_journal.py`):**

* `arm_pending_wake(install_dir, wake: PendingWake)` —
  atomic with the existing `write_pending_op` call (same
  `journal_write` envelope). See D-FA2.1.
* `mark_wake_delivering(install_dir, run_id) -> PendingWake | None`
  — CAS-style: only the holder of the journal's pipeline lock
  can transition `pending → delivering`. **The sweep acquires
  the same per-env pipeline lock** before the transition, so
  concurrent sweep + arm paths are serialized.
* `mark_wake_delivered(install_dir, run_id, message_id) -> None`
  — unconditional write. Status → `delivered`; record is
  REMOVED from `pending_wakes` on the next journal write
  (so the dict shrinks).
* `mark_wake_abandoned(install_dir, run_id, reason: str) -> None`
  — terminal-cleanup transition. Status → `abandoned`; record
  is REMOVED. The reason is journaled to `history` for
  forensics.

**Why CAS on `pending → delivering`:** the sweep is
periodic (90s) and a single journal may have many records. A
non-CAS transition would race two sweep ticks on the same
record (the periodic tick can run in parallel with the boot
pass's deferred batch). The journal lock is the existing
serialization primitive — we re-use it. **The CAS failure
(lock not acquired) logs and continues** (boot-never-wedge).

**The status field is the idempotency key (AC5):** an unrelated
later restart observes a record with status != `pending` (e.g.
`delivered` is observed AFTER the delivery; an `abandoned`
record is removed) — neither is re-delivered. The "an
unrelated later restart must not re-deliver" guarantee is
met by the status field, not by a separate "delivered" flag.

### D-FA1.3 — Why NOT a separate `wake` file (the explicit rejection) ✅ DECIDED

The alternative — a parallel `wake_records.json` — was
considered and **rejected** for the same reason D-FA1.1
rejected a parallel `pending_actions.json`: two files = two
atomicity surfaces. A crash between the two writes leaves
the system in an unknown state. The journal is single-writer
atomic (`upgrade_journal.py:281-294`); the wake rides the
same `journal_write` call. No second file.

A second consideration: the wake record is read in the SAME
sweep pass that reads `pending_op` for the terminal-state
check. One `journal_read` is cheaper than two file reads +
a coordination layer.

---

## FA2 — Arm-Side Write (D-FA2.x — integration with the existing arm)

### D-FA2.1 — One journal write covers both arm and wake ✅ DECIDED

The arm path in `upgrade_tools.py` (system_restart
:2156-2263, system_upgrade :2707-2872) currently does:

```python
# 1. acquire lock
acquired, busy_run = journal_lock_acquire(install_dir, run_id)
# 2. write in_flight
uj.journal_update_field(install_dir, "in_flight", {...})
# 3. write pending_op
uj.write_pending_op(install_dir, op)
# 4. set the in-memory execution marker
_set_execution_marker(manager, current_instance_id, {...})
# 5. release lock (implicitly, at turn-end)
```

The new arm path adds ONE call between (3) and (4):

```python
# 3. write pending_op
uj.write_pending_op(install_dir, op)
# 3.5. (NEW) write the wake record — SAME journal_write atomic envelope
uj.arm_pending_wake(
    install_dir,
    wake=PendingWake(
        run_id=run_id,
        kind=op.kind,
        env=op.env,
        arming_instance_id=op.armed_by_instance,
        arming_agent_id=_resolve_agent_id(manager, op.armed_by_instance),
        source=_capture_user_source(manager, op.armed_by_instance),
        message_id=_capture_user_message_id(manager, op.armed_by_instance),
        message_metadata=_capture_user_metadata(manager, op.armed_by_instance),
        target_version=op.target,
        mode=op.mode,
        armed_at=op.armed_at,
        expires_at=op.expires_at,
        abandon_after=uj.iso_plus(op.expires_at, PENDING_WAKE_GRACE_S),
    ),
)
# 4. set the in-memory execution marker (unchanged)
```

**The atomic guarantee:** `arm_pending_wake` is a NEW wrapper
in `upgrade_journal.py` that calls `ensure_extensions` (which
loads the current journal) and writes the wake record into
the `data["pending_wakes"]` dict, then calls the SAME
`journal_write` helper that `write_pending_op` already uses
(tmp+fsync+os.replace). A crash between the wake write and
the pending_op write is structurally impossible — they are
in the same `journal_write` call.

**Rollback of the wake on arm-failure:** the existing
arm-side `except (JournalTorn, OSError, KeyError) as exc:`
block in both tools (system_restart :2209-2236, system_upgrade
:2850-2852) already unwinds `in_flight` and releases the
lock. The wake record is also written at the same journal
write — so a JournalTorn that triggers the unwind means the
arm FAILED and the wake was NEVER written. There is no
asymmetry to handle. The `pending_op` is rolled back to `None`
by the unwind; the wake record, having never been written, is
absent. The system is consistent.

**Rollback of the wake on arm-success-then-executor-fail:** the
executor (restart.sh / promote.sh) clears `pending_op` +
`pending_restart` + `in_flight` on completion (restart.sh:250-262)
and journals a terminal event. **The wake is NOT cleared by
executor completion** — the wake survives the executor's
clearing. The boot sweep then sees the wake, observes the
terminal event, and delivers. The wake is cleared by the
sweep (mark_wake_delivered) AFTER successful enqueue_message.
This is the load-bearing reason the wake record MUST live on
a separate journal key (FA1.1).

### D-FA2.2 — Capturing the source at arm time ✅ DECIDED

The wake's `source` field is the arm-time user-origin source
of the arming turn. The capture path:

```python
def _capture_user_source(manager, instance_id: str) -> str:
    """Snapshot the arming turn's user-origin source into the wake record.
    The source is wiped at boot (in-memory _user_origin_windows),
    so the capture is a PERSISTENT copy — the wake needs the value
    post-boot.
    Returns "" if no user-origin window is set (non-chat arm — see FA5.1).
    """
    windows = getattr(manager, "_user_origin_windows", None)
    if isinstance(windows, dict):
        w = windows.get(instance_id)
        if isinstance(w, dict):
            return str(w.get("source") or "")
    return ""
```

**Mirror helpers for `message_id` and `message_metadata`:**
the same pattern, reading from
`manager._user_origin_last_stamp[instance_id]` (manager.py:4230-4238)
and from the arming turn's `MessageQueue` row (the
`message_id` of the most recent user message for the instance
— the arm is a tool call on the LATEST user message; the
"arming turn" is that message).

**Why a `""` sentinel for missing source (not `None`):**
JSON serialization — `None` is `null` in JSON, and the
`from_json` filter (`upgrade_journal.py:738`) preserves it
as a field, but the wake's enqueue_message path is simpler
when `""` is the "no source" sentinel. `None` would force
every consumer to check for both.

**`_resolve_agent_id` (helper):**

```python
def _resolve_agent_id(manager, instance_id: str) -> str | None:
    """Read the agent id from the instance repo. Tolerate miss — the
    wake record keeps the value informational; a miss is logged
    but does NOT block the arm.
    """
    try:
        repo = manager.instance_repo  # or equivalent
        inst = repo.get(instance_id)  # the precise accessor depends on the repo's contract
        if inst is not None:
            return getattr(inst, "agent_id", None)
    except Exception:
        pass
    return None
```

A miss (the instance was deleted in the same atomic envelope
as the arm — extremely unlikely, but not impossible) is
tolerated; `arming_agent_id` is `null` in the record. The
wake's *target* is `arming_instance_id` (always present).

### D-FA2.3 — Idempotency on retry ✅ DECIDED

The arm path can be retried by the agent (e.g. a network blip
in the agent's tool call). The arm is itself idempotent on
`run_id` (each retry mints a new `run_id`); the wake is
written per-`run_id`. A retry produces a second wake record
with a different `run_id`. The boot sweep coalesces (FA5.2).

**Why not collapse two wakes into one at arm time:** a
retried arm has a different `run_id` and therefore a
different `pending_op` — the prior arm's `pending_op` was
cleared (refused or rolled back). The user sees the new
run_id; the new run_id gets its own wake. The sweep
coalesces by arming_instance_id at delivery time, not at
arm time (the run_id is the wake's *identity*).

---

## FA3 — Boot Sweep + Wake Delivery (D-FA3.x)

### D-FA3.1 — Reuse `UpgradeJournalSweepService` for the boot pass ✅ DECIDED

The existing sweep is at
`daemon/services/upgrade_journal_sweep.py`, wired in
`daemon/api.py:1477-1523` with a guaranteed pre-yield boot
pass + a 90s periodic tick. The wake branch is a NEW
sub-routine of the existing boot pass:

```python
class UpgradeJournalSweepService:
    # ... existing methods (reconcile, gc_pending_actions, executor reaper) ...

    async def sweep_wake_records(self) -> WakeSweepResult:
        """Boot + periodic wake delivery. Best-effort, never raises.
        See architecture-recommendation.md §FA3.
        """
        ...
```

The boot pass in `api.py:1500` already calls
`_boot_uj.reconcile_pending_op(upgrade_install_dir)` and logs
the result. The new boot pass adds ONE call immediately
afterward:

```python
# Existing
boot_note = _boot_uj.reconcile_pending_op(upgrade_install_dir)
if boot_note:
    logger.info(...)
# NEW — wake delivery boot pass (best-effort)
try:
    wake_result = await upgrade_journal_sweep.sweep_wake_records()
    logger.info("UpgradeJournalSweepService wake boot sweep: %s", wake_result)
except Exception as boot_exc:  # never aborts boot
    logger.warning(
        "UpgradeJournalSweepService wake boot sweep failed: %s",
        boot_exc,
    )
```

**The 90s periodic tick also carries the wake sweep.** The
periodic tick is already the recovery path for sweeps that
fail mid-cycle; the wake reuses it. The cadence is the same
90s — wakes are not latency-critical (the arming instance's
report can wait 90s for the next tick), but they ARE
recovery-critical (a missed boot pass should retry).

### D-FA3.2 — Wake delivery: `enqueue_message` with re-stamped source ✅ DECIDED

The wake delivery primitive:

```python
async def _deliver_wake(
    self,
    install_dir: Path,
    wake: PendingWake,
    terminal_outcome: str,  # from FA4 — the journal terminal event name
) -> str | None:
    """Deliver one wake to the recorded arming instance. Returns the
    delivered MessageQueue.message_id, or None on failure (logged, never
    raised).
    """
    manager = self._manager  # the InstanceManager singleton
    # ── 1. Re-stamp the user-origin window so the agent's response
    #    can satisfy the 3-factor gate on any follow-up upgrade_status call
    #    (factor 2 = user-origin window, FA3.3).
    if wake.source:
        manager.stamp_user_origin_window(
            wake.arming_instance_id,
            source=wake.source,
            message_id=wake.message_id,
        )
    # ── 2. Build the wake message body. Self-describing pointer — the
    #    LLM does the work (AC3 routing preserved by the source= param).
    body = _format_wake_body(wake, terminal_outcome)
    # ── 3. enqueue_message. Source = recorded source so the agent's
    #    response routes to the original chat (FA3.3 / AC3).
    try:
        result = await manager.enqueue_message(
            instance_id=wake.arming_instance_id,
            message=body,
            source=wake.source or "api",  # empty source -> "api" sentinel
            priority=2,  # above user (1) — wake is a system message
            metadata={
                # The [SYSTEM CONTEXT] channel via metadata; mirrors the
                # WC watchdog pattern (manager.py:7984).
                "system_context": {
                    "kind": "post_restart_arm_notify",
                    "run_id": wake.run_id,
                    "arm_kind": wake.kind,
                    "terminal_outcome": terminal_outcome,
                    "target_version": wake.target_version,
                    "armed_at": wake.armed_at,
                    "wake_at": now_iso(),
                },
                # Disable the post-graph cascade's "is this a wake?"
                # heuristic — wake is a normal enqueue, not a WC nudge.
                "delivery": {"channel": "post_restart_arm_notify"},
            },
        )
        return result.message_id
    except Exception as exc:  # never raises — best-effort
        logger.warning(
            "post_restart_arm_notify: enqueue_message failed for "
            "run_id=%s instance=%s: %s",
            wake.run_id, wake.arming_instance_id[:8], exc,
        )
        return None
```

**Message body format (`_format_wake_body`):**

```
Post-restart arm-notify. Your armed <kind> <run_id> completed during
the daemon downtime.

Outcome: <terminal_outcome>
Target: <target_version or "n/a">
Armed at: <armed_at>
Wake at: <now>

This is an auto-wake — the daemon restarted, the pipeline is terminal,
and you (the arming instance) are being notified so you can call
upgrade_status(run_id="<run_id>") and report back to the user. The
report will route to the same channel where the arm was confirmed.
```

The body is a **self-describing pointer** — the LLM in the
agent's first turn reads the run_id, calls
`upgrade_status(run_id)`, and relays. The body is **not** the
prose of the outcome itself (no LLM in the critical path;
the terminal_outcome field is the journal's authoritative
terminal event name, e.g. `committed` / `rolled_back` /
`halted-for-human` / `restarted` / `quarantine`).

### D-FA3.3 — Source routing (AC3) ✅ DECIDED

The wake primitive sets `source=wake.source` on the
`enqueue_message` call. The agent's first turn after the
wake is triggered by this message; the response's `source`
is set to the **same** `wake.source` by the existing
report-delivery path (`instance_messaging.py` response
construction).

**Why this is the load-bearing routing:** the report
delivered to the user is a `MessageQueue` row whose
`source` is the source of the TRIGGERING message. The
triggering message is the wake. The wake's `source` is
the recorded arm-time source. Therefore the report
delivered to the user has the original arm-time source.
This is the same path every other wake in the system
uses (e.g. WC watchdog — manager.py:7984,
`source="system:watchdog"`).

**The 3-factor gate re-stamp (FA3.2 step 1):** the
`manager.stamp_user_origin_window` call re-installs the
user-origin window for the arming instance so that a
follow-up `upgrade_status` call within the same wake
turn can satisfy the 3-factor gate's factor-2
(user-origin marker on this turn). Without the re-stamp,
the 3-factor gate would refuse the upgrade_status call
with `user-confirmation-missing` (since the user is not
in the loop after the wake). The re-stamp closes this
hole.

**Re-stamp is safe by construction:** the recorded
`source` has ALREADY passed the live-gate on the
verified-arm path (FA1.1). The 3-factor gate's
factor-2 accepts the re-stamp because the source is in
the user-origin classification set. For the non-live
arm path (no user-origin gate), the re-stamp is
informational; the 3-factor gate does not run for
non-live arms.

### D-FA3.4 — Periodic tick (the long-downtime case) ✅ DECIDED

The 90s periodic tick carries the same wake sweep. This
covers the case where the daemon was down for an entire
arm + completion cycle and the boot pass fired BEFORE
the terminal event was journaled (e.g. the pipeline
runner journaled the terminal event during the next
boot's first tick, not the boot pass itself). The
periodic tick is the retry path; the boot pass is the
fast path.

The wake record's `abandon_after` is `expires_at + 600s`
(default grace). If the periodic tick observes a
`pending` wake past its `abandon_after`, the record is
transitioned to `abandoned` (D-FA1.2). The agent is
not re-woken; the recovery is the user's interactive
`upgrade_status` (the same pull-model recovery as
D-FA1.2 — see `supersession-record.md` §4).

---

## FA4 — Terminal-State Gating (AC4) ✅ DECIDED

### D-FA4.1 — The terminal-state predicate (when is it safe to wake) ✅ DECIDED

The wake is delivered only when the journal has a
**terminal-class history event** for `wake.run_id`. The
predicate:

```python
_TERMINAL_EVENTS = (
    "commit", "rollback", "halt", "sweep_rollback",
    "sweep", "quarantine", "restart",
)  # upgrade_journal.py:983

def _is_pipeline_terminal(install_dir: Path, run_id: str) -> str | None:
    """Return the terminal event name for run_id, or None if the
    pipeline is still in flight or pending.
    """
    try:
        data = journal_read(install_dir)
    except JournalTorn:
        return None
    history = data.get("history") or []
    # History is append-only; the LATEST matching event is the terminal one.
    for event in reversed(history):
        if isinstance(event, dict) and event.get("run_id") == run_id:
            name = event.get("name") or event.get("event")
            if isinstance(name, str) and name in _TERMINAL_EVENTS:
                return name
    return None
```

**The wake is NOT delivered if `_is_pipeline_terminal` returns `None`.** The wake is held `pending` and retried on the next tick. This is the
load-bearing AC4 guarantee: "no wake while pipeline could
still roll back".

**Edge case — the journal was lost / torn on disk:** the
sweep's best-effort read returns `None`; the wake is held
`pending` until next tick; the next tick retries. The
`abandon_after` clock is the upper bound. The launcher
journal-sweep (the deeper backstop at
`daemon/services/upgrade_journal_sweep.py:198-242`) is
the recovery for a hard-torn journal — but our wake sweep
does not depend on it (best-effort, never wedges).

### D-FA4.2 — Race: wake delivered, but agent's response interrupted by another restart

A wake can be delivered (status `delivered`) and the
agent's response is then interrupted by another daemon
restart before the response reaches the user. In this
case:

* The `pending_wakes` record is `delivered` (cleared from
  the dict).
* The agent's response was interrupted.
* The user did NOT receive the report.
* The next daemon restart observes NO wake record.
* The user re-queries via `upgrade_status` (the pull
  model) and gets the answer.

**This is the "accept the loss" decision** (Q3 in
`plan-overview.md`). The wake is one-shot; the
response-delivery is best-effort, like every other
report in the system. The D-FA1.2 pull model is the
recovery. The user, on their next message, will get the
answer.

**Why not re-wake on the next boot:** the `delivered`
status is the idempotency key. A re-wake would risk
double-delivery (if the response was queued but not
delivered before the restart) and would not actually
help (the response is in the conversation history; the
agent already has the answer). The acceptance is a
feature, not a bug — it bounds the wake surface to ONE
per arm.

### D-FA4.3 — Race: the periodic tick fires while the boot pass is still running

The sweep is in-process. A periodic tick cannot run
concurrently with the boot pass (the tick is gated on
the service's `start()` having been called and the
service is single-threaded — the `UpgradeJournalSweepService`
is an asyncio task, not a multi-process pool). The race
does not exist in this codebase.

The race that DOES exist: a periodic tick and a
arm-time write. Both acquire the per-env pipeline
lock (`journal_lock_acquire`) before the wake write /
status transition. The arm-side write holds the lock
from arm time to post-graph drain; the tick's CAS
transition (`pending → delivering`) acquires the same
lock. The two are serialized. A failed CAS
(lock not acquired) is logged and skipped — the next
tick retries.

---

## FA5 — Edge Cases (AC5) ✅ DECIDED

### D-FA5.1 — Missing arming instance (the project was deleted, or the instance was terminated) ✅ DECIDED

**Decision:** if `enqueue_message` raises `InstanceNotFound` /
no such row, the wake branch **falls back to ari** in the
same project (front-door pattern) IF an ari instance exists;
otherwise **surfaces a structured notice** in the wake
branch's `history` event (so a tester / operator can see
"wake for run X had no live arming instance; no ari
fall-back; manual upgrade_status recommended").

```python
async def _deliver_wake(self, install_dir, wake, terminal_outcome):
    # 1. Re-stamp (FA3.2)
    if wake.source:
        manager.stamp_user_origin_window(...)
    body = _format_wake_body(...)
    try:
        result = await manager.enqueue_message(...)
        return result.message_id
    except InstanceNotFound:
        # Front-door ari fall-back
        ari_id = _find_ari_instance_in_project(manager, wake.arming_instance_id)
        if ari_id:
            result = await manager.enqueue_message(
                instance_id=ari_id,
                message=body + "\n\n(arm-notice: original arming instance not found; reporting via ari fall-back)",
                source=wake.source or "api",
                priority=2,
                metadata={...},
            )
            return result.message_id
        # No fall-back available — journal the notice
        journal_history_append(
            install_dir,
            "arm_notify_no_instance",
            f"run_id={wake.run_id} arming_instance_id={wake.arming_instance_id} "
            f"no live instance, no ari fall-back; manual upgrade_status recommended",
        )
        return None  # boot-never-wedge; the user can still query
    except Exception as exc:
        # FA5.4 — never raises
        logger.warning(...)
        return None
```

**Why ari fall-back (not a generic fall-back):** ari is
the canonical front door for the upgrade surface (the
arm-time caller is always ari/jober — verified by the
existing arm path's `_actor_env_gate`). The ari
fall-back ensures the user-facing chat is the ari
chat (which is the same chat the original arm came
through if the original arm was via ari).

**Why journal the notice, not raise:** boot-never-wedge
(FA5.4). A missing instance is an exceptional state, not
a fatal one; the wake sweep continues to the next
record.

### D-FA5.2 — Multiple wake records (long downtime, two arm cycles) ✅ DECIDED

**Decision:** coalesce by `arming_instance_id` at delivery
time. The sweep groups pending wakes by instance; each
instance receives ONE wake with a run-list payload.

```python
def _coalesce_wakes(
    pending: list[PendingWake],
) -> dict[str, list[PendingWake]]:
    """Group pending wakes by arming_instance_id. Preserves the
    latest-first ordering (boot pass processes the most recent
    arm first if the user asked about it most recently — the
    run_id mint is monotonic, so reverse-sort by armed_at is
    correct).
    """
    out: dict[str, list[PendingWake]] = {}
    for w in pending:
        out.setdefault(w.arming_instance_id, []).append(w)
    for k in out:
        out[k].sort(key=lambda w: w.armed_at, reverse=True)
    return out
```

**The coalesced wake body:**

```
Post-restart arm-notify (coalesced — N arms pending for this
instance). The most recent armed <kind> <run_id> completed
during the daemon downtime.

Outcomes (newest first):
  - <run_id_a>: <terminal_outcome_a> at <armed_at_a>
  - <run_id_b>: <terminal_outcome_b> at <armed_at_b>
  - ...

This is an auto-wake — call upgrade_status(run_id="<run_id>")
on each, or upgrade_status() with no args to enumerate
all. The report will route to the same channel where the
arms were confirmed.
```

The LLM in the agent's first turn reads the run-list and
calls `upgrade_status` for each, or enumerates if
appropriate. The user sees a single wake, not N wakes.

**Why coalesce, not deliver N:** the user's mental model
is "the daemon restarted; what happened?" not "the daemon
restarted, deliver N separate notifications". A single
coalesced wake is the right granularity. The N
`upgrade_status` calls in the agent's first turn are
incidental — the LLM-driven turn fans them out.

**Bounded N (safety):** the coalesce caps at
`PENDING_WAKE_COALESCE_MAX = 16` (default). Past that, the
overflow is journaled to `history` with a "wake coalesce
overflow" event and the sweep continues. The user can
query interactively for the rest. The cap exists to
prevent a runaway coalesce body from creating a wake that
exceeds `MessageQueue.message` size.

### D-FA5.3 — Idempotent delivery (the AC5 explicit guarantee) ✅ DECIDED

The `status` field is the idempotency key:

* A `pending` record is delivered once, then transitions
  to `delivering` (CAS) and then `delivered` (unconditional).
* A `delivering` record observed by a subsequent tick is
  the CAS-loser case — the tick skips it (it is in flight).
* A `delivered` record is removed from the dict on the
  next journal write.
* An `abandoned` record is removed from the dict on the
  next journal write.

**An unrelated later restart observes a CLEAN dict (no
`pending_wakes` for the delivered/abandoned `run_id`s)**
because the record is removed from the dict on the
delivered/abandoned write. The "unrelated later restart
must not re-deliver" guarantee is **structural** (dict
removal), not conditional on a timestamp comparison.

**Crash-safety:** the CAS transition (`pending → delivering`)
acquires the journal lock; the subsequent `delivered`
write also acquires the journal lock. A crash between
the two leaves the record in `delivering`. The next tick
observes `delivering` and:
* If the lock is held by another process, the tick skips
  (the other process is the in-flight delivery).
* If the lock is free (the in-flight delivery crashed),
  the tick re-CASes the record back to `delivering` (the
  CAS is idempotent: `delivering → delivering` is a
  no-op) and re-attempts the `enqueue_message`.

The crash-safety is bounded by the lock's owner liveness
(EXECUTOR_STALENESS_WINDOW_S=900, same as the existing
executor reaper). Past the staleness window, the
in-flight delivery is presumed dead; the wake is
abandoned (`abandon_after` clock).

### D-FA5.4 — Boot never wedges (AC5 explicit guarantee) ✅ DECIDED

The wake sweep is **best-effort, never raises**. Every
catchable failure mode logs and continues:

```python
async def sweep_wake_records(self) -> WakeSweepResult:
    """Best-effort boot + periodic wake delivery. Never raises.
    Returns a structured result for the caller to log.
    """
    result = WakeSweepResult()
    if not self._is_enabled():
        return result
    try:
        install_dir = self._install_dir
        if install_dir is None:
            return result
        # 1. Read journal (best-effort)
        try:
            data = journal_read(install_dir)
        except JournalTorn as exc:
            logger.warning(...)
            return result  # skip this tick; next tick retries
        # 2. Find pending wakes
        pending_wakes = _list_pending_wakes(data)
        if not pending_wakes:
            return result
        # 3. For each pending wake: terminal-state check + deliver
        for wake, terminal_outcome in self._resolve_wake_targets(pending_wakes):
            try:
                # ... CAS, deliver, mark_delivered, all best-effort ...
                pass
            except Exception as exc:  # per-wake, never aborts the sweep
                logger.warning(...)
                result.errors += 1
        return result
    except Exception as exc:  # sweep-level, never aborts boot
        logger.warning(
            "UpgradeJournalSweepService wake sweep failed (next tick retries): %s",
            exc, exc_info=True,
        )
        return result
```

**The kill-switch (`ENSEMBLE_POST_RESTART_ARM_NOTIFY=0`):**
the sweep's `_is_enabled()` short-circuits the entire
wake branch. The arm-side write is gated on the same
env. **Default is ON** (the feature is the
zero-user-action deliverable; the kill-switch exists
for operator opt-out, not for default-off).

**Boot never aborts:** the api.py call site
(DA-FA3.1) wraps the sweep in `try/except Exception` and
logs WARNING. The lifespan yield is not blocked.

### D-FA5.5 — Live-env refused restart (no record) ✅ DECIDED

A live `system_restart` returns at
`upgrade_tools.py:2051-2058` BEFORE any journal write.
**No `pending_op` is written, no wake record is
written.** The wake surface cannot originate from a
live restart path. The kill-switch is a NO-OP for the
live case (the arm itself is the gate).

A live `system_upgrade` writes a wake record only on
the verified-arm path (the 3-factor gate at
`upgrade_tools.py:2494-2660` is the precondition for
the lock acquire; the arm's `JournalTorn`-or-`OSError`
or-`KeyError` catch is the precondition for the wake
write). A live unverified arm is refused at the gate
and writes no wake. The live wake is gated by the
same F2 surface as the live arm; the wake inherits
the F2.

---

## FA6 — Reuse Existing Machinery (AC6) + Kill-Switch ✅ DECIDED

### D-FA6.1 — Reuse, do not parallel (AC6) ✅ DECIDED

The wake rides the existing primitives:

| Concern | Existing primitive | Where |
|---|---|---|
| Durable record | `releases/state.json` atomic surface, `ensure_extensions` slot | `upgrade_journal.py:332-352` |
| Atomic write | `journal_write` (tmp+fsync+os.replace) | `upgrade_journal.py:281-294` |
| Lock | `journal_lock_acquire` / `journal_lock_release` | `upgrade_journal.py` (existing) |
| Wake delivery | `manager.enqueue_message` | `manager.py:7935-7996` |
| User-origin window re-stamp | `manager.stamp_user_origin_window` | `manager.py:4230-4245` |
| Boot pass | `UpgradeJournalSweepService.sweep_*` | `daemon/services/upgrade_journal_sweep.py` |
| Periodic tick | Same service, 90s tick | Same |
| Arm-side integration | The existing arm path's `journal_write` envelope | `upgrade_tools.py:2156-2263`, `2707-2872` |

**No new file, no new table, no new HTTP endpoint, no new
job type.** The wake is a journal extension + a sweep
sub-routine + an enqueue_message call. AC6 is met by
construction.

### D-FA6.2 — Kill-switch + observability ✅ DECIDED

**Kill-switch:** `ENSEMBLE_POST_RESTART_ARM_NOTIFY=0`
disables the entire feature. The arm-side write is gated
on the env (so a disabled feature produces no journal
writes); the sweep's wake branch is gated on the env (so
a disabled feature performs no wake work). Default is ON.

**Observability:**
* The arm-side write logs a structured INFO line
  (`post_restart_arm_notify armed run_id=<r> source=<s>`).
* The sweep's `sweep_wake_records` returns a structured
  result (delivered, abandoned, errors, pending_at_start,
  pending_at_end) — same shape as the existing sweep
  services.
* The wake delivery logs a structured INFO line
  (`post_restart_arm_notify delivered run_id=<r> message_id=<m>`).
* The abandonment journals a `history` event for forensics.
* The missing-instance case journals an
  `arm_notify_no_instance` history event.
* The coalesce-overflow case journals a
  `wake_coalesce_overflow` history event.

All structured log lines carry `run_id` for grep
correlation with the arming turn and the journal
history.

---

## 7. Risk Map Summary (full register in `risk-register.md`)

| ID | Risk | Severity | Mitigation |
|---|---|---|---|
| R-1 | Wake delivered but agent's response interrupted by another restart — user never receives the report | 🟡 Medium | One-shot delivery; pull-model recovery via `upgrade_status`. Accepted per Q3. |
| R-2 | Multiple arms over a long downtime create N wake records — wake surface floods | 🟢 Low | Coalesce by instance; bounded by `PENDING_WAKE_COALESCE_MAX = 16`; overflow journaled. |
| R-3 | Wake re-stamp of user-origin window could enable a forged live arm | 🟢 Low | Re-stamp source is the recorded arm-time source, which already passed the F2 surface on the verified-arm path. For non-live arms, re-stamp is informational; the 3-factor gate does not run. |
| R-4 | `journal_write` failure during arm — wake record absent, but `pending_op` also absent (atomic) | 🟢 Low | The arm itself failed; the existing `JournalTorn`-or-`OSError`-or-`KeyError` catch unwinds. No asymmetry. |
| R-5 | Long-running pipeline that does NOT journal a terminal event for >`abandon_after` | 🟡 Medium | Wake is abandoned; user-inquiry recovery. Grace = 600s default; tunable. |
| R-6 | `enqueue_message` raises `InstanceNotFound` because the arming instance was terminated/expired between arm and wake | 🟡 Medium | Front-door ari fall-back; otherwise journaled notice. AC5 covered. |
| R-7 | The wake is delivered but the LLM in the agent's first turn does NOT call `upgrade_status` — the user receives a "this is an auto-wake" message but no outcome | 🟡 Medium | The wake body is a self-describing pointer; the LLM is expected to read it. Prompt-level instruction (a follow-up patch to the ari / jober prompts) is the prompt-side guard. Out of scope for this feature's code; flagged in `risk-register.md` R-7. |
| R-8 | The live-outright-refusal for `system_restart` is bypassed by a malicious actor — wake record is written for a live arm | 🔴 Critical | The refusal at `upgrade_tools.py:2051-2058` is the same code path the existing live-outright-refusal uses; the wake inherits the gate by structural coupling. The kill-switch is a NO-OP for the live case. |

---

## 8. Cross-cutting invariants (the rules a reviewer must check)

1. **No second file.** The wake is a `pending_wakes` key on
   `releases/state.json`. D-FA1.1 ruling applied.
2. **No parallel messaging subsystem.** The wake is
   `enqueue_message`. D-FA3.1 ruling applied.
3. **No new HTTP endpoint.** The boot sweep is internal to
   the daemon. D-FA3.1 ruling applied.
4. **No new SQLModel table.** The journal is the durable
   surface. D-FA1.1 ruling applied.
5. **Atomic with the arm.** The wake write is in the same
   `journal_write` call as the arm. D-FA2.1 ruling applied.
6. **Boot never wedges.** Every catchable failure logs and
   continues. D-FA5.4 ruling applied.
7. **One-shot delivery.** The `status` field is the
   idempotency key. D-FA1.2 / D-FA5.3 ruling applied.
8. **Live never records.** The arm-side live-outright-refusal
   pre-empts the wake write. D-FA5.5 ruling applied.
9. **No LLM in the critical path.** The wake is a
   journal→enqueue action; the LLM is the user-facing
   delivery step, not the recovery step. D-FA3.2 ruling applied.
10. **Routing preserved.** The wake's `source` is the
    recorded arm-time source. D-FA3.3 ruling applied.
11. **Kill-switch is operator opt-out, not default-off.**
    D-FA6.2 ruling applied.
12. **Coalesce is bounded.** `PENDING_WAKE_COALESCE_MAX = 16`.
    D-FA5.2 ruling applied.
