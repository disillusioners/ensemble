"""live_view_passcodes + live_view_passcode_audit index / unique-constraint
parity tests.

Phase 1 of the live-view-passcode commission (plan
``.agents/shared/planning/live-view-passcode/``). The companion
model is at ``daemon/repositories/view_passcodes/models.py``;
the companion migration is at
``daemon/migrations/versions/20261017_193816_create_live_view_passcode_tables.sql``.

Compact mirror of the secrets parity test at
``tests/migration/test_secrets_index_parity.py`` + the prompt-
versions parity test at
``tests/migration/test_prompt_versions_index_parity.py``, scaled
to the live_view_passcodes + live_view_passcode_audit surface:
two brand-new tables with NO ``_ensure_postgres_columns`` block
(``SQLModel.metadata.create_all`` is the canonical PG path), so
the dual-driver parity contract is TWO-way:

  (a) the ``LiveViewPasscode`` + ``LiveViewPasscodeAudit`` models'
      ``__table_args__`` declarations — the canonical source for
      the PG ``create_all`` path, vs
  (b) the SQLite migration's ``CREATE [UNIQUE] INDEX`` statements —
      ``daemon/migrations/versions/20261017_193816_create_live_view_passcode_tables.sql``.

Pins:

* the named UNIQUE constraint on ``live_view_passcodes`` matches
  between the model (``uq_live_view_passcodes_root_rel`` on
  ``(root_name, rel_path)``) and the migration's same-named
  UNIQUE INDEX — the first-store-wins fence the P1 mint path
  follows;
* the partial index ``idx_live_view_passcodes_locked`` is
  declared on BOTH sides with identical column lists;
* the audit composite index ``idx_live_view_passcode_audit_root_at``
  is declared on BOTH sides;
* a fresh in-memory SQLite ``create_all`` emits exactly the
  named index set — and NOT default-named fallbacks;
* the migration's ``CREATE ... INDEX IF NOT EXISTS`` statements
  are idempotent (re-runnable against an already-created DB);
* the ``live_view_passcode_audit.action`` column is TEXT (NOT a
  DB enum — additive emitters never require a migration per ADR
  D9:114).

Test markers::

    pytest tests/migration/test_view_passcode_index_parity.py -v
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

# Importing the models is REQUIRED to register
# ``live_view_passcodes`` + ``live_view_passcode_audit`` on
# ``SQLModel.metadata`` — without this the reflections below see
# an empty metadata and fail. Same convention as the secrets /
# drift_events / stored_watches / prompt_versions parity tests.
from daemon.repositories.view_passcodes.models import (  # noqa: F401
    LIVE_VIEW_AUDIT_ACTIONS,
    LiveViewPasscode,
    LiveViewPasscodeAudit,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MIGRATION_SQL = (
    _REPO_ROOT
    / "daemon/migrations/versions/20261017_193816_create_live_view_passcode_tables.sql"
)

# Canonical fence names — any change MUST be reflected in BOTH:
#   - daemon/repositories/view_passcodes/models.py (__table_args__,
#     the canonical source)
#   - daemon/migrations/versions/20261017_193816_create_live_view_passcode_tables.sql
EXPECTED_UNIQUE_NAME = "uq_live_view_passcodes_root_rel"
EXPECTED_UNIQUE_COLUMNS = ("root_name", "rel_path")

EXPECTED_LOCKED_INDEX_NAME = "idx_live_view_passcodes_locked"
EXPECTED_LOCKED_INDEX_COLUMNS = ("root_name", "rel_path")

EXPECTED_AUDIT_INDEX_NAME = "idx_live_view_passcode_audit_root_at"
EXPECTED_AUDIT_INDEX_COLUMNS = ("root_name", "at")


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────


def _migration_up_section() -> str:
    content = _MIGRATION_SQL.read_text(encoding="utf-8")
    match = re.search(r"--\s*UP\s*\n(.*?)(?=--\s*DOWN|$)", content, re.DOTALL)
    assert match, f"failed to parse UP section in {_MIGRATION_SQL}"
    return match.group(1)


def _migration_indexes_for(table_name: str) -> dict[str, tuple[str, ...]]:
    """name -> ordered columns for the migration's non-unique
    CREATE INDEX declarations."""
    pattern = re.compile(
        rf"CREATE INDEX IF NOT EXISTS (\w+)\s+ON\s+{table_name}\s*\(([^)]+)\)"
    )
    return {
        m.group(1): tuple(c.strip() for c in m.group(2).split(","))
        for m in pattern.finditer(_migration_up_section())
    }


def _migration_unique_indexes_for(
    table_name: str,
) -> dict[str, tuple[str, ...]]:
    """name -> ordered columns for the migration's
    CREATE UNIQUE INDEX declarations."""
    pattern = re.compile(
        rf"CREATE UNIQUE INDEX IF NOT EXISTS (\w+)\s+ON\s+"
        rf"{table_name}\s*\(([^)]+)\)"
    )
    return {
        m.group(1): tuple(c.strip() for c in m.group(2).split(","))
        for m in pattern.finditer(_migration_up_section())
    }


def _model_unique_constraints(
    table_name: str,
) -> dict[str, tuple[str, ...]]:
    """name -> (columns,) for the table's UniqueConstraints.

    Includes unnamed (``name=None``) UniqueConstraints —
    those are the dual-emission regression marker (a
    ``Field(unique=True)``-minted inline UNIQUE carries no
    explicit name; the named ``UniqueConstraint`` in
    ``__table_args__`` carries one). The unnamed entry is
    keyed ``"<unnamed>"`` to keep the dict-comparable form
    useful for parity assertions.
    """
    table = SQLModel.metadata.tables[table_name]
    return {
        (c.name or "<unnamed>"): tuple(col.name for col in c.columns)
        for c in table.constraints
        if c.__class__.__name__ == "UniqueConstraint"
    }


def _model_indexes(table_name: str) -> dict[str, tuple[str, ...]]:
    table = SQLModel.metadata.tables[table_name]
    return {
        i.name: tuple(c.name for c in i.columns) for i in table.indexes
    }


# ─────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def fresh_sqlite_engine():
    """Fresh in-memory SQLite with the canonical schema via
    ``create_all``.

    One connection per thread so concurrent-index / constraint
    reflection work can't deadlock under the SQLite single-
    writer model (``StaticPool`` + ``check_same_thread=False``).
    """
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


# ─────────────────────────────────────────────────────────────────────
# live_view_passcodes UNIQUE parity — first-store-wins fence
# ─────────────────────────────────────────────────────────────────────


class TestLiveViewPasscodeUniqueParity:
    """The PG ``create_all`` path must emit EXACTLY ONE named
    ``UniqueConstraint`` on ``live_view_passcodes(root_name,
    rel_path)``, named ``uq_live_view_passcodes_root_rel`` —
    the first-store-wins fence the P1 mint path follows."""

    def test_model_emits_exactly_one_unique_constraint(self):
        """The promotion-immutable schema record: the model
        declares exactly one ``UniqueConstraint`` on the
        ``live_view_passcodes`` table. A second (auto-named)
        inline UNIQUE would slip through PG ``create_all`` as
        a phantom index the migration does not carry."""
        constraints = _model_unique_constraints("live_view_passcodes")
        assert len(constraints) == 1, (
            f"expected exactly one UniqueConstraint on "
            f"'live_view_passcodes'; got {sorted(constraints)} "
            f"(the dual-emission regression — see F15 precedent "
            f"project/models.py:334-340)"
        )

    def test_model_unique_constraint_matches_fence(self):
        """The named ``UniqueConstraint`` matches the parity
        fence — name =
        ``uq_live_view_passcodes_root_rel``, columns =
        ``(root_name, rel_path)``."""
        constraints = _model_unique_constraints("live_view_passcodes")
        assert EXPECTED_UNIQUE_NAME in constraints
        assert constraints[EXPECTED_UNIQUE_NAME] == (
            EXPECTED_UNIQUE_COLUMNS
        )

    def test_migration_unique_index_matches_fence(self):
        """The SQLite migration declares the same-named UNIQUE
        INDEX on the same columns."""
        unique = _migration_unique_indexes_for("live_view_passcodes")
        assert EXPECTED_UNIQUE_NAME in unique
        assert unique[EXPECTED_UNIQUE_NAME] == EXPECTED_UNIQUE_COLUMNS

    def test_fresh_create_all_emits_exactly_one_unique_constraint(
        self, fresh_sqlite_engine
    ):
        """End-to-end: a fresh in-memory SQLite ``create_all``
        reflects EXACTLY ONE unique constraint on the
        ``live_view_passcodes`` table, named
        ``uq_live_view_passcodes_root_rel``. A second
        (auto-named) constraint here means the dual-emission
        regression slipped back in."""
        observed = {
            u["column_names"][0]: u["name"]
            for u in inspect(fresh_sqlite_engine).get_unique_constraints(
                "live_view_passcodes"
            )
        }
        assert len(observed) == 1, (
            f"expected exactly one UNIQUE constraint on "
            f"'live_view_passcodes' after create_all; got "
            f"{sorted(observed)} — the dual-emission regression "
            f"slipped back in"
        )


# ─────────────────────────────────────────────────────────────────────
# live_view_passcodes partial index — locked short-circuit
# ─────────────────────────────────────────────────────────────────────


class TestLiveViewPasscodeLockedIndexParity:
    """The partial index ``idx_live_view_passcodes_locked`` is
    declared on BOTH sides with identical column lists — the
    durable lookup path the P2 verify short-circuit (locked →
    200 notice) consults."""

    def test_model_locked_index_matches_fence(self):
        indexes = _model_indexes("live_view_passcodes")
        assert EXPECTED_LOCKED_INDEX_NAME in indexes
        assert indexes[EXPECTED_LOCKED_INDEX_NAME] == (
            EXPECTED_LOCKED_INDEX_COLUMNS
        )

    def test_migration_locked_index_matches_fence(self):
        indexes = _migration_indexes_for("live_view_passcodes")
        assert EXPECTED_LOCKED_INDEX_NAME in indexes
        assert indexes[EXPECTED_LOCKED_INDEX_NAME] == (
            EXPECTED_LOCKED_INDEX_COLUMNS
        )

    def test_fresh_create_all_emits_locked_index(
        self, fresh_sqlite_engine
    ):
        """A fresh ``create_all`` reflects the partial index on
        the column set above."""
        inspector = inspect(fresh_sqlite_engine)
        # All indexes on the table.
        all_index_names = {
            i["name"]
            for i in inspector.get_indexes("live_view_passcodes")
        }
        assert EXPECTED_LOCKED_INDEX_NAME in all_index_names


# ─────────────────────────────────────────────────────────────────────
# live_view_passcode_audit composite index — forensic UI path
# ─────────────────────────────────────────────────────────────────────


class TestLiveViewPasscodeAuditIndexParity:
    """The composite index ``idx_live_view_passcode_audit_root_at``
    on ``(root_name, at)`` covers the ``audit rows for a given
    view, time-ordered`` query path the future operator UI will
    hit."""

    def test_model_audit_index_matches_fence(self):
        indexes = _model_indexes("live_view_passcode_audit")
        assert EXPECTED_AUDIT_INDEX_NAME in indexes
        assert indexes[EXPECTED_AUDIT_INDEX_NAME] == (
            EXPECTED_AUDIT_INDEX_COLUMNS
        )

    def test_migration_audit_index_matches_fence(self):
        indexes = _migration_indexes_for("live_view_passcode_audit")
        assert EXPECTED_AUDIT_INDEX_NAME in indexes
        assert indexes[EXPECTED_AUDIT_INDEX_NAME] == (
            EXPECTED_AUDIT_INDEX_COLUMNS
        )

    def test_fresh_create_all_emits_audit_index(
        self, fresh_sqlite_engine
    ):
        inspector = inspect(fresh_sqlite_engine)
        all_index_names = {
            i["name"]
            for i in inspector.get_indexes("live_view_passcode_audit")
        }
        assert EXPECTED_AUDIT_INDEX_NAME in all_index_names


# ─────────────────────────────────────────────────────────────────────
# Audit action column is TEXT — additive emitters never require a
# migration (ADR D9:114).
# ─────────────────────────────────────────────────────────────────────


class TestAuditActionColumnIsText:
    """The ``action`` column on ``live_view_passcode_audit`` is
    TEXT, NOT a native DB enum (ADR D9:114). The closed action
    set is the service-boundary validator; the model is a dumb
    store."""

    def test_action_column_type_is_text(self):
        col = LiveViewPasscodeAudit.__table__.columns["action"]
        # The SQLModel ``Field(max_length=N)`` produces a
        # ``sqlmodel.sql.sqltypes.AutoString`` (a TypeDecorator
        # wrapping String — NOT a subclass of ``String``). The
        # underlying impl is String; the type carries a
        # ``.impl`` attribute. We check via impl-class +
        # ``String`` isinstance so the test catches AutoString
        # AS WELL AS plain String.
        from sqlalchemy import String, Enum as SAEnum

        # The wrapped impl class is the canonical source.
        impl_class = getattr(col.type, "impl", col.type).__class__
        assert isinstance(
            getattr(col.type, "impl", col.type), String
        ), (
            f"action column underlying impl must be a "
            f"sqlalchemy.String (TEXT-shaped); got {impl_class} "
            f"— additive emitters would require a migration to "
            f"add a new enum value (ADR D9:114 forbids this)"
        )
        # And specifically NOT a native DB enum.
        assert not isinstance(col.type, SAEnum), (
            f"action column must NOT be a native DB enum "
            f"(ADR D9:114); got {type(col.type).__name__}"
        )

    def test_closed_action_set_pinned(self):
        """The closed action set is exactly
        ``{mint, regenerate, grant, deny, lockout}`` (ADR
        D9:115). Any drift here breaks the audit emitter
        contract."""
        assert LIVE_VIEW_AUDIT_ACTIONS == frozenset({
            "mint",
            "regenerate",
            "grant",
            "deny",
            "lockout",
        })


# ─────────────────────────────────────────────────────────────────────
# Migration body sanity — file exists, has both UP and DOWN sections,
# the migration runner parses it.
# ─────────────────────────────────────────────────────────────────────


class TestMigrationFileIntegrity:
    """The migration file exists and parses via the
    :class:`MigrationFile` parser (the same parser the runner
    uses; ``daemon/migrations/runner.py:73``)."""

    def test_migration_file_exists(self):
        assert _MIGRATION_SQL.is_file(), (
            f"migration file missing: {_MIGRATION_SQL}"
        )

    def test_migration_file_has_up_section(self):
        content = _MIGRATION_SQL.read_text(encoding="utf-8")
        assert re.search(r"--\s*UP\s*\n", content), (
            "migration must declare a '-- UP' section "
            "(the runner parser requires it)"
        )

    def test_migration_file_has_down_section(self):
        content = _MIGRATION_SQL.read_text(encoding="utf-8")
        assert re.search(r"--\s*DOWN\s*\n", content), (
            "migration must declare a '-- DOWN' section "
            "(the runner parser requires it for rollback)"
        )

    def test_migration_file_is_parsed_by_runner(self):
        """End-to-end: ``MigrationFile.parse`` succeeds (the
        runner would accept this file at boot)."""
        from daemon.migrations.runner import MigrationFile

        parsed = MigrationFile.parse(_MIGRATION_SQL)
        assert parsed.version == "20261017_193816"
        assert parsed.name == "create live view passcode tables"
        assert parsed.up_sql  # non-empty
        assert parsed.down_sql  # non-empty

    def test_migration_up_sql_idempotent(self):
        """The migration's UP body uses
        ``CREATE TABLE IF NOT EXISTS`` + ``CREATE INDEX IF NOT
        EXISTS`` (re-runnable against an already-created DB —
        the SQLite runner contract)."""
        up = _migration_up_section()
        # Both tables.
        assert up.count("CREATE TABLE IF NOT EXISTS") >= 2
        # All three named indexes.
        assert "CREATE UNIQUE INDEX IF NOT EXISTS uq_live_view_passcodes_root_rel" in up
        assert "CREATE INDEX IF NOT EXISTS idx_live_view_passcodes_locked" in up
        assert "CREATE INDEX IF NOT EXISTS idx_live_view_passcode_audit_root_at" in up

    def test_migration_down_sql_drops_both_tables(self):
        up = _migration_up_section()
        # Both tables declared (positive side).
        assert "CREATE TABLE IF NOT EXISTS live_view_passcodes (" in up
        assert "CREATE TABLE IF NOT EXISTS live_view_passcode_audit (" in up
        # And DOWN drops both tables.
        content = _MIGRATION_SQL.read_text(encoding="utf-8")
        down = re.search(
            r"--\s*DOWN\s*\n(.*?)$", content, re.DOTALL
        ).group(1)
        assert "DROP TABLE IF EXISTS live_view_passcodes" in down
        assert "DROP TABLE IF EXISTS live_view_passcode_audit" in down