# Test Report: VS Code auto-start + editor UI changes
Date: 2026-07-27
Branch: `feature/vscode-autostart-editor-ui`
Commits: `4cc91a4` (backend auto-start) + `cb5819c9` (frontend editor UI)

## Summary
- **Total: 689 tests | Passed: 689 | Failed: 0 | Skipped: 8**
- Backend: 296 tests (213 + 51 + 32)
- Frontend: 393 tests (263 + 130)
- Quick Fixes Applied: 2 (pre-existing test bugs in test_api.py, committed)
- Quarantined: 0
- **Overall Status: ✅ READY**

## Scope Decision
> Full test suite was NOT requested. Two independent features on one branch — backend auto-start helper (1 file + 4 tests) and frontend conditional rendering + state preservation (5 files + 3 specs). Blast radius: small/feature-isolated, no architecture change. Ran 5 scoped packs covering the complete change set (3 backend, 2 frontend). Skipped the full suite (200 packs) — not warranted for this scoped feature change. No ensure.md Release Gate needed (not big/critical/architecture).

## ensure.md Validation Results (Core — scoped to change set)
- **Critical**:
  - ✅ No regressions in changed packs — all 5 packs in the change set PASS
- **Release Gate**: NOT run (not a big/critical/architecture change)

## Pack Results

### Backend: api_unit_test — ✅ PASS (213 passed, 8 skipped, 12.3s)
- Worker: `b1-api-autostart` (f145d190)
- 4 NEW auto-start tests — ALL PASS:
  1. ✅ `test_auto_start_vscode_calls_ensure_running_when_preference_is_vscode`
  2. ✅ `test_auto_start_vscode_skips_ensure_running_when_preference_is_builtin`
  3. ✅ `test_auto_start_vscode_swallows_ensure_running_failure` (non-fatal — daemon boots)
  4. ✅ `test_auto_start_vscode_passes_project_repository_to_get_editor_preference`
- Quick Fixes (2, committed): pre-existing `test_send_message_success` mock+assertion drift, NOT related to auto-start feature

### Backend: vscode_server_manager_unit_test — ✅ PASS (51/51, 4.12s)
- Worker: `b2-vscode-mgr-regression` (bbfa776f)
- No regressions from auto-start commit

### Backend: vscode_editor_settings_api_test — ✅ PASS (32/32, 1.07s)
- Worker: `b3-editor-api-regression` (e8fe6b6e)
- No API regressions

### Frontend: workspace_frontend_unit_test — ✅ PASS (263/263, 8 suites, ~4.5s)
- Worker: `f1-workspace-frontend` (fd7f2f57)
- Verified all 4 requested behaviors:
  1. ✅ File-tree in DOM when editorMode='builtin' (`workspace.component.spec.ts:294`)
  2. ✅ File-tree removed when editorMode='vscode' (`:301`)
  3. ✅ Expanded-dir state preserved across mode switch (`:309`)
  4. ✅ Null guard for fileTree in vscode mode (`:338`)

### Frontend: vscode_frontend_unit_test — ✅ PASS (130/130, 3 specs, ~1.6s)
- Worker: `f2-vscode-frontend` (d35f2f56)
- Verified: live editor switch (`setEditorMode` called with `'vscode'`), vscode-viewer (16 cases), file-tree state preservation (`setTree`+`restoreExpandedPaths` round-trip)
- ✅ TestableSettingsComponent mirror synced correctly in commit cb5819c9 (production + test double updated together)

## Quick Fixes Applied

| Worker | Fix | Root Cause | Commit |
|--------|-----|------------|--------|
| b1 | `tests/test_api.py:81` — added `queued=False` to Mock fixture | `enqueue_message_job` mock return lacked `queued` field → Mock auto-generated child → Pydantic rejected as non-bool | `e5c351ba` |
| b1 | `tests/test_api.py:858` — added `queue_id=None` to expected kwargs | Stale `assert_called_once_with(...)` missing `queue_id` kwarg router passes | `5e6b9cc3` |

Both fixes: pre-existing test bugs (mock/assertion drift), unrelated to the auto-start feature, 1 line each, test-code only.

## Verification Matrix

| Requested Verification | Status | Evidence |
|----------------------|--------|----------|
| Auto-start calls ensure_running() when preference="vscode" | ✅ | b1 test 1 PASS |
| Auto-start does NOT call ensure_running() when "builtin" | ✅ | b1 test 2 PASS |
| Auto-start non-fatal — daemon boots if code-server fails | ✅ | b1 test 3 PASS |
| Correct repo passed to get_editor_preference | ✅ | b1 test 4 PASS |
| File-tree in DOM when editorMode='builtin' | ✅ | f1 :294 |
| File-tree removed when editorMode='vscode' | ✅ | f1 :301 |
| Expanded state captured/restored on mode switch | ✅ | f1 :309 + f2 round-trip test |
| Null guards prevent crash when fileTree undefined | ✅ | f1 :338 |
| Live editor switch → SettingsComponent → WorkspaceService | ✅ | f2 explicit setEditorMode assertion |
| No vscode_server_manager regression (51 tests) | ✅ | b2 |
| No editor_settings API regression (32 tests) | ✅ | b3 |

## Overall Status
- Backend Auto-Start: ✅ PASS
- Frontend Editor UI: ✅ PASS
- **Testing Complete: ✅ READY**
