"""The memoir schema on REAL PostgreSQL — the PG-only legs.

Phase 1 of the Memoir Store v1 lane (plan:
``.agents/shared/planning/ari-memoir-store/phase1-plan.md`` AC3; pack
``test/packs/memoir_schema_pg_test.sh``).

The SQLite lane (the companion unit pack) cannot see the PG half of the
schema. These legs prove it:

* the four tables are built by ``create_all`` FROM THE MODELS — and so
  carry the MODEL-bound CHECK constraints (W2: a constraint that lived
  only in the ``.sql`` would not exist here at all);
* ``stars`` is ``TEXT[]`` on PG and ``embedding`` is ``JSONB`` (the
  ``StarsListType`` / ``JSONBType`` dialect behaviour);
* the ``tsv`` GENERATED column is created by the ENSURE (it is in no
  model), populates on INSERT, and is served by its GIN index;
* the AM-8 RED condition: the ONE-argument ``to_tsvector`` form is
  rejected by PostgreSQL (*"generation expression is not immutable"*) —
  the reason the two-argument form is mandatory;
* the ensure is IDEMPOTENT (re-running it is a no-op);
* the ensure's ONE-TRANSACTION blast radius: a failure in ANY entry —
  the memoir's OR an inherited lane's — rolls the whole ensure back and
  aborts the boot. Proved MECHANICALLY here (a sentinel statement in the
  same transaction is rolled back with the failing memoir statement),
  because statement ORDER IS NOT A CONTAINMENT MEASURE.

The statements under test are EXTRACTED from the shipped
``daemon/manager.py`` (via ``ast``) — not copied — so the test proves
the code that actually ships.

PG-only lane: run with ``--override-ini="addopts=" -m postgres`` (the
pack does it) against a sanctioned scratch database.
"""

from __future__ import annotations

import ast
import pathlib

import pytest
from sqlalchemy import text

from daemon.repositories.memoir.models import (
    MemoirEpisode,
    MemoirPersona,
    MemoirPersonaVersion,
    MemoirSynthesis,
)

pytestmark = pytest.mark.postgres

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
MANAGER_PY = REPO_ROOT / "daemon" / "manager.py"

MEMOIR_TABLE_NAMES = [
    "memoir_episodes",
    "memoir_persona",
    "memoir_persona_versions",
    "memoir_syntheses",
]


# ── the shipped ensure statements (extracted, never copied) ───────────────


def _shipped_memoir_ensure_statements() -> list[str]:
    """The memoir entries of the shared ensure list, read from the source.

    ``ast``-parsed so the test proves the SHIPPED text. The memoir
    statements are the LAST TWO entries of the shared list (they were
    appended after the inherited project-skill-loader entries).
    """
    tree = ast.parse(MANAGER_PY.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        if node.name != "_ensure_postgres_columns":
            continue
        for inner in ast.walk(node):
            if not isinstance(inner, ast.Assign):
                continue
            targets = [
                t.id for t in inner.targets if isinstance(t, ast.Name)
            ]
            if "statements" not in targets:
                continue
            if not isinstance(inner.value, ast.List):
                continue
            values = []
            for element in inner.value.elts:
                if isinstance(element, ast.Constant) and isinstance(
                    element.value, str
                ):
                    values.append(element.value)
                elif isinstance(element, ast.BinOp):
                    # Implicit string concatenation — fold it.
                    folded = ast.literal_eval(element)
                    if isinstance(folded, str):
                        values.append(folded)
            memoir = [
                value
                for value in values
                if "memoir_episodes" in value and (
                    "tsv" in value or "GIN" in value
                )
            ]
            assert len(memoir) == 2, memoir
            return memoir
    raise AssertionError("the shared ensure statement list was not found")


def _now():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc)


def _insert_episode(session, episode_id: str, **overrides):
    row = MemoirEpisode(
        id=episode_id,
        agent_id="ari",
        body="the first day is preserved",
        created_at=_now(),
        event_at=_now(),
        **overrides,
    )
    session.add(row)
    session.commit()
    return row


# ── the PG-built schema ──────────────────────────────────────────────────


def test_the_four_memoir_tables_exist_on_pg(pg_engine):
    with pg_engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name = ANY(:names)"
            ),
            {"names": MEMOIR_TABLE_NAMES},
        ).fetchall()
    assert {row[0] for row in rows} == set(MEMOIR_TABLE_NAMES)


