"""Tests for ``daemon.tools.chart_tools.create_chart_tools`` and ``generate_chart``.

Three coverage lanes:

  1. **Factory** — ``create_chart_tools(manager, current_instance_id)`` returns
     a list with exactly one tool, ``generate_chart``.
  2. **Registration** — the returned tool is registered under the
     ``"chart"`` category (via the ``_tool_category`` attribute set by
     ``@register_tool_category``), NOT the ``"instance"`` category. This is
     the category counterpart of the ``test_tool_filter.py`` security test
     that pins ``INNATE_SKILL_TOOL_CATEGORIES``.
  3. **Invocation** — calling ``generate_chart`` delegates to
     ``invoke_agent_and_wait`` with the correct parameters
     (``agent_id="charter"``, ``return_instance_id=True``, ``timeout=600.0``)
     and constructs a message containing the description and diagram_type.

The mocking pattern mirrors ``tests/test_spawn_team_members.py``: a
``MagicMock`` manager with a ``_instance_repository.get`` that returns
``None`` (no project context to keep tests deterministic), and
``invoke_agent_and_wait`` patched at the chart_tools module level.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import daemon.tools.chart_tools as chart_tools_module


def _make_manager() -> MagicMock:
    """Build a mock manager wired for ``generate_chart`` invocation.

    The chart tool calls ``manager._instance_repository.get(...)`` to
    auto-inherit project_id; returning ``None`` keeps the project_id
    auto-injection path deterministic (no project context). The tool
    also passes the manager through to ``invoke_agent_and_wait``.

    Charter-reuse stubs (Phase 1 Task 1, adjudicated P9 with P2 flip):
    ``get_children → []`` keeps discovery empty and deterministic (a bare
    MagicMock auto-attribute would make discovery truthy by accident), and
    ``enqueue_message`` is an ``AsyncMock`` for the reuse path (the fresh
    path never touches it). NO ``shared_meta_kv_repo`` stubs — the reuse
    path never reads or writes the KV repo under pure query-discovery
    (adjudicated P2=(a)).
    """
    manager = MagicMock()
    manager._instance_repository = MagicMock()
    manager._instance_repository.get = MagicMock(return_value=None)
    manager._instance_repository.get_children = MagicMock(return_value=[])
    manager.enqueue_message = AsyncMock(return_value=MagicMock())
    return manager


class TestCreateChartToolsFactory:
    """Factory tests for ``create_chart_tools``."""

    def test_factory_returns_exactly_one_tool(self):
        """Factory returns a list containing exactly one tool."""
        from daemon.tools.chart_tools import create_chart_tools

        manager = _make_manager()
        tools = create_chart_tools(manager, "test-instance-id")

        assert isinstance(tools, list)
        assert len(tools) == 1

    def test_factory_returns_generate_chart_tool(self):
        """The returned tool is ``generate_chart``."""
        from daemon.tools.chart_tools import create_chart_tools

        manager = _make_manager()
        tools = create_chart_tools(manager, "test-instance-id")

        # ``@tool`` from langchain exposes the function name via .name
        assert tools[0].name == "generate_chart"

    def test_factory_creates_independent_tools_per_call(self):
        """Each factory call produces a fresh closure (no shared state).

        The two ``generate_chart`` tools should be distinct objects so that
        a per-instance tool list does not leak state between instances.
        """
        from daemon.tools.chart_tools import create_chart_tools

        manager = _make_manager()
        tools_a = create_chart_tools(manager, "instance-a")
        tools_b = create_chart_tools(manager, "instance-b")

        # Same name, distinct objects (each call re-binds closures).
        assert tools_a[0] is not tools_b[0]
        assert tools_a[0].name == tools_b[0].name


class TestChartToolRegistration:
    """Registration tests for the chart tool category."""

    def test_generate_chart_registered_under_chart_category(self):
        """The tool is tagged with ``_tool_category == "chart"``.

        Set by ``@register_tool_category("chart")`` in ``chart_tools.py``.
        """
        from daemon.tools.chart_tools import create_chart_tools

        manager = _make_manager()
        tools = create_chart_tools(manager, "test-instance-id")

        assert getattr(tools[0], "_tool_category", None) == "chart"

    def test_generate_chart_not_registered_under_instance_category(self):
        """SECURITY: the chart tool must NOT be tagged as ``"instance"``.

        Companion to the ``INNATE_SKILL_TOOL_CATEGORIES`` security test in
        ``test_tool_filter.py``. If this ever fails, a chart-enabled agent
        would be implicitly granted the full instance-management suite.
        """
        from daemon.tools.chart_tools import create_chart_tools

        manager = _make_manager()
        tools = create_chart_tools(manager, "test-instance-id")

        assert getattr(tools[0], "_tool_category", None) != "instance"


class TestGenerateChartInvocation:
    """Invocation tests for ``generate_chart``."""

    async def test_generate_chart_delegates_to_invoke_agent_and_wait(self):
        """generate_chart calls invoke_agent_and_wait with the documented params.

        Verifies ``agent_id="charter"``, ``return_instance_id=True``,
        ``timeout=600.0``, and ``parent_id`` flowing from the closure.
        """
        from daemon.tools.chart_tools import create_chart_tools

        manager = _make_manager()
        mock_invoke = AsyncMock(return_value=("mermaid output", "child-instance-id"))

        with patch("daemon.tools.chart_tools.invoke_agent_and_wait", mock_invoke):
            tools = create_chart_tools(manager, "test-instance-id")
            await tools[0].coroutine(
                description="User authentication flow",
                diagram_type="sequence",
            )

        # Called exactly once
        mock_invoke.assert_awaited_once()

        kwargs = mock_invoke.call_args.kwargs
        # Manager is passed through verbatim
        assert kwargs["manager"] is manager
        # Charter agent is the delegate
        assert kwargs["agent_id"] == "charter"
        # Always returns the (content, instance_id) tuple form
        assert kwargs["return_instance_id"] is True
        # 10-minute timeout (double the 5-minute explore() default)
        assert kwargs["timeout"] == 600.0
        # parent_id is the calling instance
        assert kwargs["parent_id"] == "test-instance-id"

        # Message carries the description and diagram_type
        message = kwargs["message"]
        assert "User authentication flow" in message
        assert "sequence" in message

    async def test_generate_chart_default_diagram_type_is_flowchart(self):
        """When ``diagram_type`` is omitted, the message uses ``flowchart``."""
        from daemon.tools.chart_tools import create_chart_tools

        manager = _make_manager()
        mock_invoke = AsyncMock(return_value=("output", "child-id"))

        with patch("daemon.tools.chart_tools.invoke_agent_and_wait", mock_invoke):
            tools = create_chart_tools(manager, "test-instance-id")
            await tools[0].coroutine(description="Some flow")

        message = mock_invoke.call_args.kwargs["message"]
        assert "flowchart" in message

    async def test_generate_chart_returns_agent_response(self):
        """Tool returns the agent's response content string."""
        from daemon.tools.chart_tools import create_chart_tools

        manager = _make_manager()
        expected = "```mermaid\ngraph TD\nA-->B\n```\n\nThis is the flow."
        mock_invoke = AsyncMock(return_value=(expected, "child-id"))

        with patch("daemon.tools.chart_tools.invoke_agent_and_wait", mock_invoke):
            tools = create_chart_tools(manager, "test-instance-id")
            result = await tools[0].coroutine(description="Graph")

        assert result == expected

    async def test_generate_chart_handles_none_result_as_error(self):
        """``None`` content from ``invoke_agent_and_wait`` → ``Error:`` string.

        Mirrors the contract in ``chart_tools.py``: when ``invoke_agent_and_wait``
        returns ``(None, instance_id)`` (the agent never produced a result),
        the tool returns a short error message rather than bubbling ``None``
        to the LLM (which would crash downstream parsing).
        """
        from daemon.tools.chart_tools import create_chart_tools

        manager = _make_manager()
        mock_invoke = AsyncMock(return_value=(None, "child-id"))

        with patch("daemon.tools.chart_tools.invoke_agent_and_wait", mock_invoke):
            tools = create_chart_tools(manager, "test-instance-id")
            result = await tools[0].coroutine(description="Graph")

        assert isinstance(result, str)
        assert result.startswith("Error:")

    async def test_generate_chart_propagates_explicit_project_id(self):
        """``project_id`` kwarg flows into the message + invoke_agent_and_wait."""
        from daemon.tools.chart_tools import create_chart_tools

        manager = _make_manager()
        mock_invoke = AsyncMock(return_value=("output", "child-id"))

        with patch("daemon.tools.chart_tools.invoke_agent_and_wait", mock_invoke):
            tools = create_chart_tools(manager, "test-instance-id")
            await tools[0].coroutine(
                description="Architecture overview",
                diagram_type="flowchart",
                project_id="my-project-id",
            )

        kwargs = mock_invoke.call_args.kwargs
        assert kwargs["project_id"] == "my-project-id"
        # Project id is included in the message for charter's context
        assert "my-project-id" in kwargs["message"]


