"""drift_events index parity tests — v0.18.0 regression fix (2026-10-07).

The v0.18.0 release shipped the slice-⑥ ``drift_events`` table on
PostgreSQL WITHOUT its three secondary indexes
(``ix_drift_events_plugin``, ``ix_drift_events_plugin_divergence``,
``ix_drift_events_observed_at``). pg_indexes on live PG reported the
PK only. The root cause was a three-way divergence in the data:

* The ``DriftEvent`` SQLModel had NO ``__table_args__`` Index
  declarations — ``SQLModel.metadata.create_all()`` could not emit
  anything but the PK.
* The SQLite migration at
  ``daemon/migrations/versions/20261007_000001_create_drift_events.sql``
  declared three ``CREATE INDEX`` statements with names
  ``ix_drift_events_plugin`` / ``ix_drift_events_plugin_divergence`` /
  ``ix_drift_events_observed_at``.
* The migration header comment claimed "Index names match the model
  declaration so both dialects converge" — provably FALSE.
* ``EnsembleManager._ensure_postgres_columns`` had no
  ``drift_events`` index block, so existing PG databases (where
  ``create_all`` is a no-op) never received the indexes either.
* ``MigrationRunner.run_pending_migrations`` is a NO-OP on non-SQLite
  engines (``daemon/migrations/runner.py:721-726``), so the SQLite
  migration never ran on PG.

The fix: add ``__table_args__`` to the model (canonical source for
fresh databases via ``create_all``) AND add the three ``CREATE INDEX
IF NOT EXISTS`` statements to ``_ensure_postgres_columns`` (the
explicit path for existing PG databases, run at every daemon
startup).

This test module pins the parity between:

  (a) the model's ``__table_args__`` Index declarations,
  (b) the SQLite migration's ``CREATE INDEX`` statements, and
  (c) the ``_ensure_postgres_columns`` block in
      ``daemon/manager.py``.

If any name drifts out of sync, the dual-driver contract is broken
on the new release — the test fails loudly (no silent skip). It also
verifies the ``create_all`` fresh-DB path emits the indexes on SQLite
(the proxy path the dialect we can test without a live PG database)
and that the CREATE INDEX IF NOT EXISTS statements in the migration are
IDEMPOTENT (present indexes => no-op; absent indexes => created).

Test markers::

    pytest tests/migration/test_drift_events_index_parity.py -v

The PG-specific execution path (``_ensure_postgres_columns``) is
covered by SQLite-proxy assertions below (statement-shape parity +
idempotency); a real-PG run is not required for THIS regression
fix because the assertion targets the statement strings themselves,
not PG-specific semantics (``CREATE INDEX IF NOT EXISTS`` is
identical on both dialects for the name parity + idempotency we
care about).
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

# Importing ``DriftEvent`` is REQUIRED to register the ``drift_events``
# table on ``SQLModel.metadata`` — without this the assertions below
# see an empty metadata and fail. The fixture pattern matches the
# existing migration-test convention in tests/migration/test_jsonb_migration.py
# (import every model module so SQLModel.metadata registers every
# table).
from daemon.plugin_subsystem.drift_event_publisher import DriftEvent  # noqa: F401

# Canonical name set (decisions.md D2 dual-driver contract — the
# triple-registration requirement for the three indexes). Any change
# to this tuple MUST be reflected in:
#   - daemon/plugin_subsystem/drift_event_publisher.py::DriftEvent.__table_args__
#   - daemon/migrations/versions/20261007_000001_create_drift_events.sql (CREATE INDEX)
#   - daemon/manager.py::_ensure_postgres_columns (the new ensure block)
EXPECTED_INDEX_NAMES: tuple[str, ...] = (
    "ix_drift_events_plugin",
    "ix_drift_events_plugin_divergence",
    "ix_drift_events_observed_at",
)

# Canonical column lists per index. The DriftEvent model uses
# ``Index(name, col1[, col2])`` — same shape as the SQLite migration's
# CREATE INDEX statements. Drift here is a clear regression.
EXPECTED_INDEX_COLUMNS: dict[str, tuple[str, ...]] = {
    "ix_drift_events_plugin": ("plugin",),
    "ix_drift_events_plugin_divergence": ("plugin", "divergence_id"),
    "ix_drift_events_observed_at": ("observed_at",),
}

# Path to the source files we pin against. Resolved relative to the
# repo root (same convention as ``TestDriftEventsTableRegistration`` in
# ``tests/unit/plugin_subsystem/test_drift_event_publisher.py``).
_REPO_ROOT = Path(__file__).resolve().parents[2]
_MIGRATION_SQL = (
    _REPO_ROOT / "daemon/migrations/versions/20261007_000001_create_drift_events.sql"
)
_MANAGER_PY = _REPO_ROOT / "daemon/manager.py"
_MODEL_PY = _REPO_ROOT / "daemon/plugin_subsystem/drift_event_publisher.py"


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _migration_index_names() -> list[str]:
    """Extract the index names from the SQLite migration's CREATE INDEX
    statements (UP section only)."""
    content = _MIGRATION_SQL.read_text(encoding="utf-8")
    up_match = re.search(r"--\s*UP\s*\n(.*?)(?=--\s*DOWN|$)", content, re.DOTALL)
    assert up_match, f"failed to parse UP section in {_MIGRATION_SQL}"
    up_section = up_match.group(1)
    return re.findall(r"CREATE INDEX IF NOT EXISTS (\w+)", up_section)


def _migration_index_columns() -> dict[str, tuple[str, ...]]:
    """Extract the (name -> ordered column-list) map from the SQLite
    migration's CREATE INDEX statements. The ``ON drift_events``
    clause carries the column list verbatim."""
    content = _MIGRATION_SQL.read_text(encoding="utf-8")
    up_match = re.search(r"--\s*UP\s*\n(.*?)(?=--\s*DOWN|$)", content, re.DOTALL)
    assert up_match, f"failed to parse UP section in {_MIGRATION_SQL}"
    up_section = up_match.group(1)
    # The pattern tolerates quoted-string continuations (``"..." `` and
    # ``"ON ..."`` on separate Python lines joined by implicit
    # concatenation) — the ensure-block statement shape uses this
    # pattern because the line lengths exceed PEP-8 limits. The
    # character class ``[\s'\"]+`` accepts whitespace AND the closing/
    # opening quote between segments.
    pattern = re.compile(
        r"CREATE INDEX IF NOT EXISTS (\w+)[\s'\"]+ON[\s'\"]+drift_events\s*\(([^)]+)\)"
    )
    result: dict[str, tuple[str, ...]] = {}
    for match in pattern.finditer(up_section):
        name = match.group(1)
        cols_raw = match.group(2)
        cols = tuple(c.strip() for c in cols_raw.split(","))
        result[name] = cols
    return result


def _model_index_names() -> list[str]:
    """Return the index names declared by ``DriftEvent.__table_args__``.

    Walks ``SQLModel.metadata.tables['drift_events'].indexes`` — the
    authoritative reflection of what ``SQLModel.metadata.create_all()``
    would emit on a fresh database.
    """
    table = SQLModel.metadata.tables["drift_events"]
    return [i.name for i in table.indexes]


def _model_index_columns() -> dict[str, tuple[str, ...]]:
    """Return the (name -> ordered column-list) map from the
    ``drift_events`` table's Index declarations."""
    table = SQLModel.metadata.tables["drift_events"]
    return {i.name: tuple(c.name for c in i.columns) for i in table.indexes}


