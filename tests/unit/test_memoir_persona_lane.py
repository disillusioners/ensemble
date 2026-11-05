"""The persona lane — the transition vocabulary and the human gate.

Phase 1 of the Memoir Store v1 lane (plan:
``.agents/shared/planning/ari-memoir-store/phase1-plan.md`` AC5; pack
``test/packs/memoir_persona_lane_unit_test.sh``).

What this suite pins:

* the MIRRORED transition vocabulary: ``draft -> pending_approval ->
  approved`` is permitted, ``draft -> approved`` is REFUSED (the human
  lane is not optional — DD-9);
* ``approve()`` with a non-session actor raises the house
  ``HumanLaneRequiredError`` (D1.12 / AM-17): the LANE is the source of
  authority, not the scope claim;
* ``submit()`` exists as a seam but has NO caller in v1.

The MUTATION leg lives in the pack: the human-lane guard removed ⇒ the
approve-without-a-human test must go RED.
"""

from __future__ import annotations

import pytest
from sqlmodel import SQLModel, Session, create_engine, select

from daemon.repositories.memoir.models import (
    PERSONA_VALID_TRANSITIONS,
    MemoirPersona,
    MemoirPersonaVersion,
)
from daemon.repositories.memoir.repository import (
    MemoirPersonaRepository,
    MemoirTransitionError,
)
from daemon.services.prompt_version_service import HumanLaneRequiredError


class _Actor:
    """A minimal actor carrying the house lane discriminator."""

    def __init__(self, auth_method: str) -> None:
        self.auth_method = auth_method


@pytest.fixture()
def repo():
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    yield MemoirPersonaRepository(engine)
    engine.dispose()


# ── the mirrored vocabulary ───────────────────────────────────────────────


def test_the_vocabulary_mirrors_the_prompt_version_lane():
    """The persona vocabulary is the house one, not a second dialect."""
    from daemon.repositories.prompt_version.repository import (
        VALID_TRANSITIONS as HOUSE_VALID_TRANSITIONS,
    )

    assert PERSONA_VALID_TRANSITIONS == HOUSE_VALID_TRANSITIONS
    assert ("draft", "pending_approval") in PERSONA_VALID_TRANSITIONS
    assert ("pending_approval", "approved") in PERSONA_VALID_TRANSITIONS
    assert ("draft", "approved") not in PERSONA_VALID_TRANSITIONS


# ── the transitions ───────────────────────────────────────────────────────


def test_draft_to_pending_approval_to_approved(repo):
    """The happy path through the human lane."""
    draft = repo.create_draft("ari", "# A new self")
    assert draft.status == "draft"
    assert draft.version == 1

    pending = repo.submit(draft.id, actor=_Actor("bearer"))
    assert pending.status == "pending_approval"
    assert pending.submitted_at is not None

    approved = repo.approve(pending.id, actor=_Actor("session"))
    assert approved.status == "approved"
    assert approved.approved_at is not None


def test_draft_to_approved_is_refused(repo):
    """The human lane is NOT optional — the transition is not permitted."""
    draft = repo.create_draft("ari", "# A new self")
    with pytest.raises(MemoirTransitionError):
        repo.approve(draft.id, actor=_Actor("session"))


def test_the_approved_version_becomes_the_current_one(repo):
    draft = repo.create_draft("ari", "# A new self")
    pending = repo.submit(draft.id, actor=_Actor("bearer"))
    approved = repo.approve(pending.id, actor=_Actor("session"))

    current = repo.get_current("ari")
    assert current is not None
    assert current.id == approved.id

    with Session(repo.engine) as session:
        pointer = session.exec(select(MemoirPersona)).first()
        assert pointer.current_version_id == approved.id


def test_reject_is_a_permitted_transition(repo):
    draft = repo.create_draft("ari", "# A new self")
    pending = repo.submit(draft.id, actor=_Actor("bearer"))
    rejected = repo.reject(pending.id, actor=_Actor("session"))
    assert rejected.status == "rejected"


def test_a_new_draft_takes_the_next_version_number(repo):
    first = repo.create_draft("ari", "# One")
    second = repo.create_draft("ari", "# Two")
    assert first.version == 1
    assert second.version == 2


# ── the human-lane guard (MUTATION-SENSITIVE) ────────────────────────────


def test_approve_without_a_human_actor_raises(repo):
    """MUTATION-SENSITIVE: removing the guard makes this RED."""
    draft = repo.create_draft("ari", "# A new self")
    pending = repo.submit(draft.id, actor=_Actor("bearer"))
    with pytest.raises(HumanLaneRequiredError):
        repo.approve(pending.id, actor=_Actor("bearer"))


def test_approve_with_an_internal_actor_raises(repo):
    draft = repo.create_draft("ari", "# A new self")
    pending = repo.submit(draft.id, actor=_Actor("bearer"))
    with pytest.raises(HumanLaneRequiredError):
        repo.approve(pending.id, actor=_Actor("internal"))


def test_approve_with_an_actor_without_the_attribute_raises(repo):
    """A missing ``auth_method`` is refused, never treated as human."""
    draft = repo.create_draft("ari", "# A new self")
    pending = repo.submit(draft.id, actor=_Actor("bearer"))
    with pytest.raises(HumanLaneRequiredError):
        repo.approve(pending.id, actor=object())


def test_reject_without_a_human_actor_raises(repo):
    draft = repo.create_draft("ari", "# A new self")
    pending = repo.submit(draft.id, actor=_Actor("bearer"))
    with pytest.raises(HumanLaneRequiredError):
        repo.reject(pending.id, actor=_Actor("bearer"))


def test_the_guard_uses_the_house_error_class(repo):
    """The house class — so the HTTP layer's 403 mapping keeps working."""
    draft = repo.create_draft("ari", "# A new self")
    pending = repo.submit(draft.id, actor=_Actor("bearer"))
    with pytest.raises(HumanLaneRequiredError) as excinfo:
        repo.approve(pending.id, actor=_Actor("bearer"))
    assert excinfo.value.actor_auth_method == "bearer"
    assert excinfo.value.version_id == pending.id


def test_a_refused_approval_leaves_the_version_pending(repo):
    """A refused approval changes NOTHING (the durable self is intact)."""
    draft = repo.create_draft("ari", "# A new self")
    pending = repo.submit(draft.id, actor=_Actor("bearer"))
    with pytest.raises(HumanLaneRequiredError):
        repo.approve(pending.id, actor=_Actor("bearer"))
    assert repo.get_version(pending.id).status == "pending_approval"


# ── the seam has no v1 caller ─────────────────────────────────────────────


def test_submit_is_a_seam_without_a_v1_caller():
    """The v1 persona is read-only — nothing outside the package calls it."""
    import subprocess
    import pathlib

    repo_root = pathlib.Path(__file__).resolve().parents[2]
    result = subprocess.run(
        ["git", "grep", "-l", "MemoirPersonaRepository", "--", "daemon/"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        timeout=60,
    )
    hits = [line for line in result.stdout.splitlines() if line.strip()]
    outside = [
        hit
        for hit in hits
        if not hit.startswith("daemon/repositories/memoir/")
    ]
    assert not outside, f"the persona repository has a caller: {outside}"


def test_get_current_is_none_without_a_persona(repo):
    assert repo.get_current("ari") is None
    assert repo.get_current("nobody") is None
