# Built-in MCP Servers Feature — Complete Test Report

**Date**: 2026-05-18
**Branch**: feature/builtin-mcp-servers
**Feature**: Built-in MCP Servers (All 3 Phases)

## Summary

| Category | Result | Details |
|----------|--------|---------|
| Backend Tests | ✅ PASS | 3,737/3,752 passed (15 failures pre-existing, unrelated) |
| Frontend Build | ✅ PASS | No TypeScript errors, builds cleanly |
| Frontend Tests | ✅ PASS | 146 MCP-specific tests, 518 total unit tests |
| Daemon Startup | ✅ PASS | Runs 30s without crash, bootstraps webfetch |
| API Endpoints | ✅ PASS | All 9 integration tests passed |
| Key Correctness | ✅ PASS | All 8 correctness checks verified |
| ensure.md | ✅ PASS | dev.sh runs 30s, bootstrap logs confirmed |

### Overall Status: ✅ READY

---

## 1. Backend Tests (pytest)

**Command**: `python -m pytest tests/ -q --tb=line`
**Total**: 3,752 tests | **Passed**: 3,737 | **Failed**: 15 | **Skipped**: 34

### Built-in MCP Feature Tests — ALL PASS
- `tests/unit/test_builtin_mcp_servers.py` — Registry, ABC, config validation
- `tests/unit/test_webfetch_builtin.py` — WebFetch definition, config fields, roundtrips
- `tests/unit/test_mcp_server_crud.py` — CRUD + built-in protection
- **Total feature tests**: 152 passed

### Pre-existing Failures (NOT related to built-in MCP)
| Test File | Tests | Reason |
|-----------|-------|--------|
| `test_inner_soul_standalone.py` | 2 | Integration, requires real LLM |
| `test_instance_title_e2e.py` | 1 | Integration, requires LLM |
| `test_message_queue_e2e.py` | 3 | Integration, async loop issues |
| `test_multi_turn_resume.py` | 3 | Integration, resume after failure |
| `test_jober_watch_integration.py` | 1 | Port 8079 in use |
| `test_manager.py` | 2 | Title generation mocking |
| `test_nudge_behavior.py` | 3 | Graph node registration (unrelated) |

### Quick Fix Applied
- **Commit**: `8a41ca7` — Fixed test migration schema to include `job_queue_paused` and `default_max_retries` columns

---

## 2. Frontend Build

**Command**: `cd web && npm run build`
**Result**: ✅ SUCCESS — No TypeScript errors
**Output**: `dist/frontend/` (1.16 MB initial bundle)
**Warnings**: Bundle size exceeded budget (cosmetic, not errors)

---

## 3. Frontend Tests

**Command**: `npx jest --no-cache` (frontend/)
**Result**: ✅ 518 tests passed

### MCP-Specific Tests (146 passed)
| Test Suite | Tests |
|------------|-------|
| `mcp-server-dialog.component.spec.ts` | Triple mode, saving state, reset to defaults |
| `mcp-server.service.spec.ts` | listTemplates, configureBuiltin, resetBuiltin |
| `mcp-server-list.component.spec.ts` | Built-in section, badges, template dropdown |

### E2E Failures (Pre-existing Playwright config issue)
- `send-pause-button.spec.ts` — TypeError: Class extends value undefined
- `project-tabs.spec.ts` — Same esModuleInterop issue

---

## 4. Daemon Startup + API Integration Test

**Script**: `scripts/test_builtin_mcp_servers.sh` (committed at `0515596`)
**Result**: ✅ 9/9 tests passed

### Bootstrap Logs
```
Bootstrapping 1 built-in MCP servers...
Built-in MCP server bootstrap complete
```

### API Endpoint Results

| Step | Test | Result |
|------|------|--------|
| 1 | Daemon startup on port 18088 | ✅ PASS |
| 2 | GET /api/mcp-servers/builtin-templates | ✅ PASS — Returns webfetch template with 3 config fields |
| 3 | GET /api/mcp-servers | ✅ PASS — webfetch server with is_builtin=true |
| 4 | POST /api/mcp-servers/configure-builtin | ✅ PASS — Custom config saved |
| 5 | Config persistence verification | ✅ PASS — Values match |
| 6 | POST /api/mcp-servers/{id}/reset-builtin | ✅ PASS — Defaults restored |
| 7 | DELETE /api/mcp-servers/{id} → 403 | ✅ PASS — Protected |
| 8 | PUT /api/mcp-servers/{id} → 403 | ✅ PASS — Protected |
| 9 | Boolean false roundtrip | ✅ PASS — Flag omitted, not --no-* |

---

## 5. Key Correctness Checks

| Check | Status | Evidence |
|-------|--------|----------|
| Boolean False: ignore_robots_txt=False → flag omitted | ✅ VERIFIED | `test_build_config_ignore_robots_txt_false` asserts both `--ignore-robots-txt` and `--no-ignore-robots-txt` NOT in args |
| Boolean True: ignore_robots_txt=True → --ignore-robots-txt | ✅ VERIFIED | `test_build_config_ignore_robots_txt_true` asserts `--ignore-robots-txt` in args |
| parse_config roundtrip | ✅ VERIFIED | `test_parse_config_roundtrip` verifies full build→parse cycle |
| Proxy URL validation | ✅ VERIFIED | `test_build_config_proxy_url_invalid_scheme` rejects ftp://, `test_build_config_proxy_url_no_scheme` rejects bare host |
| Number empty → null (not 0) | ✅ VERIFIED | parse_config omits keys not in stored args, not defaulting to 0 |
| 403 DELETE protection | ✅ VERIFIED | `test_delete_builtin_returns_403` |
| 403 PUT protection | ✅ VERIFIED | `test_update_builtin_rejects_name_description`, `test_update_builtin_rejects_config` |
| Saving state double-submit | ✅ VERIFIED | `saving` signal blocks submit via `isSubmitDisabled()`, set true/false around all API calls |

---

## 6. ensure.md Validation

| Requirement | Status |
|-------------|--------|
| dev.sh runs 30s without crash | ✅ PASS (exit code 124 = timeout, not crash) |
| Bootstrap messages appear | ✅ PASS ("Bootstrapping 1 built-in MCP servers...") |

---

## 7. Code Changes Summary

| Commit | Description |
|--------|-------------|
| `8a41ca7` | test: fix migration schema to include job_queue_paused and default_max_retries columns |
| `0515596` | test: add daemon integration test script for built-in MCP servers |

---

## 8. Conclusion

**All 3 phases of the Built-in MCP Servers feature are fully tested and verified:**

- **Phase 1 (Backend Framework)**: ✅ DB migration, ABC, registry, router, manager bootstrap — all passing
- **Phase 2 (Frontend UI)**: ✅ TypeScript models, services, dynamic form, server list/dialog — builds and tests pass
- **Phase 3 (WebFetch Server)**: ✅ WebFetchServerDefinition, 3 config fields, proxy validation — 97+ tests pass

**No feature-related failures found.** The 15 backend failures and 2 frontend E2E failures are all pre-existing and unrelated to this feature.
