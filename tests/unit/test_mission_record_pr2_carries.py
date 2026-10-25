"""Unit tests — the F1/F2 CARRIES (Proposal B PR2).

F1 = the OPEN-EPOCH partial UNIQUE index: at most ONE ``open``
``mission_records`` row may exist per anchor. The three sites (the
SQLite migration, the SQLModel ``__table_args__``, and the PostgreSQL
``_ensure_postgres_columns`` mirror) MUST agree on the index NAME and
the PREDICATE (the 3-site pin).

F2 = the amend guard: the founding-message attach and the re-founding
amendment touch ONLY an OPEN epoch (the ``dependency_watchers``
PENDING→FIRED guarded-UPDATE shape). A closed epoch's anchor is
immutable.

Also pinned: the ``job_continue`` continuation mint SUPERSEDES the
prior open epoch BEFORE inserting the new one (F1's ordering
constraint — the old insert-then-supersede order would raise the
open-epoch unique constraint).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.pool import NullPool
from sqlmodel import SQLModel, Session

import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.mission_record.models  # noqa: F401
from daemon.repositories.instance.models import Instance
from daemon.repositories.mission_record.models import (
    OPEN_EPOCH_STATUS_SQL,
    MissionStatus,
)
from daemon.repositories.mission_record.repository import MissionRecordError
from daemon.services.mission_record_service import MissionRecordService

ANCHOR = "11111111-1111-4111-8111-111111111111"
WORKER = "22222222-2222-4222-8222-222222222222"

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATION = (
    REPO_ROOT
    / "daemon/migrations/versions/20261025_140000_add_mission_open_epoch_unique_index.sql"
)


@pytest.fixture
def engine(tmp_path: Path):
    eng = create_engine(
        f"sqlite:///{tmp_path / 'pr2-carries.sqlite'}",
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


@pytest.fixture
def seeded(engine):
    for iid in (ANCHOR, WORKER):
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
    return MissionRecordService(engine)


class TestF1ThreeSitePin:
    def test_the_migration_declares_the_index(self):
        sql = MIGRATION.read_text()
        assert (
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_mission_records_anchor_open"
            in sql
        )
        assert "WHERE status = 'open'" in sql

    def test_the_model_declares_the_same_index_and_predicate(self):
        from daemon.repositories.mission_record.models import MissionRecord

        index_names = {
            index.name for index in MissionRecord.__table_args__
        }
        assert "ux_mission_records_anchor_open" in index_names
        # The predicate text is declared ONCE and shared by the model and
        # the migration (the byte-identical 3-site pin).
        assert OPEN_EPOCH_STATUS_SQL == "status = 'open'"

    def test_the_postgres_mirror_declares_the_same_index(self):
        """The PG mirror cites the SHARED predicate constant.

        The 3-site pin is name + predicate. The predicate TEXT lives in
        ONE place (``OPEN_EPOCH_STATUS_SQL`` in the models module) and
        the PostgreSQL mirror interpolates it — so the mirror cannot
        drift from the model's index declaration. The rendered SQL is
        proven on the real driver by the PG integration leg
        (``test_the_ensure_mirror_lands_the_index``).
        """
        source = (REPO_ROOT / "daemon/manager.py").read_text()
        assert (
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_mission_records_anchor_open"
            in source
        )
        # The mirror cites the shared constant (no hardcoded predicate).
        assert "OPEN_EPOCH_STATUS_SQL" in source
        assert f"WHERE {{OPEN_EPOCH_STATUS_SQL}}" in source
        # The constant is the model's declared predicate.
        assert OPEN_EPOCH_STATUS_SQL == "status = 'open'"

    def test_the_index_exists_on_a_fresh_database(self, engine):
        inspector = inspect(engine)
        indexes = inspector.get_indexes("mission_records")
        names = {index["name"] for index in indexes}
        assert "ux_mission_records_anchor_open" in names


class TestF1OpenEpochUniqueness:
    def test_a_second_open_epoch_is_rejected(self, seeded, engine):
        """The invariant is a CONSTRAINT, not a convention."""
        seeded.resolve_or_mint(ANCHOR, "user_api")
        with pytest.raises(Exception):
            # A raw second open epoch for the same anchor — the partial
            # unique index rejects it.
            with Session(engine) as session:
                session.add(
                    _raw_mission(ANCHOR, "open")
                )
                session.commit()

    def test_closed_epochs_do_not_constrain_the_anchor(self, seeded, engine):
        """The index is PARTIAL on ``status='open'`` — closed epochs
        accumulate per anchor without violating it."""
        seeded.resolve_or_mint(ANCHOR, "user_api")
        with Session(engine) as session:
            session.add(_raw_mission(ANCHOR, "settled"))
            session.add(_raw_mission(ANCHOR, "superseded"))
            session.commit()
        with Session(engine) as session:
            rows = list(
                session.exec(
                    text(
                        "SELECT status FROM mission_records "
                        "WHERE anchor_instance_id = :a"
                    ),
                    params={"a": ANCHOR},
                )
            )
        assert len(rows) == 3

    def test_the_continuation_mint_supersedes_before_inserting(self, seeded):
        """The ``job_continue`` lane forces a NEW epoch — the prior open
        epoch must be superseded BEFORE the insert (F1's ordering)."""
        first = seeded.resolve_or_mint(ANCHOR, "user_api")
        second = seeded.mint_continuation_epoch(ANCHOR, work_id="job-1")
        assert second.mission_id != first.mission_id
        assert second.created is True
        # Exactly ONE open epoch survives for the anchor.
        from daemon.services.mission_record_reads import read_open_mission

        record = read_open_mission(seeded._engine, ANCHOR)
        assert record is not None
        assert record.mission_id == second.mission_id


class TestF2AmendGuard:
    def test_the_amendment_only_touches_an_open_epoch(self, seeded):
        resolution = seeded.resolve_or_mint(
            ANCHOR, "user_api", founding_message_id="m-1"
        )
        # Close the epoch.
        assert (
            seeded._repo.transition_epoch_status(
                resolution.mission_id, MissionStatus.SETTLED.value
            )
            is True
        )
        # The amendment is refused (the closed epoch's anchor is
        # immutable).
        assert (
            seeded.amend_anchor(resolution.mission_id, "m-2") is False
        )
        from daemon.services.mission_record_reads import (
            read_founding_message_id,
        )

        assert read_founding_message_id(seeded._engine, ANCHOR) is None

    def test_the_amendment_works_on_an_open_epoch(self, seeded):
        resolution = seeded.resolve_or_mint(
            ANCHOR, "user_api", founding_message_id="m-1"
        )
        assert seeded.amend_anchor(resolution.mission_id, "m-2") is True
        from daemon.services.mission_record_reads import (
            read_founding_message_id,
        )

        assert (
            read_founding_message_id(seeded._engine, ANCHOR) == "m-2"
        )

    def test_the_attach_only_touches_an_open_epoch(self, seeded):
        resolution = seeded.resolve_or_mint(ANCHOR, "user_api")
        assert (
            seeded._repo.transition_epoch_status(
                resolution.mission_id, MissionStatus.CLOSED.value
            )
            is True
        )
        assert (
            seeded.attach_founding_message(resolution.mission_id, "m-9")
            is False
        )


def _raw_mission(anchor: str, status: str):
    from daemon.repositories.mission_record.models import MissionRecord

    return MissionRecord(
        anchor_instance_id=anchor,
        epoch_seq=99,
        founding_message_id=None,
        founding_source="backfill",
        status=status,
    )
