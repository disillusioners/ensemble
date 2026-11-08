import { HttpErrorResponse, HttpClient } from '@angular/common/http';
import { Injectable, inject, signal } from '@angular/core';
import { firstValueFrom } from 'rxjs';
import { timeout } from 'rxjs/operators';
import { redirectToLogin } from './auth-redirect';

/** The authenticated principal the daemon answers GET /api/auth/me with
 *  (daemon/routers/auth.py:184-201 — FR-15 context probe). */
export interface SessionUser {
  subject: string;
  scopes: string[];
  auth_method: string;
  expires_at: string;
}

/** The session probe URL (the public-401 contract endpoint). */
export const SESSION_ME_URL = '/api/auth/me';

/** The house door budget (the nest-reports 15s precedent). */
const PROBE_TIMEOUT_MS = 15_000;

/**
 * App-start session bootstrap. Wired via provideAppInitializer in
 * app.config.ts so the probe resolves BEFORE the root component renders:
 * a cold/deep-link unauthenticated visit redirects to login instead of
 * flashing raw 401 error states from every page's own API fan-out.
 */
@Injectable({ providedIn: 'root' })
export class SessionService {
  private readonly http = inject(HttpClient);

  /** The authenticated principal once the probe succeeds; null otherwise. */
  readonly user = signal<SessionUser | null>(null);

  /**
   * Probe the session exactly once at app start:
   *
   * * 200 + a SessionUser-shaped body → carried on the `user` signal;
   * * 401 → login redirect (covers deep links / cold load). This probe
   *   sits ON the auth surface, so the interceptor's loop-guard skips it
   *   — the redirect is issued HERE, through the same shared latch;
   * * anything else (network down, timeout) → leave the app alone.
   *
   * The explicit JSON Accept is NOT decoration: the auth middleware
   * 302s a wildcard-Accept request to an HTML login page (the
   * nest-reports/MetricsService precedent) — explicit
   * application/json pins the 401-JSON contract instead. A 200 whose
   * body is not SessionUser-shaped (the followed-302 HTML trap) is
   * treated as an auth redirect too — the same conservatism as
   * shouldAuthRedirect.
   */
  async init(): Promise<void> {
    try {
      const user = await firstValueFrom(
        this.http
          .get<SessionUser>(SESSION_ME_URL, {
            headers: { Accept: 'application/json' },
          })
          .pipe(timeout(PROBE_TIMEOUT_MS)),
      );
      if (typeof user?.subject === 'string') {
        this.user.set(user);
      } else {
        // Login page HTML (or any non-contract body) behind a 200.
        redirectToLogin();
      }
    } catch (err) {
      if (err instanceof HttpErrorResponse && err.status === 401) {
        redirectToLogin();
      }
    }
  }
}
