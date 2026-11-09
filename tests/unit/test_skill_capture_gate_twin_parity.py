"""I1 — the M-A3d TWIN-PARITY pin (registered pack
``skill_capture_killswitch_unit_test``; the parity arm).

WHAT THIS PINS, AND WHY IT IS A THIRD PIN RATHER THAN A FOURTH LEG.  Two
pins already exist and they answer two DIFFERENT questions:

* ``tests/unit/test_skill_capture_attribution_seam.py`` — is the
  PREDICATE right? (repository-level, three conjuncts);
* ``tests/unit/test_skill_gate4_attribution_consumer.py`` — does the
  METRICS-SERVICE gate CONSUME it? (one call site).

Neither answers the question this module answers: **do BOTH gate call
sites dispatch to the SAME predicate?** The twin is a second, independent
copy of the gate in ``SkillEvolutionService.check_and_capture`` — a
service-seam re-check that exists to close the TOCTOU window. If only one
side is repointed, the two halves of the gate disagree, and a capture can
be suppressed on one seam while admitted on the other. That is a real
divergence class, not a restatement of the consumer pin.

THE ASSERTIONS ARE RESOLUTION ASSERTIONS, NOT "BOTH GREEN".

For EACH seam, separately:

1. a BEHAVIOURAL spy: drive the real function and assert
   ``has_genuinely_evolved_for_instance`` was consulted and
   ``has_applied_for_instance`` was NOT (the stub raises if touched, so a
   silent repoint-back fails loudly rather than changing a verdict);
2. a SOURCE assertion: the seam's AST references
   ``usage_repo.has_genuinely_evolved_for_instance`` and references
   ``has_applied_for_instance`` NOWHERE in an executable position
   (docstrings/comments legitimately name the old predicate);
3. a PARITY assertion: the two seams name the SAME repository method —
   derived from the AST of both, compared as a set, so a future repoint
   of one side only reds here even if each side's own pin is updated.

Mutation leg (pack ``M-A3d-parity``): repoint the METRICS-SERVICE seam
back to ``has_applied_for_instance`` ⇒ this module REDs. The plant is on
OUR OWN file (``daemon/services/skill_metrics_service.py``) on purpose —
planting into ``daemon/services/skill_evolution_service.py`` would risk
reverting another lane's concurrent commit on restore.
"""

from __future__ import annotations

import ast
import inspect
import textwrap
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from daemon.services.skill_evolution_service import SkillEvolutionService
from daemon.services.skill_metrics_service import (
    INJECTED_SKILLS_METADATA_KEY,
    SkillMetricsService,
)

#: The two seams under test — (label, class, method name).
SEAMS = (
    ("metrics-service Gate 4", SkillMetricsService, "_check_capture_eligibility"),
    ("evolution-service twin", SkillEvolutionService, "check_and_capture"),
)

#: The A3 predicate and the retired feedback-only one.
A3_PREDICATE = "has_genuinely_evolved_for_instance"
RETIRED_PREDICATE = "has_applied_for_instance"


# ─────────────────────────────────────────────────────────────────────────────
# the AST helpers (executable references only — comments/docstrings excluded)
# ─────────────────────────────────────────────────────────────────────────────


def _usage_repo_members(func) -> set[str]:
    """``<repo>.usage_repo.<member>`` / ``self._usage_repo.<member>`` references.

    Comments and docstrings legitimately NAME the retired predicate (to
    say it is NOT the gate), so a text grep would produce false positives.
    This walks the AST and collects only ATTRIBUTE references — the gate
    passes the bound method into ``asyncio.to_thread`` rather than calling
    it inline, so a Call-only walk would miss it.
    """
    tree = ast.parse(textwrap.dedent(inspect.getsource(func)))
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute):
            continue
        base = node.value
        if not isinstance(base, ast.Attribute):
            continue
        if base.attr not in {"usage_repo", "_usage_repo"}:
            continue
        found.add(node.attr)
    return found


def _seam_predicates(cls, method_name: str) -> set[str]:
    """The capture-gate predicate(s) the seam's executable body references."""
    members = _usage_repo_members(getattr(cls, method_name))
    return {m for m in members if m in {A3_PREDICATE, RETIRED_PREDICATE}}


# ─────────────────────────────────────────────────────────────────────────────
# behavioural drives — one per seam, both against a spy repo
# ─────────────────────────────────────────────────────────────────────────────


