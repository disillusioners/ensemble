"""Tests for dynamic-toolset category expansion in ``resolve_tool_filter``.

Covers the D1 fix (2026-09-20, plane-revival final-gate defect register):
an allow/deny entry naming a DYNAMIC toolset (``DYNAMIC_TOOL_PREFIXES``,
e.g. the built-in Plane MCP server's ``plane`` / ``plane_*`` namespace)
must expand to the server's ACTUAL tool surface at agent-tool-build time.

Pre-fix mechanism (confirmed by source trace + live proof): the expansion
at ``daemon/tools/instance.py`` matched only ``name.startswith("mcp_")``;
a ``"plane"`` allow entry fell through to literal-name matching (``allowed
_tools.add("plane")``) so every ``plane_*`` tool was silently dropped at
the ``_apply_tool_filter`` membership test (``tool_name in allowed_tools``).
The ``plane`` category is registered EMPTY (``daemon/tools/plane_tools.py``
is a metadata stub — tools are runtime-created by ``create_lazy_mcp_tools``),
which is why the older ``TestResolveToolFilterPlaneVsMcp`` tests (which
PRE-POPULATE ``tool_categories``) passed while production dropped tools.

These tests use the PRODUCTION registry shape (``list_tools_by_category()``
with its empty ``plane`` category) wherever the older tests diverged from
production, plus an end-to-end ``_apply_tool_filter`` test through the real
drop site with a ``project-manager``-shaped meta (``tools.allow`` contains
``"plane"`` — agents/project-manager/meta.json).
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from daemon.registry import AgentMetadata, ToolFilter
from daemon.tools._tool_registry import (
    DYNAMIC_TOOL_PREFIXES,
    list_tools_by_category,
    scan_tools_for_full_docs,
)
from daemon.tools.instance import _apply_tool_filter, resolve_tool_filter


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures / helpers
# ─────────────────────────────────────────────────────────────────────────────


class _MockTool:
    """Minimal tool stub — ``_apply_tool_filter`` only reads ``.name``."""

    def __init__(self, name: str):
        self.name = name


def _make_meta(
    agent_id: str,
    *,
    tools_allow: list[str] | None = None,
    tools_deny: list[str] | None = None,
) -> AgentMetadata:
    """Build a real ``AgentMetadata`` with a real ``ToolFilter``.

    Real Pydantic models (not MagicMocks) because ``_apply_tool_filter``
    reads typed attributes (``agent_meta.tools.allow``).
    """
    tools = None
    if tools_allow is not None or tools_deny is not None:
        tools = ToolFilter(
            allow=list(tools_allow) if tools_allow else None,
            deny=list(tools_deny) if tools_deny else None,
        )
    return AgentMetadata(
        id=agent_id,
        name=agent_id,
        description=f"Synthetic {agent_id}",
        path=Path(f"/tmp/{agent_id}"),
        team_members=[],
        tools=tools,
        innate_skills=[],
    )


def _production_categories() -> dict[str, list[str]]:
    """The REAL default categories — the shape production resolves against.

    Production populates ``_tool_metadata`` by scanning the built tools
    BEFORE filtering (``create_instance_tools`` → ``scan_tools_for_full_docs``
    immediately before ``_apply_tool_filter``), so we scan directly here.
    (The loader's ``_ensure_tool_metadata_populated()`` is unusable in the
    test env: it early-returns once ``_tool_metadata`` is truthy, and the
    conftest pre-registers ``language_skip_check`` at import.)

    Key property (the coverage gap this suite closes): a dynamic toolset
    like ``plane`` is ABSENT-or-EMPTY in these categories — its tools are
    runtime-created by ``create_lazy_mcp_tools`` and carry no static
    registration. That absence is precisely the pre-fix trap: a
    ``"plane"`` allow entry found no category to expand and fell through
    to literal-name matching. The D1 expansion loop populates the
    category from ``all_tool_names`` whether it is missing or empty.
    """
    from daemon.tools.bash import bash
    from daemon.tools.filesystem import list_directory, read_file
    from daemon.tools.time import time

    scan_tools_for_full_docs([bash, list_directory, read_file, time])
    return dict(list_tools_by_category())


PLANE_TOOLS = {"plane_list_issues", "plane_create_issue", "plane_delete_issue"}
MCP_TOOLS = {"mcp_ctx7_get_docs", "mcp_webfetch_fetch"}
OTHER_TOOLS = {"bash", "read_file", "project_get"}


def _fake_registry(meta: AgentMetadata) -> MagicMock:
    registry = MagicMock()
    registry.get_version.return_value = meta
    registry.get_resolved.return_value = meta
    return registry


# ─────────────────────────────────────────────────────────────────────────────
# Positive: allow entry naming a dynamic toolset expands to the live surface
# ─────────────────────────────────────────────────────────────────────────────


class TestDynamicAllowExpansion:
    """POSITIVE — ``allow=['plane']`` resolves the server's actual tools."""

    def test_plane_allow_expands_against_production_registry(self):
        """Production shape: real registry (empty ``plane`` category) +
        live plane tool names → ``allow=['plane']`` returns the plane tools.

        Pre-fix this returned ``{"plane"}`` (the literal entry) and every
        ``plane_*`` tool was dropped downstream.
        """
        categories = _production_categories()
        assert not categories.get("plane")  # precondition: absent-or-empty

        result = resolve_tool_filter(
            allow=["plane"],
            deny=None,
            tool_categories=categories,
            all_tool_names=PLANE_TOOLS | MCP_TOOLS | OTHER_TOOLS,
        )

        assert result is not None
        assert PLANE_TOOLS <= result
        # No cross-namespace bleed: mcp_* and unrelated tools stay out.
        assert not (MCP_TOOLS & result)
        assert not (OTHER_TOOLS & result)

    def test_plane_allow_mixed_with_literal_tools(self):
        """A realistic allow list mixing literals, categories, and the
        dynamic toolset resolves all three."""
        categories = _production_categories()

        result = resolve_tool_filter(
            allow=["plane", "read_file", "filesystem"],
            deny=None,
            tool_categories=categories,
            all_tool_names=PLANE_TOOLS | MCP_TOOLS | OTHER_TOOLS,
        )

        assert result is not None
        assert PLANE_TOOLS <= result
        assert "read_file" in result
        assert not (MCP_TOOLS & result)

    def test_pm_allow_survives_apply_tool_filter(self):
        """END-TO-END through the real drop site: a project-manager-shaped
        meta (allow contains ``'plane'``, deny contains ``'mcp'`` — the
        actual agents/project-manager/meta.json shape) keeps ``plane_*``
        tools in the built toolset.

        Pre-fix, ``_apply_tool_filter`` dropped every plane tool here
        (the ``tool_name in allowed_tools`` membership test) — this is the
        exact live-observed D1 failure.
        """
        meta = _make_meta(
            "project-manager",
            tools_allow=[
                "explore", "project_get", "filesystem", "plane", "instance",
            ],
            tools_deny=["mcp"],
        )
        tools = (
            [_MockTool(n) for n in sorted(OTHER_TOOLS)]
            + [_MockTool(n) for n in sorted(PLANE_TOOLS)]
            + [_MockTool(n) for n in sorted(MCP_TOOLS)]
        )

        with patch("daemon.registry.get_registry", return_value=_fake_registry(meta)):
            filtered = _apply_tool_filter(
                tools,
                "project-manager",
                mcp_tool_names=sorted(PLANE_TOOLS | MCP_TOOLS),
            )

        names = {t.name for t in filtered}
        assert PLANE_TOOLS <= names  # THE fix: plane tools survive
        assert not (MCP_TOOLS & names)  # deny=['mcp'] still wins
        assert "read_file" in names  # 'filesystem' category entry intact
        assert "bash" not in names  # never allowed (no 'bash' entry)


