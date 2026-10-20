"""Duplicate migration version-prefix guard + fresh-boot regression pins.

Commission (2026-10-20): two migration files shipped under the SAME
version prefix (``20261018_000001``), so a fresh SQLite boot died with::

    sqlalchemy.exc.IntegrityError: (sqlite3.IntegrityError) UNIQUE
    constraint failed: schema_migrations.version
    [parameters: ('20261018_000001', 'secrets audit add context', ...)]

— an error that names NEITHER offending file, and that fires only AFTER
every earlier migration has already been applied and committed (the
``schema_migrations`` ledger keys on ``version``, a PRIMARY KEY, so the
second file of the pair loses the insert race).

This file pins three things:

1. **Fresh-boot ordering** — a fresh SQLite database applies BOTH
   ``20261018`` migrations (the original
   ``20261018_000001_secrets_audit_add_context`` and the renamed
   ``20261018_000002_create_app_settings``) in registry sort order, with
   both ledger rows present.
2. **The regression pin** — the REAL ``daemon/migrations/versions/``
   directory carries NO duplicate version prefix (the two interlopers
   were renamed: ``create_app_settings`` → ``20261018_000002``,
   ``drop_admission_legacy`` → ``20260629_000001``).
3. **The loud guard** — a duplicate pair in a migrations directory
   fails the boot with ``DuplicateMigrationVersionError`` naming BOTH
   files, and applies NOTHING (the guard runs inside
   ``discover_migrations``, i.e. ahead of the apply loop). The
   PostgreSQL lane is unaffected: the PG early-return in
   ``run_pending_migrations`` stays AHEAD of the guard, so a PG boot
   never enumerates the directory at all.

Fence discipline (project critical notes):

* ``ENSEMBLE_SELF_ENV``-free: no daemon boot; engine-level tests only.
* SQLite: ``tempfile.mkdtemp`` under the OS temp dir — NEVER a
  HOME-derived path (mktemp-fence convention, incident 2026-10-12).
* PostgreSQL: NO CONNECTION. The PG leg builds a URL pointing at
  ``127.0.0.1:1`` (unreachable by construction) and asserts the
  early-return short-circuits BEFORE any filesystem work — if the
  guard were ever moved ahead of the PG early-return, this test would
  fail loudly rather than touch a live database.
* Run ONLY this file (``-q``) — never bare pytest, never the full suite.
"""

from __future__ import annotations

import re
import shutil
import tempfile
from pathlib import Path

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
from daemon.repositories.snapshot.models import (  # noqa: F401
    Snapshot,
    SnapshotEmbedding,
    SnapshotUsageCounter,
)
from daemon.repositories.maintenance_runs.models import MaintenanceRun  # noqa: F401
from daemon.plugin_subsystem.drift_event_publisher import DriftEvent  # noqa: F401
from daemon.models.app_settings import AppSetting  # noqa: F401

from daemon.migrations.runner import (
    DuplicateMigrationVersionError,
    MigrationError,
    MigrationFile,
    MigrationRunner,
)

# The REAL migrations directory — repo-relative resolution (NEVER a
# HOME-derived path; tests/migration/… → parents[2] = repo root).
REAL_VERSIONS = (
    Path(__file__).resolve().parents[2] / "daemon" / "migrations" / "versions"
)

# The version token is the filename's leading ``YYYYMMDD_HHMMSS``
# (parsed at ``MigrationFile.parse``) — the same token the ledger's
# ``version`` PRIMARY KEY stores.
_VERSION_RE = re.compile(r"^(\d{8}_\d{6})")

# The two migrations this commission is about.
APP_SETTINGS_VERSION = "20261018_000002"
APP_SETTINGS_FILE = "20261018_000002_create_app_settings.sql"
SECRETS_AUDIT_VERSION = "20261018_000001"
SECRETS_AUDIT_FILE = "20261018_000001_secrets_audit_add_context.sql"

