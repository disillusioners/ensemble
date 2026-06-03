"""Unit tests for ``daemon.migrations.checkpoint_migrator.CheckpointMigrator``.

The checkpoint migrator copies langgraph checkpoints from a SQLite
saver (``AsyncSqliteSaver``) to a Postgres saver
(``AsyncPostgresSaver``) using their respective APIs. These tests
exercise the public ``migrate_checkpoints`` and the per-thread
``_migrate_thread`` / ``_migrate_checkpoint`` helpers with mocked
savers — no real database needed.

Why mock the savers?
* The real ``AsyncSqliteSaver`` requires an ``aiosqlite`` connection.
* The real ``AsyncPostgresSaver`` requires a running Postgres server.
* The contract under test is the orchestrator's behaviour: how it
  threads config, handles cancellation, validates ``channel_versions``,
  and groups pending writes by ``task_id``.

Test design
-----------
We build lightweight MagicMock stand-ins that satisfy the small slice
of the saver contract that ``CheckpointMigrator`` actually uses:

* ``lock``     - async context manager (so ``async with saver.lock:`` works)
* ``conn.execute`` - returns a cursor with ``fetchall``
* ``alist(config)`` - async generator yielding ``CheckpointTuple``-like
  objects with ``config``, ``checkpoint``, ``metadata``,
  ``parent_config`` and ``pending_writes`` attributes
* ``aput(...)``  - returns a saved config dict
* ``aput_writes(...)`` - just an ``AsyncMock``
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from daemon.migrations import MigrationCancelledError
from daemon.migrations.checkpoint_migrator import CheckpointMigrator


# ──────────────────────────────────────────────────────────────────────────────
# Helpers: mock checkpoint tuple + saver
# ──────────────────────────────────────────────────────────────────────────────


def make_checkpoint_tuple(
    *,
    thread_id: str = "t1",
    checkpoint_id: str | None = "ck-1",
    channel_values: dict[str, Any] | None = None,
    channel_versions: dict[str, Any] | None = None,
    parent_config: dict[str, Any] | None = None,
    pending_writes: list[tuple[str, str, Any]] | None = None,
    checkpoint_ns: str = "",
) -> Any:
    """Build a CheckpointTuple-like object for testing.

    The migrator reads these attributes: ``config``, ``checkpoint``,
    ``metadata``, ``parent_config``, ``pending_writes``. We use a
    simple ``MagicMock`` configured to return those values as attribute
    access.
    """
    configurable: dict[str, Any] = {"thread_id": thread_id, "checkpoint_ns": checkpoint_ns}
    if checkpoint_id is not None:
        configurable["checkpoint_id"] = checkpoint_id

    cv = channel_values if channel_values is not None else {"messages": ["hello"]}
    cver = channel_versions if channel_versions is not None else {"messages": 1}

    tup = MagicMock()
    tup.config = {"configurable": configurable}
    tup.checkpoint = {
        "v": 1,
        "id": checkpoint_id or "root",
        "channel_values": cv,
        "channel_versions": cver,
    }
    tup.metadata = {"source": "input", "step": 0, "writes": None}
    tup.parent_config = parent_config
    tup.pending_writes = pending_writes or []
    return tup


def make_saver_mock(
    *,
    thread_ids: list[str] | None = None,
    checkpoint_tuples_by_thread: dict[str, list[Any]] | None = None,
    aput_return: dict[str, Any] | None = None,
) -> MagicMock:
    """Build a saver stand-in.

    Args:
        thread_ids: Distinct thread IDs the saver should report.
        checkpoint_tuples_by_thread: Map of thread_id -> list of
            ``make_checkpoint_tuple`` outputs to yield from ``alist``.
        aput_return: Config dict to return from ``aput``.
    """
    thread_ids = thread_ids or []
    checkpoint_tuples_by_thread = checkpoint_tuples_by_thread or {}

    saver = MagicMock()
    saver.name = "test-saver"

    # ``lock`` is used as ``async with saver.lock:`` — must be a real
    # async context manager.
    saver.lock = _AsyncContextManager()

    # ``conn.execute`` returns a cursor with ``fetchall``.
    cursor = MagicMock()
    cursor.fetchall = AsyncMock(
        return_value=[(tid,) for tid in thread_ids]
    )
    saver.conn.execute = AsyncMock(return_value=cursor)

    # ``alist`` is an async generator function.
    async def alist(config):
        thread_id = config["configurable"]["thread_id"]
        for tup in checkpoint_tuples_by_thread.get(thread_id, []):
            yield tup

    saver.alist = alist

    # ``aput`` returns the saved config (mock returns the same dict).
    saver.aput = AsyncMock(
        return_value=aput_return
        or {
            "configurable": {
                "thread_id": "t1",
                "checkpoint_id": "ck-1",
            }
        }
    )

    # ``aput_writes`` is a generic AsyncMock; tests can inspect call args.
    saver.aput_writes = AsyncMock(return_value=None)

    return saver


class _AsyncContextManager:
    """Tiny async context manager for ``with saver.lock:`` patterns."""

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None


# ──────────────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def cancel_event() -> threading.Event:
    """Fresh cancel event for each test."""
    return threading.Event()


@pytest.fixture
def log_callback() -> MagicMock:
    """Mock log callback that records every invocation."""
    return MagicMock()


@pytest.fixture
def migrator(cancel_event, log_callback) -> CheckpointMigrator:
    """A fresh CheckpointMigrator with a mock log callback."""
    return CheckpointMigrator(
        cancel_event=cancel_event,
        log_callback=log_callback,
    )


# ──────────────────────────────────────────────────────────────────────────────
# Constructor + state
# ──────────────────────────────────────────────────────────────────────────────


class TestConstructorAndState:
    """``CheckpointMigrator`` initialises cleanly with sane defaults."""

    def test_initial_state(self, cancel_event):
        """A fresh migrator has an empty failed-checkpoints list."""
        m = CheckpointMigrator(cancel_event=cancel_event)
        assert m.failed_checkpoints == []
        assert m._cancel_event is cancel_event

    def test_optional_log_callback(self, cancel_event):
        """``log_callback`` is optional and defaults to None."""
        m = CheckpointMigrator(cancel_event=cancel_event)
        assert m._log_callback is None

    def test_failed_checkpoints_reset_per_run(self, migrator):
        """Each ``migrate_checkpoints`` call resets ``failed_checkpoints``."""
        migrator.failed_checkpoints.append(("t1", "ck-1", "stale"))
        # Next call resets the list before starting work.
        # The actual run will fail because the saver is a MagicMock, but
        # we can verify the reset happened before the run.
        # We use empty thread_ids so the call short-circuits.
        saver = make_saver_mock(thread_ids=[])
        asyncio.run(migrator.migrate_checkpoints(saver, saver))
        # The stale entry was wiped.
        assert migrator.failed_checkpoints == []


# ──────────────────────────────────────────────────────────────────────────────
# Empty / no-op cases
# ──────────────────────────────────────────────────────────────────────────────


class TestEmptyMigration:
    """An empty source checkpointer is a no-op."""

    @pytest.mark.asyncio
    async def test_no_thread_ids_returns_zero(self, migrator):
        """With no thread IDs, the migrator returns 0 and logs the empty case."""
        sqlite = make_saver_mock(thread_ids=[])
        pg = make_saver_mock()

        count = await migrator.migrate_checkpoints(sqlite, pg)

        assert count == 0
        # No calls into PG at all.
        pg.aput.assert_not_called()
        pg.aput_writes.assert_not_called()


# ──────────────────────────────────────────────────────────────────────────────
# Single-thread, single-checkpoint happy path
# ──────────────────────────────────────────────────────────────────────────────


class TestSingleCheckpointMigration:
    """A single checkpoint gets migrated end-to-end."""

    @pytest.mark.asyncio
    async def test_migrate_single_checkpoint(self, migrator):
        """One checkpoint on one thread → 1 migrated, 0 failed."""
        tup = make_checkpoint_tuple(
            thread_id="t1",
            checkpoint_id="ck-1",
            parent_config={
                "configurable": {"thread_id": "t1", "checkpoint_id": "ck-0"}
            },
        )
        sqlite = make_saver_mock(
            thread_ids=["t1"],
            checkpoint_tuples_by_thread={"t1": [tup]},
        )
        pg = make_saver_mock()

        count = await migrator.migrate_checkpoints(sqlite, pg)

        assert count == 1
        assert migrator.failed_checkpoints == []
        pg.aput.assert_awaited_once()
        pg.aput_writes.assert_not_called()  # no pending writes

    @pytest.mark.asyncio
    async def test_root_checkpoint_omits_parent_id(self, migrator):
        """A root checkpoint (no parent) doesn't include checkpoint_id in write_config."""
        tup = make_checkpoint_tuple(
            thread_id="t1",
            checkpoint_id="ck-root",
            parent_config=None,
        )
        sqlite = make_saver_mock(
            thread_ids=["t1"],
            checkpoint_tuples_by_thread={"t1": [tup]},
        )
        pg = make_saver_mock()

        await migrator.migrate_checkpoints(sqlite, pg)

        # Inspect the call to aput.
        call_args = pg.aput.call_args
        write_config = call_args.args[0]
        # The write_config should NOT include ``checkpoint_id`` for the
        # root — the migrator strips it to signal "this is a root".
        assert "checkpoint_id" not in write_config["configurable"]
        assert write_config["configurable"]["thread_id"] == "t1"

    @pytest.mark.asyncio
    async def test_non_root_includes_parent_id(self, migrator):
        """A non-root checkpoint passes checkpoint_id from parent_config."""
        tup = make_checkpoint_tuple(
            thread_id="t1",
            checkpoint_id="ck-1",
            parent_config={
                "configurable": {"thread_id": "t1", "checkpoint_id": "ck-0"}
            },
        )
        sqlite = make_saver_mock(
            thread_ids=["t1"],
            checkpoint_tuples_by_thread={"t1": [tup]},
        )
        pg = make_saver_mock()

        await migrator.migrate_checkpoints(sqlite, pg)

        write_config = pg.aput.call_args.args[0]
        assert write_config["configurable"].get("checkpoint_id") == "ck-0"