def _ensure_block_index_names() -> list[str]:
    """Extract the index names from the ``_ensure_postgres_columns``
    block in ``daemon/manager.py``. The block is keyed by the
    ``drift_index_statements = ( ... )`` tuple and contains three
    ``CREATE INDEX IF NOT EXISTS`` statements inside the
    ``_ensure_postgres_columns`` method.

    We bind the regex search to the tuple's exact bounds so a
    future ``CREATE INDEX (... IF NOT EXISTS ...)`` mention in a
    log line does not contaminate the name list (the v0.18.0 fix
    had a log line that referenced ``CREATE INDEX IF NOT EXISTS is
    idempotent`` and a naive ``re.findall`` would match ``is``).

    We also assert the block is INSIDE ``_ensure_postgres_columns``
    (the placement guarantee): a regression that drops the block
    inside another method (e.g. ``_migrate_overloaded_image_refs_rows``
    or another helper) would still create the indexes (the
    surrounding method is called from `_ensure_postgres_columns`)
    but would be semantically misplaced; this assertion pins the
    correct placement."""
    content = _MANAGER_PY.read_text(encoding="utf-8")
    # Bind the search to the ensure block: open at the marker
    # comment, close at the start of the next method.
    start_marker = "Slice ⑥ drift_events index parity"
    end_marker = "def _migrate_overloaded_image_refs_rows"
    start_idx = content.find(start_marker)
    assert start_idx != -1, (
        f"drift_events ensure-block marker not found in {_MANAGER_PY} — "
        f"either the block was removed or the marker comment was "
        f"changed. The fix requires the block; pin it back."
    )
    end_idx = content.find(end_marker, start_idx)
    assert end_idx != -1, (
        f"_migrate_overloaded_image_refs_rows sentinel not found "
        f"after the drift_events block in {_MANAGER_PY} — refactor "
        f"broke the block boundary; pin it back."
    )
    block = content[start_idx:end_idx]
    # Placement guarantee: the block MUST be inside
    # ``_ensure_postgres_columns``. The method boundary is the
    # closest preceding ``def _ensure_postgres_columns(self) -> None:``
    # line; the next method after the block is
    # ``_migrate_overloaded_image_refs_rows`` (sentinel above).
    # A regression that puts the block inside a different method
    # (e.g. ``_migrate_overloaded_image_refs_rows`` itself — its
    # sweep block ends with the same ``def _ensure_postgres_drop
    # _legacy_columns`` line) would still match the regex but
    # violate the placement guarantee. Verify the block sits
    # inside ``_ensure_postgres_columns``.
    method_open_marker = "def _ensure_postgres_columns(self) -> None:"
    method_open_idx = content.rfind(method_open_marker, 0, start_idx)
    assert method_open_idx != -1, (
        f"_ensure_postgres_columns def line not found before the "
        f"drift_events block in {_MANAGER_PY} — the block is placed "
        f"in the wrong method (must be inside "
        f"_ensure_postgres_columns, NOT a sibling method)."
    )
    # Restrict to the tuple body. Bound by ``drift_index_statements = (``
    # and the tuple's closing ``)`` (the line containing ONLY ``)`` is
    # the end of the tuple, not the end of the method). The end-of-
    # tuple line is the one that contains ``)`` followed by a newline
    # or whitespace + newline.
    tuple_open_marker = "drift_index_statements = ("
    tuple_open_idx = block.find(tuple_open_marker)
    assert tuple_open_idx != -1, (
        f"drift_index_statements tuple opener not found in the "
        f"drift_events ensure block — refactor broke the tuple "
        f"shape; pin it back."
    )
    tuple_body = block[tuple_open_idx:]
    # Walk forward and find the matching close paren at the tuple-end
    # depth — the tuple's last ``)`` ends on a line by itself (the
    # convention used by the existing code blocks). We use a simple
    # heuristic: the first newline-bounded ``)`` line after the
    # opener is the tuple's close.
    tuple_lines = tuple_body.splitlines()
    close_line_idx = None
    for i, line in enumerate(tuple_lines):
        if i == 0:
            continue  # opener
        if line.strip() == ")":
            close_line_idx = i
            break
    assert close_line_idx is not None, (
        f"tuple-close ')' line not found in drift_index_statements "
        f"tuple — refactor broke the tuple shape; pin it back."
    )
    tuple_only = "\n".join(tuple_lines[: close_line_idx + 1])
    return re.findall(r"CREATE INDEX IF NOT EXISTS (\w+)", tuple_only)


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def fresh_sqlite_engine_with_table() -> Iterator[Engine]:
    """Yield a fresh in-memory SQLite engine with the ``drift_events``
    table created via ``SQLModel.metadata.create_all``. This is the
    proxy for "fresh PG database" — on SQLite the same create_all
    path emits the same Index declarations (verified by the name-claim
    parity tests below). The fixture isolates the create_all emit so
    subsequent tests can inspect its output without interference from
    other tests' imports."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    # IMPORTANT: create the DriftEvent table alone — we don't want
    # unrelated SQLModel tables polluting the inspection results.
    DriftEvent.__table__.create(engine, checkfirst=True)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def fresh_sqlite_engine_table_only_no_indexes() -> Iterator[Engine]:
    """Yield a fresh in-memory SQLite engine with the ``drift_events``
    table BUT without its three secondary indexes — simulating the
    live-PG regression state. The fixture is built by:

      1. Issuing ``CREATE TABLE drift_events (...)`` by hand with the
         exact column shape from the SQLite migration (no indexes).
      2. Yielding the engine.

    Subsequent tests can run the migration's CREATE INDEX IF NOT
    EXISTS statements (or simulate the ``_ensure_postgres_columns``
    block) and verify the indexes are BUILT — proving the idempotent
    ensure path works on a "missing-indexes" state (the exact state
    live-PG is in today)."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    # Build the table WITHOUT indexes — same column shape as the
    # migration's CREATE TABLE block. This is the shape live PG has
    # today (table + PK + NOT NULL constraints, ZERO secondary
    # indexes).
    create_table_sql = (
        "CREATE TABLE drift_events ("
        "id TEXT PRIMARY KEY, "
        "plugin TEXT NOT NULL, "
        "target_class TEXT NOT NULL, "
        "divergence_id INTEGER NOT NULL DEFAULT 0, "
        "files JSON NOT NULL DEFAULT '[]', "
        "delta TEXT NOT NULL DEFAULT '', "
        "rationale TEXT NOT NULL DEFAULT '', "
        "pinning_test TEXT NOT NULL DEFAULT '', "
        "observed_at TEXT NOT NULL, "
        "observed_tag TEXT NOT NULL, "
        "created_at TEXT NOT NULL"
        ")"
    )
    with engine.begin() as conn:
        conn.execute(text(create_table_sql))
    # Sanity: zero secondary indexes on the freshly-created table.
    inspector = inspect(engine)
    assert inspector.get_indexes("drift_events") == [], (
        "fresh_sqlite_engine_table_only_no_indexes fixture is broken: "
        "expected zero indexes on the hand-built table"
    )
    try:
        yield engine
    finally:
        engine.dispose()