# The PRE-EXISTING SQLite blocker (out of scope — separate branch).
# ``ALTER TABLE … DROP CONSTRAINT IF EXISTS`` is invalid on SQLite
# ("near \"EXISTS\": syntax error"), and the documented workaround is to
# ledger-pre-mark the file with a ``skip_reason`` so it never fires.
SQLITE_BLOCKER_VERSION = "20260714_000001"
SQLITE_BLOCKER_FILE = "20260714_000001_widen_job_queue_type_constraint.sql"
SQLITE_BLOCKER_SKIP_REASON = (
    "pre-existing SQLite DROP CONSTRAINT blocker — documented workaround "
    "(ledger pre-mark); fix lives on a separate branch"
)


def _version_of(path: Path) -> str | None:
    """Extract the ``YYYYMMDD_HHMMSS`` version token from a filename."""
    match = _VERSION_RE.match(path.stem)
    return match.group(1) if match else None


def _fresh_sqlite_engine(tmp: Path) -> Engine:
    """Build a fresh SQLite engine whose schema mirrors a real boot.

    ``SQLModel.metadata.create_all`` is what a real daemon boot does
    before the .sql runner fires, so the runner sees the tables its
    ``ALTER`` statements target.
    """
    engine = create_engine(f"sqlite:///{tmp}/boot.db")
    SQLModel.metadata.create_all(engine)
    return engine


# ─────────────────────────────────────────────────────────────────────
# 1. Fresh-SQLite-boot: both 20261018 migrations apply, in order
# ─────────────────────────────────────────────────────────────────────


def test_fresh_sqlite_boot_applies_both_20261018_migrations_in_registry_order():
    """A fresh SQLite boot applies BOTH ``20261018`` migrations.

    Before the commission this died with
    ``UNIQUE constraint failed: schema_migrations.version`` on the
    second ``20261018_000001`` file. The fix renames the interloper to
    ``20261018_000002``; this pins that both now land, in registry sort
    order, with both ledger rows present.
    """
    tmp = Path(tempfile.mkdtemp(prefix="ens-migdup-freshboot-"))
    try:
        engine = _fresh_sqlite_engine(tmp)
        try:
            runner = MigrationRunner(engine, migrations_dir=REAL_VERSIONS)
            runner.ensure_migrations_table()

            # Documented workaround for the PRE-EXISTING SQLite blocker
            # (20260714_000001) — ledger-pre-mark via the runner's own
            # skip_reason path so it never fires.
            blocked = MigrationFile.parse(REAL_VERSIONS / SQLITE_BLOCKER_FILE)
            runner._persist_ledger_row(
                blocked,
                execution_time_ms=0,
                skip_reason=SQLITE_BLOCKER_SKIP_REASON,
            )

            applied = runner.run_pending_migrations()

            # Both 20261018 migrations applied.
            assert SECRETS_AUDIT_VERSION in applied, applied
            assert APP_SETTINGS_VERSION in applied, applied

            # …in registry sort order (the runner applies pending
            # migrations in version-sorted order).
            assert applied.index(SECRETS_AUDIT_VERSION) < applied.index(
                APP_SETTINGS_VERSION
            ), applied
            assert applied == sorted(applied), (
                "applied migrations must follow registry (version) sort order"
            )

            # Both ledger rows present, with the expected names.
            with engine.connect() as conn:
                rows = conn.execute(
                    text(
                        "SELECT version, name FROM schema_migrations "
                        "WHERE version IN (:a, :b) ORDER BY version"
                    ),
                    {"a": SECRETS_AUDIT_VERSION, "b": APP_SETTINGS_VERSION},
                ).fetchall()
            assert rows == [
                (SECRETS_AUDIT_VERSION, "secrets audit add context"),
                (APP_SETTINGS_VERSION, "create app settings"),
            ], rows

            # The schema the two migrations describe actually landed.
            with engine.connect() as conn:
                tables = {
                    r[0]
                    for r in conn.execute(
                        text("SELECT name FROM sqlite_master WHERE type='table'")
                    ).fetchall()
                }
                audit_cols = {
                    r[1]
                    for r in conn.execute(
                        text("PRAGMA table_info(secrets_audit)")
                    ).fetchall()
                }
            assert "app_settings" in tables, sorted(tables)
            assert "context" in audit_cols, sorted(audit_cols)
        finally:
            engine.dispose()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)  # ONLY this file's own mktemp dir


