import { Component, signal, computed, inject, OnInit, HostListener, DestroyRef, effect, viewChild, ViewContainerRef, ComponentRef } from '@angular/core';
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
import { InstancesViewStateService } from './services/instances-view-state.service';
import { NotificationBellComponent } from './components/notification-bell/notification-bell.component';
import { JobQueueIndicatorComponent } from './components/job-queue-indicator/job-queue-indicator.component';
import { PlaneViewerComponent } from './components/plane-viewer/plane-viewer.component';
import { WorkspaceComponent } from './pages/workspace/workspace.component';
// NOTE: ChatComponent is intentionally NOT statically imported here. The
// chat subtree is heavy (markdown rendering chain, Material, etc.) and is
// only ever needed once the user first opens an instance detail. The lazy
// mount below dynamic-imports it on the first ``detailVisible`` flip and
// keeps the created component alive afterwards (createComponent once,
// never destroyed) — this keeps the chat chunk OUT of the initial bundle
// (the eager root-mount previously blew the 6 MB initial budget) while
// preserving the cached-overlay behavior across hide/show cycles.
import type { ChatComponent } from './pages/chat/chat.component';
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
  /**
   * Singleton state for the instance detail overlay (root-mounted
   * chat component). The view-state service is the source of truth
   * for the active instance id / project context / visibility; the
   * App root derives visibility from the current URL so the overlay
   * hides whenever the user navigates to a non-detail route.
   *
   * The Instances nav link's ``routerLink`` is bound to the service's
   * ``lastDetailRoute`` computed so clicking it restores the cached
   * detail view (otherwise the nav link falls back to plain
   * ``/instances`` and the user sees the list).
   */
  protected readonly instancesViewState = inject(InstancesViewStateService);

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

  /**
   * True when any overlay (workspace or plane or instance-detail) is currently visible.
   * Drives the unified hide button in the app header so a single
   * affordance can dismiss whichever overlay is on screen.
   */
  readonly anyOverlayVisible = computed(() => {
    return this.workspaceOverlayService.showWorkspace()
      || this.isPlanRoute()
      || this.instancesViewState.detailVisible();
  });

  /**
   * True when the current URL is any instances-related route — either
   * the bare list (``/instances``) or a project-scoped detail
   * (``/projects/:projectId/instances/:instanceId``). The Instances
   * nav link uses this for its `active` class so the highlight is
   * consistent regardless of whether the cached nav target is the
   * detail route or the list (the routerLink itself is dynamic).
   */
  readonly isInstancesRoute = signal(false);

  // ── Lazy root-mount of the instance-detail (chat) host ──────────────────
  //
  // The chat overlay host used to be a static ``<app-chat>`` element in
  // app.html, which dragged the whole chat chunk (markdown chain,
  // Material, …) into the INITIAL bundle and crossed the 6 MB
  // ``maximumError`` budget. The host is now created on demand:
  //
  //   - app.html renders an EMPTY ``<ng-container #chatHost>`` anchor
  //     (ng-container per the codebase VCR convention — createComponent
  //     inserts as a SIBLING of the anchor, and only ng-container
  //     produces no phantom layout box).
  //   - the effect below dynamic-imports ChatComponent on the FIRST
  //     ``detailVisible`` flip and mounts it once via ``createComponent``;
  //     the component then stays alive across all later hide/show cycles
  //     (the caching behavior is preserved — created once, never
  //     destroyed while the app runs).
  //
  // The mount effect writes ``[visible]`` and ``[style.display]`` inputs
  // on every run so the lazy host keeps the exact same contract the
  // static template binding used to provide.
  /**
   * VCR anchor for the lazily-created chat host. ``viewChild`` resolves
   * after the first CD pass; the effect re-runs once it becomes
   * available.
   */
  private readonly chatHostVcr = viewChild('chatHost', { read: ViewContainerRef });

  /**
   * The single lazily-created ChatComponent reference. Null until the
   * user first opens an instance detail; non-null (and never reset)
   * afterwards.
   */
  private chatHostRef: ComponentRef<ChatComponent> | null = null;

  /**
   * True while the dynamic import for the first mount is in flight.
   * Prevents a rapid visible→false→true toggle from racing TWO
   * ``import().then(createComponent)`` chains and double-mounting the
   * host (the effect re-fires on every ``detailVisible`` flip but the
   * chunk load resolves asynchronously — ``chatHostRef`` is still null
   * during the flight).
   */
  private chatHostLoading = false;

  /**
   * Lazy-mount effect — see the block comment above. Runs on every
   * ``detailVisible`` change: the first visible flip triggers the
   * dynamic import + createComponent; later runs only re-apply the
   * visibility state to the already-mounted host. A field-initializer
   * ``effect()`` (same pattern as ChatComponent's ``tabEffect``) —
   * field initializers run inside the constructor's injection context.
   */
  private readonly lazyChatMountEffect = effect(() => {
    const visible = this.instancesViewState.detailVisible();
    const vcr = this.chatHostVcr();

    if (this.chatHostRef) {
      // Already mounted — refresh the hide/show state on EVERY run,
      // including visible→false (must not bail early: the host would
      // stay display:flex). The ``visible`` signal input drives the
      // chat's internal SSE / keyboard conventions, and the host
      // element's inline style replicates the old ``[style.display]``
      // template binding.
      // NOTE allowSignalWrites: ``setInput`` on a signal input writes
      // the child's ``visible`` input signal synchronously — a plain
      // signal write inside the effect body, which the reactive graph
      // rejects without this flag (NG0602).
      this.chatHostRef.setInput('visible', visible);
      (this.chatHostRef.location.nativeElement as HTMLElement).style.display =
        visible ? 'flex' : 'none';
      return;
    }

    if (!vcr || !visible || this.chatHostLoading) {
      // Nothing mounted and nothing to show (or a mount is already in
      // flight) — nothing to do. The VCR read is kept unconditional so
      // the effect re-runs when the anchor resolves or visibility
      // flips again.
      return;
    }

    // First open — import the chat chunk and mount it once. The
    // dynamic import defers the chunk out of the initial bundle; the
    // promise resolution mounts the component which then stays alive
    // for the rest of the session.
    this.chatHostLoading = true;
    import('./pages/chat/chat.component').then(({ ChatComponent: Chat }) => {
      // Guard against a race where visibility flipped back to false
      // while the chunk was loading: still mount (the host must be
      // created exactly once per session for cache semantics) but
      // reflect the CURRENT visibility on the host.
      const nowVisible = this.instancesViewState.detailVisible();
      const ref = vcr.createComponent(Chat);
      ref.setInput('visible', nowVisible);
      (ref.location.nativeElement as HTMLElement).style.display =
        nowVisible ? 'flex' : 'none';
      // The app is zoneless and the mount runs inside a plain promise
      // callback — no zone tick guarantees an immediate first render
      // of the dynamically created subtree, so drive its initial CD
      // explicitly (once per session; subsequent updates flow through
      // the ``visible`` signal input).
      ref.changeDetectorRef.detectChanges();
      this.chatHostRef = ref;
    }).catch((err) => {
      // Chunk load failed (offline first paint, etc.) — clear the
      // in-flight flag so the next ``detailVisible`` flip retries.
      this.chatHostLoading = false;
      console.error('[App] Failed to lazy-mount the chat overlay:', err);
    });
  }, { allowSignalWrites: true });

  /**
   * Hide whichever overlay is currently visible. If the workspace
   * overlay is up, dismiss it via the overlay service; if the plan
   * route is active, navigate back to /instances; if the instance
   * detail overlay is up, navigate to /instances AND close the
   * overlay. The detail branch MUST navigate (not just close) so the
   * URL leaves the inert detail stub — otherwise the router stays on
   * ``/projects/:pid/instances/:iid`` with nothing visible, the
   * Instances nav link matches that same URL on re-click, the router
   * suppresses the no-op navigation, and the overlay is permanently
   * stuck on a blank screen.
   */
  hideActiveOverlay(): void {
    if (this.workspaceOverlayService.showWorkspace()) {
      this.workspaceOverlayService.hide();
    }
    if (this.isPlanRoute() || this.instancesViewState.detailVisible()) {
      // The NavigationEnd handler will reconcile the detail service
      // from the new /instances URL (syncDetailVisibility -> closeDetail),
      // so we don't need a separate closeDetail() call here.
      this.router.navigate(['/instances']);
    }
  }

  readonly settingsMenuItems = signal<SettingsMenuItem[]>([
    { label: 'Blueprints', icon: 'architecture', route: '/projects/all/blueprints' },
    { label: 'MCP Servers', icon: 'settings_input_hdmi', route: '/mcp-servers' },
    { label: 'Settings', icon: 'language', route: '/settings' }
  ]);

  constructor() {
    // Boot: seed the persisted detail cache from localStorage BEFORE
    // syncDetailVisibility so the "Instances" nav link's
    // ``lastDetailRoute`` computed has a value to return even when the
    // URL on cold-reload is plain ``/`` or ``/instances`` (R6).
    // ``restoreState`` is intentionally non-flipping for visibility —
    // the URL is the source of truth for actually opening the overlay,
    // a cold reload must never auto-show a stale detail view.
    this.instancesViewState.restoreState();
    // Boot: hydrate the persisted project tabs BEFORE syncDetailVisibility
    // so the F3 cold-reload deep-link branch (``addTab`` for a project
    // whose tab isn't open yet) does NOT clobber the saved state with a
    // single-tab payload. Without this restore, a reload on
    // ``/projects/projA/instances/instA`` runs ``addTab({ project_id:
    // 'projA', name: 'projA' })`` while the in-memory openTabs signal is
    // still the default ``[ALL_TAB]``; ``addTab`` then calls
    // ``saveState()`` which overwrites the persisted
    // ``[All, projA, projB, projC]`` with ``[All, projA]`` and the
    // user's other tabs are silently lost on the next ``/instances``
    // visit. Restoring here (no projectIds — async validation still runs
    // in ``InstancesComponent.ngOnInit``) makes the F3 ``tabExists`` check
    // find projA in the restored list and fall through to
    // ``setActiveTab('projA')``, which writes back the same state.
    this.tabStateService.restoreState();
    // Initialize synchronously so a deep-link to /plan shows the overlay
    // on the very first paint, before the first NavigationEnd fires.
    this.isPlanRoute.set(this.router.url === '/plan' || this.router.url.startsWith('/plan/'));
    // Sync the detail overlay visibility with the URL on the very first
    // paint. Otherwise a deep-link to a detail route would render the
    // router-outlet stub for a frame before the stub's ngOnInit fires.
    this.syncDetailVisibility(this.router.url);
    this.router.events.pipe(
      filter(event => event instanceof NavigationEnd),
      takeUntilDestroyed(this.destroyRef)
    ).subscribe((event) => {
      const navEnd = event as NavigationEnd;
      this.isPlanRoute.set(navEnd.urlAfterRedirects === '/plan' || navEnd.urlAfterRedirects.startsWith('/plan/'));
      this.syncDetailVisibility(navEnd.urlAfterRedirects);
    });
  }

  /**
   * Reconcile the instance-detail overlay visibility with the current
   * URL. The detail view shows only when the URL points at a
   * ``projects/:projectId/instances/:instanceId`` route. Any other
   * route (home, sources, jobs, plan, settings, /instances list, …)
   * hides it.
   *
   * This is the SINGLE writer for ``InstancesViewStateService`` (W5):
   * the stub route no longer calls ``openDetail`` itself, so a deep-
   * link to a detail URL is reconciled exactly once via the very first
   * ``syncDetailVisibility(this.router.url)`` call in the constructor.
   * Centralizing avoids two writers racing to set ``detailVisible``.
   *
   * Side effect: updates ``isInstancesRoute`` so the Instances nav
   * link stays highlighted across both the list and detail routes.
   */
  private syncDetailVisibility(url: string): void {
    // WHY the strict capture (W4):
    //   - ``[^/]+`` would happily capture ``?`` query strings into the
    //     project/instance ids because Angular's NavigationEnd
    //     `urlAfterRedirects` includes the query string. Restrict
    //     the capture class to ``[^/?]+`` so a stray ``?foo=bar``
    //     suffix never poisons the persisted cache.
    //   - ``$`` (no trailing ``(\/.*)?$``) keeps the match canonical
    //     — future sub-routes like ``.../instances/:iid/logs`` must
    //     NOT silently match, otherwise the overlay would stay open
    //     after the user navigated to a sub-route. If a sub-route is
    //     ever introduced, extend the regex explicitly here.
    const match = url.match(/^\/projects\/([^/?]+)\/instances\/([^/?]+)$/);
    this.isInstancesRoute.set(url === '/instances' || match !== null);
    if (match) {
      // S2: keep the project tab bar in sync with the detail view's
      // project context, so the tab highlight matches the open
      // instance's project. Without this, opening a deep-link to
      // ``/projects/foo/instances/bar`` would show the detail for
      // ``foo`` while the tab bar stayed on ``All`` — and the tab
      // effect would poll with scope=undefined while the visibility
      // effect polled with scope=foo, racing on
      // ``InstanceService.startPolling``.
      const projectId = match[1];
      if (projectId !== this.tabStateService.activeProjectId()) {
        // F3: when the URL is project-scoped but the project tab
        // isn't open yet (cold-reload deep-link to a project the
        // user has never visited), ``setActiveTab`` silently
        // no-ops, leaving ``activeProjectId()`` out of sync with
        // the URL. The chat page's ``pollingScope()`` derives
        // from ``activeProjectId()``, so pollingScope would
        // resolve to ``undefined`` while the URL is project-scoped
        // — the URL-scope / polling-scope desync. ``addTab``
        // both creates the missing tab and switches to it, so
        // ``activeProjectId()`` agrees with the URL and polling
        // fires with the project scope the user actually navigated
        // to. A later rename flows naturally from listProjects
        // refreshes once the user opens the projects panel.
        const tabExists = this.tabStateService.openTabs().some(t => t.id === projectId);
        if (tabExists) {
          this.tabStateService.setActiveTab(projectId);
        } else {
          this.tabStateService.addTab({ project_id: projectId, name: projectId });
        }
      }
      this.instancesViewState.openDetail(projectId, match[2]);
    } else {
      this.instancesViewState.closeDetail();
    }
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
}
