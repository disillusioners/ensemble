"""F6-a / F6-e / F6-c-static pins — the idempotent mission mint (db-pool-idle-txn).

Lane: ``fix/db-pool-idle-txn`` (job ``634c38f2``). Contract:
``final-implementation-spec.md`` §1 F6.1–F6.8 and §5.2.

What these pins assert:

* **F6-a** — the boot mint uses an **upsert** with
  ``on_conflict_do_nothing`` keyed on the ANCHOR; on ``rowcount == 0``
  it re-resolves via the **anchor-scoped** probe
  (``find_open_epoch_by_anchor``) — and the sequential double-mint
  returns the SAME ``mission_id`` without raising.
* **F6-e** — the CONCRETE F6-subject mutation: the conflict-target
  predicate must stay **byte-identical** to the partial unique index's
  predicate.
* **F6-c-static** — ``OPEN_EPOCH_STATUS_SQL`` is byte-identical to the
  predicate at the model's ``Index(...)`` declaration AND to the
  migration ``20261025_140000_add_mission_open_epoch_unique_index.sql``.

Plantable mutations:

* replace the upsert with the bare ORM insert ⇒ RED (F6-a);
* change the re-resolve key from the anchor to ``founding_message_id``
  ⇒ RED (F6-a);
* change ``status = 'open'`` to ``status = "OPEN"`` at
  ``daemon/repositories/mission_record/models.py`` ⇒ RED (F6-e /
  F6-c-static).

Run (house form)::

    cd /home/nea/ensemble-src-wt-dbpool-idletxn && \
    env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD \
        -u POSTGRES_HOST -u POSTGRES_PORT -u DAEMON_PORT \
    HOME="$(mktemp -d)" \
    /home/nea/ensemble-src/.venv/bin/python -m pytest \
        tests/unit/tools/test_dbpool_mission_mint.py --tb=short -q
"""
from __future__ import annotations

import ast
import re
import tempfile
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlmodel import SQLModel, create_engine

from daemon.repositories.mission_record.models import (
    OPEN_EPOCH_STATUS_SQL,
    MissionRecord,
)
from daemon.repositories.mission_record.repository import MissionRecordRepository

#: The pack-local declared collected count (NEW packs only).
EXPECTED_COLLECTED = 8

_REPO_ROOT = Path(__file__).resolve().parents[3]
_REPO_PATH = _REPO_ROOT / "daemon" / "repositories" / "mission_record" / "repository.py"
_MODELS_PATH = _REPO_ROOT / "daemon" / "repositories" / "mission_record" / "models.py"
_MIGRATION_PATH = (
    _REPO_ROOT
    / "daemon"
    / "migrations"
    / "versions"
    / "20261025_140000_add_mission_open_epoch_unique_index.sql"
)


