"""Critical-note id resolution — in-memory SQLite, no PG needed."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

import daemon.repositories.project.models  # noqa: F401
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


def test_noncanonical_variant_listed(repo, engine, project_id):
    variant = "d0371008-a55d-4b86-b831d-1c76b381a920"  # 8-4-4-5-12
    _raw_insert(engine, project_id, variant)
    notes = repo.list_critical_notes(project_id)
    assert len(notes) == 1


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
