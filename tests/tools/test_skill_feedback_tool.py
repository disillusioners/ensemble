"""Phase 4 Skill Evolution — skill_feedback tool backend tests.

Covers Task 7b of the Phase 4 plan. The tool's body was a Phase 2
stub that logged to ``logger.info``; Phase 4 replaces it with a
real call into :meth:`SkillMetricsService.record_feedback` plus a
soft-fail return for the "service not wired" path.

The tests are scoped to the closure-injected tool produced by
``create_skill_tools(manager, current_instance_id)`` — the same
shape the agent loop sees at runtime. A small fake instance repo
backs the closure's ``_get_project_id`` and ``_get_agent_id``
helpers; a fake metrics service backs the ``record_feedback`` call
so we don't need a full DB.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from daemon.tools.skill_tools import create_skill_tools


class _FakeInstance:
    def __init__(self, instance_id, project_id, agent_id):
        self.instance_id = instance_id
        self.id = instance_id
        self.project_id = project_id
        self.agent_id = agent_id
        self.instance_metadata = {}


class _FakeInstanceRepo:
    def __init__(self, instance: _FakeInstance | None):
        self._instance = instance

    def get(self, instance_id):
        return self._instance


def _build_manager(
    *,
    metrics_service: Any = None,
    project_id: str | None = "proj-tool",
    agent_id: str | None = "developer",
):
    """Build a MagicMock ``InstanceManager`` shaped like the tool expects.

    Sets ``_instance_repository`` (used by ``_get_project_id`` /
    ``_get_agent_id``) and ``_skill_metrics_service`` (used by
    ``skill_feedback``).
    """
    instance = _FakeInstance(
        instance_id="inst-tool",
        project_id=project_id,
        agent_id=agent_id,
    )
    manager = MagicMock()
    manager._instance_repository = _FakeInstanceRepo(instance)
    manager._skill_metrics_service = metrics_service
    return manager


def _find_tool(tools, name):
    for tool in tools:
        # LangChain ``BaseTool`` exposes ``.name``; the underlying
        # function is ``tool.func`` / ``tool.coroutine``. Fall back
        # to ``__name__`` when neither attribute is set.
        n = getattr(tool, "name", None) or getattr(
            tool, "__name__", None
        )
        if n == name:
            return tool
    raise KeyError(name)


# ─── Tool-level integration ────────────────────────────────────────────────


class TestSkillFeedbackToolHappyPath:
    """``record_feedback`` is called with the closure-injected args."""

    @pytest.mark.asyncio
    async def test_records_feedback_with_project_and_agent(
        self,
    ):
        metrics = MagicMock()
        metrics.record_feedback = AsyncMock(return_value=True)
        manager = _build_manager(metrics_service=metrics)
        tools = create_skill_tools(manager, current_instance_id="inst-tool")
        feedback = _find_tool(tools, "skill_feedback")

        # ``@tool`` wraps the async function in ``BaseTool``; the
        # underlying coroutine is exposed via ``coroutine`` or
        # ``func``. Use ``ainvoke`` for the documented invocation
        # path, falling back to direct coroutine call.
        result = await feedback.ainvoke(
            {
                "skill_id": "abc-12345",
                "applied": True,
                "note": "helpful",
            }
        )

        assert "Feedback recorded" in result
        metrics.record_feedback.assert_awaited_once()
        kwargs = metrics.record_feedback.await_args.kwargs
        assert kwargs["skill_id"] == "abc-12345"
        assert kwargs["instance_id"] == "inst-tool"
        assert kwargs["agent_id"] == "developer"
        assert kwargs["project_id"] == "proj-tool"
        assert kwargs["applied"] is True
        assert kwargs["note"] == "helpful"

    @pytest.mark.asyncio
    async def test_applied_none_is_passed_through(self):
        """``applied=None`` is forwarded as ``None`` to the service."""
        metrics = MagicMock()
        metrics.record_feedback = AsyncMock(return_value=True)
        manager = _build_manager(metrics_service=metrics)
        tools = create_skill_tools(manager, current_instance_id="inst-tool")
        feedback = _find_tool(tools, "skill_feedback")

        await feedback.ainvoke(
            {"skill_id": "abc-12345", "applied": None}
        )

        kwargs = metrics.record_feedback.await_args.kwargs
        assert kwargs["applied"] is None


class TestSkillFeedbackToolSoftFail:
    """The tool never raises; every failure mode returns a string."""

    @pytest.mark.asyncio
    async def test_no_metrics_service_returns_not_available(self):
        """No service wired → "not yet available" string, no raise."""
        manager = _build_manager(metrics_service=None)
        tools = create_skill_tools(manager, current_instance_id="inst-tool")
        feedback = _find_tool(tools, "skill_feedback")

        result = await feedback.ainvoke({"skill_id": "abc-12345"})

        assert "not yet available" in result
        # No instance repo / agent lookup either — soft-fail is
        # exhaustive.
        assert "abc-12345" not in result or "not yet available" in result

    @pytest.mark.asyncio
    async def test_no_usage_record_returns_warning(self):
        """Service returns ``False`` → warning string, no raise."""
        metrics = MagicMock()
        metrics.record_feedback = AsyncMock(return_value=False)
        manager = _build_manager(metrics_service=metrics)
        tools = create_skill_tools(manager, current_instance_id="inst-tool")
        feedback = _find_tool(tools, "skill_feedback")

        result = await feedback.ainvoke({"skill_id": "abc-12345"})

        assert "No usage record found" in result

    @pytest.mark.asyncio
    async def test_service_raises_returns_error_string(self):
        """Service raises → ``ERROR: ...`` string, no raise."""
        metrics = MagicMock()

        async def _raise(**_kwargs):
            raise RuntimeError("simulated DB error")

        metrics.record_feedback = AsyncMock(side_effect=_raise)
        manager = _build_manager(metrics_service=metrics)
        tools = create_skill_tools(manager, current_instance_id="inst-tool")
        feedback = _find_tool(tools, "skill_feedback")

        result = await feedback.ainvoke({"skill_id": "abc-12345"})

        assert result.startswith("ERROR: skill_feedback failed:")
        assert "simulated DB error" in result

    @pytest.mark.asyncio
    async def test_missing_instance_repo_returns_still_records(
        self,
    ):
        """Missing instance repo → ``project_id=None`` / ``agent_id=None``,
        but the call still goes through (closure helpers degrade gracefully)."""
        metrics = MagicMock()
        metrics.record_feedback = AsyncMock(return_value=True)
        manager = MagicMock()
        manager._instance_repository = None
        manager._skill_metrics_service = metrics
        tools = create_skill_tools(manager, current_instance_id="inst-tool")
        feedback = _find_tool(tools, "skill_feedback")

        result = await feedback.ainvoke({"skill_id": "abc-12345"})

        assert "Feedback recorded" in result
        kwargs = metrics.record_feedback.await_args.kwargs
        assert kwargs["project_id"] is None
        assert kwargs["agent_id"] is None


class TestSkillFeedbackToolIntegration:
    """End-to-end against the real ``SkillMetricsService``."""

    @pytest.mark.asyncio
    async def test_real_service_records_feedback(
        self, engine, project_id
    ):
        """Wire a real ``SkillMetricsService`` against the in-memory
        skill engine; the tool's body should record feedback."""
        from daemon.repositories.skill.repository import (
            SkillRepository,
            SkillUsageRepository,
        )
        from daemon.services.skill_metrics_service import (
            SkillMetricsService,
        )
        from daemon.repositories.skill.models import Skill  # noqa: F401

        skill_repo = SkillRepository(engine)
        usage_repo = SkillUsageRepository(engine)
        skill = skill_repo.create(
            name="phase4-feedback-skill",
            description="x",
            content="y",
            project_id=project_id,
        )
        # Seed a usage record so the tool has something to update.
        usage_repo.create(
            skill_id=skill.id,
            project_id=project_id,
            instance_id="inst-real",
            agent_id="developer",
        )

        metrics = SkillMetricsService(
            usage_repo=usage_repo,
            skill_repo=skill_repo,
            trigger_repo=MagicMock(),
            ab_test_repo=MagicMock(),
            config=MagicMock(
                ab_sample_size=10,
                ab_min_difference=0.15,
                max_extensions=3,
            ),
            instance_repo=MagicMock(),
        )

        manager = _build_manager(
            metrics_service=metrics,
            project_id=project_id,
            agent_id="developer",
        )
        # The closure captures ``current_instance_id`` at creation time,
        # so make sure it matches the seeded usage record.
        tools = create_skill_tools(manager, current_instance_id="inst-real")
        feedback = _find_tool(tools, "skill_feedback")

        result = await feedback.ainvoke(
            {
                "skill_id": skill.id,
                "applied": True,
                "note": "real-path test",
            }
        )

        assert "Feedback recorded" in result

        # Verify the usage record's feedback fields were actually
        # stamped.
        record = usage_repo.get_latest_for_skill_instance(
            skill_id=skill.id, instance_id="inst-real"
        )
        assert record is not None
        assert record.feedback_applied is True
        assert record.feedback_note == "real-path test"
        assert skill_repo.get(skill.id).total_applied == 1


