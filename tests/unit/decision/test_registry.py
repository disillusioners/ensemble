"""The seam registry — exclusion, monotonicity, the D3 gate, the fence.

Covers AC-5 (the apply contract), AC-6/T-9 (direction-scoped
monotonicity), AC-7 (the D3 gate), AC-10/T-10 (the EX-1…EX-5
registration refusal) and AC-16 (the whole-consult fail-open fence).
"""

from __future__ import annotations

from typing import Any, Mapping

import httpx
import pytest

from daemon.services.decision.client import DecisionClient
from daemon.services.decision.config import DecisionConfig
from daemon.services.decision.registry import (
    DENIED_SEAM_ROLES,
    RegistrationRefused,
    ReviewRecord,
    SeamRecord,
    SeamRegistry,
    StageTransitionRefused,
)
from daemon.services.decision.types import (
    DecisionRequest,
    DecisionStage,
    FailureClass,
    Monotonicity,
    QuestionKind,
)
from tests.unit.decision.helpers import (
    choice_answer_payload,
    choice_question,
    json_response,
    make_config,
    predicate_question,
    smart_handler,
)


def _registry(
    handler=None, *, config: DecisionConfig | None = None
) -> tuple[SeamRegistry, list[httpx.Request]]:
    """A registry over a mock transport; the call log is returned."""
    calls: list[httpx.Request] = []

    def _default(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return smart_handler(request)

    transport = httpx.MockTransport(handler or _default)
    client = DecisionClient(
        config or make_config(), transport_factory=lambda: transport
    )
    return SeamRegistry(client=client, config=config or make_config()), calls


def _completion_record(apply_impl) -> SeamRecord:
    """A TOWARD_COMPLETION seam over a predicate question."""
    question = predicate_question()

    def _build(context: Mapping[str, Any]) -> DecisionRequest:
        return DecisionRequest(
            input=str(context.get("input", "is it done")),
            questions=(question,),
        )

    return SeamRecord(
        seam_id="attestation.rescue",
        role="attestation_rescue",
        question_kind=QuestionKind.PREDICATE,
        build_input=_build,
        apply=apply_impl,
        monotonicity=Monotonicity.TOWARD_COMPLETION,
        outcome_kind="set",
    )


# ── EX-1…EX-5 (T-10) ────────────────────────────────────────────────────


@pytest.mark.parametrize("role", sorted(DENIED_SEAM_ROLES))
async def test_an_excluded_role_is_refused_before_any_call_path(role: str) -> None:
    """T-10 — a registration naming an EX-1…EX-5 role is REFUSED.

    The registry holds NO record for the seam, and no client call was
    attempted — the refusal happens BEFORE any call path exists.
    """
    registry, calls = _registry()
    record = SeamRecord(
        seam_id=f"excluded.{role}",
        role=role,
        question_kind=QuestionKind.CHOICE,
        build_input=lambda context: DecisionRequest(
            input="x", questions=(choice_question(),)
        ),
        apply=lambda heuristic, overlay: heuristic,
        monotonicity=Monotonicity.WITHIN_SET,
        outcome_kind="ordered_set",
    )
    with pytest.raises(RegistrationRefused) as excinfo:
        registry.register(record)
    assert "exclusion list" in str(excinfo.value)
    assert registry.get(record.seam_id) is None
    assert record.seam_id not in registry.records()
    assert calls == []


async def test_the_registry_carries_the_five_ex_roles() -> None:
    """The registry's deny-list is the doctrine's five entries."""
    registry, _calls = _registry()
    assert registry.denied_seam_roles == DENIED_SEAM_ROLES
    assert len(registry.denied_seam_roles) == 5
    assert "retry_classification" in registry.denied_seam_roles
    assert "symptom_ladder_rungs" in registry.denied_seam_roles


# ── registration-time refusals (AC-5) ────────────────────────────────────


async def test_a_missing_monotonicity_is_refused() -> None:
    """§2.2 — a registration that omits a direction is refused."""
    registry, _calls = _registry()
    record = SeamRecord(
        seam_id="bad.monotonicity",
        role="whatever",
        question_kind=QuestionKind.CHOICE,
        build_input=lambda context: DecisionRequest(
            input="x", questions=(choice_question(),)
        ),
        apply=lambda heuristic, overlay: heuristic,
        monotonicity=None,  # type: ignore[arg-type]
        outcome_kind="ordered_set",
    )
    with pytest.raises(RegistrationRefused):
        registry.register(record)
    assert registry.get("bad.monotonicity") is None


async def test_an_incoherent_direction_is_refused() -> None:
    """A ``choice`` seam may not declare an add-only direction.

    The kind → direction table is the design's own typed mapping
    (``thin-client.md`` §8.1) generalised: S1 is ``choice`` +
    ``WITHIN_SET``, S3/S4 are ``predicate`` + an add-only direction.
    """
    registry, _calls = _registry()
    record = SeamRecord(
        seam_id="incoherent.direction",
        role="whatever",
        question_kind=QuestionKind.CHOICE,
        build_input=lambda context: DecisionRequest(
            input="x", questions=(choice_question(),)
        ),
        apply=lambda heuristic, overlay: heuristic,
        monotonicity=Monotonicity.TOWARD_COMPLETION,
        outcome_kind="set",
    )
    with pytest.raises(RegistrationRefused) as excinfo:
        registry.register(record)
    assert "incoherent direction" in str(excinfo.value)
    assert registry.get("incoherent.direction") is None


async def test_a_none_monotonicity_is_refused() -> None:
    """``NONE`` means an excluded seam — it is never registrable."""
    registry, _calls = _registry()
    record = SeamRecord(
        seam_id="none.direction",
        role="whatever",
        question_kind=QuestionKind.CHOICE,
        build_input=lambda context: DecisionRequest(
            input="x", questions=(choice_question(),)
        ),
        apply=lambda heuristic, overlay: heuristic,
        monotonicity=Monotonicity.NONE,
        outcome_kind="ordered_set",
    )
    with pytest.raises(RegistrationRefused):
        registry.register(record)


# ── the apply contract (AC-5) ───────────────────────────────────────────


async def test_apply_receives_both_the_heuristic_and_the_overlay() -> None:
    """D1 + D2 made structural — the heuristic is ALWAYS an argument."""
    seen: list[tuple[Any, Any]] = []

    def _apply(heuristic: Any, overlay: Any) -> Any:
        seen.append((heuristic, overlay))
        return heuristic

    registry, _calls = _registry()
    record = SeamRecord(
        seam_id="contract.apply",
        role="whatever",
        question_kind=QuestionKind.CHOICE,
        build_input=lambda context: DecisionRequest(
            input="x", questions=(choice_question(),)
        ),
        apply=_apply,
        monotonicity=Monotonicity.WITHIN_SET,
        outcome_kind="ordered_set",
    )
    registry.register(record)
    result = await registry.execute(
        "contract.apply",
        heuristic_outcome=["id-a", "id-b"],
        context={"input": "x"},
        allow_apply=True,
    )
    assert len(seen) == 1
    heuristic, overlay = seen[0]
    assert heuristic == ["id-a", "id-b"]
    assert overlay is not None, "the overlay outcome is the typed answer"
    assert overlay.type == "choice"


async def test_a_none_overlay_yields_the_heuristic_unchanged() -> None:
    """D2 made structural — a failed consult yields the heuristic."""
    registry, _calls = _registry(
        lambda request: json_response({"model": "decision", "answers": []})
    )
    record = SeamRecord(
        seam_id="contract.none",
        role="whatever",
        question_kind=QuestionKind.CHOICE,
        build_input=lambda context: DecisionRequest(
            input="x", questions=(choice_question(),)
        ),
        apply=lambda heuristic, overlay: heuristic,
        monotonicity=Monotonicity.WITHIN_SET,
        outcome_kind="ordered_set",
    )
    registry.register(record)
    result = await registry.execute(
        "contract.none",
        heuristic_outcome=["id-a"],
        context={"input": "x"},
        allow_apply=True,
    )
    assert result.outcome == ["id-a"]
    assert result.failure_class is FailureClass.MALFORMED_BODY


# ── direction-scoped monotonicity (AC-6 / T-9) ──────────────────────────


async def test_a_toward_completion_subset_is_refused_at_apply_time() -> None:
    """T-9 — an add-only apply that REMOVES a completion is refused."""
    registry, _calls = _registry()
    record = _completion_record(
        lambda heuristic, overlay: ["only-the-new-one"]
    )
    registry.register(record)
    result = await registry.execute(
        "attestation.rescue",
        heuristic_outcome=["judge-completion"],
        context={"input": "is it done"},
        allow_apply=True,
    )
    assert result.refused is True
    assert result.outcome == ["judge-completion"], "a refusal fails open to the heuristic"
    assert result.applied is False


async def test_a_toward_completion_superset_is_accepted() -> None:
    """T-9 (positive) — a strict superset is accepted."""
    registry, _calls = _registry()
    record = _completion_record(
        lambda heuristic, overlay: list(heuristic) + ["rescued-completion"]
    )
    registry.register(record)
    result = await registry.execute(
        "attestation.rescue",
        heuristic_outcome=["judge-completion"],
        context={"input": "is it done"},
        allow_apply=True,
    )
    assert result.applied is True
    assert result.refused is False
    assert set(result.outcome) == {"judge-completion", "rescued-completion"}


async def test_a_within_set_apply_that_empties_the_result_is_refused() -> None:
    """T-9 — ``WITHIN_SET`` may NEVER empty the result."""
    registry, _calls = _registry()
    record = SeamRecord(
        seam_id="snapshot.l3.selection",
        role="snapshot_selection",
        question_kind=QuestionKind.CHOICE,
        build_input=lambda context: DecisionRequest(
            input="x", questions=(choice_question(),)
        ),
        apply=lambda heuristic, overlay: [],
        monotonicity=Monotonicity.WITHIN_SET,
        outcome_kind="ordered_set",
    )
    registry.register(record)
    result = await registry.execute(
        "snapshot.l3.selection",
        heuristic_outcome=["id-a"],
        context={"input": "x"},
        allow_apply=True,
    )
    assert result.refused is True
    assert result.outcome == ["id-a"]


async def test_a_within_set_apply_that_leaves_the_candidate_set_is_refused() -> None:
    """``WITHIN_SET`` may not EXTEND the candidate set either."""
    registry, _calls = _registry()
    record = SeamRecord(
        seam_id="snapshot.l3.selection",
        role="snapshot_selection",
        question_kind=QuestionKind.CHOICE,
        build_input=lambda context: DecisionRequest(
            input="x", questions=(choice_question(),)
        ),
        apply=lambda heuristic, overlay: ["not-a-candidate"],
        monotonicity=Monotonicity.WITHIN_SET,
        outcome_kind="ordered_set",
    )
    registry.register(record)
    result = await registry.execute(
        "snapshot.l3.selection",
        heuristic_outcome=["id-a"],
        context={"input": "x"},
        allow_apply=True,
    )
    assert result.refused is True
    assert result.outcome == ["id-a"]


async def test_a_within_set_reorder_within_the_set_is_accepted() -> None:
    """T-9 (positive) — a reorder WITHIN the set is accepted.

    This is the proof that superset-only is NOT a global rule: a
    ``WITHIN_SET`` apply may return FEWER members than the heuristic
    (S1 picks a top-K) — what it may not do is leave the candidate set
    or empty the result.
    """
    registry, _calls = _registry()
    record = SeamRecord(
        seam_id="snapshot.l3.selection",
        role="snapshot_selection",
        question_kind=QuestionKind.CHOICE,
        build_input=lambda context: DecisionRequest(
            input="x", questions=(choice_question(),)
        ),
        apply=lambda heuristic, overlay: ["id-b"],
        monotonicity=Monotonicity.WITHIN_SET,
        outcome_kind="ordered_set",
    )
    registry.register(record)
    result = await registry.execute(
        "snapshot.l3.selection",
        heuristic_outcome=["id-a", "id-b", "id-c"],
        context={"input": "x"},
        allow_apply=True,
    )
    assert result.applied is True
    assert result.refused is False
    assert result.outcome == ["id-b"]


async def test_an_advisory_only_apply_never_moves_the_outcome() -> None:
    """``ADVISORY_ONLY`` — the heuristic decision stands regardless."""
    registry, _calls = _registry()
    record = SeamRecord(
        seam_id="busy.slow.arbitration",
        role="busy_slow",
        question_kind=QuestionKind.SCORE,
        build_input=lambda context: DecisionRequest(
            input="x", questions=(choice_question(),)
        ),
        apply=lambda heuristic, overlay: ["escalate"],
        monotonicity=Monotonicity.ADVISORY_ONLY,
        outcome_kind="scalar",
    )
    registry.register(record)
    result = await registry.execute(
        "busy.slow.arbitration",
        heuristic_outcome=["nudge"],
        context={"input": "x"},
        allow_apply=True,
    )
    assert result.outcome == ["nudge"]
    assert result.applied is False


# ── the D3 gate (AC-7) ───────────────────────────────────────────────────


async def test_the_shadow_to_deciding_transition_is_refused_without_ratification() -> None:
    """AC-7 — the registry REFUSES the transition absent ratification."""
    registry, _calls = _registry()
    record = SeamRecord(
        seam_id="snapshot.l3.selection",
        role="snapshot_selection",
        question_kind=QuestionKind.CHOICE,
        build_input=lambda context: DecisionRequest(
            input="x", questions=(choice_question(),)
        ),
        apply=lambda heuristic, overlay: heuristic,
        monotonicity=Monotonicity.WITHIN_SET,
        outcome_kind="ordered_set",
    )
    registry.register(record)
    with pytest.raises(StageTransitionRefused):
        registry.transition_stage(
            "snapshot.l3.selection", DecisionStage.DECIDING, review=None
        )
    with pytest.raises(StageTransitionRefused):
        registry.transition_stage(
            "snapshot.l3.selection",
            DecisionStage.DECIDING,
            review=ReviewRecord(reviewer="reviewer", ratified_by=""),
        )
    assert registry.stage("snapshot.l3.selection") is DecisionStage.SHADOW
    assert registry.is_deciding_permitted("snapshot.l3.selection") is False


async def test_a_ratified_transition_is_accepted() -> None:
    """AC-7 — an explicit review + ratification opens the transition."""
    registry, _calls = _registry()
    record = SeamRecord(
        seam_id="snapshot.l3.selection",
        role="snapshot_selection",
        question_kind=QuestionKind.CHOICE,
        build_input=lambda context: DecisionRequest(
            input="x", questions=(choice_question(),)
        ),
        apply=lambda heuristic, overlay: heuristic,
        monotonicity=Monotonicity.WITHIN_SET,
        outcome_kind="ordered_set",
    )
    registry.register(record)
    stage = registry.transition_stage(
        "snapshot.l3.selection",
        DecisionStage.DECIDING,
        review=ReviewRecord(reviewer="council", ratified_by="the Emperor"),
    )
    assert stage is DecisionStage.DECIDING
    assert registry.is_deciding_permitted("snapshot.l3.selection") is True


# ── the fail-open fence (AC-16) ─────────────────────────────────────────


async def test_an_exception_in_build_input_fails_open() -> None:
    """AC-16 — the fence covers ``build_input`` too."""

    def _boom(context: Mapping[str, Any]) -> DecisionRequest:
        raise RuntimeError("cannot build")

    registry, calls = _registry()
    record = SeamRecord(
        seam_id="fence.build",
        role="whatever",
        question_kind=QuestionKind.CHOICE,
        build_input=_boom,
        apply=lambda heuristic, overlay: heuristic,
        monotonicity=Monotonicity.WITHIN_SET,
        outcome_kind="ordered_set",
    )
    registry.register(record)
    result = await registry.execute(
        "fence.build",
        heuristic_outcome=["heuristic"],
        context={"input": "x"},
    )
    assert result.outcome == ["heuristic"]
    assert result.failure_class is FailureClass.SEAM_FAULT
    assert calls == [], "a build fault must not reach the socket"


async def test_an_exception_in_apply_fails_open() -> None:
    """AC-16 — the fence covers ``apply`` too."""

    def _boom(heuristic: Any, overlay: Any) -> Any:
        raise RuntimeError("cannot apply")

    registry, _calls = _registry()
    record = SeamRecord(
        seam_id="fence.apply",
        role="whatever",
        question_kind=QuestionKind.CHOICE,
        build_input=lambda context: DecisionRequest(
            input="x", questions=(choice_question(),)
        ),
        apply=_boom,
        monotonicity=Monotonicity.WITHIN_SET,
        outcome_kind="ordered_set",
    )
    registry.register(record)
    result = await registry.execute(
        "fence.apply",
        heuristic_outcome=["heuristic"],
        context={"input": "x"},
        allow_apply=True,
    )
    assert result.outcome == ["heuristic"]
    assert result.failure_class is FailureClass.SEAM_FAULT


async def test_the_registry_owns_the_model_id() -> None:
    """The seam never chooses the model — the registry overwrites it."""
    import json

    bodies: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content.decode("utf-8")))
        return json_response(choice_answer_payload())

    config = make_config(model="decision")
    registry, _calls = _registry(handler, config=config)
    record = SeamRecord(
        seam_id="model.owner",
        role="whatever",
        question_kind=QuestionKind.CHOICE,
        build_input=lambda context: DecisionRequest(
            input="x",
            questions=(choice_question(),),
            model="a-seam-chosen-model",
        ),
        apply=lambda heuristic, overlay: heuristic,
        monotonicity=Monotonicity.WITHIN_SET,
        outcome_kind="ordered_set",
    )
    registry.register(record)
    await registry.execute(
        "model.owner", heuristic_outcome=["x"], context={"input": "x"}
    )
    assert bodies[0]["model"] == "decision"


async def test_unregistering_removes_the_call_path() -> None:
    """§4.1 — unregistration is the durable half of a rollback."""
    registry, calls = _registry()
    record = SeamRecord(
        seam_id="gone.after.unregister",
        role="whatever",
        question_kind=QuestionKind.CHOICE,
        build_input=lambda context: DecisionRequest(
            input="x", questions=(choice_question(),)
        ),
        apply=lambda heuristic, overlay: heuristic,
        monotonicity=Monotonicity.WITHIN_SET,
        outcome_kind="ordered_set",
    )
    registry.register(record)
    assert registry.unregister("gone.after.unregister") is True
    assert registry.get("gone.after.unregister") is None
    result = await registry.execute(
        "gone.after.unregister", heuristic_outcome=["x"], context={"input": "x"}
    )
    assert result.outcome == ["x"]
    assert calls == []
