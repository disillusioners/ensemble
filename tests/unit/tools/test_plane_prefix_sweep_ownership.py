"""P1 — the prefix-sweep OWNERSHIP guard (lane ``plane-pm-issue-tools`` Phase 3).

The four pin cases (phase3-plan §5):

* (a) server **absent** ⇒ category ``"plane"`` must NOT contain
  ``plane_sync_project`` and resolving PM's ``allow=["plane"]`` must not
  yield it;
* (b) server **registered** ⇒ ``"plane"`` contains every ``plane_*`` name
  and NOT ``plane_sync_project``;
* (c) **positive control** — the worker still gets ``plane_sync_project``
  through its ``"plane_sync"`` allow entry;
* (d) the ``mcp_`` lane's ``owned_elsewhere`` behaviour is unchanged.

**G2 fold NB4 — the mandatory synthetic fixture.** The production
``_production_categories()`` seam never populates ``"plane_sync"`` (the
category is registered by the decorator but its tool list is empty in
that seam), so the "another category owns a plane_* tool" precondition is
ABSENT there and case (a) would pass vacuously. This pack builds its own
category map in which ``"plane_sync"`` holds a synthetic
``plane_sync_project`` alongside an EMPTY ``"plane"`` (the server-absent
shape), so the leak the FC9 boot confirmed is reproducible at unit level.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from daemon.registry import AgentMetadata, ToolFilter
from daemon.tools.instance import _apply_tool_filter, resolve_tool_filter

# Real upstream verbs (gate (i) catalog) — never the drifted *_issue names.
RUNTIME_PLANE_TOOLS = {
    "plane_list_work_items",
    "plane_retrieve_work_item",
    "plane_create_work_item",
    "plane_update_work_item",
    "plane_manage_work_item_assignee",
}
SYNTHETIC_OWNER = "plane_sync_project"


def _categories(
    *, plane_registered: bool, plane_sync_tools: list[str] | None = None
) -> dict[str, list[str]]:
    """A synthetic ownership map with a POPULATED ``plane_sync`` category.

    The category map IS the ownership map. ``plane_sync_tools`` defaults
    to the synthetic ``plane_sync_project`` so the precondition for the
    leak (a plane_*-NAMED tool owned by ANOTHER category) is real.
    """
    if plane_sync_tools is None:
        plane_sync_tools = [SYNTHETIC_OWNER]
    return {
        "filesystem": ["read_file", "list_directory"],
        "plane_sync": list(plane_sync_tools),
        # server-absent shape: the plane category is present but EMPTY
        "plane": [] if not plane_registered else [],
    }


def _make_meta(
    agent_id: str,
    *,
    tools_allow: list[str] | None = None,
    tools_deny: list[str] | None = None,
) -> AgentMetadata:
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


class TestOwnershipGuard:
    """P1 (a)–(d)."""

    def test_a_server_absent_plane_sync_not_swept_into_plane(self):
        """(a) The FC9-confirmed leak: with the plane MCP server NOT
        registered, the sweep must not pull ``plane_sync_project`` (owned
        by ``plane_sync``) into the empty ``plane`` category. Asserted on
        the RESOLUTION (the function copies the category map — the
        expansion lands in the copy the allow-list resolves against)."""
        categories = _categories(plane_registered=False)
        all_names = set(RUNTIME_PLANE_TOOLS) | {SYNTHETIC_OWNER} | {"read_file"}

        result = resolve_tool_filter(
            allow=["plane"],
            deny=None,
            tool_categories=categories,
            all_tool_names=all_names,
        )
        assert result is not None
        # With the plane server ABSENT the universe carries no runtime
        # plane tools — only the plane_sync-owned tool matches the
        # prefix, and the guard excludes it ⇒ allow=['plane'] resolves
        # NOTHING (the pre-guard defect granted the sync tool here).
        assert SYNTHETIC_OWNER not in result

    def test_a_pm_allow_plane_does_not_grant_plane_sync_project(self):
        """(a, agent side) PM's ``allow=['plane']`` resolves the OWNED
        plane tools only — never the plane_sync-owned tool."""
        categories = _categories(plane_registered=False)
        all_names = set(RUNTIME_PLANE_TOOLS) | {SYNTHETIC_OWNER} | {"read_file"}

        result = resolve_tool_filter(
            allow=["plane"],
            deny=None,
            tool_categories=categories,
            all_tool_names=all_names,
        )
        assert result is not None
        assert SYNTHETIC_OWNER not in result

    def test_b_registered_plane_contains_all_plane_tools_not_the_owner(self):
        """(b) With the server REGISTERED, the ``plane`` category contains
        every runtime ``plane_*`` name and NOT the synthetic owner."""
        categories = _categories(plane_registered=True)
        categories["plane"] = sorted(RUNTIME_PLANE_TOOLS)
        all_names = set(RUNTIME_PLANE_TOOLS) | {SYNTHETIC_OWNER} | {"read_file"}

        result = resolve_tool_filter(
            allow=["plane"],
            deny=None,
            tool_categories=categories,
            all_tool_names=all_names,
        )
        assert result is not None
        assert RUNTIME_PLANE_TOOLS <= result
        assert SYNTHETIC_OWNER not in result

    def test_c_worker_still_gets_plane_sync_project(self):
        """(c) Positive control: the worker's ``plane_sync`` allow entry
        keeps delivering ``plane_sync_project`` (NR3)."""
        categories = _categories(plane_registered=False)
        all_names = set(RUNTIME_PLANE_TOOLS) | {SYNTHETIC_OWNER} | {"read_file"}

        result = resolve_tool_filter(
            allow=["plane_sync"],
            deny=None,
            tool_categories=categories,
            all_tool_names=all_names,
        )
        assert result is not None
        assert SYNTHETIC_OWNER in result

    def test_d_mcp_lane_owned_elsewhere_unchanged(self):
        """(d) NR6 — the ``mcp_`` lane's guard keeps excluding
        other-category owners from the ``mcp`` sweep."""
        categories = {
            "mcp_rpc": ["mcp_rpc_shell"],  # a privileged category owner
            "mcp": [],
        }
        all_names = {"mcp_ctx7_get_docs", "mcp_webfetch_fetch", "mcp_rpc_shell"}

        result = resolve_tool_filter(
            allow=["mcp"],
            deny=None,
            tool_categories=categories,
            all_tool_names=all_names,
        )
        assert result is not None
        assert "mcp_rpc_shell" not in result
        assert {"mcp_ctx7_get_docs", "mcp_webfetch_fetch"} <= result

    def test_server_absent_pm_resolves_nothing_from_plane(self):
        """The end-to-end agent shape: server absent + no plane names in
        the universe ⇒ ``allow=['plane']`` resolves NOTHING for PM."""
        categories = _categories(plane_registered=False)
        result = resolve_tool_filter(
            allow=["plane"],
            deny=None,
            tool_categories=categories,
            all_tool_names={SYNTHETIC_OWNER, "read_file"},
        )
        assert result is not None
        assert result == set()


class TestBothServerStatesThroughApplyToolFilter:
    """SC5 — ``plane_sync_project`` is absent from PM's toolset in BOTH
    server states and present in the worker's.

    The production category map (``list_tools_by_category`` — imported
    into ``daemon.tools.instance`` at module scope) is patched to the
    synthetic ownership map so the ``plane_sync`` owner precondition is
    REAL at the drop site, not just at the resolver seam.
    """

    class _Tool:
        def __init__(self, name: str):
            self.name = name

    def _run(self, meta, tool_names, mcp_names):
        tools = [self._Tool(n) for n in sorted(tool_names)]
        registry = MagicMock()
        registry.get_version.return_value = meta
        registry.get_resolved.return_value = meta
        categories = _categories(plane_registered=False)
        with (
            patch("daemon.registry.get_registry", return_value=registry),
            patch(
                "daemon.tools.instance.list_tools_by_category",
                return_value=categories,
            ),
        ):
            return {
                t.name
                for t in _apply_tool_filter(
                    tools, meta.id, mcp_tool_names=sorted(mcp_names)
                )
            }

    def test_pm_absent_state(self):
        """Server absent: PM gets neither plane tools NOR the sync tool."""
        meta = _make_meta("project-manager", tools_allow=["plane", "filesystem"])
        names = self._run(
            meta,
            {"read_file", SYNTHETIC_OWNER},
            mcp_names=[],
        )
        assert SYNTHETIC_OWNER not in names
        assert "read_file" in names

    def test_pm_registered_state(self):
        """Server registered: PM gets the plane tools but NOT the sync
        tool, despite deny=['mcp'] (NR4 — the prefix override keeps
        bypassing it)."""
        meta = _make_meta(
            "project-manager", tools_allow=["plane", "filesystem"], tools_deny=["mcp"]
        )
        names = self._run(
            meta,
            set(RUNTIME_PLANE_TOOLS) | {SYNTHETIC_OWNER, "read_file"},
            mcp_names=sorted(RUNTIME_PLANE_TOOLS),
        )
        assert RUNTIME_PLANE_TOOLS <= names  # P9: deny=['mcp'] does not strip plane_*
        assert SYNTHETIC_OWNER not in names
        assert "read_file" in names

    def test_worker_gets_the_sync_tool(self):
        """Worker shape (agents/worker/meta.json:29 holds ``plane_sync``)."""
        meta = _make_meta("worker", tools_allow=["plane_sync", "filesystem"])
        names = self._run(
            meta,
            set(RUNTIME_PLANE_TOOLS) | {SYNTHETIC_OWNER, "read_file"},
            mcp_names=[],
        )
        assert SYNTHETIC_OWNER in names
        assert not (RUNTIME_PLANE_TOOLS & names)  # no plane leak to the worker