# ──────────────────────────────────────────────────────────────────────────────
# channel_versions validation
# ──────────────────────────────────────────────────────────────────────────────


class TestChannelVersionsValidation:
    """Empty ``channel_versions`` is a data-loss signal — log a warning."""

    @pytest.mark.asyncio
    async def test_empty_channel_versions_with_non_primitive_logs_warning(
        self, migrator, log_callback
    ):
        """Empty channel_versions + non-primitive channel_values → warn."""
        tup = make_checkpoint_tuple(
            thread_id="t1",
            checkpoint_id="ck-1",
            channel_values={"messages": [{"role": "user", "content": "x"}]},
            channel_versions={},  # empty
            parent_config={
                "configurable": {"thread_id": "t1", "checkpoint_id": "ck-0"}
            },
        )
        sqlite = make_saver_mock(
            thread_ids=["t1"],
            checkpoint_tuples_by_thread={"t1": [tup]},
        )
        pg = make_saver_mock()

        await migrator.migrate_checkpoints(sqlite, pg)

        # A warning was emitted about the data-loss risk.
        warning_msgs = [
            c.kwargs.get("message", "")
            for c in log_callback.call_args_list
            if c.kwargs.get("level") == "warning"
        ]
        assert any(
            "empty channel_versions" in m and "ck-1" in m for m in warning_msgs
        ), log_callback.call_args_list

    @pytest.mark.asyncio
    async def test_empty_channel_versions_with_primitives_no_warning(
        self, migrator, log_callback
    ):
        """Empty channel_versions + primitive channel_values → no warning.

        Primitives don't go to checkpoint_blobs, so the data-loss risk
        doesn't apply. The migrator should not warn.
        """
        tup = make_checkpoint_tuple(
            thread_id="t1",
            checkpoint_id="ck-1",
            channel_values={"counter": 42, "name": "ok"},
            channel_versions={},  # empty
        )
        sqlite = make_saver_mock(
            thread_ids=["t1"],
            checkpoint_tuples_by_thread={"t1": [tup]},
        )
        pg = make_saver_mock()

        await migrator.migrate_checkpoints(sqlite, pg)

        warning_msgs = [
            c.kwargs.get("message", "")
            for c in log_callback.call_args_list
            if c.kwargs.get("level") == "warning"
        ]
        assert not any("empty channel_versions" in m for m in warning_msgs)

    @pytest.mark.asyncio
    async def test_empty_channel_versions_with_pending_writes_logs_warning(
        self, migrator, log_callback
    ):
        """Empty channel_versions + non-empty pending_writes → warn.

        pending_writes is itself a non-primitive data flow; combined with
        empty channel_versions, the operator should investigate.
        """
        tup = make_checkpoint_tuple(
            thread_id="t1",
            checkpoint_id="ck-1",
            channel_values={"counter": 1},  # primitive
            channel_versions={},  # empty
            pending_writes=[("task-1", "channel", "value")],
        )
        sqlite = make_saver_mock(
            thread_ids=["t1"],
            checkpoint_tuples_by_thread={"t1": [tup]},
        )
        pg = make_saver_mock()

        await migrator.migrate_checkpoints(sqlite, pg)

        warning_msgs = [
            c.kwargs.get("message", "")
            for c in log_callback.call_args_list
            if c.kwargs.get("level") == "warning"
        ]
        assert any("empty channel_versions" in m for m in warning_msgs)


