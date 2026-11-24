"""Rider pins — fix/post-outage-recovery SCOPE (c), ITEM 5 (b4 nits).

The b4 stale-running sweep landed with five review nits. This file pins
the four that are behavioural (the fifth — the census naming docstring
— is pinned by the docstring assertions at the bottom):

NIT-1 (MEDIUM) — the report-injection MINT-SITE guard was ANCHOR-EXACT
    (``source == internal_report:{child}:{message_id}``) while the
    invariant the whole subsystem enforces is PER-CHILD PREFIX
    (``source LIKE 'internal_report:' || child || ':%'``). A b4
    candidate whose prior report is still PENDING under a DIFFERENT
    anchor therefore slipped past the guard: a SECOND report row was
    minted AND a SECOND obligation was created for the same child
    (the probe: 2 obligations for 1 child; the parent receives 2
    reports). Fixed at the MINT SITE (not by holding in the sweep).

NIT-2 — the holding-message probe returned ``str | None`` and used
    ``None`` for BOTH "no message row" and "a message row whose status
    column is NULL". A NULL status is NOT proven-terminal, so it MUST
    hold the posture — under the old shape it failed OPEN.

NIT-4 — the candidate SQL ranked by ``COALESCE(task_completed_at,
    message_completed_at)`` (the OLDEST of the two when the task stamp
    exists) while the PYTHON age leg uses the NEWEST evidence. A row
    whose task evidence is OLD but whose message evidence is NEW
    therefore consumed a cap slot and was then skipped as "young",
    starving an eligible row behind it.

NIT-6 — the typed reason (``finalize_gap_steady_state``) existed ONLY
    in a log line; the provenance of a sweep-driven terminal stamp was
    not recoverable from the row.

Harness: a real SQLite in-memory engine + the real repositories + the
real ``ChildReportsService`` (mirrors the b4 sibling pin file); the
transport boundaries (the dependency bus singleton) are fakes.

FAIL-HONEST: the NIT-1 / NIT-2 / NIT-4 / NIT-6 pins are RED at the
pre-rider base (1eda81daa) — verified in a throwaway /tmp worktree,
removed + pruned afterwards.
"""

from __future__ import annotations

import asyncio
import json
import os
from contextlib import ExitStack, contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session

from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.instance.repository import SQLModelInstanceRepository
from daemon.repositories.message_queue.models import MessageStatus
from daemon.services.timestamps import now_utc_naive

from daemon.services import stale_running_sweep as srs

PARENT = "rider-parent"
CHILD = "rider-child"
SIBLING = "rider-child2"  # id EXTENDS the child's id — the boundary case

#: The age of evidence that is OLD enough to clear the default bound.
OLD_ENOUGH = timedelta(hours=5)
#: Evidence that is too young to clear the default bound (2h).
TOO_YOUNG = timedelta(hours=1)


# ─────────────────────────────────────────────────────────────────────────────
# Engine + harness
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def rider_engine():
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture(autouse=True)
def dependency_bus():
    """The ADR-011 mock DependencyBus singleton (the b4 sibling pattern).

    The production completion chain hard-requires the singleton
    (``child_reports`` raises ``DependencyBus is not initialized``);
    the mock returns ZERO pending watchers so the cascade lane's
    ``is_parent_complete`` check is satisfied.
    """
    bus = MagicMock()

    async def _lock_factory(parent_id):
        return asyncio.Lock()

    bus.get_parent_lock = _lock_factory
    bus.count_pending_for_target_sync = MagicMock(return_value=0)
    bus.count_fulfillable_pending_for_target_sync = MagicMock(return_value=0)
    bus.count_pending_for_target = AsyncMock(return_value=0)
    bus.count_fulfillable_pending_for_target = AsyncMock(return_value=0)
    bus.had_parent_error = MagicMock(return_value=False)
    bus.parent_error_message = MagicMock(return_value=None)
    bus.get_generation = MagicMock(return_value=0)
    bus.emit_terminal = AsyncMock(return_value=[])
    bus.emit_terminal_for_child_instance = AsyncMock(return_value=[])
    bus._get_parent_lock = _lock_factory
    bus.mark_enqueued_by_source_target = AsyncMock(return_value=None)

    from daemon.services.dependency_bus import set_dependency_bus

    set_dependency_bus(bus)
    yield bus
    set_dependency_bus(None)


