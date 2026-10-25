"""Unit tests — the tap-site stamps + the amend-at-delivery (PR2, ruling ③).

Two things ride the delivery side in Proposal B PR2:

* the MISSION STAMP at the FOUR remaining ``MessageTapSlot`` sites
  (``agent_node_return`` + the three compaction taps — PR1 landed the
  entry site + the carrier mechanism). The stamp is the CARRIED value
  (the producer stamped it on the delivered user message's kwargs);
  the tap READS it, it never re-derives it from message shapes;
* the RE-FOUNDING AMENDMENT, fired AT DELIVERY (the tap site): a
  delivered p1-HUMAN user-lane message during an OPEN epoch re-anchors
  the epoch. This is the ~14-minute-lag shape: the correction is
  delivered mid-long-turn, and the amendment is visible to the gate at
  would-be END (the anchor is a durable row, not a re-derived
  heuristic).

The lag-shape test is the load-bearing one: it drives the REAL
amendment seam and then reads the anchor through the REAL gate read
path — the assertion is that the gate's SOURCE U is the CORRECTION,
not the stale founding message.
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from sqlalchemy import create_engine, event
from sqlalchemy.pool import NullPool
from sqlmodel import SQLModel, Session

import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.mission_record.models  # noqa: F401
import daemon.repositories.message_queue.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401
from daemon.repositories.instance.models import Instance
from daemon.repositories.mission_record.models import MissionRecord
from daemon.services.attestation_gate import DEFAULT_GATE_SETTINGS, evaluate
from daemon.services.instance_messaging import InstanceMessagingService
from daemon.services.message_tap import resolve_turn_mission_id
from daemon.services.mission_record_reads import (
    read_founding_message_id,
    read_mission_anchor_instance_id,
    read_open_mission_id,
)
from daemon.services.mission_record_service import (
    MISSION_RECORDS_MODE_ENV,
    MissionRecordService,
    _reset_mission_records_mode_for_tests,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _reset_mode_cache():
    _reset_mission_records_mode_for_tests()
    yield
    _reset_mission_records_mode_for_tests()


@pytest.fixture
def engine(tmp_path: Path):
    eng = create_engine(
        f"sqlite:///{tmp_path / 'pr2-tap-amend.sqlite'}",
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


def _seed_instance(engine, iid: str, status: str = "running") -> str:
    with Session(engine) as session:
        if session.get(Instance, iid) is None:
            session.add(
                Instance(
                    instance_id=iid,
                    agent_id="leader",
                    agent_dir=f"agents/leader/{iid[:8]}",
                    status=status,
                    project_id=None,
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
    mission_service = MissionRecordService(engine)
    manager._mission_record_service = mission_service
    manager.mission_record_service = mission_service
    # The PR2 facade getters the amend path uses (the real reads — the
    # manager's own facade shape).
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


class TestCarrierReader:
    def test_the_carried_stamp_is_read(self):
        messages = [
            HumanMessage(content="hello", id="m-1", additional_kwargs={}),
            HumanMessage(
                content="hi",
                id="m-2",
                additional_kwargs={"mission_id": "mission-7"},
            ),
        ]
        assert resolve_turn_mission_id(messages) == "mission-7"

    def test_the_most_recent_carrier_wins(self):
        messages = [
            HumanMessage(
                content="a",
                id="m-1",
                additional_kwargs={"mission_id": "mission-1"},
            ),
            HumanMessage(
                content="b",
                id="m-2",
                additional_kwargs={"mission_id": "mission-2"},
            ),
        ]
        assert resolve_turn_mission_id(messages) == "mission-2"

    def test_no_carrier_reads_as_none(self):
        messages = [HumanMessage(content="hello", id="m-1")]
        assert resolve_turn_mission_id(messages) is None
        assert resolve_turn_mission_id([]) is None
        assert resolve_turn_mission_id(None) is None


class TestTapSitesCarryTheMission:
    """The FOUR remaining tap sites must pass a mission id."""

    def _tap_calls(self, source: str) -> list[str]:
        # The call sites are the ``await <slot>.tap_node_return(`` calls.
        return re.findall(
            r"\.tap_node_return\((?:[^()]|\([^()]*\))*\)",
            source,
        )

    def test_every_graph_tap_site_stamps_the_mission(self):
        source = (REPO_ROOT / "daemon/graph.py").read_text()
        calls = self._tap_calls(source)
        assert len(calls) == 3, calls
        for call in calls:
            assert "mission_id=" in call, call

    def test_the_messaging_tap_sites_stamp_the_mission(self):
        source = (REPO_ROOT / "daemon/services/instance_messaging.py").read_text()
        calls = self._tap_calls(source)
        assert len(calls) == 2, calls
        for call in calls:
            assert "mission_id=" in call, call


class TestTheAmendmentIsNotNestedInTheTapGuard:
    """The amendment must NOT be gated on the message-metadata repo.

    The re-founding amendment (ruling ③) needs the mission service +
    the carried mission id — NOT the ``message_metadata`` side table.
    Nesting it under the tap's ``if graph_input is not None and
    ...message_metadata_repo is not None`` guard silently DISABLED the
    anchor amendment for every delivery that has no metadata repo (and
    for every checkpoint resume, where ``graph_input`` is None).

    The guard is an AST scan (the house pattern — the same shape as
    ``test_message_metadata_hook_placement.py``): the call must not sit
    inside an ``if`` whose test mentions the metadata repo.
    """

    def test_the_amend_call_is_not_under_the_metadata_repo_guard(self):
        import ast
        from pathlib import Path as _Path

        source_path = (
            _Path(__file__).resolve().parents[2]
            / "daemon/services/instance_messaging.py"
        )
        tree = ast.parse(source_path.read_text())

        calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "_amend_mission_anchor_at_delivery"
        ]
        assert calls, "the amend-at-delivery call site must exist"

        for call in calls:
            for parent in ast.walk(tree):
                if not isinstance(parent, ast.If):
                    continue
                if not any(child is call for child in ast.walk(parent)):
                    continue
                test_source = ast.unparse(parent.test)
                assert "message_metadata_repo" not in test_source, (
                    "the amend-at-delivery call is nested under the "
                    f"metadata-repo guard ({test_source}) — the amendment "
                    "does not need the metadata repo and must not be "
                    "disabled by its absence"
                )


class TestAmendAtDelivery:
    async def test_a_p1_human_delivery_amends_the_anchor(
        self, engine, messaging_service, monkeypatch
    ):
        monkeypatch.setenv(MISSION_RECORDS_MODE_ENV, "dry")
        _reset_mission_records_mode_for_tests()
        iid = _seed_instance(engine, str(uuid.uuid4()))
        service = messaging_service._manager.mission_record_service
        resolution = service.resolve_or_mint(
            iid, "user_api", founding_message_id="m-founding"
        ).mission_id
        assert resolution

        await messaging_service._amend_mission_anchor_at_delivery(
            instance_id=iid,
            message_id="m-correction",
            is_p1_human=True,
            mission_id=resolution,
        )
        assert (
            read_founding_message_id(engine, iid) == "m-correction"
        )

    async def test_an_internal_delivery_does_not_amend(
        self, engine, messaging_service, monkeypatch
    ):
        monkeypatch.setenv(MISSION_RECORDS_MODE_ENV, "dry")
        _reset_mission_records_mode_for_tests()
        iid = _seed_instance(engine, str(uuid.uuid4()))
        service = messaging_service._manager.mission_record_service
        resolution = service.resolve_or_mint(
            iid, "user_api", founding_message_id="m-founding"
        )

        await messaging_service._amend_mission_anchor_at_delivery(
            instance_id=iid,
            message_id="m-internal",
            is_p1_human=False,
            mission_id=resolution,
        )
        assert read_founding_message_id(engine, iid) == "m-founding"

    async def test_mode_off_amends_nothing(
        self, engine, messaging_service, monkeypatch
    ):
        monkeypatch.setenv(MISSION_RECORDS_MODE_ENV, "off")
        _reset_mission_records_mode_for_tests()
        iid = _seed_instance(engine, str(uuid.uuid4()))
        service = messaging_service._manager.mission_record_service
        resolution = service.resolve_or_mint(
            iid, "user_api", founding_message_id="m-founding"
        )

        await messaging_service._amend_mission_anchor_at_delivery(
            instance_id=iid,
            message_id="m-correction",
            is_p1_human=True,
            mission_id=resolution,
        )
        assert read_founding_message_id(engine, iid) == "m-founding"

    async def test_the_closed_epoch_is_not_amended(
        self, engine, messaging_service, monkeypatch
    ):
        monkeypatch.setenv(MISSION_RECORDS_MODE_ENV, "dry")
        _reset_mission_records_mode_for_tests()
        iid = _seed_instance(engine, str(uuid.uuid4()))
        service = messaging_service._manager.mission_record_service
        resolution = service.resolve_or_mint(
            iid, "user_api", founding_message_id="m-founding"
        ).mission_id
        service._repo.transition_epoch_status(
            resolution, "settled"
        )

        await messaging_service._amend_mission_anchor_at_delivery(
            instance_id=iid,
            message_id="m-correction",
            is_p1_human=True,
            mission_id=resolution,
        )
        # The closed epoch's anchor is immutable (F2).
        with Session(engine) as session:
            row = session.get(MissionRecord, resolution)
        assert row.founding_message_id == "m-founding"


class TestTheLagSeamShape:
    """THE 14-MINUTE-LAG SHAPE.

    A correction is delivered mid-long-turn. At would-be END the gate
    must see the CORRECTION as the mission's anchor — the amendment is
    a durable row, so it is visible even though the window's CONTENT is
    still being drained.
    """

    async def test_the_correction_is_the_anchor_at_would_be_end(
        self, engine, messaging_service, monkeypatch
    ):
        monkeypatch.setenv(MISSION_RECORDS_MODE_ENV, "enforce")
        _reset_mission_records_mode_for_tests()
        iid = _seed_instance(engine, str(uuid.uuid4()))
        service = messaging_service._manager.mission_record_service
        resolution = service.resolve_or_mint(
            iid, "user_api", founding_message_id="m-founding"
        ).mission_id
        worker_id = str(uuid.uuid4())
        _seed_instance(engine, worker_id)
        service.mint_commission(
            resolution,
            commissioner_instance_id=iid,
            worker_instance_id=worker_id,
            lane="send_message",
        )

        # The long turn is still running: the leader's messages are the
        # founding request plus its work.
        messages = [
            HumanMessage(content="the original request", id="m-founding"),
            AIMessage(content="Working on it.", id="a-1"),
        ]

        class _Manager:
            def __init__(self, eng):
                self._engine = eng

            def count_pending_children(self, _iid):
                return 0

            def get_queued_or_expected_wakeups(self, _iid):
                return 0

            def count_live_descendants(self, _iid):
                return 0

            def count_busy_descendants(self, _iid):
                return 0

            def has_open_user_answer(self, _iid):
                return False

            def mission_has_commissions(self, iid):
                from daemon.services.mission_record_reads import (
                    read_mission_census,
                )

                return read_mission_census(self._engine, iid)

            def get_mission_founding_message_id(self, iid):
                return read_founding_message_id(self._engine, iid)

            def count_outstanding_commissions(self, iid):
                from daemon.services.mission_record_reads import (
                    count_outstanding_commissions,
                )

                return count_outstanding_commissions(self._engine, iid)

        manager = _Manager(engine)

        # BEFORE the correction is delivered: the anchor is the
        # founding message.
        before = evaluate(
            iid, 0, messages, DEFAULT_GATE_SETTINGS, manager
        )
        assert before.resolver is not None
        bundle_before = before.resolver.result.bundle
        assert bundle_before is not None
        assert "the original request" in bundle_before.text

        # The correction is DELIVERED mid-turn (the tap site fires the
        # amendment).
        await messaging_service._amend_mission_anchor_at_delivery(
            instance_id=iid,
            message_id="m-correction",
            is_p1_human=True,
            mission_id=resolution,
        )

        # The correction joins the window (it was delivered), and the
        # gate now anchors on IT — not on the stale founding message.
        messages_after = messages + [
            HumanMessage(content="the correction", id="m-correction"),
            AIMessage(content="Everything is complete.", id="a-2"),
        ]
        after = evaluate(
            iid, 0, messages_after, DEFAULT_GATE_SETTINGS, manager
        )
        assert after.resolver is not None
        bundle_after = after.resolver.result.bundle
        assert bundle_after is not None
        assert "the correction" in bundle_after.text
        assert "the original request" not in bundle_after.text
