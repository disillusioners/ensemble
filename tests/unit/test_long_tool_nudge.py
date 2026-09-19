"""Phase 2 — delivery seam + episode dedup unit tests (U1-U13, U17-U19).

House location mirrors the watchdog unit tests (services-level
acceptance harness: in-memory SQLite + real ``InstanceMessagingService``
for the attestation/queue-jump pin).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.instance.repository import SQLModelInstanceRepository
from daemon.repositories.message_queue.models import MessageQueue
from daemon.repositories.task.models import Task
from daemon.services.long_tool_nudge import (
    LONG_TOOL_NUDGE_SOURCE,
    LongToolNudgeEpisodeCtx,
    LongToolNudgeScanner,
    LongToolNudgeRegistry,
    _build_long_tool_notice,
    _format_age_human,
    LONG_TOOL_REGISTRY,
)
from daemon.services.waiting_children_watchdog import _build_wedge_notice


# ─── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def engine():
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def repo(engine) -> SQLModelInstanceRepository:
    return SQLModelInstanceRepository(engine=engine)


@pytest.fixture
def registry() -> LongToolNudgeRegistry:
    return LongToolNudgeRegistry()


def _make_instance_row(engine, *, instance_id, status, parent_id=None):
    now = datetime.now(timezone.utc)
    with engine.begin() as conn:
        conn.execute(
            Instance.__table__.insert().values(
                instance_id=instance_id,
                agent_id="test-agent",
                agent_dir="/tmp/test-agent",
                agent_name="test",
                parent_id=parent_id,
                status=status,
                last_activity_at=now.replace(tzinfo=None),
                created_at=now.replace(tzinfo=None).isoformat(),
                updated_at=now.replace(tzinfo=None).isoformat(),
            )
        )
    return instance_id


def _ctx(**overrides) -> LongToolNudgeEpisodeCtx:
    """Thin alias over the shared helper (M2 consolidation)."""
    from tests.helpers.long_tool_nudge import make_episode_ctx

    return make_episode_ctx(**overrides)


class _ParentStub:
    def __init__(self, status="running", parent_id="grand-1"):
        self.status = status
        self.parent_id = parent_id


def _scanner(
    registry,
    *,
    parent_status="running",
    manager=None,
    repo=None,
    threshold_metadata=None,
) -> LongToolNudgeScanner:
    """Thin wrapper over ``make_scanner`` (M2 consolidation) that
    routes ``parent_status`` through the local ``_ParentStub`` so the
    remaining call sites in this file keep their existing kwargs.
    """
    from tests.helpers.long_tool_nudge import make_scanner

    if repo is None:
        repo = MagicMock()
        repo.get_metadata_value = MagicMock(return_value=threshold_metadata)
        repo.get = MagicMock(return_value=_ParentStub(parent_status))
    return make_scanner(
        registry,
        manager=manager,
        repo=repo,
        threshold_metadata=threshold_metadata,
    )


# ─── U1: happy path ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_u1_deliver_happy_path(registry):
    scanner = _scanner(registry)
    result = await scanner.deliver_long_tool_nudge(
        "parent-1", "child-1", _ctx()
    )
    assert result is True
    scanner._manager.enqueue_message.assert_awaited_once()
    kwargs = scanner._manager.enqueue_message.await_args.kwargs
    assert kwargs["instance_id"] == "parent-1"
    assert kwargs["source"] == LONG_TOOL_NUDGE_SOURCE
    assert kwargs["priority"] == 0
    metadata = kwargs["metadata"]
    assert metadata["long_tool_nudge"] is True
    assert metadata["long_tool_nudge_tool"] == "bash"
    assert metadata["long_tool_nudge_tool_call_id"] == "call-1"
    assert metadata["long_tool_nudge_elapsed_seconds"] == 950.0
    assert metadata["long_tool_nudge_threshold_seconds"] == 900
    assert ("parent-1", "child-1") in scanner._active_episodes
    assert scanner._nudge_counts[("parent-1", "child-1")] == 1


# ─── U2: consecutive ticks on the same stamp dedup ──────────────────────────


@pytest.mark.asyncio
async def test_u2_dedup_consecutive_ticks_same_stamp(registry):
    import time as _time

    manager = AsyncMock()
    manager.enqueue_message = AsyncMock()
    scanner = _scanner(registry, manager=manager)
    await registry.record_start("child-1", "call-1", "bash", "parent-1")
    registry._stamps["child-1"]["call-1"].started_at = (
        _time.monotonic() - 1000
    )
    first = await scanner.run_once()
    assert first["fired"] == 1
    second = await scanner.run_once()  # same in-flight stamp, next tick
    assert second["fired"] == 0
    assert manager.enqueue_message.await_count == 1


# ─── U2b: bd4b36ef replay — 5 sequential long calls, ONE nudge ───────────────


@pytest.mark.asyncio
async def test_u2b_wedge_episode_5_sequential_long_calls_one_nudge(registry):
    """BLOCKING AD-9 close-gate trace.

    5 distinct long tool_call_ids on the same child, each completing
    LONG (no intervening healthy completion): the wrapper's
    ``finally`` calls ``close_episode_for`` ZERO times (the gate fails
    on long completions), so ``_active_episodes`` stays populated the
    whole span → exactly 1 nudge, 4 suppressed.
    """
    closes: list[tuple[str, str]] = []
    registry.attach_close_handler(lambda p, c: closes.append((p, c)))
    manager = AsyncMock()
    manager.enqueue_message = AsyncMock()
    scanner = _scanner(registry, manager=manager)

    for i in range(1, 6):
        call_id = f"call-long-{i}"
        import time as _time

        await registry.record_start("child-1", call_id, "bash", "parent-1")
        stamp = registry._stamps["child-1"][call_id]
        stamp.started_at = _time.monotonic() - 1200  # LONG (> 900s threshold)
        # The scanner tick fires WHILE the stamp is in-flight (the
        # tool is still running). THEN the wrapper's finally clears
        # the stamp; the close-gate evaluates LONG → does NOT call
        # close_episode_for. Emulate exactly that (tick → clear, no
        # close):
        stats = await scanner.run_once()
        await registry.clear("child-1", call_id)

    assert closes == []  # close_episode_for called ZERO times
    assert manager.enqueue_message.await_count == 1  # exactly ONE nudge
    assert stats["fired"] == 0  # the 5th crossing was suppressed
    assert ("parent-1", "child-1") in scanner._active_episodes


# ─── U3: re-arm after close ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_u3_rearm_after_close(registry):
    manager = AsyncMock()
    manager.enqueue_message = AsyncMock()
    scanner = _scanner(registry, manager=manager)
    assert (
        await scanner.deliver_long_tool_nudge("parent-1", "child-1", _ctx(tool_call_id="call-1"))
    ) is True
    scanner.close_episode("parent-1", "child-1")
    assert (
        await scanner.deliver_long_tool_nudge(
            "parent-1", "child-1", _ctx(tool_call_id="call-2-fresh")
        )
    ) is True
    assert manager.enqueue_message.await_count == 2
    second_metadata = manager.enqueue_message.await_args.kwargs["metadata"]
    assert second_metadata["long_tool_nudge_tool_call_id"] == "call-2-fresh"


# ─── U4: per-parent independence ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_u4_per_parent_child_independence(registry):
    manager = AsyncMock()
    manager.enqueue_message = AsyncMock()
    scanner = _scanner(registry, manager=manager)
    assert (
        await scanner.deliver_long_tool_nudge("parent-A", "child-A", _ctx(parent_id="parent-A", child_id="child-A"))
    ) is True
    assert (
        await scanner.deliver_long_tool_nudge("parent-B", "child-B", _ctx(parent_id="parent-B", child_id="child-B"))
    ) is True
    assert manager.enqueue_message.await_count == 2
    # A second long call for parent-A's child is suppressed; parent-B
    # is unaffected.
    assert (
        await scanner.deliver_long_tool_nudge("parent-A", "child-A", _ctx(parent_id="parent-A", child_id="child-A", tool_call_id="call-A2"))
    ) is False
    assert manager.enqueue_message.await_count == 2


# ─── U5 / U5b: PAUSED parent + AM-7 ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_u5_paused_parent_skipped(registry, caplog):
    scanner = _scanner(registry, parent_status="paused")
    with caplog.at_level("WARNING", logger="daemon.services.long_tool_nudge"):
        result = await scanner.deliver_long_tool_nudge(
            "parent-1", "child-1", _ctx()
        )
    assert result is False
    scanner._manager.enqueue_message.assert_not_awaited()
    assert any("PAUSED" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_u5b_stamp_fired_episodes_only_on_success(registry):
    import time as _time

    repo = MagicMock()
    repo.get_metadata_value = MagicMock(return_value=None)
    manager = AsyncMock()
    manager.enqueue_message = AsyncMock()
    scanner = LongToolNudgeScanner(
        repo,
        manager=manager,
        registry=registry,
        handoff_stub_enabled=False,
    )
    await registry.record_start("child-1", "call-1", "bash", "parent-1")
    registry._stamps["child-1"]["call-1"].started_at = _time.monotonic() - 1000

    # PAUSED: refused — no enqueue, dedup NOT advanced.
    repo.get = MagicMock(return_value=_ParentStub("paused"))
    stats = await scanner.run_once()
    assert stats["fired"] == 0
    assert manager.enqueue_message.await_count == 0
    assert ("child-1", "call-1") not in scanner._fired_episodes

    # RUNNING: fires — dedup advances.
    repo.get = MagicMock(return_value=_ParentStub("running"))
    stats = await scanner.run_once()
    assert stats["fired"] == 1
    assert manager.enqueue_message.await_count == 1
    assert ("child-1", "call-1") in scanner._fired_episodes

    # Follow-up second call for the SAME stamp — dedup holds.
    stats = await scanner.run_once()
    assert stats["fired"] == 0
    assert manager.enqueue_message.await_count == 1


# ─── U5t: terminal parents skip (AD-40) ──────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["completed", "terminated", "error", "failed"])
async def test_u5t_terminal_parent_skipped(registry, status, caplog):
    scanner = _scanner(registry, parent_status=status)
    with caplog.at_level("WARNING", logger="daemon.services.long_tool_nudge"):
        result = await scanner.deliver_long_tool_nudge(
            "parent-1", "child-1", _ctx()
        )
    assert result is False
    scanner._manager.enqueue_message.assert_not_awaited()  # no revive
    assert any(status in r.getMessage() for r in caplog.records)


# ─── U6: threshold override / clamp in the delivered metadata ────────────────


@pytest.mark.asyncio
async def test_u6_threshold_override_honored(registry):
    scanner = _scanner(registry, threshold_metadata=1500)
    await scanner.deliver_long_tool_nudge("parent-1", "child-1", _ctx(threshold_seconds=900))
    metadata = scanner._manager.enqueue_message.await_args.kwargs["metadata"]
    assert metadata["long_tool_nudge_threshold_seconds"] == 1500


@pytest.mark.asyncio
async def test_u6_threshold_override_clamped_to_hard_max(registry):
    scanner = _scanner(registry, threshold_metadata=2000)
    await scanner.deliver_long_tool_nudge("parent-1", "child-1", _ctx(threshold_seconds=900))
    metadata = scanner._manager.enqueue_message.await_args.kwargs["metadata"]
    assert metadata["long_tool_nudge_threshold_seconds"] == 1800


# ─── U7: priority=0 never resets attestation + queue-jump (AM-6) ─────────────


class TestU7PriorityZeroNoAttestationReset:
    @pytest.fixture
    def messaging_env(self, engine, repo):
        """Real InstanceMessagingService over a minimal manager stub."""

        class _WorkerPoolRecorder:
            def __init__(self):
                self.notify_calls = 0

            def notify_work(self) -> None:
                self.notify_calls += 1

        class _NotShuttingDown:
            is_shutting_down = False

        stub_manager = MagicMock()
        stub_manager.engine = engine
        stub_manager.write_guard = MagicMock()
        stub_manager._deferred_question_pause = {}
        stub_manager._job_queue_service = MagicMock()
        stub_manager._live_hub = MagicMock()
        stub_manager._live_hub.stream_status_change = AsyncMock()
        pool = _WorkerPoolRecorder()
        stub_manager._worker_pool = pool

        from daemon.services.instance_messaging import InstanceMessagingService

        service = InstanceMessagingService(
            manager=stub_manager,
            cancellation_service=_NotShuttingDown(),
        )
        return service, stub_manager, pool

    @pytest.mark.asyncio
    async def test_nudge_revives_parent_without_touching_counters(
        self, engine, repo, registry, messaging_env
    ):
        service, stub_manager, pool = messaging_env
        parent = _make_instance_row(
            engine,
            instance_id="parent-att",
            status=InstanceStatus.WAITING_CHILDREN.value,
        )
        _make_instance_row(
            engine,
            instance_id="child-att",
            status=InstanceStatus.RUNNING.value,
            parent_id=parent,
        )
        # Forge an in-progress attestation episode.
        with engine.begin() as conn:
            conn.execute(
                Instance.__table__.update()
                .where(Instance.__table__.c.instance_id == parent)
                .values(attestation_denied_count=2, completion_gate_escalated=True)
            )

        scanner = LongToolNudgeScanner(
            repo,
            manager=service,
            registry=registry,
            handoff_stub_enabled=False,
        )
        result = await scanner.deliver_long_tool_nudge(
            parent, "child-att", _ctx(child_id="child-att", parent_id=parent)
        )
        assert result is True

        row = repo.get(parent)
        assert row.status == InstanceStatus.RUNNING.value  # revive happened
        assert row.attestation_denied_count == 2  # UNCHANGED
        assert row.completion_gate_escalated is True  # UNCHANGED

        msg = conn_msg = None
        with engine.connect() as conn:
            conn_msg = conn.execute(
                select(
                    MessageQueue.message_id,
                    MessageQueue.source,
                    MessageQueue.priority,
                    MessageQueue.content,
                ).where(MessageQueue.instance_id == parent)
            ).one()
        assert conn_msg.source == LONG_TOOL_NUDGE_SOURCE
        assert conn_msg.priority == 0
        assert pool.notify_calls >= 1  # A5 direct notify fired
        # A Task row was created for the message (durable wake).
        with engine.connect() as conn:
            task_rows = conn.execute(
                select(Task.instance_id).where(
                    Task.message_id == conn_msg.message_id
                )
            ).all()
        assert len(task_rows) == 1

    @pytest.mark.asyncio
    async def test_priority_zero_queue_jumps_priority_one(self, engine, repo):
        """AM-6: claim order is ``ORDER BY priority ASC`` — a priority-0
        row precedes an older priority-1 row."""
        from daemon.repositories.message_queue.repository import (
            SQLModelMessageQueueRepository,
        )

        now = datetime.now(timezone.utc)
        with engine.begin() as conn:
            conn.execute(
                MessageQueue.__table__.insert().values(
                    message_id="mq-p1",
                    instance_id="parent-q",
                    content="user message",
                    source="api",
                    priority=1,
                    status="ready",
                    enqueued_at=(now - timedelta(seconds=10)).replace(tzinfo=None),
                )
            )
            conn.execute(
                MessageQueue.__table__.insert().values(
                    message_id="mq-p0",
                    instance_id="parent-q",
                    content="nudge",
                    source=LONG_TOOL_NUDGE_SOURCE,
                    priority=0,
                    status="ready",
                    enqueued_at=now.replace(tzinfo=None),
                )
            )
        mq_repo = SQLModelMessageQueueRepository(engine=engine)
        claimed = []
        for _ in range(2):
            row = mq_repo.dequeue("parent-q")
            if row is not None:
                claimed.append(row.message_id)
        assert claimed[0] == "mq-p0"  # priority-0 first despite being newer


# ─── U8-U10: A5 direct notify ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_u8_notify_work_called_after_enqueue_sync(registry):
    manager = AsyncMock()
    manager.enqueue_message = AsyncMock()
    pool = MagicMock()
    pool.notify_work = MagicMock(return_value=None)
    manager._worker_pool = pool
    scanner = _scanner(registry, manager=manager)
    await scanner.deliver_long_tool_nudge("parent-1", "child-1", _ctx())
    manager.enqueue_message.assert_awaited_once()
    pool.notify_work.assert_called_once()


@pytest.mark.asyncio
async def test_u8_notify_work_async_mock_awaited(registry):
    manager = AsyncMock()
    manager.enqueue_message = AsyncMock()
    pool = MagicMock()
    pool.notify_work = AsyncMock(return_value=None)
    manager._worker_pool = pool
    scanner = _scanner(registry, manager=manager)
    await scanner.deliver_long_tool_nudge("parent-1", "child-1", _ctx())
    pool.notify_work.assert_awaited_once()


@pytest.mark.asyncio
async def test_u9_notify_work_pool_missing_skipped(registry, caplog):
    """The direct-notify block is a no-op when no pool is wired.

    Phase B (chat-lane-followups) extraction note: the inline
    notify site in ``long_tool_nudge.py`` was consolidated into
    ``safe_notify_all_pools`` (the helper lives in
    ``daemon/services/pool_orchestrator.py``). The helper
    silently returns ``None`` when no manager helper AND no
    manager-attribute pool AND no ``fallback_pool=`` is
    reachable — so the site no longer emits a DEBUG log on the
    no-pool branch (the helper makes that decision and the
    caller does not need to know). The behavior IS preserved
    (no notify dispatched, the durable enqueue already
    committed, the wake happens via the periodic A3 sweep if
    needed).

    Pre-Phase-B: a DEBUG log "worker_pool not wired" was
    emitted. Post-Phase-B: that log lives in the helper
    (skipped here because the helper returns ``None`` without
    dispatching — there is no log when no pool is reachable
    from any of the three branches). The behavioral assertion
    (``result is True``) is what matters; the log-text
    assertion is dropped (Phase B consolidation moved the
    per-site DEBUG into the helper's silent-no-op path).
    """
    manager = AsyncMock()
    manager.enqueue_message = AsyncMock()
    manager._worker_pool = None
    scanner = _scanner(registry, manager=manager)
    result = await scanner.deliver_long_tool_nudge(
        "parent-1", "child-1", _ctx()
    )
    assert result is True
    manager.enqueue_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_u10_notify_work_exception_swallowed(registry, caplog):
    """A ``notify_work`` exception is swallowed — the durable
    enqueue already committed, so the nudge ships regardless.

    Phase B extraction note: the WARNING log now lives in
    ``daemon.services.pool_orchestrator.safe_notify_all_pools``
    (the consolidated helper). The log message format
    changed:

      Pre-Phase-B: ``"[LongToolNudge] direct notify_work raised
        %r for parent %s... — relying on enqueue_message's
        internal notify"``

      Post-Phase-B: ``"[LongToolNudge] legacy
        _worker_pool.notify_work() raised RuntimeError('boom')
        — next tick / sweep is the systemic backstop"``

    The behavioral guarantee (the exception is swallowed, the
    nudge is durable) is preserved — that is what the test
    asserts. The logger name is now
    ``daemon.services.pool_orchestrator`` (the helper's home).
    """
    manager = AsyncMock()
    manager.enqueue_message = AsyncMock()
    pool = MagicMock()
    pool.notify_work = MagicMock(side_effect=RuntimeError("boom"))
    manager._worker_pool = pool
    scanner = _scanner(registry, manager=manager)
    with caplog.at_level(
        "WARNING", logger="daemon.services.pool_orchestrator"
    ):
        result = await scanner.deliver_long_tool_nudge(
            "parent-1", "child-1", _ctx()
        )
    assert result is True  # nudge durable even if direct notify fails
    manager.enqueue_message.assert_awaited_once()
    assert any(
        "[LongToolNudge]" in r.getMessage()
        and "raised" in r.getMessage()
        for r in caplog.records
    )


# ─── U11: notice structure ───────────────────────────────────────────────────


class TestU11NoticeStructure:
    def test_five_sections_and_no_pause_resume_advice(self):
        notice = _build_long_tool_notice("parent-1", _ctx(), 900)
        assert "[system:long-tool-nudge]" in notice  # (a) header
        assert "busy-slow / weak-model signature" in notice  # (b) why
        assert "subtree_messages" in notice  # (c) rec 1
        assert "send_message" in notice  # (c) rec 2
        assert "CANNOT receive" in notice  # bd4b36ef 19-min lesson
        assert "terminate_instance" in notice  # (c) rec 3
        assert "model_tier" in notice  # (d) rec 4 names the new opt-in param
        assert "high" in notice  # (d) rec 4 names the tier literal
        assert notice.count("Re-spawn with high intelligence") == 1  # exactly one rec 4 (no double-implementation)
        assert "advisory only" in notice  # (e) footer
        assert "child-1:call-1"[:15] in notice  # episode id
        # NO pause/resume advice as agent instructions.
        assert "pause_instance" not in notice
        assert "resume_instance" not in notice

    def test_length_within_1_5x_wedge_notice(self):
        notice = _build_long_tool_notice("parent-1", _ctx(), 900)
        wedge = _build_wedge_notice("parent-1")
        assert len(notice) <= int(len(wedge) * 1.5)

    def test_human_formatting_from_format_age_human(self):
        assert _format_age_human(45.0) == "45s"
        assert _format_age_human(125.0) == "2m"
        assert _format_age_human(3725.0) == "1h2m"
        notice = _build_long_tool_notice(
            "parent-1", _ctx(elapsed_seconds=125.0, threshold_seconds=3725), 3725
        )
        assert "2m" in notice
        assert "1h2m" in notice


# ─── U12 / U13: loop entry contracts ─────────────────────────────────────────
# M2 — deleted. Both tests are strictly weaker than the loop.py
# equivalents (tests/unit/services/test_long_tool_nudge_loop.py):
#   * test_disabled_loop_returns_immediately monkeypatches
#     ``lt.asyncio.sleep`` to assert sleep is NOT called when
#     disabled. The root test_u12 only checked that ``run_once``
#     wasn't awaited.
#   * test_cancelled_error_from_run_once_propagates monkeypatches
#     ``lt.asyncio.sleep`` to assert sleep is NOT called when
#     ``run_once`` cancels. The root test_u13 didn't pin the
#     post-cancel behavior — only that CancelledError propagates.
# Loop.py is the single source for both shapes; the family delta
# in the gate report covers the deletion.


# ─── U17: close_episode drops count, idempotent ──────────────────────────────


def test_u17_episode_close_drops_count(registry):
    scanner = _scanner(registry)
    scanner._active_episodes.add(("parent-1", "child-1"))
    scanner._nudge_counts[("parent-1", "child-1")] = 1
    scanner.close_episode("parent-1", "child-1")
    assert ("parent-1", "child-1") not in scanner._active_episodes
    assert ("parent-1", "child-1") not in scanner._nudge_counts
    scanner.close_episode("parent-1", "child-1")  # idempotent no-op


# ─── W2: post-enqueue terminal-parent TOCTOU re-check (council fix-cycle 1) ──


class TestW2PostEnqueueTerminalParentTocTou:
    """Council fix-cycle 1, W2 — AD-40 closure pin.

    The pre-read at ``deliver_long_tool_nudge`` only narrows the
    AD-40 terminal-parent TOCTOU window: a parent that transitions
    to a terminal status DURING the enqueue commit is revived
    anyway. The post-enqueue re-check surfaces the race in the log
    (WARN) and asserts the nudge is durable (no rollback). Test
    failures here mean a regression in the TOCTOU observability.
    """

    @pytest.mark.asyncio
    async def test_post_enqueue_terminal_logs_warn_and_does_not_rollback(
        self, registry, caplog
    ):
        scanner = _scanner(registry)
        # Pre-read sees a running parent.
        parent_running = _ParentStub("running")
        # Post-enqueue sees a now-terminal parent.
        parent_terminal = _ParentStub("completed")
        scanner._repo.get = MagicMock(
            side_effect=[parent_running, parent_terminal]
        )
        with caplog.at_level(
            "WARNING", logger="daemon.services.long_tool_nudge"
        ):
            result = await scanner.deliver_long_tool_nudge(
                "parent-1", "child-1", _ctx()
            )
        # The nudge is durable — the post-enqueue re-check is a
        # no-op that surfaces the race; it does NOT roll back.
        assert result is True
        scanner._manager.enqueue_message.assert_awaited_once()
        # And the race is observable in the log.
        assert any(
            "post-enqueue terminal-parent race" in r.message
            for r in caplog.records
        ), (
            "W2 invariant: the post-enqueue re-check must surface "
            "the race in a WARN log (AD-40 TOCTOU observability)"
        )

    @pytest.mark.asyncio
    async def test_post_enqueue_non_terminal_no_warn(self, registry, caplog):
        """Sanity pin: a parent that stays non-terminal across the
        enqueue emits NO TOCTOU WARN — the re-check is silent on
        the happy path."""
        scanner = _scanner(registry)
        parent_running = _ParentStub("running")
        scanner._repo.get = MagicMock(return_value=parent_running)
        with caplog.at_level(
            "WARNING", logger="daemon.services.long_tool_nudge"
        ):
            await scanner.deliver_long_tool_nudge("parent-1", "child-1", _ctx())
        assert not any(
            "post-enqueue terminal-parent race" in r.message
            for r in caplog.records
        )

    @pytest.mark.asyncio
    async def test_post_enqueue_get_parent_raises_post_status_none(
        self, registry
    ):
        """M4 — ``_get_parent`` already swallows its own exceptions
        (defensive contract) and returns ``None`` on any raise. The
        post-enqueue terminal-parent TOCTOU re-check consumes that
        ``None`` as ``post_status = None`` (no attribute lookup, no
        ``TERMINAL_PARENT_STATUSES`` membership check, no WARN).
        The dead try/except that used to wrap this in the call site
        was unreachable; this test pins the post-move behavior."""
        scanner = _scanner(registry)
        parent_running = _ParentStub("running")
        # Pre-read succeeds (running), post-read raises. ``_get_parent``
        # catches and returns ``None``; ``post_status`` becomes
        # ``None``; no TOCTOU WARN is logged; nudge still durable.
        scanner._repo.get = MagicMock(
            side_effect=[parent_running, RuntimeError("db blip")]
        )
        result = await scanner.deliver_long_tool_nudge(
            "parent-1", "child-1", _ctx()
        )
        assert result is True
        scanner._manager.enqueue_message.assert_awaited_once()


class TestParentNotFoundLogOncePerEpisode:
    """BEHAVIORAL — log-once-per-episode for the
    ``[LongToolNudge] parent ... not found — nudge skipped`` WARN.

    A long-tool episode whose parent row vanished mid-tool must not
    re-WARN every 60s tick for the entire wedge duration. The
    scanner's ``_missing_parent_warned`` set keys on
    ``(parent_id, child_id)`` and clears on ``close_episode`` so a
    fresh tool_call_id crossing after a HEALTHY close gets a single
    fresh WARN again.
    """

    @pytest.mark.asyncio
    async def test_warn_emitted_once_across_ticks(self, registry, caplog):
        scanner = _scanner(registry)
        # _get_parent returns None on every call (parent row absent).
        scanner._repo.get = MagicMock(return_value=None)
        with caplog.at_level(
            "WARNING", logger="daemon.services.long_tool_nudge"
        ):
            first = await scanner.deliver_long_tool_nudge(
                "parent-gone", "child-1", _ctx()
            )
            second = await scanner.deliver_long_tool_nudge(
                "parent-gone", "child-1", _ctx()
            )
        assert first is False
        assert second is False
        warns = [
            r for r in caplog.records
            if "parent ... not found" in r.getMessage()
            or "not found — nudge skipped" in r.getMessage()
        ]
        # ONE warn across both calls (same episode key).
        assert len(warns) == 1, (
            f"BEHAVIORAL: expected exactly one missing-parent WARN "
            f"per episode; got {len(warns)}: "
            f"{[r.getMessage() for r in warns]}"
        )

    @pytest.mark.asyncio
    async def test_warn_resets_after_close_episode(
        self, registry, caplog
    ):
        """Closing the episode via ``close_episode`` clears the
        dedup entry so a fresh ``(parent_id, child_id)`` crossing
        after a HEALTHY close can surface its missing-parent WARN
        again on the first tick of the next episode."""
        scanner = _scanner(registry)
        scanner._repo.get = MagicMock(return_value=None)
        with caplog.at_level(
            "WARNING", logger="daemon.services.long_tool_nudge"
        ):
            await scanner.deliver_long_tool_nudge(
                "parent-gone", "child-1",
                _ctx(tool_call_id="call-1"),
            )
            scanner.close_episode("parent-gone", "child-1")
            await scanner.deliver_long_tool_nudge(
                "parent-gone", "child-1",
                _ctx(tool_call_id="call-2"),
            )
        warns = [
            r for r in caplog.records
            if "not found — nudge skipped" in r.getMessage()
        ]
        assert len(warns) == 2, (
            f"close_episode must reset the missing-parent dedup so a "
            f"fresh tool_call_id crossing can WARN again; got "
            f"{len(warns)}: {[r.getMessage() for r in warns]}"
        )


# ─── U18: DB-down tick isolation ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_u18_db_down_tick_isolation(registry):
    import time as _time

    manager = AsyncMock()
    manager.enqueue_message = AsyncMock()
    repo = MagicMock()
    repo.get = MagicMock(return_value=_ParentStub("running"))

    def flaky_threshold_read(instance_id, key=None):
        if instance_id == "child-bad":
            from sqlalchemy.exc import OperationalError

            raise OperationalError("stmt", {}, Exception("DB down"))
        return None

    repo.get_metadata_value = MagicMock(
        side_effect=flaky_threshold_read
    )
    scanner = LongToolNudgeScanner(
        repo,
        manager=manager,
        registry=registry,
        handoff_stub_enabled=False,
    )
    for child in ("child-bad", "child-ok"):
        await registry.record_start(child, f"call-{child}", "bash", "parent-1")
        registry._stamps[child][f"call-{child}"].started_at = (
            _time.monotonic() - 1000
        )
    stats = await scanner.run_once()
    assert stats["errors"] == 1
    assert stats["fired"] == 1  # child-ok still scanned + fired
    assert manager.enqueue_message.await_count == 1
    assert manager.enqueue_message.await_args.kwargs["instance_id"] == "parent-1"


# ─── U19: two concurrent long children, same parent ──────────────────────────


@pytest.mark.asyncio
async def test_u19_two_concurrent_long_children_same_parent(registry):
    import time as _time

    manager = AsyncMock()
    manager.enqueue_message = AsyncMock()
    repo = MagicMock()
    repo.get_metadata_value = MagicMock(return_value=None)
    repo.get = MagicMock(return_value=_ParentStub("running"))
    scanner = LongToolNudgeScanner(
        repo,
        manager=manager,
        registry=registry,
        handoff_stub_enabled=False,
    )
    await registry.record_start("child-A", "call-a", "bash", "parent-1")
    await registry.record_start("child-B", "call-b", "send_message", "parent-1")
    registry._stamps["child-A"]["call-a"].started_at = _time.monotonic() - 1000
    registry._stamps["child-B"]["call-b"].started_at = _time.monotonic() - 1100
    stats = await scanner.run_once()
    assert stats["fired"] == 2  # distinct (parent, child) keys
    assert manager.enqueue_message.await_count == 2
    instance_ids = {
        c.kwargs["instance_id"]
        for c in manager.enqueue_message.await_args_list
    }
    assert instance_ids == {"parent-1"}
