"""Memoir Store v2 — the schema substrate on REAL PostgreSQL.

Phase 1 of the Memoir Store v2 lane (plan:
``.agents/shared/planning/ari-memoir-store/v2-phase1-plan.md`` AC3; pack
``test/packs/memoir_v2_schema_pg_test.sh``).

The SQLite lane (the companion unit pack) cannot see the PostgreSQL half
of this schema. These legs prove it:

* the SIX v2 tables are built on PostgreSQL by ``create_all`` FROM THE
  MODELS — and so carry the MODEL-bound CHECK constraints (a constraint
  that lived only in the ``.sql`` would not exist here at all);
* the FOUR new ``memoir_episodes`` columns arrive through the ENSURE —
  the tables are created by ``create_all`` but ``create_all`` cannot
  ALTER an existing table, so the columns are the ensure's job;
* ``reflected_at`` is typed ``TIMESTAMPTZ`` (NOT ``DATETIME``, which is
  SQLite-specific and would abort every PostgreSQL boot);
* the cutover-bounded legacy seed flips the PRE-cutover rows to
  ``'legacy'`` and leaves the post-cutover rows NULL;
* the ensure is IDEMPOTENT — re-running it is a no-op;
* the ensure CENSUS is ASYMMETRIC: the ``memoir_episodes``-only filter
  sees FOUR memoir entries (the v1 ``tsv`` ALTER, the v1 GIN index and
  the two v2 entries), while the narrower ``(tsv OR GIN)`` filter still
  sees TWO (V2-D 1.10).

The statements under test are EXTRACTED from the shipped
``daemon/manager.py`` (via ``ast``) — never copied — so the legs prove
the code that actually ships.

PG-only lane: run with ``--override-ini="addopts=" -m postgres`` (the
pack does it) against a sanctioned scratch database.
"""

from __future__ import annotations

import ast
import pathlib
from datetime import datetime, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from daemon.repositories.memoir.models import (
    MemoirEpisode,
    MemoirJevLedger,
    MemoirLine,
    MemoirProposalLedger,
    MemoirReflectionState,
    MemoirStar,
    MemoirStarAlias,
    MemoirSynthesis,
)

pytestmark = pytest.mark.postgres

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
MANAGER_PY = REPO_ROOT / "daemon" / "manager.py"

V2_TABLE_NAMES = {
    "memoir_star",
    "memoir_star_alias",
    "memoir_line",
    "memoir_reflection_state",
    "memoir_proposal_ledger",
    "memoir_jev_ledger",
}

#: The live databases that must NEVER be touched by this lane.
FORBIDDEN_DB_NAMES = frozenset({"ensemble_prod", "ensemble_dev", "ensemble_prod_test"})

#: The cutover literal — SPACE-SEPARATED (SF11 / V2-33).
CUTOVER_LITERAL = "2026-11-09 00:00:00"


# ── the shipped ensure statements (extracted, never copied) ───────────────


def _shared_ensure_statements() -> list[str]:
    """Every statement of the shared ensure list, read from the source."""
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
            values: list[str] = []
            for element in inner.value.elts:
                if isinstance(element, ast.Constant) and isinstance(element.value, str):
                    values.append(element.value)
                elif isinstance(element, ast.BinOp):
                    folded = ast.literal_eval(element)
                    if isinstance(folded, str):
                        values.append(folded)
            return values
    raise AssertionError("the shared ensure statement list was not found")


def _memoir_ensure_statements() -> list[str]:
    """The memoir entries of the shared ensure list.

    The corpus filter (``memoir_episodes`` only) sees FOUR; the
    retrieval filter (``memoir_episodes`` AND (``tsv`` OR ``GIN``))
    sees TWO.
    """
    corpus = [
        value
        for value in _shared_ensure_statements()
        if "memoir_episodes" in value
    ]
    retrieval = [
        value
        for value in corpus
        if "tsv" in value or "GIN" in value
    ]
    return corpus


