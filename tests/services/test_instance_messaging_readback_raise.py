"""D4(ii/iii) — the enqueue read-back guard aborts BEFORE any row is written.

Before this guard the target-instance existence check lived AFTER the
``MessageQueue`` insert and only LOGGED a warning: the enqueue proceeded
and left an ORPHAN ``MessageQueue`` row (plus its ``Task`` row) for an
instance that does not exist — and the SENDER never learned about it
(D4(iii) masking).

The guard is hoisted ABOVE the insert, so a missing target aborts
cleanly:

* a ``ValueError`` is raised (fail-CLOSED),
* ZERO ``MessageQueue`` rows are written (nothing to compensate),
* the message surfaces at the tool boundary so the mis-address is
  visible to the sender.

The check-vs-commit race backstop (the WARNING branch further down the
transaction) is KEPT — it covers the narrow window where the instance
row is deleted between the check and the commit.

Engine: the in-memory SQLite ``engine`` fixture from
``tests/services/conftest.py``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlmodel import Session, select

from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.message_queue.models import MessageQueue
from daemon.repositories.task.models import Task
from daemon.services.instance_messaging import InstanceMessagingService


_EXISTING_INSTANCE_ID = "inst-existing-1"
_MISSING_INSTANCE_ID = "inst-missing-1"


@pytest.fixture
def messaging_service(engine) -> InstanceMessagingService:
    """An ``InstanceMessagingService`` over the in-memory engine."""
    manager = MagicMock()
    manager.engine = engine
    manager.write_guard = MagicMock()
    manager._live_hub = MagicMock()
    manager._live_hub.stream_status_change = AsyncMock()
    manager._worker_pool = MagicMock()
    manager._worker_pool.notify_work = MagicMock()
    manager._job_queue_service = MagicMock()
    manager._job_queue_service._repository = MagicMock()
    manager._job_queue_service._repository.stamp_message_id = MagicMock()
    return InstanceMessagingService(
        manager=manager,
        cancellation_service=MagicMock(is_shutting_down=False),
    )


def _seed_instance(engine, instance_id: str) -> None:
    with Session(engine) as session:
        session.add(
            Instance(
                instance_id=instance_id,
                agent_id="agent-worker",
                agent_name="worker",
                agent_dir="/tmp/worker",
                parent_id=None,
                status=InstanceStatus.IDLE.value,
                version=1,
                instance_metadata={},
            )
        )
        session.commit()
    # Spawn-verification: re-fetch the row in a FRESH session and confirm
    # it exists before the test body relies on it.
    with Session(engine) as session:
        assert session.get(Instance, instance_id) is not None


def _message_rows(engine) -> list[MessageQueue]:
    with Session(engine) as session:
        return list(session.exec(select(MessageQueue)).all())


def _task_rows(engine) -> list[Task]:
    with Session(engine) as session:
        return list(session.exec(select(Task)).all())


class TestReadBackGuard:
    async def test_missing_target_raises_before_any_row_is_written(
        self, engine, messaging_service
    ):
        with pytest.raises(ValueError) as excinfo:
            await messaging_service.enqueue_message(
                instance_id=_MISSING_INSTANCE_ID,
                message="hello",
                source="api",
            )

        message = str(excinfo.value)
        assert _MISSING_INSTANCE_ID in message
        assert "not found" in message
        # The clean abort: NOTHING was written.
        assert _message_rows(engine) == []
        assert _task_rows(engine) == []

    async def test_existing_target_still_enqueues(self, engine, messaging_service):
        """Control — the guard is not unconditional: a real target still
        gets exactly one MessageQueue row and one Task row."""
        _seed_instance(engine, _EXISTING_INSTANCE_ID)

        result = await messaging_service.enqueue_message(
            instance_id=_EXISTING_INSTANCE_ID,
            message="hello",
            source="api",
        )

        assert result.status == "queued"
        assert len(_message_rows(engine)) == 1
        assert len(_task_rows(engine)) == 1

    async def test_the_guard_does_not_depend_on_the_instance_status(
        self, engine, messaging_service
    ):
        """A TERMINAL target is still addressable (agents legitimately
        message completed instances) — the guard is about EXISTENCE, not
        status."""
        _seed_instance(engine, _EXISTING_INSTANCE_ID)
        with Session(engine) as session:
            instance = session.get(Instance, _EXISTING_INSTANCE_ID)
            instance.status = InstanceStatus.COMPLETED.value
            session.commit()

        result = await messaging_service.enqueue_message(
            instance_id=_EXISTING_INSTANCE_ID,
            message="hello",
            source="api",
        )

        assert result.status == "queued"
        assert len(_message_rows(engine)) == 1
