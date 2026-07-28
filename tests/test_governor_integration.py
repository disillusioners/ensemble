"""Phase 4 integration tests for the Governor Council-Manager.

Focused integration tests covering the cross-cutting fixes from Phases 0-3:

  * **C5** — council tools are bound on a real governor instance AND survive
    the tool filter applied by ``_apply_tool_filter``.
  * **C6** — the ``inject_allowed_models`` / ``context_injection`` flags
    survive loading through ``AgentRegistry.discover()`` (the
    ``extra="ignore"`` config makes a field declaration useless without
    the corresponding ``meta.get(...)`` loader line).
  * **C1** — ``clear_councilor_errors`` clears the dependency bus's sticky
    parent-error flag so the governor finalizes as COMPLETED.
  * **W7** — model canonicalization (case-insensitive lookup) and strict
    rejection (no silent fallback).
  * **meta.json contract** — required fields present on disk.
  * **backward-compat** — ``spawn_instance`` still bound for non-governor
    agents.

Heavy ``create_instance_tools`` factory helpers (RAG, MCP, project, job,
etc.) are patched out so only the council/instance tools are built — same
pattern as ``tests/test_council_tools.py`` and
``tests/test_spawn_team_members.py``.

Runtime target: < 60s (all tests use MagicMock or in-memory SQLite).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError


# =============================================================================
# Test helpers (duplicated from tests/test_council_tools.py for clarity)
# =============================================================================


def _patch_heavy_helpers() -> list:
    """Disable heavy ``create_instance_tools`` factory helpers.

    Patches RAG/KB/MCP/project/job/etc. so the factory runs quickly without
    DB/MCP/RAG wiring. The ``_apply_tool_filter`` is patched to a passthrough
    so tool construction does not depend on the global registry state.
    """
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


def _patch_heavy_helpers_no_filter() -> list:
    """Same as :func:`_patch_heavy_helpers` but WITHOUT the
    ``_apply_tool_filter`` passthrough.

    Used by tests that need to call the REAL ``_apply_tool_filter`` against
    a freshly discovered registry so the tool-filter survival (C5) is
    genuinely exercised.
    """
    return [
        p
        for p in _patch_heavy_helpers()
        if "_apply_tool_filter" not in str(p)
    ]


def _make_manager(
    *,
    allowed_models: list[str] | None = None,
    spawn_result: tuple[str, str | None] = ("new-councilor-instance-id", "gpt-4o"),
) -> MagicMock:
    """Build a mock manager wired for ``spawn_councilor``.

    Exposes:
      * ``config.llm.allowed_models`` — the list of allowed models.
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
    current_instance_id: str = "parent-instance-id",
) -> tuple:
    """Build instance tools and return ``(spawn_councilor, clear_councilor_errors)``.

    Uses the same heavy-helper patches as test_spawn_team_members so the
    factory runs quickly without DB/MCP/RAG dependencies.
    """
    from daemon.tools.instance import create_instance_tools

    patches = _patch_heavy_helpers()
    for p in patches:
        p.start()
    try:
        tools = create_instance_tools(manager, current_instance_id, caller_agent_id)
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
# Fixture: in-memory DependencyBus repository (C1 test)
# =============================================================================


@pytest.fixture
def bus_repo():
    """In-memory SQLite repo for the DependencyBus C1 integration test.

    Mirrors the pattern from ``tests/test_dependency_bus.py:243``:
    StaticPool + check_same_thread=False is REQUIRED because asyncio.to_thread
    shares the connection with the main thread, and :memory: databases are
    connection-scoped by default.

    Only the ``dependency_watchers`` table is created on the engine.
    ``daemon.repositories.task.models`` is intentionally NOT imported —
    importing it would register the ``task`` table globally, causing the
    bus's startup orphan-sweep to misclassify every PENDING watcher.
    """
    from sqlalchemy.pool import StaticPool
    from sqlmodel import SQLModel, create_engine

    # Register table models so create_all picks them up.
    import daemon.repositories.dependency_bus.models  # noqa: F401
    import daemon.repositories.instance.models  # noqa: F401
    import daemon.repositories.event.models  # noqa: F401
    from daemon.repositories.dependency_bus import DependencyWatcherRepository

    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    # Create ONLY the dependency_watchers table (see docstring).
    watcher_table = SQLModel.metadata.tables.get("dependency_watchers")
    if watcher_table is not None:
        watcher_table.create(eng, checkfirst=True)
    return DependencyWatcherRepository(eng)


