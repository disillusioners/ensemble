# Test Report: Mermaid Chart UI Enhancement
**Date:** 2026-07-09
**Branch:** `feature/mermaid-chart-ui`
**Commits:** `30b3976` (feature) + `179a8e6` (hardening) + `2ba37c2d` (quick fixes from testing)
**Sessions:** `mermaid-ui-setup`, `mermaid-ui-browser-test`

---

## Summary
- **Total Tests:** 6
- **Passed:** 6/6 (after fixes)
- **Failed:** 0 (after fixes)
- **Quick Fixes Applied:** 2 (committed as `2ba37c2d`)
- **Overall Status:** ✅ READY — All features working correctly after fixes

## Initial vs Final Results

| # | Test | Initial Result | After Fix |
|---|------|---------------|-----------|
| 1 | Overlay buttons (visibility/opacity/positioning) | ❌ FAIL | ✅ PASS |
| 2 | Copy dropdown menu (Copy as Image + Copy Source) | ✅ PASS | ✅ PASS |
| 3 | Single-menu-open behavior | ✅ PASS | ✅ PASS |
| 4 | Fullscreen dialog (open/close/scroll/sizing) | ⚠️ PARTIAL | ✅ PASS |
| 5 | CSS scoping verification (W5 — CRITICAL) | ❌ FAIL | ✅ PASS |
| 6 | Edge cases (chart types, large charts) | ✅ PASS | ✅ PASS |

---

## Test Details

### Test 1: Overlay Buttons — FAIL → PASS

**Before fix:**
- `.mermaid` computed `position`: `static` (should be `relative`)
- `.mermaid-overlay` computed `position`: `static` (should be `absolute`)
- `.mermaid-overlay` `top`/`right`: `auto`/`auto` (should be `8px`/`8px`)
- `.mermaid-overlay` idle `opacity`: `1` (should be `0.7`)
- `.mermaid-overlay-btn` `width`: `16px` (should be `28px` / 1.75rem)
- Buttons appeared at **bottom-left** of chart instead of top-right

**After fix:**
- `.mermaid-overlay` rect: `(1096, 1539, 60, 28)` inside chart rect `(864, 1531, 300, 294)` — top-right confirmed
- Idle opacity: `0.7`, hover opacity: `1`
- Button size: 28×28

### Test 2: Copy Dropdown Menu — PASS

- **Copy as Image**: Produced 39,242-byte PNG (NOT the old 100px-tiny bug — verified FIXED). Status pill: "Copied 'Mermaid Diagram' as image"
- **Copy Mermaid Source**: Copied 114 chars of raw mermaid syntax. Status pill: "Source copied to clipboard"
- Both menu items rendered with Material icons (image / code)

### Test 3: Single-Menu-Open Behavior — PASS

- Opened menu on chart A → 1 CDK overlay pane
- Opened menu on chart B → still exactly 1 CDK overlay pane (chart A's closed)
- `disposeActiveMenu(true)` in `mermaid-actions.service.ts:91` works correctly

### Test 4: Fullscreen Dialog — PARTIAL → PASS

**Before fix:**
- Dialog opened at correct size (1216 × 601 ≈ 95vw × 95vh) ✓
- Close button + ESC worked ✓
- **Bug**: `.chart-stage` collapsed to 0×0 — SVG rendered at 0×0, dialog body showed only header and source `<pre>`

**After fix:**
- Dialog 1216 × 601 ✓
- `.chart-stage` now 1164 × 446 ✓
- SVG renders properly: 304 × 468 (decision flowchart) ✓
- Close button, ESC, and backdrop click all close cleanly ✓
- Scroll test: large 26-node chart (svg 131 × 2670) overflows body, scroll confirmed working ✓

### Test 5: CSS Scoping (W5) — FAIL → PASS (CRITICAL CONFIRMATION)

**Confirmed the W5 code-review concern end-to-end:**
- The dynamically injected `.mermaid` div has **NO `_ngcontent-xxx` attribute**
- Angular compiled rules as `.mermaid[_ngcontent-ng-c3275081694]` — attribute-selector match failure
- Computed styles: `position: static`, opacity 1, top/right auto, width 16px

**Fix:** Added `::ng-deep` prefix to all mermaid-related CSS rules in `chat-interface.scss`. After reload, all computed styles match design.

### Test 6: Edge Cases — PASS

7 charts across 5 types verified post-fix:

| Chart Type | SVG Rendered | Overlay Buttons | Top-Right Position | Button Size |
|-----------|-------------|-----------------|-------------------|-------------|
| graph TD (decision) | ✓ | 2 | ✓ | 28×28 |
| graph TD (linear) | ✓ | 2 | ✓ | 28×28 |
| sequenceDiagram | ✓ | 2 | ✓ | 28×28 |
| graph TD (26-node) | ✓ | 2 | ✓ | 28×28 |
| stateDiagram-v2 | ✓ | 2 | ✓ | 28×28 |
| erDiagram | ✓ | 2 | ✓ | 28×28 |
| gantt | ✓ | 2 | ✓ | 28×28 |

---

## Quick Fixes Applied

Commit **`2ba37c2d`** — `fix(mermaid): scope overlay/positioning rules through ::ng-deep`

Two files modified (+32 / −16 lines):

### Fix 1: CSS Scoping (W5) — `chat-interface.scss`
- Added `::ng-deep` to `.mermaid`, `.message-bubble .mermaid`, `.mermaid-overlay`, `.mermaid-overlay-btn`, `.mermaid-status-pill`
- Root cause: Angular component-scoped SCSS rewrites `.mermaid` → `.mermaid[_ngcontent-xxx]`, but innerHTML-injected SVGs lack the `_ngcontent` attribute → rules never applied
- `::ng-deep` pierces view encapsulation so rules reach dynamically injected DOM

### Fix 2: Fullscreen chart-stage collapse — `mermaid-fullscreen-dialog.scss`
- Gave `.chart-stage` `flex: 1 1 auto; min-width: 0; min-height: 0;`
- Root cause: `.chart-stage` was inside `.fullscreen-body` (a row flex container) without flex properties → collapsed to 0×0
- Now stretches to fill available space, SVG renders correctly

---

## Non-Blocking Observations

1. **Intrinsic SVG sizing in fullscreen**: Charts render at their intrinsic size in fullscreen because mermaid emits `style="max-width: <intrinsic>px"` on SVGs. Charts do NOT visually scale up in fullscreen. If design intent is larger rendering, would need `::ng-deep .chart-stage svg { max-width: none !important }` or JS-side resizing. Flagged for design review, not a test blocker.

2. **Zero unit tests for mermaid feature**: The feature ships 5 source files and zero `.spec.ts` tests. Recommend adding unit tests for the XSS-hardening logic and clipboard operations.

---

## Frontend Test Suite (No Regression)
- Jest suite: **23 suites passed, 834 tests passed, 0 failed** (3.9s)
- No mermaid-specific unit tests existed to run

## Code Changes Summary
- `frontend/src/app/components/chat-interface/chat-interface.scss` — `::ng-deep` scoping fix (W5)
- `frontend/src/app/components/mermaid-fullscreen-dialog/mermaid-fullscreen-dialog.scss` — flex sizing fix (chart-stage 0×0)
- Commit: `2ba37c2d`
