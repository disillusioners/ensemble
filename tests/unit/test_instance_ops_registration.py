"""Registration + wiring + vocabulary proof for the TWO nest-ops
categories — ``instance_read`` (4 reads) and ``instance_ops`` (6 acts).

Covers the nest-ops plan's §2.2 registration-pin matrix (AC-1 … AC-5,
AC-10a, AC-10b):

* (a) ``CATEGORY_MODULES`` carries BOTH entries, each a
  ``daemon.tools.*`` path (a ``daemon.services.*`` value is NEVER
  discovered by the AST scanner and the exact-equality drift pin
  fails at boot);
* (b) BOTH categories are in ``PRIVILEGED_TOOL_CATEGORIES``;
* (c) the frozenset LENGTH is 14;
* (d) all 10 tool names are in ``DYNAMIC_TOOL_NAMES`` and
  ``KNOWN_TOOL_NAMES``;
* (e) the AST scanner finds all 10 ``@tool`` functions at any nesting
  depth (decorator-only registration is SILENTLY INVISIBLE);
* (f) BOTH ``create_instance_tools`` extends precede
  ``create_help_tool`` / ``scan_tools_for_full_docs`` /
  ``_apply_tool_filter`` (a later extend is invisible to the help tool
  or BYPASSES the privileged strip);
* (g) AC-10a — the WIRING pin: the ``instance_ops`` protected-target
  gate resolves to the SINGLE canonical helper in
  ``daemon/tools/provenance.py``; there is NO local re-implementation
  (a source assertion, the ``test_agent_restart.py`` directional-rule
  style — NOT a behavioural comparison; the original fixture-equality
  pin was VACUOUS, it compared the helper to itself);
* (h) AC-10b — the VOCABULARY pins: the verdict tokens
  (``ok``/``pclass`` ∈ {direct, inherited}/``depth``/``detail``) and
  the gate's refusal-token mapping are pinned so NEITHER lane can
  silently change semantics;
* allow-lists: the maintenancer holds BOTH categories; ari, leader and
  every other agent hold NEITHER (D-1/D-2/D-13).

SAFETY FENCE: no HOME-derived or live-shaped path is ever resolved by
this file — every read is a repo-relative source read.
"""

from __future__ import annotations

import json
from pathlib import Path

from daemon.tools._tool_registry import (
    CATEGORY_MODULES,
    DYNAMIC_TOOL_NAMES,
    KNOWN_TOOL_NAMES,
    PRIVILEGED_TOOL_CATEGORIES,
    discover_source_only_tool_names,
)

REPO_ROOT = Path(__file__).resolve().parents[2]

READ_CATEGORY = "instance_read"
ACT_CATEGORY = "instance_ops"
READ_MODULE = "daemon.tools.instance_read"
ACT_MODULE = "daemon.tools.instance_ops"

READ_TOOL_NAMES: frozenset[str] = frozenset({
    "instance_list",
    "instance_get",
    "instance_health",
    "fleet_capacity_summary",
    # F1 (nest-ops shakedown, 2026-10-24): the ADDITIVE alias of
    # fleet_capacity_summary — a second literal @tool closure in
    # create_instance_read_tools, same ONE builder. Hand-maintained
    # set used only in the subset checks below (no len(tools) pin).
    "instance_fleet_summary",
})
ACT_TOOL_NAMES: frozenset[str] = frozenset({
    "instance_terminate",
    "instance_reclaim",
    "instance_nudge",
    "instance_mark",
    "instance_answer",
    "instance_dismiss",
})
ALL_TOOL_NAMES: frozenset[str] = READ_TOOL_NAMES | ACT_TOOL_NAMES

#: The nest-ops pin's expected privileged universe — the 12 pre-existing
#: entries plus the two nest-ops categories (12 -> 14, D-13).
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
    "board-link",
    # nest-ops (2026-10-21): the two maintenancer fleet categories
    # (read #13 / act #14) — opt-in-only, never default-granted.
    "instance_read",
    "instance_ops",
})


# ── the static registration points ───────────────────────────────────────


