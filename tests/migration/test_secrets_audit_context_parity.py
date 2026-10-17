"""agent-secrets P2 phase-0 hook: secrets_audit ``context`` create_all parity.

Pins (p2-architecture-recommendation §4 + §5, phase-0 row of §6):

* **Fresh-DB parity** — after ``SQLModel.metadata.create_all`` on a
  DEDICATED scratch database (PG truth path + SQLite portability
  leg, mirroring ``tests/integration/test_secrets_v1_basic.py``),
  ``secrets_audit`` carries the P2 ``context`` column: nullable,
  unbounded text family (no max_length — mirrors the
  ``public_material`` TEXT precedent), default NULL.
* **Value roundtrip** — a NULL P1-era row and a tagged P2 row
  (``'bash_env'``) both persist and read back.
* **Migration-file parity leg** — the additive migration
  ``20261018_000001_secrets_audit_add_context.sql`` applies through
  the real MigrationRunner against a P1-shape SQLite table (the
  column set BEFORE this commission) and lands ``context``; on a
  fresh create_all'd SQLite database it re-fires through the
  runner's documented ``duplicate column name`` idempotent catch
  (runner.py:568) and records its ledger row. The runner is
  deliberately handed an ISOLATED migrations dir containing ONLY
  this file: the full-history dir cannot run on fresh SQLite (the
  known PRE-EXISTING ``20260714_000001`` breaker — out of scope,
  do not fix here; ordering alone proves the P2 file is never
  reached on that path).

Fences (project critical notes):

* ``ENSEMBLE_SELF_ENV``-free: no daemon boot; pure engine-level
  tests against DEDICATED scratch databases.
* PG: per-test scratch DB ``ensemble_p2_ctx_test_<8hex>``
  created + dropped; HARD ABORT if the fixture ever resolves a
  live-shaped DB name (``ensemble_prod`` / ``ensemble_dev``).
* SQLite: ``tempfile.mkdtemp`` under the OS temp dir — NEVER a
  HOME-derived path (mktemp-fence convention); WAL sidecars
  cleaned in teardown.
* Run ONLY this file (``--tb=short -q``) — never bare pytest,
  never the full suite (later P2 phases own it).
"""

from __future__ import annotations

import os
import tempfile
import uuid
from pathlib import Path
from typing import Iterator

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlmodel import SQLModel

# Register every SQLModel table BEFORE create_all runs (manager-init
# parity — the secrets models join SQLModel.metadata exactly as the
# api.py lifespan imports them).
from daemon.repositories.secrets.models import (  # noqa: F401
    Secret,
    SecretAuditEvent,
)
from daemon.migrations.models import SchemaMigration  # noqa: F401
# The manager-__init__-body model set (manager.py:560-625): the
# existing-PG leg executes the REAL ``_ensure_postgres_columns``
# statements, several of which target these tables (drift_events
# indexes, maintenance_runs probe, auth / mcp_kb_keys / snapshot
# lanes) — production registers them in ``InstanceManager.__init__``
# before ``create_all``; mirror that set here so create_all emits
# the same tables a real boot sees.
from daemon.repositories.snapshot.models import (  # noqa: F401
    Snapshot,
    SnapshotEmbedding,
    SnapshotUsageCounter,
)
from daemon.repositories.maintenance_runs.models import MaintenanceRun  # noqa: F401
from daemon.plugin_subsystem.drift_event_publisher import DriftEvent  # noqa: F401
from daemon.repositories.auth_session.models import (  # noqa: F401
    AuthBootstrapConsumption,
    AuthSession,
    AuthState,
    BearerToken,
)
from daemon.repositories.mcp_kb_keys.models import McpKbKey  # noqa: F401
from daemon.manager import InstanceManager  # existing-PG leg seam
from daemon.migrations.runner import MigrationFile, MigrationRunner

# The migration under test — repo-relative resolution (NEVER a
# HOME-derived path; tests/migration/… → parents[2] = repo root).
_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "daemon" / "migrations" / "versions"
    / "20261018_000001_secrets_audit_add_context.sql"
)

# P1-shape secrets_audit DDL — the column set BEFORE this commission
# (mirrors 20261017_000001_create_secrets_tables.sql's audit table).
_P1_SHAPE_AUDIT_DDL_SQLITE = """
CREATE TABLE secrets_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    secret_id TEXT,
    action TEXT NOT NULL,
    actor TEXT NOT NULL,
    at TEXT NOT NULL
)"""


