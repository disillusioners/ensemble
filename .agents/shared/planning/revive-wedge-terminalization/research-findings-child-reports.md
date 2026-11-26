# Revive-Wedge Terminalization — child_reports Research Findings

Base: worktree fix/revive-wedge-terminalization @ 35ec50e3c9b3fc9a487e4f2dbad23c15a0837f45 (= origin/latest). Read-only.

## Q1 — Commit 58fc594eb: what it changed

Commit 58fc594eb (post-outage lane, b4 batch) is the single divergent change in the
v0.31.49 -> v0.31.52 regression window that matters (dependency bus, graph.py, and
report_injection/repository.py are byte-identical across that window).

It widened the report-mint guard in daemon/services/child_reports.py from ANCHOR-EXACT
to a per-child LIFETIME PREFIX. Evidence in the current code (comments at
child_reports.py:1360-1399 and 4654-4676) state the rationale verbatim:

OLD predicate (pre-widening), anchor-exact:
  source == f"internal_report:{instance_id}:{completed_message_id}"
  (i.e. MessageQueue.source == "internal_report:{child}:{message_id}")

NEW predicate (post-widening), per-child prefix:
  MessageQueue.source.startswith(f"internal_report:{instance_id}:", autoescape=True)
  combined with MessageQueue.status.in_([READY, PROCESSING, COMPLETED])
  (equivalent SQL: source LIKE 'internal_report:' || child_id || ':%'
   with autoescape neutralising %/_ inside the id).

WHY widened: a b4 stale-running candidate whose PRIOR report was still PENDING under a
DIFFERENT anchor (different completed_message_id) slipped past the anchor-exact guard,
minting a SECOND report row + a SECOND delivery obligation for the same child (probe:
2 obligations for 1 child; parent receives 2 reports). The fix moved the guard to the
MINT SITE and keyed it on the child prefix so ANY pending report for the child
suppresses a second mint. The colon terminator keeps the exact boundary (sibling id
extension does not alias) and the RECIPIENT predicate is dropped (obligation belongs
to the CHILD).

Duplicate that b4 fixed: duplicate child-report injection / duplicate obligation for one
child across multiple turns/anchors (resume-router re-delivery class).

## Q2 — Current guard at this base

Mint guard (inlined, authoritative production guard):
  file: daemon/services/child_reports.py
  function: _process_child_completion_db_sync
  lines: 4677-4696 (existing_report SELECT), early return 4698-4705.
  Predicate (Python):
    MessageQueue.source.startswith(f"internal_report:{instance_id}:", autoescape=True)
    AND MessageQueue.status.in_([MessageStatus.READY, PROCESSING, COMPLETED])
  Evidence query: session.exec(select(MessageQueue).where(...startswith...).where(...status.in_...)).first()

Sibling (dead-code) helper uses the SAME prefix guard:
  _should_send_completion_report, lines 1400-412 (existing_report SELECT).

Terminal-entry guard (separate, earlier):
  lines ~3781-3874: if instance.status in terminal set -> idempotency_skip
  (with T3 re-arm exception when live direct children exist).

