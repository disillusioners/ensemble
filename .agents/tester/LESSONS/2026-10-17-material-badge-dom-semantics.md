# Material Badge DOM Semantics (Angular Material, this repo's version)

Date: 2026-10-17
Source: persona-menu-icon-removal commission (RESULTS/2026-10-17-persona-menu-icon-removal.md); unit worker hit this trap live, browser worker confirmed in real DOM.

## The trap

When `matBadge` is anchored on a host element (e.g. `<a class="nav-link persona-nav-link" [matBadge]="count()" [matBadgeHidden]="count() === 0" matBadgeSize="small">`):

1. **`.mat-badge` is the HOST class** — added to the anchor itself, NOT the badge element. Querying `.persona-nav-link .mat-badge` for the badge finds nothing useful (matches the host pattern only).
2. **The badge element is `span.mat-badge-content`** — appended to the host via `appendChild` (`node_modules/@angular/material/fesm2022/badge.mjs:144,242`). Its `textContent` ALWAYS reflects the bound value — including `"0"` when the badge is hidden.
3. **Hidden state is CSS-class based**: host carries `.mat-badge-hidden`; the content span is hidden via `.mat-badge-hidden .mat-badge-content { display: none }`.

## Correct test idioms

- Visible-with-count: assert `.mat-badge-content` **is visible** AND `textContent === '<N>'`.
- Hidden: assert host carries `mat-badge-hidden` (or content span not visible). **Never** use empty `textContent` as the hidden criterion — it is never empty when a value is bound.
- Placement checks: `matBadgeSize="small"` with a single digit renders a ~6×6 px box anchored at the host's top-right corner — small but visible; overlap thresholds must account for the tiny box (measured: badge∩label = 1.39% of label area on the persona nav).

## Where this bites

Any Jest/Playwright spec pinning badge bindings after the 2026-10-17 persona nav change (commit `1c5c9ddba` re-anchored badges from a removed `mat-icon` onto the `<a>` host). Reference spec: `frontend/src/app/app.persona-nav.spec.ts` @ `a117176ed`.
