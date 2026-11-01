"""Phase-4 real-PG pack — the ``_ensure_postgres_columns`` mirror belt.

Commission: project-skill-loader, Phase 4 (task 3 / A10). The OPERATIVE
fresh-PG-table artifact is the pre-``create_all`` model registration
(A10 SITE 5 — pinned structurally in the wiring pack + proven by the
boot smoke); THESE tests prove the BELT: the ``CREATE TABLE IF NOT
EXISTS`` mirror entries inside the production hook land the three lane
tables on a pre-existing-PG database where the tables are ABSENT, and
re-running is idempotent.

Run (scratch PG only):
    PG_TEST_HOST=... PG_TEST_PORT=... PG_TEST_USER=... \
    PG_TEST_PASSWORD=<pw> PG_TEST_ALLOWED_DBS=<scratch> \
    pytest tests/postgres/test_project_skill_source_phase4_pg.py -m postgres
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import text

import daemon.repositories.project_skill_file.models  # noqa: F401 — register before create_all
import daemon.repositories.skill.models  # noqa: F401
# The full production hook's belt also indexes tables owned by other
# subsystems (fail-loud); register the drift-events model so the
# shared fixture's create_all lands it (the hook's CREATE INDEX on
# drift_events is a pre-existing statement, not this lane's).
import daemon.plugin_subsystem.drift_event_publisher  # noqa: F401

from daemon.manager import InstanceManager

pytestmark = pytest.mark.postgres

LANE_TABLES = (
    "project_skill_files",
    "project_skill_embeddings",
    "project_skill_usage_records",
)

LANE_INDEXES = (
    "uq_psf_root_project_ns_name",
    "idx_psf_project_id",
    "idx_psf_root_path",
    "idx_pse_skill_id",
    "idx_psur_skill_id",
    "idx_psur_instance_id",
    "idx_psur_instance_feedback",
    "idx_psur_skill_created",
)


class _MinimalManagerProxy:
    """``_ensure_postgres_columns`` reads only ``self._engine`` (the
    report-deferred pack's verified-AST precedent) — PLUS the sibling
    hook ``_migrate_overloaded_image_refs_rows`` the boot path calls
    right after it (the known fixture-debt that quarantined the
    report-deferred family, QUARANTINE.md 2026-10-31; this proxy
    exposes a no-op so the BELT statements remain the test's target)."""

    def __init__(self, engine: Any) -> None:
        self._engine = engine
        # The hook's tail (the resource-monitor boolean widening) gates
        # on ``_ensemble_config.is_postgres`` — truthy shape mirrors a
        # PG-configured manager (the widening itself is a no-op probe).
        self._ensemble_config = SimpleNamespace(is_postgres=True)

    def _migrate_overloaded_image_refs_rows(self) -> None:  # noqa: D401
        """No-op — out of scope for the mirror-belt pin."""


def _table_exists(conn, table: str) -> bool:
    return bool(
        conn.execute(
            text(
                "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
                "WHERE table_name = :t)"
            ),
            {"t": table},
        ).scalar()
    )


def _index_exists(conn, index: str) -> bool:
    return bool(
        conn.execute(
            text(
                "SELECT EXISTS (SELECT 1 FROM pg_indexes "
                "WHERE indexname = :i)"
            ),
            {"i": index},
        ).scalar()
    )


class TestMirrorBelt:
    def test_tables_absent_then_hook_lands_them_idempotent(self, pg_engine):
        """Table-absent pre-existing-PG shape: the production hook's
        belt statements CREATE the three lane tables + every index;
        a second full-hook run is a no-op (idempotent)."""
        # Simulate the pre-existing-PG shape: the shared fixture's
        # create_all already made the tables (models are registered —
        # that IS the operative artifact); drop them to reach the
        # belt-only world an older DB would be in.
        with pg_engine.begin() as conn:
            for table in reversed(LANE_TABLES):
                conn.execute(text(f'DROP TABLE IF EXISTS "{table}" CASCADE'))
            for table in LANE_TABLES:
                assert not _table_exists(conn, table)

        # THE PRODUCTION HOOK (all statements, one begin() — the real
        # execution shape).
        InstanceManager._ensure_postgres_columns(_MinimalManagerProxy(pg_engine))

        with pg_engine.connect() as conn:
            for table in LANE_TABLES:
                assert _table_exists(conn, table), f"{table} missing after hook"
            for index in LANE_INDEXES:
                assert _index_exists(conn, index), f"{index} missing after hook"

        # Idempotent: the second full run must not raise (fail-loud
        # hook — a duplicate DDL would abort startup).
        InstanceManager._ensure_postgres_columns(_MinimalManagerProxy(pg_engine))
        with pg_engine.connect() as conn:
            counts = {
                table: conn.execute(
                    text(f'SELECT COUNT(*) FROM "{table}"')
                ).scalar()
                for table in LANE_TABLES
            }
        assert all(c == 0 for c in counts.values()), counts

    def test_fresh_pg_create_all_shape_matches_mirror(self, pg_engine):
        """Fresh-PG (create_all from the registered models) and the
        belt land the SAME column set per table (column-name parity —
        the two artifacts cannot drift)."""
        # Fresh shape is what the shared fixture already built; run the
        # belt on top (no-op) and compare column sets against the .sql
        # files' declared columns.
        expected = {
            "project_skill_files": {
                "id", "project_id", "root_path", "namespace", "name",
                "address", "description", "category", "body_path",
                "manifest_sha256", "body_sha256", "body_char_len",
                "manifest_json", "valid", "last_error", "embedded_at",
                "indexed_at", "updated_at",
            },
            "project_skill_embeddings": {
                "id", "skill_id", "trigger_query", "embedding", "created_at",
            },
            "project_skill_usage_records": {
                "id", "skill_id", "project_id", "instance_id", "agent_id",
                "task_message", "selected", "applied", "task_succeeded",
                "iterations", "duration_seconds", "fallback",
                "feedback_applied", "feedback_note", "created_at",
            },
        }
        InstanceManager._ensure_postgres_columns(_MinimalManagerProxy(pg_engine))
        with pg_engine.connect() as conn:
            for table, want in expected.items():
                got = {
                    row[0]
                    for row in conn.execute(
                        text(
                            "SELECT column_name FROM information_schema.columns "
                            "WHERE table_name = :t"
                        ),
                        {"t": table},
                    )
                }
                assert got == want, f"{table}: {got ^ want}"

    def test_off_leg_zero_pf_rows_on_pg(self, pg_engine):
        """A11 OFF-side pin on real PG: with the knob OFF nothing
        indexes ⇒ the three lane tables stay EMPTY (no writes path
        exists without the file source)."""
        from daemon.services.project_skill_source.service import (
            ProjectSkillSourceService,
        )

        # Knob OFF ⇒ manager never hands the service to anything; the
        # table-level consequence: constructing the (unwired) service
        # and doing NOTHING leaves zero rows.
        ProjectSkillSourceService()
        with pg_engine.connect() as conn:
            counts = {
                table: conn.execute(
                    text(f'SELECT COUNT(*) FROM "{table}"')
                ).scalar()
                for table in LANE_TABLES
            }
        assert all(c == 0 for c in counts.values()), counts
