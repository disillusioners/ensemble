---
spec_id: ck-redesign-2026q4
title: Maintenance — Checkpoint Cleanup Section Redesign
status: approved
phase: new
author: designer
created_at: 2026-10-01
approved_at: 2026-10-01
pinned_spec_sha: 429c1e57a4d150f0cf4605c94431edc658a7b9c1
task_id: ck-redesign-2026q4
plan_ref: .agents/shared/planning/maintenance-console/phase2-frontend.md
advisory_fields:
  direction: C (wizard / stepper) — user-selected 2026-10-01
  density: focused (non-active context hidden within steps; persistent Status Strip decouples monitoring)
  frozen_contract: preserved (5 endpoints, section-registry invariant)
  raw_json: collapsed under a single page-level "Debug" expander (not per-card)
  operator_freq: status strip at top solves monitoring without entering wizard
  width: 1100px max wizard, status strip wraps at <720px
  a11y: WCAG AA baseline; Material stepper primitives
  test_compat: data-testids preserved on inner elements
---

# Maintenance — Checkpoint Cleanup Section Redesign

> **Spec intent.** Replace the current single-820px-vertical-stack-of-cards layout (Status → Dry-run → Execute → Result) with a 4-step Material wizard + a persistent always-visible Status Strip. Symptoms named in the brief ("many column as step", "text short and have too many line", "scroll appears") are addressed by: (a) the Status Strip providing at-a-glance state without scrolling, (b) the wizard giving the operator one decision at a time, and (c) the 1100px container cap giving the stepper room on desktop while staying 1-col on narrow.

## 1. IA (Information Architecture)

The Checkpoint Cleanup section presents a one-operator, one-decision flow: **Review → Dry-run → Confirm & Execute → Result**.