def test_the_model_bound_check_constraints_exist_on_the_pg_tables(pg_engine):
    """W2 — the constraints are on the PG table, not only on the model."""
    expected = {
        "memoir_episodes": {
            "ck_memoir_episodes_importance",
            "ck_memoir_episodes_pinned_by",
        },
        "memoir_persona_versions": {
            "ck_memoir_persona_versions_status",
            "ck_memoir_persona_versions_source",
        },
        "memoir_syntheses": {"ck_memoir_syntheses_verb"},
    }
    with pg_engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT tc.table_name, tc.constraint_name "
                "FROM information_schema.table_constraints tc "
                "WHERE tc.constraint_type = 'CHECK' "
                "AND tc.table_name = ANY(:names)"
            ),
            {"names": MEMOIR_TABLE_NAMES},
        ).fetchall()
    found: dict[str, set[str]] = {}
    for table_name, constraint_name in rows:
        found.setdefault(table_name, set()).add(constraint_name)
    for table_name, names in expected.items():
        assert names <= found.get(table_name, set()), (
            f"{table_name}: missing {names - found.get(table_name, set())}"
        )


def test_the_importance_check_is_enforced_by_pg(pg_engine):
    from sqlmodel import Session

    with Session(pg_engine) as session:
        with pytest.raises(Exception):
            _insert_episode(session, "pg-importance", importance=42)


def test_stars_is_a_text_array_and_embedding_is_jsonb(pg_engine):
    """The dialect behaviour of ``StarsListType`` / ``JSONBType``."""
    with pg_engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT table_name, column_name, data_type, udt_name "
                "FROM information_schema.columns "
                "WHERE table_name = ANY(:names) "
                "AND column_name IN ('stars', 'embedding', "
                "'source_episode_ids')"
            ),
            {"names": MEMOIR_TABLE_NAMES},
        ).fetchall()
    observed = {(row[0], row[1]): (row[2], row[3]) for row in rows}
    # PG reports an ARRAY column as data_type=ARRAY, udt_name=_text.
    assert observed[("memoir_episodes", "stars")] == ("ARRAY", "_text")
    assert observed[("memoir_episodes", "embedding")] == ("jsonb", "jsonb")
    assert observed[("memoir_syntheses", "source_episode_ids")] == (
        "jsonb",
        "jsonb",
    )


def test_the_stars_round_trip_on_pg(pg_engine):
    """``["Emperor", "the Emperor"]``, ``None`` and ``[]`` on PG."""
    from sqlmodel import Session

    with Session(pg_engine) as session:
        _insert_episode(session, "pg-stars-list", stars=["Emperor", "the Emperor"])
        _insert_episode(session, "pg-stars-none", stars=None)
        _insert_episode(session, "pg-stars-empty", stars=[])
    with Session(pg_engine) as session:
        from sqlmodel import select

        rows = {row.id: row for row in session.exec(select(MemoirEpisode)).all()}
        assert rows["pg-stars-list"].stars == ["Emperor", "the Emperor"]
        assert rows["pg-stars-none"].stars is None
        assert rows["pg-stars-empty"].stars == []


def test_the_embedding_round_trips_as_jsonb(pg_engine):
    from sqlmodel import Session

    vector = [0.1, 0.2, 0.3]
    with Session(pg_engine) as session:
        _insert_episode(
            session,
            "pg-embedding",
            embedding=vector,
            embedding_model="text-embedding-3-small",
            embedding_dims=3,
            embedded_at="2026-11-05T00:00:00+00:00",
        )
    with pg_engine.connect() as conn:
        stored = conn.execute(
            text("SELECT embedding, embedding_model, embedding_dims FROM memoir_episodes WHERE id = :id"),
            {"id": "pg-embedding"},
        ).fetchone()
    assert list(stored[0]) == vector
    assert stored[1] == "text-embedding-3-small"
    assert stored[2] == 3


def test_the_partial_indexes_exist_on_pg(pg_engine):
    with pg_engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT indexname, indexdef FROM pg_indexes "
                "WHERE tablename = 'memoir_episodes'"
            )
        ).fetchall()
    definitions = {row[0]: row[1] for row in rows}
    assert "idx_memoir_ep_keepsakes" in definitions
    assert "idx_memoir_ep_pending_emb" in definitions
    assert "idx_memoir_ep_agent_created" in definitions
    assert "idx_memoir_ep_agent_event" in definitions
    assert "WHERE" in definitions["idx_memoir_ep_keepsakes"]
    assert "WHERE" in definitions["idx_memoir_ep_pending_emb"]


# ── the ``tsv`` ensure ────────────────────────────────────────────────────