# ─────────────────────────────────────────────────────────────────────────────
# Negative: no server row → graceful absence, no phantom tools
# ─────────────────────────────────────────────────────────────────────────────


class TestDynamicAllowWithoutServer:
    """NEGATIVE — with no plane server surface, nothing phantom appears."""

    def test_no_plane_names_yields_no_phantom_tools(self):
        """Server row absent → ``all_tool_names`` has no plane names → the
        expansion resolves to an empty set; other allow entries unaffected;
        no crash."""
        categories = _production_categories()

        result = resolve_tool_filter(
            allow=["plane", "read_file"],
            deny=None,
            tool_categories=categories,
            all_tool_names=MCP_TOOLS | OTHER_TOOLS,  # no plane_* names
        )

        assert result is not None
        assert not any(name.startswith("plane_") for name in result)
        assert result == {"read_file"}

    def test_no_plane_names_apply_tool_filter_drops_none_extra(self):
        """Same through the drop site: agents keep their non-plane tools;
        plane tools are simply absent (they were never in the toolset)."""
        meta = _make_meta(
            "project-manager",
            tools_allow=["filesystem", "plane"],
            tools_deny=None,
        )
        tools = [_MockTool(n) for n in sorted(OTHER_TOOLS)]

        with patch("daemon.registry.get_registry", return_value=_fake_registry(meta)):
            filtered = _apply_tool_filter(
                tools,
                "project-manager",
                mcp_tool_names=sorted(MCP_TOOLS),  # another server's tools only
            )

        names = {t.name for t in filtered}
        assert not any(name.startswith("plane_") for name in names)
        assert "read_file" in names  # 'filesystem' allow entry still works