def _v2_ensure_statements() -> list[str]:
    """THE TWO v2 entries — the DDL evolution and the cutover seed.

    Selected BY CONTENT (never by position): the ORDER of the shared
    list is an auditability choice, not a contract.
    """
    ddl = [
        value
        for value in _shared_ensure_statements()
        if "ADD COLUMN IF NOT EXISTS poignancy_status" in value
    ]
    seed = [
        value
        for value in _shared_ensure_statements()
        if "poignancy_status = 'legacy'" in value
    ]
    assert len(ddl) == 1, ddl
    assert len(seed) == 1, seed
    return ddl + seed


# ── the write-bearing safety check ───────────────────────────────────────


def test_the_target_database_is_a_sanctioned_scratch_database(pg_engine):
    """House rule: check ``SELECT current_database()`` before ANY
    write-bearing op in a PG leg."""
    with pg_engine.connect() as conn:
        current = conn.execute(text("SELECT current_database()")).scalar()
    assert current is not None
    assert current not in FORBIDDEN_DB_NAMES, current


# ── the six tables ───────────────────────────────────────────────────────


def test_the_six_v2_tables_exist_on_postgres(pg_engine):
    with pg_engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public'"
            )
        ).scalars().all()
    assert V2_TABLE_NAMES <= set(rows), sorted(set(rows))


def test_the_model_bound_check_constraints_are_enforced_on_postgres(pg_engine):
    """The CHECKs are bound to the MODELS — on PG the table is built
    FROM the model, so a constraint that lived only in the ``.sql``
    would not exist here at all."""
    with pg_engine.begin() as conn:
        with pytest.raises(IntegrityError):
            conn.execute(
                text(
                    "INSERT INTO memoir_star (id, agent_id, name, star_type, description, "
                    "first_seen_at, last_touched_at, heat, created_at) "
                    "VALUES ('s1', 'ari', 'x', 'NOT-A-TYPE', '', now(), now(), 0.0, now())"
                )
            )
    with pg_engine.begin() as conn:
        with pytest.raises(IntegrityError):
            conn.execute(
                text(
                    "INSERT INTO memoir_proposal_ledger (id, agent_id, episode_id, "
                    "proposal_date, proposed_at, notification_status, reason, created_at) "
                    "VALUES ('p1', 'ari', 'ep1', '2026-11-09', now(), 'notified', '', now())"
                )
            )


def test_the_index_names_exist_on_postgres(pg_engine):
    with pg_engine.connect() as conn:
        names = set(
            conn.execute(
                text("SELECT indexname FROM pg_indexes WHERE schemaname = 'public'")
            ).scalars().all()
        )
    assert {
        "idx_memoir_star_agent_heat",
        "idx_memoir_star_alias_star",
        "idx_memoir_line_episode",
        "idx_memoir_line_agent",
        "idx_memoir_proposal_ledger_date",
        "idx_memoir_jev_status",
    } <= names, sorted(names)


def test_the_jsonb_columns_render_as_jsonb_on_postgres(pg_engine):
    with pg_engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT table_name, column_name, data_type FROM information_schema.columns "
                "WHERE table_name IN ('memoir_episodes', 'memoir_syntheses') "
                "AND column_name IN ('embedding', 'source_episode_ids')"
            )
        ).all()
    assert rows, "the JSONB columns were not found"
    assert {row[2] for row in rows} == {"jsonb"}, rows


# ── the four new columns ─────────────────────────────────────────────────