# ──────────────────────────────────────────────────────────────────────────────
# Pending writes
# ──────────────────────────────────────────────────────────────────────────────


class TestPendingWrites:
    """Pending writes are grouped by task_id and forwarded to aput_writes."""

    @pytest.mark.asyncio
    async def test_pending_writes_grouped_by_task_id(self, migrator):
        """Writes to multiple task_ids → aput_writes called once per task."""
        tup = make_checkpoint_tuple(
            thread_id="t1",
            checkpoint_id="ck-1",
            pending_writes=[
                ("task-A", "channel1", "v1"),
                ("task-A", "channel2", "v2"),
                ("task-B", "channel1", "v3"),
            ],
        )
        sqlite = make_saver_mock(
            thread_ids=["t1"],
            checkpoint_tuples_by_thread={"t1": [tup]},
        )
        pg = make_saver_mock()

        await migrator.migrate_checkpoints(sqlite, pg)

        # aput called once for the checkpoint itself.
        pg.aput.assert_awaited_once()
        # aput_writes called twice (once per task).
        assert pg.aput_writes.await_count == 2

        # Collect the task_ids that were used in aput_writes calls.
        task_ids_used = {
            call.kwargs.get("task_id") for call in pg.aput_writes.await_args_list
        }
        assert task_ids_used == {"task-A", "task-B"}

    @pytest.mark.asyncio
    async def test_no_pending_writes_skips_aput_writes(self, migrator):
        """Without pending writes, aput_writes is never called."""
        tup = make_checkpoint_tuple(
            thread_id="t1",
            checkpoint_id="ck-1",
            pending_writes=[],
        )
        sqlite = make_saver_mock(
            thread_ids=["t1"],
            checkpoint_tuples_by_thread={"t1": [tup]},
        )
        pg = make_saver_mock()

        await migrator.migrate_checkpoints(sqlite, pg)

        pg.aput.assert_awaited_once()
        pg.aput_writes.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_pending_writes_paired_with_correct_config(self, migrator):
        """aput_writes receives the saved_config from aput (new checkpoint id)."""
        # aput returns a config with a new checkpoint_id assigned by the saver.
        saved_config = {
            "configurable": {"thread_id": "t1", "checkpoint_id": "ck-1-saved"}
        }
        tup = make_checkpoint_tuple(
            thread_id="t1",
            checkpoint_id="ck-1",
            pending_writes=[("task-1", "ch", "val")],
        )
        sqlite = make_saver_mock(
            thread_ids=["t1"],
            checkpoint_tuples_by_thread={"t1": [tup]},
        )
        pg = make_saver_mock(aput_return=saved_config)

        await migrator.migrate_checkpoints(sqlite, pg)

        # The aput_writes call should use the saved_config.
        write_config_used = pg.aput_writes.await_args.args[0]
        assert write_config_used == saved_config


