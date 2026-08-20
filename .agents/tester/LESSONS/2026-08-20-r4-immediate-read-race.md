# Lesson: R4 immediate-read race — Angular change-detection lag defeats post-click DOM reads

Date: 2026-08-20 | Branch: fix/hide-editor-button-keep-instance | Pack: instances_state_e2e_regression

## Root cause
`instances-state-cache-regression.spec.ts` R4 (and R2, R6 — same pattern) reads `getComputedStyle(app-chat).display` IMMEDIATELY after `.overlay-hide-btn.click()`. The app-chat display flip is signal-driven and lands ~100–160ms after the click (Angular change detection). The immediate read deterministically observes the PRE-click state.

## Evidence (instrumented throwaway diag, deleted after use)
| Probe | t | chatDisplay | aria |
|---|---:|---|---|
| before click-1 | 0ms | flex | Hide overlay |
| click-1 IMMEDIATE | +44ms | flex (stale) | Hide overlay |
| click-1 WAITED | +160ms | none ✓ | Show overlay |
| click-2 IMMEDIATE | +191ms | none (stale) | Show overlay |
| click-2 WAITED | +307ms | flex ✓ | Hide overlay |

- Retry budget 3×: 0P/3F identical → deterministic test bug, NOT flaky (no quarantine — flaky-test-management anti-pattern).
- Button DOM node never replaced (elementHandle attached throughout) — re-render hypothesis rejected.
- Same-card/specific-selector setup reproduced the race → context hypothesis rejected.

## Correct pattern (used by hide-button-symptom.spec.ts S1, passes)
```ts
await hideBtn.click();
expect(page.url()).toBe(detailUrl);
await expect(async () => {
  const display = await page.locator('app-chat').evaluate(
    (el) => getComputedStyle(el).display,
  );
  expect(display).not.toBe('none');
}).toPass({ timeout: 5000 });
```

## Verbatim failing pattern (R4 lines 318-323) to replace
```ts
await hideBtn.click();
expect(page.url()).toBe(detailUrl);
const chatDisplayAfter = await page.locator('app-chat').evaluate(
  (el) => getComputedStyle(el).display,
);
expect(chatDisplayAfter).toBe('flex');
```
(Direct positive assertion `toBe('flex')` inside toPass is also acceptable; `not.toBe('none')` is what S1 uses.)

## Where to apply (follow-up; sits inside developer's uncommitted +225/-8 hunk — apply after their commit)
- R4: lines 318-323 (second click re-show read)
- R4 click-1 read (lines 292-299): same race, currently wins by accident (one sync+async op between click and read) — harden anyway
- R2 (line ~249 vicinity), R6 (line ~158 vicinity): same immediate-read pattern

## Companion fix (independent clean hunk — spec lines 56-63, page.on('console') handler)
Add collection-time CSP noise filter (working reference: hide-button-symptom.spec.ts:98-105 `isFilteredNoise()` — `plane.ensem.dev | frame-ancestors`, plus optionally NG0100 dev-mode + /api/workspace 404s). Without it, R6/R2/R4/N1/Reload-while-hidden can NEVER pass a serial run on dev :4199 (CSP allowlist only has :8079/:9797).

## Generalized rule
Signal-driven display/style flips in this codebase take ~100-160ms to reach computed style. ANY e2e assertion on computed style after a click MUST use the polling `toPass({ timeout: 5000 })` wrapper (or `expect(locator).toBeVisible()` equivalents), never an immediate single read. Recorded to KB by pack-3 worker.
