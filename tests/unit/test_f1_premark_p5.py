"""F1 pre-mark helper test — oauth2-auth Phase 5 (T5.5 / T5.7 / T5.10, F1 trap).

Verifies the F1 pre-mark workaround that lets fresh-SQLite boots
proceed past the broken PG-only ``ALTER TABLE ... DROP CONSTRAINT
IF EXISTS`` migration (20260714_000001).

The pre-mark lands in the ``schema_migrations`` ledger with a
documented skip_reason; the runner subsequently treats the
migration as already-applied and skips it. The PG-only SQL itself
is NEVER executed on SQLite.

Reference: ``/home/nea/ensemble-src/.agents/tester/RESULTS/2026-10-11-oauth2-p4-verification.md``
finding F1.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from tests.fakes.f1_premark_helper import F1_SKIP_REASON, F1_VERSION, premark_f1


class TestF1Premark:
    def test_premark_creates_ledger_row(self):
        with tempfile.NamedTemporaryFile(suffix=".db") as f:
            db_path = f.name
        premark_f1(db_path)
        engine = create_engine(f"sqlite:///{db_path}")
        with engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT version, skip_reason FROM schema_migrations "
                    "WHERE version=:v"
                ),
                {"v": F1_VERSION},
            ).first()
        assert row is not None
        assert row[0] == F1_VERSION
        assert "PG-only" in row[1]
        assert "DROP CONSTRAINT" in row[1]

    def test_premark_is_idempotent(self):
        with tempfile.NamedTemporaryFile(suffix=".db") as f:
            db_path = f.name
        premark_f1(db_path)
        premark_f1(db_path)
        engine = create_engine(f"sqlite:///{db_path}")
        with engine.connect() as conn:
            count = conn.execute(
                text("SELECT COUNT(*) FROM schema_migrations WHERE version=:v"),
                {"v": F1_VERSION},
            ).scalar()
        assert count == 1

    def test_premark_makes_runner_treat_migration_as_applied(self):
        """After the pre-mark, the runner's ``get_pending_migrations``
        excludes F1 — the migration is treated as already-applied."""
        import shutil

        with tempfile.TemporaryDirectory() as tmpdir:
            versions = Path(tmpdir) / "versions"
            versions.mkdir()
            # Copy ONLY F1 into the versions dir.
            src = Path("daemon/migrations/versions")
            shutil.copy(
                src / "20260714_000001_widen_job_queue_type_constraint.sql",
                versions / "20260714_000001_widen_job_queue_type_constraint.sql",
            )
            db_path = Path(tmpdir) / "fresh.db"
            premark_f1(str(db_path))

            from daemon.migrations.runner import MigrationRunner

            engine = create_engine(f"sqlite:///{db_path}")
            runner = MigrationRunner(engine, migrations_dir=versions)
            pending = runner.get_pending_migrations()
            assert pending == []

    def test_skip_reason_documents_the_pre_existing_defect(self):
        """The skip_reason string cites the pre-existing defect and
        the separate fix commission — it is not a generic "skipped"."""
        assert "PG-only" in F1_SKIP_REASON
        assert "DROP CONSTRAINT" in F1_SKIP_REASON
        assert "separate" in F1_SKIP_REASON.lower() or "fix commission" in F1_SKIP_REASON.lower()
        assert "2026-07-14" in F1_SKIP_REASON or "20260714" in F1_SKIP_REASON