# ──────────────────────────────────────────────────────────────────────────────
# Multi-thread
# ──────────────────────────────────────────────────────────────────────────────


class TestMultiThread:
    """Checkpoints from multiple threads are migrated in sequence."""

    @pytest.mark.asyncio
    async def test_migrate_two_threads(self, migrator):
        """Two threads, each with one checkpoint, both migrate."""
        t1_tup = make_checkpoint_tuple(thread_id="thread-1", checkpoint_id="ck-1a")
        t2_tup = make_checkpoint_tuple(thread_id="thread-2", checkpoint_id="ck-2a")

        sqlite = make_saver_mock(
            thread_ids=["thread-1", "thread-2"],
            checkpoint_tuples_by_thread={
                "thread-1": [t1_tup],
                "thread-2": [t2_tup],
            },
        )
        pg = make_saver_mock()

        count = await migrator.migrate_checkpoints(sqlite, pg)

        assert count == 2
        assert pg.aput.await_count == 2

    @pytest.mark.asyncio
    async def test_tuples_reversed_oldest_first(self, migrator):
        """alist returns newest-first; the migrator reverses to oldest-first.

        alist() typically orders by checkpoint_id DESC, so the list comes
        in reverse chronological order. The migrator reverses to put
        parents before children.
        """
        # We hand alist the tuples in newest-first order.
        latest = make_checkpoint_tuple(thread_id="t1", checkpoint_id="ck-latest")
        middle = make_checkpoint_tuple(thread_id="t1", checkpoint_id="ck-middle")
        earliest = make_checkpoint_tuple(thread_id="t1", checkpoint_id="ck-earliest")

        sqlite = make_saver_mock(
            thread_ids=["t1"],
            checkpoint_tuples_by_thread={"t1": [latest, middle, earliest]},
        )
        pg = make_saver_mock()

        await migrator.migrate_checkpoints(sqlite, pg)

        # After reverse, aput should be called in oldest-first order.
        # Capture the order via side_effect.
        call_order: list[str] = []
        original_aput = pg.aput.side_effect

        async def tracking_aput(*args, **kwargs):
            # args[0] is the write_config; args[1] is the checkpoint dict.
            ck_id = args[1].get("id")
            call_order.append(ck_id)
            return {"configurable": {"thread_id": "t1", "checkpoint_id": ck_id}}

        pg.aput = AsyncMock(side_effect=tracking_aput)

        # Re-run with the tracking aput.
        await migrator.migrate_checkpoints(sqlite, pg)

        # The order should be earliest → middle → latest.
        assert call_order == ["ck-earliest", "ck-middle", "ck-latest"]


