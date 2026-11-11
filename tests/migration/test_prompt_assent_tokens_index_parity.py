"""``prompt_assent_tokens`` index parity tests (phase1-plan task 1.3).

Compact mirror of ``tests/migration/test_prompt_versions_index_parity.py``
(the prompt_versions Phase-A parity guard), scaled to the
prompt-assent surface: a brand-new table with NO
``_ensure_postgres_columns`` block (PostgreSQL receives it via
``SQLModel.metadata.create_all`` — the notify_audit / instance_ops
precedent), so the dual-driver parity contract is TWO-way:

  (a) the ``PromptAssentToken`` model's ``__table_args__`` declarations —
      the canonical source for the PG ``create_all`` path — vs
  (b) the SQLite migration's ``CREATE [UNIQUE] INDEX`` statements —
      ``daemon/migrations/versions/20261110_221500_create_prompt_assent_tokens.sql``.

Pins:

* every named index declared on one side exists on the other with the
  same ordered column list (byte-identical NAMES — the acceptance
  line);
* the PARTIAL unique fence parity: the model's
  ``uq_prompt_assent_tokens_live_version`` (``(version_id) WHERE
  status='live'``) vs the migration's same-named partial UNIQUE INDEX;
* a fresh in-memory SQLite ``create_all`` emits exactly the named index
  set — and NOT the default-named ``ix_prompt_assent_tokens_*`` family;
* the migration's ``CREATE ... INDEX IF NOT EXISTS`` statements are
  idempotent (re-runnable against an already-created database);
* UP-first / DOWN-last ordering.

Test markers::

    pytest tests/migration/test_prompt_assent_tokens_index_parity.py -v
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

# Importing the models is REQUIRED to register ``prompt_assent_tokens``
# on ``SQLModel.metadata`` — same convention as the prompt_versions
# parity test.
from daemon.repositories.prompt_assent.models import (  # noqa: F401
    PromptAssentToken,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MIGRATION_SQL = (
    _REPO_ROOT
    / "daemon/migrations/versions/20261110_221500_create_prompt_assent_tokens.sql"
)

# Canonical index sets — any change MUST be reflected in BOTH:
#   - daemon/repositories/prompt_assent/models.py (__table_args__,
#     the canonical source)
#   - daemon/migrations/versions/20261110_221500_create_prompt_assent_tokens.sql
EXPECTED_NON_UNIQUE_INDEXES: dict[str, tuple[str, ...]] = {
    "idx_prompt_assent_tokens_version_status": ("version_id", "status"),
}

EXPECTED_UNIQUE_INDEXES: dict[str, tuple[str, tuple[str, ...], str | None]] = {
    "uq_prompt_assent_tokens_token_hash": (
        "token_hash",
        ("token_hash",),
        None,
    ),
    "uq_prompt_assent_tokens_live_version": (
        "version_id",
        ("version_id",),
        "status = 'live'",
    ),
}


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _migration_up_section() -> str:
    content = _MIGRATION_SQL.read_text(encoding="utf-8")
    match = re.search(r"--\s*UP\s*\n(.*?)(?=--\s*DOWN|$)", content, re.DOTALL)
    assert match, f"failed to parse UP section in {_MIGRATION_SQL}"
    return match.group(1)


def _migration_down_section() -> str:
    content = _MIGRATION_SQL.read_text(encoding="utf-8")
    match = re.search(r"--\s*DOWN\s*\n(.*)$", content, re.DOTALL)
    assert match, f"failed to parse DOWN section in {_MIGRATION_SQL}"
    return match.group(1)


def _migration_non_unique_indexes() -> dict[str, tuple[str, ...]]:
    pattern = re.compile(
        rf"CREATE INDEX IF NOT EXISTS (\w+)\s+ON\s+prompt_assent_tokens\s*\(([^)]+)\)"
    )
    return {
        m.group(1): tuple(c.strip() for c in m.group(2).split(","))
        for m in pattern.finditer(_migration_up_section())
    }


def _migration_unique_indexes() -> dict[str, tuple[str, tuple[str, ...], str | None]]:
    pattern = re.compile(
        rf"CREATE UNIQUE INDEX IF NOT EXISTS (\w+)\s+ON\s+prompt_assent_tokens\s*\(([^)]+)\)"
        rf"(?:\s*WHERE\s+([^;\n]+))?;"
    )
    out: dict[str, tuple[str, tuple[str, ...], str | None]] = {}
    for m in pattern.finditer(_migration_up_section()):
        where = m.group(3).strip() if m.group(3) else None
        out[m.group(1)] = (
            m.group(1),
            tuple(c.strip() for c in m.group(2).split(",")),
            where,
        )
    return out


def _model_indexes() -> dict[str, tuple[str, ...]]:
    table = SQLModel.metadata.tables["prompt_assent_tokens"]
    return {i.name: tuple(c.name for c in i.columns) for i in table.indexes}


def _model_unique_partial() -> dict[str, tuple[str, tuple[str, ...], str]]:
    table = SQLModel.metadata.tables["prompt_assent_tokens"]
    out: dict[str, tuple[str, tuple[str, ...], str]] = {}
    for i in table.indexes:
        if not i.unique:
            continue
        ddl = str(
            __import__("sqlalchemy.schema", fromlist=["CreateIndex"])
            .CreateIndex(i)
            .compile(dialect=__import__("sqlalchemy.dialects", fromlist=["sqlite"]).sqlite.dialect())
        ).replace('"', "")
        m = re.search(r"WHERE\s+(.+)$", ddl, re.IGNORECASE)
        out[i.name] = (
            i.name,
            tuple(c.name for c in i.columns),
            m.group(1).strip() if m else "",
        )
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def fresh_sqlite_engine():
    """Fresh in-memory SQLite with the canonical schema via create_all."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


