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
  M5 — post-pass ``get_stats()["processing_count"] == 0`` and the
       busy-guard's 'pending/processing counts no longer wedge' shape.
  M6 — the kill-switch (``ENSEMBLE_HARD_REBOOT_BOOT_PASS=0``) skips
       the pass entirely.
  M7 — never-wedge: a raising repository does not propagate out of the
       pass.

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
from daemon.repositories.task.models import TaskStatus
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


def test_post_pass_processing_count_is_zero_and_guard_does_not_wedge(repo, engine):
    """M5 — the stranded row leaves the 'processing' bucket.

    The busy-guard (daemon/tools/instance.py) trips on
    ``pending_count > 0 or processing_count > 0``; a stranded
    'processing' row is what wedges it. After the pass the row rests at
    'ready' (a normal, deliverable resting state), so the processing
    count is zero.
    """
    seed_instance(engine, "i1", InstanceStatus.RUNNING.value)
    seed_message(
        engine,
        "m_fresh",
        "i1",
        last_activity_at=BOOT_EPOCH - timedelta(minutes=2),
    )

    recover_stuck_messages_on_startup(repo, BOOT_EPOCH)
    stats = repo.get_stats("i1")

    assert stats["processing_count"] == 0
    # The guard's 'in progress' term is what the stranded row fed.
    assert not (stats["processing_count"] > 0)


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
