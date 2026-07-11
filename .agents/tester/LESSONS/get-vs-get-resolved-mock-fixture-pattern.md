# 2026-07-11 — Critical Mock Fixture Pattern: get() vs get_resolved()

**Issue**: Repo-wide test breakage after `daemon/registry.py` renamed `get(agent_id)` → `get_resolved(agent_id)` for skill-evolution work. Multiple test files still mock the old API.

**Symptom**: MagicMock returned from `mock_registry.return_value.get.return_value = agent_metadata`. Production code's set operations against MagicMocks silently no-op'd, hiding bugs. The most insidious: `test_explicit_deny_still_wins_over_innate_skill_grant` — deny logic was completely masked.

**Files affected** (commit `9446f30c`):
- `tests/unit/tools/test_inner_soul_redirect.py`
- `tests/unit/test_context7_builtin.py`
- `tests/test_tool_filter.py`
- `tests/unit/test_worker_agent.py`
- `tests/unit/test_devops_agent.py`
- `tests/unit/test_openspace_skill_loading.py`

**Rule for future AgentRegistry changes**:
- When renaming or aliasing `AgentRegistry` methods, add a deprecation alias OR grep + update all test mock fixtures in a single follow-up PR.
- Tests should use `get_resolved` not `get` when mocking, to match production code paths.
- For fast detection: prefer `spec=AgentRegistry` on the mock to catch attribute mismatches immediately rather than silent MagicMock no-ops.

**Detection**: When a registry-related test passes when it shouldn't (e.g. a deny-logic test passes with positive assertion), suspect silent MagicMock fallthrough. Run with `spec=AgentRegistry` to surface.
