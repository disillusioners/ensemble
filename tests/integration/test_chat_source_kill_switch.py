"""Phase 3 / chat-source-worker-lane — Task #6 — ``USE_WORKER_POOL=false``
kill-switch test.

Pins SC#7: setting ``USE_WORKER_POOL=false`` disables BOTH pools
(default + chat) — the manager short-circuits INSIDE
``setup_worker_pool`` BEFORE any pool construct, so
``manager._worker_pool`` and ``manager._chat_worker_pool`` both
stay ``None``. A subsequent chat-prefixed message remains in
``message_queue.status='ready'`` (NOT picked up by any worker —
no worker pool exists).

The boot log emits the literal ``"Worker pool disabled"`` line
(``daemon/manager.py:6460``) — pinned via ``caplog`` in-process
capture (filesystem log parsing FORBIDDEN per Phase 2/3 Exit
Criterion #1).

Env-var discipline: ``USE_WORKER_POOL=false`` is set via
``monkeypatch.setenv`` (fixture-restored — no env leakage into
other tests). The flag is cleared on teardown to avoid leaking
into subsequent tests.
"""

from __future__ import annotations

import logging
import time

import pytest
from sqlmodel import Session, select

from daemon.constants import CHAT_WORKER_POOL_SIZE, WORKER_POOL_SIZE
from daemon.repositories.message_queue.models import MessageQueue
from daemon.repositories.task.models import Task, TaskStatus
from daemon.repositories.task.repository import set_chat_lane_active
from tests.integration.chat_source_harness import (
    build_chat_source_engine,
    build_live_pool_manager,
    seed_chat_message,
)


pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Lane-flag isolation
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_chat_lane_flag():
    set_chat_lane_active(False)
    yield
    set_chat_lane_active(False)


@pytest.fixture
def engine(tmp_path):
    eng = build_chat_source_engine(str(tmp_path / "chat_kill_switch.db"))
    yield eng
    eng.dispose()


# ---------------------------------------------------------------------------
# Test class
# ---------------------------------------------------------------------------


class TestUseWorkerPoolFalseDisablesBothPools:
    """``USE_WORKER_POOL=false`` short-circuits BOTH pool constructions."""

    def test_both_pool_slots_remain_none(
        self, engine, caplog: pytest.LogCaptureFixture
    ):
        """``manager._worker_pool`` and ``manager._chat_worker_pool``
        both stay ``None`` after ``setup_worker_pool``."""
        with caplog.at_level(logging.INFO):
            with build_live_pool_manager(
                engine, use_worker_pool="false"
            ) as manager:
                # Both slots are None — short-circuit fired.
                assert manager._worker_pool is None, (
                    "default pool was constructed despite "
                    "USE_WORKER_POOL=false"
                )
                assert manager._chat_worker_pool is None, (
                    "chat pool was constructed despite "
                    "USE_WORKER_POOL=false"
                )
                assert manager._pools == [], (
                    f"_pools should be empty when kill-switch fires, "
                    f"got {manager._pools}"
                )

    def test_boot_log_emits_worker_pool_disabled(
        self, engine, caplog: pytest.LogCaptureFixture
    ):
        """The literal ``"Worker pool disabled"`` log line is emitted
        via ``logger.info`` (``daemon/manager.py:6460``) when the
        kill-switch fires. Substring match via ``caplog``
        (in-process capture; filesystem log parsing FORBIDDEN)."""
        with caplog.at_level(logging.INFO):
            with build_live_pool_manager(
                engine, use_worker_pool="false"
            ) as manager:
                # Sanity: both slots None (sanity for the log assertion).
                assert manager._worker_pool is None
                assert manager._chat_worker_pool is None

        # The literal boot log line.
        assert "Worker pool disabled" in caplog.text, (
            f"expected 'Worker pool disabled' substring in boot log; "
            f"got: {caplog.text!r}"
        )

    def test_chat_lane_flag_stays_false(self, engine):
        """``is_chat_lane_active()`` stays False — the chat pool
        never constructed, so the B1 flag is never flipped to True
        (Phase 2 wiring). The default-pool fail-open remains
        pre-P2 behavior (the gate is a no-op until the flag flips)."""
        with build_live_pool_manager(
            engine, use_worker_pool="false"
        ) as manager:
            assert manager._worker_pool is None
            assert manager._chat_worker_pool is None
            # Flag stays False — never flipped.
            from daemon.repositories.task.repository import (
                is_chat_lane_active,
            )

            assert is_chat_lane_active() is False

    def test_chat_message_remains_unexecuted(self, engine):
        """SC#7 — a chat-prefixed message enqueued AFTER the
        kill-switch fires stays in ``message_queue.status='ready'``
        (NOT picked up by any worker). With no pool running, no
        worker can claim the task; the message stays in the queue.

        NOTE: this test seeds the message AFTER the kill-switch
        has been applied (no pool construction). A brief poll
        confirms the message is not picked up. ``wait_until`` with
        a 2s window is generous — the absence of any worker means
        the message CANNOT be claimed.
        """
        # Build the manager with kill-switch FIRST (the fixture
        # applies the env var before ``setup_worker_pool``).
        with build_live_pool_manager(
            engine, use_worker_pool="false"
        ) as manager:
            # Seed a chat row. No pool is alive — nothing should
            # claim it.
            message_id, chat_wid = seed_chat_message(
                engine,
                instance_id="inst-ks-1",
                source="telegram:alice:1",
            )

            # Give the system a moment — if there WERE a worker,
            # it would claim immediately on next poll.
            time.sleep(2.0)

            # The message_queue row should still be status=ready
            # (not 'processing' or 'completed').
            from tests.integration.chat_source_harness import (
                fetch_message_queue,
                fetch_task_by_work_id,
            )

            from daemon.repositories.message_queue.models import (
                MessageStatus,
            )

            mq_row = fetch_message_queue(engine, message_id)
            t_row = fetch_task_by_work_id(engine, chat_wid)

            # The task should still be PENDING (no worker claimed it).
            assert t_row is not None
            assert t_row.status == TaskStatus.PENDING.value, (
                f"task was claimed despite kill-switch: "
                f"status={t_row.status!r}, worker_id={t_row.worker_id!r}"
            )

            # The message_queue row should still be 'ready' (the
            # claim path would have flipped it to 'processing' or
            # moved to 'completed' on success).
            assert mq_row is not None
            assert mq_row.status == MessageStatus.READY.value, (
                f"message_queue row was processed despite kill-switch: "
                f"status={mq_row.status!r}"
            )

    def test_pool_size_constants_match_production(self, engine):
        """Sanity: the production pool sizes are 5 (default) and 2
        (chat) — the same values the kill-switch SHORT-CIRCUITS
        in ``setup_worker_pool``. Documented here so future readers
        see the kill-switch skip-the-construct behavior reference."""
        # The kill-switch test runs against the same constants.
        assert WORKER_POOL_SIZE == 5
        assert CHAT_WORKER_POOL_SIZE == 2
