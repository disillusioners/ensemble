"""prompt_versions (Ud#3 class column) + prompt_template_baselines
(Ud#5B table) migration parity tests.

User Directive #3 (2026-10-14, RESOLUTION-CORE) added the
``artifact_class`` column to ``prompt_versions`` via
``daemon/migrations/versions/20261014_013702_alter_prompt_versions_add_artifact_class.sql``.

User Directive #5B (2026-10-14, RESOLUTION-CORE) added a NEW
``prompt_template_baselines`` table via
``daemon/migrations/versions/20261014_013815_create_prompt_template_baselines.sql``.

This file mirrors the parity contract used by the Phase A canonical
file (``tests/migration/test_prompt_versions_index_parity.py``):

1. The SQLite migration DDL declares each expected index by name +
   ordered columns (canonical source on the SQLite path).
2. The SQLModel ``__table_args__`` declaration declares the same
   set (canonical source for the PG ``create_all`` path).
3. The two sources MATCH byte-for-byte.
4. ``SQLModel.metadata.create_all`` on a fresh DB emits the named
   indexes (and not the ``ix_<table>_*`` default family that
   drop-down ``index=True`` flags would mint).
5. The migration's ``CREATE ... INDEX IF NOT EXISTS`` statements
   are idempotent (re-runnable on an already-created DB).

Run only with::

    pytest tests/migration/test_resolution_core_migrations.py -v

Fencing: ``ENSEMBLE_SELF_ENV=dev`` (no live posture); ``POSTGRES_DB
≠ ensemble_prod``.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

# Importing the models is REQUIRED to register ``prompt_versions``
# + ``prompt_audit_events`` + ``prompt_template_baselines`` on
# ``SQLModel.metadata`` — without this the reflections below see an
# empty metadata and fail.
from daemon.repositories.prompt_version.models import (  # noqa: F401
    PromptAuditEvent,
    PromptTemplateBaseline,
    PromptVersion,
)


_REPO_ROOT = Path(__file__).resolve().parents[2]
_MIGRATIONS_DIR = _REPO_ROOT / "daemon/migrations/versions"

# Migration for ``artifact_class`` ALTER on prompt_versions.
_MIGRATION_CLASS_COLUMN = (
    _MIGRATIONS_DIR
    / "20261014_013702_alter_prompt_versions_add_artifact_class.sql"
)
# Migration for ``prompt_template_baselines`` (new table).
_MIGRATION_BASELINES = (
    _MIGRATIONS_DIR
    / "20261014_013815_create_prompt_template_baselines.sql"
)


# ─────────────────────────────────────────────────────────────────
# Baseline-table index parity
# ─────────────────────────────────────────────────────────────────

EXPECTED_PROMPT_TEMPLATE_BASELINES_INDEXES: dict[str, tuple[str, ...]] = {
    "idx_prompt_template_baselines_artifact": ("artifact",),
    "idx_prompt_template_baselines_recorded_at": ("recorded_at",),
}

EXPECTED_PROMPT_TEMPLATE_BASELINES_UQ_NAME = (
    "uq_prompt_template_baselines_agent_tag_artifact"
)
EXPECTED_PROMPT_TEMPLATE_BASELINES_UQ_COLUMNS = (
    "agent_id",
    "version_tag",
    "artifact",
)


def _migration_up_section(path: Path) -> str:
    content = path.read_text(encoding="utf-8")
    match = re.search(r"--\s*UP\s*\n(.*?)(?=--\s*DOWN|$)", content, re.DOTALL)
    assert match, f"failed to parse UP section in {path}"
    return match.group(1)


def _migration_indexes_for(path: Path, table_name: str) -> dict[str, tuple[str, ...]]:
    pattern = re.compile(
        rf"CREATE INDEX IF NOT EXISTS (\w+)\s+ON\s+{table_name}\s*\(([^)]+)\)"
    )
    return {
        m.group(1): tuple(c.strip() for c in m.group(2).split(","))
        for m in pattern.finditer(_migration_up_section(path))
    }


def _migration_unique_index_for(
    path: Path, table_name: str,
) -> tuple[str, tuple[str, ...]]:
    pattern = re.compile(
        rf"CREATE UNIQUE INDEX IF NOT EXISTS (\w+)\s+ON"
        rf"\s+{table_name}\s*\(([^)]+)\)"
    )
    match = pattern.search(_migration_up_section(path))
    if match is None:
        return "", ()
    return match.group(1), tuple(
        c.strip() for c in match.group(2).split(",")
    )


def _model_indexes_for(table_name: str) -> dict[str, tuple[str, ...]]:
    table = SQLModel.metadata.tables[table_name]
    return {
        i.name: tuple(c.name for c in i.columns) for i in table.indexes
    }


def _model_unique_constraints_for(table_name: str) -> dict[str, tuple[str, ...]]:
    table = SQLModel.metadata.tables[table_name]
    return {
        c.name: tuple(col.name for col in c.columns)
        for c in table.constraints
        if c.__class__.__name__ == "UniqueConstraint"
    }


@pytest.fixture(scope="module")
def fresh_sqlite_engine():
    """Fresh in-memory SQLite with the canonical schema via ``create_all``."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


