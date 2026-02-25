import { Routes } from '@angular/router';

export const routes: Routes = [
  { path: '', loadComponent: () => import('./pages/home/home.component').then(m => m.HomeComponent) },
  { path: 'sessions/:sessionId', loadComponent: () => import('./pages/chat/chat.component').then(m => m.ChatComponent) },
  { path: '**', redirectTo: '' }
];