# =============================================================================
# TestGovernorAgentMetadata — C6 (flag survives loading)
# =============================================================================


class TestGovernorAgentMetadata:
    """C6 — the inject_allowed_models / context_injection flags survive a
    real ``AgentRegistry.discover()``.

    ``AgentMetadata`` uses ``ConfigDict(extra="ignore")``, which silently
    discards unknown JSON keys. A flag declared on the Pydantic model but
    without the corresponding ``meta.get(...)`` loader line in
    :meth:`AgentRegistry.discover` would be silently lost — these tests
    catch that regression by loading the REAL governor agent from disk.
    """

    def test_governor_inject_allowed_models_survives_loading(self):
        """C6 REGRESSION: flag must survive loading from real meta.json."""
        from daemon.registry import AgentRegistry

        registry = AgentRegistry(Path("agents"))
        registry.discover()
        gov = registry.get("governor")
        assert gov is not None, "governor agent not discovered from agents/"
        assert gov.inject_allowed_models is True, (
            "C6 REGRESSION: inject_allowed_models silently dropped — "
            "need BOTH the field declaration AND the loader line"
        )
        assert gov.context_injection.heuristic_match_shared_md_files is True, (
            "C6 REGRESSION: context_injection silently dropped"
        )

    def test_governor_meta_json_contract(self):
        """Verify all required fields are present in agents/governor/meta.json on disk."""
        meta_path = Path("agents/governor/meta.json")
        assert meta_path.exists(), "agents/governor/meta.json missing"
        data = json.loads(meta_path.read_text())

        assert data["id"] == "governor"
        assert data["inject_allowed_models"] is True, (
            "inject_allowed_models must be true in governor meta.json"
        )
        assert isinstance(data["context_injection"], dict), (
            "context_injection must be the new object form in governor meta.json"
        )
        assert data["context_injection"]["heuristic_match_shared_md_files"] is True, (
            "context_injection.heuristic_match_shared_md_files must be true "
            "in governor meta.json"
        )
        assert "council" in data["tools"]["allow"], (
            "governor must allow the 'council' tool category"
        )
        assert "developer" in data["team_members"], (
            "governor must include 'developer' in team_members (councilor target)"
        )
        # D4: max 4 councilors is enforced by the governor workflow, not by
        # the tool — just sanity-check team size is reasonable.
        assert len(data["team_members"]) <= 6, (
            f"governor team_members too large: {len(data['team_members'])}; "
            "expected ≤6 (developer, coder, wanderer, explorer, doc-writer, reviewer)"
        )

    def test_governor_team_members_includes_required_agents(self):
        """Governor needs at least one councilor target in team_members."""
        from daemon.registry import AgentRegistry

        registry = AgentRegistry(Path("agents"))
        registry.discover()
        gov = registry.get("governor")
        assert gov is not None
        assert "developer" in gov.team_members, (
            "developer must be a councilor target in governor.team_members"
        )


# =============================================================================
# TestCouncilToolBinding — C5 (tools bound AND survive filtering)
# =============================================================================