# ─── Phase 5: usefulness + improvement_note params (2026-07-21) ──────────────


class TestSkillFeedbackToolPhase5Params:
    """The Phase 5 ``skill_feedback`` upgrade adds two new params:
    ``usefulness`` (1-10 quality score) and ``improvement_note``
    (actionable suggestion text). These tests cover the new
    fields' plumbing through the tool → service boundary.
    """

    @pytest.mark.asyncio
    async def test_records_feedback_with_usefulness_and_improvement(
        self,
    ):
        """All-new params (usefulness=8, improvement_note=...) are
        forwarded to ``record_feedback`` and surface in the
        confirmation string."""
        metrics = MagicMock()
        metrics.record_feedback = AsyncMock(return_value=True)
        manager = _build_manager(metrics_service=metrics)
        tools = create_skill_tools(manager, current_instance_id="inst-tool")
        feedback = _find_tool(tools, "skill_feedback")

        result = await feedback.ainvoke(
            {
                "skill_id": "abc-12345",
                "applied": True,
                "usefulness": 8,
                "note": "general context",
                "improvement_note": "Should mention PACKS.md location",
            }
        )

        assert "Feedback recorded" in result
        # New fields surface in the confirmation string.
        assert "Usefulness: 8/10" in result
        assert "Improvement:" in result
        assert "PACKS.md location" in result

        kwargs = metrics.record_feedback.await_args.kwargs
        assert kwargs["usefulness"] == 8
        assert kwargs["improvement_note"] == "Should mention PACKS.md location"
        # Backward compat: note still forwarded.
        assert kwargs["note"] == "general context"

    @pytest.mark.asyncio
    async def test_backward_compat_without_new_params(self):
        """Calling skill_feedback without the new params works exactly
        like before — neither field is required."""
        metrics = MagicMock()
        metrics.record_feedback = AsyncMock(return_value=True)
        manager = _build_manager(metrics_service=metrics)
        tools = create_skill_tools(manager, current_instance_id="inst-tool")
        feedback = _find_tool(tools, "skill_feedback")

        result = await feedback.ainvoke(
            {
                "skill_id": "abc-12345",
                "applied": True,
                "note": "helpful",
            }
        )

        assert "Feedback recorded" in result
        # No usefulness/improvement line in the response.
        assert "Usefulness:" not in result
        assert "Improvement:" not in result
        kwargs = metrics.record_feedback.await_args.kwargs
        # Default values: ``usefulness=None``, ``improvement_note=""``.
        assert kwargs["usefulness"] is None
        assert kwargs["improvement_note"] == ""

    @pytest.mark.asyncio
    async def test_usefulness_zero_returns_error_string(self):
        """``usefulness=0`` is out of range — the tool returns an
        ``ERROR:`` string and NEVER calls the metrics service."""
        metrics = MagicMock()
        metrics.record_feedback = AsyncMock(return_value=True)
        manager = _build_manager(metrics_service=metrics)
        tools = create_skill_tools(manager, current_instance_id="inst-tool")
        feedback = _find_tool(tools, "skill_feedback")

        result = await feedback.ainvoke(
            {"skill_id": "abc-12345", "usefulness": 0}
        )

        assert result.startswith("ERROR:")
        assert "range 1-10" in result
        assert "0" in result
        # Service NOT called — validation rejects before dispatch.
        assert metrics.record_feedback.await_count == 0

    @pytest.mark.asyncio
    async def test_usefulness_eleven_returns_error_string(self):
        """``usefulness=11`` is out of range — the tool returns an
        ``ERROR:`` string and NEVER calls the metrics service."""
        metrics = MagicMock()
        metrics.record_feedback = AsyncMock(return_value=True)
        manager = _build_manager(metrics_service=metrics)
        tools = create_skill_tools(manager, current_instance_id="inst-tool")
        feedback = _find_tool(tools, "skill_feedback")

        result = await feedback.ainvoke(
            {"skill_id": "abc-12345", "usefulness": 11}
        )

        assert result.startswith("ERROR:")
        assert "range 1-10" in result
        assert "11" in result
        # Service NOT called.
        assert metrics.record_feedback.await_count == 0

    @pytest.mark.asyncio
    async def test_usefulness_seven_is_accepted(self):
        """A valid ``usefulness=7`` is forwarded to the service."""
        metrics = MagicMock()
        metrics.record_feedback = AsyncMock(return_value=True)
        manager = _build_manager(metrics_service=metrics)
        tools = create_skill_tools(manager, current_instance_id="inst-tool")
        feedback = _find_tool(tools, "skill_feedback")

        result = await feedback.ainvoke(
            {"skill_id": "abc-12345", "applied": True, "usefulness": 7}
        )

        assert "Feedback recorded" in result
        assert "Usefulness: 7/10" in result
        kwargs = metrics.record_feedback.await_args.kwargs
        assert kwargs["usefulness"] == 7

    @pytest.mark.asyncio
    async def test_usefulness_non_int_returns_error_string(self):
        """A non-int ``usefulness`` value is rejected with a
        type-specific error message (matches the soft-fail contract).

        NOTE: LangChain's ``@tool`` Pydantic schema coerces string
        inputs (``"8"``) to int before the function body runs, so
        calling via ``ainvoke`` would silently pass. The validation
        in ``skill_tools.py`` guards against direct callers (e.g.
        agent loop internals) — we invoke the underlying coroutine
        (``tool.coroutine``) directly to verify the type-rejection
        path. ``metrics.record_feedback`` is NOT called.
        """
        metrics = MagicMock()
        metrics.record_feedback = AsyncMock(return_value=True)
        manager = _build_manager(metrics_service=metrics)
        tools = create_skill_tools(manager, current_instance_id="inst-tool")
        feedback = _find_tool(tools, "skill_feedback")

        # ``fb.coroutine`` bypasses the LangChain @tool wrapper so
        # the source function sees the raw string instead of Pydantic
        # coercing it to int.
        inner = getattr(feedback, "coroutine", None) or getattr(
            feedback, "func", None
        )
        assert inner is not None, "Could not access underlying coroutine"

        result = await inner(
            skill_id="abc-12345", usefulness="8"
        )

        assert result.startswith("ERROR:")
        assert "must be an int" in result
        assert metrics.record_feedback.await_count == 0

    @pytest.mark.asyncio
    async def test_usefulness_bool_rejected(self):
        """``bool`` is rejected — Python ``bool`` is a subclass of
        ``int`` but the tool explicitly excludes it so the agent
        can't sneak ``True``/``False`` through as 1/0.

        As above: bypass LangChain's Pydantic coercion (which would
        promote ``True`` → ``1``) by calling the underlying coroutine.
        """
        metrics = MagicMock()
        metrics.record_feedback = AsyncMock(return_value=True)
        manager = _build_manager(metrics_service=metrics)
        tools = create_skill_tools(manager, current_instance_id="inst-tool")
        feedback = _find_tool(tools, "skill_feedback")

        inner = getattr(feedback, "coroutine", None) or getattr(
            feedback, "func", None
        )
        assert inner is not None, "Could not access underlying coroutine"

        result = await inner(
            skill_id="abc-12345", usefulness=True
        )

        assert result.startswith("ERROR:")
        assert "must be an int" in result
        assert metrics.record_feedback.await_count == 0

    @pytest.mark.asyncio
    async def test_empty_improvement_note_treated_as_none(self):
        """An empty ``improvement_note`` is forwarded as the empty
        string (not None) — the service layer distinguishes
        "no change" (None) from "explicit clear" ("")."""
        metrics = MagicMock()
        metrics.record_feedback = AsyncMock(return_value=True)
        manager = _build_manager(metrics_service=metrics)
        tools = create_skill_tools(manager, current_instance_id="inst-tool")
        feedback = _find_tool(tools, "skill_feedback")

        await feedback.ainvoke(
            {
                "skill_id": "abc-12345",
                "applied": True,
                "improvement_note": "",
            }
        )

        kwargs = metrics.record_feedback.await_args.kwargs
        # Empty string is forwarded as "" so the service can detect
        # "explicit clear" if needed.
        assert kwargs["improvement_note"] == ""

    @pytest.mark.asyncio
    async def test_instance_id_closure_capture(self):
        """The closure-injected ``current_instance_id`` is forwarded
        as ``instance_id`` — NOT a parameter on the tool itself.

        Calling the tool without ``instance_id`` and verifying the
        service receives the closure value proves the auto-capture
        is wired correctly.
        """
        metrics = MagicMock()
        metrics.record_feedback = AsyncMock(return_value=True)
        # Build with a distinctive instance id.
        manager = _build_manager(metrics_service=metrics)
        tools = create_skill_tools(
            manager, current_instance_id="closure-inst-789"
        )
        feedback = _find_tool(tools, "skill_feedback")

        # No ``instance_id`` in the call payload — the tool doesn't
        # accept it as a parameter (auto-captured).
        await feedback.ainvoke(
            {
                "skill_id": "abc-12345",
                "applied": True,
                "usefulness": 9,
            }
        )

        kwargs = metrics.record_feedback.await_args.kwargs
        assert kwargs["instance_id"] == "closure-inst-789"


