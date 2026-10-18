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
import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.engine import Engine
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


# ─────────────────────────────────────────────────────────────────────
# Additional fixtures for the new tests
# ─────────────────────────────────────────────────────────────────────


def _migration_ddl_columns(table_name: str) -> set[str]:
    """Parse the column names from the migration's
    ``CREATE TABLE IF NOT EXISTS <table_name>`` declaration.

    Strips type / NOT NULL / DEFAULT clauses and returns
    just the column-name set. Comparison is against the
    SQLModel ``__table__.columns`` set (the canonical
    source for PG ``create_all``); this is the T1.2
    column-set parity fence (a new column added to the
    model without a matching migration, or vice versa,
    trips here).
    """
    up = _migration_up_section()
    # Find the CREATE TABLE block for our table.
    pattern = re.compile(
        rf"CREATE TABLE IF NOT EXISTS {table_name}\s*\((.*?)\);",
        re.DOTALL,
    )
    m = pattern.search(up)
    assert m is not None, (
        f"failed to find CREATE TABLE for {table_name} in "
        f"{_MIGRATION_SQL}"
    )
    body = m.group(1)
    # Each line: "  col_name TYPE ..." (with optional NOT NULL
    # / DEFAULT / PRIMARY KEY). Tokenize on whitespace; the
    # first token is the column name.
    cols: set[str] = set()
    for raw in body.splitlines():
        line = raw.strip()
        if not line or line.startswith("--"):
            continue
        first = line.split()[0]
        # Strip the trailing comma if present.
        if first.endswith(","):
            first = first[:-1]
        cols.add(first)
    return cols


def _model_columns(table_name: str) -> set[str]:
    """Return the SQLModel column-name set for ``table_name``."""
    return set(SQLModel.metadata.tables[table_name].columns.keys())


