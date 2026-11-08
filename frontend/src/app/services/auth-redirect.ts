import { HttpErrorResponse, HttpRequest } from '@angular/common/http';

// ─────────────────────────────────────────────────────────────────────────
// Auth-redirect plumbing (401 → daemon-managed login).
//
// The daemon's internal OAuth is the gate since the oauth2-proxy removal:
// unauthenticated SPA visits get `401 {"error":"AUTH_REQUIRED"}` from
// every /api call and the SPA had no handler for one (the proxy used to
// 302 browsers to login BEFORE the SPA ever loaded). The daemon contract
// (daemon/middleware/auth.py:557-606, daemon/routers/auth.py:184-201):
//
// * `Accept: application/json` (XHR) → 401 AUTH_REQUIRED — the
//   interceptor catches THIS;
// * `Accept: text/html` (browser navigation) → 302
//   `/api/auth/login?return_to=…` — the full navigation WE issue rides
//   THIS chain (Casdoor → /api/auth/callback → back), hence
//   `window.location.href`, NOT router.navigate — the login chain
//   leaves the SPA origin.
//
// The pure helpers are exported for the Jest spec; only redirectToLogin()
// touches the window.
// ─────────────────────────────────────────────────────────────────────────

/** The daemon's login entry (302s to Casdoor; redirect_uri=/api/auth/callback). */
export const AUTH_LOGIN_PATH = '/api/auth/login';

/** Requests under the daemon's auth surface — redirecting on THEIR 401s
 *  would loop (the /api/auth/me probe IS our own bootstrap signal). */
export function isAuthSurfaceUrl(url: string): boolean {
  return url.startsWith('/api/auth/');
}

/** The 401 JSON shape the auth middleware answers XHR with. */
export function isAuthRequiredError(err: HttpErrorResponse): boolean {
  const body = err.error as Record<string, unknown> | null;
  return (
    err.status === 401 && !!body && typeof body === 'object' && body['error'] === 'AUTH_REQUIRED'
  );
}

/** Does the value look like an HTML document (the login page)? Mirrors
 *  pages/mcps-gateway/mcps-gateway.service.ts `bodyLooksLikeHtml`. */
export function looksLikeHtmlDocument(value: unknown): boolean {
  if (typeof value !== 'string') {
    return false;
  }
  const trimmed = value.trimStart().slice(0, 64).toLowerCase();
  return trimmed.startsWith('<!doctype') || trimmed.startsWith('<html');
}

/**
 * Should this failed request trigger the login redirect?
 *
 * Two failure shapes warrant it (scoped to daemon API calls, never the
 * auth surface itself — that loop-guard is what keeps the bootstrap
 * probe from recursively bouncing):
 *
 * 1. 401 + AUTH_REQUIRED — the standard XHR denial;
 * 2. a 2xx ERROR whose body is the login page's HTML — the
 *    followed-302 shape: the browser transparently chased a redirect
 *    and HttpClient surfaced the JSON parse failure (the known trap;
 *    same 2xx-only conservatism as
 *    pages/mcps-gateway/mcps-gateway.service.ts decodeError — an HTML
 *    5xx page is a server problem, not a login prompt).
 */
export function shouldAuthRedirect(req: HttpRequest<unknown>, err: HttpErrorResponse): boolean {
  if (!req.url.startsWith('/api/') || isAuthSurfaceUrl(req.url)) {
    return false;
  }
  if (isAuthRequiredError(err)) {
    return true;
  }
  return err.status >= 200 && err.status < 300 && looksLikeHtmlDocument(err.error);
}

/**
 * The login URL carrying the full deep-link path. The daemon sanitizes
 * return_to on arrival (``sanitize_return_to``, middleware/auth.py:107 —
 * open-redirect clamp), so encoding pathname+search verbatim is safe.
 */
export function buildLoginRedirectUrl(pathname: string, search: string): string {
  return `${AUTH_LOGIN_PATH}?return_to=${encodeURIComponent(pathname + search)}`;
}

// Module-level latch: every parallel 401 must funnel into ONE navigation.
// Full navigation tears the JS context down anyway, so a per-page-load
// latch is exactly the debounce horizon. Exported for the spec.

export let authRedirectDispatched = false;

/**
 * Claims the one-shot redirect slot: true exactly once per page load,
 * false for every subsequent caller (the debounce). Split from the
 * navigation so the spec can pin the latch without touching the window.
 */
export function claimAuthRedirect(): boolean {
  if (authRedirectDispatched) {
    return false;
  }
  authRedirectDispatched = true;
  return true;
}

export function redirectToLogin(win: { location: Location } = window): void {
  if (!claimAuthRedirect()) {
    return;
  }
  win.location.href = buildLoginRedirectUrl(win.location.pathname, win.location.search);
}

/** Test hook — resets the latch between specs. */
export function resetAuthRedirectLatch(): void {
  authRedirectDispatched = false;
}
