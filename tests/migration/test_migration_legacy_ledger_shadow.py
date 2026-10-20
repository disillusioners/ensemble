"""Legacy-ledger shadow pin (reviewer W1) — the ``20261018_000001`` pre-mark.

**Reviewer W1 (verbatim intent).** A dev/sandbox SQLite DB whose ledger
PRE-MARKS ``20261018_000001`` — the workaround used verbatim in
``.agents/tester/RESULTS/2026-10-19-resource-telemetry-fix-verification.md:212``
— never gets ``secrets_audit.context``: the version-only pending filter
(``daemon/migrations/runner.py``, ``get_pending_migrations``:
``m.version not in applied``) shadows
``20261018_000001_secrets_audit_add_context.sql`` because ``000001`` is
already recorded, and ``secret_service._append_audit_in_conn``
(``daemon/services/secret_service.py``) has no tolerance for the missing
column → ``OperationalError`` on audit emit.

**What this file pins** (current behaviour — NOT a defect to be fixed
here; the leader adjudicated that the shadow is documented, not re-keyed):

1. The shadow is SILENT — the boot does not crash.
2. ``20261018_000001_secrets_audit_add_context.sql`` is discovered but
   SHADOWED: never pending, never applied, because the version-only
   pending filter matches the pre-marked ledger row.
3. ``20261018_000002_create_app_settings.sql`` applies cleanly (its
   ``CREATE TABLE IF NOT EXISTS`` is idempotent) and gets its own
   ledger row.
4. ``secrets_audit`` carries NO ``context`` column after the boot — the
   documented consequence.
5. The documented remediation — the exact DDL from the migration file,
   ``ALTER TABLE secrets_audit ADD COLUMN context TEXT;`` — is correct
   and sufficient: the column appears and the audit emit then succeeds.

Fence discipline (project critical notes):

* Real ``MigrationRunner`` against real SQLite — NO mocks.
* ``tempfile.mkdtemp`` under the OS temp dir — NEVER a HOME-derived
  path (mktemp-fence convention, incident 2026-10-12).
* No daemon boot, no port binding, no PG connection.
* Run ONLY this file (``-q``) — never bare pytest, never the full suite.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError
from sqlmodel import SQLModel

# Register every SQLModel table BEFORE create_all runs (manager-init
# parity — the secrets models join SQLModel.metadata exactly as the
# api.py lifespan imports them).
from daemon.repositories.secrets.models import (  # noqa: F401
    Secret,
    SecretAuditEvent,
)
from daemon.migrations.models import SchemaMigration  # noqa: F401
from daemon.repositories.snapshot.models import (  # noqa: F401
    Snapshot,
    SnapshotEmbedding,
    SnapshotUsageCounter,
)
from daemon.repositories.maintenance_runs.models import MaintenanceRun  # noqa: F401
from daemon.plugin_subsystem.drift_event_publisher import DriftEvent  # noqa: F401
from daemon.models.app_settings import AppSetting  # noqa: F401

from daemon.migrations.runner import MigrationFile, MigrationRunner
from daemon.services import secret_service

# The REAL migrations directory — repo-relative resolution (NEVER a
# HOME-derived path; tests/migration/… → parents[2] = repo root).
REAL_VERSIONS = (
    Path(__file__).resolve().parents[2] / "daemon" / "migrations" / "versions"
)

# The migration whose application is shadowed, and the file that
# replaced the pre-rename interloper.
SHADOWED_FILE = "20261018_000001_secrets_audit_add_context.sql"
SHADOWED_VERSION = "20261018_000001"
REPLACEMENT_FILE = "20261018_000002_create_app_settings.sql"
REPLACEMENT_VERSION = "20261018_000002"

# The pre-rename ledger row's ``name`` — the attribution the pre-fix
# ledger carried. ``MigrationFile.parse`` derives ``name`` from the
# filename slug (``create_app_settings`` → spaces), NOT from the
# version, so the renamed file still produces this exact string.
PRE_RENAME_NAME = "create app settings"

# The pre-rename row's fixed timestamp — a constant so the seeded row
# can be asserted VERBATIM.
PRE_RENAME_APPLIED_AT = "2026-10-19T00:00:00+00:00"

# The PRE-EXISTING SQLite blocker (out of scope — separate branch):
# ``ALTER TABLE … DROP CONSTRAINT IF EXISTS`` is invalid on SQLite.
# The documented workaround is to ledger-pre-mark the file with a
# ``skip_reason`` so it never fires.
SQLITE_BLOCKER_VERSION = "20260714_000001"
SQLITE_BLOCKER_FILE = "20260714_000001_widen_job_queue_type_constraint.sql"
SQLITE_BLOCKER_SKIP_REASON = (
    "pre-existing SQLite DROP CONSTRAINT blocker — documented workaround "
    "(ledger pre-mark); fix lives on a separate branch"
)

# The migration that CREATES ``secrets_audit`` in its P1 shape (no
# ``context`` column). Its DDL is read from the shipped file at runtime
# so the test's table shape can never drift from the migration tree.
SECRETS_TABLES_FILE = "20261017_000001_create_secrets_tables.sql"

# The exact remediation statement, quoted from
# ``20261018_000001_secrets_audit_add_context.sql:66``.
EXPECTED_REMEDIATION_DDL = "ALTER TABLE secrets_audit ADD COLUMN context TEXT;"


def _up_section(path: Path) -> str:
    """Extract the ``-- UP`` section of a migration file."""
    import re

    content = path.read_text(encoding="utf-8")
    match = re.search(r"--\s*UP\s*\n(.*?)(?=--\s*DOWN|$)", content, re.DOTALL)
    assert match is not None, f"no -- UP section in {path.name}"
    return match.group(1).strip()


def _statements(up_sql: str) -> list[str]:
    """Split a migration UP section into statements (runner idiom)."""
    code_lines = [ln for ln in up_sql.splitlines() if not ln.lstrip().startswith("--")]
    clean_sql = "\n".join(code_lines)
    return [s.strip() for s in clean_sql.split(";") if s.strip()]


def _p1_shape_audit_ddl() -> str:
    """The P1-shape ``secrets_audit`` DDL, read from the shipped file.

    ``20261017_000001_create_secrets_tables.sql`` creates
    ``secrets_audit`` WITHOUT the ``context`` column — that column is
    what ``20261018_000001_secrets_audit_add_context.sql`` adds. Reading
    the DDL from the file (rather than hand-copying it) keeps the
    fixture faithful to the migration tree.
    """
    up_sql = _up_section(REAL_VERSIONS / SECRETS_TABLES_FILE)
    for stmt in _statements(up_sql):
        if "CREATE TABLE IF NOT EXISTS secrets_audit" in stmt:
            return stmt + ";"
    raise AssertionError(
        f"no 'CREATE TABLE IF NOT EXISTS secrets_audit' in {SECRETS_TABLES_FILE}"
    )


def _remediation_ddl() -> str:
    """The exact remediation statement, read from the migration file."""
    up_sql = _up_section(REAL_VERSIONS / SHADOWED_FILE)
    statements = _statements(up_sql)
    assert len(statements) == 1, statements
    return statements[0] + ";"


def _column_names(engine: Engine, table: str) -> list[str]:
    with engine.connect() as conn:
        rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
    return [row[1] for row in rows]


def _build_pre_marked_db(tmp: Path) -> tuple[Engine, MigrationRunner]:
    """Build a SQLite DB whose ledger PRE-MARKS ``20261018_000001``.

    Mirrors the workaround used verbatim in
    ``.agents/tester/RESULTS/2026-10-19-resource-telemetry-fix-verification.md:212``:
    a ledger row for the PRE-RENAME version ``20261018_000001`` whose
    ``name`` mirrors the pre-fix attribution (``create app settings`` —
    the slug-derived name of the then-current
    ``20261018_000001_create_app_settings.sql``).

    ``secrets_audit`` is rebuilt in its P1 shape (the DDL of
    ``20261017_000001_create_secrets_tables.sql``) so the ``context``
    column is ABSENT — exactly the state a DB that never ran
    ``20261018_000001_secrets_audit_add_context.sql`` is in.

    Returns:
        ``(engine, runner)`` over a fresh temp SQLite database.
    """
    engine = create_engine(f"sqlite:///{tmp}/shadow.db")
    SQLModel.metadata.create_all(engine)

    # Rebuild ``secrets_audit`` in its P1 shape (no ``context`` column).
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE secrets_audit"))
        conn.execute(text(_p1_shape_audit_ddl()))

    runner = MigrationRunner(engine, migrations_dir=REAL_VERSIONS)
    runner.ensure_migrations_table()

    # Documented workaround for the PRE-EXISTING SQLite blocker.
    blocked = MigrationFile.parse(REAL_VERSIONS / SQLITE_BLOCKER_FILE)
    runner._persist_ledger_row(
        blocked,
        execution_time_ms=0,
        skip_reason=SQLITE_BLOCKER_SKIP_REASON,
    )

    # The shadow row: the PRE-RENAME ledger entry.
    shadow_row = SchemaMigration(
        version=SHADOWED_VERSION,
        name=PRE_RENAME_NAME,
        applied_at=PRE_RENAME_APPLIED_AT,
        execution_time_ms=0,
        checksum=None,
        precondition=None,
        skip_reason=None,
    )
    with runner.engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO schema_migrations "
                "(version, name, applied_at, execution_time_ms, checksum, "
                " precondition, skip_reason) "
                "VALUES (:version, :name, :applied_at, :execution_time_ms, "
                ":checksum, :precondition, :skip_reason)"
            ),
            {
                "version": shadow_row.version,
                "name": shadow_row.name,
                "applied_at": shadow_row.applied_at,
                "execution_time_ms": shadow_row.execution_time_ms,
                "checksum": shadow_row.checksum,
                "precondition": shadow_row.precondition,
                "skip_reason": shadow_row.skip_reason,
            },
        )

    return engine, runner


def _ledger_rows(engine: Engine) -> list[tuple]:
    with engine.connect() as conn:
        return conn.execute(
            text(
                "SELECT version, name, applied_at, execution_time_ms, checksum, "
                "precondition, skip_reason FROM schema_migrations "
                "ORDER BY version"
            )
        ).fetchall()


# ─────────────────────────────────────────────────────────────────────
# 1. The shadow itself
# ─────────────────────────────────────────────────────────────────────


def test_pre_marked_20261018_000001_shadows_secrets_audit_context_migration():
    """W1: the pre-marked version SHADOWS the secrets_audit migration.

    The boot is silent (no crash), the secrets_audit migration is
    discovered but never pending and never applied, the renamed
    ``create_app_settings`` applies cleanly with its own ledger row, and
    ``secrets_audit.context`` is ABSENT afterwards — the documented
    consequence, pinned as current behaviour.
    """
    tmp = Path(tempfile.mkdtemp(prefix="ens-migdup-shadow-"))
    try:
        engine, runner = _build_pre_marked_db(tmp)
        try:
            # The seeded pre-rename row is present VERBATIM.
            seeded = [
                row
                for row in _ledger_rows(engine)
                if row[0] == SHADOWED_VERSION
            ]
            assert seeded == [
                (
                    SHADOWED_VERSION,
                    PRE_RENAME_NAME,
                    PRE_RENAME_APPLIED_AT,
                    0,
                    None,
                    None,
                    None,
                )
            ], seeded

            # The shadowed migration IS discovered — it is the version-only
            # pending filter that shadows it, not a missing file.
            discovered = runner.discover_migrations()
            discovered_versions = [m.version for m in discovered]
            assert SHADOWED_VERSION in discovered_versions, discovered_versions
            assert any(
                m.path.name == SHADOWED_FILE for m in discovered
            ), [m.path.name for m in discovered]

            # ...but it is NOT pending (the version is already recorded).
            pending_versions = [m.version for m in runner.get_pending_migrations()]
            assert SHADOWED_VERSION not in pending_versions, pending_versions

            # The boot does NOT crash — the shadow is silent.
            applied = runner.run_pending_migrations()

            # The shadowed migration was NOT applied.
            assert SHADOWED_VERSION not in applied, applied

            # The renamed create_app_settings applies cleanly and gets its
            # OWN ledger row (distinct version, same slug-derived name).
            assert REPLACEMENT_VERSION in applied, applied
            ledger = _ledger_rows(engine)
            replacement_rows = [row for row in ledger if row[0] == REPLACEMENT_VERSION]
            assert len(replacement_rows) == 1, ledger
            assert replacement_rows[0][1] == PRE_RENAME_NAME, replacement_rows
            # Both rows carry the same slug-derived name — the ledger
            # disambiguates them by VERSION only.
            assert [row[0] for row in ledger if row[1] == PRE_RENAME_NAME] == [
                SHADOWED_VERSION,
                REPLACEMENT_VERSION,
            ], ledger

            # The consequence: ``secrets_audit.context`` is ABSENT.
            audit_columns = _column_names(engine, "secrets_audit")
            assert "context" not in audit_columns, audit_columns

            # W1's consequence, proven: the audit emit raises
            # OperationalError — no tolerance for the missing column.
            with engine.connect() as conn:
                with pytest.raises(OperationalError) as excinfo:
                    secret_service._append_audit_in_conn(
                        conn,
                        action="create",
                        secret_id="shadow-probe",
                        actor="tester",
                        context=None,
                    )
            assert "context" in str(excinfo.value), str(excinfo.value)
        finally:
            engine.dispose()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)  # ONLY this file's own mktemp dir


# ─────────────────────────────────────────────────────────────────────
# 2. The documented remediation
# ─────────────────────────────────────────────────────────────────────


def test_documented_remediation_adds_secrets_audit_context():
    """The remediation quoted in the runner's guard comment is SUFFICIENT.

    The exact DDL from ``20261018_000001_secrets_audit_add_context.sql``
    — ``ALTER TABLE secrets_audit ADD COLUMN context TEXT;`` — adds the
    missing column, after which the audit emit succeeds and lands a row.
    """
    tmp = Path(tempfile.mkdtemp(prefix="ens-migdup-remed-"))
    try:
        engine, runner = _build_pre_marked_db(tmp)
        try:
            runner.run_pending_migrations()

            # The remediation statement, read from the migration file.
            ddl = _remediation_ddl()
            # …is byte-for-byte the statement quoted in the runner's
            # guard comment (and in this file's header constant).
            assert ddl == EXPECTED_REMEDIATION_DDL, (ddl, EXPECTED_REMEDIATION_DDL)

            # Pre-condition: the column is missing.
            assert "context" not in _column_names(engine, "secrets_audit")

            # Apply the documented remediation.
            with engine.begin() as conn:
                conn.execute(text(ddl))

            # The column now exists…
            assert "context" in _column_names(engine, "secrets_audit")

            # …and the audit emit now succeeds (the remediation is
            # sufficient for the W1 consequence).
            with engine.begin() as conn:
                secret_service._append_audit_in_conn(
                    conn,
                    action="create",
                    secret_id="remediated-probe",
                    actor="tester",
                    context="bash_env",
                )
            with engine.connect() as conn:
                landed = conn.execute(
                    text(
                        "SELECT secret_id, context FROM secrets_audit "
                        "WHERE secret_id = 'remediated-probe'"
                    )
                ).fetchall()
            assert landed == [("remediated-probe", "bash_env")], landed
        finally:
            engine.dispose()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)  # ONLY this file's own mktemp dir
