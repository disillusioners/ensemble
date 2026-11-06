"""Registration + wiring proof for the ``metrics-read`` privileged category.

The metrics-agent-tools commission (2026-11-03) adds THREE read-only tools
(``metrics_families`` + ``metrics_snapshot`` + ``metrics_history``) in
their own PRIVILEGED category, allow-listed to maintenancer + devops +
wanderer.

This pack pins:

* (a) ``CATEGORY_MODULES`` carries the entry and it is a
  ``daemon.tools.*`` path (a ``daemon.services.*`` value is NEVER
  discovered by the AST scanner and the exact-equality drift pin fails at
  boot);
* (b) the category is in ``PRIVILEGED_TOOL_CATEGORIES``;
* (c) the frozenset LENGTH is 18 (nest-report-read joined 2026-11-05);
* (d) the set is EXACTLY the nineteen entries;
* (e) both tool-name universes (``DYNAMIC_TOOL_NAMES`` +
  ``KNOWN_TOOL_NAMES``) carry all three names;
* (f) the AST scanner finds all three ``@tool`` functions (decorator-only
  registration is SILENTLY INVISIBLE);
* (g) the construction extend in ``daemon/tools/instance.py`` PRECEDES
  ``create_help_tool`` / ``scan_tools_for_full_docs`` /
  ``_apply_tool_filter`` (a later extend is invisible to the help tool or
  BYPASSES the privileged strip);
* (h) the DECORATOR ORDER is ``register_tool_category`` ABOVE ``tool`` —
  reversed, the privileged strip FAILS OPEN;
* (i) **A13's attribute pin** — ``getattr(tool, "_tool_category", None)
  == "metrics-read"`` on ALL THREE constructed tool objects. Without it,
  the name-prefix fallback infers ``"metrics"`` and the strip path FAILS
  OPEN if the decorator is missing or mis-ordered below ``@tool``;
* (j) **A13's default-deny BEHAVIORAL leg** — an empty-allow synthetic
  agent resolves ZERO metrics tools;
* (k) **A13's holders offenders-scan** — pinned to exactly
  ``{maintenancer, devops, wanderer}``;
* (l) the two-layer authorization posture — the shared module-level
  helper is fail-closed on a blank / non-``str`` agent id;
* (m) the GATE-ORDERING pin — the body gate precedes the substrate
  check, which precedes any provider read (D-SCOPE-4);
* (n) the LITERAL-NAME probe (D-SCOPE-2) — a literal tool name in an
  allow list resolves the tool, and the BODY GATE still refuses a
  non-holder (the allow list never reaches the body gate);
* (o) the AmA-3 EQUALITY leg — the category-gate spelling and the
  body-gate spelling are the SAME set;
* (p) the AmA-2 THREADED-``agent_id`` leg — the factory threads
  ``agent_id=agent_id`` and the THREADED VALUE reaches the body gate;
* (q) the AmRULING-4 CALLABLE-SHAPE leg — every tool is a coroutine
  function and its body build composes with ``db_offload.offload``;
* (r) the AmRULING-1 DEGRADED-BOOT leg — with the manager's mount
  ``None``, the three tools are PRESENT and each returns the typed
  ``metrics-unavailable`` refusal (never absent, never an exception).

SAFETY FENCE: no HOME-derived or live-shaped path is ever resolved by
this file — every read is a repo-relative source read.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

import pytest

from daemon.tools._tool_registry import (
    CATEGORY_MODULES,
    DYNAMIC_TOOL_NAMES,
    KNOWN_TOOL_NAMES,
    PRIVILEGED_TOOL_CATEGORIES,
    discover_source_only_tool_names,
)
from daemon.tools.instance import resolve_tool_filter

REPO_ROOT = Path(__file__).resolve().parents[2]

CATEGORY = "metrics-read"
MODULE = "daemon.tools.metrics_read"

TOOL_NAMES: frozenset[str] = frozenset({
    "metrics_families",
    "metrics_snapshot",
    "metrics_history",
})

#: The agents allowed to read the metrics registry (the allow-list is the
#: SAME set as the tool bodies' runtime gate).
EXPECTED_HOLDERS: list[str] = ["maintenancer", "devops", "wanderer"]

#: The privileged universe — the sixteen base entries plus the
#: nest-report-read category = eighteen members; the memoir merge added
#: the nineteenth (2026-11-06).
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
    "notify-user",
    "metrics-read",
    "nest-report-read",
    # memoir (2026-11-05, memoir-store-v1 lane): the ``memoir``
    # category — ari's durable episodic memory + keepsake governance.
    # opt-in-only (PRIVILEGED entry #18); the ari allow entry is the
    # only access path, and every tool body ALSO carries its own
    # factory-captured agent_id gate.
    "memoir",
})


def _module_source() -> str:
    return (REPO_ROOT / "daemon" / "tools" / "metrics_read.py").read_text(
        encoding="utf-8"
    )


def _instance_source() -> str:
    return (REPO_ROOT / "daemon" / "tools" / "instance.py").read_text(
        encoding="utf-8"
    )


# ── the static registration points ─────────────────────────────────────────


class TestStaticRegistration:
    def test_ast_discovery_finds_the_three_tools(self) -> None:
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

    def test_dynamic_tool_names_contains_the_three(self) -> None:
        missing = TOOL_NAMES - set(DYNAMIC_TOOL_NAMES)
        assert missing == set(), f"DYNAMIC_TOOL_NAMES missing: {missing}"

    def test_known_tool_names_contains_the_three(self) -> None:
        missing = TOOL_NAMES - set(KNOWN_TOOL_NAMES)
        assert missing == set(), f"KNOWN_TOOL_NAMES missing: {missing}"

    def test_construction_extend_present_in_source(self) -> None:
        """The critical list-append is greppable in
        ``daemon/tools/instance.py``."""
        source = _instance_source()
        assert "from .metrics_read import create_metrics_read_tools" in source
        assert "create_metrics_read_tools(" in source
        assert "tools.extend(" in source

    def test_the_extend_precedes_the_filter_and_the_doc_scan(self) -> None:
        """The extend MUST precede ``create_help_tool`` /
        ``scan_tools_for_full_docs`` / ``_apply_tool_filter`` — a later
        extend either bypasses the privileged strip or is invisible to
        the help tool."""
        source = _instance_source()
        extend_idx = source.index("create_metrics_read_tools(")
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

    def test_the_decorator_order_is_register_then_tool(self) -> None:
        """``@register_tool_category`` MUST sit ABOVE ``@tool``.

        Reversed, the privileged strip FAILS OPEN (the category attribute
        is set on a different object than the tool).
        """
        tree = ast.parse(_module_source())
        found: dict[str, list[str]] = {}
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if node.name not in TOOL_NAMES:
                continue
            found[node.name] = [
                (d.func.id if isinstance(d, ast.Call) and isinstance(d.func, ast.Name) else "?")
                for d in node.decorator_list
            ]
        assert set(found) == TOOL_NAMES, f"missing tool defs: {set(TOOL_NAMES) - set(found)}"
        for name, decorators in found.items():
            assert decorators == ["register_tool_category", "tool"], (
                f"{name} carries the decorators {decorators} — the order must "
                "be ('register_tool_category', 'tool')"
            )


# ── the privileged category ────────────────────────────────────────────────


class TestPrivilegedCategory:
    def test_the_category_is_privileged(self) -> None:
        assert CATEGORY in PRIVILEGED_TOOL_CATEGORIES

    def test_the_set_is_exactly_the_nineteen_entries(self) -> None:
        assert PRIVILEGED_TOOL_CATEGORIES == EXPECTED_PRIVILEGED_SET

    def test_the_set_has_nineteen_members(self) -> None:
        assert len(PRIVILEGED_TOOL_CATEGORIES) == 19


# ── A13: the attribute pin + the default-deny behavioral leg ───────────────


class TestAttributePin:
    """A13 — the ``_tool_category`` attribute pin.

    Without it, the name-prefix fallback infers ``"metrics"`` from the
    tool names and the privileged strip FAILS OPEN.
    """

    def test_the_three_tools_carry_the_category_attribute(self) -> None:
        from daemon.tools.metrics_read import create_metrics_read_tools

        tools = create_metrics_read_tools(
            None,
            current_instance_id="",
            agent_id="maintenancer",
            agent_tag=None,
        )
        assert len(tools) == 3
        for tool in tools:
            assert getattr(tool, "_tool_category", None) == CATEGORY, (
                f"{getattr(tool, 'name', tool)} must carry "
                f"_tool_category == {CATEGORY!r} — without it the "
                "name-prefix fallback infers 'metrics' and the privileged "
                "strip FAILS OPEN"
            )

    def test_the_default_deny_leg_resolves_zero_metrics_tools(self) -> None:
        """An empty-allow synthetic agent resolves ZERO metrics tools.

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
            "an empty-allow agent must resolve ZERO metrics-read tools"
        )


