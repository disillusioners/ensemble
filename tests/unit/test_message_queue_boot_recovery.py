"""Unit tests for the message-queue boot-recovery pass (Phase 2 / G2).

Feature: hard-reboot recovery. Phase 2.

Covers the boot pass ``daemon/services/message_queue_boot_recovery.py``
and its two repository primitives on
``daemon/repositories/message_queue/repository.py``:

  M1 — boot-epoch-relative selection: a row claimed *seconds* before the
       power loss (the COMMON hard-kill shape, which the 3600s age
       predicate misses) IS selected; a row touched AFTER the boot is
       not.
  M2 — the NULL-``last_activity_at`` branch is selected too.
  M3 — the skip-guards: a row on a TERMINAL instance and a row backed
       by a RUNNING/PAUSED task are left untouched (revive hazard /
       AutoContinue owns them).
  M4 — the reset is a guarded CAS: the row flips to 'ready', a second
       pass is a no-op (reboot-loop idempotency).
  M5 — the post-pass accounting: the REAL pre/post ``get_stats()``
       counts, and the busy-guard's literal predicate
       (``pending_count > 0 or processing_count > 0``) evaluated on
       those REAL post-pass values.
  M6 — the kill-switch (``ENSEMBLE_HARD_REBOOT_BOOT_PASS=0``) skips
       the pass entirely.
  M7 — never-wedge: a raising repository does not propagate out of the
       pass.
  D19 — the lane-6 composition is MUTUALLY EXCLUSIVE: the pass and
       RDRS lane 6 never co-hold on one row (the pass's skip-guard
       excludes exactly the rows lane 6 would heal). The test pins the
       non-composition BEFORE and AFTER the pass, plus the controls
       that prove the fixture is genuinely lane-6-eligible in the live
       shape — the earlier "they compose" claim was FALSE and is
       withdrawn by D19 (``.agents/shared/planning/hard-reboot-recovery/
       c4-lane6-ruling.md`` §3).

Harness: REAL file-backed SQLite + the row seeds live in
``tests/helpers/hard_reboot_fixtures.py`` (shared with the
``test_connect_timeout`` pack — house convention, no byte-copied
harness trios). The DB is a FILE with NullPool, NOT ``:memory:``,
because the pass opens more than one connection and ``:memory:`` is
per-connection.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from daemon.repositories.instance.models import InstanceStatus
from daemon.repositories.message_queue.models import MessageStatus
from daemon.repositories.report_injection.repository import ReportInjectionRepository
from daemon.repositories.task.models import TaskStatus, TaskType
from daemon.services.message_queue_boot_recovery import (
    HARD_REBOOT_BOOT_PASS_ENV,
    recover_stuck_messages_on_startup,
)
from tests.helpers.hard_reboot_fixtures import (  # noqa: F401 — fixtures
    BOOT_EPOCH,
    engine,
    message_status,
    repo,
    seed_instance,
    seed_message,
    seed_report_injection,
    seed_task,
)


# ---------------------------------------------------------------------------
# M1 / M2 — boot-epoch-relative selection
# ---------------------------------------------------------------------------


def test_fresh_dead_row_is_selected(repo, engine):
    """M1 — a row claimed seconds before the power loss IS selected.

    This is the shape the 3600s AGE predicate misses: the row is only
    minutes old, but it predates the boot.
    """
    seed_instance(engine, "i1", InstanceStatus.RUNNING.value)
    seed_message(
        engine,
        "m_fresh",
        "i1",
        last_activity_at=BOOT_EPOCH - timedelta(minutes=2),
    )

    selected = [r.message_id for r in repo.find_stuck_messages_before(BOOT_EPOCH)]

    assert selected == ["m_fresh"]


def test_row_touched_after_the_boot_is_not_selected(repo, engine):
    """M1 — a row touched AFTER the boot is NOT selected."""
    seed_instance(engine, "i1", InstanceStatus.RUNNING.value)
    seed_message(
        engine,
        "m_new",
        "i1",
        last_activity_at=BOOT_EPOCH + timedelta(minutes=1),
    )

    assert repo.find_stuck_messages_before(BOOT_EPOCH) == []


def test_null_last_activity_row_is_selected(repo, engine):
    """M2 — the NULL-last_activity_at branch is selected."""
    seed_instance(engine, "i1", InstanceStatus.RUNNING.value)
    seed_message(engine, "m_null", "i1", last_activity_at=None)

    selected = [r.message_id for r in repo.find_stuck_messages_before(BOOT_EPOCH)]

    assert selected == ["m_null"]


# ---------------------------------------------------------------------------
# M3 — skip-guards
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "instance_status",
    [
        InstanceStatus.TERMINATED.value,
        InstanceStatus.COMPLETED.value,
        InstanceStatus.ERROR.value,
        InstanceStatus.FAILED.value,
    ],
)
def test_terminal_instance_row_is_untouched(repo, engine, instance_status):
    """M3 — revive hazard: a TERMINAL instance's row is never reset."""
    seed_instance(engine, "i_term", instance_status)
    seed_message(
        engine,
        "m_term",
        "i_term",
        last_activity_at=BOOT_EPOCH - timedelta(minutes=2),
    )

    reset = recover_stuck_messages_on_startup(repo, BOOT_EPOCH)

    assert reset == 0
    assert message_status(engine, "m_term") == MessageStatus.PROCESSING.value


