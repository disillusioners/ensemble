"""Tests for the Phase 2 ``council`` tool category.

Covers the two new tools defined inside ``create_instance_tools()`` in
``daemon/tools/instance.py``:

  * ``spawn_councilor`` — strict-validation councilor spawn (REQUIRED
    councilor_agent_id + model, RAISES on invalid model — no silent
    fallback; canonical model normalization via W7).
  * ``clear_councilor_errors`` — clears the dependency bus's sticky
    parent-error flag so the governor can finalize as COMPLETED (C1/D7).

The tests use the same factory-patching pattern as
``tests/test_spawn_team_members.py``: heavy factory helpers (RAG, MCP,
project, job, etc.) are patched out so only the council/instance tools
are built.

Critical assertions (per Phase 2 plan):
  * C3 — ``_check_team_membership`` returns str|None; check return value.
  * C4 — ``resolve_to_id`` returns str|None; check for None.
  * C2 — ``manager.config.llm.allowed_models`` is the access path
         (NOT ``manager._config``).
  * W6 — pass canonical_model (not raw) to ``manager.spawn_instance``.
  * W7 — case-insensitive lookup normalizes to canonical name.
  * Integration: governor's ``create_instance_tools()`` output contains
    both council tools in the ``council`` category.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


# =============================================================================
# Test helpers (mirrors tests/test_spawn_team_members.py)
# =============================================================================


def _patch_heavy_helpers() -> list:
    """Disable heavy ``create_instance_tools`` factory helpers so the test
    runs without RAG/KB/MCP/project/job/etc. wiring."""
    return [
        patch("daemon.tools.instance.is_rag_enabled", return_value=False),
        patch("daemon.tools.instance.create_rag_tools", return_value=[]),
        patch("daemon.tools.instance.create_knowledge_tools", return_value=[]),
        patch("daemon.tools.instance.create_inner_soul_tool", return_value=MagicMock()),
        patch("daemon.tools.instance.create_access_memory_tool", return_value=MagicMock()),
        patch("daemon.tools.instance.create_project_tools", return_value=[]),
        patch("daemon.tools.instance.create_job_tools_if_available", return_value=[]),
        patch("daemon.tools.instance.create_help_tool", return_value=MagicMock()),
        patch("daemon.tools.instance.create_critical_notes_tools", return_value=[]),
        patch("daemon.tools.instance.create_project_history_tools", return_value=[]),
        patch("daemon.tools.instance.create_opencode_tools", return_value=[]),
        patch("daemon.tools.instance.create_db_tools", return_value=[]),
        patch("daemon.tools.instance.create_infra_tools", return_value=[]),
        patch("daemon.tools.instance.create_context_tools", return_value=[]),
        patch("daemon.tools.instance.create_chart_tools", return_value=[]),
        patch("daemon.tools.instance._load_mcp_tools", return_value=[]),
        patch("daemon.tools.instance.scan_tools_for_full_docs"),
        patch("daemon.tools.instance._apply_tool_filter", side_effect=lambda tools, *a, **kw: tools),
    ]


def _make_manager(
    *,
    allowed_models: list[str] | None = None,
    spawn_result: tuple[str, str | None] = ("new-councilor-instance-id", "gpt-4o"),
) -> MagicMock:
    """Build a mock manager wired for ``spawn_councilor``.

    The manager exposes:
      * ``config.llm.allowed_models`` — list of allowed models (defaults to
        a small set so canonicalization has something to work with).
      * ``_lifecycle_service._resolve_model_override`` — mimics the real
        lifecycle: case-insensitive match against allowed_models.
      * ``spawn_instance`` — sync MagicMock returning
        ``(instance_id, validated_model_override)``.
    """
    if allowed_models is None:
        allowed_models = ["gpt-4o", "claude-3-5-sonnet", "gemini-1.5-pro"]

    manager = MagicMock()
    manager.config = MagicMock()
    manager.config.llm = MagicMock()
    manager.config.llm.allowed_models = list(allowed_models)

    # Lifecycle's _resolve_model_override mimics the real implementation:
    # case-insensitive exact match; None on miss or whitespace-only.
    def _resolve(model: str | None) -> str | None:
        if not model or not str(model).strip():
            return None
        candidate = str(model).strip()
        if not allowed_models:
            return candidate
        lowered = candidate.lower()
        for entry in allowed_models:
            if entry and entry.lower() == lowered:
                return candidate
        return None

    manager._lifecycle_service = MagicMock()
    manager._lifecycle_service._resolve_model_override = MagicMock(side_effect=_resolve)

    manager.spawn_instance = MagicMock(return_value=spawn_result)
    return manager


def _get_council_tools(
    manager: MagicMock,
    caller_agent_id: str,
) -> tuple:
    """Build instance tools and return ``(spawn_councilor, clear_councilor_errors)``.

    Uses the same heavy-helper patches as test_spawn_team_members so the
    factory runs quickly without DB/MCP/RAG dependencies. Returned objects
    are LangChain StructuredTool instances (typed as bare tuple to avoid
    importing the class for type-checking; runtime dispatch via
    ``.coroutine`` works regardless).
    """
    from daemon.tools.instance import create_instance_tools

    patches = _patch_heavy_helpers()
    for p in patches:
        p.start()
    try:
        tools = create_instance_tools(manager, "parent-instance-id", caller_agent_id)
    finally:
        for p in reversed(patches):
            p.stop()

    spawn_councilor = None
    clear_errors = None
    for t in tools:
        name = getattr(t, "name", None)
        if name == "spawn_councilor":
            spawn_councilor = t
        elif name == "clear_councilor_errors":
            clear_errors = t

    if spawn_councilor is None:
        raise RuntimeError(
            "spawn_councilor tool not found in create_instance_tools output; "
            f"got {[getattr(t, 'name', None) for t in tools]}"
        )
    if clear_errors is None:
        raise RuntimeError(
            "clear_councilor_errors tool not found in create_instance_tools output; "
            f"got {[getattr(t, 'name', None) for t in tools]}"
        )
    return spawn_councilor, clear_errors


# =============================================================================
# Tests for spawn_councilor
# =============================================================================


class TestSpawnCouncilor:
    """Validation behavior for the strict-validation ``spawn_councilor`` tool."""

    async def test_invalid_councilor_agent_id_raises_value_error(self):
        """C4: ``resolve_to_id`` returns None → raise ValueError.

        Unknown ``councilor_agent_id`` (typo / path that doesn't resolve
        to a registered agent) must raise ValueError before any DB work.
        """
        manager = _make_manager()
        spawn_councilor, _ = _get_council_tools(manager, caller_agent_id="governor")

        with pytest.raises(ValueError) as excinfo:
            await spawn_councilor.coroutine(
                councilor_agent_id="ghost_agent_xyz_does_not_exist",
                model="gpt-4o",
                initial_message="please help",
            )

        # The error must mention the bad agent_id and the registry keyword.
        assert "ghost_agent_xyz_does_not_exist" in str(excinfo.value), (
            f"Error should mention the bad id; got: {excinfo.value}"
        )
        assert "registry" in str(excinfo.value).lower(), (
            f"Error should reference the registry; got: {excinfo.value}"
        )
        # CRITICAL: manager.spawn_instance must NOT have been called.
        manager.spawn_instance.assert_not_called()

    async def test_non_team_member_agent_raises_value_error(self):
        """C3: ``_check_team_membership`` returns str → raise ValueError(err).

        The caller is the governor (whose team_members = ['developer',
        'coder', 'wanderer', 'explorer', 'doc-writer', 'reviewer']) and
        the requested agent 'leader' is NOT in that list — must be
        rejected as 'not allowed to spawn'.
        """
        manager = _make_manager()
        spawn_councilor, _ = _get_council_tools(manager, caller_agent_id="governor")

        with pytest.raises(ValueError) as excinfo:
            await spawn_councilor.coroutine(
                councilor_agent_id="leader",  # NOT in governor.team_members
                model="gpt-4o",
                initial_message="please help",
            )

        err = str(excinfo.value)
        assert "not allowed to spawn" in err, (
            f"Should reject non-team member; got: {err}"
        )
        # The error must surface the caller's team_members list.
        assert "developer" in err, (
            f"Error should mention an allowed team member (developer); got: {err}"
        )
        # CRITICAL: manager.spawn_instance must NOT have been called.
        manager.spawn_instance.assert_not_called()

    async def test_invalid_model_raises_value_error_no_fallback(self):
        """Strict model validation: invalid model → raise ValueError.

        When ``allowed_models`` is non-empty and the requested model is
        NOT in it, ``spawn_councilor`` MUST raise ValueError. There is
        NO silent fallback to the default model — that is the strict
        contract (the opposite of the regular ``spawn_instance``
        behavior, which silently falls back).
        """
        manager = _make_manager(
            allowed_models=["gpt-4o", "claude-3-5-sonnet", "gemini-1.5-pro"],
        )
        spawn_councilor, _ = _get_council_tools(manager, caller_agent_id="governor")

        with pytest.raises(ValueError) as excinfo:
            await spawn_councilor.coroutine(
                councilor_agent_id="developer",  # valid team-member
                model="invalid-model-not-in-allowed",
                initial_message="please help",
            )

        err = str(excinfo.value)
        # Must mention the rejected model and list allowed alternatives.
        assert "invalid-model-not-in-allowed" in err, (
            f"Error should mention the rejected model; got: {err}"
        )
        assert "allowed_models" in err.lower() or "valid models" in err.lower(), (
            f"Error should reference allowed_models / valid models; got: {err}"
        )
        # Must explicitly state there is no fallback (strict semantics).
        assert "no fallback" in err.lower(), (
            f"Error must explicitly state 'no fallback'; got: {err}"
        )
        # CRITICAL: manager.spawn_instance must NOT have been called.
        manager.spawn_instance.assert_not_called()

    async def test_valid_agent_and_model_succeeds(self):
        """Happy path: governor spawns 'developer' with 'gpt-4o' → success."""
        manager = _make_manager(
            allowed_models=["gpt-4o", "claude-3-5-sonnet", "gemini-1.5-pro"],
            spawn_result=("new-developer-councilor-id", "gpt-4o"),
        )
        spawn_councilor, _ = _get_council_tools(manager, caller_agent_id="governor")

        result = await spawn_councilor.coroutine(
            councilor_agent_id="developer",
            model="gpt-4o",
            initial_message="please review this code",
        )

        assert isinstance(result, str), f"Expected str, got {type(result)}"
        assert "new-developer-councilor-id" in result, (
            f"Should include the new instance_id; got: {result!r}"
        )
        assert "Successfully spawned councilor instance" in result
        assert "developer" in result
        assert "gpt-4o" in result
        # manager.spawn_instance must have been called exactly once.
        manager.spawn_instance.assert_called_once()
        call_kwargs = manager.spawn_instance.call_args.kwargs
        # W6: canonical_model passed (not the raw caller's spelling). Since
        # the caller already used the canonical form 'gpt-4o', this is a
        # direct pass-through.
        assert call_kwargs["model"] == "gpt-4o", (
            f"manager.spawn_instance should receive canonical model; "
            f"got: {call_kwargs['model']!r}"
        )
        assert call_kwargs["agent_id"] == "developer"
        assert call_kwargs["parent_id"] == "parent-instance-id"

    async def test_model_canonicalization_normalizes_casing(self):
        """W7: 'GPT-4O' is normalized to canonical 'gpt-4o' from allowed_models.

        Without canonicalization, two councilors with capitalizations of
        the same model would silently coexist. spawn_councilor normalizes
        via case-insensitive lookup in allowed_models before spawn.
        """
        manager = _make_manager(
            allowed_models=["gpt-4o", "claude-3-5-sonnet", "gemini-1.5-pro"],
            spawn_result=("instance-id", "gpt-4o"),
        )
        spawn_councilor, _ = _get_council_tools(manager, caller_agent_id="governor")

        result = await spawn_councilor.coroutine(
            councilor_agent_id="developer",
            model="GPT-4O",  # caller uses mixed casing
            initial_message="hi",
        )

        assert isinstance(result, str)
        # The canonical 'gpt-4o' appears in the returned message — NOT 'GPT-4O'.
        assert "gpt-4o" in result, (
            f"Result should include the canonical 'gpt-4o'; got: {result!r}"
        )
        # W6: manager.spawn_instance received the canonical model, NOT the raw.
        manager.spawn_instance.assert_called_once()
        call_kwargs = manager.spawn_instance.call_args.kwargs
        assert call_kwargs["model"] == "gpt-4o", (
            f"W6: manager.spawn_instance should receive canonical 'gpt-4o'; "
            f"got: {call_kwargs['model']!r}"
        )


# =============================================================================
# Tests for clear_councilor_errors
# =============================================================================


class TestClearCouncilorErrors:
    """Behavior of the sticky-parent-error flag clearer."""

    async def test_no_dependency_bus_returns_warning(self):
        """When ``get_dependency_bus()`` returns None, the tool returns a warning.

        The bus is a singleton. If it hasn't been wired up (e.g. early
        boot or test environment without ``set_dependency_bus``), the
        tool must surface a warning rather than raising.
        """
        manager = _make_manager()
        _, clear_errors = _get_council_tools(manager, caller_agent_id="governor")

        with patch(
            "daemon.services.dependency_bus.get_dependency_bus",
            return_value=None,
        ):
            result = await clear_errors.coroutine()

        assert isinstance(result, str)
        assert "Warning" in result or "warning" in result.lower(), (
            f"Should warn when bus is None; got: {result!r}"
        )
        assert "bus" in result.lower() or "dependency" in result.lower()

    async def test_clears_flag_for_current_instance_id(self):
        """Happy path: dependency bus exists → clears the flag for
        ``current_instance_id`` (the governor's own instance ID captured
        in the closure).
        """
        manager = _make_manager()
        _, clear_errors = _get_council_tools(manager, caller_agent_id="governor")

        # Stub bus with a MagicMock for clear_parent_error.
        stub_bus = MagicMock()
        stub_bus.clear_parent_error = MagicMock(return_value=None)

        with patch(
            "daemon.services.dependency_bus.get_dependency_bus",
            return_value=stub_bus,
        ):
            result = await clear_errors.coroutine()

        # The tool MUST call clear_parent_error with the current_instance_id
        # from the closure scope — not the requested agent_id or anything else.
        stub_bus.clear_parent_error.assert_called_once_with("parent-instance-id")
        assert isinstance(result, str)
        assert "Cleared" in result, f"Should confirm clearing; got: {result!r}"
        # The returned string should include a preview of the instance id.
        assert "parent-i" in result, (
            f"Result should include the instance-id preview; got: {result!r}"
        )

    async def test_bus_exception_returns_warning_not_raises(self):
        """If ``clear_parent_error`` raises, the tool returns a warning
        string rather than propagating the exception — clearing is
        best-effort and the governor must not crash on a bus hiccup.
        """
        manager = _make_manager()
        _, clear_errors = _get_council_tools(manager, caller_agent_id="governor")

        stub_bus = MagicMock()
        stub_bus.clear_parent_error = MagicMock(
            side_effect=RuntimeError("simulated bus error"),
        )

        with patch(
            "daemon.services.dependency_bus.get_dependency_bus",
            return_value=stub_bus,
        ):
            result = await clear_errors.coroutine()

        assert isinstance(result, str)
        assert "Warning" in result or "warning" in result.lower(), (
            f"Should warn when bus.clear_parent_error raises; got: {result!r}"
        )
        assert "simulated bus error" in result, (
            f"Should surface the underlying error text; got: {result!r}"
        )


# =============================================================================
# Integration test (C5)
# =============================================================================


class TestGovernorCouncilToolsWiring:
    """End-to-end wiring tests for the ``council`` category.

    C5 — the new tools MUST be defined inside ``create_instance_tools()``
    as closures so the governor (whose ``tools.allow`` includes
    ``"council"``) actually has access to them.
    """

    def test_governor_has_council_tools_bound(self):
        """``create_instance_tools(manager, instance_id, agent_id="governor")``
        must produce a tool list that contains both ``spawn_councilor``
        and ``clear_councilor_errors``.

        This is the integration-level proof that the closures are
        registered (not just module-level definitions that never get
        bound to the governor's tool surface).
        """
        manager = _make_manager()

        patches = _patch_heavy_helpers()
        for p in patches:
            p.start()
        try:
            from daemon.tools.instance import create_instance_tools

            tools = create_instance_tools(
                manager,
                "governor-instance-id",
                "governor",
            )
        finally:
            for p in reversed(patches):
                p.stop()

        tool_names = {getattr(t, "name", None) for t in tools}
        assert "spawn_councilor" in tool_names, (
            f"governor must have spawn_councilor bound; "
            f"got tool names: {sorted(n for n in tool_names if n)}"
        )
        assert "clear_councilor_errors" in tool_names, (
            f"governor must have clear_councilor_errors bound; "
            f"got tool names: {sorted(n for n in tool_names if n)}"
        )

    def test_council_category_in_registry_module_map(self):
        """The ``council`` category must be in ``CATEGORY_MODULES`` so the
        ``tools.allow=["council"]`` filter resolves to both tools.

        Pinning this prevents a future refactor from dropping the
        registration and silently breaking the governor's tool surface.
        """
        from daemon.tools._tool_registry import CATEGORY_MODULES

        assert "council" in CATEGORY_MODULES, (
            f"'council' must be in CATEGORY_MODULES; "
            f"got: {sorted(CATEGORY_MODULES.keys())}"
        )
        # The mapping points to the module where the tools live (the
        # ``daemon/tools/instance.py`` factory's create_instance_tools).
        assert CATEGORY_MODULES["council"] == "daemon.tools.instance", (
            f"'council' must map to daemon.tools.instance; "
            f"got: {CATEGORY_MODULES['council']!r}"
        )

    def test_council_tools_have_council_category_marker(self):
        """Both tools must carry the ``_tool_category = 'council'`` attribute
        on the StructuredTool wrapper so ``scan_tools_for_full_docs``
        registers them under the council category (C5 + tool-filter wiring).

        Decorator order is ``@register_tool_category("council")`` OUTSIDE
        ``@tool(...)``, so the attribute is set on the StructuredTool
        wrapper itself (NOT on the underlying ``tool.func``/``tool.coroutine``).
        """
        manager = _make_manager()
        spawn_councilor, clear_errors = _get_council_tools(
            manager, caller_agent_id="governor"
        )

        assert getattr(spawn_councilor, "_tool_category", None) == "council", (
            f"spawn_councilor must have _tool_category='council'; "
            f"got: {getattr(spawn_councilor, '_tool_category', None)!r}"
        )
        assert getattr(clear_errors, "_tool_category", None) == "council", (
            f"clear_councilor_errors must have _tool_category='council'; "
            f"got: {getattr(clear_errors, '_tool_category', None)!r}"
        )
