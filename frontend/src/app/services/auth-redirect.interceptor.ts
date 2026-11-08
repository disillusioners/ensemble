import { HttpErrorResponse, HttpHandlerFn, HttpRequest } from '@angular/common/http';
import { catchError, throwError } from 'rxjs';
import { redirectToLogin, shouldAuthRedirect } from './auth-redirect';

/**
 * Functional HTTP interceptor: funnels daemon auth denials into ONE
 * full-navigation login redirect (registered in app.config.ts via
 * ``provideHttpClient(withInterceptors([authRedirectInterceptor]))``).
 *
 * The latch inside redirectToLogin() collapses every parallel 401 into a
 * single navigation, and the auth-surface loop-guard keeps /api/auth/**
 * requests from re-triggering it.
 *
 * Every error is RE-THROWN — this interceptor never swallows: the
 * redirect is a side effect only, and the caller's own error path stays
 * intact (the page is navigating away moments later regardless).
 */
export function authRedirectInterceptor(req: HttpRequest<unknown>, next: HttpHandlerFn) {
  return next(req).pipe(
    catchError((err: unknown) => {
      if (err instanceof HttpErrorResponse && shouldAuthRedirect(req, err)) {
        redirectToLogin();
      }
      return throwError(() => err);
    }),
  );
}
