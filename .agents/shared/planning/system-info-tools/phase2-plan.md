# Phase 2: Wiring + Gaia Config + Tests

## Objective
Wire the `create_system_tools()` factory into `create_instance_tools()`, update
Gaia's `meta.json` to grant the `system` category (and remove the incorrect
`context` entry), and write comprehensive unit tests for the new tools.

## Coupling
- **Depends on**: Phase 1 (`system.py` module)
- **Coupling type**: tight
- **Shared files with other phases**: imports `create_system_tools` from `daemon/tools/system.py`
- **Shared APIs/interfaces**: calls `create_system_tools(manager, current_instance_id)`
- **Why this coupling**: Phase 2 calls the factory by name and asserts on the
  exact tool names + category. Must wait for Phase 1 to be reviewed/merged.

## Context
- Phase 1 delivered `daemon/tools/system.py` with `create_system_tools()`
  returning `[system_env, system_config, system_health]`
- The wiring point in `create_instance_tools()` is well-established: system
  tools should be added **after context tools** and **before MCP tools**
  (following the layered assembly pattern, lines ~1019–1020 of `instance.py`)

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Add import to `instance.py` | `from .system import create_system_tools` alongside other imports (line ~115) | `daemon/tools/instance.py` |
| 2 | Add factory call in `create_instance_tools()` | Insert after `context_tool_list` extension (~line 1020), before MCP loading (~line 1025). Pattern: `system_tool_list = create_system_tools(manager, current_instance_id); tools.extend(system_tool_list)` | `daemon/tools/instance.py` |
| 3 | Update Gaia `meta.json` `tools.allow` | Change from `["bash", "filesystem", "help", "mcp", "context"]` to `["bash", "filesystem", "help", "mcp", "system"]` | `agents/gaia/meta.json` |
| 4 | Fix Gaia test assertion | Update `test_gaia_tool_filter_config_parsed_correctly` (line 478) to expect `["bash", "filesystem", "help", "mcp", "system"]` | `tests/unit/test_gaia_agent.py` |
| 5 | Create test file | `tests/unit/tools/test_system_tools.py` — factory shape, each tool behavior, masking, section filter | `tests/unit/tools/test_system_tools.py` (new) |
| 6 | Run full test suite | Verify 0 regressions against PostgreSQL | — |

## Key Files
- `daemon/tools/instance.py` (MODIFY) — import + factory call in assembly
- `agents/gaia/meta.json` (MODIFY) — `tools.allow` swap
- `tests/unit/test_gaia_agent.py` (MODIFY) — assertion update
- `tests/unit/tools/test_system_tools.py` (NEW) — comprehensive tests
- `daemon/tools/system.py` (from Phase 1) — imported by instance.py and tests

## Wiring Detail (Task 2)

The insertion point in `create_instance_tools()`:

```python
    # ── Context tools (list/read shared context directory) ──
    # [...existing code...]
    context_tool_list = create_context_tools(manager, current_instance_id)
    tools.extend(context_tool_list)

    # ── System info tools (read-only env/config/health inspection) ──
    # Always available — read-only system introspection for Gaia (devops mode)
    # and other agents. No mutation, secrets masked.
    system_tool_list = create_system_tools(manager, current_instance_id)
    tools.extend(system_tool_list)

    # ── MCP tools: load BEFORE creating help tool so we have the names ──
    # [...existing code...]
```

## Test Plan (`tests/unit/tools/test_system_tools.py`)

Follow the pattern of `tests/unit/tools/test_context_tools.py`:

### Fixtures
```python
@pytest.fixture
def manager_mock():
    """Mock manager with config + ensemble_config."""
    m = MagicMock()
    m.config = Config()  # real Config with defaults
    m.ensemble_config = EnsembleConfig()  # real EnsembleConfig
    return m

@pytest.fixture
def tools(manager_mock):
    return create_system_tools(manager_mock, "test-instance-id")

@pytest.fixture
def tool_by_name(tools):
    return {t.name: t for t in tools}
```

### Test Classes

| Class | Tests | Description |
|-------|-------|-------------|
| `TestSystemToolsFactory` | `test_factory_returns_three_tools` | Asserts 3 tools: `system_env`, `system_config`, `system_health` |
| | `test_tools_have_correct_category` | All tools have `_tool_category == "system"` |
| `TestSystemEnv` | `test_returns_tracked_env_vars` | Returns JSON dict of known env var names |
| | `test_masks_secrets_by_default` | `OPENAI_API_KEY` if set → `"****<last4>"`, never plaintext |
| | `test_includes_secrets_when_requested` | `include_secrets=True` shows full value |
| | `test_prefix_filter` | `prefix="POSTGRES_"` only returns PG vars |
| | `test_no_full_environ_dump` | Random unrelated env var (e.g. `PATH`) not included |
| | `test_empty_values_shown` | Unset tracked var → value is `null` or empty string (not omitted) |
| `TestSystemConfig` | `test_returns_full_config` | Returns JSON with all sections |
| | `test_masks_llm_api_key` | `config.llm.api_key` masked in output |
| | `test_section_filter_llm` | `section="llm"` returns only LLM config |
| | `test_section_filter_invalid` | `section="nonexistent"` → error with valid section list |
| | `test_all_sections_present` | Output contains keys: llm, daemon, limits, persistence, agents, queue, compaction, services, job_system, mcp_pool |
| `TestSystemHealth` | `test_returns_version` | `version` field present and matches `__version__` |
| | `test_returns_database` | `database` field is `"sqlite"` or `"postgres"` |
| | `test_returns_rag_enabled` | `rag_enabled` field is boolean |
| | `test_returns_platform_info` | `platform` and `python_version` fields present |
| `TestSecretMasking` | `test_mask_long_value` | `"sk-abcdef123456"` → `"****3456"` |
| | `test_mask_short_value` | `"abc"` → `"****"` (no partial leak) |
| | `test_mask_empty` | `""` → `"****"` |
| | `test_mask_none` | `None` → `"****"` |

## Constraints
- **Gaia `context` removal**: The `context` tool was incorrectly added to
  Gaia (Gaia doesn't use context_key-based shared context). Remove it and
  replace with `system`. This also fixes the test mismatch noted in exploration.
- **System tools always available**: Unlike RAG tools, system tools are NOT
  gated behind `is_rag_enabled()`. They are always assembled in
  `create_instance_tools()` — agents get access via their `tools.allow` list.
- **No changes to other agents' meta.json**: Only Gaia's meta.json is modified.
  Other agents (devops, etc.) can opt-in by adding `"system"` to their allow list.
- **Test against PostgreSQL**: Follow the project constraint — run tests with
  PG as the primary DB. The system tools themselves are DB-agnostic (read-only
  config inspection), but the test suite must not regress.

## Deliverables
- [ ] `daemon/tools/instance.py` imports and calls `create_system_tools()`
- [ ] `agents/gaia/meta.json` has `"system"` in `tools.allow`, `"context"` removed
- [ ] `tests/unit/test_gaia_agent.py` assertion updated to match
- [ ] `tests/unit/tools/test_system_tools.py` created with all test classes
- [ ] All new tests pass
- [ ] Full test suite passes with 0 regressions
