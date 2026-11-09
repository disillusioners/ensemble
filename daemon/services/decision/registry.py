"""Decision module — the seam registry (``seam-registry.md`` §1–§4).

The registry is the **single place** where the ensemble decides whether
a typed overlay may be consulted at a decision point — and, if so, how
its answer may be combined with the heuristic's answer.

**A seam registers a RECORD, never a call site.** The record's five
parts (``seam-registry.md`` §2): ``seam_id``, ``question_kind``,
``build_input``, ``apply``, ``monotonicity``. The registry owns the
CALL, so no seam can add a retry, a failover, a cache, or a second call
— that centralisation is what makes D2 enforceable rather than
aspirational.

Three contracts live here:

* **§2.1 the apply contract** — ``apply(heuristic_outcome,
  overlay_outcome)``: the heuristic is ALWAYS an argument (D1 made
  structural) and the overlay outcome is optional (D2 made structural).
* **§2.2 the monotonicity contract** — D4 translated, enforced AT
  REGISTRATION and AT APPLY TIME.
* **§2.3 the fail-open fence** — the WHOLE consult (``build_input`` →
  the call → ``apply``) is wrapped; any exception in ANY stage yields
  the heuristic result (``seam_fault``). **The registry owns this fence
  — a seam cannot narrow it and cannot decide to raise.**

**Direction-scoped monotonicity (FLAG5).** Superset-only is NOT a
global rule: it is a property of the ADD-ONLY directions
(``TOWARD_COMPLETION`` / ``TOWARD_TIGHTENING``). ``WITHIN_SET`` must
stay inside the candidate set and must NEVER empty it.
``ADVISORY_ONLY`` lets the heuristic stand regardless. A global
superset-only rule would break S1 — the only P1 seam.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

from daemon.services.decision.client import ClientOutcome, DecisionClient
from daemon.services.decision.config import DecisionConfig
from daemon.services.decision.types import (
    VERDICT_AGREE,
    VERDICT_DISAGREE,
    VERDICT_NO_OVERLAY,
    DecisionAnswer,
    DecisionRequest,
    DecisionStage,
    FailureClass,
    Monotonicity,
    ProvisioningState,
    QuestionKind,
    SuppressionReason,
)

logger = logging.getLogger(__name__)

__all__ = [
    "DENIED_SEAM_ROLES",
    "FencedResult",
    "RegistrationRefused",
    "ReviewRecord",
    "SeamRecord",
    "SeamRegistry",
    "StageTransitionRefused",
]


#: The standing exclusion list (EX-1 … EX-5) as REGISTRATION ROLE names.
#:
#: The canonical text lives in ``scope-and-doctrine.md`` §D5.1 — it is
#: REFERENCED here by §id, never re-quoted (``seam-registry.md`` §3).
#: The registry carries the set so a registry and the doctrine cannot
#: drift apart.
#:
#: * EX-1 retry classification
#: * EX-2 compaction message selection
#: * EX-3 snapshot L2 trigger-query generation
#: * EX-4 snapshot L1 digest generation
#: * EX-5 symptom ladder rungs
DENIED_SEAM_ROLES: frozenset[str] = frozenset(
    {
        "retry_classification",
        "compaction_message_selection",
        "snapshot_trigger_query_generation",
        "snapshot_digest_generation",
        "symptom_ladder_rungs",
    }
)

#: The kind → direction coherence table (``thin-client.md`` §8.1's typed
#: mapping, generalised). A registration outside this table is an
#: INCOHERENT DIRECTION and is refused (§2.2).
ALLOWED_DIRECTIONS: Mapping[QuestionKind, frozenset[Monotonicity]] = {
    QuestionKind.CHOICE: frozenset(
        {Monotonicity.WITHIN_SET, Monotonicity.ADVISORY_ONLY}
    ),
    QuestionKind.PREDICATE: frozenset(
        {
            Monotonicity.TOWARD_COMPLETION,
            Monotonicity.TOWARD_TIGHTENING,
            Monotonicity.ADVISORY_ONLY,
        }
    ),
    QuestionKind.SCORE: frozenset({Monotonicity.ADVISORY_ONLY}),
}

#: The add-only directions — the overlay may ADD, never remove.
_ADD_ONLY_DIRECTIONS = frozenset(
    {Monotonicity.TOWARD_COMPLETION, Monotonicity.TOWARD_TIGHTENING}
)

#: The outcome shapes a seam may declare.
OUTCOME_KINDS = ("set", "ordered_set", "signal", "scalar")


class RegistrationRefused(Exception):
    """A seam registration was refused. No call path is created."""


class StageTransitionRefused(Exception):
    """A ``shadow`` → ``deciding`` transition was refused (D3)."""


@dataclass(frozen=True)
class ReviewRecord:
    """The review + ratification record a stage transition requires (D3).

    ``ratified_by`` must name the Emperor's ratification explicitly — a
    review alone is NOT a ratification.
    """

    reviewer: str = ""
    ratified_by: str = ""
    notes: str = ""

    @property
    def ratified(self) -> bool:
        """True when the record carries an explicit ratification."""
        return bool(self.ratified_by.strip())


@dataclass(frozen=True)
class SeamRecord:
    """One registered seam.

    Attributes:
        seam_id: The stable id (used by metric labels, the shadow log
            and the kill-switch matrix).
        role: The seam's ROLE — checked against
            ``DENIED_SEAM_ROLES`` at registration (EX-1…EX-5).
        question_kind: Which typed kind this seam asks for.
        build_input: Builds the request from the seam's own context.
            The seam cannot see the transport.
        apply: Merges the overlay answer into the heuristic answer.
            ``apply(heuristic_outcome, overlay_outcome)`` — the
            heuristic is ALWAYS an argument; the overlay outcome may be
            ``None``. The seam cannot make a network call.
        monotonicity: The declared direction.
        outcome_kind: The shape of the outcome the seam's ``apply``
            returns (``set`` / ``ordered_set`` / ``signal`` /
            ``scalar``).
        confidence_floor: The seam's floor (``thin-client.md`` §8.2.1)
            — applied in the deciding stage only.
        timeout_s: The lane's per-call budget override.
        gate_switch: The seam's OWN existing gate switch (e.g.
            ``ENSEMBLE_SYMPTOM_REPAIR_LADDER``). It is part of the
            CONSULT gate composition (§3.2.2) and is read per consult.
        question_id: An opaque, non-content question id for the metric
            labels.
        compare: Optional seam-owned comparator for the shadow A/B
            verdict. ``None`` ⇒ the registry's generic comparison.
    """

    seam_id: str
    role: str
    question_kind: QuestionKind
    build_input: Callable[[Mapping[str, Any]], DecisionRequest]
    apply: Callable[[Any, Any], Any]
    monotonicity: Monotonicity
    outcome_kind: str = "set"
    confidence_floor: float | None = None
    timeout_s: float | None = None
    gate_switch: Callable[[], bool] | None = None
    question_id: str = ""
    compare: Callable[[Any, DecisionAnswer], bool] | None = None


@dataclass(frozen=True)
class FencedResult:
    """The result of one fenced consult (the whole consult, §2.3).

    Attributes:
        outcome: The effective outcome — the heuristic's, unless the
            overlay acted (deciding stage, monotonicity satisfied).
        overlay_outcome: The typed answer (``None`` on failure).
        failure_class: The failure label (rows 6–30 / ``seam_fault``).
        suppression: The suppression reason (rows 1–5).
        applied: True when the overlay's answer moved the outcome.
        refused: True when the apply violated the monotonicity (the
            heuristic stands).
        reached_socket: True when an attempt was made (cost bills).
        latency_s / cost_usd: The attempt's wall clock and cost.
        candidate_ids: The question's offered option values — the
            candidate set the ``WITHIN_SET`` check uses.
        verdict: The shadow A/B verdict.
        selection_overlap_ratio: The S1 set-level overlap, when
            computable.
    """

    outcome: Any = None
    overlay_outcome: DecisionAnswer | None = None
    failure_class: FailureClass | None = None
    suppression: SuppressionReason | None = None
    applied: bool = False
    refused: bool = False
    reached_socket: bool = False
    latency_s: float = 0.0
    cost_usd: float = 0.0
    total_tokens: int | None = None
    usage: Mapping[str, Any] | None = None
    candidate_ids: tuple[str, ...] = ()
    unanswered_questions: tuple[str, ...] = ()
    provisioning_state: ProvisioningState | None = None
    verdict: str = VERDICT_NO_OVERLAY
    selection_overlap_ratio: float | None = None


class SeamRegistry:
    """The admission point — records, exclusion, monotonicity, the fence.

    Args:
        client: The thin client (the registry owns the call).
        config: The resolved configuration (the registry owns the model).
    """

    def __init__(
        self,
        *,
        client: DecisionClient,
        config: DecisionConfig,
    ) -> None:
        self._client = client
        self._config = config
        self._records: dict[str, SeamRecord] = {}
        self._stages: dict[str, DecisionStage] = {}
        self._ratified: dict[str, ReviewRecord] = {}

    # ── admission ───────────────────────────────────────────────────────

    @property
    def denied_seam_roles(self) -> frozenset[str]:
        """The EX-1…EX-5 roles — enforced at registration (§3)."""
        return DENIED_SEAM_ROLES

    def register(self, record: SeamRecord) -> SeamRecord:
        """Register a seam record — or REFUSE it.

        Refusals (all BEFORE any call path exists):

        1. the record names an EX-1…EX-5 role (§3);
        2. the monotonicity is missing or unknown (§2.2);
        3. the declared direction is INCOHERENT with the question kind
           or the outcome shape (§2.2);
        4. the seam id is empty or already registered.

        Raises:
            RegistrationRefused: with the reason.
        """
        if not record.seam_id.strip():
            raise RegistrationRefused("seam_id must not be empty")
        if record.seam_id in self._records:
            raise RegistrationRefused(f"seam already registered: {record.seam_id}")
        if record.role.strip().lower() in DENIED_SEAM_ROLES:
            raise RegistrationRefused(
                f"seam role {record.role!r} is on the standing exclusion list "
                "(EX-1..EX-5) — refused before any call path exists"
            )
        if not isinstance(record.monotonicity, Monotonicity):
            raise RegistrationRefused(
                "monotonicity must be a Monotonicity member (§2.2)"
            )
        if record.monotonicity is Monotonicity.NONE:
            raise RegistrationRefused(
                "monotonicity NONE means an excluded seam — refused (§2.2)"
            )
        allowed = ALLOWED_DIRECTIONS.get(record.question_kind, frozenset())
        if record.monotonicity not in allowed:
            raise RegistrationRefused(
                f"incoherent direction {record.monotonicity.value} for a "
                f"{record.question_kind.value} seam (allowed: "
                f"{sorted(d.value for d in allowed)})"
            )
        if record.outcome_kind not in OUTCOME_KINDS:
            raise RegistrationRefused(
                f"unknown outcome_kind {record.outcome_kind!r} "
                f"(allowed: {list(OUTCOME_KINDS)})"
            )
        if record.monotonicity in _ADD_ONLY_DIRECTIONS and record.outcome_kind not in (
            "set",
            "signal",
        ):
            raise RegistrationRefused(
                f"an add-only direction ({record.monotonicity.value}) needs a "
                "collection outcome (set/signal) so the superset rule is "
                "checkable"
            )
        if (
            record.monotonicity is Monotonicity.WITHIN_SET
            and record.outcome_kind not in ("ordered_set", "set")
        ):
            raise RegistrationRefused(
                "WITHIN_SET needs an ordered_set/set outcome so the "
                "stay-inside-the-candidate-set rule is checkable"
            )
        if not callable(record.build_input) or not callable(record.apply):
            raise RegistrationRefused("build_input and apply must be callable")

        self._records[record.seam_id] = record
        self._stages.setdefault(record.seam_id, DecisionStage.SHADOW)
        logger.info(
            "decision seam registered: seam_id=%s kind=%s direction=%s",
            record.seam_id,
            record.question_kind.value,
            record.monotonicity.value,
        )
        return record

    def unregister(self, seam_id: str) -> bool:
        """Remove a seam's registration (the durable half of a rollback).

        With no record, the registry cannot call out on the seam's
        behalf — even if the switch were re-enabled by mistake
        (``controls-and-phasing.md`` §4.1).
        """
        self._ratified.pop(seam_id, None)
        self._stages.pop(seam_id, None)
        return self._records.pop(seam_id, None) is not None

    def get(self, seam_id: str) -> SeamRecord | None:
        """The registered record, or ``None``."""
        return self._records.get(seam_id)

    def records(self) -> Mapping[str, SeamRecord]:
        """The registered records (read-only view)."""
        return dict(self._records)

    def seam_ids(self) -> tuple[str, ...]:
        """The registered seam ids."""
        return tuple(self._records.keys())

    # ── the D3 stage transition ─────────────────────────────────────────

    def transition_stage(
        self,
        seam_id: str,
        to_stage: DecisionStage,
        *,
        review: ReviewRecord | None = None,
    ) -> DecisionStage:
        """Move a seam's stage — REFUSING an unratified promotion (D3).

        A ``shadow`` → ``deciding`` transition requires an explicit
        review + ratification record (the Emperor's ratification). In P1
        the apply path is therefore PRESENT but UNREACHABLE.

        Raises:
            StageTransitionRefused: when the transition lacks a
                ratification record.
        """
        current = self._stages.get(seam_id, DecisionStage.SHADOW)
        if to_stage is DecisionStage.DECIDING and current is DecisionStage.SHADOW:
            if review is None or not review.ratified:
                raise StageTransitionRefused(
                    "shadow -> deciding requires an explicit review + "
                    "ratification record (D3); no seam may move to deciding "
                    "until its shadow window has been reviewed and the "
                    "Emperor has ratified the transition"
                )
            self._ratified[seam_id] = review
        self._stages[seam_id] = to_stage
        return to_stage

    def stage(self, seam_id: str) -> DecisionStage:
        """The registry's recorded stage for a seam."""
        return self._stages.get(seam_id, DecisionStage.SHADOW)

    def is_deciding_permitted(self, seam_id: str) -> bool:
        """True only when a RATIFIED transition exists for the seam.

        The overlay's apply path consults this — so writing ``deciding``
        into the store alone can never make the overlay act (the D3 gate
        is structural, not a convention).
        """
        return (
            self._stages.get(seam_id) is DecisionStage.DECIDING
            and seam_id in self._ratified
        )

    # ── the fenced consult ──────────────────────────────────────────────

    async def execute(
        self,
        seam_id: str,
        *,
        heuristic_outcome: Any,
        context: Mapping[str, Any],
        floor: float | None = None,
        allow_apply: bool = False,
    ) -> FencedResult:
        """Run ONE fenced consult: ``build_input`` → the call → ``apply``.

        The fence covers the WHOLE consult (§2.3): an exception in ANY
        stage yields the heuristic result and the ``seam_fault`` label.
        This method NEVER raises.

        Args:
            seam_id: The seam to consult.
            heuristic_outcome: The heuristic's answer (D1's permanent
                default).
            context: The seam's own context — handed to ``build_input``.
            floor: The seam's confidence floor (``None`` ⇒ no floor).
            allow_apply: True only when the apply path is permitted
                (the deciding stage AND a ratified D3 transition).
        """
        record = self._records.get(seam_id)
        if record is None:
            return FencedResult(
                outcome=heuristic_outcome,
                failure_class=FailureClass.SEAM_FAULT,
            )

        # ── stage 1: build the question ────────────────────────────────
        try:
            request = record.build_input(context)
            if not isinstance(request, DecisionRequest):
                raise TypeError("build_input must return a DecisionRequest")
            request = request.with_model(self._config.model)
            candidate_ids = tuple(
                value
                for question in request.questions
                for value in question.option_values
            )
        except Exception as exc:  # noqa: BLE001 — the fence owns this
            logger.warning(
                "decision seam %s build_input failed (fail-open): %s: %s",
                seam_id,
                type(exc).__name__,
                exc,
            )
            return FencedResult(
                outcome=heuristic_outcome,
                failure_class=FailureClass.SEAM_FAULT,
            )

        # ── stage 2: the call (single attempt, never raises) ──────────
        try:
            client_outcome: ClientOutcome = await self._client.consult(
                request,
                floor=floor,
                timeout_s=record.timeout_s,
            )
        except Exception as exc:  # noqa: BLE001 — the client never raises
            logger.warning(
                "decision seam %s consult failed (fail-open): %s: %s",
                seam_id,
                type(exc).__name__,
                exc,
            )
            return FencedResult(
                outcome=heuristic_outcome,
                failure_class=FailureClass.SEAM_FAULT,
                candidate_ids=candidate_ids,
            )

        if client_outcome.suppression is not None:
            return FencedResult(
                outcome=heuristic_outcome,
                suppression=client_outcome.suppression,
                candidate_ids=candidate_ids,
            )
        if client_outcome.failure_class is not None:
            return FencedResult(
                outcome=heuristic_outcome,
                failure_class=client_outcome.failure_class,
                latency_s=client_outcome.latency_s,
                cost_usd=client_outcome.cost_usd,
                total_tokens=client_outcome.total_tokens,
                usage=client_outcome.usage,
                reached_socket=client_outcome.reached_socket,
                candidate_ids=candidate_ids,
                unanswered_questions=client_outcome.unanswered_questions,
                provisioning_state=client_outcome.provisioning_state,
            )

        answer = client_outcome.answer
        verdict = self._verdict(record, heuristic_outcome, answer, candidate_ids)
        overlap = _overlap_ratio(heuristic_outcome, answer, candidate_ids)

        # ── stage 3: apply (only when permitted) ───────────────────────
        if not allow_apply or answer is None:
            return FencedResult(
                outcome=heuristic_outcome,
                overlay_outcome=answer,
                latency_s=client_outcome.latency_s,
                cost_usd=client_outcome.cost_usd,
                total_tokens=client_outcome.total_tokens,
                usage=client_outcome.usage,
                reached_socket=client_outcome.reached_socket,
                candidate_ids=candidate_ids,
                verdict=verdict,
                selection_overlap_ratio=overlap,
            )

        if record.monotonicity is Monotonicity.ADVISORY_ONLY:
            # The overlay may only ANNOTATE — the heuristic stands.
            return FencedResult(
                outcome=heuristic_outcome,
                overlay_outcome=answer,
                latency_s=client_outcome.latency_s,
                cost_usd=client_outcome.cost_usd,
                total_tokens=client_outcome.total_tokens,
                usage=client_outcome.usage,
                reached_socket=client_outcome.reached_socket,
                candidate_ids=candidate_ids,
                verdict=verdict,
                selection_overlap_ratio=overlap,
            )

        try:
            effective = record.apply(heuristic_outcome, answer)
        except Exception as exc:  # noqa: BLE001 — the fence owns this
            logger.warning(
                "decision seam %s apply failed (fail-open): %s: %s",
                seam_id,
                type(exc).__name__,
                exc,
            )
            return FencedResult(
                outcome=heuristic_outcome,
                overlay_outcome=answer,
                failure_class=FailureClass.SEAM_FAULT,
                latency_s=client_outcome.latency_s,
                cost_usd=client_outcome.cost_usd,
                total_tokens=client_outcome.total_tokens,
                usage=client_outcome.usage,
                reached_socket=client_outcome.reached_socket,
                candidate_ids=candidate_ids,
                verdict=verdict,
                selection_overlap_ratio=overlap,
            )

        violation = self._monotonicity_violation(
            record, heuristic_outcome, effective, candidate_ids
        )
        if violation is not None:
            # Apply-time refusal ⇒ fail open to the heuristic.
            logger.warning(
                "decision seam %s apply refused (monotonicity: %s) — failing open",
                seam_id,
                violation,
            )
            return FencedResult(
                outcome=heuristic_outcome,
                overlay_outcome=answer,
                refused=True,
                latency_s=client_outcome.latency_s,
                cost_usd=client_outcome.cost_usd,
                total_tokens=client_outcome.total_tokens,
                usage=client_outcome.usage,
                reached_socket=client_outcome.reached_socket,
                candidate_ids=candidate_ids,
                verdict=verdict,
                selection_overlap_ratio=overlap,
            )

        return FencedResult(
            outcome=effective,
            overlay_outcome=answer,
            applied=True,
            latency_s=client_outcome.latency_s,
            cost_usd=client_outcome.cost_usd,
            total_tokens=client_outcome.total_tokens,
            usage=client_outcome.usage,
            reached_socket=client_outcome.reached_socket,
            candidate_ids=candidate_ids,
            verdict=verdict,
            selection_overlap_ratio=overlap,
        )

    # ── monotonicity (direction-scoped, FLAG5) ──────────────────────────

    def _monotonicity_violation(
        self,
        record: SeamRecord,
        heuristic_outcome: Any,
        effective: Any,
        candidate_ids: Sequence[str],
    ) -> str | None:
        """Check the effective outcome against the declared direction.

        Returns ``None`` when the outcome is allowed, else a short
        reason. The rule is DIRECTION-SCOPED:

        * add-only (``TOWARD_COMPLETION`` / ``TOWARD_TIGHTENING``) — the
          effective set MUST BE A SUPERSET of the heuristic's; a subset
          is REFUSED;
        * ``WITHIN_SET`` — the effective set must stay INSIDE the
          candidate set and must NEVER be empty;
        * ``ADVISORY_ONLY`` — no check (the heuristic stands).
        """
        if record.monotonicity in _ADD_ONLY_DIRECTIONS:
            heuristic_items = _as_item_set(heuristic_outcome)
            effective_items = _as_item_set(effective)
            if heuristic_items is None or effective_items is None:
                return "the add-only superset rule is not checkable"
            if not heuristic_items.issubset(effective_items):
                return "the effective outcome is not a superset of the heuristic's"
            return None

        if record.monotonicity is Monotonicity.WITHIN_SET:
            effective_items = _as_item_set(effective)
            if effective_items is None:
                return "the WITHIN_SET rule is not checkable"
            if not effective_items:
                return "the effective outcome is empty — the overlay may never empty it"
            allowed = set(candidate_ids)
            if allowed and not effective_items.issubset(allowed):
                return "the effective outcome left the candidate set"
            return None

        return None

    # ── the shadow A/B verdict ──────────────────────────────────────────

    def _verdict(
        self,
        record: SeamRecord,
        heuristic_outcome: Any,
        answer: DecisionAnswer | None,
        candidate_ids: Sequence[str],
    ) -> str:
        """The A/B verdict — ``agree`` / ``disagree`` / ``no_overlay``."""
        if answer is None:
            return VERDICT_NO_OVERLAY
        if record.compare is not None:
            try:
                return VERDICT_AGREE if record.compare(heuristic_outcome, answer) else VERDICT_DISAGREE
            except Exception as exc:  # noqa: BLE001 — observability never breaks a caller
                logger.debug(
                    "decision seam %s comparator failed (non-fatal): %s: %s",
                    record.seam_id,
                    type(exc).__name__,
                    exc,
                )
                return VERDICT_NO_OVERLAY
        heuristic_ids = _as_id_set(heuristic_outcome)
        overlay_ids = _overlay_id_set(answer, candidate_ids)
        if heuristic_ids is not None and overlay_ids is not None:
            return VERDICT_AGREE if heuristic_ids == overlay_ids else VERDICT_DISAGREE
        return VERDICT_DISAGREE


# ── module helpers ───────────────────────────────────────────────────────


def _as_item_set(value: Any) -> set[Any] | None:
    """Read a collection outcome as a set; ``None`` when not checkable."""
    if isinstance(value, (list, tuple, set, frozenset)):
        try:
            return set(value)
        except TypeError:
            return None
    return None


def _as_id_set(value: Any) -> set[str] | None:
    """Read a collection of id strings; ``None`` when not comparable."""
    if not isinstance(value, (list, tuple, set, frozenset)):
        return None
    ids: set[str] = set()
    for item in value:
        if not isinstance(item, str):
            return None
        ids.add(item)
    return ids


def _overlay_id_set(
    answer: DecisionAnswer | None, candidate_ids: Sequence[str]
) -> set[str] | None:
    """The overlay's ranked ids, intersected with the candidate set.

    The intersection is REQUIRED (``thin-client.md`` §8.2): any option
    whose value is not a candidate is dropped, so the overlay can never
    introduce an id that was not a candidate.
    """
    if answer is None:
        return None
    ranked = answer.ranked_values()
    if not ranked:
        picked = answer.choice
        if not picked:
            return None
        ranked = (picked,)
    allowed = set(candidate_ids)
    if allowed:
        return {value for value in ranked if value in allowed}
    return set(ranked)


def _overlap_ratio(
    heuristic_outcome: Any,
    answer: DecisionAnswer | None,
    candidate_ids: Sequence[str],
) -> float | None:
    """The S1 set-level overlap (Jaccard), when computable."""
    heuristic_ids = _as_id_set(heuristic_outcome)
    overlay_ids = _overlay_id_set(answer, candidate_ids)
    if heuristic_ids is None or overlay_ids is None:
        return None
    union = heuristic_ids | overlay_ids
    if not union:
        return None
    return len(heuristic_ids & overlay_ids) / len(union)
