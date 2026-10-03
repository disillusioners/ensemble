# Post-Restart Arm-Notify — Decision Log (ADR-style)

> **HARD CONSTRAINT (inherited verbatim from Phase 2, governs every decision below):**
> "NEVER touch the live/production ensemble environment — it is the
> running environment of Ari and all live agents (~/agents-ensemble,
> port 9797, prod DB, ENSEMBLE_DEPLOY_LIVE are out of bounds; live
> pids must remain untouched). ALL work/testing/drills in dev and
> demo only. If any plan step would require touching live, mark it
> as USER-GATED and design it as an explicit user-confirmed action.
> Sandbox instances (own port + throwaway PG) are fine."

- **Date:** 2026-10-03 · **Author:** architect (controller) — feature analysis
- **Siblings (do not author):** `plan-overview.md` (single-author);
  `architecture-recommendation.md` (single-author, this worker's
  decision bundle source); companion `test-strategy.md`,
  `risk-register.md` (single-author)
- **Status:** all entries below are **DECIDED** (the architecture
  recommendation is the source of truth; this file is the
  ADR-style extraction).
- **Numbering:** continues the Phase-2 ADR series
  (ADR-016…032 in
  `.agents/shared/planning/self-restart-upgrade-phase2/decisions.md`)
  — proposed **ADR-039** through **ADR-044**.
- **Renumber note (2026-10-03):** originally drafted as
  ADR-039…ADR-044, but those numbers were already in use by the
  upgrade-subsystem ADRs (ADR-039 = halt-semantics posture cited
  from `scripts/upgrade/restart.sh:231`; ADR-040 = splice/journal-append
  discipline cited from `scripts/upgrade/ledger_check.py:6,394` +
  `lib.sh:1464,3085` + `stop-ensemble.sh:213`; ADR-041 = `stage.sh`
  `rollback_safe` rider in `self-restart-upgrade-phase2/decisions.md:215`;
  ADR-042 = nonce 60min in `self-restart-upgrade-phase2/decisions.md:247`).
  Renumbered to ADR-039…ADR-044 to avoid collisions; every
  cross-reference in this directory updated to match.

Format: **Context → Options → Decision → Consequence**, each
with *recommended default* and *what breaks if chosen otherwise*.
⚠ = needs user decision at review.

---

## ADR-039: Wake record schema + write atomicity — `pending_wakes` keyed by `run_id` on the existing journal

**Context.** The post-restart arm-notify feature needs a durable
record at arm time that (a) survives `clear_pending_op` and
`restart.sh`'s post-completion clearing (which clears
`pending_op` + `pending_restart` + `in_flight`), (b) carries
the routing context (source, message_id, message_metadata)
that is in-memory-only and is wiped at boot, and (c) is
read in the same sweep pass that reads the journal's
terminal-state for the run. D-FA1.1 in
`self-restart-upgrade-phase2/architecture-recommendation.md`
explicitly rejected a parallel file (`pending_actions.json`
proposal, unanimous). The new record must ride the same
atomic surface.

**Options.** (a) New SQLModel table `arm_wake_records`; (b) a
parallel `wake_records.json` file; (c) extend the existing
journal with a new `pending_wakes` keyed dict.

**Decision.** **(c).** New dataclass `PendingWake` in
`daemon/tools/upgrade_journal.py`; new key
`pending_wakes: dict[str, PendingWake]` on
`releases/state.json`; new helpers `arm_pending_wake`,
`mark_wake_delivering`, `mark_wake_delivered`,
`mark_wake_abandoned`, `list_pending_wakes`, all in
`upgrade_journal.py` and all reusing the existing
`ensure_extensions` + `journal_write` atomic envelope.
The wake record's lifecycle state machine
(`pending → delivering → delivered | abandoned`) is the
status field. The `from_json` filter discipline
(`upgrade_journal.py:738`) is preserved — unknown fields
drop silently, known fields validated.

**Why not (a):** a SQLModel table adds a second atomicity
surface (the table is written in a separate transaction
from the journal write). A crash between the table write
and the journal write leaves the system in an unknown
state. The journal is the existing single-writer
surface; we extend it.

**Why not (b):** same as D-FA1.1's reasoning for rejecting
`pending_actions.json` — two files = two atomicity
surfaces. The wake is read alongside the terminal-state
check; one `journal_read` is cheaper than two file reads
+ a coordination layer.

