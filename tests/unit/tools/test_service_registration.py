"""Registration + default-grant tests for the ``service`` tool category
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
from daemon.tools.instance import INNATE_SKILL_TOOL_CATEGORIES

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
        """OVERRIDE 2026-09-16: ``service`` is NO LONGER in
        ``PRIVILEGED_TOOL_CATEGORIES`` (D4 reversed by user directive —
        default-grant; see decisions.md §D4 override note). The
        exact-equality side is pinned in the three pin files; this
        asserts the membership side of the REVERSAL (i.e. the
        negative: service is NOT in the trio)."""
        assert SERVICE_CATEGORY not in PRIVILEGED_TOOL_CATEGORIES


# ── 3.B.3b innate-skill ∩ privileged isolation pin (architect rider) ──────


class TestInnateSkillPrivilegedIsolation:
    """3.B.3b — closure on the architect-rider gap flagged during the
    service-tool review (plan: ``.agents/shared/planning/service-tool/
    phase3-plan.md`` task 3.B.3b).

    ``expand_allow_for_innate_skills`` (~``daemon/tools/instance.py:186``)
    appends the categories mapped from an agent's innate skills to its
    allow list REGARDLESS of whether those categories are privileged.
    That is the design — innate-skill tool access should "just work"
    without every agent having to repeat the skill's category in
    ``tools.allow``. But it has a structural consequence: ANY mapping
    in ``INNATE_SKILL_TOOL_CATEGORIES`` whose value-set intersects
    ``PRIVILEGED_TOOL_CATEGORIES`` would silently default-grant a
    default-deny category to every agent declaring that innate skill —
    a far more dangerous bypass than the chart-vs-instance mapping
    caught earlier (see
    ``tests/test_tool_filter.py:test_chart_innate_skill_adds_chart_category_not_instance``).
    ``service`` being added to the privileged set on D4 Option A made
    this worth pinning: any future agent declaring
    ``innate_skills: ["service-tools-skill"]`` would default-grant
    system-process-spawning authority without ever naming ``service``
    in its ``tools.allow``. The 2026-09-16 override REMOVED ``service``
    from the privileged set, so the negative pin is the structural
    invariant against any FUTURE re-introduction (or against any other
    future default-deny category sneaking into the innate-skill map).

    The pin screams on any such future mapping — the regression
    printout names the offending category(ies) AND the current
    privileged set so the next debugger sees the trap without
    re-reading source.
    """

    def test_innate_skill_categories_disjoint_from_privileged(self) -> None:
        """No value-set in ``INNATE_SKILL_TOOL_CATEGORIES`` may
        intersect the privileged-set. Empty intersection is the
        structural invariant — fail with names + current privileged
        set so a regression printout is self-explanatory."""
        innate_union: set[str] = set().union(
            *INNATE_SKILL_TOOL_CATEGORIES.values()
        )
        offending = innate_union & PRIVILEGED_TOOL_CATEGORIES
        assert offending == set(), (
            f"innate-skill categories intersect "
            f"PRIVILEGED_TOOL_CATEGORIES: offending={sorted(offending)}; "
            f"current privileged set={sorted(PRIVILEGED_TOOL_CATEGORIES)}; "
            f"the offending categories would be default-granted to every "
            f"agent declaring the hosting innate skill, bypassing the D4 "
            f"Option A default-deny seam (architect-rider 3.B.3b)"
        )

    def test_innate_skill_categories_not_vacuously_empty(self) -> None:
        """Positive control: the dict has real entries — the negative
        pin above is meaningful, not accidentally green because the
        dict became empty. Cheap insurance against a future refactor
        that empties the dict and silently disables the negative pin."""
        union: set[str] = set().union(*INNATE_SKILL_TOOL_CATEGORIES.values())
        assert union, (
            "INNATE_SKILL_TOOL_CATEGORIES has no entries — the negative "
            "intersection pin is vacuously clean and not testing anything. "
            "Either restore real entries or delete this pin pair."
        )


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


# ── SC-6 behavioral: default-grant (override 2026-09-16) vs explicit allow (REAL filter path) ──────


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


class TestSC6DefaultGrantBehavior:
    def test_allow_service_resolves_all_five(
        self, tmp_path: Path, registry_for
    ) -> None:
        """tools.allow=["service"] resolves ALL five tools through the
        REAL create_instance_tools() path. Explicit-grant path —
        unchanged by the override."""
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

    def test_default_universe_resolves_all_five(
        self, tmp_path: Path, registry_for
    ) -> None:
        """SC-6 core (override 2026-09-16): a default-universe agent
        (empty allow / no tools config) gets service via the
        default-open universe — the override made ``service``
        default-grant for ALL agents.

        NOTE: the meta-grant IFF rule (``bash OR proc in effective
        toolset → append ``service`` to allow``) is enforced via the
        meta files, not the filter — this test verifies the filter-
        level default-grant semantics, NOT the meta-grant policy. The
        meta-grant policy is verified by
        ``tools/dev/verify_service_default_open.py`` (per-agent harness)
        and by the metas under ``agents/``.
        """
        # Empty allow = true default universe. (A non-empty allow with
        # bash/proc is the meta-grant IFF case — covered by the
        # per-agent harness, NOT the filter.)
        _stage_synthetic_agent(tmp_path, "syn-svc-default", {"allow": []})
        registry_for(tmp_path / "agents")
        by_name = _build_instance_tools("syn-svc-default")
        got = _service_names(by_name)
        assert got == SERVICE_TOOL_NAMES, (
            f"default-universe agent resolved service_* tools "
            f"{sorted(got)} — the override 2026-09-16 default-grant "
            f"seam regressed (want all 5, got {len(got)})"
        )

    def test_non_bash_proc_agent_with_explicit_service_still_grants(
        self, tmp_path: Path, registry_for
    ) -> None:
        """Explicit-grant still works for non-bash/non-proc agents
        (no auto-grant by category; explicit allow=["service"] is the
        mechanism when neither bash nor proc is reachable)."""
        _stage_synthetic_agent(
            tmp_path,
            "syn-svc-nobash-explicit",
            {"allow": [SERVICE_CATEGORY, "filesystem", "time", "help"]},
        )
        registry_for(tmp_path / "agents")
        by_name = _build_instance_tools("syn-svc-nobash-explicit")
        got = _service_names(by_name)
        assert got == SERVICE_TOOL_NAMES, (
            f"non-bash/proc agent with explicit service allow resolved "
            f"{sorted(got)}; want all 5 (explicit-grant path)"
        )

    def test_empty_allow_resolves_all_five_via_default_universe(
        self, tmp_path: Path, registry_for
    ) -> None:
        """Default-universe (empty allow): ``service`` is in the
        default-open universe now (override 2026-09-16) — every
        default-configured agent gets all five service_* tools."""
        _stage_synthetic_agent(tmp_path, "syn-svc-empty", {"allow": []})
        registry_for(tmp_path / "agents")
        by_name = _build_instance_tools("syn-svc-empty")
        got = _service_names(by_name)
        assert got == SERVICE_TOOL_NAMES, (
            f"empty-allow agent default-grant failed; got {sorted(got)} — "
            f"the override 2026-09-16 default-grant seam regressed"
        )
        # The agent still gets ordinary categories (sanity check).
        assert "bash" in by_name or "time" in by_name

    def test_no_tools_config_at_all_resolves_all_five(
        self, tmp_path: Path, registry_for
    ) -> None:
        """The no-tools-config path (tools config entirely ABSENT)
        resolves all five service_* tools via default-universe
        default-grant."""
        _stage_synthetic_agent(tmp_path, "syn-svc-no-tools", None)
        import json

        meta_path = tmp_path / "agents" / "syn-svc-no-tools" / "meta.json"
        meta = json.loads(meta_path.read_text())
        meta["tools"] = None
        meta_path.write_text(json.dumps(meta))
        registry_for(tmp_path / "agents")
        by_name = _build_instance_tools("syn-svc-no-tools")
        got = _service_names(by_name)
        assert got == SERVICE_TOOL_NAMES, (
            f"no-tools-config agent default-grant failed; got {sorted(got)} "
            f"— the override 2026-09-16 default-grant seam regressed"
        )

    def test_control_unprivileged_category_unaffected(
        self, tmp_path: Path, registry_for
    ) -> None:
        """Control: a non-privileged category (``filesystem``) is
        unaffected by the override — its default-grant semantics were
        unchanged. Empty-allow agent gets filesystem tools."""
        # Empty allow = true default universe — filesystem category
        # is non-privileged and should be present.
        _stage_synthetic_agent(tmp_path, "syn-svc-ctrl", {"allow": []})
        registry_for(tmp_path / "agents")
        by_name = _build_instance_tools("syn-svc-ctrl")
        filesystem_tools = [
            n for n in by_name
            if n in {
                "list_directory", "read_file", "glob_files",
                "grep_files", "edit_file", "write_file",
            }
        ]
        assert filesystem_tools, (
            "filesystem category control regressed — the override "
            "should not have touched non-privileged categories "
            f"(got {sorted(by_name)[:5]}...)"
        )