@pytest.fixture
def rider_harness(rider_engine):
    instance_repo = SQLModelInstanceRepository(rider_engine)
    task_repo = __import__(
        "daemon.repositories.task.repository", fromlist=["TaskRepository"]
    ).TaskRepository(rider_engine)

    manager = MagicMock()
    manager.engine = rider_engine
    manager._engine = rider_engine
    manager._instance_repository = instance_repo
    manager._task_repo = task_repo
    from daemon.write_pause_guard import WritePauseGuard

    manager.write_guard = WritePauseGuard()
    manager._live_hub = MagicMock()
    manager._live_hub.stream_status_change = AsyncMock()
    manager.enqueue_message = AsyncMock(return_value=MagicMock(message_id="msg-rider"))
    manager.config = MagicMock()
    manager.config.report_repair = MagicMock()
    manager.config.report_repair.enabled = False
    manager.config.report_repair.repair_excluded_agents = []
    manager.config.report_repair.size_ratio_threshold = 5.0
    manager.config.report_repair.lookback_messages = 3
    manager.config.report_integrity = MagicMock()
    manager.config.report_integrity.b_terminal_waiting_guard_enabled = True
    manager._question_manager = MagicMock()
    manager._question_manager.get_pending_packs_for_instances = MagicMock(
        return_value={}
    )

    from daemon.repositories.event.repository import EventRepository
    event_bus = __import__(
        "daemon.services.event_bus", fromlist=["EventBus"]
    ).EventBus(event_repo=EventRepository(rider_engine))
    manager._event_bus = event_bus

    from daemon.services.child_reports import ChildReportsService

    events_service = MagicMock()
    events_service._publish_instance_lifecycle_event = AsyncMock()
    service = ChildReportsService(manager=manager, events_service=events_service)
    manager._child_reports_service = service
    manager._mission_strand_healer = None

    return type(
        "RiderHarness",
        (),
        {
            "engine": rider_engine,
            "manager": manager,
            "instance_repo": instance_repo,
            "service": service,
        },
    )()


def _make_sweep(harness, **kwargs):
    kwargs.setdefault("interval_seconds", 300)
    return srs.StaleRunningSweepService(harness.manager, **kwargs)


def _patch_drive_seams(history: list[dict] | None = None):
    """The drive-seam patch set (the b4 sibling pattern)."""
    if history is None:
        history = [
            {
                "role": "user",
                "content": "run the mission",
                "created_at": "2026-11-23T09:00:00+00:00",
            },
            {
                "role": "assistant",
                "content": "mission complete — final report",
                "created_at": "2026-11-23T10:00:00+00:00",
            },
        ]

    @contextmanager
    def _cm():
        stack = ExitStack()
        stack.enter_context(patch(
            "daemon.services.child_reports.get_instance_messages",
            new_callable=AsyncMock,
            return_value=history,
        ))
        stack.enter_context(patch(
            "daemon.services.child_reports.get_last_assistant_timestamp",
            new_callable=AsyncMock,
            return_value="2026-11-23T10:00:00+00:00",
        ))
        stack.enter_context(patch(
            "daemon.services.child_reports.MainLoopBridge.run_async_no_wait"
        ))
        try:
            yield stack
        finally:
            stack.close()

    return _cm()


# ─────────────────────────────────────────────────────────────────────────────
# Seeding helpers
# ─────────────────────────────────────────────────────────────────────────────


def _insert_instance(
    engine,
    instance_id: str,
    *,
    status: str,
    parent_id: str | None = None,
    agent_id: str = "leader",
    last_activity_at: datetime | None = None,
    updated_at: datetime | None = None,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    activity_iso = (
        last_activity_at.isoformat() if last_activity_at is not None else None
    )
    updated_iso = updated_at.isoformat() if updated_at is not None else now
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO instances
                    (instance_id, agent_id, agent_dir, status, project_id,
                     created_at, updated_at, last_activity_at, version,
                     parent_id)
                VALUES
                    (:instance_id, :agent_id, :agent_dir, :status,
                     :project_id, :created_at, :updated_at,
                     :last_activity_at, :version, :parent_id)
                """
            ),
            {
                "instance_id": instance_id,
                "agent_id": agent_id,
                "agent_dir": f"agents/{agent_id}",
                "status": status,
                "project_id": "test-project",
                "created_at": now,
                "updated_at": updated_iso,
                "last_activity_at": activity_iso,
                "version": 1,
                "parent_id": parent_id,
            },
        )


def _insert_task(
    engine,
    *,
    instance_id: str,
    status: str,
    completed_at: datetime | None,
    work_id: str = "work-rider-1",
) -> None:
    completed_iso = (
        completed_at.isoformat() if completed_at is not None else None
    )
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO task
                    (task_type, instance_id, status, retry_count,
                     created_at, cancel_requested, retry_scheduled,
                     work_id, is_deferred, is_background, completed_at)
                VALUES
                    ('process_message', :instance_id, :status, 0,
                     :created_at, 0, 0, :work_id, 0, 0, :completed_at)
                """
            ),
            {
                "instance_id": instance_id,
                "status": status,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "work_id": work_id,
                "completed_at": completed_iso,
            },
        )


