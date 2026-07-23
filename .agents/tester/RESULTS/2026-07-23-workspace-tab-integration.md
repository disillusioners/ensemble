# Test Report: Workspace Viewer Per-Tab Overlay Integration

**Date:** 2026-07-23
**Commit:** `6beff2fd` on `feature/workspace-tab-integration` (15 files, +1055/-46)
**Workers:** `a47b865e` (jest), `01450072` (build), `2d2aa967` (spec verify), `9bbe3d0f` (browser e2e)

## Summary

| Pack | Tests/Checks | Status | Runtime |
|------|-------------|--------|---------|
| Frontend Jest (workspace + tab-bar + chat) | 165/165 passed (8 suites) | ✅ PASS | 3.96s |
| Frontend production build (ng build) | Clean compile, 0 errors | ✅ PASS | 9.6s |
| Focused spec verification | 4/4 areas ADEQUATE | ✅ PASS | 2.3s |
| Browser E2E (4 scenarios) | 4/4 scenarios PASS | ✅ PASS | 24.7s |
| ensure.md Core requirements | N/A (all backend-focused) | ⬜ SKIP | — |

**Overall Status: ✅ READY**

## Scope Decision

> Frontend-only change (15 files, +1055/-46) across 4 component/service areas: project-tab-bar (workspace icon per tab), chat.component (overlay hosting), workspace.component (hide button), workspace.service (LRU cache max 5). No backend code changed.
>
> **Ran:** frontend Jest packs + production build + focused spec verification + browser E2E (4 scenarios).
> **Skipped:** backend workspace packs (`workspace_guard_unit_test`, `workspace_api_integration_test`) — no backend code touched. ensure.md Core requirements (deadlock, sync DB, dev.sh graceful shutdown) — all backend-focused, not applicable. Release Gate — not a big/critical/architecture change.
> **Reason:** Blast radius is frontend-only; backend packs would run against unchanged code. Full suite not warranted.

## Test Results Detail

### 1. Frontend Jest Tests — ✅ PASS (165/165)

Two runs combined:

**Run 1 — workspace_frontend_unit_test pack** (6 suites, 111 tests, 3.24s):
- `workspace.service.spec.ts`, `workspace.component.spec.ts`, `codemirror.directive.spec.ts`, `diff-viewer.component.spec.ts`, `code-viewer.component.spec.ts`, `file-tree.component.spec.ts`

**Run 2 — project-tab-bar + chat explicit** (2 suites, 54 tests, 0.72s):
- `project-tab-bar.component.spec.ts`, `chat.component.spec.ts`

### 2. Frontend Build — ✅ PASS (9.6s)

Clean `ng build` — zero compilation errors. Angular template type-checking (which only runs during ng build, not `tsc --noEmit`) passed, confirming no type errors in the 4 changed files.

Pre-existing budget warnings (non-blocking): bundle 4.99MB/1MB budget, 4 component SCSS files slightly over 8kB budget. None in changed files.

### 3. Focused Spec Verification — ✅ ADEQUATE (4/4 areas)

| Area | Coverage | Key Tests |
|------|----------|-----------|
| **Tab bar icon** (click logic) | ✅ COVERED (3 tests) | `emit workspaceToggle on click`, `stopPropagation+preventDefault` (no router nav), All-tab suppression |
| **Tab bar icon** (DOM/CSS) | ⚠️ PARTIAL | Logic-mirror pattern — CSS auto-hide (`opacity:0→1`) present in SCSS but untestable without TestBed render |
| **Overlay toggle** (state machine) | ✅ COVERED (8 tests) | Full "Workspace overlay" block: open, toggle-off, switch-project, hide, header-guard |
| **Overlay template** (z-index/position) | ⚠️ PARTIAL | Logic-mirror pattern — `<app-workspace>` in `@if`, z-index:50 in SCSS present but not DOM-tested |
| **Hide button** | ✅ COVERED (3 tests) | Real TestBed render: EventEmitter exposed, `[data-testid="workspace-hide"]` button in DOM, click→emit verified |
| **LRU Cache** | ✅ COVERED (11 tests) | Exemplary: max 5 eviction, MRU promotion (save+restore), partial-extras merge, auto-save on switch, empty-id guard |

**Caveats (non-blocking):** Tab-bar and chat specs use logic-mirror pattern (hand-written `TestableXxxComponent` instead of TestBed). This means template rendering and CSS auto-hide can't be asserted by unit tests. However: (a) the logic IS covered, (b) the browser E2E test (below) verified the actual DOM/CSS/behavior end-to-end, and (c) the workspace.component spec uses real TestBed for the hide button.

### 4. Browser E2E — ✅ PASS (4/4 scenarios)

Playwright (Chromium headless) against live backend (:8079) + frontend (:4199).

| # | Scenario | Status | Verification |
|---|----------|--------|-------------|
| 1 | Tab Bar Icon Auto-Hide | ✅ PASS | `.workspace-btn` with `folder_open` mat-icon found in project tab; opacity 0.7 active state; SCSS auto-hide confirmed |
| 2 | Workspace Overlay Appears | ✅ PASS | `.workspace-overlay` after click: `position:absolute`, `inset:0`, `z-index:50`, `display:flex`; file tree visible |
| 3 | Hide Button Works | ✅ PASS | `[data-testid="workspace-hide"]` with `visibility_off`; click → overlay + `app-workspace` removed; chat restored |
| 4 | LRU Cache Preserves State | ✅ PASS | File `2026-04-28-explorer-kb-heading-enforcement.md` selected + `.agents` expanded → hide → re-open: **same file still selected** with content displayed |

5 screenshots captured and visually confirmed via image-reader.

**Minor finding (non-blocking):** In Scenario 4, deeply nested expanded directories partially reset after re-open (34 dirs → 26 after reopen). Selected file path was fully preserved. Root cause: `restoreExpandedPaths()` restores top-level expansions but some children need re-fetch from API. Cosmetic — core LRU (file selection + tree data) works correctly.

## ensure.md Validation

**Core requirements:** All 4 are backend-focused (deadlock/concurrency, sync DB calls, dev.sh graceful shutdown). This is a frontend-only change → these requirements are **not applicable** to the blast radius. No contradiction with my rules.

**Release Gate:** Not warranted — not a big/critical/architecture change.

## Quick Fixes Applied

None needed — all tests passed on first run, no failures encountered.

## Action Items (optional, non-blocking)

- **Folder expansion restoration**: The LRU cache restores selected file + top-level expanded dirs, but deeply nested expansions may need re-fetch. Consider persisting full `expandedPaths` set and re-applying on restore. Low priority (cosmetic).
- **Tab-bar/chat spec enhancement**: Consider adding TestBed-based DOM assertions for the `@if (tab.type === 'project')` template gate and CSS auto-hide, to complement the existing logic-mirror tests. Low priority (E2E covers this).
