"""Integration — the revive epoch mint fires INSIDE the real enqueue path.

Proposal B PR1 (lane 13): a p1-HUMAN message landing on a TERMINAL
instance is the fresh-episode revive boundary — the existing
counter/ledger clears keep their exact behavior AND a ``mission_records``
revive epoch is minted IN THE SAME ``_prepare_enqueued_message``
transaction (savepoint-isolated; recorded-consequence posture).

Drives the REAL ``InstanceMessagingService.enqueue_message`` →
``_prepare_enqueued_message`` path (the
``test_attestation_epoch_clears_d5i`` harness shape — a MagicMock
manager shell around a REAL file-backed SQLite engine), asserting:

* the revive epoch row lands (``founding_source='revive'``,
  ``founding_message_id`` == the enqueued message id, parent chain to
  the anchor's prior epoch, epoch_seq monotonic);
* the instance's attestation columns/ledger clear EXACTLY as before
  (zero behavior change — the mint adds no new clear);
* the enqueue still returns a normal ``AsyncMessageResult`` (the mint
  never breaks the lane);
* mode ``off`` mints NOTHING;
* an internal (AGENT-typed) revive mints NOTHING (not a fresh episode).
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.pool import NullPool
from sqlmodel import SQLModel, Session, select

import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.mission_record.models  # noqa: F401
import daemon.repositories.message_queue.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401
from daemon.repositories.instance.models import Instance
from daemon.repositories.message_queue.models import MessageQueue
from daemon.repositories.mission_record.models import (
    MissionFoundingSource,
    MissionRecord,
)
from daemon.services.instance_messaging import InstanceMessagingService
from daemon.services.mission_record_service import (
    MISSION_ID_KEY,
    MISSION_RECORDS_MODE_ENV,
    MissionRecordService,
    _reset_mission_records_mode_for_tests,
)


@pytest.fixture(autouse=True)
def _reset_mode_cache():
    """The mode ladder is CACHED (F5 — the house switch pattern).

    Every test starts from a clean cache so the env it sets is the env
    that actually gets resolved (the ladder is restart-read).
    """
    _reset_mission_records_mode_for_tests()
    yield
    _reset_mission_records_mode_for_tests()


@pytest.fixture
def engine(tmp_path):
    eng = create_engine(
        f"sqlite:///{tmp_path / 'mission-revive-enqueue.sqlite'}",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )

    @event.listens_for(eng, "connect")
    def _configure_sqlite(dbapi_conn, _connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=10000")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    SQLModel.metadata.create_all(eng)
    yield eng
    eng.dispose()


def _seed_terminal_instance(engine, iid: str, *, prior_epochs: int = 0) -> str:
    """A TERMINAL instance with attestation debt + an optional prior epoch."""
    with Session(engine) as session:
        session.add(
            Instance(
                instance_id=iid,
                agent_id="leader",
                agent_dir=f"agents/leader/{iid[:8]}",
                status="completed",
                project_id=None,
                attestation_denied_count=2,
                instance_metadata={"attestation:denial_epochs": ["e1", "e2"]},
            )
        )
        for seq in range(prior_epochs):
            session.add(
                MissionRecord(
                    mission_id=str(uuid.uuid4()),
                    anchor_instance_id=iid,
                    epoch_seq=seq + 1,
                    founding_source="user_api",
                    status="settled",
                )
            )
        session.commit()
    return iid


@pytest.fixture
def messaging_service(engine):
    from unittest.mock import AsyncMock, MagicMock

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
    # The REAL service against the SAME engine — this is the wiring the
    # manager performs at boot (``self._mission_record_service`` plus
    # the public ``mission_record_service`` property the lanes read).
    _mission_service = MissionRecordService(engine)
    manager._mission_record_service = _mission_service
    manager.mission_record_service = _mission_service
    return InstanceMessagingService(
        manager=manager,
        cancellation_service=MagicMock(is_shutting_down=False),
    )


class TestReviveEpochMintInTheEnqueuePath:
    async def test_fresh_episode_revive_mints_the_epoch(
        self, engine, messaging_service
    ):
        iid = _seed_terminal_instance(engine, str(uuid.uuid4()))
        result = await messaging_service.enqueue_message(
            instance_id=iid,
            message="a fresh user mission",
            source="api",
        )

        # The lane result is a NORMAL enqueue result (the mint never
        # breaks the lane).
        assert result.message_id
        assert result.status == "queued"

        # The revive epoch landed — same transaction as the revive.
        with Session(engine) as session:
            epochs = list(
                session.exec(
                    select(MissionRecord).where(
                        MissionRecord.anchor_instance_id == iid
                    )
                )
            )
        assert len(epochs) == 1
        epoch = epochs[0]
        assert epoch.founding_source == MissionFoundingSource.REVIVE.value
        # The founding message is the message THIS enqueue minted.
        assert epoch.founding_message_id == result.message_id

        # ZERO behavior change: the instance clears EXACTLY as before.
        with Session(engine) as session:
            row = session.get(Instance, iid)
        assert row.status == "running"
        assert row.attestation_denied_count == 0
        assert row.completion_gate_escalated is False
        assert (row.instance_metadata or {}).get(
            "attestation:denial_epochs"
        ) == []

    async def test_revive_epoch_chains_to_the_prior_epoch(
        self, engine, messaging_service
    ):
        iid = _seed_terminal_instance(engine, str(uuid.uuid4()), prior_epochs=2)
        await messaging_service.enqueue_message(
            instance_id=iid, message="new mission", source="api"
        )
        with Session(engine) as session:
            epochs = list(
                session.exec(
                    select(MissionRecord)
                    .where(MissionRecord.anchor_instance_id == iid)
                    .order_by(MissionRecord.epoch_seq)  # type: ignore[attr-defined]
                )
            )
        assert len(epochs) == 3
        revive = epochs[-1]
        assert revive.epoch_seq == 3  # monotonic per anchor
        assert revive.parent_mission_id == epochs[-2].mission_id

    async def test_mode_off_mints_nothing(self, engine, messaging_service, monkeypatch):
        monkeypatch.setenv(MISSION_RECORDS_MODE_ENV, "off")
        iid = _seed_terminal_instance(engine, str(uuid.uuid4()))
        result = await messaging_service.enqueue_message(
            instance_id=iid, message="new mission", source="api"
        )
        assert result.message_id
        with Session(engine) as session:
            rows = list(session.exec(select(MissionRecord)))
        assert rows == []

    async def test_internal_agent_revive_mints_nothing(
        self, engine, messaging_service
    ):
        """An AGENT-typed source on a terminal instance is NOT a fresh
        episode (no clear) — and therefore NO revive epoch either."""
        iid = _seed_terminal_instance(engine, str(uuid.uuid4()))
        await messaging_service.enqueue_message(
            instance_id=iid,
            message="internal ping",
            source="internal_agent:someone",
        )
        with Session(engine) as session:
            rows = list(session.exec(select(MissionRecord)))
            row = session.get(Instance, iid)
        assert rows == []
        # the pre-existing contract: internal revives preserve the debt
        assert row.attestation_denied_count == 2
        assert row.status == "running"  # the revive still wakes it

    async def test_mission_id_kwarg_stamps_the_queue_row(
        self, engine, messaging_service
    ):
        """The carrier path: a lane-passed ``mission_id`` rides onto the
        MessageQueue row under the RESERVED key (the claim-path carrier)
        and the mission row attaches the founding message (mint-then-
        attach completed upstream by the lane; here the key is checked)."""
        iid = _seed_terminal_instance(engine, str(uuid.uuid4()))
        mission_id = str(uuid.uuid4())
        result = await messaging_service.enqueue_message(
            instance_id=iid,
            message="new mission",
            source="api",
            mission_id=mission_id,
        )
        with Session(engine) as session:
            row = session.exec(
                select(MessageQueue).where(
                    MessageQueue.message_id == result.message_id
                )
            ).first()
        assert row is not None
        assert (row.message_metadata or {}).get(MISSION_ID_KEY) == mission_id
