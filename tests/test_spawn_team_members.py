"""Tests for the ``team_members`` authorization gate on ``spawn_instance``.

The gate is enforced inside the ``spawn_instance`` tool (in
``daemon/tools/instance.py``) BEFORE any DB transaction or instance creation
work. It reads the caller's ``meta.json`` ``team_members`` list and rejects
the spawn with a clear ERROR string when:

  1. The caller agent has no ``team_members`` list (deny-by-default).
  2. The caller agent has an empty ``team_members`` list (deny-by-default).
  3. The requested ``agent_id`` (canonicalized via the registry) is NOT in
     the caller's ``team_members`` list (also canonicalized).

The tests below cover all three rejection paths AND the happy path. They
also cover alias-bypass prevention (e.g. ``"coder"`` for ``"developer"``)
on BOTH sides of the comparison.

These tests are pure authorization logic — no DB transactions are touched
because the gate runs before ``manager.spawn_instance(...)``. The
verification strategy is:

  * Inject a ``MagicMock`` manager whose ``spawn_instance`` records the
    call. If the gate works, the manager is NOT invoked on rejection paths.
  * Build the ``spawn_instance`` tool via ``create_instance_tools`` with
    heavy factory helpers patched out (same pattern as
    ``tests/tools/test_send_message_status_guard.py``).
  * Invoke ``tool.coroutine(agent_id=...)`` to call the async function.

Both SQLite and PostgreSQL are supported because the validation is pure
logic that never touches the DB.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


def _patch_heavy_helpers():
    """Return a stack of ``unittest.mock.patch`` context managers that disable
    the heavy ``create_instance_tools`` factory helpers (RAG, knowledge, MCP,
    project, job, mother, OpenCode, DB, infra, context) so only the
    instance-management tools (spawn/send/terminate/list/get) are built.
    """
    from unittest.mock import patch

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
        patch("daemon.tools.instance._load_mcp_tools", return_value=[]),
        patch("daemon.tools.instance.scan_tools_for_full_docs"),
        patch("daemon.tools.instance._apply_tool_filter", side_effect=lambda tools, *a, **kw: tools),
    ]


def _make_manager(*, spawn_result=("new-instance-id-12345", None)) -> MagicMock:
    """Build a mock manager wired for ``spawn_instance``.

    The manager exposes:
      * ``_lifecycle_service._format_model_fallback_notice`` — returns "".
      * ``spawn_instance`` — synchronous MagicMock returning a successful
        ``(instance_id, validated_model_override)`` tuple. NOTE:
        ``manager.spawn_instance`` is the SYNC lifecycle method (not the
        async tool) — see ``daemon/services/instance_lifecycle.py``.
      * ``_instance_repository.get`` — used for project_id auto-inherit;
        returns None to keep the test deterministic.
    """
    manager = MagicMock()
    manager._lifecycle_service = MagicMock()
    manager._lifecycle_service._format_model_fallback_notice = MagicMock(return_value="")

    manager.spawn_instance = MagicMock(return_value=spawn_result)

    # _get_instance_project_id calls manager._instance_repository.get; return None
    # so the test doesn't need a real project store.
    manager._instance_repository = MagicMock()
    manager._instance_repository.get = MagicMock(return_value=None)
    return manager


def _get_spawn_instance_tool(manager: MagicMock, caller_agent_id: str):
    """Build the instance tools and return the ``spawn_instance`` tool object.

    The tool object exposes a ``.coroutine`` attribute that is the actual
    async function decorated by ``@tool``. Invoking it directly bypasses
    Pydantic schema validation (we already know our inputs are valid).
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

    for t in tools:
        if getattr(t, "name", None) == "spawn_instance":
            return t
    raise RuntimeError(
        "spawn_instance tool not found in create_instance_tools output; "
        f"got {[getattr(t, 'name', None) for t in tools]}"
    )


# =============================================================================
# Tests
# =============================================================================