# ─────────────────────────────────────────────────────────────────────
# 2. Regression pin: the REAL versions dir has no duplicate prefix
# ─────────────────────────────────────────────────────────────────────


def test_real_versions_dir_has_no_duplicate_version_prefix():
    """The shipped migrations directory carries NO duplicate version.

    A duplicate prefix is a landmine: the ledger's ``version`` PRIMARY
    KEY means the second file of the pair dies mid-apply with an error
    that names neither file. This pin makes the condition a build-time
    failure instead of a boot-time one.
    """
    seen: dict[str, list[str]] = {}
    for path in sorted(REAL_VERSIONS.glob("*.sql")):
        version = _version_of(path)
        if version is None:
            continue
        seen.setdefault(version, []).append(path.name)

    duplicates = {v: names for v, names in seen.items() if len(names) > 1}
    assert not duplicates, (
        "duplicate migration version prefix in "
        f"{REAL_VERSIONS}: "
        + "; ".join(
            f"version {v} is claimed by {len(n)} files: {', '.join(n)}"
            for v, n in sorted(duplicates.items())
        )
    )

    # The guard agrees (it parses the same directory through the real
    # runner) — this is the leg the boot actually takes.
    tmp = Path(tempfile.mkdtemp(prefix="ens-migdup-scan-"))
    try:
        engine = create_engine(f"sqlite:///{tmp}/scan.db")
        try:
            runner = MigrationRunner(engine, migrations_dir=REAL_VERSIONS)
            migrations = runner.discover_migrations()
        finally:
            engine.dispose()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    assert len(migrations) > 100, len(migrations)


# ─────────────────────────────────────────────────────────────────────
# 3. The loud guard: duplicate prefix → named error, nothing applied
# ─────────────────────────────────────────────────────────────────────


