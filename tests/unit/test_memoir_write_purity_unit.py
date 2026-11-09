"""The memoir write-path purity — the repository half.

Phase 2 of the Memoir Store v1 lane (plan:
``.agents/shared/planning/ari-memoir-store/phase2-plan.md`` AC1 (the
repository surface) + AC7 (the repository half of the write purity);
pack ``test/packs/memoir_write_purity_unit_test.sh``).

PHASE 4 OWNS THE TOOL-MODULE HALF of D2.14 (the tool module references no
embedder symbol). THIS suite owns the REPOSITORY half:

* the ONE INSERT's column list is pinned BYTE-WISE and ``tsv`` is ABSENT
  from it (D2.15 / AM-13);
* EVERY UPDATE in the module is COLUMN-TARGETED — no UPDATE touches a
  record column (``body`` / ``title`` / ``importance`` / ``stars``), so
  the record columns stay write-once (DD-8 / adjudication row 5);
* the embedding write-back is ONE statement setting the four embedding
  columns TOGETHER (D2.16 / AM-15);
* the pin-admission reads PRECEDE the single INSERT (D2.15 / W3);
* the repository's PUBLIC SURFACE is exactly the closed D2.1 set — there
  is NO general-purpose update method.
"""

from __future__ import annotations

import ast
import pathlib

import pytest
from sqlmodel import SQLModel, Session, create_engine, select

