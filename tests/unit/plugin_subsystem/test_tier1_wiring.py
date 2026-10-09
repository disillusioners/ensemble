"""Slice ⑤ tier-1 wiring tests — boot-scan + tool binding + name universe.

Covers the three sanctioned tier-1 touches (REC §4.3 row ⑤ PROVES line;
⑤b native-lane-LIVE precondition):

1. ``daemon/manager.py`` — ``InstanceManager._bootstrap_plugin_registry``
   populates the plugin registry at boot (the slice-③ "standalone+CI,
   consumer lands at ⑤" consumer). Tested at the wiring seam with a
   stub manager (the ``test_infra_bootstrap.py`` precedent — a full
   ``InstanceManager`` needs an LLM config, MCP pool, migration runner
   and more; the static ctor check pins the call site).
2. ``daemon/tools/instance.py`` — ``create_instance_tools`` extends the
   instance toolset with the 4 plugin Port tools. Tested through the
   REAL ``create_instance_tools`` path with a MagicMock manager +
   synthetic agent (the ``test_service_registration.py`` precedent),
   covering: designer-shaped allow grants them, agents without the
   names do not get them (uniform gating), and build failure /
   refused ports degrade cleanly to "not bound".
3. ``daemon/tools/_tool_registry.py`` — ``DYNAMIC_TOOL_NAMES`` carries
   the 4 names for allow/deny validation. The names must NOT enter
   ``KNOWN_TOOL_NAMES``: they are runtime ``StructuredTool``s, not
   source ``@tool`` functions, so the frozen-binary drift-equality
   (``test_known_tool_names_matches_source_exactly_no_drift``) would
   fail if they leaked into the static set.

No daemon boots; no live designer spawns.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import daemon.registry as dr
from daemon.registry import AgentRegistry
from daemon.plugin_subsystem.plugin_registry import DEFAULT_PLUGINS_ROOT
from daemon.tools._tool_registry import (
    DYNAMIC_TOOL_NAMES,
    KNOWN_TOOL_NAMES,
    discover_source_only_tool_names,
)

# tests/unit/plugin_subsystem/test_tier1_wiring.py -> parents[3] = repo root.
REPO_ROOT = Path(__file__).resolve().parents[3]

PLUGIN_TOOL_NAMES: frozenset[str] = frozenset({
    "od.generate",
    "od.compose_brief",
    "od.save",
    "od.lint",
})


# =============================================================================
# 1. Boot-scan wiring (daemon/manager.py)
# =============================================================================


class TestPluginBootScanWiring:
    """``_bootstrap_plugin_registry`` — the slice-③ registry consumer."""

    MANAGER_SOURCE: str = (REPO_ROOT / "daemon" / "manager.py").read_text(
        encoding="utf-8"
    )

    def test_boot_scan_called_from_init(self) -> None:
        """Static check: ``self._bootstrap_plugin_registry()`` is invoked
        from ``InstanceManager.__init__``, positioned right after the
        infra-types bootstrap (same boot-seeding block)."""
        assert "self._bootstrap_plugin_registry()" in self.MANAGER_SOURCE, (
            "InstanceManager.__init__ no longer boots the plugin registry — "
            "the slice-③ 'consumer lands at ⑤' wiring regressed"
        )
        infra_pos = self.MANAGER_SOURCE.find("self._bootstrap_infra_types()")
        plugin_pos = self.MANAGER_SOURCE.find(
            "self._bootstrap_plugin_registry()"
        )
        assert infra_pos != -1 and plugin_pos != -1
        assert infra_pos < plugin_pos, (
            "_bootstrap_plugin_registry call drifted out of the "
            "boot-seeding block (expected after _bootstrap_infra_types)"
        )

    def test_boot_scan_populates_registry_from_real_root(self) -> None:
        """Functional seam: a stub manager + the real plugins root →
        ``_plugin_registry`` populated with the opendesign plugin (and
        no refusals on the shipped tree)."""
        from daemon.manager import InstanceManager

        stub = SimpleNamespace()
        InstanceManager._bootstrap_plugin_registry(stub)
        registry = stub._plugin_registry
        assert registry is not None
        assert "opendesign" in registry
        assert registry.skill_ids() == (
            "opendesign.generate-pipeline",
            "opendesign.list_systems",
        )
        assert not registry.has_failures()

    def test_boot_scan_absent_root_degrades_to_no_plugins(self, monkeypatch, tmp_path) -> None:
        """Graceful degradation: a nonexistent plugins root yields an
        empty registry — logged, never raised (contractual)."""
        from daemon.manager import InstanceManager
        from daemon.plugin_subsystem import plugin_registry as pr

        monkeypatch.setattr(pr, "DEFAULT_PLUGINS_ROOT", tmp_path / "nope")
        stub = SimpleNamespace()
        InstanceManager._bootstrap_plugin_registry(stub)
        assert stub._plugin_registry is not None
        assert len(stub._plugin_registry) == 0

    def test_boot_scan_invalid_plugin_refused_not_fatal(self, monkeypatch, tmp_path) -> None:
        """Graceful degradation: a plugin dir without MANIFEST.yaml is
        recorded as a refusal — boot continues, registry still builds."""
        from daemon.manager import InstanceManager
        from daemon.plugin_subsystem import plugin_registry as pr

        broken = tmp_path / "broken-plugin"
        broken.mkdir()
        monkeypatch.setattr(pr, "DEFAULT_PLUGINS_ROOT", tmp_path)
        stub = SimpleNamespace()
        InstanceManager._bootstrap_plugin_registry(stub)
        registry = stub._plugin_registry
        assert registry is not None
        assert len(registry) == 0
        assert "broken-plugin" in registry.refused_names()

    def test_boot_scan_registry_build_failure_never_raises(self, monkeypatch) -> None:
        """Fault-tolerance wrap: if the scan itself explodes, the method
        logs, leaves ``_plugin_registry = None``, and returns — boot
        proceeds without plugins."""
        from daemon.manager import InstanceManager
        from daemon.plugin_subsystem import plugin_registry as pr

        def _boom(root):
            raise RuntimeError("simulated scan crash")

        monkeypatch.setattr(pr, "load_registry", _boom)
        stub = SimpleNamespace()
        InstanceManager._bootstrap_plugin_registry(stub)  # must not raise
        assert stub._plugin_registry is None


# =============================================================================
# 2. Tool binding through the REAL create_instance_tools path
# =============================================================================

DESIGNER_ALLOW: list[str] = json.loads(
    (REPO_ROOT / "agents" / "designer" / "meta.json").read_text(encoding="utf-8")
)["tools"]["allow"]


def _stage_synthetic_agent(tmp_path: Path, agent_id: str, tools_cfg: dict) -> Path:
    """Stage a synthetic agent dir with the given tools config (the
    test_service_registration.py pattern)."""
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
    """Install a registry that discovers the staged synthetic agents."""

    def _install(agents_dir: Path) -> AgentRegistry:
        registry = AgentRegistry(agents_dir)
        registry.discover()
        monkeypatch.setattr(dr, "_registry", registry)
        return registry

    return _install


def _build_instance_tools(agent_id: str) -> dict[str, object]:
    """REAL create_instance_tools() path with a MagicMock manager (the
    test_service_registration.py precedent)."""
    from daemon.tools.instance import create_instance_tools

    manager = MagicMock(name="InstanceManager")
    manager.config.daemon.port = 0
    manager.config.llm.allowed_models = []
    tools = create_instance_tools(manager, f"inst-{agent_id}", agent_id)
    return {getattr(t, "name", "?"): t for t in tools}


class TestPluginToolBinding:
    """create_instance_tools extends with the 4 plugin Port tools."""

    def test_designer_shaped_allow_resolves_all_four(self, tmp_path, registry_for) -> None:
        """A designer-shaped agent (its real allow list, which carries
        the od.* names) resolves ALL four plugin Port tools through the
        real create_instance_tools path."""
        agents_dir = _stage_synthetic_agent(
            tmp_path, "syn-od-designer", {"allow": list(DESIGNER_ALLOW)}
        )
        registry_for(agents_dir)
        by_name = _build_instance_tools("syn-od-designer")
        got = PLUGIN_TOOL_NAMES & by_name.keys()
        assert got == set(PLUGIN_TOOL_NAMES), (
            f"designer-shaped allow must bind all 4 plugin Port tools; "
            f"missing={sorted(set(PLUGIN_TOOL_NAMES) - got)}"
        )

    def test_agent_without_od_names_gets_none(self, tmp_path, registry_for) -> None:
        """Uniform per-agent gating: an explicit-allow agent whose list
        lacks the od.* names sees NONE of them (no special-casing)."""
        allow_without_od = [a for a in DESIGNER_ALLOW if not a.startswith("od.")]
        agents_dir = _stage_synthetic_agent(
            tmp_path, "syn-od-blind", {"allow": allow_without_od}
        )
        registry_for(agents_dir)
        by_name = _build_instance_tools("syn-od-blind")
        got = PLUGIN_TOOL_NAMES & by_name.keys()
        assert got == set(), (
            f"agent without od.* allow entries must not see plugin tools; "
            f"leaked={sorted(got)}"
        )

    def test_default_universe_agent_gets_all_four(self, tmp_path, registry_for) -> None:
        """Default-open universe (empty allow): the plugin tools are NOT
        privileged — a default-universe agent carries them, exactly like
        every other non-privileged category."""
        agents_dir = _stage_synthetic_agent(tmp_path, "syn-od-default", {"allow": []})
        registry_for(agents_dir)
        by_name = _build_instance_tools("syn-od-default")
        got = PLUGIN_TOOL_NAMES & by_name.keys()
        assert got == set(PLUGIN_TOOL_NAMES)

    def test_factory_crash_degrades_to_unbound(self, tmp_path, monkeypatch, registry_for) -> None:
        """Graceful degradation (contractual): a plugin-tool build
        failure must never break instance assembly — the designer path
        degrades to tool-not-bound (its text fallback)."""
        from daemon.tools import instance as inst_mod

        def _boom(**kwargs):
            raise ValueError("simulated port-registry refusal at build")

        monkeypatch.setattr(inst_mod, "build_plugin_tools", _boom)
        agents_dir = _stage_synthetic_agent(
            tmp_path, "syn-od-degraded", {"allow": list(DESIGNER_ALLOW)}
        )
        registry_for(agents_dir)
        by_name = _build_instance_tools("syn-od-degraded")  # must not raise
        got = PLUGIN_TOOL_NAMES & by_name.keys()
        assert got == set(), "degraded build must bind zero plugin tools"
        assert "bash" in by_name, "the rest of the toolset must survive"

    def test_refused_ports_bind_nothing(self, tmp_path, monkeypatch, registry_for) -> None:
        """Seam-gate refusals surface as unbound tools (never as
        breakage): a factory returning an all-refused pair still yields
        a working toolset without the od.* names."""
        from daemon.tools import instance as inst_mod

        refused = [(name, "seam_gate_failed") for name in sorted(PLUGIN_TOOL_NAMES)]
        monkeypatch.setattr(
            inst_mod, "build_plugin_tools", lambda **kwargs: ([], refused)
        )
        agents_dir = _stage_synthetic_agent(
            tmp_path, "syn-od-refused", {"allow": list(DESIGNER_ALLOW)}
        )
        registry_for(agents_dir)
        by_name = _build_instance_tools("syn-od-refused")
        assert PLUGIN_TOOL_NAMES & by_name.keys() == set()

    def test_binding_extend_present_in_source(self) -> None:
        """Greppable three-step seam: the import + the list-extend exist
        in daemon/tools/instance.py (decorator-only = silently
        invisible; the extend is the load-bearing line)."""
        source = (REPO_ROOT / "daemon" / "tools" / "instance.py").read_text(
            encoding="utf-8"
        )
        assert "from ..plugin_subsystem.plugin_tool_factory import build_plugin_tools" in source
        assert "tools.extend(plugin_tool_list)" in source
        assert "build_plugin_tools()" in source


# =============================================================================
# 3. DYNAMIC_TOOL_NAMES universe + frozen-binary parity preservation
# =============================================================================


class TestPluginToolNameUniverse:
    """Allow/deny validation knows the 4 names — and KNOWN_TOOL_NAMES
    parity is preserved (they are NOT source ``@tool`` functions)."""

    def test_dynamic_tool_names_carries_all_four(self) -> None:
        missing = PLUGIN_TOOL_NAMES - set(DYNAMIC_TOOL_NAMES)
        assert missing == set(), f"DYNAMIC_TOOL_NAMES missing: {sorted(missing)}"

    def test_known_tool_names_does_not_carry_them(self) -> None:
        """The names are runtime StructuredTools, not source ``@tool``
        functions — they must stay OUT of KNOWN_TOOL_NAMES or the
        frozen-binary drift-equality test fails (only_in_static)."""
        leaked = PLUGIN_TOOL_NAMES & set(KNOWN_TOOL_NAMES)
        assert leaked == set(), (
            f"KNOWN_TOOL_NAMES must not carry plugin Port tools "
            f"(breaks test_known_tool_names_matches_source_exactly_no_drift): "
            f"{sorted(leaked)}"
        )

    def test_names_are_not_source_tool_discoverable(self) -> None:
        """Positive control for the pin above: the AST source discovery
        does not see these names — which is exactly why they live only
        in DYNAMIC_TOOL_NAMES."""
        source_names = discover_source_only_tool_names()
        assert PLUGIN_TOOL_NAMES & source_names == set()


# =============================================================================
# 4. Designer lane config (sanctioned designer-lane file)
# =============================================================================


class TestDesignerLaneAllow:
    """The designer's meta.json allow list carries the 4 names — the
    designer-shaped agent above derives from THIS file, so the
    functional grant test and this config pin stay in lockstep."""

    def test_designer_allow_includes_all_four(self) -> None:
        missing = PLUGIN_TOOL_NAMES - set(DESIGNER_ALLOW)
        assert missing == set(), (
            f"agents/designer/meta.json tools.allow missing plugin Port "
            f"tools: {sorted(missing)} — the designer native lane would "
            f"be filtered out despite the tier-1 binding"
        )
