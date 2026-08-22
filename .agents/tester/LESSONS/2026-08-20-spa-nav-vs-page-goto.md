# Lesson: page.goto() full-reload nulls singleton service state — use in-app SPA navigation in e2e

Date: 2026-08-20 | Branch: fix/hide-button-toggle-affordance | Spec: hide-button-symptom.spec.ts (S5b)

## Root cause
Playwright `page.goto('/some/route')` performs a FULL page load. On reload, root-provided singletons (e.g. `WorkspaceOverlayService`) reset in-memory state — `workspaceProjectId` → `null`. Symptom chain: `.overlay-hide-btn` unmounts from DOM (anyOverlayVisible=false → no header button), `app-workspace` present but inert (display none), any test expecting recoverable-workspace state silently degrades.

## Evidence (S5b diagnostic)
- `page.goto('/projects/{pid}/blueprints')` → diagnostic: `overlayHideBtn=0`, no `.overlay-hide-btn` in DOM, workspaceProjectId null.
- Clicking the existing "Sources" `routerLink` (app.html:18) instead → Angular router pushState → singleton state preserved → branch-2 precondition (show-tier active, workspace recoverable) reachable → S5b 3/3 deterministic.

## Rule
Any e2e that must change URL/route state WHILE preserving root-singleton service state (WorkspaceOverlayService, InstancesViewStateService in-memory caches) MUST navigate via in-app UI (click a routerLink) — never `page.goto()` mid-test. `goto` is only for test START (initial load).

## Related
- NotificationService opens /api/notifications/stream at boot (root-provided) — full reloads also reset SSE state; same discipline applies.
- See also LESSONS/2026-08-20-r4-immediate-read-race.md (toPass discipline) — both are "composed-runtime beats test-assumption" classes.
