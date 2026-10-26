# Adoption-Delta Notes — Proposal B PR3 (the final PR: routing + terminator + backfill)

Completion-subsystem Proposal B, **PR3 of 3**. PR1 shipped the record
substrate; PR2 shipped the enforce reads; **PR3 ships the routing flip,
the commission terminator, the cross-epoch hold, the committing
backfill — and closes the MAJOR-1 ruling.** The reconciliation sweep
(RDRS lane 7) is explicitly NOT here (a separate cycle owns it).

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

TTL is a knob with no hard decision taken — and the TTL mechanism lives
in the sweep (lane 7). PR3 ships NO TTL logic; nothing to wire.

## (i) The micro-fix

``daemon/tools/job_queue.py`` (job_continue gate): the inline comment
claimed "Non-JobItem work is unaffected" while the first clause is NOT
kind-restricted — a message-origin Task IS refused (PR2 runtime-gate
finding #3). Behavior matches the docstring; the comment now says so.

---

## Ship + promotion posture

* ``ENSEMBLE_MISSION_RECORDS`` stays ``dry`` (ship default; restart-read).
* The PR2 §(f) enforce-promotion gate ("DO NOT PROMOTE BEFORE THE PR3
  TERMINATOR") is now SATISFIED on the delivery side — but lane 7 (the
  ``abandoned`` escape + TTL) has NOT landed, so a commission whose
  report is never delivered still holds forever. **The enforce
  promotion should wait for lane 7** (or be taken with open eyes on the
  eternal-mission class).
* Activation of everything here = rebuild + restart (staging ≠
  activation).
