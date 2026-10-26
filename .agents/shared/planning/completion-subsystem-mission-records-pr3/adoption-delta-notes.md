# Adoption-Delta Notes — Proposal B PR3 (the final PR: routing + terminator + backfill)

Completion-subsystem Proposal B, **PR3 of 3**. PR1 shipped the record
substrate; PR2 shipped the enforce reads; **PR3 ships the routing flip,
the commission terminator, the cross-epoch hold, the committing
backfill — and closes the MAJOR-1 ruling.** The reconciliation sweep
(RDRS lane 7) landed in the FOLLOW-ON cycle of this same branch — see
section (j) below for its knobs, counters and vocabulary.

The authoritative design docs are UNTRACKED files in the MAIN checkout
(``.agents/shared/planning/completion-subsystem/``) and are deliberately
NOT copied here. This note records only what PR3 decided and shipped.

---

## (a) MAJOR-1 — ruling (c) CROSS-EPOCH HOLD (user ruling, binding)

The job_continue continuation supersedes the anchor's prior OPEN epoch;
the prior epoch's commission rows are ORPHANED (they keep
``state='outstanding'`` and keep pointing at the superseded epoch).
Ruling (c): **NO re-pointing, NO auto-supersede of the orphaned rows —
attribution stays with the owning epoch.**

Shipped shape:

* ``MissionRecordRepository.count_outstanding_commissions_for_anchor``
  — the ANCHOR-scoped read (joins ``mission_records`` ×
  ``mission_commissions``; counts ``state='outstanding'`` across the
  anchor's OPEN **and** SUPERSEDED epochs);
* ``mission_record_reads.count_outstanding_commissions`` consumes it —
  so the L3 publish hold and the R2 quiet predicate (both read the
  manager facade) release only when EVERY epoch of the anchor is
  commission-quiet;
* the fail-open ``-1`` sentinel contract is preserved EXACTLY (never a
  0 default); "anchor has no epochs at all" stays a real ``0``;
* the deliberate cost is the eternal-mission class (proposal-b §8) —
  the escape valve is the reconciliation sweep + its TTL backstop
  (lane 7, next cycle).

Mandatory pin:
``tests/unit/test_mission_record_pr3_cross_epoch_hold.py`` — the
live-captured orphan scenario (PR2 runtime-gate item 4) built so the L3
gate is actually REACHED (PR2 caveat (e) respected: a live non-terminal
descendant is seeded and the hold reason names ``mission_commissions=1``,
proving the mission leg fired before the liveness leg could defer).

## (b) Multi-commissioner tie-break — ruling ④ (oldest-outstanding)

The routing leg takes the OLDEST outstanding row (``created_at ASC``) —
the same deterministic order the rung-2 resolution always used, now
carrying ROUTING authority. Pinned:
``test_multi_commissioner_takes_the_oldest_outstanding``.

## (c) The D1 routing flip — taught INSIDE the resolver

``resolve_report_recipient`` gained the commission-first leg internally
(the design's "teach the RESOLUTION, not the downstream guards"): all
three production call sites (child_reports producer, pipeline Site-1
marker, RDRS lane 2) inherit it with ZERO call-site churn, and every
recipient-aware guard downstream keeps reading the resolved recipient.
The A-resolver (audit-recency heuristic) is the DECLARED LEGACY
FALLBACK leg — kept, byte-identical, demoted in the module docstring
("ADOPTION DELTA (a) — … AMENDED PR3").

* LEG 1 (enforce-gated): the child's OLDEST outstanding commission row
  (``advisory_backfill=False`` — EXCLUDED from routing authority
  FOREVER) → ``commissioner_instance_id``.
* LEG 2: the audit heuristic (unchanged; still reads its own
  ``instance_ops_audit`` ledger by its own PR1/PR2 rules — the advisory
  exclusion ruling governs the commission-ledger reads, not the
  pre-record heuristic).
* LEG 3: the structural parent.
* ``dry``/``off``: byte-for-byte the Phase-1 behavior.
* ``resolve_report_recipient_with_commission`` (NEW) exposes the
  deciding row for the ``report_injections`` provenance writers + the
  terminator.

## (d) The C3 duplication firewall (same-PR mandate)

The no-row backstop's admission predicate is CHILD-KEYED (since
``4aa1bb690``) — a mission-routed delivery ALREADY counts as delivered;
lane 2's resolution names the commissioner so its evidence guards query
the right queue. The comment block at ``report_delivery_recovery``
(:~1468-1500) documenting "teach the resolution" is preserved +
extended. Pinned end-to-end:
``TestC3CrossParentFirewall`` (delivered-not-readmitted;
undelivered-still-admitted).

## (e) The commission TERMINATOR (the enforce-promotion precondition)

``satisfy_commission`` has a PRODUCTION caller: the natural completion
write path satisfies the routing commission when the report is actually
written + addressed to its commissioner — guarded
``UPDATE ... WHERE state='outstanding'``, ATOMIC with the delivery
write (rides the WriteGuardSession via a SAVEPOINT), FAIL-OPEN (a
satisfy failure never breaks delivery), rowcount 0 = lost race counted,
never retried in place.

TERMINATOR CALL-SITE LIST (production terminal paths → terminator):

| Path | Terminator | Owner |
|---|---|---|
| Natural completion write path (report written + addressed to the commissioner) | ``satisfied`` | **PR3 (this train)** |
| Deferred-marker / recovered-marker deliveries (lanes 1/3/4 + drain claims) | ``satisfied`` on delivery evidence | RDRS lane 7 (next cycle) |
| Worker-terminal / TTL / no-report-ever | ``abandoned`` (fail-MISS probes) | RDRS lane 7 (next cycle) |
| Epoch supersession | NOTHING (ruling (c) — rows keep pointing at their epoch) | — |

``abandon_commission`` still has ZERO production callers; ``superseded``
stays mint-lane-only (zero callers — ruling (c)).

## (f) The committing backfill (mode-gated) + retention story

Boot run mode-gated per proposal-b §5.2 + PR1 notes §(b): ``off``
skips; ``dry`` keeps the PR1/PR2 dry-run preview; ``enforce`` runs
``dry_run=False`` (COMMITTING; idempotent on re-boot via the
deterministic UUID5 ids + the epoch-0 probe). RETENTION: advisory rows
are DURABLE by design (the historical ledger; no pruning/TTL) —
audit-only because they are excluded from EVERY commission-consulting
read by the ONE consistent ``advisory_backfill=False`` predicate,
pinned across routing + census + quiet/L3 from a committed run
(``tests/unit/test_mission_record_pr3_backfill_commit.py``).

## (g) The census scope ruling (documented, NOT widened)

The attestation CENSUS (``read_mission_census``) STAYS OPEN-EPOCH-SCOPED
— the ruling text names the L3 hold + the R2 quiet input only. The two
scopes coexist deliberately; pinned by
``test_the_census_stays_open_epoch_scoped``.

## (h) The TTL ⑤ ruling

TTL is a knob with no hard decision taken. The sweep (lane 7, section
(j)) landed the MECHANISM in the follow-on cycle: the knob is
``ENSEMBLE_MISSION_COMMISSION_TTL_SECONDS`` with the house default
21600s (6h) — the same horizon family as
``MISSION_LIVE_ORPHAN_TIMEOUT_SECONDS``. The DEFAULT is a starting
point, not a decision: the ``dry``-mode counters are the evidence that
tunes it.

## (i) The micro-fix

``daemon/tools/job_queue.py`` (job_continue gate): the inline comment
claimed "Non-JobItem work is unaffected" while the first clause is NOT
kind-restricted — a message-origin Task IS refused (PR2 runtime-gate
finding #3). Behavior matches the docstring; the comment now says so.

## (j) The RECONCILIATION SWEEP (RDRS lane 7) — the follow-on cycle

The escape valve for the eternal-mission class: an outstanding
``mission_commissions`` row whose commissioned work will never produce
a report holds its mission open forever (the D2(c) settle-hold refuses
to settle while ``outstanding_commissions > 0``). Lane 7 resolves the
provably-dead rows to ``abandoned`` (and the provably-delivered ones to
``satisfied``); the EXISTING settle machinery then releases the hold.

### Knobs

| Knob | Where | Default | Meaning |
|---|---|---|---|
| ``SERVICES_REPORT_DELIVERY_RECOVERY_LANE_MISSION_RECONCILIATION`` | ``ServicesConfig.report_delivery_recovery_lane_mission_reconciliation`` | ``True`` | The lane's own kill-switch (master + lane, like the other six lanes) |
| ``ENSEMBLE_MISSION_COMMISSION_TTL_SECONDS`` | ``daemon/constants.py::MISSION_COMMISSION_TTL_ENV`` (resolved by ``mission_record_service.resolve_mission_commission_ttl_seconds``) | ``21600`` (6h) | The TTL horizon — the operator lever; it OVERRIDES the config-family knob |
| ``SERVICES_REPORT_DELIVERY_RECOVERY_MISSION_COMMISSION_TTL_SECONDS`` | ``ServicesConfig.report_delivery_recovery_mission_commission_ttl_seconds`` | ``21600`` | The config-family default for the horizon |
| ``ENSEMBLE_MISSION_RECORDS`` | (the EXISTING ladder) | ``dry`` | The MODE: ``off`` → the lane is not even queried; ``dry`` → detect + counters, ZERO writes; ``enforce`` → writes. **No second mode knob.** |

The TTL resolution is restart-read (the house-switch pattern) and NEVER
raises: an invalid env value warns and falls back to the default (a
misconfigured knob must not strand every commission).

### Counters (the lane dict the crash-recovery endpoint returns verbatim)

``abandoned_worker_terminal`` · ``abandoned_ttl`` ·
``abandoned_worker_missing`` · ``satisfied_on_evidence`` ·
``skipped_live`` · ``skipped_watcher_pending`` · ``below_ttl_hold`` ·
``already_resolved`` · ``masked_suspected`` · ``probe_errors`` ·
``errors`` (+ the inherited ``recovered`` / ``skipped_*`` /
``already_recovered`` / ``orphan_disposition`` / ``skipped_already_reported``).

In ``dry`` mode the DETECTION counters move and NOTHING is written (the
dry-run evidence that tunes the TTL default); ``recovered`` stays 0
(nothing was recovered).

### Vocabulary (FROZEN)

* States written: ``satisfied`` · ``abandoned``. ``superseded`` stays
  mint-lane-only — the sweep NEVER writes it.
* Abandon reasons (the closed set): ``worker_terminal_no_report`` ·
  ``ttl_expired_not_live`` · ``worker_row_missing``.
* The resolution stamp: ``mission_commissions.resolved_at`` (TEXT ISO;
  written for EVERY resolution — abandoned OR satisfied; NULL while the
  row is outstanding).

### Posture (the binding contract)

FAIL-MISS everywhere: every probe error HOLDS the row
(``probe_errors``++, zero writes) — the sweep never resolves a
commission on a failed probe. The TTL is the ONE time-bounded
exception: past the horizon AND with a SUCCESSFUL "not live" liveness
probe the unprovable state resolves LOUDLY; a liveness-probe ERROR
still holds at any age. The sweep writes ONLY ``mission_commissions``
(no instance status, no mission_records, no report_injections, no
dependency_watchers).

### The rulings applied

* **R1b (satisfy on evidence)** — INCLUDED: positive delivery evidence
  with an outstanding row means the natural satisfy write was lost.
* **OQ-3(b) (``resolved_at``)** — ADDED: the model field, the SQLite
  migration ``20261026_060000_add_mission_commission_resolved_at.sql``
  and the PostgreSQL ``_ensure_postgres_columns`` mirror (3-site pin).
* **OQ-4 (the abandon vocabulary)** — FROZEN as above.
* **OQ-6 (the ``(state, created_at)`` index)** — DEFERRED with a perf
  note: at the expected cardinality (the outstanding set is tiny) the
  state-only filter suffices.
* **OQ-7 (the JobItem-live check)** — INCLUDED: a commission whose
  ``work_id`` resolves to a non-terminal JobItem is LIVE → HOLD.
* **Advisory rows** — EXCLUDED from the sweep admission (one
  consistent predicate with the routing / census / quiet reads); they
  are audit-only by design.

### Pins

``tests/unit/test_mission_reconciliation_sweep.py`` — the design's
T1-T9 mapped to real tests (T1 probe-error HOLD per probe, T2 the TTL
matrix, T3 R1 + R1b, T4 the guarded transition, T5 the mode ladder, T6
the write boundary, T7 the lane order / kill-switch / unwired
degrade, T8 the masked signal, T9 the endpoint shape) + the
escape-valve CHAIN at the service level (abandon → the outstanding
count reads 0 → the hold releases). T10's real-boot E2E stays the
TESTER's lane.

---

## Ship + promotion posture

* ``ENSEMBLE_MISSION_RECORDS`` stays ``dry`` (ship default; restart-read).
* The PR2 §(f) enforce-promotion gate ("DO NOT PROMOTE BEFORE THE PR3
  TERMINATOR") is now SATISFIED on the delivery side — and lane 7 (the
  ``abandoned`` escape + TTL) HAS landed (section (j)), so a commission
  whose report is never delivered no longer holds forever: the sweep
  abandons it under the fail-MISS probes + the TTL horizon.
* The enforce promotion still needs the SWEEP's own evidence: the
  ``dry``-mode counters (``abandoned_ttl`` / ``skipped_live`` /
  ``masked_suspected``) are the TTL-tuning dataset — read them BEFORE
  flipping the lane to ``enforce``.
* Activation of everything here = rebuild + restart (staging ≠
  activation).
