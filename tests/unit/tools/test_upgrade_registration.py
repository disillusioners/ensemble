"""Registration + default-deny tests for the ``system_upgrade`` tool
category (P2.2, phase2-plan T2 acceptance + R-SR16).

The 4-step registration checklist (tool-api-design §3.3/§8, mirrored as the
comment block in ``daemon/tools/upgrade_tools.py``), asserted greppably AND
functionally:

1. **AST source discovery** — ``discover_source_only_tool_names()`` finds all
   4 factory-created tools in the category module.
2. **CATEGORY_MODULES entry** — ``"system_upgrade" → daemon.tools.upgrade_tools``.
3. **DYNAMIC_TOOL_NAMES** — the 4 names (factory-created, not import-time
   registered) so startup validation knows them.
4. **The CRITICAL list-append** — ``create_instance_tools()`` extends its
   tool list with ``create_upgrade_tools(...)`` (decorator-only =
   silently invisible — the gotcha that bit before).

Plus the R-SR16 default-deny surface (review minor #3's functional gap):

* ``PRIVILEGED_TOOL_CATEGORIES = {"system_upgrade"}`` — opt-in-only.
* An agent with ``tools.allow=["system_upgrade"]`` resolves ALL 4 tool
  objects through the REAL ``create_instance_tools()`` path; without it,
  NONE — including an EMPTY-allow agent (watcher-like) which would
  otherwise get every category.
* Deny still wins over allow.
* The docs paths (``help._get_allowed_tools`` + the
  ``loader.load_tools_doc_for_agent`` system-prompt carry-in) mirror the
  execution side: an empty-allow agent's docs NEVER mention the category.
* ari's REAL ``meta.json`` resolves the 4 (get_version/get_resolved
  convention); non-allow agents (worker, jober, watcher) resolve none.

Synthetic agents are staged under ``tmp_path`` (a copy of the real
``agents/watcher/meta.json`` with modified tools config) with the registry
boot path redirected — no repo file is modified, mirroring
``test_tool_config_validation_boot.py``.
"""
from __future__ import annotations

import json
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

# Repo root: tests/unit/tools/test_upgrade_registration.py -> parents[3].
REPO_ROOT = Path(__file__).resolve().parents[3]

UPGRADE_TOOL_NAMES = {"release_info", "upgrade_status", "system_restart", "system_upgrade"}
UPGRADE_CATEGORY = "system_upgrade"
UPGRADE_MODULE = "daemon.tools.upgrade_tools"


# ── Step 1-3: static registration points (greppable checklist) ───────────────


class TestStaticRegistrationChecklist:
    def test_step1_ast_discovery_finds_all_four(self) -> None:
        """§8 step 1 + the AST walker: the category module's factory-created
        @tool functions are discoverable from source."""
        discovered = discover_source_only_tool_names()
        missing = UPGRADE_TOOL_NAMES - discovered
        assert missing == set(), f"AST discovery missed: {missing}"

    def test_step2_category_modules_entry(self) -> None:
        assert CATEGORY_MODULES.get(UPGRADE_CATEGORY) == UPGRADE_MODULE

    def test_step3_dynamic_tool_names_contains_all_four(self) -> None:
        missing = UPGRADE_TOOL_NAMES - set(DYNAMIC_TOOL_NAMES)
        assert missing == set(), f"DYNAMIC_TOOL_NAMES missing: {missing}"

    def test_step3b_known_tool_names_contains_all_four(self) -> None:
        """The frozen-binary fallback universe carries the 4 names too
        (exact drift equality is owned by test_frozen_tool_name_discovery)."""
        missing = UPGRADE_TOOL_NAMES - set(KNOWN_TOOL_NAMES)
        assert missing == set(), f"KNOWN_TOOL_NAMES missing: {missing}"

    def test_step4_create_instance_tools_list_append_present_in_source(self) -> None:
        """The CRITICAL list-append is greppable in daemon/tools/instance.py:
        create_upgrade_tools(...) extended into the tools list."""
        source = (REPO_ROOT / "daemon" / "tools" / "instance.py").read_text(
            encoding="utf-8"
        )
        assert "from .upgrade_tools import create_upgrade_tools" in source
        assert "tools.extend(upgrade_tool_list)" in source
        assert "create_upgrade_tools(" in source

    def test_privileged_categories_is_exactly_system_upgrade(self) -> None:
        """R-SR16: the opt-in-only set is exactly {system_upgrade} today —
        adding a category here is a deliberate trust decision, and this pin
        makes silent additions visible."""
        assert PRIVILEGED_TOOL_CATEGORIES == frozenset({UPGRADE_CATEGORY})

    def test_checklist_comment_block_present_in_module(self) -> None:
        """The T2 checklist is encoded as the module's comment block — the
        greppable artifact future tool changes must re-run."""
        source = (REPO_ROOT / "daemon" / "tools" / "upgrade_tools.py").read_text(
            encoding="utf-8"
        )
        assert "P2.2 REGISTRATION CHECKLIST" in source
        for marker in (
            "CATEGORY_MODULES",
            "DYNAMIC_TOOL_NAMES",
            "KNOWN_TOOL_NAMES",
            "create_instance_tools",
            "PRIVILEGED_TOOL_CATEGORIES",
        ):
            assert marker in source, f"checklist no longer names {marker}"


