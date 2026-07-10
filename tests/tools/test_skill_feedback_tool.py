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