class TestTeamMembersAuthorization:
    """Authorization tests for the ``team_members`` gate on ``spawn_instance``."""

    async def test_valid_spawn_leader_can_spawn_developer(self):
        """Happy path: 'leader' has 'developer' in its ``team_members`` list.

        leader.team_members = ["planner", "developer", "reviewer", "tidier",
        "approver", "tester", "giter", "devops"]
        """
        manager = _make_manager()
        spawn = _get_spawn_instance_tool(manager, caller_agent_id="leader")

        # Pass project_id explicitly to skip the auto-inherit + normalize path
        # (which requires a fully initialized project store; out of scope for
        # the authorization gate).
        result = await spawn.coroutine(
            agent_id="developer", project_id="test-project-id"
        )

        # Success path returns a non-ERROR result containing the instance_id.
        assert isinstance(result, str), f"Expected str, got {type(result)}"
        assert not result.startswith("ERROR"), f"Should succeed; got: {result!r}"
        assert "new-instance-id-12345" in result, (
            f"Should include the new instance_id; got: {result!r}"
        )
        # Manager's spawn_instance was called exactly once.
        manager.spawn_instance.assert_called_once()
        call_kwargs = manager.spawn_instance.call_args.kwargs
        assert call_kwargs["agent_id"] == "developer"
        assert call_kwargs["parent_id"] == "parent-instance-id"

    async def test_valid_spawn_leader_can_spawn_each_team_member(self):
        """Leader can spawn every agent in its team_members list."""
        expected_team = [
            "planner", "developer", "reviewer", "tidier",
            "approver", "tester", "giter", "devops",
        ]
        for agent_id in expected_team:
            manager = _make_manager(spawn_result=(f"id-{agent_id}", None))
            spawn = _get_spawn_instance_tool(manager, caller_agent_id="leader")
            # Pass project_id explicitly (see test_valid_spawn_leader_can_spawn_developer).
            result = await spawn.coroutine(
                agent_id=agent_id, project_id="test-project-id"
            )
            assert not result.startswith("ERROR"), (
                f"leader should be allowed to spawn '{agent_id}'; got: {result!r}"
            )
            manager.spawn_instance.assert_called_once()

    async def test_invalid_spawn_leader_cannot_spawn_leader(self):
        """'leader' is NOT in leader's own team_members — must be rejected.

        The check happens BEFORE manager.spawn_instance is called.
        """
        manager = _make_manager()
        spawn = _get_spawn_instance_tool(manager, caller_agent_id="leader")

        result = await spawn.coroutine(agent_id="leader")

        assert isinstance(result, str)
        assert result.startswith("ERROR"), f"Expected ERROR; got: {result!r}"
        assert "not allowed to spawn" in result, f"Got: {result!r}"
        assert "'leader'" in result, f"Should mention caller 'leader'; got: {result!r}"
        assert "Allowed team members" in result, (
            f"Should list allowed team members; got: {result!r}"
        )
        # CRITICAL: the manager's spawn_instance must NOT have been called.
        manager.spawn_instance.assert_not_called()

    async def test_invalid_spawn_leader_cannot_spawn_unlisted_agent(self):
        """'leader' cannot spawn 'explorer' (not in team_members)."""
        manager = _make_manager()
        spawn = _get_spawn_instance_tool(manager, caller_agent_id="leader")

        result = await spawn.coroutine(agent_id="explorer")

        assert isinstance(result, str)
        assert result.startswith("ERROR"), f"Expected ERROR; got: {result!r}"
        assert "'explorer'" in result, f"Got: {result!r}"
        # Show the allowed list so the calling agent knows its options.
        assert "'developer'" in result or "developer" in result, (
            f"Should list developer as allowed; got: {result!r}"
        )
        manager.spawn_instance.assert_not_called()

    async def test_invalid_spawn_developer_cannot_spawn_anyone(self):
        """'developer' has empty team_members — deny-by-default rejects all spawns.

        This is the deny-by-default security guarantee: an agent that didn't
        declare any team_members can spawn NOTHING, regardless of how 'trusted'
        its other code paths might be.
        """
        manager = _make_manager()
        spawn = _get_spawn_instance_tool(manager, caller_agent_id="developer")

        # Try spawning every plausible target — all should be rejected.
        for target in ["leader", "developer", "reviewer", "tester", "planner"]:
            manager2 = _make_manager()
            spawn2 = _get_spawn_instance_tool(manager2, caller_agent_id="developer")
            result = await spawn2.coroutine(agent_id=target)
            assert isinstance(result, str)
            assert result.startswith("ERROR"), (
                f"developer (empty team_members) should reject spawn of "
                f"'{target}'; got: {result!r}"
            )
            assert "not allowed to spawn" in result
            assert "Allowed team members: []" in result, (
                f"Empty team_members must be shown as []; got: {result!r}"
            )
            manager2.spawn_instance.assert_not_called()

    async def test_empty_team_members_rejects_all_spawns(self):
        """Explicit empty team_members list → reject all spawns.

        Covers the case where an agent declares ``team_members: []`` in its
        meta.json (rather than omitting it entirely).
        """
        manager = _make_manager()
        spawn = _get_spawn_instance_tool(manager, caller_agent_id="tester")

        result = await spawn.coroutine(agent_id="developer")

        assert isinstance(result, str)
        assert result.startswith("ERROR")
        assert "not allowed to spawn" in result
        assert "Allowed team members: []" in result
        manager.spawn_instance.assert_not_called()

    async def test_unknown_caller_agent_is_denied(self):
        """A caller agent_id that doesn't exist in the registry → deny.

        Wiring bug / misconfiguration: spawn_instance is invoked by an
        instance whose agent_id is unknown. Fail closed.
        """
        manager = _make_manager()
        spawn = _get_spawn_instance_tool(
            manager, caller_agent_id="ghost_agent_xyz"
        )

        result = await spawn.coroutine(agent_id="developer")

        assert isinstance(result, str)
        assert result.startswith("ERROR")
        assert "not allowed to spawn" in result
        manager.spawn_instance.assert_not_called()

    async def test_unknown_requested_agent_is_denied(self):
        """An unknown requested agent_id (typo) → deny with helpful message.

        Note: the membership check happens BEFORE the downstream
        ``manager.spawn_instance`` "Agent not found" ValueError. We surface
        the rejection as "not allowed to spawn" which is also informative.
        """
        manager = _make_manager()
        spawn = _get_spawn_instance_tool(manager, caller_agent_id="leader")

        result = await spawn.coroutine(agent_id="ghost_agent_xyz")

        assert isinstance(result, str)
        assert result.startswith("ERROR")
        assert "not allowed to spawn" in result
        manager.spawn_instance.assert_not_called()

    async def test_alias_request_resolves_to_canonical_id(self):
        """A legacy alias in the request (e.g. 'coder') is canonicalized.

        'coder' is registered as an alias for 'developer' in
        ``daemon/registry.py::AGENT_ID_ALIASES``. leader's team_members
        contains 'developer' (canonical), so the canonicalized request
        'coder' → 'developer' must succeed.
        """
        manager = _make_manager(spawn_result=("id-alias-success", None))
        spawn = _get_spawn_instance_tool(manager, caller_agent_id="leader")

        result = await spawn.coroutine(
            agent_id="coder", project_id="test-project-id"  # alias for 'developer'
        )

        assert isinstance(result, str)
        assert not result.startswith("ERROR"), (
            f"Alias 'coder' should canonicalize to 'developer' which IS in "
            f"leader's team_members; got: {result!r}"
        )
        manager.spawn_instance.assert_called_once()
        # The manager's spawn_instance receives the raw 'coder' (it also
        # canonicalizes internally); the gate's job is just to authorize.
        assert manager.spawn_instance.call_args.kwargs["agent_id"] == "coder"

    async def test_alias_caller_resolves_to_canonical_id(self):
        """A legacy alias as the CALLER (e.g. 'coder' instance invoking spawn).

        'coder' canonicalizes to 'developer', which has empty team_members.
        So even though the caller id is technically 'coder', the
        authorization is based on the CANONICAL caller (developer) which
        has no permission to spawn anyone.
        """
        manager = _make_manager()
        # Build tools with the alias as caller_agent_id (mirrors an
        # instance whose meta.json id was 'coder' before the rename).
        spawn = _get_spawn_instance_tool(manager, caller_agent_id="coder")

        result = await spawn.coroutine(agent_id="leader")

        assert isinstance(result, str)
        assert result.startswith("ERROR"), (
            f"Alias caller 'coder' canonicalizes to 'developer' (empty "
            f"team_members); should be denied; got: {result!r}"
        )
        assert "developer" in result.lower(), (
            f"Error should show the canonical caller 'developer'; got: {result!r}"
        )
        manager.spawn_instance.assert_not_called()

    async def test_empty_agent_id_request_rejected(self):
        """Empty requested agent_id is rejected with a clear message.

        The Pydantic ``model_validator`` on ``SpawnInstanceInput`` already
        raises ``ValueError`` for empty agent_id, but the gate has its own
        defense-in-depth check that returns an ERROR string.
        """
        manager = _make_manager()
        spawn = _get_spawn_instance_tool(manager, caller_agent_id="leader")

        # The Pydantic validator on SpawnInstanceInput rejects empty
        # ``agent_id`` BEFORE the function body runs, raising a ValueError
        # that propagates out of ``tool.coroutine``. This test documents
        # that behavior — empty agent_id is rejected at the schema level.
        try:
            result = await spawn.coroutine(agent_id="")
        except (ValueError, Exception) as e:
            # Pydantic validation may raise or may bubble through. Either
            # way, no instance was created.
            manager.spawn_instance.assert_not_called()
            return

        # If the schema validator allowed it through, our gate still
        # produces a clear ERROR.
        assert isinstance(result, str)
        assert result.startswith("ERROR")
        manager.spawn_instance.assert_not_called()

    async def test_missing_caller_agent_id_rejected(self):
        """Empty caller_agent_id (wiring bug) → deny with clear error."""
        manager = _make_manager()
        spawn = _get_spawn_instance_tool(manager, caller_agent_id="")

        result = await spawn.coroutine(agent_id="developer")

        assert isinstance(result, str)
        assert result.startswith("ERROR"), f"Expected ERROR; got: {result!r}"
        assert "caller agent_id" in result.lower() or "wiring" in result.lower(), (
            f"Should explain the wiring/configuration bug; got: {result!r}"
        )
        manager.spawn_instance.assert_not_called()


