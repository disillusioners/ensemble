"""Registration + allow-list proof for the ``board`` category
(ari-tools Phase 1 — the Workbook).

Covers the phase1-plan §8 N8 matrix:

* ``"board"`` is in the ``tools.allow`` of ``agents/ari/meta.json``
  — and in NO other agent's allow list (ARI-ONLY — the user ruling of
  2026-10-20 RETRACTS the earlier "ari + leader" audience);
* the four tool names are in ``DYNAMIC_TOOL_NAMES`` AND
  ``KNOWN_TOOL_NAMES`` AND the source-discovered universe (the
  three-step registration seam — decorator-only is SILENTLY INVISIBLE);
* ``CATEGORY_MODULES["board"]`` resolves to the category module;
* ``"board"`` is in ``PRIVILEGED_TOOL_CATEGORIES`` — the NINTH entry;
* every allow entry of the two meta.json files is a known tool or a
  known category (the board entry must not break the startup
  validation).

Plus the THREE mandatory tool-visibility assertions:

  1. POSITIVE — an agent whose allow list contains ``"board"``
     resolves to the four tools;
  2. PRIVILEGED DEFAULT-DENY — an agent with an EMPTY allow list
     resolves to ZERO board tools (this is the property that made the
     category privileged);
  3. NEGATIVE — an agent whose allow list is non-empty and LACKS
     ``"board"`` resolves to none.

SAFETY FENCE: synthetic agents are staged under pytest's ``tmp_path``
(a copy of the real ``agents/watcher/meta.json`` with a replaced tools
config) — no HOME-derived or live-shaped path is ever resolved.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import daemon.registry as dr
from daemon.registry import AgentRegistry
from daemon.tools._tool_registry import (
    CATEGORY_MODULES,
    DYNAMIC_TOOL_NAMES,
    KNOWN_TOOL_NAMES,
    PRIVILEGED_TOOL_CATEGORIES,
    discover_all_tool_names,
    discover_source_only_tool_names,
)

REPO_ROOT = Path(__file__).resolve().parents[2]

BOARD_CATEGORY = "board"
BOARD_MODULE = "daemon.tools.board_tools"
BOARD_TOOL_NAMES: frozenset[str] = frozenset({
    "board_create",
    "board_update",
    "board_list",
    "board_get",
})

#: The NINTH entry of the privileged set (the set held exactly eight
#: before phase 1). Phase 2 (the self-alarm wrapper) added the TENTH
#: entry, ``self-alarm`` and phase 3 (the live-views surface) added the
#: ELEVENTH + TWELFTH (``view-write`` / ``board-link``) — the same-PR
#: exact-set pin moves with them.
EXPECTED_PRIVILEGED_SET: frozenset[str] = frozenset({
    "system_upgrade",
    "system-log",
    "ens-db",
    "ens-env",
    "view-views",
    "cu",
    "secrets",
    "settings",
    "board",
    "self-alarm",
    "view-write",
    # nest-ops (2026-10-21): the two maintenancer fleet categories
    # (read #13 / act #14) — opt-in-only, never default-granted.
    "instance_read",
    "instance_ops",
    "board-link",
    # wedge-capture (2026-10-22): the READ-ONLY capture-evidence
    # surface — ``wedge_capture_list`` + ``wedge_capture_get``.
    # PRIVILEGED (entry #16): the capture payload is a privileged
    # diagnostic artifact; the empty-allow inherit universe must
    # NOT auto-grant it. The ari + leader + maintenancer allow
    # entries are the only access path, and every tool body ALSO
    # carries its own agent_id gate.
    "wedge-capture",
# remote-hands center gateway (2026-10-22): the ``mcp_rpc``
# category — the agent-facing surface that drives an ENROLLED
# remote machine (SPEC.md v0.1). opt-in-only; the ari + leader
# allow entries are the only access path, and every tool body
# ALSO carries its own agent_id gate.
"mcp_rpc",
})

#: The ONLY agent allowed to hold the board category.
#:
#: ARI-ONLY (user ruling 2026-10-20, Discord, direct) — the earlier
#: "ari + leader" audience is RETRACTED.
BOARD_ALLOW_AGENTS = ("ari",)


# ── the static registration points ───────────────────────────────────────


class TestStaticRegistration:
    def test_step1_ast_discovery_finds_all_four(self) -> None:
        """Step 1 — the AST walker finds the factory-created ``@tool``
        functions at any nesting depth."""
        discovered = discover_source_only_tool_names()
        missing = BOARD_TOOL_NAMES - discovered
        assert missing == set(), f"AST discovery missed: {missing}"

    def test_step2_category_modules_entry(self) -> None:
        """Step 2 — ``CATEGORY_MODULES['board']`` points at the module."""
        assert CATEGORY_MODULES.get(BOARD_CATEGORY) == BOARD_MODULE

    def test_step3_dynamic_tool_names_contains_all_four(self) -> None:
        """Step 3 — the factory-created names live in
        ``DYNAMIC_TOOL_NAMES`` so startup validation knows them before
        any instance is built."""
        missing = BOARD_TOOL_NAMES - set(DYNAMIC_TOOL_NAMES)
        assert missing == set(), f"DYNAMIC_TOOL_NAMES missing: {missing}"

    def test_step3b_known_tool_names_contains_all_four(self) -> None:
        """Step 3b — the frozen-binary fallback universe carries them."""
        missing = BOARD_TOOL_NAMES - set(KNOWN_TOOL_NAMES)
        assert missing == set(), f"KNOWN_TOOL_NAMES missing: {missing}"

    def test_step4_construction_extend_is_present_in_source(self) -> None:
        """Step 4 — the CRITICAL list-append is greppable in
        ``daemon/tools/instance.py``. Decorator-only registration is
        SILENTLY INVISIBLE."""
        source = (
            REPO_ROOT / "daemon" / "tools" / "instance.py"
        ).read_text(encoding="utf-8")
        assert "from .board_tools import create_board_tools" in source
        assert "create_board_tools_if_available(" in source
        assert "tools.extend(board_tool_list)" in source

    def test_the_extend_precedes_the_filter_and_the_doc_scan(self) -> None:
        """The extend MUST precede ``scan_tools_for_full_docs`` and
        ``_apply_tool_filter`` — a later extend either bypasses the
        privileged strip or is invisible to the help tool."""
        source = (
            REPO_ROOT / "daemon" / "tools" / "instance.py"
        ).read_text(encoding="utf-8")
        extend_idx = source.find("tools.extend(board_tool_list)")
        scan_idx = source.find("scan_tools_for_full_docs(tools)")
        filter_idx = source.find("_apply_tool_filter(tools, agent_id")
        assert extend_idx != -1
        assert scan_idx != -1
        assert filter_idx != -1
        assert extend_idx < scan_idx, (
            "the board extend must precede scan_tools_for_full_docs"
        )
        assert extend_idx < filter_idx, (
            "the board extend must precede _apply_tool_filter — a later "
            "extend BYPASSES the privileged-category strip"
        )

    def test_decorator_order_register_above_tool(self) -> None:
        """``@register_tool_category("board")`` MUST sit above
        ``@tool``."""
        source = (
            REPO_ROOT / "daemon" / "tools" / "board_tools.py"
        ).read_text(encoding="utf-8")
        assert source.count('@register_tool_category("board")') >= 4
        register_idx = source.find('@register_tool_category("board")')
        tool_idx = source.find("@tool(")
        assert register_idx != -1
        assert tool_idx != -1
        assert register_idx < tool_idx


# ── the privileged category ──────────────────────────────────────────────


class TestPrivilegedCategory:
    def test_board_is_in_the_privileged_set(self) -> None:
        assert BOARD_CATEGORY in PRIVILEGED_TOOL_CATEGORIES

    def test_the_set_is_exactly_the_fifteen_entries(self) -> None:
        """The set is exact — the phase-2 commit moved this pin with the
        tenth entry (``self-alarm``) and the phase-3 commit moved it
        again with the eleventh + twelfth (``view-write`` /
        ``board-link``), so the set never goes red between two commits
        (the SAME-PR rule)."""
        assert PRIVILEGED_TOOL_CATEGORIES == EXPECTED_PRIVILEGED_SET

    def test_board_is_the_ninth_entry(self) -> None:
        """``board`` was entry #9 — the set went 8 -> 9 in phase 1,
        9 -> 10 in phase 2 (``self-alarm``) and 10 -> 12 in phase 3
        (``view-write`` + ``board-link``).

        The registry constant is a frozenset (unordered), so the
        "ninth entry" is a property of the SET SIZE at the time of the
        phase-1 commit and of the registry's literal order afterwards;
        the comment block in the registry records the ordinal."""
        assert len(PRIVILEGED_TOOL_CATEGORIES) == 16
        source = (
            REPO_ROOT / "daemon" / "tools" / "_tool_registry.py"
        ).read_text(encoding="utf-8")
        assert '"board",' in source
        # The board is still the ninth entry of the registry's literal
        # order — the tenth (``self-alarm``) and the eleventh/twelfth
        # (``view-write`` / ``board-link``) were appended after it.
        assert source.index('"board",') < source.index('"self-alarm",')
        assert source.index('"self-alarm",') < source.index('"view-write",')
        assert source.index('"view-write",') < source.index('"board-link",')


# ── the allow-lists ──────────────────────────────────────────────────────


class TestAllowLists:
    def _allow(self, agent_id: str) -> list:
        meta = json.loads(
            (REPO_ROOT / "agents" / agent_id / "meta.json").read_text(
                encoding="utf-8"
            )
        )
        return meta["tools"]["allow"]

    @pytest.mark.parametrize("agent_id", BOARD_ALLOW_AGENTS)
    def test_only_ari_allows_the_board_category(self, agent_id: str) -> None:
        assert BOARD_CATEGORY in self._allow(agent_id)

    def test_leader_does_not_hold_the_board_category(self) -> None:
        """ARI-ONLY (user ruling 2026-10-20) — leader's allow list lost
        the ``board`` entry; the leader's OTHER entries are untouched."""
        assert BOARD_CATEGORY not in self._allow("leader")

    def test_board_is_the_last_entry_of_both_allow_arrays(self) -> None:
        """``board`` was appended at the END of both arrays (D15).

        Phase 2 (the self-alarm wrapper) appended its own category to
        ari's array, so the board is no longer the LAST entry there —
        the phase-1 convention holds for leader, and for ari the board
        is followed ONLY by the phase-2 category.

        Phase 3 (the live-views surface) appended ``view-write`` +
        ``board-link`` at the END of ari's array — so the trailing set
        is now the phase-2 + phase-3 categories.

        Phase B of the remote-hands center gateway (2026-10-22) appended
        the ``mcp_rpc`` category at the END of ari's array — so the
        trailing set is now the phase-2 + phase-3 + remote-hands
        categories. The pin's INTENT (only the categories that were
        appended AFTER the board may follow it) is unchanged; the SET
        grew by one entry.

        NOTE (user ruling 2026-10-20): the board categories are
        ARI-ONLY, so leader's array no longer carries ``board`` (nor
        ``board-link``) at all — the trailing discipline is asserted
        for ari alone.
        """
        trailing_by_agent = {
            "ari": ["self-alarm", "view-write", "board-link", "mcp_rpc", "wedge-capture"],
        }
        for agent_id in BOARD_ALLOW_AGENTS:
            allow = self._allow(agent_id)
            assert BOARD_CATEGORY in allow
            trailing = allow[allow.index(BOARD_CATEGORY) + 1 :]
            assert trailing == trailing_by_agent[agent_id], (
                f"{agent_id}: only the phase-2 + phase-3 + remote-hands "
                f"categories may follow the board, got {allow}"
            )

    def test_no_other_agent_holds_the_board_category(self) -> None:
        """ARI-ONLY (user ruling 2026-10-20) — the board category goes to
        ari alone, to NOBODY else. The in-tree population is every
        agent dir under ``agents/``."""
        offenders = []
        for meta_path in sorted((REPO_ROOT / "agents").glob("*/meta.json")):
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            tools_cfg = meta.get("tools") or {}
            allow = tools_cfg.get("allow") or []
            if BOARD_CATEGORY in allow:
                offenders.append(meta_path.parent.name)
        assert sorted(offenders) == sorted(BOARD_ALLOW_AGENTS), (
            f"the board category is held by unexpected agents: {offenders}"
        )

    @pytest.mark.parametrize("agent_id", BOARD_ALLOW_AGENTS)
    def test_every_allow_entry_is_a_known_tool_or_category(
        self, agent_id: str
    ) -> None:
        """The board entry must not break the startup allow-list
        validation — every entry resolves to a known tool or a known
        category."""
        known_tools = discover_all_tool_names()
        known_categories = set(CATEGORY_MODULES)
        for entry in self._allow(agent_id):
            assert (
                entry in known_tools or entry in known_categories
            ), f"{agent_id}: unknown tools.allow entry {entry!r}"


