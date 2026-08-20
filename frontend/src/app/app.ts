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
   * True when any overlay (workspace, plane, or instance-detail) is
   * currently visible, OR the chat overlay is hidden but a cached
   * instance id is bound to the current instances route and can be
   * re-shown via the same hide button.
   *
   * Drives the unified hide button in the app header so a single
   * affordance can dismiss whichever overlay is on screen, AND so the
   * button is also shown when either overlay is hidden-but-recoverable
   * (the user re-shows via the same button — pure toggle, mirroring
   * the Alt+` workspace hotkey).
   *
   * The 4th term (chat-recoverable) is GATED to ``isInstancesRoute()``
   * on purpose: without the gate, localStorage's cached id (seeded by
   * ``restoreState`` at boot) would force the hide button to render on
   * EVERY route — /, /sources, /jobs, … — even though the chat overlay
   * itself only shows on a detail URL. ``isInstancesRoute`` stays true
   * through the hidden state (``syncDetailVisibility`` writes it on
   * NavigationEnd), so re-show from the detail route is preserved
   * while non-instances routes stop showing the button.
   *
   * The 5th term (workspace-recoverable) is NOT route-gated: the
   * workspace overlay is route-agnostic — Alt+` opens it from any
   * route, the workspace overlay's own Hide button dismisses it from
   * any route, and the cached ``workspaceProjectId`` is the only
   * honest signal that the editor is bound to a project. The bound
   * projectId may correspond to a tab that is no longer active (the
   * user could have switched tabs after opening the workspace);
   * gating on ``activeProjectId`` would deny the unhide affordance
   * in that common case. The N2 route-gate lesson for the chat does
   * not apply here because the workspace has no URL-coupled presence
   * — the persisted id in the service is the only state. It is NOT
   * immortal, however: chat.component.ts ``tabWorkspaceEffect``
   * clears it (``workspaceProjectId.set(null)`` + ``hide()`` when
   * open) when the active project tab switches back to ``All`` — so
   * the unhide affordance correctly disappears once the user leaves
   * the project context entirely. That clear path only exists once
   * ChatComponent has mounted at least once (the effect is a field
   * initializer on the lazily-mounted chat host); before the first
   * chat open the id does persist for the session, matching the
   * editor's "always mounted, display-toggled" caching contract.
   *
   * The 4th term is expressed via ``isHiddenButRecoverable()`` (which
   * already encodes ``!detailVisible() && id !== null && isInstancesRoute()``)
   * so the two signals can never drift on the recoverable predicate.
   * The 5th term is expressed via ``isWorkspaceRecoverable()`` (which
   * encodes ``!showWorkspace() && workspaceProjectId() !== null``) so
   * it cannot drift from the icon/label flip that reads the same
   * predicate.
   * Boolean equivalence to the previous expanded form
   * ``(activeInstanceId() !== null && isInstancesRoute())`` holds because:
   *   - when ``detailVisible()`` is true, term 3 is already true (the
   *     difference between ``(id && isInst)`` and ``isHiddenButRecoverable``
   *     doesn't matter — disjunction absorbs it);
   *   - when ``detailVisible()`` is false, ``!detailVisible()`` is the
   *     identity factor and the two terms collapse to the same
   *     ``id && isInst`` truth value.
   * Net: anyOverlayVisible == showWorkspace || isPlanRoute || detailVisible
   * || isHiddenButRecoverable || isWorkspaceRecoverable, with no change
   * in observable behavior for the existing four terms and the new term
   * adding only the previously-dark workspace-recoverable state.
   */
  readonly anyOverlayVisible = computed(() => {
    return this.workspaceOverlayService.showWorkspace()
      || this.isPlanRoute()
      || this.instancesViewState.detailVisible()
      || this.isHiddenButRecoverable()
      || this.isWorkspaceRecoverable();
  });

  /**
   * True when the chat overlay is hidden but a cached instance id is
   * bound to the current instances route (the pure-toggle recoverable
   * state). Drives the hide button's icon / aria-label flip and the
   * Instances nav-link dead-click guard (see ``onInstancesNavClick``).
   *
   * Now also USED as the 4th term of ``anyOverlayVisible`` so the two
   * signals never disagree about whether the recoverable state is
   * active (previously the 4th term inlined the same predicate; the
   * explicit reference here prevents the two from drifting).
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
   * ``isHiddenButRecoverable`` shape but WITHOUT the route gate — see
   * ``anyOverlayVisible`` for the reasoning (the workspace overlay is
   * route-agnostic; only the bound ``workspaceProjectId`` is the
   * honest signal).
   *
   * Used by:
   *   - ``anyOverlayVisible`` — so the hide button stays rendered
   *     while the workspace is hidden-but-recoverable (otherwise the
   *     user has no affordance to re-show the editor — the workspace
   *     overlay's own Hide button only DOCKS, there is no overlay-side
   *     "show" button).
   *   - ``hideOverlayIcon`` / ``hideOverlayAriaLabel`` — so the icon
   *     flips to ``visibility`` / "Show overlay" while the editor is
   *     recoverable (pure-toggle affordance, matching the "View
   *     workspace" button on the project tab item).
   *   - ``hideActiveOverlay`` workspace branch — so the same button
   *     re-shows the workspace when hidden-but-recoverable.
   */
  readonly isWorkspaceRecoverable = computed(() => {
    return !this.workspaceOverlayService.showWorkspace()
      && this.workspaceOverlayService.workspaceProjectId() !== null;
  });

  /**
   * The show-tier gate: a recoverable overlay (workspace OR chat)
   * may claim the SHOW affordance only when no visible surface
   * competes. Single source of truth for the three consumers that
   * previously hand-duplicated this predicate — the icon, the
   * aria-label, and ``hideActiveOverlay`` branch 2 — so they can
   * never drift. Branch 2 narrows it further with
   * ``isWorkspaceRecoverable() && !isHiddenButRecoverable()`` (the
   * chat-recoverable subset is handled by the later branch, N3).
   */
  readonly showTierActive = computed(() => !this.isPlanRoute()
    && !this.instancesViewState.detailVisible()
    && (this.isWorkspaceRecoverable() || this.isHiddenButRecoverable()));

  /**
   * The hide button's mat-icon — flips between ``visibility_off``
   * (overlay visible → action is HIDE) and ``visibility`` (something
   * is hidden-but-recoverable → action is SHOW). The single
   * affordance visually telegraphs whether clicking it hides the
   * current overlay or re-shows a cached one.
   *
   * Precedence rule (mirrors the handler's branch order):
   *   - Workspace visible → ``visibility_off`` (HIDE), even if the
   *     chat is also recoverable. The handler hides the workspace
   *     and early-returns so chat does not pop underneath (N3
   *     minimize-surprise: "hide" means "hide", not "switch overlays").
   *     The next click (with workspace already hidden) takes the
   *     pure-toggle branch.
   *   - Workspace hidden + (workspace recoverable OR chat
   *     recoverable) → ``visibility`` (SHOW), but ONLY when nothing
   *     is visibly competing — the tier is gated on
   *     ``!isPlanRoute() && !detailVisible()``:
   *       - On /plan with the workspace hidden-but-bound, the plane
   *         iframe is the visible surface (z-1000 over the workspace
   *         z-100). A ``visibility``/"Show overlay" icon would lie:
   *         the click would re-show the workspace UNDER the iframe
   *         (dead click + lying affordance). The plan branch's
   *         pre-existing action (navigate to /instances) owns the
   *         affordance instead (B1).
   *       - With the chat visible (detailVisible=true) and the
   *         workspace recoverable, the visible overlay takes
   *         precedence for the hide affordance: the click hides the
   *         chat (the detail branch), exactly as it did before the
   *         workspace-recoverable feature existed. Recoverable-
   *         workspace re-show only surfaces when nothing visible is
   *         competing (W3, N3 minimize-surprise lineage).
   *     If BOTH are recoverable, the handler's chat-recoverable branch wins
   *     (preserves N3: hiding workspace → next click re-shows chat,
   *     which is the more-stale recoverable state the user most
   *     likely wanted back).
   *   - Nothing visible and nothing recoverable → ``visibility_off``
   *     (HIDE), but the button is hidden via ``anyOverlayVisible``
   *     anyway — this branch is just the default.
   */
  readonly hideOverlayIcon = computed(() => {
    // Both source signals read unconditionally so the reactive
    // graph tracks the full dependency set regardless of branch.
    const workspaceVisible = this.workspaceOverlayService.showWorkspace();
    const showTier = this.showTierActive();
    if (workspaceVisible) return 'visibility_off';
    // B1 + W3: drift-proofed via showTierActive — single source of
    // truth shared with the aria-label and the handler's branch 2.
    if (showTier) return 'visibility';
    return 'visibility_off';
  });

  /**
   * Accessible label for the unified hide button. Mirrors the icon
   * flip so screen readers announce the action the button is about
   * to take ("Hide overlay" vs "Show overlay"). The precedence rule
   * is identical to ``hideOverlayIcon`` — see that field's docblock
   * for the B1 (/plan) and W3 (chat-visible) gate rationale.
   */
  readonly hideOverlayAriaLabel = computed(() => {
    // Both source signals read unconditionally — same tracking
    // discipline as hideOverlayIcon; the label must always announce
    // what the click will actually do.
    const workspaceVisible = this.workspaceOverlayService.showWorkspace();
    const showTier = this.showTierActive();
    if (workspaceVisible) return 'Hide overlay';
    // B1 + W3: drift-proofed via showTierActive — single source of
    // truth shared with the icon and the handler's branch 2.
    if (showTier) return 'Show overlay';
    return 'Hide overlay';
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
   * Hide whichever overlay is currently visible — the branches run in
   * the order they appear in the code below.
   *
   * 1. **Workspace-visible branch** (early-return). When the workspace
   *    overlay is up, dismiss it via the overlay service and stop.
   *    N3 (the combined workspace + chat-hidden state): when the
   *    workspace is visible AND the chat is hidden-but-recoverable, the
   *    click hides the workspace and does NOT pop the chat open
   *    underneath — the user clicked "hide", not "switch overlays". An
   *    early ``return`` after the workspace-hide branch makes the
   *    intent explicit and matches the minimize-surprise reading. The
   *    next click (with workspace already hidden) takes the pure-toggle
   *    branch.
   *
   * 2. **Workspace-recoverable branch** (early-return). When the
   *    workspace editor is hidden but still bound to a project, re-show
   *    it via ``workspaceOverlayService.show(workspaceProjectId())``.
   *    Mirrors the project-tab-bar "View workspace" button's
   *    semantics. Gated by ``!isPlanRoute()`` (B1: on /plan the plane
   *    iframe z-1000 stacks over the workspace z-100 — a workspace
   *    re-show would be an invisible dead click; the plan branch
   *    below owns the click instead, preserving pre-existing plan
   *    semantics) AND ``!detailVisible()`` (W3: a visibly-open chat
   *    takes precedence for the hide affordance — the click hides
   *    the chat, as it did before this feature; the re-show tier
   *    only surfaces when nothing visible competes) AND
   *    ``!isHiddenButRecoverable()`` so the combined
   *    workspace-hidden + chat-recoverable state still
   *    takes the chat-recoverable branch — preserves the N3
   *    sequence ("hide workspace → next click re-shows chat": the
   *    chat was hidden longer ago and is the likelier intent). The
   *    icon/label show-tier is gated identically so the affordance
   *    always telegraphs the action.
   *
   * 3. **Plan-routable branch** (early-return). When the plan route is
   *    active, navigate back to ``/instances``. The plan route is a
   *    real URL with no cached state to toggle — there's no
   *    "hidden-but-recoverable" equivalent on /plan — so the branch
   *    does the only productive thing: take the user to a
   *    sensible default and leave. (B1 note: this branch now fires
   *    even when the workspace is hidden-but-recoverable — see
   *    branch 2's gate.)
   *
   * 4. **Detail-visible branch** (early-return). When the instance
   *    detail is up, toggle ``detailVisible`` straight to false — a
   *    pure signal flip, mirroring the Alt+` workspace hotkey. The URL
   *    stays on ``/projects/<pid>/instances/<iid>`` with the chat
   *    overlay display:none, and the cached id/project/state survive
   *    so the user can re-show via the same button. The user re-shows
   *    via the same hide button (which is still visible because the
   *    cached id is set), so the URL-stuck trap is bypassed — they
   *    never try to re-click the Instances nav link, which would
   *    no-op against the same URL. (W3 note: this branch is reachable
   *    even when the workspace is hidden-but-recoverable — branch 2's
   *    ``!detailVisible()`` gate defers to the visible chat, so the
   *    click hides the chat exactly as it did pre-feature. The
   *    workspace stays recoverable for the next click.)
   *
   * 5. **Hidden-but-recoverable branch** (no early-return). When the
   *    detail is hidden but a cached id is bound to the current
   *    instances route (the "hidden-but-recoverable" state), re-show
   *    the overlay via a pure ``detailVisible.set(true)`` so the URL
   *    stays on the detail route. The plan route was handled above
   *    (case 3) so this branch only fires on instances routes; on a
   *    bare /instances URL the cached id is still set, so re-show
   *    snaps the overlay over the list with the cached detail.
   */
  hideActiveOverlay(): void {
    if (this.workspaceOverlayService.showWorkspace()) {
      this.workspaceOverlayService.hide();
      // N3: workspace is now hidden. Stop here so a recoverable chat
      // does NOT pop open underneath. The next click (with
      // workspace already hidden) takes the pure-toggle branch.
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
    // B1: gated on ``!isPlanRoute()`` — on /plan the plane iframe is
    // the visible surface (z-1000, stacked over the workspace's
    // z-100); re-showing the workspace here would render it UNDER
    // the iframe: a dead click wearing a "Show overlay" icon. The
    // plan branch below (navigate to /instances) owns the click
    // instead, so the button keeps its pre-existing plan semantics.
    // No state is lost: the workspace stays hidden-but-recoverable,
    // so after leaving /plan the affordance picks back up.
    //
    // W3: additionally gated on ``!detailVisible()`` — when the chat
    // overlay is visibly up, it takes precedence for the hide
    // affordance and the click hides the chat (the detail branch
    // below), exactly as it did before this feature existed. The
    // recoverable-workspace re-show only surfaces when nothing
    // visible is competing (N3 minimize-surprise lineage: the button
    // must telegraph HIDE while something is on screen).
    //
    // Also guarded by ``!isHiddenButRecoverable()`` so the combined
    // workspace-hidden + chat-recoverable state still takes the
    // chat-recoverable branch — preserves the N3 sequence ("hide
    // workspace → next click re-shows chat": the chat was hidden
    // longer ago and is the likelier intent).
    if (this.showTierActive()
      && this.isWorkspaceRecoverable()
      && !this.isHiddenButRecoverable()) {
      const boundProjectId = this.workspaceOverlayService.workspaceProjectId();
      if (boundProjectId !== null) {
        this.workspaceOverlayService.show(boundProjectId);
      }
      return;
    }
    if (this.isPlanRoute()) {
      this.router.navigate(['/instances']);
      return;
    }
    // Detail branch: pure signal flip. No navigation — the URL
    // intentionally stays on the detail route so the user can
    // re-show via the same button (the cached id is preserved).
    if (this.instancesViewState.detailVisible()) {
      this.instancesViewState.detailVisible.set(false);
      return;
    }
    if (this.instancesViewState.activeInstanceId() !== null) {
      // Hidden-but-recoverable: re-show via the same hide button.
      this.instancesViewState.detailVisible.set(true);
    }
  }

  /**
   * Click guard for the "Instances" nav link (N1).
   *
   * While the chat overlay is hidden-but-recoverable
   * (``detailVisible``=false, cached id set, URL on a DETAIL route),
   * the nav link's ``routerLink`` resolves to the SAME detail URL via
   * ``lastDetailRoute()``. Angular's router suppresses no-op
   * navigations by default (``onSameUrlNavigation === 'ignore'``), so
   * clicking the link does nothing — the user stares at a blank
   * ``.app-main`` with no NavigationEnd firing, and the overlay stays
   * hidden.
   *
   * The guard detects that exact state and re-shows the overlay
   * directly (a pure ``detailVisible.set(true)``), skipping the
   * router round-trip entirely. Outside that state the guard is a
   * no-op — the routerLink navigates as usual.
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
   *   subset of ``isInstancesRoute()`` excluding the bare list);
   *   ``anyOverlayVisible`` still uses the broader ``isInstancesRoute()``
   *   gate so the hide button keeps rendering on /instances.
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
   * pure-toggle hide branch (``hideActiveOverlay``) flips it directly
   * while staying on the detail URL, and the Instances nav-link
   * dead-click guard (``onInstancesNavClick``) re-shows it when the
   * user clicks a same-URL link while the overlay is
   * hidden-but-recoverable. Both branches are idempotent with respect
   * to the URL — neither writes ``activeInstanceId`` or
   * ``activeProjectId`` — so no id drift can occur.
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