def _metrics_service(*, evolved: bool):
    """A metrics service whose usage repo answers ``evolved`` and RECORDS reads."""
    usage = MagicMock()
    usage.has_genuinely_evolved_for_instance = MagicMock(return_value=evolved)
    usage.has_applied_for_instance = MagicMock(
        side_effect=AssertionError(
            "the Gate-4 seam must NOT consult the feedback-only predicate"
        )
    )
    evolution = MagicMock()
    evolution.check_and_capture = AsyncMock(
        return_value={
            "instance_id": "inst-parity",
            "agent_id": "agent-x",
            "project_id": "p-parity",
            "task_message": "msg",
            "iterations": 10,
            "duration_seconds": 120,
            "task_succeeded": True,
        }
    )
    service = SkillMetricsService(
        usage_repo=usage,
        skill_repo=MagicMock(),
        trigger_repo=MagicMock(),
        ab_test_repo=MagicMock(),
        config=SimpleNamespace(
            capture_min_iterations=5, capture_min_duration_seconds=60
        ),
        instance_repo=SimpleNamespace(
            get=MagicMock(
                return_value=SimpleNamespace(
                    instance_id="inst-parity",
                    instance_metadata={INJECTED_SKILLS_METADATA_KEY: ["skill-1"]},
                )
            )
        ),
        evolution_service=evolution,
        agent_id_resolver=lambda agent_id, instance_id=None: SimpleNamespace(
            skill_injection=True
        ),
    )
    dispatcher = MagicMock()
    dispatcher.enqueue_capture = AsyncMock(return_value="job-parity")
    service.set_job_dispatcher(dispatcher)
    return service, usage


def _evolution_service(*, evolved: bool):
    """An evolution service whose usage repo answers ``evolved`` and RECORDS reads."""
    usage = MagicMock()
    usage.has_genuinely_evolved_for_instance = MagicMock(return_value=evolved)
    usage.has_applied_for_instance = MagicMock(
        side_effect=AssertionError(
            "the evolution-service twin must NOT consult the feedback-only "
            "predicate"
        )
    )
    service = SkillEvolutionService(
        skill_repo=MagicMock(),
        lineage_repo=MagicMock(),
        usage_repo=usage,
        embedding_service=MagicMock(),
        metrics_service=MagicMock(),
        ab_test_repo=MagicMock(),
        config=SimpleNamespace(
            capture_min_iterations=5, capture_min_duration_seconds=60
        ),
        llm_config={},
    )
    return service, usage


@pytest.fixture(autouse=True)
def _lane_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """Gate 0 (the kill-switch) ON — simulated by env, never by editing a switch."""
    monkeypatch.setenv("ENSEMBLE_SKILL_CAPTURE_ENABLED", "1")


async def _drive_metrics_seam(service) -> object:
    return await service._check_capture_eligibility(
        instance_id="inst-parity",
        agent_id="agent-x",
        project_id="p-parity",
        task_message="msg",
        task_succeeded=True,
        iterations=10,
        duration_seconds=120,
    )


async def _drive_evolution_seam(service) -> object:
    return await service.check_and_capture(
        instance_id="inst-parity",
        agent_id="agent-x",
        project_id="p-parity",
        task_message="msg",
        task_succeeded=True,
        iterations=10,
        duration_seconds=120,
    )


# ─────────────────────────────────────────────────────────────────────────────
# 1. the behavioural half — each seam, separately
# ─────────────────────────────────────────────────────────────────────────────


class TestEachSeamConsultsTheA3Predicate:
    @pytest.mark.asyncio
    async def test_metrics_seam_consults_the_a3_predicate(self) -> None:
        service, usage = _metrics_service(evolved=False)
        await _drive_metrics_seam(service)
        usage.has_genuinely_evolved_for_instance.assert_called_once_with(
            "inst-parity"
        )

    @pytest.mark.asyncio
    async def test_metrics_seam_never_consults_the_retired_predicate(self) -> None:
        service, usage = _metrics_service(evolved=False)
        await _drive_metrics_seam(service)
        usage.has_applied_for_instance.assert_not_called()

    @pytest.mark.asyncio
    async def test_evolution_seam_consults_the_a3_predicate(self) -> None:
        service, usage = _evolution_service(evolved=False)
        await _drive_evolution_seam(service)
        usage.has_genuinely_evolved_for_instance.assert_called_once_with(
            "inst-parity"
        )

    @pytest.mark.asyncio
    async def test_evolution_seam_never_consults_the_retired_predicate(self) -> None:
        service, usage = _evolution_service(evolved=False)
        await _drive_evolution_seam(service)
        usage.has_applied_for_instance.assert_not_called()


