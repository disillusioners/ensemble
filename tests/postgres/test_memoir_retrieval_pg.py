"""The memoir retrieval on REAL PostgreSQL — the PG-only legs.

Phase 2 of the Memoir Store v1 lane (plan:
``.agents/shared/planning/ari-memoir-store/phase2-plan.md`` AC2 (the PG
leg); pack ``test/packs/memoir_retrieval_pg_test.sh``).

The SQLite unit leg cannot see the PostgreSQL half of the lexical ranker,
and the R2 defect it would hide is the WHOLE POINT of this module:
``ts_rank_cd`` has NO ``(tsquery, tsvector)`` overload, so the reversed
argument order raises ``UndefinedFunction`` ON THE OPERATIVE DRIVER while
the SQLite BM25 fallback stays GREEN. These legs prove the shipped
argument order against a real PostgreSQL.

What is proven here:

* the SHIPPED lexical SQL runs and returns the ``ts_rank_cd`` ordering
  (the SQL is EXTRACTED from the shipped source with ``ast`` — never
  copied — so the test proves the code that actually ships);
* the GIN index serves the query (the EXPLAIN leg);
* the REVERSED argument order FAILS — the mutation condition (R2);
* the JSONB embedding round-trip and the Python cosine;
* the ``TEXT[]`` stars round-trip;
* the MODEL-bound CHECK constraints are enforced by PostgreSQL;
* the partial indexes exist with their predicates;
* the repository's ONE INSERT works on PostgreSQL (the ``TEXT[]`` and
  ``timestamptz`` binds are driver-specific and the SQLite leg cannot
  prove them).

PG-only lane: run with ``--override-ini="addopts=" -m postgres`` (the
pack does it) against a sanctioned scratch database.
"""

from __future__ import annotations

import ast
import pathlib

import pytest
from sqlalchemy import text
from sqlmodel import Session, select

from daemon.repositories.memoir.models import MemoirEpisode
from daemon.repositories.memoir.repository import MemoirRepository

pytestmark = pytest.mark.postgres

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
REPOSITORY_PY = REPO_ROOT / "daemon" / "repositories" / "memoir" / "repository.py"
MANAGER_PY = REPO_ROOT / "daemon" / "manager.py"


# ── the shipped statements (extracted, never copied) ──────────────────────


def _module_string_constant(path: pathlib.Path, name: str) -> str:
    """One module-level string constant, read from the shipped source."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not isinstance(node.value, ast.Constant):
            continue
        if not isinstance(node.value.value, str):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == name:
                return node.value.value
    raise AssertionError(f"{name} was not found in {path}")


def _shipped_pg_lexical_sql() -> str:
    """The shipped PG lexical ranker SQL (D2.2 / AM-1)."""
    return _module_string_constant(REPOSITORY_PY, "_MEMOIR_PG_LEXICAL_SQL")


def _shipped_memoir_ensure_statements() -> list[str]:
    """The memoir entries of the shared ensure list, read from the source."""
    tree = ast.parse(MANAGER_PY.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        if node.name != "_ensure_postgres_columns":
            continue
        for inner in ast.walk(node):
            if not isinstance(inner, ast.Assign):
                continue
            targets = [t.id for t in inner.targets if isinstance(t, ast.Name)]
            if "statements" not in targets or not isinstance(inner.value, ast.List):
                continue
            values = []
            for element in inner.value.elts:
                if isinstance(element, ast.Constant) and isinstance(element.value, str):
                    values.append(element.value)
                elif isinstance(element, ast.BinOp):
                    folded = ast.literal_eval(element)
                    if isinstance(folded, str):
                        values.append(folded)
            memoir = [
                value
                for value in values
                if "memoir_episodes" in value and ("tsv" in value or "GIN" in value)
            ]
            assert len(memoir) == 2, memoir
            return memoir
    raise AssertionError("the shared ensure statement list was not found")


@pytest.fixture(scope="module")
def memoir_tsv(pg_engine):
    """Apply the shipped ``tsv`` ensure (the column is in NO model)."""
    for statement in _shipped_memoir_ensure_statements():
        with pg_engine.begin() as conn:
            conn.execute(text(statement))
    return True


@pytest.fixture()
def repo(pg_engine):
    return MemoirRepository(pg_engine)


def _now():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc)


def _insert(repo, episode_id: str, **overrides):
    with Session(repo.engine) as session:
        row = MemoirEpisode(
            id=episode_id,
            agent_id="ari",
            body=overrides.pop("body", "the preserved first day"),
            created_at=overrides.pop("created_at", _now()),
            event_at=overrides.pop("event_at", _now()),
            **overrides,
        )
        session.add(row)
        session.commit()
    return episode_id


# ── AC2 — the shipped lexical SQL ─────────────────────────────────────────


def test_the_shipped_lexical_sql_uses_the_tsvector_first(repo, memoir_tsv):
    """AM-1: ``tsvector`` is the FIRST argument of ``ts_rank_cd``."""
    sql = _shipped_pg_lexical_sql()
    assert "ts_rank_cd(tsv, plainto_tsquery('english', :q))" in sql
    assert "ts_rank_cd(plainto_tsquery" not in sql


def test_the_shipped_lexical_sql_returns_the_ts_rank_cd_ordering(
    repo, memoir_tsv
):
    """The shipped SQL runs on PG and orders by the rank score."""
    _insert(repo, "pg-rank-1", body="zebra appears once here")
    _insert(repo, "pg-rank-2", body="zebra zebra zebra appears here")
    _insert(repo, "pg-rank-3", body="nothing relevant in this row")

    sql = _shipped_pg_lexical_sql() + " ORDER BY score DESC, id ASC LIMIT :cand"
    with repo.engine.connect() as conn:
        rows = conn.execute(
            text(sql),
            {"q": "zebra", "agent_id": "ari", "cand": 100},
        ).fetchall()
    assert [row[0] for row in rows] == ["pg-rank-2", "pg-rank-1"]
    assert rows[0][1] > rows[1][1]


def test_the_repository_lexical_rank_uses_the_shipped_sql(repo, memoir_tsv):
    """The repository's PG leg returns the same SHAPE as the SQLite leg."""
    _insert(repo, "pg-repo-1", body="zebra appears once here")
    _insert(repo, "pg-repo-2", body="zebra zebra zebra appears here")

    ranked = repo.lexical_rank("ari", "zebra")
    assert [row_id for row_id, _score in ranked] == ["pg-repo-2", "pg-repo-1"]
    assert all(isinstance(score, float) for _row_id, score in ranked)


