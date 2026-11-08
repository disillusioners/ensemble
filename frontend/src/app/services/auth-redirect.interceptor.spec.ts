import { HttpErrorResponse, provideHttpClient, withInterceptors } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { HttpClient } from '@angular/common/http';
import {
  authRedirectDispatched,
  resetAuthRedirectLatch,
  redirectToLogin,
  shouldAuthRedirect,
  buildLoginRedirectUrl,
} from './auth-redirect';
import { authRedirectInterceptor } from './auth-redirect.interceptor';

/**
 * The auth-redirect contract (one spec, per the hotfix mandate): a 401
 * AUTH_REQUIRED on an /api call flips the one-shot redirect latch even
 * under parallel 401s, non-auth errors pass through untouched, and
 * /api/auth/** requests are loop-guard skipped. The real redirectToLogin
 * runs under the interceptor tests (jsdom absorbs the navigation with a
 * console note — never throws); the latch's exact-once semantics are
 * pinned against a FAKE window, and the pure helpers (HTML-trap shape,
 * URL building) are asserted directly.
 */

function makeConfiguredTestBed() {
  return TestBed.configureTestingModule({
    providers: [
      provideHttpClient(withInterceptors([authRedirectInterceptor])),
      provideHttpClientTesting(),
    ],
  });
}

describe('authRedirectInterceptor', () => {
  let http: HttpClient;
  let httpTesting: HttpTestingController;

  beforeEach(() => {
    makeConfiguredTestBed();
    http = TestBed.inject(HttpClient);
    httpTesting = TestBed.inject(HttpTestingController);
    resetAuthRedirectLatch();
  });

  afterEach(() => {
    httpTesting.verify();
    resetAuthRedirectLatch();
  });
  function flush401(url: string) {
    const req = httpTesting.expectOne(url);
    req.flush(
      { error: 'AUTH_REQUIRED', message: 'Authentication required' },
      { status: 401, statusText: 'Unauthorized' },
    );
  }

  it('redirects to login on a 401 AUTH_REQUIRED from an /api call', () => {
    http.get('/api/instances').subscribe({ error: () => undefined });
    flush401('/api/instances');

    expect(authRedirectDispatched).toBe(true);
  });

  it('funnels parallel 401s into ONE redirect (latch holds)', () => {
    http.get('/api/instances').subscribe({ error: () => undefined });
    http.get('/api/projects').subscribe({ error: () => undefined });
    flush401('/api/instances');
    flush401('/api/projects');

    expect(authRedirectDispatched).toBe(true);
  });

  it('passes non-auth errors through without redirecting', () => {
    let observedStatus = 0;
    http
      .get('/api/instances')
      .subscribe({ error: (err: HttpErrorResponse) => (observedStatus = err.status) });
    const req = httpTesting.expectOne('/api/instances');
    req.flush({ error: 'SERVER_BOOM' }, { status: 500, statusText: 'Internal Server Error' });

    expect(observedStatus).toBe(500);
    expect(authRedirectDispatched).toBe(false);
  });

  it('loop-guard: never redirects for /api/auth/** requests', () => {
    let observedStatus = 0;
    http
      .get('/api/auth/me')
      .subscribe({ error: (err: HttpErrorResponse) => (observedStatus = err.status) });
    const req = httpTesting.expectOne('/api/auth/me');
    req.flush(
      { error: 'AUTH_REQUIRED', message: 'Authentication required' },
      { status: 401, statusText: 'Unauthorized' },
    );

    expect(observedStatus).toBe(401);
    expect(authRedirectDispatched).toBe(false);
  });
});

describe('redirectToLogin (real function, fake window)', () => {
  beforeEach(() => resetAuthRedirectLatch());
  afterEach(() => resetAuthRedirectLatch());

  it('navigates the fake window to the login URL with the deep link, EXACTLY once', () => {
    const win = {
      location: { href: 'about:blank', pathname: '/jobs/abc', search: '?q=1' },
    } as unknown as Window;

    redirectToLogin(win);
    expect(win.location.href).toBe(
      '/api/auth/login?return_to=' + encodeURIComponent('/jobs/abc?q=1'),
    );

    // Second call (parallel 401 arriving late): latch holds — href untouched.
    win.location.href = 'sentinel-not-touched';
    redirectToLogin(win);
    expect(win.location.href).toBe('sentinel-not-touched');
    expect(authRedirectDispatched).toBe(true);
  });
});

describe('auth-redirect pure helpers', () => {
  beforeEach(() => resetAuthRedirectLatch());

  it('buildLoginRedirectUrl carries the full deep link (path + search)', () => {
    expect(buildLoginRedirectUrl('/jobs/abc', '?q=1&x=%2F')).toBe(
      '/api/auth/login?return_to=' + encodeURIComponent('/jobs/abc?q=1&x=%2F'),
    );
  });

  it('shouldAuthRedirect: 2xx + login-page HTML body counts (followed-302 trap)', () => {
    const req = { url: '/api/instances' } as Parameters<typeof shouldAuthRedirect>[0];
    const err = { status: 200, error: '<!doctype html><html>login</html>' } as HttpErrorResponse;
    expect(shouldAuthRedirect(req, err)).toBe(true);
  });

  it('shouldAuthRedirect: HTML 5xx page is a server problem, not a login prompt', () => {
    const req = { url: '/api/instances' } as Parameters<typeof shouldAuthRedirect>[0];
    const err = { status: 502, error: '<html>Bad Gateway</html>' } as HttpErrorResponse;
    expect(shouldAuthRedirect(req, err)).toBe(false);
  });

  it('shouldAuthRedirect: ignores non-/api URLs entirely', () => {
    const req = { url: '/assets/index.html' } as Parameters<typeof shouldAuthRedirect>[0];
    const err = { status: 401, error: { error: 'AUTH_REQUIRED' } } as HttpErrorResponse;
    expect(shouldAuthRedirect(req, err)).toBe(false);
  });
});
