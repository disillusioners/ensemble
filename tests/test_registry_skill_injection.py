"""Tests for the ``skill_injection`` flag on ``AgentMetadata``.

Phase 1 of the Skill Evolution System — agent-level wiring.

The flag controls whether the agent should have dynamic skills
injected into its conversations. It's declared on
:class:`daemon.registry.AgentMetadata` with default ``False`` and is
populated from ``meta.json`` by ``AgentRegistry.discover()``.

Tests:

* Default value (``False``) on bare ``AgentMetadata``.
* Custom value (``True``) on bare ``AgentMetadata``.
* ``discover()`` reads ``skill_injection: true`` from ``meta.json``
  — the critical constructor-wiring test.
* ``discover()`` defaults to ``False`` when ``meta.json`` has no
  ``skill_injection`` key.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from daemon.registry import AgentMetadata, AgentRegistry


def _create_agent_meta(
    agents_dir: Path,
    agent_id: str,
    **meta_overrides,
) -> None:
    """Helper to create an agent directory with a meta.json.

    Mirrors the helper in ``tests/test_registry.py`` but accepts
    arbitrary meta.json overrides so each test can dial in exactly
    the slice of metadata it cares about.
    """
    agent_dir = agents_dir / agent_id
    agent_dir.mkdir()

    meta = {
        "id": agent_id,
        "name": agent_id.title(),
        "description": f"Test agent {agent_id}",
        "icon": "🤖",
        "color": "accent-blue",
        **meta_overrides,
    }

    with open(agent_dir / "meta.json", "w") as f:
        json.dump(meta, f)


class TestAgentMetadataDefaults:
    """Direct ``AgentMetadata`` constructor tests."""

    def test_skill_injection_default_false(self, tmp_path: Path):
        """A bare ``AgentMetadata`` defaults ``skill_injection`` to ``False``."""
        meta = AgentMetadata(
            id="bare",
            name="Bare",
            path=tmp_path / "bare",
        )
        assert meta.skill_injection is False

    def test_skill_injection_true(self, tmp_path: Path):
        """A bare ``AgentMetadata`` accepts ``skill_injection=True``."""
        meta = AgentMetadata(
            id="with-flag",
            name="With Flag",
            path=tmp_path / "with-flag",
            skill_injection=True,
        )
        assert meta.skill_injection is True

    def test_skill_injection_field_declared(self):
        """``skill_injection`` is a declared field (defends against removal)."""
        assert "skill_injection" in AgentMetadata.model_fields


class TestAgentRegistryDiscoverSkillInjection:
    """``AgentRegistry.discover()`` reads ``skill_injection`` from ``meta.json``."""

    def test_skill_injection_from_meta_json(self, tmp_path: Path):
        """CRITICAL: ``discover()`` wires ``skill_injection=true`` from meta.json.

        This is the integration test for the
        ``skill_injection=meta.get('skill_injection', False)`` line in
        :meth:`AgentRegistry.discover`. If the wiring breaks (e.g. the
        kwarg is renamed or removed), this test will fail loudly.
        """
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        _create_agent_meta(
            agents_dir,
            "dynamic-skills-agent",
            skill_injection=True,
        )

        registry = AgentRegistry(agents_dir)
        registry.discover()

        agent = registry.get("dynamic-skills-agent")
        assert agent is not None
        assert agent.skill_injection is True

    def test_skill_injection_absent_from_meta_json(self, tmp_path: Path):
        """When ``skill_injection`` is missing from ``meta.json``, default to False."""
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        _create_agent_meta(
            agents_dir,
            "no-flag-agent",
            # No skill_injection key in meta.json.
        )

        registry = AgentRegistry(agents_dir)
        registry.discover()

        agent = registry.get("no-flag-agent")
        assert agent is not None
        assert agent.skill_injection is False

    def test_skill_injection_false_in_meta_json(self, tmp_path: Path):
        """An explicit ``"skill_injection": false`` in ``meta.json`` is honored."""
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        _create_agent_meta(
            agents_dir,
            "explicit-false-agent",
            skill_injection=False,
        )

        registry = AgentRegistry(agents_dir)
        registry.discover()

        agent = registry.get("explicit-false-agent")
        assert agent is not None
        assert agent.skill_injection is False

    def test_skill_injection_per_agent_isolation(self, tmp_path: Path):
        """Two agents with different ``skill_injection`` flags don't bleed."""
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        _create_agent_meta(
            agents_dir, "with-flag", skill_injection=True
        )
        _create_agent_meta(
            agents_dir, "without-flag"  # no skill_injection
        )

        registry = AgentRegistry(agents_dir)
        registry.discover()

        with_flag = registry.get("with-flag")
        without_flag = registry.get("without-flag")
        assert with_flag is not None
        assert without_flag is not None
        assert with_flag.skill_injection is True
        assert without_flag.skill_injection is False

    def test_skill_injection_survives_discover_reload(self, tmp_path: Path):
        """Adding ``skill_injection`` to meta.json is picked up on re-discover."""
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()

        # First write without the flag.
        _create_agent_meta(agents_dir, "reload-agent")

        registry = AgentRegistry(agents_dir)
        registry.discover()
        first = registry.get("reload-agent")
        assert first is not None
        assert first.skill_injection is False

        # Now rewrite meta.json with the flag set, and re-discover.
        meta_path = agents_dir / "reload-agent" / "meta.json"
        with open(meta_path, "w") as f:
            json.dump(
                {
                    "id": "reload-agent",
                    "name": "Reload Agent",
                    "skill_injection": True,
                },
                f,
            )

        registry.discover()
        second = registry.get("reload-agent")
        assert second is not None
        assert second.skill_injection is True