# ──────────────────────────────────────────────────────────────────────────────
# Cancellation
# ──────────────────────────────────────────────────────────────────────────────


class TestCancellation:
    """``cancel_event`` raised mid-run aborts the migration cooperatively."""

    @pytest.mark.asyncio
    async def test_cancel_before_run_raises(self, migrator, cancel_event):
        """If the event is set before the run starts, the first thread raises."""
        cancel_event.set()
        tup = make_checkpoint_tuple(thread_id="t1", checkpoint_id="ck-1")
        sqlite = make_saver_mock(
            thread_ids=["t1"],
            checkpoint_tuples_by_thread={"t1": [tup]},
        )
        pg = make_saver_mock()

        with pytest.raises(MigrationCancelledError):
            await migrator.migrate_checkpoints(sqlite, pg)

    @pytest.mark.asyncio
    async def test_cancel_mid_run_raises(self, migrator, cancel_event):
        """Setting the event between checkpoints aborts the run."""
        # Build 3 checkpoints; we cancel after the first aput completes.
        tuples = [
            make_checkpoint_tuple(thread_id="t1", checkpoint_id=f"ck-{i}")
            for i in range(3)
        ]
        sqlite = make_saver_mock(
            thread_ids=["t1"],
            checkpoint_tuples_by_thread={"t1": tuples},
        )
        pg = make_saver_mock()

        aput_call_count = {"n": 0}

        async def cancel_on_second_call(*args, **kwargs):
            aput_call_count["n"] += 1
            if aput_call_count["n"] == 1:
                cancel_event.set()
            return {"configurable": {"thread_id": "t1", "checkpoint_id": "ck"}}

        pg.aput = AsyncMock(side_effect=cancel_on_second_call)

        with pytest.raises(MigrationCancelledError):
            await migrator.migrate_checkpoints(sqlite, pg)

        # At least one aput happened, but not all three.
        assert aput_call_count["n"] == 1


# ──────────────────────────────────────────────────────────────────────────────
# Error handling
# ──────────────────────────────────────────────────────────────────────────────


