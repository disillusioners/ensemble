"""Tests for the Skill Evolution Tool Category (Phase 5).

These tests exercise the 5 LangChain tools produced by
:func:`daemon.tools.skill_evolution_tools.create_skill_evolution_tools`.

The skill-evolution tools wrap :class:`SkillEvolutionService` methods
(the service itself lives in
``daemon.services.skill_evolution_service``). Each tool delegates to
:func:`daemon.tools.skill_evolution_tools._invoke_service`, which
attempts to call ``manager._skill_evolution_service.<method>`` when
the service is wired up and otherwise returns a soft-fail stub
message so the agent sees a tool response instead of a stack trace.

Phase 5 (this revision) replaces the Phase 2 stub method names with
the *real* :class:`SkillEvolutionService` method names:

* ``analyze`` -> ``analyze_skill(skill_id, reason="", stats=None)``
* ``evolve`` -> ``evolve_skill(skill_id, evolution_type, direction)``
* ``resolve_ab`` -> ``check_ab_test_resolution(ab_test_group)``
* ``get_metrics`` -> ``get_skill_metrics(skill_id)``
* ``execute_capture`` -> ``capture_skill(current_instance_id, task_details)``

The ``skill_execute_capture`` tool's signature also changed from
``(instance_id, task_details)`` to ``(instance_id, task_message,
iterations, duration_seconds)`` so callers don't have to construct
the ``task_details`` dict themselves - the tool wraps the args
into the dict and forwards the closure-supplied
``current_instance_id`` as the service's first parameter.

Test classes
------------

* :class:`TestFactory` - ``create_skill_evolution_tools`` returns 5
  tools with the expected names, and the ``"skill-evolution"``
  category is registered in the tool registry.
* :class:`TestStubWhenServiceMissing` - when
  ``manager._skill_evolution_service`` is absent, every one of the 5
  tools returns the ``"\u23f3"`` stub message.
* :class:`TestStubWhenServiceAvailable` - when a real (mocked)
  service is wired up, every tool dispatches to the **real**
  Phase 5 service method with the right args and the result string
  contains the expected JSON.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest


SKILL_EVOLUTION_TOOL_NAMES: frozenset[str] = frozenset({
    "skill_analyze",
    "skill_evolve",
    "skill_resolve_ab",
    "skill_get_metrics",
    "skill_execute_capture",
})


STUB_NEEDLE = "\u23f3"


@pytest.fixture
def skill_evolution_tools():
    """Build the 5 LangChain tools, indexed by name."""
    from daemon.tools.skill_evolution_tools import create_skill_evolution_tools

    manager = MagicMock()
    manager._skill_evolution_service = None
    tools_list = create_skill_evolution_tools(manager, "test-instance")
    return {getattr(t, "name", None): t for t in tools_list}

# =============================================================================
# Group 1: Factory
# =============================================================================


class TestFactory:
    """The ``create_skill_evolution_tools`` factory must return 5
    tools with the expected names, and the ``"skill-evolution"``
    category must be registered in the tool registry."""

    def test_factory_returns_five_tools(self):
        """Factory returns exactly 5 tools."""
        from daemon.tools.skill_evolution_tools import create_skill_evolution_tools

        manager = MagicMock()
        manager._skill_evolution_service = None
        tools_list = create_skill_evolution_tools(manager, "test-instance")
        assert len(tools_list) == 5

    def test_factory_returns_expected_tool_names(self, skill_evolution_tools):
        """The 5 returned tool names match the expected set exactly."""
        assert set(skill_evolution_tools.keys()) == set(SKILL_EVOLUTION_TOOL_NAMES)

    def test_factory_accepts_mock_manager(self):
        """Factory accepts a MagicMock manager without crashing."""
        from daemon.tools.skill_evolution_tools import create_skill_evolution_tools

        manager = MagicMock()
        manager._skill_evolution_service = None
        result = create_skill_evolution_tools(manager, "x")
        assert len(result) == 5

    def test_factory_registers_skill_evolution_category(self, skill_evolution_tools):
        """The ``"skill-evolution"`` category is registered with all 5 names."""
        from daemon.tools._tool_registry import (
            clear_registry,
            get_tool_categories,
            list_tools_by_category,
            scan_tools_for_full_docs,
        )

        clear_registry()
        try:
            scan_tools_for_full_docs(list(skill_evolution_tools.values()))
            categories = list_tools_by_category()
            assert "skill-evolution" in categories
            assert set(categories["skill-evolution"]) == set(SKILL_EVOLUTION_TOOL_NAMES)

            named = get_tool_categories()
            assert "Skill Evolution" in named
            assert set(named["Skill Evolution"]) == set(SKILL_EVOLUTION_TOOL_NAMES)
        finally:
            clear_registry()


# =============================================================================
# Group 2: stub message when manager._skill_evolution_service is missing
# =============================================================================


class TestStubWhenServiceMissing:
    """Every one of the 5 tools must return the stub message when
    ``manager._skill_evolution_service`` is absent.
    """

    @pytest.mark.asyncio
    async def test_skill_analyze_returns_stub_when_service_missing(
        self, skill_evolution_tools
    ):
        """``skill_analyze`` returns the stub message."""
        tool = skill_evolution_tools["skill_analyze"]
        result = await tool.ainvoke({"skill_id": "skill-keeper"})
        assert STUB_NEEDLE in result
        assert "skill-keeper" in result or "skill-ke" in result

    @pytest.mark.asyncio
    async def test_skill_evolve_returns_stub_when_service_missing(
        self, skill_evolution_tools
    ):
        """``skill_evolve`` returns the stub message."""
        tool = skill_evolution_tools["skill_evolve"]
        result = await tool.ainvoke({
            "skill_id": "skill-keeper",
            "evolution_type": "prompt_rewrite",
            "direction": "tighten error handling",
        })
        assert STUB_NEEDLE in result

    @pytest.mark.asyncio
    async def test_skill_resolve_ab_returns_stub_when_service_missing(
        self, skill_evolution_tools
    ):
        """``skill_resolve_ab`` returns the stub message."""
        tool = skill_evolution_tools["skill_resolve_ab"]
        result = await tool.ainvoke({"ab_test_group": "prompt-style-2026-07"})
        assert STUB_NEEDLE in result

    @pytest.mark.asyncio
    async def test_skill_get_metrics_returns_stub_when_service_missing(
        self, skill_evolution_tools
    ):
        """``skill_get_metrics`` returns the stub message."""
        tool = skill_evolution_tools["skill_get_metrics"]
        result = await tool.ainvoke({"skill_id": "skill-keeper"})
        assert STUB_NEEDLE in result

    @pytest.mark.asyncio
    async def test_skill_execute_capture_returns_stub_when_service_missing(
        self, skill_evolution_tools
    ):
        """``skill_execute_capture`` returns the stub message."""
        tool = skill_evolution_tools["skill_execute_capture"]
        result = await tool.ainvoke({
            "instance_id": "inst-abc12345",
            "task_message": "Capture a skill-cap record",
            "iterations": 3,
            "duration_seconds": 30,
        })
        assert STUB_NEEDLE in result


# =============================================================================
# Group 3: dispatch when manager._skill_evolution_service is present
# =============================================================================


class TestStubWhenServiceAvailable:
    """When ``manager._skill_evolution_service`` is wired up, every
    one of the 5 tools must:

    1. Call the matching **Phase 5** service method with the right args.
    2. Return a JSON string that contains the service's return value.

    We mock the service as ``AsyncMock`` so the ``hasattr(result,
    "__await__")`` branch in :func:`_invoke_service` is taken. The
    real :class:`SkillEvolutionService` exposes ``async def`` methods.
    """

    @pytest.mark.asyncio
    async def test_skill_analyze_dispatches_to_service(self, skill_evolution_tools):
        """``skill_analyze`` calls
        ``service.analyze_skill(skill_id, reason="", stats=None)`` and
        returns the JSON-encoded result.
        """
        from unittest.mock import AsyncMock
        from daemon.tools.skill_evolution_tools import create_skill_evolution_tools

        service = MagicMock()
        service.analyze_skill = AsyncMock(return_value={
            "should_evolve": True,
            "evolution_type": "FIX",
            "direction": "tighten errors",
            "analysis_summary": "low completion rate",
        })
        manager = MagicMock()
        manager._skill_evolution_service = service
        tools = {t.name: t for t in create_skill_evolution_tools(manager, "i")}

        result = await tools["skill_analyze"].ainvoke({"skill_id": "skill-keeper"})

        service.analyze_skill.assert_awaited_once_with(
            "skill-keeper", reason="", stats=None
        )
        decoded = json.loads(result)
        assert decoded["should_evolve"] is True
        assert decoded["evolution_type"] == "FIX"

    @pytest.mark.asyncio
    async def test_skill_evolve_dispatches_to_service(self):
        """``skill_evolve`` calls
        ``service.evolve_skill(skill_id, evolution_type, direction)``.
        """
        from unittest.mock import AsyncMock
        from daemon.tools.skill_evolution_tools import create_skill_evolution_tools

        service = MagicMock()
        service.evolve_skill = AsyncMock(return_value={
            "new_skill_id": "skill-new",
            "old_skill_id": "skill-old",
            "ab_test_group": "grp-1",
            "skipped": False,
        })
        manager = MagicMock()
        manager._skill_evolution_service = service
        tools = {t.name: t for t in create_skill_evolution_tools(manager, "i")}

        result = await tools["skill_evolve"].ainvoke({
            "skill_id": "skill-keeper",
            "evolution_type": "FIX",
            "direction": "tighten error handling",
        })

        service.evolve_skill.assert_awaited_once_with(
            "skill-keeper", "FIX", "tighten error handling"
        )
        decoded = json.loads(result)
        assert decoded["new_skill_id"] == "skill-new"
        assert decoded["skipped"] is False

    @pytest.mark.asyncio
    async def test_skill_resolve_ab_dispatches_to_service(self):
        """``skill_resolve_ab`` calls
        ``service.check_ab_test_resolution(ab_test_group)``.
        """
        from unittest.mock import AsyncMock
        from daemon.tools.skill_evolution_tools import create_skill_evolution_tools

        service = MagicMock()
        service.check_ab_test_resolution = AsyncMock(return_value={
            "resolved": True,
            "winner_id": "skill-new",
            "loser_id": "skill-old",
            "reason": "threshold_met",
            "extension_count": 0,
        })
        manager = MagicMock()
        manager._skill_evolution_service = service
        tools = {t.name: t for t in create_skill_evolution_tools(manager, "i")}

        result = await tools["skill_resolve_ab"].ainvoke(
            {"ab_test_group": "prompt-style-2026-07"}
        )

        service.check_ab_test_resolution.assert_awaited_once_with(
            "prompt-style-2026-07"
        )
        decoded = json.loads(result)
        assert decoded["resolved"] is True
        assert decoded["winner_id"] == "skill-new"

    @pytest.mark.asyncio
    async def test_skill_get_metrics_dispatches_to_service(self):
        """``skill_get_metrics`` calls
        ``service.get_skill_metrics(skill_id)``.
        """
        from unittest.mock import AsyncMock
        from daemon.tools.skill_evolution_tools import create_skill_evolution_tools

        service = MagicMock()
        service.get_skill_metrics = AsyncMock(return_value={
            "skill_id": "skill-keeper",
            "found": True,
            "stats": {"invocations": 42, "success_rate": 0.97},
            "usage_recent_count": 10,
            "ab_test": None,
        })
        manager = MagicMock()
        manager._skill_evolution_service = service
        tools = {t.name: t for t in create_skill_evolution_tools(manager, "i")}

        result = await tools["skill_get_metrics"].ainvoke({"skill_id": "skill-keeper"})

        service.get_skill_metrics.assert_awaited_once_with("skill-keeper")
        decoded = json.loads(result)
        assert decoded["found"] is True
        assert decoded["stats"]["invocations"] == 42
        assert abs(decoded["stats"]["success_rate"] - 0.97) < 1e-9

    @pytest.mark.asyncio
    async def test_skill_execute_capture_dispatches_to_service(self):
        """``skill_execute_capture`` constructs ``task_details`` from
        its args and calls
        ``service.capture_skill(current_instance_id, task_details)``.
        """
        from unittest.mock import AsyncMock
        from daemon.tools.skill_evolution_tools import create_skill_evolution_tools

        service = MagicMock()
        service.capture_skill = AsyncMock(return_value={
            "new_skill_id": "skill-captured",
            "skipped": False,
        })
        manager = MagicMock()
        manager._skill_evolution_service = service
        tools = {t.name: t for t in create_skill_evolution_tools(manager, "inst-closure-id")}

        result = await tools["skill_execute_capture"].ainvoke({
            "instance_id": "inst-arg-id",
            "task_message": "Capture a skill-cap record",
            "iterations": 5,
            "duration_seconds": 60,
        })

        service.capture_skill.assert_awaited_once_with(
            "inst-closure-id",
            {
                "instance_id": "inst-arg-id",
                "task_message": "Capture a skill-cap record",
                "iterations": 5,
                "duration_seconds": 60,
            },
        )
        decoded = json.loads(result)
        assert decoded["new_skill_id"] == "skill-captured"
        assert decoded["skipped"] is False


# =============================================================================
# Group 4: explicit Phase 5 dispatch tests (spec-named)
# =============================================================================


class TestPhase5Dispatch:
    """Phase 5 spec-named tests — each tool → matching service method.

    These are equivalent to ``TestStubWhenServiceAvailable`` but use
    the exact test names from the Phase 5 plan so it's trivial to
    verify the dispatch contract from the plan checklist.
    """

    @pytest.mark.asyncio
    async def test_skill_analyze_calls_analyze_skill(self):
        from unittest.mock import AsyncMock
        from daemon.tools.skill_evolution_tools import create_skill_evolution_tools

        service = MagicMock()
        service.analyze_skill = AsyncMock(return_value={
            "should_evolve": True,
            "evolution_type": "FIX",
            "direction": "x",
            "analysis_summary": "y",
        })
        manager = MagicMock()
        manager._skill_evolution_service = service
        tools = {t.name: t for t in create_skill_evolution_tools(manager, "i")}

        await tools["skill_analyze"].ainvoke({"skill_id": "skill-x"})

        service.analyze_skill.assert_awaited_once_with(
            "skill-x", reason="", stats=None
        )

    @pytest.mark.asyncio
    async def test_skill_evolve_calls_evolve_skill(self):
        from unittest.mock import AsyncMock
        from daemon.tools.skill_evolution_tools import create_skill_evolution_tools

        service = MagicMock()
        service.evolve_skill = AsyncMock(return_value={
            "new_skill_id": "s-new",
            "old_skill_id": "s-old",
            "ab_test_group": "g",
            "skipped": False,
        })
        manager = MagicMock()
        manager._skill_evolution_service = service
        tools = {t.name: t for t in create_skill_evolution_tools(manager, "i")}

        await tools["skill_evolve"].ainvoke({
            "skill_id": "s-old",
            "evolution_type": "FIX",
            "direction": "d",
        })

        service.evolve_skill.assert_awaited_once_with(
            "s-old", "FIX", "d"
        )

    @pytest.mark.asyncio
    async def test_skill_resolve_ab_calls_check_ab_test_resolution(self):
        from unittest.mock import AsyncMock
        from daemon.tools.skill_evolution_tools import create_skill_evolution_tools

        service = MagicMock()
        service.check_ab_test_resolution = AsyncMock(return_value={
            "resolved": True,
            "winner_id": "w",
            "loser_id": "l",
            "reason": "threshold_met",
            "extension_count": 0,
        })
        manager = MagicMock()
        manager._skill_evolution_service = service
        tools = {t.name: t for t in create_skill_evolution_tools(manager, "i")}

        await tools["skill_resolve_ab"].ainvoke(
            {"ab_test_group": "g-1"}
        )

        service.check_ab_test_resolution.assert_awaited_once_with("g-1")

    @pytest.mark.asyncio
    async def test_skill_get_metrics_calls_get_skill_metrics(self):
        from unittest.mock import AsyncMock
        from daemon.tools.skill_evolution_tools import create_skill_evolution_tools

        service = MagicMock()
        service.get_skill_metrics = AsyncMock(return_value={
            "skill_id": "x",
            "found": True,
            "stats": {},
            "usage_recent_count": 0,
            "ab_test": None,
        })
        manager = MagicMock()
        manager._skill_evolution_service = service
        tools = {t.name: t for t in create_skill_evolution_tools(manager, "i")}

        await tools["skill_get_metrics"].ainvoke({"skill_id": "x"})

        service.get_skill_metrics.assert_awaited_once_with("x")

    @pytest.mark.asyncio
    async def test_skill_execute_capture_calls_capture_skill(self):
        from unittest.mock import AsyncMock
        from daemon.tools.skill_evolution_tools import create_skill_evolution_tools

        service = MagicMock()
        service.capture_skill = AsyncMock(return_value={
            "new_skill_id": "x",
            "skipped": False,
        })
        manager = MagicMock()
        manager._skill_evolution_service = service
        tools = {t.name: t for t in create_skill_evolution_tools(
            manager, "closure-inst-id"
        )}

        await tools["skill_execute_capture"].ainvoke({
            "instance_id": "arg-inst-id",
            "task_message": "msg",
            "iterations": 7,
            "duration_seconds": 80,
        })

        service.capture_skill.assert_awaited_once_with(
            "closure-inst-id",
            {
                "instance_id": "arg-inst-id",
                "task_message": "msg",
                "iterations": 7,
                "duration_seconds": 80,
            },
        )

    @pytest.mark.asyncio
    async def test_skill_execute_capture_signature_consistency(self):
        """Tool → ``capture_skill()`` → ``_evolve_captured()`` chain uses consistent dict format.

        The tool wraps its 4 positional args into a ``task_details``
        dict and forwards it to ``service.capture_skill(instance_id,
        task_details)``. ``capture_skill`` then forwards that same
        dict (verbatim) to ``_evolve_captured``. We mock
        ``_evolve_captured`` on a real :class:`SkillEvolutionService`
        instance and assert the dict shape survives the chain.
        """
        from unittest.mock import AsyncMock
        from daemon.services.skill_evolution_service import SkillEvolutionService
        from daemon.tools.skill_evolution_tools import create_skill_evolution_tools

        # Build a real service with the minimum wiring the constructor needs.
        service = SkillEvolutionService.__new__(SkillEvolutionService)
        service._skill_repo = MagicMock()
        service._lineage_repo = MagicMock()
        service._usage_repo = MagicMock()
        service._embedding_service = MagicMock()
        service._metrics_service = MagicMock()
        service._ab_test_repo = MagicMock()
        service._config = MagicMock()
        service._llm_config = {"model": "gpt-4o"}
        # Spy on _evolve_captured so we can assert on what got through.
        service._evolve_captured = AsyncMock(
            return_value={"new_skill_id": "skill-spy", "skipped": False}
        )

        manager = MagicMock()
        manager._skill_evolution_service = service
        tools = {t.name: t for t in create_skill_evolution_tools(
            manager, "closure-inst"
        )}

        await tools["skill_execute_capture"].ainvoke({
            "instance_id": "arg-inst",
            "task_message": "do the thing",
            "iterations": 11,
            "duration_seconds": 99,
        })

        # _evolve_captured was called once with the dict the tool built.
        service._evolve_captured.assert_awaited_once()
        passed_dict = service._evolve_captured.await_args.args[0]
        assert passed_dict == {
            "instance_id": "arg-inst",
            "task_message": "do the thing",
            "iterations": 11,
            "duration_seconds": 99,
        }


# =============================================================================
# Group 5: soft-fail helpful message
# =============================================================================


class TestSoftFailMessage:
    """When the service is missing, every tool returns a "not yet initialized"
    stub message that includes a short hint (first positional arg) so the
    skill-keeper can correlate the stub with the operation it triggered.
    """

    @pytest.mark.asyncio
    async def test_tools_soft_fail_when_service_missing(
        self, skill_evolution_tools
    ):
        """Every tool returns the ⏳ stub message + short hint."""
        # skill_analyze — hint should include the skill_id (truncated to 10 chars).
        result_analyze = await skill_evolution_tools["skill_analyze"].ainvoke(
            {"skill_id": "skill-abcdef123"}
        )
        assert STUB_NEEDLE in result_analyze
        assert "skill-ab" in result_analyze

        # skill_evolve — hint includes the skill_id (truncated to 10 chars).
        result_evolve = await skill_evolution_tools["skill_evolve"].ainvoke({
            "skill_id": "skill-xyz123",
            "evolution_type": "FIX",
            "direction": "d",
        })
        assert STUB_NEEDLE in result_evolve
        assert "skill-xy" in result_evolve

        # skill_resolve_ab — hint includes the ab_test_group.
        result_resolve = await skill_evolution_tools["skill_resolve_ab"].ainvoke(
            {"ab_test_group": "abgroup-789"}
        )
        assert STUB_NEEDLE in result_resolve
        assert "abgroup-7" in result_resolve

        # skill_get_metrics — hint includes the skill_id.
        result_metrics = await skill_evolution_tools["skill_get_metrics"].ainvoke(
            {"skill_id": "skill-mno456"}
        )
        assert STUB_NEEDLE in result_metrics
        assert "skill-mn" in result_metrics

        # skill_execute_capture — hint includes the closure-supplied
        # current_instance_id (NOT the arg-supplied instance_id — see the
        # implementation: the first positional arg forwarded is the closure
        # value).
        result_capture = await skill_evolution_tools[
            "skill_execute_capture"
        ].ainvoke({
            "instance_id": "inst-capture-789",
            "task_message": "msg",
            "iterations": 1,
            "duration_seconds": 1,
        })
        assert STUB_NEEDLE in result_capture
        assert "test-ins" in result_capture  # "test-instance"[:10]

    @pytest.mark.asyncio
    async def test_soft_fail_includes_service_name(self, skill_evolution_tools):
        """Stub message names the missing service attribute so it's debuggable."""
        result = await skill_evolution_tools["skill_analyze"].ainvoke(
            {"skill_id": "x"}
        )
        # Should mention the attribute name the agent needs to wire.
        assert "_skill_evolution_service" in result

    @pytest.mark.asyncio
    async def test_service_present_but_method_missing_returns_warning(self):
        """Service exists but method is missing → ⚠️ stub message."""
        from daemon.tools.skill_evolution_tools import create_skill_evolution_tools

        service = MagicMock(spec=[])  # no methods at all
        manager = MagicMock()
        manager._skill_evolution_service = service
        tools = {t.name: t for t in create_skill_evolution_tools(manager, "i")}

        result = await tools["skill_analyze"].ainvoke({"skill_id": "x"})

        # Spec=[] means getattr returns a MagicMock, not None — but
        # the missing-method branch returns ⚠️ not the callable result.
        assert "not found" in result or STUB_NEEDLE in result