@pytest.mark.parametrize("task_status", [TaskStatus.RUNNING.value, TaskStatus.PAUSED.value])
def test_live_task_row_is_untouched(repo, engine, task_status):
    """M3 — a RUNNING/PAUSED task owns its row (AutoContinue completes it)."""
    seed_instance(engine, "i1", InstanceStatus.RUNNING.value)
    seed_message(
        engine,
        "m_task",
        "i1",
        last_activity_at=BOOT_EPOCH - timedelta(minutes=2),
    )
    seed_task(engine, "w1", "i1", "m_task", task_status)

    reset = recover_stuck_messages_on_startup(repo, BOOT_EPOCH)

    assert reset == 0
    assert message_status(engine, "m_task") == MessageStatus.PROCESSING.value
    # The accounting counterpart: the row is still in the 'processing'
    # bucket, so the busy-guard's processing term still fires on it.
    assert repo.get_stats("i1")["processing_count"] == 1


# ---------------------------------------------------------------------------
# M4 / M5 — the guarded reset, idempotency, and the accounting
# ---------------------------------------------------------------------------


def test_reset_flips_the_row_to_ready(repo, engine):
    """M4 — the reset flips a stranded row to 'ready'."""
    seed_instance(engine, "i1", InstanceStatus.RUNNING.value)
    seed_message(
        engine,
        "m_fresh",
        "i1",
        last_activity_at=BOOT_EPOCH - timedelta(minutes=2),
    )

    reset = recover_stuck_messages_on_startup(repo, BOOT_EPOCH)

    assert reset == 1
    assert message_status(engine, "m_fresh") == MessageStatus.READY.value


def test_second_pass_is_a_noop(repo, engine):
    """M4 — reboot-loop idempotency: a committed reset leaves the predicate."""
    seed_instance(engine, "i1", InstanceStatus.RUNNING.value)
    seed_message(
        engine,
        "m_fresh",
        "i1",
        last_activity_at=BOOT_EPOCH - timedelta(minutes=2),
    )

    assert recover_stuck_messages_on_startup(repo, BOOT_EPOCH) == 1
    assert recover_stuck_messages_on_startup(repo, BOOT_EPOCH) == 0