Step 1 is read-only orientation. Steps 2-3 drive action. Step 4 renders automatically after execute completes (page-refresh-safe — `lastExecuteResult` is fed by the `status` endpoint's `last_run`, so a refresh during execute lands the operator on Step 4 with the actual result).

A persistent **Status Strip** above the stepper gives at-a-glance state so the operator does not need to enter the wizard to know whether the system is healthy.

### Navigation graph

```
   [Status Strip]   ← always visible, page-level, NOT gated by step
        │
        ▼
   ┌─ Step 1: Review ──► Step 2: Dry-run ──► Step 3: Confirm & Execute ──► Step 4: Result ─┐
   │  (read-only)             (gated on a          (gated on confirm-           (auto-renders  │
   │                            reasonable dry-run)   dialog acceptance)              post-execute) │
   └──────────────────────────────────────────────────────────────────────────────────────────────────────────────────┘
   • Back button visible from steps 2/3/4; Step 1 has no Back.
   • "Back to start" on Step 4 resets `activeStep()` to 0 and returns to Step 1.
   • Step labels rendered via Material stepper primitive (aria-current on active step).
```

### Pages preserved (no change)

- `maintenance/checkpoint-cleanup` route + page shell (`maintenance.component.{html,ts}`).
- Section registry in `maintenance.component.ts` stays load-bearing (one-line append invariant preserved — see AC-14).
- All 5 endpoints (`availability / status / dry-run / execute / runs/{id}`) keep current wire shape (see AC-17).

## 2. Components

### 2.1 Status Strip (always visible, page-level)

**Purpose.** Compact 3-tile summary so the operator lands → knows state → without entering the wizard. Solves the "scroll appears" complaint at the entry point.

**Location.** Above `<mat-stepper>`, below global banners (kill-switch / origin-guard).

**Content (3 tiles, dense monospace numbers, muted labels).**

| Tile | Term (`<dt>`) | Value (`<dd>`) |
|---|---|---|
| 1 | Keep N | `status().config.checkpoint_max_per_thread` |
| 2 | Last run | `status().last_run ? "{status} · {freed-or-deleted} · {formatTimestamp(last_run.completed_at)}" : "Never run"` |
| 3 | Dry-run fresh | `lastDryRun() ? "{scanned} pairs · expires in {formatTimestamp(dry.fresh_until)}" : "Stale — run dry-check"` |

**States.** `default` | `kill-switch-disabled-muted` (all tiles rendered but values replaced with "—" + muted subtitle) | `origin-blocked-muted` (same treatment)

**A11y.** `role="status" aria-live="polite"`; tiles are `<dl>` so screen readers announce term/def pairs natively.

**Wireframe.**

```
┌──────────────────────────────────────────────────────────────────────┐
│  KEEP N       │  LAST RUN              │  DRY-RUN FRESH               │
│  6            │  ✓ freed 12.4 GB · 2h  │  1,204 pairs · expires 41m   │
└──────────────────────────────────────────────────────────────────────┘
```

**Responsive.** ≥720px 3-col flex; <720px stacks 1-col.

### 2.2 Material Stepper shell

**Purpose.** 4-step wizard matching the existing workflow shape.

**Component shape.** `<mat-stepper [orientation]="'vertical'" [linear]="false" [selectedIndex]="activeStep()">` with custom step labels and a custom footer (Material's default buttons are hidden; we render an `<h2>` footer with explicit Back / Continue / Cleanup now).

**Step labels.** `1. Review` · `2. Dry-run` · `3. Confirm & Execute` · `4. Result`

**States.** `step-active` (current — Material handles), `step-completed` (✓ checkmark — derived from step gating state), `step-pending` (muted).

**A11y.** Material stepper handles aria-current, focus management between steps, and keyboard nav (arrow keys move between steps; Tab cycles within step content).

**Wireframe (vertical stepper with custom footer).**

```
┌─ Step 1: Review ──●── Step 2: Dry-run ──○── Step 3: Confirm ──○── Step 4: Result ──○─┐
│                                                                                       │
│   [step content per active step]                                                      │
│                                                                                       │
│                                              [ ← Back ]    [ Continue → ]             │
└───────────────────────────────────────────────────────────────────────────────────────┘
```

### 2.3 Step 1 — Review

**Content.**

- `Configuration` sub-card (4 dl rows in a 2×2 grid on ≥1024px, 1-col on narrow)
- `Last run` sub-card (6 dl rows in a 2×3 grid on ≥1024px, 1-col on narrow)
- In-flight warning (when `status().in_flight` exists)
- `[Continue →]` button (disabled when `!canDryRun() || isMaintenanceDisabled() || isRunInFlight()`)

**A11y.** H3 sub-headings; `<dl>` is native; warning uses `role="alert"`.

**Wireframe.**

```
┌── Review ──────────────────────────────────────────────────────────────────────────┐
│  Configuration                            │  Last run                                │
│  Keep N checkpoints       6               │  Kind              cleanup               │
│  Cleanup interval         6 hours         │  Completed         2026-10-15 02:14      │
│  Blob-prune dry default   ON (safe)       │  Status            completed             │
│  Destructive armed        no              │  Freed             12.4 GB               │
│                                            │  Duration          3m 12s                │
│                                            │  Skipped           4 pairs — fail-safe   │
│                                                                                    │
│  ⚠ An execute run is in flight (started 02:18). Dry-run and execute disabled.      │
│                                                                  [ Continue → ]    │
└────────────────────────────────────────────────────────────────────────────────────┘
```

### 2.4 Step 2 — Dry-run

**Content.**

- `<button>` "Dry-run check" (gated on `canDryRun() && !dryRunning()`, raises `ck-dry-run-btn` testid)
- Result `<dl>` (would-delete, would-free, scanned, duration, fresh-until)
- Projection line (`~now · ~after · ~total`) — preserves the 3-line projection block from current page
- Skipped honesty banner (`ck-projection-skipped`) — when `dryRunSkippedHonestyActive(dry)`
- Never-pruned sub-copy (`ck-never-pruned-subcopy`) — when `isNeverPrunedProfile(dry)`
- Skipped details (collapsed `<details>` by default — preserves `ck-dry-skipped`)
- `[← Back] [Continue →]` (Continue gated on `lastDryRun()` existence AND `dry.would_delete_count > 0` OR explicit operator override; disabled state visible)

**A11y.** All existing `data-testid` attributes preserved on inner elements.

**Wireframe.**

```
┌── Dry-run check ────────────────────────────────────────────────────────────────────┐
│  Compute what would be deleted without writing anything. Required before execute.   │
│                                              [ Dry-run check ]                       │
│                                                                                     │
│  Would delete (blobs)   4,521        │  Would free              12.4 GB              │
│  Excess checkpoint rows 18           │  Excess writes           4,521                │
│  Scanned pairs           1,204       │  Duration                412 ms               │
│  Fresh until             2026-10-15 03:14                                            │
│                                                                                     │
│  This run: ~12.4 GB · After this run: ~0 · Combined: ~12.4 GB                       │
│  ✓ 4 pairs skipped — cleanup effectiveness may be understated                       │
│                                                                                     │
│                          [ ← Back ]    [ Continue → ]                              │
└─────────────────────────────────────────────────────────────────────────────────────┘
```

### 2.5 Step 3 — Confirm & Execute

**Content.**

- Echo panel: "This run will delete 4,521 blobs and free 12.4 GB." (matches dry-run promise)
- Warning copy: "Destructive — permanently deletes unreferenced blobs and excess checkpoint rows." (rose-colored, `ck-warning-text` testid)
- `<button>` "Cleanup now" (warn color, raises `ck-execute-btn` testid, gated on `lastDryRun() && !executing() && !isRunInFlight()`)
- Reuses existing `ConfirmDialogComponent` for the destructive confirm (already in the codebase — `panelClass: 'dark-modal-panel'`, `destructive: true`)
- Progress (during execute): `<mat-progress-bar mode="indeterminate">` + run ID (`ck-active-run-id`) + expected duration (`ck-expected-duration`)

**A11y.** Confirm dialog already screen-reader-tuned; destructive button has `aria-describedby` pointing to the warning copy.

**Wireframe.**

```
┌── Confirm & Execute ───────────────────────────────────────────────────────────────┐
│  This run will:                                                                     │
│    • Delete 4,521 excess checkpoint rows                                             │
│    • Reclaim ~12.4 GB of blob storage                                                │
│                                                                                     │
│  Destructive — permanently deletes unreferenced blobs and excess checkpoint rows.   │
│                                              [ Cleanup now ]                        │
│                                                                                     │
│  ⏳ Polling run status. Run ID: abc-123. Expected duration: ~412 ms.                 │
│                                                                                     │
│                          [ ← Back ]                                                 │
└─────────────────────────────────────────────────────────────────────────────────────┘
```

### 2.6 Step 4 — Result

**Content.**

- Status banner (✓ completed | ⚠ interrupted | ✗ failed) — color via `$accent-emerald / $accent-amber / $accent-rose`
- Run summary `<dl>` (kind, started_at, completed_at, blobs freed/deleted, checkpoint rows deleted, skipped summary)
- Interrupted-recovery card (`ck-interrupted-card`) — when `canRerunInterrupted(res)`
- Run-again banner (`ck-run-again-banner`, `ck-run-again-btn`) — when `showRunAgainBanner()`
- `[← Back to start]` resets `activeStep()` to 0

**A11y.** Status banner uses `role="status"` for completed/failed, `role="alert"` for interrupted.

**Wireframe.**

```
┌── Result ───────────────────────────────────────────────────────────────────────────┐
│  ✓ Run completed                                                                    │
│                                                                                     │
│  Kind              cleanup         │  Started at       2026-10-15 02:18             │
│  Run ID            abc-123          │  Completed at     2026-10-15 02:21             │
│  Reclaimed         12.4 GB          │  Checkpoint rows  18 deleted                   │
│  Duration          3m 12s           │                                                │
│                                                                                     │
│  ⟳ Run cleanup again to reclaim ~4.2 MB more                                        │
│  A follow-up run will free the blobs orphaned by the row deletions.                 │
│                                            [ Run again ]                            │
│                                                                                     │
│  [ ← Back to start ]                                                                │
└────────────────────────────────────────────────────────────────────────────────────┘
```

### 2.7 Global banners (preserve, reorder)

Above Status Strip (in DOM order):

- `ck-banner-disabled` (AM-13) — kill-switch off; renders when `isMaintenanceDisabled()`
- `ck-error-banner` (AM-1) — origin-guard blocked; renders when `lastError()?.error !== 'maintenance_disabled'`

These two banners are the only blocks that survive across all steps; everything else lives inside the active step.

### 2.8 Page-level "Debug" expander (consolidated raw JSON)

**Purpose.** Replace the 3 per-card `<details><summary>Raw JSON</summary>` blocks (Status, Dry-run, Result) with a single collapsed-by-default expander at the page level.

**Location.** Below `<mat-stepper>`, page-bottom.

**Content.** Raw `status()` + `lastDryRun()` + `lastExecuteResult()` payloads as a single `<pre><code>` block.

**A11y.** `<details>` is native; no extra ARIA needed.

## 3. Tokens

### Color palette (already adopted by `ck-*.scss`; no change)

| Token | Hex | Use |
|---|---|---|
| `$bg-primary` | #0f172a | Page bg |
| `$bg-card` | #1e293b | Card bg |
| `$border-color` | #334155 | Card borders |
| `$text-primary` | #f1f5f9 | Body text |
| `$text-secondary` | #94a3b8 | Labels |
| `$text-muted` | #64748b | Footnotes |
| `$accent-cyan` | #10a7f7 | Info (skipped honesty, projection) |
| `$accent-emerald` | #10b981 | Success (completed run) |
| `$accent-rose` | #f43f5e | Destructive / error |
| `$accent-amber` | #f59e0b | Warning (in-flight / interrupted) |
| `$accent-blue` | #3b82f6 | Action (primary buttons) |

### New tokens (stepper-specific)

| Token | Value | Use |
|---|---|---|
| `$step-gap` | 1.5rem | Gap between step content cards |
| `$status-strip-min-width` | 200px | Min width per status tile |
| `$wizard-max-width` | 1100px | Wizard container cap (was 820px on shell) |

### Spacing / typography (no change — use existing `ck-card`, `h3`, `dl` styles)

- Status strip tile value: 1rem / 600 weight / monospace
- Status strip tile label: 0.6875rem / uppercase / `$text-muted`
- Step `<h2>` (stepper header): 1.125rem / 600 weight
- Step content `<h3>` (sub-card header): 1.0625rem / 600 weight
- `<dl>` rows: 0.5rem row-gap, 1.5rem column-gap (existing)

### Motion (existing Material stepper default)

- Step transition: 225ms ease (Material default)
- No custom keyframes introduced

## 4. A11y

| Concern | Spec |
|---|---|
| **Keyboard nav** | Material stepper: arrow keys move between steps; Tab cycles within step content; Enter on Continue / Cleanup now triggers; Esc on Confirm dialog cancels (provided by `ConfirmDialogComponent`). |
| **Focus management** | Material stepper moves focus to the new step header on transition; custom Back / Continue buttons explicitly call `stepper.selected.focus()` (or equivalent) before step content renders. |
| **Live regions** | Status Strip = `aria-live="polite"`; in-flight warning = `role="alert"`; interrupted-state card = `role="alert"`; completed / failed result banners = `role="status"`. |
| **Labels** | Every actionable `<button>` has either text content or `aria-label`; every `<dt>` / `<dd>` pair is reachable via screen reader table reading. |
| **Contrast** | All `$text-*` and `$accent-*` combinations on `$bg-primary` and `$bg-card` already pass WCAG AA at the existing font sizes (verified at phase2-frontend implementation, see `maintenance-console/phase2-frontend.md` §T5.3). |
| **Step labels** | Stepper header uses `<h2>` for the active step (visible) + Material stepper's screen-reader header (inactive steps are screen-reader-navigable via aria-current). |
| **Reduced motion** | Material stepper respects `prefers-reduced-motion` automatically; no custom keyframes override it. |

## 5. Wireframe (page-level, full)

```
┌──────────────────────────────────────────────────────────────────────────────────────┐
│  Maintenance                                                                          │
│  Operator controls for daemon maintenance                                             │
├──────────────────────────────────────────────────────────────────────────────────────┤
│  Checkpoint Cleanup                                                                   │
│                                                                                       │
│  ┌── KEEP N ──┐  ┌── LAST RUN ──────────────┐  ┌── DRY-RUN FRESH ────────────────┐  │
│  │ 6          │  │ ✓ freed 12.4 GB · 2h     │  │ 1,204 pairs · expires 41m       │  │
│  └────────────┘  └──────────────────────────┘  └──────────────────────────────────┘  │
│                                                                                       │
│  ┌── Step 1: Review ─●── Step 2: Dry-run ──○── Step 3: Confirm ──○── Step 4: Result ─○──┐
│  │                                                                                    │
│  │  [step content per active step]                                                    │
│  │                                                                                    │
│  │                                            [ ← Back ]    [ Continue → ]           │
│  └────────────────────────────────────────────────────────────────────────────────────┘
│                                                                                       │
│  ▾ Debug (collapsed by default — shows raw last response payloads for diagnostics)     │
└──────────────────────────────────────────────────────────────────────────────────────┘
```

## 6. Tradeoffs

| Considered | Decision | Reason |
|---|---|---|
| **A. Dashboard (KPI + 2-col cards)** | Rejected | User picked C. Dashboard loses the "guided" property — operator can click Execute without ever running a Dry-run (today the template forces a Dry-run by gating the Execute button on `lastDryRun()`). Wizard preserves the same gating without an inline explainer. |
| **B. Tabs (Overview / Dry-run / Cleanup)** | Rejected | User picked C. Tabs hide context operators occasionally need (e.g., last-run timestamp while on the Cleanup tab). Stepper with persistent Status Strip solves the "peek at state without losing place" problem. |
| **C. Wizard / stepper** | ✅ Chosen | Best match to the existing 4-card workflow shape. Material handles keyboard / focus / aria-current natively. Persistent Status Strip decouples monitoring from action. |
| **Custom stepper (CdkStepper)** | Rejected | Material `<mat-stepper>` ships accessibility primitives (aria-current, focus management, header/footer roles) we'd otherwise re-implement. Cost: ~2 deps already in tree (`@angular/material`, `@angular/cdk`). |
| **Horizontal stepper** | Rejected | At <720px width, horizontal stepper labels truncate; vertical reads as a checklist which fits the "review → confirm" mental model. |
| **Linear stepper (`[linear]="true"`)** | Rejected | Step 4 (Result) should auto-render when execute succeeds even if operator never visited Step 3 in the active session (e.g. page refresh during execute → result returns from `lastExecuteResult` signal). Non-linear allows this; linear would block it. |
| **Drop raw JSON `<details>` per card** | Rejected (collaborator: keep, but consolidated) | Useful for debugging; moving to a single page-level expander keeps diagnostic power without cluttering every step. |
| **Widen container to 1100px** | ✅ Chosen | 820px cramped Status Strip (had to stack 3 tiles instead of row). 1100px max with 1-col narrow fallback respects modern operator displays without forcing horizontal scroll on mobile. |
| **Sub-component for Status Strip** | Rejected | Only one consumer (this page). Inline in template is simpler; can extract later if a second use appears. |
| **Add cancel/abort to execute** | Rejected (out of scope) | Plan decision-log R-1: deferred to v2. Not in this redesign. |
| **Hide Status Strip inside the wizard (per-step header instead)** | Rejected | Operators want state at-a-glance; Status Strip stays page-level. |

## 7. Acceptance criteria (observable + testable)

Each AC packs to a `Validation:` block (the agent-searchable shape). Static greps reference the post-implementation file paths.

### AC-1 — Status Strip renders above the stepper

- **Given** a healthy daemon returning `state: 'ready'`, `config.checkpoint_max_per_thread: 6`, and `last_run` data
- **When** the operator navigates to `/maintenance/checkpoint-cleanup`
- **Then** 3 tiles render in this order: `Keep N`, `Last run`, `Dry-run fresh`
- **And** the strip's `aria-live="polite"` region is reachable in the accessibility tree
- **Validation:** `pack e2e/maintenance-checkpoint-cleanup.spec.ts; static: grep -n 'data-testid="ck-status-strip"' checkpoint-cleanup.component.html`

### AC-2 — Stepper renders 4 steps with vertical orientation

- **Given** any daemon state
- **When** the page renders
- **Then** `<mat-stepper [orientation]="'vertical'">` contains exactly 4 `<mat-step>` children labelled `Review`, `Dry-run`, `Confirm & Execute`, `Result`
- **Validation:** `pack e2e/maintenance-checkpoint-cleanup.spec.ts; static: grep -n '<mat-stepper' checkpoint-cleanup.component.html`

### AC-3 — Step 1 (Review) shows config + last-run + in-flight warning when applicable

- **Given** `status()` resolves with 4 config rows and a `last_run` block
- **When** the operator lands on `/maintenance/checkpoint-cleanup`
- **Then** the Review step shows the 4 config rows and 6 last-run rows in a 2×2 / 2×3 grid on ≥1024px, 1-col on narrow
- **And** if `status().in_flight` exists, the warning copy renders with `role="alert"`
- **Validation:** `pack e2e/maintenance-checkpoint-cleanup.spec.ts; static: grep -n 'data-testid="ck-status"' checkpoint-cleanup.component.html`

### AC-4 — Continue gates correctly

- **Given** the operator is on Step 1
- **When** `canDryRun()` returns `false` (e.g. kill-switch off, in-flight, origin blocked)
- **Then** the "Continue →" button is `disabled`
- **And** keyboard focus skips the disabled button
- **Validation:** `pack unit/checkpoint-cleanup.component.spec.ts; static: grep -n 'canDryRun' checkpoint-cleanup.component.ts`

### AC-5 — Dry-run step preserves all existing `data-testid` attributes

- **Given** the operator clicks "Continue →" from Step 1
- **When** Step 2 renders
- **Then** the following testids are reachable: `ck-dry-run-btn`, `ck-dry-would-delete`, `ck-dry-would-free`, `ck-dry-fresh-until`, `ck-projection-now`, `ck-projection-after`, `ck-projection-total`, `ck-projection-skipped`, `ck-never-pruned-subcopy`, `ck-dry-skipped`
- **Validation:** `pack e2e/maintenance-checkpoint-cleanup.spec.ts; static: grep -n 'data-testid=' checkpoint-cleanup.component.html`

### AC-6 — Execute step preserves `ConfirmDialogComponent` flow

- **Given** a valid dry-run exists and operator is on Step 3
- **When** the operator clicks "Cleanup now"
- **Then** the existing `ConfirmDialogComponent` opens (`destructive: true`, `panelClass: 'dark-modal-panel'`), and only a `result === true` triggers `onExecute()`
- **Validation:** `pack e2e/maintenance-checkpoint-cleanup.spec.ts; static: grep -n 'ConfirmDialogComponent\|panelClass.*dark-modal-panel' checkpoint-cleanup.component.ts`

### AC-7 — Execute step preserves `data-testid` ck-execute-btn, ck-active-run-id, ck-expected-duration

- **Validation:** `static: grep -n 'data-testid=' checkpoint-cleanup.component.html`

### AC-8 — Result step preserves `data-testid` ck-result, ck-interrupted-card, ck-rerun-btn, ck-run-again-banner, ck-run-again-btn, ck-last-skipped-summary

- **Validation:** `pack e2e/maintenance-checkpoint-cleanup.spec.ts; static: grep -n 'data-testid=' checkpoint-cleanup.component.html`

### AC-9 — "Back to start" on Step 4 resets `activeStep()` to 0

- **Given** the operator is on Step 4
- **When** the operator clicks "← Back to start"
- **Then** `activeStep()` is `0` and the Review step is visible
- **Validation:** `pack unit/checkpoint-cleanup.component.spec.ts; static: grep -n 'activeStep' checkpoint-cleanup.component.ts`

### AC-10 — Dry-run payload does NOT re-fetch on step navigation

- **Given** operator completed a dry-run 30 minutes ago (fresh_until = now+30m)
- **When** the operator navigates Review → Dry-run → Confirm & Execute → Result
- **Then** the freshness stamp is recomputed at each navigation but the dry-run payload does not re-fetch from the server
- **Validation:** `pack unit/checkpoint-cleanup.component.spec.ts`

### AC-11 — Global banners render above Status Strip

- **Given** the daemon is started with `MAINTENANCE_ENDPOINTS_ENABLED=0` OR returns `origin_not_trusted`
- **When** the page renders
- **Then** the corresponding banner (`ck-banner-disabled` or `ck-error-banner`) is the first element in the DOM, before Status Strip and stepper
- **Validation:** `pack e2e/maintenance-checkpoint-cleanup.spec.ts; static: grep -n 'data-testid="ck-banner-disabled"\|data-testid="ck-error-banner"' checkpoint-cleanup.component.html`

### AC-12 — Container caps at 1100px; status strip wraps at <720px

- **Given** a viewport of 1920px
- **When** the page renders
- **Then** the wizard container width is 1100px and Status Strip is a 3-col flex
- **And** at 600px viewport, Status Strip wraps to 1-col and the wizard remains 1-col
- **Validation:** `pack e2e/maintenance-checkpoint-cleanup.spec.ts; static: grep -n '\$wizard-max-width: 1100px\|@media' checkpoint-cleanup.component.scss`

### AC-13 — Page-level "Debug" expander (consolidated raw JSON)

- **Given** a dry-run has been completed
- **When** the operator expands the "Debug" expander
- **Then** they see the raw `dry` payload and `lastExecuteResult` payload
- **And** no per-card `<details><summary>Raw JSON</summary>` blocks remain
- **Validation:** `pack unit/checkpoint-cleanup.component.spec.ts; static: grep -n 'Raw JSON' checkpoint-cleanup.component.html` (must return empty)

### AC-14 — Section registry extensibility invariant preserved

- **Given** the `maintenance.component.ts` `sections` array
- **When** a second section is appended (e.g. `{ id: 'db-vacuum', ... }`)
- **Then** the Maintenance page renders both sections via the registry's `@for` loop and `<ng-container *ngComponentOutlet>`
- **And** the existing `sections-registry-load-bearing` source-grep pin still passes
- **Validation:** `pack unit/maintenance.component.spec.ts; static: grep -n 'sections-registry-load-bearing' maintenance.bindings.pins.spec.ts`

### AC-15 — No scroll required for Status Strip + first Continue at 1024×768 viewport

- **Given** a healthy daemon state
- **When** the operator opens `/maintenance/checkpoint-cleanup` at 1024×768
- **Then** the Status Strip and Step 1 Continue button are both visible without scrolling
- **Validation:** `pack e2e/maintenance-checkpoint-cleanup.spec.ts` (Playwright viewport assertion)

### AC-16 — In-flight run state disables dry-run + execute buttons across all steps

- **Given** `status().in_flight` exists
- **When** the operator navigates to any step
- **Then** the dry-run and execute buttons are `disabled` AND a step-level inline notice appears in Step 1 only
- **Validation:** `pack unit/checkpoint-cleanup.component.spec.ts; static: grep -n 'isRunInFlight\|in_flight' checkpoint-cleanup.component.ts`

### AC-17 — Frozen API contract: no endpoint additions

- The implementation does NOT add or modify any endpoint. All 5 endpoints (`availability / status / dry-run / execute / runs/{id}`) keep their current shape.
- **Validation:** `static: grep -n 'POST\|GET' checkpoint-cleanup.service.ts` (5 endpoints, no new methods added)

### AC-18 — A11y baseline

- **Given** the page renders
- **When** keyboard-only navigation is exercised (Tab through strip → stepper → step content → footer)
- **Then** all interactive elements are reachable, focus indicators visible, and step transitions announce to screen readers via Material stepper's aria-current
- **Validation:** `pack e2e/maintenance-checkpoint-cleanup.spec.ts` (axe-core scan passes)

## 8. Files in scope

| File | Change |
|---|---|
| `frontend/src/app/pages/maintenance/checkpoint-cleanup/checkpoint-cleanup.component.html` | Major restructure: Status Strip + 4 mat-step blocks; remove per-card raw JSON; preserve all inner `data-testid`. |
| `frontend/src/app/pages/maintenance/checkpoint-cleanup/checkpoint-cleanup.component.scss` | New tokens (`$step-gap`, `$status-strip-min-width`, `$wizard-max-width`); dark-theme overrides for `mat-stepper`; Status Strip styles; responsive media query. |
| `frontend/src/app/pages/maintenance/checkpoint-cleanup/checkpoint-cleanup.component.ts` | New `activeStep` signal + `stepperNav()` helper; step gating logic (`canContinueFromStep1/2/3`); preserved signals, methods, and testids. |
| `frontend/src/app/pages/maintenance/maintenance.component.scss` | Widen container from 820px to 1100px max (one-line change at `:22`). |

## 9. Files NOT in scope (out of bounds)

- `frontend/src/app/pages/maintenance/checkpoint-cleanup/checkpoint-cleanup.service.ts` — no API changes
- `frontend/src/app/pages/maintenance/maintenance.component.{html,ts}` — no template change, only width tweak in scss
- All `__fixtures__/*` files
- `frontend/src/app/pages/maintenance/maintenance.bindings.pins.spec.ts` — existing source-grep pins must still pass (AC-14)

## 10. Test pack expectations

| Action | Note |
|---|---|
| Existing Playwright E2E `frontend/e2e/maintenance-checkpoint-cleanup.spec.ts` | Most selectors still work (testids preserved on inner elements). Likely needs updates only for selectors that previously asserted top-level card ordering. |
| Existing Jest unit `frontend/src/app/pages/maintenance/checkpoint-cleanup/checkpoint-cleanup.component.spec.ts` | Needs new stepper nav specs + Status Strip spec; existing data-signal specs remain. |
| Existing source-grep pin `sections-registry-load-bearing` | Must continue to pass (AC-14). |
| New unit specs | Add `activeStep` initial-value, Continue/Back navigation, gating logic, in-flight handling. |
| New E2E specs | Add Status Strip visibility, stepper navigation, viewport fit at 1024×768, debug-expander visibility. |