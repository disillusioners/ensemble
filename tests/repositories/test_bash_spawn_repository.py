"""Repository pack for the bash-spawn durable registry (reaper Phase 1).

Pins (plan task 1.1 acceptance):
* CRUD: ``insert_open_row`` (record + kwargs forms), ``list_open_by_instance``,
  ``mark_reaped``.
* Dialect/index presence: the two PARTIAL open-subset indexes and the
  plain pgid index exist on the SQLite table with names byte-identical
  to the model constants; the model Index objects carry BOTH
  ``sqlite_where`` and ``postgresql_where`` (dual-dialect contract).
* ``list_open_previous_boot`` candidate shape: NULL/empty boot_id rows
  ARE treated as previous-boot (plan D3 adjudication 2), current-boot
  rows are excluded, reaped rows are never listed.

Self-contained in-memory SQLite (StaticPool) per the
tests/repositories conftest convention.
"""

from __future__ import annotations

import pytest
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, create_engine

from daemon.repositories.bash_spawn import (
    IDX_BOOT_OPEN,
    IDX_INSTANCE_OPEN,
    IDX_PGID,
    BashSpawnRecord,
    BashSpawnRepository,
)
from daemon.services.bash_spawn_reaper import CURRENT_BOOT_ID


def _make_engine() -> Engine:
    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(eng, "connect")
    def _pragma(dbapi_conn, _record):  # noqa: ANN001
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    return eng


@pytest.fixture
def engine() -> Engine:
    eng = _make_engine()
    SQLModel.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def repo(engine: Engine) -> BashSpawnRepository:
    return BashSpawnRepository(engine=engine)


def _mkrow(**over):
    fields = dict(
        pid=101,
        pgid=101,
        start_time=12345,
        tracking_id="t" * 32,
        instance_id="inst-a",
        command_sanitized="sleep 30",
        boot_id=CURRENT_BOOT_ID,
        daemon_pid=4242,
        created_at="2026-10-31T10:00:00+00:00",
    )
    fields.update(over)
    return BashSpawnRecord(**fields)


class TestCrud:
    def test_insert_kwargs_returns_persisted_row(self, repo) -> None:
        row = repo.insert_open_row(
            pid=101,
            pgid=101,
            start_time=None,
            tracking_id="a" * 32,
            instance_id="inst-a",
            command_sanitized="echo hi",
            boot_id=CURRENT_BOOT_ID,
            daemon_pid=4242,
            created_at="2026-10-31T10:00:00+00:00",
        )
        assert row.id is not None
        assert row.reaped_at is None

    def test_insert_record_instance(self, repo) -> None:
        row = repo.insert_open_row(_mkrow(tracking_id="b" * 32))
        assert row.id is not None
        rows = repo.list_open_by_instance("inst-a")
        assert len(rows) == 1
        assert rows[0].tracking_id == "b" * 32

    def test_list_open_by_instance_excludes_reaped_and_other_instances(
        self, repo
    ) -> None:
        repo.insert_open_row(_mkrow(tracking_id="c" * 32))
        repo.insert_open_row(_mkrow(tracking_id="d" * 32, instance_id="inst-b"))
        closed = repo.insert_open_row(_mkrow(tracking_id="e" * 32))
        repo.mark_reaped(closed.id, "gc@1", "gc")
        rows = repo.list_open_by_instance("inst-a")
        assert len(rows) == 1
        assert rows[0].tracking_id == "c" * 32

    def test_mark_reaped_sets_columns(self, repo) -> None:
        row = repo.insert_open_row(_mkrow(tracking_id="f" * 32))
        out = repo.mark_reaped(row.id, "terminal-tier1@99", "refused:pgid<=1")
        assert out is not None
        assert out.reaped_at is not None
        assert out.reaped_at.endswith("+00:00")
        assert out.reaped_by == "terminal-tier1@99"
        assert out.reap_reason == "refused:pgid<=1"

    def test_mark_reaped_missing_row_returns_none(self, repo) -> None:
        assert repo.mark_reaped(999999, "gc@1", "gc") is None