# ── Synthetic-agent staging (tmp tree + registry redirect) ───────────────────


def _stage_synthetic_agent(
    tmp_path: Path, agent_id: str, tools_cfg: dict | None
) -> Path:
    """Stage a synthetic agent dir under tmp_path by cloning watcher's real
    meta.json shape with a replaced tools config. Returns the tmp agents dir."""
    real_meta = json.loads(
        (REPO_ROOT / "agents" / "watcher" / "meta.json").read_text(encoding="utf-8")
    )
    real_meta.pop("watchover", None)  # irrelevant for tool filtering
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
    from daemon.tools.instance import create_instance_tools

    manager = MagicMock(name="InstanceManager")
    manager.config.daemon.port = 0
    tools = create_instance_tools(manager, f"inst-{agent_id}", agent_id)
    return {getattr(t, "name", "?"): t for t in tools}


def _upgrade_names(by_name: dict) -> set:
    return set(by_name) & UPGRADE_TOOL_NAMES


# ── Step 4 functional: allow resolves all 4; default-deny resolves none ─────


class TestFunctionalRegistration:
    def test_allow_entry_resolves_all_four(
        self, tmp_path: Path, registry_for
    ) -> None:
        agents_dir = _stage_synthetic_agent(
            tmp_path, "syn-allow", {"allow": [UPGRADE_CATEGORY]}
        )
        registry = registry_for(agents_dir)
        assert registry.exists("syn-allow")
        by_name = _build_instance_tools("syn-allow")
        got = _upgrade_names(by_name)
        assert got == UPGRADE_TOOL_NAMES, (
            f"tools.allow=[system_upgrade] must resolve all 4, got {got}"
        )

    def test_allow_single_tool_name_grants_only_that_tool(
        self, tmp_path: Path, registry_for
    ) -> None:
        """An individual tool name in allow passes through WITHOUT expanding
        the whole category (resolve_tool_filter semantics: category names
        expand, individual names don't)."""
        _stage_synthetic_agent(
            tmp_path, "syn-one-tool", {"allow": ["bash", "release_info"]}
        )
        agents_dir = tmp_path / "agents"
        registry = registry_for(agents_dir)
        assert registry.exists("syn-one-tool")
        by_name = _build_instance_tools("syn-one-tool")
        assert _upgrade_names(by_name) == {"release_info"}

    def test_without_allow_entry_resolves_none(
        self, tmp_path: Path, registry_for
    ) -> None:
        _stage_synthetic_agent(
            tmp_path, "syn-none", {"allow": ["bash", "filesystem", "time", "help"]}
        )
        registry_for(tmp_path / "agents")
        by_name = _build_instance_tools("syn-none")
        assert _upgrade_names(by_name) == set(), (
            "agent without a system_upgrade allow entry must not see the tools"
        )

    def test_empty_allow_default_deny_rsr16(
        self, tmp_path: Path, registry_for
    ) -> None:
        """R-SR16: an EMPTY allow list means "every non-privileged category"
        — the privileged system_upgrade category is NEVER default-granted
        (watcher-like agent; the review-minor-#3 functional gap)."""
        _stage_synthetic_agent(tmp_path, "syn-empty-allow", {"allow": []})
        registry = registry_for(tmp_path / "agents")
        assert registry.exists("syn-empty-allow")
        by_name = _build_instance_tools("syn-empty-allow")
        assert _upgrade_names(by_name) == set(), (
            "empty-allow agent default-granted the privileged system_upgrade "
            "category — R-SR16 regression"
        )
        # …but the agent still gets the ordinary categories (the exclusion
        # is scoped to privileged categories only, not a blanket deny).
        assert "bash" in by_name or "time" in by_name

    def test_no_tools_config_at_all_default_deny_rsr16(
        self, tmp_path: Path, registry_for
    ) -> None:
        """The other default-allow path: tools config entirely ABSENT — the
        strip must apply there too (defense-in-depth)."""
        _stage_synthetic_agent(tmp_path, "syn-no-tools", None)
        # tools: null in meta
        meta_path = tmp_path / "agents" / "syn-no-tools" / "meta.json"
        meta = json.loads(meta_path.read_text())
        meta["tools"] = None
        meta_path.write_text(json.dumps(meta))
        registry_for(tmp_path / "agents")
        by_name = _build_instance_tools("syn-no-tools")
        assert _upgrade_names(by_name) == set()

    def test_deny_wins_over_allow(self, tmp_path: Path, registry_for) -> None:
        _stage_synthetic_agent(
            tmp_path,
            "syn-deny",
            {"allow": [UPGRADE_CATEGORY], "deny": [UPGRADE_CATEGORY]},
        )
        registry_for(tmp_path / "agents")
        by_name = _build_instance_tools("syn-deny")
        assert _upgrade_names(by_name) == set(), "deny must strip the category"

    def test_deny_single_tool_strips_that_tool_only(
        self, tmp_path: Path, registry_for
    ) -> None:
        """deny=["release_info"] (an individual tool name — NOT the category
        key) strips only that tool; the category-named deny in the test
        above expands to all 4."""
        _stage_synthetic_agent(
            tmp_path,
            "syn-deny-one",
            {"allow": [UPGRADE_CATEGORY], "deny": ["release_info"]},
        )
        registry_for(tmp_path / "agents")
        by_name = _build_instance_tools("syn-deny-one")
        assert _upgrade_names(by_name) == UPGRADE_TOOL_NAMES - {"release_info"}