# ── the live resolution (three mandatory assertions) ─────────────────────


def _stage_synthetic_agent(
    tmp_path: Path, agent_id: str, tools_cfg: dict | None
) -> Path:
    """Stage a synthetic agent dir under tmp_path by cloning watcher's
    real meta.json shape with a replaced tools config."""
    real_meta = json.loads(
        (REPO_ROOT / "agents" / "watcher" / "meta.json").read_text(
            encoding="utf-8"
        )
    )
    real_meta.pop("watchover", None)  # irrelevant for tool filtering
    real_meta["id"] = agent_id
    real_meta["name"] = agent_id.title()
    real_meta["team_members"] = []
    real_meta["innate_skills"] = []
    real_meta["tools"] = tools_cfg
    agents_dir = tmp_path / "agents"
    agent_dir = agents_dir / agent_id
    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / "meta.json").write_text(json.dumps(real_meta), encoding="utf-8")
    return agents_dir


@pytest.fixture
def registry_for(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Factory fixture: stage a synthetic agent, install a registry that
    discovers it, and return a fresh tool-builder bound to it."""

    def _install(agents_dir: Path) -> AgentRegistry:
        registry = AgentRegistry(agents_dir)
        registry.discover()
        monkeypatch.setattr(dr, "_registry", registry)
        return registry

    return _install


def _build_instance_tools(agent_id: str) -> dict[str, object]:
    from daemon.tools.instance import create_instance_tools

    manager = MagicMock(name="InstanceManager")
    manager.config.daemon.port = 0
    manager.config.llm.allowed_models = []
    # Tolerate a None-stub SecretService (the factory dereferences it
    # at call time only).
    manager._secret_service = None
    manager._instance_repository = None
    tools = create_instance_tools(manager, f"inst-{agent_id}", agent_id)
    return {getattr(t, "name", "?"): t for t in tools}


def _board_names(by_name: dict) -> set:
    return set(by_name) & BOARD_TOOL_NAMES


class TestToolVisibility:
    def test_positive_allow_resolves_all_four(
        self, tmp_path: Path, registry_for
    ) -> None:
        """1. POSITIVE — an explicit ``tools.allow: ["board"]`` resolves
        the four tools through the REAL ``create_instance_tools()``."""
        agents_dir = _stage_synthetic_agent(
            tmp_path, "syn-board-allow", {"allow": [BOARD_CATEGORY]}
        )
        registry = registry_for(agents_dir)
        assert registry.exists("syn-board-allow")
        by_name = _build_instance_tools("syn-board-allow")
        assert _board_names(by_name) == BOARD_TOOL_NAMES, (
            f"tools.allow=[board] must resolve all four, got "
            f"{_board_names(by_name)}"
        )

    def test_privileged_default_deny_empty_allow(
        self, tmp_path: Path, registry_for
    ) -> None:
        """2. PRIVILEGED DEFAULT-DENY — an EMPTY allow list expands to
        every category EXCEPT the privileged ones, so an empty-allow
        agent resolves ZERO board tools. This is the property that made
        the category privileged."""
        _stage_synthetic_agent(
            tmp_path, "syn-board-empty-allow", {"allow": []}
        )
        registry = registry_for(tmp_path / "agents")
        assert registry.exists("syn-board-empty-allow")
        by_name = _build_instance_tools("syn-board-empty-allow")
        assert _board_names(by_name) == set(), (
            "empty-allow agent default-granted the privileged board "
            "category — the privileged default-deny is broken"
        )
        # …but the ordinary categories are NOT stripped — the exclusion
        # is scoped to privileged categories only.
        assert "bash" in by_name or "time" in by_name

    def test_negative_non_empty_allow_without_the_category(
        self, tmp_path: Path, registry_for
    ) -> None:
        """3. NEGATIVE — a non-empty allow list that LACKS ``"board"``
        resolves to none."""
        _stage_synthetic_agent(
            tmp_path,
            "syn-board-non-empty",
            {"allow": ["bash", "filesystem", "time", "help"]},
        )
        registry_for(tmp_path / "agents")
        by_name = _build_instance_tools("syn-board-non-empty")
        assert _board_names(by_name) == set(), (
            "a non-empty allow list without the board category must not "
            "resolve any board tool"
        )

    def test_no_tools_config_at_all_default_denies(
        self, tmp_path: Path, registry_for
    ) -> None:
        """The other default-grant path — an agent with NO ``tools`` key
        at all (the ``agent_create``-born custom-agent shape) — also
        resolves zero board tools."""
        _stage_synthetic_agent(tmp_path, "syn-board-no-tools", None)
        registry_for(tmp_path / "agents")
        by_name = _build_instance_tools("syn-board-no-tools")
        assert _board_names(by_name) == set()