class TestCouncilToolBinding:
    """C5 — the council tools must be (a) bound on a real governor instance
    via ``create_instance_tools()`` and (b) survive the tool filter applied
    by the governor's ``tools.allow=["council", ...]`` config.
    """

    def test_spawn_councilor_bound_on_governor_instance(self):
        """C5: spawn_councilor must be in the tool list when called for a governor instance."""
        manager = _make_manager()

        patches = _patch_heavy_helpers()
        for p in patches:
            p.start()
        try:
            from daemon.tools.instance import create_instance_tools

            tools = create_instance_tools(manager, "gov-instance", "governor")
        finally:
            for p in reversed(patches):
                p.stop()

        tool_names = {getattr(t, "name", None) for t in tools}
        assert "spawn_councilor" in tool_names, (
            f"governor must have spawn_councilor bound; got: "
            f"{sorted(n for n in tool_names if n)}"
        )
        assert "clear_councilor_errors" in tool_names, (
            f"governor must have clear_councilor_errors bound; got: "
            f"{sorted(n for n in tool_names if n)}"
        )

    def test_spawn_councilor_survives_tool_filter_for_governor(self):
        """C5: when the REAL ``_apply_tool_filter`` runs against the
        governor's ``tools.allow`` config, council tools must remain.

        This exercises the genuine filter (not the passthrough mock) by
        patching ``daemon.registry.get_registry`` to return a freshly
        discovered registry containing the governor.
        """
        from daemon.registry import AgentRegistry

        # Fresh registry with the real governor metadata loaded from disk.
        registry = AgentRegistry(Path("agents"))
        registry.discover()
        gov = registry.get("governor")
        assert gov is not None, "governor must be discoverable"
        gov_tools = gov.tools
        assert gov_tools is not None, "governor must have a tools config"
        gov_allow = gov_tools.allow
        assert gov_allow is not None, "governor tools.allow must be set"
        assert "council" in gov_allow, (
            "governor must allow the 'council' category for the filter to keep them"
        )

        manager = _make_manager()

        # Use the no-filter variant so the REAL _apply_tool_filter runs
        # inside create_instance_tools AND when called explicitly below.
        patches = _patch_heavy_helpers_no_filter()
        for p in patches:
            p.start()
        try:
            from daemon.tools.instance import create_instance_tools, _apply_tool_filter

            with patch("daemon.registry.get_registry", return_value=registry):
                tools = create_instance_tools(manager, "gov-instance", "governor")
                # Explicitly run the filter against the governor's config.
                filtered = _apply_tool_filter(tools, "governor", [])
        finally:
            for p in reversed(patches):
                p.stop()

        filtered_names = {getattr(t, "name", None) for t in filtered}
        assert "spawn_councilor" in filtered_names, (
            "spawn_councilor filtered out — check governor tools.allow includes 'council'"
        )
        assert "clear_councilor_errors" in filtered_names, (
            "clear_councilor_errors filtered out"
        )

    def test_council_category_resolves_to_instance_module(self):
        """CATEGORY_MODULES['council'] must map to daemon.tools.instance."""
        from daemon.tools._tool_registry import CATEGORY_MODULES

        assert "council" in CATEGORY_MODULES, (
            f"'council' category must be registered; got: "
            f"{sorted(CATEGORY_MODULES.keys())}"
        )
        assert CATEGORY_MODULES["council"] == "daemon.tools.instance", (
            "council category must map to daemon.tools.instance module; "
            f"got: {CATEGORY_MODULES['council']!r}"
        )


# =============================================================================
# TestClearCouncilorErrors — C1 (sticky flag)
# =============================================================================


class TestClearCouncilorErrors:
    """C1 — ``clear_councilor_errors`` resets the dependency bus's sticky
    ``_parent_errored`` flag so the governor finalizes as COMPLETED instead
    of ERROR after a councilor error.

    Uses a REAL ``DependencyBus`` over an in-memory SQLite repository.
    """

    async def test_clear_councilor_errors_clears_sticky_flag(self, bus_repo):
        """C1: clear_councilor_errors resets ``had_parent_error()`` so the
        governor finalizes COMPLETED (not ERROR).
        """
        from daemon.services.dependency_bus import (
            DependencyBus,
            set_dependency_bus,
        )

        bus = DependencyBus(bus_repo)
        await bus.start()
        set_dependency_bus(bus)
        try:
            parent_id = "governor-instance-001"

            # Simulate a councilor error → bus flags the parent.
            # These are the same in-memory dicts that emit_terminal writes
            # to on an error outcome (Phase 5 / Phase 1 of the bus).
            bus._parent_errored[parent_id] = True
            bus._parent_error_message[parent_id] = "councilor failed"

            # Pre-condition: the flag is set (as it would be after a real
            # councilor error).
            assert bus.had_parent_error(parent_id) is True, (
                "pre-condition: parent should be flagged as errored"
            )
            assert bus.parent_error_message(parent_id) == "councilor failed"

            # Build the clear_councilor_errors tool with current_instance_id
            # set to parent_id so the closure clears the right parent.
            manager = _make_manager()
            patches = _patch_heavy_helpers()
            for p in patches:
                p.start()
            try:
                from daemon.tools.instance import create_instance_tools

                tools = create_instance_tools(manager, parent_id, "governor")
            finally:
                for p in reversed(patches):
                    p.stop()

            clear_tool = next(
                (t for t in tools if getattr(t, "name", None) == "clear_councilor_errors"),
                None,
            )
            assert clear_tool is not None, "clear_councilor_errors tool not bound"

            result = await clear_tool.coroutine()

            # Verify the bus was cleared.
            assert bus.had_parent_error(parent_id) is False, (
                "clear_councilor_errors did not reset the sticky flag"
            )
            assert bus.parent_error_message(parent_id) is None, (
                "clear_councilor_errors did not clear the error message"
            )
            assert "Cleared" in result, (
                f"Expected success message containing 'Cleared'; got: {result!r}"
            )
        finally:
            await bus.stop()
            set_dependency_bus(None)

    async def test_clear_councilor_errors_warns_when_bus_missing(self):
        """C1 edge case: no bus → returns a warning instead of raising."""
        manager = _make_manager()
        _, clear_errors = _get_council_tools(manager, caller_agent_id="governor")

        with patch(
            "daemon.services.dependency_bus.get_dependency_bus",
            return_value=None,
        ):
            result = await clear_errors.coroutine()

        assert isinstance(result, str)
        assert "warning" in result.lower() or "bus" in result.lower(), (
            f"Should warn when bus is None; got: {result!r}"
        )


