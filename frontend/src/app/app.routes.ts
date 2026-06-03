import { Routes } from '@angular/router';

export const routes: Routes = [
  { path: '', loadComponent: () => import('./pages/home/home.component').then(m => m.HomeComponent) },
  { path: 'instances', loadComponent: () => import('./pages/instances/instances.component').then(m => m.InstancesComponent) },
  // Backward compatibility: redirect old /instances/:instanceId to /projects/all/instances/:instanceId
  { path: 'instances/:instanceId', redirectTo: 'projects/all/instances/:instanceId', pathMatch: 'full' },
  // New project-aware route
  { path: 'projects/:projectId/instances/:instanceId', loadComponent: () => import('./pages/chat/chat.component').then(m => m.ChatComponent) },
  { path: 'sources', loadComponent: () => import('./components/source-list/source-list.component').then(m => m.SourceListComponent) },
  { path: 'jobs', loadComponent: () => import('./pages/jobs/jobs.component').then(m => m.JobsComponent) },
  { path: 'schedules', loadComponent: () => import('./pages/schedules/schedules.component').then(m => m.SchedulesComponent) },
  { path: 'mcp-servers', loadComponent: () => import('./components/mcp-server-list/mcp-server-list.component').then(m => m.McpServerListComponent) },
  { path: 'migration', loadComponent: () => import('./components/migration/migration.component').then(m => m.MigrationComponent) },
  { path: '**', redirectTo: '' }
];
