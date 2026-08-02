import { Routes } from '@angular/router';

export const routes: Routes = [
  { path: '', loadComponent: () => import('./pages/home/home.component').then(m => m.HomeComponent) },
  { path: 'instances', loadComponent: () => import('./pages/instances/instances.component').then(m => m.InstancesComponent) },
  // Backward compatibility: redirect old /instances/:instanceId to /projects/all/instances/:instanceId
  { path: 'instances/:instanceId', redirectTo: 'projects/all/instances/:instanceId', pathMatch: 'full' },
  // New project-aware route
  { path: 'projects/:projectId/instances/:instanceId', loadComponent: () => import('./pages/chat/chat.component').then(m => m.ChatComponent) },
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
  { path: '**', redirectTo: '' }
];
