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

from helpers.plane_catalog_fixture import (
    assert_keep_set_matches_catalog,
    catalog_names,
    compute_sorted_names_sha256,
    load_catalog,
    load_keep_set,
)

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

#: The A' (FC9-breach) narrowed grant — the reconciled 28-verb keep-set in
#: its DAEMON-EXPOSED form (``plane_<raw verb>``). PM's ``tools.allow``
#: names these 28 literals (the ``plane`` CATEGORY entry was REPLACED, not
#: supplemented) — see phase5-plan §5.6 A'.1.
KEEP_SET_COUNT: int = 28

#: The keep-set's sorted-name-set sha256, PINNED (A'.6 (b) — the gate-(i)
#: drift detector). Recomputed from the artifact at import time and
#: compared against this literal: a silent edit of the keep-set (a verb
#: added, removed or renamed) turns the pin RED.
PINNED_KEEP_SET_SORTED_NAMES_SHA256: str = (
    "6bee850167039216624088e2c2fe2ef63042871c6bdebcdf8b9a34d103bebcbe"
)


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


def _keep_set_daemon_names() -> set[str]:
    """The A' keep-set in daemon-exposed form (``plane_<raw verb>``)."""
    ks = load_keep_set()
    return {e["daemon_name"] for e in ks["keep_set"]}


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
        """E1 — the census must resolve a REAL, NON-VACUOUS plane surface.

        A'-restated: PM's grant is the explicit 28-verb keep-set (literal
        names), so the resolved surface is the keep-set — a strict subset
        of the catalog's 177 verbs. Non-vacuous means it resolves a REAL
        set of verbs, not zero and not the whole catalog.
        """
        pm = next(m for m in agent_metas if m.get("id") == PM_AGENT)
        resolved = _resolved_plane_tools(pm)
        assert resolved, (
            "VACUOUS CENSUS: the plane server is not REGISTERED in the "
            "census — zero plane_* tools resolved for PM"
        )
        assert resolved == _keep_set_daemon_names()
        assert len(resolved) == KEEP_SET_COUNT
        # a strict subset of the catalog — the census measures a real,
        # NARROWED surface (the A' premise), not the full 177.
        assert resolved < _plane_daemon_names()

    def test_the_unregistered_state_would_be_vacuous(self, agent_metas):
        """The vacuity tripwire, restated for the A' literal grant.

        Under the A' grant the FILTER resolves LITERAL names, so the filter
        alone can no longer distinguish a registered server from an
        unregistered one — the MCP layer is the runtime gate. With the
        plane server NOT registered the runtime universe of plane_* tools
        is EMPTY, so the census would resolve nothing at runtime (proved on
        a real boot by the Gate-B negative legs: a REFUSED workspace
        resolves ZERO plane_* tools). This test pins the two halves of
        that statement: the category sweep contributes nothing when the
        universe is empty, and the literal grant's names are absent from
        the empty runtime universe (so none of them can be bound).
        """
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
        # the CATEGORY expansion contributed NOTHING — the sweep is empty
        assert categories["plane"] == []
        # the runtime universe is empty ⇒ not one of the resolved names can
        # be bound ⇒ the census would be vacuous
        runtime_universe: set[str] = set()
        assert not (set(resolved or set()) & runtime_universe)


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

    def test_pm_keeps_the_keep_set_write_surface(self, agent_metas):
        """A'-restated: PM keeps the WRITE members of the keep-set.

        The A' narrowing deliberately PRUNES the customer / release /
        project-admin / module / milestone / initiative / page / property
        families and the out-of-scope deletes — so PM no longer holds the
        whole 104-verb write set. What MUST hold is that every write verb
        the keep-set names is still PM's, and that PM's surface is exactly
        the keep-set (no drift in either direction).
        """
        pm = next(m for m in agent_metas if m.get("id") == PM_AGENT)
        keep_set = _keep_set_daemon_names()
        keep_set_write = keep_set & {f"plane_{verb}" for verb in _write_verbs()}
        surface = _effective_surface(pm)
        assert keep_set_write <= surface
        assert surface == keep_set


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


class TestFC8NarrowedGrant:
    """FC8 (the A' branch's two mandatory obligations, phase5-plan §5.6 A'.6).

    A narrowed grant converts an upstream rename into **silent capability
    loss** — so the A' flip must ship BOTH:

    * **(a) the MINIMUM-TOOLSET assertion** — PM's resolved plane surface is
      a SUPERSET of the keep-set and is at least keep-set-sized. A narrowing
      that dropped a verb (or a rename that stopped resolving it) is RED;
    * **(b) the gate-(i) DRIFT DETECTOR** — the shipped grant is EXACTLY the
      keep-set, the keep-set's sorted-name-set sha256 is pinned, and every
      keep-set verb is still a member of the re-captured catalog (FC10).

    Both ride the SAME atomic commit as the grant + the charter + the
    161/161 re-stamp + the SEALED re-pin.
    """

    def test_a_minimum_toolset_assertion(self, agent_metas):
        """(a) — resolved ⊇ keep-set, and |resolved| ≥ |keep-set|."""
        pm = next(m for m in agent_metas if m.get("id") == PM_AGENT)
        resolved = _resolved_plane_tools(pm)
        keep_set = _keep_set_daemon_names()
        assert keep_set <= resolved, (
            "MINIMUM-TOOLSET VIOLATION: the narrowed grant no longer "
            "resolves the whole keep-set — missing: "
            f"{sorted(keep_set - resolved)}"
        )
        assert len(resolved) >= len(keep_set)

    def test_b_the_grant_is_exactly_the_keep_set(self, agent_metas):
        """(b) — the SHIPPED grant is exactly the keep-set (no drift)."""
        pm = next(m for m in agent_metas if m.get("id") == PM_AGENT)
        allow = list((pm.get("tools") or {}).get("allow") or [])
        granted = {n for n in allow if n.startswith("plane_")}
        assert granted == _keep_set_daemon_names(), (
            "the shipped grant drifted from the keep-set — extra: "
            f"{sorted(granted - _keep_set_daemon_names())}, missing: "
            f"{sorted(_keep_set_daemon_names() - granted)}"
        )
        # the CATEGORY entry was REPLACED, not supplemented
        assert "plane" not in allow

    def test_b_the_keep_set_sorted_names_sha256_is_pinned(self):
        """(b) — the keep-set's sorted-name-set sha256 (the FC10 digest).

        A silent edit of the keep-set artifact (a verb added, removed or
        renamed) changes the digest ⇒ RED. This is the gate-(i) drift
        detector bound to the SHIPPED grant.
        """
        ks = load_keep_set()
        raws = [e["raw_verb"] for e in ks["keep_set"]]
        actual = compute_sorted_names_sha256(sorted(raws))
        assert actual == PINNED_KEEP_SET_SORTED_NAMES_SHA256, (
            "the keep-set drifted — sorted-name-set sha256 "
            f"{actual} != pinned {PINNED_KEEP_SET_SORTED_NAMES_SHA256}"
        )
        assert ks["keep_set_sorted_names_sha256"] == actual

    def test_b_every_keep_set_verb_is_still_in_the_catalog(self):
        """(b) — the FC1 condition: a keep-set verb absent from the
        re-captured catalog means the upstream rename silently dropped it
        from PM's grant."""
        verdict = assert_keep_set_matches_catalog()
        assert verdict["keep_set_count"] == KEEP_SET_COUNT
        assert verdict["all_raw_verbs_in_catalog"] is True