class TestTeamMembersRegistryParsing:
    """Tests that ``team_members`` is correctly parsed from meta.json."""

    def test_leader_team_members_parsed(self):
        """leader.meta.json has a non-empty team_members list."""
        from daemon.registry import get_registry

        leader = get_registry().get("leader")
        assert leader is not None
        expected = [
            "planner", "developer", "reviewer", "tidier",
            "approver", "tester", "giter", "devops",
        ]
        assert set(leader.team_members) == set(expected), (
            f"leader.team_members mismatch: got {leader.team_members}"
        )

    def test_developer_team_members_empty(self):
        """developer.meta.json has team_members = [] (deny-by-default)."""
        from daemon.registry import get_registry

        dev = get_registry().get("developer")
        assert dev is not None
        assert dev.team_members == [], (
            f"developer.team_members should be []; got {dev.team_members}"
        )

    def test_planner_team_members_empty(self):
        """planner.meta.json has team_members = [] (deny-by-default)."""
        from daemon.registry import get_registry

        planner = get_registry().get("planner")
        assert planner is not None
        assert planner.team_members == []

    def test_all_agents_have_team_members_field(self):
        """Every registered agent exposes a team_members attribute (possibly empty)."""
        from daemon.registry import get_registry

        registry = get_registry()
        for agent_meta in registry.list_all():
            assert hasattr(agent_meta, "team_members"), (
                f"Agent '{agent_meta.id}' missing team_members field"
            )
            assert isinstance(agent_meta.team_members, list), (
                f"Agent '{agent_meta.id}' team_members is not a list: "
                f"{type(agent_meta.team_members)}"
            )


