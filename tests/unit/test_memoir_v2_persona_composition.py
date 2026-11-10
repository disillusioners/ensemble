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
  - Multi-candidate passes spend at most 1 composition call per pass; the
    remainder is PARKED as 'pending' (deferred:composition_budget) and is
    picked up FROM THE LEDGER by the next pass — no candidate is lost.
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
    DEFERRED_COMPOSITION_BUDGET,
    GATE_ERROR_PREFIX,
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


class _ExplodingRepo(MemoirPersonaRepository):
    """A persona store whose ``create_draft`` ALWAYS raises.

    The raise happens AFTER the classification was paid for and AFTER
    the composition succeeded, but BEFORE any ledger row exists — the
    exact raise path the gate must journal (F1).
    """

    def create_draft(self, agent_id, body, source="ari"):
        raise RuntimeError("the proposal store exploded")


class _FirstCallExplodingRepo(MemoirPersonaRepository):
    """A persona store whose FIRST ``create_draft`` raises.

    The later calls succeed — so a pass can hold a raising candidate
    AND a clean one (the no-head-of-line-stall leg).
    """

    def __init__(self, engine):
        super().__init__(engine)
        self.create_draft_calls = 0

    def create_draft(self, agent_id, body, source="ari"):
        self.create_draft_calls += 1
        if self.create_draft_calls == 1:
            raise RuntimeError("the proposal store exploded")
        return super().create_draft(agent_id, body, source=source)


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
    """MEMOIR_PERSONA_COMPOSITION_MAX_LLM_CALLS_PER_PASS = 1 per reflection pass.

    THE PRODUCTION SHAPE (C1): the reflection service passes ONLY this
    pass's accepted syntheses, and the ONLY re-selection source is the
    ledger (``get_pending_candidates()``).  A candidate the per-pass
    budget never reached MUST therefore be PARKED as 'pending' — with no
    ledger row it would be lost from the JEV lane forever (V2-38).
    """

    def test_a_budget_deferred_candidate_is_parked_and_picked_up_next_pass(
        self, engine, persona_repo
    ):
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

        # PASS 1 — two gated candidates, a budget of ONE composition call.
        outcomes = svc.process_pass(
            new_syntheses=[syn1, syn2],
            max_composition_calls=MEMOIR_PERSONA_COMPOSITION_MAX_LLM_CALLS_PER_PASS,
        )

        assert len(outcomes) == 1  # 1 composed; the second is DEFERRED
        assert comp_calls == 1
        assert outcomes[0].status == "drafted"
        assert outcomes[0].synthesis_id == syn1.id

        entry1 = svc.get_ledger_entry(syn1.id)
        assert entry1 is not None
        assert entry1.status == "drafted"

        # THE PARK: the deferred candidate is NOT lost — it is parked as
        # 'pending' with the deferral marker, so the ledger can
        # re-select it.
        entry2 = svc.get_ledger_entry(syn2.id)
        assert entry2 is not None
        assert entry2.status == "pending"
        assert entry2.last_error == "deferred:composition_budget"

        # PASS 2 — the reflection service passes NO new syntheses; the
        # candidate comes back FROM THE LEDGER and is drafted.
        outcomes2 = svc.process_pass(
            new_syntheses=[],
            max_composition_calls=MEMOIR_PERSONA_COMPOSITION_MAX_LLM_CALLS_PER_PASS,
        )
        assert len(outcomes2) == 1
        assert outcomes2[0].status == "drafted"
        assert outcomes2[0].synthesis_id == syn2.id
        assert comp_calls == 2

        entry2_after = svc.get_ledger_entry(syn2.id)
        assert entry2_after.status == "drafted"
        assert entry2_after.last_error is None

    def test_a_deferred_candidate_with_a_known_classification_is_not_reclassified(
        self, engine, persona_repo
    ):
        """A candidate whose classification is ALREADY KNOWN keeps it — the
        retry must NOT re-pay the classifier."""
        classify_calls: list[str] = []
        comp_calls: list[str] = []

        def failing_composition(prompt):
            if "Taxonomy:" in prompt:
                classify_calls.append(prompt)
                return json.dumps({
                    "classification": "new_rule",
                    "reasoning": "Valid new rule.",
                })
            raise RuntimeError("composition exploded")

        svc_fail = MemoirPersonaGateService(
            engine, persona_repository=persona_repo, llm_caller=failing_composition
        )
        syn = _make_synthesis(engine, claim="Rule to integrate")

        # The candidate is classified, then its composition FAILS -> parked
        # as 'pending' WITH the classification stored.
        outcome = svc_fail.process_candidate(syn)
        assert outcome.status == "pending"
        assert outcome.classification == "new_rule"
        assert len(classify_calls) == 1

        def working(prompt):
            if "Taxonomy:" in prompt:
                classify_calls.append(prompt)
                return json.dumps({
                    "classification": "new_rule",
                    "reasoning": "Valid new rule.",
                })
            comp_calls.append(prompt)
            return "# Ari Persona\n\nUpdated with rule."

        svc = MemoirPersonaGateService(engine, persona_repository=persona_repo, llm_caller=working)
        retry = svc.process_candidate(syn)
        assert retry.status == "drafted"
        assert retry.classification == "new_rule"
        # NO second classifier call — the STORED classification was used.
        assert len(classify_calls) == 1
        assert len(comp_calls) == 1