# ── the two-layer authorization posture ────────────────────────────────────


class TestAuthorizationHelper:
    def test_the_helper_is_fail_closed_on_a_blank_agent(self) -> None:
        from daemon.tools.metrics_read import _is_metrics_authorized

        assert _is_metrics_authorized("") is False
        assert _is_metrics_authorized(None) is False
        assert _is_metrics_authorized(123) is False

    def test_the_helper_admits_exactly_the_three_holders(self) -> None:
        from daemon.tools.metrics_read import (
            METRICS_AUTHORIZED_AGENTS,
            _is_metrics_authorized,
        )

        assert METRICS_AUTHORIZED_AGENTS == frozenset(
            {"maintenancer", "devops", "wanderer"}
        )
        for agent_id in ("maintenancer", "devops", "wanderer"):
            assert _is_metrics_authorized(agent_id) is True
        for agent_id in ("ari", "coder", "tester", "designer", "leader", ""):
            assert _is_metrics_authorized(agent_id) is False


class TestGateOrdering:
    """The body gate precedes the substrate check, which precedes any
    provider read (D-SCOPE-4)."""

    @pytest.mark.parametrize("tool_name", sorted(TOOL_NAMES))
    def test_the_gate_order_is_body_gate_then_substrate_then_read(
        self, tool_name: str
    ) -> None:
        source = _module_source()
        start = source.index(f"async def {tool_name}(")
        end = source.find("\n    async def ", start + 1)
        body = source[start:] if end == -1 else source[start:end]
        gate_idx = body.index("_is_metrics_authorized(")
        substrate_idx = body.index("_provider_or_refuse(")
        read_idx = body.index("await _offload(")
        assert gate_idx < substrate_idx, "the body gate must come first"
        assert substrate_idx < read_idx, (
            "the substrate check must precede any provider read"
        )