@pytest.fixture
def isolated_live_view_passcode_versions_dir() -> Iterator[Path]:
    """Temp ``versions/`` dir holding ONLY the live-view-passcode
    migration + a no-op baseline.

    The ``MigrationRunner`` walks the directory it's pointed at
    and tries to apply EVERY ``*.sql`` file. The project's main
    ``daemon/migrations/versions/`` directory carries a KNOWN
    pre-existing breaker
    (``20260714_000001_widen_job_queue_type_constraint.sql``)
    that uses PG-only ``ALTER TABLE ... DROP CONSTRAINT`` —
    a fresh-SQLite boot dies on lifespan if it tries to apply
    that file. Isolating JUST the live-view-passcode migration
    keeps the test pointed at the migration under test
    (secrets-test precedent: ``test_auth_tables_g2_2.py`` +
    ``test_turn_handle_schema.py``).
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        versions_dir = Path(tmpdir) / "versions"
        versions_dir.mkdir()
        # No-op baseline so the runner has at least one
        # parsable file in the dir.
        (versions_dir / "20250101_000000_baseline.sql").write_text(
            "-- Migration: no-op baseline\n"
            "-- Created: 2025-01-01\n"
            "-- Description: test fixture baseline\n\n"
            "-- UP\n\n"
            "-- DOWN\n"
        )
        shutil.copy(_MIGRATION_SQL, versions_dir / _MIGRATION_SQL.name)
        try:
            yield versions_dir
        finally:
            pass


@pytest.fixture
def throwaway_sqlite_file_db(tmp_path) -> Iterator[Engine]:
    """Yield a file-backed SQLite engine on a ``tmp_path`` DB.

    Fenced per the project critical note: NEVER resolve
    HOME-derived install paths in tests. ``tmp_path`` is the
    canonical pytest fenced-fixture pattern. The runner
    requires a file-backed DB (not ``:memory:``) so the
    helper that ledger-pre-marks the F1 trap can target the
    same file.
    """
    db_path = tmp_path / "live-view-passcode-parity.sqlite"
    eng = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False, "timeout": 30},
    )
    try:
        yield eng
    finally:
        eng.dispose()


# ─────────────────────────────────────────────────────────────────────
# Model ↔ migration DDL column-set parity (T1.2)
# ─────────────────────────────────────────────────────────────────────


class TestModelDdlColumnSetParity:
    """T1.2 — the SQLModel column-set MUST match the migration
    ``CREATE TABLE`` column-set BY NAME. A drift here means
    the PG ``create_all`` path emits columns the migration
    does not, or vice versa — the durable-row-vs-create_all
    divergence that breaks any future P2 column-add (the
    canonical example is ``last_attempt_at``: the model
    declares it as NULLable TEXT, the migration declares it
    as the same — drift here means the migration will not
    create the column and PG ``create_all`` will fail with
    "column does not exist" on first INSERT)."""

    def test_live_view_passcodes_column_set_parity(self):
        """The ``live_view_passcodes`` model column-set
        equals the migration ``CREATE TABLE`` column-set,
        BY NAME (order-independent)."""
        model_cols = _model_columns("live_view_passcodes")
        ddl_cols = _migration_ddl_columns("live_view_passcodes")
        missing_in_ddl = model_cols - ddl_cols
        missing_in_model = ddl_cols - model_cols
        assert not missing_in_ddl, (
            f"model declares columns the migration does not: "
            f"{sorted(missing_in_ddl)} — PG create_all would "
            f"create columns absent from the migration (PG "
            f"reads the model; SQLite reads the migration; "
            f"drift here breaks dual-driver parity)"
        )
        assert not missing_in_model, (
            f"migration declares columns the model does not: "
            f"{sorted(missing_in_model)} — SQLite would have "
            f"columns the PG create_all path lacks"
        )
        assert model_cols == ddl_cols

    def test_live_view_passcode_audit_column_set_parity(self):
        """The ``live_view_passcode_audit`` model column-set
        equals the migration ``CREATE TABLE`` column-set,
        BY NAME."""
        model_cols = _model_columns("live_view_passcode_audit")
        ddl_cols = _migration_ddl_columns("live_view_passcode_audit")
        missing_in_ddl = model_cols - ddl_cols
        missing_in_model = ddl_cols - model_cols
        assert not missing_in_ddl, (
            f"model declares columns the migration does not: "
            f"{sorted(missing_in_ddl)}"
        )
        assert not missing_in_model, (
            f"migration declares columns the model does not: "
            f"{sorted(missing_in_model)}"
        )
        assert model_cols == ddl_cols


# ─────────────────────────────────────────────────────────────────────
# Execute the migration up_sql via the runner on a throwaway SQLite
# DB. This is the missing path the parity test did not previously
# exercise: a fresh-SQLite DB boots ``MigrationRunner`` and the
# runner applies the up_sql exactly the way the daemon does at
# lifespan.
# ─────────────────────────────────────────────────────────────────────


class TestMigrationUpSqlExecutesCleanly:
    """Execute the migration's ``up_sql`` via ``MigrationRunner``
    on a throwaway SQLite DB. This is the path the parity test
    previously skipped — the test file only parsed the
    migration text + ran ``SQLModel.metadata.create_all``, never
    the actual runner. End-to-end: a fresh SQLite DB boots the
    runner, the runner applies the up_sql, both tables + all
    three indexes materialize, the ledger row registers.

    The F1 trap (a KNOWN pre-existing defect at
    ``20260714_000001_widen_job_queue_type_constraint.sql``)
    would block a fresh-SQLite boot with the FULL
    ``daemon/migrations/versions/`` dir, so the fixture
    isolates ONLY the live-view-passcode migration (see
    ``isolated_live_view_passcode_versions_dir``).

    The test ALSO exercises the canonical FAKE_HOME fence:
    ``tmp_path`` provides the throwaway DB; the runner never
    touches HOME-derived install paths (the project critical
    note: "test scripts must NEVER resolve live-shaped
    paths")."""

    def test_runner_applies_up_sql_to_fresh_sqlite_db(
        self,
        throwaway_sqlite_file_db: Engine,
        isolated_live_view_passcode_versions_dir: Path,
    ):
        """End-to-end: a fresh SQLite DB boots the runner +
        the runner applies the up_sql cleanly + both tables +
        all three named indexes materialize + the ledger row
        registers the migration as applied."""
        from daemon.migrations.runner import MigrationRunner

        runner = MigrationRunner(
            throwaway_sqlite_file_db,
            migrations_dir=isolated_live_view_passcode_versions_dir,
        )
        runner.ensure_migrations_table()
        applied = runner.run_pending_migrations()
        # The runner returns the list of versions it applied.
        # Our fixture has TWO files (the no-op baseline + the
        # live-view-passcode migration) so both should be
        # applied.
        assert "20261017_193816" in applied, (
            f"runner did not apply the live-view-passcode "
            f"migration; applied={applied}"
        )
        assert "20250101_000000" in applied, (
            f"runner did not apply the no-op baseline; "
            f"applied={applied}"
        )

        # Both tables exist.
        inspector = inspect(throwaway_sqlite_file_db)
        assert inspector.has_table("live_view_passcodes")
        assert inspector.has_table("live_view_passcode_audit")

        # All three named indexes materialize.
        lvp_idx = {
            i["name"]
            for i in inspector.get_indexes("live_view_passcodes")
        }
        assert "uq_live_view_passcodes_root_rel" in lvp_idx
        assert "idx_live_view_passcodes_locked" in lvp_idx

        audit_idx = {
            i["name"]
            for i in inspector.get_indexes("live_view_passcode_audit")
        }
        assert "idx_live_view_passcode_audit_root_at" in audit_idx

        # UNIQUE index survived the runner. On SQLite, a
        # separate ``CREATE UNIQUE INDEX`` statement
        # surfaces via ``inspect.get_indexes`` with
        # ``unique=1`` (NOT via ``get_unique_constraints``,
        # which only sees inline UNIQUE clauses inside the
        # CREATE TABLE — the secrets parity-test precedent).
        all_lvp_idx = list(inspector.get_indexes("live_view_passcodes"))
        uq_rows = [
            i for i in all_lvp_idx
            if i["name"] == "uq_live_view_passcodes_root_rel"
        ]
        assert len(uq_rows) == 1, (
            f"uq_live_view_passcodes_root_rel not materialized "
            f"as a UNIQUE index; indexes={all_lvp_idx}"
        )
        assert uq_rows[0].get("unique") == 1, (
            f"uq_live_view_passcodes_root_rel is not unique; "
            f"row={uq_rows[0]!r}"
        )

    def test_runner_up_sql_emits_partial_index_with_locked_predicate(
        self,
        throwaway_sqlite_file_db: Engine,
        isolated_live_view_passcode_versions_dir: Path,
    ):
        """The runner applies the migration's
        ``WHERE locked <> 0`` partial-index predicate (the
        SQLite INTEGER-0/1 shape — the model-side parallel
        uses ``WHERE locked IS TRUE`` which is the
        PG-portable form; both predicates select the same
        row set on their respective drivers)."""
        from daemon.migrations.runner import MigrationRunner
        from sqlalchemy import text

        runner = MigrationRunner(
            throwaway_sqlite_file_db,
            migrations_dir=isolated_live_view_passcode_versions_dir,
        )
        runner.ensure_migrations_table()
        runner.run_pending_migrations()

        # The migration emits ``WHERE locked <> 0`` (SQLite
        # INTEGER-0/1 predicate — the dual-driver twin of
        # the model's ``IS TRUE`` PG-portable form).
        with throwaway_sqlite_file_db.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT sql FROM sqlite_master "
                    "WHERE type='index' "
                    "AND name='idx_live_view_passcodes_locked'"
                )
            ).fetchall()
        assert len(rows) == 1, (
            f"idx_live_view_passcodes_locked not materialized "
            f"by the runner; sqlite_master rows={rows}"
        )
        ddl = rows[0][0]
        assert "WHERE locked" in ddl, (
            f"idx_live_view_passcodes_locked DDL does not "
            f"carry a WHERE predicate; DDL={ddl!r}"
        )

    def test_runner_records_ledger_row_for_live_view_passcode_migration(
        self,
        throwaway_sqlite_file_db: Engine,
        isolated_live_view_passcode_versions_dir: Path,
    ):
        """The ``schema_migrations`` ledger row for the
        live-view-passcode migration registers the file's
        true SHA-256 checksum (the secrets parity-test
        precedent at ``test_secrets_index_parity.py``)."""
        import hashlib

        from daemon.migrations.runner import MigrationRunner
        from sqlalchemy import text

        runner = MigrationRunner(
            throwaway_sqlite_file_db,
            migrations_dir=isolated_live_view_passcode_versions_dir,
        )
        runner.ensure_migrations_table()
        runner.run_pending_migrations()

        with throwaway_sqlite_file_db.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT version, checksum FROM schema_migrations "
                    "WHERE version = :v"
                ),
                {"v": "20261017_193816"},
            ).first()
        assert row is not None, (
            "runner did not record a schema_migrations row for "
            "the live-view-passcode migration"
        )
        version, checksum = row[0], row[1]
        assert version == "20261017_193816"
        expected_checksum = hashlib.sha256(
            _MIGRATION_SQL.read_bytes()
        ).hexdigest()
        assert checksum == expected_checksum, (
            f"ledger checksum mismatch — runner recorded "
            f"{checksum}, expected {expected_checksum}; the "
            f"parity test would silently drift if the runner "
            f"recorded a stale hash"
        )



