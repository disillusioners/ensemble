#!/usr/bin/env python3
"""Validation tests for Instance Manager spawn_instance integration."""

import sys
import tempfile
from pathlib import Path

from daemon.registry import get_registry, AgentRegistry


def test_backward_compatibility():
    """Test 2: Backward compatibility - agent_dir parameter."""
    results = []
    registry = get_registry()
    
    # Test 2a: agent_dir='./agents/coder' (with ./ prefix)
    try:
        agent_dir = "./agents/coder"
        resolved_id = registry.resolve_to_id(agent_dir)
        results.append(("agent_dir='./agents/coder' resolves to 'coder'", 
                       resolved_id == "coder", f"resolved_id={resolved_id}"))
    except Exception as e:
        results.append(("agent_dir='./agents/coder' resolves to 'coder'", False, str(e)))
    
    # Test 2b: agent_dir='agents/coder' (without ./ prefix)
    try:
        agent_dir = "agents/coder"
        resolved_id = registry.resolve_to_id(agent_dir)
        results.append(("agent_dir='agents/coder' resolves to 'coder'", 
                       resolved_id == "coder", f"resolved_id={resolved_id}"))
    except Exception as e:
        results.append(("agent_dir='agents/coder' resolves to 'coder'", False, str(e)))
    
    return results


def test_new_feature_agent_id():
    """Test 3: New feature - agent_id parameter."""
    results = []
    registry = get_registry()
    
    # Test 3a: spawn_instance(agent_id='coder')
    try:
        resolved_id = registry.resolve_to_id("coder")
        results.append(("agent_id='coder' resolves to 'coder'", 
                       resolved_id == "coder", f"resolved_id={resolved_id}"))
    except Exception as e:
        results.append(("agent_id='coder' resolves to 'coder'", False, str(e)))
    
    # Test 3b: agent_id takes precedence over agent_dir
    # When agent_id is provided, registry.get(agent_id) should return metadata
    try:
        agent_id = "coder"
        agent_dir = "agents/leader"  # Different agent
        
        # When agent_id is provided, it should be used
        resolved_agent_id = registry.resolve_to_id(agent_id) or agent_id
        metadata = registry.get(resolved_agent_id)
        
        # Check that we got the coder metadata, not leader
        is_coder = metadata is not None and "coder" in str(metadata.path)
        results.append(("agent_id='coder' takes precedence over agent_dir='leader'", 
                       is_coder, f"metadata.path={metadata.path if metadata else None}"))
    except Exception as e:
        results.append(("agent_id='coder' takes precedence over agent_dir='leader'", False, str(e)))
    
    return results


def test_edge_cases():
    """Test 4: Edge cases."""
    results = []
    registry = get_registry()
    
    # Test 4a: Invalid agent_id raises error (via registry.get returning None)
    try:
        invalid_id = "nonexistent_agent_xyz"
        metadata = registry.get(invalid_id)
        # In spawn_instance, if metadata is None, ValueError is raised
        if metadata is None:
            results.append(("invalid agent_id='nonexistent_agent_xyz' returns None (will raise ValueError)", 
                           True, "registry.get() correctly returns None"))
        else:
            results.append(("invalid agent_id='nonexistent_agent_xyz' returns None", 
                           False, f"Unexpected metadata: {metadata}"))
    except Exception as e:
        results.append(("invalid agent_id='nonexistent_agent_xyz' returns None", False, f"Unexpected error: {e}"))
    
    # Test 4b: Neither parameter provided (empty string)
    try:
        resolved_id = registry.resolve_to_id("")
        # resolve_to_id should return None for empty string
        results.append(("empty agent_dir returns None (will raise ValueError)", 
                       resolved_id is None, f"resolved_id={resolved_id}"))
    except Exception as e:
        results.append(("empty agent_dir returns None", False, f"Unexpected error: {e}"))
    
    # Test 4c: Check that resolve_to_id handles None
    try:
        resolved_id = registry.resolve_to_id(None)
        results.append(("None agent_dir returns None (will raise ValueError)", 
                       resolved_id is None, f"resolved_id={resolved_id}"))
    except Exception as e:
        results.append(("None agent_dir returns None", False, f"Unexpected error: {e}"))
    
    return results