class TestCheckTeamMembershipUnit:
    """Unit tests for ``_check_team_membership`` directly.

    These tests pin the contract of the helper independent of the tool
    closure wiring, so refactoring the tool layer doesn't lose coverage.
    """

    def test_returns_none_when_allowed(self):
        from daemon.tools.instance import _check_team_membership

        assert _check_team_membership("leader", "developer") is None

    def test_returns_error_when_denied(self):
        from daemon.tools.instance import _check_team_membership

        err = _check_team_membership("leader", "leader")
        assert err is not None
        assert "not allowed to spawn" in err
        assert "leader" in err

    def test_returns_error_for_empty_team_members(self):
        from daemon.tools.instance import _check_team_membership

        err = _check_team_membership("developer", "leader")
        assert err is not None
        assert "Allowed team members: []" in err

    def test_alias_request_canonicalizes(self):
        """Request 'coder' canonicalizes to 'developer' (in leader's list)."""
        from daemon.tools.instance import _check_team_membership

        assert _check_team_membership("leader", "coder") is None

    def test_alias_caller_canonicalizes(self):
        """Caller 'coder' canonicalizes to 'developer' (empty list)."""
        from daemon.tools.instance import _check_team_membership

        err = _check_team_membership("coder", "leader")
        assert err is not None
        # Error message should reference the CANONICAL caller ('developer').
        assert "developer" in err

    def test_unknown_caller_returns_error(self):
        from daemon.tools.instance import _check_team_membership

        err = _check_team_membership("ghost_agent", "developer")
        assert err is not None
        assert "not allowed to spawn" in err

    def test_unknown_request_returns_error(self):
        from daemon.tools.instance import _check_team_membership

        err = _check_team_membership("leader", "ghost_agent")
        assert err is not None
        assert "not allowed to spawn" in err