# ─────────────────────────────────────────────────────────────────────
# PG scratch-DB convention (mirrors
# tests/integration/test_secrets_v1_basic.py:81-196 — attributed copy,
# prefix + forbidden-set tightened for the P2 lane)
# ─────────────────────────────────────────────────────────────────────

_PG_HOST = os.environ.get("PG_TEST_HOST", "127.0.0.1")
_PG_PORT = int(os.environ.get("PG_TEST_PORT", "5432"))
_PG_USER = os.environ.get("PG_TEST_USER", "ensemble")
_PG_PASSWORD = os.environ.get("PG_TEST_PASSWORD", "ensemble_dev")
_PG_MAINT_DB = os.environ.get("PG_TEST_MAINT_DB", "ensemble_dev")
_PG_SCRATCH_PREFIX = "ensemble_p2_ctx_test_"

#: HARD ABORT set — a resolved target in this set means the fixture
#: is pointed at a live database; fail the whole session instead of
#: writing test rows into it (env-poison 3-guard convention).
_PG_FORBIDDEN_DBS = frozenset({"ensemble_prod", "ensemble_dev"})


def _pg_admin_engine():
    """Maintenance engine (AUTOCOMMIT) for CREATE/DROP DATABASE.

    Probes connectivity first and distinguishes auth failures from
    "PG is down" so the skip reason is diagnostic. Returns
    ``(None, reason)`` when PG is unreachable — the caller skips
    loudly (CI portability, P1 precedent).
    """
    from sqlalchemy.exc import DBAPIError, OperationalError

    url = (
        f"postgresql+psycopg://{_PG_USER}:{_PG_PASSWORD}"
        f"@{_PG_HOST}:{_PG_PORT}/{_PG_MAINT_DB}"
    )
    engine = create_engine(url, isolation_level="AUTOCOMMIT", pool_pre_ping=True)
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except OperationalError as exc:
        engine.dispose()
        orig = getattr(exc, "orig", exc)
        sqlstate = getattr(getattr(orig, "diag", None), "sqlstate", None)
        kind = (
            "auth failure (check PG_TEST_USER / PG_TEST_PASSWORD)"
            if sqlstate in {"28000", "28P01"}
            else "unreachable"
        )
        return None, (
            f"PostgreSQL {kind} at {_PG_HOST}:{_PG_PORT}/{_PG_MAINT_DB}: {exc}"
        )
    except DBAPIError as exc:
        engine.dispose()
        return None, f"PostgreSQL DBAPI error at {_PG_HOST}:{_PG_PORT}: {exc}"
    return engine, None


@pytest.fixture
def _postgres_scratch_engine() -> Iterator[Engine]:
    """Yield a fresh scratch-PostgreSQL engine per test.

    Lifecycle: probe maintenance connection → CREATE DATABASE
    ``ensemble_p2_ctx_test_<8hex>`` → SQLModel.metadata.create_all
    (the PG truth path — api-lifespan parity) → yield → dispose →
    terminate stray backends → DROP DATABASE.
    """
    admin, skip_reason = _pg_admin_engine()
    if admin is None:
        pytest.skip(skip_reason)

    scratch_db = f"{_PG_SCRATCH_PREFIX}{uuid.uuid4().hex[:8]}"
    if scratch_db in _PG_FORBIDDEN_DBS or _PG_MAINT_DB in _PG_FORBIDDEN_DBS:
        pytest.exit(
            f"HARD ABORT: PG scratch fixture resolved a live-shaped "
            f"database (scratch={scratch_db!r}, maint={_PG_MAINT_DB!r}) "
            f"— refuse to point secrets tests at a live database.",
            returncode=1,
        )

    engine: Engine | None = None
    try:
        with admin.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{scratch_db}"'))
        scratch_url = (
            f"postgresql+psycopg://{_PG_USER}:{_PG_PASSWORD}"
            f"@{_PG_HOST}:{_PG_PORT}/{scratch_db}"
        )
        engine = create_engine(scratch_url, pool_pre_ping=True)
        SQLModel.metadata.create_all(engine)
        yield engine
    finally:
        if engine is not None:
            engine.dispose()
        try:
            with admin.connect() as conn:
                conn.execute(
                    text(
                        "SELECT pg_terminate_backend(pid) FROM "
                        "pg_stat_activity WHERE datname = :d "
                        "AND pid <> pg_backend_pid()"
                    ),
                    {"d": scratch_db},
                )
                conn.execute(text(f'DROP DATABASE IF EXISTS "{scratch_db}"'))
        finally:
            admin.dispose()