class TestRaisePathParkSemantics:
    """THE RAISE PATH (F1) — a candidate that RAISES is JOURNALED.

    ``process_candidate`` can raise AFTER the classification was paid for
    and BEFORE any ledger row exists (the composition ``try`` covers only
    ``compose_persona_document``; ``create_draft`` / ``submit`` sit
    OUTSIDE it).  The pre-fix shape let that escape the candidate loop:
    the pass ABORTED, the PAID classification was discarded, the attempt
    counter stayed at 0 (the terminal-at-5 ladder was unreachable), a
    store failure was MASKED as a budget deferral, and the poison row
    was re-selected FIRST on every pass — a permanent HEAD-OF-LINE
    stall of the whole JEV lane.

    These legs pin the post-fix semantics:

    * the failure is CONTAINED and JOURNALED (``gate_error: …`` — never
      the deferral marker), with the attempt consumed;
    * the retry ladder TERMINALIZES at ``MAX_ATTEMPTS``;
    * the pass CONTINUES — a later CLEAN candidate still drafts in the
      SAME pass (no stall);
    * the PAID classification is PRESERVED — the retry does NOT re-pay
      the classifier;
    * the two markers stay DISTINGUISHABLE in the ledger.
    """

    def test_a_raising_candidate_is_journaled_as_a_gate_error_not_a_deferral(
        self, engine, persona_repo
    ):
        """The raise is CONTAINED and JOURNALED — not a deferral, not a loss."""

        def fake_llm(prompt):
            if "Taxonomy:" in prompt:
                return json.dumps({
                    "classification": "new_rule",
                    "reasoning": "Valid new rule.",
                })
            return "# Ari Persona\n\nUpdated with rule."

        svc = MemoirPersonaGateService(
            engine,
            persona_repository=_ExplodingRepo(engine),
            llm_caller=fake_llm,
        )
        syn = _make_synthesis(engine, claim="Rule 1 to integrate")

        # The pass does NOT raise — the failure is contained inside the
        # candidate loop (pre-fix: the RuntimeError escaped the pass).
        svc.process_pass(
            new_syntheses=[syn],
            max_composition_calls=MEMOIR_PERSONA_COMPOSITION_MAX_LLM_CALLS_PER_PASS,
        )

        entry = svc.get_ledger_entry(syn.id)
        assert entry is not None
        # THE ATTEMPT WAS CONSUMED (pre-fix: frozen at 0, so the
        # terminal-at-5 ladder was unreachable on this path).
        assert entry.attempts >= 1
        # THE DIAGNOSTIC IS A GATE ERROR, NOT A DEFERRAL (pre-fix: the
        # store failure was MASKED as 'deferred:composition_budget').
        assert entry.last_error is not None
        assert entry.last_error.startswith("gate_error:")
        assert entry.last_error != DEFERRED_COMPOSITION_BUDGET
        assert "deferred" not in entry.last_error
        assert entry.status == "pending"

    def test_a_raising_candidate_terminalizes_by_pass_five(
        self, engine, persona_repo
    ):
        """The retry ladder TERMINALIZES — the attempt counter advances."""

        def fake_llm(prompt):
            if "Taxonomy:" in prompt:
                return json.dumps({
                    "classification": "new_rule",
                    "reasoning": "Valid new rule.",
                })
            return "# Ari Persona\n\nUpdated with rule."

        svc = MemoirPersonaGateService(
            engine,
            persona_repository=_ExplodingRepo(engine),
            llm_caller=fake_llm,
        )
        syn = _make_synthesis(engine, claim="Rule 1 to integrate")

        for attempt in range(MAX_ATTEMPTS):
            svc.process_pass(
                new_syntheses=[syn] if attempt == 0 else [],
                max_composition_calls=MEMOIR_PERSONA_COMPOSITION_MAX_LLM_CALLS_PER_PASS,
            )

        entry = svc.get_ledger_entry(syn.id)
        assert entry is not None
        assert entry.attempts >= MAX_ATTEMPTS
        assert entry.status == "terminal"
        assert entry.last_error.startswith("gate_error:")

    def test_a_later_clean_candidate_still_drafts_in_the_same_pass(
        self, engine, persona_repo
    ):
        """NO HEAD-OF-LINE STALL — the pass CONTINUES past a raising
        candidate, and the later CLEAN candidate drafts in the SAME pass.

        Pre-fix the raise escaped the loop: the pass aborted, so the
        later candidate was never even reached.
        """

        def fake_llm(prompt):
            if "Taxonomy:" in prompt:
                return json.dumps({
                    "classification": "new_rule",
                    "reasoning": "Valid new rule.",
                })
            return "# Ari Persona\n\nUpdated with rule."

        svc = MemoirPersonaGateService(
            engine,
            persona_repository=_FirstCallExplodingRepo(engine),
            llm_caller=fake_llm,
        )
        syn1 = _make_synthesis(engine, claim="Rule 1 to integrate")
        syn2 = _make_synthesis(engine, claim="Rule 2 to integrate")

        outcomes = svc.process_pass(
            new_syntheses=[syn1, syn2],
            max_composition_calls=2,
        )

        # THE LATER CLEAN CANDIDATE DRAFTED IN THE SAME PASS.
        assert len(outcomes) == 1
        assert outcomes[0].synthesis_id == syn2.id
        assert outcomes[0].status == "drafted"
        entry2 = svc.get_ledger_entry(syn2.id)
        assert entry2 is not None
        assert entry2.status == "drafted"

        # AND THE RAISING CANDIDATE IS STILL JOURNALED (not lost).
        entry1 = svc.get_ledger_entry(syn1.id)
        assert entry1 is not None
        assert entry1.status == "pending"
        assert entry1.attempts >= 1
        assert entry1.last_error.startswith("gate_error:")

    def test_a_raise_after_classification_preserves_the_classification(
        self, engine, persona_repo
    ):
        """THE PAID CLASSIFICATION SURVIVES THE RAISE — the retry does NOT
        re-pay the classifier."""

        classify_calls: list[str] = []
        comp_calls: list[str] = []

        def counting_llm(prompt):
            if "Taxonomy:" in prompt:
                classify_calls.append(prompt)
                return json.dumps({
                    "classification": "new_rule",
                    "reasoning": "Valid new rule.",
                })
            comp_calls.append(prompt)
            return "# Ari Persona\n\nUpdated with rule."

        svc = MemoirPersonaGateService(
            engine,
            persona_repository=_ExplodingRepo(engine),
            llm_caller=counting_llm,
        )
        syn = _make_synthesis(engine, claim="Rule to integrate")

        svc.process_pass(
            new_syntheses=[syn],
            max_composition_calls=MEMOIR_PERSONA_COMPOSITION_MAX_LLM_CALLS_PER_PASS,
        )

        entry = svc.get_ledger_entry(syn.id)
        assert entry is not None
        # THE CLASSIFICATION IS PRESERVED (pre-fix: it was DISCARDED, so
        # every pass re-classified AND re-composed the candidate).
        assert entry.classification == "new_rule"
        assert len(classify_calls) == 1

        # THE RETRY REUSES THE STORED CLASSIFICATION — no second
        # classifier call.
        retry_svc = MemoirPersonaGateService(
            engine,
            persona_repository=persona_repo,
            llm_caller=counting_llm,
        )
        outcomes = retry_svc.process_pass(
            new_syntheses=[],
            max_composition_calls=MEMOIR_PERSONA_COMPOSITION_MAX_LLM_CALLS_PER_PASS,
        )
        assert len(classify_calls) == 1  # NO re-pay
        assert len(outcomes) == 1
        assert outcomes[0].status == "drafted"
        assert outcomes[0].classification == "new_rule"
        assert len(comp_calls) == 2  # the composition is re-paid, the
        # classification is NOT

    def test_the_gate_error_marker_and_the_budget_deferral_marker_stay_distinguishable(
        self, engine, persona_repo
    ):
        """A GATE ERROR and a BUDGET DEFERRAL are different ledger states."""

        def fake_llm(prompt):
            if "Taxonomy:" in prompt:
                return json.dumps({
                    "classification": "new_rule",
                    "reasoning": "Valid new rule.",
                })
            return "# Ari Persona\n\nUpdated with rule."

        svc = MemoirPersonaGateService(
            engine,
            persona_repository=_FirstCallExplodingRepo(engine),
            llm_caller=fake_llm,
        )
        raising = _make_synthesis(engine, claim="Rule 1 to integrate")
        composed = _make_synthesis(engine, claim="Rule 2 to integrate")
        deferred = _make_synthesis(engine, claim="Rule 3 to integrate")

        svc.process_pass(
            new_syntheses=[raising, composed, deferred],
            max_composition_calls=1,
        )

        gate_error_entry = svc.get_ledger_entry(raising.id)
        deferral_entry = svc.get_ledger_entry(deferred.id)
        assert gate_error_entry is not None
        assert deferral_entry is not None

        # THE GATE ERROR IS NOT A DEFERRAL.
        assert gate_error_entry.last_error.startswith("gate_error:")
        assert gate_error_entry.last_error != DEFERRED_COMPOSITION_BUDGET
        # THE DEFERRAL IS NOT A GATE ERROR.
        assert deferral_entry.last_error == DEFERRED_COMPOSITION_BUDGET
        assert not deferral_entry.last_error.startswith("gate_error:")
        # AND THE TWO MARKERS NEVER OVERLAP.
        assert DEFERRED_COMPOSITION_BUDGET not in gate_error_entry.last_error
        assert GATE_ERROR_PREFIX not in deferral_entry.last_error


