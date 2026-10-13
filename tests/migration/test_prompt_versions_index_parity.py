"""prompt_versions + prompt_audit_events index parity tests.

Phase A of the agent-prompt-immutability commission
(``daemon/repositories/prompt_version/`` + the migration file at
``daemon/migrations/versions/20261013_214000_create_prompt_versions.sql``).

Compact mirror of ``tests/migration/test_stored_watches_index_parity.py``
(the stored_watches v0.20.2 parity test) and
``tests/migration/test_drift_events_index_parity.py`` (the
v0.18.0 drift_events regression guard), scaled to the
prompt_versions + prompt_audit_events surface: brand-new tables
with NO ``_ensure_postgres_columns`` block (PostgreSQL receives
them via ``SQLModel.metadata.create_all`` — the snapshot /
stored_watches precedent), so the dual-driver parity contract
is TWO-way:

  (a) the ``PromptVersion`` + ``PromptAuditEvent`` models'
      ``__table_args__`` declarations — the canonical source
      for the PG ``create_all`` path, vs
  (b) the SQLite migration's ``CREATE [UNIQUE] INDEX`` statements —
      ``daemon/migrations/versions/20261013_214000_create_prompt_versions.sql``.

Pins:

* every named index declared on one side exists on the other with
  the same ordered column list (the guard that would have caught
  the drift_events v0.18.0 gap);
* the new-row UNIQUE fence parity: the model's ``UniqueConstraint``
  vs the migration's same-named UNIQUE INDEX (the
  ``create_draft`` / ``create_and_submit`` INSERT path's
  retry-on-IntegrityError target);
* a fresh in-memory SQLite ``create_all`` emits exactly the named
  index set — and NOT the default-named ``ix_prompt_versions_*``
  family that dropped ``index=True`` flags would mint;
* the migration's ``CREATE ... INDEX IF NOT EXISTS`` statements are
  idempotent (re-runnable against an already-created database).

Test markers::

    pytest tests/migration/test_prompt_versions_index_parity.py -v
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

# Importing the models is REQUIRED to register ``prompt_versions``
# + ``prompt_audit_events`` on ``SQLModel.metadata`` — without this
# the reflections below see an empty metadata and fail. Same
# convention as the drift_events + stored_watches parity tests
# (import every model module the assertions reflect on).
from daemon.repositories.prompt_version.models import (  # noqa: F401
    PromptAuditEvent,
    PromptVersion,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MIGRATION_SQL = (
    _REPO_ROOT
    / "daemon/migrations/versions/20261013_214000_create_prompt_versions.sql"
)

# Canonical index sets — any change MUST be reflected in BOTH:
#   - daemon/repositories/prompt_version/models.py (__table_args__,
#     the canonical source)
#   - daemon/migrations/versions/20261013_214000_create_prompt_versions.sql
EXPECTED_PROMPT_VERSIONS_INDEXES: dict[str, tuple[str, ...]] = {
    "idx_prompt_versions_key": ("agent_id", "version_tag", "artifact"),
    "idx_prompt_versions_key_state": (
        "agent_id",
        "version_tag",
        "artifact",
        "state",
    ),
    "idx_prompt_versions_state_updated": ("state", "updated_at"),
}

# New-row INSERT UNIQUE fence (the create_draft / create_and_submit
# retry-on-IntegrityError target — A7/C5/G-4e).
EXPECTED_PROMPT_VERSIONS_UQ_NAME = (
    "uq_prompt_versions_agent_tag_artifact_version"
)
EXPECTED_PROMPT_VERSIONS_UQ_COLUMNS = (
    "agent_id",
    "version_tag",
    "artifact",
    "version",
)

EXPECTED_PROMPT_AUDIT_EVENTS_INDEXES: dict[str, tuple[str, ...]] = {
    "idx_prompt_audit_events_key_created": (
        "agent_id",
        "version_tag",
        "artifact",
        "created_at",
    ),
    "idx_prompt_audit_events_version": ("version_id",),
    "idx_prompt_audit_events_type": ("event_type", "created_at"),
}


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _migration_up_section() -> str:
    content = _MIGRATION_SQL.read_text(encoding="utf-8")
    match = re.search(r"--\s*UP\s*\n(.*?)(?=--\s*DOWN|$)", content, re.DOTALL)
    assert match, f"failed to parse UP section in {_MIGRATION_SQL}"
    return match.group(1)


def _migration_indexes_for(table_name: str) -> dict[str, tuple[str, ...]]:
    """name -> ordered columns for the migration's non-unique CREATE INDEX."""
    pattern = re.compile(
        rf"CREATE INDEX IF NOT EXISTS (\w+)\s+ON\s+{table_name}\s*\(([^)]+)\)"
    )
    return {
        m.group(1): tuple(c.strip() for c in m.group(2).split(","))
        for m in pattern.finditer(_migration_up_section())
    }


def _migration_unique_index_for(
    table_name: str,
) -> tuple[str, tuple[str, ...]]:
    pattern = re.compile(
        rf"CREATE UNIQUE INDEX IF NOT EXISTS (\w+)\s+ON"
        rf"\s+{table_name}\s*\(([^)]+)\)"
    )
    match = pattern.search(_migration_up_section())
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


