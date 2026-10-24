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

SIZE: ≈535 lines — registration + wiring + vocabulary + the DF-4
gate-predicate source pins; split the source-pin classes out when a
third lane lands.

SAFETY FENCE: no HOME-derived or live-shaped path is ever resolved by
this file — every read is a repo-relative source read.
"""

from __future__ import annotations

import ast
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
#: entries plus the two nest-ops categories (12 -> 14, D-13). The ``cu``
#: category was REMOVED 2026-10-24 together with the cu builtin MCP
#: server, taking the base from 12 to 11 (16 -> 15 members).
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
    # wedge-capture (2026-10-22): the READ-ONLY capture-evidence
    # surface — ``wedge_capture_list`` + ``wedge_capture_get``.
    # PRIVILEGED (entry #12): the capture payload is a privileged
    # diagnostic artifact; the empty-allow inherit universe must
    # NOT auto-grant it. The ari + leader + maintenancer allow
    # entries are the only access path, and every tool body ALSO
    # carries its own agent_id gate.
    "wedge-capture",
    # nest-ops (2026-10-21): the two maintenancer fleet categories
    # (read #13 / act #14) — opt-in-only, never default-granted.
    "instance_read",
    "instance_ops",
# remote-hands center gateway (2026-10-22): the ``mcp_rpc``
# category — the agent-facing surface that drives an ENROLLED
# remote machine (SPEC.md v0.1). opt-in-only; the ari + leader
# allow entries are the only access path, and every tool body
# ALSO carries its own agent_id gate.
"mcp_rpc",
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

    def test_the_set_is_exactly_the_fifteen_entries(self) -> None:
        assert PRIVILEGED_TOOL_CATEGORIES == EXPECTED_PRIVILEGED_SET

    def test_the_set_has_fifteen_members(self) -> None:
        assert len(PRIVILEGED_TOOL_CATEGORIES) == 15


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


class TestNoGateKeysOnPclass:
    """DF-4 pin (iv) — NEITHER gate predicate keys on the window's CLASS.

    ``pclass`` is a READ-TIME derivation of the live window — never
    stored in the window store (the ledger snapshots it as
    ``provenance_class``), never re-verified. Keying a gate on it is
    unsound by construction: it carried zero trust signal and it was
    the cause of the wrong-side refusal (the genuine operator path).
    The class survives as OBSERVABILITY in the audit rows.

    This pin REPLACES the vacuous fixture-parity pin (it compared the
    helper to itself). It is a SOURCE-level pin, analysed with ``ast``
    so a docstring mention cannot false-positive and a comment cannot
    mask a real keying.

    SCOPE (the house anti-false-positive rule): the restart-lane scan
    covers the GATE PREDICATE BLOCK ONLY — the ``verdicts.append(...)``
    tuple that carries the provenance verdict. A function-wide scan
    false-positives: five lines above the gate the lane legitimately
    constructs ``ok=False`` for the non-live case.
    """

    def _instance_ops_gate_pclass_reads(self) -> list[int]:
        source = (
            REPO_ROOT / "daemon" / "tools" / "instance_ops.py"
        ).read_text(encoding="utf-8")
        tree = ast.parse(source)
        gate = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "_protected_gate":
                gate = node
                break
        assert gate is not None, "the protected-target gate must exist"
        return [
            sub.lineno
            for sub in ast.walk(gate)
            if isinstance(sub, ast.Attribute) and sub.attr == "pclass"
        ]

    @staticmethod
    def _restart_gate_pclass_reads() -> list[int]:
        """The restart-lane gate tuple — located STRUCTURALLY.

        The previous locator matched the PROSE LITERAL
        ``"no-user-provenance"``: a mere token rename (or a reworded
        refusal string) would have made the pin report "block not found"
        instead of the DEFECT it exists to catch. The gate is now found
        by its SHAPE — the ``verdicts.append((...))`` tuple that carries
        the LOCAL ``provenance`` verdict object (``provenance.ok`` /
        ``provenance.detail``). The sibling rails append plain names
        (``not cooldown_active`` / ``uptime_ok`` / ``budget_ok``), so
        exactly one tuple references ``provenance``.

        The scan stays INSIDE that appended tuple: five lines above it
        the lane legitimately constructs ``ok=False`` for the non-live
        case, and a function-wide scan would false-positive on it.
        """
        source = (
            REPO_ROOT / "daemon" / "tools" / "upgrade_tools.py"
        ).read_text(encoding="utf-8")
        tree = ast.parse(source)
        block = None
        for node in ast.walk(tree):
            if not isinstance(node, ast.AsyncFunctionDef):
                continue
            if node.name != "system_restart":
                continue
            for sub in ast.walk(node):
                if not isinstance(sub, ast.Call):
                    continue
                func = sub.func
                if not (
                    isinstance(func, ast.Attribute)
                    and func.attr == "append"
                    and isinstance(func.value, ast.Name)
                    and func.value.id == "verdicts"
                ):
                    continue
                if not sub.args or not isinstance(sub.args[0], ast.Tuple):
                    continue
                elts = sub.args[0].elts
                if len(elts) < 2:
                    continue
                # the STRUCTURAL marker — the tuple references the local
                # ``provenance`` verdict object.
                if not any(
                    isinstance(name, ast.Name) and name.id == "provenance"
                    for elt in elts
                    for name in ast.walk(elt)
                ):
                    continue
                block = sub
                break
            if block is not None:
                break
        assert block is not None, (
            "the restart-lane provenance gate tuple must be found BY ITS "
            "SHAPE (the verdicts.append tuple that references the local "
            "`provenance` verdict) — a MISS means the gate no longer keys "
            "on the provenance verdict, which is a DEFECT (a pclass-keyed "
            "or reworded gate), not a benign refactor"
        )
        return [
            sub.lineno
            for sub in ast.walk(block)
            if isinstance(sub, ast.Attribute) and sub.attr == "pclass"
        ]

    def test_no_gate_predicate_keys_on_pclass(self) -> None:
        hits = self._instance_ops_gate_pclass_reads()
        assert hits == [], (
            "the instance_ops protected gate must NOT key on pclass — "
            f"class reads found at lines {hits}"
        )
        hits = self._restart_gate_pclass_reads()
        assert hits == [], (
            "the restart-lane gate predicate must NOT key on pclass — "
            f"class reads found at lines {hits}"
        )

    def test_the_class_survives_as_observability(self) -> None:
        """The class must STILL be recorded — the pin is about the GATE,
        not about deleting the field. Every audit write site keeps
        carrying ``provenance_class``."""
        source = (
            REPO_ROOT / "daemon" / "tools" / "instance_ops.py"
        ).read_text(encoding="utf-8")
        assert "provenance_class=verdict.pclass or None" in source
        assert '"provenance_class": verdict.pclass or None' in source

    def test_the_instance_ops_gate_still_keys_on_ok(self) -> None:
        """The gate keys on LIVENESS (``verdict.ok``) — the pin must not
        be satisfiable by deleting the check."""
        source = (
            REPO_ROOT / "daemon" / "tools" / "instance_ops.py"
        ).read_text(encoding="utf-8")
        assert "if not verdict.ok:" in source


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
        """EXACTLY ONE provenance refusal token remains.

        ``ok=False`` → ``no-user-provenance``. The class-keyed token is
        RETIRED (DF-4 / P2): the gate keys on the LIVENESS of the
        window, not on its class, so there is no second provenance
        refusal to map.

        The retired token is reconstructed from its two halves rather
        than spelled out: DF-4's acceptance rule forbids the literal
        anywhere under ``daemon/`` OR ``tests/``, so the pin must not
        introduce it either — and the scan below covers BOTH trees.
        """
        retired = "direct-provenance" + "-insufficient"
        source = (
            REPO_ROOT / "daemon" / "tools" / "instance_ops.py"
        ).read_text(encoding="utf-8")
        assert '"no-user-provenance"' in source
        # the retired token is absent from the source — and from the
        # whole tree (the token is gone, not merely un-referenced).
        assert retired not in source
        offenders = []
        for scope in ("daemon", "tests"):
            for path in (REPO_ROOT / scope).rglob("*.py"):
                if retired in path.read_text(encoding="utf-8"):
                    offenders.append(str(path.relative_to(REPO_ROOT)))
        assert not offenders, (
            "the retired provenance token must not survive anywhere in "
            f"daemon/ or tests/: {offenders}"
        )
        # the surviving token is the SOLE provenance refusal inside the
        # gate block. The gate mints it from the CONSTANT (the tidier
        # fold replaced the regex-recovered token with a structurally
        # returned one), so the block never re-spells the literal, and
        # EXACTLY ONE refusal branch carries the provenance token.
        gate = source[
            source.index("def _protected_gate(") : source.index(
                "async def _resolve_target("
            )
        ]
        assert '"no-user-provenance"' not in gate
        tree = ast.parse(source)
        gate_fn = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "_protected_gate"
        )
        refusal_calls = sorted(
            (
                call
                for call in ast.walk(gate_fn)
                if isinstance(call, ast.Call)
                and isinstance(call.func, ast.Name)
                and call.func.id == "_refusal"
            ),
            key=lambda call: call.lineno,
        )
        # THREE refusal branches — one per gate condition — and each one
        # mints its token from a DISTINCT constant. A fresh literal in
        # any branch is RED.
        assert len(refusal_calls) == 3
        minted = [
            arg.id
            for call in refusal_calls
            for arg in call.args
            if isinstance(arg, ast.Name) and arg.id.startswith("GATE_TOKEN_")
        ]
        assert minted == [
            "GATE_TOKEN_CONFIRMATION_REQUIRED",
            "GATE_TOKEN_CONFIRMATION_MISMATCH",
            "GATE_TOKEN_NO_USER_PROVENANCE",
        ]

    def test_the_preview_payload_derives_the_token_from_the_gate(self) -> None:
        """``would_refuse_with`` is DERIVED from the gate's own token.

        The pre-fix shape hardcoded the provenance token in the preview
        payload. On the REACHABLE path the two forms coincide (only the
        provenance refusal ever reaches the preview), so this is a
        STRUCTURAL pin: the payload's value expression must read the
        gate's ``gate_token`` and must NOT re-spell a constant.

        Why it matters even though the values coincide today: a
        hardcoded fallback is a SILENT-mislabel seam — the moment the
        gate grows a second non-confirmation condition, the hardcoded
        form reports the wrong token with no test going red.
        """
        source = (
            REPO_ROOT / "daemon" / "tools" / "instance_ops.py"
        ).read_text(encoding="utf-8")
        tree = ast.parse(source)
        payload_dicts = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Dict)
            and any(
                isinstance(key, ast.Constant) and key.value == "would_refuse_with"
                for key in node.keys
            )
        ]
        assert len(payload_dicts) == 1, (
            "the preview payload must carry exactly one "
            f"'would_refuse_with' key (found {len(payload_dicts)})"
        )
        payload_dict = payload_dicts[0]
        index = next(
            i
            for i, key in enumerate(payload_dict.keys)
            if isinstance(key, ast.Constant) and key.value == "would_refuse_with"
        )
        value_src = ast.unparse(payload_dict.values[index])
        assert "gate_token" in value_src, (
            "the preview's would_refuse_with must be derived from the "
            f"gate's token, got: {value_src!r}"
        )
        assert "GATE_TOKEN_NO_USER_PROVENANCE" not in value_src, (
            "the preview's would_refuse_with must not re-spell a "
            f"hardcoded token, got: {value_src!r}"
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
