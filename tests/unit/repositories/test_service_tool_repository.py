"""Repository round-trip + migration-apply tests for the ``service`` tool category.

Phase 1.A task 1.A.7 — file-backed SQLite + migration-runner pattern,
mirroring ``tests/test_chart_tools_reuse_integration.py:80-109`` (the
canonical fixture) and ``tests/unit/test_ensure_deferred_schema_pin.py``
(the migration-runner test pattern).

Coverage:

* **Round-trip** — ``insert`` + ``get_by_id`` + ``get_by_name``
  active/any-status + ``list_active`` + ``list_all``.
* **A11 name-reuse-after-EXITED** — insert name X, mark_exited, insert
  name X again succeeds. The partial UNIQUE index
  ``idx_service_tracking_name_active`` only gates ``STARTING`` /
  ``RUNNING`` — an ``EXITED`` row clears the slot.
* **Concurrent same-name insert** — a second insert with the same name
  while the first is still ``STARTING`` / ``RUNNING`` raises
  ``sqlalchemy.exc.IntegrityError`` (the partial UNIQUE index
  rejection — the write-once gate).
* **A13 mark_exited atomic-guard** — second ``mark_exited`` on an
  EXITED row returns 0 (race-lost / idempotent success).
* **F8 insert_with_status helper** — synchronous-spawn-failure path
  writes ``status="exited"`` directly.
* **Fresh-SQLite migration apply** — the real ``MigrationRunner``
  applies ``20260915_212810_create_service_tracking.sql`` to a fresh
  SQLite DB and asserts the table + both indexes exist with
  byte-identical names (the 3-site index pattern).
* **3-site name-pin test** — ``idx_service_tracking_name_active`` and
  ``idx_service_tracking_pid`` appear in the ``.sql`` site AND in
  ``models.py`` ``__table_args__`` STRICTLY; the ``manager.py``
  ``_ensure_postgres_columns`` site is gated with an explicit
  ``pytest.mark.skip`` whose message points to 1.C.13b (the F3
  reassignment — manager.py is not modified in Phase 1.A). 1.C.13b
  flips the skip to a strict assertion in one line.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator

import pytest
from sqlalchemy import create_engine, event as sa_event, inspect as sa_inspect
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import NullPool
from sqlmodel import SQLModel

import daemon.repositories.dependency_bus.models  # noqa: F401
import daemon.repositories.event.models  # noqa: F401
import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.job_queue.models  # noqa: F401
import daemon.repositories.message_queue.models  # noqa: F401
import daemon.repositories.report_injection.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401
import daemon.repositories.service_tool.models  # noqa: F401

from daemon.migrations.runner import MigrationFile, MigrationRunner
from daemon.repositories.service_tool.models import (
    ServiceStatus,
    ServiceTracking,
)
from daemon.repositories.service_tool.repository import ServiceRepo


# ── fixtures ────────────────────────────────────────────────────────


@pytest.fixture
def engine(tmp_path) -> Iterator[Engine]:
    """File-backed SQLite engine (NullPool + WAL + busy_timeout).

    Thin wrapper around :func:`tests.helpers.service_tool_sqlite.
    make_file_backed_engine`. See that module for the busy_timeout
    drift fix (10000 vs the pre-refactor 30000).
    """
    from tests.helpers.service_tool_sqlite import make_file_backed_engine

    eng = make_file_backed_engine(tmp_path)
    SQLModel.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture
def repo(engine: Engine) -> ServiceRepo:
    return ServiceRepo(engine=engine)


def _make_args(
    name: str = "alpha",
    pid: int | None = 4242,
    start_time: int | None = 1_700_000_000,
    status: str = ServiceStatus.STARTING.value,
    command: list[str] | None = None,
) -> dict:
    """Helper: build the kwargs for ``repo.insert(...)`` with sensible defaults."""
    if command is None:
        command = ["sleep", "60"]
    return {
        "name": name,
        "command": command,
        "pid": pid,
        "start_time": start_time,
        "cwd": "/tmp",
        "status": status,
        "started_by_instance_id": "inst-1",
        "started_by_agent_id": "worker",
        "log_path": f"/tmp/{name}.log",
    }


# ── Round-trip ──────────────────────────────────────────────────────


class TestServiceRepoRoundTrip:
    """End-to-end round-trip: insert + reads + list."""

    def test_insert_then_get_by_id(self, repo: ServiceRepo) -> None:
        row = repo.insert(**_make_args(name="alpha"))
        assert row.id is not None and row.id > 0
        assert row.name == "alpha"
        assert row.command == json.dumps(["sleep", "60"])
        assert row.status == ServiceStatus.STARTING.value
        assert row.started_by_instance_id == "inst-1"
        assert row.started_by_agent_id == "worker"
        assert row.log_path == "/tmp/alpha.log"
        assert row.pid == 4242
        assert row.start_time == 1_700_000_000
        assert row.cwd == "/tmp"
        assert row.exit_code is None

        reread = repo.get_by_id(row.id)
        assert reread is not None
        assert reread.id == row.id
        assert reread.name == "alpha"

    def test_get_by_id_returns_none_for_missing(self, repo: ServiceRepo) -> None:
        assert repo.get_by_id(999_999_999) is None

    def test_get_by_name_active(self, repo: ServiceRepo) -> None:
        row = repo.insert(**_make_args(name="bravo"))
        got = repo.get_by_name("bravo")
        assert got is not None
        assert got.id == row.id

    def test_get_by_name_active_excludes_exited(self, repo: ServiceRepo) -> None:
        """A11: ``get_by_name(active_only=True)`` does NOT see EXITED rows."""
        row = repo.insert(**_make_args(name="charlie"))
        repo.mark_exited(row.id)
        # active_only default: nothing returned.
        assert repo.get_by_name("charlie") is None
        # explicit active_only=False: the EXITED row shows up.
        got = repo.get_by_name("charlie", active_only=False)
        assert got is not None
        assert got.id == row.id

    def test_get_by_name_any_status_returns_exited(
        self, repo: ServiceRepo
    ) -> None:
        """``get_by_name_any_status`` returns the latest row regardless of status."""
        row = repo.insert(**_make_args(name="delta"))
        repo.mark_exited(row.id)
        got = repo.get_by_name_any_status("delta")
        assert got is not None
        assert got.id == row.id
        assert got.status == ServiceStatus.EXITED.value

    def test_list_active_filters_exited(self, repo: ServiceRepo) -> None:
        a = repo.insert(**_make_args(name="a", status=ServiceStatus.RUNNING.value))
        b = repo.insert(**_make_args(name="b", status=ServiceStatus.STARTING.value))
        c = repo.insert(**_make_args(name="c", status=ServiceStatus.RUNNING.value))
        repo.mark_exited(c.id)

        rows = repo.list_active()
        ids = {r.id for r in rows}
        assert a.id in ids and b.id in ids and c.id not in ids
        # created_at DESC ordering — second insert is "newer" than the first.
        assert rows[0].id == b.id
        assert rows[1].id == a.id

    def test_list_all_includes_exited(self, repo: ServiceRepo) -> None:
        a = repo.insert(**_make_args(name="a"))
        b = repo.insert(**_make_args(name="b"))
        c = repo.insert(**_make_args(name="c"))
        repo.mark_exited(c.id)

        rows = repo.list_all()
        ids = {r.id for r in rows}
        assert {a.id, b.id, c.id} <= ids
        # created_at DESC: c (newest) first.
        assert rows[0].id == c.id


# ── A13 mark_exited atomic-guard ───────────────────────────────────


class TestMarkExitedAtomicGuard:
    """A13 contract: guarded UPDATE returns 1=transitioned, 0=race-lost."""

    def test_mark_exited_returns_1_for_active_row(self, repo: ServiceRepo) -> None:
        row = repo.insert(**_make_args(name="e", status=ServiceStatus.RUNNING.value))
        assert repo.mark_exited(row.id) == 1
        reread = repo.get_by_id(row.id)
        assert reread is not None
        assert reread.status == ServiceStatus.EXITED.value

    def test_mark_exited_returns_0_on_already_exited(
        self, repo: ServiceRepo
    ) -> None:
        row = repo.insert(**_make_args(name="f", status=ServiceStatus.RUNNING.value))
        assert repo.mark_exited(row.id) == 1
        # Second call: idempotent success (0).
        assert repo.mark_exited(row.id) == 0

    def test_mark_exited_records_exit_code(self, repo: ServiceRepo) -> None:
        row = repo.insert(**_make_args(name="g", status=ServiceStatus.RUNNING.value))
        repo.mark_exited(row.id, exit_code=137)
        reread = repo.get_by_id(row.id)
        assert reread is not None
        assert reread.exit_code == 137

    def test_mark_exited_bumps_updated_at(self, repo: ServiceRepo) -> None:
        row = repo.insert(**_make_args(name="h"))
        original_updated_at = row.updated_at
        # Sleep is not reliable here — instead just verify the bump
        # produces a strictly different value (default_factory stamps
        # wall-clock time, so even sub-millisecond differences will
        # surface; if the implementation re-uses original_updated_at
        # this assertion catches it).
        repo.mark_exited(row.id)
        reread = repo.get_by_id(row.id)
        assert reread is not None
        assert reread.updated_at != original_updated_at, (
            "mark_exited MUST bump updated_at (A12 repo-side bump)."
        )


# ── A11 name-reuse-after-EXITED ────────────────────────────────────


class TestNameReuseAfterExited:
    """A11 contract: same name can be reused once the prior row is EXITED."""

    def test_same_name_insert_raises_when_first_is_active(
        self, repo: ServiceRepo
    ) -> None:
        """Two ``STARTING`` rows with the same name -> IntegrityError.

        The partial UNIQUE index ``idx_service_tracking_name_active``
        rejects the second insert with ``IntegrityError``. The
        repo does NOT absorb the error — callers must handle it
        as a "name already in use" signal.
        """
        repo.insert(**_make_args(name="reuse-busy"))
        with pytest.raises(IntegrityError):
            repo.insert(**_make_args(name="reuse-busy"))

    def test_same_name_insert_raises_when_first_is_running(
        self, repo: ServiceRepo
    ) -> None:
        repo.insert(**_make_args(name="reuse-running", status=ServiceStatus.RUNNING.value))
        with pytest.raises(IntegrityError):
            repo.insert(**_make_args(name="reuse-running", status=ServiceStatus.RUNNING.value))

    def test_same_name_insert_succeeds_after_first_is_exited(
        self, repo: ServiceRepo
    ) -> None:
        """A11: name-reuse-after-EXITED works (the partial UNIQUE clears)."""
        first = repo.insert(**_make_args(name="reuse-success"))
        assert repo.mark_exited(first.id) == 1
        # Second insert with the SAME name: must NOT raise — the partial
        # UNIQUE index is scoped to STARTING/RUNNING, and the first
        # row is now EXITED.
        second = repo.insert(**_make_args(name="reuse-success"))
        assert second.id != first.id
        assert second.name == "reuse-success"
        # Both rows are visible in list_all (the EXITED one + the new one).
        all_rows = repo.list_all()
        names = {r.name for r in all_rows}
        assert names == {"reuse-success"}
        # But only the new one is active.
        active_rows = repo.list_active()
        assert [r.id for r in active_rows] == [second.id]


# ── F8 insert_with_status helper ───────────────────────────────────


class TestInsertWithStatusF8:
    """F8 synchronous-spawn-failure path: ``insert_with_status(status="exited", reason="spawn_failed")``."""

    def test_insert_with_status_writes_exit_status(
        self, repo: ServiceRepo
    ) -> None:
        args = _make_args(name="spawn-fail", pid=None, start_time=None)
        # Drop the default ``status`` from _make_args (F8 path supplies its own).
        args.pop("status")
        row = repo.insert_with_status(
            **args,
            status=ServiceStatus.EXITED.value,
            reason="spawn_failed",
            exit_code=-1,
        )
        assert row.status == ServiceStatus.EXITED.value
        assert row.exit_code == -1
        assert row.pid is None
        assert row.start_time is None

    def test_insert_with_status_exited_does_not_block_name(
        self, repo: ServiceRepo
    ) -> None:
        """An EXITED row written by F8 does NOT hold the active-slot.

        Mirrors the A11 contract: the partial UNIQUE index is scoped
        to ``STARTING`` / ``RUNNING``, so an F8-written EXITED row
        frees the name for a fresh attempt. This is important for
        1.B's retry path: ``service_start`` can re-attempt with the
        same name after a synchronous spawn failure without an
        IntegrityError.
        """
        args = _make_args(name="f8-retry")
        args.pop("status")
        repo.insert_with_status(
            **args,
            status=ServiceStatus.EXITED.value,
            reason="spawn_failed",
            exit_code=-1,
        )
        # Fresh attempt with the same name succeeds.
        retry = repo.insert(**_make_args(name="f8-retry"))
        assert retry.id is not None


# ── update_status atomic-guard ─────────────────────────────────────


class TestUpdateStatusAtomicGuard:
    """``update_status`` shares the A13 atomic-guard contract."""

    def test_update_starting_to_running(self, repo: ServiceRepo) -> None:
        row = repo.insert(**_make_args(name="promo"))
        rc = repo.update_status(row.id, ServiceStatus.RUNNING.value)
        assert rc == 1
        reread = repo.get_by_id(row.id)
        assert reread is not None
        assert reread.status == ServiceStatus.RUNNING.value

    def test_update_status_returns_0_after_exit(self, repo: ServiceRepo) -> None:
        row = repo.insert(**_make_args(name="after-exit"))
        repo.mark_exited(row.id)
        # Race-lost: row is no longer in the active set.
        rc = repo.update_status(row.id, ServiceStatus.RUNNING.value)
        assert rc == 0


# ── Fresh-SQLite migration apply ───────────────────────────────────


class TestMigrationApply:
    """The fresh-SQLite boot regression test (mirror ``tests/unit/test_ensure_deferred_schema_pin.py``).

    Pinned by Phase 1.A task 1.A.7: the real ``MigrationRunner``
    applies ``20260915_212810_create_service_tracking.sql`` to a
    fresh SQLite DB; the table + both indexes MUST exist after
    the apply. If a future change breaks dual-dialect validity
    (e.g. reintroduces PG-only ``DROP CONSTRAINT IF EXISTS`` or
    PG-only ``DEFAULT now()``), this test fails at the first
    ``MigrationRunner.apply_migration`` call rather than silently
    poisoning every fresh-SQLite boot.
    """

    def test_migration_applies_to_fresh_sqlite(
        self, tmp_path: Path
    ) -> None:
        # Fresh SQLite DB.
        db_path = tmp_path / "fresh-migration.sqlite"
        eng = create_engine(
            f"sqlite:///{db_path}",
            connect_args={"check_same_thread": False, "timeout": 30},
            poolclass=NullPool,
        )

        @sa_event.listens_for(eng, "connect")
        def _pragmas(dbapi_conn, _record):  # noqa: ANN001
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA busy_timeout=30000")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

        # Phase 1: create_all produces the FRESH schema (the
        # migration runner's pre-condition for additive work —
        # without ``projects``, ``task``, ``instances`` etc. the
        # very first migration fails on its ALTER TABLE).
        SQLModel.metadata.create_all(eng)

        # Phase 2: apply the new migration through the real runner.
        migrations_dir = (
            Path(__file__).resolve().parents[3]
            / "daemon"
            / "migrations"
            / "versions"
        )
        target_version = "20260915_212810"
        migration_path = (
            migrations_dir / f"{target_version}_create_service_tracking.sql"
        )
        assert migration_path.exists(), (
            f"service_tracking migration file must exist at "
            f"{migration_path}"
        )

        runner = MigrationRunner(engine=eng)
        runner.ensure_migrations_table()
        migration = MigrationFile.parse(migration_path)
        execution_time = runner.apply_migration(migration)
        assert execution_time > 0, (
            "the migration runner must report positive execution time "
            "after applying the service_tracking schema"
        )

        # Verify post-migration schema: the table + both indexes exist
        # with byte-identical names to the SQLModel ``__table_args__``.
        inspector = sa_inspect(eng)
        tables = inspector.get_table_names()
        assert "service_tracking" in tables, (
            "service_tracking table must exist after migration apply"
        )

        cols = {c["name"] for c in inspector.get_columns("service_tracking")}
        expected_cols = {
            "id",
            "name",
            "command",
            "pid",
            "start_time",
            "cwd",
            "status",
            "started_by_instance_id",
            "started_by_agent_id",
            "log_path",
            "exit_code",
            "created_at",
            "updated_at",
        }
        assert cols == expected_cols, (
            f"service_tracking columns diverged: got {cols}, "
            f"expected {expected_cols}"
        )

        indexes = inspector.get_indexes("service_tracking")
        index_names = {idx["name"] for idx in indexes}
        assert "idx_service_tracking_name_active" in index_names, (
            "the partial UNIQUE index idx_service_tracking_name_active "
            "must be created by the migration; the A11 D5 same-name "
            "guard depends on it."
        )
        assert "idx_service_tracking_pid" in index_names, (
            "the pid index idx_service_tracking_pid must be created by "
            "the migration; the D6 reconcile sweep + 1.B PID-reuse "
            "defense depend on it."
        )

        # The name-active index is unique (the D5 same-name guard).
        name_active_idx = next(
            i for i in indexes if i["name"] == "idx_service_tracking_name_active"
        )
        assert name_active_idx.get("unique", False), (
            "idx_service_tracking_name_active MUST be UNIQUE — that is "
            "the A11 D5 same-name guard."
        )

        eng.dispose()


# ── 3-site name-pin test (1.A.7) ───────────────────────────────────


class TestThreeSiteIndexNamePin:
    """Index names MUST be byte-identical across the 3 sites (1.A.7).

    The .sql + models.py sites are pinned STRICTLY (Phase 1.A
    deliverable). The ``daemon/manager.py:_ensure_postgres_columns``
    site is gated with an explicit ``pytest.mark.skip`` because the
    F3 reassignment puts that block in Phase 1.C task 1.C.13b
    (NOT in Phase 1.A — manager.py is single-writer). 1.C.13b
    flips the skip to a strict assertion in one line; the
    skip message points to that exact handoff so the un-skip is
    obvious in review.

    If a future change renames an index at the ``models.py`` site
    OR the ``.sql`` site, this test fires immediately on the next
    CI run and surfaces the drift. The 3-site pin is the single
    source of truth for index-name drift detection until 1.C.13b
    un-skips the third arm.
    """

    SQL_FILE_REL = (
        "daemon/migrations/versions/"
        "20260915_212810_create_service_tracking.sql"
    )
    MODELS_FILE_REL = "daemon/repositories/service_tool/models.py"
    MANAGER_FILE_REL = "daemon/manager.py"

    INDEX_NAMES = (
        "idx_service_tracking_name_active",
        "idx_service_tracking_pid",
    )

    @pytest.fixture
    def repo_root(self) -> Path:
        """Repository root (``pyproject.toml`` sibling)."""
        # This test file lives at tests/unit/repositories/test_service_tool_repository.py
        # → parents[0]=repositories, [1]=unit, [2]=tests, [3]=<repo root>.
        return Path(__file__).resolve().parents[3]

    def _read(self, repo_root: Path, rel: str) -> str:
        path = repo_root / rel
        assert path.exists(), f"file missing: {path}"
        return path.read_text(encoding="utf-8")

    @pytest.mark.parametrize("index_name", INDEX_NAMES)
    def test_index_name_in_sql_migration(
        self, repo_root: Path, index_name: str
    ) -> None:
        """The index name appears verbatim in the .sql migration file.

        Pinned STRICTLY — the ``.sql`` is the canonical SQLite path;
        Phase 1.A ships it. If a future change renames an index at
        either the .sql OR models.py site, this test (and the
        models.py arm below) fails immediately and surfaces the
        drift before merge.
        """
        sql_text = self._read(repo_root, self.SQL_FILE_REL)
        assert index_name in sql_text, (
            f"index {index_name!r} must appear in {self.SQL_FILE_REL} "
            f"(the SQLite migration path). 3-site name-pin."
        )

    @pytest.mark.parametrize("index_name", INDEX_NAMES)
    def test_index_name_in_models_py(
        self, repo_root: Path, index_name: str
    ) -> None:
        """The index name appears verbatim in models.py ``__table_args__``."""
        models_text = self._read(repo_root, self.MODELS_FILE_REL)
        assert index_name in models_text, (
            f"index {index_name!r} must appear in {self.MODELS_FILE_REL} "
            f"(SQLModel __table_args__, the create_all path). "
            f"3-site name-pin."
        )

    @pytest.mark.parametrize("index_name", INDEX_NAMES)
    def test_index_name_in_manager_py(
        self, repo_root: Path, index_name: str
    ) -> None:
        """The 3rd site — daemon/manager.py ``_ensure_postgres_columns``.

        STRICT since Phase 1.C task 1.C.13b (the F3 reassignment —
        manager.py is single-writer): the byte-identical CREATE TABLE /
        CREATE UNIQUE INDEX / CREATE INDEX statements now live in
        ``EnsembleManager._ensure_postgres_columns``, so this arm
        mirrors ``test_index_name_in_models_py`` exactly. If any of
        the three sites (.sql / models.py / manager.py) drifts, this
        test fails immediately and surfaces the drift before merge.
        """
        manager_text = self._read(repo_root, self.MANAGER_FILE_REL)
        assert index_name in manager_text, (
            f"index {index_name!r} must appear in "
            f"{self.MANAGER_FILE_REL} (EnsembleManager."
            f"_ensure_postgres_columns, the PG mirror). "
            f"3-site name-pin — Phase 1.C task 1.C.13b added "
            f"the byte-identical CREATE INDEX statements."
        )

    def test_models_table_args_has_unique_partial_name_active(
        self, repo_root: Path
    ) -> None:
        """The partial UNIQUE index is declared ``unique=True`` with the
        dual-dialect WHERE clause (A11)."""
        from daemon.repositories.service_tool.models import ServiceTracking

        indexes = ServiceTracking.__table_args__
        name_active_idx = next(
            i for i in indexes if i.name == "idx_service_tracking_name_active"
        )
        # unique=True (the partial UNIQUE guard).
        assert name_active_idx.unique is True, (
            "idx_service_tracking_name_active MUST be declared unique=True "
            "(the A11 D5 same-name guard)."
        )
        # dual-dialect WHERE clause — both dialect paths present.
        dialect_options = name_active_idx.dialect_options
        assert "sqlite" in dialect_options, (
            "idx_service_tracking_name_active MUST declare a "
            "sqlite_where clause (precedent: report_injection/models.py:"
            "227-235 dual-dialect partial index)."
        )
        assert "postgresql" in dialect_options, (
            "idx_service_tracking_name_active MUST declare a "
            "postgresql_where clause (precedent: report_injection/models."
            "py:227-235 dual-dialect partial index)."
        )

    def test_pid_index_is_not_unique(self, repo_root: Path) -> None:
        """The PID index is plain (non-unique) — PIDs are kernel-recycled."""
        from daemon.repositories.service_tool.models import ServiceTracking

        indexes = ServiceTracking.__table_args__
        pid_idx = next(i for i in indexes if i.name == "idx_service_tracking_pid")
        assert pid_idx.unique is False, (
            "idx_service_tracking_pid MUST be non-unique — PIDs are "
            "kernel-recycled, a unique index would also reject "
            "EXITED rows that still carry the historical PID."
        )


# ── Model create_all smoke (1.A.3 acceptance) ──────────────────────


class TestCreateAllEmitsBothIndexes:
    """Per 1.A.3 acceptance: ``SQLModel.metadata.create_all`` must emit
    both indexes with the documented names + ``unique=True`` on
    ``idx_service_tracking_name_active``.

    A direct smoke (``uv run python -c "from
    daemon.repositories.service_tool.models import ServiceTracking;
    ..."``) is hard to keep stable; the in-fixture version below
    runs on the shared file-backed engine used by every other
    test in this file.
    """

    def test_create_all_emits_both_indexes(
        self, engine: Engine  # noqa: ARG002
    ) -> None:
        """The fixture's ``create_all`` already exercised the emit;
        we just verify the schema is what the plan expects."""
        inspector = sa_inspect(engine)
        indexes = inspector.get_indexes("service_tracking")
        index_names = {idx["name"] for idx in indexes}
        assert "idx_service_tracking_name_active" in index_names
        assert "idx_service_tracking_pid" in index_names

        name_active_idx = next(
            i for i in indexes if i["name"] == "idx_service_tracking_name_active"
        )
        assert name_active_idx.get("unique", False), (
            "create_all must emit idx_service_tracking_name_active as "
            "UNIQUE (the A11 D5 same-name guard)."
        )

        pid_idx = next(i for i in indexes if i["name"] == "idx_service_tracking_pid")
        assert not pid_idx.get("unique", False), (
            "create_all must emit idx_service_tracking_pid as plain "
            "(non-unique) — PIDs are kernel-recycled."
        )

    def test_status_field_defaults_to_starting(self) -> None:
        """``status`` field default is ``ServiceStatus.STARTING.value`` (Python-side).

        The D2 schema declares ``status`` with Python-side default
        ``Field(default=ServiceStatus.STARTING.value)`` — SQLModel
        emits this as a client-side default (the Python default
        fires when the ORM constructs an instance without an
        explicit value). The create_all DDL does NOT carry a
        ``DEFAULT 'starting'`` clause (mirrors the
        ``task/models.py:104-117`` precedent: SQLModel does NOT
        propagate ``Field(default=...)`` to SQL DEFAULT — the
        ``sa_column=Column(..., server_default=...)`` declaration
        is required to materialize the SQL DEFAULT, and we do not
        take that path here). The .sql migration declares
        ``DEFAULT 'starting'`` server-side (so a raw ``INSERT INTO
        service_tracking (name, …) VALUES (…)`` would land the
        default). Both paths agree semantically: a row inserted
        without an explicit ``status`` carries ``status='starting'``.

        This test pins the Python-side default — the source of
        truth for ORM-mediated inserts (every service-tracked
        write goes through ``ServiceRepo.insert`` /
        ``insert_with_status`` which constructs a
        ``ServiceTracking`` instance and lets the model default
        fire).
        """
        status_field = ServiceTracking.model_fields["status"]
        assert status_field.default == ServiceStatus.STARTING.value, (
            f"ServiceTracking.status default must be "
            f"ServiceStatus.STARTING.value; got {status_field.default!r}"
        )