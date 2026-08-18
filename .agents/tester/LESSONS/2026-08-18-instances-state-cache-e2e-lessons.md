# Persistent-Overlay E2E: networkidle, app-wide SSE counting, serial-abort suppression, fixture noise

Date: 2026-08-18 · Feature: instances-state-cache · Packs: instances_state_e2e_core/_regression + sweep A/B

## 1. `waitForLoadState('networkidle')` is unreachable in this app
`NotificationService` (providedIn root) opens `/api/notifications/stream` in its constructor at boot and never disconnects → network never idles. Any spec waiting `networkidle` times out deterministically even when the SPA is fully rendered (proven by error-context snapshot).
**Rule:** use `domcontentloaded` + explicit element-readiness waits. Fixed in 2 new specs (authored) — 8 sites in 12 pre-existing specs still carry it and will fail as class-4 fixture/infra noise.

## 2. Never count EventSources app-wide
The app has ≥5 EventSource sites (sse.service chat `/api/instances/{id}/events`, notification.service, job-sse, workspace, migration). App-wide net-open assertions are always +1 (permanent notifications stream) → false failures.
**Rule:** monkey-patch `window.EventSource` via `addInitScript` recording `{url, type}`; scope assertions to the exact stream URL pattern. Chat-stream closure on hide: opens=1/closes=1/net=0 — the feature's SSE lifecycle is verifiable this way.

## 3. Serial mode aborts suppress evidence, not just tests
`test.describe.configure({mode:'serial'})` stops the file at first failure — repeatedly hid downstream tests (terminate suppressed 4×, workspace-file-tabs 18 blocked). Blocked ≠ failed; don't report suppressed tests as failures.
**Rule:** order tests independent-first, state-dependent last (destructive flows terminal); reorder to harvest evidence before known-failing asserts; report suppression explicitly.

## 4. Synthetic API-only fixtures can't feed the workspace overlay
Projects created via `POST /api/projects` have no on-disk files → `/api/workspace/*` 404s → tree `files=[]`. Any spec that opens the workspace against them fails at tree-wait (6 pre-existing specs, class-4).
**Rule:** fixture must seed on-disk content for workspace surface, or scope specs to API-only flows. Console-error asserts must filter documented fixture-noise URL classes (`/api/workspace/`, `/vscode-folder`), never blanket-ignore.

## 5. Terminated instances log handled SSE errors
Killing a live instance fires `[SSE] Connection error` (sse.service.ts:495/508 → handleClose). Logged-and-handled by design; do not fail console asserts on it. App polish option: `disconnect()` before delete.

## 6. `@if` guards destroy inner state even when the shell survives
Root-mounted-chat persistence pattern: outer container node survives display-toggle (marker-proven), but anything inside `@if (currentInstance() && !instanceNotFound())` is destroyed when the signal transiently nulls → draft/scroll lost. DOM-identity markers prove shell persistence; they do NOT prove inner-state preservation — assert both separately.

## 7. hrefs are context-built
`instance-list.html` builds card hrefs via `getProjectContext()` → `/projects/all/instances/{id}` on the All tab. Never select cards by owning-project path; match by instance id. Same drift broke `project-tabs` spec (pre-existing).

## 8. Dynamic createComponent hosts escape component-scoped styles (BUG5 class)
A component created via `ViewContainerRef.createComponent` after a dynamic import does NOT carry the host component's `_ngcontent` scoping attribute. Any rule in a component-scoped stylesheet targeting the host tag (`app-chat { … }` in app.scss) silently stops matching — no error, just missing z-index/position/background. Visibility-based asserts (count/attached/identity) do NOT catch it; only computed-style asserts do (`getComputedStyle(el).zIndex`).
**Rule:** when validating lazy/dynamic mounts, assert computed layout (z-index, position) — not just presence; and prefer global styles for dynamically-mounted host tags.

## 9. Empty-chat fixtures clamp scroll evidence (BUG1/BUG2 correction)
A zero-message instance has no overflow: `scrollTop = 100` silently clamps to 0 — "scroll lost" evidence from such a fixture is void even when a teardown bug exists. 
**Rule:** before asserting scroll preservation, inject filler rows AND assert the set took (`scrollTop ≥ 90` immediately) as a fixture-readiness gate.

## 10. 404 paths need explicit cache-clear verification
Not-found UI rendering ≠ cache hygiene. Deep-link 404 must ALSO clear the nav cache (`clearInstance`) synchronously in the error handler — relying on a polling validator races the user's next click (BUG6 loop).
**Rule:** e2e 404 journeys must assert the nav-link target after the not-found render, not just the not-found panel.

## 11. Legacy specs with pinned fixture ids rot
`auto-scroll-to-bottom` pinned a 2026-era instance id; the instance was deleted → 3 deterministic timeouts misreadable as regressions. Live-API check (`GET /api/instances/{id}`) is the 10-second discriminator between APP-REGRESSION and ENVIRONMENT.
**Rule:** triage legacy failures against the live API before blaming the tree; specs must self-create fixtures.