class TestBothSeamsHonourTheSameVerdict:
    """The parity's OBSERVABLE: the two seams return the same verdict."""

    @pytest.mark.asyncio
    async def test_evolved_suppresses_on_both_seams(self) -> None:
        metrics_service, _ = _metrics_service(evolved=True)
        evolution_service, _ = _evolution_service(evolved=True)
        assert await _drive_metrics_seam(metrics_service) is None
        assert await _drive_evolution_seam(evolution_service) is None

    @pytest.mark.asyncio
    async def test_not_evolved_admits_on_both_seams(self) -> None:
        metrics_service, _ = _metrics_service(evolved=False)
        evolution_service, _ = _evolution_service(evolved=False)
        assert await _drive_metrics_seam(metrics_service) is not None
        assert await _drive_evolution_seam(evolution_service) is not None


# ─────────────────────────────────────────────────────────────────────────────
# 2. the source half — each seam's EXECUTABLE references
# ─────────────────────────────────────────────────────────────────────────────


class TestEachSeamSourceReferencesOnlyTheA3Predicate:
    @pytest.mark.parametrize("label,cls,method", SEAMS)
    def test_the_seam_references_the_a3_predicate(self, label, cls, method) -> None:
        predicates = _seam_predicates(cls, method)
        assert A3_PREDICATE in predicates, (
            f"{label} ({cls.__name__}.{method}) must reference "
            f"{A3_PREDICATE!r} — found {sorted(predicates)}"
        )

    @pytest.mark.parametrize("label,cls,method", SEAMS)
    def test_the_seam_does_not_reference_the_retired_predicate(
        self, label, cls, method
    ) -> None:
        predicates = _seam_predicates(cls, method)
        assert RETIRED_PREDICATE not in predicates, (
            f"{label} ({cls.__name__}.{method}) still references the retired "
            f"feedback-only predicate — the two halves of the gate must agree"
        )


class TestTheTwoSeamsAgree:
    """THE PARITY ASSERTION — derived from both ASTs, compared as sets."""

    def test_both_seams_resolve_to_the_same_predicate(self) -> None:
        resolved = {
            label: _seam_predicates(cls, method) for label, cls, method in SEAMS
        }
        assert all(
            predicates == {A3_PREDICATE} for predicates in resolved.values()
        ), (
            "the twin gate is a SECOND implementation of the same rule; if the "
            f"two seams resolve differently the gate disagrees with itself: {resolved}"
        )
        # …and the sets are EQUAL to each other, not merely each equal to the
        # expected constant (a future third predicate would red here).
        assert len({frozenset(v) for v in resolved.values()}) == 1

    def test_the_two_seams_are_distinct_implementations(self) -> None:
        """A parity pin is only meaningful while there ARE two seams.

        If the twin is ever folded away, this pin must be re-derived rather
        than silently comparing a method with itself.
        """
        assert SkillMetricsService._check_capture_eligibility is not (
            SkillEvolutionService.check_and_capture
        )
        assert SkillMetricsService is not SkillEvolutionService


class TestTheParityIsNotAccidental:
    """Both seams must ALSO pass the same five pre-conditions, or the parity
    is only about one gate step and the verdicts would diverge elsewhere."""

    @pytest.mark.asyncio
    async def test_a_failed_task_admits_nothing_on_either_seam(self) -> None:
        metrics_service, metrics_usage = _metrics_service(evolved=False)
        evolution_service, evolution_usage = _evolution_service(evolved=False)
        assert (
            await metrics_service._check_capture_eligibility(
                instance_id="inst-parity",
                agent_id="agent-x",
                project_id="p-parity",
                task_message="msg",
                task_succeeded=False,
                iterations=10,
                duration_seconds=120,
            )
            is None
        )
        assert (
            await evolution_service.check_and_capture(
                instance_id="inst-parity",
                agent_id="agent-x",
                project_id="p-parity",
                task_message="msg",
                task_succeeded=False,
                iterations=10,
                duration_seconds=120,
            )
            is None
        )
        # Neither seam reached the A/B-state read at all.
        metrics_usage.has_genuinely_evolved_for_instance.assert_not_called()
        evolution_usage.has_genuinely_evolved_for_instance.assert_not_called()
