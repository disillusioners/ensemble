# Lesson: Mermaid Popup — `width: fit-content` Circular Sizing Collapse

**Date**: 2026-07-12
**Component**: `mermaid-fullscreen-dialog`
**File**: `frontend/src/app/components/mermaid-fullscreen-dialog/mermaid-fullscreen-dialog.scss`
**Commit**: 3be97190

## Problem
The original CSS fix for the mermaid popup clipping bug used:
```scss
.chart-stage {
  width: fit-content;
  max-width: 100%;
  margin: auto;
}
```

This was intended to shrink-wrap the stage to the SVG's intrinsic size so `margin: auto` could center small charts. However, it introduced a **worse bug**: the chart became completely invisible (0×0 collapse).

## Root Cause
Mermaid emits an inline `<svg>` with `width="100%"` as an HTML attribute. This creates a **circular sizing dependency**:

1. `.chart-stage { width: fit-content }` → resolves to child's max-content size
2. SVG `width="100%"` → needs parent's definite width to resolve the percentage
3. Parent has no definite width (it's trying to fit the child) → both collapse to **0×0**

In headless Chromium verification:
- `stageCss.width: "0px"`
- `stageCss.margin: "0px 659px"` (auto margins distributed around 0-width)
- SVG `<g>` elements positioned correctly at viewBox y-coords (95→2008) but rendered into invisible 0-sized stage
- Body had `overflow: false` because there was nothing to overflow

## Fix
Use a definite containing block width + inline-block centering instead:
```scss
.chart-stage {
  width: 100%;            // definite containing block — breaks the circular dependency
  text-align: center;     // centers inline-block children horizontally
}
::ng-deep .chart-stage svg {
  max-width: 100%; height: auto; width: auto;
  display: inline-block;  // needed for text-align centering to work
  vertical-align: top;    // prevents inline-block baseline gap
}
```

## Why This Works
- `width: 100%` gives `.chart-stage` a definite width → SVG's `width="100%"` resolves correctly
- `width: auto` on the SVG reverts it to its intrinsic viewBox size (not 100% of parent)
- `max-width: 100%` caps wide SVGs to the dialog body
- `display: inline-block` + `text-align: center` centers small charts horizontally
- Large charts scroll via the body's `overflow: auto`

## Key Takeaway
**Never pair `width: fit-content` with a child that has `width: 100%`** — it creates a circular sizing dependency. When the child's size depends on the parent and the parent's size depends on the child, both collapse to 0.

## Verification
- Large chart (22-node vertical): stage 1318×1921, scrollHeight=1969, last node fully visible ✅
- Small chart (3-node LR): SVG 312px, centered with 24px gaps ✅
- Wide chart (15-node horizontal): SVG capped at 1302px, no overflow ✅
- Angular build: PASS (no SCSS errors)
- Frontend test suite: 880/880 PASS (no regressions)
