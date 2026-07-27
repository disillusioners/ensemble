# Test Report: VS Code Status Contract Fix

Date: 2026-07-27
Branch: `feature/fix-vscode-status-contract`
Commit: `2e1ff60c`
Project: agents-ensemble

## Summary

| Metric | Value |
|--------|-------|
| Total tests run | 164 |
| Passed | 164 |
| Failed | 0 |
| Errors | 0 |
| Timeouts | 0 |
| Quick fixes applied | 0 |
| Quarantined | 0 |

**Overall Status: ✅ READY — all in-scope tests pass, no regressions.**

The fix (status="running" set before PID write; failed PID write caught+logged) is verified by:
- A new backend regression test that PASSES.
- No regressions in the editor_settings API layer (32/32).
- Frontend settings spec covers the new status contract (81/81), including terminal-state polling-stop and per-status label mapping.

## Scope Decision

> Full suite NOT run — change is scoped. The commit touches 6 files across 1 feature (VS Code status contract hardening): 1 backend service (`vscode_server_manager.py`, internal status ordering), 1 backend test (+1 regression test), 4 frontend files (1 model/interface, 1 scss, 1 spec, 1 component). No API signature change, no DB schema change, no concurrency/queue/agent-architecture impact. Running the full 200-pack suite (~2400 tests) would burn ~40 min for a non-architecture change. **Scoped to the 3 packs directly covering the changed code.** Full suite NOT warranted. Release Gate (E2E) NOT warranted.

Changed packs run:
- `vscode_server_manager_unit_test` (51 tests) — direct coverage of the production fix
- `vscode_editor_settings_api_test` (32 tests) — regression check on the API layer (no signature change)
- `vscode_frontend_unit_test` / `settings.component.spec.ts` (81 tests) — the rewritten spec

Packs skipped: all other 197 packs (no changed files in their modules).

## Worker Sessions (3 parallel, skill: test-pack-execution)

| Worker | Pack | Result | Count | Runtime |
|--------|------|--------|-------|---------|
| pack-vscode-unit | `tests/unit/test_vscode_server_manager.py` | ✅ PASS | 51/51 | 4.17s |
| pack-editor-settings-api | `tests/api/test_editor_settings.py` | ✅ PASS | 32/32 | 1.03s |
| pack-frontend-settings | `frontend/.../settings.component.spec.ts` | ✅ PASS | 81/81 | ~0.9s |

All three dispatched in parallel; aggregate wall-clock ≈ 4.2s (bounded by the longest pack). Each worker used `load_skill="test-pack-execution"` for 1:1 skill attribution. Each worker reported `skill_feedback` to the `test-pack-execution` skill.

## Required-Behavior Verification

### Backend — `test_start_marks_running_when_pid_write_fails` ✅ PASS
The new regression test (added in this commit) confirms:
- `state.status = "running"` is set **before** `_write_pid_file()`.
- If `_write_pid_file()` raises, the exception is caught and logged (best-effort), and status remains `"running"` — the server is NOT stranded in `"starting"`.

Quoted from worker: *"the regression test … PASSES, confirming that commit 2e1ff60c correctly sets `state.status = "running"` before the PID write and tolerates a failing write."*

### Frontend — behaviors requested in the task

| # | Behavior | Status | Evidence |
|---|----------|--------|----------|
| 1 | Polling stops when status="running" (terminal) | ✅ PASS | `should mark terminal running status and stop polling`; `should stop polling for stopped and crashed statuses` |
| 2 | Polling continues when status="starting" | ✅ PASS | `should continue polling while status is starting`; (`stopping` also covered) |
| 3 | `vscodeStatusLabel()`/`vscodeStatusClass()` correct per status | ✅ PASS | `it.each` parameterized: running/starting/stopping/stopped/crashed (label + class); plus `should return Not started when no status has been fetched` |
| 4 | "View Workspace" button enabled when status="running" | ⚠️ N/A — **misattribution in task** | The settings component has no "View Workspace" button (only language/custom-save/custom-clear/editor-apply buttons). This feature lives in a *different* component (e.g., `project-tab-bar` has a "View workspace" tooltip). No test exists because the feature isn't here. See Coverage Gaps below. |
| 5 | Edge cases: null/undefined status, unexpected status, timer cleanup | 🟡 PARTIAL | ✅ null status: `should return Not started when no status has been fetched`; ✅ poll error fallback: `should fall back to stopped status on poll error`; ✅ timer cleanup on unmount: `should clear polling interval on ngOnDestroy`. ❌ **No explicit test for an *unknown/unexpected status string*** (e.g., "frobnicate") — production `switch…default` handles it defensively (label='Not started', class='') but the branch is only exercised implicitly. ❌ **No explicit test for `undefined` status** (distinct from null). |

