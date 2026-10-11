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
