# The nest-reports pixel item — the auth-off geometry (phase 4, Task 7)

Lane: **nest-anomaly-report** (`wi-2d691ed2`) · capture date: **2026-11-05**
Capture script: `pixels/capture-nest-reports.js` (re-runnable, hermetic)
Evidence JSON: `pixels/nest-reports-auth-off-geometry.json`

## THE FIXTURE — the LIVE posture, hermetically

* the app is served from the **production build's** `dist/frontend/browser`
  by a tiny read-only static server (a random loopback port);
* **no daemon, no auth token, no live install, no socket to the daemon**;
* every `/api/**` call is intercepted by Playwright:
  * `/api/metrics/**` → `503 {"detail": {"error": "AUTH_NOT_CONFIGURED", "message": "Auth not configured (fail-closed)"}}`
  * `/api/nest-reports/**` → the same 503 body
  * every other API call → an empty envelope the FE can iterate (the app
    shell's own reads — they are NOT part of this surface);
* the URL: `/metrics?tab=nest-reports`.

**The body shape is the REAL wire shape**: the daemon's `_err()` raises
`HTTPException(detail={"error": ..., "message": ...})` and FastAPI renders
`{"detail": {"error": ..., "message": ...}}` — byte-identical for the
metrics door (`daemon/routers/metrics.py:124-133`) and the nest-reports
route (`daemon/routers/nest_reports.py:82-91`).

> The FIRST capture attempt used the nested `{"error": {"detail": {...}}}`
> body and decoded to `unexpected` on BOTH doors. That was a FIXTURE
> defect, not a product defect: the Angular `HttpErrorResponse.error`
> IS the response body, so the body must carry `detail` at its root. The
> corrected fixture decodes to `auth_not_configured` on both doors.

## THE SIX ASSERTIONS — the observed boxes

| # | Assertion | Observed | Verdict |
|---|---|---|---|
| 1 | the central door-state panel is visible and non-degenerate | visible=true, box `x=20 y=140.3 w=848.5 h=105.6`; the tab-local `central-tab-error` panel is **absent** (the frame owns the central failure) | PASS |
| 2 | exactly ONE `data-error-class="auth_not_configured"` banner | count=**1** (the frame's `metrics-catalog-error`; the domain panel is SUPPRESSED) | PASS |
| 3 | no Retry button | `metrics-catalog-retry`=0, `nest-reports-retry`=0, any "Retry" button=0 (the auth class is not retryable) | PASS |
| 4 | the runbook pointer is visible inside the banner | `docs/runbooks/oauth2-auth.md` present in the banner text | PASS |
| 5 | the incident-list section renders a degraded placeholder, non-degenerate | placeholder box `x=20 y=471.8 w=848.5 h=20.1`; the section box `x=20 y=437.9 w=848.5 h=54.0` (the tab never blanks) | PASS |
| 6 | the banner's box and the placeholder's box do not intersect | banner `y 140.3–245.9` vs placeholder `y 471.8–491.9` — no overlap | PASS |
| 7 (extra) | no request storm | `/api/nest-reports/*` calls = **1**; 26 API calls in total (the shell's own reads included) | PASS |

**RESULT: GREEN — 7/7.**

## WHAT THE ITEM PROVES

* **the single-banner discipline holds in the REAL app**: under the
  auth-off posture exactly ONE banner states the posture — the metrics
  frame's page-level one — and the nest-reports domain panel renders a
  degraded PLACEHOLDER instead of a second banner (both sources share
  the daemon's auth configuration, and the operator's action is
  identical). The tab-local `central-tab-error` panel is absent because
  the frame owns the central failure.
* **the auth affordance is honest**: no Retry button (a configuration
  error is not transient) and the runbook pointer is visible.
* **the tab never blanks**: the incident section is degraded-but-present
  with a non-degenerate box.
* **no request storm**: the incident list is read ONCE under the failing
  posture.

## THE NOISE THAT IS NOT THIS SURFACE

The capture's console errors come from the app SHELL's own mocked reads
(`{}` envelopes the shell iterates — e.g. `e.projects is not iterable`)
and from the SSE endpoint's MIME type. They are the FIXTURE's generic
mocks, not the nest-reports surface: the metrics page rendered, the
banner rendered, and the tab rendered. The binary screenshot stays in
`/tmp/nest-anomaly-pixels/` (never in the repo).
