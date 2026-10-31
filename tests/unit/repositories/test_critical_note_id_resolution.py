"""Critical-note id resolution — in-memory SQLite, no PG needed."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

import daemon.repositories.project.models  # noqa: F401
from daemon.repositories.project.critical_note_ids import (
    AmbiguousCriticalNoteId,
    MissingCriticalNoteId,
    ResolvedCriticalNoteId,
    stored_id_or_none,
)
from daemon.repositories.project.repository import SQLModelProjectRepository


@pytest.fixture
def engine():
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng)
    return eng


@pytest.fixture
def repo(engine):
    return SQLModelProjectRepository(engine)


@pytest.fixture
def project_id(repo):
    p = repo.create(name="id-res-proj")
    return p.project_id


def _raw_insert(engine, project_id, nid):
    with engine.connect() as conn:
        conn.execute(
            text(
                "INSERT INTO critical_notes (id, project_id, created_at, updated_at, "
                "source_agent, category, priority, summary, pinned, last_reviewed_at) "
                "VALUES (:id, :pid, :c, :u, 'leader', 'risk', 'high', 's', 0, :u)"
            ),
            {"id": nid, "pid": project_id, "c": "2026-01-01T00:00:00", "u": "2026-01-01T00:00:00"},
        )
        conn.commit()


CANONICAL = "d0371008-a55d-4b86-831d-1c76b381a920"


@pytest.mark.parametrize("variant", [
    CANONICAL + " ",          # trailing space
    "\u200b" + CANONICAL,     # leading zero-width
    CANONICAL.upper(),        # uppercase
    CANONICAL.replace("-", ""),  # 32-hex-no-hyphens
])
def test_variant_resolves_and_removable(repo, engine, project_id, variant):
    _raw_insert(engine, project_id, variant)
    # list returns the row
    notes = repo.list_critical_notes(project_id)
    assert len(notes) == 1
    # remove by canonical form -> True
    assert repo.remove_critical_note(project_id, CANONICAL) is True
    assert repo.list_critical_notes(project_id) == []


def test_malformed_shape_is_asserted_limitation_not_resolved(
    repo, engine, project_id
):
    """W1(b): the 8-4-4-5-12 shape is an ASSERTED LIMITATION, not resolved.

    A structurally malformed id (wrong hex count) never auto-resolves:
    ``uuid.UUID`` rejects it and the fingerprint arm requires exactly 32
    hex on BOTH sides. Pins the full contract:

    * repo get/remove with the CANONICAL form → None / False;
    * the tool not-found error fires the diagnostics-only near-match
      hint carrying ``repr()`` of the exact stored id;
    * remove(entry_id=<exact stored bytes>) → True (leader-explicit).
    """
    from daemon.tools.critical_notes import create_critical_notes_tools

    variant = "d0371008-a55d-4b86-b831d-1c76b381a920"  # 8-4-4-5-12 (33 hex)
    _raw_insert(engine, project_id, variant)
    # The row is listable (non-raising reads are the class fix).
    notes = repo.list_critical_notes(project_id)
    assert len(notes) == 1

    # Canonical form does NOT resolve to it (limitation, asserted).
    assert repo.get_critical_note(project_id, CANONICAL) is None
    assert repo.remove_critical_note(project_id, CANONICAL) is False

    # Tool not-found error carries the repr()-escaped stored id hint.
    tools = {
        t.name: t
        for t in create_critical_notes_tools(repo, agent_id="leader")
    }
    result = tools["project_cn_remove"].invoke(
        {"project_id": project_id, "entry_id": CANONICAL}
    )
    assert "error" in result
    assert "Near-match(es):" in result["error"]
    assert repr(variant) in result["error"]

    # Exact-bytes retry removes it (never auto-applied — explicit).
    assert repo.remove_critical_note(project_id, variant) is True
    assert repo.list_critical_notes(project_id) == []


def test_junk_prefixed_id_never_fingerprint_matches(repo, engine, project_id):
    """W2: non-hex padding/junk survives the OLD fingerprint stripper.

    The fingerprint arm strips ONLY whitespace + separators and requires
    BOTH sides to fullmatch 32-hex — a junk-prefixed request matches
    nothing. A hyphenless 32-hex variant still DOES resolve.
    """
    _raw_insert(engine, project_id, CANONICAL)
    junk = "zz-" + CANONICAL.replace("-", "")
    assert repo.get_critical_note(project_id, junk) is None
    assert repo.remove_critical_note(project_id, junk) is False
    assert len(repo.list_critical_notes(project_id)) == 1
    # Hyphenless 32-hex remains a representation variant.
    assert repo.remove_critical_note(project_id, CANONICAL.replace("-", "")) is True


def test_predicate_rejects_junk_and_blank_unit_level():
    """Unit pins for the matching predicate (W2/W3 arms)."""
    from daemon.repositories.project.critical_note_ids import (
        critical_note_id_matches,
        critical_note_hex_fingerprint,
        normalize_critical_note_id,
    )

    canon = CANONICAL
    # W2: junk prefix survives no stripper → never matches.
    assert critical_note_id_matches(canon, "zz-" + canon.replace("-", "")) is False
    assert critical_note_hex_fingerprint("zz-" + canon.replace("-", "")) is None
    # W2 positive: representation variants still match.
    assert critical_note_id_matches(canon, canon.replace("-", "")) is True
    assert critical_note_id_matches(canon, canon.upper()) is True
    assert critical_note_hex_fingerprint(canon.replace("-", "")) == (
        canon.replace("-", "")
    )
    assert critical_note_hex_fingerprint(canon) == canon.replace("-", "")
    # W3: whitespace-only normalizes to "" and matches NOTHING — not
    # even a stored blank id with identical bytes beyond arm 1.
    assert normalize_critical_note_id("   ") == ""
    assert critical_note_id_matches("", "   ") is False
    assert critical_note_id_matches("   ", "") is False
    # Exact byte equality (arm 1) is untouched.
    assert critical_note_id_matches("x", "x") is True


def test_blank_request_never_deletes_stored_empty_pk(repo, engine, project_id):
    """W3: a request that normalizes to "" refuses BEFORE the scan.

    A stored "" PK (legacy shape, seeded raw) must never be deletable
    via a whitespace-only id: remove returns not-found and the row
    survives.
    """
    _raw_insert(engine, project_id, "")
    _raw_insert(engine, project_id, CANONICAL)
    assert repo.remove_critical_note(project_id, "   ") is False
    assert repo.remove_critical_note(project_id, "\t\u200b") is False
    # Both rows untouched.
    assert len(repo.list_critical_notes(project_id)) == 2
    # The resolver refuses with the empty_after_normalize degraded line.
    resolution = repo.resolve_critical_note_id(project_id, "   ")
    assert isinstance(resolution, MissingCriticalNoteId)


def test_exact_hit_with_representation_twin_is_ambiguous(repo, engine, project_id):
    """W4: the collision check runs on the EXACT hit too.

    Canonical row + uppercase twin → remove(CANON) refuses (ambiguous —
    nothing changes) even though the requested id byte-matches one row.
    A case-variant twin must never let a remove silently pick one of two.
    """
    _raw_insert(engine, project_id, CANONICAL)
    _raw_insert(engine, project_id, CANONICAL.upper())
    resolution = repo.resolve_critical_note_id(project_id, CANONICAL)
    assert isinstance(resolution, AmbiguousCriticalNoteId)
    assert resolution.match_count == 2
    assert repo.remove_critical_note(project_id, CANONICAL) is False
    assert repo.remove_critical_note(project_id, CANONICAL.upper()) is False
    # Both rows survive.
    assert len(repo.list_critical_notes(project_id)) == 2


def test_ambiguous_refusal_logs_degraded_line(repo, engine, project_id, caplog):
    """W5 pin (REPO SEAM): the refuse path logs stage=id_resolve
    reason=ambiguous under the [CriticalNotes:Degraded] prefix."""
    import logging

    _raw_insert(engine, project_id, CANONICAL)
    _raw_insert(engine, project_id, CANONICAL.upper())
    with caplog.at_level(logging.INFO, logger="daemon.repositories.project.repository"):
        repo.remove_critical_note(project_id, CANONICAL)  # refuses
    degraded = [
        r for r in caplog.records
        if "stage=id_resolve" in r.getMessage()
        and "reason=ambiguous" in r.getMessage()
        and "[CriticalNotes:Degraded]" in r.getMessage()
    ]
    assert degraded, "expected [CriticalNotes:Degraded] stage=id_resolve reason=ambiguous"
    assert len(repo.list_critical_notes(project_id)) == 2


def test_resolve_probe_tri_state_shapes(repo, engine, project_id):
    """W5: the public probe returns the three result types."""
    _raw_insert(engine, project_id, CANONICAL)
    resolved = repo.resolve_critical_note_id(project_id, CANONICAL.upper())
    assert isinstance(resolved, ResolvedCriticalNoteId)
    assert resolved.stored_id == CANONICAL
    assert stored_id_or_none(resolved) == CANONICAL

    _raw_insert(engine, project_id, CANONICAL + " ")
    amb = repo.resolve_critical_note_id(project_id, CANONICAL)
    assert isinstance(amb, AmbiguousCriticalNoteId)
    assert stored_id_or_none(amb) is None

    missing = repo.resolve_critical_note_id(project_id, "00000000-0000-0000-0000-000000000000")
    assert isinstance(missing, MissingCriticalNoteId)
    assert stored_id_or_none(missing) is None


def test_ambiguity_returns_none(repo, engine, project_id):
    # two rows whose fingerprints/normalizations collide
    _raw_insert(engine, project_id, CANONICAL.upper())
    _raw_insert(engine, project_id, CANONICAL + " ")
    # resolver via get should return None due to ambiguity
    assert repo.get_critical_note(project_id, CANONICAL) is None
    # remove should not delete (ambiguous -> False)
    assert repo.remove_critical_note(project_id, CANONICAL) is False
    assert len(repo.list_critical_notes(project_id)) == 2


def test_get_update_pin_supersede_resolve(repo, engine, project_id):
    variant = CANONICAL.upper()
    _raw_insert(engine, project_id, variant)
    # get
    n = repo.get_critical_note(project_id, CANONICAL)
    assert n is not None
    # update
    upd = repo.update_critical_note(project_id, CANONICAL, summary="updated")
    assert upd is not None
    assert upd.summary == "updated"
    # pin
    pinned = repo.pin_critical_note(project_id, CANONICAL, pinned=True)
    assert pinned is not None
    assert pinned.pinned is True
    # supersede needs a second row
    _raw_insert(engine, project_id, "11111111-2222-3333-4444-555555555555")
    succ = repo.supersede_critical_note(project_id, CANONICAL, "11111111-2222-3333-4444-555555555555")
    assert succ is not None


def test_supersede_old_equals_new_guard(repo, engine, project_id):
    _raw_insert(engine, project_id, CANONICAL)
    # same resolved id
    assert repo.supersede_critical_note(project_id, CANONICAL, CANONICAL + " ") is None


def test_pk_immutable_on_load_and_write(repo, engine, project_id):
    """ORM load preserves stored bytes; writes never rewrite the PK.

    Guard against the "silent PK rewrite" class: a future edit to
    ``__init__``/validator must not normalize the stored id in place.
    """
    variant = CANONICAL + "\u200b"  # trailing zero-width space
    _raw_insert(engine, project_id, variant)

    # ORM load resolves the canonical request to the stored variant row.
    n = repo.get_critical_note(project_id, CANONICAL)
    assert n is not None

    # Writes resolve and apply, without rewriting the id.
    assert repo.pin_critical_note(project_id, CANONICAL, True, "leader") is not None
    assert repo.update_critical_note(project_id, CANONICAL, summary="changed") is not None

    # Raw SQL read: PK is byte-identical to the original variant.
    with engine.connect() as conn:
        raw_id = conn.execute(
            text("SELECT id FROM critical_notes WHERE project_id = :p"),
            {"p": project_id},
        ).scalar_one()
    assert raw_id == variant
    assert "\u200b" in raw_id

    # ORM-load path does not mutate either.
    listed = repo.list_critical_notes(project_id)
    assert len(listed) == 1
    assert "\u200b" in listed[0].id
