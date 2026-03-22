import { Routes } from '@angular/router';

export const routes: Routes = [
  { path: '', loadComponent: () => import('./pages/home/home.component').then(m => m.HomeComponent) },
  { path: 'sessions/:sessionId', loadComponent: () => import('./pages/chat/chat.component').then(m => m.ChatComponent) },
  { path: 'sources', loadComponent: () => import('./components/source-list/source-list.component').then(m => m.SourceListComponent) },
  { path: 'jobs', loadComponent: () => import('./pages/jobs/jobs.component').then(m => m.JobsComponent) },
  { path: 'schedules', loadComponent: () => import('./pages/schedules/schedules.component').then(m => m.SchedulesComponent) },
  { path: '**', redirectTo: '' }
];