# ---------------------------------------------------------------------------
# Charter reuse (Phase 1 — T8) — discovery, reuse wait helper, fresh kwarg,
# busy guard, revive counter, mode log.
# ---------------------------------------------------------------------------


def _charter_row(
    instance_id: str = "charter-1",
    status: str = "completed",
    last_activity_at=None,
    created_at: str = "2026-09-10T00:00:00+00:00",
    agent_id: str = "charter",
    invoked_as_tool: bool = True,
) -> SimpleNamespace:
    """Fabricate an ``instances`` row stand-in for discovery/reuse tests.

    Field names mirror ``daemon/repositories/instance/models.py`` — note the
    primary key is ``instance_id`` (NOT ``id``) and ``created_at`` is an ISO
    string while ``last_activity_at`` is a nullable datetime.
    """
    return SimpleNamespace(
        instance_id=instance_id,
        agent_id=agent_id,
        status=status,
        instance_metadata={"invoked_as_tool": True} if invoked_as_tool else {},
        last_activity_at=last_activity_at,
        created_at=created_at,
        project_id=None,
    )


def _make_registry(wait_result=None) -> MagicMock:
    """Mock CompletionRegistry with a configured ``wait_for`` result.

    Patched at the ``daemon.services.completion_registry`` MODULE attribute —
    ``_reuse_charter`` imports ``get_completion_registry`` lazily inside the
    function body, so the patched name must be visible at the module
    attribute (the local import re-binds on every call), mirroring
    ``tests/test_finalize_instance.py::patched_completion_registry``.
    """
    registry = MagicMock(name="CompletionRegistry")
    registry.wait_for = AsyncMock(return_value=wait_result)
    return registry