def test_post_pass_accounting_moves_the_stranded_row_from_processing_to_pending(
    repo, engine
):
    """M5 — the post-pass accounting, measured on the REAL ``get_stats`` output.

    This is the M5-a assertion set of the C4 ruling
    (``c4-lane6-ruling.md`` §4, M5-a): post-pass accounting is HONEST
    (the processing bucket empties) but the busy-guard is NOT cleared
    on the shipped default (the pending bucket does not empty). The
    ruling's M5-a test body is covered here assertion-for-assertion —
    ``reset == 1``, the row is ``'ready'``, ``processing_count == 0``,
    ``pending_count == 1``, and the busy-guard predicate STILL
    satisfied — plus the pre-pass counts and the bucket-move
    invariant, so the count is seen to MOVE rather than disappear.

    ``get_stats`` (``daemon/repositories/message_queue/repository.py``)
    reports two counts per instance:

      * ``processing_count`` — rows in ``status='processing'``;
      * ``pending_count``   — rows in ``status='ready'`` (plus
        ``retrying`` rows whose ``next_retry_at`` is due).

    The busy-guard's predicate (``daemon/tools/instance.py``) is the
    disjunction ``pending_count > 0 or processing_count > 0``.

    This test pins the REAL pre-pass counts, the REAL post-pass counts,
    and the guard's literal predicate evaluated on those REAL post-pass
    values. It asserts what the shipped code actually produces — the
    reset MOVES the row from the 'processing' bucket into the 'ready'
    bucket, and 'ready' rows are counted into ``pending_count`` — so
    both counts are stated as observed and the predicate is evaluated on
    them, rather than a desired outcome being asserted in their place.
    """
    seed_instance(engine, "i1", InstanceStatus.RUNNING.value)
    seed_message(
        engine,
        "m_fresh",
        "i1",
        last_activity_at=BOOT_EPOCH - timedelta(minutes=2),
    )

    pre = repo.get_stats("i1")

    reset = recover_stuck_messages_on_startup(repo, BOOT_EPOCH)
    post = repo.get_stats("i1")

    # PRE-PASS — the stranded row sits in the 'processing' bucket.
    assert pre["processing_count"] == 1
    assert pre["pending_count"] == 0

    assert reset == 1
    assert message_status(engine, "m_fresh") == MessageStatus.READY.value

    # POST-PASS — the row left 'processing' and landed in 'ready'.
    assert post["processing_count"] == 0
    assert post["pending_count"] == 1
    # ... it MOVED between the two buckets; it was not dropped.
    assert (
        pre["processing_count"] + pre["pending_count"]
    ) == (
        post["processing_count"] + post["pending_count"]
    ) == 1

    # The busy-guard's literal predicate, evaluated on the REAL post-pass
    # values.
    processing_term = post["processing_count"] > 0
    pending_term = post["pending_count"] > 0
    assert processing_term is False  # the stranded row left 'processing'
    assert pending_term is True  # ... and landed in 'ready'
    assert (pending_term or processing_term) is True


def test_busy_guard_predicate_on_an_instance_with_no_queued_rows(repo, engine):
    """M5 — control: the guard's predicate is sensitive to the queue rows.

    The control for the assertion above: with NO queued rows both counts
    are zero and the guard's predicate evaluates to False, so the True
    in the previous test is a fact ABOUT THE ROW, not a constant.
    """
    seed_instance(engine, "i_empty", InstanceStatus.RUNNING.value)

    stats = repo.get_stats("i_empty")

    assert stats["processing_count"] == 0
    assert stats["pending_count"] == 0
    assert (stats["pending_count"] > 0 or stats["processing_count"] > 0) is False


def test_stranded_row_is_indistinguishable_from_a_freshly_queued_row(repo, engine):
    """M5 — control: after the pass a stranded row is EXACTLY a queued row.

    This is the real invariant of the pass: a row that was stranded in
    'processing' by the crash is indistinguishable, to every consumer
    (claim path, accounting, finalizer), from a row that was enqueued and
    never claimed. It is what makes the reset safe; it is also why the
    busy-guard does not clear (both rows count as pending).

    (C4 ruling §4, M5-b — the control the ruling specifies. It is
    ADDITIVE to the two controls above: those pin the guard's predicate
    on an EMPTY instance, this one pins the post-pass equivalence of a
    stranded row and a freshly queued row.)
    """
    seed_instance(engine, "i1", InstanceStatus.RUNNING.value)
    seed_instance(engine, "i2", InstanceStatus.RUNNING.value)
    seed_message(  # stranded by the crash
        engine,
        "m_stranded",
        "i1",
        last_activity_at=BOOT_EPOCH - timedelta(minutes=2),
    )
    seed_message(  # never claimed, enqueued normally
        engine,
        "m_fresh_queued",
        "i2",
        last_activity_at=None,
        status=MessageStatus.READY.value,
    )

    recover_stuck_messages_on_startup(repo, BOOT_EPOCH)

    stranded = repo.get_stats("i1")
    queued = repo.get_stats("i2")

    assert message_status(engine, "m_stranded") == MessageStatus.READY.value
    assert stranded["processing_count"] == queued["processing_count"] == 0
    assert stranded["pending_count"] == queued["pending_count"] == 1


# ---------------------------------------------------------------------------
# M6 / M7 — kill-switch and never-wedge
# ---------------------------------------------------------------------------


