"""Unit tests for Memoir Store v2 Phase 5 — Persona Document Composition.

Plan: .agents/shared/planning/ari-memoir-store/v2-phase5-plan.md (§3.3, §4 T5.3; §5 AC2, AC3).
Test Pack: test/packs/memoir_v2_persona_composition_test.sh.

Coverage:
* Persona document composition within 2000-token budget (MEMOIR_PERSONA_TOKEN_BUDGET):
  - Composes updated persona text from current persona + triggering claim
  - Metred via daemon.loader.estimate_tokens (cl100k tiktoken)
  - Result strictly <= 2000 tokens
  - Carries serialized structured metadata comment (V2-D 5.5 / ND-15)
* Unbuildable diff / over-budget parking in memoir_jev_ledger (AC2 / B3 / V2-39):
  - Over-budget diff (>2000 tokens) -> parked with status='pending', last_error='diff_over_budget'
  - Empty or malformed diff -> parked with status='pending', last_error='unbuildable_diff'
  - Terminalizes after 5 attempts as status='terminal', last_error='terminal:unbuildable_diff'
  - NEVER silently dropped to 'none'
* Composition budget ceiling per pass:
  - MEMOIR_PERSONA_COMPOSITION_MAX_LLM_CALLS_PER_PASS = 1
  - Multi-candidate passes spend at most 1 composition call per pass; remainder deferred.
"""

from __future__ import annotations

import json
import uuid
from unittest.mock import MagicMock
import pytest
from sqlmodel import SQLModel, Session, create_engine

from daemon.loader import estimate_tokens
from daemon.repositories.memoir.models import (
    MemoirJevLedger,
    MemoirPersona,
    MemoirPersonaVersion,
    MemoirSynthesis,
)
from daemon.repositories.memoir.repository import MemoirPersonaRepository
from daemon.services.memoir_briefing import MEMOIR_PERSONA_TOKEN_BUDGET
from daemon.services.memoir_persona_gate import (
    MAX_ATTEMPTS,
    MEMOIR_PERSONA_COMPOSITION_MAX_LLM_CALLS_PER_PASS,
    TERMINAL_UNBUILDABLE_DIFF,
    DiffOverBudgetError,
    MemoirPersonaGateService,
    UnbuildableDiffError,
)
from daemon.services.timestamps import now_utc


@pytest.fixture
def engine():
    eng = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def persona_repo(engine):
    repo = MemoirPersonaRepository(engine)
    draft = repo.create_draft("ari", "# Ari Persona\n\nI am Ari, companion to the Emperor.", source="seed")
    repo.submit(draft.id)
    actor = MagicMock()
    actor.auth_method = "session"
    repo.approve(draft.id, actor)
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


class TestPersonaComposition:
    """Persona document composition and token bounds (AC3)."""

    def test_composed_document_within_budget(self, engine, persona_repo):
        def fake_llm(prompt):
            if "Taxonomy:" in prompt:
                return json.dumps({
                    "classification": "new_rule",
                    "reasoning": "Introduces new operational protocol.",
                })
            # Composition prompt
            return "# Ari Persona\n\nI am Ari, companion to the Emperor.\n\n## Principles\n- Always verify backups before initiating upgrades."

        svc = MemoirPersonaGateService(engine, persona_repository=persona_repo, llm_caller=fake_llm)
        syn = _make_synthesis(engine, claim="Always verify backups before initiating upgrades")

        outcome = svc.process_candidate(syn)
        assert outcome.status == "drafted"
        assert outcome.classification == "new_rule"
        assert outcome.proposed_body is not None

        # Verify token count
        token_count = estimate_tokens(outcome.proposed_body)
        assert token_count <= MEMOIR_PERSONA_TOKEN_BUDGET
        assert "memoir-jev: base_version=" in outcome.proposed_body

        # Verify draft was created and submitted
        draft = persona_repo.get_version(outcome.draft_version_id)
        assert draft is not None
        assert draft.status == "pending_approval"
        assert draft.version == 2
        assert draft.source == "ari"