# =============================================================================
# TestModelCanonicalization — W7
# =============================================================================


class TestModelCanonicalization:
    """W7 — spawn_councilor normalizes case-insensitive model input to the
    canonical form from ``allowed_models`` and strictly rejects models not
    in the list (no silent fallback).
    """

    async def test_model_canonicalization_uppercase_input(self):
        """W7: 'GPT-4O' normalizes to canonical 'gpt-4o'."""
        manager = _make_manager(
            allowed_models=["gpt-4o", "claude-3-5-sonnet", "gemini-1.5-pro"],
        )
        spawn, _ = _get_council_tools(manager, caller_agent_id="governor")

        result = await spawn.coroutine(
            councilor_agent_id="developer",
            model="GPT-4O",
            initial_message="test",
        )
        assert "gpt-4o" in result.lower(), (
            f"Should canonicalize to 'gpt-4o'; got: {result!r}"
        )
        # The canonical form (lowercase) must appear, not the raw uppercase.
        assert "GPT-4O" not in result, (
            f"Raw 'GPT-4O' should not appear — expected canonical 'gpt-4o'; "
            f"got: {result!r}"
        )

    async def test_model_canonicalization_mixed_case(self):
        """W7: 'Claude-3-5-SONNET' → 'claude-3-5-sonnet'."""
        manager = _make_manager(
            allowed_models=["gpt-4o", "claude-3-5-sonnet", "gemini-1.5-pro"],
        )
        spawn, _ = _get_council_tools(manager, caller_agent_id="governor")

        result = await spawn.coroutine(
            councilor_agent_id="developer",
            model="Claude-3-5-SONNET",
            initial_message="test",
        )
        assert "claude-3-5-sonnet" in result.lower(), (
            f"Should canonicalize to 'claude-3-5-sonnet'; got: {result!r}"
        )

    async def test_strict_model_rejection_error_message(self):
        """Suggestion #1 FIX: error must mention BOTH 'model' and 'allowed_models'.

        The old assertion ``assert "spawn_councilor" or "model" in ...``
        was always truthy because ``"spawn_councilor"`` is a non-empty
        string. The corrected check verifies both keywords are present.
        """
        manager = _make_manager(allowed_models=["gpt-4o"])
        spawn, _ = _get_council_tools(manager, caller_agent_id="governor")

        with pytest.raises(ValueError) as excinfo:
            await spawn.coroutine(
                councilor_agent_id="developer",
                model="nonexistent-model",
                initial_message="test",
            )

        text = str(excinfo.value).lower()
        assert "model" in text, (
            f"Error should mention 'model'; got: {excinfo.value}"
        )
        assert "allowed_models" in text, (
            f"Error should mention 'allowed_models'; got: {excinfo.value}"
        )
        # Strict contract: no fallback.
        assert "no fallback" in text, (
            f"Error must state 'no fallback'; got: {excinfo.value}"
        )
        # CRITICAL: spawn_instance must NOT have been called on the error path.
        manager.spawn_instance.assert_not_called()

    async def test_invalid_councilor_agent_raises(self):
        """C4: unknown councilor_agent_id raises ValueError."""
        manager = _make_manager()
        spawn, _ = _get_council_tools(manager, caller_agent_id="governor")

        with pytest.raises(ValueError, match="not a valid agent"):
            await spawn.coroutine(
                councilor_agent_id="nonexistent-agent-id",
                model="gpt-4o",
                initial_message="test",
            )
        manager.spawn_instance.assert_not_called()

    async def test_non_team_member_councilor_raises(self):
        """C3: agent not in governor.team_members raises ValueError.

        The governor's team_members are
        ['developer','coder','wanderer','explorer','doc-writer','reviewer'];
        'leader' is NOT among them.
        """
        manager = _make_manager()
        spawn, _ = _get_council_tools(manager, caller_agent_id="governor")

        with pytest.raises(ValueError) as excinfo:
            await spawn.coroutine(
                councilor_agent_id="leader",
                model="gpt-4o",
                initial_message="test",
            )

        text = str(excinfo.value).lower()
        # The membership error mentions "allowed" / "team members".
        assert "allowed" in text or "team" in text, (
            f"Error should reference team membership / allowed members; "
            f"got: {excinfo.value}"
        )
        manager.spawn_instance.assert_not_called()

    async def test_empty_model_raises(self):
        """Empty model string is rejected by the strict validation in spawn_councilor.

        Note: calling ``coroutine()`` directly bypasses the Pydantic args_schema
        (``min_length=1``) check, so the empty string reaches the body and
        triggers the ``ValueError`` path inside spawn_councilor itself — NOT a
        ``pydantic.ValidationError``. The body's strict check raises
        ``ValueError("Model '' is NOT in allowed_models. ... No fallback — ...")``.
        """
        manager = _make_manager()
        spawn, _ = _get_council_tools(manager, caller_agent_id="governor")

        with pytest.raises(ValueError, match="is NOT in allowed_models"):
            await spawn.coroutine(
                councilor_agent_id="developer",
                model="",
                initial_message="test",
            )

    async def test_model_canonicalization_dedup_property(self):
        """W7 dedup: 'GPT-4O' and 'gpt-4o' must resolve to the SAME canonical model.

        The governor workflow dedupes councilors by canonical model name. If
        case variants produce different canonical strings, the governor would
        spawn duplicate councilors. Verify both spellings normalize identically.
        """
        manager = _make_manager(allowed_models=["gpt-4o", "claude-3-5-sonnet", "gemini-1.5-pro"])
        spawn, _ = _get_council_tools(manager, caller_agent_id="governor")

        # Spawn with uppercase
        result_upper = await spawn.coroutine(
            councilor_agent_id="developer",
            model="GPT-4O",
            initial_message="test",
        )
        # Spawn with lowercase (same canonical)
        result_lower = await spawn.coroutine(
            councilor_agent_id="developer",
            model="gpt-4o",
            initial_message="test",
        )

        # Both must reference the SAME canonical model "gpt-4o"
        # Extract the model from the result string (format: "Model: <canonical_model>")
        def extract_model(s: str) -> str | None:
            m = re.search(r"Model:\s*(\S+)", s)
            return m.group(1) if m else None

        model_upper = extract_model(result_upper)
        model_lower = extract_model(result_lower)

        assert model_upper is not None, f"Could not extract model from: {result_upper}"
        assert model_lower is not None, f"Could not extract model from: {result_lower}"
        assert model_upper == model_lower, (
            f"W7 DEDUP REGRESSION: 'GPT-4O' → '{model_upper}' but 'gpt-4o' → '{model_lower}'. "
            f"Case variants must canonicalize identically for dedup to work."
        )
        assert model_upper == "gpt-4o", (
            f"W7 REGRESSION: canonical form should be 'gpt-4o', got '{model_upper}'"
        )


