"""Unit tests — the mission-record READ side (Proposal B PR2).

File-backed SQLite per the repo recipe (the PR1 test file's fixture).

Covers the read primitives the PR2 flip sites consume:

* the census read (``read_mission_census``): True / False / None
  ("no record" / "read failure");
* the SOURCE U anchor read (``read_founding_message_id``): the epoch's
  founding message id, and the honest-NULL case;
* the 5th R2 input's count (``count_outstanding_commissions``): the
  REAL zero, the positive count, and the ``-1`` "unknown" sentinel;
* the commission list read (the judge's SOURCE C union half);
* the mode gate (``mission_records_enforce_enabled``): off/dry ⇒ the
  reads are INACTIVE, enforce ⇒ armed;
* the fail-open CONTRACT: a broken engine never raises and never
  degrades to a ``0`` default (convention #3).

The FAIL-OPEN SENTINEL PINS are the load-bearing part: a ``-1`` must
never be confused with a ``0`` (a quiet mission) and a ``None`` census
must never be confused with a ``False`` census (a mission that never
delegated).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.pool import NullPool
from sqlmodel import SQLModel, Session

import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.mission_record.models  # noqa: F401
from daemon.repositories.instance.models import Instance
from daemon.repositories.mission_record.models import CommissionState
from daemon.services.mission_record_reads import (
    MISSION_READ_UNKNOWN,
    count_outstanding_commissions,
    list_outstanding_commissions,
    mission_records_enforce_enabled,
    read_founding_message_id,
    read_mission_census,
    read_mission_id,
    read_open_mission,
)
from daemon.services.mission_record_service import (
    MISSION_RECORDS_MODE_ENV,
    MissionRecordMode,
    MissionRecordService,
    _reset_mission_records_mode_for_tests,
)


ANCHOR = "11111111-1111-4111-8111-111111111111"
WORKER = "22222222-2222-4222-8222-222222222222"


@pytest.fixture
def engine(tmp_path: Path):
    eng = create_engine(
        f"sqlite:///{tmp_path / 'mission-reads.sqlite'}",
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


@pytest.fixture(autouse=True)
def _reset_mode():
    _reset_mission_records_mode_for_tests()
    yield
    _reset_mission_records_mode_for_tests()


def _make_instance(engine, instance_id: str, status: str = "running") -> None:
    with Session(engine) as session:
        if session.get(Instance, instance_id) is None:
            session.add(
                Instance(
                    instance_id=instance_id,
                    agent_id="leader",
                    agent_dir=f"agents/leader/{instance_id[:8]}",
                    status=status,
                    project_id=None,
                )
            )
            session.commit()


@pytest.fixture
def seeded(engine):
    """Two instances + a service."""
    _make_instance(engine, ANCHOR)
    _make_instance(engine, WORKER)
    return MissionRecordService(engine)


class TestModeGate:
    def test_enforce_is_the_only_armed_mode(self, monkeypatch):
        monkeypatch.setenv(MISSION_RECORDS_MODE_ENV, "enforce")
        _reset_mission_records_mode_for_tests()
        assert mission_records_enforce_enabled() is True

    def test_dry_and_off_are_not_armed(self, monkeypatch):
        for mode in ("dry", "off"):
            monkeypatch.setenv(MISSION_RECORDS_MODE_ENV, mode)
            _reset_mission_records_mode_for_tests()
            assert mission_records_enforce_enabled() is False

    def test_unknown_token_degrades_to_not_armed(self, monkeypatch):
        monkeypatch.setenv(MISSION_RECORDS_MODE_ENV, "shipped-yesterday")
        _reset_mission_records_mode_for_tests()
        # Never raises on the gate hot path (the resolver raises loudly
        # at the boot site — this helper must not).
        assert mission_records_enforce_enabled() is False


class TestCensusRead:
    def test_no_record_reads_as_none(self, seeded, engine):
        # None = "no answer" → the caller uses the LEGACY delegation
        # scan. NEVER False (that would un-arm the gate).
        assert read_mission_census(engine, ANCHOR) is None

    def test_open_mission_with_a_commission_reads_true(self, seeded, engine):
        resolution = seeded.resolve_or_mint(ANCHOR, "user_api")
        seeded.mint_commission(
            resolution.mission_id,
            commissioner_instance_id=ANCHOR,
            worker_instance_id=WORKER,
            lane="send_message",
        )
        assert read_mission_census(engine, ANCHOR) is True

    def test_open_mission_without_a_commission_reads_false(self, seeded, engine):
        seeded.resolve_or_mint(ANCHOR, "user_api")
        assert read_mission_census(engine, ANCHOR) is False

    def test_a_broken_engine_reads_as_none_not_false(self, seeded):
        class _Boom:
            def find_open_epoch_by_anchor(self, *a, **k):
                raise RuntimeError("db is down")

        # The read must not raise and must not answer False.
        with pytest.MonkeyPatch.context() as mp:
            import daemon.services.mission_record_reads as reads

            mp.setattr(
                reads,
                "_repo",
                lambda engine: _Boom(),
            )
            assert reads.read_mission_census(object(), ANCHOR) is None


class TestAnchorRead:
    def test_no_record_reads_as_none(self, seeded, engine):
        assert read_founding_message_id(engine, ANCHOR) is None

    def test_the_founding_message_id_is_returned(self, seeded, engine):
        seeded.resolve_or_mint(
            ANCHOR, "user_api", founding_message_id="msg-1"
        )
        assert read_founding_message_id(engine, ANCHOR) == "msg-1"

    def test_an_honest_null_founding_reads_as_none(self, seeded, engine):
        # The boot / backfill epoch has NO founding message — an honest
        # null, not a heuristic miss. The caller falls back to the
        # legacy ladder.
        seeded.mint_boot_epoch_if_none(ANCHOR)
        assert read_founding_message_id(engine, ANCHOR) is None

    def test_the_amended_anchor_is_read_back(self, seeded, engine):
        resolution = seeded.resolve_or_mint(
            ANCHOR, "user_api", founding_message_id="msg-1"
        )
        assert seeded.amend_anchor(resolution.mission_id, "msg-2") is True
        assert read_founding_message_id(engine, ANCHOR) == "msg-2"


class TestOutstandingCount:
    def test_no_record_is_a_real_zero(self, seeded, engine):
        # A REAL zero (not the unknown sentinel): no mission ⇒ no
        # outstanding commissions ⇒ quiet.
        assert count_outstanding_commissions(engine, ANCHOR) == 0
        assert count_outstanding_commissions(engine, ANCHOR) != (
            MISSION_READ_UNKNOWN
        )

    def test_an_outstanding_commission_counts(self, seeded, engine):
        resolution = seeded.resolve_or_mint(ANCHOR, "user_api")
        commission = seeded.mint_commission(
            resolution.mission_id,
            commissioner_instance_id=ANCHOR,
            worker_instance_id=WORKER,
            lane="instance_nudge",
        )
        assert count_outstanding_commissions(engine, ANCHOR) == 1
        # A satisfied commission is NOT outstanding.
        seeded.satisfy_commission(commission.commission_id)
        assert count_outstanding_commissions(engine, ANCHOR) == 0

    def test_a_read_failure_constructs_the_unknown_sentinel(self, seeded):
        import daemon.services.mission_record_reads as reads

        class _Boom:
            def find_open_epoch_by_anchor(self, *a, **k):
                raise RuntimeError("db is down")

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(reads, "_repo", lambda engine: _Boom())
            value = reads.count_outstanding_commissions(object(), ANCHOR)
        # NEVER a 0 — the unknown sentinel is -1 (convention #3).
        assert value == MISSION_READ_UNKNOWN
        assert value == -1


class TestOutstandingList:
    def test_the_list_is_the_union_half(self, seeded, engine):
        resolution = seeded.resolve_or_mint(ANCHOR, "user_api")
        seeded.mint_commission(
            resolution.mission_id,
            commissioner_instance_id=ANCHOR,
            worker_instance_id=WORKER,
            lane="instance_nudge",
        )
        rows = list_outstanding_commissions(engine, ANCHOR)
        assert len(rows) == 1
        assert rows[0].worker_instance_id == WORKER
        assert rows[0].state == CommissionState.OUTSTANDING.value

    def test_a_read_failure_degrades_to_empty(self, seeded):
        import daemon.services.mission_record_reads as reads

        class _Boom:
            def find_open_epoch_by_anchor(self, *a, **k):
                raise RuntimeError("db is down")

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(reads, "_repo", lambda engine: _Boom())
            assert reads.list_outstanding_commissions(object(), ANCHOR) == []


class TestMissionIdRead:
    def test_the_open_epoch_id_is_returned(self, seeded, engine):
        resolution = seeded.resolve_or_mint(ANCHOR, "user_api")
        assert read_mission_id(engine, ANCHOR) == resolution.mission_id

    def test_no_record_reads_as_none(self, seeded, engine):
        assert read_mission_id(engine, ANCHOR) is None


class TestOpenMissionRead:
    def test_the_open_epoch_is_found(self, seeded, engine):
        resolution = seeded.resolve_or_mint(ANCHOR, "user_api")
        record = read_open_mission(engine, ANCHOR)
        assert record is not None
        assert record.mission_id == resolution.mission_id

    def test_an_empty_anchor_reads_as_none(self, seeded, engine):
        assert read_open_mission(engine, "") is None


def test_the_mode_vocabulary_is_intact():
    # The three-mode vocabulary is the mode ladder's contract; the
    # PR2 read sites are gated on `enforce`.
    assert MissionRecordMode.OFF == "off"
    assert MissionRecordMode.DRY == "dry"
    assert MissionRecordMode.ENFORCE == "enforce"
