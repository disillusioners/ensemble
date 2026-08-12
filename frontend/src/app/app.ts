import { Component, signal, inject, OnInit, HostListener, DestroyRef } from '@angular/core';
import { Router, RouterOutlet, RouterLink, RouterLinkActive, NavigationEnd } from '@angular/router';
import { CommonModule } from '@angular/common';
import { HttpClient } from '@angular/common/http';
import { filter } from 'rxjs/operators';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { MatToolbarModule } from '@angular/material/toolbar';
import { MatIconModule } from '@angular/material/icon';
import { MatButtonModule } from '@angular/material/button';
import { MatMenuModule } from '@angular/material/menu';
import { ApiService } from './services/api.service';
import { SseService } from './services/sse.service';
import { TabStateService } from './services/tab-state.service';
import { WorkspaceOverlayService } from './services/workspace-overlay.service';
import { NotificationBellComponent } from './components/notification-bell/notification-bell.component';
import { JobQueueIndicatorComponent } from './components/job-queue-indicator/job-queue-indicator.component';
import { PlaneViewerComponent } from './components/plane-viewer/plane-viewer.component';
import { WorkspaceComponent } from './pages/workspace/workspace.component';
import type { HealthResponse, MigrationAvailability } from './models';

interface SettingsMenuItem {
  label: string;
  icon: string;
  route: string;
}

interface PlaneConfig {
  enabled: boolean;
  url: string;
}

@Component({
  selector: 'app-root',
  standalone: true,
  imports: [
    CommonModule,
    RouterOutlet,
    RouterLink,
    RouterLinkActive,
    MatToolbarModule,
    MatIconModule,
    MatButtonModule,
    MatMenuModule,
    NotificationBellComponent,
    JobQueueIndicatorComponent,
    PlaneViewerComponent,
    WorkspaceComponent
  ],
  templateUrl: './app.html',
  styleUrl: './app.scss'
})
export class App implements OnInit {
  private readonly api = inject(ApiService);
  private readonly http = inject(HttpClient);
  private readonly router = inject(Router);
  private readonly destroyRef = inject(DestroyRef);
  readonly sseService = inject(SseService);
  /**
   * Singleton state for the global workspace overlay. Exposed to the
   * template so the overlay element's inputs/outputs can bind directly
   * to its signals. The same service instance is injected by the chat
   * page (toggle handlers, tabWorkspaceEffect) and any other consumer
   * that needs to show/hide the overlay.
   */
  protected readonly workspaceOverlayService = inject(WorkspaceOverlayService);
  private readonly tabStateService = inject(TabStateService);

  readonly health = signal<HealthResponse | null>(null);
  readonly isStreaming = this.sseService.isStreaming;
  /**
   * Sticky flag: true once the running daemon reports that PostgreSQL
   * env vars were ever configured. Drives the Database gear-menu item —
   * the menu stays visible even after the active database has been
   * flipped to PostgreSQL, so the operator can still switch back.
   */
  readonly databaseMenuVisible = signal(false);

  /**
   * Plane integration visibility: true when the backend reports
   * ``PLANE_BASE_URL`` is set. Drives the "Plan" nav item and the
   * root-level iframe overlay. Set once from the
   * ``GET /api/settings/plane`` response on app boot.
   */
  readonly planeEnabled = signal(false);
  readonly planeUrl = signal('');
  /**
   * Tracks whether the active route is the ``/plan`` route. When
   * true, the root-level Plane iframe overlay is shown (display:flex);
   * when false, it's hidden (display:none) but kept mounted so the
   * iframe survives route changes and keeps its internal state.
   */
  readonly isPlanRoute = signal(false);

  readonly settingsMenuItems = signal<SettingsMenuItem[]>([
    { label: 'Blueprints', icon: 'architecture', route: '/projects/all/blueprints' },
    { label: 'MCP Servers', icon: 'settings_input_hdmi', route: '/mcp-servers' },
    { label: 'Settings', icon: 'language', route: '/settings' }
  ]);

  constructor() {
    // Initialize synchronously so a deep-link to /plan shows the overlay
    // on the very first paint, before the first NavigationEnd fires.
    this.isPlanRoute.set(this.router.url === '/plan' || this.router.url.startsWith('/plan/'));
    this.router.events.pipe(
      filter(event => event instanceof NavigationEnd),
      takeUntilDestroyed(this.destroyRef)
    ).subscribe(() => {
      this.isPlanRoute.set(this.router.url === '/plan' || this.router.url.startsWith('/plan/'));
     });
   }

  /**
   * Global Alt+` hotkey that toggles the workspace overlay from any
   * route. Bound at the document level so it fires regardless of which
   * element has focus. The gate on `activeProjectId` mirrors the
   * chat-header toggle: the overlay only makes sense when a project
   * tab is active (the "All" tab has no project to show).
   *
   * Two refinements:
   * - Skips when focus is in an input/textarea/contentEditable so the
   *   hotkey doesn't fire while the user is typing in chat or message
   *   fields.
   * - Uses `event.code === 'Backquote'` (layout-independent physical key)
   *   and excludes Ctrl/Meta to dodge AltGr conflicts on European
   *   keyboard layouts (where AltGr+` produces the localized character).
   */
  @HostListener('document:keydown', ['$event'])
  onGlobalKeydown(event: KeyboardEvent): void {
    // Don't intercept while typing in inputs/textareas
    const target = event.target as HTMLElement;
    if (target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement || target.isContentEditable) return;

    // Alt+` (without Ctrl/Meta = excludes AltGr on European layouts)
    if (event.altKey && !event.ctrlKey && !event.metaKey && event.code === 'Backquote') {
      const activeProjectId = this.tabStateService.activeProjectId();
      if (activeProjectId === null || activeProjectId === 'all') return;
      event.preventDefault();
      event.stopPropagation();
      this.workspaceOverlayService.toggle(activeProjectId);
    }
  }

  ngOnInit(): void {
    this.loadHealth();
    this.checkMigrationAvailability();
    this.checkPlaneAvailability();
  }

  private loadHealth(): void {
    this.api.health().subscribe({
      next: (data) => {
        this.health.set(data);
      },
      error: (err) => {
        console.error('Failed to load health:', err);
      }
    });
  }

  private checkMigrationAvailability(): void {
    this.http.get<MigrationAvailability>('/api/migration/availability').subscribe({
      next: (data) => {
        // Show the Database menu whenever PostgreSQL env was ever set
        // (sticky — the menu stays visible after the active database
        // flips to PostgreSQL, so the operator can switch back).
        if (data.postgres_env_set && !this.databaseMenuVisible()) {
          this.databaseMenuVisible.set(true);
          this.settingsMenuItems.update(items => [
            ...items,
            { label: 'Database', icon: 'storage', route: '/migration' }
          ]);
        }
      },
      error: () => {
        // Migration endpoint not available; feature stays hidden.
      }
    });
  }

  private checkPlaneAvailability(): void {
    this.http.get<PlaneConfig>('/api/settings/plane').subscribe({
      next: (data) => {
        if (data.enabled && data.url) {
          this.planeUrl.set(data.url);
          this.planeEnabled.set(true);
        }
      },
      error: () => {
        // Plane not configured; feature stays hidden.
      }
    });
  }

  closePlan(): void {
    this.router.navigate(['/instances']);
  }
}
