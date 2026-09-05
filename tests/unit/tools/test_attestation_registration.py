"""Registration + opt-in tests for the ``attestation`` tool category
(Phase 1 of the Leader Completion Attestation feature, 2026-09-05).

Mirrors the 4-step discipline documented in
``daemon/tools/upgrade_tools.py:110-143`` (the P2.2 REGISTRATION
CHECKLIST) and adapted for the LCA category:

1. **Decorator order** — ``@register_tool_category("attestation")``
   MUST sit ABOVE ``@tool`` so the category attr is set on the raw
   function before langchain wraps it. Verified by (a) reading the
   live ``_tool_category`` and ``_tool_category_first_party`` attrs
   on the StructuredTool and (b) source-greping the decorator order.
2. **CATEGORY_MODULES entry** — ``"attestation": "daemon.tools.attestation"``
   in ``daemon/tools/_tool_registry.py``.
3. **DYNAMIC_TOOL_NAMES** — the factory-created ``attest_completion``
   name (decorator-only registration is SILENTLY INVISIBLE — this
   the gotcha the §8 checklist exists to catch).
4. **KNOWN_TOOL_NAMES** — the regen-pasted frozen-binary fallback
   also carries the name (drift equality is owned by
   ``test_frozen_tool_name_discovery``).

Plus the leader opt-in:

* The leader's REAL ``meta.json`` (loaded via ``get_version(id, tag)``
  with ``get_resolved()`` fallback) carries ``"attestation"`` in
  ``tools.allow`` — so a leader instance resolves ``attest_completion``
  through the REAL ``create_instance_tools()`` path.
* No other agent in the registry has ``"attestation"`` in
  ``tools.allow`` — the leader is the only one with the category
  enabled in v1 (fail-closed authz; the category is opt-in-only by
  convention but NOT in ``PRIVILEGED_TOOL_CATEGORIES``).

Synthetic agents are staged under ``tmp_path`` (a copy of the real
``agents/watcher/meta.json`` with modified tools config) with the
registry boot path redirected — same pattern as
``test_upgrade_registration.py``.
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

# Repo root: tests/unit/tools/test_attestation_registration.py -> parents[3].
REPO_ROOT = Path(__file__).resolve().parents[3]

ATTESTATION_TOOL_NAME = "attest_completion"
ATTESTATION_CATEGORY = "attestation"
ATTESTATION_MODULE = "daemon.tools.attestation"


# ── Steps 1-4: static registration points (greppable checklist) ──────────────


class TestStaticRegistrationChecklist:
    """The §8 / P2.2 checklist adapted for the attestation category."""

    def test_step1_ast_discovery_finds_attest_completion(self) -> None:
        """Step 1 + AST walker: the category module's factory-created
        ``@tool`` function ``attest_completion`` is discoverable from
        source. ``discover_source_only_tool_names`` walks the entire
        AST tree, so the module-level function is caught whether the
        factory closure or module-level registration is used.
        """
        discovered = discover_source_only_tool_names()
        assert ATTESTATION_TOOL_NAME in discovered, (
            f"AST discovery missed {ATTESTATION_TOOL_NAME}; "
            f"check that @register_tool_category sits above @tool in "
            f"{ATTESTATION_MODULE}"
        )

    def test_step2_category_modules_entry(self) -> None:
        """Step 2: ``CATEGORY_MODULES['attestation']`` points at
        ``daemon.tools.attestation``."""
        assert CATEGORY_MODULES.get(ATTESTATION_CATEGORY) == ATTESTATION_MODULE

    def test_step3_dynamic_tool_names_contains_attest_completion(self) -> None:
        """Step 3: the factory-created name lives in ``DYNAMIC_TOOL_NAMES``
        so startup validation knows it before any instance is built
        (decorator-only = SILENTLY INVISIBLE if missing)."""
        assert ATTESTATION_TOOL_NAME in set(DYNAMIC_TOOL_NAMES), (
            f"DYNAMIC_TOOL_NAMES missing {ATTESTATION_TOOL_NAME}"
        )

    def test_step3b_known_tool_names_contains_attest_completion(self) -> None:
        """Step 3b: the frozen-binary fallback universe (``KNOWN_TOOL_NAMES``)
        carries the name too. Drift equality is owned by
        ``test_frozen_tool_name_discovery``; this test stays focused on
        the explicit LCA category pin."""
        assert ATTESTATION_TOOL_NAME in set(KNOWN_TOOL_NAMES), (
            f"KNOWN_TOOL_NAMES missing {ATTESTATION_TOOL_NAME}; "
            f"regen via: uv run python -c \"from daemon.tools._tool_registry "
            f"import discover_source_only_tool_names; print(sorted(discover_source_only_tool_names()))\""
        )

    def test_step4_create_instance_tools_list_append_present_in_source(self) -> None:
        """Step 4: the CRITICAL list-append is greppable in
        ``daemon/tools/instance.py``: ``create_attestation_tools(...)``
        extended into the tools list. Decorator-only registration is
        SILENTLY INVISIBLE — the §8 checklist's central gotcha."""
        source = (REPO_ROOT / "daemon" / "tools" / "instance.py").read_text(
            encoding="utf-8"
        )
        assert "from .attestation import create_attestation_tools" in source
        assert "tools.extend(attestation_tool_list)" in source
        assert "create_attestation_tools(" in source

    def test_decorator_order_register_above_tool_in_source(self) -> None:
        """Decorator order: ``@register_tool_category("attestation")``
        MUST sit above ``@tool`` so the category attr is set on the
        raw function before langchain wraps it (langchain ``@tool``
        preserves the attr via functools.wraps, but only if the
        register decorator ran first). Verified by source-grepping
        the decorator order in the attestation module."""
        source = (REPO_ROOT / "daemon" / "tools" / "attestation.py").read_text(
            encoding="utf-8"
        )
        # Locate the block of decorator + def lines for attest_completion
        register_idx = source.find('@register_tool_category("attestation")')
        tool_idx = source.find("@tool")
        def_idx = source.find("def attest_completion(")
        assert register_idx != -1, "@register_tool_category decorator missing"
        assert tool_idx != -1, "@tool decorator missing"
        assert def_idx != -1, "def attest_completion(...) missing"
        assert register_idx < tool_idx < def_idx, (
            f"decorator order wrong: register@{register_idx}, "
            f"tool@{tool_idx}, def@{def_idx}"
        )

    def test_attestation_not_in_privileged_categories(self) -> None:
        """``PRIVILEGED_TOOL_CATEGORIES`` stays at its current single
        entry (``system_upgrade``). The attestation category is
        opt-in-only by convention (fail-closed authz), NOT because
        it is privileged — D7 sub-question RESOLVED-by-leader:
        NOT privileged. Adding ``attestation`` to
        ``PRIVILEGED_TOOL_CATEGORIES`` would be a regression (it
        would force every opt-in path to use the privileged-default-deny
        seam, which attestation does NOT need)."""
        assert ATTESTATION_CATEGORY not in PRIVILEGED_TOOL_CATEGORIES
        # system_upgrade stays the only privileged entry — pin it
        # so silent additions are visible.
        assert PRIVILEGED_TOOL_CATEGORIES == frozenset({"system_upgrade"})


