import { Routes, CanMatchFn, Router } from '@angular/router';
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
  { path: 'skills', loadComponent: () => import('./pages/skills/skills.component').then(m => m.SkillsComponent) },
  // /skills/bank MUST come before /skills/:id — Angular first-match wins
  { path: 'skills/bank', loadComponent: () => import('./pages/skill-bank/skill-bank.component').then(m => m.SkillBankComponent) },
  // /skills/triggers MUST come before /skills/:id — Angular first-match wins
  { path: 'skills/triggers', loadComponent: () => import('./pages/skills/skill-triggers/skill-triggers.page.component').then(m => m.SkillTriggersPageComponent), title: 'Skill Triggers' },
  { path: 'skills/:id', loadComponent: () => import('./pages/skills/skill-detail/skill-detail.component').then(m => m.SkillDetailComponent) },
  { path: 'schedules', loadComponent: () => import('./pages/schedules/schedules.component').then(m => m.SchedulesComponent) },
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
  { path: '**', redirectTo: '' }
];