# ── Real-meta resolution (ari + non-allow agents) ────────────────────────────


class TestRealAgentResolution:
    @pytest.fixture
    def real_registry(self, monkeypatch: pytest.MonkeyPatch) -> AgentRegistry:
        registry = AgentRegistry(REPO_ROOT / "agents")
        registry.discover()
        monkeypatch.setattr(dr, "_registry", registry)
        return registry

    def test_ari_real_meta_resolves_all_four(
        self, real_registry: AgentRegistry
    ) -> None:
        """ari's REAL meta.json carries the category (T3) — resolved through
        the get_version/get_resolved convention."""
        meta = real_registry.get_version("ari", None) or real_registry.get_resolved("ari")
        assert meta is not None
        assert UPGRADE_CATEGORY in meta.tools.allow
        by_name = _build_instance_tools("ari")
        assert _upgrade_names(by_name) == UPGRADE_TOOL_NAMES

    @pytest.mark.parametrize("agent_id", ["worker", "jober", "watcher"])
    def test_non_allow_agents_resolve_none(
        self, real_registry: AgentRegistry, agent_id: str
    ) -> None:
        meta = (
            real_registry.get_version(agent_id, None)
            or real_registry.get_resolved(agent_id)
        )
        assert meta is not None, f"{agent_id} must exist in the real registry"
        allow = meta.tools.allow if meta.tools else []
        assert UPGRADE_CATEGORY not in allow and not (
            set(allow) & UPGRADE_TOOL_NAMES
        ), f"{agent_id} unexpectedly allow-lists system_upgrade"
        by_name = _build_instance_tools(agent_id)
        assert _upgrade_names(by_name) == set()


# ── Docs paths mirror the execution side (no docs leak) ─────────────────────


class TestDocsDefaultDeny:
    """The help/loader docs carry-in must not advertise privileged tools to
    agents that cannot call them (the system-prompt side of R-SR16)."""

    @pytest.fixture
    def staged(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        def _stage(agent_id: str, tools_cfg: dict | None) -> None:
            agents_dir = _stage_synthetic_agent(tmp_path, agent_id, tools_cfg)
            # Redirect the registry boot path's agents-dir resolution.
            monkeypatch.setattr(dr, "__file__", str(tmp_path / "daemon" / "registry.py"))
            monkeypatch.setattr(dr, "_registry", None)

        (tmp_path / "daemon").mkdir(exist_ok=True)
        return _stage

    def test_empty_allow_agent_no_docs_leak(self, staged) -> None:
        from daemon.loader import load_tools_doc_for_agent
        from daemon.tools.help import _get_allowed_tools

        staged("docs-empty-allow", {"allow": []})
        allowed = _get_allowed_tools("docs-empty-allow")
        assert allowed is not None  # the None path became the non-privileged set
        assert set(allowed) & UPGRADE_TOOL_NAMES == set()
        doc = load_tools_doc_for_agent("docs-empty-allow")
        for leaked in (
            "system_upgrade",
            "system_restart",
            "release_info",
            "upgrade_status",
        ):
            assert leaked not in doc, f"docs leak: {leaked} advertised to empty-allow agent"

    def test_allow_agent_docs_include_category(self, staged) -> None:
        from daemon.loader import load_tools_doc_for_agent
        from daemon.tools.help import _get_allowed_tools

        staged("docs-allow", {"allow": [UPGRADE_CATEGORY]})
        allowed = _get_allowed_tools("docs-allow")
        assert UPGRADE_TOOL_NAMES <= set(allowed)
        doc = load_tools_doc_for_agent("docs-allow")
        for name in sorted(UPGRADE_TOOL_NAMES):
            assert name in doc, f"allowed agent's docs must include {name}"

    def test_default_documented_tools_excludes_privileged(self) -> None:
        from daemon.tools.help import _default_documented_tools

        universe = _default_documented_tools(None)
        assert set(universe) & UPGRADE_TOOL_NAMES == set()
        # …while the full registry DOES know the tools (the exclusion is the
        # privileged-category strip, not ignorance of the category).
        from daemon.tools._tool_registry import list_tools_by_category

        assert set(list_tools_by_category()[UPGRADE_CATEGORY]) == UPGRADE_TOOL_NAMES