# ── P5-W1: the budget is charged AT THE COMPOSE CALL SITE ────────────────


class TestCompositionBudgetChargedAtCallSite:
    """The per-pass cap counts PAID ATTEMPTS, not outcome shapes.

    PRE-FIX DEFECT (P5-W1, executed): ``composition_calls`` was inferred
    after the fact from ``outcome.proposed_body is not None or "diff" in
    outcome.last_error``.  A candidate whose composition SUCCEEDED and then
    raised in ``create_draft`` produced neither signal, so the pass went on
    to compose a SECOND candidate — two composition LLM calls in one pass
    against a declared cap of one.
    """

    def test_a_compose_that_raises_after_success_consumes_the_budget(
        self, engine, persona_repo
    ):
        comp_calls: list[str] = []

        def counting_llm(prompt):
            if "Taxonomy:" in prompt:
                return json.dumps({
                    "classification": "new_rule",
                    "reasoning": "Valid new rule.",
                })
            comp_calls.append(prompt)
            return "# Ari Persona\n\nUpdated with rule."

        svc = MemoirPersonaGateService(
            engine,
            persona_repository=_FirstCallExplodingRepo(engine),
            llm_caller=counting_llm,
        )
        first = _make_synthesis(engine, claim="Rule 1 to integrate")
        second = _make_synthesis(engine, claim="Rule 2 to integrate")

        svc.process_pass(
            new_syntheses=[first, second],
            max_composition_calls=MEMOIR_PERSONA_COMPOSITION_MAX_LLM_CALLS_PER_PASS,
        )

        # ONE paid composition attempt — the candidate that raised AFTER a
        # successful compose still spent it.
        assert len(comp_calls) == 1, (
            f"the pass spent {len(comp_calls)} composition calls against a "
            f"cap of {MEMOIR_PERSONA_COMPOSITION_MAX_LLM_CALLS_PER_PASS}"
        )
        # ...and the candidate the cap never reached is parked, not lost.
        second_entry = svc.get_ledger_entry(second.id)
        assert second_entry is not None
        assert second_entry.status == "pending"
        assert second_entry.last_error == DEFERRED_COMPOSITION_BUDGET


