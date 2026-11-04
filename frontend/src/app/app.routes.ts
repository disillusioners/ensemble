import { Routes, CanMatchFn, Params, Router, UrlTree } from '@angular/router';
import { inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { catchError, map, of } from 'rxjs';
import type { MaintenanceAvailability } from './models';
import { CheckpointCleanupService } from './pages/maintenance/checkpoint-cleanup/checkpoint-cleanup.service';

/**
 * AM-14 — canMatch guard for the Maintenance section. Returns true
 * iff availability.state === 'ready'. On transport failure, returns
 * `router.parseUrl('/')` so the route is hidden (the SPA fallback
 * home), not errored — matches gear-menu probe semantics: clean hide,
 * never an error toast.
 *
 * The guard does its OWN `/availability` probe. The duplication with
 * `checkMaintenanceAvailability()` in app.ts is intentional: the
 * gear-menu probe runs at app boot; the route guard runs at navigation
 * time (which may be minutes later — deep-link from bookmark, or
 * after a state flip). The probe is cheap (one indexed single-row
 * SELECT — see Focus Area 3 of architect recommendation).
 */
export const maintenanceAvailabilityGuard: CanMatchFn = () => {
  const http = inject(HttpClient);
  const router = inject(Router);
  return http.get<MaintenanceAvailability>(CheckpointCleanupService.AVAILABILITY_URL).pipe(
    map((data) => (data.state === 'ready' ? true : router.parseUrl('/'))),
    catchError(() => of(router.parseUrl('/'))),
  );
};

/**
 * ── THE `/resources` RETIREMENT REDIRECT (D-3 · T1 phase 7, task 7.1) ──
 *
 * The standalone `/resources` page is retired in the SAME release that
 * mounts the resource module as tab 1 of `/metrics`. A bookmarked
 * `/resources?...` must keep working, so the route SURVIVES as a
 * `canMatch` guard that returns a `UrlTree` — the router treats a
 * `UrlTree` returned from `canMatch` as an ABSOLUTE redirect.
 *
 * ── WHY A GUARD AND NOT `redirectTo` ────────────────────────────────────
 *
 * Angular's `redirectTo` matches on the PATH only: the query params are
 * dropped. A bookmarked `/resources?window=24h` would therefore land on
 * the default window — a silent loss. The guard carries them over.
 *
 * ── THE MECHANISM IS NAMED (AM-3) ───────────────────────────────────────
 *
 * A `canMatch` guard receives `(route, segments)` — the PATH SEGMENTS
 * only; the query params are NOT an argument. They are therefore read
 * from the in-flight navigation:
 * `router.getCurrentNavigation()?.extractedUrl.queryParams`.
 *
 * The read is EXPLICITLY NULL-FALLBACKED: with no current navigation the
 * guard LOGS and degrades to a PARAM-LESS redirect. It never crashes and
 * never silently drops params it could have carried.
 *
 * ── THE PRECEDENCE RULE ─────────────────────────────────────────────────
 *
 * The redirect's OWN `tab=resource` OVERRIDES an incoming `tab`: the
 * resource module IS tab 1 of `/metrics`, so a stale `?tab=charts` on a
 * retired URL must not select some other tab. The preservation claim is
 * therefore narrowed to "all params EXCEPT `tab`".
 */

/** The redirect's TARGET path — the metrics page that now hosts tab 1. */
export const RESOURCES_REDIRECT_TARGET = '/metrics';

/**
 * The tab the redirect selects — the resource module's own id.
 *
 * This is a LITERAL on purpose: it is the redirect's OWN copy of the id
 * and the phase-7 spec asserts it against the manifest's tab-1 id
 * (task 7.2). Were it DERIVED from the manifest, that agreement
 * assertion would be vacuous — a manifest rename would silently follow
 * the redirect instead of failing the spec.
 */
export const RESOURCES_REDIRECT_TAB_ID = 'resource';

/** The query-parameter name that carries the selected tab. */
const METRICS_TAB_QUERY_PARAM = 'tab';

/**
 * Build the redirect's `UrlTree` — the target path, the preserved
 * params, and the redirect's own `tab`.
 *
 * The preservation rule: every incoming param is carried over EXCEPT
 * `tab`, which the redirect overrides with its own value.
 */
function resourcesRedirectTree(router: Router, incoming: Params): UrlTree {
  const preserved: Params = {};
  for (const [key, value] of Object.entries(incoming)) {
    if (key === METRICS_TAB_QUERY_PARAM) {
      continue; // the redirect's own tab OVERRIDES the incoming one
    }
    preserved[key] = typeof value === 'string' ? value : String(value);
  }
  preserved[METRICS_TAB_QUERY_PARAM] = RESOURCES_REDIRECT_TAB_ID;

  // `UrlTree.queryParams` is the tree's own param bag; the router's
  // serializer encodes it with Angular's own query encoding when the URL
  // is written, so the values survive the round-trip verbatim (no
  // hand-rolled encoding, no `+`-for-space drift).
  const tree = router.parseUrl(RESOURCES_REDIRECT_TARGET);
  tree.queryParams = preserved;
  return tree;
}

/**
 * The `/resources` → `/metrics?tab=resource` redirect (D-3).
 *
 * Returns a `UrlTree` — never `true` (which would keep the retired
 * route) and never an exception.
 */
export const resourcesRedirectGuard: CanMatchFn = () => {
  const router = inject(Router);
  const incoming = router.getCurrentNavigation()?.extractedUrl.queryParams;

  if (!incoming) {
    // The EXPLICIT null-fallback (AM-3): no navigation to read the
    // params from → log and degrade to a param-less redirect. The
    // operator still lands on the resource tab; only the extra params
    // are lost, and the loss is never silent.
    console.warn(
      '[ResourcesRedirect] no current navigation to read query params from — ' +
        `degrading to a param-less redirect to ${RESOURCES_REDIRECT_TARGET}?` +
        `${METRICS_TAB_QUERY_PARAM}=${RESOURCES_REDIRECT_TAB_ID}.`,
    );
    return resourcesRedirectTree(router, {});
  }

  return resourcesRedirectTree(router, incoming);
};

export const routes: Routes = [
  { path: '', loadComponent: () => import('./pages/home/home.component').then(m => m.HomeComponent) },
  { path: 'instances', loadComponent: () => import('./pages/instances/instances.component').then(m => m.InstancesComponent) },
  // Backward compatibility: redirect old /instances/:instanceId to /projects/all/instances/:instanceId
  { path: 'instances/:instanceId', redirectTo: 'projects/all/instances/:instanceId', pathMatch: 'full' },
  // Project-aware instance detail route. The detail view (ChatComponent)
  // is mounted once at the App root inside .app-main and display-toggled,
  // so this route renders an invisible stub that forwards the route
  // params into InstancesViewStateService for deep-link support. The
  // overlay covers the routed content visually when the view-state is
  // visible.
  { path: 'projects/:projectId/instances/:instanceId', loadComponent: () => import('./pages/instance-detail/instance-detail.component').then(m => m.InstanceDetailComponent) },
  { path: 'projects/:projectId/workspace', loadComponent: () => import('./pages/workspace/workspace.component').then(m => m.WorkspaceComponent), title: 'Workspace Viewer' },
  // Project Blueprint management (Phase 5) — lazy-loaded page
  // scoped by :projectId; the component reads the project id from
  // ActivatedRoute and hits /api/projects/{projectId}/blueprints/*.
  { path: 'projects/:projectId/blueprints', loadComponent: () => import('./pages/blueprint/blueprint.component').then(m => m.BlueprintComponent), title: 'Project Blueprints' },
  { path: 'sources', loadComponent: () => import('./components/source-list/source-list.component').then(m => m.SourceListComponent) },
  { path: 'jobs', loadComponent: () => import('./pages/jobs/jobs.component').then(m => m.JobsComponent) },
  { path: 'settings', loadComponent: () => import('./pages/settings/settings.component').then(m => m.SettingsComponent) },
  // Metrics — the central-metrics charts page (Phase 5 of the
  // central-metrics-v1 lane): the Overview / Charts / Families tabs on
  // the `/api/metrics/*` query door.
  //
  // Declared BEFORE the `:id` route family (`skills/:id`) so Angular's
  // first-match-wins can never shadow a static path — the
  // `/skills/bank` + `/skills/triggers` + `/persona` precedent.
  { path: 'metrics', loadComponent: () => import('./pages/metrics/metrics.component').then(m => m.MetricsComponent), title: 'Metrics' },
  { path: 'skills', loadComponent: () => import('./pages/skills/skills.component').then(m => m.SkillsComponent) },
  // /skills/bank MUST come before /skills/:id — Angular first-match wins
  { path: 'skills/bank', loadComponent: () => import('./pages/skill-bank/skill-bank.component').then(m => m.SkillBankComponent) },
  // /skills/triggers MUST come before /skills/:id — Angular first-match wins
  { path: 'skills/triggers', loadComponent: () => import('./pages/skills/skill-triggers/skill-triggers.page.component').then(m => m.SkillTriggersPageComponent), title: 'Skill Triggers' },
  { path: 'skills/:id', loadComponent: () => import('./pages/skills/skill-detail/skill-detail.component').then(m => m.SkillDetailComponent) },
  // Persona (prompt-version) — agent-prompt-immutability Phase C P1.
  // Static path BEFORE any :id route so Angular's first-match-wins picks
  // it up; mirrors the /skills/bank precedent.
  { path: 'persona', loadComponent: () => import('./pages/persona/persona.component').then(m => m.PersonaComponent), title: 'Persona' },
  { path: 'schedules', loadComponent: () => import('./pages/schedules/schedules.component').then(m => m.SchedulesComponent) },
  { path: 'snapshots', loadComponent: () => import('./pages/snapshots/snapshots.component').then(m => m.SnapshotsComponent), title: 'Snapshots' },
  // Resources — RETIRED (D-3 · T1 phase 7, task 7.1/7.4). The page
  // shell and its lazy import are DELETED with this route; the resource
  // module now lives as tab 1 of `/metrics`. The route SURVIVES as a
  // param-preserving redirect: a `canMatch` guard returning a `UrlTree`
  // (Angular's `redirectTo` would DROP the query params).
  //
  // THE `loadComponent` IS REQUIRED BY ANGULAR (a recorded deviation —
  // see the phase-7 evidence): a route MUST name something to activate
  // (`component` / `loadComponent` / `redirectTo` / `children` /
  // `loadChildren`), and `redirectTo` + `canMatch` is EXPLICITLY
  // forbidden ("Redirects happen before guards are executed"). The
  // fallback therefore points at the REDIRECT'S OWN TARGET — the
  // metrics page. It is unreachable in practice (the guard always
  // returns a `UrlTree`, so the route never activates), and Angular
  // resolves `loadComponent` only in the PREACTIVATION phase — after
  // the guards — so a retired URL never loads it twice.
  {
    path: 'resources',
    canMatch: [resourcesRedirectGuard],
    loadComponent: () => import('./pages/metrics/metrics.component').then(m => m.MetricsComponent),
    title: 'Metrics',
  },
  { path: 'mcp-servers', loadComponent: () => import('./components/mcp-server-list/mcp-server-list.component').then(m => m.McpServerListComponent) },
  { path: 'migration', loadComponent: () => import('./components/migration/migration.component').then(m => m.MigrationComponent) },
  // /plan is a thin route whose content is rendered by the root-mounted
  // Plane iframe overlay (app.html). The component itself is invisible;
  // the overlay covers it when the route is active. Keeping the route
  // registered ensures the Angular router reflects /plan in the URL bar
  // and activates the nav link's `active` class.
  { path: 'plan', loadComponent: () => import('./pages/plan/plan.component').then(m => m.PlanComponent) },
  // Maintenance Console — Section 1 (Checkpoint Cleanup) — Phase 2.
  // Route target is the PAGE SHELL (`MaintenanceComponent`), not the
  // section component directly. The shell renders sections via a local
  // `sections` registry (`maintenance.component.ts`); the registry is
  // load-bearing for extensibility (adding section 2 = one line, no
  // template change).
  //
  // `canMatch` (not `canActivate`) so the router treats the route as
  // absent when not ready — prevents the stale-FE-dist + missing-BE-router
  // class of 404-into-SPA-fallback (AM-14 route hardening).
  {
    path: 'maintenance/checkpoint-cleanup',
    loadComponent: () => import('./pages/maintenance/maintenance.component').then(m => m.MaintenanceComponent),
    canMatch: [maintenanceAvailabilityGuard],
    title: 'Maintenance · Checkpoint Cleanup',
  },
  // Remote Hands / MCP Gateway — the READ-ONLY operator page (Phase C).
  // Lazy loadComponent (the snapshots precedent) so the page cost stays
  // off the initial bundle. The page itself only READS: the gateway is
  // driven by the agent tools, never by the browser.
  { path: 'mcps-gateway', loadComponent: () => import('./pages/mcps-gateway/mcps-gateway.component').then(m => m.McpsGatewayComponent), title: 'MCP Gateway' },
  { path: '**', redirectTo: '' }
];
