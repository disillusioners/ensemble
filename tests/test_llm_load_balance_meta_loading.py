"""C6 regression tests: ``llm_models`` survives meta.json loading.

The ``AgentMetadata`` Pydantic model uses ``ConfigDict(extra="ignore")``,
which silently discards unknown JSON keys. A field declared on the
Pydantic model but WITHOUT the corresponding ``meta.get(...)`` loader
line in :meth:`AgentRegistry.discover` would be silently lost.

These tests catch that regression by:
  1. Loading a real (temporary) agent directory with ``llm_models`` in
     ``meta.json``, calling ``discover()``, and asserting the parsed
     list is preserved.
  2. Verifying backward-compat scenarios (absent, empty, malformed).

Mirrors the pattern in ``tests/test_governor_integration.py:229-274``
(C6 tests for ``inject_allowed_models`` / ``context_injection``).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest


@pytest.fixture
def agents_dir(tmp_path: Path) -> Path:
    """Create a minimal ``agents/`` directory; individual tests populate it."""
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    return agents_dir


def _write_meta(agent_id: str, meta: dict[str, Any], base: Path) -> Path:
    """Write a single agent's ``meta.json`` under ``base/<agent_id>/``."""
    agent_path = base / agent_id
    agent_path.mkdir(exist_ok=True)
    (agent_path / "meta.json").write_text(json.dumps(meta))
    return agent_path


class TestLlmModelsSurvivesLoading:
    """C6 — the new ``llm_models`` field must survive ``discover()``."""

    def test_llm_models_survives_loading(self, agents_dir: Path) -> None:
        """llm_models list with two entries round-trips through discover()."""
        _write_meta(
            "load_balance_test_agent",
            {
                "id": "load_balance_test_agent",
                "name": "Load Balance Test",
                "llm_models": [
                    {"model": "gpt-4o", "weight": 70},
                    {"model": "claude-sonnet-4", "weight": 30},
                ],
            },
            agents_dir,
        )
        from daemon.registry import AgentRegistry

        registry = AgentRegistry(agents_dir)
        registry.discover()
        agent = registry.get("load_balance_test_agent")
        assert agent is not None, "agent not discovered"
        assert agent.llm_models is not None, (
            "C6 REGRESSION: llm_models silently dropped — "
            "need BOTH the Pydantic field declaration AND the loader line"
        )
        assert len(agent.llm_models) == 2
        assert agent.llm_models[0].model == "gpt-4o"
        assert agent.llm_models[0].weight == 70
        assert agent.llm_models[1].model == "claude-sonnet-4"
        assert agent.llm_models[1].weight == 30

    def test_llm_models_absent_returns_none(self, agents_dir: Path) -> None:
        """Backward compat: agents without llm_models still load fine."""
        _write_meta(
            "no_load_balance",
            {"id": "no_load_balance", "name": "Plain"},
            agents_dir,
        )
        from daemon.registry import AgentRegistry

        registry = AgentRegistry(agents_dir)
        registry.discover()
        agent = registry.get("no_load_balance")
        assert agent is not None
        assert agent.llm_models is None

    def test_llm_models_empty_array_loads_as_empty_list(self, agents_dir: Path) -> None:
        """Backward compat: empty llm_models array does not crash."""
        _write_meta(
            "empty_pool",
            {
                "id": "empty_pool",
                "name": "Empty Pool",
                "llm_models": [],
            },
            agents_dir,
        )
        from daemon.registry import AgentRegistry

        registry = AgentRegistry(agents_dir)
        registry.discover()
        agent = registry.get("empty_pool")
        assert agent is not None
        assert agent.llm_models == []

    def test_llm_models_default_weight(self, agents_dir: Path) -> None:
        """An entry without ``weight`` gets the Pydantic default of 1."""
        _write_meta(
            "default_weight_agent",
            {
                "id": "default_weight_agent",
                "name": "Default Weight",
                "llm_models": [{"model": "gpt-4o"}],  # no weight
            },
            agents_dir,
        )
        from daemon.registry import AgentRegistry

        registry = AgentRegistry(agents_dir)
        registry.discover()
        agent = registry.get("default_weight_agent")
        assert agent is not None
        assert agent.llm_models is not None
        assert agent.llm_models[0].weight == 1

    def test_llm_models_with_float_weight(self, agents_dir: Path) -> None:
        """Float weights are truncated to int (50.7 → 50)."""
        _write_meta(
            "float_weight_agent",
            {
                "id": "float_weight_agent",
                "name": "Float Weight",
                "llm_models": [{"model": "gpt-4o", "weight": 50.7}],
            },
            agents_dir,
        )
        from daemon.registry import AgentRegistry

        registry = AgentRegistry(agents_dir)
        registry.discover()
        agent = registry.get("float_weight_agent")
        assert agent is not None
        assert agent.llm_models is not None
        assert agent.llm_models[0].weight == 50

    def test_llm_models_with_numeric_string_weight(self, agents_dir: Path) -> None:
        """Numeric string weights are coerced to int."""
        _write_meta(
            "string_weight_agent",
            {
                "id": "string_weight_agent",
                "name": "String Weight",
                "llm_models": [{"model": "gpt-4o", "weight": "70"}],
            },
            agents_dir,
        )
        from daemon.registry import AgentRegistry

        registry = AgentRegistry(agents_dir)
        registry.discover()
        agent = registry.get("string_weight_agent")
        assert agent is not None
        assert agent.llm_models is not None
        assert agent.llm_models[0].weight == 70