class TestLiteralNameProbe:
    """D-SCOPE-2 — a LITERAL tool name in an allow list bypasses the
    category gate, so the body gate is the second, independent layer."""

    def test_the_literal_name_resolves_the_tool(self) -> None:
        allowed = resolve_tool_filter(
            allow=["metrics_families"],
            deny=[],
            all_tool_names=set(TOOL_NAMES),
        )
        assert allowed is not None
        assert "metrics_families" in allowed, (
            "a literal tool name must resolve the tool — that is exactly "
            "why the body gate exists"
        )

    async def test_the_body_gate_still_refuses_a_non_holder(self) -> None:
        from daemon.tools.metrics_read import create_metrics_read_tools

        tools = create_metrics_read_tools(
            None, current_instance_id="", agent_id="ari", agent_tag=None
        )
        by_name = {getattr(t, "name", ""): t for t in tools}
        result = await by_name["metrics_families"].ainvoke({})
        assert "not-authorized" in result, (
            "the allow list never reaches the body gate — a non-holder "
            "must be refused even when the tool is handed to them"
        )


class TestThreadedAgentId:
    """AmA-2 — the factory THREADS ``agent_id=agent_id``."""

    def test_the_factory_call_threads_the_agent_id(self) -> None:
        source = _instance_source()
        idx = source.index("create_metrics_read_tools(")
        window = source[idx : idx + 400]
        assert "agent_id=agent_id," in window, (
            "the factory call must thread the caller's agent_id — without "
            "it the body gate denies every call (fail-closed, but dead)"
        )

    async def test_the_threaded_value_reaches_the_body_gate(self) -> None:
        from daemon.tools.metrics_read import create_metrics_read_tools

        tools = create_metrics_read_tools(
            None, current_instance_id="", agent_id="maintenancer", agent_tag=None
        )
        by_name = {getattr(t, "name", ""): t for t in tools}
        result = await by_name["metrics_families"].ainvoke({})
        # The holder passed the body gate: the NEXT layer (the unmounted
        # provider) answers with the substrate refusal.
        assert "metrics-unavailable" in result, (
            "a holder must reach the substrate check; the threaded agent_id "
            "is what let it through the body gate"
        )


