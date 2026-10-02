# Lesson: ck-redesign-2026q4 — wizard e2e navigation gotchas (Material stepper, Playwright, Jest 30)

Date: 2026-10-02 · Branch `feature/checkpoint-cleanup-ui-redesign` @ `b56e290a` (worktree ck-ui-test)
Context: checkpoint-cleanup page redesigned to a 4-step responsive stepper; e2e pack updated + AC-15 runtime verified (see RESULTS/2026-10-02-ck-redesign-e2e-ac15.md).

## 1. Material stepper focus-restoration oscillation (PRODUCTION defect family)
Focusing a button inside a NON-ACTIVE step body makes the stepper's focus-restoration handler emit `(selectionChange)`; combined with the `[selectedIndex]` binding + `(selectionChange)` sync this produces a brief Step1↔Step2 oscillation after in-body button clicks (e.g. dry-run).
- **Test-side workaround**: navigate via `mat-step-header` clicks (Step 2→3→4) — bypasses the race entirely.
- **Production fix** (needs own commission): likely `[cdkStepperFocusable]` config or a signal-only stepper without the two-way selection sync.

## 2. Auto-advance vs stepper focus restoration in headless Chromium
The component's auto-advance to Step 4 after the execute-poll finishes races Material's focus restoration. **Always follow the poll with an explicit Step-4 tab-header click** before asserting Result content.

## 3. `<details>` visibility race
Playwright treats `<details>` children as hidden even when the summary is toggled. Force the attribute instead: `page.evaluate("el.open = true")` (or `details[open]` set), then assert children.

## 4. Orientation-scoped viewport assertions
Below 1024px the stepper is vertical and the stacked-header layout pushes the footer (Continue) **below the fold by design** (spec §5.2). `ck-continue-btn` in-viewport assertions are only valid at ≥1024px. The 1023px flip case asserts orientation only (vertical visible, horizontal absent).

## 5. STOP-rule findings: hard vs soft assertions
Encode runtime-budget breaches (AC-15 scrollHeight) as HARD assertions — a standing red gate honestly represents a live spec violation until the spec is re-amended; encode a11y remediation-pending findings with `expect.soft` so they report without blocking unrelated work.

## 6. Jest 30 rejects the npm test script
`frontend/package.json` `test` still passes `--testPathPattern` → Jest 30.3.0 dies with a rename-error before running specs. Invoke `node_modules/.bin/jest <paths>` directly until the script is fixed (out of tester scope).

## 7. Runtime is ground truth for layout budgets
Static layout math had ±50px uncertainty; estimates clustered 712–738px but runtime measured **768px** at 1024×768 (8px over the ≤760 budget) with all §2.9 levers already applied. Never trust static estimates for AC-style pixel budgets — measure, then STOP and escalate per commission.


## 9. `<dl role="status">` defeats dlitem remediation (axe-core ARIA override)
Flattening dt/dd as direct children of `<dl>` satisfies the dlitem rule ONLY without a role override. `<dl role="status">` turns the parent into a status region in the ARIA tree → dt/dd read as orphaned → dlitem violations persist regardless of markup order. Fix shape: outer `<div role="status">` wrapping an inner plain `<dl>`. (ck-redesign v5 hit this — the flatten was correct but insufficient.)

## 10. Playwright `--update-snapshots` default SKIPS passing screenshots
Default update mode only rewrites snapshots of FAILING assertions — a passing-but-stale baseline silently survives regen. Force full overwrite with `--update-snapshots=all`.

## 11. Pixel-budget ACs must never gate on document scrollHeight vs a sub-viewport constant
DOM invariant: `scrollHeight = max(clientHeight, contentHeight)` — at 1024×768 any page with content ≤768px reports scrollHeight=768, making `scrollHeight ≤ 760` structurally unsatisfiable. Gate on no-overflow (`scrollHeight ≤ clientHeight`) or a content-element height instead. (ck-redesign v5 corrected exactly this.)

## 12. Axe counts can stay flat while nodes migrate — resolve selectors, not counts
After remediation killed the in-scope button-name/color-contrast nodes, the violation COUNT stayed 1/2/6 because the scan resurfaced pre-existing app-header nodes (`.bell-button`, `.queue-count`, `.version`). Always resolve violating node selectors before declaring a fix failed.
