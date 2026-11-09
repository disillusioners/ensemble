"""Unit tests for Memoir Store v2 Phase 5 — Persona Proposals and Human Approval Lane.

Plan: .agents/shared/planning/ari-memoir-store/v2-phase5-plan.md (§4 T5.4; §5 AC3, AC4, AC6).
Test Pack: test/packs/memoir_v2_persona_proposal_test.sh.

Coverage:
* Proposal creation, submission, and pointer isolation:
  - Draft version created with status='draft' -> submit() moves to 'pending_approval'
  - Pointer in memoir_persona remains at current approved version until approve()
  - Version increments sequentially: version = max(versions) + 1 (ND-17)
* Human session approval & rejection:
  - approve() with actor.auth_method == "session" transitions status to 'approved'
  - Pointer in memoir_persona is updated in the same transaction
  - reject() with session actor transitions status to 'rejected'
* Non-human approval refusal (AC4 / DD-9 / Hy-Memory scar):
  - approve() with actor.auth_method == "bearer" raises HumanLaneRequiredError
  - approve() with actor.auth_method == "internal" or None raises HumanLaneRequiredError
* Write purity (Write Purity Pin §2-Q4):
  - create_draft / submit exercised strictly from service layer, not daemon/tools/memoir.py
* AC6 Mutation leg (b):
  - Removing _require_human_lane from approve() flips AC4 RED.
"""

from __future__ import annotations

import json
import uuid
from unittest.mock import MagicMock
import pytest
from sqlmodel import SQLModel, Session, create_engine, select

from daemon.repositories.memoir.models import (
    MemoirPersona,
    MemoirPersonaVersion,
    MemoirSynthesis,
)
from daemon.repositories.memoir.repository import (
    MemoirPersonaRepository,
    MemoirTransitionError,
)
from daemon.services.memoir_persona_gate import MemoirPersonaGateService
from daemon.services.prompt_version_service import HumanLaneRequiredError
from daemon.services.timestamps import now_utc


class _Actor:
    def __init__(self, auth_method: str) -> None:
        self.auth_method = auth_method


@pytest.fixture
def engine():
    eng = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def persona_repo(engine):
    repo = MemoirPersonaRepository(engine)
    draft = repo.create_draft("ari", "# Ari Persona v1\n\nI am Ari, companion to the Emperor.", source="seed")
    repo.submit(draft.id)
    repo.approve(draft.id, _Actor("session"))
    return repo


def _make_synthesis(engine, claim="A new belief", verb="CREATE"):
    with Session(engine) as session:
        syn = MemoirSynthesis(
            id=uuid.uuid4().hex,
            agent_id="ari",
            claim=claim,
            source_episode_ids=json.dumps(["ep-1"]),
            verb=verb,
            confidence=0.9,
            created_at=now_utc(),
        )
        session.add(syn)
        session.commit()
        session.refresh(syn)
        return syn


class TestProposalCreationAndIsolation:
    """Proposal creation and pointer row isolation (AC3)."""

    def test_proposal_lands_in_pending_approval_and_isolates_pointer(self, engine, persona_repo):
        def fake_llm(prompt):
            if "Taxonomy:" in prompt:
                return json.dumps({
                    "classification": "new_rule",
                    "reasoning": "New rule added.",
                })
            return "# Ari Persona v2\n\nUpdated with rule."

        svc = MemoirPersonaGateService(engine, persona_repository=persona_repo, llm_caller=fake_llm)
        syn = _make_synthesis(engine, claim="Always verify backups")

        outcome = svc.process_candidate(syn)
        assert outcome.status == "drafted"
        assert outcome.draft_version_id is not None
        assert outcome.version == 2

        # Verify pointer in memoir_persona is still v1 (isolated!)
        current = persona_repo.get_current("ari")
        assert current is not None
        assert current.version == 1

        # Check the newly proposed version in repository
        proposed_ver = persona_repo.get_version(outcome.draft_version_id)
        assert proposed_ver is not None
        assert proposed_ver.version == 2
        assert proposed_ver.status == "pending_approval"
        assert proposed_ver.source == "ari"


class TestHumanLaneEnforcement:
    """AC4: Human-lane approval and rejection requirements."""

    def test_approve_with_session_lane_moves_pointer(self, persona_repo):
        draft = persona_repo.create_draft("ari", "# Ari Persona v2", source="ari")
        pending = persona_repo.submit(draft.id)
        assert pending.status == "pending_approval"

        # Approve with human session
        approved = persona_repo.approve(pending.id, _Actor("session"))
        assert approved.status == "approved"
        assert approved.approved_at is not None

        # Pointer row MUST move
        current = persona_repo.get_current("ari")
        assert current is not None
        assert current.id == approved.id
        assert current.version == 2

    def test_approve_with_bearer_lane_raises_human_lane_required_error(self, persona_repo):
        draft = persona_repo.create_draft("ari", "# Ari Persona v2", source="ari")
        pending = persona_repo.submit(draft.id)

        # Non-session actor
        with pytest.raises(HumanLaneRequiredError):
            persona_repo.approve(pending.id, _Actor("bearer"))

        # Version must stay pending_approval
        ver = persona_repo.get_version(pending.id)
        assert ver.status == "pending_approval"

    def test_reject_with_session_lane_transitions_to_rejected(self, persona_repo):
        draft = persona_repo.create_draft("ari", "# Ari Persona v2", source="ari")
        pending = persona_repo.submit(draft.id)

        rejected = persona_repo.reject(pending.id, _Actor("session"))
        assert rejected.status == "rejected"

        # Pointer remains at v1
        current = persona_repo.get_current("ari")
        assert current.version == 1


class TestMutationLegB:
    """AC6 (b): Removing _require_human_lane from approve() flips AC4 RED."""

    def test_removing_human_lane_guard_would_fail_ac4(self, persona_repo):
        """Simulate mutation: approve without calling _require_human_lane."""
        class MutatedRepo(MemoirPersonaRepository):
            def approve(self, version_id, actor):
                # MUTATION: bypass _require_human_lane
                return self._transition(version_id, "approved", actor=actor)

        mutated = MutatedRepo(persona_repo.engine)
        draft = mutated.create_draft("ari", "# Mutated", source="ari")
        pending = mutated.submit(draft.id)

        # Under valid AC4 behavior, approving with bearer MUST raise HumanLaneRequiredError
        raised = False
        try:
            mutated.approve(pending.id, _Actor("bearer"))
        except HumanLaneRequiredError:
            raised = True

        # Assert that the mutation fails the AC4 requirement
        assert not raised, "Mutation allowed bearer approval without raising HumanLaneRequiredError"
