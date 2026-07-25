"""Tests for inject_allowed_models flag and append_allowed_models appender.

Phase 3 of Governor Council-Manager.
C2: manager.config (no underscore) verification.
C6: flag survives loading from meta.json (regression test).
W8: error path appends status="error" block (not silent no-op).
"""
import json
from pathlib import Path
from typing import Any

import pytest


# --- Test 1: Flag off → no injection ---

class FakeMetaOff:
    inject_allowed_models = False


class FakeMetaOn:
    inject_allowed_models = True


class FakeLLM:
    def __init__(self, models: list[str]) -> None:
        self.allowed_models = models


class FakeConfig:
    def __init__(self, models: list[str]) -> None:
        self.llm = FakeLLM(models)


class FakeManager:
    """C2: uses .config (NO underscore)."""
    def __init__(self, models: list[str]) -> None:
        self.config = FakeConfig(models)


class FakeManagerBroken:
    """W8: config is None → AttributeError when accessing manager.config.llm."""
    config = None


@pytest.fixture
def append_allowed_models():
    from daemon.services.instance_lifecycle import append_allowed_models as fn
    return fn


def test_flag_off_returns_prompt_unchanged(append_allowed_models):
    """When the flag is False, the appender must return the prompt unchanged."""
    result = append_allowed_models("base", FakeMetaOff(), FakeManager(["gpt-4o"]))
    assert result == "base"


def test_flag_on_with_models_injects_block(append_allowed_models):
    """When flag is True and allowed_models is non-empty, models are listed."""
    result = append_allowed_models("base", FakeMetaOn(), FakeManager(["gpt-4o", "claude-3-5-sonnet"]))
    assert "<allowed_models>" in result
    assert "gpt-4o" in result
    assert "claude-3-5-sonnet" in result
    # Read-only notice (prompt-injection guard)
    assert "read-only" in result.lower() or "not instructions" in result.lower()
    # Models list should be present
    assert "- gpt-4o" in result
    assert "- claude-3-5-sonnet" in result


def test_flag_on_with_empty_models_unrestricted_message(append_allowed_models):
    """When flag is True but allowed_models is empty, show unrestricted message."""
    result = append_allowed_models("base", FakeMetaOn(), FakeManager([]))
    assert "<allowed_models>" in result
    assert "No model restriction" in result or "empty" in result.lower() or "unrestricted" in result.lower()


def test_flag_on_error_returns_error_block(append_allowed_models):
    """W8: exception path appends status='error' block (NOT silent no-op)."""
    result = append_allowed_models("base", FakeMetaOn(), FakeManagerBroken())
    assert 'status="error"' in result
    assert "ASK the user" in result or "ask the user" in result.lower()
    # Error block must still be present — original prompt followed by error marker
    assert result.startswith("base")
    assert "<allowed_models" in result


# ===== C6 INTEGRATION TEST =====

def test_inject_allowed_models_loads_from_meta_json(tmp_path):
    """C6 regression: flag survives loading from a real meta.json."""
    # Create a fake agent directory with a meta.json that has the flag
    agent_dir = tmp_path / "test-agent"
    agent_dir.mkdir()
    meta_file = agent_dir / "meta.json"
    meta_file.write_text(json.dumps({
        "id": "test-agent",
        "name": "Test Agent",
        "inject_allowed_models": True,
    }))

    # Load via the registry
    from daemon.registry import AgentRegistry
    registry = AgentRegistry(tmp_path)
    registry.discover()

    meta = registry.get("test-agent")
    assert meta is not None, "Agent should be discovered"
    assert meta.inject_allowed_models is True, (
        "C6 REGRESSION: inject_allowed_models flag was silently discarded! "
        "Likely cause: loader line missing in AgentRegistry.discover()."
    )


def test_inject_allowed_models_default_false_when_missing():
    """When meta.json omits the flag, default is False."""
    from daemon.registry import AgentMetadata
    meta = AgentMetadata(id="x", name="x", path=Path("/tmp"))
    assert meta.inject_allowed_models is False


def test_inject_allowed_models_explicit_true():
    """Explicit True in the constructor is honored."""
    from daemon.registry import AgentMetadata
    meta = AgentMetadata(id="x", name="x", path=Path("/tmp"), inject_allowed_models=True)
    assert meta.inject_allowed_models is True
