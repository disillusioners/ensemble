"""Unit tests — the amend-at-delivery TARGETING GUARD (PR2 round 1).

The guard
---------
The re-founding amendment (ruling ③) re-anchors the epoch on the
delivered p1-HUMAN user-lane message. The carrier names the mission
the delivery BELONGS TO — but a delivery can carry a FOREIGN mission
(a mission whose anchor is a DIFFERENT instance). Without a guard, a
worker's delivery would re-found the COMMISSIONER's epoch (moving the
commissioner's anchor to a worker's message id).

The guard: the amendment may only re-found the epoch whose
``anchor_instance_id`` IS the delivering instance. A foreign mission is
REFUSED and the delivery DEGRADES TO THE LEGACY LADDER (the gate keeps
deriving the anchor from the message ladder — today's behavior,
exactly). The guard is F2-bounded: the amend write itself is guarded on
the epoch being OPEN.

This file also carries the FACADE-FORWARDING guard for the two new
manager getters the amend path uses (``get_open_mission_id`` /
``get_mission_anchor_instance_id``) — the council's Minor 7: the amend
must not reach into ``manager._engine`` (a private attribute) but go
through the manager's public surface, and the new getters must FORWARD
their argument.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.pool import NullPool
from sqlmodel import SQLModel, Session

import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.mission_record.models  # noqa: F401
import daemon.repositories.message_queue.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401
from daemon.repositories.instance.models import Instance
from daemon.services.instance_messaging import InstanceMessagingService
from daemon.services.mission_record_reads import (
    read_mission_anchor_instance_id,
    read_open_mission_id,
)
from daemon.services.mission_record_service import (
    MISSION_RECORDS_MODE_ENV,
    MissionRecordService,
    _reset_mission_records_mode_for_tests,
)


@pytest.fixture(autouse=True)
def _reset_mode_cache():
    _reset_mission_records_mode_for_tests()
    yield
    _reset_mission_records_mode_for_tests()


@pytest.fixture
def engine(tmp_path: Path):
    eng = create_engine(
        f"sqlite:///{tmp_path / 'pr2-amend-guard.sqlite'}",
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


def _seed_instance(engine, iid: str) -> str:
    with Session(engine) as session:
        if session.get(Instance, iid) is None:
            session.add(
                Instance(
                    instance_id=iid,
                    agent_id="leader",
                    agent_dir=f"agents/leader/{iid[:8]}",
                    status="running",
                    project_id=None,
                )
            )
            session.commit()
    return iid


@pytest.fixture
def messaging_service(engine):
    manager = MagicMock()
    manager.engine = engine
    manager._engine = engine
    manager.write_guard = MagicMock()
    manager._live_hub = MagicMock()
    manager._live_hub.stream_status_change = AsyncMock()
    manager._worker_pool = MagicMock()
    manager._worker_pool.notify_work = MagicMock()
    manager._job_queue_service = MagicMock()
    manager._job_queue_service._repository = MagicMock()
    manager._job_queue_service._repository.stamp_message_id = MagicMock()
    mission_service = MissionRecordService(engine)
    manager._mission_record_service = mission_service
    manager.mission_record_service = mission_service
    # The PR2 facade getters — wired to the REAL reads (the same shape
    # the manager's own facades use).
    manager.get_open_mission_id = lambda iid: read_open_mission_id(
        engine, iid
    )
    manager.get_mission_anchor_instance_id = (
        lambda mid: read_mission_anchor_instance_id(engine, mid)
    )
    return InstanceMessagingService(
        manager=manager,
        cancellation_service=MagicMock(is_shutting_down=False),
    )


class TestTheAnchorInstanceGuard:
    async def test_a_foreign_mission_does_not_re_found_the_epoch(
        self, engine, messaging_service, monkeypatch
    ):
        """The council's shape: a delivery whose carrier names a
        FOREIGN mission (another instance's epoch) must NOT move that
        mission's anchor — the delivery degrades to the legacy ladder."""
        monkeypatch.setenv(MISSION_RECORDS_MODE_ENV, "dry")
        _reset_mission_records_mode_for_tests()

        commissioner = _seed_instance(engine, str(uuid.uuid4()))
        worker = _seed_instance(engine, str(uuid.uuid4()))
        service = messaging_service._manager.mission_record_service
        resolution = service.resolve_or_mint(
            commissioner, "user_api", founding_message_id="m-founding"
        ).mission_id

        # The WORKER's delivery carries the COMMISSIONER's mission.
        await messaging_service._amend_mission_anchor_at_delivery(
            instance_id=worker,
            message_id="m-worker-delivery",
            is_p1_human=True,
            mission_id=resolution,
        )

        # The commissioner's anchor is UNCHANGED (the foreign delivery
        # did not re-found it).
        from daemon.services.mission_record_reads import (
            read_founding_message_id,
        )

        assert (
            read_founding_message_id(engine, commissioner) == "m-founding"
        )

    async def test_the_own_mission_is_amended(
        self, engine, messaging_service, monkeypatch
    ):
        """The positive half: the instance's OWN epoch is re-found."""
        monkeypatch.setenv(MISSION_RECORDS_MODE_ENV, "dry")
        _reset_mission_records_mode_for_tests()

        anchor_instance = _seed_instance(engine, str(uuid.uuid4()))
        service = messaging_service._manager.mission_record_service
        resolution = service.resolve_or_mint(
            anchor_instance, "user_api", founding_message_id="m-founding"
        ).mission_id

        await messaging_service._amend_mission_anchor_at_delivery(
            instance_id=anchor_instance,
            message_id="m-correction",
            is_p1_human=True,
            mission_id=resolution,
        )

        from daemon.services.mission_record_reads import (
            read_founding_message_id,
        )

        assert (
            read_founding_message_id(engine, anchor_instance)
            == "m-correction"
        )

    async def test_the_no_carrier_fallback_uses_the_facade_getter(
        self, engine, messaging_service, monkeypatch
    ):
        """With no carrier, the amend resolves THIS instance's open
        epoch through the manager's PUBLIC facade (no private-attribute
        reach) and amends it."""
        monkeypatch.setenv(MISSION_RECORDS_MODE_ENV, "dry")
        _reset_mission_records_mode_for_tests()

        anchor_instance = _seed_instance(engine, str(uuid.uuid4()))
        service = messaging_service._manager.mission_record_service
        service.resolve_or_mint(
            anchor_instance, "user_api", founding_message_id="m-founding"
        )

        await messaging_service._amend_mission_anchor_at_delivery(
            instance_id=anchor_instance,
            message_id="m-correction",
            is_p1_human=True,
            mission_id=None,
        )

        from daemon.services.mission_record_reads import (
            read_founding_message_id,
        )

        assert (
            read_founding_message_id(engine, anchor_instance)
            == "m-correction"
        )


class TestTheManagerFacadeGetters:
    """Facade-forwarding guard (the council's Minor 7).

    The new getters are READ getters on the manager facade: they must
    FORWARD their argument to the read helper and return its value.
    The house style: a REAL call against a spied seam (not a source
    grep) — ``tests/unit/test_manager_enqueue_message_work_id_required.py``
    shape.
    """

    def test_get_open_mission_id_forwards_its_argument(self):
        from daemon.manager import InstanceManager

        manager = InstanceManager.__new__(InstanceManager)
        manager._engine = "engine-sentinel"
        sentinel = "mission-id-sentinel"
        spy = MagicMock(return_value=sentinel)
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "daemon.manager._read_open_mission_id", spy
            )
            result = manager.get_open_mission_id("inst-1")

        assert result == sentinel
        spy.assert_called_once_with("engine-sentinel", "inst-1")

    def test_get_mission_anchor_instance_id_forwards_its_argument(self):
        from daemon.manager import InstanceManager

        manager = InstanceManager.__new__(InstanceManager)
        manager._engine = "engine-sentinel"
        sentinel = "anchor-instance-sentinel"
        spy = MagicMock(return_value=sentinel)
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "daemon.manager._read_mission_anchor_instance_id", spy
            )
            result = manager.get_mission_anchor_instance_id("mission-1")

        assert result == sentinel
        spy.assert_called_once_with("engine-sentinel", "mission-1")

    def test_the_amend_path_does_not_reach_the_private_engine(self):
        """No private-attribute reach: the amend path goes through the
        manager's public facade."""
        import inspect

        from daemon.services import instance_messaging

        source = inspect.getsource(
            instance_messaging.InstanceMessagingService._amend_mission_anchor_at_delivery
        )
        assert "_manager._engine" not in source