@pytest.fixture
def _postgres_p1shape_scratch_engine() -> Iterator[Engine]:
    """Fresh scratch PG simulating an EXISTING (P1-era) database.

    Lifecycle mirrors ``_postgres_scratch_engine`` (create database →
    create_all in boot order → yield → terminate backends → drop),
    but AFTER create_all the P2 ``context`` column is REMOVED from
    ``secrets_audit`` — the resulting shape is exactly what an
    existing PG database holds after the P1 commission (table
    present, no context column). Other tables keep their
    current-model shapes: every ``_ensure_postgres_columns``
    statement is ``IF NOT EXISTS`` / probe-gated, so it tolerates
    both shapes and this simulation isolates the one column this
    commission owns.
    """
    admin, skip_reason = _pg_admin_engine()
    if admin is None:
        pytest.skip(skip_reason)

    scratch_db = f"{_PG_SCRATCH_PREFIX}{uuid.uuid4().hex[:8]}"
    if scratch_db in _PG_FORBIDDEN_DBS or _PG_MAINT_DB in _PG_FORBIDDEN_DBS:
        pytest.exit(
            f"HARD ABORT: PG scratch fixture resolved a live-shaped "
            f"database (scratch={scratch_db!r}, maint={_PG_MAINT_DB!r}) "
            f"— refuse to point secrets tests at a live database.",
            returncode=1,
        )

    engine: Engine | None = None
    try:
        with admin.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{scratch_db}"'))
        scratch_url = (
            f"postgresql+psycopg://{_PG_USER}:{_PG_PASSWORD}"
            f"@{_PG_HOST}:{_PG_PORT}/{scratch_db}"
        )
        engine = create_engine(scratch_url, pool_pre_ping=True)
        SQLModel.metadata.create_all(engine)  # boot order: model parity first
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE secrets_audit DROP COLUMN context"))
        yield engine
    finally:
        if engine is not None:
            engine.dispose()
        try:
            with admin.connect() as conn:
                conn.execute(
                    text(
                        "SELECT pg_terminate_backend(pid) FROM "
                        "pg_stat_activity WHERE datname = :d "
                        "AND pid <> pg_backend_pid()"
                    ),
                    {"d": scratch_db},
                )
                conn.execute(text(f'DROP DATABASE IF EXISTS "{scratch_db}"'))
        finally:
            admin.dispose()


@pytest.fixture
def _sqlite_scratch_engine() -> Iterator[Engine]:
    """Yield a fresh file-SQLite engine (OS-temp mkdtemp fence)."""
    db_dir = tempfile.mkdtemp(prefix="p2_ctx_parity_")
    db_path = os.path.join(db_dir, "scratch.db")
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    try:
        yield engine
    finally:
        engine.dispose()
        for ext in ("", "-wal", "-shm"):
            try:
                os.unlink(db_path + ext)
            except OSError:
                pass


def _audit_columns_pg(engine: Engine) -> dict[str, tuple[str, str]]:
    """{column_name: (data_type, is_nullable)} for secrets_audit (PG)."""
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT column_name, data_type, is_nullable "
                "FROM information_schema.columns "
                "WHERE table_name = 'secrets_audit'"
            )
        ).fetchall()
    return {r[0]: (r[1], r[2]) for r in rows}


def _audit_columns_sqlite(engine: Engine) -> dict[str, tuple[str, bool]]:
    """{column_name: (declared_type, notnull)} for secrets_audit (SQLite)."""
    with engine.connect() as conn:
        rows = conn.execute(text("PRAGMA table_info(secrets_audit)")).fetchall()
    return {r[1]: (r[2], bool(r[3])) for r in rows}


