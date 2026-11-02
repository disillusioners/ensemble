"""FC7 — the per-agent resolved-toolset census (lane ``plane-pm-issue-tools`` Phase 3).

The detector behind FC7: the plane **write** surface must not leak to a
non-PM agent. The census is run **with the plane server REGISTERED** (an
unregistered census is vacuous — it resolves zero ``plane_*`` tools for
everyone, so it can never fire).

The enumerated expectations (phase3-plan §2.5):

* **E1** — the census runs with the plane server REGISTERED: the resolved
  universe contains the ``plane_*`` names for PM ⇒ a census that resolves
  zero plane tools for everyone is VACUOUS ⇒ FAIL;
* **E2** — the WRITE surface: no agent other than PM resolves any
  ``plane_*`` write tool (the write set is the classifier's 104 verbs);
* **E3** — the READ-exposure disclosure: the census RECORDS which non-PM
  agents resolve ``plane_*`` read tools via the empty-allow universe
  branch (``daemon/tools/instance.py``'s default-universe branch) and
  REDs on any GROWTH beyond the disclosed set (frozen in this pack);
* **E4** — PM is the ONLY ``mcp_full_access`` holder of ``plane``;
* **E5** — consistency with the P2-K widening: the read set a
  non-full-access agent sees is exactly the corrected classifier's 73 read
  verbs (no verb appears that the classifier does not classify as read).

The toolset model mirrors production in two stages:

1. the TOOL-FILTER layer (``resolve_tool_filter``) resolves the agent's
   allow/deny list against the category map — this is where the
   ``plane`` category is expanded from the runtime names;
2. the MCP layer (CR-3) strips the WRITE schemas for every agent that
   does NOT declare ``mcp_full_access`` for the server — the read-only
   strip. PM (``mcp_full_access: ["plane"]``) keeps the full surface.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from helpers.plane_catalog_fixture import catalog_names, load_catalog

from daemon.mcp.builtin_servers.plane import PlaneServerDefinition
from daemon.mcp.resilience import is_read_tool
from daemon.tools.instance import resolve_tool_filter

REPO_ROOT = Path(__file__).resolve().parents[3]
AGENTS_DIR = REPO_ROOT / "agents"

PLANE_SERVER = "plane"
PM_AGENT = "project-manager"

#: The DISCLOSED read-holder set (E3) — the agents that reach the plane
#: category through the EMPTY-ALLOW universe branch (no explicit
#: ``tools.allow`` ⇒ the default universe includes every non-privileged
#: category, ``plane`` among them). Frozen at the census's first run
#: (phase3-plan §2.5 E3): any GROWTH beyond this set is a RED.
DISCLOSED_READ_HOLDERS: frozenset[str] = frozenset({"watcher"})


def _load_agent_metas() -> list[dict]:
    metas = []
    for meta_path in sorted(AGENTS_DIR.glob("*/meta.json")):
        try:
            metas.append(json.loads(meta_path.read_text(encoding="utf-8")))
        except json.JSONDecodeError:  # pragma: no cover — a broken meta is not this pin's business
            continue
    return metas


def _plane_daemon_names() -> set[str]:
    """The daemon-shaped plane tool names (``plane_<raw verb>``)."""
    return {f"plane_{name}" for name in catalog_names(load_catalog())}


def _classifier():
    return PlaneServerDefinition().resilience_config


def _read_verbs() -> set[str]:
    cfg = _classifier()
    return {
        name
        for name in catalog_names(load_catalog())
        if is_read_tool(f"plane_{name}", cfg)
    }


def _write_verbs() -> set[str]:
    cfg = _classifier()
    return {
        name
        for name in catalog_names(load_catalog())
        if not is_read_tool(f"plane_{name}", cfg)
        and any(
            f"plane_{name}".split("_", 1)[1].startswith(p)
            for p in cfg.write_tool_patterns
        )
    }


def _production_categories() -> dict[str, list[str]]:
    """The PRODUCTION category map (the ownership map).

    ``create_instance_tools`` extends the tool list with the plane-sync
    factory's tools BEFORE ``scan_tools_for_full_docs`` runs, so in
    production the ``plane_sync`` category OWNS ``plane_sync_project``.
    A test process never builds that factory (it needs a project store),
    so the owner is added here — the same shape production resolves
    against, and the precondition the prefix-sweep ownership guard
    depends on.
    """
    from daemon.tools._tool_registry import list_tools_by_category

    categories = dict(list_tools_by_category())
    plane_sync = list(categories.get("plane_sync", []))
    if "plane_sync_project" not in plane_sync:
        plane_sync.append("plane_sync_project")
    categories["plane_sync"] = plane_sync
    return categories


def _resolved_plane_tools(meta: dict) -> set[str]:
    """The ``plane_*`` names in the agent's resolved toolset.

    The production categories are used with the plane category left EMPTY
    (its runtime shape) — the D1 expansion populates it from the runtime
    names, exactly as production does when the server is REGISTERED.
    """
    categories = _production_categories()
    categories["plane"] = []  # the runtime shape (server registered ⇒ expanded below)

    tools_cfg = meta.get("tools") or {}
    allow = tools_cfg.get("allow")
    deny = tools_cfg.get("deny")

    resolved = resolve_tool_filter(
        allow=allow,
        deny=deny,
        tool_categories=categories,
        all_tool_names=_plane_daemon_names(),
    )
    if resolved is None:
        # Empty allow AND empty deny ⇒ the DEFAULT UNIVERSE (production
        # keeps every tool; the plane category WAS expanded in the
        # resolver's copy). For the plane census that means the full
        # 177-name surface — the empty-allow universe branch
        # (instance.py's default-universe arm) the E3 disclosure covers.
        resolved = set(_plane_daemon_names())
    # The census measures the PLANE MCP surface: the 177 catalog names.
    # A ``plane_``-NAMED tool owned by another category (e.g.
    # ``plane_sync_project`` via the worker's ``plane_sync`` allow entry)
    # is NOT part of the MCP surface — the ownership guard guarantees it
    # never entered the ``plane`` category either.
    return {name for name in resolved if name in _plane_daemon_names()}


def _effective_surface(meta: dict) -> set[str]:
    """The agent's EFFECTIVE plane surface (tool-filter ∩ CR-3 strip)."""
    resolved = _resolved_plane_tools(meta)
    if PLANE_SERVER in (meta.get("mcp_full_access") or []):
        return resolved  # full access — the strip does not apply
    read_only = {f"plane_{verb}" for verb in _read_verbs()}
    return resolved & read_only


