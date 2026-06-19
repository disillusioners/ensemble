"""Smoke tests for the PostgreSQL test infrastructure.

These tests prove the PG fixtures work end-to-end:

* engine connects to ``ensemble_test``
* ``SQLModel.metadata.create_all`` emits all tables
* INSERT/SELECT round-trip via the repository layer
* data survives a commit and is readable in a fresh session

They run only via ``pytest -m postgres`` (opt-in, see pyproject.toml).
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import text
from sqlmodel import Session, select

from daemon.repositories.event.models import Event, EventKind
from daemon.repositories.event.repository import EventRepository


def test_engine_connected_and_schema_exists(pg_engine):
    """The PG engine has all SQLModel tables after ``create_all``."""
    from sqlmodel import SQLModel

    with pg_engine.connect() as conn:
        pg_table_rows = conn.execute(
            text("SELECT tablename FROM pg_tables WHERE schemaname='public'")
        ).fetchall()
    pg_tables = {row[0] for row in pg_table_rows}
    metadata_tables = {t.name for t in SQLModel.metadata.sorted_tables}

    assert metadata_tables, "SQLModel metadata is empty — models not imported"
    missing = metadata_tables - pg_tables
    assert not missing, f"PG schema missing tables: {missing}"


def test_event_repository_roundtrip(pg_repository_factory):
    """Insert + read back an Event through the repository API."""
    repo: EventRepository = pg_repository_factory(EventRepository)

    event = repo.create_event(
        instance_id="smoke-instance",
        kind=EventKind.MESSAGE_RECEIVED.value,
        data={"hello": "world", "n": 42},
        message_id="msg-1",
    )

    assert event.id is not None, "Event id must be set after commit/refresh"
    assert event.instance_id == "smoke-instance"
    assert event.kind == EventKind.MESSAGE_RECEIVED.value
    assert event.message_id == "msg-1"
    # PG returns naive datetimes for non-tz columns; just verify it's set.
    assert event.created_at is not None


def test_event_repository_get_returns_row(pg_repository_factory):
    """``repo.get(id)`` round-trips the data we just wrote."""
    repo: EventRepository = pg_repository_factory(EventRepository)
    created = repo.create_event(
        instance_id="smoke-instance",
        kind=EventKind.PROCESSING_COMPLETED.value,
        data={"status": "ok"},
    )

    fetched = repo.get(created.id)
    assert fetched is not None
    assert fetched.id == created.id
    assert fetched.instance_id == "smoke-instance"
    assert fetched.kind == EventKind.PROCESSING_COMPLETED.value
    assert fetched.data is not None
    assert '"status"' in fetched.data and '"ok"' in fetched.data


def test_truncate_fixture_isolates_tests(pg_repository_factory):
    """The autouse TRUNCATE fixture wipes rows between tests."""
    repo: EventRepository = pg_repository_factory(EventRepository)
    repo.create_event(instance_id="leak-check", kind=EventKind.ERROR.value)

    rows = repo.get_by_instance("leak-check")
    assert len(rows) == 1

    # This second repo call happens in a fresh test (after TRUNCATE).
    # The assertion below lives in the next test; here we just confirm
    # the row exists in this test's window.


def test_truncate_fixture_wiped_previous_test(pg_repository_factory):
    """A new repo sees zero rows — autouse TRUNCATE ran between tests."""
    repo: EventRepository = pg_repository_factory(EventRepository)
    rows = repo.get_by_instance("leak-check")
    assert rows == [], f"TRUNCATE failed; leaked rows: {rows}"


def test_session_factory_roundtrip(pg_session_factory):
    """The pg_session_factory fixture yields a working Session.

    Uses SQLModel's ``Session`` (returned by ``sessionmaker`` bound to a
    SQLModel metadata engine) so we get the ``.exec()`` convenience API.
    """
    with pg_session_factory() as session:
        event = Event(
            instance_id="session-smoke",
            kind=EventKind.MESSAGE_RECEIVED.value,
            data='{"via":"session"}',
            created_at=datetime.now(timezone.utc),
        )
        session.add(event)
        session.commit()
        session.refresh(event)
        event_id = event.id

    with pg_session_factory() as session:
        stmt = select(Event).where(Event.id == event_id)
        loaded = session.exec(stmt).one()
        assert loaded.instance_id == "session-smoke"


def test_two_connections_are_independent(pg_two_connections):
    """Two connections hold independent transaction state.

    Verifies that an uncommitted INSERT on conn_a is invisible to conn_b
    until conn_a commits — the isolation guarantee that the Phase 3
    concurrency tests rely on for race-condition coverage.
    """
    probe_instance_id = "tx-vis-probe"
    insert_sql = text(
        "INSERT INTO event (instance_id, kind, data, created_at) "
        "VALUES (:instance_id, :kind, :data, NOW())"
    )
    count_sql = text(
        "SELECT count(*) FROM event WHERE instance_id = :instance_id"
    )

    with pg_two_connections() as (conn_a, conn_b):
        # conn_a inserts inside its autobegun transaction.
        conn_a.execute(
            insert_sql,
            {
                "instance_id": probe_instance_id,
                "kind": "TX_TEST",
                "data": "{}",
            },
        )

        # While conn_a is uncommitted, conn_b must NOT see the row.
        unseen = conn_b.execute(
            count_sql, {"instance_id": probe_instance_id}
        ).scalar()
        assert unseen == 0, (
            "conn_b saw conn_a's uncommitted insert — connections share state"
        )

        # Commit conn_a. Under READ COMMITTED, conn_b's next statement
        # will see the freshly-committed row.
        conn_a.commit()

        seen = conn_b.execute(
            count_sql, {"instance_id": probe_instance_id}
        ).scalar()
        assert seen == 1, (
            "conn_b did not see conn_a's committed insert — connections are not isolated"
        )