# ─────────────────────────────────────────────────────────────────────
# Tests — the phase-0 parity hook
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.integration
def test_fresh_pg_create_all_carries_context_column(_postgres_scratch_engine):
    """Fresh PG: create_all emits secrets_audit.context (design phase-0)."""
    engine = _postgres_scratch_engine
    cols = _audit_columns_pg(engine)

    assert "context" in cols, f"context missing from fresh-PG secrets_audit: {sorted(cols)}"
    data_type, is_nullable = cols["context"]
    # Unbounded text family (no max_length → VARCHAR/TEXT without
    # a length cap — mirrors the public_material TEXT precedent and
    # the migration's ADD COLUMN ... TEXT).
    assert data_type in {"text", "character varying"}, (
        f"context must be unbounded text family, got {data_type!r}"
    )
    assert is_nullable == "YES", "context must be nullable (P1 rows stay NULL)"

    # Roundtrip: a NULL P1-era row + a tagged P2 row.
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO secrets_audit (secret_id, action, actor, at, context) "
                "VALUES (:sid, :action, :actor, :at, :ctx)"
            ),
            [
                {"sid": "s1", "action": "create", "actor": "agent-a", "at": "2026-10-18T00:00:00Z", "ctx": None},
                {"sid": "s1", "action": "use_marker", "actor": "agent-a", "at": "2026-10-18T00:00:01Z", "ctx": "bash_env"},
            ],
        )
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT action, context FROM secrets_audit ORDER BY at")
        ).fetchall()
    assert [tuple(r) for r in rows] == [
        ("create", None),
        ("use_marker", "bash_env"),
    ]


@pytest.mark.integration
def test_fresh_sqlite_create_all_carries_context_column(_sqlite_scratch_engine):
    """Fresh SQLite: create_all parity for secrets_audit.context."""
    engine = _sqlite_scratch_engine
    cols = _audit_columns_sqlite(engine)

    assert "context" in cols, f"context missing from fresh-SQLite secrets_audit: {sorted(cols)}"
    declared_type, notnull = cols["context"]
    assert notnull is False, "context must be nullable"
    # TEXT affinity — the declared type carries TEXT (VARCHAR is
    # TEXT-affinity too; assert the family, not the spelling).
    assert "TEXT" in declared_type.upper() or "VARCHAR" in declared_type.upper(), (
        f"context must be TEXT-affinity, got {declared_type!r}"
    )

    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO secrets_audit (secret_id, action, actor, at, context) "
                "VALUES ('s1', 'use_marker', 'agent-a', '2026-10-18T00:00:00Z', 'scrub')"
            )
        )
    with engine.connect() as conn:
        value = conn.execute(text("SELECT context FROM secrets_audit")).scalar()
    assert value == "scrub"


def test_migration_file_lands_context_on_p1_shape_sqlite(tmp_path):
    """The additive migration applies via the REAL runner.

    P1-shape table (pre-commission column set) → the runner applies
    20261018_000001 from an ISOLATED migrations dir (only this file —
    the full-history dir cannot run on fresh SQLite due to the
    PRE-EXISTING 20260714_000001 breaker, out of scope) → context
    column lands → ledger row records version + checksum.
    """
    assert _MIGRATION_PATH.exists(), f"migration file missing: {_MIGRATION_PATH}"

    iso_dir = tmp_path / "iso_versions"
    iso_dir.mkdir()
    (iso_dir / _MIGRATION_PATH.name).write_text(
        _MIGRATION_PATH.read_text(encoding="utf-8"), encoding="utf-8"
    )
    # Byte-identical copy — the checksum recorded in the ledger must
    # match the real file's checksum.
    parsed = MigrationFile.parse(_MIGRATION_PATH)
    iso_parsed = MigrationFile.parse(iso_dir / _MIGRATION_PATH.name)
    assert parsed.checksum == iso_parsed.checksum

    db_path = tmp_path / "p1_shape.db"
    engine = create_engine(f"sqlite:///{db_path}")
    try:
        with engine.begin() as conn:
            conn.execute(text(_P1_SHAPE_AUDIT_DDL_SQLITE))
        before = _audit_columns_sqlite(engine)
        assert "context" not in before, "P1-shape fixture must lack context"

        runner = MigrationRunner(engine, migrations_dir=iso_dir)
        applied = runner.run_pending_migrations()
        assert applied == ["20261018_000001"], f"unexpected applied set: {applied}"

        after = _audit_columns_sqlite(engine)
        assert "context" in after
        assert after["context"][1] is False  # nullable

        with engine.connect() as conn:
            led = conn.execute(
                text("SELECT version, checksum FROM schema_migrations")
            ).fetchall()
        assert len(led) == 1 and led[0][0] == "20261018_000001"
        assert led[0][1] == parsed.checksum

        # Re-run: ledger-gated idempotency — zero pending, no error.
        assert runner.run_pending_migrations() == []
    finally:
        engine.dispose()