def _write_migration(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")


def test_duplicate_prefix_fails_bootstrap_with_loud_named_error(tmp_path):
    """A duplicate version prefix fails the boot LOUDLY and applies nothing.

    The error must name BOTH offending files (the pre-fix symptom named
    neither) and must fire BEFORE the apply loop, so the schema is
    never left half-applied.
    """
    versions_dir = tmp_path / "versions"
    versions_dir.mkdir()

    _write_migration(
        versions_dir / "20261018_000001_alpha_probe.sql",
        "-- UP\nCREATE TABLE alpha_guard_probe (id INTEGER);\n",
    )
    _write_migration(
        versions_dir / "20261018_000001_beta_probe.sql",
        "-- UP\nCREATE TABLE beta_guard_probe (id INTEGER);\n",
    )

    engine = create_engine(f"sqlite:///{tmp_path}/guard.db")
    try:
        runner = MigrationRunner(engine, migrations_dir=versions_dir)
        with pytest.raises(DuplicateMigrationVersionError) as excinfo:
            runner.run_pending_migrations()

        message = str(excinfo.value)
        # BOTH offending file names are in the message.
        assert "20261018_000001_alpha_probe.sql" in message, message
        assert "20261018_000001_beta_probe.sql" in message, message
        assert "20261018_000001" in message, message
        # It is a MigrationError (the module's established base type).
        assert isinstance(excinfo.value, MigrationError)

        # NOTHING was applied — the guard runs ahead of the apply loop.
        with engine.connect() as conn:
            tables = {
                r[0]
                for r in conn.execute(
                    text("SELECT name FROM sqlite_master WHERE type='table'")
                ).fetchall()
            }
            ledger_rows = conn.execute(
                text("SELECT COUNT(*) FROM schema_migrations")
            ).fetchone()[0]
        assert "alpha_guard_probe" not in tables, sorted(tables)
        assert "beta_guard_probe" not in tables, sorted(tables)
        assert ledger_rows == 0, ledger_rows
    finally:
        engine.dispose()


def test_invalid_migration_file_does_not_swallow_duplicate_error(tmp_path):
    """The invalid-file skip must NOT swallow the duplicate error.

    ``MigrationFile.parse`` raises ``ValueError`` for a malformed file,
    which the runner skips with a WARNING. The duplicate-prefix error is
    a different failure class and must survive that handler — otherwise
    the guard would be silently disabled by an unrelated broken file.
    """
    versions_dir = tmp_path / "versions"
    versions_dir.mkdir()

    # A malformed file (no "-- UP" section) → ValueError → WARNING skip.
    _write_migration(
        versions_dir / "20261018_000001_broken_probe.sql",
        "-- this file has no UP section at all\n",
    )
    _write_migration(
        versions_dir / "20261018_000001_alpha_probe.sql",
        "-- UP\nCREATE TABLE alpha_guard_probe (id INTEGER);\n",
    )
    _write_migration(
        versions_dir / "20261018_000001_beta_probe.sql",
        "-- UP\nCREATE TABLE beta_guard_probe (id INTEGER);\n",
    )

    engine = create_engine(f"sqlite:///{tmp_path}/guard2.db")
    try:
        runner = MigrationRunner(engine, migrations_dir=versions_dir)
        with pytest.raises(DuplicateMigrationVersionError) as excinfo:
            runner.run_pending_migrations()
        message = str(excinfo.value)
        assert "20261018_000001_alpha_probe.sql" in message, message
        assert "20261018_000001_beta_probe.sql" in message, message
    finally:
        engine.dispose()


def test_clean_migrations_dir_boots_without_duplicate_error(tmp_path):
    """The PASS@clean leg: a directory with unique prefixes applies fine."""
    versions_dir = tmp_path / "versions"
    versions_dir.mkdir()
    _write_migration(
        versions_dir / "20261018_000001_alpha_probe.sql",
        "-- UP\nCREATE TABLE alpha_guard_probe (id INTEGER);\n",
    )
    _write_migration(
        versions_dir / "20261018_000002_beta_probe.sql",
        "-- UP\nCREATE TABLE beta_guard_probe (id INTEGER);\n",
    )

    engine = create_engine(f"sqlite:///{tmp_path}/clean.db")
    try:
        runner = MigrationRunner(engine, migrations_dir=versions_dir)
        applied = runner.run_pending_migrations()
        assert applied == ["20261018_000001", "20261018_000002"], applied
        with engine.connect() as conn:
            tables = {
                r[0]
                for r in conn.execute(
                    text("SELECT name FROM sqlite_master WHERE type='table'")
                ).fetchall()
            }
        assert {"alpha_guard_probe", "beta_guard_probe"} <= tables, sorted(tables)
    finally:
        engine.dispose()


# ─────────────────────────────────────────────────────────────────────
# 4. PostgreSQL lane: the early-return stays AHEAD of the guard
# ─────────────────────────────────────────────────────────────────────


def test_pg_engine_noop_returns_empty_and_never_touches_the_guard(tmp_path):
    """A PG engine short-circuits BEFORE the duplicate-version guard.

    ``run_pending_migrations`` returns ``[]`` for any non-SQLite engine.
    The guard lives in ``discover_migrations``, which the PG path never
    reaches — so PG boots are untouched by a filesystem-level duplicate
    prefix, and PG schema evolution keeps riding
    ``EnsembleManager._ensure_postgres_columns``.

    NO CONNECTION IS MADE: the URL points at ``127.0.0.1:1``
    (unreachable by construction) and ``create_engine`` is lazy. If the
    guard were ever moved ahead of the PG early-return, this test would
    fail with the loud duplicate error instead of silently connecting.
    """
    versions_dir = tmp_path / "versions"
    versions_dir.mkdir()
    # A real duplicate pair, so a regression WOULD be visible here.
    _write_migration(
        versions_dir / "20261018_000001_alpha_probe.sql",
        "-- UP\nCREATE TABLE alpha_guard_probe (id INTEGER);\n",
    )
    _write_migration(
        versions_dir / "20261018_000001_beta_probe.sql",
        "-- UP\nCREATE TABLE beta_guard_probe (id INTEGER);\n",
    )

    engine = create_engine(
        "postgresql+psycopg://nobody:nowhere@127.0.0.1:1/no_such_db"
    )
    try:
        assert "sqlite" not in str(engine.url)
        runner = MigrationRunner(engine, migrations_dir=versions_dir)
        # No exception, no filesystem guard, empty result.
        assert runner.run_pending_migrations() == []
    finally:
        engine.dispose()
