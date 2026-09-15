"""Registration + default-deny tests for the ``service`` tool category
(service-tool Phase 1.C, tasks 1.C.7 SC-6 acceptance + the 10-step
registration checklist).

The 3-step registration seam, asserted greppably AND functionally:

1. **AST source discovery** — ``discover_source_only_tool_names()``
   finds all 5 factory-created ``service_*`` tools in the category
   module (owned jointly with ``test_frozen_tool_name_discovery``).
2. **CATEGORY_MODULES entry** — ``"service" →
   daemon.tools.service_tools``; the 5 names in
   ``DYNAMIC_TOOL_NAMES`` + ``KNOWN_TOOL_NAMES``.
3. **The CRITICAL list-append** — ``create_instance_tools()``
   extends its tool list with ``create_service_tools(...)``
   (decorator-only = silently invisible — the gotcha that bit
   before).

Plus the D4 Option A privilege surface (SC-6 behavioral acceptance):

* **Decorator order PINNED** — ``@register_tool_category("service")``
  OUTER, ``@tool`` INNER for all five tools (the
  ``test_attestation_registration`` precedent), verified per tool by
  regex on the module source AND at runtime (the live StructuredTool
  carries ``_tool_category == "service"`` through the langchain
  wrap).
* **SC-6** — a default-configured agent (empty allow / no tools
  config) resolves ZERO ``service_*`` tools through the REAL
  ``create_instance_tools()`` path; ``tools.allow=["service"]``
  resolves ALL five. This is the behavioral half of the D4 same-PR
  pin contract (the exact-equality frozenset pins live in
  ``test_upgrade_registration`` / ``test_attestation_registration`` /
  ``test_maintenancer_spawn_resolves_tools`` — pin files 1-3 of 3).

Synthetic agents are staged under ``tmp_path`` (a copy of the real
``agents/watcher/meta.json`` with modified tools config) with the
registry boot path redirected — no repo file is modified, mirroring
``test_upgrade_registration.py``.
"""
from __future__ import annotations

import re
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
    discover_source_only_tool_names,
)

# Repo root: tests/unit/tools/test_service_registration.py -> parents[3].
REPO_ROOT = Path(__file__).resolve().parents[3]

SERVICE_TOOL_NAMES = {
    "service_start",
    "service_stop",
    "service_status",
    "service_list",
    "service_logs",
}
SERVICE_CATEGORY = "service"
SERVICE_MODULE = "daemon.tools.service_tools"


# ── Steps 1-3: static registration points (greppable checklist) ──────────────


class TestStaticRegistrationChecklist:
    def test_step1_ast_discovery_finds_all_five(self) -> None:
        """§8 step 1 + the AST walker: the category module's
        factory-created ``@tool`` functions are discoverable from
        source."""
        discovered = discover_source_only_tool_names()
        missing = SERVICE_TOOL_NAMES - discovered
        assert missing == set(), f"AST discovery missed: {missing}"

    def test_step2_category_modules_entry(self) -> None:
        assert CATEGORY_MODULES.get(SERVICE_CATEGORY) == SERVICE_MODULE

    def test_step3_dynamic_tool_names_contains_all_five(self) -> None:
        missing = SERVICE_TOOL_NAMES - set(DYNAMIC_TOOL_NAMES)
        assert missing == set(), f"DYNAMIC_TOOL_NAMES missing: {missing}"

    def test_step3b_known_tool_names_contains_all_five(self) -> None:
        """The frozen-binary fallback universe carries the 5 names too
        (exact drift equality is owned by test_frozen_tool_name_discovery)."""
        missing = SERVICE_TOOL_NAMES - set(KNOWN_TOOL_NAMES)
        assert missing == set(), f"KNOWN_TOOL_NAMES missing: {missing}"

    def test_step4_create_instance_tools_list_append_present_in_source(self) -> None:
        """The CRITICAL list-append is greppable in daemon/tools/instance.py:
        create_service_tools(...) extended into the tools list."""
        source = (REPO_ROOT / "daemon" / "tools" / "instance.py").read_text(
            encoding="utf-8"
        )
        assert "from .service_tools import create_service_tools" in source
        assert "tools.extend(service_tool_list)" in source
        assert "create_service_tools(" in source

    def test_category_is_privileged_d4_option_a(self) -> None:
        """D4 Option A: the category is in the default-deny union —
        the exact-equality side is pinned in the three pin files;
        this asserts the membership side."""
        assert SERVICE_CATEGORY in PRIVILEGED_TOOL_CATEGORIES


