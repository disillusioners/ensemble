"""Unit tests — the F2 amend-guard CONCURRENCY hardening (PR3 carry).

The PR2 F2 guard: the re-founding amendment touches ONLY an OPEN epoch
(``UPDATE ... WHERE status='open'``) — a closed epoch's anchor is
immutable.

The PR2 gate observed a ~1/8 flake across the pack runs (the LESSONS
recipe: ``.agents/tester/LESSONS/2026-10-25-pr2-f2-amend-guard-flake.md``):
an amendment landed on an epoch a concurrent close had already moved.
The prescribed production-side fix was LESSONS option 1 — "make
close+amend mutually exclusive in a SINGLE STATEMENT: the status
predicate lives in the UPDATE's WHERE and the outcome is decided by the
ROWCOUNT, with NO prior read".

What this file PINS:

* the guard is a SINGLE statement (no read-then-write window): the
  amend path issues no SELECT, and the compiled UPDATE carries the
  ``status = 'open'`` predicate;
* a close that COMMITTED first blocks the amendment (the F2 contract);
* the amend-then-close order stays ALLOWED — the mid-turn correction
  must be able to re-found the epoch BEFORE the settle (the lag shape);
* the concurrent pair never LOSES a write (the row-lock order decides,
  and no write silently disappears).
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.pool import NullPool
from sqlmodel import SQLModel, Session

import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.mission_record.models  # noqa: F401
from daemon.repositories.instance.models import Instance
from daemon.repositories.mission_record.models import (
    MissionRecord,
    MissionStatus,
)
from daemon.repositories.mission_record.repository import MissionRecordRepository
from daemon.services.mission_record_service import MissionRecordService

ANCHOR = "11111111-1111-4111-8111-111111111111"


@pytest.fixture
def engine(tmp_path: Path):
    eng = create_engine(
        f"sqlite:///{tmp_path / 'f2-concurrency.sqlite'}",
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
    with Session(engine) as session:
        session.add(
            Instance(
                instance_id=ANCHOR,
                agent_id="leader",
                agent_dir="agents/leader/11111111",
                status="running",
                project_id=None,
            )
        )
        session.commit()
    service = MissionRecordService(engine)
    return service


def _mission_id(engine, mission_id: str) -> MissionRecord | None:
    with Session(engine) as session:
        return session.get(MissionRecord, mission_id)


class TestTheSingleStatementGuard:
    """LESSONS option 1: the guard is ONE statement, no prior read."""

    def test_the_amend_path_issues_no_prior_read(self):
        """The amend's write must not be preceded by a status READ."""
        import inspect

        from daemon.repositories.mission_record.repository import (
            MissionRecordRepository as _Repo,
        )

        source = inspect.getsource(_Repo.amend_founding_message)
        assert "select(" not in source.lower()
        # The predicate lives in the shared guarded-UPDATE helper (the
        # single-statement shape), NOT in a hand-rolled statement.
        assert "_guarded_epoch_update" in source

    def test_the_emitted_amend_sql_carries_the_status_predicate(
        self, engine, seeded
    ):
        """The REAL production SQL carries the guard predicate.

        The statement is captured from the engine itself (the
        ``before_cursor_execute`` hook) — this pins what the database
        actually receives, not a hand-written replica.
        """
        from sqlalchemy import event as sa_event

        captured: list[tuple[str, object]] = []

        @sa_event.listens_for(engine, "before_cursor_execute")
        def _capture(conn, cursor, statement, parameters, context, executemany):
            captured.append((statement, parameters))

        resolution = seeded.resolve_or_mint(
            ANCHOR, "user_api", founding_message_id="m-1"
        )
        seeded.amend_anchor(resolution.mission_id, "m-2")

        amends = [
            (sql, params)
            for sql, params in captured
            if "founding_message_id" in sql.lower()
            and "mission_records" in sql.lower()
        ]
        assert amends, "the amend did not emit an UPDATE"
        sql, params = amends[-1]
        assert "status" in sql.lower()
        # The bound predicate value is the OPEN status.
        assert "open" in [str(p).lower() for p in (params or [])]

    def test_the_three_guarded_writes_share_one_helper(self):
        import inspect

        from daemon.repositories.mission_record.repository import (
            MissionRecordRepository as _Repo,
        )

        for name in (
            "transition_epoch_status",
            "attach_founding_message",
            "amend_founding_message",
        ):
            source = inspect.getsource(getattr(_Repo, name))
            assert "_guarded_epoch_update" in source, name