def test_the_reversed_argument_order_does_not_exist(repo, memoir_tsv):
    """R2 / AM-1 — THE MUTATION CONDITION.

    The reversed form raises ``UndefinedFunction`` on PostgreSQL, while
    the SQLite fallback stays GREEN. This is why the argument order is
    proven against a real PG and not against the unit leg.
    """
    _insert(repo, "pg-reversed", body="zebra appears here")
    reversed_sql = (
        "SELECT id, ts_rank_cd(plainto_tsquery('english', :q), tsv) AS score "
        "FROM memoir_episodes "
        "WHERE agent_id = :agent_id "
        "AND tsv @@ plainto_tsquery('english', :q)"
    )
    with repo.engine.connect() as conn:
        with pytest.raises(Exception) as excinfo:
            conn.execute(
                text(reversed_sql), {"q": "zebra", "agent_id": "ari"}
            ).fetchall()
    message = str(excinfo.value).lower()
    assert "does not exist" in message or "undefinedfunction" in message


def test_the_gin_index_serves_the_lexical_query(repo, memoir_tsv):
    """The GIN index is USED — the lexical half does not scan the table."""
    with repo.engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO memoir_episodes "
                "(id, agent_id, body, created_at, event_at, importance, "
                "is_keepsake, embedding_attempts, access_count) "
                "VALUES (:id, 'ari', :body, now(), now(), 5, false, 0, 0)"
            ),
            [
                {
                    "id": f"pg-gin-{index}",
                    "body": ("filler " * 40) + (f"zebra{index} " if index == 7 else ""),
                }
                for index in range(300)
            ],
        )
    with repo.engine.begin() as conn:
        conn.execute(text("ANALYZE memoir_episodes"))

    sql = _shipped_pg_lexical_sql() + " ORDER BY score DESC, id ASC LIMIT :cand"
    with repo.engine.connect() as conn:
        plan = "\n".join(
            row[0]
            for row in conn.execute(
                text("EXPLAIN " + sql),
                {"q": "zebra7", "agent_id": "ari", "cand": 100},
            ).fetchall()
        )
    assert "idx_memoir_episodes_tsv" in plan, plan


# ── the PG-only column behaviour ──────────────────────────────────────────