# ── Decorator order (1.B's file — pinned here, not edited here) ──────────────


class TestDecoratorOrder:
    @staticmethod
    def _order_ok_at_some_site(source: str, tool_name: str) -> bool:
        """True when SOME ``@register_tool_category("service")`` site in
        the module source is followed by ``@tool`` then ``def
        <tool_name>(`` — the pinned OUTER/INNER order. (The docstring
        also quotes the decorator, so every occurrence is checked, not
        just the first.)"""
        needle = '@register_tool_category("service")'
        pos = source.find(needle)
        while pos != -1:
            tail = source[pos : pos + 300]
            if re.match(
                r'@register_tool_category\("service"\)[ \t]*\n'
                r"[ \t]*@tool[ \t]*\n"
                r"[ \t]*(?:async )?def " + re.escape(tool_name) + r"\(",
                tail,
            ):
                return True
            pos = source.find(needle, pos + 1)
        return False

    def test_register_outer_tool_inner_for_all_five(self) -> None:
        """``@register_tool_category("service")`` MUST sit OUTER
        (above) ``@tool`` for EVERY tool, so the category attr is set
        on the raw function before langchain wraps it. Pinned per
        tool across all decorator sites (the module docstring quotes
        the decorator too — the runtime attr-survival test below is
        the behavioral complement)."""
        source = (
            REPO_ROOT / "daemon" / "tools" / "service_tools.py"
        ).read_text(encoding="utf-8")
        for tool_name in sorted(SERVICE_TOOL_NAMES):
            assert self._order_ok_at_some_site(source, tool_name), (
                f"decorator order wrong (or tool renamed) for "
                f"{tool_name!r}: expected the exact block "
                f'@register_tool_category("service") -> @tool -> def '
                f"{tool_name}("
            )


# ── Live tool behavior: decorator order survives the langchain wrap ─────────


def _service_names(by_name: dict) -> set:
    return set(by_name) & SERVICE_TOOL_NAMES


def _build_service_tools_via_factory() -> dict[str, object]:
    """Build the 5 tools through the REAL factory (construction-level
    attribute survival — no filtering)."""
    from daemon.tools.service_tools import create_service_tools

    manager = MagicMock(name="InstanceManager")
    tools = create_service_tools(manager, "inst-reg-check", "reg-check")
    return {getattr(t, "name", "?"): t for t in tools}


class TestLiveToolBehavior:
    def test_all_five_carry_category_attr(self) -> None:
        """The live StructuredTool carries the category metadata that
        ``@register_tool_category`` set on the raw function — i.e.
        the decorator order preserved the attr through the langchain
        ``@tool`` wrap (runtime half of the order pin)."""
        by_name = _build_service_tools_via_factory()
        got = _service_names(by_name)
        assert got == SERVICE_TOOL_NAMES, (
            f"factory must build all 5 tools, got {sorted(got)}"
        )
        for tool_name in sorted(SERVICE_TOOL_NAMES):
            tool = by_name[tool_name]
            assert getattr(tool, "_tool_category", None) == SERVICE_CATEGORY, (
                f"{tool_name} lost its _tool_category attr through the "
                f"langchain wrap — decorator order regressed"
            )

    def test_factory_returns_empty_for_falsy_instance_id(self) -> None:
        """The F-guard: falsy current_instance_id ⇒ [] (proc_tools
        precedent) — this is what lets the loader warm-list stub
        receive a truthy placeholder id and keeps instance-less
        contexts tool-less."""
        from daemon.tools.service_tools import create_service_tools

        assert create_service_tools(MagicMock(name="m"), "") == []
        assert create_service_tools(None, None) == []


# ── SC-6 behavioral: default-deny vs explicit allow (REAL filter path) ──────