**Consequence.** The journal schema gains one new key.
Existing readers of the journal ignore it (Python `dict.get`
returns `{}` if absent). The arm-side write acquires
`journal_lock_acquire` once; the wake write is in the
same `journal_write` envelope — atomic by construction.
**Recommended default:** (c). **If declined:** the
zero-user-action guarantee (AC1) cannot be met without
introducing a new atomicity surface, which D-FA1.1
unanimously rejected.

---

## ADR-040: Boot sweep + wake delivery primitive — `UpgradeJournalSweepService.sweep_wake_records` + `manager.enqueue_message`

**Context.** The wake must be delivered to the arming
instance after the daemon restart. The existing
`UpgradeJournalSweepService` (api.py:1477-1523) has the
guaranteed pre-yield boot pass + the 90s periodic tick —
the right cadence for the wake surface. The wake
primitive is `manager.enqueue_message` (manager.py:7935),
THE existing internal wake primitive used by reports,
nudges, [JOB_EVENT] delivery, compaction, system
messages, and the WC watchdog (manager.py:7984).

**Options.** (a) New dedicated wake service +
HTTP endpoint; (b) extend the existing sweep + reuse
`enqueue_message`; (c) new message bus separate from
MessageQueue.

**Decision.** **(b).** Add
`UpgradeJournalSweepService.sweep_wake_records()` as a
new sub-routine of the existing boot pass + periodic
tick. The wake primitive is `manager.enqueue_message`
with `source=<recorded>`, `priority=2`, `metadata=
{"system_context": {"kind": "post_restart_arm_notify",
...}, "delivery": {"channel": "post_restart_arm_notify"}}`.
The re-stamp of the user-origin window is
`manager.stamp_user_origin_window` (manager.py:4230-4245)
— the existing in-memory stamp, set on the wake turn so
a follow-up `upgrade_status` call within the same wake
turn passes the 3-factor gate.

**Why not (a):** AC6 explicitly forbids a parallel
messaging subsystem. The boot sweep IS the wake
delivery surface; adding a second service is exactly
the parallel-machinery rejection.

**Why not (c):** a new message bus is an even larger
deviation from the existing surface. The existing
`MessageQueue` + `enqueue_message` is the canonical
internal wake primitive.

**Consequence.** The wake rides the existing
infrastructure. The only new code is the sweep's
sub-routine and the helper that formats the wake body.
The 90s tick is the recovery path; the boot pass is the
fast path. **Recommended default:** (b). **If declined:**
AC2 (boot delivery) and AC6 (no parallel messaging
subsystem) cannot be simultaneously met.

---

## ADR-041: Routing preservation — re-stamp the user-origin window for the wake turn; the wake's `source` is the recorded arm-time source

**Context.** AC3 (ROUTING) is non-negotiable: the wake
turn's outcome report must flow back to the confirming
chat. The arming turn's source is in-memory only
(`_user_origin_windows[instance_id]`, manager.py:4239-4245)
and is wiped at boot. The wake primitive
(`enqueue_message`) accepts a `source` parameter, but
the report-delivery path's response-source is set by
the message-processing pipeline to the TRIGGERING
message's source. The wake IS the triggering message;
its `source` becomes the response's `source`.

The 3-factor gate (factor 2 = user-origin window) also
reads `_user_origin_windows[instance_id]`. A follow-up
`upgrade_status` call within the same wake turn needs
the window re-stamped to pass the gate.

**Options.** (a) Re-stamp the window at wake delivery
and set the wake's `source` to the recorded value; (b)
inject a new "reply-to-source" metadata field on the
wake that the report-delivery path consults; (c) a new
"channel override" envelope that pins the source for N
turns after the wake.

**Decision.** **(a).** Re-stamp the user-origin window
on the wake turn via `manager.stamp_user_origin_window`;
set the wake's `source` to the recorded arm-time
source. The existing report-delivery path's
response-source routing is unchanged — it already
inherits from the triggering message's `source`. The
3-factor gate's factor-2 is satisfied by the re-stamp.

**Why not (b):** a new "reply-to-source" field requires
a parallel read in the report-delivery path, which
violates AC6 (no parallel messaging subsystem). The
existing `source` field on `MessageQueue` is the
canonical channel; using it is the additive change.