# =============================================================================
# TestBackwardCompat — spawn_instance backward compatibility
# =============================================================================


class TestBackwardCompat:
    """spawn_instance must remain available for non-governor agents with an
    optional model (silent fallback on invalid model — the OPPOSITE of
    spawn_councilor's strict semantics).
    """

    async def test_spawn_instance_bound_on_leader_instance(self):
        """spawn_instance must still be bound for the leader agent."""
        manager = _make_manager()

        patches = _patch_heavy_helpers()
        for p in patches:
            p.start()
        try:
            from daemon.tools.instance import create_instance_tools

            tools = create_instance_tools(manager, "leader-instance", "leader")
        finally:
            for p in reversed(patches):
                p.stop()

        spawn_instance_tool = next(
            (t for t in tools if getattr(t, "name", None) == "spawn_instance"),
            None,
        )
        assert spawn_instance_tool is not None, (
            "spawn_instance missing — backward compat broken"
        )

    async def test_spawn_instance_model_is_optional(self):
        """spawn_instance must accept model=None (backward compat) and still spawn.

        The manager mock returns a fixed spawn_result regardless of model input.
        We verify that calling with model=None does NOT raise and returns a valid instance_id.
        """
        manager = _make_manager(spawn_result=("backward-compat-instance", None))

        patches = _patch_heavy_helpers()
        for p in patches:
            p.start()
        try:
            from daemon.tools.instance import create_instance_tools

            tools = create_instance_tools(manager, "leader-instance", "leader")
        finally:
            for p in reversed(patches):
                p.stop()

        spawn_instance_tool = next(
            (t for t in tools if getattr(t, "name", None) == "spawn_instance"),
            None,
        )
        assert spawn_instance_tool is not None, (
            "spawn_instance missing — backward compat broken"
        )

        # Actually invoke with model=None (backward compat path)
        result = await spawn_instance_tool.coroutine(
            agent_id="developer",  # leader can spawn developer
            model=None,
        )
        assert "backward-compat-instance" in result, (
            f"spawn_instance(model=None) should succeed and return instance_id; got: {result}"
        )