# ─────────────────────────────────────────────────────────────────────────────
# Incumbent: the mcp_ expansion keeps its historical shape
# ─────────────────────────────────────────────────────────────────────────────


class TestIncumbentMcpExpansionUnchanged:
    """The incumbent ``mcp`` lane must be regression-free."""

    def test_mcp_allow_still_expands(self):
        """``allow=['mcp']`` expands mcp_* tools exactly as before.

        Uses the incumbent lane's exercised shape (empty ``mcp`` category
        + ``all_tool_names``), the same contract
        ``TestResolveToolFilterPlaneVsMcp`` pins."""
        categories = {"mcp": [], "plane": []}

        result = resolve_tool_filter(
            allow=["mcp"],
            deny=None,
            tool_categories=categories,
            all_tool_names=PLANE_TOOLS | MCP_TOOLS,
        )

        assert result is not None
        assert MCP_TOOLS <= result
        # plane_* never leaked into the mcp category (isolation contract
        # pinned by TestResolveToolFilterPlaneVsMcp — kept here on the
        # production-shaped input).
        assert not (PLANE_TOOLS & result)

    def test_populated_plane_category_not_clobbered(self):
        """When a caller pre-populates the dynamic category (the older
        tests' shape, or a future statically-registered tool), the fix
        must trust it — never clobber or double-expand."""
        categories = _production_categories()
        categories["plane"] = ["plane_custom_static"]

        result = resolve_tool_filter(
            allow=["plane"],
            deny=None,
            tool_categories=categories,
            all_tool_names=PLANE_TOOLS,
        )

        assert result == {"plane_custom_static"}


# ─────────────────────────────────────────────────────────────────────────────
# Deny direction + genericity
# ─────────────────────────────────────────────────────────────────────────────


class TestDenyAndGenericity:
    """``deny`` targets the toolset; the mechanism is prefix-generic."""

    def test_deny_plane_removes_live_plane_tools(self):
        """``deny=['plane']`` strips live plane tools from the default
        universe (deny-side expansion mirrors allow-side)."""
        categories = _production_categories()

        result = resolve_tool_filter(
            allow=None,
            deny=["plane"],
            tool_categories=categories,
            all_tool_names=PLANE_TOOLS | MCP_TOOLS | OTHER_TOOLS,
        )

        assert result is not None
        assert not (PLANE_TOOLS & result)
        # The scanned default universe survives untouched.
        assert {"bash", "read_file"} <= result

    def test_future_dynamic_prefix_needs_no_new_filter_code(self):
        """GENERIC contract: an allow entry naming ANY dynamic toolset
        resolves — proven with a synthetic prefix registered only in the
        constant (monkeypatched at the importing module). No plane or
        mcp special-casing in the expansion loop."""
        from daemon.tools import instance as instance_module

        categories = _production_categories()
        synthetic_all = {"jira_search", "jira_create", "read_file"}

        with patch.object(
            instance_module,
            "DYNAMIC_TOOL_PREFIXES",
            frozenset({*DYNAMIC_TOOL_PREFIXES, "jira_"}),
        ):
            result = resolve_tool_filter(
                allow=["jira"],
                deny=None,
                tool_categories=categories,
                all_tool_names=synthetic_all,
            )

        assert result == {"jira_search", "jira_create"}


