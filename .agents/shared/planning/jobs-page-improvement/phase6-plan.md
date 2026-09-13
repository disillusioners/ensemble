# Phase 6: Accessibility, Mobile & Test-Completion Close-Out

Date: 2026-09-10 · Author: planner[v2] via plan-creation worker · Status: Draft
Arc: jobs-page-improvement · Direction: A as C-shaped groundwork

## Objective

Close the polish debt that blocks trust and broad use: port the panel's WAI-ARIA patterns to the grouped list (P7), and fill the missing spec suites the research flagged (P9) — ending with a green, fully-attributed build. **Mobile ≤768px sidebar sheet (task 4) is SKIPPED-per-leader in this arc — see D9/OQ-7 entry in `decisions.md` (as-built reconciliation, 2026-09-12).**

## Shared Context (true at phase start)

- Phases 1–5 landed: grouped virtual list (Phase 3 gives the tree-ish structure ARIA attaches to), drawer gates fixed, URL state live, defer banner present.
- A11y baseline (research): **exactly one `aria-label` on the whole page template** (`html:19`); card expand state not `aria-expanded`; icon-only semantics (`sync`, `wifi` spinners) covered by `title` only; no keyboard model. The panel already demonstrates the full pattern to port (alignment §7): `role="tree"`+`aria-label` sections, `role="treeitem"`+`aria-level`+`aria-expanded`, real-button chevrons, clamped arrow nav (no wrap), focus-id single-writer via `(focus)`, Enter/Space activation, Enter `preventDefault` in mat-menu contexts (F2 trap, live-reproduced 2×).
- Mobile baseline: single 768px breakpoint (`jobs.component.scss:573`); sidebar never collapses in queues view; chip row wraps only. Menu-shell gotcha: global `.mat-mdc-menu-panel` caps menus at 280px — any sheet/select override must be a GLOBAL `panelClass` rule (CDK overlay mounts at `<body>`; `::ng-deep` component-scoped can never match).
- Spec gaps (research §7): no component spec for `system-cleanup-confirm-dialog` (only `cleanup-preflight.model.spec.ts` consts), none for `queue-list`, none for `job-create-dialog`; `work.service.spec.ts` is a MIRROR (real-service construction parity pinned in Phase 1 task 7). P9.2: any redesign must pair with the template-extraction audit — this phase is the final audit sweep.
- Known constraint: 10 pre-existing build warnings (6 scss budgets, 1 bundle, NG8113, 2 Sass deprecations) — the arc must end with exactly that corpus, every new warning attributed.

## Components / Services / Models Touched

| Path | Action |
|---|---|
| `frontend/src/app/pages/jobs/jobs.component.html` / `.scss` / `.ts` | Modify — ARIA roles, keyboard model, responsive rules |
| `frontend/src/app/pages/jobs/jobs-keyboard.model.ts` (+ `.spec.ts`) | **NEW** — clamped nav over flattened items (port `nextInstanceTreeItem`, `instance-node.model.ts:664-676`) |
| `frontend/src/app/components/queue-list/` | Modify — mobile collapse (sheet/select) + **NEW** `.spec.ts` |
| `frontend/src/app/components/system-cleanup-confirm-dialog/` | **NEW** `.spec.ts` (component logic; copy consts already pinned at model level) |
| `frontend/src/app/components/job-create-dialog/` | **NEW** `.spec.ts` |
| `frontend/src/styles.scss` | Only if a global sheet/menu-width `panelClass` rule is required |

## Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 1 | ARIA port to grouped list: `role="tree"` + per-section `aria-label`; group headers = `role="treeitem"` + `aria-level` + `aria-expanded`; chevron = real `<button type="button">` with `aria-expanded`/`aria-label` + `stopPropagation`; rows `role="button" tabindex="0"` | P3 structure | Grep specs pin role/aria-expanded presence; chevron tap never toggles selection |
| 2 | Keyboard model: ArrowUp/Down move REAL DOM focus over the flattened items (clamped — no wrap); ArrowRight expands collapsed group; ArrowLeft collapses (child ⇒ nearest ancestor); Enter/Space activate; focus-id single-writer via `(focus)`; Enter `preventDefault()` wherever rows live in a mat-menu context (F2 trap). **CROSS-WORKER NOTE — keyboard focus × virtual-scroll DOM recycling:** focused rows scrolled out of the viewport get RECYCLED; every focus move must `scrollToIndex` on the `CdkVirtualScrollViewport` FIRST, then re-resolve `document.getElementById(id)?.focus()` after the render tick (viewport-container fallback when the id is not yet mounted) | Task 1 | `jobs-keyboard.model.spec.ts`: navigation table incl. clamp ends + ancestor-collapse case; DOM order == keyboard order by construction; recycling contract spec'd NOW — scrollToIndex-then-refocus-after-render-tick sequence pinned in the keyboard model contract |
| 3 | Icon-only semantics: `aria-label` on spinners/status/wifi glyphs; status chip listbox gets a programmatic label; live/reconnecting pill gets honest degraded aria-label (indicator parity `job-queue-indicator.component.html:20-29`); the Phase-4 **defer-holders-panel is IN a11y scope**: banner is an `aria-live` region, holder action rows keyboard-reachable with programmatic labels | Task 1 | Grep spec: zero `title`-only icon controls on the page incl. defer-holders-panel |
| 4 | Mobile ≤768px: queue sidebar collapses into a sheet (mat-select or bottom-sheet per existing primitives); queue selection moves with it (OQ-7 default, D9); chip row gets horizontal-scroll-or-wrap strategy; drawer already full-width (keep). **SKIPPED-per-leader (2026-09-12) — mobile scope dropped from this arc (D9/OQ-7 default deferred to a later arc; queue-list mobile-collapse sheet, queue-list mobile-crud spec, and the global `panelClass` width override land later, not here). The arc still exits on the Phase 6 close-out criterion with task 4 closed by deferral, not by completion.** | — | SCSS breakpoints pinned; sheet uses global `panelClass` width rule if a mat-menu is involved (280px cap gotcha) |
| 5 | `queue-list.component.spec.ts` (NEW): selection emit, queue CRUD action dispatch, mobile-collapse state logic — plain-TS mirror | Task 4 (states final) | Suite green; construction parity for any queue-list change |
| 6 | `system-cleanup-confirm-dialog.component.spec.ts` (NEW): two-stage confirm state machine, defer note rendering, destructive-copy surface (consts already verbatim-pinned at model level) | — | Suite green |
| 7 | `job-create-dialog.component.spec.ts` (NEW): form state → `createJob` payload mapping, invalid-state gating | — | Suite green |
| 8 | P5.3 copy: New Job disabled in all-work gets explanatory tooltip/copy ("task creation requires a queue — switch to Queues view") | — | Grep spec pins the copy |
| 9 | Final template-extraction audit sweep + warning attribution: diff-audit EVERY `(click)`/`(keydown)` hunk changed across the whole arc; build; classify all warnings vs known-10 | All | Warning corpus == known-10 or every delta attributed and justified |

## Dependencies

**Internal:** Phases 1–5 (tasks 1–2 need the Phase-3 grouped item list; task 4 assumes queue sidebar survives OQ-1 default). This is the close-out phase — nothing depends on it.

**External:** none. **GATE-COMBO-FIX** not implicated. **needs-BE:** none.

## Risks & Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| ARIA restructure is the arc's largest template diff | Medium | The mandatory template-extraction audit is THIS phase's task 9; keyboard model extracted to pure TS so specs carry the logic |
| Focus management inside virtual scroll (rows recycle) | Medium | Focus-id single-writer via `(focus)` event; arrow-nav re-resolves `document.getElementById(id)?.focus()` at press time (panel pattern `:402-477`) |
| Mobile sheet vs global menu-width cap | Low | Global `panelClass` override rule (styles.scss pattern `:87-96`); never component-scoped `::ng-deep` |
| New component specs mirror-drift from real components | Medium | Mirror-parity rule: each new spec pins real construction (grep the real `.set()`/event sites) per Testing & QC conventions |

## Test Strategy

New suites per tasks 5–7 (plain-TS, no TestBed, real-construction parity pins). Keyboard model truth-table spec. ARIA presence via grep specs (they pin TEMPLATE text — acceptable as pins, never as behavioral proof; behavior lives in the pure model). Mobile: breakpoint constants pinned + collapse-state logic spec'd. Final: full `npx jest` run, tsc, build with warning attribution table vs known-10.

## Verification Commands

```bash
cd frontend
npx tsc --noEmit -p tsconfig.app.json
npx jest jobs-keyboard queue-list system-cleanup-confirm-dialog job-create-dialog
npx jest   # full suite — arc-level regression gate
npm run build
```

## Sizing

**M (2–4 days).** ARIA/keyboard port (M) + mobile collapse (S–M) + three new spec suites (S each) — individually small, bundled because they share the final audit sweep and the grouped-list structure.

## Exit Criterion

Full keyboard traversal works (clamped, expand/collapse, activate) over the grouped list; zero title-only icon controls; ≤768px hides the sidebar behind a working sheet; the three missing suites exist and pass; `npm run build` green with warning corpus == known-10 or fully attributed; full jest suite green.
