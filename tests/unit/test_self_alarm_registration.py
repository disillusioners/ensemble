"""Registration + allow-list proof for the ``self-alarm`` category
(ari-tools Phase 2 — the alarm/timer wrapper).

Covers the phase2-plan Task 6 / Task 10 matrix:

* ``"self-alarm"`` is in the ``tools.allow`` of ``agents/ari/meta.json``
  — and in NO other agent's allow list (the leader pre-ruling:
  ari-for-ari);
* the tool name is in ``DYNAMIC_TOOL_NAMES`` AND ``KNOWN_TOOL_NAMES``
  AND the source-discovered universe (the three-step registration seam
  — decorator-only is SILENTLY INVISIBLE);
* ``CATEGORY_MODULES["self-alarm"]`` resolves to a ``daemon.tools.*``
  module (a ``daemon.services.*`` value is NEVER discovered);
* the construction ``extend`` is greppable in ``daemon/tools/instance.py``
  and precedes ``scan_tools_for_full_docs`` / ``_apply_tool_filter``;
* ``"self-alarm"`` is in ``PRIVILEGED_TOOL_CATEGORIES`` — the TENTH
  entry (the exact set is ten);
* every allow entry of ari's meta.json is a known tool or a known
  category (the new entry must not break the startup validation).

Plus the THREE mandatory tool-visibility assertions:

  1. POSITIVE — an agent whose allow list contains ``"self-alarm"``
     resolves to the tool;
  2. PRIVILEGED DEFAULT-DENY — an agent with an EMPTY allow list
     resolves to ZERO self-alarm tools (this is the property that made
     the category privileged: the empty-allow inherit universe must not
     be able to arm an alarm whose text a fresh Ari instance executes
     verbatim);
  3. NEGATIVE — an agent whose allow list is non-empty and LACKS
     ``"self-alarm"`` resolves to none.

Plus the FIVE tool-doc obligation pins (the ``_full_doc_`` contract).

NOT in this phase: the ``agents/ari/tools_note.md`` forbidden-token +
cross-ref assertions — that section is authored in the conventions step
(phase2-plan D13), so those assertions move to that step's checklist.

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
from daemon.tools.self_alarm import CATEGORY_DOC, CATEGORY_NAME

REPO_ROOT = Path(__file__).resolve().parents[2]

SELF_ALARM_CATEGORY = "self-alarm"
SELF_ALARM_MODULE = "daemon.tools.self_alarm"
SELF_ALARM_TOOL_NAME = "self_alarm"

#: The ``self-alarm`` entry of the privileged set (it joined the set
#: 10th; the set held 9 entries before this phase). Phase 3 (the
#: live-views surface) added the 11th + 12th (``view-write`` /
#: ``board-link``) — the same-PR exact-set pin moves with them. The
#: ``cu`` category was REMOVED 2026-10-24 together with the cu builtin
#: MCP server (16 -> 15 members).
EXPECTED_PRIVILEGED_SET: frozenset[str] = frozenset({
    "system_upgrade",
    "system-log",
    "ens-db",
    "ens-env",
    "view-views",
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
    # PRIVILEGED (entry #12): the capture payload is a privileged
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

#: The ONLY agent allowed to hold the self-alarm category.
SELF_ALARM_ALLOW_AGENTS = ("ari",)


# ── the static registration points ───────────────────────────────────────


class TestStaticRegistration:
    def test_step1_ast_discovery_finds_the_tool(self) -> None:
        """Step 1 — the AST walker finds the factory-created ``@tool``
        function at any nesting depth."""
        discovered = discover_source_only_tool_names()
        assert SELF_ALARM_TOOL_NAME in discovered

    def test_step2_category_modules_entry_is_a_daemon_tools_path(self) -> None:
        """Step 2 — ``CATEGORY_MODULES['self-alarm']`` points at the
        module — and the value MUST be a ``daemon.tools.*`` path: the
        AST scanner resolves the value as
        ``daemon/tools/<parts[2:]>.py``; a ``daemon.services.*`` value is
        never discovered and the exact-equality drift pin fails at boot.
        """
        value = CATEGORY_MODULES.get(SELF_ALARM_CATEGORY)
        assert value == SELF_ALARM_MODULE
        assert value.startswith("daemon.tools.")

    def test_step3_dynamic_tool_names_contains_the_tool(self) -> None:
        """Step 3 — the factory-created name lives in
        ``DYNAMIC_TOOL_NAMES`` so startup validation knows it before any
        instance is built."""
        assert SELF_ALARM_TOOL_NAME in set(DYNAMIC_TOOL_NAMES)

    def test_step3b_known_tool_names_contains_the_tool(self) -> None:
        """Step 3b — the frozen-binary fallback universe carries it."""
        assert SELF_ALARM_TOOL_NAME in set(KNOWN_TOOL_NAMES)

    def test_step4_construction_extend_is_present_in_source(self) -> None:
        """Step 4 — the CRITICAL list-append is greppable in
        ``daemon/tools/instance.py``. Decorator-only registration is
        SILENTLY INVISIBLE."""
        source = (REPO_ROOT / "daemon" / "tools" / "instance.py").read_text(
            encoding="utf-8"
        )
        assert "from .self_alarm import create_self_alarm_tools" in source
        assert "create_self_alarm_tools_if_available(" in source
        assert "tools.extend(self_alarm_tool_list)" in source

    def test_the_extend_precedes_the_filter_and_the_doc_scan(self) -> None:
        """The extend MUST precede ``scan_tools_for_full_docs`` and
        ``_apply_tool_filter`` — a later extend either bypasses the
        privileged strip or is invisible to the help tool."""
        source = (REPO_ROOT / "daemon" / "tools" / "instance.py").read_text(
            encoding="utf-8"
        )
        extend_idx = source.find("tools.extend(self_alarm_tool_list)")
        scan_idx = source.find("scan_tools_for_full_docs(tools)")
        filter_idx = source.find("_apply_tool_filter(tools, agent_id")
        assert extend_idx != -1
        assert scan_idx != -1
        assert filter_idx != -1
        assert extend_idx < scan_idx, (
            "the self-alarm extend must precede scan_tools_for_full_docs"
        )
        assert extend_idx < filter_idx, (
            "the self-alarm extend must precede _apply_tool_filter — a later "
            "extend BYPASSES the privileged-category strip"
        )

    def test_decorator_order_register_above_tool(self) -> None:
        """``@register_tool_category("self-alarm")`` MUST sit above
        ``@tool``."""
        source = (REPO_ROOT / "daemon" / "tools" / "self_alarm.py").read_text(
            encoding="utf-8"
        )
        assert source.count('@register_tool_category("self-alarm")') >= 1
        register_idx = source.find('@register_tool_category("self-alarm")')
        tool_idx = source.find("@tool(")
        assert register_idx != -1
        assert tool_idx != -1
        assert register_idx < tool_idx

    def test_the_category_metadata_is_present(self) -> None:
        """``CATEGORY_NAME`` / ``CATEGORY_DOC`` are the category's
        discovery surface."""
        assert CATEGORY_NAME == "Self-Alarm"
        assert "self_alarm" in CATEGORY_DOC


# ── the privileged category ──────────────────────────────────────────────


class TestPrivilegedCategory:
    def test_self_alarm_is_in_the_privileged_set(self) -> None:
        assert SELF_ALARM_CATEGORY in PRIVILEGED_TOOL_CATEGORIES

    def test_the_set_is_exactly_the_fifteen_entries(self) -> None:
        assert PRIVILEGED_TOOL_CATEGORIES == EXPECTED_PRIVILEGED_SET

    def test_the_set_has_fifteen_members(self) -> None:
        assert len(PRIVILEGED_TOOL_CATEGORIES) == 15


# ── the allow-lists ──────────────────────────────────────────────────────


class TestAllowLists:
    def _allow(self, agent_id: str) -> list:
        meta = json.loads(
            (REPO_ROOT / "agents" / agent_id / "meta.json").read_text(
                encoding="utf-8"
            )
        )
        return meta["tools"]["allow"]

    @pytest.mark.parametrize("agent_id", SELF_ALARM_ALLOW_AGENTS)
    def test_ari_allows_the_self_alarm_category(self, agent_id: str) -> None:
        assert SELF_ALARM_CATEGORY in self._allow(agent_id)

    def test_no_other_agent_holds_the_self_alarm_category(self) -> None:
        """The leader pre-ruling: ari-for-ari — the category goes to ari
        and to NOBODY else. The in-tree population is every agent dir
        under ``agents/``."""
        offenders = []
        for meta_path in sorted((REPO_ROOT / "agents").glob("*/meta.json")):
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            tools_cfg = meta.get("tools") or {}
            allow = tools_cfg.get("allow") or []
            if SELF_ALARM_CATEGORY in allow:
                offenders.append(meta_path.parent.name)
        assert sorted(offenders) == sorted(SELF_ALARM_ALLOW_AGENTS), (
            f"the self-alarm category is held by unexpected agents: {offenders}"
        )

    @pytest.mark.parametrize("agent_id", SELF_ALARM_ALLOW_AGENTS)
    def test_every_allow_entry_is_a_known_tool_or_category(
        self, agent_id: str
    ) -> None:
        """The self-alarm entry must not break the startup allow-list
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