**Why not (c):** a "channel override" envelope is a
new messaging primitive. The wake's `source` is
already the channel; an envelope is redundant.

**Consequence.** The wake's routing is structural — the
same path every other wake in the system uses. The
re-stamp is safe by construction: the recorded source
ALREADY passed the F2 surface on the verified-arm path
(the 3-factor gate is a precondition for the arm on
live; the arm's `pending_wakes` is written in the same
atomic envelope as the arm). For non-live arms, the
re-stamp is informational; the 3-factor gate does not
run. **Recommended default:** (a). **If declined:** AC3
(routing) cannot be met without a new messaging
primitive, which AC6 forbids.

---

## ADR-042: Terminal-state gating & abandonment policy — wake only on terminal-class history event; grace = 600s post-`expires_at`

**Context.** AC4: "no wake while pipeline could still
roll back, OR race harmless by design." The wake is
delivered only when the journal has a terminal-class
history event for the wake's `run_id`. The
`_TERMINAL_EVENTS` constant is at
`upgrade_journal.py:983`:
`(commit, rollback, halt, sweep_rollback, sweep,
quarantine, restart)`. The grace = 600s post-`expires_at`
prevents infinite retention of un-deliverable wakes
(e.g. a torn journal that never journaled a terminal
event).

**Options.** (a) Wake on terminal event, with grace
abandonment; (b) wake on `in_flight` clearing (with
race risk); (c) wake on `pending_op` clearing (which
is wrong — `pending_op` is cleared BEFORE the terminal
event is journaled in some paths).

**Decision.** **(a).** The wake is delivered only when
`_is_pipeline_terminal(install_dir, run_id)` returns a
non-None terminal event name. The grace is
`PENDING_WAKE_GRACE_S = 600` (default), tunable. Past
the grace, the wake is marked `abandoned` and a
`history` event is journaled for forensics. The
abandoned record is removed from the dict.

**Why not (b):** `in_flight` is cleared on the
post-completion side BEFORE the terminal event is
journaled in some paths (e.g. restart.sh:250-262 clears
`in_flight` THEN journals the `restart` event). A wake
on `in_flight` clearing would race the terminal event
journaling; the wake could fire on a pipeline that
"almost completed" but is about to abort or rollback.
The terminal event is the safe predicate.

**Why not (c):** `pending_op` is cleared on the
post-completion side (the `clear_pending_op` function at
`upgrade_journal.py:761-766` clears `pending_op` and
`pending_restart`). A wake on `pending_op` clearing
would miss wakes for `kind=promote` (where
`clear_pending_op` is the normal completion path) AND
would race the same way as (b). The terminal event is
the safe predicate.

**Consequence.** The wake is held `pending` until a
terminal event is observed; the next tick retries. The
grace bounds the wake surface. **Recommended default:**
(a). **If declined:** the AC4 guarantee is racy; an
abort-then-rollback path could wake the agent on a
still-rolling-back pipeline.

---

## ADR-043: Edge cases & coalescing — front-door ari fall-back, coalesce by arming_instance_id, bounded coalesce, one-shot delivery

**Context.** AC5 enumerates four edge cases: missing
instance, multiple records, idempotent delivery, boot
never wedges, live-refused restarts have no record. The
decisions are:

* **Missing instance:** front-door ari fall-back IF an
  ari instance exists in the same project; otherwise
  journal an `arm_notify_no_instance` history event.
* **Multiple records:** coalesce by `arming_instance_id`
  at delivery time; ONE wake with a run-list payload;
  bounded by `PENDING_WAKE_COALESCE_MAX = 16` (default).
* **Idempotent delivery:** the `status` field
  (`pending → delivering → delivered | abandoned`) is
  the idempotency key; structural removal from the dict
  on `delivered` / `abandoned` makes the guarantee
  unconditional.
* **Boot never wedges:** every catchable failure logs
  and continues; the api.py call site wraps the sweep
  in `try/except Exception`; the lifespan yield is not
  blocked.
* **Live-refused restarts:** the existing
  live-outright-refusal at `upgrade_tools.py:2051-2058`
  pre-empts the journal write; the wake record is
  structurally absent for live-arming paths.