# ─────────────────────────────────────────────────────────────────────
# C1 — partial-index predicate is PG-portable: no
# ``boolean <> integer`` comparison in the emitted PG DDL.
# ─────────────────────────────────────────────────────────────────────


class TestPartialIndexPredicatePortable:
    """C1 — the partial index ``idx_live_view_passcodes_locked``
    uses ``WHERE locked IS TRUE`` (PG: native BOOLEAN test;
    SQLite 3.24+: INTEGER 0/1 boolean test). The previous
    ``WHERE locked <> 0`` would raise ``operator does not
    exist: boolean <> integer`` at PG ``create_all`` time —
    a PG-boot-breaking latent defect (the bug-fix commission
    2026-10-17 C1 ruling).

    The compile-side assertion below pins the predicate shape
    against the postgresql dialect (the canonical PG
    ``create_all`` path; ``daemon/manager.py:649``). A
    regression to ``locked <> 0`` would surface as the
    expected substring appearing in the compiled DDL."""

    def test_pg_compiled_predicate_uses_is_true_not_ne_zero(self):
        """The compiled PG DDL for the partial index uses
        ``WHERE locked IS TRUE`` (not ``WHERE locked <> 0``).
        The OLD predicate (``locked <> 0``) would fail on
        PG at ``create_all`` with ``operator does not
        exist: boolean <> integer`` — the bug we are
        closing here."""
        from sqlalchemy import text
        from sqlalchemy.dialects import postgresql
        from sqlalchemy.schema import CreateIndex

        # Find the LiveViewPasscode table's
        # ``idx_live_view_passcodes_locked`` index in
        # SQLModel.metadata.
        table = SQLModel.metadata.tables["live_view_passcodes"]
        locked_idx = next(
            i for i in table.indexes
            if i.name == "idx_live_view_passcodes_locked"
        )
        # Compile the CreateIndex against the postgresql
        # dialect. The emitted predicate must be
        # ``IS TRUE`` (NOT ``<> 0``).
        compiled = str(
            CreateIndex(locked_idx).compile(
                dialect=postgresql.dialect()
            )
        )
        assert "IS TRUE" in compiled, (
            f"PG-compiled partial-index predicate must be "
            f"``WHERE locked IS TRUE`` (PG-portable BOOLEAN "
            f"test); got {compiled!r}. Regression marker: "
            f"``<> 0`` would crash PG create_all with "
            f"``operator does not exist: boolean <> "
            f"integer`` at table-create time."
        )
        assert "<> 0" not in compiled, (
            f"PG-compiled partial-index predicate must NOT "
            f"contain the boolean<>integer comparison "
            f"``<> 0`` (PG has no such operator on BOOLEAN "
            f"columns); got {compiled!r}"
        )

    def test_sqlite_compiled_predicate_also_uses_is_true(self):
        """The compiled SQLite DDL ALSO uses
        ``WHERE locked IS TRUE`` (the IS operator landed
        in SQLite 3.24; the project pins SQLite 3.53+)."""
        from sqlalchemy import text
        from sqlalchemy.dialects import sqlite
        from sqlalchemy.schema import CreateIndex

        table = SQLModel.metadata.tables["live_view_passcodes"]
        locked_idx = next(
            i for i in table.indexes
            if i.name == "idx_live_view_passcodes_locked"
        )
        compiled = str(
            CreateIndex(locked_idx).compile(
                dialect=sqlite.dialect()
            )
        )
        assert "IS TRUE" in compiled, (
            f"SQLite-compiled partial-index predicate must "
            f"be ``WHERE locked IS TRUE`` (SQLite 3.24+ "
            f"INTEGER 0/1 boolean test); got {compiled!r}"
        )
