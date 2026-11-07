"""Real-PostgreSQL integration proof for the POK tables (Phase 1 T3).

AC9 — PG MATERIALIZATION: a scratch/dev-PG boot materializes BOTH
POK tables. The operative fresh-PG artifact is the pre-create_all
model registration in ``daemon/manager.py`` (B7) — proven here at the
same seam the manager uses (``SQLModel.metadata.create_all`` over a
registered model set), on the allowlist-fenced scratch database
(the shared ``pg_engine`` fixture: create_all at session setup,
drop_all at teardown — throwaway by construction).

Legs:

* **AC9 materialization** — both ``project_operating_knowledge`` and
  ``project_operating_knowledge_embeddings`` exist in the created
  schema with the FULL §4.1 column set.
* **two-artifact rule** — the SQLite-side ``.sql`` CREATE TABLE text
  and the ``create_all``-built PG table agree column-for-column
  (house rule: the .sql files are the SQLite canonical DDL, the
  models are the PG source-of-truth; drift between them is a
  defect).
* **CHECK vocabulary carry** — the ``audience`` CHECK
  (operation|implementation|both, the ER-1 ruling) and the other
  three CHECK vocabularies exist on the PG table.
* **partial unique index carry** — ``uq_pok_active_slug`` exists on
  PG with the ``WHERE status = 'active'`` predicate, and a
  dual-active insert IntegrityErrors on PG while the supersede
  path succeeds (the structurally-impossible-dual-active guard,
  materialized).
* **NO-FK posture** — neither table carries a foreign key
  (rollback-inert adjudication, materialized).

Run (serial; xdist skip-all enforced by the shared conftest):
``pytest tests/postgres/test_pok_tables_pg.py --override-ini="addopts=" -m postgres``
"""

from __future__ import annotations

import re

import pytest
from sqlalchemy import text
from sqlmodel import SQLModel

# Module-level imports so the lane's models are registered in
# SQLModel.metadata BEFORE the session-scoped pg_engine fixture runs
# create_all (collection-time imports precede session fixtures).
import daemon.repositories.pok.models  # noqa: F401
import daemon.repositories.project_skill_file.models  # noqa: F401
import daemon.repositories.project.models  # noqa: F401


POK_TABLES = (
    "project_operating_knowledge",
    "project_operating_knowledge_embeddings",
)

#: The §4.1 column set, verbatim (readiness constraint 1) PLUS the
#: Phase-2 additive trust-tier column (``source_tier``, trust-tier
#: addendum T-1 — the sanctioned additive schema evolution; the
#: Phase-1 CREATE TABLE text is never amended, the column arrives via
#: ``20261107_110003_add_pok_source_tier.sql`` / ``_ensure_postgres_
#: columns`` / the model field).
POK_EXPECTED_COLUMNS = {
    "id",
    "project_id",
    "slug",
    "domain",
    "audience",
    "audience_agents",
    "body",
    "provenance",
    "status",
    "disputed",
    "superseded_by_id",
    "content_sha256",
    "created_by_agent",
    "created_at",
    "updated_at",
    "last_verified",
    "verify_count",
    "access_count",
    "last_accessed_at",
    "pinned",
    "source_tier",
}

POK_EMBEDDINGS_EXPECTED_COLUMNS = {"id", "entry_id", "embedding", "created_at"}

#: The lane's .sql files (the SQLite-side canonical DDL).
POK_SQL_FILES = (
    "daemon/migrations/versions/20261107_110001_create_project_operating_knowledge.sql",
    "daemon/migrations/versions/20261107_110002_create_project_operating_knowledge_embeddings.sql",
)


def _pg_columns(engine, table: str) -> dict[str, str]:
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_name = :t ORDER BY ordinal_position"
            ),
            {"t": table},
        ).fetchall()
    return {name: dtype for name, dtype in rows}


def _pg_foreign_keys(engine, table: str) -> list[str]:
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT constraint_name FROM information_schema.table_constraints "
                "WHERE table_name = :t AND constraint_type = 'FOREIGN KEY'"
            ),
            {"t": table},
        ).fetchall()
    return [r[0] for r in rows]