# ── P5-W2: the park loop isolates per-candidate failures ────────────────


class TestParkLoopIsolation:
    """One failed ledger write must not lose every later candidate.

    The park runs in a ``finally`` (C1); pre-fix a store error inside it
    aborted the loop, so every candidate after the failing one stayed
    ROWLESS — permanently invisible to ``get_pending_candidates()``.
    """

    def test_a_failed_park_does_not_abort_the_remaining_candidates(
        self, engine, persona_repo
    ):
        svc = MemoirPersonaGateService(
            engine, persona_repository=persona_repo, llm_caller=lambda p: "{}"
        )
        first = _make_synthesis(engine, claim="Rule A")
        second = _make_synthesis(engine, claim="Rule B")

        real_record = svc.record_ledger_entry

        def flaky_record(**kwargs):
            if kwargs.get("synthesis_id") == first.id:
                raise RuntimeError("the ledger store exploded")
            return real_record(**kwargs)

        svc.record_ledger_entry = flaky_record

        svc._park_unprocessed_candidates([first, second], set())

        # The FIRST park failed (nothing landed for it), the SECOND landed.
        assert svc.get_ledger_entry(first.id) is None
        second_entry = svc.get_ledger_entry(second.id)
        assert second_entry is not None
        assert second_entry.status == "pending"
        assert second_entry.last_error == DEFERRED_COMPOSITION_BUDGET