class TestStaticRegistration:
    def test_step1_ast_discovery_finds_all_eleven(self) -> None:
        """Step 1 — the AST walker finds the factory-created ``@tool``
        functions at any nesting depth, in BOTH modules."""
        discovered = discover_source_only_tool_names()
        missing = ALL_TOOL_NAMES - discovered
        assert missing == set(), f"AST discovery missed: {missing}"

    def test_step2_category_modules_entries(self) -> None:
        """Step 2 — both ``CATEGORY_MODULES`` entries point at their
        modules, and each is a ``daemon.tools.*`` path (the AST scanner
        resolves a value as ``daemon/tools/<parts[2:]>.py``)."""
        assert CATEGORY_MODULES.get(READ_CATEGORY) == READ_MODULE
        assert CATEGORY_MODULES.get(ACT_CATEGORY) == ACT_MODULE
        for value in (READ_MODULE, ACT_MODULE):
            assert value.startswith("daemon.tools."), (
                f"{value} is not a daemon.tools.* path — a "
                "daemon.services.* value is never discovered by the "
                "AST scanner and the drift pin fails at boot"
            )

    def test_step3_dynamic_tool_names_contains_all_eleven(self) -> None:
        missing = ALL_TOOL_NAMES - set(DYNAMIC_TOOL_NAMES)
        assert missing == set(), f"DYNAMIC_TOOL_NAMES missing: {missing}"

    def test_step3b_known_tool_names_contains_all_eleven(self) -> None:
        missing = ALL_TOOL_NAMES - set(KNOWN_TOOL_NAMES)
        assert missing == set(), f"KNOWN_TOOL_NAMES missing: {missing}"

    def test_step4_construction_extends_present_in_source(self) -> None:
        """Step 4 — the CRITICAL list-appends are greppable in
        ``daemon/tools/instance.py``. Decorator-only registration is
        SILENTLY INVISIBLE."""
        source = (
            REPO_ROOT / "daemon" / "tools" / "instance.py"
        ).read_text(encoding="utf-8")
        assert "from .instance_read import create_instance_read_tools" in source
        assert "from .instance_ops import create_instance_ops_tools" in source
        assert "create_instance_read_tools(" in source
        assert "create_instance_ops_tools(" in source
        assert "tools.extend(" in source

    def test_the_extends_precede_the_filter_and_the_doc_scan(self) -> None:
        """The extends MUST precede ``create_help_tool`` /
        ``scan_tools_for_full_docs`` / ``_apply_tool_filter`` — a later
        extend either bypasses the privileged strip or is invisible to
        the help tool."""
        source = (
            REPO_ROOT / "daemon" / "tools" / "instance.py"
        ).read_text(encoding="utf-8")
        read_idx = source.index("create_instance_read_tools(")
        act_idx = source.index("create_instance_ops_tools(")
        help_idx = source.index("create_help_tool(tools")
        scan_idx = source.index("scan_tools_for_full_docs(tools)")
        filter_idx = source.index("_apply_tool_filter(tools, agent_id")
        for name, idx in (
            ("read extend", read_idx),
            ("act extend", act_idx),
        ):
            assert idx < help_idx, f"{name} must precede create_help_tool"
            assert idx < scan_idx, f"{name} must precede scan_tools_for_full_docs"
            assert idx < filter_idx, f"{name} must precede _apply_tool_filter"


class TestPrivilegedCategory:
    def test_both_categories_are_privileged(self) -> None:
        assert READ_CATEGORY in PRIVILEGED_TOOL_CATEGORIES
        assert ACT_CATEGORY in PRIVILEGED_TOOL_CATEGORIES

    def test_the_set_is_exactly_the_fourteen_entries(self) -> None:
        assert PRIVILEGED_TOOL_CATEGORIES == EXPECTED_PRIVILEGED_SET

    def test_the_set_has_fourteen_members(self) -> None:
        assert len(PRIVILEGED_TOOL_CATEGORIES) == 14


class TestAllowLists:
    """The two categories are MAINTENANCER-ONLY (D-1 / D-2 / D-13).

    Ari does NOT hold them; the leader does NOT hold them; no other
    agent's allow list gains either.
    """

    def _allow(self, agent_dir: str) -> list:
        meta = json.loads(
            (REPO_ROOT / "agents" / agent_dir / "meta.json").read_text(
                encoding="utf-8"
            )
        )
        return meta.get("tools", {}).get("allow", [])

    def test_maintenancer_holds_both(self) -> None:
        allow = self._allow("maintenancer")
        assert READ_CATEGORY in allow
        assert ACT_CATEGORY in allow

    def test_ari_does_not_hold_either(self) -> None:
        allow = self._allow("ari")
        assert READ_CATEGORY not in allow
        assert ACT_CATEGORY not in allow

    def test_leader_does_not_hold_either(self) -> None:
        allow = self._allow("leader")
        assert READ_CATEGORY not in allow
        assert ACT_CATEGORY not in allow

    def test_no_other_agent_gained_either(self) -> None:
        """Neither category appears in ANY other agent's allow list."""
        holders = []
        for meta_path in sorted((REPO_ROOT / "agents").glob("*/meta.json")):
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            allow = meta.get("tools", {}).get("allow", [])
            if READ_CATEGORY in allow or ACT_CATEGORY in allow:
                holders.append(meta_path.parent.name)
        assert holders == ["maintenancer"], (
            f"unexpected holders of the nest-ops categories: {holders}"
        )


# ── AC-10a: the WIRING pin (source assertion, NOT a comparison) ──────────


