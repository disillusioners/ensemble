"""secrets + secrets_audit index / unique-constraint parity tests.

Tidier pass #2 parity pin (2026-10-17, governor pre-promote
verification round). The ``Secret`` model historically
declared BOTH a ``Field(unique=True)`` on the ``name`` column
AND a named ``UniqueConstraint(\"name\", name=\"uq_secrets_name\")``
in ``__table_args__`` — PG ``create_all`` mints TWO
constraints in that layout, while the SQLite migration
declares only the inline ``UNIQUE`` (the F15 dual-emission
regression — ``daemon/repositories/project/models.py:334-340``
removed exactly the same pattern). This parity test pins the
post-tidy invariant: **exactly one** named UNIQUE constraint
on ``name``, name = ``uq_secrets_name``, on a fresh PG-shaped
``create_all``.

Dual-driver parity contract (matches the prompt_versions /
stored_watches precedents at
``tests/migration/test_prompt_versions_index_parity.py``
and ``tests/migration/test_stored_watches_index_parity.py``):

  (a) the ``Secret`` model's ``__table_args__`` — the
      canonical source for the PG ``create_all`` path, vs
  (b) the SQLite migration's inline ``UNIQUE`` declaration
      on ``secrets.name`` at
      ``daemon/migrations/versions/20261017_000001_create_secrets_tables.sql``.

Pins:

* exactly ONE named ``UniqueConstraint(\"name\",)`` on the
  ``secrets`` table after a fresh ``create_all`` (the dual-
  emission guard);
* the named constraint is ``uq_secrets_name`` — the parity
  marker with the migration's inline ``UNIQUE``;
* the column set of the named constraint is exactly
  ``(\"name\",)``;
* a fresh in-memory SQLite ``create_all`` reports EXACTLY ONE
  unique constraint on the ``secrets`` table whose name is
  ``uq_secrets_name`` (the promotion-immutable schema
  record).
* the migration declares ``UNIQUE`` inline on the
  ``name`` column (parity with the model's named
  ``UniqueConstraint`` — SQLite : PG agreement).

Test markers::

    pytest tests/migration/test_secrets_index_parity.py -v
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

# Importing the models is REQUIRED to register ``secrets`` +
# ``secrets_audit`` on ``SQLModel.metadata`` — without this
# the reflections below see an empty metadata and fail. Same
# convention as the drift_events / stored_watches /
# prompt_versions parity tests (import every model module
# the assertions reflect on).
from daemon.repositories.secrets.models import (  # noqa: F401
    SECRET_AUDIT_ACTIONS,
    Secret,
    SecretAuditEvent,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MIGRATION_SQL = (
    _REPO_ROOT
    / "daemon/migrations/versions/20261017_000001_create_secrets_tables.sql"
)

# New-row INSERT UNIQUE fence — the canonical name carried
# by both the model's named ``UniqueConstraint`` and the
# migration's inline ``UNIQUE`` on ``secrets.name``. The
# parity-record contract: a PG ``create_all`` on the model
# MUST emit exactly this-named constraint, and the SQLite
# migration MUST declare exactly this-named UNIQUE on the
# same column.
EXPECTED_SECRETS_UQ_NAME = "uq_secrets_name"
EXPECTED_SECRETS_UQ_COLUMNS = ("name",)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _migration_up_section() -> str:
    content = _MIGRATION_SQL.read_text(encoding="utf-8")
    match = re.search(r"--\s*UP\s*\n(.*?)(?=--\s*DOWN|$)", content, re.DOTALL)
    assert match, f"failed to parse UP section in {_MIGRATION_SQL}"
    return match.group(1)


def _migration_inline_unique_columns() -> dict[str, tuple[str, ...]]:
    """Map table_name -> column-tuple for the migration's
    inline UNIQUE on a column declaration
    (``CREATE TABLE foo (..., col TYPE NOT NULL UNIQUE, ...)``).

    Returns at most one entry for the ``secrets`` table
    because the P1 migration carries a single inline UNIQUE
    on ``name``.
    """
    # Capture: ``CREATE TABLE [IF NOT EXISTS] <name> ( ... <col> <type> ... UNIQUE ... )``.
    pattern = re.compile(
        r"CREATE TABLE(?:\s+IF NOT EXISTS)?\s+(\w+)\s*\((.*?)\)",
        re.DOTALL,
    )
    out: dict[str, tuple[str, ...]] = {}
    for m in pattern.finditer(_migration_up_section()):
        table_name = m.group(1)
        # Inside the column list, find a token sequence
        # ``... UNIQUE`` not followed by ``INDEX`` or
        # ``KEY`` (which would be a table-level UNIQUE
        # INDEX/KEY, not an inline column UNIQUE).
        body = m.group(2)
        for line in body.splitlines():
            token = line.strip()
            if not token:
                continue
            # Strip the trailing comma.
            if token.endswith(","):
                token = token[:-1].strip()
            # Match ``<col> <rest-with-UNIQUE-keyword-trailing>``
            # — e.g. ``name TEXT NOT NULL UNIQUE``. Split
            # on whitespace; the FIRST token is the column
            # name; the line MUST end with ``UNIQUE`` (case
            # insensitive) or contain `` UNIQUE`` as a
            # distinct token.
            parts = token.split()
            if not parts:
                continue
            col = parts[0]
            upper = token.upper()
            # Inline column UNIQUE iff `` UNIQUE`` is a
            # token at end (NOT ``UNIQUE INDEX`` / ``UNIQUE
            # KEY`` / ``UNIQUE CONSTRAINT``).
            if re.search(
                r"\bUNIQUE\b(?!\s+(?:INDEX|KEY|CONSTRAINT))",
                upper,
            ) and (
                upper.endswith("UNIQUE") or upper.endswith("UNIQUE,")
            ):
                cols = out.setdefault(table_name, ())
                out[table_name] = cols + (col,)
    return out


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


def _model_indexes(
    table_name: str,
) -> dict[str, tuple[str, ...]]:
    table = SQLModel.metadata.tables[table_name]
    return {
        i.name: tuple(c.name for c in i.columns) for i in table.indexes
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
# secrets UNIQUE parity — the dual-emission guard
# ─────────────────────────────────────────────────────────────────────────────


class TestSecretsUniqueParity:
    """The PG ``create_all`` path must emit EXACTLY ONE
    named ``UniqueConstraint`` on ``secrets.name``, named
    ``uq_secrets_name``. Pre-tidy-pass-2 the model carried
    BOTH ``Field(unique=True)`` (auto-named inline UNIQUE)
    AND the named ``UniqueConstraint`` in
    ``__table_args__`` — PG created both, contradicting the
    migration's single-constraint record. F15 precedent at
    ``daemon/repositories/project/models.py:334-340``
    removed the same dual emission."""

    def test_model_emits_exactly_one_unique_constraint(self):
        """The promotion-immutable schema record: the model
        declares exactly one ``UniqueConstraint`` on the
        ``secrets`` table. A second (auto-named) inline
        UNIQUE would slip through PG ``create_all`` as a
        phantom index the migration does not carry."""
        constraints = _model_unique_constraints("secrets")
        assert len(constraints) == 1, (
            f"expected exactly one UniqueConstraint on 'secrets'; "
            f"got {sorted(constraints)} (the dual-emission regression "
            f"— see F15 precedent project/models.py:334-340)"
        )

    def test_model_unique_constraint_matches_fence(self):
        """The named ``UniqueConstraint`` matches the
        parity fence — name = ``uq_secrets_name``,
        column = ``name``."""
        constraints = _model_unique_constraints("secrets")
        assert EXPECTED_SECRETS_UQ_NAME in constraints
        assert constraints[EXPECTED_SECRETS_UQ_NAME] == (
            EXPECTED_SECRETS_UQ_COLUMNS
        )

    def test_migration_inline_unique_matches_fence(self):
        """The SQLite migration declares the inline UNIQUE
        on ``secrets.name`` — i.e. the single-constraint
        record the PG ``create_all`` must mirror."""
        inline = _migration_inline_unique_columns()
        assert "secrets" in inline, (
            "migration must carry an inline UNIQUE on "
            "'secrets.name' (the parity record)"
        )
        assert EXPECTED_SECRETS_UQ_COLUMNS in (inline["secrets"],)

    def test_fresh_create_all_emits_exactly_one_unique_constraint(
        self, fresh_sqlite_engine
    ):
        """End-to-end: a fresh in-memory SQLite ``create_all``
        reflects EXACTLY ONE unique constraint on the
        ``secrets`` table, named ``uq_secrets_name``. A
        second (auto-named) constraint here means the
        dual-emission regression slipped back in."""
        observed = {
            u["column_names"][0]: u["name"]
            for u in inspect(fresh_sqlite_engine).get_unique_constraints(
                "secrets"
            )
        }
        # Exactly one inline UNIQUE in the SQLite reflection.
        assert len(observed) == 1, (
            f"expected exactly one UNIQUE constraint on 'secrets' "
            f"after create_all; got {sorted(observed)} — the "
            f"dual-emission regression slipped back in"
        )
        # And the column is ``name``.
        assert observed.get(EXPECTED_SECRETS_UQ_COLUMNS[0]) == (
            EXPECTED_SECRETS_UQ_NAME
        )

    def test_model_index_set_is_intact(self):
        """The P1 partial index ``idx_secrets_kind_active``
        is unchanged by the dual-emission cleanup — pinned
        so future tidy passes don't drop it accidentally."""
        indexes = _model_indexes("secrets")
        assert "idx_secrets_kind_active" in indexes
        assert indexes["idx_secrets_kind_active"] == ("kind",)