class TestErrorHandling:
    """A single failure is logged and the migration continues."""

    @pytest.mark.asyncio
    async def test_single_checkpoint_failure_continues(self, migrator):
        """If aput fails for one checkpoint, the next one still migrates."""
        tup_ok_1 = make_checkpoint_tuple(thread_id="t1", checkpoint_id="ck-1")
        tup_fail = make_checkpoint_tuple(thread_id="t1", checkpoint_id="ck-2")
        tup_ok_2 = make_checkpoint_tuple(thread_id="t1", checkpoint_id="ck-3")

        sqlite = make_saver_mock(
            thread_ids=["t1"],
            checkpoint_tuples_by_thread={"t1": [tup_ok_1, tup_fail, tup_ok_2]},
        )
        pg = make_saver_mock()

        aput_call_count = {"n": 0}

        async def fail_on_second(*args, **kwargs):
            aput_call_count["n"] += 1
            if aput_call_count["n"] == 2:
                raise RuntimeError("boom")
            return {"configurable": {"thread_id": "t1", "checkpoint_id": "ck"}}

        pg.aput = AsyncMock(side_effect=fail_on_second)

        count = await migrator.migrate_checkpoints(sqlite, pg)

        # Two succeeded (1 and 3), one failed.
        assert count == 2
        assert len(migrator.failed_checkpoints) == 1
        # The failure was recorded with the right thread + checkpoint id.
        thread_id, ck_id, err = migrator.failed_checkpoints[0]
        assert thread_id == "t1"
        assert ck_id == "ck-2"
        assert "boom" in err

    @pytest.mark.asyncio
    async def test_alist_failure_for_thread_continues(self, migrator):
        """If listing a thread's checkpoints fails, log and continue to next thread."""
        # For thread-1 alist raises; thread-2 alist returns one checkpoint.
        sqlite = MagicMock()
        sqlite.name = "src"
        sqlite.lock = _AsyncContextManager()
        cursor = MagicMock()
        cursor.fetchall = AsyncMock(return_value=[("thread-1",), ("thread-2",)])
        sqlite.conn.execute = AsyncMock(return_value=cursor)

        async def alist_func(config):
            thread_id = config["configurable"]["thread_id"]
            if thread_id == "thread-1":
                raise RuntimeError("alist failure")
            yield make_checkpoint_tuple(thread_id="thread-2", checkpoint_id="ck-x")

        sqlite.alist = alist_func

        pg = make_saver_mock()

        count = await migrator.migrate_checkpoints(sqlite, pg)

        # The successful thread contributed 1.
        assert count == 1
        # The failed thread was recorded as a failure with no checkpoint id.
        assert len(migrator.failed_checkpoints) == 1
        assert migrator.failed_checkpoints[0][0] == "thread-1"
        assert migrator.failed_checkpoints[0][1] is None

    @pytest.mark.asyncio
    async def test_failure_summary_logged(self, migrator, log_callback):
        """When failures occur, a summary log line is emitted."""
        tup_fail = make_checkpoint_tuple(thread_id="t1", checkpoint_id="ck-bad")
        sqlite = make_saver_mock(
            thread_ids=["t1"],
            checkpoint_tuples_by_thread={"t1": [tup_fail]},
        )
        pg = make_saver_mock()

        async def always_fail(*args, **kwargs):
            raise RuntimeError("nope")

        pg.aput = AsyncMock(side_effect=always_fail)

        count = await migrator.migrate_checkpoints(sqlite, pg)

        assert count == 0
        # The summary warning mentions the failure count.
        messages = [
            c.kwargs.get("message", "")
            for c in log_callback.call_args_list
        ]
        assert any("completed with 1 failed" in m for m in messages), messages


# ──────────────────────────────────────────────────────────────────────────────
# Progress logging
# ──────────────────────────────────────────────────────────────────────────────


class TestProgressLogging:
    """Per-thread and per-checkpoint progress is reported via the callback."""

    @pytest.mark.asyncio
    async def test_thread_progress_logged(self, migrator, log_callback):
        """A "Migrating checkpoints" line is emitted per thread."""
        tup = make_checkpoint_tuple(thread_id="t1", checkpoint_id="ck-1")
        sqlite = make_saver_mock(
            thread_ids=["t1"],
            checkpoint_tuples_by_thread={"t1": [tup]},
        )
        pg = make_saver_mock()

        await migrator.migrate_checkpoints(sqlite, pg)

        messages = [
            c.kwargs.get("message", "")
            for c in log_callback.call_args_list
        ]
        assert any("Migrating checkpoints" in m for m in messages)

    @pytest.mark.asyncio
    async def test_starting_summary_logged(self, migrator, log_callback):
        """A starting summary mentions the total number of threads."""
        tuples = [
            make_checkpoint_tuple(thread_id=f"t{i}", checkpoint_id=f"ck-{i}")
            for i in range(3)
        ]
        sqlite = make_saver_mock(
            thread_ids=["t1", "t2", "t3"],
            checkpoint_tuples_by_thread={f"t{i}": [tuples[i]] for i in range(3)},
        )
        pg = make_saver_mock()

        await migrator.migrate_checkpoints(sqlite, pg)

        messages = [
            c.kwargs.get("message", "")
            for c in log_callback.call_args_list
        ]
        assert any("3 threads to process" in m for m in messages)

    @pytest.mark.asyncio
    async def test_no_checkpoints_logged(self, migrator, log_callback):
        """Empty source emits a "no checkpoints to migrate" log."""
        sqlite = make_saver_mock(thread_ids=[])
        pg = make_saver_mock()

        await migrator.migrate_checkpoints(sqlite, pg)

        messages = [
            c.kwargs.get("message", "")
            for c in log_callback.call_args_list
        ]
        assert any("No checkpoints to migrate" in m for m in messages)
