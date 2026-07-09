# Pre-existing Bug: `_apply_tool_filter` Doesn't Remove Disallowed Tools

## Discovered
2026-07-09 during coder agent regression testing.

## Symptom
Tests that call `_apply_tool_filter(tools, agent_id)` and assert that disallowed tools (like `spawn_instance`, `external_opencode_init_session`) are filtered out FAIL because those tools leak through.

## Root Cause
The `_apply_tool_filter` function in `daemon/tools/instance.py` does not properly remove tools that are outside the agent's `tools.allow` list. This affects ALL agents, not just coder — the same failure pattern appears in `test_devops_agent.py:595` and `test_tool_filter.py`.

## Affected Tests
- `tests/unit/test_coder_agent.py` — 1 test (worked around by mocking `registry.get_resolved` instead of `registry.get`)
- `tests/unit/test_devops_agent.py` — 3 tests
- `tests/test_tool_filter.py` — 6 tests

## Impact
Agents may receive tools they shouldn't have. However, since tool resolution at runtime uses `resolve_tool_filter` (which works correctly), the practical impact may be limited. Still, this is a correctness bug.

## Workaround (Applied in test_coder_agent.py)
Mock `registry.get_resolved` instead of `registry.get` when testing `_apply_tool_filter`.

## Recommendation
This should be fixed in a separate PR. The fix likely involves correcting how `_apply_tool_filter` looks up the agent metadata (using `get_resolved` which merges innate_skills expansion, rather than raw `get`).

## Status
Pre-existing — tracked for future fix. NOT introduced by coder agent.