def _insert_message(
    engine,
    *,
    instance_id: str,
    status: str | None,
    completed_at: datetime | None = None,
    message_id: str = "msg-rider-1",
    content: str = "scheduled mission",
    source: str = "api",
) -> None:
    completed_iso = (
        completed_at.isoformat() if completed_at is not None else None
    )
    enqueued_iso = (
        datetime.now(timezone.utc) - timedelta(hours=30)
    ).isoformat()
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO message_queue
                    (message_id, instance_id, content, type, source,
                     status, priority, retry_count, max_retries,
                     enqueued_at, completed_at, metadata)
                VALUES
                    (:message_id, :instance_id, :content, 'agent', :source,
                     :status, 1, 0, 3, :enqueued_at, :completed_at, :metadata)
                """
            ),
            {
                "message_id": message_id,
                "instance_id": instance_id,
                "content": content,
                "source": source,
                "status": status,
                "enqueued_at": enqueued_iso,
                "completed_at": completed_iso,
                "metadata": json.dumps({}),
            },
        )


def _report_rows(engine, *, child_id: str, recipient_id: str | None = None) -> list[str]:
    """The ``source`` values of every report row for ``child_id``."""
    sql = (
        "SELECT source FROM message_queue WHERE source LIKE :prefix"
    )
    params = {"prefix": f"internal_report:{child_id}:%"}
    if recipient_id is not None:
        sql += " AND instance_id = :recipient"
        params["recipient"] = recipient_id
    with engine.begin() as conn:
        rows = conn.execute(text(sql), params).all()
    return [r[0] for r in rows]


def _seed_shape_b(
    engine,
    *,
    instance_id: str,
    parent_id: str | None = None,
    with_task: bool = True,
    with_message: bool = True,
    task_status: str = "completed",
    message_status: str | None = "completed",
    task_age: timedelta = OLD_ENOUGH,
    message_age: timedelta = OLD_ENOUGH,
) -> None:
    """Seed the Shape-B specimen: ``running`` posture, every work row
    terminal, completion evidence aged past the bound."""
    _insert_instance(
        engine, instance_id,
        status=InstanceStatus.RUNNING.value,
        parent_id=parent_id,
        agent_id="sonic-leader",
        last_activity_at=now_utc_naive() - timedelta(hours=30),
    )
    aged = datetime.now(timezone.utc)
    if with_task:
        _insert_task(
            engine,
            instance_id=instance_id,
            status=task_status,
            completed_at=(aged - task_age) if task_status == "completed" else None,
            work_id=f"work-{instance_id}",
        )
    if with_message:
        _insert_message(
            engine,
            instance_id=instance_id,
            status=message_status,
            completed_at=(aged - message_age) if message_status == "completed" else None,
            message_id=f"msg-{instance_id}",
        )


# ─────────────────────────────────────────────────────────────────────────────
# NIT-1 — the MINT-SITE guard is per-child PREFIX
# ─────────────────────────────────────────────────────────────────────────────


class TestNit1MintSitePrefixGuard:
    @pytest.mark.asyncio
    async def test_prior_pending_report_under_a_different_anchor_blocks_a_second(
        self, rider_harness,
    ):
        """THE PROBE: the child's prior report is still PENDING under a
        DIFFERENT anchor. The mint site must suppress the second
        report — the parent receives ONE report, and the child carries
        ONE obligation.

        At the pre-rider base the guard was anchor-exact, so a second
        report row was minted (the parent received 2 reports for 1
        child) — the pin is RED there.
        """
        engine = rider_harness.engine
        _insert_instance(
            engine, PARENT,
            status=InstanceStatus.IDLE.value,
            agent_id="leader",
        )
        _seed_shape_b(engine, instance_id=CHILD, parent_id=PARENT)
        # The PRIOR report — pending, under a DIFFERENT anchor.
        _insert_message(
            engine,
            instance_id=PARENT,
            status=MessageStatus.READY.value,
            message_id="msg-prior-report",
            content="prior report under anchor A",
            source=f"internal_report:{CHILD}:anchor-A",
        )

        with _patch_drive_seams():
            await rider_harness.service._process_child_completion_and_notify_parent(
                CHILD, "", skip_repair=True,
            )

        sources = _report_rows(engine, child_id=CHILD)
        assert len(sources) == 1, (
            f"expected exactly ONE report for the child, got {sources!r}"
        )
        assert sources[0] == f"internal_report:{CHILD}:anchor-A"

    @pytest.mark.asyncio
    async def test_a_genuinely_new_child_report_still_mints(
        self, rider_harness,
    ):
        """The fix must not turn the mint site into a blanket refusal: a
        child with NO prior report still gets exactly ONE."""
        engine = rider_harness.engine
        _insert_instance(
            engine, PARENT,
            status=InstanceStatus.IDLE.value,
            agent_id="leader",
        )
        _seed_shape_b(engine, instance_id=CHILD, parent_id=PARENT)

        with _patch_drive_seams():
            await rider_harness.service._process_child_completion_and_notify_parent(
                CHILD, "", skip_repair=True,
            )

        sources = _report_rows(engine, child_id=CHILD)
        assert len(sources) == 1, sources
        assert sources[0].startswith(f"internal_report:{CHILD}:")

    @pytest.mark.asyncio
    async def test_a_sibling_child_id_does_not_alias_onto_the_child(
        self, rider_harness,
    ):
        """The EXACT-BOUNDARY property must survive the prefix change: a
        sibling whose id EXTENDS the child's id does NOT suppress the
        child's report (the colon terminator anchors the boundary)."""
        engine = rider_harness.engine
        _insert_instance(
            engine, PARENT,
            status=InstanceStatus.IDLE.value,
            agent_id="leader",
        )
        _seed_shape_b(engine, instance_id=CHILD, parent_id=PARENT)
        # The SIBLING's report — its id extends the child's id.
        _insert_message(
            engine,
            instance_id=PARENT,
            status=MessageStatus.READY.value,
            message_id="msg-sibling-report",
            content="sibling report",
            source=f"internal_report:{SIBLING}:anchor-A",
        )

        with _patch_drive_seams():
            await rider_harness.service._process_child_completion_and_notify_parent(
                CHILD, "", skip_repair=True,
            )

        child_sources = _report_rows(engine, child_id=CHILD)
        assert len(child_sources) == 1, child_sources
        assert child_sources[0].startswith(f"internal_report:{CHILD}:")


