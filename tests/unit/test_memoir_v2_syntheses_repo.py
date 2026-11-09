"""Memoir Store v2 — the syntheses repository (the SQLite lane).

Phase 1 of the Memoir Store v2 lane (plan:
``.agents/shared/planning/ari-memoir-store/v2-phase1-plan.md`` AC5;
pack ``test/packs/memoir_v2_syntheses_repo_test.sh``).

What this suite pins:

* **MANDATORY CITATIONS** — a synthesis without source episodes is
  refused with ``ValueError`` (both ``None`` and ``[]``). This is the
  AC6 mutation leg: removing the citation check flips AC5 RED;
* **THE CLOSED VERB VOCABULARY** — a verb outside ``SYNTHESIS_VERBS``
  is refused with ``ValueError`` (the same vocabulary the model's CHECK
  constraint enforces in the database);
* **THE SCAN-WINDOW DEDUP** (SF4 / V2-30) — the same claim written
  twice inside the 2-day window returns the EXISTING row and does not
  grow a second copy. The comparison key is NFC + whitespace-collapse +
  casefold, so a re-worded but identical claim is the same claim;
* **THE ATOMIC SUPERSESSION WRITER** (V2-D 1.8) — the insert, the old
  row's ``superseded_by`` and the new row's ``supersedes`` land TOGETHER
  or not at all;
* the active listing excludes the superseded rows.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlmodel import SQLModel, Session, create_engine

from daemon.repositories.memoir.models import SYNTHESIS_VERBS, MemoirSynthesis
from daemon.repositories.memoir.syntheses_repository import (
    MemoirSynthesesRepository,
    normalize_claim,
)

pytestmark = pytest.mark.filterwarnings("ignore::UserWarning")


@pytest.fixture()
def engine():
    eng = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture()
def repo(engine):
    return MemoirSynthesesRepository(engine)


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ── mandatory citations ──────────────────────────────────────────────────


def test_a_synthesis_without_citations_is_refused(repo):
    with pytest.raises(ValueError) as excinfo:
        repo.create_synthesis("ari", "the Emperor is patient", None, "CREATE", 0.9)
    assert "source_episode_ids" in str(excinfo.value)


def test_a_synthesis_with_an_empty_citation_list_is_refused(repo):
    with pytest.raises(ValueError) as excinfo:
        repo.create_synthesis("ari", "the Emperor is patient", [], "CREATE", 0.9)
    assert "source_episode_ids" in str(excinfo.value)


def test_a_cited_synthesis_is_written(repo):
    row = repo.create_synthesis(
        "ari", "the Emperor is patient", ["ep1", "ep2"], "CREATE", 0.9
    )
    assert row.claim == "the Emperor is patient"
    assert row.verb == "CREATE"
    assert row.confidence == 0.9
    assert list(row.source_episode_ids) == ["ep1", "ep2"]
    assert row.superseded_by is None
    assert row.created_at is not None


# ── the closed verb vocabulary ───────────────────────────────────────────


def test_a_verb_outside_the_vocabulary_is_refused(repo):
    with pytest.raises(ValueError) as excinfo:
        repo.create_synthesis("ari", "a claim", ["ep1"], "BELIEVE", 0.9)
    assert "BELIEVE" in str(excinfo.value)


def test_every_verb_of_the_vocabulary_is_accepted(repo):
    for index, verb in enumerate(sorted(SYNTHESIS_VERBS)):
        row = repo.create_synthesis("ari", f"claim number {index}", ["ep1"], verb, 0.5)
        assert row.verb == verb


# ── the scan-window dedup ────────────────────────────────────────────────


def test_a_duplicate_claim_inside_the_window_returns_the_existing_row(repo):
    first = repo.create_synthesis("ari", "the Emperor is patient", ["ep1"], "CREATE", 0.9)
    second = repo.create_synthesis("ari", "the Emperor is patient", ["ep2"], "CREATE", 0.9)
    assert second.id == first.id
    assert len(repo.list_active_syntheses("ari")) == 1


def test_the_dedup_key_normalizes_case_and_whitespace(repo):
    first = repo.create_synthesis("ari", "The Emperor   is patient", ["ep1"], "CREATE", 0.9)
    second = repo.create_synthesis("ari", "the emperor is patient", ["ep2"], "CREATE", 0.9)
    assert second.id == first.id


def test_the_dedup_key_normalizes_unicode_to_nfc(repo):
    # "é" as ONE code point (NFC) versus "e" + U+0301 (NFD) — the same
    # character, so the same claim.
    composed = "the Emperor is caf\u00e9"
    decomposed = "the Emperor is cafe\u0301"
    assert composed != decomposed
    assert normalize_claim(composed) == normalize_claim(decomposed)
    first = repo.create_synthesis("ari", composed, ["ep1"], "CREATE", 0.9)
    second = repo.create_synthesis("ari", decomposed, ["ep2"], "CREATE", 0.9)
    assert second.id == first.id


def test_a_claim_outside_the_window_is_written_again(repo):
    first = repo.create_synthesis("ari", "the Emperor is patient", ["ep1"], "CREATE", 0.9)
    # Age the first row past the 2-day window.
    with Session(repo.engine) as session:
        row = session.get(MemoirSynthesis, first.id)
        row.created_at = _now() - timedelta(days=3)
        session.add(row)
        session.commit()
    second = repo.create_synthesis("ari", "the Emperor is patient", ["ep2"], "CREATE", 0.9)
    assert second.id != first.id
    assert len(repo.list_active_syntheses("ari")) == 2


def test_a_superseded_claim_does_not_block_a_new_write(repo):
    first = repo.create_synthesis("ari", "the Emperor is patient", ["ep1"], "CREATE", 0.9)
    repo.supersede_with(
        "ari",
        first.id,
        {
            "claim": "the Emperor is very patient",
            "source_episode_ids": ["ep1"],
            "verb": "REFINE",
            "confidence": 0.95,
        },
    )
    second = repo.create_synthesis("ari", "the Emperor is patient", ["ep2"], "CREATE", 0.9)
    assert second.id != first.id


def test_the_dedup_is_scoped_to_one_agent(repo):
    first = repo.create_synthesis("ari", "the Emperor is patient", ["ep1"], "CREATE", 0.9)
    second = repo.create_synthesis("zoe", "the Emperor is patient", ["ep1"], "CREATE", 0.9)
    assert second.id != first.id


# ── the atomic supersession writer ───────────────────────────────────────


def test_supersede_with_links_both_pointers(repo):
    old = repo.create_synthesis("ari", "the Emperor is patient", ["ep1"], "CREATE", 0.9)
    new = repo.supersede_with(
        "ari",
        old.id,
        {
            "claim": "the Emperor is very patient",
            "source_episode_ids": ["ep1", "ep2"],
            "verb": "REFINE",
            "confidence": 0.95,
        },
    )
    assert new.id != old.id
    assert new.supersedes == old.id
    assert new.superseded_by is None
    assert new.source_episode_ids == ["ep1", "ep2"]
    reloaded = repo.get_synthesis(old.id)
    assert reloaded is not None
    assert reloaded.superseded_by == new.id


def test_supersede_with_is_atomic_the_insert_and_the_pointer_land_together(repo):
    """The insert, the old row's pointer and the new row's back-pointer
    are ONE transaction — a failure in any part leaves NOTHING behind."""
    old = repo.create_synthesis("ari", "the Emperor is patient", ["ep1"], "CREATE", 0.9)
    before = len(repo.list_active_syntheses("ari"))
    with pytest.raises(ValueError):
        repo.supersede_with(
            "ari",
            old.id,
            {
                "claim": "a replacement without citations",
                "source_episode_ids": [],
                "verb": "REFINE",
                "confidence": 0.95,
            },
        )
    # The old row is UNCHANGED — no orphan new row, no half-linked pair.
    reloaded = repo.get_synthesis(old.id)
    assert reloaded is not None and reloaded.superseded_by is None
    assert len(repo.list_active_syntheses("ari")) == before


def test_supersede_with_refuses_an_unknown_old_row(repo):
    with pytest.raises(KeyError):
        repo.supersede_with(
            "ari",
            "no-such-synthesis",
            {
                "claim": "a claim",
                "source_episode_ids": ["ep1"],
                "verb": "CREATE",
                "confidence": 0.5,
            },
        )


def test_supersede_with_refuses_a_second_supersession(repo):
    old = repo.create_synthesis("ari", "the Emperor is patient", ["ep1"], "CREATE", 0.9)
    repo.supersede_with(
        "ari",
        old.id,
        {
            "claim": "first replacement",
            "source_episode_ids": ["ep1"],
            "verb": "REFINE",
            "confidence": 0.9,
        },
    )
    with pytest.raises(ValueError):
        repo.supersede_with(
            "ari",
            old.id,
            {
                "claim": "second replacement",
                "source_episode_ids": ["ep1"],
                "verb": "REFINE",
                "confidence": 0.9,
            },
        )


def test_supersede_with_refuses_an_out_of_vocabulary_verb(repo):
    old = repo.create_synthesis("ari", "the Emperor is patient", ["ep1"], "CREATE", 0.9)
    with pytest.raises(ValueError):
        repo.supersede_with(
            "ari",
            old.id,
            {
                "claim": "a replacement",
                "source_episode_ids": ["ep1"],
                "verb": "BELIEVE",
                "confidence": 0.9,
            },
        )


# ── the reads ────────────────────────────────────────────────────────────


def test_get_synthesis_returns_none_for_an_unknown_id(repo):
    assert repo.get_synthesis("no-such-synthesis") is None


def test_get_synthesis_returns_the_row(repo):
    row = repo.create_synthesis("ari", "the Emperor is patient", ["ep1"], "CREATE", 0.9)
    fetched = repo.get_synthesis(row.id)
    assert fetched is not None
    assert fetched.id == row.id
    assert fetched.claim == "the Emperor is patient"


def test_the_active_listing_excludes_the_superseded_rows(repo):
    first = repo.create_synthesis("ari", "the Emperor is patient", ["ep1"], "CREATE", 0.9)
    repo.create_synthesis("ari", "the garden is quiet", ["ep2"], "CREATE", 0.8)
    repo.supersede_with(
        "ari",
        first.id,
        {
            "claim": "the Emperor is very patient",
            "source_episode_ids": ["ep1"],
            "verb": "REFINE",
            "confidence": 0.95,
        },
    )
    active = repo.list_active_syntheses("ari")
    assert first.id not in {row.id for row in active}
    assert {row.claim for row in active} == {"the Emperor is very patient", "the garden is quiet"}


def test_the_active_listing_is_scoped_to_one_agent(repo):
    repo.create_synthesis("ari", "the Emperor is patient", ["ep1"], "CREATE", 0.9)
    assert repo.list_active_syntheses("zoe") == []


def test_the_active_listing_honours_its_limit(repo):
    for index in range(5):
        repo.create_synthesis("ari", f"claim number {index}", ["ep1"], "CREATE", 0.5)
    assert len(repo.list_active_syntheses("ari", limit=2)) == 2


# ── the concurrent double-claim guard (the phase-2 hardening) ───────────
#
# The reviewer note: "Phase 2 gives syntheses their first real writers;
# add the concurrency-leg test (second supersede attempt on the same old
# row must fail loudly, not silently double-link)."
#
# The hardening: the old-row UPDATE carries ``WHERE superseded_by IS
# NULL`` and ASSERTS the affected rowcount is 1 — the read above it is a
# hint, not the arbitration.


def test_a_second_supersede_on_the_same_old_row_fails_loudly(repo):
    """The second attempt is REFUSED — never a silent double-link."""
    old = repo.create_synthesis("ari", "the Emperor is patient", ["ep1"], "CREATE", 0.9)
    first = repo.supersede_with(
        "ari",
        old.id,
        {
            "claim": "the Emperor is very patient",
            "source_episode_ids": ["ep1"],
            "verb": "REFINE",
            "confidence": 0.95,
        },
    )
    with pytest.raises((ValueError, RuntimeError)) as excinfo:
        repo.supersede_with(
            "ari",
            old.id,
            {
                "claim": "the Emperor is impatient",
                "source_episode_ids": ["ep1"],
                "verb": "CORRECT",
                "confidence": 0.6,
            },
        )
    assert "supersed" in str(excinfo.value).lower()
    # NO FORK: the chain still has exactly two rows and one pointer.
    rows = repo.list_active_syntheses("ari", limit=50)
    assert len(rows) == 1
    assert rows[0].id == first.id
    assert rows[0].supersedes == old.id


def test_the_pointer_update_is_guarded_by_the_superseded_by_predicate(
    engine, repo
):
    """THE RACE: a rival writer claims the row between the read and the
    write ⇒ the update affects ZERO rows ⇒ the writer fails LOUDLY and
    the transaction rolls back (no orphan, no double-link)."""
    from sqlalchemy import event, text as sql_text

    old = repo.create_synthesis("ari", "the Emperor is patient", ["ep1"], "CREATE", 0.9)
    fired = {"n": 0}

    def _rival_claims_the_row(conn, cursor, statement, parameters, context, executemany):
        if fired["n"] == 0 and statement.lstrip().upper().startswith(
            "UPDATE MEMOIR_SYNTHESES"
        ):
            fired["n"] += 1
            # The RIVAL writer wins the row between our SELECT and our
            # UPDATE — the exact interleaving the guard exists for.
            conn.execute(
                sql_text(
                    "UPDATE memoir_syntheses SET superseded_by='rival' WHERE id=:id"
                ),
                {"id": old.id},
            )

    event.listen(engine, "before_cursor_execute", _rival_claims_the_row)
    try:
        with pytest.raises(RuntimeError) as excinfo:
            repo.supersede_with(
                "ari",
                old.id,
                {
                    "claim": "the Emperor is very patient",
                    "source_episode_ids": ["ep1"],
                    "verb": "REFINE",
                    "confidence": 0.95,
                },
            )
    finally:
        event.remove(engine, "before_cursor_execute", _rival_claims_the_row)

    assert fired["n"] == 1, "the rival writer never ran"
    assert "claimed by another writer" in str(excinfo.value)
    # THE ROLLBACK: the new row was NOT orphaned, and the old row keeps
    # its pointer (the rival's claim rolled back with the transaction).
    all_rows = repo.list_active_syntheses("ari", limit=50)
    assert len(all_rows) == 1
    assert all_rows[0].id == old.id
    assert all_rows[0].superseded_by is None
