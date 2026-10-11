# Deferred Debt — carry-forward from fix-retry-supersede-double-silence (e8a61da3)

Deferred-debt inventory for the watch-loss fix commission (Option A+E,
user-picked per `.agents/shared/planning/watch-loss-fix-options/
options-matrix.md`). Recorded at commission close-out so the next
commission starts from a clean backlog. None of these block this
branch; each names its own trigger and owner-commission shape.

## (a) watch-row audit/history table design gap — the big one

**The gap.** `job_watchers` rows are DELETE-on-claim (the N1/C1
exactly-once CAS). Once a row is claimed there is NO durable record
of: what the watcher originally subscribed to, which work_ids the
coverage rode, when/why it was re-keyed (F6), when it was consumed,
or what envelope was delivered. The `stored_watches` quarantine table
introduced by Option A is a **partial, lane-scoped** audit record
ONLY for recovery-armed re-keys on the usage-limit deferral lane — it
is NOT a general watch-row history:

* rows are INSERTed only by the in-txn F6 hook with
  `rearm_watchers=True` (usage-limit lane only — the timeout lane,
  stale-recovery lanes, and natural arming/claiming are invisible);
* the GC transitions (`released` / `rehydrated`) describe the
  quarantine lifecycle, not the carrier row's delivery history;
* ordinary watch registration, transport-kind consumption, S15
  compensation re-inserts, and zombie-GC retires all remain
  unrecorded anywhere durable.

**Why deferred.** The original commission brief explicitly fenced it:
"watch-row audit/history table design — do NOT design it; instead
record the design gap in the deferred-debt journal". A general
history table has its own schema surface (own `*.sql` migration per
the zero-exceptions rule), its own retention story, and its own
writer-coordination questions (three-writer contention on
`job_watchers` is exactly what the Option A matrix depth-3 ruling
avoided for the quarantine table) — a design commission, not a rider
on a fix lane.

**Trigger to revisit.** The E2 durable `JOB_COMPLETED` witness rows
(via the EventBus durable lane) give mission-terminal-level audit
already. Escalate to a full watch-row history design when (i) the
timeout-lane silent-loss family confirms live (matrix §2(v)) and
operators need per-row forensics, or (ii) ≥2 silent-loss families are
confirmed (the same trigger the matrix names for reopening Option D —
design both together, since dual-key identity and row history share
the migration surface).

## (b) timeout-lane family #2 — unconfirmed, parked by ruling

`worker_pool.py:834-862` (the TIMEOUT lane) shares the exact
silent-supersede shape with the usage-limit lane (same
`schedule_retry`, same no-notify retry branch). The brief ruled:
"do not 'fix' it, just don't break it" — this commission's
`rearm_watchers=True` flag deliberately does NOT pass from that lane.
If the family confirms live, the fix is one flag-flip on the timeout
caller PLUS extending the confirmation evidence (a
`stored_watches`-shaped quarantine row would then appear for timeout
rekeys too, which the GC leg already handles generically).

## (c) `evaluate_mission_live` subtree semantics on the notify lane — inherited, documented

The C1 partition's mission-live guard walks the tree rooted at the
WORK's `instance_id` (work_record.instance_id), not the mission's
leader. A worker-subtree that goes all-terminal while the leader (an
ANCESTOR, invisible to the walk) still runs would read `live=False`
on that lane. This is the frozen C2 contract (council 2026-09-28) —
inherited verbatim by the E1 gate (which passes the JobItem's
instance) and by the stored-watch GC (which passes the stored
`mission_instance_id` root — the GC leg is actually the ONE caller
that anchors at the true root). Not changed here; any revisit is a
frozen-contract matter (council), not a fix-lane rider.

## (d) pre-existing test failures observed on this branch — base-proven

`tests/message_queue_redesign/test_usage_limit_worker_seam.py::
TestCarveOut::test_usage_limit_re_raised_no_cascade` and
`test_other_exceptions_cascade_unchanged` fail IDENTICALLY on the
branch base `5cb964187` (verified via stash-run 2026-10-11;
`SimpleNamespace` fixture missing `message.priority` at
task_processor.py:552 — unrelated to this commission). Not
introduced here; recorded so the next lane doesn't re-triage them.

## Related out-of-scope threads (recorded, not debt of this branch)

- **mission_terminal-by-default (Option C)** — user-gated defer;
  revisit trigger per matrix §2(v): timeout-lane family confirms live
  or a third family appears.
- **dual-key watch migration (Option D)** — architect-gated; trigger:
  ≥2 confirmed silent-loss families.

## (e) stored-watch GC hardening trio — optional future follow-ups

Three small GC-leg hardenings, all OPTIONAL and out of scope for
this commission. Recorded so the next lane that touches the GC
sees them already costed. None unblock this branch; each is
self-contained and additive — adopt any combination without
shaping the others.