# ─── Phase 5: real service round-trip ──────────────────────────────────────


class TestSkillFeedbackToolPhase5RoundTrip:
    """End-to-end: real ``SkillMetricsService`` + new params.

    Mirrors :class:`TestSkillFeedbackToolIntegration` but exercises
    the new ``usefulness`` / ``improvement_note`` columns so we
    catch any regression in the on-disk schema path.
    """

    @pytest.mark.asyncio
    async def test_real_service_persists_usefulness_and_improvement(
        self, engine, project_id
    ):
        """The full tool → service → repo path stamps
        ``feedback_usefulness`` and ``feedback_improvement`` onto the
        usage row."""
        from daemon.repositories.skill.repository import (
            SkillRepository,
            SkillUsageRepository,
        )
        from daemon.services.skill_metrics_service import (
            SkillMetricsService,
        )

        skill_repo = SkillRepository(engine)
        usage_repo = SkillUsageRepository(engine)
        skill = skill_repo.create(
            name="phase5-feedback-skill",
            description="x",
            content="y",
            project_id=project_id,
        )
        usage_repo.create(
            skill_id=skill.id,
            project_id=project_id,
            instance_id="inst-phase5",
            agent_id="developer",
        )

        metrics = SkillMetricsService(
            usage_repo=usage_repo,
            skill_repo=skill_repo,
            trigger_repo=MagicMock(),
            ab_test_repo=MagicMock(),
            config=MagicMock(
                ab_sample_size=10,
                ab_min_difference=0.15,
                max_extensions=3,
            ),
            instance_repo=MagicMock(),
        )

        manager = _build_manager(
            metrics_service=metrics,
            project_id=project_id,
            agent_id="developer",
        )
        tools = create_skill_tools(
            manager, current_instance_id="inst-phase5"
        )
        feedback = _find_tool(tools, "skill_feedback")

        result = await feedback.ainvoke(
            {
                "skill_id": skill.id,
                "applied": True,
                "usefulness": 9,
                "improvement_note": "Add timeout checklist example",
            }
        )

        assert "Feedback recorded" in result
        # Verify the new columns landed on the row.
        record = usage_repo.get_latest_for_skill_instance(
            skill_id=skill.id, instance_id="inst-phase5"
        )
        assert record is not None
        assert record.feedback_usefulness == 9
        assert record.feedback_improvement == "Add timeout checklist example"