# ─────────────────────────────────────────────────────────────────────────────
# NIT-2 — a NULL message status HOLDS the posture
# ─────────────────────────────────────────────────────────────────────────────


class TestNit2NullStatusHolds:
    @pytest.mark.asyncio
    async def test_a_null_status_result_holds_the_candidate(
        self, rider_harness, monkeypatch,
    ):
        """The CALLER must fail CLOSED on a ``None`` probe result.

        A ``None`` status is NOT proven-terminal (``is_proven_terminal_
        message_status`` returns False for it — the conservative
        unknown-status rule), so it MUST hold the posture. Under the
        old two-valued probe the caller compared against ``None`` and
        therefore read a NULL-status row as "nothing holding" — the
        fail-OPEN defect.

        The NULL-status row itself is UNREACHABLE in the shipped schema
        (``message_queue.status`` is NOT NULL), so the probe result is
        injected here — the leg's behaviour is what is pinned.
        """
        engine = rider_harness.engine
        _seed_shape_b(
            engine, instance_id=CHILD,
            with_task=True, with_message=False,
        )
        sweep = _make_sweep(rider_harness)
        monkeypatch.setattr(
            sweep, "_holding_message", lambda instance_id: None,
        )
        with _patch_drive_seams():
            result = await sweep.sweep_once()

        assert result.healed == 0, result
        assert result.skipped_holding_message == 1, result
        with Session(engine) as session:
            row = session.get(Instance, CHILD)
        assert row.status == InstanceStatus.RUNNING.value

    def test_probe_reports_no_holding_with_the_sentinel(self, rider_harness):
        """The probe's "nothing holds" answer is the SENTINEL, never
        ``None`` — ``None`` is a legitimate (holding) status value."""
        engine = rider_harness.engine
        _insert_instance(
            engine, CHILD,
            status=InstanceStatus.RUNNING.value,
            agent_id="sonic-leader",
        )
        sweep = _make_sweep(rider_harness)
        probe = sweep._holding_message(CHILD)
        assert probe is srs.NO_HOLDING_MESSAGE
        assert probe is not None

    def test_probe_returns_none_for_a_null_status_row(
        self, rider_harness, monkeypatch,
    ):
        """A NULL status row is REPORTED as ``None`` — the caller must
        still treat it as holding (it is not the sentinel).

        The row itself cannot exist in the shipped schema
        (``status`` is NOT NULL), so the probe's row set is injected.
        """
        rider_harness  # (harness fixture — keeps the engine alive)

        class _FakeResult:
            @staticmethod
            def all():
                return [(None,)]

        class _FakeSession:
            def __init__(self, *args, **kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def exec(self, statement):
                return _FakeResult()

        sweep = _make_sweep(rider_harness)
        monkeypatch.setattr(srs, "Session", _FakeSession)
        probe = sweep._holding_message(CHILD)
        assert probe is None
        assert probe is not srs.NO_HOLDING_MESSAGE


# ─────────────────────────────────────────────────────────────────────────────
# NIT-4 — the candidate ORDER BY must rank by the NEWEST evidence
# ─────────────────────────────────────────────────────────────────────────────


class TestNit4OrderByNewestEvidence:
    @pytest.mark.asyncio
    async def test_a_young_evidence_row_does_not_starve_an_eligible_row(
        self, rider_harness, monkeypatch,
    ):
        """THE CAP-STARVATION PROBE (cap = 1):

        * ``young`` — task evidence OLD, message evidence NEW ⇒ its
          NEWEST evidence is young ⇒ the age leg skips it;
        * ``eligible`` — task evidence old enough to clear the bound.

        Under the old ORDER BY (``COALESCE(task, message)``) the young
        row sorted FIRST (its task stamp is the older one), consumed
        the single cap slot, and the eligible row was never even
        selected — so it was never driven. The ORDER BY must rank by
        the NEWEST evidence, which puts the eligible row first.
        """
        monkeypatch.setenv(srs.BATCH_CAP_ENV, "1")
        engine = rider_harness.engine
        _seed_shape_b(
            engine, instance_id="young",
            with_task=True, with_message=True,
            task_age=timedelta(hours=10),
            message_age=TOO_YOUNG,
        )
        _seed_shape_b(
            engine, instance_id="eligible",
            with_task=True, with_message=False,
            task_age=OLD_ENOUGH,
        )

        sweep = _make_sweep(rider_harness)
        with _patch_drive_seams():
            result = await sweep.sweep_once()

        assert result.healed == 1, result
        with Session(engine) as session:
            young = session.get(Instance, "young")
            eligible = session.get(Instance, "eligible")
        assert eligible.status == InstanceStatus.COMPLETED.value
        # The young row was NOT driven (its newest evidence is too
        # recent) — but it did NOT consume the cap slot.
        assert young.status == InstanceStatus.RUNNING.value


# ─────────────────────────────────────────────────────────────────────────────
# NIT-6 — the typed reason is persisted on the ROW
# ─────────────────────────────────────────────────────────────────────────────


class TestNit6TypedReasonPersisted:
    @pytest.mark.asyncio
    async def test_the_terminal_reason_is_persisted_on_the_instance_row(
        self, rider_harness,
    ):
        """The typed reason must be recoverable from the DATA, not only
        from a log line."""
        engine = rider_harness.engine
        _seed_shape_b(engine, instance_id=CHILD)
        sweep = _make_sweep(rider_harness)
        with _patch_drive_seams():
            result = await sweep.sweep_once()
        assert result.healed == 1, result

        with Session(engine) as session:
            row = session.get(Instance, CHILD)
        assert row.status == InstanceStatus.COMPLETED.value
        assert row.instance_metadata.get(
            srs.TERMINAL_REASON_METADATA_KEY
        ) == srs.TERMINAL_REASON_VALUE

    def test_the_reason_metadata_key_is_content_free(self):
        """The persisted value is a short vocabulary token — never free
        text, never a transcript excerpt."""
        assert srs.TERMINAL_REASON_VALUE == "finalize_gap_steady_state"
        assert " " not in srs.TERMINAL_REASON_VALUE
        assert len(srs.TERMINAL_REASON_VALUE) <= 64


# ─────────────────────────────────────────────────────────────────────────────
# NIT-3 — the census naming docstring + the architecture note
# ─────────────────────────────────────────────────────────────────────────────


class TestNit3DocstringHonesty:
    def test_the_census_docstring_states_the_cap(self):
        """The census method's NAME promises a population; the
        implementation is a CAPPED sample. The docstring must say so."""
        doc = srs.StaleRunningSweepService.census_stale_running.__doc__ or ""
        assert "LOWER BOUND" in doc
        assert "cap" in doc.lower()

    def test_the_module_docstring_states_the_chain_is_the_net(self):
        """The sweep's legs deliberately do NOT cover the
        old-live-descendant class — the PRODUCTION CHAIN is the net for
        it. The module docstring must state that, so a reader never
        reads a still-running descendant as a missed heal."""
        doc = srs.__doc__ or ""
        assert "CHAIN IS THE NET FOR THE OLD-LIVE-DESCENDANT" in doc
        assert "old-live-descendant" in doc
