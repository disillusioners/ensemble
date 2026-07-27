# VS Code Frontend — Coverage Gaps in settings.component.spec.ts

**Date:** 2026-07-27
**Commit:** `2e1ff60c` (feature/fix-vscode-status-contract)
**Source:** worker report (pack-frontend-settings), test-pack-execution skill

## Context
While verifying the VS Code status contract fix (VSCodeStatus `{running,port,allow_remote}` → `{status:string}`; polling stops on terminal states), the frontend worker surfaced two gaps relative to the test task's expectations. Neither blocked the pack (81/81 PASS), but they are worth tracking.

## Finding 1 — "View Workspace" button is in a different component

**Symptom:** The test task (behavior #4) asked to verify "View Workspace" button is enabled when status="running". The worker found the settings component has **no such button** — its only buttons are language-save, custom-save, custom-clear, and editor-apply, none gated on VS Code status.

**Root cause:** Task misattribution. The "View Workspace" / "View workspace" UI lives in a *different* component — `project-tab-bar.component.html` has a "View workspace" tooltip. The status-gating contract for that button, if any, must be tested in `project-tab-bar.component.spec.ts`, not here.

**Action:** If the contract "View Workspace enabled when status=running" is a real requirement, it belongs in a separate test target. For this commit, the settings.component.spec.ts correctly covers the settings component's own VS Code status UI (labels, classes, polling). No change needed here.

## Finding 2 — Unknown/unexpected status string not explicitly tested

**Symptom:** `vscodeStatusLabel()` and `vscodeStatusClass()` use a `switch` with a `default` branch (label='Not started', class=''). Production handles an unknown status like `"frobnicate"` gracefully, but **no test pins that contract** — the `default` branch is only exercised implicitly by the `null`-status test.

Similarly, no dedicated test for `undefined` status (distinct from `null`), though it hits the same fallback path.

**Suggested future test** (nice-to-have, not a regression):
```ts
it('should treat unknown status string as Not started', () => {
  mockService.getVscodeStatus.mockReturnValue(of({ status: 'frobnicate' }));
  component.ngOnInit();
  expect(component.vscodeStatusLabel()).toBe('Not started');
  expect(component.vscodeStatusClass()).toBe('');
});

it('should treat undefined status as Not started', () => {
  mockService.getVscodeStatus.mockReturnValue(of({ status: undefined }));
  component.ngOnInit();
  expect(component.vscodeStatusLabel()).toBe('Not started');
});
```

**Action:** Optional follow-up. Does not block `2e1ff60c`.

## Lesson
When a test task lists behaviors to verify, the executor should grep the target spec/component for each behavior *before* running, to catch (a) behaviors that live in a different component, and (b) defensive branches with no dedicated test. The `test-pack-execution` skill could prompt for this pre-run grep (recorded as improvement_note in skill_feedback).
