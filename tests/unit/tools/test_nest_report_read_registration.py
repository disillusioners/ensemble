"""The ``nest-report-read`` registration pack (the nest-anomaly lane).

WHAT IT PROVES
--------------
* the category is PRIVILEGED (entry #18 of
  ``PRIVILEGED_TOOL_CATEGORIES``);
* the AST discovery finds BOTH tools in the category module;
* ``CATEGORY_MODULES`` / ``DYNAMIC_TOOL_NAMES`` / ``KNOWN_TOOL_NAMES``
  carry them (the three-step registration seam: decorator + registry
  entries + construction);
* the ``instance.py`` extend PRECEDES the privileged strip and the
  doc scan (a later extend is invisible to the help tool or BYPASSES
  the strip);
* the decorator order is register-then-tool (reversed, the privileged
  strip FAILS OPEN);
* the default-deny leg resolves ZERO of these tools for an empty-allow
  agent;
* the body gate and the category gate admit the SAME set (the R8 ops
  trio) — and the three ``meta.json`` allow arrays carry the category;
* the cold-boot wiring (loader warm import + stub + flatten) is present.

THE PIN SITE IS PART OF THIS PACK
---------------------------------
``PRIVILEGED_TOOL_CATEGORIES`` grew 17 → 18 with this category; the
SAME-PR rule demands that EVERY pin site moves in the SAME commit —
this pack's exact-set + count legs are two of them (the other seventeen
live in the sibling registration packs).
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

from daemon.tools._tool_registry import (
    CATEGORY_MODULES,
    DYNAMIC_TOOL_NAMES,
    KNOWN_TOOL_NAMES,
    PRIVILEGED_TOOL_CATEGORIES,
    discover_source_only_tool_names,
)
from daemon.tools.nest_report_read import (
    NEST_REPORT_READ_AUTHORIZED_AGENTS,
    NEST_REPORT_READ_CATEGORY_KEY,
    TOOL_NAMES,
)

REPO_ROOT = Path(__file__).resolve().parents[3]

CATEGORY = NEST_REPORT_READ_CATEGORY_KEY
MODULE = "daemon.tools.nest_report_read"

#: The three agents the R8 ruling grants the read surface to.
EXPECTED_HOLDERS = frozenset({"maintenancer", "devops", "wanderer"})


def _module_source() -> str:
    return (REPO_ROOT / "daemon" / "tools" / "nest_report_read.py").read_text(
        encoding="utf-8"
    )


def _instance_source() -> str:
    return (REPO_ROOT / "daemon" / "tools" / "instance.py").read_text(
        encoding="utf-8"
    )


def _loader_source() -> str:
    return (REPO_ROOT / "daemon" / "loader.py").read_text(encoding="utf-8")


def _agent_meta(agent: str) -> dict[str, Any]:
    return json.loads(
        (REPO_ROOT / "agents" / agent / "meta.json").read_text(encoding="utf-8")
    )


class TestStaticRegistration:
    def test_ast_discovery_finds_the_two_tools(self) -> None:
        tree = ast.parse(_module_source())
        found: set[str] = set()
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                target = decorator.func if isinstance(decorator, ast.Call) else decorator
                if isinstance(target, ast.Name) and target.id == "tool":
                    found.add(node.name)
        assert set(TOOL_NAMES) <= found, sorted(set(TOOL_NAMES) - found)

    def test_category_modules_entry(self) -> None:
        assert CATEGORY_MODULES[CATEGORY] == MODULE
        # The value MUST be a ``daemon.tools.*`` path — the AST scanner
        # resolves the value as ``daemon/tools/<parts[2:]>.py``; a
        # ``daemon.services.*`` value is NEVER discovered.
        assert CATEGORY_MODULES[CATEGORY].startswith("daemon.tools.")

    def test_dynamic_tool_names_contains_the_two(self) -> None:
        for name in TOOL_NAMES:
            assert name in DYNAMIC_TOOL_NAMES, name

    def test_known_tool_names_contains_the_two(self) -> None:
        for name in TOOL_NAMES:
            assert name in KNOWN_TOOL_NAMES, name

    def test_the_frozen_name_universe_has_no_drift(self) -> None:
        discovered = discover_source_only_tool_names()
        for name in TOOL_NAMES:
            assert name in discovered, name
        assert not (discovered - set(KNOWN_TOOL_NAMES)), (
            "the frozen KNOWN_TOOL_NAMES universe drifted from the source "
            f"discovery: {sorted(discovered - set(KNOWN_TOOL_NAMES))}"
        )

    def test_the_decorator_order_is_register_then_tool(self) -> None:
        """``@register_tool_category`` MUST sit ABOVE ``@tool``.

        Reversed, the privileged strip FAILS OPEN (the category
        attribute is set on a different object than the tool).
        """
        tree = ast.parse(_module_source())
        found: dict[str, list[str]] = {}
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if node.name not in TOOL_NAMES:
                continue
            found[node.name] = [
                (
                    d.func.id
                    if isinstance(d, ast.Call) and isinstance(d.func, ast.Name)
                    else "?"
                )
                for d in node.decorator_list
            ]
        assert set(found) == set(TOOL_NAMES), sorted(set(TOOL_NAMES) - set(found))
        for name, decorators in found.items():
            assert decorators == ["register_tool_category", "tool"], (
                f"{name} carries the decorators {decorators} — the order must "
                "be ('register_tool_category', 'tool')"
            )

    def test_the_construction_extend_is_present(self) -> None:
        source = _instance_source()
        assert "create_nest_report_read_tools" in source
        assert "tools.extend(" in source

    def test_the_extend_precedes_the_filter_and_the_doc_scan(self) -> None:
        """The extend MUST precede ``create_help_tool`` /
        ``scan_tools_for_full_docs`` / ``_apply_tool_filter`` — a later
        extend either bypasses the privileged strip or is invisible to
        the help tool."""
        source = _instance_source()
        extend_idx = source.index("create_nest_report_read_tools(")
        help_idx = source.index("create_help_tool(tools")
        scan_idx = source.index("scan_tools_for_full_docs(tools)")
        filter_idx = source.index("_apply_tool_filter(tools, agent_id")
        assert extend_idx < help_idx, "the extend must precede create_help_tool"
        assert extend_idx < scan_idx, (
            "the extend must precede scan_tools_for_full_docs"
        )
        assert extend_idx < filter_idx, (
            "the extend must precede _apply_tool_filter"
        )


class TestPrivilegedCategory:
    def test_the_category_is_privileged(self) -> None:
        assert CATEGORY in PRIVILEGED_TOOL_CATEGORIES

    def test_the_set_has_eighteen_members(self) -> None:
        assert len(PRIVILEGED_TOOL_CATEGORIES) == 18

    def test_the_category_is_entry_eighteen(self) -> None:
        """The category was appended AFTER ``metrics-read`` (entry #17).

        The registry literal carries a comment block between the two
        entries, so the pin is an ORDER assertion on the two entry
        lines, not an adjacency assertion.
        """
        source = (
            REPO_ROOT / "daemon" / "tools" / "_tool_registry.py"
        ).read_text(encoding="utf-8")
        metrics_at = source.index('    "metrics-read",\n')
        read_at = source.index('    "nest-report-read",\n')
        assert metrics_at < read_at, (
            "the nest-report-read entry must follow the metrics-read entry"
        )


class TestAttributePin:
    def test_the_two_tools_carry_the_category_attribute(self) -> None:
        from daemon.tools.nest_report_read import create_nest_report_read_tools

        tools = create_nest_report_read_tools(
            None,
            current_instance_id="",
            agent_id="maintenancer",
            agent_tag=None,
        )
        assert len(tools) == 2
        for tool in tools:
            assert getattr(tool, "_tool_category", None) == CATEGORY, (
                f"{getattr(tool, 'name', tool)} must carry "
                f"_tool_category == {CATEGORY!r} — without it the "
                "name-prefix fallback infers 'anomaly' and the privileged "
                "strip FAILS OPEN"
            )

    def test_the_default_deny_leg_resolves_zero_nest_report_read_tools(self) -> None:
        """An empty-allow synthetic agent resolves ZERO of these tools."""
        from daemon.tools.instance import _apply_tool_filter

        class _FakeTool:
            def __init__(self, name: str) -> None:
                self.name = name
                self._tool_category = CATEGORY

        tools = [_FakeTool(name) for name in sorted(TOOL_NAMES)]
        filtered = _apply_tool_filter(tools, "synthetic-empty-allow-agent")
        assert filtered == [], (
            "an empty-allow agent must resolve ZERO nest-report-read tools"
        )


class TestAuthorizationHelper:
    def test_the_helper_is_fail_closed_on_a_blank_agent(self) -> None:
        from daemon.tools.nest_report_read import _is_nest_report_authorized

        assert _is_nest_report_authorized("") is False
        assert _is_nest_report_authorized(None) is False
        assert _is_nest_report_authorized(123) is False

    def test_the_helper_admits_exactly_the_three_holders(self) -> None:
        from daemon.tools.nest_report_read import _is_nest_report_authorized

        assert NEST_REPORT_READ_AUTHORIZED_AGENTS == EXPECTED_HOLDERS
        for agent_id in ("maintenancer", "devops", "wanderer"):
            assert _is_nest_report_authorized(agent_id) is True
        for agent_id in ("ari", "worker", "coder", "tester", "designer", "leader", ""):
            assert _is_nest_report_authorized(agent_id) is False


class TestTheGrants:
    def test_the_three_meta_json_files_carry_the_category(self) -> None:
        for agent in ("maintenancer", "devops", "wanderer"):
            meta = _agent_meta(agent)
            allow = meta["tools"]["allow"]
            assert CATEGORY in allow, (agent, allow)

    def test_ari_does_not_carry_the_category(self) -> None:
        """``ari`` is NOT in the R8 ops trio — its allow array stays
        untouched (the ari trailing-entry pin is unaffected)."""
        meta = _agent_meta("ari")
        assert CATEGORY not in meta["tools"]["allow"]

    def test_the_grant_arrays_keep_a_clean_append_point(self) -> None:
        """Phase 5 appends ``nest-report`` AFTER this category — and it has
        LANDED: the array's LAST element is now the phase-5 write
        category, and the phase-3 read category sits immediately before it
        (the clean append point was USED, and nothing was reordered).

        The pre-ceremony form of this pin asserted that the LAST element
        was still this (read) category — true while phase 5 was ahead,
        and RED the moment the grant ceremony landed.  The intent (a
        mis-ordered array is caught mechanically) is unchanged: the order
        is now pinned END-TO-END.
        """
        for agent in ("maintenancer", "devops", "wanderer"):
            allow = _agent_meta(agent)["tools"]["allow"]
            assert allow[-2] == CATEGORY, (agent, allow[-3:])
            assert allow[-1] == "nest-report", (agent, allow[-3:])


class TestColdBootWiring:
    def test_the_warm_import_is_present(self) -> None:
        assert "from .tools.nest_report_read import create_nest_report_read_tools" in (
            _loader_source()
        )

    def test_the_metadata_scan_stub_is_present(self) -> None:
        assert "nest_report_read_tools = create_nest_report_read_tools(None)" in (
            _loader_source()
        )

    def test_the_flatten_entry_is_present(self) -> None:
        source = _loader_source()
        assert "nest_report_read_tools," in source
