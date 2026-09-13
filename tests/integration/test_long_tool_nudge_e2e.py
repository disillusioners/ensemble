"""Phase 2 integration tests — long-tool-nudge over the REAL wake path.

Harness: real ``InstanceMessagingService`` over an in-memory SQLite
engine (the watchdog pure-hang acceptance pattern). The nudge travels
the REAL delivery primitive — MessageQueue + Task rows in one txn,
WAITING_CHILDREN → RUNNING flip, worker-pool notify — while the only
fake is the seeded registry stamp (the child's in-flight tool call).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

import daemon.services.long_tool_nudge as lt
from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.instance.repository import SQLModelInstanceRepository
from daemon.repositories.message_queue.models import MessageQueue
from daemon.repositories.task.models import Task
from daemon.services.instance_messaging import InstanceMessagingService
from daemon.services.long_tool_nudge import (
    LONG_TOOL_NUDGE_SOURCE,
    LongToolNudgeRegistry,
    LongToolNudgeScanner,
    LONG_TOOL_REGISTRY,
)


class _WorkerPoolRecorder:
    def __init__(self):
        self.notify_calls = 0

    def notify_work(self) -> None:
        self.notify_calls += 1


class _NotShuttingDown:
    is_shutting_down = False


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
def repo(engine):
    return SQLModelInstanceRepository(engine=engine)


@pytest.fixture
def registry():
    return LongToolNudgeRegistry()


@pytest.fixture
def lt_real():
    """Fresh feature module with the REAL langgraph (the root conftest
    installs a global langgraph mock; the wrapper delegates to the real
    ToolNode). Repo-standard evict/restore pattern."""
    import importlib
    import sys

    from tests.helpers.checkpoint_prune_pg import (
        evict_langgraph_mocks,
        restore_langgraph_mocks,
    )

    saved = evict_langgraph_mocks()
    saved_lt = sys.modules.pop("daemon.services.long_tool_nudge", None)
    try:
        yield importlib.import_module("daemon.services.long_tool_nudge")
    finally:
        sys.modules.pop("daemon.services.long_tool_nudge", None)
        if saved_lt is not None:
            sys.modules["daemon.services.long_tool_nudge"] = saved_lt
        restore_langgraph_mocks(saved)


async def _run_through_mini_graph(lt, node, state, config):
    from typing import TypedDict

    from langgraph.graph import END, START, StateGraph

    class S(TypedDict, total=False):
        messages: list

    g = StateGraph(S)
    g.add_node("tools", node)
    g.add_edge(START, "tools")
    g.add_edge("tools", END)
    return await g.compile().ainvoke(state, config=config)


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


def _real_messaging(engine):
    stub_manager = MagicMock()
    stub_manager.engine = engine
    stub_manager.write_guard = MagicMock()
    stub_manager._deferred_question_pause = {}
    stub_manager._job_queue_service = MagicMock()
    stub_manager._live_hub = MagicMock()
    stub_manager._live_hub.stream_status_change = AsyncMock()
    pool = _WorkerPoolRecorder()
    stub_manager._worker_pool = pool
    service = InstanceMessagingService(
        manager=stub_manager,
        cancellation_service=_NotShuttingDown(),
    )
    return service, pool


def _ctx(child_id, parent_id, **overrides):
    fields = dict(
        child_id=child_id,
        parent_id=parent_id,
        tool_name="bash",
        tool_call_id="call-e2e-1",
        elapsed_seconds=905.0,
        threshold_seconds=900,
        episode_started_at=100.0,
    )
    fields.update(overrides)
    return fields


def _seed_stamp(registry, child_id, call_id="call-e2e-1", age=905.0):
    """Simulate the wrapper's record_start for an in-flight call that
    crossed the threshold (AD-28: the scanner reads the singleton).

    ``started_at`` uses the REAL monotonic clock minus ``age`` so the
    crossing is deterministic regardless of process uptime."""
    import time as _time

    registry._stamps.setdefault(child_id, {})[call_id] = lt._Stamp(
        tool_call_id=call_id,
        tool_name="bash",
        started_at=_time.monotonic() - age,
        parent_id=None,
    )


def _messages_for(engine, instance_id):
    with engine.connect() as conn:
        rows = conn.execute(
            select(
                MessageQueue.message_id,
                MessageQueue.source,
                MessageQueue.content,
                MessageQueue.priority,
            ).where(MessageQueue.instance_id == instance_id)
        ).all()
    return rows


class TestLongToolNudgeE2E:
    async def _run_tick(self, scanner):
        """One scanner tick (the loop body, without the wall-clock sleep)."""
        return await scanner.run_once()

    @pytest.mark.asyncio
    async def test_i1_child_long_tool_nudges_parent(self, engine, repo, registry):
        parent = _make_instance_row(
            engine, instance_id="i1-parent", status=InstanceStatus.WAITING_CHILDREN.value
        )
        _make_instance_row(
            engine,
            instance_id="i1-child",
            status=InstanceStatus.RUNNING.value,
            parent_id=parent,
        )
        service, pool = _real_messaging(engine)
        scanner = LongToolNudgeScanner(
            repo, manager=service, registry=registry, handoff_stub_enabled=False
        )
        _seed_stamp(registry, "i1-child")

        stats = await self._run_tick(scanner)
        assert stats["fired"] == 1

        rows = _messages_for(engine, parent)
        assert len(rows) == 1
        assert rows[0].source == LONG_TOOL_NUDGE_SOURCE
        assert rows[0].priority == 0
        content = rows[0].content
        assert "[system:long-tool-nudge]" in content
        assert "busy-slow / weak-model signature" in content
        assert "# FUTURE" in content
        assert "advisory only" in content

        # (b) the wake is real: WAITING_CHILDREN → RUNNING in the DB.
        assert repo.get(parent).status == InstanceStatus.RUNNING.value

        # (d) dedup: three more ticks → no SECOND nudge.
        for _ in range(3):
            await self._run_tick(scanner)
        rows = _messages_for(engine, parent)
        assert len(rows) == 1

    @pytest.mark.asyncio
    async def test_i2_parent_in_waiting_children_revives_to_running(
        self, engine, repo, registry
    ):
        parent = _make_instance_row(
            engine, instance_id="i2-parent", status=InstanceStatus.WAITING_CHILDREN.value
        )
        _make_instance_row(
            engine, instance_id="i2-child", status=InstanceStatus.RUNNING.value, parent_id=parent
        )
        service, _pool = _real_messaging(engine)
        scanner = LongToolNudgeScanner(
            repo, manager=service, registry=registry, handoff_stub_enabled=False
        )
        _seed_stamp(registry, "i2-child")
        await self._run_tick(scanner)
        row = repo.get(parent)
        assert row.status == InstanceStatus.RUNNING.value

    @pytest.mark.asyncio
    async def test_i3_daemon_restart_resets_dedup(self, engine, repo, registry):
        parent = _make_instance_row(
            engine, instance_id="i3-parent", status=InstanceStatus.WAITING_CHILDREN.value
        )
        _make_instance_row(
            engine, instance_id="i3-child", status=InstanceStatus.RUNNING.value, parent_id=parent
        )
        service, _pool = _real_messaging(engine)

        # "Pre-restart" scanner: fires once.
        scanner_a = LongToolNudgeScanner(
            repo, manager=service, registry=registry, handoff_stub_enabled=False
        )
        _seed_stamp(registry, "i3-child")
        await self._run_tick(scanner_a)
        assert len(_messages_for(engine, parent)) == 1

        # Daemon restart: the RAM scanner (episodes/fired sets) dies
        # with the process; the DB persists. A child still mid-tool is
        # re-observed on the next tick → ≤1 duplicate nudge (AD-14,
        # documented trade-off pinned here).
        scanner_b = LongToolNudgeScanner(
            repo, manager=service, registry=registry, handoff_stub_enabled=False
        )
        _seed_stamp(registry, "i3-child", call_id="call-post-restart")
        await self._run_tick(scanner_b)
        rows = _messages_for(engine, parent)
        assert len(rows) == 2  # duplicate nudge after restart — accepted

    @pytest.mark.asyncio
    async def test_i3_restart_no_stamps_no_nudges(
        self, engine, repo, registry
    ):
        """Council fix-cycle 1, W5 — restart-boundary regression pin.

        Pins the OTHER half of the I3 contract: a fresh scanner with
        an empty registry MUST NOT emit nudges (no phantom
        revives). Pairs with ``test_i3_daemon_restart_resets_dedup``
        above: ≤1 duplicate is the UPPER bound; 0 with no stamps is
        the lower bound. Together they fully pin the AD-14 "≤1
        duplicate nudge after restart" trade-off.
        """
        parent = _make_instance_row(
            engine,
            instance_id="i3b-parent",
            status=InstanceStatus.WAITING_CHILDREN.value,
        )
        _make_instance_row(
            engine,
            instance_id="i3b-child",
            status=InstanceStatus.RUNNING.value,
            parent_id=parent,
        )
        service, _pool = _real_messaging(engine)
        scanner = LongToolNudgeScanner(
            repo, manager=service, registry=registry, handoff_stub_enabled=False
        )
        # No stamp seeded — restart cold-start with empty registry.
        stats = await self._run_tick(scanner)
        assert stats["fired"] == 0
        assert stats["instances_scanned"] == 0
        assert _messages_for(engine, parent) == []

    @pytest.mark.asyncio
    async def test_i3_restart_one_duplicate_upper_bound(
        self, engine, repo, registry
    ):
        """Council fix-cycle 1, W5 — restart-boundary regression pin.

        Pins the EXACT upper bound of the AD-14 contract: a fresh
        scanner observing ONE fresh stamp after restart emits
        AT MOST one nudge (the post-restart one). Pre-restart state
        contributes no phantom nudges. This is the "≤1 duplicate"
        semantic the docstring claims — test failure means the
        upper bound regressed (e.g., a new bug class that re-fires
        a stamp the scanner already saw pre-restart, or a phantom
        nudge from a dead-but-not-yet-GCed scanner instance).
        """
        parent = _make_instance_row(
            engine,
            instance_id="i3c-parent",
            status=InstanceStatus.WAITING_CHILDREN.value,
        )
        _make_instance_row(
            engine,
            instance_id="i3c-child",
            status=InstanceStatus.RUNNING.value,
            parent_id=parent,
        )
        service, _pool = _real_messaging(engine)

        # Pre-restart: scanner A fires once (counts as the "first
        # nudge" of the duplicate pair).
        scanner_a = LongToolNudgeScanner(
            repo, manager=service, registry=registry, handoff_stub_enabled=False
        )
        _seed_stamp(registry, "i3c-child", call_id="call-pre-restart")
        await self._run_tick(scanner_a)
        assert len(_messages_for(engine, parent)) == 1

        # Post-restart: scanner B with empty dedup state + ONE
        # fresh stamp → exactly one MORE nudge (the duplicate).
        scanner_b = LongToolNudgeScanner(
            repo, manager=service, registry=registry, handoff_stub_enabled=False
        )
        _seed_stamp(registry, "i3c-child", call_id="call-post-restart")
        await self._run_tick(scanner_b)

        # EXACTLY one post-restart nudge (the duplicate) — no
        # second duplicate, no phantom.
        rows = _messages_for(engine, parent)
        assert len(rows) == 2, (
            f"≤1 duplicate after restart violated: got {len(rows)} "
            f"nudges (expected 2 = 1 pre + 1 post-restart duplicate)"
        )

        # Follow-up ticks on the post-restart scanner with NO new
        # stamps → no further nudges (the fresh dedup state holds
        # for the fresh stamp; no phantom from dead scanner A).
        for _ in range(3):
            await self._run_tick(scanner_b)
        assert len(_messages_for(engine, parent)) == 2

    @pytest.mark.asyncio
    async def test_i4_paused_parent_no_nudge(self, engine, repo, registry, caplog):
        parent = _make_instance_row(
            engine, instance_id="i4-parent", status=InstanceStatus.PAUSED.value
        )
        _make_instance_row(
            engine, instance_id="i4-child", status=InstanceStatus.RUNNING.value, parent_id=parent
        )
        service, _pool = _real_messaging(engine)
        scanner = LongToolNudgeScanner(
            repo, manager=service, registry=registry, handoff_stub_enabled=False
        )
        _seed_stamp(registry, "i4-child")
        with caplog.at_level("WARNING", logger="daemon.services.long_tool_nudge"):
            await self._run_tick(scanner)
        assert _messages_for(engine, parent) == []
        assert any("PAUSED" in r.getMessage() for r in caplog.records)
        assert repo.get(parent).status == InstanceStatus.PAUSED.value

    @pytest.mark.asyncio
    async def test_i5_priority_zero_does_not_reset_attestation(
        self, engine, repo, registry
    ):
        parent = _make_instance_row(
            engine, instance_id="i5-parent", status=InstanceStatus.WAITING_CHILDREN.value
        )
        _make_instance_row(
            engine, instance_id="i5-child", status=InstanceStatus.RUNNING.value, parent_id=parent
        )
        with engine.begin() as conn:
            conn.execute(
                Instance.__table__.update()
                .where(Instance.__table__.c.instance_id == parent)
                .values(attestation_denied_count=2, completion_gate_escalated=True)
            )
        service, _pool = _real_messaging(engine)
        scanner = LongToolNudgeScanner(
            repo, manager=service, registry=registry, handoff_stub_enabled=False
        )
        _seed_stamp(registry, "i5-child")
        await self._run_tick(scanner)

        row = repo.get(parent)
        assert row.status == InstanceStatus.RUNNING.value  # revived
        assert row.attestation_denied_count == 2  # unchanged
        assert row.completion_gate_escalated is True  # unchanged


class TestU20CompactionWindowInterleave:
    """U20 (relocated to the integration file per the phase-2 table).

    Expected behavior (documented pin): a mid-compaction window on the
    child has NO special interaction with the stamp lifecycle — stamps
    record at batch entry, survive the window, clear at ``tool_end``,
    and the HEALTHY-gated close (AD-9) is unchanged. Proactive
    compaction rewrites message state on the instance; it never
    touches the RAM stamp registry, so the wrapper's contract is
    unchanged during the window.
    """

    @pytest.mark.asyncio
    async def test_stamps_survive_compaction_window_and_clear_normally(
        self, registry, lt_real
    ):
        from langchain_core.messages import AIMessage
        from langchain_core.tools import tool

        observed: dict[str, object] = {}

        @tool
        def cw_probe(x: str) -> str:
            """Runs mid-batch — simulates a tool call spanning a
            compaction window on the child instance."""
            observed["during_window"] = {
                iid: dict(tcs) for iid, tcs in registry._stamps.items()
            }
            return "cw-probed"

        node = lt_real.wrapped_tools_node([cw_probe], registry)
        state = {
            "messages": [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "cw_probe",
                            "args": {"x": "1"},
                            "id": "call-cw-1",
                            "type": "tool_call",
                        }
                    ],
                )
            ]
        }
        config = {"configurable": {"thread_id": "cw-child"}}
        registry.attach_threshold_resolver(lambda iid: 900)

        result = await _run_through_mini_graph(lt_real, node, state, config)
        # The stamp was present DURING the window (mid-batch).
        assert "call-cw-1" in observed["during_window"].get("cw-child", {})
        # ...and the batch completed through the wrapper untouched.
        assert result["messages"][-1].content == "cw-probed"
        # Post-completion: registry empty; the healthy close-gate fired.
        assert await registry.snapshot() == {}
