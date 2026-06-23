"""Phase 4 PostgreSQL column-drop verification tests.

Phase 4 of ``feature/cleanup-old-architecture`` removes the vestigial
``waiting_for`` and ``children`` columns from the ``instances`` table.
The ``instance_hierarchy`` junction table is LIVE and must NOT be dropped.

Background
----------
* ``Instance.waiting_for`` (INTEGER, count of pending children) — was a
  rebuild-only cache never read for runtime control flow; the Dependency
  Bus + ``instance_hierarchy`` are now the source of truth.
* ``Instance.children`` (TEXT/JSON, denormalized cache) — was doubly
  broken (RMW races at 4 sites AND overridden on every read via
  ``_enrich_instance()``); the junction table is the canonical source.
* The ``instance_hierarchy`` junction table is STILL LIVE — entries are
  inserted on spawn and deleted on child completion (working set, not
  permanent storage). It is actively used at 6+ query sites
  (``repository.py``, ``instance_lifecycle.py``, ``child_reports.py``,
  ``error_reporting.py``, etc.) and must remain in the schema.

PostgreSQL migration story
--------------------------
* The file-based migration ``daemon/migrations/versions/
  20260621_000002_drop_legacy_completion_columns.sql`` drops both
  columns. ``DROP COLUMN IF EXISTS`` makes the statements idempotent.
* The migration runner is a NO-OP on PostgreSQL
  (``daemon/migrations/runner.py:446-448``). Therefore the equivalent
  statements are issued by ``InstanceManager._ensure_postgres_drop_legacy_columns``
  (``daemon/manager.py:1838-1882``) at every daemon startup. The
  ``IF EXISTS`` clauses make the hook idempotent.
* The ``.sql`` file contains a DOWN section that recreates the columns
  as empty containers (``waiting_for INTEGER NOT NULL DEFAULT 0``,
  ``children TEXT NOT NULL DEFAULT '[]'``). Rolling back does NOT
  restore data — only schema.

What this test verifies
-----------------------
1. Baseline (after ``SQLModel.metadata.create_all``) has no
   ``waiting_for`` or ``children`` columns on ``instances``.
2. ``instance_hierarchy`` table still exists with its three columns
   (``parent_id``, ``child_id``, ``created_at``).
3. ``_ensure_postgres_drop_legacy_columns()`` removes the columns when
   they exist AND is idempotent — running twice in a row never raises.
4. The migration's DOWN section recreates the columns with the
   declared types.

Run with::

    uv run pytest tests/postgres/test_legacy_column_drop.py -v \\
        --override-ini="addopts=" -m postgres
"""
from __future__ import annotations

import logging
from typing import Any

import pytest
from sqlalchemy import text

# Import models so ``SQLModel.metadata.create_all`` (run by the
# session-scoped ``pg_engine`` fixture) registers both ``instances``
# and ``instance_hierarchy``. The Phase-4 baseline schema MUST include
# ``instance_hierarchy`` (live) and MUST NOT include ``waiting_for`` /
# ``children`` on ``instances``.
from daemon.manager import InstanceManager
from daemon.repositories.instance.models import (  # noqa: F401
    Instance,
    InstanceHierarchy,
)

logger = logging.getLogger(__name__)


# =============================================================================
# SQL constants — mirror the migration file
# =============================================================================
#
# These statements match ``daemon/migrations/versions/
# 20260621_000002_drop_legacy_completion_columns.sql`` exactly. The
# runner does NOT apply .sql files on PostgreSQL, so tests must
# execute the statements themselves. Keeping the strings here (instead
# of reading the file at test time) makes the SQL contract explicit
# and avoids a fixture-time dependency on the daemon package layout.
# =============================================================================

UP_STATEMENTS: tuple[str, ...] = (
    # Drop the legacy waiting_for counter column.
    "ALTER TABLE instances DROP COLUMN IF EXISTS waiting_for",
    # Drop the legacy denormalized children JSON cache column.
    "ALTER TABLE instances DROP COLUMN IF EXISTS children",
)

DOWN_STATEMENTS: tuple[str, ...] = (
    # Recreate waiting_for as INTEGER with default 0 (rebuild-only cache).
    "ALTER TABLE instances ADD COLUMN waiting_for INTEGER NOT NULL DEFAULT 0",
    # Recreate children as TEXT (denormalized cache; default '[]').
    "ALTER TABLE instances ADD COLUMN children TEXT NOT NULL DEFAULT '[]'",
)

# The instance_hierarchy junction table is LIVE and must never be
# dropped. Its three columns (per ``daemon/repositories/instance/
# models.py``):
#   - parent_id:   TEXT PK
#   - child_id:    TEXT PK
#   - created_at:  TEXT (ISO-8601 timestamp)
EXPECTED_HIERARCHY_COLUMNS = ("parent_id", "child_id", "created_at")


