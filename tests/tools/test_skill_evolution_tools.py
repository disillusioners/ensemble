"""Tests for the Skill Evolution Tool Category (Phase 2).

These tests exercise the 5 LangChain tools produced by
:func:`daemon.tools.skill_evolution_tools.create_skill_evolution_tools`.

The skill-evolution tools are Phase 2 stubs that wrap
:class:`SkillEvolutionService` methods (the service itself is
implemented in Phase 5). Each tool delegates to
:func:`daemon.tools.skill_evolution_tools._invoke_service`, which
attempts to call ``manager._skill_evolution_service.<method>`` when
the service is wired up and otherwise returns a soft-fail stub
message so the agent sees a tool response instead of a stack trace.

Test classes
------------

* :class:`TestFactory` — ``create_skill_evolution_tools`` returns 5
  tools with the expected names, and the ``"skill-evolution"``
  category is registered in the tool registry.
* :class:`TestStubWhenServiceMissing` — when
  ``manager._skill_evolution_service`` is absent (either not set or
  explicitly ``None``), every one of the 5 tools returns the
  ``"\u23f3 \u2026 queued for Phase 5"`` stub message.
* :class:`TestStubWhenServiceAvailable` — when a real (mocked)
  service is wired up, every tool dispatches to the service method
  with the right args and the result string contains the expected
  JSON.

Conventions
-----------

* Use ``pytest-asyncio`` (mode=auto via ``pyproject.toml``).
* Build the tools once per test via the
  ``skill_evolution_tools`` fixture, which returns a dict keyed by
  tool name.
* The :class:`InstanceManager` is a plain ``MagicMock`` because the
  factory accepts it for parity but the stubs only read
  ``manager._skill_evolution_service`` (and the service is mocked
  per-test as needed).
* Call each tool via ``tool.ainvoke({...})`` and assert on the
  returned string. Tools are sync (def, not async def) but
  ``ainvoke`` works on both sync and async LangChain tools.
* The :mod:`tests.tools.conftest` is infra-specific and is NOT used
  here \u2014 these tests need plain ``MagicMock`` only.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest


# =============================================================================
# Constants
# =============================================================================


# The 5 tool names registered by ``create_skill_evolution_tools``.
# Repeated as a module constant so the factory and registry tests can
# assert against a single source of truth.
SKILL_EVOLUTION_TOOL_NAMES: frozenset[str] = frozenset({
    "skill_analyze",
    "skill_evolve",
    "skill_resolve_ab",
    "skill_get_metrics",
    "skill_execute_capture",
})


# Substrings that MUST appear in every "service not yet connected"
# stub message. Anchored on the "⏳" emoji and the "Phase 5" reference
# so a future copy edit cannot silently break the stub contract.
STUB_NEEDLE = "\u23f3"
STUB_PHASE_NEEDLE = "Phase 5"


# =============================================================================
# Shared fixtures
# =============================================================================


@pytest.fixture
def skill_evolution_tools():
    """Build the 5 LangChain tools, indexed by name.

    The ``manager`` arg is a plain ``MagicMock`` with
    ``_skill_evolution_service = None`` so every tool falls into the
    "service missing" branch by default. Tests that want to exercise
    the "service present" path override ``manager._skill_evolution_service``
    on the SAME mock before invoking the tool \u2014 the closure captures
    the mock object, so attribute mutation is visible.

    Returns:
        Dict of ``{tool_name: tool_function}``.
    """
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
        """Factory accepts a MagicMock manager without crashing.

        The factory captures ``manager`` in the closure but the stubs
        only read ``manager._skill_evolution_service``. A MagicMock
        with the attribute explicitly set to ``None`` therefore
        exercises the "service missing" branch cleanly and must not
        raise.
        """
        from daemon.tools.skill_evolution_tools import create_skill_evolution_tools

        manager = MagicMock()
        manager._skill_evolution_service = None
        # Should not raise.
        result = create_skill_evolution_tools(manager, "x")
        assert len(result) == 5

    def test_factory_registers_skill_evolution_category(self, skill_evolution_tools):
        """The ``"skill-evolution"`` category is registered with all
        5 names after the factory has run ``scan_tools_for_full_docs``.

        This verifies the ``@register_tool_category("skill-evolution")``
        decorator on each tool function, the ``_full_doc_`` attribute
        wiring, and that ``scan_tools_for_full_docs`` correctly picks
        up the metadata.

        The category is also expected to resolve to the human-readable
        ``CATEGORY_NAME`` ("Skill Evolution") via
        :func:`get_tool_categories`, which requires the
        ``"skill-evolution"`` entry in :data:`CATEGORY_MODULES`.
        """
        from daemon.tools._tool_registry import (
            clear_registry,
            get_tool_categories,
            list_tools_by_category,
            scan_tools_for_full_docs,
        )

        clear_registry()
        try:
            # ``skill_evolution_tools`` is a dict (already built by
            # the fixture). Pass the underlying list to the scanner.
            scan_tools_for_full_docs(list(skill_evolution_tools.values()))
            categories = list_tools_by_category()
            assert "skill-evolution" in categories
            assert set(categories["skill-evolution"]) == set(SKILL_EVOLUTION_TOOL_NAMES)

            # ``get_tool_categories`` resolves the category key to the
            # human-readable ``CATEGORY_NAME`` via ``CATEGORY_MODULES``.
            # The entry for "skill-evolution" is added in Phase 2.
            named = get_tool_categories()
            assert "Skill Evolution" in named
            assert set(named["Skill Evolution"]) == set(SKILL_EVOLUTION_TOOL_NAMES)
        finally:
            clear_registry()


# =============================================================================
# Group 2: stub message when manager._skill_evolution_service is missing
# =============================================================================


class TestStubWhenServiceMissing:
    """Every one of the 5 tools must return the
    "\u23f3 \u2026 queued for Phase 5" stub message when
    ``manager._skill_evolution_service`` is absent.

    The fixture already sets ``manager._skill_evolution_service = None``
    so every tool falls into the "service missing" branch.
    """

    @pytest.mark.asyncio
    async def test_skill_analyze_returns_stub_when_service_missing(
        self, skill_evolution_tools
    ):
        """``skill_analyze`` returns the stub message."""
        tool = skill_evolution_tools["skill_analyze"]
        result = await tool.ainvoke({"skill_id": "skill-keeper"})
        assert STUB_NEEDLE in result
        assert STUB_PHASE_NEEDLE in result
        # The skill id hint should be echoed back so the agent can
        # correlate the stub message to the operation it issued.
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
        assert STUB_PHASE_NEEDLE in result

    @pytest.mark.asyncio
    async def test_skill_resolve_ab_returns_stub_when_service_missing(
        self, skill_evolution_tools
    ):
        """``skill_resolve_ab`` returns the stub message even though
        it takes no ``skill_id`` (its first positional arg is the A/B
        group slug, which the stub still echoes back as the hint).
        """
        tool = skill_evolution_tools["skill_resolve_ab"]
        result = await tool.ainvoke({"ab_test_group": "prompt-style-2026-07"})
        assert STUB_NEEDLE in result
        assert STUB_PHASE_NEEDLE in result

    @pytest.mark.asyncio
    async def test_skill_get_metrics_returns_stub_when_service_missing(
        self, skill_evolution_tools
    ):
        """``skill_get_metrics`` returns the stub message."""
        tool = skill_evolution_tools["skill_get_metrics"]
        result = await tool.ainvoke({"skill_id": "skill-keeper"})
        assert STUB_NEEDLE in result
        assert STUB_PHASE_NEEDLE in result

    @pytest.mark.asyncio
    async def test_skill_execute_capture_returns_stub_when_service_missing(
        self, skill_evolution_tools
    ):
        """``skill_execute_capture`` returns the stub message even
        though its first positional arg is an ``instance_id``, not a
        ``skill_id`` (the stub still echoes back a hint).
        """
        tool = skill_evolution_tools["skill_execute_capture"]
        result = await tool.ainvoke({
            "instance_id": "inst-abc12345",
            "task_details": "Capture a skill-cap record",
        })
        assert STUB_NEEDLE in result
        assert STUB_PHASE_NEEDLE in result


# =============================================================================
# Group 3: dispatch when manager._skill_evolution_service is present
# =============================================================================


class TestStubWhenServiceAvailable:
    """When ``manager._skill_evolution_service`` is wired up, every
    one of the 5 tools must:

    1. Call the matching service method with the right args.
    2. Return a JSON string that contains the service's return value.

    We mock the service as a plain ``MagicMock`` (NOT ``AsyncMock``)
    so the ``hasattr(result, "__await__")`` branch in
    :func:`_invoke_service` is never taken. The skill-evolution
    service is free to be either sync or async at the Phase 5
    implementation; the stubs handle both shapes uniformly.
    """

    @pytest.mark.asyncio
    async def test_skill_analyze_dispatches_to_service(self, skill_evolution_tools):
        """``skill_analyze`` calls ``service.analyze(skill_id)`` and
        returns the JSON-encoded result.
        """
        service = MagicMock()
        service.analyze = MagicMock(return_value={
            "skill_id": "skill-keeper",
            "tier": 2,
            "issues": [],
        })
        # Reach into the closure-captured manager via the tool's name
        # attribute, then swap in the real mock service. The simplest
        # path is to reconstruct the factory call with a fresh mock
        # manager that already has the service attached.
        from daemon.tools.skill_evolution_tools import create_skill_evolution_tools

        manager = MagicMock()
        manager._skill_evolution_service = service
        tools = {t.name: t for t in create_skill_evolution_tools(manager, "i")}

        result = await tools["skill_analyze"].ainvoke({"skill_id": "skill-keeper"})

        service.analyze.assert_called_once_with("skill-keeper")
        # The return value must round-trip through ``json.dumps``.
        assert "skill-keeper" in result
        assert '"tier"' in result
        # And it should be valid JSON.
        decoded = json.loads(result)
        assert decoded == {
            "skill_id": "skill-keeper",
            "tier": 2,
            "issues": [],
        }

    @pytest.mark.asyncio
    async def test_skill_evolve_dispatches_to_service(self):
        """``skill_evolve`` calls
        ``service.evolve(skill_id, evolution_type, direction)``.
        """
        from daemon.tools.skill_evolution_tools import create_skill_evolution_tools

        service = MagicMock()
        service.evolve = MagicMock(return_value={
            "skill_id": "skill-keeper",
            "diff": "--- before\n+++ after",
        })
        manager = MagicMock()
        manager._skill_evolution_service = service
        tools = {t.name: t for t in create_skill_evolution_tools(manager, "i")}

        result = await tools["skill_evolve"].ainvoke({
            "skill_id": "skill-keeper",
            "evolution_type": "prompt_rewrite",
            "direction": "tighten error handling",
        })

        service.evolve.assert_called_once_with(
            "skill-keeper", "prompt_rewrite", "tighten error handling"
        )
        decoded = json.loads(result)
        assert decoded["skill_id"] == "skill-keeper"
        assert decoded["diff"].startswith("--- before")

    @pytest.mark.asyncio
    async def test_skill_resolve_ab_dispatches_to_service(self):
        """``skill_resolve_ab`` calls
        ``service.resolve_ab(ab_test_group)``.
        """
        from daemon.tools.skill_evolution_tools import create_skill_evolution_tools

        service = MagicMock()
        service.resolve_ab = MagicMock(return_value={
            "ab_test_group": "prompt-style-2026-07",
            "winner": "variant_a",
            "decided_at": "2026-07-11T00:00:00Z",
        })
        manager = MagicMock()
        manager._skill_evolution_service = service
        tools = {t.name: t for t in create_skill_evolution_tools(manager, "i")}

        result = await tools["skill_resolve_ab"].ainvoke(
            {"ab_test_group": "prompt-style-2026-07"}
        )

        service.resolve_ab.assert_called_once_with("prompt-style-2026-07")
        decoded = json.loads(result)
        assert decoded["winner"] == "variant_a"

    @pytest.mark.asyncio
    async def test_skill_get_metrics_dispatches_to_service(self):
        """``skill_get_metrics`` calls ``service.get_metrics(skill_id)``."""
        from daemon.tools.skill_evolution_tools import create_skill_evolution_tools

        service = MagicMock()
        service.get_metrics = MagicMock(return_value={
            "skill_id": "skill-keeper",
            "invocations": 42,
            "success_rate": 0.97,
        })
        manager = MagicMock()
        manager._skill_evolution_service = service
        tools = {t.name: t for t in create_skill_evolution_tools(manager, "i")}

        result = await tools["skill_get_metrics"].ainvoke({"skill_id": "skill-keeper"})

        service.get_metrics.assert_called_once_with("skill-keeper")
        decoded = json.loads(result)
        assert decoded["invocations"] == 42
        assert abs(decoded["success_rate"] - 0.97) < 1e-9

    @pytest.mark.asyncio
    async def test_skill_execute_capture_dispatches_to_service(self):
        """``skill_execute_capture`` calls
        ``service.execute_capture(instance_id, task_details)``.
        """
        from daemon.tools.skill_evolution_tools import create_skill_evolution_tools

        service = MagicMock()
        service.execute_capture = MagicMock(return_value={
            "capture_id": "cap-001",
            "skill_id": "skill-keeper",
        })
        manager = MagicMock()
        manager._skill_evolution_service = service
        tools = {t.name: t for t in create_skill_evolution_tools(manager, "i")}

        result = await tools["skill_execute_capture"].ainvoke({
            "instance_id": "inst-abc12345",
            "task_details": "Capture a skill-cap record",
        })

        service.execute_capture.assert_called_once_with(
            "inst-abc12345", "Capture a skill-cap record"
        )
        decoded = json.loads(result)
        assert decoded["capture_id"] == "cap-001"