def test_the_four_new_columns_are_present_with_the_expected_types(pg_engine):
    with pg_engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT column_name, data_type, is_nullable, column_default "
                "FROM information_schema.columns "
                "WHERE table_name = 'memoir_episodes' AND column_name IN "
                "('poignancy_status', 'poignancy', 'poignancy_attempts', 'reflected_at')"
            )
        ).all()
    columns = {row[0]: row for row in rows}
    assert set(columns) == {
        "poignancy_status",
        "poignancy",
        "poignancy_attempts",
        "reflected_at",
    }, sorted(columns)
    assert columns["poignancy_status"][1] == "text"
    assert columns["poignancy_status"][2] == "YES"
    assert columns["poignancy"][1] == "integer"
    assert columns["poignancy"][2] == "YES"
    assert columns["poignancy_attempts"][1] == "integer"
    assert columns["poignancy_attempts"][2] == "NO"
    assert columns["poignancy_attempts"][3] is not None
    assert "0" in str(columns["poignancy_attempts"][3])
    # TIMESTAMPTZ — NOT ``timestamp without time zone``. The model is
    # ``DateTime(timezone=True)`` and ``DATETIME`` is SQLite-specific.
    assert columns["reflected_at"][1] == "timestamp with time zone", columns["reflected_at"]
    assert columns["reflected_at"][2] == "YES"


def test_the_ensure_adds_the_four_columns_to_a_pre_v2_table(pg_engine):
    """The pre-v2 database has NO v2 columns — the ensure is what puts
    them there (``create_all`` cannot ALTER an existing table)."""
    with pg_engine.begin() as conn:
        for column in (
            "reflected_at",
            "poignancy_attempts",
            "poignancy",
            "poignancy_status",
        ):
            conn.execute(
                text(f"ALTER TABLE memoir_episodes DROP COLUMN IF EXISTS {column}")
            )
        remaining = conn.execute(
            text(
                "SELECT count(*) FROM information_schema.columns "
                "WHERE table_name = 'memoir_episodes' AND column_name IN "
                "('poignancy_status', 'poignancy', 'poignancy_attempts', 'reflected_at')"
            )
        ).scalar()
    assert remaining == 0

    for statement in _v2_ensure_statements():
        with pg_engine.begin() as conn:
            conn.execute(text(statement))

    with pg_engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_name = 'memoir_episodes' AND column_name IN "
                "('poignancy_status', 'poignancy', 'poignancy_attempts', 'reflected_at')"
            )
        ).all()
    columns = dict(rows)
    assert set(columns) == {
        "poignancy_status",
        "poignancy",
        "poignancy_attempts",
        "reflected_at",
    }
    assert columns["reflected_at"] == "timestamp with time zone"


def test_the_legacy_seed_flips_only_the_pre_cutover_rows(pg_engine):
    with pg_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO memoir_episodes (id, agent_id, title, body, created_at, event_at) "
                "VALUES ('pre', 'ari', 't', 'b', TIMESTAMPTZ '2026-11-05 12:00:00+00', "
                "TIMESTAMPTZ '2026-11-05 12:00:00+00')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO memoir_episodes (id, agent_id, title, body, created_at, event_at) "
                "VALUES ('post', 'ari', 't', 'b', TIMESTAMPTZ '2026-11-09 00:00:01+00', "
                "TIMESTAMPTZ '2026-11-09 00:00:01+00')"
            )
        )
    for statement in _v2_ensure_statements():
        with pg_engine.begin() as conn:
            conn.execute(text(statement))
    with pg_engine.connect() as conn:
        rows = dict(
            conn.execute(
                text("SELECT id, poignancy_status FROM memoir_episodes")
            ).all()
        )
    assert rows["pre"] == "legacy"
    assert rows["post"] is None


def test_the_ensure_is_idempotent(pg_engine):
    """Re-running the ensure is completely idempotent."""
    for _ in range(2):
        for statement in _v2_ensure_statements():
            with pg_engine.begin() as conn:
                conn.execute(text(statement))
    with pg_engine.connect() as conn:
        columns = conn.execute(
            text(
                "SELECT count(*) FROM information_schema.columns "
                "WHERE table_name = 'memoir_episodes' AND column_name IN "
                "('poignancy_status', 'poignancy', 'poignancy_attempts', 'reflected_at')"
            )
        ).scalar()
    assert columns == 4