def test_the_one_insert_works_on_pg_with_an_array_and_a_timestamp(repo):
    """The raw INSERT's binds are driver-specific — only PG proves them."""
    episode = repo.insert_episode(
        agent_id="ari",
        body="the preserved first day",
        title="First Day",
        importance=8,
        stars=["Emperor", "the Emperor"],
        event_at="2026-03-01T10:00:00+00:00",
    )
    with Session(repo.engine) as session:
        row = session.exec(
            select(MemoirEpisode).where(MemoirEpisode.id == episode)
        ).first()
        assert row.stars == ["Emperor", "the Emperor"]
        assert row.importance == 8
        assert row.event_at.year == 2026
        assert row.event_at.month == 3
        assert row.embedding is None
        assert row.embedding_attempts == 0
        assert row.access_count == 0
        assert row.last_accessed_at is None
        assert row.superseded_by is None


def test_the_stars_round_trip_on_pg(repo):
    """``TEXT[]`` — the three shapes (D1.8 / AM-7)."""
    with Session(repo.engine) as session:
        session.add(
            MemoirEpisode(
                id="pg-stars-list",
                agent_id="ari",
                body="a",
                created_at=_now(),
                event_at=_now(),
                stars=["Emperor", "the Emperor"],
            )
        )
        session.add(
            MemoirEpisode(
                id="pg-stars-none",
                agent_id="ari",
                body="b",
                created_at=_now(),
                event_at=_now(),
                stars=None,
            )
        )
        session.add(
            MemoirEpisode(
                id="pg-stars-empty",
                agent_id="ari",
                body="c",
                created_at=_now(),
                event_at=_now(),
                stars=[],
            )
        )
        session.commit()
    with Session(repo.engine) as session:
        rows = {row.id: row for row in session.exec(select(MemoirEpisode)).all()}
        assert rows["pg-stars-list"].stars == ["Emperor", "the Emperor"]
        assert rows["pg-stars-none"].stars is None
        assert rows["pg-stars-empty"].stars == []


def test_the_embedding_round_trips_as_jsonb_and_the_cosine_works(repo):
    """The JSONB half + the Python cosine (DD-1: no pgvector)."""
    episode = repo.insert_episode(agent_id="ari", body="a vector memory")
    vector = [0.1, 0.2, 0.3]
    assert repo.store_embedding(episode, vector, "text-embedding-3-small", 3)
    with repo.engine.connect() as conn:
        stored = conn.execute(
            text(
                "SELECT embedding, embedding_model, embedding_dims, embedded_at "
                "FROM memoir_episodes WHERE id = :id"
            ),
            {"id": episode},
        ).fetchone()
    assert list(stored[0]) == vector
    assert stored[1] == "text-embedding-3-small"
    assert stored[2] == 3
    assert stored[3] is not None

    from daemon.services.critical_notes_selector import _cosine_similarity

    assert _cosine_similarity(vector, list(stored[0])) == pytest.approx(1.0)
    assert _cosine_similarity([1.0, 0.0, 0.0], list(stored[0])) == pytest.approx(
        0.2672612419124244, rel=1e-6
    )


def test_the_importance_check_is_enforced_by_pg(repo):
    with Session(repo.engine) as session:
        with pytest.raises(Exception):
            session.add(
                MemoirEpisode(
                    id="pg-importance",
                    agent_id="ari",
                    body="a",
                    created_at=_now(),
                    event_at=_now(),
                    importance=42,
                )
            )
            session.commit()


def test_the_pinned_by_check_is_enforced_by_pg(repo):
    with Session(repo.engine) as session:
        with pytest.raises(Exception):
            session.add(
                MemoirEpisode(
                    id="pg-pinned-by",
                    agent_id="ari",
                    body="a",
                    created_at=_now(),
                    event_at=_now(),
                    is_keepsake=True,
                    pinned_by="somebody",
                )
            )
            session.commit()


def test_the_partial_indexes_exist_on_pg(repo):
    with repo.engine.connect() as conn:
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


def test_the_timeline_window_is_inclusive_on_pg(repo, memoir_tsv):
    """D2.9 / DR-4 on the operative driver."""
    from datetime import datetime, timedelta, timezone

    base = datetime(2026, 3, 1, tzinfo=timezone.utc)
    before = repo.insert_episode(
        agent_id="ari", body="before", event_at=base - timedelta(days=1)
    )
    at_since = repo.insert_episode(
        agent_id="ari", body="at since", event_at=base
    )
    at_until = repo.insert_episode(
        agent_id="ari", body="at until", event_at=base + timedelta(days=2)
    )
    after = repo.insert_episode(
        agent_id="ari", body="after", event_at=base + timedelta(days=3)
    )
    rows = repo.timeline_window("ari", since=base, until=base + timedelta(days=2))
    ids = {row.id for row in rows}
    assert at_since in ids
    assert at_until in ids
    assert before not in ids
    assert after not in ids
