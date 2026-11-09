"""A3 — the GATE-4 CONSUMER pin (registered pack
``skill_capture_killswitch_unit_test``; the A3 consumption arm).

WHAT THIS PINS, AND WHY IT IS NOT THE PREDICATE PIN.  The predicate pin
(``tests/unit/test_skill_capture_attribution_seam.py``) proves the
repository method has the right shape. It does NOT prove that the CAPTURE
GATE actually CONSUMES it. Both defects are real and independent:

* the predicate could be right while Gate 4 still calls
  ``has_applied_for_instance`` (a repoint that was never wired); and
* a repoint could be wired while the predicate is wrong.

This module is the CONSUMPTION arm: it drives
``SkillMetricsService._check_capture_eligibility`` (Gate 4's real call
site) and asserts

1. ``has_genuinely_evolved_for_instance`` is the collaborator that gets
   consulted;
2. ``has_applied_for_instance`` is NEVER consulted (the feedback-only
   predicate must not run at the gate);
3. a genuinely-evolved instance REFUSES capture;
4. a not-evolved instance PROCEEDS to Gate 5 and dispatches;
5. the population caveat still holds — an instance with no injected
   skills never reaches Gate 4 at all.

Mutation leg (pack ``M-A3d-consumer``): plant the repoint BACK to
``has_applied_for_instance`` at Gate 4 ⇒ (2) and (1) go RED. Restore
sha-identically ⇒ GREEN.
"""

from __future__ import annotations

import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from daemon.services.skill_metrics_service import (
    INJECTED_SKILLS_METADATA_KEY,
    SkillMetricsService,
)


class _Config:
    """The two capture-threshold attributes Gate 5 reads."""

    capture_min_iterations = 5
    capture_min_duration_seconds = 60


def _service(*, evolved: bool, injected: bool = True):
    """A service whose usage repo answers ``evolved`` and records reads."""
    usage = MagicMock()
    usage.has_genuinely_evolved_for_instance = MagicMock(return_value=evolved)
    usage.has_applied_for_instance = MagicMock(
        side_effect=AssertionError(
            "Gate 4 must NOT consult the feedback-only predicate"
        )
    )

    skill = MagicMock()
    evolution = MagicMock()
    evolution.check_and_capture = AsyncMock(
        return_value={
            "instance_id": "inst-g4",
            "agent_id": "agent-x",
            "project_id": "p-g4",
            "task_message": "msg",
            "iterations": 10,
            "duration_seconds": 120,
            "task_succeeded": True,
        }
    )
    service = SkillMetricsService(
        usage_repo=usage,
        skill_repo=skill,
        trigger_repo=MagicMock(),
        ab_test_repo=MagicMock(),
        config=_Config(),
        instance_repo=SimpleNamespace(
            get=MagicMock(
                return_value=SimpleNamespace(
                    instance_id="inst-g4",
                    instance_metadata=(
                        {INJECTED_SKILLS_METADATA_KEY: ["skill-1"]}
                        if injected
                        else {}
                    ),
                )
            )
        ),
        evolution_service=evolution,
        agent_id_resolver=lambda agent_id, instance_id=None: SimpleNamespace(
            skill_injection=True
        ),
    )
    dispatcher = MagicMock()
    dispatcher.enqueue_capture = AsyncMock(return_value="job-g4")
    service.set_job_dispatcher(dispatcher)
    return service, usage, evolution


@pytest.fixture(autouse=True)
def _lane_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """Gate 0 (the kill-switch) ON — bypassed so Gate 4 is reachable.

    Simulated by env, never by editing a switch file (house rule).
    """
    monkeypatch.setenv("ENSEMBLE_SKILL_CAPTURE_ENABLED", "1")


async def _call_gate(service: SkillMetricsService, instance_id: str = "inst-g4"):
    return await service._check_capture_eligibility(
        instance_id=instance_id,
        agent_id="agent-x",
        project_id="p-g4",
        task_message="msg",
        task_succeeded=True,
        iterations=10,
        duration_seconds=120,
    )