class TestUnbuildableDiffParking:
    """Unbuildable or over-budget diff handling and parking in ledger (AC2 / B3 / V2-39)."""

    def test_over_budget_diff_parks_in_ledger_as_diff_over_budget(self, engine, persona_repo):
        def fake_llm(prompt):
            if "Taxonomy:" in prompt:
                return json.dumps({
                    "classification": "new_rule",
                    "reasoning": "Substantial new guideline.",
                })
            # Generate a giant response exceeding 2000 tokens
            return "# Ari Persona\n\n" + ("Extremely verbose guideline content sentence. " * 600)

        svc = MemoirPersonaGateService(engine, persona_repository=persona_repo, llm_caller=fake_llm)
        syn = _make_synthesis(engine, claim="A giant guideline requiring massive explanation")

        outcome = svc.process_candidate(syn)
        assert outcome.status == "pending"
        assert outcome.classification == "new_rule"
        assert outcome.last_error == "diff_over_budget"
        assert outcome.attempts == 1

        entry = svc.get_ledger_entry(syn.id)
        assert entry.status == "pending"
        assert entry.last_error == "diff_over_budget"
        assert entry.classification == "new_rule"

    def test_empty_composed_diff_parks_as_unbuildable(self, engine, persona_repo):
        def fake_llm(prompt):
            if "Taxonomy:" in prompt:
                return json.dumps({
                    "classification": "boundary_change",
                    "reasoning": "Expands operational boundary.",
                })
            return "   "  # Empty whitespace response

        svc = MemoirPersonaGateService(engine, persona_repository=persona_repo, llm_caller=fake_llm)
        syn = _make_synthesis(engine, claim="Expands operational boundary")

        outcome = svc.process_candidate(syn)
        assert outcome.status == "pending"
        assert "unbuildable_diff" in (outcome.last_error or "")
        assert outcome.attempts == 1

    def test_unbuildable_diff_terminalizes_at_max_attempts(self, engine, persona_repo):
        def fake_llm(prompt):
            if "Taxonomy:" in prompt:
                return json.dumps({
                    "classification": "conflict",
                    "reasoning": "Contradiction.",
                })
            raise RuntimeError("LLM exploded during composition")

        svc = MemoirPersonaGateService(engine, persona_repository=persona_repo, llm_caller=fake_llm)
        syn = _make_synthesis(engine, claim="Contradictory claim")

        # Attempt 1 to 4: pending
        for i in range(1, 5):
            outcome = svc.process_candidate(syn)
            assert outcome.status == "pending"
            assert outcome.attempts == i

        # 5th attempt: terminalizes
        outcome5 = svc.process_candidate(syn)
        assert outcome5.status == "terminal"
        assert outcome5.attempts == 5
        assert outcome5.last_error == TERMINAL_UNBUILDABLE_DIFF

        entry = svc.get_ledger_entry(syn.id)
        assert entry.status == "terminal"
        assert entry.attempts == 5
        assert entry.last_error == TERMINAL_UNBUILDABLE_DIFF


class TestCompositionBudgetPerPass:
    """MEMOIR_PERSONA_COMPOSITION_MAX_LLM_CALLS_PER_PASS = 1 per reflection pass."""

    def test_pass_spends_at_most_one_composition_call(self, engine, persona_repo):
        comp_calls = 0

        def counting_llm(prompt):
            nonlocal comp_calls
            if "Taxonomy:" in prompt:
                return json.dumps({
                    "classification": "new_rule",
                    "reasoning": "Valid new rule.",
                })
            comp_calls += 1
            return "# Ari Persona\n\nUpdated with rule."

        svc = MemoirPersonaGateService(engine, persona_repository=persona_repo, llm_caller=counting_llm)
        syn1 = _make_synthesis(engine, claim="Rule 1 to integrate")
        syn2 = _make_synthesis(engine, claim="Rule 2 to integrate")

        outcomes = svc.process_pass(
            new_syntheses=[syn1, syn2],
            max_composition_calls=MEMOIR_PERSONA_COMPOSITION_MAX_LLM_CALLS_PER_PASS,
        )

        assert len(outcomes) == 1  # 1 composed; second deferred
        assert comp_calls == 1
        assert outcomes[0].status == "drafted"

        # syn1 is drafted, syn2 was not processed in this pass (still un-evaluated)
        entry1 = svc.get_ledger_entry(syn1.id)
        assert entry1.status == "drafted"
        entry2 = svc.get_ledger_entry(syn2.id)
        assert entry2 is None  # Never touched in this pass

        # Next pass processes syn2
        outcomes2 = svc.process_pass(
            new_syntheses=[syn2],
            max_composition_calls=MEMOIR_PERSONA_COMPOSITION_MAX_LLM_CALLS_PER_PASS,
        )
        assert len(outcomes2) == 1
        assert outcomes2[0].status == "drafted"
        assert comp_calls == 2