# ─────────────────────────────────────────────────────────────────────────────
# Parity: model ⇄ migration ⇄ ensure block — names triple-
# drift is a regression
# ─────────────────────────────────────────────────────────────────────────────


class TestDriftEventIndexNameParity:
    """Pin the canonical name set across the three declaration sites.

    The dual-driver contract (decisions.md D2) is "table exists +
    index name matches". If ANY of the three declaration sites
    drifts, the next release ships a divergence on one of the
    dialects — exactly the v0.18.0 regression this test exists to
    prevent. These tests MUST fail loudly (no silent skip) when
    names drift.
    """

    def test_model_declares_all_three_expected_indexes(self):
        """The DriftEvent SQLModel's ``__table_args__`` MUST declare
        exactly the three canonical index names. ``create_all`` only
        emits what the model declares — if any name is missing here,
        a fresh PG database will never receive it."""
        names = set(_model_index_names())
        assert names == set(EXPECTED_INDEX_NAMES), (
            f"DriftEvent model declares indexes {sorted(names)}, "
            f"expected {sorted(EXPECTED_INDEX_NAMES)}. "
            f"The model is the canonical name source for fresh databases. "
            f"Add the missing Index declaration to "
            f"DriftEvent.__table_args__ in "
            f"daemon/plugin_subsystem/drift_event_publisher.py."
        )

    def test_migration_declares_all_three_expected_indexes(self):
        """The SQLite migration MUST declare exactly the three canonical
        index names. The migration's CREATE INDEX statements are the
        sole source of truth for the SQLite path; SQLite never sees
        ``__table_args__`` because ``SQLModel.metadata.create_all``
        just runs ``CREATE TABLE`` and the migration runner is the one
        that emits the indexes on SQLite."""
        names = set(_migration_index_names())
        assert names == set(EXPECTED_INDEX_NAMES), (
            f"SQLite migration declares {sorted(names)}, "
            f"expected {sorted(EXPECTED_INDEX_NAMES)}. "
            f"The migration's CREATE INDEX IF NOT EXISTS statements "
            f"are the sole SQLite path; add the missing statement."
        )

    def test_ensure_block_declares_all_three_expected_indexes(self):
        """The ``_ensure_postgres_columns`` ensure block MUST declare
        exactly the three canonical index names. This is the path
        that runs against EXISTING PG databases at every daemon boot —
        the exact path live PG needs to actually receive the indexes
        that v0.18.0 forgot to ship."""
        names = set(_ensure_block_index_names())
        assert names == set(EXPECTED_INDEX_NAMES), (
            f"_ensure_postgres_columns ensure block declares "
            f"{sorted(names)}, expected {sorted(EXPECTED_INDEX_NAMES)}. "
            f"The ensure block is the only path that creates the "
            f"indexes on existing PG databases — live PG is in the "
            f"'no indexes' state today because this block was missing."
        )

    def test_model_and_migration_agree_on_index_names(self):
        """The model and the migration MUST declare the same index
        name set — the dual-driver contract. If they diverge, one
        dialect ships without an index that the other dialect has
        (exactly the v0.18.0 regression). This is the loud-fail
        guard."""
        model_names = set(_model_index_names())
        migration_names = set(_migration_index_names())
        assert model_names == migration_names, (
            f"drift detected: model declares {sorted(model_names)}, "
            f"migration declares {sorted(migration_names)}. "
            f"The dual-driver contract is 'same name set on both "
            f"dialects'. Rename the diverging index in BOTH files."
        )

    def test_model_and_migration_agree_on_index_columns(self):
        """Beyond names, the (name -> columns) mapping MUST match
        between the model and the migration. The model uses
        ``Index(name, col1, col2, ...)`` and the migration uses
        ``CREATE INDEX ON table(col1, col2, ...)``; both render the
        same column list. Drift here means the index covers different
        columns on each dialect — silent query-shape mismatch."""
        model_cols = _model_index_columns()
        migration_cols = _migration_index_columns()
        assert set(model_cols.keys()) == set(migration_cols.keys()), (
            f"name mismatch (caught by earlier test) — "
            f"model {set(model_cols)}, migration {set(migration_cols)}"
        )
        for name in EXPECTED_INDEX_NAMES:
            assert model_cols[name] == migration_cols[name], (
                f"column-list drift on index {name}: "
                f"model {model_cols[name]}, migration {migration_cols[name]}"
            )

    def test_ensure_block_and_migration_agree_on_index_names(self):
        """The ``_ensure_postgres_columns`` ensure block and the
        SQLite migration MUST declare the same index name set. The
        migration is canonical for SQLite; the ensure block is
        canonical for existing PG. If they diverge, fresh PG
        (via ``create_all``) ships indexes that existing PG (via
        the ensure block) does NOT ship, or vice versa."""
        ensure_names = set(_ensure_block_index_names())
        migration_names = set(_migration_index_names())
        assert ensure_names == migration_names, (
            f"drift detected: ensure block declares {sorted(ensure_names)}, "
            f"migration declares {sorted(migration_names)}."
        )