# =============================================================================
# Helpers
# =============================================================================


def _table_exists(pg_engine, table_name: str) -> bool:
    """Return True if ``table_name`` exists in the ``public`` schema."""
    sql = text(
        """
        SELECT 1
        FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = :table_name
        LIMIT 1
        """
    )
    with pg_engine.connect() as conn:
        row = conn.execute(sql, {"table_name": table_name}).fetchone()
    return row is not None


def _column_names(pg_engine, table_name: str) -> set[str]:
    """Return the set of column names on ``table_name`` in ``public``."""
    sql = text(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = :table_name
        """
    )
    with pg_engine.connect() as conn:
        rows = conn.execute(sql, {"table_name": table_name}).fetchall()
    return {row[0] for row in rows}


def _column_type(pg_engine, table_name: str, column_name: str) -> str | None:
    """Return the declared data type for a column (or None if absent)."""
    sql = text(
        """
        SELECT data_type
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = :table_name
          AND column_name = :column_name
        LIMIT 1
        """
    )
    with pg_engine.connect() as conn:
        row = conn.execute(
            sql, {"table_name": table_name, "column_name": column_name}
        ).fetchone()
    return row[0] if row else None


def _apply(pg_engine, statements: tuple[str, ...]) -> None:
    """Execute each statement in a single transaction.

    Using ``engine.begin()`` keeps all statements in one transaction so
    tests can leave the schema in a consistent state on success or
    roll back atomically on failure.
    """
    with pg_engine.begin() as conn:
        for stmt in statements:
            conn.execute(text(stmt))


def _ensure_baseline(pg_engine) -> None:
    """Force the schema back to the Phase-4 baseline (columns dropped).

    The session-scoped ``pg_engine`` schema is shared across tests in
    this module, so each test that mutates schema state calls this in
    a ``finally`` to restore the baseline for the next test.
    """
    _apply(pg_engine, UP_STATEMENTS)


def _build_minimal_manager(pg_engine) -> Any:
    """Return an object suitable for binding ``_ensure_postgres_drop_legacy_columns``.

    The production ``InstanceManager.__init__`` is heavy — it creates
    an engine, runs migrations, registers repositories, and pulls in
    CredentialManager / ConnectionPoolManager. For the column-drop
    hook we only need ``self._engine`` (the method reads ``self._engine``
    and calls the module-level ``logger``). A minimal stand-in avoids
    standing up the full daemon surface area.
    """
    return _MinimalManagerProxy(engine=pg_engine)


class _MinimalManagerProxy:
    """Minimal stand-in for ``InstanceManager`` exposing only ``_engine``.

    The ``_ensure_postgres_drop_legacy_columns`` method accesses two
    names on ``self``:
      * ``self._engine`` — the SQLAlchemy engine bound at startup
      * module-level ``logger`` (in ``daemon/manager.py``)
    No other attributes are required.
    """

    def __init__(self, engine: Any) -> None:
        self._engine = engine


# =============================================================================
# Autouse fixture: keep the shared schema in the Phase-4 baseline state
# =============================================================================
#
# The session-scoped ``pg_engine`` fixture creates the schema ONCE for
# the entire module. Each test that mutates the schema must restore the
# baseline in its teardown — but to be defensive against test failures
# mid-test (and against test ordering), an autouse fixture resets the
# schema AFTER every test in this module.
# =============================================================================


@pytest.fixture(autouse=True)
def _restore_baseline(pg_engine):
    """Yield to the test, then restore the Phase-4 baseline schema.

    Each test runs against the post-Phase-4 baseline by default. Tests
    that intentionally add the legacy columns must do so inside the
    test body (the fixture's try/finally will undo the additions).
    """
    yield
    try:
        _ensure_baseline(pg_engine)
    except Exception:  # pragma: no cover - defensive: never fail in teardown
        logger.exception("Failed to restore baseline after test")


# =============================================================================
# Tests
# =============================================================================


def test_baseline_waiting_for_and_children_columns_absent(pg_engine) -> None:
    """After ``SQLModel.metadata.create_all``, neither legacy column exists.

    The Phase-4 ``Instance`` model has no ``waiting_for`` or ``children``
    fields, so ``create_all`` produces a clean schema without them.
    This is the expected post-migration state.
    """
    instances_columns = _column_names(pg_engine, "instances")

    assert "waiting_for" not in instances_columns, (
        "Phase-4 baseline must NOT have ``waiting_for`` column on "
        f"``instances``; got columns: {sorted(instances_columns)}"
    )
    assert "children" not in instances_columns, (
        "Phase-4 baseline must NOT have ``children`` column on "
        f"``instances``; got columns: {sorted(instances_columns)}"
    )


def test_instance_hierarchy_table_intact_with_all_columns(pg_engine) -> None:
    """The ``instance_hierarchy`` junction table must still exist.

    The Phase-4 migration explicitly does NOT drop this table — it is
    the canonical source of parent->child relationships and is still
    actively used at 6+ query sites (spawn INSERT, terminate DELETE,
    child_reports, error_reporting, ``_load_children``).
    """
    assert _table_exists(pg_engine, "instance_hierarchy"), (
        "``instance_hierarchy`` junction table must remain — it is the "
        "canonical source of child IDs (replaced the dropped ``children`` "
        "denormalized column)."
    )

    hierarchy_columns = _column_names(pg_engine, "instance_hierarchy")
    assert hierarchy_columns == set(EXPECTED_HIERARCHY_COLUMNS), (
        f"``instance_hierarchy`` columns drifted; "
        f"expected {sorted(EXPECTED_HIERARCHY_COLUMNS)}, "
        f"got {sorted(hierarchy_columns)}"
    )


def test_instance_hierarchy_insert_and_query_after_phase4(pg_engine) -> None:
    """Inserts into ``instance_hierarchy`` still work in the Phase-4 schema.

    Verifies the junction table is fully functional (not just present)
    after the legacy columns are dropped. Uses the live engine to
    insert a parent/child pair and read it back via raw SQL — exercises
    the same write path that ``SQLModelInstanceRepository.spawn``
    takes during parent-child spawning.
    """
    parent_id = "phase4-parent-001"
    child_id = "phase4-child-001"

    with pg_engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO instance_hierarchy (parent_id, child_id, created_at)
                VALUES (:parent_id, :child_id, :created_at)
                """
            ),
            {
                "parent_id": parent_id,
                "child_id": child_id,
                "created_at": "2026-06-23T00:00:00+00:00",
            },
        )

    with pg_engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT parent_id, child_id, created_at
                FROM instance_hierarchy
                WHERE parent_id = :parent_id AND child_id = :child_id
                """
            ),
            {"parent_id": parent_id, "child_id": child_id},
        ).fetchall()

    assert len(rows) == 1, f"Expected 1 hierarchy row, got {len(rows)}"
    assert rows[0][0] == parent_id
    assert rows[0][1] == child_id
    assert rows[0][2] == "2026-06-23T00:00:00+00:00"


def test_ensure_postgres_drop_legacy_columns_removes_columns(pg_engine) -> None:
    """The production hook drops both legacy columns when present.

    Adds the legacy columns (simulating a pre-Phase-4 schema), invokes
    ``InstanceManager._ensure_postgres_drop_legacy_columns`` via a
    minimal proxy, and verifies both columns are gone.
    """
    # Simulate the pre-Phase-4 schema: add both legacy columns back.
    _apply(pg_engine, DOWN_STATEMENTS)
    pre_columns = _column_names(pg_engine, "instances")
    assert {"waiting_for", "children"}.issubset(pre_columns), (
        f"Test setup failed to add legacy columns; got: {sorted(pre_columns)}"
    )

    # Invoke the production hook via the minimal proxy.
    proxy = _build_minimal_manager(pg_engine)
    InstanceManager._ensure_postgres_drop_legacy_columns(proxy)

    post_columns = _column_names(pg_engine, "instances")
    assert "waiting_for" not in post_columns, (
        f"``_ensure_postgres_drop_legacy_columns`` failed to drop "
        f"``waiting_for``; columns now: {sorted(post_columns)}"
    )
    assert "children" not in post_columns, (
        f"``_ensure_postgres_drop_legacy_columns`` failed to drop "
        f"``children``; columns now: {sorted(post_columns)}"
    )


def test_ensure_postgres_drop_legacy_columns_is_idempotent(pg_engine) -> None:
    """Running the hook twice in a row never raises.

    The hook uses ``DROP COLUMN IF EXISTS`` so re-running it on an
    already-clean schema is a no-op. This property is critical because
    ``InstanceManager.__init__`` invokes the hook on every daemon
    startup — a failure here would block all subsequent startups.
    """
    # First invocation: starts from the baseline (columns absent),
    # so this is the "already-clean" path.
    proxy = _build_minimal_manager(pg_engine)
    InstanceManager._ensure_postgres_drop_legacy_columns(proxy)

    # Second invocation immediately after — must not raise.
    try:
        InstanceManager._ensure_postgres_drop_legacy_columns(proxy)
    except Exception as exc:  # pragma: no cover - failure path
        pytest.fail(
            f"Second invocation of ``_ensure_postgres_drop_legacy_columns`` "
            f"raised {type(exc).__name__}: {exc} — the hook must be "
            f"idempotent for repeated daemon startups."
        )

    # And a third invocation after explicitly re-adding the columns,
    # then dropping them — exercises both branches of the IF EXISTS.
    _apply(pg_engine, DOWN_STATEMENTS)
    InstanceManager._ensure_postgres_drop_legacy_columns(proxy)
    InstanceManager._ensure_postgres_drop_legacy_columns(proxy)

    final_columns = _column_names(pg_engine, "instances")
    assert "waiting_for" not in final_columns
    assert "children" not in final_columns


def test_down_section_recreates_columns_with_declared_types(
    pg_engine,
) -> None:
    """The migration's DOWN section recreates the columns with correct types.

    The .sql DOWN section declares:
      * ``waiting_for INTEGER NOT NULL DEFAULT 0``
      * ``children TEXT NOT NULL DEFAULT '[]'``

    We verify the DOWN statements recreate both columns and that
    PostgreSQL records the declared types in ``information_schema``.
    """
    # Start from a clean baseline (autouse fixture ran before this
    # test, but be explicit in case a sibling test re-ordered).
    _ensure_baseline(pg_engine)
    pre_columns = _column_names(pg_engine, "instances")
    assert "waiting_for" not in pre_columns
    assert "children" not in pre_columns

    # Apply DOWN: columns should reappear.
    _apply(pg_engine, DOWN_STATEMENTS)

    assert _column_type(pg_engine, "instances", "waiting_for") == "integer", (
        "DOWN should recreate ``waiting_for`` as INTEGER. Got: "
        f"{_column_type(pg_engine, 'instances', 'waiting_for')}"
    )
    assert _column_type(pg_engine, "instances", "children") == "text", (
        "DOWN should recreate ``children`` as TEXT. Got: "
        f"{_column_type(pg_engine, 'instances', 'children')}"
    )

    # Verify the recreated columns actually accept DEFAULT values.
    # The ``version`` and ``metadata`` columns on ``instances`` are NOT NULL
    # but their defaults (``1`` and ``{}``) are applied at the Python/ORM
    # level, not as PG ``DEFAULT`` clauses. We supply them explicitly so
    # the INSERT focuses on validating the DOWN-section defaults
    # (``waiting_for=0``, ``children='[]'``) rather than tripping on
    # unrelated NOT NULL constraints.
    from datetime import datetime, timezone

    now_iso = datetime.now(timezone.utc).isoformat()
    with pg_engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO instances (
                    instance_id, agent_id, agent_dir, status,
                    version, created_at, updated_at, metadata
                ) VALUES (
                    :instance_id, :agent_id, :agent_dir, :status,
                    :version, :created_at, :updated_at,
                    CAST(:metadata AS jsonb)
                )
                """
            ),
            {
                "instance_id": "phase4-default-probe-001",
                "agent_id": "test-agent",
                "agent_dir": "/tmp/test-agent",
                "status": "idle",
                "version": 1,
                "created_at": now_iso,
                "updated_at": now_iso,
                "metadata": "{}",
            },
        )

    with pg_engine.connect() as conn:
        row = conn.execute(
            text(
                """
                SELECT waiting_for, children
                FROM instances
                WHERE instance_id = :instance_id
                """
            ),
            {"instance_id": "phase4-default-probe-001"},
        ).fetchone()

    assert row is not None, "Inserted instance row not found"
    assert row[0] == 0, (
        f"DEFAULT 0 not applied for ``waiting_for``; got {row[0]!r}"
    )
    assert row[1] == "[]", (
        f"DEFAULT '[]' not applied for ``children``; got {row[1]!r}"
    )


def test_up_and_down_round_trip(pg_engine) -> None:
    """UP → DOWN → UP round-trip restores the baseline cleanly.

    Exercises the full migration lifecycle as documented in the .sql
    file's UP/DOWN sections. After the final UP, the schema is back to
    the Phase-4 baseline (no legacy columns) and ``instance_hierarchy``
    is intact.
    """
    # First UP (already the baseline, but make it explicit).
    _apply(pg_engine, UP_STATEMENTS)

    # DOWN → columns reappear.
    _apply(pg_engine, DOWN_STATEMENTS)
    assert {"waiting_for", "children"}.issubset(_column_names(pg_engine, "instances"))

    # UP again → columns gone again.
    _apply(pg_engine, UP_STATEMENTS)
    final = _column_names(pg_engine, "instances")
    assert "waiting_for" not in final
    assert "children" not in final

    # instance_hierarchy survived the entire round-trip.
    assert _table_exists(pg_engine, "instance_hierarchy")
    assert _column_names(pg_engine, "instance_hierarchy") == set(
        EXPECTED_HIERARCHY_COLUMNS
    )