def test_migration_file_idempotent_after_create_all(tmp_path):
    """Fresh create_all'd SQLite + the runner = documented idempotent skip.

    Boot order is create_all FIRST (model parity emits the column),
    then the runner — the migration's plain ADD COLUMN re-fires onto
    an existing column and the runner's ``duplicate column name``
    catch (runner.py:568) treats it as idempotent, recording the
    ledger row. This is the fresh-SQLite boot path for this file.
    """
    iso_dir = tmp_path / "iso_versions"
    iso_dir.mkdir()
    (iso_dir / _MIGRATION_PATH.name).write_text(
        _MIGRATION_PATH.read_text(encoding="utf-8"), encoding="utf-8"
    )

    db_path = tmp_path / "fresh_boot_order.db"
    engine = create_engine(f"sqlite:///{db_path}")
    try:
        SQLModel.metadata.create_all(engine)  # boot order: model parity first
        assert "context" in _audit_columns_sqlite(engine)

        runner = MigrationRunner(engine, migrations_dir=iso_dir)
        applied = runner.run_pending_migrations()
        assert applied == ["20261018_000001"], (
            f"migration must apply (idempotently) on the create_all'd DB: {applied}"
        )
        assert "context" in _audit_columns_sqlite(engine)

        with engine.connect() as conn:
            led = conn.execute(
                text("SELECT version FROM schema_migrations")
            ).fetchall()
        assert led == [("20261018_000001",)]
    finally:
        engine.dispose()


# ─────────────────────────────────────────────────────────────────────
# Existing-PG leg — R10 adjudication (2026-10-18): the
# ``EnsembleManager._ensure_postgres_columns`` entry is the PG lane
# that lands ``context`` on databases created BEFORE this commission.
# ─────────────────────────────────────────────────────────────────────


def _run_column_ensure(engine: Engine) -> None:
    """Exercise the REAL ``EnsembleManager._ensure_postgres_columns`` body.

    Bare-instance seam: ``__init__`` is skipped and only the two
    attributes the method (and its callee
    ``_migrate_overloaded_image_refs_rows``) read are supplied —
    ``_engine`` and ``_ensemble_config``. ``_ensemble_config = None``
    makes the config-gated image-refs migration and autovacuum tail
    self-guard-skip; the statements list carrying the secrets_audit
    entry runs UNCONDITIONALLY (manager.py: ``with self._engine.begin()
    as conn: for stmt in statements``), exactly as on a real PG boot.
    """
    manager = object.__new__(InstanceManager)
    manager._engine = engine  # noqa: SLF001 — deliberate test seam
    manager._ensemble_config = None  # noqa: SLF001
    manager._ensure_postgres_columns()


@pytest.mark.integration
def test_existing_pg_lands_context_via_column_ensure(
    _postgres_p1shape_scratch_engine,
):
    """Existing-PG leg: column-ensure lands ``context``; re-run idempotent.

    P1-era shape (table present, no context) → the REAL
    ``_ensure_postgres_columns`` pass → column lands (TEXT, nullable)
    → SECOND pass raises no 42710 ``DuplicateColumn`` (the IF NOT
    EXISTS form — the phase-0 gate proved the plain form in the .sql
    file is NOT self-idempotent on PG) and the column is unchanged.
    """
    engine = _postgres_p1shape_scratch_engine

    # P1-era state: the fixture removed the column.
    before = _audit_columns_pg(engine)
    assert "context" not in before, (
        f"fixture must present the P1-era shape (no context): {sorted(before)}"
    )

    # Mechanism: the REAL column-ensure pass (PG boot evolution path).
    _run_column_ensure(engine)

    landed = _audit_columns_pg(engine)
    assert "context" in landed, (
        f"column-ensure did not land context: {sorted(landed)}"
    )
    data_type, is_nullable = landed["context"]
    assert data_type == "text", (
        f"the IF NOT EXISTS entry must add TEXT, got {data_type!r}"
    )
    assert is_nullable == "YES", "context must stay nullable"

    # Idempotent re-run: no 42710 DuplicateColumn; column unchanged.
    _run_column_ensure(engine)
    again = _audit_columns_pg(engine)
    assert again["context"] == ("text", "YES"), (
        f"re-run must leave the column intact: {again.get('context')!r}"
    )
