# Test Report: Ari + Worker Phase 3 Integration Tests

**Date**: 2026-07-09T12:12:00+00:00  
**Branch**: `feature/ari-worker-agents`  
**Phase**: Phase 3 (Integration, Wiring & Cross-Agent Testing)  
**Plan**: `.agents/shared/planning/ari-worker-agents/phase3-plan.md`  

## Summary

- **Phase 3 Status**: ✅ PASS
- **New tests created**: 13 (integration tests)
- **Total Phase 1+2+3 tests**: 66 (26 Worker + 27 Ari + 13 Integration)
- **Phase 3 regressions**: 0 (1 cross-cutting assertion fixed)
- **Pre-existing failures**: 11 (unrelated to Ari/Worker, documented)

## Deliverables — All Complete

- [x] `tests/unit/test_ari_worker_integration.py` — 13 cross-agent tests, all passing
- [x] Registry validation confirms both agents discoverable
- [x] OpenSpace skill loads in Worker prompt composition
- [x] Dispatch graph verified acyclic (Ari → {leader, worker} via job tools, no cycles)
- [x] Neither agent has `instance` tools or `team_members` confirmed in tests
- [x] Autonomy model (Ari TrueAuto, Worker SemiAuto) present in prompt content

## New Test File: `tests/unit/test_ari_worker_integration.py`

### Test Classes (13 tests total)

| Class | Tests | What It Verifies |
|-------|-------|------------------|
| `TestAgentCoexistence` | 3 | Both discoverable via single discover(); no ID collisions; neither in SKIP_DIRS |
| `TestNoTeamMembers` | 2 | Ari + Worker both have empty/missing team_members (deny-by-default) |
| `TestNoInstanceTools` | 2 | Neither agent has `instance` category in tools.allow |
| `TestDispatchGraphAcyclic` | 2 | Ari has `job`, Worker does NOT; Leader's team_members excludes ari + worker |
| `TestPromptCompositionBoth` | 2 | Both agents' innate_skills load through load_agent_skills → compose_system_prompt |
| `TestAutonomyModelInPrompts` | 2 | Composed Ari prompt mentions TrueAuto; Worker prompt mentions SemiAuto |

### Critical Integration Assertions Verified
- Both agents discovered: `registry.get("ari")` and `registry.get("worker")` both not None
- Neither has `instance` tools (no spawn capability)
- Neither has `team_members` (no spawn authorization)
- Leader does NOT list `ari` or `worker` in team_members (no circular dispatch)
- Ari has `job` in tools.allow (dispatch capability)
- Worker has `mcp_openspace_execute_task` in tools.allow (OpenSpace execution)
- Worker does NOT have `job` (terminal executor — no re-dispatch)
- Both agents compose prompts successfully with their innate skills

## Quick Fixes Applied

### Fix 1: Updated job-orchestration skill holder assertion
- **File**: `tests/test_innate_skills_refactoring.py`
- **Test**: `TestRegistryInnateSkills::test_find_skill_checks_innate_first`
- **Root cause**: Ari legitimately declares `job-orchestration` in innate_skills (jober-hybrid pattern). The test expected only `["jober"]` as job-orchestration skill holders.
- **Fix**: Changed `assert agents_with_job == ["jober"]` → `assert sorted(agents_with_job) == sorted(["ari", "jober"])`
- **Commit**: `65b37bbf test: include 'ari' in expected job-orchestration skill holders`

## Regression Check Results

### Batch 1: New + Existing Agent Tests — ✅ ALL PASS
| File | Total | Passed | Failed |
|------|-------|--------|--------|
| test_ari_worker_integration.py | 13 | 13 | 0 |
| test_worker_agent.py | 26 | 26 | 0 |
| test_ari_agent.py | 27 | 27 | 0 |
| **Total** | **66** | **66** | **0** |

### Batch 2: DevOps + OpenSpace + Registry — Pre-existing failures only
| File | Total | Passed | Failed |
|------|-------|--------|--------|
| test_devops_agent.py | 62 | 58 | 4* |
| test_openspace_skill_loading.py | 17 | 17 | 0 |
| test_registry.py | 48 | 48 | 0 |
| **Total** | **127** | **123** | **4** |

*4 DevOps failures are pre-existing (broken since `baf006c5 feat: register todo innate skill for all agents`), NOT caused by Phase 3.

### Batch 3: Skill + Job Orchestration Tests — ✅ CLEAN
- test_innate_skills_refactoring.py: FIXED (see Quick Fix above)
- test_tool_filter.py: 6 pre-existing failures (mock patching issues from charter/MCP refactors)
- tests/job_queue/: 1328 passed, 1 pre-existing SQLite flaky test
- **Post-fix re-run**: 177 passed, 0 failed ✅

### Batch 4: Todo Skill Tests — ✅ ALL PASS
| File | Total | Passed | Failed |
|------|-------|--------|--------|
| test_todo_manager.py | - | ✓ | 0 |
| test_todo_tools.py | - | ✓ | 0 |
| test_todo_sse.py | - | ✓ | 0 |
| **Total** | **50** | **50** | **0** |

## Pre-Existing Failures (NOT caused by Phase 3)

### DevOps Tests (4) — Broken since commit `baf006c5`
- `test_innate_skills_is_empty_list` — DevOps now has `["todo"]`, test asserts `[]`
- `test_no_opencode_skill_content_in_system_prompt` — DevOps prompt now contains `todo` skill
- `test_apply_tool_filter_restricts_to_devops_tools` — DevOps tool list changed
- `test_load_agent_skills_empty_list_falls_through` — precondition broken

### Tool Filter Tests (6) — Broken since MCP/charter refactors
- `test_deny_filter_removes_tools`, `test_tool_without_name_gets_warning`, etc.
- Root cause: mock patching of `daemon.registry.get_registry` no longer matches implementation

### SQLite Concurrency (1) — Pre-existing flaky
- `test_concurrent_terminal_writes_only_one_succeeds` — SQLite WAL race condition

## Commits

| Commit | Description |
|--------|-------------|
| `46ea9cf6` | test: add Phase 3 cross-agent integration tests for Ari + Worker |
| `65b37bbf` | test: include 'ari' in expected job-orchestration skill holders |

## Overall Status

- **Phase 3 Integration Tests**: ✅ PASS (13/13)
- **All Ari/Worker Tests**: ✅ PASS (66/66)
- **Regression Check**: ✅ CLEAN (1 cross-cutting fix applied)
- **Pre-existing Failures**: 11 (documented, out of scope)
- **job-orchestration skill.md fix**: ✅ Verified no regressions
