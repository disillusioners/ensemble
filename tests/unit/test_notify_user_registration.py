"""Registration + allow-list proof for the ``notify-user`` category
(ari-voice, 2026-10-25 — base 7af1d2632d).

The three-step registration seam (decorator + registry entries +
construction) is proven END-TO-END here, in the instance_ops shape:

* (a) the AST discovery finds ``notify_user`` in the category module;
* (b) the category is in ``PRIVILEGED_TOOL_CATEGORIES``;
* (c) the frozenset LENGTH is 16 (ari-voice joined 2026-10-25);
* (d) the set is EXACTLY the sixteen entries (pin);
* (e) ``CATEGORY_MODULES["notify-user"]`` resolves to a
  ``daemon.tools.*`` path (the AST scanner resolves the value as
  ``daemon/tools/<parts[2:]>.py`` — a ``daemon.services.*`` value is
  never discovered and the drift pin fails at boot);
* (f) ``DYNAMIC_TOOL_NAMES`` + ``KNOWN_TOOL_NAMES`` carry
  ``notify_user`` (factory-built, not import-time registered);
* (g) the ``create_instance_tools`` extend is greppable in source AND
  precedes ``create_help_tool`` / ``scan_tools_for_full_docs`` /
  ``_apply_tool_filter`` (decorator-only registration is SILENTLY
  INVISIBLE — a late extend bypasses the privileged strip);
* (h) the ari-ONLY census: ``notify-user`` appears in
  ``agents/ari/meta.json`` and NOWHERE ELSE across every
  ``agents/*/meta.json`` (the v0.26.2 ari-only ruling posture);
* (i) the in-body runtime gate exists in source (layer 3 — a literal
  tool name in an allow list bypasses the category gate);
* (j) the decorator order is load-bearing and pinned:
  ``@register_tool_category`` ABOVE ``@tool``.

MUTATION VERIFICATION: the ari-only census and the extends-order pins
were mutation-verified (planted defect → RED → restore → GREEN) — see
the commission report / ``test/packs/notify_user_unit_test.sh``.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

from daemon.tools._tool_registry import (
    CATEGORY_MODULES,
    DYNAMIC_TOOL_NAMES,
    KNOWN_TOOL_NAMES,
    PRIVILEGED_TOOL_CATEGORIES,
    discover_source_only_tool_names,
)

CATEGORY = "notify-user"
MODULE = "daemon.tools.notify_user"
TOOL_NAMES: frozenset[str] = frozenset({"notify_user"})

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


# ── the static registration points ───────────────────────────────────────


class TestStaticRegistration:
    def test_step1_ast_discovery_finds_notify_user(self) -> None:
        """Step 1 — the AST walker finds the factory-created ``@tool``
        function at its nesting depth inside the factory."""
        discovered = discover_source_only_tool_names()
        missing = TOOL_NAMES - discovered
        assert missing == set(), f"AST discovery missed: {missing}"

    def test_step1b_the_module_ast_carries_the_decorator_order(self) -> None:
        """The decorator order is LOAD-BEARING (pinned at
        test_self_alarm_registration.py for the sibling): if
        ``@tool`` ran first, the wrapper would carry the category but
        the strip reads the WRAPPED object — the order must be
        ``@register_tool_category`` ABOVE ``@tool``."""
        source = (REPO_ROOT / "daemon" / "tools" / "notify_user.py").read_text(
            encoding="utf-8"
        )
        tree = ast.parse(source)
        orders: list[tuple[str, ...]] = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                decorators = [
                    d.func.id
                    for d in node.decorator_list
                    if isinstance(d, ast.Call)
                    and isinstance(d.func, ast.Name)
                ]
                if "register_tool_category" in decorators:
                    orders.append(tuple(decorators))
        assert orders, "no @register_tool_category-decorated tool found"
        assert ("register_tool_category", "tool") in orders, (
            f"decorator order drifted: {orders} — register_tool_category "
            "must sit ABOVE @tool"
        )

    def test_step2_category_modules_entry(self) -> None:
        """Step 2 — ``CATEGORY_MODULES['notify-user']`` points at the
        module, and it is a ``daemon.tools.*`` path."""
        assert CATEGORY_MODULES.get(CATEGORY) == MODULE
        assert MODULE.startswith("daemon.tools."), (
            f"{MODULE} is not a daemon.tools.* path — a "
            "daemon.services.* value is never discovered by the "
            "AST scanner and the drift pin fails at boot"
        )

    def test_step3_dynamic_tool_names_contains_notify_user(self) -> None:
        missing = TOOL_NAMES - set(DYNAMIC_TOOL_NAMES)
        assert missing == set(), f"DYNAMIC_TOOL_NAMES missing: {missing}"

    def test_step3b_known_tool_names_contains_notify_user(self) -> None:
        missing = TOOL_NAMES - set(KNOWN_TOOL_NAMES)
        assert missing == set(), f"KNOWN_TOOL_NAMES missing: {missing}"

    def test_step4_construction_extends_present_in_source(self) -> None:
        """Step 4 — the CRITICAL list-append is greppable in
        ``daemon/tools/instance.py``. Decorator-only registration is
        SILENTLY INVISIBLE."""
        source = (
            REPO_ROOT / "daemon" / "tools" / "instance.py"
        ).read_text(encoding="utf-8")
        assert "from .notify_user import create_notify_user_tools" in source
        assert "create_notify_user_tools_if_available(" in source
        assert "tools.extend(" in source

    def test_the_extend_precedes_the_filter_and_the_doc_scan(self) -> None:
        """The extend MUST precede ``create_help_tool`` /
        ``scan_tools_for_full_docs`` / ``_apply_tool_filter`` — a later
        extend either bypasses the privileged strip or is invisible to
        the help tool."""
        source = (
            REPO_ROOT / "daemon" / "tools" / "instance.py"
        ).read_text(encoding="utf-8")
        extend_idx = source.index("create_notify_user_tools_if_available(")
        help_idx = source.index("create_help_tool(tools")
        scan_idx = source.index("scan_tools_for_full_docs(tools)")
        filter_idx = source.index("_apply_tool_filter(tools, agent_id")
        assert extend_idx < help_idx, "extend must precede create_help_tool"
        assert extend_idx < scan_idx, (
            "extend must precede scan_tools_for_full_docs"
        )
        assert extend_idx < filter_idx, (
            "extend must precede _apply_tool_filter"
        )


class TestPrivilegedCategory:
    def test_the_category_is_privileged(self) -> None:
        assert CATEGORY in PRIVILEGED_TOOL_CATEGORIES

    def test_the_set_is_exactly_the_sixteen_entries(self) -> None:
        assert PRIVILEGED_TOOL_CATEGORIES == EXPECTED_PRIVILEGED_SET

    def test_the_set_has_sixteen_members(self) -> None:
        assert len(PRIVILEGED_TOOL_CATEGORIES) == 16


class TestAriOnlyAllowList:
    """The v0.26.2 ari-only ruling posture, census-shaped."""

    def test_ari_meta_carries_the_category_key(self) -> None:
        meta = json.loads(
            (REPO_ROOT / "agents" / "ari" / "meta.json").read_text(
                encoding="utf-8"
            )
        )
        assert CATEGORY in meta.get("tools", {}).get("allow", []), (
            "agents/ari/meta.json must carry the exact category key "
            f"{CATEGORY!r} (N7: never 'notify')"
        )
        assert "notify" not in meta.get("tools", {}).get("allow", []), (
            "the WRONG category key 'notify' must never appear (N7)"
        )

    def test_no_other_agent_carries_the_category(self) -> None:
        """The ari-ONLY census — every agents/*/meta.json EXCEPT ari
        must NOT carry ``notify-user`` (nor the wrong 'notify' key)."""
        offenders: list[str] = []
        for meta_path in sorted(
            (REPO_ROOT / "agents").glob("*/meta.json")
        ):
            if meta_path.parent.name == "ari":
                continue
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            allow = meta.get("tools", {}).get("allow", []) or []
            if CATEGORY in allow or "notify" in allow:
                offenders.append(str(meta_path.relative_to(REPO_ROOT)))
        assert offenders == [], (
            f"notify-user is ARI-ONLY; found in: {offenders}"
        )

    def test_the_tool_module_authorizes_ari_only(self) -> None:
        """Layer 3 — the in-body runtime gate constant. A literal tool
        name in an allow list bypasses the category gate, so the body
        gate is mandatory."""
        from daemon.tools.notify_user import (
            NOTIFY_AUTHORIZED_AGENT,
            NOTIFY_CATEGORY_KEY,
        )

        assert NOTIFY_AUTHORIZED_AGENT == "ari"
        assert NOTIFY_CATEGORY_KEY == CATEGORY


class TestBodyGateInSource:
    def test_the_body_gate_refuses_before_any_ledger_access(self) -> None:
        """The ari gate is the FIRST check in the impl — a non-ari
        caller never reaches the ledger (pre-audit, self_alarm
        precedent)."""
        source = (REPO_ROOT / "daemon" / "tools" / "notify_user.py").read_text(
            encoding="utf-8"
        )
        gate_idx = source.index("if agent_id != NOTIFY_AUTHORIZED_AGENT:")
        kill_idx = source.index("if not bounds[\"enabled\"]:")
        size_idx = source.index("len(message) > bounds[\"max_chars\"]")
        assert gate_idx < kill_idx < size_idx, (
            "the layer-3 ari gate must precede the kill-switch, which "
            "must precede the size gate"
        )