@pytest.fixture(scope="module")
def agent_metas() -> list[dict]:
    return _load_agent_metas()


class TestE1RegisteredCensus:
    """E1 — the census must run with the plane server REGISTERED."""

    def test_the_census_resolves_plane_tools_for_pm(self, agent_metas):
        pm = next(m for m in agent_metas if m.get("id") == PM_AGENT)
        resolved = _resolved_plane_tools(pm)
        assert resolved, (
            "VACUOUS CENSUS: the plane server is not REGISTERED in the "
            "census — zero plane_* tools resolved for PM"
        )
        assert len(resolved) == 177

    def test_the_unregistered_state_would_be_vacuous(self, agent_metas):
        """The vacuity tripwire: with NO plane names in the universe the
        census resolves nothing — which is why E1 exists."""
        categories = _production_categories()
        categories["plane"] = []
        pm = next(m for m in agent_metas if m.get("id") == PM_AGENT)
        tools_cfg = pm.get("tools") or {}
        resolved = resolve_tool_filter(
            allow=tools_cfg.get("allow"),
            deny=tools_cfg.get("deny"),
            tool_categories=categories,
            all_tool_names=set(),  # server NOT registered
        )
        assert not {n for n in (resolved or set()) if n.startswith("plane_")}


class TestE2WriteSurface:
    """E2 — no agent other than PM resolves any plane_* WRITE tool."""

    def test_only_pm_has_write_tools(self, agent_metas):
        write = {f"plane_{verb}" for verb in _write_verbs()}
        leaks = {}
        for meta in agent_metas:
            if meta.get("id") == PM_AGENT:
                continue  # PM is the declared full-access holder (E4)
            surface = _effective_surface(meta)
            leaked = surface & write
            if leaked:
                leaks[meta.get("id")] = sorted(leaked)
        assert leaks == {}, f"plane WRITE surface leaked to: {leaks}"

    def test_pm_keeps_the_full_write_surface(self, agent_metas):
        pm = next(m for m in agent_metas if m.get("id") == PM_AGENT)
        write = {f"plane_{verb}" for verb in _write_verbs()}
        assert write <= _effective_surface(pm)