class TestProvenanceWiring:
    """The instance_ops protected-target gate resolves to the SINGLE
    canonical helper in ``daemon/tools/provenance.py``.

    The original drift pin compared the helper to itself (vacuous).
    This pin asserts the WIRING: the module imports the canonical
    public name and contains NO local re-implementation of the reader.
    """

    def test_act_module_imports_the_canonical_helper(self) -> None:
        source = (
            REPO_ROOT / "daemon" / "tools" / "instance_ops.py"
        ).read_text(encoding="utf-8")
        assert "from .provenance import" in source, (
            "instance_ops must import the provenance helper from the "
            "neutral home daemon/tools/provenance.py"
        )
        assert "restart_provenance" in source

    def test_no_local_reimplementation(self) -> None:
        """No second reader lives in the act module — the tell-tale is
        a direct poke at the manager's window dicts, which only the
        canonical helper performs."""
        source = (
            REPO_ROOT / "daemon" / "tools" / "instance_ops.py"
        ).read_text(encoding="utf-8")
        assert 'getattr(manager, "_user_origin_windows"' not in source
        assert "manager._user_origin_windows" not in source
        assert "manager._inherited_provenance_windows" not in source

    def test_the_alias_block_still_resolves_from_upgrade_tools(self) -> None:
        """D-14 W1: EVERY consumed name still resolves from the live
        lane's module — the delegating alias block is NOT one name."""
        import daemon.tools.provenance as pv
        import daemon.tools.upgrade_tools as ut

        assert ut._restart_provenance is pv.restart_provenance
        assert ut._lookup_provenance_field is pv.lookup_provenance_field
        assert ut._capture_user_source is pv.capture_user_source
        assert ut._capture_provenance_class is pv.capture_provenance_class
        assert ut._ProvenanceVerdict is pv.ProvenanceVerdict
        assert ut._EMPTY_PROVENANCE is pv.EMPTY_PROVENANCE

    def test_t0a_slice_untouched_by_the_move(self) -> None:
        """AC-26 (author side of the named gate): the T0a source pin's
        slice — ``async def system_upgrade`` … ``system_upgrade._full_doc_``
        — contains NONE of the moved names, so a module-level alias
        block is invisible to the pin."""
        source = (
            REPO_ROOT / "daemon" / "tools" / "upgrade_tools.py"
        ).read_text(encoding="utf-8")
        si = source.index("async def system_upgrade")
        ei = source.index("system_upgrade._full_doc_", si)
        body = source[si:ei]
        assert "windows.get(current_instance_id)" in body
        for name in (
            "_restart_provenance",
            "_lookup_provenance_field",
            "_capture_user_source",
            "_capture_provenance_class",
            "_ProvenanceVerdict",
            "_EMPTY_PROVENANCE",
        ):
            assert name not in body, (
                f"the T0a slice must not contain the moved name {name}"
            )


# ── AC-10b: the VOCABULARY pins ───────────────────────────────────────────


class TestProvenanceVocabulary:
    """The verdict tokens and the gate's refusal-token mapping are
    pinned — neither lane can silently change semantics."""

    def _verdict(self, **kwargs):
        from daemon.tools.provenance import ProvenanceVerdict

        return ProvenanceVerdict(**kwargs)

    def test_verdict_token_vocabulary(self) -> None:
        import dataclasses

        fields = {f.name for f in dataclasses.fields(self._verdict(ok=True, pclass="direct"))}
        assert {"ok", "pclass", "root_source", "root_message_id", "depth",
                "stamped_at", "expires_at", "detail"} <= fields

    def test_pclass_domain_is_direct_or_inherited(self) -> None:
        source = (
            REPO_ROOT / "daemon" / "tools" / "provenance.py"
        ).read_text(encoding="utf-8")
        assert 'pclass="direct"' in source
        assert 'pclass="inherited"' in source
        assert 'pclass=""' in source  # the fail-closed empty spelling

    def test_gate_refusal_token_mapping(self) -> None:
        """``ok=False`` → no-user-provenance; ``pclass=="direct"`` on a
        protected target → direct-provenance-insufficient."""
        source = (
            REPO_ROOT / "daemon" / "tools" / "instance_ops.py"
        ).read_text(encoding="utf-8")
        assert '"no-user-provenance"' in source
        assert '"direct-provenance-insufficient"' in source
        # the mapping is not free text: the ok check precedes the pclass
        # check inside _protected_gate
        assert source.index('"no-user-provenance"') < source.index(
            '"direct-provenance-insufficient"'
        )


# ── the kill-switch surface ──────────────────────────────────────────────


class TestKillSwitchSurface:
    def test_the_env_name_and_default(self) -> None:
        import daemon.tools.instance_ops as io

        assert io.KILL_SWITCH_ENV == "ENSEMBLE_INSTANCE_OPS_ENABLED"
        # default ON when unset
        import os

        saved = os.environ.pop(io.KILL_SWITCH_ENV, None)
        try:
            assert io._resolve_instance_ops_enabled() is True
        finally:
            if saved is not None:
                os.environ[io.KILL_SWITCH_ENV] = saved

    def test_off_spellings_disable(self) -> None:
        import os

        import daemon.tools.instance_ops as io

        for raw in ("0", "false", "no", "off"):
            os.environ[io.KILL_SWITCH_ENV] = raw
            try:
                assert io._resolve_instance_ops_enabled() is False, raw
            finally:
                os.environ.pop(io.KILL_SWITCH_ENV, None)

    def test_the_read_module_has_no_kill_switch(self) -> None:
        """The reads STAY AVAILABLE when the acts are disabled — the
        read module carries no switch of its own."""
        source = (
            REPO_ROOT / "daemon" / "tools" / "instance_read.py"
        ).read_text(encoding="utf-8")
        assert "KILL_SWITCH_ENV" not in source