def _model_unique_constraints_for(
    table_name: str,
) -> dict[str, tuple[str, ...]]:
    table = SQLModel.metadata.tables[table_name]
    return {
        c.name: tuple(col.name for col in c.columns)
        for c in table.constraints
        if c.__class__.__name__ == "UniqueConstraint"
    }


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def fresh_sqlite_engine():
    """Fresh in-memory SQLite with the canonical schema via ``create_all``.

    One connection per thread so concurrent-index / constraint
    reflection work can't deadlock under the SQLite single-writer
    model (``StaticPool`` + ``check_same_thread=False``).
    """
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


# ─────────────────────────────────────────────────────────────────────────────
# prompt_versions parity
# ─────────────────────────────────────────────────────────────────────────────


class TestPromptVersionsIndexParity:
    def test_migration_declares_expected_indexes(self):
        assert _migration_indexes_for("prompt_versions") == (
            EXPECTED_PROMPT_VERSIONS_INDEXES
        )

    def test_model_declares_expected_indexes(self):
        assert _model_indexes_for("prompt_versions") == (
            EXPECTED_PROMPT_VERSIONS_INDEXES
        )

    def test_migration_unique_index_matches_fence(self):
        name, columns = _migration_unique_index_for("prompt_versions")
        assert name == EXPECTED_PROMPT_VERSIONS_UQ_NAME
        assert columns == EXPECTED_PROMPT_VERSIONS_UQ_COLUMNS

    def test_model_unique_constraint_matches_fence(self):
        constraints = _model_unique_constraints_for("prompt_versions")
        assert EXPECTED_PROMPT_VERSIONS_UQ_NAME in constraints
        assert constraints[EXPECTED_PROMPT_VERSIONS_UQ_NAME] == (
            EXPECTED_PROMPT_VERSIONS_UQ_COLUMNS
        )

    def test_fresh_create_all_emits_named_indexes_not_ix_family(
        self, fresh_sqlite_engine
    ):
        names = {
            i["name"]
            for i in inspect(fresh_sqlite_engine).get_indexes(
                "prompt_versions"
            )
        }
        # Explicit UNIQUE constraint does NOT surface in
        # get_indexes() on SQLite (it surfaces in
        # get_unique_constraints()); include both in the
        # observed set so the assertion covers the fence.
        unique_names = {
            u["name"]
            for u in inspect(fresh_sqlite_engine).get_unique_constraints(
                "prompt_versions"
            )
        }
        observed = names | unique_names
        assert set(EXPECTED_PROMPT_VERSIONS_INDEXES) <= observed
        assert EXPECTED_PROMPT_VERSIONS_UQ_NAME in observed
        assert not any(
            n.startswith("ix_prompt_versions_") for n in observed
        ), (
            "default-named index=True indexes reappeared — the explicit "
            "__table_args__ declarations are the parity contract"
        )

    def test_migration_index_statements_idempotent(
        self, fresh_sqlite_engine
    ):
        # Filter to prompt_versions statements only.
        statements = [
            s.strip()
            for s in _migration_up_section().split(";")
            if "CREATE" in s.upper()
            and "INDEX" in s.upper()
            and "prompt_versions" in s.lower()
        ]
        assert len(statements) == len(EXPECTED_PROMPT_VERSIONS_INDEXES) + 1
        # +1 = the UNIQUE INDEX
        with fresh_sqlite_engine.begin() as conn:
            for statement in statements:
                conn.execute(text(statement))  # must not raise


# ─────────────────────────────────────────────────────────────────────────────
# prompt_audit_events parity
# ─────────────────────────────────────────────────────────────────────────────


class TestPromptAuditEventsIndexParity:
    def test_migration_declares_expected_indexes(self):
        assert _migration_indexes_for("prompt_audit_events") == (
            EXPECTED_PROMPT_AUDIT_EVENTS_INDEXES
        )

    def test_model_declares_expected_indexes(self):
        assert _model_indexes_for("prompt_audit_events") == (
            EXPECTED_PROMPT_AUDIT_EVENTS_INDEXES
        )

    def test_fresh_create_all_emits_named_indexes_not_ix_family(
        self, fresh_sqlite_engine
    ):
        names = {
            i["name"]
            for i in inspect(fresh_sqlite_engine).get_indexes(
                "prompt_audit_events"
            )
        }
        assert set(EXPECTED_PROMPT_AUDIT_EVENTS_INDEXES) <= names
        assert not any(
            n.startswith("ix_prompt_audit_events_") for n in names
        ), (
            "default-named index=True indexes reappeared — the explicit "
            "__table_args__ declarations are the parity contract"
        )

    def test_migration_index_statements_idempotent(
        self, fresh_sqlite_engine
    ):
        # Filter to prompt_audit_events statements only.
        statements = [
            s.strip()
            for s in _migration_up_section().split(";")
            if "CREATE" in s.upper()
            and "INDEX" in s.upper()
            and "prompt_audit_events" in s.lower()
        ]
        assert len(statements) == len(
            EXPECTED_PROMPT_AUDIT_EVENTS_INDEXES
        )
        with fresh_sqlite_engine.begin() as conn:
            for statement in statements:
                conn.execute(text(statement))  # must not raise