@pytest.fixture()
def mint_engine():
    """A file-backed SQLite engine (file-backed so concurrent writers
    share ONE database — an in-memory engine is per-connection)."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "mission_mint.db"
        engine = create_engine(f"sqlite:///{path}")
        SQLModel.metadata.create_all(engine, tables=[MissionRecord.__table__])
        try:
            yield engine
        finally:
            engine.dispose()


def _function_source(path: Path, name: str) -> str:
    source = path.read_text()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(source, node) or ""
    raise AssertionError(f"function {name} not found in {path}")


class TestF6aUpsertShape:
    """F6-a — the upsert is anchor-keyed and re-resolves on the
    ``rowcount == 0`` path."""

    def test_insert_mission_uses_on_conflict_do_nothing(self):
        source = _function_source(_REPO_PATH, "insert_mission")
        assert "on_conflict_do_nothing" in source, (
            "F6-a: the boot mint is not an upsert — a bare INSERT loses "
            "the concurrent double-mint race with an IntegrityError"
        )
        assert 'index_elements=["anchor_instance_id"]' in source, (
            "F6-a: the conflict target must be the anchor (the partial "
            "unique index's own key)"
        )
        assert "index_where=text(OPEN_EPOCH_STATUS_SQL)" in source, (
            "F6-a: the conflict target must carry the open-epoch "
            "predicate (byte-identical to the partial index)"
        )

    def test_loser_path_resolves_by_anchor(self):
        source = _function_source(_REPO_PATH, "insert_mission")
        # The LOSER SIGNAL: ``RETURNING mission_id`` — the winner gets
        # one returned row, the loser gets none. It is used INSTEAD of
        # ``rowcount`` because psycopg3 reports ``rowcount = -1``
        # ("unknown") for ``INSERT ... ON CONFLICT DO NOTHING``
        # (measured on PostgreSQL 16 in the db-pool-idle-txn Phase-1
        # PG leg) — the affected-row count is simply not available on
        # that path, so a ``rowcount == 0`` test can never fire.
        assert ".returning(" in source, (
            "F6-a: the upsert has no loser signal — an INSERT ... ON "
            "CONFLICT DO NOTHING without RETURNING cannot distinguish "
            "the winner from the loser (psycopg3 reports rowcount=-1)"
        )
        assert "won_the_race" in source, (
            "F6-a: the upsert's loser path is missing"
        )
        assert "find_open_epoch_by_anchor" in source, (
            "F6-a: the loser path must re-resolve via the ANCHOR-scoped "
            "probe (a NULL-founding boot epoch is invisible to the "
            "founding-keyed probe — D-09 amended)"
        )
        assert "find_epoch_by_anchor_and_founding_message" not in source, (
            "F6-a: the re-resolve key is still founding_message_id — the "
            "boot path's probe was SKIPPED for NULL-founding epochs, "
            "never mis-keyed (DR-33)"
        )

    def test_sequential_double_mint_returns_the_same_mission_id(self, mint_engine):
        repo = MissionRecordRepository(mint_engine)
        first = repo.insert_mission(
            MissionRecord(
                anchor_instance_id="anchor-f6",
                founding_source="boot",
                epoch_seq=1,
            )
        )
        second = repo.insert_mission(
            MissionRecord(
                anchor_instance_id="anchor-f6",
                founding_source="boot",
                epoch_seq=2,
            )
        )
        assert first.mission_id == second.mission_id, (
            "F6-a: the sequential double-mint returned a DIFFERENT "
            "mission_id — the mint is not idempotent"
        )
        with mint_engine.connect() as conn:
            count = conn.execute(
                text(
                    "select count(*) from mission_records "
                    "where anchor_instance_id = :a"
                ),
                {"a": "anchor-f6"},
            ).scalar()
        assert count == 1, (
            f"F6-a: {count} open-epoch rows exist for one anchor — the "
            "upsert minted twice"
        )


class TestF6eConflictPredicate:
    """F6-e — the conflict-target predicate must stay byte-identical to
    the partial unique index's predicate."""

    def test_open_epoch_status_sql_is_the_canonical_predicate(self):
        assert OPEN_EPOCH_STATUS_SQL == "status = 'open'", (
            "F6-e: OPEN_EPOCH_STATUS_SQL drifted from the partial index's "
            "predicate — the upsert's conflict target would no longer "
            "byte-match the index and the mint would stop being "
            "idempotent"
        )

    def test_migration_predicate_is_byte_identical(self):
        migration = _MIGRATION_PATH.read_text()
        match = re.search(
            r"CREATE UNIQUE INDEX IF NOT EXISTS ux_mission_records_anchor_open\s+"
            r"ON mission_records\(anchor_instance_id\)\s+WHERE\s+(.+?);",
            migration,
            re.S,
        )
        assert match is not None, (
            "F6-e: the migration no longer declares the open-epoch "
            "partial unique index"
        )
        migration_predicate = " ".join(match.group(1).split())
        assert migration_predicate == OPEN_EPOCH_STATUS_SQL, (
            "F6-e: the migration's index predicate "
            f"({migration_predicate!r}) is not byte-identical to "
            f"OPEN_EPOCH_STATUS_SQL ({OPEN_EPOCH_STATUS_SQL!r})"
        )


class TestF6cStaticIndexPredicate:
    """F6-c-static — the model's index predicate mirrors the constant."""

    def test_model_index_uses_the_shared_constant(self):
        source = _MODELS_PATH.read_text()
        assert "ux_mission_records_anchor_open" in source, (
            "F6-c-static: the open-epoch partial unique index is gone from "
            "the model"
        )
        assert "sqlite_where=text(OPEN_EPOCH_STATUS_SQL)" in source, (
            "F6-c-static: the SQLite index predicate must cite the shared "
            "constant"
        )
        assert "postgresql_where=text(OPEN_EPOCH_STATUS_SQL)" in source, (
            "F6-c-static: the PostgreSQL index predicate must cite the "
            "shared constant"
        )

    def test_no_literal_open_predicate_beside_the_constant(self):
        """The predicate text lives in ONE place (the constant) — a
        second CODE copy is how the two drift apart. Docstrings and
        comments are prose, not code, and are not counted."""
        source = _MODELS_PATH.read_text()
        tree = ast.parse(source)
        docstring_nodes = set()
        constant_value_nodes = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                # A docstring — prose, not a code copy.
                docstring_nodes.add(id(node.value))
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == "OPEN_EPOCH_STATUS_SQL":
                        constant_value_nodes.add(id(node.value))
        pattern = re.compile(r"status\s*=\s*['\"]open['\"]")
        code_copies = [
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and pattern.search(node.value)
            and id(node) not in docstring_nodes
            and id(node) not in constant_value_nodes
        ]
        assert not code_copies, (
            "F6-e: a second CODE copy of the open-epoch predicate literal "
            f"exists in models.py: {code_copies!r} — the constant is the "
            "single source of truth"
        )
