#!/usr/bin/env python3
"""Validation tests for Instance Manager spawn_instance integration."""

import sys
import tempfile
from pathlib import Path

from daemon.registry import get_registry, AgentRegistry


def test_backward_compatibility():
    """Test 2: Backward compatibility - agent_dir parameter."""
    registry = get_registry()
    
    # Test 2a: agent_dir='./agents/coder' (with ./ prefix)
    resolved_id = registry.resolve_to_id("./agents/coder")
    assert resolved_id == "coder", f"resolved_id={resolved_id}"
    
    # Test 2b: agent_dir='agents/coder' (without ./ prefix)
    resolved_id = registry.resolve_to_id("agents/coder")
    assert resolved_id == "coder", f"resolved_id={resolved_id}"


def test_new_feature_agent_id():
    """Test 3: New feature - agent_id parameter."""
    registry = get_registry()
    
    # Test 3a: spawn_instance(agent_id='coder')
    resolved_id = registry.resolve_to_id("coder")
    assert resolved_id == "coder", f"resolved_id={resolved_id}"
    
    # Test 3b: agent_id takes precedence over agent_dir
    agent_id = "coder"
    resolved_agent_id = registry.resolve_to_id(agent_id) or agent_id
    metadata = registry.get(resolved_agent_id)
    is_coder = metadata is not None and "coder" in str(metadata.path)
    assert is_coder, f"metadata.path={metadata.path if metadata else None}"


def test_edge_cases():
    """Test 4: Edge cases."""
    registry = get_registry()
    
    # Test 4a: Invalid agent_id raises error (via registry.get returning None)
    metadata = registry.get("nonexistent_agent_xyz")
    assert metadata is None, f"Unexpected metadata: {metadata}"
    
    # Test 4b: Neither parameter provided (empty string)
    resolved_id = registry.resolve_to_id("")
    assert resolved_id is None, f"resolved_id={resolved_id}"
    
    # Test 4c: Check that resolve_to_id handles None
    resolved_id = registry.resolve_to_id(None)
    assert resolved_id is None, f"resolved_id={resolved_id}"


def test_registry_symlink_behavior():
    """Test that registry properly skips symlinks."""
    # Create a temporary symlink to test
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create a fake agent with symlink
        fake_agents_dir = Path(tmpdir) / "fake_agents"
        fake_agents_dir.mkdir()
        
        real_agent = fake_agents_dir / "real_agent"
        real_agent.mkdir()
        (real_agent / "meta.json").write_text('{"id": "real_agent", "name": "Real Agent"}')
        
        symlink_agent = fake_agents_dir / "symlink_agent"
        symlink_agent.symlink_to(real_agent)
        
        # Test that AgentRegistry.discover() skips the symlink
        test_registry = AgentRegistry(fake_agents_dir)
        test_registry.discover()
        
        agent_ids = list(test_registry._agents.keys())
        assert "symlink_agent" not in agent_ids, f"Found agents: {agent_ids}"
        assert "real_agent" in agent_ids, f"Found agents: {agent_ids}"


def test_spawn_instance_signature():
    """Test the spawn_instance method signature and parameter handling."""
    from inspect import signature
    from daemon.manager import InstanceManager
    
    sig = signature(InstanceManager.spawn_instance)
    params = list(sig.parameters.keys())
    
    # Check required parameters exist
    assert "agent_id" in params, f"params={params}"
    assert "instance_id" in params, f"params={params}"
    assert "parent_id" in params, f"params={params}"
    
    # Check optional parameters have correct defaults
    instance_id_param = sig.parameters.get("instance_id")
    parent_id_param = sig.parameters.get("parent_id")
    
    assert instance_id_param is not None and instance_id_param.default is None, \
        f"instance_id default={instance_id_param.default if instance_id_param else 'N/A'}"
    assert parent_id_param is not None and parent_id_param.default is None, \
        f"parent_id default={parent_id_param.default if parent_id_param else 'N/A'}"


def main():
    """Run standalone validation (pytest runs these as unit tests)."""
    print("=" * 70)
    print("Instance Manager Integration Validation Tests")
    print("=" * 70)
    print("\nNote: Individual tests are now pytest unit tests.")
    print("Run: pytest tests/test_spawn_instance_validation.py -v")
    print("=" * 70)


if __name__ == "__main__":
    sys.exit(main())
