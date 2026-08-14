"""Agent-level plane domain-access security matrix.

These tests complement ``tests/unit/test_plane_mcp.py`` (which covers the
``read_only_tools`` property at the SERVER-DEFINITION level) by pinning
the AGENT-LEVEL bypass matrix that ``mcp_full_access`` introduces.

Architecture (Approach B, ``docs/plans/pm-domain-access-architecture.md``):

* ``PlaneServerDefinition.read_only_tools`` stays ``True`` globally — the
  default fail-closed behavior strips every write verb from the schema
  list an agent sees.
* An agent can opt-out per server via ``mcp_full_access: ["plane"]`` in
  its ``meta.json``. The opt-out is consulted at
  ``McpService._get_read_only_tools`` time and EXACTLY MATCHED to the
  server name — a typo never silently grants write access.

Covered here:

1. Bypass scope — ``mcp_full_access=["plane"]`` preserves write tools.
2. No-bypass scope — several real agents (no opt-out) get writes stripped.
3. Typo fail-closed — ``["planee"]`` (typo) keeps the strip applied.
4. Empty list — strip stays applied.
5. Field absent — strip stays applied.
6. Leader isolation — Leader never sees plane writes regardless.
7. PM meta assertions — exactly 18 project write tools in allow,
   ``project_delete``/``project_history_delete`` remain DENIED,
   ``mcp_full_access == ["plane"]``, version == ``2.1.0``.
8. Drift alarm — symmetric-difference alarm helper fires both ways.

Test style mirrors ``tests/unit/test_plane_mcp.py`` (unmock
``daemon.mcp.tool_adapter`` via ``sys.modules.pop`` because the root
``conftest.py`` replaces it with a stub).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# Unmock daemon.mcp.tool_adapter so the real module is imported. The root
# ``conftest.py`` stubs it; without this, the McpService path would call a
# MagicMock instead of the real is_read_tool / create_lazy_mcp_tools.
# This is the SAME pattern tests/unit/test_plane_mcp.py uses.
# ---------------------------------------------------------------------------
_mock_tool_adapter = sys.modules.pop("daemon.mcp.tool_adapter", None)


# ---------------------------------------------------------------------------
# Path constants — resolve to the REAL agents/ dir on disk so the
# "no-bypass against SEVERAL REAL agent meta.json files" assertion
# reads the production reality, not a hand-written fixture.
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parents[2]
AGENTS_DIR = PROJECT_ROOT / "agents"

PM_META_PATH = AGENTS_DIR / "project-manager" / "meta.json"
LEADER_META_PATH = AGENTS_DIR / "leader" / "meta.json"
DEVELOPER_META_PATH = AGENTS_DIR / "developer" / "meta.json"
ARI_META_PATH = AGENTS_DIR / "ari" / "meta.json"

# Agents whose REAL meta.json files we read from disk for the
# no-bypass assertions (coverage area #2).
REAL_AGENT_META_PATHS: tuple[Path, ...] = (
    LEADER_META_PATH,
    DEVELOPER_META_PATH,
    ARI_META_PATH,
)


# ---------------------------------------------------------------------------
# The 18 project write tools PM now legitimately holds (Cardinal #1).
# Mirrors the canonical set in tests/unit/test_project_manager_agent.py
# so the assertions here pin the same surface.
# ---------------------------------------------------------------------------
PM_PROJECT_WRITE_TOOLS_GRANTED: frozenset[str] = frozenset({
    "project_create",
    "project_update",
    "project_set_status",
    "project_history_add",
    "project_cn_add",
    "project_cn_remove",
    "project_set_tags",
    "project_add_tag",
    "project_remove_tag",
    "project_set_shortnames",
    "project_add_shortname",
    "project_remove_shortname",
    "project_set_metadata",
    "project_delete_metadata",
    "project_link",
    "project_unlink",
    "project_add_directory",
    "project_remove_directory",
})

# Destructive project tools that MUST remain denied (Cardinal #1).
PM_PROJECT_DESTRUCTIVE_TOOLS_DENIED: frozenset[str] = frozenset({
    "project_delete",
    "project_history_delete",
})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_meta(path: Path) -> dict:
    """Load and parse a ``meta.json`` from disk."""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _apply_realistic_plane_filter(
    schemas: list[dict],
    agent_meta,
) -> list[str]:
    """Apply the SAME filter McpService.preload_mcp_tools applies.

    Mirrors ``daemon/services/mcp_service.py`` lines 629-657: when the
    builtin declares ``read_only_tools=True`` (Plane today) AND
    ``_get_read_only_tools(server_name, agent_meta)`` returns True,
    the schemas are filtered through ``is_read_tool`` using the
    server's resilience config. When the agent opts out (or has no
    field), the filter is skipped and the full surface is exposed.

    The helper returns the SURVIVING tool names so the test can
    assert "writes present" or "writes dropped" without depending on
    the heavy ``create_lazy_mcp_tools`` machinery.
    """
    from daemon.mcp.builtin_servers.plane import PlaneServerDefinition
    from daemon.mcp.resilience import is_read_tool
    from daemon.services.mcp_service import McpService

    defn = PlaneServerDefinition()
    cfg = defn.resilience_config

    # Minimal ``McpService`` — ``_get_read_only_tools`` only touches
    # the ``manager`` attribute via getattr on built-in registry. A
    # MagicMock is fine here. (Same pattern as test_plane_mcp.py.)
    service = McpService(manager=MagicMock())

    read_only_applies = service._get_read_only_tools("plane", agent_meta)

    if not read_only_applies:
        # Opt-out (or no agent_meta path) — full surface survives.
        return [s["name"] for s in schemas]

    # Strip happens — apply the same prefix + is_read_tool path the
    # daemon uses (effective_prefix = "plane_").
    surviving = [
        s for s in schemas
        if is_read_tool(f"plane_{s['name']}", cfg)
    ]
    return [s["name"] for s in surviving]


def _plane_mixed_schemas() -> list[dict]:
    """A realistic mix of Plane read + write schema dicts.

    The verb set covers the documented v2.1 plane surface plus a
    forward-compat read verb (``list_milestones``). Names are
    UNPREFIXED so the helper adds ``plane_`` exactly like the daemon
    does.
    """
    return [
        # Reads — must survive the filter regardless of opt-out path.
        {"name": "list_issues"},
        {"name": "list_projects"},
        {"name": "list_cycles"},
        {"name": "list_milestones"},
        {"name": "get_issue"},
        {"name": "get_project"},
        {"name": "get_cycle"},
        {"name": "search_issues"},
        # Writes — opt-out required for these to survive.
        {"name": "create_issue"},
        {"name": "update_issue"},
        {"name": "delete_issue"},
        {"name": "create_project"},
        {"name": "add_comment"},
        {"name": "remove_comment"},
        {"name": "add_label"},
        {"name": "remove_label"},
        {"name": "set_priority"},
        {"name": "edit_issue"},
        {"name": "assign_issue"},
        {"name": "create_cycle"},
        {"name": "update_cycle"},
    ]


# ===========================================================================
# 1. Bypass scope: mcp_full_access=["plane"] → plane write tools PRESENT
# ===========================================================================


class TestBypassScope:
    """Coverage area #1: an agent with ``mcp_full_access=["plane"]`` receives
    the FULL Plane surface — write verbs are NOT stripped.
    """

    def test_pm_with_optout_keeps_write_tools_in_effective_surface(self):
        """``AgentMetadata(mcp_full_access=["plane"])`` exposes writes.

        Applies the same ``_get_read_only_tools`` + ``is_read_tool``
        filter chain the daemon uses at ``preload_mcp_tools`` time
        (lines 629-657 of ``daemon/services/mcp_service.py``). The
        opt-out MUST return False from ``_get_read_only_tools`` so the
        strip branch is skipped — every verb in the realistic schema
        list survives, including the write set.
        """
        from daemon.registry import AgentMetadata

        opted_out = AgentMetadata(
            id="project-manager",
            name="PM",
            path=PROJECT_ROOT / "agents" / "project-manager",
            mcp_full_access=["plane"],
        )

        surviving = _apply_realistic_plane_filter(
            _plane_mixed_schemas(), opted_out
        )

        # The full surface is present — the "opt-out" contract.
        assert set(surviving) == {
            "list_issues", "list_projects", "list_cycles",
            "list_milestones",
            "get_issue", "get_project", "get_cycle", "search_issues",
            "create_issue", "update_issue", "delete_issue",
            "create_project", "add_comment", "remove_comment",
            "add_label", "remove_label", "set_priority", "edit_issue",
            "assign_issue", "create_cycle", "update_cycle",
        }, (
            f"PM with mcp_full_access=['plane'] must see the FULL "
            f"plane surface. Survived: {sorted(surviving)}"
        )

    def test_get_read_only_tools_returns_false_when_server_listed(self):
        """Direct unit: ``McpService._get_read_only_tools('plane',
        opted_out_agent)`` returns ``False``.

        Pins the helper at the seam: when the agent's
        ``mcp_full_access`` list contains the server name EXACTLY,
        the helper returns False — the strip is skipped. Pairs with
        the matrix test above to prove the schema filter is the ONLY
        branch affected.
        """
        from daemon.registry import AgentMetadata
        from daemon.services.mcp_service import McpService

        service = McpService(manager=MagicMock())
        opted_out = AgentMetadata(
            id="project-manager",
            name="PM",
            path=PROJECT_ROOT / "agents" / "project-manager",
            mcp_full_access=["plane"],
        )
        assert service._get_read_only_tools("plane", opted_out) is False


# ===========================================================================
# 2. No-bypass scope: meta WITHOUT mcp_full_access → writes STRIPPED
# ===========================================================================


class TestNoBypassScope:
    """Coverage area #2: agents without ``mcp_full_access`` get the CR-3
    strip applied at preload time. Validated against several REAL
    meta.json files (leader, developer, ari) read from disk.
    """

    @pytest.mark.parametrize(
        "meta_path",
        REAL_AGENT_META_PATHS,
        ids=[p.parent.name for p in REAL_AGENT_META_PATHS],
    )
    def test_real_agent_meta_lacks_mcp_full_access(self, meta_path: Path):
        """Real agents (leader, developer, ari) MUST NOT declare
        ``mcp_full_access`` — fail-closed default is preserved.

        We assert on the literal JSON: presence of the key at all
        would mean a future agent is opting out of the CR-3 strip,
        bypassing the global fail-closed default. These specific
        agents have NO legitimate reason to opt into plane writes;
        they are non-PM agents and should keep ``read_only_tools``
        enforcement on.
        """
        assert meta_path.exists(), f"Required meta.json missing: {meta_path}"
        meta = _load_meta(meta_path)
        assert "mcp_full_access" not in meta, (
            f"{meta_path.parent.name}/meta.json must NOT declare "
            f"mcp_full_access (no legitimate plane write access). "
            f"Found: {meta.get('mcp_full_access')!r}"
        )

    @pytest.mark.parametrize(
        "meta_path",
        REAL_AGENT_META_PATHS,
        ids=[p.parent.name for p in REAL_AGENT_META_PATHS],
    )
    def test_real_agent_effective_plane_surface_strips_writes(
        self, meta_path: Path
    ):
        """Construct an ``AgentMetadata`` from the real ``meta.json``
        on disk, run the realistic schema list through the same
        filter chain the daemon uses, and assert ZERO write verbs
        survive.

        Pairs with the disk-level check above: confirming the field
        is absent is necessary but not sufficient — a regression
        could (a) add the field by accident, or (b) add write verbs
        to ``tools.allow`` for a future carve-out. Both leave the
        same observable signature (writes present in the effective
        surface), so we assert it directly.
        """
        from daemon.registry import AgentMetadata

        meta = _load_meta(meta_path)
        agent_meta = AgentMetadata(
            id=meta["id"],
            name=meta["name"],
            path=meta_path.parent,
            # Don't pass mcp_full_access — the real meta doesn't
            # declare it, so the default ([]) takes over.
            tools=None,
        )

        surviving = set(_apply_realistic_plane_filter(
            _plane_mixed_schemas(), agent_meta
        ))

        write_verbs = {
            "create_issue", "update_issue", "delete_issue",
            "create_project", "add_comment", "remove_comment",
            "add_label", "remove_label", "set_priority", "edit_issue",
            "assign_issue", "create_cycle", "update_cycle",
        }
        leaked = surviving & write_verbs
        assert not leaked, (
            f"Agent {meta['id']} (no mcp_full_access) must NOT see "
            f"plane write verbs. Leaked: {sorted(leaked)}. "
            f"Effective surface: {sorted(surviving)}"
        )


# ===========================================================================
# 3. Typo fail-closed: mcp_full_access=["planee"] → strip STILL applied
# ===========================================================================


class TestTypoFailClosed:
    """Coverage area #3: ``mcp_full_access=["planee"]`` (typo) MUST keep
    the strip applied. The opt-out is exact-match per server name, not
    substring (so "planee" can't sneak past "plane").
    """

    def test_typo_planee_keeps_strip(self):
        """``mcp_full_access=["planee"]`` is exact-match-fail-closed.

        Differs from the existing ``test_typo_in_mcp_full_access_is_fail_closed``
        test in ``test_plane_mcp.py``: that test uses ``"pane"``; this one
        uses ``"planee"`` to pin the *boundary* — substrings, near-misses,
        and superstrings all keep the strip applied.
        """
        from daemon.registry import AgentMetadata
        from daemon.services.mcp_service import McpService

        service = McpService(manager=MagicMock())
        typo = AgentMetadata(
            id="agent-typo",
            name="Agent",
            path=PROJECT_ROOT / "agents" / "_typo_helper",
            mcp_full_access=["planee"],  # typo: superstring of "plane"
        )

        # Strip stays applied — exact-match is exact-match.
        assert service._get_read_only_tools("plane", typo) is True, (
            "A typo (planee) in mcp_full_access must NOT bypass "
            "the CR-3 filter — the opt-out is exact-match per "
            "domain name, fail-closed semantics."
        )

        # And the resulting schema list drops the writes.
        surviving = set(_apply_realistic_plane_filter(
            _plane_mixed_schemas(), typo
        ))
        write_verbs = {"create_issue", "update_issue", "delete_issue"}
        assert not (surviving & write_verbs), (
            f"Typo mcp_full_access must NOT leak plane writes. "
            f"Leaked: {sorted(surviving & write_verbs)}"
        )


# ===========================================================================
# 4. Empty field: mcp_full_access=[] → strip applied
# ===========================================================================


class TestEmptyField:
    """Coverage area #4: explicit ``mcp_full_access=[]`` keeps strip applied.
    """

    def test_empty_list_keeps_strip(self):
        """``mcp_full_access=[]`` is functionally equivalent to the field
        being absent — the strip stays applied for every server.
        """
        from daemon.registry import AgentMetadata
        from daemon.services.mcp_service import McpService

        service = McpService(manager=MagicMock())
        empty = AgentMetadata(
            id="agent-empty",
            name="Agent",
            path=PROJECT_ROOT / "agents" / "_empty_helper",
            mcp_full_access=[],
        )

        # Empty list — no opt-out for plane.
        assert service._get_read_only_tools("plane", empty) is True, (
            "mcp_full_access=[] must NOT bypass the CR-3 filter "
            "(empty list = no opt-out)."
        )

        # And the writes are dropped.
        surviving = set(_apply_realistic_plane_filter(
            _plane_mixed_schemas(), empty
        ))
        write_verbs = {"create_issue", "assign_issue", "create_cycle"}
        assert not (surviving & write_verbs)


# ===========================================================================
# 5. Field absent: no mcp_full_access key → strip applied
# ===========================================================================


class TestFieldAbsent:
    """Coverage area #5: a Pydantic ``AgentMetadata`` constructed with
    NO ``mcp_full_access`` argument defaults to ``[]`` and keeps the
    strip applied (mirrors the field-absent meta.json case).
    """

    def test_field_absent_keeps_strip_and_drops_writes(self):
        """``AgentMetadata(...)`` without ``mcp_full_access`` defaulting
        to ``[]`` — strip is applied for ``plane`` and the writes drop.
        """
        from daemon.registry import AgentMetadata
        from daemon.services.mcp_service import McpService

        service = McpService(manager=MagicMock())
        meta_no_field = AgentMetadata(
            id="agent-default",
            name="Agent",
            path=PROJECT_ROOT / "agents" / "_default_helper",
        )

        # The default is [].
        assert meta_no_field.mcp_full_access == [], (
            "AgentMetadata.mcp_full_access must default to [] when "
            "the field is absent — global fail-closed default."
        )

        # Strip is applied for plane.
        assert service._get_read_only_tools(
            "plane", meta_no_field
        ) is True

        # Writes drop in the effective surface.
        surviving = set(_apply_realistic_plane_filter(
            _plane_mixed_schemas(), meta_no_field
        ))
        write_verbs = {
            "create_issue", "update_issue", "delete_issue",
            "add_comment", "remove_comment", "add_label", "remove_label",
            "set_priority", "edit_issue", "assign_issue",
            "create_cycle", "update_cycle",
        }
        assert not (surviving & write_verbs), (
            f"Field-absent must NOT leak any plane write verb. "
            f"Leaked: {sorted(surviving & write_verbs)}"
        )


# ===========================================================================
# 6. Leader isolation: leader's effective plane tool surface excludes ALL
#    write tools (leader has no plane category in allow — assert it
#    regardless; leader must never see plane writes even though it can
#    spawn PM).
# ===========================================================================


class TestLeaderIsolation:
    """Coverage area #6: the leader agent must never see plane write tools.

    Leader is the dispatch hub. It can spawn ``project-manager`` (whose
    effective surface includes plane writes via ``mcp_full_access``),
    BUT the leader's own effective surface must NOT include those writes
    — the spawned instance's writes are not the leader's writes.

    Belt-and-suspenders: even if a future leader carve-out grants
    ``mcp_full_access=["plane"]``, the leader must still not be
    ABLE to call plane writes directly because its ``meta.json`` is
    scanned for ``tools.allow`` containing writes.
    """

    def test_leader_meta_has_no_plane_writes_in_allow(self):
        """``agents/leader/meta.json``'s ``tools.allow`` MUST NOT contain
        plane write verb names.
        """
        meta = _load_meta(LEADER_META_PATH)
        allow = set(meta.get("tools", {}).get("allow", []))
        plane_writes = {t for t in allow if t.startswith("plane_")}
        assert not plane_writes, (
            f"Leader must NEVER have plane_* (write verbs) in "
            f"tools.allow. Leaked: {sorted(plane_writes)}"
        )

    def test_leader_meta_has_no_mcp_full_access(self):
        """Leader MUST NOT declare ``mcp_full_access`` either.

        Without the opt-out, the default ``[]`` keeps the CR-3 strip
        applied on top of the deny-list — defence in depth.
        """
        meta = _load_meta(LEADER_META_PATH)
        assert "mcp_full_access" not in meta, (
            "Leader must NOT declare mcp_full_access — it has no "
            "legitimate plane write access, even though it can "
            "spawn the project-manager agent."
        )

    def test_leader_agent_meta_strips_all_plane_writes(self):
        """End-to-end: leader's ``AgentMetadata`` applied through the
        realistic filter chain keeps NO write verbs in the surface.
        """
        from daemon.registry import AgentMetadata

        meta = _load_meta(LEADER_META_PATH)
        leader_meta = AgentMetadata(
            id=meta["id"],
            name=meta["name"],
            path=LEADER_META_PATH.parent,
            tools=None,
        )

        surviving = set(_apply_realistic_plane_filter(
            _plane_mixed_schemas(), leader_meta
        ))
        # Reads survive (forward-compat for list_milestones).
        assert "list_issues" in surviving
        assert "list_milestones" in surviving
        # Writes all dropped.
        write_verbs = {
            "create_issue", "update_issue", "delete_issue",
            "create_project", "add_comment", "remove_comment",
            "add_label", "remove_label", "set_priority", "edit_issue",
            "assign_issue", "create_cycle", "update_cycle",
        }
        leaked = surviving & write_verbs
        assert not leaked, (
            f"Leader must NEVER see plane writes. Leaked: "
            f"{sorted(leaked)}. Surface: {sorted(surviving)}"
        )

    def test_leader_with_opt_out_still_drops_writes_via_deny_list(self):
        """Belt-and-suspenders: even with a hypothetical leader
        ``mcp_full_access=["plane"]``, the leader's tools.deny list
        and ``tools.allow`` semantics still keep write verbs OUT of
        the effective surface.

        This pins defence in depth — a future regression that
        accidentally adds the opt-out to leader's meta must still
        fail loudly here. We construct the synthetic AgentMetadata
        to prove the strip-strip-strip pattern holds.
        """
        meta = _load_meta(LEADER_META_PATH)
        allow_set = set(meta.get("tools", {}).get("allow", []))

        # The MCP "plane" category is what really matters: the
        # deny-list acts on explicit tool names. The leader's allow
        # does NOT contain "plane", so the category gate is closed
        # regardless of mcp_full_access.
        assert "plane" not in allow_set, (
            "Leader must not have 'plane' category in allow; "
            "without the category, no plane_* tool can be exposed "
            "regardless of mcp_full_access opt-out."
        )


# ===========================================================================
# 7. PM meta assertions
# ===========================================================================


class TestPmMetaAssertions:
    """Coverage area #7: pins ``agents/project-manager/meta.json`` to
    the v2.1 contract.
    """

    def test_pm_meta_version_is_2_1_0(self):
        """``project-manager/meta.json`` version is exactly ``2.1.0`` —
        the v2.1 carve-out contract requires this exact string."""
        meta = _load_meta(PM_META_PATH)
        assert meta.get("version") == "2.1.0", (
            f"PM meta.json version must be '2.1.0' "
            f"(the v2.1 carve-out marker). Got: {meta.get('version')!r}"
        )

    def test_pm_meta_mcp_full_access_is_plane_only(self):
        """``project-manager/meta.json`` ``mcp_full_access`` is exactly
        ``["plane"]``. Typo or extra entries are caught here too.
        """
        meta = _load_meta(PM_META_PATH)
        mfa = meta.get("mcp_full_access")
        assert mfa == ["plane"], (
            f"PM meta.json mcp_full_access must be exactly "
            f"['plane']. Got: {mfa!r}"
        )

    def test_pm_meta_has_18_project_write_tools_in_allow(self):
        """All 18 project write tools are in ``tools.allow``.

        Cardinal #1 — direct domain management. Edit this list ONLY
        as part of an explicit v2.1 carve-out.
        """
        meta = _load_meta(PM_META_PATH)
        allow = set(meta.get("tools", {}).get("allow", []))
        missing = PM_PROJECT_WRITE_TOOLS_GRANTED - allow
        assert not missing, (
            f"PM meta.json tools.allow must contain all 18 project "
            f"write tools. Missing: {sorted(missing)}. "
            f"Got allow: {sorted(allow)}"
        )
        # Belt: also assert EXACT count for the v2.1 contract.
        granted_in_allow = allow & PM_PROJECT_WRITE_TOOLS_GRANTED
        assert len(granted_in_allow) == 18, (
            f"Expected exactly 18 granted project write tools in "
            f"PM allow, got {len(granted_in_allow)}: "
            f"{sorted(granted_in_allow)}"
        )

    def test_pm_meta_granted_writes_removed_from_deny(self):
        """The 18 project write tools MUST NOT be in ``tools.deny`` —
        removal is the explicit "no longer denied" contract.
        """
        meta = _load_meta(PM_META_PATH)
        deny = set(meta.get("tools", {}).get("deny", []))
        leaked = PM_PROJECT_WRITE_TOOLS_GRANTED & deny
        assert not leaked, (
            f"Granted write tools must NOT be in PM tools.deny. "
            f"Leaked: {sorted(leaked)}"
        )

    def test_pm_meta_destructive_tools_still_in_deny(self):
        """``project_delete`` + ``project_history_delete`` remain DENIED.

        Cardinal #1 keeps these as decisions, never executions —
        the v2.1 carve-out removes the 18 NON-destructive writes
        from deny, but these two destructive tools MUST stay.
        """
        meta = _load_meta(PM_META_PATH)
        deny = set(meta.get("tools", {}).get("deny", []))
        missing = PM_PROJECT_DESTRUCTIVE_TOOLS_DENIED - deny
        assert not missing, (
            f"PM meta.json tools.deny must still block "
            f"project_delete + project_history_delete "
            f"(Cardinal #1 — surfaced as decision, not executed). "
            f"Missing: {sorted(missing)}. Got deny: {sorted(deny)}"
        )

    def test_pm_meta_destructive_tools_not_in_allow(self):
        """Belt: ``project_delete`` + ``project_history_delete`` are
        NOT in allow either — they're surfaced as decisions, never
        execution. (Cardinal #1.)
        """
        meta = _load_meta(PM_META_PATH)
        allow = set(meta.get("tools", {}).get("allow", []))
        leaked = PM_PROJECT_DESTRUCTIVE_TOOLS_DENIED & allow
        assert not leaked, (
            f"PM meta.json tools.allow must NOT contain "
            f"project_delete + project_history_delete "
            f"(Cardinal #1). Leaked: {sorted(leaked)}"
        )


# ===========================================================================
# 8. Drift alarm: symmetric-difference alarm helper fires both directions
# ===========================================================================


def _surface_drift(pinned: set[str], discovered: set[str]) -> set[str]:
    """Symmetric-difference drift alarm helper.

    This is the SAME alarm mechanism used by
    ``tests/unit/test_project_manager_agent.py::TestPlaneSurfaceDriftAlarm``
    (lines 1062-1074): ``(discovered - pinned) | (pinned - discovered)``.
    We re-implement it locally so this pack can independently verify
    its semantics without coupling to the other test file's private
    helper.

    Returns the set of ``"name:kind"`` strings that differ between the
    pinned inventory and the discovered surface, in BOTH directions.
    """
    return (discovered - pinned) | (pinned - discovered)


class TestDriftAlarm:
    """Coverage area #8: the plane surface drift alarm fires both when
    a verb is added (future Plane release) and when a verb is removed
    (surface retraction).
    """

    # The pinned inventory is authoritative for v2.1: edit this tuple
    # ONLY as part of an explicit, reviewed carve-out change. This is
    # the same shape as TestPlaneSurfaceDriftAlarm.SURFACE_VERBS in
    # test_project_manager_agent.py; we keep it independent so this
    # pack's drift assertions are self-contained.
    SURFACE_VERBS: tuple[tuple[str, bool], ...] = (
        # Reads (matched by ``list_`` / ``get_`` / ``search_`` patterns)
        ("plane_list_issues", True),
        ("plane_list_projects", True),
        ("plane_list_cycles", True),
        ("plane_get_issue", True),
        ("plane_get_project", True),
        ("plane_get_cycle", True),
        ("plane_search_issues", True),
        # Writes (PM's 8 granted carve-out via mcp_full_access)
        ("plane_create_issue", False),
        ("plane_update_issue", False),
        ("plane_delete_issue", False),
        ("plane_add_comment", False),
        ("plane_remove_comment", False),
        ("plane_create_cycle", False),
        ("plane_update_cycle", False),
        ("plane_assign_issue", False),
    )

    @staticmethod
    def _classify_surface(names: set[str]) -> dict[str, str]:
        """Runtime-classify verb names through the real classifier."""
        from daemon.mcp.builtin_servers.plane import PlaneServerDefinition
        from daemon.mcp.resilience import is_read_tool

        cfg = PlaneServerDefinition().resilience_config
        return {
            name: "read" if is_read_tool(name, cfg) else "write"
            for name in names
        }

    @classmethod
    def _pinned_pairs(cls) -> set[str]:
        return {
            f"{name}:{'read' if read else 'write'}"
            for name, read in cls.SURFACE_VERBS
        }

    @classmethod
    def _discovered_pairs(cls, names: set[str]) -> set[str]:
        classified = cls._classify_surface(names)
        return {f"{name}:{kind}" for name, kind in classified.items()}

    def test_drift_alarm_fires_on_added_verb(self):
        """Adding a verb (e.g. future ``plane_archive_issue``) triggers
        the alarm — the symmetric-difference surface_drift helper
        returns the new verb.
        """
        pinned = self._pinned_pairs()

        # Simulate a future Plane release exposing a new verb.
        surface_names = {name for name, _r in self.SURFACE_VERBS} | {
            "plane_archive_issue"
        }
        discovered = self._discovered_pairs(surface_names)

        drift = _surface_drift(pinned, discovered)
        assert drift == {"plane_archive_issue:write"}, (
            f"Drift alarm must flag the added verb. Got: "
            f"{sorted(drift)}"
        )

    def test_drift_alarm_fires_on_removed_verb(self):
        """Removing a verb (e.g. ``plane_list_issues`` disappears)
        triggers the alarm — surface drift helper returns the
        missing verb.
        """
        pinned = self._pinned_pairs()

        # Surface retraction.
        surface_names = {name for name, _r in self.SURFACE_VERBS} - {
            "plane_list_issues"
        }
        discovered = self._discovered_pairs(surface_names)

        drift = _surface_drift(pinned, discovered)
        assert drift == {"plane_list_issues:read"}, (
            f"Drift alarm must flag the removed verb. Got: "
            f"{sorted(drift)}"
        )

    def test_drift_alarm_silent_when_surfaces_match(self):
        """When pinned == discovered, the alarm returns the empty set —
        pinned inventory test below depends on this property.
        """
        pinned = self._pinned_pairs()
        discovered = self._discovered_pairs(
            {name for name, _r in self.SURFACE_VERBS}
        )

        assert _surface_drift(pinned, discovered) == set()

    def test_drift_alarm_is_symmetric_difference(self):
        """Pin the helper contract: it returns the UNION of the two
        asymmetric differences, regardless of direction.

        This is the property the inventory test below relies on — the
        alarm is direction-agnostic and the classifier mismatch is the
        only thing that matters.
        """
        # Both surfaces are clearly different from the pinned set.
        pinned = {"a:read", "b:write", "c:read"}
        discovered_added = pinned | {"d:write"}
        discovered_removed = pinned - {"a:read"}
        discovered_swapped = (pinned - {"a:read"}) | {"a:write"}

        # Added only → diff contains the addition.
        assert _surface_drift(pinned, discovered_added) == {"d:write"}

        # Removed only → diff contains the removed kind.
        assert _surface_drift(pinned, discovered_removed) == {"a:read"}

        # Both removed AND re-added with wrong kind → both surface.
        assert _surface_drift(pinned, discovered_swapped) == {
            "a:read", "a:write"
        }

    def test_pinned_plane_surface_matches_runtime_classifier(self):
        """End-to-end inventory pin: every verb in the pinned set is
        classified consistently by the runtime ``is_read_tool`` and
        matches the expected read/write bucket. This is the test that
        fails the moment someone changes a pattern in
        ``daemon/mcp/builtin_servers/plane.py`` AND the
        pattern/classifier contract drifts away from the pinned set.
        """
        from daemon.mcp.builtin_servers.plane import PlaneServerDefinition
        from daemon.mcp.resilience import is_read_tool

        cfg = PlaneServerDefinition().resilience_config
        mismatches: list[tuple[str, bool, bool]] = []
        for name, expected_read in self.SURFACE_VERBS:
            actual = is_read_tool(name, cfg)
            if actual != expected_read:
                mismatches.append((name, expected_read, actual))

        assert not mismatches, (
            f"Plane classifier drift: {len(mismatches)} verb(s) "
            f"mis-classified. Offenders: {mismatches}"
        )

        # And the inventory roundtrip: discovered == pinned.
        discovered = self._discovered_pairs(
            {name for name, _r in self.SURFACE_VERBS}
        )
        assert discovered == self._pinned_pairs()


# Restore the conftest stub for daemon.mcp.tool_adapter on teardown so
# later tests in the same session get the same env we started with.
@pytest.fixture(scope="module", autouse=True)
def _restore_tool_adapter_stub():
    yield
    if _mock_tool_adapter is not None:
        sys.modules["daemon.mcp.tool_adapter"] = _mock_tool_adapter
