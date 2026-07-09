# Lesson: Angular Component-Scoped SCSS + innerHTML Injection (W5 Bug)

**Date:** 2026-07-09
**Context:** Mermaid Chart UI feature testing (`feature/mermaid-chart-ui`)
**Commit:** `2ba37c2d`

## The Bug

Angular's component-scoped SCSS (ViewEncapsulation.Emulated) rewrites CSS selectors by appending `[_ngcontent-xxx]` attribute selectors. For example:

```scss
.mermaid { position: relative; }
```

compiles to:

```css
.mermaid[_ngcontent-ng-c3275081694] { position: relative; }
```

This works for elements rendered by Angular templates (which get the `_ngcontent` attribute). But **content injected via `innerHTML`** — like Mermaid SVGs rendered by the mermaid library — does **NOT** get the `_ngcontent` attribute. The attribute-selector match fails, and the CSS rule never applies.

## Symptoms

- Overlay buttons appeared at bottom-left of charts instead of top-right
- `.mermaid` had `position: static` instead of `relative`
- Overlay opacity was 1 instead of 0.7
- Buttons were 16px instead of 28px
- Fullscreen dialog `.chart-stage` collapsed to 0×0

## The Fix

Use `::ng-deep` to pierce Angular's view encapsulation:

```scss
::ng-deep .mermaid { position: relative; }
```

This compiles to a plain `.mermaid` selector that matches innerHTML-injected elements.

## Pattern to Remember

**Any CSS targeting dynamically-injected DOM (innerHTML, third-party libraries like mermaid, Prism, KaTeX) in an Angular component-scoped stylesheet MUST use `::ng-deep`.** Without it, the styles silently fail to apply.

Verify with DevTools: check `getAttributeNames()` on the injected element — if `_ngcontent-xxx` is absent, component-scoped CSS won't match.

## Second Fix: Flex Layout Collapse

The fullscreen dialog had `.chart-stage` inside `.fullscreen-body` (a `display: flex` row container). Without explicit flex properties, `.chart-stage` collapsed to 0×0. Fix: `flex: 1 1 auto; min-width: 0; min-height: 0;`.

## General Lesson
When testing Angular features that render content via innerHTML or third-party libraries, always verify CSS scoping in DevTools — don't assume component-scoped styles apply.