@contextmanager
def _patched_registry(mock_registry):
    """Patch ``get_completion_registry`` at the module attribute."""
    with patch(
        "daemon.services.completion_registry.get_completion_registry",
        return_value=mock_registry,
    ):
        yield mock_registry


_BUSY_STRING = "Error: Charter busy; pass fresh=True for parallel charts."
_PAUSED_STRING = (
    "Error: Charter is paused; resume it or pass fresh=True for a new charter."
)
_LOG_FORMAT = "generate_chart: caller=%s charter=%s mode=%s prior_status=%s"


def _mode_records(caplog) -> list:
    """All chart_tools log records captured by ``caplog``."""
    return [r for r in caplog.records if r.name == "daemon.tools.chart_tools"]


class TestGenerateChartReuse:
    """Charter-reuse unit tests (Phase 1 T8.1–T8.13)."""

    @pytest.fixture(autouse=True)
    def _reset_reuse_module_state(self):
        """Module-level reuse state must not leak between tests."""
        chart_tools_module._inflight_reuse.clear()
        chart_tools_module._reuse_revive_attempts.clear()
        yield
        chart_tools_module._inflight_reuse.clear()
        chart_tools_module._reuse_revive_attempts.clear()

    # ── T8.1 ──────────────────────────────────────────────────────────────
    async def test_first_call_spawns_fresh(self, caplog):
        """Discovery empty → fresh spawn with legacy kwargs; log mode=fresh."""
        from daemon.tools.chart_tools import create_chart_tools

        manager = _make_manager()  # get_children → [] (deterministic miss)
        mock_invoke = AsyncMock(return_value=("mermaid output", "child-id"))
        mock_registry = _make_registry()

        with caplog.at_level(logging.INFO, logger="daemon.tools.chart_tools"):
            with patch(
                "daemon.tools.chart_tools.invoke_agent_and_wait", mock_invoke
            ):
                tools = create_chart_tools(manager, "test-instance-id")
                result = await tools[0].coroutine(
                    description="User authentication flow",
                    diagram_type="sequence",
                )

        assert result == "mermaid output"
        mock_invoke.assert_awaited_once()
        kwargs = mock_invoke.call_args.kwargs
        assert kwargs["manager"] is manager
        assert kwargs["agent_id"] == "charter"
        assert kwargs["return_instance_id"] is True
        assert kwargs["timeout"] == 600.0
        assert kwargs["parent_id"] == "test-instance-id"
        assert "User authentication flow" in kwargs["message"]
        # Fresh path never enqueues on an existing instance.
        manager.enqueue_message.assert_not_awaited()

        records = _mode_records(caplog)
        assert len(records) == 1
        assert "mode=fresh" in records[0].getMessage()

    # ── T8.2 ──────────────────────────────────────────────────────────────
    async def test_second_call_reuses_same_instance(self, caplog):
        """Terminal COMPLETED charter → same-id enqueue; invoke NOT awaited."""
        from daemon.tools.chart_tools import create_chart_tools

        manager = _make_manager()
        completed = _charter_row(
            instance_id="charter-1",
            status="completed",
            last_activity_at=datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc),
        )
        manager._instance_repository.get_children = MagicMock(
            return_value=[completed]
        )
        manager._instance_repository.get = MagicMock(return_value=completed)
        mock_invoke = AsyncMock(return_value=("should not be used", "x"))
        expected = "```mermaid\ngraph TD\nA-->B\n```\n\nRefined."
        mock_registry = _make_registry(
            wait_result=SimpleNamespace(content=expected, is_error=False)
        )

        with caplog.at_level(logging.INFO, logger="daemon.tools.chart_tools"):
            with patch(
                "daemon.tools.chart_tools.invoke_agent_and_wait", mock_invoke
            ):
                with _patched_registry(mock_registry):
                    tools = create_chart_tools(manager, "test-instance-id")
                    result = await tools[0].coroutine(
                        description="User authentication flow",
                        diagram_type="sequence",
                    )

        # Content returned verbatim.
        assert result == expected
        # The existing charter is enqueued with the reuse source prefix and
        # the chart_reuse metadata flag — ONLY existing kwargs (M11).
        manager.enqueue_message.assert_awaited_once()
        enqueue_kwargs = manager.enqueue_message.call_args.kwargs
        assert enqueue_kwargs["instance_id"] == "charter-1"
        assert enqueue_kwargs["source"] == "internal_chart_reuse:test-instance-id"
        assert enqueue_kwargs["metadata"] == {"chart_reuse": True}
        assert "User authentication flow" in enqueue_kwargs["message"]
        # The spawn helper is NOT used on the reuse path.
        mock_invoke.assert_not_awaited()
        # register → wait → unregister in finally.
        mock_registry.register.assert_called_once_with("charter-1")
        # W1 fix: ``unregister`` fires TWICE now (step-3 stale-buffer
        # drain + step-7 finally cleanup) — pin the target id without
        # constraining the count.
        mock_registry.unregister.assert_called_with("charter-1")
        assert mock_registry.unregister.call_count == 2

        records = _mode_records(caplog)
        assert len(records) == 1
        assert "mode=reuse" in records[0].getMessage()
        assert "prior_status=completed" in records[0].getMessage()

    # ── T8.3 ──────────────────────────────────────────────────────────────
    async def test_explicit_fresh_spawns_new_instance(self):
        """fresh=True spawns a NEW charter; the NEXT call discovers it."""
        from daemon.tools.chart_tools import create_chart_tools

        manager = _make_manager()
        old_charter = _charter_row(
            instance_id="old-charter",
            last_activity_at=datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc),
        )
        manager._instance_repository.get_children = MagicMock(
            return_value=[old_charter]
        )
        mock_invoke = AsyncMock(return_value=("fresh output", "brand-new-id"))
        mock_registry = _make_registry(
            wait_result=SimpleNamespace(content="reused new", is_error=False)
        )

        with patch("daemon.tools.chart_tools.invoke_agent_and_wait", mock_invoke):
            with _patched_registry(mock_registry):
                tools = create_chart_tools(manager, "test-instance-id")

                # Call 1: fresh=True with an existing child → STILL spawns.
                result = await tools[0].coroutine(
                    description="Brand new chart", fresh=True
                )
                assert result == "fresh output"
                mock_invoke.assert_awaited_once()
                manager.enqueue_message.assert_not_awaited()

                # Call 2: default (reuse) → discovers the NEW charter.
                new_charter = _charter_row(
                    instance_id="brand-new-id",
                    last_activity_at=datetime(
                        2026, 9, 11, 12, 0, tzinfo=timezone.utc
                    ),
                )
                manager._instance_repository.get_children = MagicMock(
                    return_value=[new_charter]
                )
                manager._instance_repository.get = MagicMock(
                    return_value=new_charter
                )
                result2 = await tools[0].coroutine(description="Refine it")

                assert result2 == "reused new"
                manager.enqueue_message.assert_awaited_once()
                assert (
                    manager.enqueue_message.call_args.kwargs["instance_id"]
                    == "brand-new-id"
                )

    # ── T8.4 ──────────────────────────────────────────────────────────────
    async def test_error_charter_one_revive_then_respawn(self, caplog):
        """ERROR charter: call 1 revives (counter 1), call 2 spawns fresh."""
        from daemon.tools.chart_tools import create_chart_tools

        manager = _make_manager()
        error_charter = _charter_row(
            instance_id="charter-err",
            status="error",
            last_activity_at=datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc),
        )
        manager._instance_repository.get_children = MagicMock(
            side_effect=[[error_charter], [error_charter]]
        )
        manager._instance_repository.get = MagicMock(return_value=error_charter)
        mock_registry = _make_registry(
            wait_result=SimpleNamespace(content="revived once", is_error=False)
        )
        mock_invoke = AsyncMock(return_value=("respawned", "brand-new-id"))

        with caplog.at_level(logging.INFO, logger="daemon.tools.chart_tools"):
            with _patched_registry(mock_registry):
                tools = create_chart_tools(manager, "test-instance-id")

                # Call 1: ERROR + counter 0 → revive (consume).
                result1 = await tools[0].coroutine(description="Retry")
                assert result1 == "revived once"
                assert chart_tools_module._reuse_revive_attempts[
                    "charter-err"
                ] == 1
                manager.enqueue_message.assert_awaited_once()

                # Call 2: counter ≥ 1 → treat as MISS → fresh spawn.
                with patch(
                    "daemon.tools.chart_tools.invoke_agent_and_wait",
                    mock_invoke,
                ):
                    result2 = await tools[0].coroutine(description="Retry again")
                assert result2 == "respawned"
                # Counter NOT incremented again by the respawn path.
                assert chart_tools_module._reuse_revive_attempts[
                    "charter-err"
                ] == 1
                # Still only ONE enqueue (call 1); call 2 spawned fresh.
                assert manager.enqueue_message.await_count == 1
                mock_invoke.assert_awaited_once()

        records = _mode_records(caplog)
        assert len(records) == 2  # exactly ONE mode line per call
        assert "mode=reuse" in records[0].getMessage()
        assert (
            "mode=reuse-respawn-after-failure" in records[1].getMessage()
        )
        assert "prior_status=error" in records[1].getMessage()

    # ── T8.5 ──────────────────────────────────────────────────────────────
    async def test_busy_reject_on_concurrent_reuse(self, caplog):
        """In-flight id → exact busy string; NO enqueue, NO register."""
        from daemon.tools.chart_tools import create_chart_tools

        manager = _make_manager()
        completed = _charter_row(
            instance_id="charter-1", status="completed"
        )
        manager._instance_repository.get_children = MagicMock(
            return_value=[completed]
        )
        manager._instance_repository.get = MagicMock(return_value=completed)
        chart_tools_module._inflight_reuse.add("charter-1")
        mock_registry = _make_registry()

        with caplog.at_level(logging.WARNING, logger="daemon.tools.chart_tools"):
            with _patched_registry(mock_registry):
                tools = create_chart_tools(manager, "test-instance-id")
                result = await tools[0].coroutine(description="Racing call")

        assert result == _BUSY_STRING
        manager.enqueue_message.assert_not_awaited()
        mock_registry.register.assert_not_called()

        records = _mode_records(caplog)
        assert len(records) == 1
        assert records[0].levelno == logging.WARNING
        assert "mode=busy-reject" in records[0].getMessage()

    # ── T8.6 ──────────────────────────────────────────────────────────────
    async def test_busy_guard_fires_before_register(self):
        """P9 addition 1 — ordering pin with a REAL registry.

        The first reuse call is driven to (and parked inside)
        ``registry.wait_for`` via explicit ``asyncio.sleep(0)`` yield points;
        the second call must then busy-reject with ZERO register side-effect
        and register call count exactly 1 (guards the two-waiter
        event-coalescing hazard).
        """
        from daemon.services.completion_registry import CompletionRegistry
        from daemon.tools.chart_tools import create_chart_tools

        completed = _charter_row(
            instance_id="charter-1", status="completed"
        )
        manager = _make_manager()
        manager._instance_repository.get_children = MagicMock(
            return_value=[completed]
        )
        manager._instance_repository.get = MagicMock(return_value=completed)

        class _CountingRegistry(CompletionRegistry):
            """Real registry + register-call recording."""

            def __init__(self):
                super().__init__()
                self.register_calls: list[str] = []

            def register(self, instance_id):
                self.register_calls.append(instance_id)
                return super().register(instance_id)

        registry = _CountingRegistry()

        with patch(
            "daemon.services.completion_registry.get_completion_registry",
            return_value=registry,
        ):
            tools = create_chart_tools(manager, "test-instance-id")

            # Start caller 1; yield until it holds the slot past the
            # set-add and is registered (i.e. inside wait_for — the only
            # await left before its mapping code).
            first_task = asyncio.create_task(
                tools[0].coroutine(description="First refinement")
            )
            for _ in range(100):
                if (
                    "charter-1" in chart_tools_module._inflight_reuse
                    and registry.is_registered("charter-1")
                ):
                    break
                await asyncio.sleep(0)
            await asyncio.sleep(0)
            await asyncio.sleep(0)

            assert "charter-1" in chart_tools_module._inflight_reuse
            assert registry.is_registered("charter-1")

            # Caller 2 runs while caller 1 is parked inside wait_for.
            second = await tools[0].coroutine(description="Second refinement")
            assert second == _BUSY_STRING

            # The REJECTED call observed NO register side-effect: the check
            # precedes register — exactly one register call, from caller 1.
            assert registry.register_calls == ["charter-1"]
            assert manager.enqueue_message.await_count == 1

            # Release caller 1; the registry ends clean.
            registry.complete("charter-1", result="late mermaid", is_error=False)
            first_result = await asyncio.wait_for(first_task, timeout=5.0)

        assert first_result == "late mermaid"
        assert "charter-1" not in chart_tools_module._inflight_reuse
        assert not registry.is_registered("charter-1")

    # ── T8.7 ──────────────────────────────────────────────────────────────
    async def test_reuse_timeout_does_not_terminate(self):
        """wait_for → None → timeout error string; NO terminate (M8)."""
        from daemon.tools.chart_tools import create_chart_tools

        manager = _make_manager()
        completed = _charter_row(
            instance_id="charter-1", status="completed"
        )
        manager._instance_repository.get_children = MagicMock(
            return_value=[completed]
        )
        manager._instance_repository.get = MagicMock(return_value=completed)
        manager.terminate_instance = AsyncMock()
        mock_registry = _make_registry(wait_result=None)  # timeout

        with _patched_registry(mock_registry):
            tools = create_chart_tools(manager, "test-instance-id")
            result = await tools[0].coroutine(description="Slow refinement")

        assert result.startswith("Error: Charter timed out after 600.0s")
        assert "may still be running" in result
        # Shared/durable charter is NEVER terminated on the reuse path.
        manager.terminate_instance.assert_not_awaited()
        # finally-cleanup still ran. W1 fix: ``unregister`` fires TWICE
        # now (step-3 stale-buffer drain + step-7 finally cleanup) — pin
        # the target id without constraining the count.
        mock_registry.unregister.assert_called_with("charter-1")
        assert mock_registry.unregister.call_count == 2
        assert "charter-1" not in chart_tools_module._inflight_reuse

    # ── T8.8 ──────────────────────────────────────────────────────────────
    def test_discovery_picks_latest_charter(self):
        """W2 determinism pin: latest last_activity_at wins; tie-breaks.

        Fixture includes a NULL-last_activity_at row (treated as OLDEST —
        never wins, even with the newest created_at) and REVERSE-ordered
        input rows (proves the Python-side sort — ``get_children`` ships no
        ORDER BY). Non-charter / non-tool rows are excluded regardless of
        recency.
        """
        t0 = datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc).isoformat()
        t1 = datetime(2026, 9, 10, 13, 0, tzinfo=timezone.utc)
        t2 = datetime(2026, 9, 10, 14, 0, tzinfo=timezone.utc)
        t3 = datetime(2026, 9, 10, 15, 0, tzinfo=timezone.utc)

        find = chart_tools_module._find_reusable_charter

        # 1. Latest activity wins — input rows in REVERSE order.
        rows = [
            _charter_row("charter-old", last_activity_at=t1, created_at=t2.isoformat()),
            _charter_row("charter-mid", last_activity_at=t2, created_at=t1.isoformat()),
            _charter_row("charter-latest", last_activity_at=t3, created_at=t0),
        ]
        manager = _make_manager()
        manager._instance_repository.get_children = MagicMock(
            return_value=list(reversed(rows))
        )
        assert find(manager, "caller-1").instance_id == "charter-latest"

        # 2. NULL last_activity_at is OLDEST — never wins, even with the
        #    newest created_at.
        null_row = _charter_row("charter-null", last_activity_at=None, created_at=t3.isoformat())
        manager._instance_repository.get_children = MagicMock(
            return_value=[null_row, rows[1]]
        )
        assert find(manager, "caller-1").instance_id == "charter-mid"
        manager._instance_repository.get_children = MagicMock(
            return_value=[null_row]
        )
        assert find(manager, "caller-1").instance_id == "charter-null"

        # 3. Equal activity → created_at desc; equal both → id desc.
        tie_created = _charter_row(
            "charter-tie-created", last_activity_at=t3, created_at=t1
        )
        winner_created = _charter_row(
            "charter-tie-created-later", last_activity_at=t3, created_at=t2
        )
        manager._instance_repository.get_children = MagicMock(
            return_value=[tie_created, winner_created]
        )
        assert (
            find(manager, "caller-1").instance_id == "charter-tie-created-later"
        )
        tie_id_a = _charter_row("charter-tie-a", last_activity_at=t3, created_at=t2)
        tie_id_b = _charter_row("charter-tie-b", last_activity_at=t3, created_at=t2)
        manager._instance_repository.get_children = MagicMock(
            return_value=[tie_id_b, tie_id_a]
        )
        assert find(manager, "caller-1").instance_id == "charter-tie-b"

        # 4. Non-charter and non-tool-invoked children are excluded
        #    regardless of recency.
        non_charter = _charter_row(
            "explorer-1", agent_id="explorer", last_activity_at=t3
        )
        non_tool = _charter_row(
            "charter-manual", last_activity_at=t3, invoked_as_tool=False
        )
        manager._instance_repository.get_children = MagicMock(
            return_value=[non_charter, non_tool]
        )
        assert find(manager, "caller-1") is None
        manager._instance_repository.get_children = MagicMock(
            return_value=[non_charter, non_tool, rows[2]]
        )
        assert find(manager, "caller-1").instance_id == "charter-latest"

        # 5. No charter children at all → None.
        manager = _make_manager()  # get_children → []
        assert find(manager, "caller-1") is None

    # ── T8.9 ──────────────────────────────────────────────────────────────
    async def test_log_line_mode_field(self, caplog):
        """M6 format pin: exact template, one line per call, per mode."""
        from daemon.tools.chart_tools import create_chart_tools

        manager = _make_manager()  # get_children → [] → call 1 is a fresh miss
        completed = _charter_row(
            instance_id="charter-1", status="completed"
        )
        manager._instance_repository.get = MagicMock(return_value=completed)
        mock_invoke = AsyncMock(return_value=("out", "new-id"))
        mock_registry = _make_registry(
            wait_result=SimpleNamespace(content="ok", is_error=False)
        )

        with caplog.at_level(logging.INFO, logger="daemon.tools.chart_tools"):
            with patch(
                "daemon.tools.chart_tools.invoke_agent_and_wait", mock_invoke
            ):
                with _patched_registry(mock_registry):
                    tools = create_chart_tools(manager, "test-instance-id")
                    # Call 1: discovery miss → fresh.
                    await tools[0].coroutine(description="First")

                    # Call 2: discovery hit → reuse.
                    manager._instance_repository.get_children = MagicMock(
                        return_value=[completed]
                    )
                    await tools[0].coroutine(description="Second")

                    # Call 3: fresh=True → fresh again.
                    await tools[0].coroutine(description="Third", fresh=True)

        records = _mode_records(caplog)
        assert len(records) == 3
        for record in records:
            assert record.msg == _LOG_FORMAT
            assert record.args[0] == "test-ins"  # caller_id[:8]
            assert record.getMessage().startswith(
                "generate_chart: caller=test-ins charter="
            )
        assert "charter=spawn mode=fresh prior_status=none" in records[
            0
        ].getMessage()
        assert (
            "charter=charter- mode=reuse prior_status=completed"
            in records[1].getMessage()
        )
        assert "charter=spawn mode=fresh prior_status=none" in records[
            2
        ].getMessage()

    # ── T8.10 ─────────────────────────────────────────────────────────────
    async def test_busy_reject_on_paused_charter(self, caplog):
        """W6 pin: PAUSED charter → exact paused string; NO enqueue/register."""
        from daemon.tools.chart_tools import create_chart_tools

        manager = _make_manager()
        paused = _charter_row(instance_id="charter-1", status="paused")
        manager._instance_repository.get_children = MagicMock(
            return_value=[paused]
        )
        manager._instance_repository.get = MagicMock(return_value=paused)
        mock_registry = _make_registry()

        with caplog.at_level(logging.WARNING, logger="daemon.tools.chart_tools"):
            with _patched_registry(mock_registry):
                tools = create_chart_tools(manager, "test-instance-id")
                result = await tools[0].coroutine(description="Refine paused")

        assert result == _PAUSED_STRING
        manager.enqueue_message.assert_not_awaited()
        mock_registry.register.assert_not_called()

        records = _mode_records(caplog)
        assert len(records) == 1
        assert records[0].levelno == logging.WARNING
        assert "mode=busy-reject" in records[0].getMessage()
        assert "prior_status=paused" in records[0].getMessage()

    # ── T8.11 ─────────────────────────────────────────────────────────────
    async def test_completed_charter_does_not_consume_counter(self):
        """W6 pin: repeated COMPLETED revives never touch the counter."""
        from daemon.tools.chart_tools import create_chart_tools

        manager = _make_manager()
        completed = _charter_row(
            instance_id="charter-1", status="completed"
        )
        manager._instance_repository.get_children = MagicMock(
            return_value=[completed]
        )
        manager._instance_repository.get = MagicMock(return_value=completed)
        mock_registry = _make_registry(
            wait_result=SimpleNamespace(content="ok", is_error=False)
        )

        with _patched_registry(mock_registry):
            tools = create_chart_tools(manager, "test-instance-id")
            for i in range(3):
                result = await tools[0].coroutine(description=f"Refine {i}")
                assert result == "ok"

        assert manager.enqueue_message.await_count == 3
        # COMPLETED/TERMINATED are free revives — counter NEVER created.
        assert chart_tools_module._reuse_revive_attempts.get("charter-1") is None

    # ── T8.12 ─────────────────────────────────────────────────────────────
    async def test_discovery_repository_error_degrades_to_fresh(self):
        """W6 pin: get_children raising → fresh spawn; never raises to LLM."""
        from daemon.tools.chart_tools import create_chart_tools

        manager = _make_manager()
        manager._instance_repository.get_children = MagicMock(
            side_effect=RuntimeError("database unavailable")
        )
        mock_invoke = AsyncMock(return_value=("mermaid output", "child-id"))

        with patch(
            "daemon.tools.chart_tools.invoke_agent_and_wait", mock_invoke
        ):
            tools = create_chart_tools(manager, "test-instance-id")
            result = await tools[0].coroutine(description="Graph")

        # Discovery failure degraded to the fresh path — no exception
        # reached the caller.
        assert result == "mermaid output"
        mock_invoke.assert_awaited_once()
        manager.enqueue_message.assert_not_awaited()
        # The helper's direct contract: repo error → None.
        assert (
            chart_tools_module._find_reusable_charter(manager, "test-instance-id")
            is None
        )

    # ── T8.13 ─────────────────────────────────────────────────────────────
    async def test_terminated_charter_revives_free(self, caplog):
        """W3 pin: TERMINATED charter → reuse proceeds, counter untouched."""
        from daemon.tools.chart_tools import create_chart_tools

        manager = _make_manager()
        terminated = _charter_row(
            instance_id="charter-term", status="terminated"
        )
        manager._instance_repository.get_children = MagicMock(
            return_value=[terminated]
        )
        manager._instance_repository.get = MagicMock(return_value=terminated)
        mock_invoke = AsyncMock(return_value=("unused", "x"))
        mock_registry = _make_registry(
            wait_result=SimpleNamespace(content="revived", is_error=False)
        )

        with caplog.at_level(logging.INFO, logger="daemon.tools.chart_tools"):
            with patch(
                "daemon.tools.chart_tools.invoke_agent_and_wait", mock_invoke
            ):
                with _patched_registry(mock_registry):
                    tools = create_chart_tools(manager, "test-instance-id")
                    result = await tools[0].coroutine(description="After terminate")

        assert result == "revived"
        manager.enqueue_message.assert_awaited_once()
        assert (
            manager.enqueue_message.call_args.kwargs["instance_id"]
            == "charter-term"
        )
        assert (
            manager.enqueue_message.call_args.kwargs["source"]
            == "internal_chart_reuse:test-instance-id"
        )
        mock_invoke.assert_not_awaited()
        assert chart_tools_module._reuse_revive_attempts.get("charter-term") is None
        records = _mode_records(caplog)
        assert len(records) == 1
        assert "mode=reuse" in records[0].getMessage()
        assert "prior_status=terminated" in records[0].getMessage()
    # ── T8.14 (W1 unit pin) ───────────────────────────────────────────────
    async def test_stale_buffered_completion_is_drained_before_register(self):
        """W1 unit pin: a buffered completion from a PRIOR turn must NOT
        be consumed by the next ``_reuse_charter`` call.

        Scenario: the registry is pre-seeded with a stale buffered
        completion for the charter id (no waiter was parked when it
        landed — e.g. fresh-path 600s-timeout unregister, or an external
        message completing the charter). Without the W1 fix, the next
        ``register()`` would consume that stale entry, set the event
        immediately (``completion_registry.py:79-85``), and ``wait_for``
        would return the OLD content. With the fix, ``unregister`` clears
        ``_buffered`` first so the new turn starts clean, and the
        side-task's NEW completion is what ``wait_for`` returns.

        Asserts:
          * Returned content == NEW (the OLD content would have leaked
            without the W1 fix).
          * ``register`` fires AFTER the drain — exactly twice (step-3
            + post-enqueue re-register).
          * ``unregister`` fires at least twice (step-3 drain + step-7
            finally cleanup).
        """
        from daemon.services.completion_registry import CompletionRegistry
        from daemon.tools.chart_tools import create_chart_tools

        charter_id = "charter-w1-stale"
        old_content = "```mermaid\nOLD\n```\nStale."
        new_content = "```mermaid\nNEW\n```\nFresh."

        completed = _charter_row(instance_id=charter_id, status="completed")
        manager = _make_manager()
        manager._instance_repository.get_children = MagicMock(
            return_value=[completed]
        )
        manager._instance_repository.get = MagicMock(return_value=completed)

        class _CountingRegistry(CompletionRegistry):
            """Real registry + register/unregister call recording."""

            def __init__(self):
                super().__init__()
                self.register_calls: list[str] = []
                self.unregister_calls: list[str] = []

            def register(self, instance_id):
                self.register_calls.append(instance_id)
                return super().register(instance_id)

            def unregister(self, instance_id):
                self.unregister_calls.append(instance_id)
                return super().unregister(instance_id)

        registry = _CountingRegistry()

        # Pre-seed the registry with a stale buffered completion for the
        # same charter id, with NO waiter parked. ``CompletionRegistry.
        # complete`` writes to ``_buffered`` when no event exists
        # (``completion_registry.py:139-142``).
        registry.complete(charter_id, result=old_content, is_error=False)
        # Sanity: the stale entry is buffered, not yet consumed.
        assert charter_id in registry._buffered
        assert not registry.is_registered(charter_id)

        with patch(
            "daemon.services.completion_registry.get_completion_registry",
            return_value=registry,
        ):
            tools = create_chart_tools(manager, "test-instance-id")

            # Side task: complete the registry AFTER the enqueue fires so
            # the new turn has a completion to capture. Without the W1
            # fix, ``register`` would have drained the OLD entry first,
            # so this side-task complete would be a duplicate no-op.
            async def _side_complete():
                await asyncio.sleep(0.02)
                registry.complete(charter_id, result=new_content, is_error=False)

            side = asyncio.create_task(_side_complete())
            result = await tools[0].coroutine(description="Refine after stale")
            await side

        # The returned content is the NEW turn's completion — NOT the
        # stale buffered OLD entry.
        assert result == new_content, (
            f"W1 fix regression: stale buffered completion leaked; "
            f"got {result!r}, expected {new_content!r}"
        )

        # The OLD content is gone from the registry (drained by the
        # step-3 unregister, not consumed).
        assert charter_id not in registry._buffered
        assert charter_id not in registry._results

        # register fires exactly once (step-3). The post-enqueue
        # re-register is CONDITIONAL on ``is_registered`` being False;
        # in this test the side task is sleeping (0.02s) when the
        # re-register check runs, so the event is still registered and
        # the re-register is skipped.
        assert registry.register_calls == [charter_id], (
            f"register call sequence: {registry.register_calls}"
        )
        # unregister fires exactly twice (step-3 drain + step-7 finally).
        assert registry.unregister_calls == [charter_id, charter_id], (
            f"unregister call sequence: {registry.unregister_calls}"
        )
        # Cleanup is clean post-call.
        assert charter_id not in chart_tools_module._inflight_reuse
        assert not registry.is_registered(charter_id)