def _build_instance_tools(agent_id: str) -> dict:
    from daemon.tools.instance import create_instance_tools

    manager = MagicMock(name="InstanceManager")
    manager.config.daemon.port = 0
    manager.config.llm.allowed_models = []
    # Tolerate a None-stub SecretService (the factory dereferences it
    # at call time only).
    manager._secret_service = None
    manager._instance_repository = None
    # The scheduling service is NOT mounted on the double ⇒ the
    # self-alarm factory returns an empty list, exactly like every other
    # ``*_if_available`` factory. The REAL manager mounts it, and the
    # construction extend is pinned by the source assertions above.
    manager.scheduling_service = None
    tools = create_instance_tools(manager, f"inst-{agent_id}", agent_id)
    return {getattr(t, "name", "?"): t for t in tools}


class TestToolVisibility:
    def test_the_positive_case_needs_the_mounted_service(
        self, tmp_path: Path, registry_for
    ) -> None:
        """1. POSITIVE — an explicit ``tools.allow: ["self-alarm"]``
        resolves the tool through the REAL ``create_instance_tools()``.

        The tool is factory-built, so the manager must mount the
        scheduling service — the double does, with the real service
        module (no DB is touched: the tools are only CONSTRUCTED, never
        called)."""
        from daemon.services import scheduling_service

        agents_dir = _stage_synthetic_agent(
            tmp_path, "syn-self-alarm-allow", {"allow": [SELF_ALARM_CATEGORY]}
        )
        registry = registry_for(agents_dir)
        assert registry.exists("syn-self-alarm-allow")

        from daemon.tools.instance import create_instance_tools

        manager = MagicMock(name="InstanceManager")
        manager.config.daemon.port = 0
        manager.config.llm.allowed_models = []
        manager._secret_service = None
        manager._instance_repository = None
        manager.scheduling_service = scheduling_service
        tools = create_instance_tools(manager, "inst-syn", "syn-self-alarm-allow")
        by_name = {getattr(t, "name", "?"): t for t in tools}
        assert SELF_ALARM_TOOL_NAME in by_name, (
            "tools.allow=[self-alarm] must resolve the self_alarm tool"
        )

    def test_privileged_default_deny_empty_allow(
        self, tmp_path: Path, registry_for
    ) -> None:
        """2. PRIVILEGED DEFAULT-DENY — an EMPTY allow list expands to
        every category EXCEPT the privileged ones, so an empty-allow
        agent resolves ZERO self-alarm tools. This is the property that
        made the category privileged: the empty-allow inherit universe
        (watcher and every tools-less custom agent) must not be able to
        arm an alarm whose text a fresh Ari instance executes verbatim.
        """
        from daemon.services import scheduling_service

        _stage_synthetic_agent(
            tmp_path, "syn-self-alarm-empty-allow", {"allow": []}
        )
        registry = registry_for(tmp_path / "agents")
        assert registry.exists("syn-self-alarm-empty-allow")

        from daemon.tools.instance import create_instance_tools

        manager = MagicMock(name="InstanceManager")
        manager.config.daemon.port = 0
        manager.config.llm.allowed_models = []
        manager._secret_service = None
        manager._instance_repository = None
        manager.scheduling_service = scheduling_service
        tools = create_instance_tools(
            manager, "inst-syn", "syn-self-alarm-empty-allow"
        )
        by_name = {getattr(t, "name", "?"): t for t in tools}
        assert SELF_ALARM_TOOL_NAME not in by_name, (
            "empty-allow agent default-granted the privileged self-alarm "
            "category — the privileged default-deny is broken"
        )
        # …but the ordinary categories are NOT stripped — the exclusion
        # is scoped to privileged categories only.
        assert "bash" in by_name or "time" in by_name

    def test_negative_non_empty_allow_without_the_category(
        self, tmp_path: Path, registry_for
    ) -> None:
        """3. NEGATIVE — a non-empty allow list that LACKS
        ``"self-alarm"`` resolves to none."""
        from daemon.services import scheduling_service

        _stage_synthetic_agent(
            tmp_path,
            "syn-self-alarm-non-empty",
            {"allow": ["bash", "filesystem", "time", "help"]},
        )
        registry_for(tmp_path / "agents")

        from daemon.tools.instance import create_instance_tools

        manager = MagicMock(name="InstanceManager")
        manager.config.daemon.port = 0
        manager.config.llm.allowed_models = []
        manager._secret_service = None
        manager._instance_repository = None
        manager.scheduling_service = scheduling_service
        tools = create_instance_tools(
            manager, "inst-syn", "syn-self-alarm-non-empty"
        )
        by_name = {getattr(t, "name", "?"): t for t in tools}
        assert SELF_ALARM_TOOL_NAME not in by_name, (
            "a non-empty allow list without the self-alarm category must "
            "not resolve the self_alarm tool"
        )

    def test_no_tools_config_at_all_default_denies(
        self, tmp_path: Path, registry_for
    ) -> None:
        """The other default-grant path — an agent with NO ``tools`` key
        at all (the ``agent_create``-born custom-agent shape) — also
        resolves zero self-alarm tools."""
        _stage_synthetic_agent(tmp_path, "syn-self-alarm-no-tools", None)
        registry_for(tmp_path / "agents")
        by_name = _build_instance_tools("syn-self-alarm-no-tools")
        assert SELF_ALARM_TOOL_NAME not in by_name


