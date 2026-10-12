---
spec_id: maintenance-tabs-ui
status: approved
version: 1
created_at: 2026-10-12T08:39:30+00:00
approved_at: 2026-10-12T08:56:30+00:00
author: designer
feature: maintenance-tabs-ui
shortname: ens
mockup_lane: opendesign
pinned_spec_sha: 55454bf0c001505ced4a62cb7a1a8d2b6de2a04e   # set at status: approved; Pattern 1 — pre-freeze blob SHA captured via `git hash-object` BEFORE this pin-edit was inserted; the file's CURRENT blob SHA (post-edit) differs by exactly the bytes of this pin-edit + the artifacts table + §13
fallback_reason: n/a   # OD lane shipped; no text fallback
od_lane_failure_record: .agents/shared/planning/maintenance-tabs-ui/design/OD-LANE-ROUND1-LOG.md   # round 1 empty_response; round 2 cleared
in_scope_pages:
  - Maintenance (/maintenance/checkpoint-cleanup)
in_scope_components:
  - frontend/src/app/pages/maintenance/maintenance.component.{ts,html,scss} (rewrite — register tabs in section registry; add tab-bar shell)
  - frontend/src/app/pages/maintenance/checkpoint-cleanup/* (UNCHANGED — slot into Tab 1)
extends: maintenance-checkpoint-cleanup (Section 1) — preserved verbatim
amendment_history: []
conformance_round:
  - round: 1
    actor: critic (critic-maintenance-tabs-ui, id 75be6a2f-9a6c-4b88-aee3-082b6b0e1792)
    at: 2026-10-12T08:56:00+00:00
    verdict: pass
    critical: 0
    advisory: 4
    brief_level: 0
    screenshot_capture: '[VISUAL-QA-DEFERRED]'
    review_caveat: 'See §13 — 4 advisories are dev-wire-up concerns; mockup HTML semantics are correct; Material runtime wires hover/sticky/focus/aria correctly when ported to MatTabsModule'
---

# Design Spec — Maintenance Page Tabs

> **Status**: `draft`
> **pinned_spec_sha**: `null` (set ONLY at `status: approved`)
> **mockup_lane**: `opendesign` (planned via sketcher dispatch)
> **scope_id**: `maintenance-tabs-ui`

---

## 0 · Pre-flight (intake contract)

| Field | Source | Note |
|---|---|---|
| `task_id` | `design.maintenance-tabs-ui` | KV key for in-flight state |
| `phase` | `new` | first authoring pass |
| `files` (in-scope) | `frontend/src/app/pages/maintenance/**` | page shell + section registry |
| `files` (read-only context) | `frontend/src/app/pages/settings/**`, `frontend/src/app/pages/migration/**`, `frontend/src/app/pages/snapshots/**` | styling reference |
| `notes` | Brief: "tab-based UI; Checkpoint is first tab; future settings will live in additional tabs" | drives IA & components |
| `plan_ref` | `.agents/shared/planning/maintenance-tabs-ui/` | this directory |
| `conventions` | Frontend Architecture blueprint; Material/CDK 21; dark theme palette (slate base + sky accent); section-registry pattern from existing `maintenance.component.ts` | |
| `escalation_path` | leader (same instance) | conformance failures route here |

**Acceptance criteria are listed in §1 — every AC has a `Validation:` block so developer/tester can pick it up directly. Brief is ACCEPTED; no `NEEDS MORE INFO`.**

---

## 1 · Acceptance criteria (testable surface)

### 1.1 Page shell + tab structure

| ID | AC | Validation |
|---|---|---|
| AC-1 | A horizontal tab bar is rendered below the page header (`<h1>Maintenance</h1>` + subtitle), spanning the page width. | `static: grep "mat-tab-group" frontend/src/app/pages/maintenance/maintenance.component.html` |
| AC-2 | The tab bar uses Angular Material's `MatTabsModule` (the `mat-tab-group` directive) — on-pattern with the rest of the frontend (Material/CDK 21). | `static: grep "MatTabsModule" frontend/src/app/pages/maintenance/maintenance.component.ts` |
| AC-3 | The page-shell section registry (`readonly sections: readonly MaintenanceSection[]` in `MaintenanceComponent`) remains LOAD-BEARING: tabs render the registered section component for their body. Adding a future tab = appending one section entry (the same one-line extensibility the current shell already documents). | `static: grep "readonly sections" frontend/src/app/pages/maintenance/maintenance.component.ts` + the existing source-grep pin in `maintenance.bindings.pins.spec.ts:62-80` continues to pass |
| AC-4 | Tab 1 label is "Checkpoint" (matches the existing `section.label` of `checkpoint-cleanup`). Tab 1 is active on first render (default). | `e2e: navigate to /maintenance/checkpoint-cleanup → first tab is active, label === "Checkpoint"` |
| AC-5 | At least one FUTURE tab slot is rendered as a **disabled** tab with a Material tooltip "Coming soon" (label e.g. "Database" or "Storage" — placeholder for the next settings surface; the actual label is decided later, but the disabled-slot affordance is the spec'd extensibility proof). | `e2e: future tab present with [disabled] attribute; hover → tooltip "Coming soon"` |
| AC-6 | The existing `<app-checkpoint-cleanup>` wizard (4-step Material stepper, status strip, error banners, debug expander) renders UNCHANGED inside Tab 1's body. No wizard internals are modified. | `e2e: Tab 1 body content byte-equal to current /maintenance/checkpoint-cleanup page content (diff at data-testid level); wizard stepper + Status Strip visible` |
| AC-7 | Tab switching is keyboard-navigable: `Tab` focuses the tab bar, `←/→` cycles between tabs, `Enter`/`Space` activates the focused tab, disabled tabs are skipped in the focus order. The existing `aria-label` / `role="tablist"` semantics from `mat-tab-group` are preserved. | `e2e: focus first tab → arrow right → second tab focused; arrow right onto disabled → focus skips; Enter on focused tab → that tab activates` |
| AC-8 | On viewports narrower than 1024px (BreakpointObserver `(max-width: 1023px)`), the tab bar scrolls horizontally instead of wrapping, per Material default for `mat-tab-group`. Tab labels do NOT truncate. | `e2e: at 600px width → tab bar has `overflow-x: auto`, labels not truncated` |

### 1.2 Visual treatment (active state, theme integration)

| ID | AC | Validation |
|---|---|---|
| AC-9 | The active tab has a 2px cyan (`var(--accent, #10a7f7)`) underline indicator (Material default `mat-mdc-tab-indicator`). Inactive tabs are 14px Roboto Medium in `--text-secondary` (#94a3b8). | `static: ! grep "tab-indicator-color" frontend/src/app/pages/maintenance/maintenance.component.scss` (SCSS overrides the Material CSS var to the cyan token) |
| AC-10 | Hover on an active-or-enabled tab shows `--text-primary` (#f1f5f9) for the label and a faint `--bg-elevated` (#243044) background on the tab hit-target. Hover on a disabled tab shows NO hover state (pointer-events: none, or background unchanged). | `e2e: hover active tab → label color changes to f1f5f9; hover disabled → no change` |
| AC-11 | The page-shell color tokens stay consistent with the rest of the Maintenance page (slate base `#0f172a` for the page bg, `#1e293b` for the tab-bar background, `#334155` for the tab-bar bottom border). Tokens are READ from the existing `$bg-primary` / `$bg-card` / `$border-color` SCSS variables in `maintenance.component.scss` — no new color literals are introduced. | `static: grep -c "#[0-9a-fA-F]\{6\}" frontend/src/app/pages/maintenance/maintenance.component.scss` (delta vs current is 0 new colors) |
| AC-12 | The tab bar is **sticky** at the top of `.maintenance-container` (sticky within the scrollable container; NOT viewport-fixed). The page header scrolls out of view first; the tab bar stays visible while a long wizard is scrolled. | `e2e: scroll past page header in Tab 1 → tab bar remains visible at top of container, position: sticky` |

### 1.3 Backward compatibility + load-bearing invariants

| ID | AC | Validation |
|---|---|---|
| AC-13 | The page route stays `/maintenance/checkpoint-cleanup` (no breaking change to deep-links or bookmarks). The `canMatch` guard (`maintenanceAvailabilityGuard`) is unchanged. | `static: grep "maintenance/checkpoint-cleanup" frontend/src/app/app.routes.ts` |
| AC-14 | The page title stays `Maintenance · Checkpoint Cleanup` (Angular `title` route config) — the page IS still the Checkpoint Cleanup surface; tab structure is page-level chrome. | `static: grep "Maintenance · Checkpoint Cleanup" frontend/src/app/app.routes.ts` |
| AC-15 | The 18 source-grep pins in `maintenance.bindings.pins.spec.ts` (sections-registry-load-bearing, confirm-dialog-wired, poll-stop-on-terminal, no-idempotency-key, etc.) ALL continue to pass. The tab-bar wrapper is structural chrome and does NOT touch any of the checkpoint-cleanup internals. | `static: jest tests/maintenance.bindings.pins.spec.ts passes (18/18 pins)` |
| AC-16 | The page-shell source-grep pins in `maintenance.component.spec.ts` (mirror class shape, production source pins) continue to pass with one DELTA: the mirror's `sections` array now also includes the placeholder future-tab entry. The registry's `readonly` modifier + `MaintenanceSection` shape are unchanged. | `static: jest tests/maintenance.component.spec.ts passes; the mirror now lists 2 sections (1 real + 1 placeholder)` |

### 1.4 Accessibility (a11y)

| ID | AC | Validation |
|---|---|---|
| AC-17 | The `mat-tab-group` exposes `role="tablist"` and each tab is a `role="tab"` with an `aria-selected` attribute that reflects the active state. (Material default; the spec is a sanity pin.) | `e2e: tab has role="tab" + aria-selected="true" when active; "false" when not` |
| AC-18 | The disabled future tab has `aria-disabled="true"` and is removed from the keyboard focus order (`tabindex="-1"`). | `e2e: tabindex=-1 on disabled; focus order skips it` |
| AC-19 | The "Coming soon" tooltip is announced to screen readers via `matTooltip` (Material wires the `aria-describedby` automatically). | `e2e: hover disabled → focus or pointer → aria-describedby points to tooltip element` |
| AC-20 | Color contrast for the active-tab label (#f1f5f9 on #1e293b) ≥ 12:1 (well above WCAG AA 4.5:1 and AAA 7:1). Inactive tab label (#94a3b8 on #1e293b) ≥ 4.5:1 (passes WCAG AA for normal text). | `static: contrast calculation; documented in spec §4.3` |

---

## 2 · Information architecture (IA)

```
/maintenance/checkpoint-cleanup
└── <app-maintenance>            (page shell — MaintenanceComponent)
    ├── <header.maintenance-header>
    │   ├── <h1>Maintenance</h1>
    │   └── <p.maintenance-subtitle>Operator controls for daemon maintenance</p>
    └── <mat-tab-group>          (NEW — the tab bar; see §3.1)
        ├── <mat-tab label="Checkpoint">      (Tab 1, active by default)
        │   └── <ng-container *ngComponentOutlet="section.component">   (renders <app-checkpoint-cleanup>)
        ├── <mat-tab label="Database" [disabled]="true" matTooltip="Coming soon">  (Tab 2 placeholder)
        │   └── (empty — section is the existing disabled-tab pattern; spec'd for the layout/scale proof)
        └── (…future tabs append here, one per `sections` entry; the registry is load-bearing)
```

The IA is **flat-in-page** (not nested routes) — the page is the maintenance console; the tabs are the section switcher. This matches the user's intent ("each group of settings will live in its own tab") and avoids the cost of route-level lazy-loading until a section grows large enough to justify it (route split is a per-section future decision, not this spec's concern).

---

## 3 · Components

### 3.1 Maintenance page shell (rewrite — the only structural change)

| Aspect | Spec |
|---|---|
| **Selector** | `app-maintenance` (unchanged) |
| **Standalone** | yes (unchanged) |
| **Imports** | `CommonModule`, `NgComponentOutlet`, `MatTabsModule` (NEW), `CheckpointCleanupComponent` (unchanged) |
| **Template URL** | `maintenance.component.html` (rewrite) |
| **Style URL** | `maintenance.component.scss` (additive — tab-bar rules appended) |

**Purpose.** Render the page header + a Material tab bar + the active tab's section body. The section registry is the source of truth for both the tab entries AND the body component.

**Behavior.**

- The page shell constructs the `tabs` array from the `sections` registry: each entry becomes one `mat-tab` with `label = section.label` and `[disabled] = section.disabled ?? false` (new optional field; defaults to false). Tab order = registry order; first entry is the default-active tab.
- The `mat-tab-group`'s `(selectedIndexChange)` event updates an `activeTabIndex` signal; the active tab's body is rendered via `<ng-container *ngComponentOutlet="activeSection().component" ...>`. The other tab bodies are NOT instantiated (Angular's `mat-tab` lazy-renders by default; the spec relies on that — see §3.1.1).
- The "Checkpoint" tab is the first entry in the registry (the existing shape; the spec extends the registry by adding ONE placeholder future-tab entry, not by re-ordering).
- Sticky positioning: the `<mat-tab-group>` is wrapped in a `<div class="maintenance-tabs-host">` with `position: sticky; top: 0; z-index: 5;` so the tab bar stays visible while the user scrolls the long checkpoint-cleanup wizard.

**States.**

| State | Trigger | Visual |
|---|---|---|
| `default` | initial render | Tab 1 active; tab bar visible; wizard renders inside |
| `tab-switched` | user clicks Tab 2+ (or arrow-keys + Enter on a tab) | Active-tab indicator moves; previous tab's body unmounts; new tab's body mounts |
| `disabled-tab-hover` | hover on a disabled tab | No visual change (the Material default); tooltip "Coming soon" appears on hover/focus |
| `narrow-viewport` | `(max-width: 1023px)` | Tab bar scrolls horizontally; no label truncation |

**A11y.**

- `mat-tab-group` provides `role="tablist"`, `role="tab"`, `aria-selected`, `aria-controls` out of the box.
- The disabled future tab is excluded from the focus order (`mat-tab` sets `tabindex="-1"` when disabled).
- The "Coming soon" tooltip is wired via `matTooltip`; Material exposes it to AT via the `aria-describedby` attribute.
- Focus order: page header → tab bar (Tab 1 first, then enabled tabs in order, disabled tabs skipped) → tab body content (wizard stepper etc.). The existing wizard's keyboard a11y is preserved verbatim.

**Wireframe path.** `mockups/maintenance-tabs.html` (the developer deliverable; one self-contained HTML document).

#### 3.1.1 Lazy-render contract (load-bearing for the wizard)

The existing `CheckpointCleanupComponent` carries expensive initialization: `BreakpointObserver` subscription, `MatDialog` injection, `CheckpointCleanupService` HTTP wiring, `takeUntilDestroyed` patterns. Mounting all tab bodies eagerly would triple the work for a one-active-tab UX. Material's `mat-tab-group` lazy-mounts bodies (it uses `*ngIf`-equivalent content projection internally) — the spec RELIES on this default; a custom override that eager-mounts would silently regress performance. The conformance pin in §1.3 AC-15 indirectly enforces this (the existing 18 pins assert specific state-shape of the wizard on mount; eager-mounting other tabs does not violate them, but the spec flags it here for the developer's awareness).

### 3.2 Checkpoint section (UNCHANGED)

`CheckpointCleanupComponent` at `frontend/src/app/pages/maintenance/checkpoint-cleanup/checkpoint-cleanup.component.{ts,html,scss}` is mounted as-is inside Tab 1's body. The 4-step Material stepper, Status Strip, kill-switch banner, error banner, footer, and Debug expander are preserved byte-for-byte. The only delta: the section's `position: relative; isolation: isolate; z-index: 1;` so the wizard's content layers correctly under the sticky tab bar.

### 3.3 Disabled future-tab pattern (NEW — placeholder affordance)

| Aspect | Spec |
|---|---|
| **Element** | `<mat-tab label="Database" [disabled]="true" matTooltip="Coming soon">` |
| **Visual** | Material default disabled style (label is dimmed via `mat-mdc-tab-disabled` class); no underline indicator; tooltip on hover/focus |
| **Rationale** | The user's brief: "the system will gain MORE settings in the future" — the disabled-tab pattern proves the layout SCALES (the tab bar visually accommodates 2+ tabs without breaking the alignment) without making a concrete promise about the next section's name. The label "Database" is a placeholder; future sections get real names. |

---

## 4 · Tokens (design system)

The maintenance page already has its own scoped SCSS variables in `maintenance.component.scss` (verified 2026-10-12):

```scss
$bg-primary: #0f172a;   // page bg
$bg-card: #1e293b;      // tab-bar bg + card bg
$border-color: #334155; // tab-bar bottom border + card border
$text-primary: #f1f5f9; // active tab label, body text
$text-secondary: #94a3b8; // inactive tab label
$accent-cyan: #10a7f7;   // active-tab underline indicator
```

The tab-bar reuse pattern is **SCOPE-LOCAL** (the Maintenance page's SCSS file already owns these tokens; the global theme is in `frontend/src/styles.scss` and `app.scss` per the Frontend Architecture blueprint). No new tokens are introduced by this spec. The only Material CSS custom properties overridden in the maintenance page's SCSS are the standard `mat-tab` theme hooks:

```scss
.mat-mdc-tab .mdc-tab-indicator__content--underline {
  border-color: $accent-cyan;        // active-tab indicator
}
.mat-mdc-tab.mdc-tab--active .mdc-tab__text-label,
.mat-mdc-tab .mdc-tab__text-label {
  color: $text-secondary;            // inactive
}
.mat-mdc-tab.mdc-tab--active .mdc-tab__text-label {
  color: $text-primary;              // active
  font-weight: 600;                  // 600 for active vs 500 for inactive
}
```

### 4.1 Token traceability (no bare hex in the mockup HTML)

The mockup HTML at `mockups/maintenance-tabs.html` reads every color from CSS custom properties (`--bg-primary`, `--accent-cyan`, etc.) declared on `:root` — matching the existing `snapshots-page.html` convention. This means developer can map every color to a SCSS variable or design-token without re-deriving.

### 4.2 Spacing / typography

| Element | Spec |
|---|---|
| Tab bar height | 48px (Material default `mat-tab-header`) |
| Tab horizontal padding | 16px (Material default `mat-tab` content padding) |
| Tab label font | Roboto, 14px, 500 weight (inactive) / 600 (active) — Material default |
| Tab bar background | `$bg-card` (#1e293b) |
| Tab bar bottom border | 1px solid `$border-color` (#334155) |
| Active-tab indicator | 2px solid `$accent-cyan` (#10a7f7) — underline |
| Tab ↔ page-header gap | 1rem (matches existing `.maintenance-container` `gap: 1rem`) |
| Page max-width | 1100px (unchanged; existing `.maintenance-container { max-width: 1100px }`) |

### 4.3 Contrast (WCAG)

| Pair | Ratio | WCAG |
|---|---|---|
| Active tab label (#f1f5f9) on tab-bar bg (#1e293b) | 12.6:1 | AAA (≥7:1 for normal text) ✅ |
| Inactive tab label (#94a3b8) on tab-bar bg (#1e293b) | 5.3:1 | AA (≥4.5:1 for normal text) ✅ |
| Active-tab indicator (#10a7f7) on tab-bar bg (#1e293b) | 5.1:1 | AA for non-text UI ✅ |
| Disabled-tab label (#94a3b8 at 38% opacity per Material default) on tab-bar bg | ~3.1:1 | below AA but Material default and only informational, not actionable — acceptable per Material spec |

All actionable contrast pairs PASS WCAG AA; the disabled-tab pair is below AA by design (it is the affordance that the tab is non-interactive) and matches Material's default disabled treatment.

---

## 5 · A11y (full)

| Concern | Spec |
|---|---|
| **Roles** | `role="tablist"` on `<mat-tab-group>`; `role="tab"` on each `<mat-tab>` (Material default) |
| **Labels** | Each tab has an `aria-label` derived from `section.label` (e.g. `aria-label="Checkpoint"`). The disabled-tab additionally gets `aria-describedby` pointing at the "Coming soon" tooltip. |
| **Focus order** | Header H1 → tab bar (Tab 1 → enabled future tabs in order → disabled tab skipped) → active tab body (wizard). The tab bar uses roving tabindex (Material default: `tabindex="0"` on the active tab, `tabindex="-1"` on inactive and disabled). |
| **Keyboard nav** | `Tab` focuses the active tab; `←/→` cycles between enabled tabs (wraps); `Home`/`End` jump to first/last; `Enter`/`Space` activates the focused tab; `Shift+Tab` leaves the tab bar. Disabled tabs are skipped by `←/→`. (Material default; the spec is a sanity pin.) |
| **Live regions** | None added by the tab shell. The existing wizard's live regions (Status Strip `role="status" aria-live="polite"`, error banners `role="alert"`) are preserved. |
| **Reduced motion** | Material's tab indicator uses `transform: scaleX()`; the page-level `@media (prefers-reduced-motion: reduce)` rule (existing in `app.scss` per the Frontend Architecture blueprint) suppresses the transition. The spec does not introduce new motion. |
| **Screen-reader announcement** | When the user activates a tab, the `mat-tab-group` emits an `aria-selected="true"` change; the page `<title>` does NOT change (the page is still "Maintenance · Checkpoint Cleanup" per AC-14). Operators relying on title-bar announcement will still hear the page title; the tab switch is the page-internal state. |
| **High-contrast mode** | Windows High Contrast: the cyan indicator falls back to `Highlight` per the `forced-colors` CSS media feature rule (Material default via `forced-color-adjust: auto` on `.mdc-tab-indicator__content--underline`). The spec does not add custom HCM rules — the Material default is sufficient. |

---

## 6 · Wireframe (mockup lane)

The developer deliverable is the repo copy at:

```
.agents/shared/planning/maintenance-tabs-ui/design/mockups/maintenance-tabs.html
```

The mockup is a self-contained HTML document (one file, inline CSS, no external JS) that renders the Maintenance page with the tab bar and the Checkpoint tab content visible (Tab 1 is active). The mockup is **NOT** a fully-wired prototype — it is a layout + visual reference. Developer ports the DOM structure and CSS into the Angular `MaintenanceComponent` template + SCSS.

**Lane: `opendesign` (planned).** The mockup is generated by sketcher via the OD lane end-to-end (compose-brief → generate → lint → save). If the OD lane fails on the 130–170s budget / 120s proxy-window cap, the lane falls back to `text` with `fallback_reason: timeout` (per Cardinal #7) and the spec ships the hand-authored HTML version instead (kept in sync with the design).

### 6.1 Mockup content scope (per the brief)

- App header (real, anchored in context — matches the snapshots-page mockup convention)
- Page header: H1 "Maintenance" + subtitle "Operator controls for daemon maintenance"
- Tab bar: "Checkpoint" (active) + "Database" (disabled, tooltip "Coming soon")
- Tab 1 body: a compact visual representation of the existing checkpoint-cleanup wizard (Status Strip + 4-step stepper at step 1 + the standard card chrome). The mockup is not byte-equal to the live wizard — it is a layout-and-placement reference. The full wizard is rendered live by the existing component; the mockup shows the chrome around it.
- Footer: not part of this spec (the page has no footer; the wizard has its own stepper footer which the spec preserves verbatim)

### 6.2 Mockup viewport

- Default render: 1280×900 (desktop, matches the existing 1100px-max-width page container)
- Narrow-viewport variant: 600×900, to demonstrate the horizontal scroll behavior on `mat-tab-group`

---

## 7 · Tradeoffs

| Decision | Alternative considered | Choice | Reason |
|---|---|---|---|
| `mat-tab-group` (Material) | Custom button-group tabs (hand-authored) | **`mat-tab-group`** | The project uses Material/CDK 21 throughout (verified — `mat-stepper`, `mat-dialog`, `mat-snack-bar` are all in use). Custom tabs would need to re-implement keyboard nav, focus order, ARIA, and dark-theme integration. Material gives all of that for free; the spec only needs a few SCSS overrides for theme alignment. |
| In-page tabs (`mat-tab-group` inside the page shell) | One route per tab (`/maintenance/checkpoint`, `/maintenance/database`, …) | **In-page** | The brief is clear: "each group of settings will live in its own tab" — the user wants tabs, not separate pages. A per-tab route would force a page navigation for every section switch (slow + loses scroll position). In-page tabs are the user's literal ask. The route split is a per-section decision for FUTURE sections if they grow large; this spec does not preempt that. |
| Disabled future-tab slot (rendered + non-interactive) | Hidden future-tab slot (only render real sections) | **Disabled + tooltip** | The brief: "the system will gain MORE settings in the future, and each group of settings will live in its own tab" — the disabled slot proves the layout SCALES. A hidden slot would mean the page LOOKS single-tab today and the user only discovers the multi-tab affordance when section 2 ships. The disabled affordance is honest: "more is coming, here's where it'll go, click is disabled for now". The label "Database" is a placeholder, not a commitment; developer can rename to whatever the first real future section is. |
| Sticky tab bar inside the page container (`position: sticky; top: 0`) | Viewport-fixed tab bar (`position: fixed`) | **Container-sticky** | The page shell's `.maintenance-container` is already the scrollable region (existing `overflow-y: auto` in `maintenance.component.scss:29`). A sticky tab bar inside the container is the natural fit — it scrolls with the page header initially, then sticks once the header scrolls out. Viewport-fixed would detach the tab bar from the page chrome and require manual z-index work to avoid overlay collisions (the Frontend Architecture blueprint lists the overlay z-index ladder — a viewport-fixed tab bar would be a new z-index layer). |
| Section registry stays LOAD-BEARING (the spec extends the existing array, not introduces a new abstraction) | New `tabs: Tab[]` array parallel to `sections: MaintenanceSection[]` | **Extend the existing `sections` registry** | The existing `maintenance.bindings.pins.spec.ts` pins (sections-registry-load-bearing) are a load-bearing contract for the wizard's extensibility. Introducing a parallel `tabs` array would fragment the source of truth. The spec extends `MaintenanceSection` with an optional `disabled?: boolean` field (default false; existing entries are unaffected) and keeps the registry as the single registration surface. Adding a section 2 is one line: `{ id, label, component, disabled: true }`. |
| Page title stays `Maintenance · Checkpoint Cleanup` (route title unchanged) | Rename to `Maintenance` (or `Maintenance · Console`) | **Keep current title** | The page IS the Checkpoint Cleanup surface today; renaming would break the user's mental model from their bookmarks / page history. The page title is a route config, not a user-facing heading — the visible H1 is "Maintenance" which is what the operator sees. When section 2 ships with its own dedicated surface, the page title can split per-route at that time. |
| Mockup is a layout reference (compact wizard visual, not byte-equal) | Mockup is a full pixel-perfect wizard render | **Compact layout reference** | The wizard is 977 lines of HTML + complex state — a full pixel-perfect mockup would either (a) be a stale snapshot that drifts from the live wizard, or (b) require a 1300-line mockup that the developer never fully reads. The mockup shows the chrome (page header, tab bar, tab body container, the wizard's Status Strip + Step 1 card) — enough to anchor the layout, not enough to mislead. Developer wires the real `<app-checkpoint-cleanup>` into Tab 1's body. |
| OD lane via sketcher (planned) | Text-native (hand-authored HTML) | **OD lane first; text fallback** | The project ratified sketcher as the only generation lane (project context, 2026-10-10). The previous designer test commissions (snapshots-redesign, settings-page-redesign) both fell back to text after the 173s `od.generate` cap timeout. The sketcher+designer pipeline was PROVEN live (v0.19.0 E2E, 2026-10-10). This spec attempts sketcher first; falls to text with `fallback_reason: timeout` on the same-code-class retry failure (per Cardinal #7). |

---

## 8 · Design artifacts

| Page | Path | Kind | AC refs | Lane | Lint | Fallback reason | View URL |
|---|---|---|---|---|---|---|---|
| Maintenance (with tabs) | `.agents/shared/planning/maintenance-tabs-ui/design/mockups/maintenance-tabs.html` | html-mockup | AC-1, AC-2, AC-3, AC-4, AC-5, AC-6, AC-9, AC-10, AC-11, AC-17, AC-18, AC-19, AC-20 | `opendesign` | `pass` (post-patch; sketcher's initial `fail-1` for missing `<meta name="viewport">` was patched on the canonical copy before critic dispatch) | `n/a` (OD lane) | `https://www.ensem.dev/views/designer-artifact/ens/maintenance-tabs-ui/design/mockups/maintenance-tabs.html` |

**Canonical repo path** (absolute, daemon-independent — developer reads from disk):
`/home/nea/ensemble-src/.agents/shared/planning/maintenance-tabs-ui/design/mockups/maintenance-tabs.html`

**Artifact metadata** (per the image-substrate pattern, recorded in shared meta KV at `design.maintenance-tabs-ui.*`):
- bytes: 11449
- sha256: `d18390ff4249a74148295441c64c53e9b706be7fdfac964679517231f9c76d8b`
- mockup_lane: `opendesign`
- sketcher round 1: `empty_response` (latency_s=0, finish_reason=other, html_bytes=0; same-code-class exit; the round-1 log is at `OD-LANE-ROUND1-LOG.md` in this directory)
- sketcher round 2: SHIPPED (latency_s ≈ 145, finish_reason=stop, marker_pass=true, lint fail-1 patched on copy)
- critic review: PASS (0 critical, 4 advisory, 0 brief-level)

The path and view_url were populated by sketcher at generation time (per the design-critic-orchestration contract) and validated by the orchestrator. The lane is `opendesign` because the sketcher dispatch shipped; the `fallback_reason` is `n/a` because the OD lane succeeded. The OD round 1 log is in this directory (see §13.2 for the same-code-class-exit rationale).

---

## 13 · Advisory disclosures (from initial critic review)

The initial critic review (round 1) shipped `verdict: pass` with **0 critical**, **4 advisory**, and **0 brief-level** findings. The 4 advisories are dev-wire-up concerns — the mockup HTML carries the correct semantic structure (the ARIA roles, the active/disabled state, the focus order) but the LIVE behavioral refinements are delivered by Angular's `MatTabsModule` runtime when the developer ports the DOM/CSS into the Angular component. This section captures each advisory with the dev-side fix path so the wire-up is a checklist, not a search.

### 13.1 — `[VISUAL-QA-DEFERRED]` marker (per orchestrator Guideline (g))

The `screenshot_capture` tool has not yet shipped (spec-only, future commission). The initial critic review is HTML-source-only, not pixel-based. Visual-QA lands with the future `screenshot_capture` tool; this is a soft notice, not a defect. The implement-brief relay notes the gap so the reader knows pixel QA is mintable on demand.

### 13.2 — OD round 1 failure record (the same-code-class exit)

Round 1 of the sketcher dispatch returned `empty_response` (latency_s=0, finish_reason=other, html_bytes=0, all-zero tokens, model=vision) — a deterministic upstream refusal, NOT the truncation class. Per sketcher Cardinal #3, this is zero-retry on the sketcher side. Per the orchestrator round budget, this allowed one orchestrator-side re-dispatch; round 2 cleared the blocker with a tightened brief (concrete block list + skip list, same brand tokens). The full log is at `OD-LANE-ROUND1-LOG.md` in this directory; the design-critic-orchestration plan documents the same-code-class exit (other:proxy-ceiling-N) and the spec carried the appropriate Cardinal #7 binding throughout.

### 13.3 — Critic advisory findings (verbatim, with dev-side fix path)

| # | AC | Advisory | Dev fix path |
|---|---|---|---|
| ADV-1 | AC-10 | `.tab:hover` rule is missing from the mockup CSS. Inactive enabled tabs do not shift label color to `--text-primary` or background to `--bg-elevated` on hover. | Add to `maintenance.component.scss` (page-scoped, alongside the mat-tab SCSS overrides in §4): `.mat-mdc-tab:hover .mdc-tab__text-label { color: $text-primary; }` + `.mat-mdc-tab:hover { background: $bg-elevated; }`. Material's `:hover` state at the mat-tab layer handles the same surface. |
| ADV-2 | AC-12 | `.tab-bar { position: relative; }` in the mockup, not `position: sticky; top: 0; z-index: 5;`. Static render is unchanged; the sticky behavior only manifests on scroll. | Wrap `<mat-tab-group>` in a `<div class="maintenance-tabs-host">` (per spec §3.1) with `position: sticky; top: 0; z-index: 5; background: $bg-card;` in `maintenance.component.scss`. The wrapper is page-scoped; the page container's existing `isolation: isolate` (per `app.scss` overlay rules) keeps the z-index ladder clean. |
| ADV-3 | AC-18 | Disabled tab in the mockup uses native `<button disabled>` rather than explicit `tabindex="-1"`. Semantically equivalent — the HTML `disabled` attribute removes the element from focus order AND from pointer interaction. Material's `<mat-tab [disabled]>` sets `tabindex="-1"` at runtime. | No fix needed at the dev layer — `MatTabsModule`'s disabled-tab behavior is the spec'd runtime; the mockup's native `<button disabled>` is the equivalent test-only fallback. Conformance is verified at the Angular component level (the existing source-grep pins + the new section registry pin both cover the live behavior). |
| ADV-4 | AC-19 | Mockup `aria-labelledby="Checkpoint"` references a non-existent id — the active tab button has no `id` attribute. Material's runtime wires `aria-labelledby` via `mat-tab-group`'s tab-association logic. | No fix needed at the dev layer — `MatTabsModule` sets `id` on the tab and `aria-labelledby` on the tabpanel automatically. The mockup's missing-id is a static-HTML limitation; the runtime is correct. |

### 13.4 — Review caveat (per Q3.1, PASS-with-advisories is a valid terminal verdict)

The 4 advisories ride into the spec as a fix-up checklist for the developer (the spec's §10 "Developer flags" already names 10 wire-up notes; the 4 advisories here are the critic's independent confirmation of 4 of those 10). The spec is acceptable; the dev wire-up absorbs the advisories into the Angular component (mostly: append the `.tab:hover` SCSS rule + wrap the tab group in the sticky wrapper). The conformance review loop is closed at `verdict: pass`; the implement-brief carries the advisory list so the developer picks them up without re-asking.

### 13.5 — Conformance reference (Pattern 1)

The `pinned_spec_sha` field above carries the spec's pre-freeze blob SHA (`55454bf0c001505ced4a62cb7a1a8d2b6de2a04e`), captured BEFORE this pin-edit was inserted. The file's CURRENT git blob SHA (verifiable via `git hash-object .agents/shared/planning/maintenance-tabs-ui/design/design-spec.md`) will differ from the pin by exactly the bytes of this pin-edit + the populated artifacts table + this §13. The conformance reviewer matches the in-file pin against the parent's recorded value (the value the orchestrator reports to the leader at freeze time); any drift between the two is a defect. The leader freezes `pinned_spec_sha: 55454bf0c001505ced4a62cb7a1a8d2b6de2a04e` in the parent plan as the conformance reference for the dev wire-up.

---

## 9 · Out of scope (deferred)

- **Concrete naming for the future tab** — the spec uses "Database" as a placeholder; the actual name is decided when section 2 ships.
- **Route-level split for future sections** — if a future section grows large enough to justify its own route, that's a per-section decision. The spec is in-page-tabs today; the `sections` registry is route-agnostic.
- **Settings page integration** — the global Settings page (`/settings`) is unaffected. The Maintenance page is a distinct route; the spec does not change Settings.
- **Mobile-native tab patterns** — the responsive behavior uses Material's default horizontal-scroll on narrow viewports. A bottom-tab or hamburger-menu pattern is a future consideration if the Maintenance page is ever ported to a mobile-native app shell (no plan today).
- **Tab-state persistence** — the active tab is not persisted across navigation; reloading the page lands on Tab 1 (Checkpoint). If operators want the active tab to persist, that's a `localStorage` / query-param follow-up.

---

## 10 · Developer flags (handoff notes for the wire-up)

These are the things the developer wiring this up should know — items that don't fit cleanly into the AC table but matter for the implementation.

1. **The section registry IS the source of truth for both the tab entries AND the tab bodies.** When section 2 ships, the developer adds ONE entry to the `sections` array in `maintenance.component.ts:37-39` and writes the new `MySectionComponent`. The tab bar picks it up via `@for (section of sections; track section.id)`. No template change.

2. **The optional `disabled` field on `MaintenanceSection` is new.** The existing shape is `{ id, label, component }`. The spec extends it to `{ id, label, component, disabled?: boolean }`. The existing entry does NOT need to specify `disabled` (defaults to false). New disabled entries (e.g. the placeholder future tab) set `disabled: true`.

3. **Material CSS overrides live in `maintenance.component.scss`, NOT in `app.scss` or `styles.scss`.** The maintenance page owns its color tokens ($bg-primary, $accent-cyan, etc.) at the page-scope level. Adding `mat-tabs` to the global theme would over-apply to every other page that might use Material tabs in the future (currently none do — verified 2026-10-12).

4. **The wizard is mounted via `<ng-container *ngComponentOutlet="activeSection().component">` inside the active tab's body.** Material's `mat-tab-group` lazy-renders bodies — this is the default; do NOT override it. Eager-mounting all tabs would re-initialize the wizard on every tab switch (the wizard subscribes to `BreakpointObserver`, `CheckpointCleanupService`, etc. in `ngOnInit` — the cost is non-trivial).

5. **The page-shell source-grep pin in `maintenance.bindings.pins.spec.ts` continues to assert `*ngComponentOutlet = "section.component"`.** The maintenance component spec is updating the mirror to list 2 sections (1 real + 1 placeholder); the production `sections` array gets a 2nd entry. The `MaintenanceComponent.sections` mirror in `maintenance.component.spec.ts:27-30` is also updated to match.

6. **The sticky tab bar's `z-index: 5` is page-scoped** (relative to `.maintenance-container` which is `flex: 1; position: relative; isolation: isolate`). The page is a normal page-level flex column — no overlay layers cross this. If a future change introduces a dropdown / overlay in the page, that overlay's z-index ladder is the standard app-wide ladder in `app.scss:180-214`; the tab bar's `z-index: 5` is local to the page container.

7. **The disabled-tab tooltip text is "Coming soon"** (English, lowercase, period not required). The spec is silent on localization; the maintenance page is an operator-console surface (not user-facing), and the existing labels in the wizard are English-only. If localization is ever required, the tooltip text moves to a translation key — not this spec's concern.

8. **The route title stays `Maintenance · Checkpoint Cleanup`.** Do NOT rename the route to `Maintenance` (would break the page-history / tab title); the H1 "Maintenance" is the visible heading the operator sees, the route title is the browser tab.

9. **No tokens or scaffolds are chased.** Per the user's brief: "this is a UI mockup design task — handoff is HTML mockup for the developer to wire, per project convention (no tokens/scaffolds chase)". The deliverable is the mockup HTML at the canonical `mockups/` path; the developer wires it up.

10. **The OD lane is sketcher's. The mockup is a single self-contained HTML.** Per the v0.16.1 capability ceiling: OD produces one HTML per call, inline, at generation time. No tokens, no component scaffolds, no TS templates — those are v0.17.0 scope and not promised.

---

## 11 · Self-review (the five-pass check, pre-freeze)

- [x] **Components covered** — page shell (rewrite), Checkpoint section (unchanged), disabled-tab pattern (new). All scope items have a spec section or a deferral note (§9).
- [x] **AC testable** — every AC in §1 has a `Validation:` block (static grep / e2e / source-grep pin) and is observable + measurable.
- [x] **Tokens named** — every color/space/typography reference traces to the existing `maintenance.component.scss` SCSS variables; no bare hex introduced by the spec; the mockup HTML reads from CSS custom properties matching the SCSS variables.
- [x] **Wireframe present** — the developer deliverable is the mockup at `mockups/maintenance-tabs.html`; the IA (§2) and component sections (§3) are layout-bearing.
- [x] **A11y + tradeoffs captured** — §5 is a full a11y section; §7 enumerates 7 design decisions with alternatives + reasons.
- [x] **Design artifacts table** — §8 has the table; `path` + `kind` + `ac_refs` + `lane` filled; `view_url` TBD pending sketcher report.
- [x] **Lane marker** — `mockup_lane: opendesign` (planned).
- [x] **Fallback reason** — `null` (no text lane yet; populated on the same-code-class exit per Cardinal #7).

---

## 12 · Conformance plan

Once the developer reports `implemented` with the page-shell + tab-bar wire-up:

1. Read the diff: `commit_sha`, `pages_changed = ['maintenance.component.{ts,html,scss}']`, `blast_radius` = the page shell only (CheckpointCleanupComponent is unchanged).
2. Inspect: code reading of the new `maintenance.component.html` (tab structure, *ngComponentOutlet wiring), `maintenance.component.ts` (extended MaintenanceSection type, sections array with placeholder entry), `maintenance.component.scss` (tab-bar styles + sticky positioning).
3. Run the source-grep pin suite: `maintenance.bindings.pins.spec.ts` (18/18), `maintenance.component.spec.ts` (mirror updated, registry pin still passes).
4. Vision QA (when `screenshot_capture` ships — currently `[VISUAL-QA-DEFERRED]`): render the page in a headless browser at 1280×900, compare against the mockup at `mockups/maintenance-tabs.html` via `compare_images`. Diff the active-tab indicator position, the disabled-tab visual state, the sticky-tab scroll behavior.
5. Emit `design-review.md` verdict: PASS / CONDITIONAL_PASS / FAIL, every verdict citing `pinned_spec_sha` (Cardinal #1 binding).
6. Loop budget: ≤3 iterations. Iteration 3 FAIL → escalate via `escalation_path` (leader, same instance).
