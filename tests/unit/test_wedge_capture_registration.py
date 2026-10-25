"""Registration + wiring proof for the ``wedge-capture`` privileged category.

The wedge-capture commission (2026-10-22) adds TWO read-only tools
(``wedge_capture_list`` + ``wedge_capture_get``) in their own
PRIVILEGED category, allow-listed to ari + leader + maintenancer.

This pack pins:

* (a) ``CATEGORY_MODULES`` carries the entry and it is a
  ``daemon.tools.*`` path (a ``daemon.services.*`` value is NEVER
  discovered by the AST scanner and the exact-equality drift pin
  fails at boot);
* (b) the category is in ``PRIVILEGED_TOOL_CATEGORIES``;
* (c) the frozenset LENGTH is 16 (ari-voice joined 2026-10-25);
* (d) both tool names are in ``DYNAMIC_TOOL_NAMES`` and
  ``KNOWN_TOOL_NAMES``;
* (e) the AST scanner finds both ``@tool`` functions (decorator-only
  registration is SILENTLY INVISIBLE);
* (f) the construction extend in ``daemon/tools/instance.py`` PRECEDES
  ``create_help_tool`` / ``scan_tools_for_full_docs`` /
  ``_apply_tool_filter`` (a later extend is invisible to the help tool
  or BYPASSES the privileged strip);
* (g) **A13's attribute pin** — ``getattr(tool, "_tool_category",
  None) == "wedge-capture"`` on BOTH constructed tool objects. Without
  it, the name-prefix fallback (``daemon/tools/_tool_registry.py``)
  infers ``"wedge"`` and the strip path FAILS OPEN if the decorator
  is missing or mis-ordered below ``@tool``;
* (h) **A13's default-deny BEHAVIORAL leg** — an empty-allow
  synthetic agent resolves ZERO wedge tools;
* (i) **A13's holders offenders-scan** — pinned to exactly
  ``{ari, leader, maintenancer}`` (the
  ``test_instance_ops_registration.py`` pattern);
* (j) the two-layer authorization posture — the shared module-level
  helper is fail-closed on a blank / non-``str`` agent id.

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

CATEGORY = "wedge-capture"
MODULE = "daemon.tools.wedge_capture"

TOOL_NAMES: frozenset[str] = frozenset({
    "wedge_capture_list",
    "wedge_capture_get",
})

#: The agents allowed to read the capture store (the allow-list is the
#: SAME set as the tool bodies' runtime gate).
EXPECTED_HOLDERS: list[str] = ["ari", "leader", "maintenancer"]

#: The privileged universe — 14 pre-existing entries (base's 15 minus
#: the ``cu`` category REMOVED 2026-10-24 with the cu builtin MCP
#: server) plus the wedge-capture category = 15 members.
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
    "board-link",
    "wedge-capture",
    "instance_read",
    "instance_ops",
    "mcp_rpc",
# ari-voice (2026-10-25): the ``notify-user`` category — ONE
# deliberate message from a non-user-originated turn to the
# SESSION'S OWN registered chat source. opt-in-only; the ari
# allow entry is the only access path, and the tool body ALSO
# carries its own agent_id gate.
"notify-user",
})


# ── the static registration points ─────────────────────────────────────────


class TestStaticRegistration:
    def test_ast_discovery_finds_both_tools(self) -> None:
        """The AST walker finds the factory-created ``@tool`` functions.

        Decorator-only registration is SILENTLY INVISIBLE — a missing
        module or a renamed function shrinks the discovered universe
        without any error.
        """
        discovered = discover_source_only_tool_names()
        missing = TOOL_NAMES - discovered
        assert missing == set(), f"AST discovery missed: {missing}"

    def test_category_modules_entry(self) -> None:
        """``CATEGORY_MODULES`` maps the category to its module, and the
        value is a ``daemon.tools.*`` path (the AST scanner resolves a
        value as ``daemon/tools/<parts[2:]>.py``)."""
        assert CATEGORY_MODULES.get(CATEGORY) == MODULE
        value = CATEGORY_MODULES.get(CATEGORY)
        assert value.startswith("daemon.tools."), (
            f"{value} is not a daemon.tools.* path — a daemon.services.* "
            "value is never discovered by the AST scanner and the "
            "drift pin fails at boot"
        )

    def test_dynamic_tool_names_contains_both(self) -> None:
        missing = TOOL_NAMES - set(DYNAMIC_TOOL_NAMES)
        assert missing == set(), f"DYNAMIC_TOOL_NAMES missing: {missing}"

    def test_known_tool_names_contains_both(self) -> None:
        missing = TOOL_NAMES - set(KNOWN_TOOL_NAMES)
        assert missing == set(), f"KNOWN_TOOL_NAMES missing: {missing}"

    def test_construction_extend_present_in_source(self) -> None:
        """The critical list-append is greppable in
        ``daemon/tools/instance.py``."""
        source = (
            REPO_ROOT / "daemon" / "tools" / "instance.py"
        ).read_text(encoding="utf-8")
        assert "from .wedge_capture import create_wedge_capture_tools" in source
        assert "create_wedge_capture_tools(" in source
        assert "tools.extend(" in source

    def test_the_extend_precedes_the_filter_and_the_doc_scan(self) -> None:
        """The extend MUST precede ``create_help_tool`` /
        ``scan_tools_for_full_docs`` / ``_apply_tool_filter`` — a later
        extend either bypasses the privileged strip or is invisible to
        the help tool."""
        source = (
            REPO_ROOT / "daemon" / "tools" / "instance.py"
        ).read_text(encoding="utf-8")
        wedge_idx = source.index("create_wedge_capture_tools(")
        help_idx = source.index("create_help_tool(tools")
        scan_idx = source.index("scan_tools_for_full_docs(tools)")
        filter_idx = source.index("_apply_tool_filter(tools, agent_id")
        assert wedge_idx < help_idx, "the extend must precede create_help_tool"
        assert wedge_idx < scan_idx, (
            "the extend must precede scan_tools_for_full_docs"
        )
        assert wedge_idx < filter_idx, (
            "the extend must precede _apply_tool_filter"
        )


# ── the privileged category ────────────────────────────────────────────────


class TestPrivilegedCategory:
    def test_the_category_is_privileged(self) -> None:
        assert CATEGORY in PRIVILEGED_TOOL_CATEGORIES

    def test_the_set_is_exactly_the_sixteen_entries(self) -> None:
        assert PRIVILEGED_TOOL_CATEGORIES == EXPECTED_PRIVILEGED_SET

    def test_the_set_has_sixteen_members(self) -> None:
        assert len(PRIVILEGED_TOOL_CATEGORIES) == 16


# ── A13: the attribute pin + the default-deny behavioral leg ───────────────


class TestAttributePin:
    """A13 — the ``_tool_category`` attribute pin.

    Without it, the name-prefix fallback infers ``"wedge"`` from the
    tool names and the privileged strip FAILS OPEN.
    """

    def test_both_tools_carry_the_category_attribute(self) -> None:
        from daemon.tools.wedge_capture import create_wedge_capture_tools

        tools = create_wedge_capture_tools(
            None,
            current_instance_id="",
            agent_id="ari",
            agent_tag=None,
        )
        assert len(tools) == 2
        for tool in tools:
            assert getattr(tool, "_tool_category", None) == CATEGORY, (
                f"{getattr(tool, 'name', tool)} must carry "
                f"_tool_category == {CATEGORY!r} — without it the "
                "name-prefix fallback infers 'wedge' and the privileged "
                "strip FAILS OPEN"
            )

    def test_the_default_deny_leg_resolves_zero_wedge_tools(self) -> None:
        """An empty-allow synthetic agent resolves ZERO wedge tools.

        The privileged category must never join the empty-allow
        default-grant universe.
        """
        from daemon.tools.instance import _apply_tool_filter

        class _FakeTool:
            def __init__(self, name: str) -> None:
                self.name = name
                self._tool_category = CATEGORY

        tools = [_FakeTool(name) for name in sorted(TOOL_NAMES)]
        filtered = _apply_tool_filter(tools, "synthetic-empty-allow-agent")
        assert filtered == [], (
            "an empty-allow agent must resolve ZERO wedge-capture tools"
        )


# ── the two-layer authorization posture ────────────────────────────────────


class TestAuthorizationHelper:
    def test_the_helper_is_fail_closed_on_a_blank_agent(self) -> None:
        from daemon.tools.wedge_capture import _is_wedge_capture_authorized

        assert _is_wedge_capture_authorized("") is False
        assert _is_wedge_capture_authorized(None) is False
        assert _is_wedge_capture_authorized(123) is False

    def test_the_helper_admits_exactly_the_three_holders(self) -> None:
        from daemon.tools.wedge_capture import (
            WEDGE_CAPTURE_AUTHORIZED_AGENTS,
            _is_wedge_capture_authorized,
        )

        assert WEDGE_CAPTURE_AUTHORIZED_AGENTS == frozenset(
            {"ari", "leader", "maintenancer"}
        )
        for agent_id in ("ari", "leader", "maintenancer"):
            assert _is_wedge_capture_authorized(agent_id) is True
        for agent_id in ("coder", "tester", "designer", "architect", ""):
            assert _is_wedge_capture_authorized(agent_id) is False


# ── the allow-lists ────────────────────────────────────────────────────────


class TestAllowLists:
    def _allow(self, agent_dir: str) -> list:
        meta = json.loads(
            (REPO_ROOT / "agents" / agent_dir / "meta.json").read_text(
                encoding="utf-8"
            )
        )
        return meta.get("tools", {}).get("allow", [])

    def test_the_three_holders_hold_the_category(self) -> None:
        for agent_id in EXPECTED_HOLDERS:
            allow = self._allow(agent_id)
            assert CATEGORY in allow, f"{agent_id} must hold {CATEGORY}"

    def test_the_holders_scan_is_exactly_the_three(self) -> None:
        """A13's holders offenders-scan — the category appears in NO
        other agent's allow list."""
        holders = []
        for meta_path in sorted((REPO_ROOT / "agents").glob("*/meta.json")):
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            allow = meta.get("tools", {}).get("allow", [])
            if CATEGORY in allow:
                holders.append(meta_path.parent.name)
        assert holders == EXPECTED_HOLDERS, (
            f"unexpected holders of the {CATEGORY} category: {holders}"
        )


# ── the read-only posture ──────────────────────────────────────────────────


class TestReadOnlyPosture:
    def test_the_module_exposes_no_write_tools(self) -> None:
        """The manual capture trigger is deliberately NOT an agent tool —
        an agent must not be able to force captures."""
        from daemon.tools.wedge_capture import (
            TOOL_NAMES as module_tool_names,
        )

        assert "wedge_capture_capture" not in module_tool_names
        assert "wedge_capture_trigger" not in module_tool_names
        assert module_tool_names == TOOL_NAMES

    def test_the_fetch_enumeration_excludes_the_sidecar(self) -> None:
        """MR-5 — the fetch payload carries EXACTLY the D8 file set; the
        ``.pending_row.json`` reconcile sidecar is NOT capture evidence
        and must never leak into it."""
        from daemon.tools.wedge_capture import ARTIFACT_FILE_NAMES

        assert ".pending_row.json" not in ARTIFACT_FILE_NAMES
        assert "manifest.json" in ARTIFACT_FILE_NAMES
        assert len(ARTIFACT_FILE_NAMES) == 8