# ─────────────────────────────────────────────────────────────────────────────
# Real-seam pin: drives ``create_instance_tools`` end-to-end (no mock at the
# filter surface). Closes the reviewer-flagged D1 gap — the expansion only
# works if the real spawn path threads ``mcp_tool_names`` (and thus
# ``all_tool_names``) all the way down to ``resolve_tool_filter``.
# ─────────────────────────────────────────────────────────────────────────────


class TestRealSpawnSeamForDynamicExpansion:
    """REAL-seam pin: ``create_instance_tools()`` (the public spawn-path
    entry point, called from ``instance_lifecycle.py:1766``) with a plane
    server row present and ``tools.allow=['plane']`` must surface
    ``plane_*`` tools in the resolved toolset.

    Mirrors the ``_build_instance_tools`` pattern from
    ``tests/unit/tools/test_service_registration.py`` — drives the real
    factory with a synthetic agent staged on disk + a MagicMock manager
    whose ``_mcp_service.get_mcp_tools`` returns stub plane tools (no
    live MCP server, no DB, no network).

    The D1 expansion loop at ``daemon/tools/instance.py:362-375``
    populates the ``plane`` category from ``all_tool_names``; if the
    real path drops ``mcp_tool_names`` anywhere between
    ``_load_mcp_tools`` (instance.py:4835) and the
    ``resolve_tool_filter`` call (instance.py:4959), the plane tools
    silently vanish — exactly the live-observed pre-fix defect.

    To isolate the D1 expansion from the name-prefix-inference
    side-effect of ``scan_tools_for_full_docs`` (which infers
    ``category="plane"`` from a tool name's first underscore-separated
    token at ``_tool_registry.py:478-480`` and would otherwise mask
    the expansion as a no-op), we patch
    ``daemon.tools.instance.list_tools_by_category`` to return an
    empty registry — the canonical pre-D1 production shape (per
    ``.agents/tester/RESULTS/2026-09-20-plane-revival-verification.md``,
    ``tool_categories["plane"]`` was ``[]``). The D1 expansion is
    what closes the gap when no other mechanism populates the
    category.
    """

    REPO_ROOT = Path(__file__).resolve().parents[3]

    @staticmethod
    def _stage_synthetic_agent(
        agents_dir: Path,
        agent_id: str,
        tools_allow: list[str],
    ) -> Path:
        """Clone watcher/meta.json's shape; replace id + tools.allow."""
        import json

        repo_root = Path(__file__).resolve().parents[3]
        real_meta = json.loads(
            (repo_root / "agents" / "watcher" / "meta.json").read_text(
                encoding="utf-8"
            )
        )
        real_meta.pop("watchover", None)
        real_meta["id"] = agent_id
        real_meta["name"] = agent_id.title()
        real_meta["team_members"] = []
        real_meta["innate_skills"] = []
        real_meta["tools"] = {"allow": tools_allow, "deny": None}
        agent_dir = agents_dir / agent_id
        agent_dir.mkdir(parents=True, exist_ok=True)
        (agent_dir / "meta.json").write_text(
            json.dumps(real_meta), encoding="utf-8"
        )
        return agents_dir

    @staticmethod
    def _install_registry(
        agents_dir: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """Real AgentRegistry pointed at the synthetic agents_dir."""
        import daemon.registry as dr
        from daemon.registry import AgentRegistry

        registry = AgentRegistry(agents_dir)
        registry.discover()
        monkeypatch.setattr(dr, "_registry", registry)
        return registry

    def test_create_instance_tools_threads_plane_through_real_seam(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """End-to-end through the REAL factory: synthetic PM-shaped
        agent (``tools.allow=['plane']``) + manager whose MCP service
        reports a plane server row + empty registry (canonical
        pre-D1 shape) → ``create_instance_tools`` returns ``plane_*``
        tools in the resolved toolset.

        Pre-D1, this returned no plane tools: the ``_apply_tool_filter``
        membership test (``tool_name in allowed_tools``) saw
        ``allowed_tools == {"plane"}`` (literal-name matching) and
        dropped every ``plane_*`` tool. The fix threads
        ``mcp_tool_names`` → ``all_tool_names`` → ``resolve_tool_filter``
        expansion loop, populating the plane category from the live
        surface so the membership test passes.

        Drives the REAL ``create_instance_tools`` call site — no
        patching of ``_apply_tool_filter`` or ``resolve_tool_filter``
        at the surface. Mirrors
        ``test_service_registration._build_instance_tools``.

        The ``list_tools_by_category`` patch is the key to
        isolating the D1 expansion — without it, the
        name-prefix inference in ``scan_tools_for_full_docs``
        (``_tool_registry.py:478-480``) silently populates the
        plane category from the plane tool names, masking the
        expansion. The empty-registry patch reproduces the
        canonical pre-D1 production shape.
        """
        from daemon.tools.instance import create_instance_tools

        agents_dir = tmp_path / "agents"
        self._stage_synthetic_agent(agents_dir, "syn-pm-plane", ["plane"])
        self._install_registry(agents_dir, monkeypatch)

        # Stub MCP service row — no live MCP server needed. Plane
        # server exposes tools under the "plane_" prefix (per
        # PlaneServerDefinition.tool_name_prefix at plane.py:159).
        plane_tool_names = [
            "plane_list_issues",
            "plane_get_issue",
            "plane_search_issues",
        ]
        mcp_service = MagicMock(name="McpService")
        mcp_service.get_mcp_tools.return_value = [
            _MockTool(n) for n in plane_tool_names
        ]

        manager = MagicMock(name="InstanceManager")
        manager.config.daemon.port = 0
        manager.config.limits.max_children_per_instance = 0
        manager.config.llm.allowed_models = []
        manager._mcp_service = mcp_service

        # Canonical pre-D1 production shape: empty registry, no
        # plane category. See the test class docstring for the
        # isolation rationale.
        from daemon.tools import instance as instance_module

        monkeypatch.setattr(
            instance_module,
            "list_tools_by_category",
            lambda: {},
        )

        tools = create_instance_tools(
            manager, "inst-syn-pm-plane", "syn-pm-plane"
        )

        resolved_names = {getattr(t, "name", None) for t in tools}
        for name in plane_tool_names:
            assert name in resolved_names, (
                f"D1 expansion failed at the REAL spawn seam: "
                f"{name!r} missing from create_instance_tools() output "
                f"(resolved={sorted(n for n in resolved_names if n)})"
            )

    def test_no_plane_server_row_yields_no_phantom_tools(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Negative companion: with the MCP service reporting NO plane
        tools (server row absent) and an empty registry, ``allow=['plane']``
        resolves to the empty set — no phantom tools injected. Same
        real seam, same mocking style; differs only in the MCP stub."""
        from daemon.tools.instance import create_instance_tools

        agents_dir = tmp_path / "agents"
        self._stage_synthetic_agent(agents_dir, "syn-pm-no-row", ["plane"])
        self._install_registry(agents_dir, monkeypatch)

        # MCP service present but reports NO plane tools (other servers
        # only — or empty). Combined with the empty registry below,
        # the D1 expansion has no source names to populate from.
        mcp_service = MagicMock(name="McpService")
        mcp_service.get_mcp_tools.return_value = [
            _MockTool("mcp_ctx7_get_docs"),
        ]

        manager = MagicMock(name="InstanceManager")
        manager.config.daemon.port = 0
        manager.config.limits.max_children_per_instance = 0
        manager.config.llm.allowed_models = []
        manager._mcp_service = mcp_service

        from daemon.tools import instance as instance_module

        monkeypatch.setattr(
            instance_module,
            "list_tools_by_category",
            lambda: {},
        )

        tools = create_instance_tools(
            manager, "inst-syn-pm-no-row", "syn-pm-no-row"
        )

        resolved_names = {getattr(t, "name", None) for t in tools}
        # Assert: NO MCP-plane tool names (the ones the MCP service
        # would have returned). ``plane_sync_project`` is the static
        # sync tool that ``create_plane_sync_tools`` adds directly to
        # every instance — it is NOT an MCP-fetched plane_* tool, so
        # its appearance here is expected and unrelated to the D1
        # expansion seam.
        mcp_plane_names = {"plane_list_issues", "plane_get_issue",
                           "plane_search_issues", "plane_create_issue"}
        leaked = mcp_plane_names & resolved_names
        assert not leaked, (
            f"no-row path produced phantom MCP-plane tools: "
            f"{sorted(leaked)} — the D1 expansion is using a non-empty "
            f"all_tool_names source it shouldn't have"
        )