1. **Post-verdict carrier-existence re-check before rehydrate.**
   The current GC leg takes ``evaluate_mission_live``'s verdict
   and walks to rehydrate; on a long-held row the carrier
   (``job_queue_items`` / ``task``) may have been concurrently
   deleted between the verdict read and the rehydrate INSERT. A
   re-read just before the INSERT closes the no-op-insert class
   (UPSERT no-op on a missing key is silently silent; a stale
   verdict + vanished carrier = a witness row whose job_id no
   longer resolves, which the next zombie-leg retires as
   transient noise). The hardening is one extra
   ``repository.get`` call per rehydrate attempt; the cost is
   bounded by the GC sweep rate.

2. **Defer-on-error instead of permanent release in the
   legitimate-consume arm.** The ``rehydrated`` terminal GC
   state currently transitions straight to ``released`` on a
   downstream failure (the durable witness row was published,
   the carrier was consumed — the held row has done its job).
   On a transient downstream error (e.g. notify_watchers raised
   but the witness row committed) the row is permanently
   released and the at-least-once guarantee relies on the
   observer's parallel publish. Switching the
   transient-failure branch to a deferred state (with the same
   sweep-cadence re-attempt the U1 GC leg already runs) makes
   the held row's lifecycle self-healing without changing the
   at-least-once contract.

3. **``ON CONFLICT DO NOTHING`` on the rehydrate INSERT.** The
   quarantine table already uses ``ON CONFLICT DO NOTHING`` on
   the first-store-wins fence (``turn_transitions.py:806-808``);
   the rehydrate INSERT into ``stored_watches`` currently
   raises on conflict (the carrier leg re-enters with the same
   ``stored_watch_id`` after a daemon restart). Adopting the
   same idempotent-insert idiom on the rehydrate path closes
   the post-restart ``IntegrityError``-abort class and makes
   the GC leg restart-DURABLE without an external recovery
   step. Cost: one SQL token; benefit: a row that survives a
   daemon death mid-GC does not need a sweep to clear the
   IntegrityError footprint.

**Why deferred.** The trio is hardening, not a defect class. The
GC leg already self-heals via the 300s sweep cadence (any of
the three failure modes clears on the next tick) — these are
operator-observability and restart-durability refinements, not
silent-loss closures. Out of scope for a watch-loss fix
commission; recorded so the next GC-touching commission
(likely the watch-row audit/history table from entry (a), or
the dual-key migration from the related out-of-scope list)
sees them in one place.

**Trigger to revisit.** Any of: (i) the GC leg enters a
sustained error rate (sweep logs show the same shape ≥3
consecutive cycles); (ii) a daemon-restart-during-GC RCA
opens; (iii) the audit/history table (entry (a)) design
commission lands — adopt the trio inside the same migration
since the schema surface is shared.

## (f) E2 witness rows inherit event-table retention semantics if an event-table TTL ever lands

The E2 (fix e8a61da3, Option E phase 2) durable
``JOB_COMPLETED`` witness rows are published via the EventBus
durable lane — they land in the same ``event`` table the
observer's natural-path producer uses, with ``EventKind.
JOB_COMPLETED`` + the same payload shape (job_id, status,
``source: "job_recovery_service.pattern_f"``). Today the
``event`` table has no TTL — the rows are durable for the
lifetime of the row's parent (the cap is the row's
parent-instance lifetime, not a time-based retention policy).

**The risk to flag.** If an event-table TTL ever lands (a
plausible future ergonomics / storage-pressure commission), the
witness rows MUST be on the exempt list — the recovery
finalize's whole job-side trace depends on them surviving past
the natural-path observer's parallel-publish window. A
time-based eviction that catches the recovery witness first
(but not the observer's row) re-opens a partial-trace class
identical in shape to the 043d64a8 ``(ii) witness no-publish``
failure. The same exemption covers the ``child_reports``
``mid_flight_report`` events + the question/answer channel
events — the durable record of a recovery decision must outlive
any retention policy that would otherwise evict it on a
time-only basis.

**Why deferred.** No event-table TTL is on the roadmap today
(retention is bounded by the row's natural parent lifecycle
and the ``CHECKPOINT_TTL_HOURS`` (default 168h) sweep covers
checkpoint, not event). The risk is a future commission's
contract decision, not a defect in this one. Recorded here so
the next event-table-touching design commission (or a
storage-pressure audit) sees the exemption requirement before
the eviction policy is finalized.

**Trigger to revisit.** Any of: (i) a storage-pressure
commission proposes an event-table TTL; (ii) an
operator-observability gap opens around missing witness rows
on long-running recovery missions; (iii) a witness-row
eviction pattern surfaces in incident RCAs. The action is
trivial — add ``event_kind IN ('JOB_COMPLETED',
'JOB_COMPLETED_RECOVERY')`` (or the equivalent at the time)
to the TTL exempt list — but it MUST land BEFORE the TTL
itself, not after.
