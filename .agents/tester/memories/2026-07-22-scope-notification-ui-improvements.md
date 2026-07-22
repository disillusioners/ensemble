# Scope Decision: Notification UI/UX Improvements

**Branch:** `feature/notification-ui-improvements`
**Commit:** `4502aebb` (+ 4 follow-up)
**Date:** 2026-07-22

## Blast Radius Assessment
- **Change shape:** Small / isolated feature (UI-focused + backend payload enrichment)
- **Files touched:** 10 (backend: event_publisher, instance_lifecycle, notification_broadcaster; frontend: notification-bell html/scss/ts, models/index.ts, notification.service.ts; tests: 2 backend test files)
- **Architecture impact:** None — additive payload fields + UI redesign

## Scope Decision
**REDUCED scope.** Full suite NOT warranted.
- Scoped to 7 packs: 3 backend notification + frontend jest + frontend build + web automation + mock SSE flow
- All 177 other packs skipped (no changed files in those modules)

## Results
- All 7 packs PASS (58 backend + 1237 frontend + 37 mock + 8 web automation + 6 static)
- 1 quick fix applied (project_id UUID truncation, commit dd9db04e)
- 1 coverage gap found (notification.service.ts SSE handler) — mitigated by mock test
- ensure.md: 2/2 critical in-scope PASS

## Key Pattern
- Mock test (pure-logic simulation of Angular service + live mock SSE server) is an effective
  fallback when frontend feature code lacks direct jest coverage.
- Web automation via Playwright caught a real UX bug (raw UUID in label) that unit tests missed.