# ── Live tool behavior: decorator order survives langchain wrap ─────────────


class TestLiveToolBehavior:
    """Verify the live StructuredTool carries the category metadata
    that ``@register_tool_category`` set on the raw function — i.e.
    that decorator order preserved the attr through the langchain
    ``@tool`` wrap."""

    def test_attest_completion_carries_category_attrs(self) -> None:
        from daemon.tools.attestation import attest_completion

        assert attest_completion._tool_category == ATTESTATION_CATEGORY
        assert attest_completion._tool_category_first_party is True
        assert attest_completion.name == ATTESTATION_TOOL_NAME
        assert hasattr(attest_completion, "_full_doc_")
        # No-arg tool: args schema is empty.
        assert attest_completion.args == {}

    def test_create_attestation_tools_returns_single_tool(self) -> None:
        from daemon.tools.attestation import (
            create_attestation_tools,
            attest_completion,
        )

        manager = MagicMock(name="InstanceManager")
        tools = create_attestation_tools(manager, "test-inst-id", "leader")
        assert len(tools) == 1
        assert tools[0] is attest_completion
        assert tools[0]._tool_category == ATTESTATION_CATEGORY


# ── Synthetic-agent staging (tmp tree + registry redirect) ──────────────────


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


# ── Step 4 functional: allow resolves attest_completion; default-deny resolves none ─