def test_kill_switch_zero_skips_the_pass(repo, engine, monkeypatch):
    """M6 — ENSEMBLE_HARD_REBOOT_BOOT_PASS=0 disables the pass."""
    monkeypatch.setenv(HARD_REBOOT_BOOT_PASS_ENV, "0")
    seed_instance(engine, "i1", InstanceStatus.RUNNING.value)
    seed_message(
        engine,
        "m_fresh",
        "i1",
        last_activity_at=BOOT_EPOCH - timedelta(minutes=2),
    )

    reset = recover_stuck_messages_on_startup(repo, BOOT_EPOCH)

    assert reset == 0
    assert message_status(engine, "m_fresh") == MessageStatus.PROCESSING.value


def test_kill_switch_defaults_on(repo, engine, monkeypatch):
    """M6 — the default is ON (the deliverable is zero-user-action)."""
    monkeypatch.delenv(HARD_REBOOT_BOOT_PASS_ENV, raising=False)
    seed_instance(engine, "i1", InstanceStatus.RUNNING.value)
    seed_message(
        engine,
        "m_fresh",
        "i1",
        last_activity_at=BOOT_EPOCH - timedelta(minutes=2),
    )

    assert recover_stuck_messages_on_startup(repo, BOOT_EPOCH) == 1


def test_raising_repository_does_not_propagate(repo, engine, monkeypatch):
    """M7 — never-wedge: a broken repository cannot break the boot."""
    seed_instance(engine, "i1", InstanceStatus.RUNNING.value)
    seed_message(
        engine,
        "m_fresh",
        "i1",
        last_activity_at=BOOT_EPOCH - timedelta(minutes=2),
    )

    def boom(_epoch):
        raise RuntimeError("simulated repository failure")

    monkeypatch.setattr(repo, "find_stuck_messages_before", boom)

    assert recover_stuck_messages_on_startup(repo, BOOT_EPOCH) == 0


def test_no_boot_epoch_skips_the_pass(repo, engine):
    """M7 — a failed epoch capture leaves the rows untouched."""
    seed_instance(engine, "i1", InstanceStatus.RUNNING.value)
    seed_message(
        engine,
        "m_fresh",
        "i1",
        last_activity_at=BOOT_EPOCH - timedelta(minutes=2),
    )

    assert recover_stuck_messages_on_startup(repo, None) == 0
    assert message_status(engine, "m_fresh") == MessageStatus.PROCESSING.value


# ---------------------------------------------------------------------------
# D19 — the lane-6 composition (MUTUAL EXCLUSIVITY, documented)
# ---------------------------------------------------------------------------


def _seed_lane6_wake_shape(engine, *, task_status: str, row_status: str) -> None:
    """Seed the live RDRS lane-6 wake shape.

    The shape (per ``report_injection/repository.py:1980-2012`` and the
    PG lane-6 test at ``tests/postgres/test_report_delivery_recovery_pg.py``):

      * parent instance ``p1`` (running) and child instance ``c1`` (completed);
      * the wake MESSAGE row ``wake-msg-1`` on the parent, sourced
        ``internal_report:c1:child-msg-1`` — the colon-form the lane-6
        join matches on (``internal_report:{child}:%``);
      * the wake TASK ``wake-task-1`` on the parent: task_type
        ``process_report``, bound to the wake row's message id, with a
        stale heartbeat (lane 6 requires a stale heartbeat);
      * the PENDING report-injection obligation marker (the triple
        ``(p1, c1, child-msg-1)``).
    """
    seed_instance(engine, "p1", InstanceStatus.RUNNING.value)
    seed_instance(engine, "c1", InstanceStatus.COMPLETED.value)
    seed_message(
        engine,
        "wake-msg-1",
        "p1",
        status=row_status,
        source="internal_report:c1:child-msg-1",
        last_activity_at=BOOT_EPOCH - timedelta(minutes=2),
    )
    seed_task(
        engine,
        "wake-task-1",
        "p1",
        "wake-msg-1",
        task_status,
        task_type=TaskType.PROCESS_REPORT.value,
        last_heartbeat_at=BOOT_EPOCH - timedelta(minutes=2),
    )
    seed_report_injection(
        engine,
        injection_id="inj-1",
        parent_instance_id="p1",
        child_instance_id="c1",
        child_message_id="child-msg-1",
        content="child report content",
    )