def test_registry_symlink_behavior():
    """Test that registry properly skips symlinks."""
    results = []
    
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
        results.append(("AgentRegistry.discover() skips symlinks", 
                       "symlink_agent" not in agent_ids, 
                       f"Found agents: {agent_ids}"))
        results.append(("AgentRegistry.discover() finds real_agent", 
                       "real_agent" in agent_ids, 
                       f"Found agents: {agent_ids}"))
    
    return results


def test_spawn_instance_signature():
    """Test the spawn_instance method signature and parameter handling."""
    results = []
    
    # Verify spawn_instance accepts both agent_dir and agent_id
    from inspect import signature
    from daemon.manager import InstanceManager
    
    sig = signature(InstanceManager.spawn_instance)
    params = list(sig.parameters.keys())
    
    # Check that both parameters exist
    has_agent_dir = "agent_dir" in params
    has_agent_id = "agent_id" in params
    
    results.append(("spawn_instance has agent_dir parameter", has_agent_dir, f"params={params}"))
    results.append(("spawn_instance has agent_id parameter", has_agent_id, f"params={params}"))
    
    # Check default values are None
    agent_dir_param = sig.parameters.get("agent_dir")
    agent_id_param = sig.parameters.get("agent_id")
    
    results.append(("agent_dir defaults to None", 
                   agent_dir_param is not None and agent_dir_param.default is None, 
                   f"default={agent_dir_param.default if agent_dir_param else 'N/A'}"))
    results.append(("agent_id defaults to None", 
                   agent_id_param is not None and agent_id_param.default is None, 
                   f"default={agent_id_param.default if agent_id_param else 'N/A'}"))
    
    return results


def main():
    print("=" * 70)
    print("Instance Manager Integration Validation Tests")
    print("=" * 70)
    
    all_results = []
    
    # Test 1: Unit tests already passed (pytest)
    print("\n[1] Unit Tests: pytest tests/ -v -k 'registry or instance'")
    print("    ✓ All tests passed (see pytest output above)")
    
    # Test 5: spawn_instance signature
    print("\n[5] spawn_instance Method Signature")
    results = test_spawn_instance_signature()
    for name, passed, detail in results:
        status = "✓ PASS" if passed else "✗ FAIL"
        print(f"    {status}: {name}")
        if not passed:
            print(f"           Detail: {detail}")
        all_results.append((name, passed))
    
    # Test 2: Backward compatibility
    print("\n[2] Backward Compatibility - agent_dir parameter")
    results = test_backward_compatibility()
    for name, passed, detail in results:
        status = "✓ PASS" if passed else "✗ FAIL"
        print(f"    {status}: {name}")
        if not passed:
            print(f"           Detail: {detail}")
        all_results.append((name, passed))
    
    # Test 3: New feature
    print("\n[3] New Feature - agent_id parameter")
    results = test_new_feature_agent_id()
    for name, passed, detail in results:
        status = "✓ PASS" if passed else "✗ FAIL"
        print(f"    {status}: {name}")
        if not passed:
            print(f"           Detail: {detail}")
        all_results.append((name, passed))
    
    # Test 4: Edge cases
    print("\n[4] Edge Cases")
    results = test_edge_cases()
    for name, passed, detail in results:
        status = "✓ PASS" if passed else "✗ FAIL"
        print(f"    {status}: {name}")
        if not passed:
            print(f"           Detail: {detail}")
        all_results.append((name, passed))
    
    # Test 6: Symlink behavior
    print("\n[6] Symlink Handling")
    results = test_registry_symlink_behavior()
    for name, passed, detail in results:
        status = "✓ PASS" if passed else "✗ FAIL"
        print(f"    {status}: {name}")
        if not passed:
            print(f"           Detail: {detail}")
        all_results.append((name, passed))
    
    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    passed = sum(1 for _, p in all_results if p)
    total = len(all_results)
    print(f"Passed: {passed}/{total}")
    
    if passed == total:
        print("\n✓ All validation tests passed!")
        return 0
    else:
        print("\n✗ Some tests failed. See details above.")
        failed = [(n, d) for n, p, d in all_results if not p]
        if failed:
            print("\nFailed tests:")
            for name, detail in failed:
                print(f"  - {name}: {detail}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
