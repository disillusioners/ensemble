import { Component } from '@angular/core';

/**
 * Inert placeholder for the ``projects/:projectId/instances/:instanceId``
 * route.
 *
 * The ChatComponent (the actual instance detail view) is mounted once at
 * the App root inside ``.app-main`` (alongside the workspace and plane
 * overlays) and display-toggled, so the underlying chat tree stays alive
 * across route changes. This stub exists solely so the Angular router
 * has a real routed component on the path (the URL bar stays in sync
 * and the wildcard fallback doesn't catch the detail URL).
 *
 * As of W5 the stub is INERT — it does NOT read route params and does
 * NOT call the view-state service. The App root's NavigationEnd handler
 * (see ``App.syncDetailVisibility``) is the SINGLE writer to the
 * view-state service, so a deep link to the detail URL is reconciled
 * exactly once via the App constructor's ``syncDetailVisibility(this.router.url)``
 * call. Splitting that responsibility across the stub AND the App root
 * used to cause race conditions on first paint.
 */
@Component({
  selector: 'app-instance-detail',
  standalone: true,
  template: '',
})
export class InstanceDetailComponent {}