class TestTheOrderingContract:
    def test_a_committed_close_blocks_the_amendment(self, engine, seeded):
        """The F2 contract: the close committed FIRST → no amendment."""
        resolution = seeded.resolve_or_mint(
            ANCHOR, "user_api", founding_message_id="m-1"
        )
        assert (
            seeded._repo.transition_epoch_status(
                resolution.mission_id, MissionStatus.SETTLED.value
            )
            is True
        )
        assert seeded.amend_anchor(resolution.mission_id, "m-2") is False
        row = _mission_id(engine, resolution.mission_id)
        assert row.founding_message_id == "m-1"

    def test_the_amendment_before_the_close_is_allowed(
        self, engine, seeded
    ):
        """The lag shape: a mid-turn correction re-founds the epoch
        BEFORE the settle — that order must stay possible."""
        resolution = seeded.resolve_or_mint(
            ANCHOR, "user_api", founding_message_id="m-1"
        )
        assert seeded.amend_anchor(resolution.mission_id, "m-2") is True
        assert (
            seeded._repo.transition_epoch_status(
                resolution.mission_id, MissionStatus.SETTLED.value
            )
            is True
        )
        row = _mission_id(engine, resolution.mission_id)
        assert row.founding_message_id == "m-2"
        assert row.status == MissionStatus.SETTLED.value


class TestTheConcurrentPair:
    def test_the_concurrent_pair_never_loses_a_write(self, engine, seeded):
        """The row-lock order decides — and NO write silently vanishes.

        The F2 invariant under concurrency: the amendment may only land
        while the epoch is OPEN. When both writes fire, the row lock
        serializes them, so the final row must show BOTH effects (the
        amendment's value AND the closed status) — a lost write would
        mean a stale-snapshot update, i.e. exactly the flake class.
        """
        repo = MissionRecordRepository(engine)
        for iteration in range(40):
            mission_id = f"mission-{iteration}"
            # A DISTINCT anchor per iteration: the partial unique index
            # is (anchor_instance_id, founding_message_id), so a shared
            # anchor would collide on the amended value.
            anchor = f"anchor-{iteration}"
            with Session(engine) as session:
                session.add(
                    MissionRecord(
                        mission_id=mission_id,
                        anchor_instance_id=anchor,
                        epoch_seq=100 + iteration,
                        founding_message_id="m-1",
                        founding_source="user_api",
                        status=MissionStatus.OPEN.value,
                    )
                )
                session.commit()

            barrier = threading.Barrier(2)
            outcome: dict[str, object] = {}

            def _close():
                barrier.wait()
                outcome["closed"] = repo.transition_epoch_status(
                    mission_id, MissionStatus.SETTLED.value
                )

            def _amend():
                barrier.wait()
                outcome["amended"] = repo.amend_founding_message(
                    mission_id, "m-2"
                )

            t1 = threading.Thread(target=_close)
            t2 = threading.Thread(target=_amend)
            t1.start()
            t2.start()
            t1.join(timeout=30)
            t2.join(timeout=30)

            row = _mission_id(engine, mission_id)
            assert row is not None
            amended = bool(outcome.get("amended"))
            closed = bool(outcome.get("closed"))
            # The amendment's write is visible EXACTLY when it fired.
            assert (row.founding_message_id == "m-2") is amended
            # The close's write is visible EXACTLY when it fired.
            assert (row.status == MissionStatus.SETTLED.value) is closed
            # The F2 invariant: a CLOSED epoch's anchor is never
            # rewritten by an amendment that fired AFTER the close.
            if closed and not amended:
                assert row.founding_message_id == "m-1"