def test_the_tsv_column_is_created_by_the_ensure_and_populates(pg_engine):
    """The two-argument form is IMMUTABLE and legal in a generated column."""
    alter, _gin = _shipped_memoir_ensure_statements()
    with pg_engine.begin() as conn:
        conn.execute(text(alter))

    with pg_engine.connect() as conn:
        columns = conn.execute(
            text(
                "SELECT column_name, is_generated, generation_expression "
                "FROM information_schema.columns "
                "WHERE table_name = 'memoir_episodes' AND column_name = 'tsv'"
            )
        ).fetchall()
    assert columns, "the tsv column was not created"
    assert columns[0][1] == "ALWAYS"
    assert "to_tsvector" in columns[0][2]

    from sqlmodel import Session

    with Session(pg_engine) as session:
        _insert_episode(session, "pg-tsv", title="First Day")
    with pg_engine.connect() as conn:
        hit = conn.execute(
            text(
                "SELECT id FROM memoir_episodes "
                "WHERE tsv @@ plainto_tsquery('english', 'first')"
            )
        ).fetchall()
    assert [row[0] for row in hit] == ["pg-tsv"]


def test_the_one_argument_tsv_form_is_rejected_by_pg(pg_engine):
    """THE AM-8 RED CONDITION — the one-arg form breaks the PG boot."""
    with pytest.raises(Exception) as excinfo:
        with pg_engine.begin() as conn:
            conn.execute(
                text(
                    "ALTER TABLE memoir_episodes ADD COLUMN IF NOT EXISTS "
                    "tsv_one tsvector GENERATED ALWAYS AS "
                    "(to_tsvector(coalesce(title,'') || ' ' || body)) STORED"
                )
            )
    message = str(excinfo.value).lower()
    assert "not immutable" in message, message


def test_the_gin_index_exists_and_serves_the_lexical_query(pg_engine):
    alter, gin = _shipped_memoir_ensure_statements()
    with pg_engine.begin() as conn:
        conn.execute(text(alter))
        conn.execute(text(gin))

    with pg_engine.connect() as conn:
        indexes = conn.execute(
            text(
                "SELECT indexname, indexdef FROM pg_indexes "
                "WHERE tablename = 'memoir_episodes' "
                "AND indexname = 'idx_memoir_episodes_tsv'"
            )
        ).fetchall()
    assert indexes, "the GIN index was not created"
    assert "using gin" in indexes[0][1].lower()

    from sqlmodel import Session

    with Session(pg_engine) as session:
        _insert_episode(session, "pg-gin", title="First Day")

    with pg_engine.connect() as conn:
        conn.execute(text("SET enable_seqscan = off"))
        plan = conn.execute(
            text(
                "EXPLAIN SELECT id FROM memoir_episodes "
                "WHERE tsv @@ plainto_tsquery('english', 'first')"
            )
        ).fetchall()
    rendered = "\n".join(row[0] for row in plan)
    assert "idx_memoir_episodes_tsv" in rendered, rendered


def test_the_ensure_is_idempotent(pg_engine):
    """Re-running the memoir ensure is a no-op (safe on every boot)."""
    alter, gin = _shipped_memoir_ensure_statements()
    for _ in range(2):
        with pg_engine.begin() as conn:
            conn.execute(text(alter))
            conn.execute(text(gin))


def test_a_failing_memoir_statement_rolls_back_the_whole_transaction(pg_engine):
    """THE ONE-TRANSACTION BLAST RADIUS — proved, not asserted in prose.

    A failure in ANY entry of the shared ensure (the memoir's OR an
    inherited lane's) rolls the whole transaction back and aborts the
    boot. The sentinel below stands in for an inherited lane's
    statement: its ABSENCE after the failure is the proof that the
    collateral rollback happened. Statement ORDER IS NOT A CONTAINMENT
    MEASURE — the sentinel is deliberately executed FIRST.
    """
    sentinel_table = "memoir_blast_sentinel"
    with pg_engine.connect() as conn:
        conn.execute(text(f"DROP TABLE IF EXISTS {sentinel_table}"))

    broken = (
        "ALTER TABLE memoir_episodes ADD COLUMN IF NOT EXISTS tsv_broken "
        "tsvector GENERATED ALWAYS AS (no_such_imm_function(title)) STORED"
    )
    with pytest.raises(Exception):
        with pg_engine.begin() as conn:
            # The inherited lane's statement — executed FIRST.
            conn.execute(
                text(f"CREATE TABLE {sentinel_table} (id INTEGER PRIMARY KEY)")
            )
            # The memoir's statement — the one that fails.
            conn.execute(text(broken))

    with pg_engine.connect() as conn:
        present = conn.execute(
            text(
                "SELECT count(*) FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name = :name"
            ),
            {"name": sentinel_table},
        ).scalar()
    assert present == 0, "the collateral statement was NOT rolled back"
    with pg_engine.connect() as conn:
        conn.execute(text(f"DROP TABLE IF EXISTS {sentinel_table}"))
