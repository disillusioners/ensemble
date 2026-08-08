# Integration Test Pattern for `_apply_tool_filter`

**Date:** 2026-08-08
**Context:** Phase 5 — System Log Tools test suite
**Problem solved:** How to integration-test tools surviving the factory + filter path without spinning up a real daemon

## The Pattern

When testing that a tool category survives `_apply_tool_filter` (used by `create_instance_tools`), the real function signature is:

```python
_apply_tool_filter(tools, agent_id, mcp_tool_names=None, version_tag=None)
```

It reads the agent's `meta.json` config to determine `tools.allow`. To test this hermetically, **do NOT** call the real function with a real agent_id — instead, mock at the registry layer.

```python
from unittest.mock import patch, MagicMock

def test_tools_visible_after_apply_tool_filter():
    from daemon.tools.instance import create_instance_tools, _apply_tool_filter

    manager = MagicMock()
    manager._instance_repository = MagicMock()
    manager._instance_repository.get = MagicMock(return_value=None)

    # Build all tools via the real factory
    all_tools = create_instance_tools(manager, "test-instance-id", "developer")

    # Mock the registry to return a config that allows "system-log"
    mock_config = MagicMock()
    mock_config.tools = MagicMock()
    mock_config.tools.allow = ["system-log"]

    with patch("daemon.registry.get_registry") as mock_registry:
        mock_registry.return_value.get_agent_config.return_value = mock_config
        with patch("daemon.tools._tool_registry.list_tools_by_category") as mock_list:
            mock_list.return_value = [t for t in all_tools if getattr(t, "_tool_category", None) == "system-log"]
            filtered = _apply_tool_filter(all_tools, "developer")

    tool_names = [t.name for t in filtered]
    assert "ens_system_log_list" in tool_names
    # ... etc
```

## Why This Works

1. `create_instance_tools` returns the unfiltered list (all categories the factory emits)
2. `_apply_tool_filter` queries the agent's config (`get_agent_config`) for `tools.allow`
3. `list_tools_by_category` provides the actual tool list per category
4. By mocking both at the registry boundary, we control what `_apply_tool_filter` "sees" without needing a real daemon/agent

## Reference Tests to Mirror

This pattern is already in use in:
- `tests/unit/test_wanderer_agent.py` — wanderer agent integration
- `tests/unit/tools/test_version_tag_tool_resolution.py` — version tag resolution

When writing future integration tests for new tool categories, follow this exact mock structure.

## Gotcha

The plan-level pseudocode often uses simplified signatures (e.g., `_apply_tool_filter(tools, allow={...})`) because authors don't always verify the real signature. **Always inspect the actual function signature first** (grep for `def _apply_tool_filter` in `daemon/tools/instance.py`). This is the W6 reviewer fix that came out of the system-log-tools feature.