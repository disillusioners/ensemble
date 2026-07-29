"""Tests for version-tag aware tool resolution.

Covers the C1 fix: ``create_instance_tools()``, ``_apply_tool_filter()``,
``_check_team_membership()`` and ``load_tools_doc_for_agent()`` now accept
an optional ``version_tag`` parameter. When provided, they use
``registry.get_version(agent_id, version_tag)`` (preferring the versioned
meta) and fall back to ``registry.get_resolved(agent_id)`` if the
versioned meta is not found.

Pre-fix behavior (the bug): those helpers were VERSION-BLIND — they only
called ``registry.get_resolved(agent_id)`` and so a v2 instance got the
base (v1) tools.allow/deny, the base team_members policy, and the base
tools doc. The fix threads ``version_tag`` through to the registry
lookups.

These tests are pure unit tests. They use ``unittest.mock.patch`` to
stub ``daemon.registry.get_registry`` (the import target used inside the
helpers) and inject synthetic ``AgentMetadata`` objects whose ``tools``
and ``team_members`` differ between the versioned and base views.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from daemon.registry import AgentMetadata, ToolFilter, get_registry


# Minimal but realistic tool-category map mirroring the categories the
# helper uses (bash, filesystem, instance, ...). We keep it small so the
# test stays focused on the version-tag plumbing, not the registry scan.
TOOL_CATEGORIES: dict[str, list[str]] = {
    "bash": ["bash"],
    "filesystem": [
        "list_directory", "read_file", "write_file",
        "glob_files", "grep_files", "edit_file",
    ],
    "time": ["time"],
    "instance": [
        "spawn_instance", "send_message", "terminate_instance",
        "list_instances", "get_instance_info",
    ],
    "self": ["inner_soul", "access_memory"],
    "project": ["project_create", "project_get", "project_list"],
    "help": ["tool_help"],
    "mother": ["agent_list", "agent_create"],
    "knowledge": ["explore", "experience"],
}


# ─────────────────────────────────────────────────────────────────────────────
# Test fixtures / helpers
# ─────────────────────────────────────────────────────────────────────────────


class _MockTool:
    """Minimal tool stub — the helpers only read ``.name``."""

    def __init__(self, name: str):
        self.name = name


def _make_meta(
    agent_id: str,
    *,
    team_members: list[str] | None = None,
    tools_allow: list[str] | None = None,
    tools_deny: list[str] | None = None,
    innate_skills: list[str] | None = None,
) -> AgentMetadata:
    """Build a real ``AgentMetadata`` with optional tools/team_members.

    Using a real ``AgentMetadata`` (not a ``MagicMock``) is important
    because the helpers read typed attributes (``agent_meta.tools.allow``
    is a real list, not a Mock attribute). The ``tools`` filter is a
    real ``ToolFilter`` so Pydantic field access works.
    """
    tools = None
    if tools_allow is not None or tools_deny is not None:
        tools = ToolFilter(allow=list(tools_allow) if tools_allow else None,
                           deny=list(tools_deny) if tools_deny else None)
    return AgentMetadata(
        id=agent_id,
        name=agent_id,
        description=f"Synthetic {agent_id}",
        path=Path(f"/tmp/{agent_id}"),
        team_members=list(team_members) if team_members is not None else [],
        tools=tools,
        innate_skills=list(innate_skills) if innate_skills else [],
    )


def _make_registry_with_versions(
    *,
    base_meta: AgentMetadata | None,
    versioned_meta: AgentMetadata | None,
) -> MagicMock:
    """Build a mock registry with versioned + base resolution stubs.

    Both ``get_version`` and ``get_resolved`` accept the same ``(agent_id,
    ...)`` calling convention used by the helpers. ``resolve_pure_id``
    defaults to identity so tests don't need to wire alias resolution
    unless they care.

    IMPORTANT — ``fake_get_version`` mirrors the production contract:
    when called with ``version_tag=None``, it returns the base meta (not
    the versioned one). The production ``AgentRegistry.get_version``
    branches on ``version_tag is not None``: with None it returns
    ``self._agents.get(agent_id)`` (base); with a tag it looks up the
    composite key in ``self._versioned_agents``. A naïve
    ``MagicMock(return_value=...)`` would return the versioned meta
    regardless of argument, breaking the "version_tag=None → base
    meta" backward-compat tests. We must match production.
    """
    registry = MagicMock()

    def fake_get_version(agent_id: str, version_tag=None):
        # version_tag is None → base meta (per production).
        if version_tag is None:
            return base_meta
        # version_tag provided → versioned meta (None simulates unknown).
        return versioned_meta

    registry.get_version.side_effect = fake_get_version
    registry.get_resolved.return_value = base_meta
    # Identity resolve_pure_id so canonicalization is a no-op unless a
    # test overrides it (keeps tests simple).
    registry.resolve_pure_id.side_effect = lambda x: x
    return registry


# ─────────────────────────────────────────────────────────────────────────────
# Test 1: _apply_tool_filter uses versioned meta
# ─────────────────────────────────────────────────────────────────────────────


class TestApplyToolFilterUsesVersionedMeta:
    """When ``version_tag`` is provided, the helper MUST read the
    versioned meta's ``tools.allow``/``tools.deny`` — not the base
    resolved meta. This is the core bug being fixed.
    """

    def test_version_tag_v2_restricts_to_bash_only(self):
        """v2 has tools.allow=['bash']; calling the filter with
        version_tag='v2' must yield ONLY bash tools — instance tools
        must be excluded even though the base meta allows them.
        """
        from daemon.tools.instance import _apply_tool_filter

        base_meta = _make_meta(
            "reviewer",
            tools_allow=["bash", "filesystem", "instance"],
        )
        v2_meta = _make_meta(
            "reviewer",
            tools_allow=["bash"],  # v2 restricts to bash only
        )
        registry = _make_registry_with_versions(
            base_meta=base_meta, versioned_meta=v2_meta
        )

        tools = [
            _MockTool("bash"),
            _MockTool("read_file"),
            _MockTool("write_file"),
            _MockTool("spawn_instance"),
            _MockTool("send_message"),
        ]

        with patch("daemon.tools.instance.list_tools_by_category",
                   return_value=TOOL_CATEGORIES), \
             patch("daemon.registry.get_registry", return_value=registry):
            result = _apply_tool_filter(tools, "reviewer", version_tag="v2")

        result_names = {t.name for t in result}
        assert "bash" in result_names, "bash should be allowed by v2"
        # Filesystem tools and instance tools are NOT in v2's allow → excluded.
        for forbidden in ("read_file", "write_file", "spawn_instance",
                          "send_message"):
            assert forbidden not in result_names, (
                f"v2's allow=['bash'] must exclude {forbidden!r}; "
                f"got: {result_names}"
            )

    def test_version_tag_none_uses_base_meta_backward_compat(self):
        """When ``version_tag=None`` (default), the helper falls back to
        the base resolved meta. A v2-tagged meta must NOT be consulted
        in that case — this pins the backward-compatible contract.
        """
        from daemon.tools.instance import _apply_tool_filter

        base_meta = _make_meta(
            "reviewer",
            tools_allow=["bash", "filesystem", "instance"],
        )
        v2_meta = _make_meta(
            "reviewer",
            tools_allow=["bash"],  # present but must be ignored
        )
        registry = _make_registry_with_versions(
            base_meta=base_meta, versioned_meta=v2_meta
        )

        tools = [
            _MockTool("bash"),
            _MockTool("read_file"),
            _MockTool("spawn_instance"),
        ]

        with patch("daemon.tools.instance.list_tools_by_category",
                   return_value=TOOL_CATEGORIES), \
             patch("daemon.registry.get_registry", return_value=registry):
            result = _apply_tool_filter(tools, "reviewer")  # version_tag omitted

        result_names = {t.name for t in result}
        # Base meta's allow list applies (instance tools included).
        assert "bash" in result_names
        assert "read_file" in result_names
        assert "spawn_instance" in result_names


# ─────────────────────────────────────────────────────────────────────────────
# Test 2: _apply_tool_filter falls back to get_resolved when version unknown
# ─────────────────────────────────────────────────────────────────────────────


class TestApplyToolFilterFallbackOnUnknownVersion:
    """If ``get_version(agent_id, version_tag)`` returns ``None`` (the
    version is unknown / deleted), the helper MUST fall back to
    ``get_resolved(agent_id)`` rather than crashing or returning an
    empty list. This is the documented fallback in the helper docstring.
    """

    def test_unknown_version_falls_back_to_get_resolved(self):
        from daemon.tools.instance import _apply_tool_filter

        base_meta = _make_meta(
            "reviewer",
            tools_allow=["bash", "filesystem"],
        )
        registry = _make_registry_with_versions(
            base_meta=base_meta, versioned_meta=None,  # versioned unknown
        )

        tools = [
            _MockTool("bash"),
            _MockTool("read_file"),
            _MockTool("spawn_instance"),  # excluded by base allow
        ]

        with patch("daemon.tools.instance.list_tools_by_category",
                   return_value=TOOL_CATEGORIES), \
             patch("daemon.registry.get_registry", return_value=registry):
            result = _apply_tool_filter(tools, "reviewer", version_tag="v9")

        result_names = {t.name for t in result}
        assert "bash" in result_names
        assert "read_file" in result_names
        assert "spawn_instance" not in result_names, (
            "Fallback to base meta must still apply its allow/deny filter"
        )
        # Both registry methods were consulted in the expected order.
        registry.get_version.assert_called_with("reviewer", "v9")
        registry.get_resolved.assert_called_with("reviewer")

    def test_unknown_version_and_no_base_returns_all_tools(self):
        """If BOTH lookups return None (wiring bug / truly unknown
        agent), the helper returns the input list unchanged — same
        no-config behavior as the no-version case. Pins the
        'never crash' contract for the version path too.
        """
        from daemon.tools.instance import _apply_tool_filter

        registry = _make_registry_with_versions(
            base_meta=None, versioned_meta=None,
        )
        tools = [_MockTool("bash"), _MockTool("read_file")]

        with patch("daemon.tools.instance.list_tools_by_category",
                   return_value=TOOL_CATEGORIES), \
             patch("daemon.registry.get_registry", return_value=registry):
            result = _apply_tool_filter(tools, "ghost", version_tag="v9")

        assert {t.name for t in result} == {"bash", "read_file"}


# ─────────────────────────────────────────────────────────────────────────────
# Test 3: _check_team_membership uses versioned team_members
# ─────────────────────────────────────────────────────────────────────────────


class TestCheckTeamMembershipUsesVersionedMeta:
    """Pin the authorization fix: a v2 caller (e.g., reviewerv2) must
    enforce the v2 ``team_members`` list — not the base (v1) list.
    Without the fix, the auth gate would let through any target that
    the base (v1) reviewer is allowed to spawn, even though the v2
    policy has tightened the list.
    """

    def test_version_tag_v2_authorizes_only_v2_team_members(self):
        """v2 reviewer's team_members=['developer','coder']; calling
        with version_tag='v2' authorizes 'developer'. Base meta has
        empty team_members (deny-by-default), so without the fix the
        call would be REJECTED — the bug being fixed.
        """
        from daemon.tools.instance import _check_team_membership

        base_meta = _make_meta("reviewer", team_members=[])
        v2_meta = _make_meta("reviewer", team_members=["developer", "coder"])

        # ``_check_team_membership`` only consults ``get_version`` (and
        # optionally ``get_resolved`` as a fallback). ``resolve_pure_id``
        # is identity-canonicalized by the helper factory.
        registry = _make_registry_with_versions(
            base_meta=base_meta, versioned_meta=v2_meta,
        )

        with patch("daemon.registry.get_registry", return_value=registry):
            # v2-policy: developer is in v2's team → authorized.
            err = _check_team_membership(
                "reviewer", "developer", version_tag="v2"
            )
            assert err is None, (
                f"v2 reviewer's team includes 'developer'; expected "
                f"None, got: {err!r}"
            )

    def test_version_tag_none_uses_base_team_members(self):
        """Without ``version_tag``, the helper consults the base
        resolved meta. Base has empty team_members, so 'developer' is
        NOT in the base allow-set → rejected. This proves the helper
        falls back to base meta when no version_tag is provided.
        """
        from daemon.tools.instance import _check_team_membership

        base_meta = _make_meta("reviewer", team_members=[])
        v2_meta = _make_meta("reviewer", team_members=["developer", "coder"])

        # With the new helper, get_version(id, None) returns base_meta
        # (production behavior) — so the v2_meta is NOT consulted.
        registry = _make_registry_with_versions(
            base_meta=base_meta, versioned_meta=v2_meta,
        )

        with patch("daemon.registry.get_registry", return_value=registry):
            err = _check_team_membership("reviewer", "developer")
            assert err is not None, (
                "Base reviewer has empty team_members; expected rejection, "
                f"got None"
            )
            assert "not allowed to spawn" in err

    def test_unknown_version_tag_falls_back_to_base(self):
        """If the version_tag is unknown (deleted version),
        ``_check_team_membership`` falls back to the base meta. Base
        has empty team_members → reject. This pins the same fallback
        contract that ``_apply_tool_filter`` obeys.
        """
        from daemon.tools.instance import _check_team_membership

        base_meta = _make_meta("reviewer", team_members=[])
        # versioned_meta=None simulates "v999 doesn't exist".
        registry = _make_registry_with_versions(
            base_meta=base_meta, versioned_meta=None,
        )

        with patch("daemon.registry.get_registry", return_value=registry):
            err = _check_team_membership("reviewer", "developer",
                                          version_tag="v999")
            assert err is not None
            assert "not allowed to spawn" in err


# ─────────────────────────────────────────────────────────────────────────────
# Test 4: create_instance_tools threads version_tag through (smoke test)
# ─────────────────────────────────────────────────────────────────────────────


class TestCreateInstanceToolsThreadsVersionTag:
    """Smoke test for the higher-level wiring: the version_tag passed
    to ``create_instance_tools`` must be threaded into
    ``_apply_tool_filter`` so the returned tool list reflects the
    versioned (not base) ``tools.allow``/``tools.deny``.

    The full ``create_instance_tools`` factory builds a lot of helper
    tools (RAG, knowledge, MCP, etc.) that are out of scope for this
    fix. We patch them all to short-circuits so the call only exercises
    the version-tag plumbing — same pattern as
    ``tests/test_spawn_team_members.py::_patch_heavy_helpers``.
    """

    def test_version_tag_flows_into_apply_tool_filter(self):
        """When called with version_tag='v2', the inner
        ``_apply_tool_filter`` must receive the same ``version_tag``.
        Without the fix, _apply_tool_filter would have ignored the
        version and used base meta's allow list.
        """
        from daemon.tools.instance import create_instance_tools

        # Capture the kwargs passed to _apply_tool_filter so we can
        # assert that version_tag was forwarded.
        captured: dict = {}

        def fake_apply(tools, agent_id, mcp_tool_names=None,
                        version_tag=None):
            captured["agent_id"] = agent_id
            captured["version_tag"] = version_tag
            captured["mcp_tool_names"] = mcp_tool_names
            return tools  # no-op filter

        # Same heavy-helper stub pattern as test_spawn_team_members.py
        # so we only exercise the version-tag plumbing, not the rest of
        # the factory.
        heavy_patches = [
            patch("daemon.tools.instance.is_rag_enabled", return_value=False),
            patch("daemon.tools.instance.create_rag_tools", return_value=[]),
            patch("daemon.tools.instance.create_knowledge_tools", return_value=[]),
            patch("daemon.tools.instance.create_inner_soul_tool",
                  return_value=_MockTool("inner_soul")),
            patch("daemon.tools.instance.create_access_memory_tool",
                  return_value=_MockTool("access_memory")),
            patch("daemon.tools.instance.create_project_tools", return_value=[]),
            patch("daemon.tools.instance.create_job_tools_if_available",
                  return_value=[]),
            patch("daemon.tools.instance.create_help_tool",
                  return_value=_MockTool("tool_help")),
            patch("daemon.tools.instance.create_critical_notes_tools",
                  return_value=[]),
            patch("daemon.tools.instance.create_project_history_tools",
                  return_value=[]),
            patch("daemon.tools.instance.create_opencode_tools",
                  return_value=[]),
            patch("daemon.tools.instance.create_db_tools", return_value=[]),
            patch("daemon.tools.instance.create_infra_tools", return_value=[]),
            patch("daemon.tools.instance.create_context_tools", return_value=[]),
            patch("daemon.tools.instance.create_chart_tools", return_value=[]),
            patch("daemon.tools.instance._load_mcp_tools", return_value=[]),
            patch("daemon.tools.instance.scan_tools_for_full_docs"),
            patch("daemon.tools.instance._apply_tool_filter",
                  side_effect=fake_apply),
        ]

        # Minimal manager stub — create_instance_tools doesn't call
        # any manager methods (those happen inside the tool closures),
        # but the signature requires a manager parameter.
        manager = MagicMock()

        for p in heavy_patches:
            p.start()
        try:
            create_instance_tools(
                manager,
                current_instance_id="parent-iid",
                agent_id="reviewer",
                version_tag="v2",
            )
        finally:
            for p in reversed(heavy_patches):
                p.stop()

        assert captured.get("agent_id") == "reviewer"
        assert captured.get("version_tag") == "v2", (
            f"create_instance_tools must thread version_tag into "
            f"_apply_tool_filter; got version_tag={captured.get('version_tag')!r}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Test 5: Backward compat — version_tag=None is identical to old behavior
# ─────────────────────────────────────────────────────────────────────────────


class TestBackwardCompatibilityVersionTagNone:
    """When ``version_tag`` is omitted (defaults to ``None``), the
    helpers MUST behave exactly as they did before the fix: consult
    the base ``get_resolved`` meta only. This is the backward-compat
    contract that keeps every existing caller (which doesn't pass a
    version_tag) working unchanged.
    """

    def test_apply_tool_filter_version_tag_none_never_calls_get_version_with_tag(self):
        """``_apply_tool_filter(tools, agent_id)`` — no version_tag —
        must NOT pass a tag into ``get_version``. (A MagicMock called
        with no kwargs records ``call_args.kwargs == {}`` and
        ``call_args.args == (agent_id,)``.)
        """
        from daemon.tools.instance import _apply_tool_filter

        base_meta = _make_meta("reviewer", tools_allow=["bash"])
        registry = _make_registry_with_versions(
            base_meta=base_meta, versioned_meta=None,
        )

        with patch("daemon.tools.instance.list_tools_by_category",
                   return_value=TOOL_CATEGORIES), \
             patch("daemon.registry.get_registry", return_value=registry):
            _apply_tool_filter([_MockTool("bash")], "reviewer")

        # get_version must have been called (the helper always tries
        # it first) but with version_tag=None — i.e. as
        # ``get_version(agent_id, None)`` or
        # ``get_version(agent_id, version_tag=None)``.
        assert registry.get_version.called
        call_args = registry.get_version.call_args
        # The first positional arg is always agent_id.
        assert call_args.args[0] == "reviewer"
        # Second arg (positional or keyword) must be None.
        version_arg = (
            call_args.args[1] if len(call_args.args) > 1
            else call_args.kwargs.get("version_tag")
        )
        assert version_arg is None, (
            f"version_tag=None must propagate as None to get_version; "
            f"got {version_arg!r}"
        )

    def test_check_team_membership_version_tag_none_never_calls_get_version_with_tag(self):
        """``_check_team_membership(caller, requested)`` — no
        version_tag — must pass ``None`` into ``get_version`` (the
        helper always tries it first, then falls back to base).
        """
        from daemon.tools.instance import _check_team_membership

        base_meta = _make_meta("reviewer", team_members=["developer"])
        registry = _make_registry_with_versions(
            base_meta=base_meta, versioned_meta=None,
        )

        with patch("daemon.registry.get_registry", return_value=registry):
            err = _check_team_membership("reviewer", "developer")

        # Happy path with base meta: developer is in team → None.
        assert err is None, f"Expected authorized, got: {err!r}"
        # get_version must have been consulted with version_tag=None.
        assert registry.get_version.called
        call_args = registry.get_version.call_args
        version_arg = (
            call_args.args[1] if len(call_args.args) > 1
            else call_args.kwargs.get("version_tag")
        )
        assert version_arg is None


# ─────────────────────────────────────────────────────────────────────────────
# Test 6: Integration sanity — versioned meta is preferred over base meta
# ─────────────────────────────────────────────────────────────────────────────


class TestVersionedMetaPreferredOverBaseMeta:
    """When BOTH ``get_version(agent_id, version_tag)`` and
    ``get_resolved(agent_id)`` return non-None meta, the versioned
    meta MUST win. This is the central contract — without it, the
    'prefer versioned' design intent is silently broken.
    """

    def test_apply_tool_filter_uses_versioned_meta_over_base(self):
        """v2 allows ['bash']; base allows ['bash', 'filesystem'].
        With version_tag='v2', only bash survives — proves the helper
        consults the versioned meta, not the base.
        """
        from daemon.tools.instance import _apply_tool_filter

        base_meta = _make_meta("reviewer",
                                tools_allow=["bash", "filesystem"])
        v2_meta = _make_meta("reviewer", tools_allow=["bash"])
        registry = _make_registry_with_versions(
            base_meta=base_meta, versioned_meta=v2_meta
        )

        tools = [_MockTool("bash"), _MockTool("read_file")]

        with patch("daemon.tools.instance.list_tools_by_category",
                   return_value=TOOL_CATEGORIES), \
             patch("daemon.registry.get_registry", return_value=registry):
            result = _apply_tool_filter(tools, "reviewer", version_tag="v2")

        result_names = {t.name for t in result}
        assert "bash" in result_names
        assert "read_file" not in result_names, (
            "If the helper were using the base meta, 'read_file' would "
            "be allowed. Its exclusion proves the versioned meta won."
        )

    def test_check_team_membership_uses_versioned_meta_over_base(self):
        """v2 team_members=['developer']; base team_members=[].
        With version_tag='v2', 'developer' is authorized — proves the
        helper consults the versioned meta's team_members.
        """
        from daemon.tools.instance import _check_team_membership

        base_meta = _make_meta("reviewer", team_members=[])
        v2_meta = _make_meta("reviewer", team_members=["developer"])
        registry = _make_registry_with_versions(
            base_meta=base_meta, versioned_meta=v2_meta,
        )

        with patch("daemon.registry.get_registry", return_value=registry):
            err = _check_team_membership(
                "reviewer", "developer", version_tag="v2"
            )

        assert err is None, (
            f"v2's team includes 'developer'; expected None, got: {err!r}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Test 7: load_tools_doc_for_agent uses versioned meta's tools.allow
# ─────────────────────────────────────────────────────────────────────────────


class TestLoadToolsDocForAgentUsesVersionedMeta:
    """Pin the documentation-fix contract: ``load_tools_doc_for_agent``
    (in ``daemon/loader.py``) must consult the versioned meta's
    ``tools.allow`` when ``version_tag`` is provided. Without the fix,
    a v2-restricted agent would see the base (v1) tool list in its
    help docs — masking the versioned restriction.

    ``load_tools_doc_for_agent`` lazy-imports ``get_registry``,
    ``resolve_tool_filter``, ``expand_allow_for_innate_skills``,
    ``get_tool_categories``, ``get_category_doc``, and ``_tool_metadata``
    from sibling modules — those are the patch targets. To avoid
    triggering the heavy ``_ensure_tool_metadata_populated`` import
    path inside the helper, we pre-populate ``_tool_metadata`` with a
    sentinel so the guard short-circuits.
    """

    def _ensure_metadata_populated_sentinel(self, monkeypatch):
        """Force ``_tool_metadata`` to be truthy for the duration of
        this test so ``load_tools_doc_for_agent`` skips the heavy
        tool-module import path inside ``_ensure_tool_metadata_populated``.
        """
        from daemon.tools import _tool_registry as _reg

        monkeypatch.setattr(
            _reg, "_tool_metadata",
            {"__sentinel__": {"category": None, "short_doc": "", "full_doc": ""}},
            raising=False,
        )

    def test_version_tag_v2_filters_doc_to_bash_only(self, monkeypatch):
        """v2 has ``tools.allow=['bash']``. ``load_tools_doc_for_agent``
        with ``version_tag='v2'`` must surface ONLY bash in the rendered
        docs — filesystem tools must NOT appear, even though the base
        meta allows them. This is the help-docs version of the bug
        being fixed: a v2 instance reading its own tool docs would
        otherwise see filesystem tools it cannot invoke.
        """
        from daemon.loader import load_tools_doc_for_agent

        self._ensure_metadata_populated_sentinel(monkeypatch)

        base_meta = _make_meta(
            "reviewer",
            tools_allow=["bash", "filesystem"],
        )
        v2_meta = _make_meta(
            "reviewer",
            tools_allow=["bash"],  # v2 restricts to bash only
        )
        registry = _make_registry_with_versions(
            base_meta=base_meta, versioned_meta=v2_meta,
        )

        # Return predictable (allow, deny) → set mapping. The exact
        # implementation of resolve_tool_filter is irrelevant; what we
        # pin is that the helper is invoked with the v2 allow list.
        captured = {}

        def fake_resolve(allow, deny, tool_categories=None,
                         all_tool_names=None):
            captured["allow"] = list(allow) if allow else None
            captured["deny"] = list(deny) if deny else None
            # Return a set derived from the allow list so the
            # ``get_tool_categories`` path can render sections.
            if allow is None:
                return None
            out: set[str] = set()
            for item in allow:
                if item == "bash":
                    out.add("bash")
                elif item == "filesystem":
                    out |= {"read_file", "write_file", "list_directory"}
            return out

        def fake_categories(allowed):
            if allowed is None:
                return {"Bash": ["bash"], "Filesystem": ["read_file"]}
            # Bucket each tool by simple prefix category so the doc
            # builder produces visible section names.
            cats: dict[str, list[str]] = {}
            for name in sorted(allowed):
                cat = "Bash" if name == "bash" else "Filesystem"
                cats.setdefault(cat, []).append(name)
            return cats

        def fake_category_doc(key):
            return (key.title(), f"doc-for-{key}")

        # Expand the v2 allow list (no innate skills).
        def fake_expand(allow, innate_skills):
            return list(allow) if allow else None

        # ``load_tools_doc_for_agent`` lazy-imports its deps from
        # their source modules (``daemon.registry``,
        # ``daemon.tools.instance``, ``daemon.tools._tool_registry``)
        # — patch those, not ``daemon.loader``.
        with patch("daemon.registry.get_registry", return_value=registry), \
             patch("daemon.tools.instance.resolve_tool_filter",
                   side_effect=fake_resolve), \
             patch("daemon.tools.instance.expand_allow_for_innate_skills",
                   side_effect=fake_expand), \
             patch("daemon.tools._tool_registry.get_tool_categories",
                   side_effect=fake_categories), \
             patch("daemon.tools._tool_registry.get_category_doc",
                   side_effect=fake_category_doc):
            result = load_tools_doc_for_agent(
                "reviewer", version_tag="v2",
            )

        # The registry MUST have been consulted with version_tag="v2"
        # (proves the helper is threading the tag, not silently
        # dropping to base).
        registry.get_version.assert_called_with("reviewer", "v2")

        # The versioned meta's allow list (NOT base) was used.
        assert captured["allow"] == ["bash"], (
            f"v2 restricts tools.allow to ['bash']; got {captured['allow']!r}"
        )

        # The rendered docs reflect the v2 allow list — bash appears,
        # filesystem tools do NOT. The versioned meta won.
        assert "bash" in result, f"v2 bash tool missing from doc: {result!r}"
        for forbidden in ("read_file", "write_file", "list_directory"):
            assert forbidden not in result, (
                f"v2 docs must not mention {forbidden!r} (not allowed); "
                f"got: {result!r}"
            )

    def test_version_tag_none_uses_base_meta_filter(self, monkeypatch):
        """Backward-compat — with ``version_tag=None`` (default), the
        helper consults ``get_version(id, None)`` (returns base per
        production) and so MUST use base meta's ``tools.allow``. v2's
        restriction is irrelevant in this path.
        """
        from daemon.loader import load_tools_doc_for_agent

        self._ensure_metadata_populated_sentinel(monkeypatch)

        base_meta = _make_meta(
            "reviewer",
            tools_allow=["bash", "filesystem"],
        )
        v2_meta = _make_meta(
            "reviewer",
            tools_allow=["bash"],  # v2 — must be IGNORED with version_tag=None
        )
        registry = _make_registry_with_versions(
            base_meta=base_meta, versioned_meta=v2_meta,
        )

        captured = {}

        def fake_resolve(allow, deny, tool_categories=None,
                         all_tool_names=None):
            captured["allow"] = list(allow) if allow else None
            if allow is None:
                return None
            out: set[str] = set()
            for item in allow:
                if item == "bash":
                    out.add("bash")
                elif item == "filesystem":
                    out |= {"read_file", "write_file", "list_directory"}
            return out

        def fake_categories(allowed):
            if allowed is None:
                return {"Bash": ["bash"], "Filesystem": ["read_file"]}
            cats: dict[str, list[str]] = {}
            for name in sorted(allowed):
                cat = "Bash" if name == "bash" else "Filesystem"
                cats.setdefault(cat, []).append(name)
            return cats

        def fake_category_doc(key):
            return (key.title(), f"doc-for-{key}")

        def fake_expand(allow, innate_skills):
            return list(allow) if allow else None

        with patch("daemon.registry.get_registry", return_value=registry), \
             patch("daemon.tools.instance.resolve_tool_filter",
                   side_effect=fake_resolve), \
             patch("daemon.tools.instance.expand_allow_for_innate_skills",
                   side_effect=fake_expand), \
             patch("daemon.tools._tool_registry.get_tool_categories",
                   side_effect=fake_categories), \
             patch("daemon.tools._tool_registry.get_category_doc",
                   side_effect=fake_category_doc):
            result = load_tools_doc_for_agent("reviewer")  # no version_tag

        # registry MUST have been consulted with version_tag=None
        # (the helper always tries it first; production get_version
        # returns base_meta when version_tag=None).
        assert registry.get_version.called
        call_args = registry.get_version.call_args
        version_arg = (
            call_args.args[1] if len(call_args.args) > 1
            else call_args.kwargs.get("version_tag")
        )
        assert version_arg is None, (
            f"version_tag=None must propagate as None; got {version_arg!r}"
        )

        # Base meta's allow list (full set) was used — NOT v2's.
        assert captured["allow"] == ["bash", "filesystem"], (
            f"Base meta allows ['bash', 'filesystem']; got "
            f"{captured['allow']!r}"
        )
        # Rendered docs include BOTH bash and filesystem tools.
        assert "bash" in result
        assert "read_file" in result


# ─────────────────────────────────────────────────────────────────────────────
# Test 8: _get_allowed_tools / create_help_tool use versioned meta's tools.allow
# ─────────────────────────────────────────────────────────────────────────────


class TestGetAllowedToolsUsesVersionedMeta:
    """Pin the help-tool fix: ``_get_allowed_tools`` in
    ``daemon/tools/help.py`` (and the ``create_help_tool`` wrapper
    that invokes it) MUST consult the versioned meta's
    ``tools.allow`` when ``version_tag`` is provided. Without the fix,
    a v2-restricted agent's ``tool_help()`` would report tools it
    cannot actually invoke — same class of bug as
    ``_apply_tool_filter``.

    These helpers lazy-import their dependencies inside the function
    body, so the patch targets are ``daemon.tools.help.<name>`` (not
    the underlying module).
    """

    def test_version_tag_v2_returns_only_v2_filter_set(self):
        """``_get_allowed_tools("reviewer", version_tag="v2")`` must
        return ONLY the v2 ``tools.allow`` set — filesystem tools are
        excluded even though base meta allows them.
        """
        from daemon.tools.help import _get_allowed_tools

        base_meta = _make_meta(
            "reviewer",
            tools_allow=["bash", "filesystem"],
        )
        v2_meta = _make_meta(
            "reviewer",
            tools_allow=["bash"],  # v2 restricts
        )
        registry = _make_registry_with_versions(
            base_meta=base_meta, versioned_meta=v2_meta,
        )

        # Stub the downstream ``resolve_tool_filter`` so the test does
        # not need a populated tool-category registry. We capture the
        # allow list the helper passes IN — that's the contract:
        # ``_get_allowed_tools`` must pass the v2 allow list, not base.
        captured = {}

        def fake_resolve(allow, deny, all_tool_names=None):
            captured["allow"] = list(allow) if allow else None
            return set(allow) if allow else None

        with patch("daemon.registry.get_registry", return_value=registry), \
             patch("daemon.tools.instance.resolve_tool_filter",
                   side_effect=fake_resolve), \
             patch("daemon.tools.instance.expand_allow_for_innate_skills",
                   side_effect=lambda a, s: list(a) if a else None):
            result = _get_allowed_tools("reviewer", version_tag="v2")

        registry.get_version.assert_called_with("reviewer", "v2")
        assert captured["allow"] == ["bash"], (
            f"v2 allow=['bash'] expected; got {captured['allow']!r}"
        )
        assert result == {"bash"}, (
            f"v2-restricted set must be {{'bash'}}; got {result!r}"
        )

    def test_version_tag_none_uses_base_filter_set(self):
        """Backward-compat — ``_get_allowed_tools("reviewer")`` (no
        version_tag) must use base meta's ``tools.allow``. v2's
        restriction is irrelevant in this path.
        """
        from daemon.tools.help import _get_allowed_tools

        base_meta = _make_meta(
            "reviewer",
            tools_allow=["bash", "filesystem"],
        )
        v2_meta = _make_meta(
            "reviewer",
            tools_allow=["bash"],  # present but ignored
        )
        registry = _make_registry_with_versions(
            base_meta=base_meta, versioned_meta=v2_meta,
        )

        captured = {}

        def fake_resolve(allow, deny, all_tool_names=None):
            captured["allow"] = list(allow) if allow else None
            return set(allow) if allow else None

        with patch("daemon.registry.get_registry", return_value=registry), \
             patch("daemon.tools.instance.resolve_tool_filter",
                   side_effect=fake_resolve), \
             patch("daemon.tools.instance.expand_allow_for_innate_skills",
                   side_effect=lambda a, s: list(a) if a else None):
            # No MCP names — fresh signature; no version_tag.
            result = _get_allowed_tools("reviewer")

        # registry called with version_tag=None.
        assert registry.get_version.called
        call_args = registry.get_version.call_args
        version_arg = (
            call_args.args[1] if len(call_args.args) > 1
            else call_args.kwargs.get("version_tag")
        )
        assert version_arg is None

        # Base allow list passed in (full set), v2 ignored.
        assert captured["allow"] == ["bash", "filesystem"], (
            f"Base allow=['bash','filesystem'] expected; got "
            f"{captured['allow']!r}"
        )
        # Result is the base set (filesystem is back).
        assert result == {"bash", "filesystem"}, (
            f"Base meta set must include filesystem; got {result!r}"
        )

    def test_create_help_tool_threads_version_tag_into_filter(self):
        """``create_help_tool(tools, 'reviewer', version_tag='v2')``
        must consult the versioned meta when the returned ``tool_help``
        is invoked. The tool's filtering path goes through
        ``_get_allowed_tools``, so we patch that and verify v2 wins.
        """
        from daemon.tools.help import create_help_tool

        base_meta = _make_meta(
            "reviewer",
            tools_allow=["bash", "filesystem"],
        )
        v2_meta = _make_meta(
            "reviewer",
            tools_allow=["bash"],
        )
        registry = _make_registry_with_versions(
            base_meta=base_meta, versioned_meta=v2_meta,
        )

        # The wrapped tool's ``tool_help`` (no args) calls
        # ``_get_allowed_tools(agent_id, mcp_tool_names, version_tag)``
        # to decide which tools to list. We patch it and capture the
        # call to confirm the version_tag was forwarded.
        import daemon.tools.help as help_mod

        captured = {}

        def fake_get_allowed(agent_id, mcp_tool_names=None,
                             version_tag=None):
            captured["agent_id"] = agent_id
            captured["version_tag"] = version_tag
            # Return v2's restricted set.
            return {"bash"} if version_tag == "v2" else None

        with patch.object(help_mod, "_get_allowed_tools",
                          side_effect=fake_get_allowed), \
             patch.object(help_mod, "get_tool_categories",
                         return_value={"Bash": ["bash"]}), \
             patch.object(help_mod, "list_tools_by_category",
                         return_value={"bash": ["bash"]}), \
             patch("daemon.registry.get_registry", return_value=registry):
            help_tool = create_help_tool(
                [_MockTool("bash")], "reviewer", version_tag="v2",
            )
            # ``create_help_tool`` returns a langchain ``BaseTool``;
            # call via ``.invoke({})`` (no args → list all tools).
            result = help_tool.invoke({})

        assert captured.get("agent_id") == "reviewer"
        assert captured.get("version_tag") == "v2", (
            "create_help_tool must thread version_tag into "
            "_get_allowed_tools; got "
            f"{captured.get('version_tag')!r}"
        )
        # Bash is in v2's allow list — must appear in the doc string.
        assert "bash" in result, (
            f"v2 allow=['bash']; expected bash in tool_help output, "
            f"got: {result!r}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Test 9: closure-level — spawn_instance honors v2 team_members
# ─────────────────────────────────────────────────────────────────────────────


class TestClosureLevelSpawnInstanceUsesVersionedMeta:
    """Closure-level integration: build the actual ``spawn_instance``
    tool via ``create_instance_tools(..., version_tag='v2')`` and verify
    it honors the v2 ``team_members`` list (not the base empty list).

    Complements the unit-level ``_check_team_membership`` tests above by
    exercising the full closure wiring (capture of ``caller_version_tag``,
    forwarding into the auth helper, and the manager dispatch path).
    """

    async def test_v2_team_members_authorize_coder_and_deny_developer(self):
        """v2 ``reviewer.team_members=['coder']``; base ``team_members=[]``.

        Closure path:
          * ``agent_id='coder'`` → allowed (v2 policy), spawn succeeds.
          * ``agent_id='developer'`` → denied (v2 doesn't include it).

        Without the version-tag fix the v2 caller would see the base
        empty list and both spawns would be rejected. We deliberately
        do NOT patch ``_check_team_membership`` — the whole point is to
        verify the closure passes ``version_tag='v2'`` into it.
        """
        from daemon.tools.instance import create_instance_tools

        # Registry: base has empty team_members (deny-by-default); v2
        # adds 'coder'. ``resolve_pure_id`` is identity so reviewer /
        # coder / developer all canonicalize to themselves (matches the
        # production registry with no aliases).
        base_meta = _make_meta("reviewer", team_members=[])
        v2_meta = _make_meta("reviewer", team_members=["coder"])
        registry = _make_registry_with_versions(
            base_meta=base_meta, versioned_meta=v2_meta,
        )

        # Manager wired per tests/test_spawn_team_members.py so the
        # spawn_instance closure reaches manager.spawn_instance(...).
        manager = MagicMock()
        manager._lifecycle_service = MagicMock()
        manager._lifecycle_service._format_model_fallback_notice = (
            MagicMock(return_value="")
        )
        manager.spawn_instance = MagicMock(
            return_value=("new-instance-id-12345", None)
        )
        manager._instance_repository = MagicMock()
        manager._instance_repository.get = MagicMock(return_value=None)

        # Mirror the heavy-helper stub pattern (lines 416-443) so the
        # factory only exercises the version-tag plumbing.
        # ``_apply_tool_filter`` is a no-op so spawn_instance remains
        # in the returned tool list.
        def _noop_filter(tools, agent_id, mcp_tool_names=None,
                          version_tag=None):
            return tools

        heavy_patches = [
            patch("daemon.tools.instance.is_rag_enabled", return_value=False),
            patch("daemon.tools.instance.create_rag_tools", return_value=[]),
            patch("daemon.tools.instance.create_knowledge_tools", return_value=[]),
            patch("daemon.tools.instance.create_inner_soul_tool",
                  return_value=_MockTool("inner_soul")),
            patch("daemon.tools.instance.create_access_memory_tool",
                  return_value=_MockTool("access_memory")),
            patch("daemon.tools.instance.create_project_tools", return_value=[]),
            patch("daemon.tools.instance.create_job_tools_if_available",
                  return_value=[]),
            patch("daemon.tools.instance.create_help_tool",
                  return_value=_MockTool("tool_help")),
            patch("daemon.tools.instance.create_critical_notes_tools",
                  return_value=[]),
            patch("daemon.tools.instance.create_project_history_tools",
                  return_value=[]),
            patch("daemon.tools.instance.create_opencode_tools",
                  return_value=[]),
            patch("daemon.tools.instance.create_db_tools", return_value=[]),
            patch("daemon.tools.instance.create_infra_tools", return_value=[]),
            patch("daemon.tools.instance.create_context_tools", return_value=[]),
            patch("daemon.tools.instance.create_chart_tools", return_value=[]),
            patch("daemon.tools.instance._load_mcp_tools", return_value=[]),
            patch("daemon.tools.instance.scan_tools_for_full_docs"),
            patch("daemon.tools.instance._apply_tool_filter",
                  side_effect=_noop_filter),
        ]

        # Build the real spawn_instance tool with version_tag='v2'.
        # CRITICAL: the ``patch`` for ``get_registry`` MUST stay active
        # across BOTH factory build-time AND the closure invocation —
        # ``_check_team_membership`` calls ``get_registry()`` inside
        # ``spawn_instance`` at run-time. We keep the tool invocation
        # inside the ``with`` block instead of exiting it prematurely.
        for p in heavy_patches:
            p.start()
        try:
            with patch("daemon.registry.get_registry", return_value=registry):
                tools = create_instance_tools(
                    manager,
                    current_instance_id="parent-iid",
                    agent_id="reviewer",
                    version_tag="v2",
                )
                spawn = next(
                    t for t in tools
                    if getattr(t, "name", None) == "spawn_instance"
                )

                # ── Allowed: 'coder' is in v2's team_members → spawn
                # succeeds. Must run inside the registry-patch context
                # so the auth gate consults our v2_meta.
                result_ok = await spawn.coroutine(
                    agent_id="coder", project_id="test-project-id",
                )
                # ── Denied: 'developer' is NOT in v2's team_members →
                # ERROR. Same registry-patch context required.
                result_denied = await spawn.coroutine(agent_id="developer")
        finally:
            for p in reversed(heavy_patches):
                p.stop()
        assert isinstance(result_ok, str)
        assert not result_ok.startswith("ERROR"), (
            f"v2 reviewer.team_members=['coder'] must authorize 'coder'; "
            f"got: {result_ok!r}"
        )
        assert "new-instance-id-12345" in result_ok, (
            f"Success result must include the spawned instance_id; "
            f"got: {result_ok!r}"
        )
        manager.spawn_instance.assert_called_once()
        first_kwargs = manager.spawn_instance.call_args.kwargs
        assert first_kwargs["agent_id"] == "coder"

        assert isinstance(result_denied, str)
        assert result_denied.startswith("ERROR"), (
            f"v2 reviewer.team_members=['coder'] must deny 'developer'; "
            f"got: {result_denied!r}"
        )
        assert "not allowed to spawn" in result_denied, (
            f"Closure must surface the team_members denial; "
            f"got: {result_denied!r}"
        )
        # CRITICAL: no additional manager.spawn_instance call — the
        # auth gate runs BEFORE the manager dispatch.
        manager.spawn_instance.assert_called_once()


# ─────────────────────────────────────────────────────────────────────────────
# Test 10: closure-level — convene_council honors v2 team_members gate
# ─────────────────────────────────────────────────────────────────────────────


class TestClosureLevelConveneCouncilUsesVersionedMeta:
    """Closure-level integration for the council convening tool.

    The ``convene_council`` closure gates the council convening path
    on the caller's ``team_members`` containing ``"governor"`` (the
    agent the closure spawns to run the council). With
    ``version_tag='v2'``, this gate MUST consult the versioned meta —
    not the base empty list.

    NOTE on the user's stated fixture: the task brief described the
    v2 governor as ``team_members=['reviewer']``, but the closure
    hard-codes the authorization target as ``"governor"`` (the agent
    it spawns). We therefore reconcile by setting
    ``v2_meta.team_members=['governor']`` so the closure's actual gate
    passes; this still faithfully exercises
    ``get_version('governor', 'v2')`` (the central C1 contract) and
    proves the v2 membership gate is consulted end-to-end. We also
    do NOT patch ``_check_team_membership`` — the closure threads
    ``caller_version_tag`` into the real helper.
    """

    async def test_v2_governor_team_members_authorize_convene_council(self):
        """v2 ``governor.team_members=['governor']``; base ``team_members=[]``.

        Closure path:
          * ``convene_council(...)`` → authorization passes (v2 policy
            includes 'governor'), ``manager.spawn_instance`` is called
            with ``agent_id='governor'``.

        Without the version-tag fix, the closure would consult the base
        empty ``team_members`` and ``_check_team_membership`` would
        reject with the standard "not allowed to spawn 'governor'"
        denial — and the closure would raise ``ValueError`` before
        reaching ``manager.spawn_instance``.
        """
        from daemon.tools.instance import create_instance_tools

        # Registry: base has empty team_members; v2 includes 'governor'
        # (reconciled with the closure's hard-coded target).
        # ``resolve_pure_id`` is identity so 'governor' canonicalizes to
        # itself.
        base_meta = _make_meta("governor", team_members=[])
        v2_meta = _make_meta("governor", team_members=["governor"])
        registry = _make_registry_with_versions(
            base_meta=base_meta, versioned_meta=v2_meta,
        )
        # ``convene_council`` calls ``get_registry().resolve_to_id(...)``
        # on the *councilor_agent_id* (a separate lookup from the auth
        # helper). Our mock must return a truthy canonical id so the
        # closure proceeds past the ``if not canonical`` guard.
        registry.resolve_to_id.return_value = "developer"

        # Manager: spawn_instance returns a tuple; enqueue_message is an
        # AsyncMock because the closure awaits it.
        manager = MagicMock()
        manager.spawn_instance = MagicMock(
            return_value=("governor-instance-id", None)
        )
        manager.enqueue_message = AsyncMock()

        # Mirror the heavy-helper stub pattern (lines 416-443) so the
        # factory only exercises the version-tag plumbing.
        # ``_apply_tool_filter`` is a no-op so convene_council survives.
        def _noop_filter(tools, agent_id, mcp_tool_names=None,
                          version_tag=None):
            return tools

        heavy_patches = [
            patch("daemon.tools.instance.is_rag_enabled", return_value=False),
            patch("daemon.tools.instance.create_rag_tools", return_value=[]),
            patch("daemon.tools.instance.create_knowledge_tools", return_value=[]),
            patch("daemon.tools.instance.create_inner_soul_tool",
                  return_value=_MockTool("inner_soul")),
            patch("daemon.tools.instance.create_access_memory_tool",
                  return_value=_MockTool("access_memory")),
            patch("daemon.tools.instance.create_project_tools", return_value=[]),
            patch("daemon.tools.instance.create_job_tools_if_available",
                  return_value=[]),
            patch("daemon.tools.instance.create_help_tool",
                  return_value=_MockTool("tool_help")),
            patch("daemon.tools.instance.create_critical_notes_tools",
                  return_value=[]),
            patch("daemon.tools.instance.create_project_history_tools",
                  return_value=[]),
            patch("daemon.tools.instance.create_opencode_tools",
                  return_value=[]),
            patch("daemon.tools.instance.create_db_tools", return_value=[]),
            patch("daemon.tools.instance.create_infra_tools", return_value=[]),
            patch("daemon.tools.instance.create_context_tools", return_value=[]),
            patch("daemon.tools.instance.create_chart_tools", return_value=[]),
            patch("daemon.tools.instance._load_mcp_tools", return_value=[]),
            patch("daemon.tools.instance.scan_tools_for_full_docs"),
            patch("daemon.tools.instance._apply_tool_filter",
                  side_effect=_noop_filter),
        ]

        # Build and invoke the real convene_council tool with version_tag='v2'.
        # Keep the registry patch active through invocation because both the
        # authorization helper and the closure resolve agents at run time.
        for p in heavy_patches:
            p.start()
        try:
            with patch("daemon.registry.get_registry", return_value=registry):
                tools = create_instance_tools(
                    manager,
                    current_instance_id="parent-iid",
                    agent_id="governor",
                    version_tag="v2",
                )
                convene = next(
                    t for t in tools
                    if getattr(t, "name", None) == "convene_council"
                )

                # Invoke with minimal valid arguments per the closure signature
                # (councilor_agent_id + request are required; models /
                # max_councilors / instance_name are optional).
                result = await convene.coroutine(
                    councilor_agent_id="developer",
                    request="Refactor X",
                )
        finally:
            for p in reversed(heavy_patches):
                p.stop()

        # The v2 team-membership gate MUST have PASSED — i.e. the
        # closure did NOT raise a ValueError carrying a team-members
        # denial. The gate's signature rejection is
        # ``"not allowed to spawn 'governor'"`` so we explicitly assert
        # that string is absent from the result path.
        assert isinstance(result, dict), (
            f"convene_council must return a dict on success; got: "
            f"{type(result).__name__}: {result!r}"
        )
        assert "not allowed to spawn" not in str(result), (
            f"v2 governor.team_members=['governor'] must satisfy the "
            f"membership gate; result should NOT carry a denial. "
            f"Got: {result!r}"
        )
        assert result.get("status") == "convened", (
            f"v2-authorized convene_council must reach the success "
            f"branch; got: {result!r}"
        )
        assert result.get("governor_instance_id") == "governor-instance-id", (
            f"Result must echo the spawned governor's instance_id; "
            f"got: {result!r}"
        )

        # Spawn fired exactly once with the right kwargs — the closure's
        # non-block dispatch path. If the membership gate had rejected,
        # the closure would have raised ValueError BEFORE this call.
        manager.spawn_instance.assert_called_once_with(
            agent_id="governor",
            parent_id="parent-iid",
            instance_name=None,
            version_tag=None,
        )
        # enqueue_message was awaited with the governor's id.
        manager.enqueue_message.assert_awaited_once()
        enqueue_kwargs = manager.enqueue_message.await_args.kwargs
        assert enqueue_kwargs["instance_id"] == "governor-instance-id"