class TestMalformedLlmModelsFallback:
    """Malformed ``llm_models`` entries must not crash agent discovery.

    The discover() retry path drops the whole ``llm_models`` block (graceful
    fallback to ``llm_models=None``) and the rest of the agent metadata
    still loads.
    """

    def test_malformed_structural_string_drops_llm_models(self, agents_dir: Path) -> None:
        """``llm_models: "not a list"`` → dropped, agent still loads."""
        _write_meta(
            "broken_pool",
            {
                "id": "broken_pool",
                "name": "Broken Pool",
                "llm_models": "not a list",
                "llm_model": "fallback",
            },
            agents_dir,
        )
        from daemon.registry import AgentRegistry

        registry = AgentRegistry(agents_dir)
        registry.discover()  # must not raise
        agent = registry.get("broken_pool")
        assert agent is not None  # agent still loads
        assert agent.llm_models is None  # but llm_models is dropped
        # Other fields preserved
        assert agent.llm_model == "fallback"

    def test_malformed_entry_missing_model_drops_llm_models(self, agents_dir: Path) -> None:
        """Entry without 'model' key → Pydantic rejects → whole list dropped."""
        _write_meta(
            "missing_model",
            {
                "id": "missing_model",
                "name": "Missing Model",
                "llm_models": [{"weight": 50}],
            },
            agents_dir,
        )
        from daemon.registry import AgentRegistry

        registry = AgentRegistry(agents_dir)
        registry.discover()  # must not raise
        agent = registry.get("missing_model")
        assert agent is not None
        assert agent.llm_models is None

    def test_bool_weight_drops_llm_models(self, agents_dir: Path) -> None:
        """Bool weight → Pydantic rejects (custom validator) → dropped."""
        _write_meta(
            "bool_weight",
            {
                "id": "bool_weight",
                "name": "Bool Weight",
                "llm_models": [{"model": "gpt-4o", "weight": True}],
            },
            agents_dir,
        )
        from daemon.registry import AgentRegistry

        registry = AgentRegistry(agents_dir)
        registry.discover()  # must not raise
        agent = registry.get("bool_weight")
        assert agent is not None
        assert agent.llm_models is None

    def test_empty_string_model_drops_llm_models(self, agents_dir: Path) -> None:
        """Empty string model name → Pydantic min_length=1 → dropped."""
        _write_meta(
            "empty_model",
            {
                "id": "empty_model",
                "name": "Empty Model",
                "llm_models": [{"model": "", "weight": 50}],
            },
            agents_dir,
        )
        from daemon.registry import AgentRegistry

        registry = AgentRegistry(agents_dir)
        registry.discover()  # must not raise
        agent = registry.get("empty_model")
        assert agent is not None
        assert agent.llm_models is None

    def test_other_fields_survive_malformed_llm_models(self, agents_dir: Path) -> None:
        """When llm_models is dropped, other agent fields are preserved."""
        _write_meta(
            "other_fields",
            {
                "id": "other_fields",
                "name": "Other Fields",
                "description": "An agent with a broken llm_models block",
                "llm_model": "agent-level",
                "llm_models": "not a list",
                "team_members": ["worker"],
            },
            agents_dir,
        )
        from daemon.registry import AgentRegistry

        registry = AgentRegistry(agents_dir)
        registry.discover()
        agent = registry.get("other_fields")
        assert agent is not None
        assert agent.llm_models is None
        assert agent.llm_model == "agent-level"
        assert agent.description == "An agent with a broken llm_models block"
        assert "worker" in agent.team_members


class TestRealAgentsBackwardCompat:
    """Sanity check: real agents without ``llm_models`` still load fine."""

    def test_governor_unchanged(self) -> None:
        """The real governor agent has no ``llm_models`` → backward compatible."""
        from daemon.registry import AgentRegistry

        registry = AgentRegistry(Path("agents"))
        registry.discover()
        gov = registry.get("governor")
        assert gov is not None
        assert gov.llm_models is None  # no llm_models in governor's meta.json

    def test_coder_unchanged(self) -> None:
        """The real coder agent has ``llm_model`` but no ``llm_models``."""
        from daemon.registry import AgentRegistry

        registry = AgentRegistry(Path("agents"))
        registry.discover()
        coder = registry.get("coder")
        assert coder is not None
        assert coder.llm_models is None
        assert coder.llm_model == "coding"  # existing field preserved