class TestDialectAndIndexes:
    def test_partial_indexes_declared_dual_dialect(self) -> None:
        """Both sqlite_where AND postgresql_where present on the two
        open-subset partial indexes (dual-dialect contract)."""
        idx = {i.name: i for i in BashSpawnRecord.__table__.indexes}
        assert IDX_INSTANCE_OPEN in idx
        assert IDX_BOOT_OPEN in idx
        assert IDX_PGID in idx
        for name in (IDX_INSTANCE_OPEN, IDX_BOOT_OPEN):
            i = idx[name]
            kwargs = i.dialect_kwargs
            assert kwargs.get("sqlite_where") is not None, name
            assert kwargs.get("postgresql_where") is not None, name
            assert "reaped_at IS NULL" in str(kwargs["sqlite_where"]), name

    def test_indexes_exist_on_sqlite_table(self, engine) -> None:
        rows = engine.connect().exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND tbl_name='bash_spawn_registry'"
        ).fetchall()
        names = {r[0] for r in rows}
        assert IDX_INSTANCE_OPEN in names
        assert IDX_BOOT_OPEN in names
        assert IDX_PGID in names
        assert "uq_bash_spawn_registry_tracking_id" in names

    def test_table_registered_in_shared_metadata(self) -> None:
        assert "bash_spawn_registry" in SQLModel.metadata.tables


class TestOpenPreviousBootCandidates:
    def test_null_and_empty_boot_id_rows_are_previous_boot(self, repo) -> None:
        """D3 adjudication 2: a raced/legacy writer row with NULL or
        empty boot_id lands as previous-boot."""
        repo.insert_open_row(_mkrow(tracking_id="1" * 32, boot_id=None))
        repo.insert_open_row(_mkrow(tracking_id="2" * 32, boot_id=""))
        repo.insert_open_row(_mkrow(tracking_id="3" * 32, boot_id="foreign-boot"))
        rows = repo.list_open_previous_boot(CURRENT_BOOT_ID, my_pid=4242)
        bids = {r.boot_id for r in rows}
        assert None in bids
        assert "" in bids
        assert "foreign-boot" in bids
        assert len(rows) == 3

    def test_current_boot_rows_excluded(self, repo) -> None:
        repo.insert_open_row(_mkrow(tracking_id="4" * 32, boot_id=CURRENT_BOOT_ID))
        rows = repo.list_open_previous_boot(CURRENT_BOOT_ID, my_pid=4242)
        assert all(r.boot_id != CURRENT_BOOT_ID for r in rows)
        assert rows == []

    def test_reaped_rows_never_listed(self, repo) -> None:
        r1 = repo.insert_open_row(_mkrow(tracking_id="5" * 32, boot_id="old-boot"))
        repo.mark_reaped(r1.id, "boot-reconcile@7", "gc")
        rows = repo.list_open_previous_boot(CURRENT_BOOT_ID, my_pid=4242)
        assert rows == []

    def test_null_current_boot_id_treats_all_as_previous(self, repo) -> None:
        repo.insert_open_row(_mkrow(tracking_id="6" * 32, boot_id=None))
        repo.insert_open_row(_mkrow(tracking_id="7" * 32, boot_id="x"))
        rows = repo.list_open_previous_boot(None, my_pid=4242)
        assert len(rows) == 2


class TestOpenAllRows:
    """Phase 2 (task 2.4): the shutdown lane enumerates ALL open rows."""

    def test_lists_every_open_row_regardless_of_boot(self, repo) -> None:
        repo.insert_open_row(_mkrow(tracking_id="a" * 32, boot_id=CURRENT_BOOT_ID))
        repo.insert_open_row(_mkrow(tracking_id="b" * 32, boot_id="old-boot"))
        repo.insert_open_row(_mkrow(tracking_id="c" * 32, boot_id=None))
        repo.insert_open_row(_mkrow(tracking_id="d" * 32, boot_id=""))
        rows = repo.list_open_all()
        assert len(rows) == 4

    def test_reaped_rows_never_listed(self, repo) -> None:
        r1 = repo.insert_open_row(_mkrow(tracking_id="e" * 32))
        repo.mark_reaped(r1.id, "shutdown@7", None)
        r2 = repo.insert_open_row(_mkrow(tracking_id="f" * 32))
        rows = repo.list_open_all()
        assert [r.id for r in rows] == [r2.id]


class TestManagerImportBeforeCreateAll:
    def test_model_importable_from_manager_block(self) -> None:
        """The manager create_all import-block registration is pinned by
        import success + table-presence after a manager-shaped create_all:
        importing the model module then create_all lands the table on a
        bare engine (the PG boot path shape)."""
        import importlib

        mod = importlib.import_module("daemon.repositories.bash_spawn.models")
        eng = _make_engine()
        try:
            SQLModel.metadata.create_all(eng)
            names = {
                r[0]
                for r in eng.connect()
                .exec_driver_sql(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
                .fetchall()
            }
            assert "bash_spawn_registry" in names
        finally:
            eng.dispose()
        assert hasattr(mod, "BashSpawnRecord")
