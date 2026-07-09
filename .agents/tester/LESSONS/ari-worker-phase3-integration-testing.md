# Phase 3 Ari + Worker Integration Testing

**Date**: 2026-07-09  
**Branch**: `feature/ari-worker-agents`  
**Phase**: Phase 3 — Integration, Wiring & Cross-Agent Testing

## Key Findings

### 1. Jober-Hybrid Pattern Affects Innate Skill Assertions
When adding a new agent with the jober-hybrid pattern (has `job-orchestration` in innate_skills), existing tests that assert which agents hold specific innate skills will break. Specifically `test_innate_skills_refactoring.py::test_find_skill_checks_innate_first` expected only `["jober"]` but now needs `["ari", "jober"]`.

**Lesson**: When adding agents with innate_skills that overlap with existing agents, grep for tests asserting on those specific skill-to-agent mappings.

### 2. Full Test Suite Too Large for Single Session
The full test suite has 3869 tests (unit only). Running it via a single opencode session times out (>10 min). 

**Solution**: Run tests in targeted batches with `timeout 120` enforcement per batch. Group by:
- New + related agent tests
- Pre-existing agent tests (expect failures)
- Skill/job tests
- Infrastructure tests

### 3. DevOps Tests Are Pre-Existing Broken (4 failures)
Commit `baf006c5 feat: register todo innate skill for all agents` changed devops meta.json to have `["todo"]` innate skills instead of `[]`. The test_devops_agent.py tests were NOT updated and still assert `[]`. These failures predate the Ari/Worker work by ~13 hours.

**Action**: DevOps tests need updating to reflect the todo innate skill registration. Out of scope for Phase 3.

### 4. Tool Filter Tests Pre-Existing Broken (6 failures)
The test_tool_filter.py tests have mock patching issues from MCP/charter refactors (`9197e726` / `dc4f5883` / `6290e1d0`). Mock patching of `daemon.registry.get_registry` / `daemon.tools.instance.list_tools_by_category` no longer matches the implementation.

**Action**: These mocks need updating to match the refactored registry implementation. Out of scope for Phase 3.

### 5. Integration Test Pattern Works Well
The `test_ari_worker_integration.py` follows the established pattern from `test_devops_agent.py`:
- Class-per-concern organization
- Registry discovery as the primary validation mechanism
- Filesystem-based assertions (no live server needed)
- `Path(__file__).parent.parent.parent / "agents" / <name>` path constants
- Deferred imports inside test methods

This pattern is robust and reliable for cross-agent validation.
