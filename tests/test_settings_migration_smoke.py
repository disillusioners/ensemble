"""db-backed-settings Phase 4 (t6) — migration-runner smoke (adopted O1).

Applies JUST ``20261018_000001_create_app_settings.sql`` to a temp
SQLite DB through the REAL migration runner
(``daemon.migrations.MigrationRunner``), then asserts the
``app_settings`` table exists with the EXACT model column set
(``daemon.models.app_settings.AppSetting``) — making the migration's
acceptance non-vacuous on PG dev hosts (where the .sql runner is a
NO-OP and the schema would otherwise come from create_all).

Isolation: the runner applies a migrations DIRECTORY — exactly ONE
file (the commission's migration, copied verbatim) is placed in a
fresh ``tempfile.mkdtemp`` dir, so the runner's scan applies only it.

Fence discipline: everything under this file's OWN mktemp dir (the db,
the copied .sql, the ledger); nothing outside it is created or
removed.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from sqlalchemy import create_engine, text

import daemon
from daemon.migrations import MigrationRunner
from daemon.models.app_settings import AppSetting

MIGRATION_FILENAME = "20261018_000001_create_app_settings.sql"
MIGRATION_VERSION = "20261018_000001"
SOURCE_SQL = Path(daemon.__file__).parent / "migrations" / "versions" / MIGRATION_FILENAME


def test_app_settings_sql_alone_creates_exact_model_columns():
    tmp = tempfile.mkdtemp(prefix="ens-settings-migration-smoke-")
    try:
        migrations_dir = Path(tmp) / "versions"
        migrations_dir.mkdir()
        shutil.copy(SOURCE_SQL, migrations_dir / MIGRATION_FILENAME)

        engine = create_engine(f"sqlite:///{tmp}/migration_smoke.db")
        try:
            runner = MigrationRunner(engine, migrations_dir=migrations_dir)
            applied = runner.run_pending_migrations()

            # The one file was discovered and applied by the runner.
            assert applied == [MIGRATION_VERSION], applied

            status = runner.get_migration_status()
            assert MIGRATION_VERSION in status["applied"]

            with engine.connect() as conn:
                table_exists = conn.execute(
                    text("SELECT name FROM sqlite_master WHERE type='table' AND name='app_settings'")
                ).fetchone()
                assert table_exists is not None, "app_settings table was not created"

                pragma_rows = conn.execute(text("PRAGMA table_info(app_settings)")).fetchall()

            # Column names + ORDER match the model exactly.
            pragma_names = [row[1] for row in pragma_rows]
            model_names = [col.name for col in AppSetting.__table__.columns]
            assert pragma_names == model_names, (pragma_names, model_names)

            by_name = {row[1]: row for row in pragma_rows}
            for col in AppSetting.__table__.columns:
                row = by_name[col.name]
                if col.primary_key:
                    # PK flag on `key` (the accepted PK-on-the-Column posture).
                    assert row[5] == 1, f"{col.name}: migration pk={row[5]}"
                    # STRICT PARITY (fix 2026-10-18): the .sql previously
                    # declared bare `key TEXT PRIMARY KEY` — SQLite's
                    # non-INTEGER-PK quirk reported notnull=0 for it, weaker
                    # than the model (a NULL-key direct-DB INSERT was
                    # accepted on SQLite only). The .sql now declares the
                    # key column NOT NULL, matching create_all's emission
                    # on both dialects; the artifact pin is replaced by
                    # model parity (nullable=False ⇔ notnull=1).
                    assert col.nullable is False, f"{col.name}: model key must stay NOT NULL"
                    assert row[3] == 1, (
                        f"{col.name}: migration notnull={row[3]}, expected 1 "
                        f"(parity with model nullable={col.nullable})"
                    )
                    continue
                # NULLABILITY: notnull=0 ⇔ nullable.
                assert row[3] == (0 if col.nullable else 1), (
                    f"{col.name}: migration notnull={row[3]} vs model nullable={col.nullable}"
                )
                # Type parity: every column is TEXT (Ruling 1 — no native typed columns).
                assert row[2].upper() == "TEXT", f"{col.name}: migration type {row[2]!r} != TEXT"

            # The model's PK set is exactly {key} — pins the Column-level PK posture.
            pk_columns = [c.name for c in AppSetting.__table__.columns if c.primary_key]
            assert pk_columns == ["key"]
        finally:
            engine.dispose()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)  # ONLY this file's own mktemp dir
