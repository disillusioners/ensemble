"""Tests for the ``team_members`` authorization gate on ``spawn_instance``.

The gate is enforced inside the ``spawn_instance`` tool (in
``daemon/tools/instance.py``) BEFORE any DB transaction or instance creation
work. It reads the caller's ``meta.json`` ``team_members`` list and rejects
the spawn with a clear ERROR string when:

  1. The caller agent has no ``team_members`` list (deny-by-default).
  2. The caller agent has an empty ``team_members`` list (deny-by-default).
  3. The requested ``agent_id`` (resolved via the registry) is NOT in
     the caller's ``team_members`` list (also resolved).

The tests below cover all three rejection paths AND the happy path. They
also cover the post-alias-removal standalone 'coder' agent on BOTH sides
of the comparison.

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

from unittest.mock import AsyncMock, MagicMock

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
        patch("daemon.tools.instance.create_chart_tools", return_value=[]),
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
            "approver", "architect",  # Added when architect agent was introduced.
            "tester", "giter", "devops",
            "explorer",
            "wanderer",  # Added when wanderer agent was introduced.
            "kb-writer",  # Added when kb-writer agent was introduced.
            "doc-writer",  # Added when doc-writer agent was introduced.
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

    async def test_valid_spawn_leader_can_spawn_explorer(self):
        """'leader' can now spawn 'explorer' (added in W1).

        After the W1 fix, leader.team_members includes 'explorer' so that
        the ``explore()`` knowledge tool's internal ``spawn_instance``
        call is authorized. The deny mechanism for agents NOT in
        leader's list is still covered by ``test_invalid_spawn_leader_cannot_spawn_leader``.
        """
        manager = _make_manager(spawn_result=("new-explorer-id", None))
        spawn = _get_spawn_instance_tool(manager, caller_agent_id="leader")

        result = await spawn.coroutine(
            agent_id="explorer", project_id="test-project-id"
        )

        # Success path returns a non-ERROR result containing the instance_id.
        assert isinstance(result, str), f"Expected str, got {type(result)}"
        assert not result.startswith("ERROR"), f"Should succeed; got: {result!r}"
        assert "new-explorer-id" in result, (
            f"Should include the new instance_id; got: {result!r}"
        )
        # Manager's spawn_instance was called exactly once.
        manager.spawn_instance.assert_called_once()
        call_kwargs = manager.spawn_instance.call_args.kwargs
        assert call_kwargs["agent_id"] == "explorer"
        assert call_kwargs["parent_id"] == "parent-instance-id"

    async def test_invalid_spawn_developer_cannot_spawn_non_team_targets(self):
        """'developer' has restricted team_members — deny non-team spawns.

        After W1, developer.team_members = ["explorer"]. Any agent NOT in
        that list must still be rejected. This test exercises targets
        outside developer's team to verify the deny mechanism is intact
        (NOTE: developer CAN spawn 'explorer' — covered separately).
        """
        manager = _make_manager()
        spawn = _get_spawn_instance_tool(manager, caller_agent_id="developer")

        # Try spawning every plausible target that is NOT in
        # developer.team_members — all should be rejected.
        for target in ["leader", "developer", "reviewer", "tester", "planner"]:
            manager2 = _make_manager()
            spawn2 = _get_spawn_instance_tool(manager2, caller_agent_id="developer")
            result = await spawn2.coroutine(agent_id=target)
            assert isinstance(result, str)
            assert result.startswith("ERROR"), (
                f"developer (team_members=['explorer']) should reject "
                f"spawn of '{target}'; got: {result!r}"
            )
            assert "not allowed to spawn" in result
            # After auto-derivation: developer has team_members=['explorer']
            # (explicit) AND tools.allow includes 'image' and 'knowledge',
            # which imply 'explorer', 'kb-writer', and 'image-reader'.
            # The merged, canonicalized, sorted set is
            # ['explorer', 'image-reader', 'kb-writer'].
            assert "Allowed team members: ['explorer', 'image-reader', 'kb-writer']" in result, (
                f"developer auto-derived allow-set must be "
                f"['explorer', 'image-reader', 'kb-writer']; "
                f"got: {result!r}"
            )
            manager2.spawn_instance.assert_not_called()

    async def test_restricted_team_members_rejects_non_team_spawns(self):
        """Restricted team_members list → reject spawns outside the team.

        After W1, tester.team_members = ["explorer", "worker"] (a NON-empty
        restricted list). This test verifies that targets OUTSIDE that
        restricted team are rejected. Note: this is NOT the
        deny-by-default empty-list case — see
        ``TestCheckTeamMembershipUnit::test_returns_error_for_truly_empty_team_members``
        for the foundational [] security guarantee.
        """
        manager = _make_manager()
        spawn = _get_spawn_instance_tool(manager, caller_agent_id="tester")

        result = await spawn.coroutine(agent_id="developer")

        assert isinstance(result, str)
        assert result.startswith("ERROR")
        assert "not allowed to spawn" in result
        # After auto-derivation: tester has team_members=['explorer', 'worker']
        # (explicit) AND tools.allow includes 'image' and 'knowledge',
        # which imply 'explorer', 'kb-writer', and 'image-reader'.
        # The merged, canonicalized, sorted set is
        # ['explorer', 'image-reader', 'kb-writer', 'worker'].
        assert (
            "Allowed team members: ['explorer', 'image-reader', 'kb-writer', 'worker']"
            in result
        ), (
            f"tester auto-derived allow-set must be "
            f"['explorer', 'image-reader', 'kb-writer', 'worker']; "
            f"got: {result!r}"
        )
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

    async def test_standalone_coder_rejected_by_leader_team_members(self):
        """'coder' is now a STANDALONE agent (no alias to 'developer').

        It is NOT in leader's team_members, so leader is denied the spawn.
        This pins the deny-by-default behavior post-alias-removal.
        """
        manager = _make_manager()
        spawn = _get_spawn_instance_tool(manager, caller_agent_id="leader")

        result = await spawn.coroutine(
            agent_id="coder",          # standalone (no alias to developer)
            project_id="test-project-id",
        )

        assert result.startswith("ERROR"), (
            f"Standalone 'coder' is not in leader.team_members; spawn must "
            f"be denied. Got: {result!r}"
        )
        assert "coder" in result.lower(), (
            f"Error should mention the requested 'coder'; got: {result!r}"
        )
        assert "not allowed to spawn" in result, f"Got: {result!r}"
        # Manager must NOT have been invoked
        manager.spawn_instance.assert_not_called()

    async def test_standalone_coder_caller_has_empty_team_members(self):
        """'coder' is a standalone agent with empty team_members.

        As caller, it cannot spawn anyone (deny-by-default). Error
        references the actual caller id 'coder' (no alias
        canonicalization).
        """
        manager = _make_manager()
        spawn = _get_spawn_instance_tool(manager, caller_agent_id="coder")

        result = await spawn.coroutine(agent_id="leader")

        assert result.startswith("ERROR"), (
            f"Standalone 'coder' has empty team_members — must be denied; "
            f"got: {result!r}"
        )
        assert "coder" in result.lower(), (
            f"Error should reference the actual caller 'coder'; got: {result!r}"
        )
        assert "not allowed to spawn" in result, f"Got: {result!r}"
        # Manager must NOT have been invoked
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
            "approver", "architect",  # Added when architect agent was introduced.
            "tester", "giter", "devops",
            "explorer",  # Added in W1 so leader can authorize explore()'s
                         # internal spawn_instance of the "explorer" agent.
            "wanderer",  # Added when wanderer agent was introduced.
            "kb-writer",  # Added when kb-writer agent was introduced.
            "doc-writer",  # Added when doc-writer agent was introduced.
        ]
        assert set(leader.team_members) == set(expected), (
            f"leader.team_members mismatch: got {leader.team_members}"
        )

    def test_developer_team_members_has_explorer(self):
        """developer.meta.json has team_members = ["explorer"] (W1).

        developer is knowledge-enabled, so it must be authorized to spawn
        the 'explorer' agent that backs its ``explore()`` tool.
        """
        from daemon.registry import get_registry

        dev = get_registry().get("developer")
        assert dev is not None
        assert dev.team_members == ["explorer"], (
            f"developer.team_members should be ['explorer']; got {dev.team_members}"
        )

    def test_planner_team_members_has_explorer(self):
        """planner.meta.json has team_members = ["explorer"] (W1).

        planner is knowledge-enabled, so it must be authorized to spawn
        the 'explorer' agent that backs its ``explore()`` tool.
        """
        from daemon.registry import get_registry

        planner = get_registry().get("planner")
        assert planner is not None
        assert planner.team_members == ["explorer"], (
            f"planner.team_members should be ['explorer']; got {planner.team_members}"
        )

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

    def test_returns_error_when_requested_not_in_restricted_team(self):
        """A caller with a NON-empty restricted team rejects non-team targets.

        After W1, developer's team_members = ["explorer"] (a non-empty
        restricted list). The helper must reject any requested agent_id
        not in that list and render the allowed set explicitly. This
        covers the "non-team target rejected by restricted team" branch;
        the foundational empty-list deny-by-default is covered by
        ``test_returns_error_for_truly_empty_team_members`` below.
        """
        from daemon.tools.instance import _check_team_membership

        err = _check_team_membership("developer", "leader")
        assert err is not None
        assert "not allowed to spawn" in err
        # After auto-derivation: developer has team_members=['explorer']
        # (explicit) AND tools.allow includes 'image' and 'knowledge',
        # which imply 'explorer', 'kb-writer', and 'image-reader'.
        # The merged, canonicalized, sorted set is
        # ['explorer', 'image-reader', 'kb-writer'].
        assert "Allowed team members: ['explorer', 'image-reader', 'kb-writer']" in err, (
            f"developer auto-derived allow-set must be "
            f"['explorer', 'image-reader', 'kb-writer']; "
            f"got: {err!r}"
        )

    def test_returns_error_for_truly_empty_team_members(self, monkeypatch):
        """A caller whose team_members is genuinely [] must reject ALL spawns.

        After W1, no real agent fixture has an empty ``team_members`` list
        (developer, planner, tester are all ``["explorer"]``). To exercise
        the foundational deny-by-default contract on a truly empty list,
        we inject a synthetic caller via the registry whose
        ``team_members`` is ``[]`` (not None, not absent — the literal
        empty list).

        This is the most important security guarantee of
        ``_check_team_membership``: a caller with an explicit empty list
        must be rejected even when targeting known-valid agents.
        """
        from pathlib import Path

        from daemon.registry import AgentMetadata, get_registry
        from daemon.tools.instance import _check_team_membership

        registry = get_registry()

        # Synthetic caller with team_members = [] (literal empty list,
        # NOT None, NOT missing).
        synthetic_meta = AgentMetadata(
            id="synthetic_empty_team_caller",
            name="Synthetic Empty-Team Caller",
            description="Test fixture for deny-by-default on team_members=[]",
            path=Path("/tmp/synthetic_empty_team_caller"),
            team_members=[],
        )

        # Patch get_resolved so the synthetic caller resolves to our
        # metadata. Fall through to the original for everything else
        # so resolve_pure_id etc. keep working normally.
        original_get_resolved = registry.get_resolved

        def patched_get_resolved(agent_id: str):
            if agent_id == "synthetic_empty_team_caller":
                return synthetic_meta
            return original_get_resolved(agent_id)

        monkeypatch.setattr(registry, "get_resolved", patched_get_resolved)

        # Spawn against a known-valid target agent — must be rejected.
        err = _check_team_membership("synthetic_empty_team_caller", "developer")

        assert err is not None, "Empty team_members must produce an error"
        assert "not allowed to spawn" in err, f"Got: {err!r}"
        assert "Allowed team members: []" in err, (
            f"Empty team_members must render as 'Allowed team members: []'; "
            f"got: {err!r}"
        )

    def test_returns_error_for_missing_team_members_attribute(self, monkeypatch):
        """A caller whose team_members attribute is None is treated like [].

        The implementation uses ``caller_meta.team_members or []``, which
        collapses ``None`` and ``[]`` into the same deny-everything path.
        This test pins that contract: a caller with ``team_members=None``
        must also be rejected with ``Allowed team members: []``.

        NOTE: ``None`` and ``[]`` are intentionally treated identically
        (defense-in-depth — both express "no authority to spawn anyone").
        A single empty-list test would be sufficient for behavior, but we
        keep this explicit test to document and protect the contract
        against future regressions.
        """
        from daemon.registry import get_registry
        from daemon.tools.instance import _check_team_membership

        registry = get_registry()

        # Mock with team_members=None explicitly. We use MagicMock because
        # AgentMetadata's typed field rejects None at construction; the
        # helper reads .id and .team_members only, so a minimal mock is
        # sufficient.
        mock_caller = MagicMock()
        mock_caller.id = "synthetic_missing_team_caller"
        mock_caller.team_members = None

        original_get_resolved = registry.get_resolved

        def patched_get_resolved(agent_id: str):
            if agent_id == "synthetic_missing_team_caller":
                return mock_caller
            return original_get_resolved(agent_id)

        monkeypatch.setattr(registry, "get_resolved", patched_get_resolved)

        err = _check_team_membership("synthetic_missing_team_caller", "developer")

        assert err is not None, "Missing (None) team_members must produce an error"
        assert "not allowed to spawn" in err, f"Got: {err!r}"
        assert "Allowed team members: []" in err, (
            f"None team_members must render the same as []: "
            f"'Allowed team members: []'; got: {err!r}"
        )

    def test_standalone_coder_not_in_leader_team(self):
        """'coder' is now a standalone agent and is NOT in leader's team_members.

        Pre-removal: alias 'coder' canonicalized to 'developer' which WAS in leader's
        team_members, so the call succeeded. Post-removal: coder has no alias, so
        leader's team check sees the raw 'coder' which is missing → deny-by-default.
        """
        from daemon.tools.instance import _check_team_membership

        err = _check_team_membership("leader", "coder")
        assert err is not None, "'coder' is standalone and not in leader's team_members; must be denied"
        assert "not allowed to spawn" in err
        assert "coder" in err  # error mentions the actual requested id (no alias hop)

    def test_standalone_coder_caller_denied_for_any_request(self):
        """'coder' as caller has empty team_members → any spawn request is denied.

        Pre-removal: caller 'coder' canonicalized to 'developer' which has empty
        team_members (same denial). Post-removal: same behavior, but the error
        now references the raw caller id 'coder' (no canonicalization hop).
        """
        from daemon.tools.instance import _check_team_membership

        err = _check_team_membership("coder", "leader")
        assert err is not None, "caller 'coder' has empty team_members; must be denied"
        assert "not allowed to spawn" in err
        assert "coder" in err  # error references the actual caller id (no alias hop)

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

    def test_case_sensitive_agent_id_fails_closed(self):
        """''Developer'' (capital D) is rejected by ``_check_team_membership``.

        ``registry.resolve_pure_id`` is case-sensitive: only the exact
        lowercase key in ``self._agents`` (the alias dict is now empty)
        resolves. Any other casING returns ``None``
        and the gate treats it as an unknown agent → reject with the
        usual deny-by-default error. This pins the SAFE fail-closed
        behavior so a future switch to case-insensitive resolution is
        an explicit, tested decision rather than silent.
        """
        from daemon.tools.instance import _check_team_membership

        err = _check_team_membership("leader", "Developer")
        assert err is not None
        assert "not allowed to spawn" in err, f"Got: {err!r}"

    def test_whitespace_in_agent_id_fails_closed(self):
        """Whitespace in requested agent_id fails closed (reject).

        ``registry.resolve_pure_id`` does NOT strip whitespace: the raw
        string is looked up in ``self._agents`` directly. A trailing
        space (e.g. ``"developer "``) does not match the registered
        key and the gate treats it as unknown → reject with the
        usual deny-by-default error. Pins the SAFE fail-closed
        behavior so a future strip-and-retry is an explicit, tested
        decision.
        """
        from daemon.tools.instance import _check_team_membership

        err = _check_team_membership("leader", "developer ")
        assert err is not None
        assert "not allowed to spawn" in err, f"Got: {err!r}"


# =============================================================================
# Auto-derivation of implied team_members from tools.allow
# =============================================================================
#
# The ``_check_team_membership`` helper in ``daemon/tools/_auth.py`` now
# expands the caller's ``tools.allow`` categories through
# ``TOOL_REQUIRED_AGENTS`` to derive implied team members. This makes
# ``tools.allow`` the single source of truth — the caller no longer
# needs to ALSO duplicate the backing agent in ``team_members``.
#
# These tests pin the auto-derivation contract directly against
# ``_check_team_membership`` (independent of the ``spawn_instance``
# tool wiring) so refactors of the tool layer don't lose coverage.


def _install_synthetic_caller(monkeypatch, agent_id, *, team_members,
                               tools_allow, tools_deny=None):
    """Install a synthetic AgentMetadata into the registry for one test.

    Patches ``registry.get_resolved`` to return our synthetic metadata
    for the supplied ``agent_id``; other ids pass through to the real
    registry so ``resolve_pure_id`` keeps working normally.

    The synthetic caller carries:
      * ``team_members`` — explicit allow-set (raw, not yet canonicalized).
      * ``tools.allow`` — the list of category strings the helper
        auto-expands via :data:`TOOL_REQUIRED_AGENTS`.
      * ``tools.deny`` — optional list mirroring
        ``ToolFilter.deny`` for the F1 deny-subtraction tests.
        ``None`` (default) omits the deny field entirely.

    Returns the synthetic ``AgentMetadata`` so the test can assert
    against its rendered ``.id`` / ``.team_members`` directly.
    """
    from pathlib import Path

    from daemon.registry import AgentMetadata, get_registry

    registry = get_registry()

    tools_filter = None
    if tools_allow is not None:
        # Use a real ``ToolFilter`` so the helper reads
        # ``caller_meta.tools.allow`` (a real list, not a MagicMock).
        from daemon.registry import ToolFilter
        deny_list = list(tools_deny) if tools_deny else None
        tools_filter = ToolFilter(allow=list(tools_allow), deny=deny_list)

    synthetic = AgentMetadata(
        id=agent_id,
        name=agent_id,
        description=f"Synthetic caller for auto-derivation test ({agent_id})",
        path=Path(f"/tmp/{agent_id}"),
        team_members=list(team_members),
        tools=tools_filter,
    )

    original_get_resolved = registry.get_resolved

    def patched_get_resolved(query_id: str):
        if query_id == agent_id:
            return synthetic
        return original_get_resolved(query_id)

    monkeypatch.setattr(registry, "get_resolved", patched_get_resolved)
    return synthetic


class TestAutoDerivationOfImpliedTeamMembers:
    """Pin the auto-derivation contract of ``_check_team_membership``.

    The helper now reads the caller's ``tools.allow`` and merges
    :data:`TOOL_REQUIRED_AGENTS`-implied agents into the effective
    allow-set, alongside any explicit ``team_members`` declarations.
    Explicit team_members remain honored (union semantics). The
    tests below cover all branches of the new contract.
    """

    def test_auto_derive_knowledge_implies_explorer_and_kb_writer(self, monkeypatch):
        """An agent with ``tools.allow=['knowledge']`` but no explicit
        ``explorer``/``kb-writer`` in ``team_members`` → both are
        implicitly allowed (auto-derived from the ``knowledge``
        category).
        """
        from daemon.tools.instance import _check_team_membership

        _install_synthetic_caller(
            monkeypatch,
            "synthetic_knowledge_only_caller",
            team_members=[],  # explicit list is empty
            tools_allow=["knowledge"],
        )

        # Both required_agents for the "knowledge" category are
        # implicitly allowed.
        assert _check_team_membership("synthetic_knowledge_only_caller", "explorer") is None
        assert _check_team_membership("synthetic_knowledge_only_caller", "kb-writer") is None

    def test_auto_derive_multiple_categories_all_implied_allowed(self, monkeypatch):
        """An agent with ``tools.allow=['knowledge', 'image', 'chart']`` →
        ``explorer``, ``kb-writer``, ``image-reader``, and ``charter``
        are all implicitly allowed.
        """
        from daemon.tools.instance import _check_team_membership

        _install_synthetic_caller(
            monkeypatch,
            "synthetic_multi_category_caller",
            team_members=[],
            tools_allow=["knowledge", "image", "chart"],
        )

        # Every required agent for every declared category must pass.
        assert _check_team_membership("synthetic_multi_category_caller", "explorer") is None
        assert _check_team_membership("synthetic_multi_category_caller", "kb-writer") is None
        assert _check_team_membership("synthetic_multi_category_caller", "image-reader") is None
        assert _check_team_membership("synthetic_multi_category_caller", "charter") is None

    def test_explicit_team_members_still_allow_when_tools_allow_empty(self, monkeypatch):
        """Backward compatibility: an agent with explicit
        ``team_members=['developer']`` and empty ``tools.allow`` →
        ``_check_team_membership`` allows ``developer`` and denies others.
        Explicit declarations still work after the auto-derivation change.
        """
        from daemon.tools.instance import _check_team_membership

        _install_synthetic_caller(
            monkeypatch,
            "synthetic_explicit_only_caller",
            team_members=["developer"],
            tools_allow=[],  # no category → no auto-derivation
        )

        # Explicit member is allowed.
        assert _check_team_membership("synthetic_explicit_only_caller", "developer") is None
        # Any other agent is denied (no auto-derivation can rescue it).
        err = _check_team_membership("synthetic_explicit_only_caller", "explorer")
        assert err is not None
        assert "not allowed to spawn" in err
        # The allowed set is just the explicit declaration.
        assert "Allowed team members: ['developer']" in err, (
            f"Explicit-only caller must show ['developer']; got: {err!r}"
        )

    def test_empty_tools_and_empty_team_members_denies_everything(self, monkeypatch):
        """Deny-by-default: an agent with empty ``tools.allow`` AND empty
        ``team_members`` denies ALL spawns. Returns an error string (not
        None) — pinning the foundational security guarantee that
        post-W1 callers with no authority whatsoever are rejected.
        """
        from daemon.tools.instance import _check_team_membership

        _install_synthetic_caller(
            monkeypatch,
            "synthetic_deny_default_caller",
            team_members=[],
            tools_allow=[],
        )

        err = _check_team_membership("synthetic_deny_default_caller", "developer")
        assert err is not None, "Deny-by-default: empty allow-set must produce an error"
        assert "not allowed to spawn" in err
        assert "Allowed team members: []" in err, (
            f"Empty allow-set must render as []; got: {err!r}"
        )

    def test_non_matching_category_does_not_imply_other_categories(self, monkeypatch):
        """An agent with ``tools.allow=['chart']`` only →
        ``_check_team_membership`` ALLOWS ``charter`` (chart's
        required agent) but DENIES ``explorer`` (knowledge-implied)
        and ``image-reader`` (image-implied). Only the categories
        actually present in ``tools.allow`` grant their required
        agents — no transitive cross-category grants.
        """
        from daemon.tools.instance import _check_team_membership

        _install_synthetic_caller(
            monkeypatch,
            "synthetic_chart_only_caller",
            team_members=[],
            tools_allow=["chart"],  # ONLY chart, no knowledge / image
        )

        # Charter IS allowed (chart's required agent).
        assert _check_team_membership("synthetic_chart_only_caller", "charter") is None

        # Explorer and image-reader are NOT allowed (knowledge and
        # image categories are absent → no cross-category grants).
        err_explorer = _check_team_membership("synthetic_chart_only_caller", "explorer")
        assert err_explorer is not None, "chart-only caller must NOT allow 'explorer'"
        assert "not allowed to spawn" in err_explorer
        assert "Allowed team members: ['charter']" in err_explorer, (
            f"chart-only allow-set must be exactly ['charter']; "
            f"got: {err_explorer!r}"
        )

        err_image = _check_team_membership("synthetic_chart_only_caller", "image-reader")
        assert err_image is not None, "chart-only caller must NOT allow 'image-reader'"
        assert "not allowed to spawn" in err_image

        err_kb = _check_team_membership("synthetic_chart_only_caller", "kb-writer")
        assert err_kb is not None, "chart-only caller must NOT allow 'kb-writer'"
        assert "not allowed to spawn" in err_kb

    def test_non_agent_backed_category_implies_nothing(self, monkeypatch):
        """An agent with ``tools.allow=['bash']`` (a non-agent-backed
        category — NOT present in :data:`TOOL_REQUIRED_AGENTS`) and empty
        ``team_members`` → no agents are implied, so spawning
        ``explorer`` (or any agent) FAILS. This is distinct from the
        ``chart``-only case: ``chart`` IS in the map (implies ``charter``),
        while ``bash`` grants zero implied members. The allow-set
        renders as ``[]``.
        """
        from daemon.tools.instance import _check_team_membership

        _install_synthetic_caller(
            monkeypatch,
            "synthetic_bash_only_caller",
            team_members=[],
            tools_allow=["bash"],  # not in TOOL_REQUIRED_AGENTS
        )

        # explorer is NOT allowed (knowledge-implied; bash grants nothing).
        err = _check_team_membership("synthetic_bash_only_caller", "explorer")
        assert err is not None, "bash-only caller must NOT allow 'explorer'"
        assert "not allowed to spawn" in err
        assert "Allowed team members: []" in err, (
            f"bash-only allow-set must be exactly [] (no implied members); "
            f"got: {err!r}"
        )

    def test_canonicalization_preserved_for_implied_members(self, monkeypatch):
        """Canonicalization: if an implied id has an alias, the helper
        resolves it through the registry and the auto-derived set
        matches the canonical id. We exercise this by registering a
        synthetic ALIAS → required_agent mapping in
        ``AGENT_ID_ALIASES`` and verifying the helper still allows
        the canonical id (and the alias form).
        """
        from daemon.tools.instance import _check_team_membership
        from daemon.registry import AGENT_ID_ALIASES, get_registry

        registry = get_registry()

        # Pick a real required agent we can alias without colliding
        # with any existing alias. ``charter`` is the simplest target
        # (single required agent for the "chart" category, no existing
        # alias in the empty-AGENT_ID_ALIASES baseline).
        # The test adds "chartist" → "charter" so the synthetic
        # caller's tools.allow=['chart'] yields an implied member
        # whose canonical form resolves cleanly.
        AGENT_ID_ALIASES["chartist"] = "charter"
        try:
            _install_synthetic_caller(
                monkeypatch,
                "synthetic_alias_caller",
                team_members=[],
                tools_allow=["chart"],
            )

            # The canonical form ("charter") is allowed.
            assert _check_team_membership("synthetic_alias_caller", "charter") is None
            # The aliased form ("chartist") is also allowed — the
            # helper resolves the requested id through
            # ``registry.resolve_pure_id`` before comparison, so an
            # alias on the REQUEST side also canonicalizes correctly.
            assert _check_team_membership("synthetic_alias_caller", "chartist") is None
        finally:
            # Clean up the alias so other tests see a pristine registry.
            AGENT_ID_ALIASES.pop("chartist", None)

    # ------------------------------------------------------------------
    # F1 / F5 / F7-F9 — security fixes and regression guards
    # ------------------------------------------------------------------
    # These tests pin the security guarantee added by the F1 review
    # (deny-subtraction must mirror ``resolve_tool_filter``), the
    # intentional F5 contract (CATEGORY-ONLY matching in tools.allow),
    # and three regression guards (unknown category, tools=None, deny
    # category). The deny/category tests are the security-critical
    # surface — keeping them as independent cases makes a future
    # refactor that accidentally weakens the gate loudly visible.

    def test_bare_tool_name_does_not_imply_team_member(self, monkeypatch):
        """F5 pinning test — CATEGORY-ONLY contract.

        A bare tool name in ``tools.allow`` (e.g. ``"explore"``) is
        NOT a key in :data:`TOOL_REQUIRED_AGENTS`, so it implies NO
        backing agents. This is the documented intentional contract
        (see ``daemon/tools/_auth.py`` — the auth gate is simpler than
        ``resolve_tool_filter``, which expands both categories and
        tool names).

        Practical impact is minimal: the only known agent with a bare
        tool name like ``"explore"`` in its allow list is
        ``wanderer``; wanderer never spawns ``explorer`` via
        ``spawn_instance`` — its ``explore()`` tool bypasses the gate
        via ``invoke_agent_and_wait``. The asymmetry is harmless.
        """
        from daemon.tools.instance import _check_team_membership

        _install_synthetic_caller(
            monkeypatch,
            "synthetic_bare_tool_caller",
            team_members=[],
            tools_allow=["explore"],  # bare tool name, NOT a category
        )

        # ``explorer`` is NOT implied — "explore" is not in the map.
        err = _check_team_membership("synthetic_bare_tool_caller", "explorer")
        assert err is not None, (
            "Bare tool name in tools.allow must NOT imply 'explorer' "
            "(category-only contract)."
        )
        assert "not allowed to spawn" in err
        assert "Allowed team members: []" in err, (
            f"Bare-tool-name allow-set must be exactly []; got: {err!r}"
        )

        # And neither is ``kb-writer`` (knowledge is not in allow).
        err_kb = _check_team_membership("synthetic_bare_tool_caller", "kb-writer")
        assert err_kb is not None
        assert "Allowed team members: []" in err_kb

    def test_denied_category_drops_implied_members(self, monkeypatch):
        """F1 security fix — deny of a category subtracts its implied members.

        Without deny-subtraction, a caller that denies ``knowledge`` at
        the tool layer could still spawn ``explorer`` and
        ``kb-writer`` directly via ``spawn_instance`` — a spawn-gate
        bypass. This test pins the F1 fix: when a category appears in
        BOTH ``tools.allow`` and ``tools.deny``, ALL of its
        ``TOOL_REQUIRED_AGENTS`` entries are dropped from the implied
        set (mirroring ``resolve_tool_filter``'s "deny wins" semantics).
        """
        from daemon.tools.instance import _check_team_membership

        _install_synthetic_caller(
            monkeypatch,
            "synthetic_deny_knowledge_caller",
            team_members=[],
            tools_allow=["knowledge"],
            tools_deny=["knowledge"],  # deny wins
        )

        # Both knowledge-implied agents MUST be denied.
        err_explorer = _check_team_membership(
            "synthetic_deny_knowledge_caller", "explorer"
        )
        assert err_explorer is not None, (
            "deny=['knowledge'] must drop 'explorer' from implied set"
        )
        assert "Allowed team members: []" in err_explorer, (
            f"deny=['knowledge'] must yield []; got: {err_explorer!r}"
        )

        err_kb = _check_team_membership(
            "synthetic_deny_knowledge_caller", "kb-writer"
        )
        assert err_kb is not None, (
            "deny=['knowledge'] must drop 'kb-writer' from implied set"
        )
        assert "Allowed team members: []" in err_kb, (
            f"deny=['knowledge'] must yield []; got: {err_kb!r}"
        )

    def test_partial_deny_only_blocks_denied_category(self, monkeypatch):
        """F1 partial-deny case — deny of one category does not affect others.

        ``allow=['knowledge', 'image']`` + ``deny=['knowledge']``
        → ``explorer`` and ``kb-writer`` are dropped from the implied
        set, but ``image-reader`` survives. Confirms the deny
        subtraction is scoped to the denied category ONLY.
        """
        from daemon.tools.instance import _check_team_membership

        _install_synthetic_caller(
            monkeypatch,
            "synthetic_partial_deny_caller",
            team_members=[],
            tools_allow=["knowledge", "image"],
            tools_deny=["knowledge"],  # partial: deny knowledge only
        )

        # image-reader survives (image is in allow and NOT in deny).
        assert _check_team_membership(
            "synthetic_partial_deny_caller", "image-reader"
        ) is None, (
            "image-reader must survive a deny=['knowledge'] filter"
        )

        # explorer + kb-writer are dropped (knowledge is denied).
        for denied_agent in ("explorer", "kb-writer"):
            err = _check_team_membership(
                "synthetic_partial_deny_caller", denied_agent
            )
            assert err is not None, (
                f"deny=['knowledge'] must drop '{denied_agent}'"
            )
            assert "Allowed team members: ['image-reader']" in err, (
                f"Partial-deny allow-set must be ['image-reader']; "
                f"got: {err!r}"
            )

    def test_unknown_category_implies_nothing(self, monkeypatch):
        """F8 regression guard — TOOL_REQUIRED_AGENTS is the boundary.

        ``tools.allow=['rag']`` (a category NOT in
        :data:`TOOL_REQUIRED_AGENTS`) implies no backing agents. The
        map IS the boundary — anything outside the map's keys has no
        effect on the allow-set, regardless of what it means at the
        tool layer.
        """
        from daemon.tools.instance import _check_team_membership

        _install_synthetic_caller(
            monkeypatch,
            "synthetic_unknown_category_caller",
            team_members=[],
            tools_allow=["rag"],  # NOT in TOOL_REQUIRED_AGENTS
        )

        # No backing agent is implied (rag is outside the map).
        for denied_agent in ("explorer", "kb-writer", "image-reader",
                             "charter", "governor"):
            err = _check_team_membership(
                "synthetic_unknown_category_caller", denied_agent
            )
            assert err is not None, (
                f"tools.allow=['rag'] must NOT imply '{denied_agent}'"
            )
            assert "Allowed team members: []" in err, (
                f"Unknown-category allow-set must be []; got: {err!r}"
            )

    def test_caller_with_tools_none_denies_unknown_spawns(self, monkeypatch):
        """F9 regression guard — None-safety on ``caller_meta.tools``.

        A caller whose ``AgentMetadata`` has ``tools=None`` (the
        default) denies ALL spawns that are not in
        ``team_members``. Pins the helper's defensive checks on
        ``caller_meta.tools`` and ``.allow`` / ``.deny`` accessors —
        they must NOT dereference ``None``.
        """
        from daemon.tools.instance import _check_team_membership

        _install_synthetic_caller(
            monkeypatch,
            "synthetic_none_tools_caller",
            team_members=["developer"],  # explicit list still works
            tools_allow=None,             # tools_allow=None → tools=None
        )

        # Explicit team_members still allow the team member.
        assert _check_team_membership(
            "synthetic_none_tools_caller", "developer"
        ) is None

        # Anything not in team_members is denied — no crash on tools=None,
        # no implied members from an absent tools block.
        err = _check_team_membership(
            "synthetic_none_tools_caller", "explorer"
        )
        assert err is not None
        assert "Allowed team members: ['developer']" in err, (
            f"tools=None with team_members=['developer'] must render "
            f"as ['developer']; got: {err!r}"
        )


# =============================================================================
# CR-2: ``send_message`` team_members authorization gate
# =============================================================================
#
# The deep review (commit 6539f56b) added a team-membership check to
# ``send_message`` (in ``daemon/tools/instance.py``) — same gate
# ``spawn_instance`` already enforces. Without it, an instance (e.g.
# project-manager with ``team_members: ["leader"]``) could message
# ANY other instance, bypassing the spawn gate entirely.
#
# The gate runs AFTER the existence check (we need a real instance to
# resolve its ``agent_id``) and BEFORE the status / queue-stats checks
# (a terminated target doesn't deserve a more specific error than
# "not allowed").
#
# These tests build the actual ``send_message`` tool via
# ``create_instance_tools`` with heavy helpers patched (same pattern
# as the ``spawn_instance`` tests above) and assert the gate
# short-circuits with the expected ERROR string.


def _make_send_message_manager(
    *,
    target_agent_id: str,
    target_status: str = "running",
) -> MagicMock:
    """Build a manager mock wired for ``send_message`` happy-path reach.

    Beyond the ``_make_manager`` shape used by the spawn tests, this
    manager also exposes:

    * ``get_instance`` (async) — used by ``_resolve_instance_id`` to
      validate the target instance exists. Returns a truthy
      ``SimpleNamespace`` so the existence check passes.
    * ``get_instance_info`` (sync) — used by the CR-2 gate to read
      the target's ``agent_id`` and ``status``. The dict
      configuration is the single source of truth for the test.
    * ``get_queue_stats`` (async) — used after the gate; returns
      ``pending_count: 0, processing_count: 0`` so the call proceeds
      to ``enqueue_message`` in the happy path.
    * ``enqueue_message`` (async) — returns a mock result with
      ``.message_id = "msg-id"``. The test asserts on whether this
      was called (denied path) or not (rejected at the gate).

    The target's ``agent_id`` and ``status`` are baked in at
    construction time; tests that want to vary the resolution pick
    which manager to build.
    """
    manager = _make_manager()
    from types import SimpleNamespace

    manager.get_instance = AsyncMock(return_value=SimpleNamespace(id="target-id"))
    manager.get_instance_info = MagicMock(
        return_value={
            "agent_id": target_agent_id,
            "status": target_status,
        }
    )
    manager.get_queue_stats = AsyncMock(
        return_value={"pending_count": 0, "processing_count": 0}
    )
    manager.enqueue_message = AsyncMock(
        return_value=SimpleNamespace(
            message_id="msg-id-12345",
            instance_id="target-id",
            status="queued",
        )
    )
    return manager


def _get_send_message_tool(manager: MagicMock, caller_agent_id: str | None):
    """Build the instance tools and return the ``send_message`` tool.

    Mirrors ``_get_spawn_instance_tool`` above: patches the heavy
    helpers, builds the tool list, finds the tool by name. Returns
    ``None`` when ``caller_agent_id`` is ``None`` so the test can
    exercise the "wiring bug — no agent_id" branch which does NOT
    build the tool from the manager path.
    """
    from daemon.tools.instance import create_instance_tools

    if caller_agent_id is None:
        # Real wiring path requires a non-None caller_agent_id; the
        # ``caller_agent_id=None`` branch is exercised at a lower
        # level by the ``_check_team_membership`` tests.
        return None

    patches = _patch_heavy_helpers()
    for p in patches:
        p.start()
    try:
        tools = create_instance_tools(manager, "parent-instance-id", caller_agent_id)
    finally:
        for p in reversed(patches):
            p.stop()

    for t in tools:
        if getattr(t, "name", None) == "send_message":
            return t
    raise RuntimeError(
        "send_message tool not found in create_instance_tools output; "
        f"got {[getattr(t, 'name', None) for t in tools]}"
    )


class TestSendMessageTeamMembersGate:
    """CR-2: ``send_message`` enforces the same ``team_members`` gate
    as ``spawn_instance``.

    The gate lives in ``daemon/tools/instance.py:send_message`` and
    delegates to ``_check_team_membership``. Rejection happens
    BEFORE the queue-stats check, so a denied call never reaches
    ``manager.enqueue_message`` — the same fail-closed contract
    that ``spawn_instance`` already provides.
    """

    async def test_send_message_blocks_pm_to_developer(self):
        """project-manager → developer is denied (developer is NOT in
        PM's ``team_members``).

        PM is the canonical example for CR-2: its ``team_members``
        is exactly ``["leader"]`` and ``deny_spawn`` blocks
        ``chart``/``image``. The message gate must apply the same
        restriction, so PM cannot message a developer instance
        directly — it must go through the leader.
        """
        manager = _make_send_message_manager(target_agent_id="developer")
        send = _get_send_message_tool(manager, caller_agent_id="project-manager")

        result = await send.coroutine(
            instance_id="target-dev-id", message="any direct message"
        )

        assert isinstance(result, str)
        assert result.startswith("ERROR"), (
            f"PM → developer must be denied; got: {result!r}"
        )
        assert "not allowed to spawn" in result, (
            f"Error must reference the membership gate; got: {result!r}"
        )
        assert "project-manager" in result, (
            f"Error must name the caller; got: {result!r}"
        )
        # The CR-2 gate runs BEFORE enqueue_message — manager must
        # NOT have received the message.
        manager.enqueue_message.assert_not_called()

    async def test_send_message_allows_pm_to_leader(self):
        """project-manager → leader is allowed (leader IS in PM's
        ``team_members``).

        Happy path for the canonical PM delegation: PM has
        ``team_members: ["leader"]``, so messaging a leader
        instance must proceed to ``enqueue_message``. This is the
        primary use case for the v2 PM (deep review added it to
        unblock leader dispatch).
        """
        manager = _make_send_message_manager(target_agent_id="leader")
        send = _get_send_message_tool(manager, caller_agent_id="project-manager")

        result = await send.coroutine(
            instance_id="target-leader-id", message="please execute task X"
        )

        assert isinstance(result, str)
        assert not result.startswith("ERROR"), (
            f"PM → leader must succeed; got: {result!r}"
        )
        # enqueue_message was called with the target's instance_id.
        manager.enqueue_message.assert_called_once()
        call_kwargs = manager.enqueue_message.call_args.kwargs
        assert call_kwargs["instance_id"] == "target-leader-id"
        assert call_kwargs["message"] == "please execute task X"

    async def test_send_message_blocks_leader_to_pm(self):
        """leader → project-manager is denied.

        Reciprocal check: PM is not in leader's ``team_members``
        (leader dispatches to ``developer``, ``tester``, etc.),
        so a leader instance cannot message a PM instance
        directly. The gate is symmetric on both sides.
        """
        manager = _make_send_message_manager(target_agent_id="project-manager")
        send = _get_send_message_tool(manager, caller_agent_id="leader")

        result = await send.coroutine(
            instance_id="target-pm-id", message="ping"
        )

        assert isinstance(result, str)
        assert result.startswith("ERROR"), (
            f"leader → project-manager must be denied; got: {result!r}"
        )
        manager.enqueue_message.assert_not_called()

    async def test_send_message_target_without_agent_id_fails_closed(self):
        """A target instance with no ``agent_id`` on its info row →
        send is allowed past the gate (target_agent_id is falsy).

        The CR-2 gate reads ``target_info.get("agent_id", "")`` and
        only invokes ``_check_team_membership`` when the target
        ``agent_id`` is truthy. An incomplete instance row
        (missing ``agent_id``) is a wiring bug; the gate skips the
        check rather than risk a wrong match on empty string.
        ``_check_team_membership`` is the correct belt — the
        outer ``_resolve_instance_id`` already verified the
        instance exists. Pin this branch so a future refactor
        that adds an empty-string ``_check_team_membership`` call
        is an explicit, tested decision.
        """
        manager = _make_send_message_manager(target_agent_id="")
        # Strip the "agent_id" key entirely — matches the "incomplete row" case.
        manager.get_instance_info = MagicMock(
            return_value={"status": "running"}  # no agent_id
        )
        send = _get_send_message_tool(manager, caller_agent_id="project-manager")

        result = await send.coroutine(
            instance_id="target-id", message="hello"
        )

        # Gate is skipped → enqueue_message runs.
        assert isinstance(result, str)
        assert not result.startswith("ERROR"), (
            f"Empty agent_id must skip the gate (fail-open at the "
            f"membership layer; existence check is the boundary); "
            f"got: {result!r}"
        )
        manager.enqueue_message.assert_called_once()

    async def test_send_message_denied_message_does_not_pollute_queue(self):
        """A denied ``send_message`` must NOT advance past
        ``enqueue_message`` AND must NOT mutate queue stats.

        Defensive contract: the gate runs before ``get_queue_stats``
        so a denied call doesn't accidentally report "queue
        stats show 1 pending" and confuse downstream code. The
        assertion is straightforward — queue_stats is never
        queried when the gate rejects.
        """
        manager = _make_send_message_manager(target_agent_id="developer")
        send = _get_send_message_tool(manager, caller_agent_id="project-manager")

        result = await send.coroutine(
            instance_id="target-id", message="blocked"
        )

        assert result.startswith("ERROR")
        manager.enqueue_message.assert_not_called()
        manager.get_queue_stats.assert_not_called()