from daemon.repositories.memoir.models import MemoirEpisode
from daemon.repositories.memoir.repository import (
    MEMOIR_INSERT_COLUMNS,
    MEMOIR_INSERT_SQL,
    MemoirRepository,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
REPOSITORY_PY = REPO_ROOT / "daemon" / "repositories" / "memoir" / "repository.py"

#: The D2.1 closed surface — verbatim from the plan.
#:
#: Memoir Store v2 (phase 4, T4.2) DELIBERATELY AMENDS this set with the
#: three POIGNANCY methods: ``get_pending_poignancy_episodes``,
#: ``record_poignancy_score`` and ``record_poignancy_attempt``. The
#: amendment IS the evidence trail (V2-42's discipline: the pin stays
#: byte-exact, and the reason for the change is recorded).
#:
#: WHY the surface grows: the phase-4 worker needs a queue read and two
#: column-targeted writes, and the D2.1 hazard the plan names is a
#: GENERAL-PURPOSE update (``update`` / ``patch`` / …) — a method that
#: could rewrite any column. The three new methods are exactly as
#: narrow as the embedding lane's (``get_pending_embeddings`` /
#: ``store_embedding`` / ``record_embedding_attempt``): one named queue
#: read and two named, column-targeted writes.
#:
#: WHAT the amendment does NOT do: ``RECORD_COLUMNS`` (the write-once
#: record family) stays GREEN and UNTOUCHED. The scoring writes touch
#: ``poignancy`` / ``poignancy_status`` / ``poignancy_attempts`` only —
#: ``importance`` is WRITE-FROZEN (V2-10 / §3-A2), and AC6's mutation
#: leg (pointing ``record_poignancy_score`` at ``importance``) is what
#: keeps that true.
D2_1_SURFACE = {
    "insert_episode",
    "mark_recalled",
    "set_pin",
    "clear_pin",
    "store_embedding",
    "record_embedding_attempt",
    "get_pending_poignancy_episodes",
    "record_poignancy_score",
    "record_poignancy_attempt",
    "mark_superseded",
    "get_pending_embeddings",
    "fetch_keepsakes",
    "fetch_latest",
    "get_approved_persona",
    "seed_persona_if_absent",
    "lexical_rank",
    "timeline_window",
    "count_pins",
    "keepsake_zone_tokens",
}

#: The record columns — written ONCE, by the single INSERT.
RECORD_COLUMNS = {
    "id",
    "agent_id",
    "title",
    "body",
    "created_at",
    "event_at",
    "importance",
    "stars",
}


@pytest.fixture()
def engine():
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture()
def repo(engine):
    return MemoirRepository(engine)


# ── the module's SQL constants (read from the SOURCE, never copied) ───────


def _module_sql_constants() -> dict[str, str]:
    """The module-level string constants, extracted with ``ast``."""
    tree = ast.parse(REPOSITORY_PY.read_text(encoding="utf-8"))
    constants: dict[str, str] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not isinstance(node.value, ast.Constant):
            continue
        if not isinstance(node.value.value, str):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                constants[target.id] = node.value.value
    return constants


def _set_columns(sql: str) -> set[str]:
    """The columns an UPDATE's SET clause names."""
    upper = sql.upper()
    start = upper.index("SET ") + len("SET ")
    end = upper.index(" WHERE ")
    clause = sql[start:end]
    columns: set[str] = set()
    for assignment in clause.split(","):
        name = assignment.strip().split("=")[0].strip()
        if name:
            columns.add(name.lower())
    return columns


# ── AC1 — the closed surface ──────────────────────────────────────────────


def test_the_public_surface_is_exactly_the_closed_d2_1_set():
    """AC1: the exact method set — and NO general-purpose update.

    Memoir Store v2 (phase 4, T4.2): the set carries the three
    POIGNANCY methods (the queue read and the two column-targeted
    writes). The general-purpose-update hazard is UNCHANGED — the
    amendment adds named, narrow methods, never a generic mutator.
    """
    public = {
        name
        for name, value in vars(MemoirRepository).items()
        if callable(value) and not name.startswith("_")
    }
    assert public == D2_1_SURFACE
    # The general-purpose update the plan names as the hazard must not
    # exist under ANY name.
    forbidden = {"update", "update_episode", "update_row", "set_column", "patch"}
    assert not (public & forbidden)


def test_the_persona_lane_is_still_present_and_untouched():
    """The wave-1 persona lane survives the phase-2 extension."""
    from daemon.repositories.memoir.repository import MemoirPersonaRepository

    public = {
        name
        for name, value in vars(MemoirPersonaRepository).items()
        if callable(value) and not name.startswith("_")
    }
    assert {
        "create_draft",
        "submit",
        "approve",
        "reject",
        "get_current",
        "get_version",
        "list_versions",
    } <= public


# ── AC7 — the ONE INSERT ──────────────────────────────────────────────────


def test_the_insert_column_list_is_the_d2_15_set():
    """D2.15 / AM-13 — the EXACT column set, in the adjudicated order.

    Memoir Store v2 (V2-D 1.14 / B7 / V2-42) DELIBERATELY amends this
    pin: ``poignancy_status`` joins the column list because the shipped
    ``MEMOIR_INSERT_SQL`` now writes it EXPLICITLY with a literal NULL
    in the VALUES clause (no reliance on a column-default accident).
    The amendment IS the evidence trail — the pin stays byte-exact, and
    ``RECORD_COLUMNS`` (the write-once record family) stays GREEN and
    untouched: ``poignancy_status`` is an operational column, not a
    record column.
    """
    assert MEMOIR_INSERT_COLUMNS == (
        "id",
        "agent_id",
        "title",
        "body",
        "created_at",
        "event_at",
        "importance",
        "stars",
        "is_keepsake",
        "pinned_by",
        "pinned_reason",
        "pinned_at",
        "embedding",
        "embedding_model",
        "embedding_dims",
        "embedded_at",
        "embedding_attempts",
        "embedding_error",
        "last_accessed_at",
        "access_count",
        "superseded_by",
        "poignancy_status",
    )


def test_the_insert_sql_carries_exactly_that_column_list():
    """The shipped SQL's column list is the adjudicated list — byte-wise."""
    start = MEMOIR_INSERT_SQL.index("(")
    end = MEMOIR_INSERT_SQL.index(")", start)
    columns = tuple(
        part.strip() for part in MEMOIR_INSERT_SQL[start + 1 : end].split(",")
    )
    assert columns == MEMOIR_INSERT_COLUMNS


def test_the_insert_does_not_mention_tsv():
    """``tsv`` is DB-generated and is in NO statement but the PG ranker."""
    assert "tsv" not in MEMOIR_INSERT_SQL.lower()
    constants = _module_sql_constants()
    tsv_users = [
        name
        for name, sql in constants.items()
        if "tsv" in sql.lower() and "plainto_tsquery" not in sql
    ]
    assert tsv_users == [], f"tsv leaked into {tsv_users}"


def test_the_insert_starts_the_semantic_half_empty():
    """D2.15: the semantic columns start NULL / 0 — the worker fills them.

    Memoir Store v2 (V2-D 1.14): the count is EIGHT, not seven — the
    eighth NULL is the explicit ``poignancy_status`` literal.
    """
    values = MEMOIR_INSERT_SQL[
        MEMOIR_INSERT_SQL.index("VALUES") : MEMOIR_INSERT_SQL.rindex(")")
    ]
    assert values.count("NULL") == 8  # the four embedding + error + stats + pointer + poignancy_status
    assert ":embedding" not in values
    assert ":embedding_model" not in values
    assert ":embedding_dims" not in values
    assert ":embedded_at" not in values
    assert ":embedding_attempts" not in values
    assert ":last_accessed_at" not in values
    assert ":access_count" not in values
    assert ":superseded_by" not in values
    assert ":poignancy_status" not in values


def test_the_insert_names_poignancy_status_explicitly():
    """V2-D 1.14 — the column is NAMED in the INSERT, with a literal NULL.

    The mutation leg: dropping ``poignancy_status`` from the column
    list (relying on the column default instead) flips THIS pin RED.
    """
    assert "poignancy_status" in MEMOIR_INSERT_SQL
    start = MEMOIR_INSERT_SQL.index("(")
    end = MEMOIR_INSERT_SQL.index(")", start)
    columns = tuple(
        part.strip() for part in MEMOIR_INSERT_SQL[start + 1 : end].split(",")
    )
    assert columns[-1] == "poignancy_status"
    values = MEMOIR_INSERT_SQL[
        MEMOIR_INSERT_SQL.index("VALUES") : MEMOIR_INSERT_SQL.rindex(")")
    ]
    assert values.rstrip().endswith("NULL")


def test_the_insert_writes_the_record_columns_exactly_once(repo):
    """The record columns are written ONCE — there is no second writer."""
    episode = repo.insert_episode(
        agent_id="ari", body="the preserved first day", title="first"
    )
    with Session(repo.engine) as session:
        row = session.exec(
            select(MemoirEpisode).where(MemoirEpisode.id == episode)
        ).first()
        assert row.body == "the preserved first day"
        assert row.title == "first"
        assert row.importance == 5
        assert row.stars is None


# ── AC1 — every UPDATE is column-targeted ─────────────────────────────────


def test_no_update_touches_a_record_column():
    """AC1: an UPDATE that touches ``body`` ⇒ this pin goes RED."""
    constants = _module_sql_constants()
    updates = {
        name: sql for name, sql in constants.items() if sql.lstrip().upper().startswith("UPDATE")
    }
    assert updates, "the module has no UPDATE statements — the census is vacuous"
    for name, sql in updates.items():
        touched = _set_columns(sql)
        assert not (touched & RECORD_COLUMNS), (
            f"{name} touches the record columns {touched & RECORD_COLUMNS}"
        )


def test_the_write_back_is_one_statement_setting_the_four_columns_together():
    """D2.16 / AM-15: the four embedding columns move TOGETHER."""
    constants = _module_sql_constants()
    write_back = constants["_MEMOIR_STORE_EMBEDDING_SQL"]
    assert write_back.count("UPDATE") == 1
    touched = _set_columns(write_back)
    assert {
        "embedding",
        "embedding_model",
        "embedding_dims",
        "embedded_at",
    } <= touched
    # The error is CLEARED by the same statement — a partial write would
    # leave the row re-embedded forever.
    assert "embedding_error = NULL" in write_back


def test_the_write_back_sets_the_four_columns_together(repo):
    """D2.16 / AM-15 — the behavioural half: all four columns land at once."""
    episode = repo.insert_episode(agent_id="ari", body="a vector memory")
    assert repo.store_embedding(episode, [0.1, 0.2, 0.3], "text-embedding-3-small", 3)
    with Session(repo.engine) as session:
        row = session.exec(
            select(MemoirEpisode).where(MemoirEpisode.id == episode)
        ).first()
        assert row.embedding == [0.1, 0.2, 0.3]
        assert row.embedding_model == "text-embedding-3-small"
        assert row.embedding_dims == 3
        assert row.embedded_at is not None
        assert row.embedding_error is None


def test_the_stats_update_is_one_statement_setting_two_columns():
    """D2.8: ONE column-targeted UPDATE."""
    constants = _module_sql_constants()
    stats = constants["_MEMOIR_MARK_RECALLED_SQL"]
    assert stats.count("UPDATE") == 1
    touched = _set_columns(stats)
    assert touched == {"last_accessed_at", "access_count"}


def test_the_pin_writes_and_clears_the_whole_pin_set():
    """N-1: the pin columns are written and cleared as a SET."""
    constants = _module_sql_constants()
    touched = _set_columns(constants["_MEMOIR_SET_PIN_SQL"])
    assert touched == {"is_keepsake", "pinned_by", "pinned_reason", "pinned_at"}
    cleared = constants["_MEMOIR_CLEAR_PIN_SQL"]
    assert "pinned_by = NULL" in cleared
    assert "pinned_reason = NULL" in cleared
    assert "pinned_at = NULL" in cleared


# ── AC7 — the admission reads PRECEDE the INSERT ──────────────────────────


def test_the_admission_reads_precede_the_insert_in_the_source():
    """D2.15 / W3: the reads come FIRST — an inversion would write the
    13th keepsake and THEN refuse it."""
    tree = ast.parse(REPOSITORY_PY.read_text(encoding="utf-8"))
    function = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "insert_episode":
            function = node
            break
    assert function is not None, "insert_episode was not found"

    admit_line = None
    insert_line = None
    for node in ast.walk(function):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == "_admit_pin":
            admit_line = node.lineno
        if (
            isinstance(func, ast.Attribute)
            and func.attr == "execute"
            and node.args
            and isinstance(node.args[0], ast.Name)
            and node.args[0].id == "_MEMOIR_INSERT_STATEMENT"
        ):
            insert_line = node.lineno
    assert admit_line is not None, "the admission call was not found"
    assert insert_line is not None, "the INSERT call was not found"
    assert admit_line < insert_line, (
        f"the admission (line {admit_line}) must precede the INSERT "
        f"(line {insert_line})"
    )


def test_a_refused_keepsake_write_leaves_no_row_behind(repo):
    """The behavioural half of the same rule."""
    from daemon.repositories.memoir.repository import MemoirAdmissionError

    for _ in range(12):
        repo.insert_episode(
            agent_id="ari",
            body="a small moment",
            is_keepsake=True,
            pinned_by="empress",
            pinned_reason="worth keeping",
        )
    with pytest.raises(MemoirAdmissionError):
        repo.insert_episode(
            agent_id="ari",
            body="one too many",
            is_keepsake=True,
            pinned_by="emperor",
            pinned_reason="one too many",
        )
    with Session(repo.engine) as session:
        assert len(session.exec(select(MemoirEpisode)).all()) == 12