Mint site (where the marker is WRITTEN):
  file: daemon/services/child_reports.py
  lines: 4959-4973
  anchor VALUE at write time: completed_message_id (the message ID that just completed,
  passed into _process_child_completion_db_sync as completed_message_id: str | None).
  format string: source=f"internal_report:{instance.instance_id}:{completed_message_id}"
  report_message_id (the wake row's own id) is a fresh uuid4 (line 4959) — a different id space.

Check site (guard): uses prefix f"internal_report:{instance_id}:" — i.e. ANY anchor.

idempotency_skip log line format (verbatim):
  "Instance {instance_id[:8]}... completion report skipped: reason={skip_reason}"
  (line 4699). Also terminal-entry: "Instance {instance_id[:8]}... already in terminal state ({instance.status}), skipping _process_child_completion_db_sync (idempotency)".

## Q3 — Three disjoint id spaces

1. BaseMessage.id (LangChain) = completed_message_id passed into the function; this is
   the ANCHOR written into source at mint time (line 4967).
2. wake-row MessageQueue.message_id = fresh uuid4 minted at line 4959 (report_message_id);
   this is the row's own primary key, independent of the anchor.
3. completed_message_id source-suffix = the anchor embedded in the source string.

Is there an id-space mismatch that MOTIVATED the prefix widening?
NO. The anchor (completed_message_id) IS reconstructible at check time — it is passed
as a parameter (line 3639) and threaded through (line 3534). The widening was NOT
forced by an inability to reconstruct the id. It was a deliberate design choice to
catch cross-turn duplicates: a prior pending report under a DIFFERENT completed_message_id
(a different turn) must still discharge the "one obligation per child" invariant. So a
per-TURN key (child + completed_message_id exact) IS reconstructible at check time —
but using it would re-open b4's duplicate-obligation hole. This is the core tension.

## Q4 — Coupling / completion path / terminalization

Landing ladder: _process_child_completion_db_sync:
  - terminal-entry guard (~3781): if already terminal -> idempotency_skip (early return)
    (T3 re-arm exception for live direct children -> deferred_waiting_children).
  - PAUSED -> deferred_pause (early return).
  - instance_not_found / no_parent_id -> idempotency_skip (early return).
  - pending_messages_exist -> deferred (marker written, early return).
  - mint guard (4677-4696): if existing report (per-child prefix, READY/PROC/COMPLETED)
    -> should_send=False, skip_reason=idempotency_skip, then EARLY RETURN 4698-4705.
  - tool invocation branch (4709): status flip to COMPLETED + return tool_invocation_completed.
  - active children / pending tasks (4859): defer (early return).
  - REGULAR PATH (4880+): status flip to COMPLETED (4901 UPDATE ... WHERE status NOT IN
    paused/completed/error), mint report row (4959-4973), wake task, report_injection,
    parent cascade.

The mint guard's early return at 4698-4705 happens BEFORE the status flip at 4901.
Therefore when the guard fires (prior report exists for this child — the revive case),
the instance is NEVER flipped to COMPLETED. It stays RUNNING forever.

Downstream consequence: _dispatch_post_commit_side_effects runs the B4 cycle-2 re-mint
(6563-6630) ONLY when inst.status == COMPLETED (line 6581). Since the instance never
becomes COMPLETED, the re-mint never fires → chicken-and-egg (the "revive-path wedge").

Minimal seam to decouple "suppress duplicate mint" from "still terminalize":
  The early-return block at 4698-4705 is the seam. The guard already distinguishes
  the mint-suppression case (skip_reason == "idempotency_skip" from existing_report).
  Fix: do NOT early-return for that specific case; instead set a flag
  suppress_mint=True and continue to the regular path's status flip (4901), while
  guarding the mint (4959-4973) and wake-task/report_injection creation with
  `if not suppress_mint:`. Terminalization (status->COMPLETED) then happens, unblocking
  the B4 re-mint (which emits the terminal via the bus) and allowing the instance to
  reach COMPLETED. This keeps b4's duplicate-mint protection (no second report row)
  while fixing terminalization.

  Exact control-flow lines to change:
    4691-4696: add `suppress_mint = True` in the existing_report branch.
    4698-4705: condition early return on `not suppress_mint`.
    4959+: guard mint with `if not suppress_mint:`.
  _ChildCompletionDbResult.outcome remains "idempotency_skip" (so _dispatch's B4
  re-mint path at 6563 still runs, and now finds inst.status==COMPLETED).

## Q5 — Existing tests

Pinning the WIDENED prefix behavior (would need to change if narrowing):
  - tests/unit/services/test_stale_running_sweep_rider_pins.py
      test_prior_pending_report_under_a_different_anchor_blocks_a_second
      test_a_genuinely_new_child_report_still_mints
      test_a_sibling_child_id_does_not_alias_onto_the_child
  - tests/unit/services/test_child_reports.py (prefix-related assertions)

Pinning b4 duplicate-obligation protection (must stay green):
  - tests/unit/services/test_b4_child_report_obligation.py
  - tests/unit/services/test_report_obligation_emission_pins.py
  - tests/postgres/test_obligation_triple_discriminator_pg.py
  - tests/postgres/test_report_deferred_savepoint_pg.py
  - tests/unit/services/test_terminal_row_status_strand.py
  - tests/repositories/test_report_injection_prefix_boundary.py (colon boundary)

## Q6 — Change surface

(a) Narrowing the guard to a per-TURN key (child + completed_message_id exact):
    Source: daemon/services/child_reports.py (_should_send_completion_report helper lines
      1360-1419 AND the inlined guard lines 4654-4696) — change startswith(prefix) back
      to exact source == f"internal_report:{instance_id}:{completed_message_id}".
    Tests: test_stale_running_sweep_rider_pins.py (3 tests above) would need updates.
    SQL: YES — the guard's evidence query changes (startswith -> equality). Also
      report_injection/repository.py find_completed_children_without_delivery and
      find_wake_already_delivered_evidence use the prefix LIKE (these are delivery-side
      and should STAY prefix — only the mint guard should narrow). So SQL touched only
      in child_reports.py's own query; repository.py unchanged.

(b) Decoupling terminalization from minting (recommended, keeps b4 protection):
    Source: daemon/services/child_reports.py only — early-return seam (4698-4705) +
      mint guard (4959+) + flag. No other source files.
    Tests: new/updated tests asserting terminalization happens on mint-suppression;
      existing b4 tests stay green (they assert no duplicate mint, which is preserved).
    SQL: NO — no repository queries or marker reads change; only control flow in
      child_reports.py. The guard query (startswith prefix) stays as-is.

    Verdict: (b) touches NO SQL; (a) touches SQL in child_reports.py only.