# ─────────────────────────────────────────────────────────────────────────────
# Idempotency: present = no-op, absent = created
# ─────────────────────────────────────────────────────────────────────────────


class TestDriftEventIndexIdempotency:
    """The ``CREATE INDEX IF NOT EXISTS`` statements in both the
    migration and the ``_ensure_postgres_columns`` block MUST be
    idempotent. Live PG today is in the 'absent' state — the
    migration / ensure path needs to BUILD the indexes. After it
    builds them, subsequent runs must be clean no-ops (present =
    no-op) so a daemon restart doesn't fail or warn.

    The migration uses ``CREATE INDEX IF NOT EXISTS`` (a SQLite-
    supported form) and the ``_ensure_postgres_columns`` block uses
    the same form — so the idempotency contract is identical on both
    dialects for these statements. SQLite is the test-friendly proxy.
    """

    def _expected_create_index_statements(self) -> list[str]:
        """Return the three CREATE INDEX IF NOT EXISTS statements as
        they appear in the migration (verbatim, in order)."""
        content = _MIGRATION_SQL.read_text(encoding="utf-8")
        up_match = re.search(r"--\s*UP\s*\n(.*?)(?=--\s*DOWN|$)", content, re.DOTALL)
        assert up_match, f"failed to parse UP section in {_MIGRATION_SQL}"
        up_section = up_match.group(1)
        pattern = re.compile(
            r"CREATE INDEX IF NOT EXISTS \w+\s+ON\s+drift_events\s*\([^)]+\)\s*;?",
        )
        return pattern.findall(up_section)

    def _inspect_indexes(self, engine: Engine) -> dict[str, tuple[str, ...]]:
        """Return ``{index_name: frozenset(columns)}`` for every index
        on the ``drift_events`` table. Frozensets for order-independent
        comparison (SQLite may return columns in any order)."""
        inspector = inspect(engine)
        raw = inspector.get_indexes("drift_events")
        return {i["name"]: frozenset(i["column_names"]) for i in raw}

    def test_create_indexes_idempotent_when_already_present(
        self, fresh_sqlite_engine_with_table
    ):
        """Running the CREATE INDEX IF NOT EXISTS statements against
        a table that ALREADY has the indexes (via ``create_all`` +
        ``__table_args__``) MUST succeed cleanly — no exception, no
        abort, just a no-op for each statement.

        This is the "subsequent boot" case: live PG will get the
        indexes on the FIRST boot after the fix; every boot after
        that must be a clean no-op (the daemon restart loop depends
        on this)."""
        engine = fresh_sqlite_engine_with_table
        # Sanity: ``create_all`` emitted all three indexes.
        before = self._inspect_indexes(engine)
        assert set(before.keys()) == set(EXPECTED_INDEX_NAMES), (
            f"create_all did not emit all three indexes — before state: "
            f"{sorted(before.keys())}. The model's __table_args__ is "
            f"the canonical source for fresh databases."
        )

        # Re-execute every CREATE INDEX IF NOT EXISTS statement. Each
        # must succeed silently — no exception, no abort. This is the
        # idempotency assertion.
        statements = self._expected_create_index_statements()
        assert len(statements) == len(EXPECTED_INDEX_NAMES), (
            f"expected {len(EXPECTED_INDEX_NAMES)} CREATE INDEX "
            f"statements, found {len(statements)}. Migration file "
            f"shape has drifted — update the test helper."
        )
        with engine.begin() as conn:
            for stmt in statements:
                conn.execute(text(stmt))  # MUST be a no-op, not raise

        # Same state afterward — indexes still present, no extras.
        after = self._inspect_indexes(engine)
        assert after == before, (
            f"idempotent re-run changed index set: "
            f"before {before}, after {after}"
        )

    def test_create_indexes_builds_missing_indexes(
        self, fresh_sqlite_engine_table_only_no_indexes
    ):
        """Running the CREATE INDEX IF NOT EXISTS statements against
        a table that is MISSING the indexes MUST build them — this
        is the live-PG regression state today.

        The fixture deliberately omits the indexes (mimics the live
        PG state). After running the statements, all three indexes
        must exist with the expected column sets."""
        engine = fresh_sqlite_engine_table_only_no_indexes
        # Sanity: zero indexes on the hand-built table.
        before = self._inspect_indexes(engine)
        assert before == {}, (
            f"fixture is broken: expected zero indexes, found {before}"
        )

        # Run the CREATE INDEX IF NOT EXISTS statements — this is the
        # migration path AND the ``_ensure_postgres_columns`` block
        # path. Same statements, same idempotency guarantee.
        statements = self._expected_create_index_statements()
        assert len(statements) == len(EXPECTED_INDEX_NAMES), (
            f"expected {len(EXPECTED_INDEX_NAMES)} CREATE INDEX "
            f"statements, found {len(statements)}"
        )
        with engine.begin() as conn:
            for stmt in statements:
                conn.execute(text(stmt))

        # Verify all three indexes are now present with the expected
        # column sets.
        after = self._inspect_indexes(engine)
        assert set(after.keys()) == set(EXPECTED_INDEX_NAMES), (
            f"ensure path built indexes {sorted(after.keys())}, "
            f"expected {sorted(EXPECTED_INDEX_NAMES)}."
        )
        for name in EXPECTED_INDEX_NAMES:
            expected_cols = frozenset(EXPECTED_INDEX_COLUMNS[name])
            assert after[name] == expected_cols, (
                f"index {name} has columns {set(after[name])}, "
                f"expected {set(expected_cols)}"
            )

    def test_ensure_block_statements_are_byte_identical_to_migration(
        self,
    ):
        """The CREATE INDEX IF NOT EXISTS statements in the
        ``_ensure_postgres_columns`` ensure block MUST be byte-
        identical (same column lists, same names) to those in the
        SQLite migration. Both paths converge on the same final
        state — the test guards the contract by string comparison.

        Equality is structural (same set of (name, columns) pairs)
        so a future cosmetic change to whitespace does not break
        the guard."""
        # Migration side.
        content = _MIGRATION_SQL.read_text(encoding="utf-8")
        up_match = re.search(r"--\s*UP\s*\n(.*?)(?=--\s*DOWN|$)", content, re.DOTALL)
        assert up_match, f"failed to parse UP section in {_MIGRATION_SQL}"
        up_section = up_match.group(1)
        # Ensure-block side — bound by the same sentinels as
        # ``_ensure_block_index_names`` so a log line containing
        # ``CREATE INDEX IF NOT EXISTS is idempotent`` does not
        # contaminate the comparison.
        start_marker = "Slice ⑥ drift_events index parity"
        end_marker = "def _ensure_postgres_drop_legacy_columns"
        ensure_content = _MANAGER_PY.read_text(encoding="utf-8")
        block_start = ensure_content.find(start_marker)
        block_end = ensure_content.find(end_marker, block_start)
        assert block_start != -1 and block_end != -1, (
            f"ensure block markers not found in {_MANAGER_PY}"
        )
        block = ensure_content[block_start:block_end]
        tuple_open_marker = "drift_index_statements = ("
        tuple_open_idx = block.find(tuple_open_marker)
        assert tuple_open_idx != -1, (
            f"drift_index_statements tuple opener not found in the "
            f"drift_events ensure block — refactor broke the tuple "
            f"shape; pin it back."
        )
        tuple_body = block[tuple_open_idx:]
        tuple_lines = tuple_body.splitlines()
        close_line_idx = None
        for i, line in enumerate(tuple_lines):
            if i == 0:
                continue
            if line.strip() == ")":
                close_line_idx = i
                break
        assert close_line_idx is not None, (
            f"tuple-close ')' line not found in drift_index_statements "
            f"tuple — refactor broke the tuple shape; pin it back."
        )
        block_only = "\n".join(tuple_lines[: close_line_idx + 1])
        # Normalize whitespace inside the parenthesized column list
        # so the migration's ``(plugin, divergence_id)`` and the
        # ensure block's ``(plugin,\n    divergence_id)`` compare
        # equal.
        def _norm(match: re.Match[str]) -> str:
            name = match.group(1)
            cols = " ".join(c.strip() for c in match.group(2).split(","))
            return f"CREATE INDEX IF NOT EXISTS {name} ON drift_events ({cols})"

        # Pattern tolerates quoted-string continuations (the ensure
        # block uses Python implicit string concatenation across
        # lines — see the "Copy" + "ON" pattern in the tuple body).
        pattern = re.compile(
            r"CREATE INDEX IF NOT EXISTS (\w+)[\s'\"]+ON[\s'\"]+drift_events\s*\(([^)]+)\)"
        )
        ensure_pairs_normalized = {
            _norm(m) for m in pattern.finditer(block_only)
        }
        migration_pairs_normalized = {
            _norm(m) for m in pattern.finditer(up_section)
        }
        assert ensure_pairs_normalized == migration_pairs_normalized, (
            f"ensure-block CREATE INDEX statements differ from migration: "
            f"ensure-only {sorted(ensure_pairs_normalized - migration_pairs_normalized)}, "
            f"migration-only {sorted(migration_pairs_normalized - ensure_pairs_normalized)}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Smoke: model emits all three indexes via create_all
# ─────────────────────────────────────────────────────────────────────────────


class TestCreateAllEmitsDriftEventIndexes:
    """``SQLModel.metadata.create_all`` is the path that emits schema
    on FRESH PG databases (and on FRESH SQLite at startup). The fix
    requires that this path emits the three indexes — the model's
    ``__table_args__`` is the canonical source for that emit. This
    test guards the verification mechanism on SQLite (a faithful
    proxy — ``Index(...)`` declarations render identically on both
    dialects for the create_all emit; the only thing that differs
    per-dialect is the index method, which we don't use here)."""

    def test_create_all_emits_three_secondary_indexes(self):
        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        try:
            DriftEvent.__table__.create(engine, checkfirst=True)
            inspector = inspect(engine)
            raw = inspector.get_indexes("drift_events")
            names = {i["name"] for i in raw}
            assert names == set(EXPECTED_INDEX_NAMES), (
                f"create_all emitted indexes {sorted(names)}, "
                f"expected {sorted(EXPECTED_INDEX_NAMES)}. "
                f"If the model's __table_args__ is correct, create_all "
                f"must emit all three; if create_all drops one, "
                f"the Index declaration is broken (e.g. wrong name)."
            )
        finally:
            engine.dispose()

    def test_create_all_emits_correct_columns_per_index(self):
        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        try:
            DriftEvent.__table__.create(engine, checkfirst=True)
            inspector = inspect(engine)
            raw = inspector.get_indexes("drift_events")
            for entry in raw:
                name = entry["name"]
                cols = frozenset(entry["column_names"])
                expected = frozenset(EXPECTED_INDEX_COLUMNS[name])
                assert cols == expected, (
                    f"create_all emitted index {name} on columns "
                    f"{set(cols)}, expected {set(expected)}"
                )
        finally:
            engine.dispose()