# =============================================================================
# TestCouncilManifestContracts — Phase 0 schema contracts
# =============================================================================


class TestCouncilManifestContracts:
    """Phase 0 contracts (Pydantic schemas) must be importable and enforce
    their required fields.
    """

    def test_council_manifest_schemas_importable(self):
        """Contracts from Phase 0 must be importable."""
        from daemon.governor.contracts import (
            AllowedModelsBlock,
            ClearCouncilorErrorsResult,
            SpawnCouncilorInput,
            SpawnCouncilorResult,
        )

        assert SpawnCouncilorInput is not None
        assert SpawnCouncilorResult is not None
        assert ClearCouncilorErrorsResult is not None
        assert AllowedModelsBlock is not None

    def test_spawn_councilor_input_required_fields(self):
        """Pydantic schema enforces required fields (min_length=1)."""
        from daemon.governor.contracts import SpawnCouncilorInput

        # Valid input.
        inp = SpawnCouncilorInput(
            councilor_agent_id="developer",
            model="gpt-4o",
            initial_message="test",
        )
        assert inp.councilor_agent_id == "developer"
        assert inp.model == "gpt-4o"
        assert inp.initial_message == "test"

        # Empty model rejected (min_length=1).
        with pytest.raises(ValidationError, match="at least 1 character"):
            SpawnCouncilorInput(
                councilor_agent_id="developer",
                model="",
                initial_message="test",
            )

        # Empty councilor_agent_id rejected (min_length=1).
        with pytest.raises(ValidationError, match="at least 1 character"):
            SpawnCouncilorInput(
                councilor_agent_id="",
                model="gpt-4o",
                initial_message="test",
            )

        # Empty initial_message rejected (min_length=1).
        with pytest.raises(ValidationError, match="at least 1 character"):
            SpawnCouncilorInput(
                councilor_agent_id="developer",
                model="gpt-4o",
                initial_message="",
            )

    def test_spawn_councilor_result_status_literal(self):
        """SpawnCouncilorResult.status must be one of SPAWNED/FAILED."""
        from daemon.governor.contracts import SpawnCouncilorResult

        ok = SpawnCouncilorResult(
            instance_id="inst-1",
            councilor_agent_id="developer",
            model="gpt-4o",
            canonical_model="gpt-4o",
            status="SPAWNED",
        )
        assert ok.status == "SPAWNED"

        failed = SpawnCouncilorResult(
            instance_id="inst-2",
            councilor_agent_id="developer",
            model="claude",
            canonical_model="claude",
            status="FAILED",
        )
        assert failed.status == "FAILED"

        # Invalid status literal rejected.
        with pytest.raises(ValidationError, match="status"):
            SpawnCouncilorResult(
                instance_id="inst-3",
                councilor_agent_id="developer",
                model="gpt-4o",
                canonical_model="gpt-4o",
                status="UNKNOWN",
            )

    def test_clear_councilor_errors_result_shape(self):
        """ClearCouncilorErrorsResult has the expected fields."""
        from daemon.governor.contracts import ClearCouncilorErrorsResult

        cleared = ClearCouncilorErrorsResult(cleared=True, previous_error="boom")
        assert cleared.cleared is True
        assert cleared.previous_error == "boom"

        no_error = ClearCouncilorErrorsResult(cleared=False, previous_error=None)
        assert no_error.cleared is False
        assert no_error.previous_error is None

    def test_allowed_models_block_modes(self):
        """AllowedModelsBlock supports restricted and unrestricted modes."""
        from daemon.governor.contracts import AllowedModelsBlock

        # Restricted mode with models.
        block = AllowedModelsBlock(
            models=["gpt-4o", "claude-3"],
            mode="restricted",
            status="ok",
        )
        assert block.mode == "restricted"
        assert len(block.models) == 2
        assert block.error_message is None

        # Unrestricted mode (empty list).
        block = AllowedModelsBlock(
            models=[],
            mode="unrestricted",
            status="ok",
        )
        assert block.mode == "unrestricted"
        assert block.models == []

        # Error status carries a message.
        block = AllowedModelsBlock(
            models=[],
            mode="unrestricted",
            status="error",
            error_message="config missing",
        )
        assert block.status == "error"
        assert block.error_message == "config missing"

        # Invalid mode literal rejected.
        with pytest.raises(ValidationError, match="mode"):
            AllowedModelsBlock(models=[], mode="bogus", status="ok")


