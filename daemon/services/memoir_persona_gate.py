"""Memoir Store v2 — the Persona Gate (JEV).

Phase 5 of Memoir Store v2 (plan:
``.agents/shared/planning/ari-memoir-store/v2-phase5-plan.md``; flags V2-9,
V2-13, V2-20, V2-37, V2-38, V2-39, DD-9).

The Persona Gate (Judgment, Evaluation, Validation) evaluates whether
reflection syntheses warrant durable persona evolution:
1. Two-stage JEV classification:
   - Heuristic filter (Stage 1): rule-based detection of trivial diffs and
     pleasantries without spending LLM tokens.
   - LLM classifier (Stage 2): evaluates behavioral impact against the active
     persona.
   - Gated classes: conflict > boundary_change > new_rule force persona
     regeneration.  An AMBIGUOUS or MULTI-LABEL classifier response is
     resolved by that precedence (``resolve_classification_precedence``).
   - Suppressed classes: reinforcement and none are suppressed (no proposals).
2. Failure isolation & candidate parking in ``memoir_jev_ledger`` (B2 / V2-38):
   - On classifier failure, do NOT default to none (silently lossy).
   - Park candidate in ``memoir_jev_ledger`` (status='pending', classification=NULL,
     attempts incremented).
   - Terminalize at 5 attempts as status='terminal',
     last_error='terminal:gate_unavailable'.
   - The per-pass composition budget may end the pass before every candidate
     was evaluated: every candidate this pass did NOT process is PARKED as
     'pending' (last_error='deferred:composition_budget') — the ledger is the
     only re-selection source, so a candidate with no row would be lost
     forever.
   - A candidate whose processing RAISES is JOURNALED, not parked: the
     attempt is consumed (attempts+1), the classification already paid for
     is PRESERVED, and the diagnostic is 'gate_error: <Type>: <message>' —
     never the deferral marker (F1).  The failure is CONTAINED: the pass
     CONTINUES with the next candidate (no head-of-line stall).
3. Persona document composition & unbuildable diff parking (B3 / V2-39):
   - Composes proposed_body within <= 2000 tokens (``MEMOIR_PERSONA_TOKEN_BUDGET``).
   - Over-budget, unbuildable, or empty diffs park candidate in ledger;
     terminalize after 5 attempts as 'terminal:unbuildable_diff'.
4. Dormant seam activation (DD-9 / V2-D 5.3):
   - Calls ``create_draft(agent_id='ari', body=proposed_body, source='ari')``.
   - Calls ``submit(draft.id)`` to land version in ``pending_approval``.
   - Only human session approval may move it to ``approved``.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Sequence

from sqlmodel import Session, select

from daemon.loader import estimate_tokens
from daemon.repositories.memoir.models import (
    MemoirJevLedger,
    MemoirPersonaVersion,
    MemoirSynthesis,
)
from daemon.repositories.memoir.repository import MemoirPersonaRepository
from daemon.services.memoir_briefing import MEMOIR_PERSONA_TOKEN_BUDGET
from daemon.services.timestamps import now_utc

logger = logging.getLogger(__name__)

#: Max persona composition LLM calls per reflection pass (§3.3 / ND-9).
MEMOIR_PERSONA_COMPOSITION_MAX_LLM_CALLS_PER_PASS = 1

#: Complete JEV classification taxonomy.
JEV_CLASSIFICATIONS: tuple[str, ...] = (
    "conflict",
    "boundary_change",
    "new_rule",
    "reinforcement",
    "none",
)

#: Classes that force persona draft proposal creation.
JEV_GATED_CLASSES: frozenset[str] = frozenset(
    {"conflict", "boundary_change", "new_rule"}
)

#: Classes that are suppressed (no persona proposal).
JEV_SUPPRESSED_CLASSES: frozenset[str] = frozenset({"reinforcement", "none"})

#: THE PRECEDENCE ORDER (plan §3.1 taxonomy priority; V2-D 5.1).
#:
#: The plan writes the gated classes as ``conflict > boundary_change >
#: new_rule`` (plan §1.2 / §3.1) — a TAXONOMY PRIORITY, but it never
#: defines a COMPARATOR: nothing said what an ambiguous or multi-label
#: classifier response resolves to.  This tuple is that comparator (see
#: :func:`resolve_classification_precedence` and the implementation
#: notes §15.4 for the recorded ambiguity + the caller's ruling).
#:
#: THE ORDER IS THE TAXONOMY ORDER: the strongest identity-impact
#: signal wins.  A suppressed class NEVER masks a gated one — V2-38's
#: rule is that a candidate must not be silently dropped, and dropping
#: a real contradiction because the model also mentioned 'none' is
#: exactly the silent loss the ledger exists to prevent.
JEV_CLASSIFICATION_PRECEDENCE: tuple[str, ...] = (
    "conflict",
    "boundary_change",
    "new_rule",
    "reinforcement",
    "none",
)

#: Terminal error codes (V2-38 / V2-39).
TERMINAL_GATE_UNAVAILABLE = "terminal:gate_unavailable"
TERMINAL_UNBUILDABLE_DIFF = "terminal:unbuildable_diff"
MAX_ATTEMPTS = 5

#: The deferral marker written when a candidate is PARKED because the
#: per-pass composition budget was exhausted (C1).  It is NOT a
#: failure: the candidate is intact and re-selectable from the ledger.
DEFERRED_COMPOSITION_BUDGET = "deferred:composition_budget"

#: The marker prefix written when a candidate's processing RAISED (F1).
#: It is DISTINCT from the deferral marker on purpose: a budget
#: deferral means "the candidate is intact and will be re-selected",
#: while a gate error means "the candidate's evaluation FAILED" (the
#: attempt was consumed and the diagnostic is the exception).  The two
#: must never be confused in the ledger.
GATE_ERROR_PREFIX = "gate_error: "


class JevClassificationError(RuntimeError):
    """Raised when JEV LLM classification fails or returns invalid output."""


class DiffOverBudgetError(ValueError):
    """Raised when composed persona document exceeds the 2000-token budget."""


class UnbuildableDiffError(ValueError):
    """Raised when composed persona document is empty or malformed."""


@dataclass(frozen=True)
class JevClassificationResult:
    """The outcome of a JEV classification."""

    classification: str
    reasoning: str
    source: str  # "heuristic" | "llm"


@dataclass(frozen=True)
class JevOutcome:
    """The complete outcome of evaluating one synthesis through the gate."""

    synthesis_id: str
    status: str  # "drafted" | "suppressed" | "pending" | "terminal"
    classification: str | None
    attempts: int
    last_error: str | None
    draft_version_id: str | None = None
    version: int | None = None
    reasoning: str | None = None
    proposed_body: str | None = None


def _extract_json_object(text: str) -> dict[str, Any] | None:
    """Extract first valid JSON object from LLM response text."""
    if not text:
        return None
    # Look for json fenced code block
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except Exception:
            pass
    # Find outer curly braces
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except Exception:
            pass
    return None


def _clean_markdown_fences(text: str) -> str:
    """Strip wrapping markdown triple backticks if present."""
    s = text.strip()
    if s.startswith("```markdown"):
        s = s[len("```markdown") :].strip()
    elif s.startswith("```md"):
        s = s[len("```md") :].strip()
    elif s.startswith("```"):
        s = s[len("```") :].strip()
    if s.endswith("```"):
        s = s[:-3].strip()
    return s


def _extract_classification_labels(raw: Any) -> list[str]:
    """Split a raw classifier label field into its candidate labels.

    The classifier prompt asks for ONE category, but an LLM may answer
    with a list (``["conflict", "new_rule"]``) or a compound string
    (``"conflict, new_rule"``).  Both shapes are ACCEPTED here and
    resolved by :func:`resolve_classification_precedence` — the plan's
    ``conflict > boundary_change > new_rule`` has no comparator without
    this step (implementation notes §15.4).
    """
    if isinstance(raw, (list, tuple, set)):
        chunks = [str(item) for item in raw]
    else:
        chunks = [str(raw or "")]

    labels: list[str] = []
    for chunk in chunks:
        for part in re.split(r"[,;/|]|\band\b|\bor\b", chunk.lower()):
            part = part.strip().strip("\"'` \t\n")
            if part:
                labels.append(part)
    return labels


def resolve_classification_precedence(labels: Sequence[str]) -> str | None:
    """Resolve one or more candidate labels to ONE JEV class.

    THE PRECEDENCE RESOLUTION (the comparator the plan never wrote):
    the labels are ranked by :data:`JEV_CLASSIFICATION_PRECEDENCE` and
    the FIRST match wins — so an ambiguous or multi-label response
    resolves by ``conflict > boundary_change > new_rule``.

    Args:
        labels: The candidate labels extracted from the classifier
            response (unknown labels are IGNORED, not fatal).

    Returns:
        The winning classification, or ``None`` when NO candidate label
        is a known taxonomy member — the caller then parks the
        candidate (fail-closed, never a silent 'none').
    """
    seen = {str(label).strip().lower() for label in labels}
    for candidate in JEV_CLASSIFICATION_PRECEDENCE:
        if candidate in seen:
            return candidate
    return None


class MemoirPersonaGateService:
    """The Persona Gate Service managing JEV evaluation and proposal lifecycle."""

    def __init__(
        self,
        engine: Any,
        *,
        persona_repository: MemoirPersonaRepository | None = None,
        llm_caller: Callable[[str], str] | None = None,
        llm_config: dict[str, Any] | None = None,
        clock: Callable[[], datetime] | None = None,
        agent_id: str = "ari",
        call_timeout_s: float | None = None,
    ) -> None:
        self._engine = engine
        self._persona_repo = persona_repository or MemoirPersonaRepository(engine)
        self._llm_caller = llm_caller
        self._llm_config = dict(llm_config or {})
        self._clock = clock or now_utc
        self._agent_id = agent_id
        #: The per-call timeout for the LOCAL fallback lane (F2).  It
        #: RIDES the reflection service's ``call_timeout_s`` knob (the
        #: caller passes it in) — the house rule is ONE truth per knob,
        #: never a second hard-coded copy.  The default is the house
        #: constant, imported lazily so the two services keep their
        #: lazy (cycle-free) relationship.
        if call_timeout_s is None:
            from daemon.services.memoir_reflection_service import (
                MEMOIR_REFLECTION_CALL_TIMEOUT_S,
            )

            call_timeout_s = MEMOIR_REFLECTION_CALL_TIMEOUT_S
        self._call_timeout_s = float(call_timeout_s)
        #: The classification determined by the LAST ``process_candidate``
        #: call on THIS instance (``None`` when that call raised BEFORE a
        #: classification was determined).  The raise path in
        #: ``process_pass`` reads it so a PAID classification is never
        #: discarded (F1).  The gate is driven by ONE thread (the
        #: reflection worker), so a plain attribute is safe here.
        self._last_classification: str | None = None

    # ── Database ledger access ──────────────────────────────────────────

    def get_ledger_entry(self, synthesis_id: str) -> MemoirJevLedger | None:
        """Fetch the ledger row for this synthesis if it exists."""
        with Session(self._engine) as session:
            return session.exec(
                select(MemoirJevLedger).where(
                    MemoirJevLedger.agent_id == self._agent_id,
                    MemoirJevLedger.synthesis_id == synthesis_id,
                )
            ).first()

    def record_ledger_entry(
        self,
        *,
        synthesis_id: str,
        classification: str | None,
        attempts: int,
        last_error: str | None,
        status: str,
    ) -> MemoirJevLedger:
        """Upsert a row in ``memoir_jev_ledger`` atomically."""
        now = self._clock()
        with Session(self._engine) as session:
            entry = session.exec(
                select(MemoirJevLedger).where(
                    MemoirJevLedger.agent_id == self._agent_id,
                    MemoirJevLedger.synthesis_id == synthesis_id,
                )
            ).first()
            if entry is None:
                entry = MemoirJevLedger(
                    id=uuid.uuid4().hex,
                    agent_id=self._agent_id,
                    synthesis_id=synthesis_id,
                    classification=classification,
                    attempts=attempts,
                    last_error=last_error,
                    status=status,
                    created_at=now,
                    updated_at=now,
                )
                session.add(entry)
            else:
                entry.classification = classification
                entry.attempts = attempts
                entry.last_error = last_error
                entry.status = status
                entry.updated_at = now
            session.commit()
            session.refresh(entry)
            return entry

    def get_pending_candidates(self, limit: int = 10) -> list[MemoirJevLedger]:
        """Fetch candidate records waiting for retry (status == 'pending')."""
        with Session(self._engine) as session:
            stmt = (
                select(MemoirJevLedger)
                .where(
                    MemoirJevLedger.agent_id == self._agent_id,
                    MemoirJevLedger.status == "pending",
                    MemoirJevLedger.attempts < MAX_ATTEMPTS,
                )
                .order_by(MemoirJevLedger.created_at.asc())
                .limit(limit)
            )
            return list(session.exec(stmt).all())

    # ── Stage 1: Rule-based Heuristic Filter ─────────────────────────────

    def evaluate_heuristic(
        self,
        active_persona_body: str,
        claim: str,
        verb: str | None = None,
    ) -> JevClassificationResult | None:
        """Evaluate candidate synthesis with rule heuristics (zero LLM calls).

        Returns:
            JevClassificationResult if definitively classified, or None to
            defer to LLM classifier.
        """
        claim_stripped = (claim or "").strip()
        if not claim_stripped:
            return JevClassificationResult(
                classification="none",
                reasoning="Empty or whitespace claim has no identity impact.",
                source="heuristic",
            )

        claim_lower = claim_stripped.lower()
        active_lower = (active_persona_body or "").lower()

        # Pleasantries and transient conversational courtesies
        pleasantry_patterns = (
            r"\b(hello|hi|hey|good\s+(morning|afternoon|evening|night))\b",
            r"\b(thank\s+you|thanks|much\s+appreciated|you're\s+welcome)\b",
            r"\b(nice\s+to\s+meet\s+you|glad\s+to\s+help|pleasure)\b",
            r"\b(have\s+a\s+(good|great|nice)\s+day)\b",
            r"\b(how\s+are\s+you|how's\s+it\s+going)\b",
        )
        for pattern in pleasantry_patterns:
            if re.search(pattern, claim_lower):
                # If the entire claim is a pleasantry without substantial rules
                if len(claim_stripped.split()) <= 15:
                    return JevClassificationResult(
                        classification="none",
                        reasoning="Conversational pleasantry with no identity impact.",
                        source="heuristic",
                    )

        # Operational status remarks with no identity impact
        operational_patterns = (
            r"^(tests?\s+(passed|failed|green|red)|build\s+(succeeded|failed)|file\s+(written|saved)|command\s+executed|acknowledged\b|step\s+\d+\s+done)",
        )
        for pattern in operational_patterns:
            if re.search(pattern, claim_lower):
                return JevClassificationResult(
                    classification="none",
                    reasoning="Incidental operational status remark.",
                    source="heuristic",
                )

        # Verbatim overlap check: if the claim is already directly asserted in persona
        if claim_lower in active_lower and len(claim_stripped) > 10:
            return JevClassificationResult(
                classification="reinforcement",
                reasoning="Claim text is already asserted verbatim in current persona.",
                source="heuristic",
            )

        # Verb is CORROBORATE and key terms already present
        if verb and str(verb).upper() == "CORROBORATE":
            # Check if majority of non-stopwords in claim already appear in persona
            words = [w for w in re.findall(r"\w+", claim_lower) if len(w) > 3]
            if words and all(w in active_lower for w in words):
                return JevClassificationResult(
                    classification="reinforcement",
                    reasoning="Corroboration of existing concepts in persona.",
                    source="heuristic",
                )

        # Not discardable by heuristic -> defer to LLM classifier
        return None

    # ── Stage 2: LLM Classifier ──────────────────────────────────────────

    def _classification_prompt(
        self,
        active_persona_body: str,
        claim: str,
        verb: str | None = None,
    ) -> str:
        """Compose the JEV classification prompt."""
        verb_str = f"Synthesis Verb: {verb}\n" if verb else ""
        return (
            "You are the Judgment, Evaluation, and Validation (JEV) Classifier for Ari's Persona Evolution.\n"
            "Analyze this candidate belief claim against Ari's current approved persona document.\n\n"
            "Taxonomy:\n"
            "1. 'conflict': Directly contradicts an existing persona rule, directive, or belief.\n"
            "2. 'boundary_change': Expands or restricts Ari's operational domain, autonomy boundaries, or relationship stance.\n"
            "3. 'new_rule': Introduces a novel, enduring behavioral principle not previously covered.\n"
            "4. 'reinforcement': Re-asserts or validates an existing persona rule without altering boundaries.\n"
            "5. 'none': Incidental operational context, transient state, or pleasantries with no enduring identity impact.\n\n"
            "Current Approved Persona:\n"
            f"```markdown\n{active_persona_body}\n```\n\n"
            "Candidate Synthesis:\n"
            f"{verb_str}Claim: {claim}\n\n"
            "Respond ONLY with a JSON object in this format:\n"
            "{\n"
            '  "classification": "conflict" | "boundary_change" | "new_rule" | "reinforcement" | "none",\n'
            '  "reasoning": "A concise explanation of why this category was chosen."\n'
            "}\n"
        )

    def classify_with_llm(
        self,
        active_persona_body: str,
        synthesis: Any,
    ) -> JevClassificationResult:
        """Classify candidate synthesis using the LLM.

        Raises:
            JevClassificationError: On timeout, parse error, or invalid category.
        """
        claim = getattr(synthesis, "claim", str(synthesis))
        verb = getattr(synthesis, "verb", None)
        prompt = self._classification_prompt(active_persona_body, claim, verb)

        try:
            raw_response = self._call_llm(prompt)
        except Exception as exc:
            raise JevClassificationError(f"LLM call failed: {exc}") from exc

        parsed = _extract_json_object(raw_response)
        if not parsed or not isinstance(parsed, dict):
            raise JevClassificationError(
                f"Failed to parse JSON classification from response: {raw_response[:200]!r}"
            )

        cat = str(parsed.get("classification") or "").strip().lower()
        reasoning = str(parsed.get("reasoning") or "").strip()

        # THE PRECEDENCE RESOLUTION: a single label behaves exactly as
        # before; an AMBIGUOUS or MULTI-LABEL response resolves by
        # ``conflict > boundary_change > new_rule`` (the plan's taxonomy
        # priority — implementation notes §15.4).  NO known label is
        # still a failure: the candidate is PARKED, never defaulted.
        labels = _extract_classification_labels(parsed.get("classification"))
        resolved = resolve_classification_precedence(labels)
        if resolved is None:
            raise JevClassificationError(
                f"Classification {cat!r} not in {JEV_CLASSIFICATIONS}"
            )
        if len({label for label in labels if label in JEV_CLASSIFICATIONS}) > 1:
            reasoning = (
                f"{reasoning} [precedence-resolved from: "
                f"{', '.join(labels)}]".strip()
            )

        return JevClassificationResult(
            classification=resolved,
            reasoning=reasoning,
            source="llm",
        )

    # ── Stage 3: Persona Document Composition (B3 / V2-39) ──────────────

    def _composition_prompt(
        self,
        active_persona_body: str,
        claim: str,
        verb: str | None = None,
        classification: str = "new_rule",
        reasoning: str = "",
    ) -> str:
        """Compose the prompt for generating an updated persona document."""
        verb_str = f"Verb: {verb}\n" if verb else ""
        return (
            "You are composing an evolved version of Ari's durable Persona Document.\n"
            f"A new belief has been evaluated and classified as '{classification}' by the JEV gate.\n\n"
            f"Classification: {classification}\n"
            f"Reasoning: {reasoning}\n"
            f"{verb_str}Triggering Claim: {claim}\n\n"
            "Current Persona Document:\n"
            f"```markdown\n{active_persona_body}\n```\n\n"
            "Instructions:\n"
            "1. Integrate the new belief/boundary/rule into the persona markdown document.\n"
            "2. Preserve Ari's overall voice, structure, and Markdown headings.\n"
            "3. Keep the document concise and strictly within 2000 tokens.\n"
            "4. Return ONLY the complete updated Persona markdown document without markdown wrapper backticks."
        )

    def compose_persona_document(
        self,
        active_persona_body: str,
        synthesis: Any,
        classification: str,
        reasoning: str,
        base_version: int = 0,
    ) -> str:
        """Compose updated persona document and check token budget ceiling.

        Raises:
            DiffOverBudgetError: If generated text exceeds 2000 tokens.
            UnbuildableDiffError: If generated text is empty or malformed.
        """
        claim = getattr(synthesis, "claim", str(synthesis))
        verb = getattr(synthesis, "verb", None)
        synthesis_id = getattr(synthesis, "id", "")

        prompt = self._composition_prompt(
            active_persona_body=active_persona_body,
            claim=claim,
            verb=verb,
            classification=classification,
            reasoning=reasoning,
        )

        try:
            raw_response = self._call_llm(prompt)
        except Exception as exc:
            raise UnbuildableDiffError(f"LLM composition call failed: {exc}") from exc

        composed = _clean_markdown_fences(raw_response)
        if not composed or not composed.strip():
            raise UnbuildableDiffError("Composed persona document is empty")

        # Serialized structured metadata comment (V2-D 5.5 / ND-15)
        metadata_comment = (
            f"\n\n<!-- memoir-jev: base_version={base_version} "
            f"classification={classification} synthesis_id={synthesis_id} -->\n"
        )
        proposed_body = composed.strip() + metadata_comment

        tokens = estimate_tokens(proposed_body)
        if tokens > MEMOIR_PERSONA_TOKEN_BUDGET:
            raise DiffOverBudgetError(
                f"Composed persona exceeds token budget: {tokens} > {MEMOIR_PERSONA_TOKEN_BUDGET}"
            )

        return proposed_body

    # ── Candidate Evaluation Lifecycle ───────────────────────────────────

    def process_candidate(self, synthesis: Any) -> JevOutcome:
        """Evaluate one candidate synthesis through the JEV pipeline.

        Adheres strictly to V2-38 failure parking and V2-39 unbuildable diff
        parking.
        """
        synthesis_id = getattr(synthesis, "id", "")
        claim = getattr(synthesis, "claim", "")
        verb = getattr(synthesis, "verb", None)

        # The RAISE PATH reads this (F1): it is the classification this
        # call determined, so a later raise can PRESERVE the paid work
        # instead of discarding it.  Reset FIRST — a raise before any
        # classification must leave it ``None``.
        self._last_classification = None

        if not synthesis_id:
            raise ValueError("synthesis must have an 'id'")

        # Check existing ledger status
        existing = self.get_ledger_entry(synthesis_id)
        if existing:
            # If already resolved or terminal, do not re-process (ND-14)
            if existing.status in ("drafted", "suppressed", "terminal"):
                return JevOutcome(
                    synthesis_id=synthesis_id,
                    status=existing.status,
                    classification=existing.classification,
                    attempts=existing.attempts,
                    last_error=existing.last_error,
                )
            attempts = existing.attempts
        else:
            attempts = 0

        current_persona = self._persona_repo.get_current(self._agent_id)
        active_body = current_persona.body if current_persona else ""
        base_version = current_persona.version if current_persona else 0

        # If already classified in a prior pass, resume with that classification
        if existing and existing.classification in JEV_GATED_CLASSES:
            attempts += 1
            classification_res = JevClassificationResult(
                classification=existing.classification,
                reasoning="",
                source="ledger",
            )
        else:
            # Step 1: Heuristic classification
            classification_res = self.evaluate_heuristic(active_body, claim, verb)

            # Step 2: LLM classification if heuristic did not decide
            if classification_res is None:
                attempts += 1
                try:
                    classification_res = self.classify_with_llm(active_body, synthesis)
                except Exception as exc:
                    # LLM classification failure (V2-D 5.6 / B2)
                    # DO NOT default to 'none'! Park in ledger.
                    terminal = attempts >= MAX_ATTEMPTS
                    status = "terminal" if terminal else "pending"
                    last_err = TERMINAL_GATE_UNAVAILABLE if terminal else f"classification_failed: {exc}"
                    self.record_ledger_entry(
                        synthesis_id=synthesis_id,
                        classification=None,
                        attempts=attempts,
                        last_error=last_err,
                        status=status,
                    )
                    logger.warning(
                        "[MemoirPersonaGate] classification failed for synthesis %s "
                        "(attempt %d/%d, status=%s): %s",
                        synthesis_id,
                        attempts,
                        MAX_ATTEMPTS,
                        status,
                        exc,
                    )
                    return JevOutcome(
                        synthesis_id=synthesis_id,
                        status=status,
                        classification=None,
                        attempts=attempts,
                        last_error=last_err,
                    )

        cat = classification_res.classification
        reason = classification_res.reasoning
        # The classification is now PAID FOR — record it so the raise
        # path (F1) can preserve it instead of re-classifying the
        # candidate on every later pass.
        self._last_classification = cat

        # Step 3: Check category routing
        if cat in JEV_SUPPRESSED_CLASSES:
            # Suppressed classes (reinforcement, none) -> no proposal
            self.record_ledger_entry(
                synthesis_id=synthesis_id,
                classification=cat,
                attempts=attempts,
                last_error=None,
                status="suppressed",
            )
            logger.info(
                "[MemoirPersonaGate] synthesis %s suppressed as %s: %s",
                synthesis_id,
                cat,
                reason,
            )
            return JevOutcome(
                synthesis_id=synthesis_id,
                status="suppressed",
                classification=cat,
                attempts=attempts,
                last_error=None,
                reasoning=reason,
            )

        # Gated classes (conflict, boundary_change, new_rule) -> compose proposed_body
        try:
            proposed_body = self.compose_persona_document(
                active_persona_body=active_body,
                synthesis=synthesis,
                classification=cat,
                reasoning=reason,
                base_version=base_version,
            )
        except Exception as exc:
            # Unbuildable or over-budget diff (V2-D 5.9 / B3)
            terminal = attempts >= MAX_ATTEMPTS
            status = "terminal" if terminal else "pending"
            is_budget = isinstance(exc, DiffOverBudgetError)
            last_err = TERMINAL_UNBUILDABLE_DIFF if terminal else ("diff_over_budget" if is_budget else f"unbuildable_diff: {exc}")
            self.record_ledger_entry(
                synthesis_id=synthesis_id,
                classification=cat,
                attempts=attempts,
                last_error=last_err,
                status=status,
            )
            logger.warning(
                "[MemoirPersonaGate] persona composition failed for synthesis %s "
                "(attempt %d/%d, status=%s): %s",
                synthesis_id,
                attempts,
                MAX_ATTEMPTS,
                status,
                exc,
            )
            return JevOutcome(
                synthesis_id=synthesis_id,
                status=status,
                classification=cat,
                attempts=attempts,
                last_error=last_err,
                reasoning=reason,
            )

        # Valid diff -> create draft and submit (T5.4)
        draft = self._persona_repo.create_draft(
            agent_id=self._agent_id,
            body=proposed_body,
            source="ari",
        )
        submitted = self._persona_repo.submit(draft.id)
        self.record_ledger_entry(
            synthesis_id=synthesis_id,
            classification=cat,
            attempts=attempts,
            last_error=None,
            status="drafted",
        )
        logger.info(
            "[MemoirPersonaGate] draft persona v%d created and submitted (%s) for synthesis %s",
            draft.version,
            draft.id,
            synthesis_id,
        )
        return JevOutcome(
            synthesis_id=synthesis_id,
            status="drafted",
            classification=cat,
            attempts=attempts,
            last_error=None,
            draft_version_id=submitted.id,
            version=submitted.version,
            reasoning=reason,
            proposed_body=proposed_body,
        )

    def process_pass(
        self,
        new_syntheses: Sequence[Any] = (),
        max_composition_calls: int = MEMOIR_PERSONA_COMPOSITION_MAX_LLM_CALLS_PER_PASS,
    ) -> list[JevOutcome]:
        """Run JEV evaluation for a reflection pass.

        Evaluates pending retry candidates and newly committed syntheses,
        respecting the per-pass composition LLM call budget.
        """
        outcomes: list[JevOutcome] = []
        candidates_to_process: list[Any] = []

        # 1. Fetch pending candidates from ledger
        pending_records = self.get_pending_candidates(limit=5)
        if pending_records:
            with Session(self._engine) as session:
                for rec in pending_records:
                    syn = session.exec(
                        select(MemoirSynthesis).where(
                            MemoirSynthesis.id == rec.synthesis_id
                        )
                    ).first()
                    if syn:
                        candidates_to_process.append(syn)

        # 2. Add new syntheses (avoiding duplicates)
        seen_ids = {getattr(c, "id", "") for c in candidates_to_process}
        for s in new_syntheses:
            sid = getattr(s, "id", "")
            if sid and sid not in seen_ids:
                candidates_to_process.append(s)
                seen_ids.add(sid)

        composition_calls = 0
        processed_ids: set[str] = set()
        try:
            for cand in candidates_to_process:
                cand_id = getattr(cand, "id", "")
                try:
                    # Check if this candidate already classified as gated but pending composition
                    existing = self.get_ledger_entry(cand.id)
                    if existing and existing.classification in JEV_GATED_CLASSES and existing.status == "pending":
                        if composition_calls >= max_composition_calls:
                            # Defer to next pass — the ledger row ALREADY exists,
                            # so the candidate stays re-selectable.
                            continue

                    outcome = self.process_candidate(cand)
                except Exception as exc:
                    # ── THE RAISE PATH (F1) ────────────────────────────
                    # ``process_candidate`` can raise AFTER the
                    # classification was paid for and BEFORE any ledger
                    # row exists (the composition ``try`` covers only
                    # ``compose_persona_document``; ``create_draft`` /
                    # ``submit`` sit OUTSIDE it).  The pre-fix shape
                    # let that escape the loop: the pass ABORTED (a
                    # HEAD-OF-LINE STALL — the poison row is re-selected
                    # first every pass and breaks the loop before anyone
                    # else), the PAID classification was discarded (a
                    # re-classify + re-compose EVERY pass), the attempt
                    # counter stayed at 0 (the terminal-at-5 ladder was
                    # unreachable), and a store failure was MASKED as a
                    # budget deferral.
                    #
                    # HERE the failure is CONTAINED: the candidate is
                    # JOURNALED (attempts consumed, classification
                    # preserved, a NON-deferral diagnostic), the id is
                    # marked processed, and the pass CONTINUES with the
                    # next candidate.
                    if self._journal_gate_error(cand_id, exc):
                        processed_ids.add(cand_id)
                    continue
                outcomes.append(outcome)
                processed_ids.add(cand_id)
                if outcome.proposed_body is not None or (outcome.last_error and "diff" in outcome.last_error):
                    composition_calls += 1
                if composition_calls >= max_composition_calls:
                    # Reached composition budget for this pass — the REMAINING
                    # candidates are parked below (they are NOT dropped).
                    break
        finally:
            # ── THE NO-CANDIDATE-IS-LOST PARK (C1) ────────────────────
            # The per-pass composition budget can end the loop before every
            # candidate was evaluated.  The ONLY re-selection source is
            # ``get_pending_candidates()`` (the ledger), so a candidate with
            # NO ledger row is PERMANENTLY LOST from the JEV lane — exactly
            # the data loss V2-38 exists to forbid.  Every candidate this
            # pass did NOT process therefore lands a 'pending' row NOW.
            #
            # THE PARK IS IN A ``finally``: the invariant is UNCONDITIONAL —
            # a candidate that RAISES must be parked too, or the pass's
            # failure would drop every candidate after it.
            self._park_unprocessed_candidates(candidates_to_process, processed_ids)

        return outcomes

    def _journal_gate_error(self, synthesis_id: str, exc: BaseException) -> bool:
        """Journal a candidate whose processing RAISED (F1).

        The raise path must leave the ledger in a state that says
        "this candidate FAILED", never "this candidate was merely
        deferred".  The row carries:

        * the classification if one was already determined — the PAID
          work is preserved, so the retry does NOT re-pay the
          classifier (``None`` only when the raise happened BEFORE any
          classification);
        * ``attempts`` incremented by ONE — the attempt was consumed, so
          the retry ladder can reach ``MAX_ATTEMPTS`` and TERMINALIZE
          (the pre-fix shape froze the counter at 0, so the ladder was
          unreachable on this path);
        * a NON-deferral diagnostic — ``gate_error: <Type>: <message>`` —
          so a store failure is never MASKED as a budget deferral;
        * ``status='terminal'`` once the attempts reach ``MAX_ATTEMPTS``,
          else ``'pending'`` (the candidate stays re-selectable).

        Returns:
            True when the row was written — the caller then marks the
            candidate processed so the ``finally``-park does not touch
            it.  False when the row could NOT be written (a store
            outage); the caller then leaves the candidate unprocessed so
            the park still gets a chance to give it a row: a store
            failure must never SILENTLY lose a candidate.
        """
        if not synthesis_id:
            # An un-addressable candidate (no id) cannot be keyed in the
            # ledger — there is nothing to re-select it by.
            logger.error(
                "[MemoirPersonaGate] a raising candidate carries no id — "
                "it cannot be journaled (nothing to re-select it by): %s: %s",
                type(exc).__name__,
                exc,
            )
            return False

        prior_attempts = 0
        classification: str | None = None
        try:
            existing = self.get_ledger_entry(synthesis_id)
        except Exception as read_exc:  # pragma: no cover - store outage
            logger.error(
                "[MemoirPersonaGate] could not read the ledger row for "
                "synthesis %s while journaling a gate error: %s",
                synthesis_id,
                read_exc,
            )
            existing = None
        if existing is not None:
            prior_attempts = int(existing.attempts or 0)
            classification = existing.classification
        if classification is None:
            # The classification this pass already PAID for (F1).
            classification = self._last_classification

        attempts = prior_attempts + 1
        terminal = attempts >= MAX_ATTEMPTS
        status = "terminal" if terminal else "pending"
        last_error = f"{GATE_ERROR_PREFIX}{type(exc).__name__}: {exc}"

        try:
            self.record_ledger_entry(
                synthesis_id=synthesis_id,
                classification=classification,
                attempts=attempts,
                last_error=last_error,
                status=status,
            )
        except Exception as write_exc:  # pragma: no cover - store outage
            logger.error(
                "[MemoirPersonaGate] could not journal the gate error for "
                "synthesis %s: %s",
                synthesis_id,
                write_exc,
            )
            return False

        logger.warning(
            "[MemoirPersonaGate] synthesis %s raised in the gate "
            "(attempt %d/%d, status=%s): %s: %s",
            synthesis_id,
            attempts,
            MAX_ATTEMPTS,
            status,
            type(exc).__name__,
            exc,
        )
        return True

    def _park_unprocessed_candidates(
        self,
        candidates: Sequence[Any],
        processed_ids: set[str],
    ) -> None:
        """Park every candidate this pass did NOT process (C1).

        A candidate dropped by the per-pass composition budget MUST land
        a ``pending`` ledger row: the ledger is the ONLY re-selection
        source (``get_pending_candidates()``), so a candidate with no
        row is lost forever.

        The parked row carries:
        * the classification if it is ALREADY KNOWN (a candidate that was
          classified in an earlier pass keeps its stored classification —
          the retry must NOT re-pay the classifier).  NOTE: a candidate
          whose classification was determined THIS pass and then RAISED
          is NOT parked here — the raise path journals it with its
          classification PRESERVED (F1, ``_journal_gate_error``); only a
          candidate the pass never got to (the budget ended the loop) is
          parked without a classification;
        * ``attempts`` as they stand (a candidate parked by an earlier
          CLASSIFICATION FAILURE keeps its attempt count — it is already
          parked and must not be re-counted);
        * a deferral marker in ``last_error`` (``deferred:composition_budget``)
          so the deferral is visible and is NOT mistaken for a failure.
          A candidate whose processing RAISED never carries this marker
          — it carries ``gate_error: …`` (F1).
        """
        for cand in candidates:
            synthesis_id = getattr(cand, "id", "")
            if not synthesis_id or synthesis_id in processed_ids:
                continue
            existing = self.get_ledger_entry(synthesis_id)
            if existing is not None:
                # ALREADY parked (the row IS the re-selection handle) —
                # keep it, and keep its diagnostic last_error intact.
                continue
            self.record_ledger_entry(
                synthesis_id=synthesis_id,
                classification=None,
                attempts=0,
                last_error=DEFERRED_COMPOSITION_BUDGET,
                status="pending",
            )
            logger.info(
                "[MemoirPersonaGate] synthesis %s deferred by the per-pass "
                "composition budget — parked as 'pending' so the next pass "
                "re-selects it (no candidate is lost)",
                synthesis_id,
            )

    # ── LLM dispatch helper ──────────────────────────────────────────────

    def _call_llm(self, prompt: str) -> str:
        """Dispatch prompt through injected caller or house failover."""
        if self._llm_caller is not None:
            return self._llm_caller(prompt)
        return self._house_llm_call(prompt)

    def _house_llm_call(self, prompt: str) -> str:
        """House failover caller using invoke_raw_with_failover.

        THE ARGUMENT ORDER IS ``invoke_raw_with_failover(factory,
        llm_config)`` (F1) — the reversed order raised
        ``AttributeError: 'function' object has no attribute 'get'`` at
        the first real call.

        The per-call timeout RIDES ``self._call_timeout_s`` (the
        reflection service's ``call_timeout_s`` knob — F2), and the
        helper's signature is ``httpx_timeout(timeout_s)`` — the
        ``connect=`` keyword does not exist there.
        """
        import openai

        from daemon.services.llm_failover import (
            current_failover_url,
            invoke_raw_with_failover,
        )
        from daemon.services.memoir_reflection_service import httpx_timeout

        config = self._llm_config
        model = str(config.get("model") or "gpt-4")
        api_key = str(config.get("api_key") or "")
        base_url = config.get("base_url")
        timeout_s = float(self._call_timeout_s)

        def _factory() -> str:
            url = current_failover_url() or base_url
            client = openai.OpenAI(
                api_key=api_key,
                base_url=url or None,
                timeout=httpx_timeout(timeout_s),
                max_retries=0,
            )
            response = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,
            )
            choices = getattr(response, "choices", None) or []
            if not choices:
                raise ValueError("persona_gate_response_empty: no choices")
            message = getattr(choices[0], "message", None)
            content = getattr(message, "content", None)
            if not content:
                raise ValueError("persona_gate_response_empty: empty content")
            return str(content)

        return str(invoke_raw_with_failover(_factory, config))


__all__ = [
    "DEFERRED_COMPOSITION_BUDGET",
    "DiffOverBudgetError",
    "GATE_ERROR_PREFIX",
    "JEV_CLASSIFICATIONS",
    "JEV_CLASSIFICATION_PRECEDENCE",
    "JEV_GATED_CLASSES",
    "JEV_SUPPRESSED_CLASSES",
    "JevClassificationError",
    "JevClassificationResult",
    "JevOutcome",
    "MEMOIR_PERSONA_COMPOSITION_MAX_LLM_CALLS_PER_PASS",
    "MAX_ATTEMPTS",
    "MemoirPersonaGateService",
    "TERMINAL_GATE_UNAVAILABLE",
    "TERMINAL_UNBUILDABLE_DIFF",
    "UnbuildableDiffError",
    "resolve_classification_precedence",
]