class TestFunctionalRegistration:
    """The functional side: a synthetic agent with ``attestation`` in
    ``tools.allow`` resolves the tool through the REAL
    ``create_instance_tools()`` path; without it, the tool is NOT
    resolved."""

    def test_allow_entry_resolves_attest_completion(
        self, tmp_path: Path, registry_for
    ) -> None:
        agents_dir = _stage_synthetic_agent(
            tmp_path, "syn-allow", {"allow": [ATTESTATION_CATEGORY]}
        )
        registry = registry_for(agents_dir)
        assert registry.exists("syn-allow")
        by_name = _build_instance_tools("syn-allow")
        assert ATTESTATION_TOOL_NAME in by_name, (
            f"tools.allow=[attestation] must resolve {ATTESTATION_TOOL_NAME}, "
            f"got {sorted(by_name)}"
        )

    def test_without_allow_entry_resolves_none(
        self, tmp_path: Path, registry_for
    ) -> None:
        _stage_synthetic_agent(
            tmp_path, "syn-none", {"allow": ["bash", "filesystem", "time", "help"]}
        )
        registry_for(tmp_path / "agents")
        by_name = _build_instance_tools("syn-none")
        assert ATTESTATION_TOOL_NAME not in by_name, (
            "agent without an attestation allow entry must not see the tool"
        )


# ── Real-meta resolution (leader + non-leader agents) ─────────────────────


class TestRealAgentResolution:
    """The REAL registry: only the leader has ``attestation`` in
    ``tools.allow`` (Phase 1 v1 scope). Every other agent is denied
    the tool through the real ``create_instance_tools()`` path."""

    @pytest.fixture
    def real_registry(self, monkeypatch: pytest.MonkeyPatch) -> AgentRegistry:
        registry = AgentRegistry(REPO_ROOT / "agents")
        registry.discover()
        monkeypatch.setattr(dr, "_registry", registry)
        return registry

    def test_leader_real_meta_resolves_attest_completion(
        self, real_registry: AgentRegistry
    ) -> None:
        """The leader's REAL meta.json (loaded via ``get_version(id, tag)``
        with ``get_resolved()`` fallback per the rule ``ALL meta lookups
        MUST use get_version() w/ fallback to get_resolved()``) carries
        ``attestation`` in ``tools.allow`` — and the leader's instance
        resolves ``attest_completion`` through the REAL
        ``create_instance_tools()`` path."""
        meta = real_registry.get_version("leader", None) or real_registry.get_resolved(
            "leader"
        )
        assert meta is not None, "leader meta must exist"
        assert meta.tools is not None
        assert ATTESTATION_CATEGORY in meta.tools.allow, (
            f"leader meta.json tools.allow missing {ATTESTATION_CATEGORY}; "
            f"got {meta.tools.allow}"
        )
        by_name = _build_instance_tools("leader")
        assert ATTESTATION_TOOL_NAME in by_name, (
            f"leader instance must resolve {ATTESTATION_TOOL_NAME} "
            f"via the create_instance_tools path; got {sorted(by_name)}"
        )

    @pytest.mark.parametrize(
        "agent_id",
        [
            "developer",
            "reviewer",
            "tidier",
            "approver",
            "architect",
            "tester",
            "giter",
            "devops",
            "explorer",
            "wanderer",
            "kb-writer",
            "doc-writer",
        ],
    )
    def test_non_leader_agents_resolve_none(
        self, real_registry: AgentRegistry, agent_id: str
    ) -> None:
        """All 12 non-leader agents MUST NOT have ``attestation`` in
        their ``tools.allow`` and MUST NOT resolve the tool through
        ``create_instance_tools()``. This is the leader-only scope
        pin (D3 RESOLVED)."""
        meta = (
            real_registry.get_version(agent_id, None)
            or real_registry.get_resolved(agent_id)
        )
        assert meta is not None, f"{agent_id} must exist in the real registry"
        allow = meta.tools.allow if meta.tools else []
        assert ATTESTATION_CATEGORY not in allow, (
            f"{agent_id} unexpectedly allow-lists {ATTESTATION_CATEGORY}"
        )
        assert ATTESTATION_TOOL_NAME not in allow, (
            f"{agent_id} unexpectedly allow-lists the tool name directly"
        )
        by_name = _build_instance_tools(agent_id)
        assert ATTESTATION_TOOL_NAME not in by_name, (
            f"{agent_id} unexpectedly resolves {ATTESTATION_TOOL_NAME} — "
            f"leader-only scope regression"
        )