# Test Report: Workspace State Preservation

**Date:** 2026-07-24
**Branch:** `feature/workspace-state-preserve`
**Commit:** `2fd787aa`
**Workers:**
- `frontend-unit-tests` (id: b28add21-e807-48d8-bb45-3f1cbdd8e053) — skill: `test-pack-execution`
- `workspace-e2e` (id: 33557af0-b96e-4f36-aeda-c47c3cb725d8) — skill: `e2e-test`

---

## Summary
- **Total packs run:** 2 (1 unit, 1 E2E)
- **Passed:** 2 | **Failed:** 0 | **Errors:** 0
- **Unit Tests:** 1663 tests, 48 suites — ALL PASS
- **E2E Tests:** 7/7 scenario steps — ALL PASS
- **ensure.md:** N/A (frontend-only change; Core requirements are backend-focused, not relevant to blast radius)
- **Quick Fixes Applied:** 0 (none needed — feature works correctly)
- **Quarantined:** 0

---

## Scope Decision

> **Full frontend unit suite requested; change is frontend-only** (workspace state preservation during tab switches — file content refetch + directory tree expansion). The full Jest suite was run as a regression gate because the change touches shared workspace/tab-state components (`workspace.component`, `tab-state.service`, `workspace.service`, `chat.component` effect). Backend `ensure.md` Core requirements (concurrency, deadlock, sync DB calls) were scoped out — change does not touch backend concurrency/architecture. No Release Gate warranted (small UI fix, not architecture). Full suite justified here per user's explicit request + ripple-risk across frontend components.

---

## Scope: What was tested

**Change (commit 2fd787aa):**
1. File content is refetched via `getFileContent()` when restoring a previously-viewed file after a tab switch (was being nulled with no refetch)
2. Directory tree expansion state is preserved across tab switches (captured as `outgoingUiExtras` before switch, reapplied after `setTree()` clears cross-project leakage)

---

## Unit Test Results: `frontend_full_unit_test`

**Status:** ✅ PASS (1663/1663, 48 suites, ~5.1s)

**Pack:** `test/packs/frontend_full_unit_test.sh`
**Command:** `timeout 300 bash test/packs/frontend_full_unit_test.sh` (dual-layer timeout confirmed)

**Key relevant suites (all green):**
- ✅ `chat.component.spec.ts` — includes `TestTabWorkspaceEffectHostComponent` for tab→workspace effect tracking
- ✅ `workspace.component.spec.ts`
- ✅ `tab-state.service.spec.ts`
- ✅ `workspace.service.spec.ts`
- ✅ `project-tab-bar.component.spec.ts`

**Warnings (non-failing):** Console warnings/errors in test output are expected test instrumentation (negative-path test cases deliberately triggering error handlers, `allowSignalWrites` deprecation warning). No actual failures.

---

## E2E Test Results: Workspace State Preservation

**Status:** ✅ PASS (7/7 steps, ~50.5s runtime)
**Spec:** `frontend/e2e/workspace-state-preserve.spec.ts` (350 lines, NEW)
**Screenshots:** 9 in `frontend/e2e/screenshots/preserve-*.png`

**Server startup:** Backend (8079) and frontend (4199) were not running; worker started both, confirmed health, ran test, cleaned up.

| Step | Description | Result | Details |
|------|-------------|--------|---------|
| **a** | Open Project A workspace, expand dirs, open file | ✅ PASS | Workspace A open, `src/` expanded, `file1.ts` loaded with content |
| **b** | Switch to Project B → different tree, no file open | ✅ PASS | Workspace B showing Beta files, no file open (fresh state) |
| **c** | Switch back to A → **file content restored** | ✅ PASS | File content restored: `file1.ts` shows `export function alphaFn() { return 'alpha'; }` — refetch works |
| **d** | Switch back to A → **expanded dirs preserved** | ✅ PASS | Directory `src/` still expanded after A→B→A roundtrip |
| **e** | Repeat A→B→A 3× → consistent each time | ✅ PASS | All 3 roundtrips consistent (content + expansion verified each time) |
| **f** | Expand different dirs in B → A unaffected | ✅ PASS | B's expansion of `lib/` did NOT affect A — A's `src/` still expanded, no B files bleeding |
| **g** | Open different file in B → A's file still correct | ✅ PASS | Opened `utils.ts` in B, switched to A — A's `file1.ts` content still correct |

**Warnings:** 4 SSE connection errors (expected — EventSource reconnects on project switch); 33 Angular `allowSignalWrites` deprecation warnings (pre-existing, unrelated).

---

## ensure.md Validation Results

**Scope:** Frontend-only change. ensure.md Core requirements (concurrency integrity, sync DB calls, deadlock) are backend-focused and not relevant to this blast radius.

- **Critical — "No regressions in changed packs":** ✅ PASS — `frontend_full_unit_test` returned PASS (1663/1663)
- **Critical — Deadlock/concurrency integrity:** ⏭️ SKIPPED — backend concern, change is frontend-only
- **Critical — No sync DB calls on event loop:** ⏭️ SKIPPED — backend concern, change is frontend-only
- **Critical — `dev.sh` includes `--timeout-graceful-shutdown 10`:** ⏭️ SKIPPED — no change to `dev.sh`
- **Release Gate:** ⏭️ SKIPPED — small UI fix, not architecture/critical change

No contradictions with ensure.md (it says "scope by blast radius").

---

## Documentation Updated
- [x] RESULTS/2026-07-24-workspace-state-preserve.md — this report
- [x] PACKS.md — updated `frontend_full_unit_test` last run + status; added `workspace_state_preserve_e2e_test` pack
- [ ] rules/ensure.md — no changes (user-maintained)
- [ ] MOCK_TESTS.md — no changes
- [ ] LESSONS/ — none needed (no bugs found, no quick fixes)

---

## Code Changes Summary
- **NEW:** `frontend/e2e/workspace-state-preserve.spec.ts` (350 lines) — Playwright E2E spec for workspace state preservation
- **NEW:** 9 screenshots in `frontend/e2e/screenshots/preserve-*.png`
- **No production code changes** — feature works correctly as implemented in commit `2fd787aa`
- **Commit:** Worker may have committed the new spec — verify working tree

---

## Overall Status
- **Unit Tests:** ✅ PASS (1663/1663)
- **E2E Tests:** ✅ PASS (7/7 scenario steps)
- **ensure.md:** ✅ PASS (in-scope requirement — no regressions — verified)
- **Testing Complete:** ✅ **READY** — Feature verified, no regressions, all original bug scenarios pass