class TestE3ReadExposureDisclosure:
    """E3 — the read holders are recorded; GROWTH beyond the disclosed set
    is RED."""

    def test_read_holders_match_the_disclosed_set(self, agent_metas):
        read = {f"plane_{verb}" for verb in _read_verbs()}
        holders = set()
        for meta in agent_metas:
            if meta.get("id") == PM_AGENT:
                continue
            surface = _effective_surface(meta)
            if surface & read:
                holders.add(meta.get("id"))
        assert holders == set(DISCLOSED_READ_HOLDERS), (
            "the plane READ exposure set changed — the disclosed set is "
            f"{sorted(DISCLOSED_READ_HOLDERS)}, the census found "
            f"{sorted(holders)}"
        )

    def test_the_read_holders_reach_plane_via_the_empty_allow_branch(
        self, agent_metas
    ):
        """The mechanism behind the disclosure: the holders have NO
        explicit ``plane`` allow entry — they inherit the default
        universe (the accepted caveat: the guard governs WHERE, not WHO)."""
        for agent_id in DISCLOSED_READ_HOLDERS:
            meta = next(m for m in agent_metas if m.get("id") == agent_id)
            allow = (meta.get("tools") or {}).get("allow") or []
            assert "plane" not in allow


class TestE4SoleFullAccessHolder:
    """E4 — PM is the only ``mcp_full_access`` holder of the plane."""

    def test_exactly_one_holder(self, agent_metas):
        holders = [
            m.get("id")
            for m in agent_metas
            if PLANE_SERVER in (m.get("mcp_full_access") or [])
        ]
        assert holders == [PM_AGENT]

    def test_no_meta_file_carries_a_second_holder(self):
        """The on-disk check (the census reads the same files)."""
        holders = []
        for meta_path in sorted(AGENTS_DIR.glob("*/meta.json")):
            try:
                data = json.loads(meta_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            if PLANE_SERVER in (data.get("mcp_full_access") or []):
                holders.append(data.get("id"))
        assert holders == [PM_AGENT]


class TestE5ClassifierConsistency:
    """E5 — the census's read set IS the corrected classifier's read set."""

    def test_the_census_read_set_is_exactly_73_verbs(self):
        assert len(_read_verbs()) == 73
        assert len(_write_verbs()) == 104

    def test_the_disclosed_holder_sees_exactly_the_read_verbs(self, agent_metas):
        holder = next(
            m for m in agent_metas if m.get("id") in DISCLOSED_READ_HOLDERS
        )
        surface = _effective_surface(holder)
        expected = {f"plane_{verb}" for verb in _read_verbs()}
        assert surface == expected

    def test_no_verb_in_the_census_is_unclassified(self, agent_metas):
        """Every verb the census shows is classified (read or write) by the
        classifier — the FC11 empty-unclassified property, re-asserted
        through the census."""
        cfg = _classifier()
        shown = set()
        for meta in agent_metas:
            shown |= _resolved_plane_tools(meta)
        for name in shown:
            verb = name.split("_", 1)[1]
            classified = is_read_tool(name, cfg) or any(
                verb.startswith(p) for p in cfg.write_tool_patterns
            )
            assert classified, f"verb {verb!r} is unclassified"
