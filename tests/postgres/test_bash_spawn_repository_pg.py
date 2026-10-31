"""PostgreSQL parity leg for the bash-spawn durable registry repository.

minor-9 (review round 2, 2026-10-31): the plan §1.1 acceptance required a
PG-fixture leg for the repository pack; the shipped pack covered SQLite
only. This module exercises :class:`BashSpawnRepository` against a REAL
PostgreSQL engine — including the dual-dialect partial indexes actually
MATERIALIZING on PG (``WHERE reaped_at IS NULL`` in ``pg_indexes``) —
and the minor-1 CAS close + minor-4 retention prune on the real dialect.

Run with (tests/postgres/ conventions — STRICTLY)::

    pytest tests/postgres/test_bash_spawn_repository_pg.py \\
        --override-ini='addopts='

NO ``-m`` on the invocation: the module-level ``pytestmark`` below marks
every test ``postgres``. The target is the shared lane scratch DB
(``PG_TEST_DB``, default ``ensemble_test`` — already a member of the
shared ``ALLOWED_SCRATCH_DBS`` fence, so no allowlist export is needed;
``ensemble_dev``/``ensemble_prod`` are hard-refused by the fence). The
shared ``pg_engine`` fixture runs the scratch-DB fence BEFORE any SQL,
drops the schema at session setup, and truncates between tests. NEVER
point this lane at ``ensemble_dev``.
"""
from __future__ import annotations

import pytest
from sqlalchemy import text

from daemon.repositories.bash_spawn.repository import BashSpawnRepository

# Auto-apply the postgres marker so the default
# ``-m 'not integration and not postgres'`` addopts skips this module
# unless overridden with ``--override-ini='addopts='``.
pytestmark = pytest.mark.postgres

IDX_INSTANCE_OPEN = "idx_bash_spawn_registry_instance_open"
IDX_BOOT_OPEN = "idx_bash_spawn_registry_boot_open"
IDX_PGID = "idx_bash_spawn_registry_pgid"
UQ_TRACKING_ID = "uq_bash_spawn_registry_tracking_id"


def _repo(pg_engine) -> BashSpawnRepository:
    return BashSpawnRepository(engine=pg_engine)


def _insert(repo: BashSpawnRepository, tracking_id: str, **over) -> object:
    fields = dict(
        pid=4100,
        pgid=4100,
        start_time=None,
        tracking_id=tracking_id,
        instance_id="pg-leg-inst",
        command_sanitized="sleep 30",
        boot_id="previous-boot",
        daemon_pid=999999,
    )
    fields.update(over)
    return repo.insert_open_row(**fields)


class TestBashSpawnRepositoryPG:
    def test_crud_roundtrip_on_pg(self, pg_engine) -> None:
        repo = _repo(pg_engine)
        row = _insert(repo, "p" * 32)
        assert row.id is not None
        open_rows = repo.list_open_by_instance("pg-leg-inst")
        assert [r.tracking_id for r in open_rows] == ["p" * 32]

        closed = repo.mark_reaped(row.id, "shutdown@1234", None)
        assert closed is not None
        assert closed.reaped_at is not None
        assert closed.reaped_at.endswith("+00:00")
        assert getattr(closed, "cas_rowcount", None) == 1
        assert repo.list_open_by_instance("pg-leg-inst") == []

    def test_cas_close_second_sweeper_cannot_overwrite(self, pg_engine) -> None:
        """minor-1 on the REAL dialect: conditional UPDATE ... WHERE
        reaped_at IS NULL keeps the first sweeper's close verbatim."""
        repo = _repo(pg_engine)
        row = _insert(repo, "q" * 32)
        first = repo.mark_reaped(row.id, "shutdown@1", None)
        assert first is not None
        assert getattr(first, "cas_rowcount", None) == 1
        second = repo.mark_reaped(row.id, "boot-reconcile@2", "gc")
        assert second is not None
        assert getattr(second, "cas_rowcount", None) == 0
        assert second.reaped_by == "shutdown@1"
        assert second.reap_reason is None

    def test_previous_boot_candidate_shape_on_pg(self, pg_engine) -> None:
        repo = _repo(pg_engine)
        _insert(repo, "r" * 32, boot_id="previous-boot")
        _insert(repo, "s" * 32, boot_id=None)  # NULL ⇒ previous-boot
        current = _insert(repo, "t" * 32, boot_id="current-boot")
        rows = repo.list_open_previous_boot("current-boot", 1234)
        tids = sorted(r.tracking_id for r in rows)
        assert tids == sorted(["r" * 32, "s" * 32])
        repo.mark_reaped(current.id, "gc@1", "gc")

    def test_retention_prune_on_pg(self, pg_engine) -> None:
        """minor-4 on the REAL dialect: only reaped rows older than the
        window are deleted; open rows survive."""
        from sqlmodel import Session

        from daemon.repositories.bash_spawn import BashSpawnRecord

        repo = _repo(pg_engine)
        old = _insert(repo, "u" * 32)
        repo.mark_reaped(old.id, "shutdown@1", None)
        with Session(pg_engine) as session:
            persisted = session.get(BashSpawnRecord, old.id)
            persisted.reaped_at = "2026-01-01T00:00:00+00:00"
            session.add(persisted)
            session.commit()

        fresh_open = _insert(repo, "v" * 32)
        assert repo.prune_reaped_older_than(days=30) == 1
        assert [
            r.id for r in repo.list_open_by_instance("pg-leg-inst")
        ] == [fresh_open.id]

    def test_partial_indexes_materialize_on_pg(self, pg_engine) -> None:
        """minor-9 core: the dual-dialect PARTIAL open-subset indexes
        actually EXIST on PostgreSQL and carry the ``WHERE reaped_at IS
        NULL`` predicate (``pg_indexes.indexdef``)."""
        with pg_engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT indexname, indexdef FROM pg_indexes "
                    "WHERE tablename = 'bash_spawn_registry'"
                )
            ).all()
        by_name = {name: definition for name, definition in rows}
        for expected in (
            IDX_INSTANCE_OPEN,
            IDX_BOOT_OPEN,
            IDX_PGID,
            UQ_TRACKING_ID,
        ):
            assert expected in by_name, (
                f"index {expected} missing on PG; found "
                f"{sorted(by_name)}"
            )
        for partial in (IDX_INSTANCE_OPEN, IDX_BOOT_OPEN):
            assert "WHERE" in by_name[partial].upper(), by_name[partial]
            assert "reaped_at IS NULL" in by_name[partial], by_name[partial]