**Options.** (a) The above decisions; (b) per-wake
delivery (no coalesce) — simpler but floods the wake
surface on long downtime; (c) no fall-back (drop on
missing instance) — loses the AC5 explicit guarantee.

**Decision.** **(a).** The decisions above are the
design. The coalesce body format is documented in
`architecture-recommendation.md` §FA5.2; the
fall-back chain is documented in §FA5.1; the
one-shot delivery is documented in §FA5.3; the
boot-never-wedge contract is documented in §FA5.4; the
live-refusal inheritance is documented in §FA5.5.

**Why not (b):** the user mental model is "what
happened during downtime" not "deliver N separate
notifications". A single coalesced wake is the right
granularity. The LLM in the agent's first turn fans
out the `upgrade_status` calls.

**Why not (c):** the AC5 explicit guarantee is the
user-facing recovery; a drop-on-missing instance
breaks the guarantee. The ari fall-back is the
front-door pattern; the journaled notice is the
operator forensics.

**Consequence.** All four edge cases are covered
explicitly. The coalesce cap is a safety bound, not a
delivery rate-limit. **Recommended default:** (a).
**If declined:** AC5 cannot be met as a single
guarantee; per-edge-case patches are required.

---

## ADR-044: Kill-switch & boot-never-wedge — `ENSEMBLE_POST_RESTART_ARM_NOTIFY=0` (default ON); every catchable failure logs and continues

**Context.** The feature is operator opt-out-able
(`ENSEMBLE_POST_RESTART_ARM_NOTIFY=0`) but default-ON
(zero-user-action is the deliverable; the kill-switch
is for operator opt-out, not default-off). The sweep
is best-effort, never raises; the boot pass wraps the
sweep in `try/except Exception` and logs WARNING.

**Options.** (a) The above; (b) default-OFF with
opt-in; (c) no kill-switch.

**Decision.** **(a).** `ENSEMBLE_POST_RESTART_ARM_NOTIFY=0`
disables both the arm-side write AND the sweep's wake
branch. Default is ON. Every catchable failure in the
sweep logs and continues; the api.py call site wraps
the sweep in `try/except Exception` and never aborts
boot.

**Why not (b):** default-OFF with opt-in defeats the
zero-user-action deliverable. The user must remember
to enable the feature for the deliverable to be in
force. The whole point of the feature is that it
works by default.

**Why not (c):** a kill-switch is operator hygiene.
A bug in the wake delivery must be disable-able
without a code change. The kill-switch is the
operator's "stop the bleeding" lever.

**Consequence.** The feature is operator-controllable.
The boot pass is never blocked by the wake sweep.
**Recommended default:** (a). **If declined:** the
operator has no opt-out lever; a bug in the wake
delivery is a hard outage.

---

## Cross-references

* **D-FA1.1 (pending_op record)** — UNCHANGED; the wake
  is on the same `releases/state.json` atomic surface.
* **D-FA1.2 (pull model for outcome reporting)** —
  **SUPERSEDED for the wake concern** (this feature's
  `supersession-record.md`); **UNCHANGED for
  interactive `upgrade_status` / `release_info` queries**.
* **D-FA1.3 (daemonized executor)** — UNCHANGED; the
  wake is a post-pipeline concern.
* **D-FA1.4 (post-turn trigger)** — UNCHANGED; the
  wake is the BOOT-time analog for the case when the
  turn-end path could not run.
* **D-FA1.5 (in-flight semantics)** — UNCHANGED; the
  in-flight freeze-at-checkpoint model is preserved;
  the wake is delivered only when the journal terminal
  event is observed.
* **D-FA2.4 (env-self-match, live outright-refusal)** —
  UNCHANGED; the wake inherits the live-outright-refusal
  by structural coupling.
* **ADR-017 (env-target permission model)** — UNCHANGED;
  the wake record is written only for non-live arms OR
  verified-live arms; the wake inherits the F2 surface
  on the verified path.
* **ADR-025 (watchdog-watcher)** — UNCHANGED; the
  daemon-DOWN notifier is the complementary failure
  surface; the wake is the daemon-RESTARTED analog.
  The P2.3 T8 hard-dependency for the failure-path
  story is now downgraded — the wake is the *primary*
  restart path; the watcher is the *backstop* for
  stays-down.
