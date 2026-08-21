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
   *
   * Round 4 (D1): the /plan URL is also the trigger that suppresses
   * the header hide button when the workspace is hidden-but-
   * recoverable (anyOverlayVisible drops the recoverable term on
   * /plan — see that field's docblock). The plane iframe's own
   * dismiss is URL-driven (leaving /plan); there is no in-iframe
   * toolbar surface.
   */
  readonly isPlanRoute = signal(false);

  /**
   * Round 4 contract (D1): the header button is the WORKSPACE EDITOR
   * toggle ONLY, and visibility is gated on the workspace state alone
   * — with one extra constraint: when the user is on /plan, the
   * button only renders if the workspace is currently VISIBLE
   * (anyRecoverable click would re-show the workspace UNDER the plane
   * iframe z-1000 — a dead click). The hidden-but-recoverable branch
   * on /plan is therefore ABSENCE, not "rendered with hide affordance".
   *
   *   - workspace visible → button rendered (always)
   *   - workspace recoverable AND not on /plan → button rendered
   *   - workspace recoverable AND on /plan → button ABSENT (D1)
   *   - workspace hidden AND unbound → button ABSENT (always)
   *
   * Chat-detail visibility terms are deliberately removed:
   *   - ``detailVisible()`` — the chat overlay has its own management
   *     surface (URL-driven via ``syncDetailVisibility``; the
   *     Instances nav-link dead-click guard re-shows when the user
   *     clicks while the cached id survives).
   *   - ``isHiddenButRecoverable()`` — the chat re-show path is
   *     owned by the Instances nav-link dead-click guard
   *     (``onInstancesNavClick``), not the header button.
   *
   * Reachability drives the handler (see ``hideActiveOverlay``):
   * since the button is now absent when the user is on /plan with
   * the workspace hidden-but-recoverable, the previous "navigate
   * to /instances" branch is unreachable and was removed.
   */
  readonly anyOverlayVisible = computed(() => {
    return this.workspaceOverlayService.showWorkspace()
      || (this.isWorkspaceRecoverable() && !this.isPlanRoute());
  });

  /**
   * True when the chat overlay is hidden but a cached instance id is
   * bound to the current instances route (the pure-toggle recoverable
   * state). Drives the Instances nav-link dead-click guard (see
   * ``onInstancesNavClick``).
   *
   * Round 3: NO LONGER feeds the header button's icon / aria-label
   * flip or the button's hide / show affordance. The header button is
   * the workspace editor toggle ONLY; chat re-show is owned entirely
   * by this recoverable predicate + the nav-link dead-click guard.
   * The computed is retained for the guard to keep reading.
   */
  readonly isHiddenButRecoverable = computed(() => {
    return !this.instancesViewState.detailVisible()
      && this.instancesViewState.activeInstanceId() !== null
      && this.isInstancesRoute();
  });

  /**
   * True when the workspace editor is hidden but still bound to a
   * project — i.e. the editor cache is alive (the overlay element is
   * always mounted, ``[style.display]="none"``, but its file tree,
   * open buffers, and Monaco state survive) and can be re-shown by
   * the same hide button that hides the overlays. Mirrors the chat's
   * ``isHiddenButRecoverable`` shape but WITHOUT the route gate — the
   * workspace overlay is route-agnostic; only the bound
   * ``workspaceProjectId`` is the honest signal.
   *
   * Round 3 contract: this is the single source of truth for the
   * header button's show-tier. Used by:
   *   - ``anyOverlayVisible`` — so the header button stays rendered
   *     while the editor is hidden-but-recoverable (the only way the
   *     user gets an UNHIDE affordance for the editor without
   *     reaching for the project tab bar's "View workspace" button).
   *   - ``hideOverlayIcon`` / ``hideOverlayAriaLabel`` — so the icon
   *     flips to ``visibility`` / "Show editor" while the editor is
   *     recoverable.
   *   - ``hideActiveOverlay`` workspace-recoverable branch — so the
   *     same button re-shows the workspace at the bound project.
   *   - ``showTierActive`` — the single workspace-only gate driving
   *     the icon / aria-label / handler branch.
   */
  readonly isWorkspaceRecoverable = computed(() => {
    return !this.workspaceOverlayService.showWorkspace()
      && this.workspaceOverlayService.workspaceProjectId() !== null;
  });

  /**
   * Round 4 contract: the show-tier gate is the workspace tier ONLY.
   * The single consumer is the icon / aria-label flip and the
   * handler's workspace-recoverable branch. The cleanest gate is
   * ``isWorkspaceRecoverable() && !isPlanRoute()``:
   *   - workspace recoverable → eligible for the SHOW affordance
   *   - !isPlanRoute → on /plan the button is absent for this state
   *     (D1: ``anyOverlayVisible`` drops the recoverable term on
   *     /plan), so this gate also locks the handler's branch 2
   *     out of the click path on /plan.
   *
   * Round 4: the handler's branch 2 collapses to this single
   * ``showTierActive()`` check (the previous ``&& isWorkspaceRecoverable
   * && !isPlanRoute`` triple was redundant — reachability guarantees
   * both predicates by the time the click reaches the handler).
   *
   * ``isHiddenButRecoverable`` is RETAINED as a standalone computed
   * (the Instances nav-link dead-click guard at
   * ``onInstancesNavClick`` still reads it) but it no longer feeds
   * the show-tier. Chat re-show moved entirely to the nav-link
   * mechanism + the chat page's own affordance (when present).
   */
  readonly showTierActive = computed(() => {
    return this.isWorkspaceRecoverable() && !this.isPlanRoute();
  });

  /**
   * The hide button's mat-icon — flips between ``visibility_off``
   * (overlay visible → action is HIDE) and ``visibility`` (something
   * is hidden-but-recoverable → action is SHOW). The single
   * affordance visually telegraphs whether clicking it hides the
   * current overlay or re-shows a cached one.
   *
   * Round 3 contract: the button is the workspace editor toggle ONLY,
   * so the precedence rule is the two-state workspace gate. The
   * previous chat / plan permutations are gone — the button does not
   * render at all when the chat is visible or the user is on /plan
   * unless the workspace editor is also visible or recoverable.
   */
  readonly hideOverlayIcon = computed(() => {
    // Read both source signals unconditionally so the reactive
    // graph tracks the full dependency set regardless of branch.
    const workspaceVisible = this.workspaceOverlayService.showWorkspace();
    const showTier = this.showTierActive();
    if (workspaceVisible) return 'visibility_off';
    if (showTier) return 'visibility';
    return 'visibility_off';
  });

  /**
   * Accessible label for the unified hide button. Mirrors the icon
   * flip so screen readers announce the action the button is about
   * to take ("Hide editor" vs "Show editor"). The precedence rule is
   * identical to ``hideOverlayIcon`` — see that field's docblock for
   * the two-state workspace gate rationale.
   */
  readonly hideOverlayAriaLabel = computed(() => {
    const workspaceVisible = this.workspaceOverlayService.showWorkspace();
    const showTier = this.showTierActive();
    if (workspaceVisible) return 'Hide editor';
    if (showTier) return 'Show editor';
    return 'Hide editor';
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

  /**
   * True when the current URL is a project-scoped INSTANCE DETAIL
   * route — a STRICT SUBSET of ``isInstancesRoute`` that excludes the
   * bare ``/instances`` list. Updated in ``syncDetailVisibility`` from
   * the same regex match, alongside ``isInstancesRoute``.
   *
   * Drives the dead-click guard in ``onInstancesNavClick``: the guard
   * must only intercept clicks on DETAIL routes. On the bare list
   * (``/instances``) with a cached id, the URL and the cached target
   * genuinely diverge — clicking the Instances link must navigate
   * normally so the router delivers the user to the cached detail
   * route (the F3 cold-reload deep-link path relies on this).
   */
  readonly isOnDetailRoute = signal(false);

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
   * Round 4 contract: the header button is the WORKSPACE EDITOR
   * toggle ONLY. The handler has exactly two branches (Round 4
   * removed the previous branch-3 plan-navigate path — see below):
   *
   * 1. **Workspace-visible branch** (early-return). When the workspace
   *    overlay is up, dismiss it via the overlay service and stop.
   *    ``workspaceProjectId`` is retained so the button stays rendered
   *    (the recoverable state arms). The next click takes the
   *    workspace-recoverable branch.
   *
   * 2. **Workspace-recoverable branch** (early-return). When the
   *    workspace editor is hidden but still bound to a project, re-show
   *    it via ``workspaceOverlayService.show(workspaceProjectId())``.
   *    Mirrors the project-tab-bar "View workspace" button's
   *    semantics. Gated on ``showTierActive()`` — the same predicate
   *    the icon / aria-label flip reads — so the affordance always
   *    telegraphs the action: visibility / "Show editor" implies
   *    branch 2, visibility_off / "Hide editor" implies branch 1.
   *
   * Round 4 (D1): the previous branch-3 ("plan-routable — navigate to
   * /instances") was REMOVED. Reachability makes it unreachable:
   * ``anyOverlayVisible`` drops the workspace-recoverable term when
   * ``isPlanRoute()`` is true, so on /plan the button only renders
   * when the workspace is visible — and the visible case is owned by
   * branch 1 (hide). There is no remaining "click on /plan with
   * recoverable workspace" path that the handler needs to handle; the
   * gate was redundant defense, not a real click target.
   *
   * If neither branch fires (defensive — both gates are
   * anyOverlayVisible predicates), the click is a no-op.
   *
   * Chat hide / re-show branches were REMOVED (Round 3). Chat
   * toggling moved entirely to the Instances nav-link's dead-click
   * guard (``onInstancesNavClick``) and any chat-side affordance;
   * the header button no longer touches ``detailVisible`` or
   * ``activeInstanceId``.
   */
  hideActiveOverlay(): void {
    if (this.workspaceOverlayService.showWorkspace()) {
      this.workspaceOverlayService.hide();
      // Round 3: workspace is now hidden. The button stays rendered
      // (workspaceProjectId is preserved — the recoverable state
      // arms). The next click takes the workspace-recoverable
      // branch and re-shows the editor at the same project.
      return;
    }
    // Workspace hidden but still bound to a project — re-show the
    // editor via the same button. The toggle path mirrors the
    // project-tab-bar's "View workspace" button (tab-bar calls
    // ``workspaceOverlayService.toggle(projectId)``, which on a
    // hidden-but-bound editor sets show=true — per the service's
    // toggle(), sameId-from-HIDDEN SHOWS; it is not a no-op). We use
    // ``.show()`` explicitly because the outcome is equivalent and
    // the test can assert the cached ``workspaceProjectId`` is
    // preserved (show() re-binds the same id — the cached id is
    // already correct).
    //
    // Round 4: the guard collapses to ``showTierActive()`` — the
    // same computed the icon / aria-label flip reads. Reachability
    // guarantees that on /plan the button is absent (anyOverlayVisible
    // dropped the recoverable term), so by the time we reach this
    // branch, ``showTierActive`` is true iff ``isWorkspaceRecoverable``
    // is true; the redundant ``&& isWorkspaceRecoverable &&
    // !isPlanRoute`` triple-check was a no-op.
    if (this.showTierActive()) {
      const boundProjectId = this.workspaceOverlayService.workspaceProjectId();
      if (boundProjectId !== null) {
        this.workspaceOverlayService.show(boundProjectId);
      }
      return;
    }
    // Defensive no-op: the button is only rendered for the
    // workspace-visible / workspace-recoverable states
    // (``anyOverlayVisible``). If neither holds (e.g. a race
    // between a hide click and a recoverable clear, or a stale
    // click on /plan where the button is now absent), the click
    // is a no-op — the handler returns without mutating state.
  }

  /**
   * Click guard for the "Instances" nav link — defense-in-depth over
   * the chat re-show path (the primary mechanism is URL-driven: the
   * nav link's ``routerLink`` resolves to the cached detail URL via
   * ``lastDetailRoute()``). The guard's role is to handle the
   * hidden-but-recoverable state where a same-URL click would
   * otherwise be suppressed by Angular's
   * ``onSameUrlNavigation === 'ignore'`` and the user would stare at
   * a blank ``.app-main`` while the cached id survives.
   *
   * Round 4: this is the ONLY chat re-show surface — the header
   * button no longer touches ``detailVisible``. The guard is also
   * the only mechanism that re-shows the chat on /plan (where the
   * header button is now absent for the workspace-recoverable case,
   * per D1).
   *
   * Two refinements:
   *
   * - **Bare-/instances exclusion** (Warning #3): the dead-click
   *   guard must NOT fire when the current URL is the bare
   *   ``/instances`` list with a cached id. On the list, the URL and
   *   the cached target genuinely diverge — clicking the Instances
   *   link must navigate normally so the router delivers the user to
   *   the cached detail route (the F3 cold-reload deep-link path
   *   relies on this). The gate uses ``isOnDetailRoute()`` (a strict
   *   subset of ``isInstancesRoute()`` excluding the bare list).
   *
   * - **Modifier-click fall-through** (Warning #1): the guard must
   *   only intercept plain left-click without modifiers. Ctrl-click,
   *   cmd-click, middle-click, shift-click and alt-click fall through
   *   to the browser's native "open in new tab" / "open in new window"
   *   handling — preventing them breaks user expectations about
   *   browser nav shortcuts and would also block middle-click which
   *   users routinely use to open a tab. Modifier flags and ``button``
   *   are the standard MouseEvent properties, no synthetic event
   *   involved.
   */
  onInstancesNavClick(event: MouseEvent): void {
    if (!this.isHiddenButRecoverable()) {
      // Outside the recoverable state — let the routerLink drive the
      // navigation. ``lastDetailRoute()`` may legitimately differ
      // from the current URL here (e.g. user is on / and has a
      // cached detail), so the router does the right thing.
      return;
    }
    // Bare-/instances exclusion (Warning #3): on /instances the URL
    // and the cached target diverge — the link must navigate so the
    // router delivers the user to the cached detail route. Without
    // this gate, we'd preventDefault a genuinely useful navigation
    // and the URL/UI would desync (overlay re-shown while URL stays
    // on /instances, blocking the F3 cold-reload deep-link path).
    if (!this.isOnDetailRoute()) {
      return;
    }
    // Modifier-click fall-through (Warning #1): only intercept a
    // plain left-click. ctrl/cmd/shift/alt-click and middle/right-click
    // bypass the guard so the browser can handle "open in new tab"
    // etc. normally. ``button === 0`` is the left mouse button per
    // the W3C UI Events spec.
    if (event.button !== 0
      || event.ctrlKey || event.metaKey
      || event.shiftKey || event.altKey) {
      return;
    }
    // Recoverable state + plain left-click on a detail URL: prevent
    // the no-op navigation and re-show the overlay directly.
    event.preventDefault();
    this.instancesViewState.detailVisible.set(true);
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
   * Writer scope (W5): this method is the single writer for the
   * ``openDetail`` / ``closeDetail`` calls that bind the cached
   * instance id + project context to the URL — the stub route no
   * longer calls ``openDetail`` itself, so a deep-link to a detail
   * URL is reconciled exactly once via the very first
   * ``syncDetailVisibility(this.router.url)`` call in the constructor.
   * Centralizing avoids two writers racing to bind ids.
   *
   * ``detailVisible`` is NOT exclusively owned by this method: the
   * pure-toggle hide branch (``hideActiveOverlay``) USED TO flip it
   * directly while staying on the detail URL. Round 3 dropped that
   * branch — the header button is the workspace editor toggle ONLY.
   * The Instances nav-link dead-click guard (``onInstancesNavClick``)
   * still re-shows the chat when the user clicks a same-URL link
   * while the overlay is hidden-but-recoverable. Both branches are
   * idempotent with respect to the URL — neither writes
   * ``activeInstanceId`` or ``activeProjectId`` — so no id drift can
   * occur.
   *
   * Side effect: updates ``isInstancesRoute`` so the Instances nav
   * link stays highlighted across both the list and detail routes,
   * AND ``isOnDetailRoute`` so the dead-click guard in
   * ``onInstancesNavClick`` can distinguish detail URLs from the bare
   * ``/instances`` list (the bare list with a cached id is the case
   * where the link must navigate normally — see that method's docs).
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
    // isOnDetailRoute is the strict subset — excludes bare /instances.
    // True exactly when the regex matched, so the dead-click guard
    // only intercepts clicks on detail URLs.
    this.isOnDetailRoute.set(match !== null);
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