def _pg_check_constraints(engine, table: str) -> dict[str, str]:
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conrelid = (SELECT oid FROM pg_class WHERE relname = :t) "
                "AND contype = 'c'"
            ),
            {"t": table},
        ).fetchall()
    return {name: definition for name, definition in rows}


def _pg_indexes(engine, table: str) -> dict[str, str]:
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT indexname, indexdef FROM pg_indexes WHERE tablename = :t"
            ),
            {"t": table},
        ).fetchall()
    return {name: definition for name, definition in rows}


def _pg_exec(engine, statement: str, params: dict | None = None) -> None:
    """One fenced write through a short-lived connection."""
    with engine.begin() as conn:
        conn.execute(text(statement), params or {})


def _sql_create_columns(path: str) -> set[str]:
    """The column names the .sql CREATE TABLE declares (UP section).

    Parses ONLY the first CREATE TABLE's balanced parenthesized body —
    the later CREATE INDEX statements are not column declarations.
    """
    import pathlib

    text_src = pathlib.Path(path).read_text(encoding="utf-8")
    up = text_src.split("-- UP", 1)[1].split("-- DOWN", 1)[0]
    start = up.index("(", up.index("CREATE TABLE"))
    depth = 0
    end = -1
    for i in range(start, len(up)):
        if up[i] == "(":
            depth += 1
        elif up[i] == ")":
            depth -= 1
            if depth == 0:
                end = i
                break
    body = up[start + 1 : end]
    cols: set[str] = set()
    for raw_line in body.splitlines():
        line = raw_line.strip().rstrip(",")
        if not line or line.startswith("--"):
            continue
        if line.upper().startswith(("PRIMARY KEY", "UNIQUE ", "CHECK ", "FOREIGN ", "CONSTRAINT ")):
            continue
        cols.add(line.split()[0])
    return cols


def _sql_additive_columns(table: str) -> set[str]:
    """Column names added to ``table`` by LATER additive migrations
    (``ALTER TABLE <table> ADD COLUMN <name> ...`` in the UP sections
    of ``daemon/migrations/versions/*.sql``). The two-artifact rule
    covers the FULL evolved schema: create-table columns ∪ additive
    ALTER columns (Phase 2 — the trust-tier column arrives this way;
    the checksummed Phase-1 CREATE TABLE is never amended).
    """
    import pathlib

    versions = pathlib.Path("daemon/migrations/versions")
    cols: set[str] = set()
    for path in sorted(versions.glob("*.sql")):
        text_src = path.read_text(encoding="utf-8")
        if "-- DOWN" in text_src:
            up = text_src.split("-- UP", 1)[1].split("-- DOWN", 1)[0]
        else:
            up = text_src
        for raw_line in up.splitlines():
            line = raw_line.strip().rstrip(",").rstrip(";")
            if line.startswith("--"):
                line = line.lstrip("- ").strip()
            m = re.match(
                rf"ALTER TABLE {re.escape(table)} ADD COLUMN (\w+)", line, re.IGNORECASE
            )
            if m:
                cols.add(m.group(1))
    return cols