def test_lane6_composition_is_mutually_exclusive_documented(repo, engine):
    """D19 — the MQ boot pass and RDRS lane 6 are MUTUALLY EXCLUSIVE for one row.

    Lane 6 heals a wake row that is 'ready' while its wake task is 'running'
    (report_injection/repository.py:2122-2147). The boot pass flips 'processing'
    -> 'ready' ONLY for rows whose owning task is NOT 'running'/'paused'
    (message_queue/repository.py:470-471) — INV-1, queue ownership. A row that
    lane 6 could heal is therefore skipped by the pass, and a row the pass flips
    can never enter lane 6's predicate. The earlier architecture-recommendation
    claim that the two 'compose' was FALSE and is withdrawn by D19.
    Narrowing the skip-guard to force the composition would re-arm the row while
    its task is still running -> the message is delivered twice.
    """
    rdrs_repo = ReportInjectionRepository(engine)
    _seed_lane6_wake_shape(
        engine, task_status=TaskStatus.RUNNING.value, row_status=MessageStatus.PROCESSING.value
    )

    # (1) BEFORE the pass — the wake row is 'processing', lane 6 needs 'ready'.
    assert rdrs_repo.find_stuck_wake_candidates(limit=10) == []

    # (2) the pass does NOT flip the row (its task is 'running' -> skip-guard).
    #     This is the INV-1 assertion.
    assert recover_stuck_messages_on_startup(repo, BOOT_EPOCH) == 0

    # (3) the row is untouched.
    assert message_status(engine, "wake-msg-1") == MessageStatus.PROCESSING.value

    # (4) AFTER the pass — still no candidate. The composition does NOT happen.
    assert rdrs_repo.find_stuck_wake_candidates(limit=10) == []

    # (5) the row is still counted as in-progress — the GUARD, not the pass,
    #     is what holds it.
    assert repo.get_stats("p1")["processing_count"] == 1


def test_lane6_composition_control_the_guard_is_what_holds_the_row(repo, engine):
    """D19 — control: the guard (not a malformed fixture) holds the row.

    The same fixture, with the wake task flipped to 'pending' so the
    skip-guard no longer applies: the pass DOES flip the row to
    'ready'. So the empty lane-6 candidate list in the test above is
    caused by the GUARD, not by a broken fixture.

    And lane 6 still cannot match the flipped row, because lane 6
    requires ``tk.status='running'`` — the second half of the mutual
    exclusivity: the pass's target class and lane 6's target class
    never overlap.
    """
    rdrs_repo = ReportInjectionRepository(engine)
    _seed_lane6_wake_shape(
        engine, task_status=TaskStatus.PENDING.value, row_status=MessageStatus.PROCESSING.value
    )

    # The guard no longer applies -> the row IS flipped.
    assert recover_stuck_messages_on_startup(repo, BOOT_EPOCH) == 1
    assert message_status(engine, "wake-msg-1") == MessageStatus.READY.value

    # ... and lane 6 still does not match it (tk.status must be 'running').
    assert rdrs_repo.find_stuck_wake_candidates(limit=10) == []


def test_lane6_live_shape_proves_the_fixture_is_lane6_eligible(repo, engine):
    """D19 — positive control: the fixture IS lane-6-eligible in the live shape.

    The counterfactual that closes the "vacuous fixture" gap: with the
    wake row 'ready' and its task 'running' (the shape lane 6's own
    producer creates — the claim-window shape, preserved by the F-1
    epoch-belt), the SAME fixture yields exactly one lane-6 candidate.

    So the empty candidate list in
    ``test_lane6_composition_is_mutually_exclusive_documented`` is
    caused by the mutual exclusivity, not by a fixture that could
    never match.
    """
    rdrs_repo = ReportInjectionRepository(engine)
    _seed_lane6_wake_shape(
        engine, task_status=TaskStatus.RUNNING.value, row_status=MessageStatus.READY.value
    )

    candidates = rdrs_repo.find_stuck_wake_candidates(limit=10)

    assert len(candidates) == 1
    assert candidates[0]["wake_message_id"] == "wake-msg-1"
    assert candidates[0]["parent_instance_id"] == "p1"
    assert candidates[0]["child_instance_id"] == "c1"
    assert candidates[0]["child_message_id"] == "child-msg-1"