def _stage_synthetic_agent(
    tmp_path: Path, agent_id: str, tools_cfg: dict | None
) -> Path:
    """Stage a synthetic agent dir under tmp_path by cloning watcher's real
    meta.json shape with a replaced tools config (mirrors
    test_upgrade_registration._stage_synthetic_agent)."""
    import json

    real_meta = json.loads(
        (REPO_ROOT / "agents" / "watcher" / "meta.json").read_text(encoding="utf-8")
    )
    real_meta.pop("watchover", None)
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
    discovers it, and return a fresh tool-builder bound to that registry."""

    def _install(agents_dir: Path) -> AgentRegistry:
        registry = AgentRegistry(agents_dir)
        registry.discover()
        monkeypatch.setattr(dr, "_registry", registry)
        return registry

    return _install


def _build_instance_tools(agent_id: str) -> dict[str, object]:
    """REAL create_instance_tools() path — includes the filter (the
    SC-6 behavioral half)."""
    from daemon.tools.instance import create_instance_tools

    manager = MagicMock(name="InstanceManager")
    manager.config.daemon.port = 0
    tools = create_instance_tools(manager, f"inst-{agent_id}", agent_id)
    return {getattr(t, "name", "?"): t for t in tools}


class TestSC6DefaultDenyBehavior:
    def test_allow_service_resolves_all_five(
        self, tmp_path: Path, registry_for
    ) -> None:
        """tools.allow=["service"] resolves ALL five tools through the
        REAL create_instance_tools() path."""
        agents_dir = _stage_synthetic_agent(
            tmp_path, "syn-svc-allow", {"allow": [SERVICE_CATEGORY]}
        )
        registry = registry_for(agents_dir)
        assert registry.exists("syn-svc-allow")
        by_name = _build_instance_tools("syn-svc-allow")
        got = _service_names(by_name)
        assert got == SERVICE_TOOL_NAMES, (
            f"tools.allow=[service] must resolve all 5, got {sorted(got)}"
        )

    def test_default_configured_agent_resolves_zero_service_tools(
        self, tmp_path: Path, registry_for
    ) -> None:
        """SC-6 core: a default-configured agent (allow list WITHOUT
        service) resolves ZERO service_* tools — the behavioral effect
        of the D4 Option A frozenset add."""
        _stage_synthetic_agent(
            tmp_path,
            "syn-svc-none",
            {"allow": ["bash", "filesystem", "time", "help"]},
        )
        registry_for(tmp_path / "agents")
        by_name = _build_instance_tools("syn-svc-none")
        got = _service_names(by_name)
        assert got == set(), (
            f"default-configured agent resolved service_* tools {sorted(got)} "
            f"— the PRIVILEGED_TOOL_CATEGORIES default-deny seam regressed"
        )

    def test_empty_allow_resolves_zero_service_tools(
        self, tmp_path: Path, registry_for
    ) -> None:
        """R-SR16 empty-allow path: "everything non-privileged" must
        NOT include service (watcher-like agent)."""
        _stage_synthetic_agent(tmp_path, "syn-svc-empty", {"allow": []})
        registry_for(tmp_path / "agents")
        by_name = _build_instance_tools("syn-svc-empty")
        assert _service_names(by_name) == set(), (
            "empty-allow agent default-granted the privileged service "
            "category — D4 Option A / R-SR16 regression"
        )
        # …but the agent still gets ordinary categories (scoped deny).
        assert "bash" in by_name or "time" in by_name

    def test_no_tools_config_at_all_resolves_zero_service_tools(
        self, tmp_path: Path, registry_for
    ) -> None:
        """The other default-allow path (tools config entirely ABSENT)
        — the strip must apply there too (defense-in-depth)."""
        _stage_synthetic_agent(tmp_path, "syn-svc-no-tools", None)
        import json

        meta_path = tmp_path / "agents" / "syn-svc-no-tools" / "meta.json"
        meta = json.loads(meta_path.read_text())
        meta["tools"] = None
        meta_path.write_text(json.dumps(meta))
        registry_for(tmp_path / "agents")
        by_name = _build_instance_tools("syn-svc-no-tools")
        assert _service_names(by_name) == set(), (
            "no-tools-config agent default-granted the privileged "
            "service category — the _strip_privileged_category_tools "
            "defense-in-depth regressed"
        )