# ─────────────────────────────────────────────────────────────────────────────
# Parity
# ─────────────────────────────────────────────────────────────────────────────


class TestPromptAssentTokensIndexParity:
    def test_migration_declares_expected_indexes(self):
        assert _migration_non_unique_indexes() == EXPECTED_NON_UNIQUE_INDEXES

    def test_model_declares_expected_indexes(self):
        table = SQLModel.metadata.tables["prompt_assent_tokens"]
        observed = {
            i.name: tuple(c.name for c in i.columns)
            for i in table.indexes
            if not i.unique
        }
        assert observed == EXPECTED_NON_UNIQUE_INDEXES

    def test_migration_unique_indexes_match_model(self):
        mig = _migration_unique_indexes()
        assert set(mig) == set(EXPECTED_UNIQUE_INDEXES)
        for name, (_, cols, where) in mig.items():
            expected = EXPECTED_UNIQUE_INDEXES[name]
            assert cols == expected[1]
            assert (where or "").replace(" ", "") == (
                expected[2] or ""
            ).replace(" ", "")

    def test_model_unique_indexes_match_migration(self):
        model = _model_unique_partial()
        assert set(model) == set(EXPECTED_UNIQUE_INDEXES)
        for name, (_, cols, where) in model.items():
            expected = EXPECTED_UNIQUE_INDEXES[name]
            assert cols == expected[1]
            assert where.replace(" ", "") == (expected[2] or "").replace(" ", "")

    def test_fresh_create_all_emits_named_indexes_not_ix_family(
        self, fresh_sqlite_engine
    ):
        names = {
            i["name"]
            for i in inspect(fresh_sqlite_engine).get_indexes(
                "prompt_assent_tokens"
            )
        }
        unique_names = {
            u["name"]
            for u in inspect(fresh_sqlite_engine).get_unique_constraints(
                "prompt_assent_tokens"
            )
        }
        observed = names | unique_names
        assert set(EXPECTED_NON_UNIQUE_INDEXES) <= observed
        assert set(EXPECTED_UNIQUE_INDEXES) <= observed
        assert not any(
            n.startswith("ix_prompt_assent_tokens_") for n in observed
        ), (
            "default-named index=True indexes reappeared — the explicit "
            "__table_args__ declarations are the parity contract"
        )

    def test_migration_index_statements_idempotent(
        self, fresh_sqlite_engine
    ):
        statements = [
            s.strip()
            for s in _migration_up_section().split(";")
            if "CREATE" in s.upper() and "INDEX" in s.upper()
        ]
        assert len(statements) == (
            len(EXPECTED_NON_UNIQUE_INDEXES) + len(EXPECTED_UNIQUE_INDEXES)
        )
        with fresh_sqlite_engine.begin() as conn:
            for statement in statements:
                conn.execute(text(statement))  # must not raise (2nd apply)

    def test_up_first_down_last(self):
        up_pos = re.search(r"^-- UP\s*$", _MIGRATION_SQL.read_text(encoding="utf-8"), re.MULTILINE)
        down_pos = re.search(r"^-- DOWN\s*$", _MIGRATION_SQL.read_text(encoding="utf-8"), re.MULTILINE)
        assert up_pos and down_pos and up_pos.start() < down_pos.start()
        down = _migration_down_section()
        # DOWN drops in reverse order and is idempotent.
        assert "DROP INDEX IF EXISTS" in down
        assert "DROP TABLE IF EXISTS prompt_assent_tokens" in down

    def test_migration_columns_match_model_columns(self):
        """Column names byte-identical, model = migration (both drivers)."""
        model_cols = [
            c.name
            for c in SQLModel.metadata.tables["prompt_assent_tokens"].columns
        ]
        create_table = re.search(
            r"CREATE TABLE IF NOT EXISTS prompt_assent_tokens \((.*?)\);",
            _migration_up_section(),
            re.DOTALL,
        )
        assert create_table, "table CREATE not found in the UP section"
        mig_cols = [
            m.group(1)
            for m in re.finditer(r"^\s+(\w+) (?:TEXT|INTEGER)", create_table.group(1), re.MULTILINE)
        ]
        assert mig_cols == model_cols, (
            "migration and model column sets diverged — the .sql file is "
            "the SQLite record and the model is the PG source; they must "
            "stay byte-identical"
        )