class TestPokPgMaterialization:
    def test_ac9_both_tables_materialize(self, pg_engine) -> None:
        """AC9: the scratch-PG boot (create_all) lands BOTH tables."""
        with pg_engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT tablename FROM pg_tables WHERE schemaname = 'public' "
                    "AND tablename IN (:a, :b)"
                ),
                {"a": POK_TABLES[0], "b": POK_TABLES[1]},
            ).fetchall()
        found = {r[0] for r in rows}
        assert set(POK_TABLES) <= found, (
            f"AC9 FAIL — missing POK tables on scratch PG: "
            f"{set(POK_TABLES) - found}"
        )

    def test_pok_table_carries_the_41_column_set(self, pg_engine) -> None:
        cols = _pg_columns(pg_engine, POK_TABLES[0])
        assert set(cols) == POK_EXPECTED_COLUMNS, (
            f"column drift: missing={POK_EXPECTED_COLUMNS - set(cols)} "
            f"extra={set(cols) - POK_EXPECTED_COLUMNS}"
        )

    def test_embeddings_table_carries_its_columns(self, pg_engine) -> None:
        cols = _pg_columns(pg_engine, POK_TABLES[1])
        assert set(cols) == POK_EMBEDDINGS_EXPECTED_COLUMNS

    def test_sql_and_pg_agree_column_for_column(self, pg_engine) -> None:
        """Two-artifact rule: the .sql canonical DDL and the PG
        create_all agree on the column sets (drift = defect). The
        SQL side is the FULL EVOLVED schema: the CREATE TABLE columns
        ∪ the additive-ALTER columns (Phase 2 — trust-tier addendum
        T-1; the Phase-1 CREATE TABLE text is never amended)."""
        main_sql_cols = _sql_create_columns(POK_SQL_FILES[0]) | _sql_additive_columns(
            POK_TABLES[0]
        )
        main_pg_cols = set(_pg_columns(pg_engine, POK_TABLES[0]))
        assert main_sql_cols == main_pg_cols, (
            f"main table .sql↔PG drift: sql-only={main_sql_cols - main_pg_cols} "
            f"pg-only={main_pg_cols - main_sql_cols}"
        )
        emb_sql_cols = _sql_create_columns(POK_SQL_FILES[1]) | _sql_additive_columns(
            POK_TABLES[1]
        )
        emb_pg_cols = set(_pg_columns(pg_engine, POK_TABLES[1]))
        assert emb_sql_cols == emb_pg_cols

    def test_audience_check_vocabulary_on_pg(self, pg_engine) -> None:
        """The ER-1 audience CHECK is materialized on PG (and the
        other three vocabularies ride along)."""
        checks = _pg_check_constraints(pg_engine, POK_TABLES[0])
        blob = " ".join(checks.values())
        for value in ("operation", "implementation", "both"):
            assert value in blob, f"audience CHECK missing {value!r}"
        for value in ("artifact", "deploy", "verify", "environment",
                      "catalog", "other"):
            assert value in blob, f"domain CHECK missing {value!r}"
        for value in ("declared", "observed"):
            assert value in blob, f"provenance CHECK missing {value!r}"
        for value in ("active", "superseded", "archived"):
            assert value in blob, f"status CHECK missing {value!r}"
        # Phase 2 (trust-tier addendum T-1): the FIFTH vocabulary
        for value in ("agent", "curated"):
            assert value in blob, f"source_tier CHECK missing {value!r}"

    def test_pg_rejects_out_of_vocab_audience(self, pg_engine) -> None:
        """The audience CHECK actually FIRES on PG."""
        with pytest.raises(Exception):
            _pg_exec(
                pg_engine,
                "INSERT INTO project_operating_knowledge "
                "(id, project_id, slug, body, content_sha256, "
                "created_by_agent, created_at, updated_at, audience) "
                "VALUES ('ac9-neg', 'p1', 'ac9-neg', 'b', 'h', 'ag', "
                "'t', 't', 'everyone')",
            )

    def test_pg_source_tier_default_and_check_fire(self, pg_engine) -> None:
        """Phase 2 (trust-tier addendum T-1), materialized on PG: the
        column defaults to 'agent' on insert (the DEFAULT satisfies
        the CHECK), an explicit 'curated' passes, and an
        out-of-vocabulary value FIRES the CHECK."""
        base = (
            "INSERT INTO project_operating_knowledge "
            "(id, project_id, slug, body, content_sha256, "
            "created_by_agent, created_at, updated_at{tier}) "
            "VALUES (:id, 'p-ac9-tier', :slug, :body, :sha, 'ag', 't', 't'{val})"
        )
        with pg_engine.connect() as conn:

            def _tier_of(row_id: str) -> str:
                return conn.execute(
                    text(
                        "SELECT source_tier FROM project_operating_knowledge "
                        "WHERE id = :rid"
                    ),
                    {"rid": row_id},
                ).fetchone()[0]

            # 1. omitted tier → DEFAULT 'agent'
            _pg_exec(
                pg_engine,
                base.format(tier="", val=""),
                {"id": "ac9-tier-a", "slug": "tier-def", "body": "b", "sha": "h1"},
            )
            assert _tier_of("ac9-tier-a") == "agent"
            # 2. explicit 'curated' passes
            _pg_exec(
                pg_engine,
                base.format(tier=", source_tier", val=", 'curated'"),
                {"id": "ac9-tier-b", "slug": "tier-cur", "body": "b", "sha": "h2"},
            )
            assert _tier_of("ac9-tier-b") == "curated"
            # 3. out-of-vocabulary FIRES the CHECK
            with pytest.raises(Exception):
                conn.execute(  # noqa: S101 — the refusal IS the assertion
                    text(
                        "INSERT INTO project_operating_knowledge "
                        "(id, project_id, slug, body, content_sha256, "
                        "created_by_agent, created_at, updated_at, source_tier) "
                        "VALUES ('ac9-tier-c', 'p-ac9-tier', 'tier-bad', 'b', "
                        "'h3', 'ag', 't', 't', 'tier')"
                    )
                )
                conn.rollback()
            conn.rollback()
        # teardown of this leg's rows
        _pg_exec(
            pg_engine,
            "DELETE FROM project_operating_knowledge WHERE project_id = 'p-ac9-tier'",
        )

    def test_partial_unique_index_materialized_and_binding(self, pg_engine) -> None:
        """uq_pok_active_slug exists WITH the partial predicate and
        makes dual-active structurally impossible on PG."""
        idx = _pg_indexes(pg_engine, POK_TABLES[0])
        assert "uq_pok_active_slug" in idx, (
            f"partial index missing; have: {sorted(idx)}"
        )
        assert re.search(r"WHERE\s+\(?status\s*=\s*'active'", idx["uq_pok_active_slug"]), (
            f"partial predicate missing from index def: {idx['uq_pok_active_slug']}"
        )
        # dual-active structurally impossible
        base = (
            "INSERT INTO project_operating_knowledge "
            "(id, project_id, slug, body, content_sha256, "
            "created_by_agent, created_at, updated_at) "
            "VALUES (:id, 'p-ac9', 'ac9-slug', :body, :sha, 'ag', 't', 't')"
        )
        _pg_exec(pg_engine, base, {"id": "ac9-a", "body": "v1", "sha": "h1"})
        with pytest.raises(Exception):
            _pg_exec(pg_engine, base, {"id": "ac9-b", "body": "v2", "sha": "h2"})
        # supersede frees the slug
        _pg_exec(
            pg_engine,
            "UPDATE project_operating_knowledge SET status = 'superseded', "
            "superseded_by_id = 'ac9-c' WHERE id = 'ac9-a'",
        )
        _pg_exec(pg_engine, base, {"id": "ac9-c", "body": "v3", "sha": "h3"})
        # teardown of this leg's rows
        _pg_exec(
            pg_engine,
            "DELETE FROM project_operating_knowledge WHERE project_id = 'p-ac9'",
        )

    def test_no_foreign_keys_anywhere(self, pg_engine) -> None:
        """The rollback-inert posture, materialized: zero FKs on both
        tables (project_id and superseded_by_id are plain TEXT)."""
        assert _pg_foreign_keys(pg_engine, POK_TABLES[0]) == []
        assert _pg_foreign_keys(pg_engine, POK_TABLES[1]) == []

    def test_metadata_registry_carries_both_models(self) -> None:
        """B7 seam: both models are registered in SQLModel.metadata —
        the same registry the manager's pre-create_all block feeds."""
        assert "project_operating_knowledge" in SQLModel.metadata.tables
        assert (
            "project_operating_knowledge_embeddings" in SQLModel.metadata.tables
        )