## Coverage Gaps (informational — not blocking)

These are opportunities surfaced by the worker; no test failed and no behavior is unverified in a way that blocks this fix.

1. **Behavior #4 misattribution** — The task asked to verify a "View Workspace" button in the settings component, but that button lives in another component (`project-tab-bar`). If the task author intended to also test the tab-bar component's gating on VS Code status, that is a *separate* test target (`project-tab-bar.component.spec.ts`) and was not in scope here.

2. **Unexpected/unknown status string** — `vscodeStatusLabel()`/`vscodeStatusClass()` use a `switch` with a `default` branch (→ 'Not started' / ''), so an unknown status like `"frobnicate"` is handled gracefully, but no dedicated test pins that contract. Suggested future test:
   ```ts
   it('should treat unknown status string as Not started', () => {
     mockService.getVscodeStatus.mockReturnValue(of({ status: 'frobnicate' }));
     component.ngOnInit();
     expect(component.vscodeStatusLabel()).toBe('Not started');
     expect(component.vscodeStatusClass()).toBe('');
   });
   ```
3. **`undefined` status** — distinct from `null`; the `null`-status test exercises the same fallback path, but a dedicated `undefined` assertion would make the contract explicit.

These are **nice-to-have** coverage additions, not regressions. They can be added in a follow-up.

## ensure.md Validation Results

Scoping applied per Blast Radius Control (small, single-feature change — Release Gate NOT warranted).

### Core (always-on)
- **Critical**
  - ✅ **No regressions in changed packs** — every pack in the change set returns PASS. Validated: `vscode_server_manager_unit_test` (51/51), `vscode_editor_settings_api_test` (32/32), `vscode_frontend_unit_test`/settings spec (81/81). **PASS.**
  - ⬜ Deadlock / concurrency integrity (`concurrency_atomic_unit_test`) — **OUT OF SCOPE**: no concurrency/lock changes in this commit. Skipped, not failed.
  - ⬜ Sync DB calls on asyncio loop — **OUT OF SCOPE**: no DB-access changes.
  - ⬜ `dev.sh --timeout-graceful-shutdown 10` — **OUT OF SCOPE**: no `dev.sh` change. (Static check, fast; not relevant to this change.)
- **Important / Nice-to-have** — all reference the deadlock/async-DB fix area; **OUT OF SCOPE** for this change.

### Release Gate (slow)
- **NOT WARRANTED** — small scoped change (6 files, 1 feature, no architecture/DB/concurrency/agent impact). Not run. Per ensure.md: *"Run ONLY when blast-radius determines the change is big/critical."*

**ensure.md outcome: ✅ PASS** — the single in-scope Core Critical requirement ("no regressions in changed packs") is satisfied.

## Contradictions / Improvement Notices
None. No ensure.md requirement contradicts the pack/timeout/scoping rules for this change.

## Quick Fixes Applied
None — all tests passed on first run. No code changes made during this test session.

## Code Changes Summary
- No code changes by the test session. The production fix is already committed at `2e1ff60c`.
- PACKS.md updated (3 entries — last-run status + the new PID-write-hardening note).

## Documentation Updated
- [x] `.agents/tester/PACKS.md` — updated 3 VS Code pack entries (last run, status, PID-write-hardening note)
- [ ] rules/ensure.md — no changes (user-maintained, read-only)
- [x] RESULTS/2026-07-27-vscode-status-contract-fix.md — this report
- [x] LESSONS/2026-07-27-vscode-frontend-coverage-gaps.md — frontend coverage gaps + behavior #4 misattribution

---

### Overall Status
- Unit Tests (vscode_server_manager): ✅ PASS (51/51, regression test green)
- API Tests (editor_settings): ✅ PASS (32/32, no regressions)
- Frontend (settings.component.spec): ✅ PASS (81/81, new status contract covered)
- ensure.md: ✅ PASS (1/1 in-scope Core Critical requirement; Release Gate not warranted)
- **Testing Complete: ✅ READY**