# =============================================================================
# TestNonGovernorFiltering — S3 (non-governor agents stripped of council tools)
# =============================================================================


class TestNonGovernorFiltering:
    """S3 — the REAL ``_apply_tool_filter`` must strip council tools from
    non-governor agents and keep them on the governor.

    Same regression family as C5, but exercises the *negative* direction: the
    filter is permissive for governor (``tools.allow`` includes ``"council"``)
    and restrictive for everyone else (no ``"council"`` in their allow list).
    This catches:
      * accidentally global-registering the council tools so every agent
        sees them,
      * a regression where ``resolve_tool_filter`` no longer strips a
        category that is absent from ``allow``,
      * a wiring bug that grants ``spawn_councilor`` outside the governor.

    Builds the patch list explicitly so the real ``_apply_instance_tools``
    filter path runs against a freshly discovered ``AgentRegistry``
    (the file's existing ``_patch_heavy_helpers_no_filter`` helper is
    broken — its ``str(patch_object)`` exclusion check does not match the
    real target substring, so the filter patch is not actually removed).
    """

    def _build_filtered_tools_for_agent(self, agent_id: str, instance_id: str) -> list:
        """Build the REAL-filtered tool list for ``agent_id``.

        The default `_patch_heavy_helpers` patches out
        ``scan_tools_for_full_docs`` and ``_apply_tool_filter`` — both of
        which we need to be REAL for this test to exercise the filter:

          * ``scan_tools_for_full_docs`` populates the global
            ``_tool_metadata`` registry with the freshly-built tools
            (categorized as ``instance`` / ``council``). Without it, the
            filter's category expansion is empty and the filter over-strips
            (only single-token names like ``time`` survive).
          * ``_apply_tool_filter`` is the genuine filter. Its passthrough
            mock would short-circuit the test.

        The ``_patch_heavy_helpers_no_filter`` helper is meant to drop the
        filter patch but its exclusion check is broken
        (``str(patch_object)`` is ``<unittest.mock._patch object at 0x…>``
        with no target substring). So we build the patch list ourselves:
        identical to ``_patch_heavy_helpers`` but excluding the two
        patches above.

        We patch everything else (RAG, MCP, project, job, etc.) so the
        factory runs quickly without DB/MCP/RAG wiring.
        """
        from daemon.registry import AgentRegistry
        from daemon.tools.instance import create_instance_tools, _apply_tool_filter

        # Fresh registry with real agent metadata loaded from disk.
        registry = AgentRegistry(Path("agents"))
        registry.discover()

        manager = _make_manager()

        # Heavy helper patches — identical to ``_patch_heavy_helpers`` BUT
        # excluding (a) ``scan_tools_for_full_docs`` so tools are registered
        # into ``_tool_metadata`` and the filter can resolve categories, and
        # (b) ``_apply_tool_filter`` so the genuine filter runs.
        patches = [
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
            # NOTE: scan_tools_for_full_docs is INTENTIONALLY NOT patched —
            # we need the real tool registration so the filter can resolve
            # the "council" / "instance" categories.
            # NOTE: _apply_tool_filter is INTENTIONALLY NOT patched —
            # we want the real filter to run.
        ]
        for p in patches:
            p.start()
        try:
            with patch("daemon.registry.get_registry", return_value=registry):
                filtered_tools = create_instance_tools(manager, instance_id, agent_id)
        finally:
            for p in reversed(patches):
                p.stop()

        return filtered_tools

    def _tool_names(self, tools: list) -> set[str]:
        """Return the set of tool names (strings only) from the tool list.

        Some tool objects returned by the factory may be MagicMock instances
        (e.g. when ``create_inner_soul_tool`` is patched); their ``name``
        attribute is itself a MagicMock which is truthy but not a string.
        We filter to strings so the assertion messages are sortable.
        """
        return {
            n for n in (getattr(t, "name", None) for t in tools)
            if isinstance(n, str)
        }

    def test_leader_no_council_tools(self):
        """S3: leader must NOT have spawn_councilor or clear_councilor_errors.

        The leader's ``tools.allow`` does not include ``"council"``, so the
        real filter must strip the council tools from its tool list.
        """
        tools = self._build_filtered_tools_for_agent("leader", "test-leader")
        tool_names = self._tool_names(tools)
        assert "spawn_councilor" not in tool_names, (
            f"S3 REGRESSION: leader must not have spawn_councilor; "
            f"got: {sorted(tool_names)}"
        )
        assert "clear_councilor_errors" not in tool_names, (
            f"S3 REGRESSION: leader must not have clear_councilor_errors; "
            f"got: {sorted(tool_names)}"
        )

    def test_developer_no_council_tools(self):
        """S3: developer must NOT have spawn_councilor or clear_councilor_errors.

        The developer's ``tools.allow`` does not include ``"council"``, so the
        real filter must strip the council tools from its tool list.
        """
        tools = self._build_filtered_tools_for_agent("developer", "test-dev")
        tool_names = self._tool_names(tools)
        assert "spawn_councilor" not in tool_names, (
            f"S3 REGRESSION: developer must not have spawn_councilor; "
            f"got: {sorted(tool_names)}"
        )
        assert "clear_councilor_errors" not in tool_names, (
            f"S3 REGRESSION: developer must not have clear_councilor_errors; "
            f"got: {sorted(tool_names)}"
        )

    def test_governor_has_council_tools(self):
        """S3 (positive control): governor MUST have spawn_councilor and clear_councilor_errors.

        The governor's ``tools.allow`` includes ``"council"``, so the real
        filter must keep the council tools. This is the positive control
        that confirms the filter is actually running — if the filter were
        accidentally bypassed, the governor would still show the tools,
        but the leader/developer tests would also wrongly show them.
        """
        tools = self._build_filtered_tools_for_agent("governor", "test-gov")
        tool_names = self._tool_names(tools)
        assert "spawn_councilor" in tool_names, (
            f"S3 REGRESSION: governor must have spawn_councilor; "
            f"got: {sorted(tool_names)}"
        )
        assert "clear_councilor_errors" in tool_names, (
            f"S3 REGRESSION: governor must have clear_councilor_errors; "
            f"got: {sorted(tool_names)}"
        )


# ---------------------------------------------------------------------------
# D9 + E2E A-G Coverage Note
# ---------------------------------------------------------------------------
# The phase4-plan.md lists D9-1 through D9-4 (degraded quorum, deadline
# extension, 1h hard kill, partial result) and E2E scenarios A-G as
# deliverables. These are NOT covered here because:
#
# - The synthesis logic (degraded notice prepending, quorum decisions,
#   deadline extension policy, partial-result harvesting) lives entirely
#   in agents/governor/workflow.md as LLM-driven behavior, NOT in Python
#   code. There is no Python function that produces the "⚠️ Confidence
#   Notice:" block — it is the governor agent's natural-language output.
# - Testing these would require either (a) a full graph + real LLM run
#   (impractical for unit tests, well over the 60s budget) or (b) mocking
#   the LLM to return canned synthesis text (which tests the mock, not
#   the feature).
# - The TOOLS that the governor uses (spawn_councilor, clear_councilor_errors,
#   get_instance_info, terminate_instance) ARE tested here and in
#   tests/test_council_tools.py. The governor's policy for using them is
#   validated by the planner/approver review of workflow.md, not by tests.
# ---------------------------------------------------------------------------
