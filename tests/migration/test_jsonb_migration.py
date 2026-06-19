"""JSONB migration verification tests (Phase 1, 2026-06-20).

Verifies the two halves of the JSON → JSONB migration:

1. **Fresh PostgreSQL database**: When ``SQLModel.metadata.create_all()``
   runs against a clean PG schema, all 17 ``Column(JSONBType)`` columns
   are created as ``jsonb`` (NOT ``json``). The ``JSONBType``
   TypeDecorator's ``load_dialect_impl`` hook in
   ``daemon/repositories/infra/types.py`` maps to ``JSONB()`` on
   PostgreSQL, so this is purely a schema-level assertion.

2. **Existing PostgreSQL database**: When ``_ensure_postgres_columns()``
   runs against a PG database that still has the old ``json`` columns,
   the PL/pgSQL DO block at the bottom of the statement list converts
   each ``json`` column in the known 17-row hardcoded list to
   ``jsonb``. The DO block is idempotent: re-running finds zero
   remaining ``json`` columns (the ``WHERE data_type = 'json'``
   filter excludes already-converted columns) and is a no-op.

3. **SQLite regression**: ``JSONBType`` resolves to ``JSON`` on SQLite
   (via ``load_dialect_impl``), so changing the column type does NOT
   break the SQLite test paths. The schema-level guard at the bottom
   of the file asserts every converted column still uses
   ``JSONBType`` (not plain ``JSON``).

PostgreSQL-only tests skip with a clear reason when the test DB is
not reachable. SQLite tests always run.

Test markers::

    pytest tests/migration/test_jsonb_migration.py -v

PG environment (matches the project's existing convention in
``tests/integration/test_migration_e2e_comprehensive.py`` and
``tests/manual_test_pg_*.py``)::

    PG_HOST=localhost, PG_PORT=5432, PG_DB=ensemble_test
    user=ensemble, password=ensemble_dev
    (or override via E2E_PG_HOST / E2E_PG_PORT / E2E_PG_DB / E2E_PG_USER / E2E_PG_PASSWORD)
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlmodel import SQLModel

# Import every model module so SQLModel.metadata registers every table.
# This mirrors the import list in daemon.migrations.data_migrator and
# tests/migration/test_data_factory.py so the fresh-DB create_all()
# path emits the same schema the production daemon would.
from daemon.repositories.instance import models as _instance_models  # noqa: F401
from daemon.repositories.project import models as _project_models  # noqa: F401
from daemon.repositories.source import models as _source_models  # noqa: F401
from daemon.repositories.job_queue import models as _job_queue_models  # noqa: F401
from daemon.repositories.job_queue import watcher_models as _watcher_models  # noqa: F401
from daemon.repositories.message_queue import models as _message_queue_models  # noqa: F401
from daemon.repositories.mcp_server import models as _mcp_server_models  # noqa: F401
from daemon.repositories.task import models as _task_models  # noqa: F401
from daemon.repositories.event import models as _event_models  # noqa: F401
from daemon.migrations.models import SchemaMigration as _SchemaMigration  # noqa: F401
from daemon.repositories.infra import models as _infra_models  # noqa: F401

# NOTE: ``OpenCodeSessionRecord`` is intentionally NOT in
# ``SQLModel.metadata`` — its module uses ``OpenCodeSessionRecord
# .__table__.create()`` to avoid polluting the dedicated opencode
# engine with ensemble's other 22+ tables. See
# ``daemon/opencode/repository.py`` docstring for the rationale.
# This means the fresh-DB tests below must also call
# ``__table__.create()`` explicitly for the opencode table.
from daemon.opencode.repository import OpenCodeSessionRecord  # noqa: F401

from daemon.repositories.infra.types import JSONBType


# ─────────────────────────────────────────────────────────────────────────────
# Constants — the 17 (table, column) pairs the DO block converts.
# ─────────────────────────────────────────────────────────────────────────────
#
# These must match the hardcoded list in
# ``daemon/manager.py:_ensure_postgres_columns()`` (the DO block's
# ``(table_name, column_name) IN (...)`` predicate) exactly. If you
# add a new JSONBType column, add it here AND in the DO block AND
# run the test again.

JSONB_MIGRATION_PAIRS: list[tuple[str, str]] = [
    ("source_configs", "config"),
    ("instance_mappings", "mapping_metadata"),
    ("project_metadata_records", "meta_value"),
    ("projects", "related_directories"),
    ("projects", "metadata"),
    ("projects", "relationships"),
    ("project_history", "entry_metadata"),
    ("job_queue_items", "metadata"),
    ("dead_letter_items", "metadata"),
    ("job_watchers", "watch_events"),
    ("instances", "metadata"),
    ("message_queue", "metadata"),
    ("message_queue", "images"),
    ("mcp_servers", "config"),
    ("mcp_servers", "config_schema"),
    ("opencode_sessions", "latest_response"),
    ("opencode_sessions", "questions"),
]

assert len(JSONB_MIGRATION_PAIRS) == 17, (
    f"Expected 17 JSONB migration pairs, got {len(JSONB_MIGRATION_PAIRS)}. "
    f"Update this list AND the DO block in daemon/manager.py to match."
)


# ─────────────────────────────────────────────────────────────────────────────
# PG environment probing — matches existing test conventions.
# ─────────────────────────────────────────────────────────────────────────────

PG_HOST = os.environ.get("E2E_PG_HOST", "localhost")
PG_PORT = int(os.environ.get("E2E_PG_PORT", "5432"))
PG_DB = os.environ.get("E2E_PG_DB", "ensemble_test")
PG_USER = os.environ.get("E2E_PG_USER", os.environ.get("USER", "ensemble"))
PG_PASSWORD = os.environ.get("E2E_PG_PASSWORD", "ensemble_dev")


def _pg_url(driver: str = "psycopg") -> str:
    """Build a sync PG URL (psycopg driver by default)."""
    return (
        f"postgresql+{driver}://{PG_USER}:{PG_PASSWORD}"
        f"@{PG_HOST}:{PG_PORT}/{PG_DB}"
    )


def _pg_available() -> bool:
    """Probe whether the test PG is reachable. Skip otherwise."""
    try:
        import psycopg  # type: ignore[import-not-found]
    except ImportError:
        return False
    try:
        # Probe with the libpq-style URL; psycopg accepts the
        # ``postgresql://`` scheme directly.
        url = f"postgresql://{PG_USER}:{PG_PASSWORD}@{PG_HOST}:{PG_PORT}/{PG_DB}"
        with psycopg.connect(url, connect_timeout=3) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
        return True
    except Exception as e:  # pragma: no cover — diagnostic only
        print(f"[jsonb-migration] PG probe failed: {type(e).__name__}: {e}")
        return False


_PG_SKIP_REASON = (
    f"PostgreSQL test DB not reachable at {PG_HOST}:{PG_PORT}/{PG_DB} "
    f"as user {PG_USER!r}"
)


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def fresh_pg_schema() -> Iterator[Engine]:
    """Yield a fresh PG engine whose schema mirrors production.

    Drops every public-schema table, re-creates the schema via
    ``SQLModel.metadata.create_all``, and yields the engine. The
    test cleans up by dropping every public-schema table again on
    exit. This gives every test a clean slate without conflicting
    with other test modules that may also be running against the
    same DB.
    """
    if not _pg_available():
        pytest.skip(_PG_SKIP_REASON)

    engine = create_engine(_pg_url(), pool_pre_ping=True)
    try:
        # Drop everything first so the create_all below installs the
        # current model definitions (not whatever a prior test left
        # behind).
        with engine.begin() as conn:
            tables = conn.execute(
                text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
            ).fetchall()
            for (name,) in tables:
                conn.execute(text(f'DROP TABLE IF EXISTS "{name}" CASCADE'))

        SQLModel.metadata.create_all(engine)
        # ``OpenCodeSessionRecord`` is created separately because
        # its module uses ``__table__.create()`` rather than
        # ``SQLModel.metadata.create_all()`` (see module docstring).
        OpenCodeSessionRecord.__table__.create(engine, checkfirst=True)
        yield engine
    finally:
        # Best-effort cleanup. Use a fresh connection to avoid
        # issues with the engine being in an aborted state from a
        # failed test.
        try:
            with engine.begin() as conn:
                tables = conn.execute(
                    text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
                ).fetchall()
                for (name,) in tables:
                    conn.execute(text(f'DROP TABLE IF EXISTS "{name}" CASCADE'))
        except Exception:
            pass
        engine.dispose()


@pytest.fixture
def sqlite_engine() -> Iterator[Engine]:
    """Yield a fresh in-memory SQLite engine with the full schema."""
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    # ``OpenCodeSessionRecord`` is created separately because
    # its module uses ``__table__.create()`` rather than
    # ``SQLModel.metadata.create_all()`` (see module docstring).
    OpenCodeSessionRecord.__table__.create(engine, checkfirst=True)
    try:
        yield engine
    finally:
        engine.dispose()


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _pg_column_types(engine: Engine) -> dict[tuple[str, str], str]:
    """Return ``{(table_name, column_name): data_type}`` for every
    user column in the public schema.

    Uses ``information_schema.columns`` so the test is dialect-agnostic
    (SQLAlchemy's reflection API is heavier and conflates PostgreSQL
    type aliases).
    """
    types: dict[tuple[str, str], str] = {}
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT table_name, column_name, data_type "
                "FROM information_schema.columns "
                "WHERE table_schema = 'public'"
            )
        ).fetchall()
        for table_name, column_name, data_type in rows:
            types[(table_name, column_name)] = data_type
    return types


def _drop_and_recreate_with_plain_json(engine: Engine) -> None:
    """Replace the 17 ``jsonb`` columns with ``json`` so the DO block
    has something to convert.

    Used by the idempotency tests to simulate an "existing PG DB with
    old schema". We issue ``ALTER TABLE ... ALTER COLUMN ... TYPE
    json`` (the inverse of the DO block's conversion) so the test
    can verify the DO block correctly re-converts them back to
    ``jsonb``.
    """
    with engine.begin() as conn:
        for table_name, column_name in JSONB_MIGRATION_PAIRS:
            conn.execute(
                text(
                    f"ALTER TABLE {table_name} "
                    f"ALTER COLUMN {column_name} TYPE json "
                    f"USING {column_name}::text::json"
                )
            )


# ─────────────────────────────────────────────────────────────────────────────
# Test classes
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.skipif(not _pg_available(), reason=_PG_SKIP_REASON)
class TestFreshPGSchemaIsJSONB:
    """Schema-level guard: fresh PG DB has ``jsonb`` for all 17 columns.

    With ``SQLModel.metadata.create_all()`` against a clean PG schema,
    every ``Column(JSONBType)`` is created as ``jsonb`` because the
    TypeDecorator's ``load_dialect_impl`` hook resolves to
    ``JSONB()`` on PostgreSQL. This pins that behaviour.
    """

    def test_all_seventeen_columns_are_jsonb_on_fresh_pg(self, fresh_pg_schema):
        """All 17 known JSON columns come back as ``jsonb`` on a fresh PG DB."""
        types = _pg_column_types(fresh_pg_schema)
        mismatches: list[str] = []
        for table_name, column_name in JSONB_MIGRATION_PAIRS:
            key = (table_name, column_name)
            actual = types.get(key)
            if actual != "jsonb":
                mismatches.append(f"{table_name}.{column_name} = {actual!r} (expected 'jsonb')")
        assert not mismatches, (
            "Fresh PG schema has wrong column types:\n  "
            + "\n  ".join(mismatches)
        )

    def test_no_json_columns_remain_on_fresh_pg(self, fresh_pg_schema):
        """Zero columns in the public schema have ``data_type = 'json'``."""
        types = _pg_column_types(fresh_pg_schema)
        json_columns = [
            f"{t}.{c}" for (t, c), dt in types.items() if dt == "json"
        ]
        assert json_columns == [], (
            f"Fresh PG schema has unexpected json columns: {json_columns}"
        )


@pytest.mark.skipif(not _pg_available(), reason=_PG_SKIP_REASON)
class TestEnsurePostgresColumnsConvertsJSONtoJSONB:
    """Verify ``_ensure_postgres_columns()`` converts ``json`` → ``jsonb``.

    The DO block in ``_ensure_postgres_columns()`` must:
      1. Convert every ``json`` column in the hardcoded 17-row list
         to ``jsonb``.
      2. Be idempotent — re-running on a DB that already has
         ``jsonb`` columns must be a no-op (no error, no churn).

    These tests build the DO block SQL inline (instead of instantiating
    ``EnsembleManager``) to avoid pulling in the full daemon
    bootstrap path. The DO block SQL is identical to the one in
    ``daemon/manager.py`` so any divergence here would indicate the
    migration block has drifted out of sync.
    """

    # The DO block SQL — kept in sync with daemon/manager.py.
    # Changes to either must be reflected in the other.
    _DO_BLOCK_SQL = """
DO $$
DECLARE
    r RECORD;
BEGIN
    FOR r IN
        SELECT table_name, column_name
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND data_type = 'json'
          AND (table_name, column_name) IN (
              ('source_configs','config'),
              ('instance_mappings','mapping_metadata'),
              ('project_metadata_records','meta_value'),
              ('projects','related_directories'),
              ('projects','metadata'),
              ('projects','relationships'),
              ('project_history','entry_metadata'),
              ('job_queue_items','metadata'),
              ('dead_letter_items','metadata'),
              ('job_watchers','watch_events'),
              ('instances','metadata'),
              ('message_queue','metadata'),
              ('message_queue','images'),
              ('mcp_servers','config'),
              ('mcp_servers','config_schema'),
              ('opencode_sessions','latest_response'),
              ('opencode_sessions','questions')
          )
    LOOP
        BEGIN
            EXECUTE format(
                'ALTER TABLE %I ALTER COLUMN %I TYPE jsonb USING %I::jsonb',
                r.table_name, r.column_name, r.column_name
            );
        EXCEPTION WHEN OTHERS THEN
            RAISE EXCEPTION 'jsonb migration failed for %.%: %',
                r.table_name, r.column_name, SQLERRM;
        END;
    END LOOP;
END $$;
"""

    def test_do_block_converts_json_columns_to_jsonb(self, fresh_pg_schema):
        """After flipping to plain json, the DO block converts to jsonb."""
        # Step 1: simulate "existing PG DB with old schema" by reverting
        # the 17 jsonb columns back to json.
        _drop_and_recreate_with_plain_json(fresh_pg_schema)

        # Sanity check: confirm the conversion target really is json.
        types = _pg_column_types(fresh_pg_schema)
        for table_name, column_name in JSONB_MIGRATION_PAIRS:
            assert types[(table_name, column_name)] == "json", (
                f"Test setup error: {table_name}.{column_name} should be 'json' "
                f"before DO block runs, got {types[(table_name, column_name)]!r}"
            )

        # Step 2: run the DO block.
        with fresh_pg_schema.begin() as conn:
            conn.execute(text(self._DO_BLOCK_SQL))

        # Step 3: every column should now be jsonb.
        types = _pg_column_types(fresh_pg_schema)
        mismatches: list[str] = []
        for table_name, column_name in JSONB_MIGRATION_PAIRS:
            actual = types[(table_name, column_name)]
            if actual != "jsonb":
                mismatches.append(
                    f"{table_name}.{column_name} = {actual!r} (expected 'jsonb')"
                )
        assert not mismatches, (
            "DO block failed to convert the following columns:\n  "
            + "\n  ".join(mismatches)
        )

    def test_do_block_is_idempotent_on_already_converted_db(self, fresh_pg_schema):
        """Re-running the DO block on an already-converted DB is a no-op."""
        # fresh_pg_schema already has jsonb columns. Run the DO block
        # twice — both invocations must succeed and leave the schema
        # unchanged.
        with fresh_pg_schema.begin() as conn:
            conn.execute(text(self._DO_BLOCK_SQL))
            conn.execute(text(self._DO_BLOCK_SQL))

        types = _pg_column_types(fresh_pg_schema)
        json_columns = [
            f"{t}.{c}" for (t, c), dt in types.items() if dt == "json"
        ]
        assert json_columns == [], (
            f"DO block left {len(json_columns)} json columns behind: {json_columns}"
        )

    def test_do_block_converts_only_listed_columns(self, fresh_pg_schema):
        """The DO block touches ONLY the 17 listed pairs, no others.

        The ``(table_name, column_name) IN (...)`` predicate should
        scope the rewrite. We simulate by adding a fresh ``json``
        column outside the list and asserting it survives the DO
        block unchanged.
        """
        # Add an extra json column that is NOT in the hardcoded list.
        with fresh_pg_schema.begin() as conn:
            conn.execute(
                text(
                    "ALTER TABLE projects "
                    "ADD COLUMN out_of_scope_json JSON"
                )
            )

        # Confirm the DO block leaves it as json.
        with fresh_pg_schema.begin() as conn:
            conn.execute(text(self._DO_BLOCK_SQL))

        types = _pg_column_types(fresh_pg_schema)
        assert types[("projects", "out_of_scope_json")] == "json", (
            "DO block wrongly converted an out-of-scope column."
        )


class TestSQLiteRegression:
    """SQLite regression: ``JSONBType`` resolves to ``JSON`` on SQLite.

    These tests run unconditionally (no PG dependency) and verify
    that changing the column type from ``JSON`` to ``JSONBType`` did
    not break the SQLite schema-creation path or the schema-level
    invariants every other test relies on.
    """

    def test_jsonbtype_resolves_to_json_on_sqlite(self, sqlite_engine):
        """``JSONBType.load_dialect_impl('sqlite')`` returns ``JSON``.

        The TypeDecorator contract: on SQLite (or any non-PG
        dialect), ``JSONBType`` must resolve to ``JSON`` so SQLite
        gets the same TEXT-with-JSON semantics it had before.
        """
        from sqlalchemy import JSON
        from sqlalchemy.dialects.sqlite import dialect as sqlite_dialect_cls

        sqlite_dialect = sqlite_dialect_cls()
        impl = JSONBType().load_dialect_impl(sqlite_dialect)
        assert isinstance(impl, JSON), (
            f"JSONBType resolved to {type(impl).__name__} on SQLite, "
            f"expected sqlalchemy.JSON"
        )

    def test_all_seventeen_columns_use_jsonbtype_type(self):
        """Schema guard: every converted column is typed as ``JSONBType``.

        Cross-checks the model definitions themselves (independent
        of the DB). Catches a regression where someone retypes one
        of the 17 columns back to ``Column(JSON)``.
        """
        from daemon.repositories.job_queue.models import DeadLetterItem, JobItem
        from daemon.repositories.job_queue.watcher_models import JobWatcher
        from daemon.repositories.instance.models import Instance
        from daemon.repositories.mcp_server.models import McpServer
        from daemon.repositories.message_queue.models import MessageQueue
        from daemon.opencode.repository import OpenCodeSessionRecord
        from daemon.repositories.project.models import (
            Project,
            ProjectHistoryEntry,
            ProjectMetadataRecord,
        )
        from daemon.repositories.source.models import InstanceMapping, SourceConfig

        # Map (table, column) -> table class containing it.
        table_to_class: dict[str, type] = {
            "source_configs": SourceConfig,
            "instance_mappings": InstanceMapping,
            "project_metadata_records": ProjectMetadataRecord,
            "projects": Project,
            "project_history": ProjectHistoryEntry,
            "job_queue_items": JobItem,
            "dead_letter_items": DeadLetterItem,
            "job_watchers": JobWatcher,
            "instances": Instance,
            "message_queue": MessageQueue,
            "mcp_servers": McpServer,
            "opencode_sessions": OpenCodeSessionRecord,
        }

        for table_name, column_name in JSONB_MIGRATION_PAIRS:
            model_cls = table_to_class.get(table_name)
            assert model_cls is not None, (
                f"No model mapping for table {table_name!r}"
            )
            column = model_cls.__table__.columns[column_name]
            assert isinstance(column.type, JSONBType), (
                f"{table_name}.{column_name} is {type(column.type).__name__}, "
                f"expected JSONBType. Re-running Phase 1 conversion is required."
            )

    def test_create_all_succeeds_on_sqlite(self, sqlite_engine):
        """``SQLModel.metadata.create_all`` runs cleanly on SQLite.

        Sanity check that the schema-level change didn't break
        SQLite's CREATE TABLE emission. If a column has an
        unsupported type or wrong arity, create_all raises.
        """
        # The fixture already ran create_all — drop and re-run to
        # exercise the path explicitly here.
        with sqlite_engine.begin() as conn:
            tables = conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='table'")
            ).fetchall()
            for (name,) in tables:
                conn.execute(text(f'DROP TABLE IF EXISTS "{name}"'))
        SQLModel.metadata.create_all(sqlite_engine)

        # Verify the 17 columns exist in the SQLite schema.
        with sqlite_engine.connect() as conn:
            for table_name, column_name in JSONB_MIGRATION_PAIRS:
                row = conn.execute(
                    text(
                        "SELECT type FROM pragma_table_info(:table) "
                        "WHERE name = :column"
                    ),
                    {"table": table_name, "column": column_name},
                ).fetchone()
                assert row is not None, (
                    f"Column {table_name}.{column_name} missing from SQLite schema"
                )
                # SQLite stores JSON as TEXT. The pragma_table_info
                # ``type`` for a JSON-typed column is the literal
                # ``JSON`` (SQLite ≥ 3.45) or ``TEXT`` (older).
                # We accept either.
                assert row[0].upper() in {"JSON", "TEXT"}, (
                    f"{table_name}.{column_name} has unexpected SQLite type "
                    f"{row[0]!r}"
                )
