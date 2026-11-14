"""Registration + allow-list proof for the ``prompt-reload`` category
(prompt-reload lane, 2026-11-06 — the phase-2 ceremony pack).

The three-step registration seam (decorator + registry entries +
construction) in the notify-user/memoir shape:

* (a) the AST discovery finds the tool in the category module;
* (b) the category is in ``PRIVILEGED_TOOL_CATEGORIES`` (#20);
* (c) the frozenset LENGTH is 20;
* (d) the set is EXACTLY the twenty entries (derived from the
  registry's own literal at test time);
* (e) ``CATEGORY_MODULES["prompt-reload"]`` resolves to a
  ``daemon.tools.*`` path;
* (f) ``DYNAMIC_TOOL_NAMES`` + ``KNOWN_TOOL_NAMES`` carry the tool
  name (factory-built);
* (g) the ``create_instance_tools`` extend is greppable in source AND
  precedes ``create_help_tool`` / ``scan_tools_for_full_docs`` /
  ``_apply_tool_filter``;
* (h) the ari-ONLY census: ``prompt-reload`` appears in
  ``agents/ari/meta.json`` and NOWHERE ELSE, as the LAST entry
  (trailing-entry discipline);
* (i) the in-body runtime gate exists in source (layer 3);
* (j) the decorator order is load-bearing: ``@register_tool_category``
  ABOVE ``@tool``;
* (k) SWITCH-STATE-INDEPENDENT registration (AM-11): the tool is
  constructed identically with the kill-switch ON and OFF; only the
  per-call refusal differs.
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
)

REPO_ROOT = Path(__file__).resolve().parents[3]

CATEGORY = "prompt-reload"
MODULE = "daemon.tools.prompt_reload"
TOOL_NAME = "prompt_reload"
AUTHORIZED_AGENT = "ari"


def _registry_literal_members() -> frozenset[str]:
    source = (REPO_ROOT / "daemon" / "tools" / "_tool_registry.py").read_text(
        encoding="utf-8"
    )
    block = source.split(
        "PRIVILEGED_TOOL_CATEGORIES: frozenset[str] = frozenset({", 1
    )[1]
    literal = block.split("\n})", 1)[0]
    return frozenset(
        line.strip().strip(",").strip('"')
        for line in literal.splitlines()
        if line.strip().startswith('"')
    )


class TestTheCategory:
    def test_the_category_is_privileged(self) -> None:
        assert CATEGORY in PRIVILEGED_TOOL_CATEGORIES

    def test_the_set_has_twenty_two_members(self) -> None:
        assert len(PRIVILEGED_TOOL_CATEGORIES) == 22

    def test_the_derived_set_is_exactly_the_twenty_two_entries(self) -> None:
        derived = _registry_literal_members()
        assert len(derived) == 22, sorted(derived)
        assert CATEGORY in derived
        assert PRIVILEGED_TOOL_CATEGORIES == derived

    def test_the_prompt_reload_entry_is_second_to_last_in_the_literal(self) -> None:
        source = (REPO_ROOT / "daemon" / "tools" / "_tool_registry.py").read_text(
            encoding="utf-8"
        )
        block = source.split(
            "PRIVILEGED_TOOL_CATEGORIES: frozenset[str] = frozenset({", 1
        )[1]
        literal = block.split("\n})", 1)[0]
        entries = [
            line.strip().strip(",").strip('"')
            for line in literal.splitlines()
            if line.strip().startswith('"')
        ]
        # prompt-assent (2026-11-10) appended after prompt-reload —
        # prompt-reload is now second-to-last (the tail discipline is
        # a RELATIVE one: a later lane may append after it).
        # guidelines (2026-11-13) appended after prompt-assent —
        # prompt-reload is now third-to-last; the relative discipline
        # is unchanged.
        assert entries[-3] == CATEGORY, entries[-4:]
        assert entries[-2] == "prompt-assent", entries[-4:]
        assert entries[-1] == "guidelines", entries[-4:]

    def test_the_category_module_resolves_to_a_daemon_tools_path(self) -> None:
        value = CATEGORY_MODULES.get(CATEGORY)
        assert value == MODULE
        # The AST scanner resolves the value as daemon/tools/<parts[2:]>.py.
        parts = value.split(".")
        assert (REPO_ROOT / "daemon" / "tools" / f"{parts[-1]}.py").is_file()

    def test_the_tool_names_are_registered(self) -> None:
        assert TOOL_NAME in DYNAMIC_TOOL_NAMES
        assert TOOL_NAME in KNOWN_TOOL_NAMES


class TestTheToolModule:
    def test_the_decorator_order_is_category_above_tool(self) -> None:
        """``@register_tool_category`` ABOVE ``@tool`` — the AST scanner
        requires the category decorator to wrap the tool decorator."""
        source = (REPO_ROOT / "daemon" / "tools" / "prompt_reload.py").read_text(
            encoding="utf-8"
        )
        tree = ast.parse(source)
        found = False
        for node in ast.walk(tree):
            if not isinstance(node, ast.AsyncFunctionDef) or node.name != TOOL_NAME:
                continue
            names = [
                d.func.id if isinstance(d, ast.Call) and isinstance(d.func, ast.Name) else None
                for d in node.decorator_list
            ]
            assert names[0] == "register_tool_category", names
            assert names[1] == "tool", names
            found = True
        assert found, "the prompt_reload tool function was not found"

    def test_the_in_body_agent_gate_exists(self) -> None:
        source = (REPO_ROOT / "daemon" / "tools" / "prompt_reload.py").read_text(
            encoding="utf-8"
        )
        assert 'caller_agent_id != PROMPT_RELOAD_AUTHORIZED_AGENT' in source
        assert 'PROMPT_RELOAD_AUTHORIZED_AGENT = "ari"' in source

    def test_the_kill_switch_is_per_call_and_the_knob_is_one_constant(self) -> None:
        source = (REPO_ROOT / "daemon" / "tools" / "prompt_reload.py").read_text(
            encoding="utf-8"
        )
        assert 'KILL_SWITCH_ENV = "ENSEMBLE_PROMPT_RELOAD_ENABLED"' in source
        assert "_resolve_enabled()" in source  # read per call, in body
        # …and the resolver is the instance_ops-weight off-spelling set.
        assert '"0", "false", "no", "off", "disabled"' in source


class TestTheConstructionSeam:
    def test_the_extend_precedes_help_and_filter(self) -> None:
        source = (REPO_ROOT / "daemon" / "tools" / "instance.py").read_text(
            encoding="utf-8"
        )
        extend = source.find("create_prompt_reload_tools(")
        assert extend != -1, "the construction extend is missing"
        for later in ("create_help_tool(", "scan_tools_for_full_docs(", "_apply_tool_filter("):
            later_idx = source.find(later, extend)
            assert later_idx > extend, f"{later} must come AFTER the extend"

    def test_tool_constructed_switch_state_independent(self) -> None:
        """AM-11 — registration identical under kill-switch ON and OFF;
        only the per-call refusal differs (proven in the refusal pack)."""
        import os

        from daemon.tools.prompt_reload import create_prompt_reload_tools

        results = {}
        for state in ("1", "false"):
            os.environ["ENSEMBLE_PROMPT_RELOAD_ENABLED"] = state
            try:
                tools = create_prompt_reload_tools(
                    object(),
                    current_instance_id="iid-pin",
                    agent_id=AUTHORIZED_AGENT,
                )
                results[state] = (len(tools), tools[0].name)
            finally:
                os.environ.pop("ENSEMBLE_PROMPT_RELOAD_ENABLED", None)
        assert results["1"] == results["false"] == (1, TOOL_NAME)


class TestTheAriOnlyAllowList:
    def test_ari_meta_carries_the_category_second_to_last(self) -> None:
        meta = json.loads(
            (REPO_ROOT / "agents" / "ari" / "meta.json").read_text(encoding="utf-8")
        )
        allow = meta.get("tools", {}).get("allow", []) or []
        assert CATEGORY in allow
        # "pok" re-point 2026-11-08 (POK B4 sweep 16b4e70b2): prompt-reload
        # was the LAST entry (the sweep inserts BEFORE nest-report,
        # mid-array — the trailing discipline is untouched); the array
        # length grew 36 -> 37.
        # prompt-assent (2026-11-10) appended AFTER it — prompt-reload is
        # now second-to-last; the array length grew 37 -> 38.
        # guidelines (2026-11-13, guideline-read-tool lane) appended AFTER
        # prompt-assent — prompt-reload is now third-to-last; the array
        # length grew 38 -> 39.
        # ari nest-ops fleet grant (2026-11-14) appended
        # "instance_read", "instance_ops" AFTER guidelines — prompt-reload
        # is now fifth-to-last; the array length grew 39 -> 41.
        assert allow[-5] == CATEGORY, (
            "the prompt-reload entry must sit IMMEDIATELY BEFORE the "
            "prompt-assent entry in ari's allow array (the trailing-entry "
            "discipline — a later lane may append after it)"
        )
        assert allow[-4] == "prompt-assent"
        assert allow[-3] == "guidelines"
        assert allow[-2] == "instance_read"
        assert allow[-1] == "instance_ops"
        assert len(allow) == 41

    def test_no_other_agent_carries_the_category(self) -> None:
        offenders: list[str] = []
        for meta_path in sorted((REPO_ROOT / "agents").glob("*/meta.json")):
            if meta_path.parent.name == AUTHORIZED_AGENT:
                continue
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            allow = meta.get("tools", {}).get("allow", []) or []
            if CATEGORY in allow:
                offenders.append(str(meta_path.relative_to(REPO_ROOT)))
        assert offenders == [], (
            f"the prompt-reload category is ARI-ONLY; found in: {offenders}"
        )