def test_the_shipped_seed_literal_is_space_separated(pg_engine):
    """SF11 / V2-33 — the shipped literal is the SPACE-SEPARATED form.

    The mutation leg (AC6): an ISO-8601 ``'T'`` literal in the seed
    UPDATE flips THIS pin RED (and flips the SQLite cutover leg RED —
    SQLite compares datetimes lexicographically and ``'T'`` > ``' '``).
    """
    seed = [
        value
        for value in _shared_ensure_statements()
        if "poignancy_status = 'legacy'" in value
    ][0]
    assert f"'{CUTOVER_LITERAL}'" in seed
    assert "2026-11-09T00:00:00" not in seed


def test_the_shipped_ddl_uses_timestamptz_for_reflected_at(pg_engine):
    """C1 / V2-26 — ``DATETIME`` in the PostgreSQL ensure is a DDL
    syntax error that would ABORT EVERY PostgreSQL boot."""
    ddl = [
        value
        for value in _shared_ensure_statements()
        if "ADD COLUMN IF NOT EXISTS poignancy_status" in value
    ][0]
    assert "reflected_at TIMESTAMPTZ" in ddl
    assert "DATETIME" not in ddl.upper()


# ── the ensure census (V2-D 1.10 — asymmetric) ───────────────────────────


def test_the_ensure_census_is_asymmetric(pg_engine):
    """``memoir_episodes``-only ⇒ FOUR; ``(tsv OR GIN)`` ⇒ TWO.

    Updating BOTH assertions to the same number would be a defect, not
    symmetry (V2-D 1.10).
    """
    corpus = _memoir_ensure_statements()
    retrieval = [value for value in corpus if "tsv" in value or "GIN" in value]
    assert len(corpus) == 4, corpus
    assert len(retrieval) == 2, retrieval


def test_the_two_v2_entries_are_in_the_shared_list(pg_engine):
    statements = _v2_ensure_statements()
    assert len(statements) == 2
    assert "ALTER TABLE memoir_episodes" in statements[0]
    assert "UPDATE memoir_episodes" in statements[1]


# ── the repositories run on PostgreSQL too ───────────────────────────────


def test_the_constellation_repository_runs_on_postgres(pg_engine):
    from daemon.repositories.memoir.constellation_repository import (
        MemoirConstellationRepository,
    )

    # PostgreSQL ENFORCES the foreign key — the parent episode must
    # exist before a mention line can reference it.
    with pg_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO memoir_episodes (id, agent_id, title, body, created_at, event_at) "
                "VALUES ('ep1', 'ari', 't', 'b', now(), now())"
            )
        )

    repo = MemoirConstellationRepository(pg_engine)
    star = repo.upsert_star("ari", "the Emperor", "person")
    repo.add_alias("ari", star.id, "my dear")
    assert repo.resolve_star("ari", "my dear").id == star.id
    line = repo.record_line("ari", star.id, "ep1", datetime.now(timezone.utc), pass_id="p1")
    again = repo.record_line("ari", star.id, "ep1", datetime.now(timezone.utc), pass_id="p1")
    assert again.mention_count == line.mention_count == 1
    assert repo.increment_heat("ari", star.id) == 1.0


def test_the_syntheses_repository_runs_on_postgres(pg_engine):
    from daemon.repositories.memoir.syntheses_repository import (
        MemoirSynthesesRepository,
    )

    repo = MemoirSynthesesRepository(pg_engine)
    with pytest.raises(ValueError):
        repo.create_synthesis("ari", "a claim", [], "CREATE", 0.9)
    row = repo.create_synthesis("ari", "the Emperor is patient", ["ep1"], "CREATE", 0.9)
    duplicate = repo.create_synthesis("ari", "the emperor is patient", ["ep2"], "CREATE", 0.9)
    assert duplicate.id == row.id
    new = repo.supersede_with(
        "ari",
        row.id,
        {
            "claim": "the Emperor is very patient",
            "source_episode_ids": ["ep1", "ep2"],
            "verb": "REFINE",
            "confidence": 0.95,
        },
    )
    assert new.supersedes == row.id
    assert repo.get_synthesis(row.id).superseded_by == new.id