# ─────────────────────────────────────────────────────────────────
# Class-column ALTER parity
# ─────────────────────────────────────────────────────────────────


class TestArtifactClassColumnParity:
    """The ``artifact_class`` column lands on the schema with the
    right shape on both drivers."""

    def test_migration_file_present(self):
        assert _MIGRATION_CLASS_COLUMN.exists(), (
            f"missing class-column migration: {_MIGRATION_CLASS_COLUMN}"
        )

    def test_migration_adds_artifact_class_column_default_core(self):
        up = _migration_up_section(_MIGRATION_CLASS_COLUMN)
        assert "ALTER TABLE prompt_versions ADD COLUMN artifact_class" in up
        assert "DEFAULT 'CORE'" in up
        assert "NOT NULL" in up

    def test_migration_backfill_classifies_memory_artifacts(self):
        up = _migration_up_section(_MIGRATION_CLASS_COLUMN)
        # The case-when backfill branches.
        assert "'MEMORY'" in up
        assert "memories/" in up

    def test_class_column_visible_on_fresh_create_all(
        self, fresh_sqlite_engine,
    ):
        # Fresh create_all (the PG equivalent path) declares
        # the new column via the model.
        cols = {
            c["name"]
            for c in inspect(fresh_sqlite_engine).get_columns("prompt_versions")
        }
        assert "artifact_class" in cols


# ─────────────────────────────────────────────────────────────────
# prompt_template_baselines table parity
# ─────────────────────────────────────────────────────────────────


class TestPromptTemplateBaselinesParity:
    """The new ``prompt_template_baselines`` table — model + migration match."""

    def test_migration_file_present(self):
        assert _MIGRATION_BASELINES.exists(), (
            f"missing baseline migration: {_MIGRATION_BASELINES}"
        )

    def test_migration_declares_expected_table_name(self):
        up = _migration_up_section(_MIGRATION_BASELINES)
        assert "CREATE TABLE IF NOT EXISTS prompt_template_baselines" in up

    def test_migration_declares_expected_columns(self):
        up = _migration_up_section(_MIGRATION_BASELINES)
        for col in [
            "baseline_id",
            "agent_id",
            "version_tag",
            "artifact",
            "template_sha256",
            "recorded_at",
        ]:
            assert col in up, (
                f"column {col!r} missing from baseline migration DDL"
            )

    def test_migration_indexes_match_model(self):
        assert (
            _migration_indexes_for(
                _MIGRATION_BASELINES, "prompt_template_baselines",
            )
            == EXPECTED_PROMPT_TEMPLATE_BASELINES_INDEXES
        )
        assert (
            _model_indexes_for("prompt_template_baselines")
            == EXPECTED_PROMPT_TEMPLATE_BASELINES_INDEXES
        )

    def test_migration_unique_constraint_matches_model(self):
        name, columns = _migration_unique_index_for(
            _MIGRATION_BASELINES, "prompt_template_baselines",
        )
        assert name == EXPECTED_PROMPT_TEMPLATE_BASELINES_UQ_NAME
        assert columns == EXPECTED_PROMPT_TEMPLATE_BASELINES_UQ_COLUMNS

        constraints = _model_unique_constraints_for("prompt_template_baselines")
        assert EXPECTED_PROMPT_TEMPLATE_BASELINES_UQ_NAME in constraints
        assert (
            constraints[EXPECTED_PROMPT_TEMPLATE_BASELINES_UQ_NAME]
            == EXPECTED_PROMPT_TEMPLATE_BASELINES_UQ_COLUMNS
        )

    def test_fresh_create_all_emits_named_indexes_not_ix_family(
        self, fresh_sqlite_engine,
    ):
        names = {
            i["name"]
            for i in inspect(fresh_sqlite_engine).get_indexes(
                "prompt_template_baselines"
            )
        }
        unique_names = {
            u["name"]
            for u in inspect(fresh_sqlite_engine).get_unique_constraints(
                "prompt_template_baselines"
            )
        }
        observed = names | unique_names
        assert set(EXPECTED_PROMPT_TEMPLATE_BASELINES_INDEXES) <= observed
        assert EXPECTED_PROMPT_TEMPLATE_BASELINES_UQ_NAME in observed
        assert not any(
            n.startswith("ix_prompt_template_baselines_") for n in observed
        ), (
            "default-named index=True indexes reappeared — the explicit "
            "__table_args__ declarations are the parity contract"
        )

    def test_migration_index_statements_idempotent(
        self, fresh_sqlite_engine,
    ):
        statements = [
            s.strip()
            for s in _migration_up_section(_MIGRATION_BASELINES).split(";")
            if "CREATE" in s.upper()
            and "INDEX" in s.upper()
            and "prompt_template_baselines" in s.lower()
        ]
        assert len(statements) == (
            len(EXPECTED_PROMPT_TEMPLATE_BASELINES_INDEXES) + 1
        )
        # Re-apply each statement on an already-built DB; all must
        # be no-ops (the ``IF NOT EXISTS`` clauses).
        with fresh_sqlite_engine.begin() as conn:
            for statement in statements:
                conn.execute(text(statement))  # must not raise
