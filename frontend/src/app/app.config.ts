import {
  ApplicationConfig,
  inject,
  provideAppInitializer,
  provideBrowserGlobalErrorListeners,
} from '@angular/core';
import { provideRouter } from '@angular/router';
import { provideHttpClient, withInterceptors } from '@angular/common/http';
import { provideAnimations } from '@angular/platform-browser/animations';
import { provideMarkdown, MERMAID_OPTIONS } from 'ngx-markdown';

import { routes } from './app.routes';
import { authRedirectInterceptor } from './services/auth-redirect.interceptor';
import { SessionService } from './services/session.service';

export const appConfig: ApplicationConfig = {
  providers: [
    provideBrowserGlobalErrorListeners(),
    provideRouter(routes),
    // 401 AUTH_REQUIRED (and the followed-302 HTML shape) funnel into ONE
    // full-navigation login redirect — see services/auth-redirect.ts.
    provideHttpClient(withInterceptors([authRedirectInterceptor])),
    provideAnimations(),
    // Session bootstrap: probe /api/auth/me BEFORE the root renders so
    // cold/deep-link unauthenticated visits go to login, not raw 401s.
    provideAppInitializer(() => inject(SessionService).init()),
    provideMarkdown({
      mermaidOptions: {
        provide: MERMAID_OPTIONS,
        useValue: {
          theme: 'dark',
          themeVariables: {
            background: '#0f172a',
            primaryColor: '#1e293b',
            primaryTextColor: '#e2e8f0',
            primaryBorderColor: '#475569',
            lineColor: '#64748b',
            secondaryColor: '#334155',
            tertiaryColor: '#1e293b',
            fontFamily: 'Roboto, "Helvetica Neue", sans-serif',
          },
          securityLevel: 'strict',
        },
      },
    }),
  ],
};
