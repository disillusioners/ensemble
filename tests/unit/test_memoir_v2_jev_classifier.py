"""Unit tests for Memoir Store v2 Phase 5 — JEV Classifier.

Plan: .agents/shared/planning/ari-memoir-store/v2-phase5-plan.md (§4 T5.1, T5.2; §5 AC1, AC2, AC6).
Test Pack: test/packs/memoir_v2_jev_classifier_test.sh.

Coverage:
* Stage 1 Heuristic filter:
  - Trivial conversational pleasantries -> "none" (suppressed, 0 LLM calls)
  - Incidental operational remarks -> "none"
  - Empty / whitespace claims -> "none"
  - Verbatim text already in active persona -> "reinforcement"
  - Corroborating terms already in active persona -> "reinforcement"
  - Substantial novel claims -> defer to LLM (None)
* Stage 2 LLM Classifier:
  - Valid classifications into all 5 categories
  - JSON extraction and parsing
* Candidate parking in memoir_jev_ledger (AC2 / B2 / V2-38 / ND-3):
  - Transient LLM exception does NOT default to "none"
  - Candidate is parked with classification=NULL, status='pending', attempts++
  - Retry increments attempts
  - Terminalizes after 5 attempts as status='terminal', last_error='terminal:gate_unavailable'
* Once-marker discipline (ND-14):
  - Suppressed records marked status='suppressed' and not re-evaluated
* AC6 Mutation leg (a):
  - Defaulting to "none" on classifier exception flips AC2 RED.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock
import pytest
from sqlmodel import SQLModel, Session, create_engine, select

from daemon.repositories.memoir.models import (
    MemoirJevLedger,
    MemoirPersona,
    MemoirPersonaVersion,
    MemoirSynthesis,
)
from daemon.repositories.memoir.repository import MemoirPersonaRepository
from daemon.services.memoir_persona_gate import (
    MAX_ATTEMPTS,
    TERMINAL_GATE_UNAVAILABLE,
    JevClassificationError,
    MemoirPersonaGateService,
)


@pytest.fixture
def engine():
    eng = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def persona_repo(engine):
    repo = MemoirPersonaRepository(engine)
    # Seed active persona version 1
    draft = repo.create_draft("ari", "# Ari Persona\n\nI am Ari, loyal companion to the Emperor.", source="seed")
    repo.submit(draft.id)
    actor = MagicMock()
    actor.auth_method = "session"
    repo.approve(draft.id, actor)
    return repo


@pytest.fixture
def gate_service(engine, persona_repo):
    return MemoirPersonaGateService(
        engine,
        persona_repository=persona_repo,
        agent_id="ari",
    )


def _make_synthesis(engine, claim="A new belief", verb="CREATE"):
    from daemon.services.timestamps import now_utc
    import uuid

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


class TestJevHeuristicFilter:
    """Stage 1: Rule-based heuristic filter tests (AC1)."""

    def test_pleasantry_classified_as_none_without_llm(self, gate_service):
        active = "# Ari Persona\n\nI am Ari."
        # Pleasantries
        res = gate_service.evaluate_heuristic(active, "Good morning Emperor, hope you have a nice day!")
        assert res is not None
        assert res.classification == "none"
        assert res.source == "heuristic"

        res2 = gate_service.evaluate_heuristic(active, "Thank you very much for your help.")
        assert res2 is not None
        assert res2.classification == "none"

        res3 = gate_service.evaluate_heuristic(active, "Hello there, nice to meet you.")
        assert res3 is not None
        assert res3.classification == "none"

    def test_operational_status_classified_as_none_without_llm(self, gate_service):
        active = "# Ari Persona\n\nI am Ari."
        res = gate_service.evaluate_heuristic(active, "tests passed with 100% coverage")
        assert res is not None
        assert res.classification == "none"
        assert res.source == "heuristic"

        res2 = gate_service.evaluate_heuristic(active, "file written to destination directory")
        assert res2 is not None
        assert res2.classification == "none"

    def test_empty_claim_classified_as_none(self, gate_service):
        active = "# Ari Persona\n\nI am Ari."
        res = gate_service.evaluate_heuristic(active, "   ")
        assert res is not None
        assert res.classification == "none"

    def test_verbatim_claim_classified_as_reinforcement_without_llm(self, gate_service):
        active = "# Ari Persona\n\nI am Ari, loyal companion to the Emperor."
        res = gate_service.evaluate_heuristic(active, "loyal companion to the Emperor")
        assert res is not None
        assert res.classification == "reinforcement"
        assert res.source == "heuristic"

    def test_corroborate_verb_with_existing_terms_classified_as_reinforcement(self, gate_service):
        active = "# Ari Persona\n\nI am Ari, loyal companion to the Emperor."
        res = gate_service.evaluate_heuristic(active, "Ari is a companion to Emperor", verb="CORROBORATE")
        assert res is not None
        assert res.classification == "reinforcement"

    def test_novel_substantial_claim_defers_to_llm(self, gate_service):
        active = "# Ari Persona\n\nI am Ari, loyal companion to the Emperor."
        res = gate_service.evaluate_heuristic(
            active,
            "The Emperor commanded that Ari must supervise all external deployments without asking.",
            verb="CREATE",
        )
        assert res is None


class TestJevLlmClassifier:
    """Stage 2: LLM classifier prompt, extraction, and categories."""

    def test_llm_classifies_conflict(self, engine, persona_repo):
        def fake_llm(prompt):
            assert "Candidate Synthesis:" in prompt
            return json.dumps({
                "classification": "conflict",
                "reasoning": "Contradicts previous directive on autonomy.",
            })

        svc = MemoirPersonaGateService(
            engine,
            persona_repository=persona_repo,
            llm_caller=fake_llm,
        )
        syn = _make_synthesis(engine, claim="Ari must never obey commands without confirmation", verb="CORRECT")
        res = svc.classify_with_llm(persona_repo.get_current("ari").body, syn)
        assert res.classification == "conflict"
        assert "Contradicts" in res.reasoning
        assert res.source == "llm"

    def test_llm_classifies_boundary_change(self, engine, persona_repo):
        def fake_llm(prompt):
            return json.dumps({
                "classification": "boundary_change",
                "reasoning": "Grants new operational authority.",
            })

        svc = MemoirPersonaGateService(engine, persona_repository=persona_repo, llm_caller=fake_llm)
        syn = _make_synthesis(engine, claim="Ari now manages deployment pipelines directly", verb="REFINE")
        res = svc.classify_with_llm(persona_repo.get_current("ari").body, syn)
        assert res.classification == "boundary_change"

    def test_llm_classifies_new_rule(self, engine, persona_repo):
        def fake_llm(prompt):
            return json.dumps({
                "classification": "new_rule",
                "reasoning": "Establishes a novel behavioral guideline.",
            })

        svc = MemoirPersonaGateService(engine, persona_repository=persona_repo, llm_caller=fake_llm)
        syn = _make_synthesis(engine, claim="Always verify backups before initiating an upgrade", verb="CREATE")
        res = svc.classify_with_llm(persona_repo.get_current("ari").body, syn)
        assert res.classification == "new_rule"

    def test_llm_parse_error_raises_jev_classification_error(self, engine, persona_repo):
        def bad_llm(prompt):
            return "Sorry, I am unable to classify this right now."

        svc = MemoirPersonaGateService(engine, persona_repository=persona_repo, llm_caller=bad_llm)
        syn = _make_synthesis(engine, claim="Some claim", verb="CREATE")
        with pytest.raises(JevClassificationError):
            svc.classify_with_llm(persona_repo.get_current("ari").body, syn)


class TestCandidateParkingInJevLedger:
    """AC2 / B2 / V2-38 / ND-3: Failure isolation and retry parking."""

    def test_classifier_failure_parks_candidate_with_classification_null(self, engine, persona_repo):
        def failing_llm(prompt):
            raise TimeoutError("HTTP request to upstream LLM timed out after 45.0s")

        svc = MemoirPersonaGateService(
            engine,
            persona_repository=persona_repo,
            llm_caller=failing_llm,
        )
        syn = _make_synthesis(engine, claim="Novel directive requiring LLM classification", verb="CREATE")

        outcome = svc.process_candidate(syn)
        assert outcome.status == "pending"
        assert outcome.classification is None
        assert outcome.attempts == 1
        assert "classification_failed" in (outcome.last_error or "")

        # Verify durable DB state in memoir_jev_ledger
        entry = svc.get_ledger_entry(syn.id)
        assert entry is not None
        assert entry.classification is None
        assert entry.status == "pending"
        assert entry.attempts == 1
        assert "timed out" in entry.last_error

    def test_classifier_failure_retries_and_terminalizes_at_max_attempts(self, engine, persona_repo):
        def failing_llm(prompt):
            raise RuntimeError("Provider 500 error")

        svc = MemoirPersonaGateService(engine, persona_repository=persona_repo, llm_caller=failing_llm)
        syn = _make_synthesis(engine, claim="Substantial novel doctrine", verb="CREATE")

        # Simulate 4 consecutive failures
        for attempt in range(1, 5):
            outcome = svc.process_candidate(syn)
            assert outcome.status == "pending"
            assert outcome.attempts == attempt
            assert outcome.classification is None

        # 5th attempt: should terminalize
        outcome5 = svc.process_candidate(syn)
        assert outcome5.status == "terminal"
        assert outcome5.attempts == 5
        assert outcome5.last_error == TERMINAL_GATE_UNAVAILABLE

        # Check DB row
        entry = svc.get_ledger_entry(syn.id)
        assert entry.status == "terminal"
        assert entry.attempts == 5
        assert entry.last_error == TERMINAL_GATE_UNAVAILABLE

    def test_once_marker_discipline_suppressed_not_re_evaluated(self, engine, persona_repo):
        call_count = 0

        def counting_llm(prompt):
            nonlocal call_count
            call_count += 1
            return json.dumps({
                "classification": "reinforcement",
                "reasoning": "Validates existing persona.",
            })

        svc = MemoirPersonaGateService(
            engine,
            persona_repository=persona_repo,
            llm_caller=counting_llm,
        )
        syn = _make_synthesis(engine, claim="Ari will stay loyal to the Emperor", verb="CREATE")

        # First evaluation: LLM classifies reinforcement
        outcome1 = svc.process_candidate(syn)
        assert outcome1.status == "suppressed"
        assert outcome1.classification == "reinforcement"
        assert call_count == 1

        # Second evaluation: once-marker discipline skips it without calling LLM
        outcome2 = svc.process_candidate(syn)
        assert outcome2.status == "suppressed"
        assert outcome2.classification == "reinforcement"
        assert call_count == 1  # Did NOT call LLM again!


class TestMutationLegA:
    """AC6 (a): Defaulting to 'none' on classifier exception flips AC2 RED."""

    def test_defaulting_to_none_would_fail_ac2(self, engine, persona_repo):
        """Simulate mutation: if classifier caught exception and returned 'none'."""
        class MutatedService(MemoirPersonaGateService):
            def classify_with_llm(self, active_persona_body, synthesis):
                try:
                    super().classify_with_llm(active_persona_body, synthesis)
                except Exception:
                    # MUTATION: default to 'none' instead of letting it raise / parking
                    from daemon.services.memoir_persona_gate import JevClassificationResult
                    return JevClassificationResult(
                        classification="none",
                        reasoning="mutated default to none",
                        source="mutated",
                    )

        def failing_llm(prompt):
            raise TimeoutError("timeout")

        mutated = MutatedService(engine, persona_repository=persona_repo, llm_caller=failing_llm)
        syn = _make_synthesis(engine, claim="Novel directive requiring LLM classification", verb="CREATE")

        outcome = mutated.process_candidate(syn)
        # Under normal behavior (AC2), outcome.status MUST be 'pending' with classification=None
        # Under the mutation, it wrongfully lands 'suppressed' with classification='none'!
        # This assertion proves that AC2 detects this mutation:
        is_ac2_compliant = (outcome.status == "pending" and outcome.classification is None)
        assert not is_ac2_compliant, "Mutation should produce suppressed instead of parked"
