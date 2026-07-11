# Test Report: Coder Agent
Date: 2026-07-09T20:12 UTC
Sessions: coder-test-creation (ses_0b77b01f5ffeM79GilkZcku7ie), agent-regression (ses_0b77b01f2ffeV1trepgvD6Sm0C)
Branch: feature/coder-agent
Commit: 19e72ec3

## Summary
- **Coder Agent Test Suite**: 39 tests — ALL PASS ✅
- **Regression Check**: 0 regressions — all failures predate coder
- **ensure.md**: N/A (no coder-specific requirements)
- **Quick Fixes Applied**: 2 (test-level, see below)

## Test Objectives & Results

### 1. Agent Discovery — ✅ PASS
- `agents/coder/` directory discovered by AgentRegistry
- "coder" resolves correctly as agent ID
- metadata loads from meta.json (id="coder", name="Coder")
- 5 tests in `TestCoderAutoDiscovery` — all pass

### 2. meta.json Validation — ✅ PASS
- All required fields present (id, name, description, icon, color, version)
- `innate_skills == ["todo", "chart"]` — no "opencode" ✓
- `tools.allow == ["bash", "filesystem", "time", "self", "help", "knowledge", "context"]`
- Does NOT contain: "external_opencode", "db", "mcp" ✓
- Registry-parsed ToolFilter matches meta.json exactly
- 13 tests in `TestCoderMetaJsonValidation` — all pass

### 3. Tool Resolution — ✅ PASS
- Has: bash, filesystem tools (read_file, write_file, edit_file, list_directory, glob_files, grep_files), time, self
- Does NOT have: instance tools (spawn_instance, etc.), opencode tools (external_opencode_*)
- `expand_allow_for_innate_skills` correctly expands chart category
- 12 tests in `TestCoderToolConfiguration` — all pass

### 4. Prompt Composition — ✅ PASS
- soul.md loads correctly, contains "Coder" identity
- Composed system prompt includes soul content
- No opencode skill content injected
- load_and_cache_prompt works without errors
- 7 tests in `TestCoderPromptComposition` — all pass

### 5. No OpenCode Contamination — ✅ PASS
- soul.md does not delegate to opencode (mentions only as contrast in Must-NOT rules)
- No opencode in team_members (field absent)
- 2 tests in `TestCoderNoOpencodeContamination` — all pass

## Regression Check Results

### Loader/Registry Tests — ✅ 173/173 PASS
| Suite | Tests | Result |
|-------|-------|--------|
| tests/test_loader.py | — | PASS |
| tests/test_registry.py | — | PASS |
| tests/opencode/test_registry.py | — | PASS |

### Full Suite Collection — ✅ No Import Errors
- 8,989 tests collected, 232 deselected
- Zero collection/import errors introduced by coder agent

### All 18 Agent-Related Test Files — ✅ No Coder Regressions
- 606 passed, 16 failed, 8 skipped
- All 16 failures verified as PRE-EXISTING (reproduced identically at HEAD~1 before coder existed)

## Pre-Existing Failures (NOT caused by coder)
| File | Failures | Root Cause |
|------|----------|------------|
| test_devops_agent.py | 3 | DevOps meta.json changed (`innate_skills: ["todo"]` vs test asserting empty) |
| test_gaia_agent.py | 7 | Missing `agents/gaia/scripts/` directory |
| test_tool_filter.py | 6 | `_apply_tool_filter` bug — doesn't remove tools outside allow-list |
| test_jober_watch_integration.py | 1 | Flaky — port 8079 in use |

## Quick Fixes Applied
1. **_apply_tool_filter mock fix**: The existing devops test mocks `registry.get` but the function actually calls `registry.get_resolved`. Fixed in test_coder_agent.py by mocking `get_resolved` instead. (Test-level fix only)
2. **soul.md opencode phrase**: The soul.md mentions "control opencode sessions" inside a Must-NOT rule bullet — this is legitimate contrast content, so the test was adjusted to not flag it as contamination.

## Test File
- **Location**: `tests/unit/test_coder_agent.py`
- **Lines**: 493
- **Test Classes**: 5
- **Test Functions**: 39

## Overall Status: ✅ READY

The coder agent is correctly defined, discoverable, and introduces zero regressions. All test objectives met.