# ── the five tool-doc obligations (phase2-plan §Tool-doc obligations) ────


def _full_doc() -> str:
    from daemon.tools.self_alarm import create_self_alarm_tools

    service = MagicMock()
    tools = create_self_alarm_tools(service, current_instance_id="i", agent_id="ari")
    return tools[0]._full_doc_


def _flat_doc() -> str:
    """The tool doc with its line wrapping collapsed.

    The obligation pins assert on PHRASES, not on the line breaks — a
    re-wrapped doc must not break the contract.
    """
    return " ".join(_full_doc().split())


class TestToolDocObligations:
    def test_obligation_1_lateness_both_cap_states_and_the_skip_signature(self) -> None:
        """Lateness — BOTH cap states named explicitly + the skip
        signature."""
        doc = _flat_doc()
        assert "one_shot_max_lateness_seconds" in doc
        assert "lateness_cap_seconds" in doc
        # The UNLIMITED cap state.
        assert "UNLIMITED lateness" in doc
        # The CAPPED cap state + its skip signature.
        assert "skipped" in doc
        assert "no dispatch" in doc.lower() or "dispatches NOTHING" in doc
        assert "no user-visible signal" in doc

    def test_obligation_2_the_board_is_the_surface_never_a_ping(self) -> None:
        """The board is the user-visible surface — a ping is NOT
        promised."""
        doc = _flat_doc()
        assert "The board is the user-visible surface" in doc
        assert "NO ping" in doc
        assert "What is NOT promised" in doc
        assert "Slack" in doc
        assert "interruption" in doc

    def test_obligation_3_the_fire_is_unobservable(self) -> None:
        """What the wrapper CANNOT observe."""
        doc = _flat_doc()
        assert "CANNOT observe" in doc
        assert "post-fire hook" in doc
        assert "stopped" in doc

    def test_obligation_4_list_semantics(self) -> None:
        """List semantics — the 100-row cap + the ``status='running'``
        armed filter + what 'stopped' means."""
        doc = _flat_doc()
        assert "task_schedule_list" in doc
        assert "100 rows" in doc
        assert "status='running'" in doc
        assert "dispatched, not acted upon" in doc

    def test_obligation_5_the_label_namespace(self) -> None:
        """The ``self-alarm:`` namespace."""
        doc = _flat_doc()
        assert "self-alarm:<label>" in doc
        assert "prefix is added automatically" in doc

    def test_the_due_at_convention_is_documented(self) -> None:
        """D8 — the board ``due_at`` convention (the prompt-side section
        is authored in the conventions step; the TOOL side ships here)."""
        doc = _flat_doc()
        assert "due date" in doc
        assert "link_board_item" in doc
