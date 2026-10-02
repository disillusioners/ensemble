"""F1a wire test — exercises the actual ``_tools_allow`` closure built by
``InstanceManager._wire_skill_injection_capability_gate``.

This is the test that would have caught the day-1 dead accessor bug
(review block on commit 954e06cb): the previous ``_tools_allow``
closure relied on ``self.get_agent_meta()`` which does not exist on
``InstanceManager`` (``hasattr`` was always False → ``meta`` always
``None`` → ``[]`` returned on every call). The unit tests for
``check_tool_capability`` (which feed the resolver a custom
``tools_allow=lambda``) never exercised the actual closure — the bug
slipped through.

F1a wire completion (2026-10-02): the corrected closure looks up the
instance's ``agent_id`` via ``_instance_repository`` (canonical source
at spawn time) and resolves the meta via ``get_registry().get_version``
+ ``get_resolved`` fallback — the project pattern every other meta
consumer uses. Pin the new wire path here.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from daemon.manager import InstanceManager
from daemon.registry import get_registry
from daemon.services.capability_resolver import check_tool_capability


def _make_manager_stub(instance_rows: dict[str, object]):
    """Build a stub with the deps the wire reads.

    The wire reads three things off ``self``:
      * ``_skill_injection_service`` (early-return guard)
      * ``_skill_bank_repo`` (requirement_lookup closure, irrelevant here)
      * ``_mcp_server_repository`` (mcp_lookup closure, irrelevant here)
      * ``_instance_repository`` (NEW — the F1a fix)

    Note: ``MagicMock`` auto-generates methods on attribute access, so
    ``manager._wire_skill_injection_capability_gate()`` would return a
    mock (call_count 0). Bind the real method from
    ``InstanceManager`` to the stub so the test exercises the actual
    wire code path.
    """
    manager = MagicMock()
    manager._skill_injection_service = MagicMock()
    manager._skill_bank_repo = MagicMock()
    manager._mcp_server_repository = MagicMock()

    # The repository lookup ``row = instance_repository.get(instance_id)``.
    # Real production uses SQLModel; here a dict-backed stub.
    def _get_instance(instance_id: str):
        return instance_rows.get(instance_id)

    manager._instance_repository = MagicMock()
    manager._instance_repository.get.side_effect = _get_instance

    # Bind the real wire function (MagicMock auto-generates a mock
    # method on attribute access, so we must rebind explicitly).
    manager._wire_skill_injection_capability_gate = (
        InstanceManager._wire_skill_injection_capability_gate.__get__(
            manager
        )
    )
    return manager


def _build_closure(manager):
    """Run the wire, capture the ``tools_allow`` callable, return it.

    The wire writes the closure into
    ``manager._skill_injection_service.set_capability_gate(...,
    tools_allow=..., ...)``. We return the callable directly so the
    tests can pin its behavior independent of the SkillInjectionService.
    """
    manager._wire_skill_injection_capability_gate()
    # The wire calls ``set_capability_gate`` exactly once with the
    # closure as the ``tools_allow`` keyword.
    assert (
        manager._skill_injection_service.set_capability_gate.call_count
        == 1
    ), "wire must call set_capability_gate exactly once"
    kwargs = manager._skill_injection_service.set_capability_gate.call_args.kwargs
    assert "tools_allow" in kwargs
    return kwargs["tools_allow"]


class TestF1aToolsAllowWire:
    """Pin the closure built by
    ``_wire_skill_injection_capability_gate``.

    Day-1 repro: ``_tools_allow()`` returned ``[]`` for every call
    because ``self.get_agent_meta()`` did not exist on
    ``InstanceManager``. The fix threads the active ``instance_id``
    into the closure so it can read the canonical agent config from
    ``_instance_repository`` + ``get_registry``.
    """

    def test_wire_resolves_real_tools_allow_for_known_agent(self):
        """Worker instance → tools_allow = worker's full allow list.

        The day-1 closure returned ``[]`` regardless of input. The fix
        looks up the instance row's ``agent_id``, resolves via
        ``get_registry().get_version`` (with ``get_resolved``
        fallback), and returns ``meta.tools.allow``. Worker has
        ``"bash"`` and ``"mcp"`` in its allow — pin both are present.
        """
        worker_meta = get_registry().get_resolved("worker")
        assert worker_meta is not None, (
            "test prerequisite: 'worker' agent must be registered"
        )

        # Build an instance row stub: agent_id="worker".
        row = MagicMock()
        row.agent_id = "worker"
        manager = _make_manager_stub({"inst-worker-1": row})

        tools_allow = _build_closure(manager)
        result = tools_allow("inst-worker-1")

        # The closure must surface the actual allow list — not [].
        assert isinstance(result, list)
        assert len(result) > 0, (
            "F1a wire regression: closure returned empty list for a "
            "real agent (day-1 dead accessor bug returned [] always)."
        )
        # Worker allow list contains bash and mcp per meta.json.
        assert "bash" in result
        assert "mcp" in result
        # Must match the registry's resolved view exactly.
        assert result == list(worker_meta.tools.allow)

    def test_wire_resolves_different_agents_independently(self):
        """Two instances, two different agents → two different allow lists.

        Confirms the closure resolves per-instance (not a singleton
        capture). Designer has ``design`` in allow; worker does not.
        """
        worker_row = MagicMock()
        worker_row.agent_id = "worker"
        designer_row = MagicMock()
        designer_row.agent_id = "designer"

        tools_allow = _build_closure(
            _make_manager_stub(
                {
                    "inst-worker": worker_row,
                    "inst-designer": designer_row,
                }
            )
        )

        worker_list = tools_allow("inst-worker")
        designer_list = tools_allow("inst-designer")

        # Different agents → different content.
        assert worker_list != designer_list
        # Designer has 'design' category; worker does not.
        assert "design" in designer_list
        assert "design" not in worker_list
        # Worker has 'plane_sync'; designer does not.
        assert "plane_sync" in worker_list
        assert "plane_sync" not in designer_list

    def test_wire_returns_empty_for_unknown_instance(self):
        """Unknown instance_id → ``[]`` (inherits universe under F1b).

        F1a wire behavior: an instance the repository can't find
        yields no agent context, the closure falls through to ``[]``.
        Under F1b's semantics that means "inherit/default universe"
        (state=present for every tool) — the safe default. Pin that
        the closure does NOT raise.
        """
        manager = _make_manager_stub({})  # no rows
        tools_allow = _build_closure(manager)

        assert tools_allow("inst-does-not-exist") == []
        assert tools_allow(None) == []  # no instance id → []

    def test_wire_returns_empty_for_instance_without_agent_id(self):
        """Instance row with agent_id=None → ``[]`` (defensive).

        Defensive: a corrupt row (no agent_id) should not raise and
        should not inject bogus state. F1a wire returns ``[]`` and
        lets F1b semantics resolve the result to "inherit/default
        universe".
        """
        row = MagicMock()
        row.agent_id = None
        manager = _make_manager_stub({"inst-corrupt": row})
        tools_allow = _build_closure(manager)

        assert tools_allow("inst-corrupt") == []

    def test_wire_returns_empty_for_unknown_agent_id(self):
        """Instance with unknown agent_id → ``[]`` (defensive).

        Defensive: an agent_id that the registry can't find should not
        propagate an exception into the gate. F1a wire falls through to
        ``[]``.
        """
        row = MagicMock()
        row.agent_id = "this-agent-does-not-exist"
        manager = _make_manager_stub({"inst-unknown-agent": row})
        tools_allow = _build_closure(manager)

        assert tools_allow("inst-unknown-agent") == []

    def test_wire_closure_feeds_resolver_with_real_list(self):
        """End-to-end: closure → resolver returns ``present`` for
        tools in the agent's allow list.

        Pre-F1a: closure returned ``[]``, resolver read empty list,
        F1b flipped empty→present so the worker always passed. But
        that masked the day-1 dead accessor — a non-empty
        ``tools.allow`` would never reach the resolver. This test
        pins the end-to-end contract: a worker instance with
        ``bash`` in allow must yield ``present`` for the ``bash``
        capability check, NOT just ``present`` because of the
        empty-allow fallback.
        """
        row = MagicMock()
        row.agent_id = "worker"
        manager = _make_manager_stub({"inst-worker-1": row})
        tools_allow = _build_closure(manager)

        # ``bash`` is in worker's allow — must be ``present``.
        result = check_tool_capability(
            "bash",
            tools_allow=tools_allow,
            instance_id="inst-worker-1",
        )
        assert result.state == "present"
        # The evidence should show ``bash`` actually being checked in
        # the list — not the empty-allow fallback.
        assert "-> present" in result.detection_evidence
        assert "empty allowlist" not in result.detection_evidence

        # ``definitely_not_a_real_tool_abc123`` is missing from any
        # worker's allow — must be ``missing``.
        result2 = check_tool_capability(
            "definitely_not_a_real_tool_abc123",
            tools_allow=tools_allow,
            instance_id="inst-worker-1",
        )
        assert result2.state == "missing"
        assert "not in" in result2.detection_evidence

    def test_wire_closure_pre_flight_fix_invariant_preserved(self):
        """Original F1b invariant — empty allow = present.

        Pin the F1b semantic alongside F1a wire completion: an agent
        with empty ``tools.allow`` (or no instance context at all)
        still resolves to ``[]`` and the resolver still treats that
        as the inherit/default universe. The wire fix must not
        regress the F1b semantic.
        """
        # No instance row → [] → F1b present.
        manager = _make_manager_stub({})
        tools_allow = _build_closure(manager)
        result = check_tool_capability(
            "bash",
            tools_allow=tools_allow,
            instance_id="inst-no-such",
        )
        assert result.state == "present"
        assert "empty allowlist" in result.detection_evidence