class TestGateFourConsumesTheOutcomeFieldsPredicate:
    """The consumption assertions — all four go RED on a repoint-back."""

    @pytest.mark.asyncio
    async def test_the_gate_consults_the_genuinely_evolved_predicate(self) -> None:
        service, usage, _ = _service(evolved=True)
        await _call_gate(service)
        usage.has_genuinely_evolved_for_instance.assert_called_once_with("inst-g4")

    @pytest.mark.asyncio
    async def test_the_gate_never_consults_the_feedback_only_predicate(self) -> None:
        """THE REPOINT PIN — ``M-A3d-consumer`` reds exactly here.

        ``has_applied_for_instance`` is configured to RAISE if touched, so
        a repoint-back fails loudly rather than silently changing the
        verdict.
        """
        service, usage, _ = _service(evolved=True)
        await _call_gate(service)
        usage.has_applied_for_instance.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_genuinely_evolved_instance_refuses_capture(self) -> None:
        service, _, evolution = _service(evolved=True)
        assert await _call_gate(service) is None
        evolution.check_and_capture.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_not_evolved_instance_proceeds_to_capture(self) -> None:
        service, _, evolution = _service(evolved=False)
        result = await _call_gate(service)
        assert result is not None
        evolution.check_and_capture.assert_awaited_once()


class TestTheResultIsTheSameVerdictForTheWindowRow:
    """The census window's shape decides the same way the predicate does.

    ``selected AND task_succeeded AND feedback_applied IS NULL`` is NOT
    genuinely evolved ⇒ the gate PROCEEDS (it is a capture candidate).
    This is the behavioural counterpart of the predicate pin, driven
    through the real Gate-4 call site.
    """

    @pytest.mark.asyncio
    async def test_a_window_shaped_instance_is_capturable(self) -> None:
        # The fake repo models the predicate's verdict for that shape.
        service, usage, evolution = _service(evolved=False)
        result = await _call_gate(service)
        assert result is not None
        usage.has_genuinely_evolved_for_instance.assert_called_once()
        evolution.check_and_capture.assert_awaited_once()


class TestThePopulationCaveatStillHolds:
    """The early return precedes Gate 4 — the metric's population matches."""

    @pytest.mark.asyncio
    async def test_no_injected_skills_never_reaches_gate_four(self) -> None:
        service, usage, evolution = _service(evolved=False, injected=False)
        # Drive the PUBLIC entry so the early return is exercised for real.
        inserted = await service.record_task_completion(
            instance_id="inst-g4",
            agent_id="agent-x",
            project_id="p-g4",
            task_succeeded=True,
            iterations=10,
            duration_seconds=120,
            task_message="msg",
        )
        assert inserted == 0, "no injected skills ⇒ zero usage rows recorded"
        usage.has_genuinely_evolved_for_instance.assert_not_called()
        evolution.check_and_capture.assert_not_called()

    def test_the_early_return_still_precedes_the_capture_call(self) -> None:
        """The caveat's premise is REAL code, not prose."""
        source = inspect.getsource(
            SkillMetricsService.record_task_completion
        )
        assert "if not injected_ids:" in source
        assert source.index("if not injected_ids:") < source.index(
            "_check_capture_eligibility"
        )


class TestTheGateFourBodyNamesThePredicate:
    """A source-level backstop so a repoint cannot hide behind a mock."""

    @staticmethod
    def _usage_repo_members(func) -> set[str]:
        """The ``self.usage_repo.<member>`` references in the function (AST).

        Comments and docstrings legitimately NAME the old predicate (to say
        it is NOT the gate), so a text grep would be wrong. The gate passes
        the bound method into ``asyncio.to_thread`` rather than calling it
        inline, so this collects ATTRIBUTE references, not Calls.
        """
        import ast
        import textwrap

        tree = ast.parse(textwrap.dedent(inspect.getsource(func)))
        referenced: set[str] = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute):
                continue
            if (
                isinstance(node.value, ast.Attribute)
                and node.value.attr == "usage_repo"
                and isinstance(node.value.value, ast.Name)
                and node.value.value.id == "self"
            ):
                referenced.add(node.attr)
        return referenced

    def test_the_gate_calls_the_new_predicate_and_only_that_one(self) -> None:
        referenced = self._usage_repo_members(
            SkillMetricsService._check_capture_eligibility
        )
        assert "has_genuinely_evolved_for_instance" in referenced
        assert "has_applied_for_instance" not in referenced, (
            "Gate 4 must not consult the feedback-only predicate; the new "
            "outcome-fields predicate is the only gate input"
        )
