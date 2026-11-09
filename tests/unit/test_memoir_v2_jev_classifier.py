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
* Suppression rule (V2-D 5.2 / AC1):
  - reinforcement / none NEVER create or submit a persona version
* Heuristic ORDERING (V2-D 5.1):
  - the rule filter runs FIRST — a trivial candidate makes ZERO LLM calls
  - through the PRODUCTION entry point (process_candidate)
* Precedence resolution (the plan's `conflict > boundary_change > new_rule`):
  - an ambiguous / multi-label classifier response resolves by precedence
* The local fallback lane (F1 / F2):
  - the gate's OWN failover lane works with NO injected caller
  - the per-call timeout rides the call_timeout_s knob
* The production wiring (F1 / F2):
  - the gate classifies through the reflection service's injected caller
* AC6 Mutation leg (a) — a PRODUCTION REVERT recipe:
  - reverting the production park to a 'none' default flips AC2 RED.
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
    """AC6 (a): the PRODUCTION classifier-failure path PARKS — never defaults to 'none'.

    THE RECIPE IS A PRODUCTION REVERT (implementation notes §15.2): the
    production park is reverted to a 'none' default and THIS node reddens
    (together with the two parking nodes above).  Nothing here mutates
    anything: a self-mutating leg cannot be reddened by any production
    revert, so it proves nothing.
    """

    def test_a_failing_classifier_parks_the_candidate_and_writes_no_version(
        self, engine, persona_repo
    ):
        """The production seam is driven to RAISE (a failing LLM caller)."""
        def failing_llm(prompt):
            raise TimeoutError("HTTP request to upstream LLM timed out after 45.0s")

        svc = MemoirPersonaGateService(
            engine,
            persona_repository=persona_repo,
            llm_caller=failing_llm,
        )
        versions_before = persona_repo.list_versions("ari")
        syn = _make_synthesis(engine, claim="Novel directive requiring LLM classification", verb="CREATE")

        outcome = svc.process_candidate(syn)

        # THE PARK (AC2): never 'none', never 'suppressed'.
        assert outcome.status == "pending"
        assert outcome.classification is None
        assert outcome.attempts == 1
        assert "classification_failed" in (outcome.last_error or "")

        entry = svc.get_ledger_entry(syn.id)
        assert entry is not None
        assert entry.status == "pending"
        assert entry.classification is None

        # THE CONSEQUENCE: the candidate was NOT dropped — and no persona
        # version was drafted for it (a 'none' default would have
        # SUPPRESSED it; a broken gate would have drafted one).
        assert len(persona_repo.list_versions("ari")) == len(versions_before)
        assert persona_repo.list_pending("ari") == []
        assert persona_repo.get_current("ari").version == versions_before[0].version


class TestSuppressionRule:
    """V2-D 5.2 / AC1: reinforcement and none NEVER generate a version.

    THE RECIPE IS A PRODUCTION REVERT (implementation notes §15.2):
    making the SUPPRESSED classes draft+submit reddens these nodes.
    """

    def test_a_reinforcement_candidate_never_creates_or_submits_a_version(
        self, engine, persona_repo
    ):
        def counting_llm(prompt):
            return json.dumps({
                "classification": "reinforcement",
                "reasoning": "Re-asserts an existing persona rule.",
            })

        svc = MemoirPersonaGateService(
            engine,
            persona_repository=persona_repo,
            llm_caller=counting_llm,
        )
        versions_before = persona_repo.list_versions("ari")
        syn = _make_synthesis(engine, claim="Ari will stay loyal to the Emperor", verb="CREATE")

        outcome = svc.process_candidate(syn)
        assert outcome.status == "suppressed"
        assert outcome.classification == "reinforcement"

        # NO draft, NO submit: the version set is untouched.
        versions_after = persona_repo.list_versions("ari")
        assert [v.id for v in versions_after] == [v.id for v in versions_before]
        assert persona_repo.list_pending("ari") == []
        # The pointer did not move.
        assert persona_repo.get_current("ari").version == versions_before[0].version

    def test_a_trivial_candidate_never_creates_or_submits_a_version(
        self, engine, persona_repo
    ):
        def counting_llm(prompt):
            raise AssertionError("the heuristic must decide a trivial claim")

        svc = MemoirPersonaGateService(
            engine,
            persona_repository=persona_repo,
            llm_caller=counting_llm,
        )
        versions_before = persona_repo.list_versions("ari")
        syn = _make_synthesis(engine, claim="Thank you very much for your help.", verb="CREATE")

        outcome = svc.process_candidate(syn)
        assert outcome.status == "suppressed"
        assert outcome.classification == "none"
        assert len(persona_repo.list_versions("ari")) == len(versions_before)
        assert persona_repo.list_pending("ari") == []


class TestHeuristicOrdering:
    """V2-D 5.1: the rule filter runs FIRST — the LLM is never called on the trivial path.

    THE LEG GOES THROUGH THE PRODUCTION ENTRY POINT (``process_candidate``):
    the heuristic legs that call ``evaluate_heuristic`` directly cannot
    see a pipeline that skips the heuristic.

    THE RECIPE IS A PRODUCTION REVERT (implementation notes §15.2): ignoring
    the heuristic result reddens these nodes (the LLM is called for every
    candidate).
    """

    def test_a_trivial_candidate_makes_zero_llm_calls(self, engine, persona_repo):
        calls = []

        def counting_llm(prompt):
            calls.append(prompt)
            return json.dumps({"classification": "new_rule", "reasoning": "Should never run."})

        svc = MemoirPersonaGateService(
            engine,
            persona_repository=persona_repo,
            llm_caller=counting_llm,
        )
        syn = _make_synthesis(engine, claim="Good morning Emperor, hope you have a nice day!", verb="CREATE")

        outcome = svc.process_candidate(syn)
        assert calls == []
        assert outcome.status == "suppressed"
        assert outcome.classification == "none"

    def test_a_verbatim_candidate_makes_zero_llm_calls(self, engine, persona_repo):
        calls = []

        def counting_llm(prompt):
            calls.append(prompt)
            return json.dumps({"classification": "new_rule", "reasoning": "Should never run."})

        svc = MemoirPersonaGateService(
            engine,
            persona_repository=persona_repo,
            llm_caller=counting_llm,
        )
        # The claim is ALREADY asserted verbatim in the active persona.
        syn = _make_synthesis(engine, claim="loyal companion to the Emperor", verb="CREATE")

        outcome = svc.process_candidate(syn)
        assert calls == []
        assert outcome.status == "suppressed"
        assert outcome.classification == "reinforcement"


class TestPrecedenceResolution:
    """The plan's ``conflict > boundary_change > new_rule`` — the comparator.

    THE PLAN NEVER DEFINED ONE (an ambiguous or multi-label response had
    no defined resolution); this is the documented resolution, applied to
    the PRODUCTION classifier seam.
    """

    def test_a_multi_label_response_resolves_by_precedence(self, engine, persona_repo):
        def multi_label_llm(prompt):
            return json.dumps({
                "classification": ["new_rule", "conflict"],
                "reasoning": "Both labels apply.",
            })

        svc = MemoirPersonaGateService(
            engine,
            persona_repository=persona_repo,
            llm_caller=multi_label_llm,
        )
        syn = _make_synthesis(engine, claim="Ari must never obey commands without confirmation", verb="CORRECT")
        res = svc.classify_with_llm(persona_repo.get_current("ari").body, syn)
        assert res.classification == "conflict"
        assert res.source == "llm"

    def test_a_compound_string_response_resolves_by_precedence(self, engine, persona_repo):
        def compound_llm(prompt):
            return json.dumps({
                "classification": "boundary_change, new_rule",
                "reasoning": "Both labels apply.",
            })

        svc = MemoirPersonaGateService(
            engine,
            persona_repository=persona_repo,
            llm_caller=compound_llm,
        )
        syn = _make_synthesis(engine, claim="Ari now manages deployment pipelines directly", verb="REFINE")
        res = svc.classify_with_llm(persona_repo.get_current("ari").body, syn)
        assert res.classification == "boundary_change"

    def test_a_single_label_response_is_unchanged(self, engine, persona_repo):
        def single_llm(prompt):
            return json.dumps({"classification": "new_rule", "reasoning": "A novel principle."})

        svc = MemoirPersonaGateService(
            engine,
            persona_repository=persona_repo,
            llm_caller=single_llm,
        )
        syn = _make_synthesis(engine, claim="Always verify backups before initiating an upgrade", verb="CREATE")
        res = svc.classify_with_llm(persona_repo.get_current("ari").body, syn)
        assert res.classification == "new_rule"
        assert res.reasoning == "A novel principle."

    def test_an_unknown_label_still_raises_and_parks(self, engine, persona_repo):
        def unknown_llm(prompt):
            return json.dumps({"classification": "gibberish", "reasoning": "Nonsense."})

        svc = MemoirPersonaGateService(
            engine,
            persona_repository=persona_repo,
            llm_caller=unknown_llm,
        )
        syn = _make_synthesis(engine, claim="Some claim", verb="CREATE")
        with pytest.raises(JevClassificationError):
            svc.classify_with_llm(persona_repo.get_current("ari").body, syn)

    def test_the_resolver_ignores_unknown_labels(self):
        from daemon.services.memoir_persona_gate import resolve_classification_precedence

        assert resolve_classification_precedence(["new_rule", "conflict"]) == "conflict"
        assert resolve_classification_precedence(["banana", "none"]) == "none"
        assert resolve_classification_precedence(["banana"]) is None


class _FakeMessage:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeChoice:
    def __init__(self, content: str) -> None:
        self.message = _FakeMessage(content)


class _FakeResponse:
    def __init__(self, content: str) -> None:
        self.choices = [_FakeChoice(content)]


def _install_fake_openai(monkeypatch, content: str = "FALLBACK-OK"):
    """Install a fake ``openai.OpenAI`` — NO network, NO provider contact."""
    import openai

    calls: list[dict] = []
    created: list[object] = []

    class _FakeCompletions:
        def create(self, **kwargs):
            calls.append(kwargs)
            return _FakeResponse(content)

    class _FakeChat:
        def __init__(self) -> None:
            self.completions = _FakeCompletions()

    class _FakeOpenAI:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs
            self.chat = _FakeChat()
            created.append(self)

    monkeypatch.setattr(openai, "OpenAI", _FakeOpenAI)
    return calls, created


class TestLocalFallbackLane:
    """F1 / F2: the gate's OWN failover lane — the one no test exercised.

    The lane was doubly broken (the failover arguments were reversed and
    the timeout helper was called with a keyword it does not accept), so
    it was DORMANT — the production wiring injects the reflection
    service's caller, so the gate's own lane never ran.  A dormant,
    broken lane rots silently: this leg runs it.
    """

    def test_the_local_fallback_lane_calls_the_provider(self, engine, persona_repo, monkeypatch):
        calls, created = _install_fake_openai(monkeypatch, content="FALLBACK-OK")

        svc = MemoirPersonaGateService(
            engine,
            persona_repository=persona_repo,
            # NO injected caller -> the local fallback lane is the one used.
            llm_config={
                "model": "test-model",
                "api_key": "test-key",
                "base_url": "http://127.0.0.1:9/v1",
            },
            call_timeout_s=12.5,
        )
        assert svc._llm_caller is None

        out = svc._house_llm_call("hello")
        assert out == "FALLBACK-OK"

        assert len(created) == 1
        client = created[0]
        assert client.kwargs["api_key"] == "test-key"
        assert client.kwargs["base_url"] == "http://127.0.0.1:9/v1"
        # THE SDK'S OWN RETRY IS OFF — the facade owns the retry discipline.
        assert client.kwargs["max_retries"] == 0
        # THE TIMEOUT RIDES THE KNOB (F2) — not a second hard-coded copy.
        timeout = client.kwargs["timeout"]
        assert float(timeout.read) == 12.5
        assert float(timeout.connect) == 5.0
        assert calls and calls[0]["model"] == "test-model"

    def test_the_local_fallback_lane_raises_on_an_empty_response(
        self, engine, persona_repo, monkeypatch
    ):
        _install_fake_openai(monkeypatch, content="")

        svc = MemoirPersonaGateService(
            engine,
            persona_repository=persona_repo,
            llm_config={"model": "test-model", "api_key": "k", "base_url": "http://127.0.0.1:9/v1"},
        )
        with pytest.raises(Exception):
            svc._house_llm_call("hello")


class TestProductionWiring:
    """F1 / F2: the PRODUCTION wiring — the gate classifies TODAY.

    The reflection service builds the gate and injects ITS OWN caller, so
    the gate never uses its local fallback lane in production.  This leg
    pins that wiring end-to-end: the gate must classify through the
    reflection service's injected caller.
    """

    def test_the_gate_classifies_through_the_reflection_services_caller(
        self, engine, persona_repo
    ):
        from daemon.services.memoir_reflection_service import MemoirReflectionService

        calls: list[str] = []

        def fake_llm(prompt):
            calls.append(prompt)
            if "Taxonomy:" in prompt:
                return json.dumps({
                    "classification": "conflict",
                    "reasoning": "Contradicts an existing directive.",
                })
            return "# Ari Persona\n\nUpdated with the new directive."

        service = MemoirReflectionService(
            reflection_repository=MagicMock(),
            syntheses_repository=MagicMock(),
            persona_repository=persona_repo,
            llm_caller=fake_llm,
        )

        gate = service._persona_gate
        assert isinstance(gate, MemoirPersonaGateService)
        # THE SAME CALLER OBJECT — the gate does not build its own lane.
        assert gate._llm_caller is fake_llm
        # The gate rides the reflection service's timeout knob (F2).
        assert gate._call_timeout_s == float(service.settings.call_timeout_s)

        syn = _make_synthesis(
            engine,
            claim="Ari must never obey commands without confirmation",
            verb="CORRECT",
        )
        outcome = gate.process_candidate(syn)
        assert outcome.status == "drafted"
        assert outcome.classification == "conflict"
        assert calls  # the classification went through the injected caller
