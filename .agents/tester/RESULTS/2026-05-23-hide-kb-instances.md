# Test Report: Hide KB Instances Feature
Date: 2026-05-23
Branch: feature/hide-kb-instances (commits 8bef626, d083bbb, 7a036eb)
Sessions: backend-tests, frontend-tests, ensure-md

## Summary
- **Unit Tests (Backend)**: 20/20 PASS (5 existing + 15 new)
- **Unit Tests (Frontend)**: 658/658 PASS (616 existing + 42 new)
- **ensure.md**: PASS — dev.sh stable 30s+
- **Quick Fixes**: 0 (all tests pass as-is)
- **Overall Status**: ✅ READY

---

## Backend Unit Tests

### Existing Tests (5/5 PASS)
All existing `list_instances` tests pass with updated assertions (`exclude_kb=True`):
- `test_list_instances` ✅
- `test_list_instances_no_project_id_filter` ✅
- `test_list_instances_filter_by_project_id` ✅
- `test_list_instances_filter_by_nonexistent_project_id` ✅
- `test_list_instances_project_id_with_status_filter` ✅

### New Tests: `tests/unit/test_hide_kb_instances.py` (15/15 PASS)
Commit: `76741f3`

**Repository-Level Tests (6 tests)**:
| Test | Description | Status |
|------|-------------|--------|
| `test_kb_agent_ids_constant` | KB_AGENT_IDS == frozenset(["experiencer", "kb-importer"]) | ✅ |
| `test_list_excludes_kb_by_default` | Only regular instances returned | ✅ |
| `test_list_includes_kb_when_excluded_false` | All instances returned with exclude_kb=False | ✅ |
| `test_list_kb_filter_with_project_id` | Combined filtering works | ✅ |
| `test_list_kb_filter_pagination` | limit/offset work with exclude_kb | ✅ |
| `test_list_kb_filter_status_combined` | KB exclusion + status filter | ✅ |

**API Router Tests (5 tests)**:
| Test | Description | Status |
|------|-------------|--------|
| `test_list_instances_exclude_kb_default` | Default → exclude_kb=True | ✅ |
| `test_list_instances_exclude_kb_false` | ?exclude_kb=false → exclude_kb=False | ✅ |
| `test_list_instances_exclude_kb_true_explicit` | ?exclude_kb=true → exclude_kb=True | ✅ |
| `test_list_instances_exclude_kb_with_project_id` | Combined with project_id | ✅ |
| `test_list_instances_exclude_kb_false_with_project_id` | Combined project_id + exclude_kb=false | ✅ |

**SSE agent_id Tests (4 tests)**:
| Test | Description | Status |
|------|-------------|--------|
| `test_stream_status_change_includes_agent_id` | With agent_id → event has agent_id | ✅ |
| `test_stream_status_change_without_agent_id` | No agent_id → no agent_id in event | ✅ |
| `test_stream_status_change_with_none_agent_id` | agent_id=None → no agent_id in event | ✅ |
| `test_stream_status_change_different_statuses` | agent_id included for all status values | ✅ |

---

## Frontend Unit Tests

### Existing Tests: 616/616 PASS (no regressions)

### New Tests (42 new tests across 4 files)
Commit: `ae36c80`

**instance.service.spec.ts (Extended — 7 new tests)**:
- showKb signal defaults to false ✅
- toggleKb flips false→true ✅
- toggleKb flips true→false ✅
- Multiple toggles work ✅
- loadInstances passes excludeKb=true when showKb=false ✅
- loadInstances passes excludeKb=false when showKb=true ✅
- State changes respected between calls ✅

**api.service.spec.ts (Extended — 3 new tests)**:
- Sends exclude_kb=false query param ✅
- Defaults exclude_kb to true ✅
- Accepts excludeKb parameter ✅

**sse.service.spec.ts (Extended — 6 new tests)**:
- Signal starts null ✅
- Parses agent_id from status_change event ✅
- Handles event without agent_id ✅
- Adds status_change to events array ✅
- Parses KB agent IDs correctly ✅
- clearEvents clears statusChange signal ✅

**instance-list.component.spec.ts (New — 258 lines)**:
- onToggleKb calls toggleKb() ✅
- onToggleKb calls loadInstances() ✅
- onToggleKb calls both in correct order ✅
- onToggleKb toggles showKb signal ✅
- Handles multiple rapid toggles ✅
- Plus existing component tests (instanceTree, expandedInstances, etc.)

---

## ensure.md Validation: ✅ PASS
- dev.sh ran for 30 seconds without crash (exit code 124 = timeout)
- All components initialized: Ensemble v0.3.0, RAG auto-test, WorkerPool, MCP warmup

---

## Feature Coverage Summary

| Feature Area | Backend Tested | Frontend Tested |
|-------------|---------------|-----------------|
| `exclude_kb` query param default | ✅ | ✅ |
| `exclude_kb=true` explicit | ✅ | ✅ |
| `exclude_kb=false` explicit | ✅ | ✅ |
| Repository KB filtering | ✅ | — |
| Combined filters (project_id + exclude_kb) | ✅ | — |
| Pagination with KB filter | ✅ | — |
| SSE `agent_id` in payload | ✅ | ✅ |
| SSE without `agent_id` | ✅ | ✅ |
| KB toggle signal (showKb) | — | ✅ |
| KB toggle reload (loadInstances) | — | ✅ |
| SSE client-side KB filtering | — | ✅ |
| KB toggle UI (checkbox) | — | ✅ |

---

## Documentation Updated
- [x] RESULTS/2026-05-23-hide-kb-instances.md — This report
- [ ] PACKS.md — No new packs created (tests added to existing files)
- [ ] MOCK_TESTS.md — No mock tests needed
- [ ] LESSONS/ — No issues found

## Code Changes Summary
- `tests/unit/test_hide_kb_instances.py` — NEW: 15 backend tests (commit 76741f3)
- `frontend/src/app/**/*.spec.ts` — EXTENDED: 42 frontend tests (commit ae36c80)

---

### Overall Status: ✅ READY (678/678 tests pass, dev.sh stable, no regressions)