class TestCallableShape:
    """AmRULING-4 — the bodies are coroutines and the body build is
    off-loaded via ``db_offload.offload``."""

    def test_every_tool_is_a_coroutine_function(self) -> None:
        from daemon.tools.metrics_read import create_metrics_read_tools

        tools = create_metrics_read_tools(
            None, current_instance_id="", agent_id="maintenancer", agent_tag=None
        )
        for tool in tools:
            func = getattr(tool, "func", None) or getattr(tool, "coroutine", None)
            assert func is not None, f"{tool} carries no underlying function"
            assert asyncio_is_coroutine_function(func), (
                f"{getattr(tool, 'name', tool)} must be an async def — the "
                "house template"
            )

    def test_the_body_build_is_offloaded(self) -> None:
        source = _module_source()
        assert "db_offload" in source, (
            "the body build must compose with db_offload.offload — the "
            "event loop never pays for the registry read"
        )
        assert "await _offload(" in source


class TestDegradedBoot:
    """AmRULING-1 — the tools stay PRESENT on a degraded boot and refuse
    loudly (never absent, never an exception)."""

    async def test_the_tools_are_present_and_refuse_loudly(self) -> None:
        from daemon.tools.metrics_read import create_metrics_read_tools

        class _UnmountedManager:
            metrics_query_provider = None

        tools = create_metrics_read_tools(
            _UnmountedManager(),
            current_instance_id="",
            agent_id="maintenancer",
            agent_tag=None,
        )
        assert len(tools) == 3, "the tools must stay PRESENT on a degraded boot"
        for tool in tools:
            result = await tool.ainvoke({})
            assert "metrics-unavailable" in result, (
                f"{getattr(tool, 'name', tool)} must return the typed "
                "metrics-unavailable refusal"
            )
            assert "Traceback" not in result, "a refusal is never an exception"


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
        """A13's holders offenders-scan — the category appears in NO other
        agent's allow list."""
        holders = []
        for meta_path in sorted((REPO_ROOT / "agents").glob("*/meta.json")):
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            allow = meta.get("tools", {}).get("allow", [])
            if CATEGORY in allow:
                holders.append(meta_path.parent.name)
        assert sorted(holders) == sorted(EXPECTED_HOLDERS), (
            f"unexpected holders of the {CATEGORY} category: {holders}"
        )

    def test_the_category_gate_and_the_body_gate_are_the_same_set(
        self,
    ) -> None:
        """AmA-3 — the two spellings of the audience are INDEPENDENT, and
        this lane keeps them EQUAL (the equality is the point)."""
        from daemon.tools.metrics_read import METRICS_AUTHORIZED_AGENTS

        holders = set()
        for meta_path in sorted((REPO_ROOT / "agents").glob("*/meta.json")):
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            allow = meta.get("tools", {}).get("allow", [])
            if CATEGORY in allow:
                holders.add(meta_path.parent.name)
        assert holders == set(METRICS_AUTHORIZED_AGENTS)


# ── the read-only posture ──────────────────────────────────────────────────


class TestReadOnlyPosture:
    def test_the_module_exposes_no_write_tools(self) -> None:
        """There is NO act half: the category is structurally read-only."""
        from daemon.tools.metrics_read import TOOL_NAMES as module_tool_names

        assert module_tool_names == TOOL_NAMES
        for forbidden in (
            "metrics_write",
            "metrics_act",
            "metrics_subscribe",
            "metrics_alert",
        ):
            assert forbidden not in module_tool_names

    def test_the_category_doc_declares_the_architect_fold_additions(self) -> None:
        """The four architect-fold additions are loader-rendered, so the
        agent SEES them."""
        from daemon.tools.metrics_read import CATEGORY_DOC

        flat = " ".join(CATEGORY_DOC.split())
        assert "~1 320 points" in flat, "the default-call cost"
        assert "round trips to 3" in flat, "the patrol note"
        assert "NO anomaly-first ordering" in flat, "the non-goal"
        assert "ALERT surface may end up ari-only" in flat, (
            "the read-broad / alert-may-be-narrow asymmetry"
        )
        assert "NOT the Agent Snapshot artifact" in flat, (
            "the metrics_snapshot disambiguation sentence"
        )

    def test_the_shipped_strings_carry_no_defect_attribution(self) -> None:
        """The caveat is GENERIC — it asserts the runtime-verifiable fact
        and never names a defect (a shipped string that names one goes
        stale the moment the owning lane lands)."""
        source = _module_source()
        for token in ("R-i", "R-ii", "capture_active", "census inverted"):
            assert token not in source, (
                f"the shipped strings must not attribute a cause — {token!r} "
                "must be absent from metrics_read.py"
            )


def asyncio_is_coroutine_function(func: Any) -> bool:
    """Is ``func`` a coroutine function (property-or-method tolerant)?"""
    import inspect

    return inspect.iscoroutinefunction(func)
