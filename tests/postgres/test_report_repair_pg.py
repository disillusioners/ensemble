"""PostgreSQL parity test for unhappy-path report repair.

Verifies that the report repair path in ``ChildReportsService._get_last_assistant_message_raw``
works correctly when the service is backed by a real PostgreSQL engine. The
repair logic itself is DB-agnostic (operates on message dicts), but this test
exercises ``_get_last_assistant_message_raw`` against a real PG-backed
``Instance`` row — confirming the in-memory repair path remains stable when
real PG instance rows are present (the ``get_instance_messages`` path is
mocked at the call site because LangGraph's ``AsyncPostgresSaver`` requires a
running checkpointer).

NOTE (S8, 2026-08-08): these tests do NOT exercise the full
``_process_child_completion_and_notify_parent`` delivery path — they only
call ``_get_last_assistant_message_raw`` directly. The persistence path is
covered separately by the unit-test ``TestEndToEndPersistence`` class in
``tests/unit/test_report_repair.py`` which wraps the raw output through
``_get_last_assistant_message`` (the function used at line 1309 to write to
``MessageQueue.content``).

Run with::

    pytest tests/postgres/test_report_repair_pg.py \\
        -v -m postgres --override-ini="addopts="

The ``pg_engine`` fixture in ``tests/postgres/conftest.py`` skips the entire
module cleanly when PostgreSQL is unreachable, so this file is safe to collect
even on machines without a running PG.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlmodel import Session

from daemon.config import Config
from daemon.repositories.event.models import Event  # noqa: F401
from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.message_queue.models import (  # noqa: F401
    MessageQueue,
    MessageStatus,
    MessageType,
)
from daemon.repositories.task.models import Task  # noqa: F401
from daemon.services.child_reports import ChildReportsService
from daemon.write_pause_guard import WritePauseGuard


# Auto-apply the postgres marker so ``pytest -m postgres`` selects these
# tests and the default ``-m 'not integration and not postgres'`` addopts
# skips them unless overridden.
pytestmark = pytest.mark.postgres


# =============================================================================
# Helpers
# =============================================================================


def _build_service(engine: Engine) -> tuple[ChildReportsService, MagicMock]:
    """Build a ``ChildReportsService`` with a mock manager on a real PG engine.

    Returns ``(service, manager)``. The manager uses the PG engine for
    ``WriteGuardSession`` but has the checkpointer mocked (LangGraph's
    ``AsyncPostgresSaver`` API is mocked at the ``get_instance_messages``
    call site).
    """
    manager = MagicMock(name="InstanceManager")
    manager.engine = engine
    manager.write_guard = WritePauseGuard()
    # S8 fix: ``_checkpointer`` is a @property on ChildReportsService that returns
    # ``adapter.raw_saver`` when adapter is truthy, else None. The mocked
    # ``get_instance_messages`` patch is only invoked when the function reaches
    # line 1225's ``if self._checkpointer:`` branch. Setting the mock to None
    # short-circuits the property and bypasses the mock entirely, so the test
    # receives ``messages=[]`` and returns None (causing all 3 tests to fail).
    # Use a non-None MagicMock so the property yields a truthy ``raw_saver``
    # and the mocked ``get_instance_messages`` is actually consulted.
    manager._checkpointer = MagicMock(name="CheckpointerAdapter")
    manager._live_hub = None
    manager._queue_repository = MagicMock()
    manager._instance_repository = MagicMock()
    manager._task_repo = None
    manager._worker_pool = None
    manager.config = Config()

    service = ChildReportsService.__new__(ChildReportsService)
    service._manager = manager
    service._events_service = None
    return service, manager


def _seed_instance(
    engine: Engine,
    *,
    instance_id: str,
    parent_id: str | None = None,
    status: str = InstanceStatus.RUNNING.value,
    agent_id: str = "worker",
) -> Instance:
    """Insert an Instance row on PG."""
    inst = Instance(
        instance_id=instance_id,
        agent_id=agent_id,
        agent_dir=f"/tmp/{agent_id}",
        parent_id=parent_id,
        status=status,
        version=1,
        created_at=datetime.now(timezone.utc).isoformat(),
        updated_at=datetime.now(timezone.utc).isoformat(),
        instance_metadata={},
    )
    with Session(engine) as session:
        session.add(inst)
        session.commit()
        session.refresh(inst)
    return inst


def _count_report_messages(engine: Engine, *, parent_id: str) -> int:
    """Count completion report messages enqueued for ``parent_id``."""
    with Session(engine) as session:
        stmt = (
            select(func.count())
            .select_from(MessageQueue)
            .where(MessageQueue.instance_id == parent_id)
            .where(MessageQueue.type == MessageType.COMPLETION_REPORT.value)
        )
        return int(session.scalar(stmt) or 0)


def _read_report_content(engine: Engine, *, parent_id: str) -> str | None:
    """Read the content of the most recent completion report for ``parent_id``."""
    with Session(engine) as session:
        stmt = (
            select(MessageQueue.content)
            .where(MessageQueue.instance_id == parent_id)
            .where(MessageQueue.type == MessageType.COMPLETION_REPORT.value)
            .order_by(MessageQueue.created_at.desc())
            .limit(1)
        )
        return session.scalar(stmt)


_LONG = " ".join(f"word{i}" for i in range(50))
_SHORT = "done"


# =============================================================================
# Tests
# =============================================================================


@pytest.mark.asyncio
async def test_pg_unhappy_path_repair_sends_repaired_report(pg_engine):
    """PG-backed: truncated child → ``_get_last_assistant_message_raw`` returns repaired text.

    Seeds a child + parent on PG to confirm the in-memory repair path stays
    stable when real PG instance rows exist. Verifies the raw output is the
    repaired text, not just the short sign-off. This test does NOT verify
    that the repaired content reaches the ``ReportInjection.content``
    persistence layer — that is covered by the ``TestEndToEndPersistence``
    unit tests (mocked checkpointer, no PG required).
    """
    parent_id = "pg-parent-repair-001"
    child_id = "pg-child-repair-001"

    _seed_instance(pg_engine, instance_id=parent_id, agent_id="leader")
    _seed_instance(
        pg_engine,
        instance_id=child_id,
        parent_id=parent_id,
        agent_id="worker",
    )

    service, manager = _build_service(pg_engine)

    messages = [
        {"role": "assistant", "content": _LONG},
        {"role": "assistant", "content": _LONG},
        {"role": "assistant", "content": _SHORT},
    ]

    mock_llm_instance = MagicMock()
    mock_llm_instance.invoke = MagicMock(
        return_value=MagicMock(content="Repaired: full report content from PG test.")
    )
    mock_llm_class = MagicMock(return_value=mock_llm_instance)

    with (
        patch(
            "daemon.services.child_reports.get_instance_messages",
            new=AsyncMock(return_value=messages),
        ),
        patch(
            "daemon.services.child_reports.ThinkingChatOpenAI",
            mock_llm_class,
        ),
    ):
        result = await service._get_last_assistant_message_raw(child_id)

    assert result is not None
    assert result != _SHORT
    assert "full report content" in result
    mock_llm_class.assert_called_once()


@pytest.mark.asyncio
async def test_pg_unhappy_path_combine_fallback(pg_engine):
    """PG-backed: LLM fails → ``_get_last_assistant_message_raw`` combine fallback runs.

    Confirms the combine-fallback path includes the substantive content
    from earlier messages rather than the short sign-off. Does NOT verify
    delivery to ``ReportInjection.content`` — that is covered by the
    ``TestEndToEndPersistence`` unit tests.
    """
    parent_id = "pg-parent-repair-002"
    child_id = "pg-child-repair-002"

    _seed_instance(pg_engine, instance_id=parent_id, agent_id="leader")
    _seed_instance(
        pg_engine,
        instance_id=child_id,
        parent_id=parent_id,
        agent_id="worker",
    )

    service, manager = _build_service(pg_engine)

    messages = [
        # Spec (2026-08-08): no earlier_wc floor — substantive earlier
        # messages trigger the truncation heuristic purely on the 2× ratio.
        # Padding to >=20 words mirrors the W5-era setup but is no longer
        # required to trip the heuristic; kept for readability.
        {"role": "assistant", "content": "alpha detailed findings report content padding word word word word word word word word word word word word word word word word word"},
        {"role": "assistant", "content": "beta implementation details report content padding word word word word word word word word word word word word word word word word word"},
        {"role": "assistant", "content": _SHORT},
    ]

    # LLM raises → combine fallback
    mock_llm_class = MagicMock(side_effect=RuntimeError("PG test: LLM unavailable"))

    with (
        patch(
            "daemon.services.child_reports.get_instance_messages",
            new=AsyncMock(return_value=messages),
        ),
        patch(
            "daemon.services.child_reports.ThinkingChatOpenAI",
            mock_llm_class,
        ),
    ):
        result = await service._get_last_assistant_message_raw(child_id)

    assert result is not None
    assert result != _SHORT
    assert "alpha detailed findings" in result
    assert "beta implementation details" in result


@pytest.mark.asyncio
async def test_pg_happy_path_returns_last_message(pg_engine):
    """PG-backed: similar-size messages → ``_get_last_assistant_message_raw`` returns last message, no repair.

    Confirms the happy-path short-circuit still fires when PG instance rows
    are present. Does NOT verify delivery to ``ReportInjection.content`` —
    that is covered by the ``TestEndToEndPersistence`` unit tests.
    """
    parent_id = "pg-parent-repair-003"
    child_id = "pg-child-repair-003"

    _seed_instance(pg_engine, instance_id=parent_id, agent_id="leader")
    _seed_instance(
        pg_engine,
        instance_id=child_id,
        parent_id=parent_id,
        agent_id="worker",
    )

    service, manager = _build_service(pg_engine)

    messages = [
        {"role": "assistant", "content": "medium step one content"},
        {"role": "assistant", "content": "medium step two content"},
        {"role": "assistant", "content": "I completed the task successfully here."},
    ]

    mock_llm_class = MagicMock()

    with (
        patch(
            "daemon.services.child_reports.get_instance_messages",
            new=AsyncMock(return_value=messages),
        ),
        patch(
            "daemon.services.child_reports.ThinkingChatOpenAI",
            mock_llm_class,
        ),
    ):
        result = await service._get_last_assistant_message_raw(child_id)

    assert result == "I completed the task successfully here."
    mock_llm_class.assert_not_called()
