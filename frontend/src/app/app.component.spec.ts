import { signal, computed, WritableSignal } from '@angular/core';
import { Router, NavigationEnd } from '@angular/router';
import { Subject } from 'rxjs';
import * as fs from 'fs';
import * as path from 'path';
// Importing the real App class is a compile-time safety net — if the class
// is ever renamed or its file location changes this spec fails fast. The
// behavioural tests below use a logic-mirror (see class doc below) so we
// can stay free of TestBed / Material / HTTP plumbing the way the rest of
// the project does for components with many collaborators.
//
// Type-only import: the spec does not instantiate the real App, so
// there's no need to resolve its transitive imports (which now include
// the chat component and its ngx-markdown chain). The compile-time
// check still fires if App is renamed or removed.
import type { App } from './app';

/**
 * Mock WorkspaceOverlayService. Mirrors the subset of the real service
 * that `anyOverlayVisible` / `hideActiveOverlay` read or write:
 * `showWorkspace` (signal), `workspaceProjectId` (signal),
 * `hide()` / `show()` / `toggle()` (methods). All are exercised by
 * the methods under test — the new workspace-recoverable branch in
 * `hideActiveOverlay` calls `show(workspaceProjectId)` so the mock
 * must mirror both the signal AND the show() method (the previous
 * mock only had `hide()`).
 */
class MockWorkspaceOverlayService {
  /** Mirrors the public WritableSignal<boolean>. */
  readonly showWorkspace: WritableSignal<boolean> = signal(false);

  /** Mirrors the bound project id — drives isWorkspaceRecoverable and
   *  the workspace-recoverable branch of hideActiveOverlay. Production
   *  clears it only via explicit service actions, never on hide — the
   *  mirror keeps the same persistence semantics so the recoverable
   *  predicate reads true after a hide (until the test calls
   *  ``resetProjectId()`` or constructs a fresh mock). */
  readonly workspaceProjectId: WritableSignal<string | null> = signal(null);

  /** Tracks calls; setters exposed for tests that need to flip state. */
  hideCalls = 0;
  showCalls: Array<{ projectId: string }> = [];
  toggleCalls: Array<{ projectId?: string }> = [];

  hide(): void {
    this.hideCalls++;
    this.showWorkspace.set(false);
  }

  /** Mirror of the production ``show(projectId)``. Sets the bound id
   *  AND flips visibility true. Idempotent on the bound id. */
  show(projectId: string): void {
    this.showCalls.push({ projectId });
    this.workspaceProjectId.set(projectId);
    this.showWorkspace.set(true);
  }

  /** Mirror of the production ``toggle(projectId?)``. Same project
   *  + visible → hide. Same project + hidden → show. Different
   *  project → switch to new project (set id + show). No project +
   *  hidden → no-op (no id to bind). */
  toggle(projectId?: string): void {
    this.toggleCalls.push({ projectId });
    const currentId = this.workspaceProjectId();
    const targetId = projectId ?? currentId;
    if (!targetId) return;
    if (this.showWorkspace() && currentId === targetId) {
      this.showWorkspace.set(false);
      return;
    }
    this.workspaceProjectId.set(targetId);
    this.showWorkspace.set(true);
  }

  /** Test helper — clear the bound project id (simulates a session
   *  that has NEVER opened the workspace editor). The real service
   *  has no such helper; tests that need the cold-boot state
   *  (workspaceProjectId=null) use this to avoid a fresh mock. */
  resetProjectId(): void {
    this.workspaceProjectId.set(null);
  }
}

/**
 * Mock Router with the minimum surface needed by `anyOverlayVisible` and
 * `hideActiveOverlay`:
 *
 *  - `url` — read by the constructor (and the NavigationEnd subscriber)
 *    to derive `isPlanRoute`. Exposes a public setter so tests can drive
 *    deep-link / navigation scenarios.
 *  - `events` — an RxJS Subject the constructor subscribes to in order
 *    to refresh `isPlanRoute` whenever `NavigationEnd` fires.
 *  - `navigate` — recorded so tests can assert the plan-route branch
 *    navigates back to /instances.
 *
 * Real Router has many other members; we only stub the surface the
 * methods-under-test need.
 */
class MockRouter {
  /** Current URL. Tests set this directly to drive `isPlanRoute`. */
  url = '/';

  /** Observable the real `App` constructor subscribes to (filtered to NavigationEnd). */
  readonly events = new Subject<unknown>();

  /** Recorded `navigate(commands)` calls. */
  navigateCalls: unknown[][] = [];

  navigate(commands: unknown[]): Promise<boolean> {
    this.navigateCalls.push(commands);
    return Promise.resolve(true);
  }
}

/**
 * Mock InstancesViewStateService. Mirrors the subset of the real
 * service that `anyOverlayVisible` reads and that `hideActiveOverlay`
 * / `syncDetailVisibility` write: `detailVisible` (signal),
 * `activeInstanceId`, `activeProjectId`, `openDetail()`,
 * `clearInstance()`. Tests pass it explicitly to exercise the new
 * detail branch of `hideActiveOverlay` (R4) and the URL→service flow
 * (W5 / R6). The `lastDetailRoute`` computed is also mirrored so the
 * reopen-path test (R7) can assert the cache contract end-to-end.
 */
class MockInstancesViewStateService {
  /** Mirrors the public WritableSignal<boolean>. */
  readonly detailVisible: WritableSignal<boolean> = signal(false);

  /** Mirrors the cached instance id and project context. */
  readonly activeInstanceId: WritableSignal<string | null> = signal(null);
  readonly activeProjectId: WritableSignal<string> = signal('all');

  /**
   * Mirrors the real service's ``lastDetailRoute`` computed. The
   * Instances nav link's routerLink binds to this so the link
   * toggles between the bare ``/instances`` list and the cached
   * detail route (e.g. ``/projects/all/instances/inst-1``). The
   * reopen-path regression (R7) needs to read this exact value to
   * assert the cache is preserved across the hide→reopen cycle.
   */
  readonly lastDetailRoute: Signal<string[] | null> = computed(() => {
    const id = this.activeInstanceId();
    if (!id) return null;
    return ['/projects', this.activeProjectId(), 'instances', id];
  });

  /** Recorded calls for the tests to assert. */
  openDetailCalls: Array<{ projectId: string; instanceId: string }> = [];
  closeDetailCalls = 0;
  clearInstanceCalls: string[] = [];

  openDetail(projectId: string, instanceId: string): void {
    this.openDetailCalls.push({ projectId, instanceId });
    this.activeProjectId.set(projectId || 'all');
    this.activeInstanceId.set(instanceId);
    this.detailVisible.set(true);
  }

  closeDetail(): void {
    this.closeDetailCalls++;
    this.detailVisible.set(false);
  }

  clearInstance(instanceId: string): void {
    this.clearInstanceCalls.push(instanceId);
    if (this.activeInstanceId() !== instanceId) return;
    this.activeInstanceId.set(null);
    this.detailVisible.set(false);
  }

  /** Mirrors the real service's restoreState() (R6 boot wire-up). */
  restoreState(): void {}
}

/**
 * Mock TabStateService. Mirrors the subset the App root reads in
 * ``syncDetailVisibility`` (S2 — sync the project tab bar with the
 * detail's project context). Tracks openTabs so the F3 cold-reload
 * deep-link branch (addTab vs setActiveTab) is exercised by the
 * spec.
 *
 * Also mirrors the production ``saveState()`` side effect so the
 * regression test for the "tabs not remembered on reload" bug can
 * observe the localStorage writes the real service makes. The
 * saved-state key is the same as the production service
 * (``ensemble-project-tabs``); tests use this storage to pin
 * the bug-and-fix behavior end-to-end.
 */
const REGRESSION_STORAGE_KEY = 'ensemble-project-tabs';

class MockTabStateService {
  readonly activeProjectId: WritableSignal<string | null> = signal(null);
  readonly openTabs: WritableSignal<Array<{ id: string; name: string; type: string }>> = signal([
    { id: 'all', name: 'All', type: 'all' }
  ]);
  setActiveTabCalls: string[] = [];
  addTabCalls: Array<{ project_id: string; name: string }> = [];
  /**
   * Mirrors the production ``restoreState()`` call. Clears
   * ``addTabCalls`` / ``setActiveTabCalls`` so regression tests can
   * count calls across the App constructor flow without picking up
   * pre-seed activity.
   */
  restoreStateCalls = 0;

  setActiveTab(tabId: string): void {
    this.setActiveTabCalls.push(tabId);
    // Production behavior: silently no-op when the tab isn't open
    // (the lookup fails and activeTab is left untouched). Tests that
    // exercise this contract pass a project id that hasn't been
    // opened yet and assert activeProjectId stays at its prior value.
    const tab = this.openTabs().find(t => t.id === tabId);
    if (tab) {
      this.activeProjectId.set(tabId);
      this.saveState();
    }
  }

  /**
   * F3: production addTab creates a project tab if missing AND
   * switches to it; no-op switch if already present. The mock
   * mirrors both branches so syncDetailVisibility can be tested
   * for the cold-reload deep-link case.
   *
   * Saves to localStorage on the create-branch (mirrors production
   * ``saveState()``). The pre-existing-tab branch delegates to
   * ``setActiveTab`` which also saves.
   */
  addTab(project: { project_id: string; name: string }): void {
    this.addTabCalls.push(project);
    const existing = this.openTabs().find(t => t.id === project.project_id);
    if (existing) {
      this.activeProjectId.set(project.project_id);
      this.saveState();
      return;
    }
    this.openTabs.update(tabs => [...tabs, { id: project.project_id, name: project.name, type: 'project' }]);
    this.activeProjectId.set(project.project_id);
    this.saveState();
  }

  /**
   * Mirror of the production ``TabStateService.restoreState()``
   * (no projectIds — async validation lives in
   * InstancesComponent.ngOnInit). Reads the persisted state from
   * localStorage and hydrates the signals; missing key is a no-op.
   */
  restoreState(): void {
    this.restoreStateCalls++;
    const raw = localStorage.getItem(REGRESSION_STORAGE_KEY);
    if (!raw) return;
    try {
      const state = JSON.parse(raw) as {
        openTabs: Array<{ id: string; name: string; type: string }>;
        activeTabId: string;
      };
      const validTabs: Array<{ id: string; name: string; type: string }> = [
        { id: 'all', name: 'All', type: 'all' },
      ];
      for (const tab of state.openTabs) {
        if (tab.type === 'project') validTabs.push(tab);
      }
      this.openTabs.set(validTabs);
      const activeTab = validTabs.find(t => t.id === state.activeTabId);
      this.activeProjectId.set(activeTab ? activeTab.id : null);
    } catch {
      // Bad JSON — drop the key, matching the production contract.
      localStorage.removeItem(REGRESSION_STORAGE_KEY);
    }
  }

  /** Mirror of the production ``saveState()`` private method. */
  private saveState(): void {
    const state = {
      openTabs: this.openTabs(),
      activeTabId: this.activeProjectId() ?? 'all',
    };
    localStorage.setItem(REGRESSION_STORAGE_KEY, JSON.stringify(state));
  }
}

/**
 * Logic-mirror of `App`.
 *
 * This project does NOT use Angular TestBed for component tests (see
 * `job-queue-indicator.component.spec.ts`, `instance-delete-dialog.component.spec.ts`,
 * `job-detail-drawer.component.spec.ts`). Components with many
 * collaborators (HTTP, Material, child components, multiple services)
 * are tested through a plain TS class that mirrors the production
 * logic byte-for-byte. The test file still imports the real `App`
 * (top of file) so renames or deletions break the build at compile time.
 *
 * Logic mirrored verbatim from `frontend/src/app/app.ts`:
 *
 *   anyOverlayVisible = computed(() =>
 *     this.workspaceOverlayService.showWorkspace() || this.isPlanRoute()
 *       || this.instancesViewState.detailVisible()
 *   );
 *
 *   hideActiveOverlay(): void {
 *     if (this.workspaceOverlayService.showWorkspace()) {
 *       this.workspaceOverlayService.hide();
 *     }
 *     if (this.isPlanRoute() || this.instancesViewState.detailVisible()) {
 *       this.router.navigate(['/instances']);
 *     }
 *   }
 *
 *   syncDetailVisibility(url): void {
 *     const match = url.match(/^\/projects\/([^/?]+)\/instances\/([^/?]+)$/);
 *     this.isInstancesRoute.set(url === '/instances' || match !== null);
 *     if (match) {
 *       const projectId = match[1];
 *       if (projectId !== this.tabStateService.activeProjectId()) {
 *         // F3: when the URL is project-scoped but the tab isn't
 *         // open (cold-reload deep-link), addTab creates the
 *         // missing tab AND switches to it; setActiveTab silently
 *         // no-ops on an unknown id, which is what previously
 *         // desynced URL-scope from pollingScope() (chat derives
 *         // polling scope from activeProjectId()).
 *         const tabExists = this.tabStateService.openTabs().some(t => t.id === projectId);
 *         if (tabExists) {
 *           this.tabStateService.setActiveTab(projectId);
 *         } else {
 *           this.tabStateService.addTab({ project_id: projectId, name: projectId });
 *         }
 *       }
 *       this.instancesViewState.openDetail(projectId, match[2]);
 *     } else {
 *       this.instancesViewState.closeDetail();
 *     }
 *   }
 *
 *   // Constructor also sets up isPlanRoute from router.url and
 *   // subscribes to NavigationEnd. The constructor calls
 *   // restoreState() on the view-state service (R6) and then
 *   // syncDetailVisibility(this.router.url) — both are mirrored
 *   // below.
 */
class TestableApp {
  /** Mirrors the real constructor body that derives plan route from URL. */
  readonly isPlanRoute: WritableSignal<boolean>;
  readonly isInstancesRoute: WritableSignal<boolean> = signal(false);

  /**
   * Mirrors the production ``isOnDetailRoute`` signal — strict subset
   * of ``isInstancesRoute`` that excludes the bare ``/instances`` list.
   * Updated by ``syncDetailVisibility`` from the same regex match that
   * drives the openDetail/closeDetail branch (production wiring is
   * also in ``syncDetailVisibility``).
   */
  readonly isOnDetailRoute: WritableSignal<boolean> = signal(false);

  /** Mirrors the unified-overlay-visibility computed. The 4th term
   *  (chat-recoverable) is expressed via ``isHiddenButRecoverable``
   *  and the 5th term (workspace-recoverable) is expressed via
   *  ``isWorkspaceRecoverable`` — see app.ts docblock for the
   *  boolean-equivalence derivation. The previous expanded form
   *  ``(activeInstanceId !== null && isInstancesRoute)`` is encoded
   *  verbatim inside ``isHiddenButRecoverable``, so the change is
   *  observationally a no-op for the existing 4 terms but adds the
   *  new workspace-recoverable term (no route gate — see
   *  ``isWorkspaceRecoverable`` docblock for the reasoning). */
  readonly anyOverlayVisible = computed(
    () => this.workspaceOverlayService.showWorkspace()
      || this.isPlanRoute()
      || this.instancesViewState.detailVisible()
      || this.isHiddenButRecoverable()
      || this.isWorkspaceRecoverable()
  );

  /** Mirrors ``isHiddenButRecoverable``: chat is hidden but cached
   *  id is set and the URL is on an instances route. Drives the hide
   *  button's icon/label flip and the Instances nav-link dead-click
   *  guard. Now also feeds ``anyOverlayVisible``'s 4th term (the
   *  same predicate was inlined there before). */
  readonly isHiddenButRecoverable = computed(
    () => !this.instancesViewState.detailVisible()
      && this.instancesViewState.activeInstanceId() !== null
      && this.isInstancesRoute()
  );

  /** Mirrors ``isWorkspaceRecoverable``: workspace editor is hidden
   *  BUT still bound to a project (workspaceProjectId !== null).
   *  Drives the hide button's icon/label flip AND the 5th term of
   *  ``anyOverlayVisible`` so the button stays rendered while the
   *  editor is recoverable. No route gate — the workspace overlay is
   *  route-agnostic and the cached projectId is the only honest
   *  signal (mirrors the production ``isWorkspaceRecoverable``
   *  docblock). */
  readonly isWorkspaceRecoverable = computed(
    () => !this.workspaceOverlayService.showWorkspace()
      && this.workspaceOverlayService.workspaceProjectId() !== null
  );

  /** Mirrors ``showTierActive`` (production drift-proofing): the
   *  show-tier gate delegating to the same predicates — no plan
   *  route, no visible chat, and at least one recoverable overlay.
   *  Single source of truth for the icon / aria-label / handler
   *  branch-2 mirrors below so they can never drift from each
   *  other (or from the production computed). */
  readonly showTierActive = computed(
    () => !this.isPlanRoute()
      && !this.instancesViewState.detailVisible()
      && (this.isWorkspaceRecoverable() || this.isHiddenButRecoverable())
  );

  /** Hide-button icon — mirrors production precedence rule:
   *    - workspace visible → visibility_off (HIDE), even if chat
   *      recoverable (N3 minimize-surprise);
   *    - workspace hidden + no visible surface competing (not on
   *      /plan, chat not visible) + (workspace recoverable OR chat
   *      recoverable) → visibility (SHOW). The B1/W3 gates keep the
   *      icon telegraphing the real action: on /plan the click
   *      navigates (plan branch), with chat up the click hides the
   *      chat (detail branch);
   *    - default → visibility_off (button hidden by anyOverlayVisible
   *      anyway). */
  readonly hideOverlayIcon = computed(() => {
    const workspaceVisible = this.workspaceOverlayService.showWorkspace();
    const showTier = this.showTierActive();
    if (workspaceVisible) return 'visibility_off';
    // B1 + W3 mirror: drift-proofed via showTierActive — single
    // source of truth shared with the aria-label and the handler's
    // branch-2 mirrors (same shape as production).
    if (showTier) return 'visibility';
    return 'visibility_off';
  });

  /** Hide-button aria-label — mirrors the icon flip; the precedence
   *  rule and B1/W3 gates are identical to ``hideOverlayIcon``. */
  readonly hideOverlayAriaLabel = computed(() => {
    const workspaceVisible = this.workspaceOverlayService.showWorkspace();
    const showTier = this.showTierActive();
    if (workspaceVisible) return 'Hide overlay';
    if (showTier) return 'Show overlay';
    return 'Hide overlay';
  });

  constructor(
    protected readonly workspaceOverlayService: MockWorkspaceOverlayService,
    protected readonly router: MockRouter,
    protected readonly instancesViewState: MockInstancesViewStateService,
    protected readonly tabStateService: MockTabStateService = new MockTabStateService(),
  ) {
    // Mirror: R6 boot wire-up — restoreState seeds the cached nav-link
    // route before syncDetailVisibility runs.
    this.instancesViewState.restoreState();
    // Mirror: tab-state restore (regression fix) — hydrate the
    // persisted project tabs BEFORE syncDetailVisibility so the F3
    // cold-reload deep-link branch (``addTab`` for a project whose
    // tab isn't open yet) does NOT clobber the saved state with a
    // single-tab payload. Without this restore, a reload on
    // ``/projects/projA/instances/instA`` runs ``addTab('projA')``
    // while the in-memory openTabs signal is still the default
    // ``[ALL_TAB]``; ``addTab`` then writes
    // ``saveState()`` → localStorage gets ``[All, projA]`` and the
    // user's other tabs are silently lost on the next ``/instances``
    // visit. Restoring here makes the F3 ``tabExists`` check find
    // projA in the restored list and fall through to
    // ``setActiveTab('projA')``, which writes back the same state.
    this.tabStateService.restoreState();
    // Mirror: constructor seeds isPlanRoute from router.url.
    this.isPlanRoute = signal(this.computeIsPlanRoute());
    // Mirror: sync detail visibility from URL on first paint.
    this.syncDetailVisibility(this.router.url);
    // Mirror: subscribe to NavigationEnd so subsequent navigations refresh.
    this.router.events.subscribe((event) => {
      if (event instanceof NavigationEnd) {
        this.isPlanRoute.set(this.computeIsPlanRoute());
        this.syncDetailVisibility((event as NavigationEnd).urlAfterRedirects);
      }
    });
  }

  // ── BUG 3 — lazy root-mount of the chat host (mirror) ──────────────────
  //
  // Production (app.ts ``lazyChatMountEffect``) replaced the static
  // ``<app-chat>`` element — which dragged the chat chunk into the
  // initial bundle (6.09 MB > 6 MB budget) — with a
  // ``ViewContainerRef.createComponent`` mount on the FIRST
  // ``detailVisible`` flip. The mirror below reproduces the mount /
  // no-mount / hide-refresh decision table exactly, driven through a
  // real Angular effect so the reactive wiring is under test. The
  // VCR is faked with the minimum surface the effect touches.
  mountCalls: string[] = [];
  hostMounted = false;
  hostVisibleState: boolean | null = null;
  hostDisplay: string | null = null;
  chatHostLoading = false;

  runLazyChatMountEffect(vcrAvailable: boolean): void {
    const visible = this.instancesViewState.detailVisible();

    if (this.hostMounted) {
      // Already mounted — refresh hide/show state on EVERY run,
      // including visible→false.
      this.hostVisibleState = visible;
      this.hostDisplay = visible ? 'flex' : 'none';
      return;
    }

    if (!vcrAvailable || !visible || this.chatHostLoading) {
      return;
    }

    this.chatHostLoading = true;
    this.mountCalls.push('import+createComponent');
    // The dynamic import resolves synchronously in the mirror.
    const nowVisible = this.instancesViewState.detailVisible();
    this.hostVisibleState = nowVisible;
    this.hostDisplay = nowVisible ? 'flex' : 'none';
    this.chatHostLoading = false;
    this.hostMounted = true;
  }

  private computeIsPlanRoute(): boolean {
    return this.router.url === '/plan' || this.router.url.startsWith('/plan/');
  }

  /** Mirror of the URL → view-state reconciliation. */
  syncDetailVisibility(url: string): void {
    const match = url.match(/^\/projects\/([^/?]+)\/instances\/([^/?]+)$/);
    this.isInstancesRoute.set(url === '/instances' || match !== null);
    // Mirrors the production ``isOnDetailRoute.set(match !== null)``
    // call (app.ts syncDetailVisibility). Strict subset of
    // isInstancesRoute — bare /instances is excluded.
    this.isOnDetailRoute.set(match !== null);
    if (match) {
      const projectId = match[1];
      if (projectId !== this.tabStateService.activeProjectId()) {
        // F3: when the URL is project-scoped but the tab isn't
        // open (cold-reload deep-link), addTab creates the
        // missing tab AND switches to it; setActiveTab silently
        // no-ops on an unknown id, which is what previously
        // desynced URL-scope from pollingScope() (chat derives
        // polling scope from activeProjectId()).
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
   * Mirror of the public hideActiveOverlay method. Pure toggle for
   * the detail branch (no navigation) — the cached id is preserved,
   * the URL stays on the detail route, and the user re-shows via
   * the same hide button (which remains visible because the cached
   * id is set). The plan-routable branch still navigates to
   * /instances (the plan route has no cached state to toggle).
   *
   * N3 — combined workspace + chat-hidden: when the workspace is
   * visible AND the chat is hidden-but-recoverable, the click hides
   * the workspace and does NOT pop the chat open underneath
   * (minimize-surprise: "hide" means "hide", not "switch overlays").
   * The early ``return`` after the workspace-hide branch pins this.
   *
   * Workspace-recoverable — when the workspace editor is hidden but
   * still bound to a project (workspaceProjectId !== null), chat is
   * NOT visible, NOT on /plan, AND chat is NOT also recoverable, the
   * click re-shows the workspace via
   * ``workspaceOverlayService.show(workspaceProjectId())``. The
   * ``!isPlanRoute`` (B1), ``!detailVisible`` (W3) and
   * ``!isHiddenButRecoverable`` gates keep the visible surfaces
   * (plane iframe, open chat) owning the click and preserve the N3
   * sequence — when both are recoverable, the chat-recoverable
   * branch wins (the chat was hidden longer ago, more likely what
   * the user wanted back).
   */
  hideActiveOverlay(): void {
    if (this.workspaceOverlayService.showWorkspace()) {
      this.workspaceOverlayService.hide();
      // N3: workspace is now hidden — stop here so a recoverable
      // chat does NOT pop open underneath. The next click (with
      // workspace already hidden) takes the pure-toggle branch.
      return;
    }
    // Workspace-recoverable + no visible competitor + chat NOT
    // recoverable → re-show the workspace via the same button.
    // Mirrors the production handler (production docblock: "match
    // the tab-bar semantics") including the B1 (!isPlanRoute) and
    // W3 (!detailVisible) gates — drift-proofed via showTierActive.
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
    if (this.instancesViewState.detailVisible()) {
      this.instancesViewState.detailVisible.set(false);
      return;
    }
    if (this.instancesViewState.activeInstanceId() !== null) {
      this.instancesViewState.detailVisible.set(true);
    }
  }

  /**
   * Mirror of the public ``onInstancesNavClick`` dead-click guard
   * (N1). When the chat overlay is hidden-but-recoverable AND the
   * current URL is a DETAIL route AND the click is a plain left-click
   * without modifiers, ``preventDefault`` is called and the overlay
   * is re-shown via a direct ``detailVisible.set``. Otherwise the
   * guard is a no-op and the routerLink drives the navigation as
   * usual.
   *
   * Takes the minimum surface the production method touches: a
   * ``preventDefault``-able event with the standard MouseEvent
   * modifier flags (``button``, ``ctrlKey``, ``metaKey``, ``shiftKey``,
   * ``altKey``) and recorded-call counters so the tests can assert
   * the dead-click path didn't fire on a given input.
   */
  onInstancesNavClickCalls = 0;
  onInstancesNavClickReveals = 0;

  onInstancesNavClick(event: {
    preventDefaultCalls: number[];
    preventDefault: () => void;
    button: number;
    ctrlKey: boolean;
    metaKey: boolean;
    shiftKey: boolean;
    altKey: boolean;
  }): void {
    this.onInstancesNavClickCalls++;
    if (!this.isHiddenButRecoverable()) {
      // Outside the recoverable state — let the routerLink drive the
      // navigation. Tests verify ``preventDefault`` was NOT called.
      return;
    }
    // Bare-/instances exclusion (Warning #3): on /instances the URL
    // and the cached target diverge — the link must navigate so the
    // router delivers the user to the cached detail route. Mirrors
    // the production ``!isOnDetailRoute()`` early-return.
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
    // the no-op navigation and re-show.
    event.preventDefault();
    this.instancesViewState.detailVisible.set(true);
    this.onInstancesNavClickReveals++;
  }
}

// ---------------------------------------------------------------------------
// Factory: build a TestableApp with the given initial URL and overlay state.
// ---------------------------------------------------------------------------

function makeApp(initial: {
  url?: string;
  showWorkspace?: boolean;
  detailVisible?: boolean;
  workspaceProjectId?: string;
} = {}): {
  app: TestableApp;
  overlay: MockWorkspaceOverlayService;
  router: MockRouter;
  viewState: MockInstancesViewStateService;
} {
  const overlay = new MockWorkspaceOverlayService();
  const router = new MockRouter();
  const viewState = new MockInstancesViewStateService();
  router.url = initial.url ?? '/';
  if (initial.workspaceProjectId !== undefined) {
    // Set the bound id first so show=true below preserves it. The
    // production contract is: projectId binds via ``show()`` /
    // ``toggle()``; if a test sets showWorkspace=true directly
    // without setting projectId, the editor is in the
    // "visible-but-unbound" state (the recoverable predicate is
    // false), which mirrors the same state in production if a
    // caller manually flips ``showWorkspace.set(true)``.
    overlay.workspaceProjectId.set(initial.workspaceProjectId);
  }
  if (initial.showWorkspace) {
    overlay.showWorkspace.set(true);
  }
  if (initial.detailVisible) {
    viewState.detailVisible.set(true);
  }
  const app = new TestableApp(overlay, router, viewState);
  return { app, overlay, router, viewState };
}

// ---------------------------------------------------------------------------
// 1) Real `App` class must be exportable from `./app`.
//    Compile-time safety net — if this test compiles, the class exists.
// ---------------------------------------------------------------------------

describe('App class import', () => {
  it('App is exported from ./app', () => {
    // Compile-time check: the `import type { App }` at the top of
    // this file fails if App is renamed or removed from './app'. The
    // import is `type`-only so the transitive ngx-markdown chain
    // never resolves at runtime — the behaviour tests below use the
    // TestableApp mirror.
    void (0 as unknown as App);
  });
});

// Clear localStorage between every test. The MockTabStateService now
// mirrors the production saveState() side effect (it writes to
// ``ensemble-project-tabs``), so a test that constructs an App on a
// detail URL will leave a stale entry behind in the same domain the
// next test's TabStateService.restoreState() reads. Without the wipe,
// the F3 ``tabExists`` short-circuit fires from a previous test's
// residue and the assertion ``setActiveTabCalls === ['proj-a']``
// fails (the F3 branch is a no-op because restoreState already
// hydrated ``activeProjectId`` to ``'proj-a'``).
beforeEach(() => {
  localStorage.removeItem(REGRESSION_STORAGE_KEY);
});
afterEach(() => {
  localStorage.removeItem(REGRESSION_STORAGE_KEY);
});

// ---------------------------------------------------------------------------
// 2) anyOverlayVisible — six behaviour tests.
// ---------------------------------------------------------------------------

describe('App.anyOverlayVisible', () => {
  it('(a) returns false when neither workspace nor plan route is active', () => {
    const { app } = makeApp({ url: '/', showWorkspace: false });
    expect(app.anyOverlayVisible()).toBe(false);
  });

  it('(b) returns true when workspace overlay showWorkspace() is true', () => {
    const { app } = makeApp({ url: '/', showWorkspace: true });
    expect(app.anyOverlayVisible()).toBe(true);
  });

  it('(c) returns true when router is on /plan route (workspace NOT active)', () => {
    const { app } = makeApp({ url: '/plan', showWorkspace: false });
    expect(app.anyOverlayVisible()).toBe(true);
  });

  // Mirrors the production `anyOverlayVisible` branch for
  // instancesViewState.detailVisible(). The default mock has
  // detailVisible=false, so these tests pass it explicitly to
  // exercise the new third term in the computed.
  it('(g) returns true when instancesViewState.detailVisible() is true (workspace AND plan both false)', () => {
    // R4 / W5: detailVisible=true only persists if the URL actually
    // matches the detail pattern. Use a real detail URL here so the
    // constructor's syncDetailVisibility does not close the overlay
    // mid-test, and so the assertion is exercising the same shape
    // production sees.
    const { app } = makeApp({
      url: '/projects/proj-1/instances/inst-1',
      showWorkspace: false,
      detailVisible: true,
    });
    expect(app.anyOverlayVisible()).toBe(true);
  });

  it('(h) returns false when all three terms are false (workspace, plan, AND detailVisible)', () => {
    const { app } = makeApp({
      url: '/',
      showWorkspace: false,
      detailVisible: false,
    });
    expect(app.anyOverlayVisible()).toBe(false);
  });

  // R7 — anyOverlayVisible hidden-but-recoverable branch.
  //
  // The hide button must stay visible after the user hides the chat
  // overlay, so the same button can re-show the cached instance
  // (the pure-toggle R4 contract). ``anyOverlayVisible`` returns
  // true when the view-state service has a cached ``activeInstanceId``
  // even though ``detailVisible`` is false.
  it('(R7) returns true when activeInstanceId is set but detailVisible is false (hidden-but-recoverable)', () => {
    // Boot on a detail URL — the constructor's syncDetailVisibility
    // seeds the cached id via openDetail, then the user toggles the
    // overlay closed via hideActiveOverlay. The hide button must
    // remain visible so the user can re-show the same instance.
    const { app, viewState } = makeApp({
      url: '/projects/all/instances/inst-1',
      showWorkspace: false,
    });
    expect(viewState.activeInstanceId()).toBe('inst-1');
    // First hide — pure toggle closes the overlay but keeps the id.
    app.hideActiveOverlay();
    expect(viewState.detailVisible()).toBe(false);
    // The hide button is still rendered (visible id → anyOverlayVisible).
    expect(app.anyOverlayVisible()).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// 3) hideActiveOverlay — three behaviour tests.
// ---------------------------------------------------------------------------

describe('App.hideActiveOverlay', () => {
  it('(d) calls workspaceOverlayService.hide() when workspace overlay is active', () => {
    const { app, overlay, router } = makeApp({
      url: '/',
      showWorkspace: true,
    });

    app.hideActiveOverlay();

    expect(overlay.hideCalls).toBe(1);
    expect(overlay.showWorkspace()).toBe(false);
    // Plan route is inactive → no navigation should fire.
    expect(router.navigateCalls).toEqual([]);
  });

  it('(e) calls router.navigate(["/instances"]) when on plan route and workspace is NOT active', () => {
    const { app, overlay, router } = makeApp({
      url: '/plan',
      showWorkspace: false,
    });

    app.hideActiveOverlay();

    expect(router.navigateCalls).toEqual([['/instances']]);
    // Workspace overlay was inactive → hide() must NOT have been called.
    expect(overlay.hideCalls).toBe(0);
  });

  it('(f) handles both overlays visible simultaneously — workspace hide runs and PLAN navigation is skipped (N3)', () => {
    // Production ``hideActiveOverlay`` early-returns after the
    // workspace hide branch (N3: "hide" means "hide", not "switch
    // overlays"). The OLD behavior also fired ``router.navigate`` to
    // /instances when the plan route was active, but with the early
    // return the plan navigation is suppressed while the workspace
    // is up — the user's next click (with workspace already hidden)
    // takes the plan navigation branch. This pins the N3 intent.
    const { app, overlay, router } = makeApp({
      url: '/plan',
      showWorkspace: true,
    });

    app.hideActiveOverlay();

    expect(overlay.hideCalls).toBe(1);
    expect(overlay.showWorkspace()).toBe(false);
    // No navigation — the early return suppressed it.
    expect(router.navigateCalls).toEqual([]);
  });

  // R4 — Hide button detail branch: pure signal flip (toggle).
  //
  // The previous behaviour navigated to /instances on hide. The user
  // reported "the selection is lost" when re-showing the chat overlay
  // via the Instances nav link — the URL change was unnecessary, and
  // the navigate side-effect caused a confusing UX hop to the list
  // page while the chat state was still alive (the cached id and
  // log entries survive, but the user has to re-click the nav link
  // to restore the overlay).
  //
  // The fix: detail branch is a PURE toggle. ``detailVisible``
  // flips directly to false, no navigation. The URL stays on the
  // detail route; the user re-shows via the SAME hide button (which
  // remains visible because the cached id is set, see
  // ``anyOverlayVisible``). Cached id, project, and chat overlay
  // state all survive the toggle.
  //
  // Previously (R4 v1) this branch navigated to /instances to avoid
  // the URL-stuck trap (re-clicking the Instances nav link matched
  // the same URL and the router suppressed the no-op navigation).
  // That trap is sidestepped now because the user re-shows via the
  // hide button itself, not the nav link.
  it('(R4) detail branch is a pure toggle — detailVisible flips, no navigation, id preserved', () => {
    const { app, overlay, router, viewState } = makeApp({
      url: '/projects/all/instances/inst-1',
      showWorkspace: false,
      detailVisible: true,
    });

    // Boot seeded the detail via syncDetailVisibility on the initial
    // URL — openDetail was called with the right ids.
    expect(viewState.activeInstanceId()).toBe('inst-1');
    expect(viewState.detailVisible()).toBe(true);

    app.hideActiveOverlay();

    // CRITICAL: the hide button does NOT navigate. The URL stays on
    // the detail route, the cached id is preserved, and the user
    // re-shows via the same hide button. The detailVisible flag
    // flips directly (no need to round-trip through syncDetailVisibility
    // because the URL is already on the detail route).
    expect(router.navigateCalls).toEqual([]);
    expect(viewState.detailVisible()).toBe(false);
    expect(viewState.activeInstanceId()).toBe('inst-1'); // preserved
    expect(viewState.activeProjectId()).toBe('all');    // preserved
    expect(overlay.hideCalls).toBe(0);
    // anyOverlayVisible is still true because the cached id is set
    // — the hide button remains visible, ready to re-show.
    expect(app.anyOverlayVisible()).toBe(true);
  });

  it('(R4) hidden-but-recoverable: re-click on the hide button re-shows the same overlay', () => {
    // Initial state: boot on the detail URL — the constructor's
    // syncDetailVisibility seeded the cached id AND flipped the
    // overlay open. The user clicks hide (pure toggle) which closes
    // the overlay but keeps the cached id. The hide button is still
    // visible. The user clicks hide again — the toggle re-shows.
    const { app, viewState } = makeApp({
      url: '/projects/all/instances/inst-1',
      showWorkspace: false,
    });
    // Pre-condition: detailVisible is true (constructor opened it).
    expect(viewState.activeInstanceId()).toBe('inst-1');
    expect(viewState.detailVisible()).toBe(true);

    // First click — hide.
    app.hideActiveOverlay();
    expect(viewState.detailVisible()).toBe(false);
    expect(viewState.activeInstanceId()).toBe('inst-1'); // preserved
    expect(app.anyOverlayVisible()).toBe(true); // button still visible

    // Second click — re-show.
    app.hideActiveOverlay();
    expect(viewState.detailVisible()).toBe(true);
    expect(viewState.activeInstanceId()).toBe('inst-1');
    expect(viewState.activeProjectId()).toBe('all');
  });

  it('(R4) detail+workspace combined: workspace hides, detail stays visible (N3 minimize-surprise)', () => {
    // N3: when the workspace is visible AND the chat overlay is
    // hidden-but-recoverable, one click hides the workspace and
    // does NOT pop the chat open underneath. The intent is
    // "hide" not "switch overlays" — minimize-surprise. The
    // early ``return`` after the workspace-hide branch pins this.
    // After the click, the user clicks hide again (workspace
    // already hidden) and the pure-toggle branch flips the chat
    // open. This combines (f) and (R4) into one scenario.
    const { app, overlay, router, viewState } = makeApp({
      url: '/projects/foo/instances/bar',
      showWorkspace: true,
    });

    app.hideActiveOverlay();

    expect(overlay.hideCalls).toBe(1);
    // Pure-toggle detail branch never ran — workspace hide returned
    // early. detailVisible stays true (the chat overlay was visible
    // before the click; the workspace overlay layered above it).
    expect(router.navigateCalls).toEqual([]);
    expect(viewState.detailVisible()).toBe(true);
    expect(viewState.activeInstanceId()).toBe('bar');  // preserved
  });

  it('(R4) no cached id + no overlay visible = hide button is inert (no-op)', () => {
    // The pure-toggle path requires either ``detailVisible`` or
    // ``activeInstanceId`` to be set; otherwise the hide button is
    // inert (matches the production behaviour — the hide button
    // itself is only rendered when ``anyOverlayVisible`` is true,
    // so this case is defensive coverage).
    const { app, router, viewState } = makeApp({
      url: '/',
      showWorkspace: false,
    });
    expect(viewState.activeInstanceId()).toBeNull();
    expect(viewState.detailVisible()).toBe(false);
    expect(app.anyOverlayVisible()).toBe(false);

    app.hideActiveOverlay();

    expect(router.navigateCalls).toEqual([]);
    expect(viewState.detailVisible()).toBe(false);
    expect(viewState.activeInstanceId()).toBeNull();
  });

  // R7 — Hide button: full hide→reopen roundtrip preserves the
  // selection (the canonical state-preservation contract).
  //
  // The bug report: clicking the hide button while the detail overlay
  // is up made the selection "lost" when the user re-opened the
  // overlay. The fix is the pure toggle (R4) — the user re-shows
  // via the SAME hide button. The cached id + project + chat overlay
  // state all survive the roundtrip.
  //
  // This walks the same surface the live app uses (the view-state
  // service signals + the unified computed) so the assertion is
  // end-to-end, not just a unit-level check on a single method.
  it('(R7) hide→re-show roundtrip preserves activeInstanceId + project + state + lastDetailRoute', () => {
    const { app, router, viewState } = makeApp({
      url: '/projects/all/instances/inst-1',
      showWorkspace: false,
      detailVisible: true,
    });

    // Boot seeded the detail via syncDetailVisibility on the initial
    // URL — openDetail was called with the right ids.
    expect(viewState.activeInstanceId()).toBe('inst-1');
    expect(viewState.activeProjectId()).toBe('all');
    expect(viewState.detailVisible()).toBe(true);
    // N6 — exercise the lastDetailRoute mirror end-to-end so the
    // Instances nav link's routerLink binding is asserted to
    // resolve to the same detail URL across the roundtrip. Without
    // this, a future refactor that drops the computed (or breaks
    // its reactive wiring) would silently regress the cache
    // contract the nav link depends on.
    expect(viewState.lastDetailRoute()).toEqual([
      '/projects', 'all', 'instances', 'inst-1',
    ]);

    // First click — hides the overlay (pure toggle, no navigation).
    app.hideActiveOverlay();
    expect(router.navigateCalls).toEqual([]);
    expect(viewState.detailVisible()).toBe(false);
    expect(viewState.activeInstanceId()).toBe('inst-1'); // preserved
    expect(viewState.activeProjectId()).toBe('all');    // preserved
    // The hide button stays visible — anyOverlayVisible is still
    // true via the cached-id branch (gated by isInstancesRoute,
    // see N2 test below).
    expect(app.anyOverlayVisible()).toBe(true);
    // The cached lastDetailRoute survives the toggle — the nav
    // link still resolves to the same detail URL (which is
    // exactly the dead-click trap N1 guards against).
    expect(viewState.lastDetailRoute()).toEqual([
      '/projects', 'all', 'instances', 'inst-1',
    ]);

    // Second click — re-shows the overlay (toggle back to true).
    // The URL is still ``/projects/all/instances/inst-1``; the
    // chat overlay opens with the same instance + messages.
    app.hideActiveOverlay();
    expect(viewState.detailVisible()).toBe(true);
    expect(viewState.activeInstanceId()).toBe('inst-1');
    expect(viewState.activeProjectId()).toBe('all');
    expect(router.navigateCalls).toEqual([]); // still no navigation
    expect(viewState.lastDetailRoute()).toEqual([
      '/projects', 'all', 'instances', 'inst-1',
    ]);
  });
});

// ---------------------------------------------------------------------------
// N2 — anyOverlayVisible 4th-term gate (cached id is only relevant on
// instances routes). Without the gate, the hide button would render on
// every route whenever localStorage had a cached id.
// ---------------------------------------------------------------------------
describe('App.anyOverlayVisible — N2 gate to isInstancesRoute', () => {
  it('(N2-b) cached id + non-instances route (e.g. /sources) → anyOverlayVisible is false', () => {
    // N2: the 4th term of ``anyOverlayVisible`` is gated to
    // ``isInstancesRoute``. Without the gate, ``restoreState``'s
    // localStorage-cached id (seeded at boot) would force the hide
    // button to render on every route — /, /sources, /jobs, … —
    // even though the chat overlay itself only shows on a detail
    // URL. The pre-seed below mirrors the cold-reload-on-/sources
    // case: the cached id is in localStorage, the URL is not a
    // detail URL.
    const { app, viewState } = makeApp({ url: '/sources', showWorkspace: false });
    // Simulate localStorage restore: activeInstanceId set, URL not
    // matching the detail pattern. ``syncDetailVisibility`` ran
    // closeDetail (detailVisible=false) without touching the id.
    viewState.activeInstanceId.set('inst-1');
    expect(app.isInstancesRoute()).toBe(false);
    expect(viewState.detailVisible()).toBe(false);

    // The hide button must NOT render — cached id alone is not
    // enough; the URL must also be on an instances route.
    expect(app.anyOverlayVisible()).toBe(false);
    // The recoverable state is also false (gated off), so the
    // Instances nav-link dead-click guard does not fire either.
    expect(app.isHiddenButRecoverable()).toBe(false);
  });

  it('(N2-a) cached id + instances route + hidden → anyOverlayVisible is true (re-showable)', () => {
    // The (R7) hidden-but-recoverable test already exercises a
    // detail URL; this variant exercises the bare /instances list
    // (which also satisfies isInstancesRoute — the gate accepts
    // either /instances or a detail URL).
    const { app, viewState } = makeApp({
      url: '/projects/all/instances/inst-1',
      showWorkspace: false,
    });
    app.hideActiveOverlay();
    expect(viewState.detailVisible()).toBe(false);
    expect(viewState.activeInstanceId()).toBe('inst-1');
    expect(app.isInstancesRoute()).toBe(true);
    expect(app.anyOverlayVisible()).toBe(true);
    expect(app.isHiddenButRecoverable()).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// N1 — hide button affordance flip (icon + aria-label) and the
// Instances nav-link dead-click guard.
// ---------------------------------------------------------------------------
describe('App hide-button affordance — N1 icon/label flip', () => {
  it('visibility_off / "Hide overlay" when overlay is visible (chat open)', () => {
    // Default boot on a detail URL — the chat overlay is visible.
    const { app } = makeApp({
      url: '/projects/all/instances/inst-1',
      showWorkspace: false,
    });
    expect(app.isHiddenButRecoverable()).toBe(false);
    expect(app.hideOverlayIcon()).toBe('visibility_off');
    expect(app.hideOverlayAriaLabel()).toBe('Hide overlay');
  });

  it('visibility / "Show overlay" when overlay is hidden-but-recoverable', () => {
    // First click — hide the chat. The cached id is preserved, the
    // URL is on a detail route, so the recoverable state is true.
    const { app, viewState } = makeApp({
      url: '/projects/all/instances/inst-1',
      showWorkspace: false,
    });
    app.hideActiveOverlay();
    expect(viewState.detailVisible()).toBe(false);
    expect(app.isHiddenButRecoverable()).toBe(true);
    expect(app.hideOverlayIcon()).toBe('visibility');
    expect(app.hideOverlayAriaLabel()).toBe('Show overlay');
  });
});

describe('App.onInstancesNavClick — N1 dead-click guard', () => {
  /**
   * Mock MouseEvent with the minimum surface the production handler
   * touches: ``preventDefault`` (counted so tests can assert the
   * dead-click path didn't fire) plus the standard modifier flags
   * (``button``, ``ctrlKey``, ``metaKey``, ``shiftKey``, ``altKey``)
   * used by the modifier-click fall-through guard. Defaults emulate
   * a plain left-click without modifiers — exactly the case the
   * dead-click guard should intercept.
   */
  function makeEvent(overrides: {
    button?: number;
    ctrlKey?: boolean;
    metaKey?: boolean;
    shiftKey?: boolean;
    altKey?: boolean;
  } = {}): {
    preventDefaultCalls: number;
    preventDefault: () => void;
    button: number;
    ctrlKey: boolean;
    metaKey: boolean;
    shiftKey: boolean;
    altKey: boolean;
  } {
    return {
      preventDefaultCalls: 0,
      preventDefault() { this.preventDefaultCalls++; },
      button: overrides.button ?? 0,
      ctrlKey: overrides.ctrlKey ?? false,
      metaKey: overrides.metaKey ?? false,
      shiftKey: overrides.shiftKey ?? false,
      altKey: overrides.altKey ?? false,
    };
  }

  it('(N1) re-shows the overlay when hidden-but-recoverable (router.navigate is NOT called)', () => {
    // Set up the recoverable state: boot on a detail URL, then
    // first click hides the chat (pure toggle — cached id preserved).
    const { app, router, viewState } = makeApp({
      url: '/projects/all/instances/inst-1',
      showWorkspace: false,
    });
    app.hideActiveOverlay();
    expect(viewState.detailVisible()).toBe(false);
    expect(viewState.activeInstanceId()).toBe('inst-1');
    expect(app.isHiddenButRecoverable()).toBe(true);

    // Simulate the Instances nav-link click.
    const event = makeEvent();
    app.onInstancesNavClick(event);

    // preventDefault fired (suppresses the no-op router navigation).
    expect(event.preventDefaultCalls).toBe(1);
    // The overlay re-shows directly via a pure signal flip.
    expect(viewState.detailVisible()).toBe(true);
    // The cached id is preserved.
    expect(viewState.activeInstanceId()).toBe('inst-1');
    // Router was NOT consulted — the dead-click trap is bypassed.
    expect(router.navigateCalls).toEqual([]);
    expect(app.onInstancesNavClickReveals).toBe(1);
  });

  it('(N1) is a no-op when the chat is already visible (routerLink proceeds normally)', () => {
    // When the chat is visible, the Instances nav link should
    // navigate as usual (the routerLink resolves to the same
    // detail URL only on the hidden state — when visible, the
    // user is already on it and the link acts like a no-op or a
    // fresh navigation depending on router config, but the
    // dead-click guard must NOT interfere).
    const { app, viewState } = makeApp({
      url: '/projects/all/instances/inst-1',
      showWorkspace: false,
      detailVisible: true,
    });
    expect(viewState.detailVisible()).toBe(true);
    expect(app.isHiddenButRecoverable()).toBe(false);

    const event = makeEvent();
    app.onInstancesNavClick(event);
    expect(event.preventDefaultCalls).toBe(0);
    expect(viewState.detailVisible()).toBe(true); // untouched
    expect(app.onInstancesNavClickReveals).toBe(0); // no reveal
  });

  it('(N1) is a no-op on /sources with cached id (N2 gate keeps recoverable state false)', () => {
    // Even though there's a cached id (localStorage restore), when
    // the user is on a non-instances route the recoverable state
    // is false (N2 gate — isInstancesRoute must be true). The
    // nav-link falls back to plain /instances and a click
    // navigates normally — the dead-click guard must NOT fire
    // because the link IS productive (it goes to /instances, a
    // different URL).
    const { app, viewState } = makeApp({ url: '/sources', showWorkspace: false });
    viewState.activeInstanceId.set('inst-1');
    expect(app.isInstancesRoute()).toBe(false);
    expect(app.isHiddenButRecoverable()).toBe(false);

    const event = makeEvent();
    app.onInstancesNavClick(event);
    expect(event.preventDefaultCalls).toBe(0);
    expect(viewState.detailVisible()).toBe(false); // untouched
    expect(app.onInstancesNavClickReveals).toBe(0);
  });

  // (N1) Modifier-click fall-through (Warning #1): the dead-click
  // guard must NOT intercept ctrl-click / cmd-click / shift-click /
  // alt-click / middle-click. Those are the browser's "open in new
  // tab" / "open in new window" shortcuts and the user expects them
  // to work normally — silently swallowing them would be a serious
  // UX regression. Each flag is exercised in its own assertion so a
  // future refactor that drops one of the gates fails fast.
  it('(N1) does NOT intercept ctrl-click (browser "open in new tab" must work)', () => {
    // Set up the recoverable state: boot on a detail URL, then
    // first click hides the chat (pure toggle — cached id preserved).
    // The recoverable state is on, the URL is on a detail route —
    // everything is primed for the dead-click guard to fire EXCEPT
    // the modifier flag, which the test pins.
    const { app, viewState } = makeApp({
      url: '/projects/all/instances/inst-1',
      showWorkspace: false,
    });
    app.hideActiveOverlay();
    expect(app.isHiddenButRecoverable()).toBe(true);
    expect(app.isOnDetailRoute()).toBe(true);

    // ctrl-click (cmd on macOS is metaKey; same guard for both).
    const event = makeEvent({ ctrlKey: true });
    app.onInstancesNavClick(event);

    // preventDefault must NOT fire — the browser should handle the
    // click natively so the user gets a new tab.
    expect(event.preventDefaultCalls).toBe(0);
    // The overlay must NOT pop open via the guard — the click went
    // through to the browser's default handling.
    expect(viewState.detailVisible()).toBe(false);
    expect(app.onInstancesNavClickReveals).toBe(0);
  });

  it('(N1) does NOT intercept middle-click (button !== 0)', () => {
    // Middle-click is the browser's "open in new tab" trigger in
    // many configurations; ``button === 1`` per the W3C UI Events
    // spec. The guard must NOT swallow it.
    const { app, viewState } = makeApp({
      url: '/projects/all/instances/inst-1',
      showWorkspace: false,
    });
    app.hideActiveOverlay();
    expect(app.isHiddenButRecoverable()).toBe(true);

    const event = makeEvent({ button: 1 });
    app.onInstancesNavClick(event);

    expect(event.preventDefaultCalls).toBe(0);
    expect(viewState.detailVisible()).toBe(false);
    expect(app.onInstancesNavClickReveals).toBe(0);
  });

  // (N1) Bare-/instances exclusion (Warning #3): the dead-click
  // guard must NOT fire when the current URL is the bare
  // ``/instances`` list with a cached id. On the list, the URL and
  // the cached target diverge — the link must navigate normally so
  // the router delivers the user to the cached detail route. The
  // pre-fix behavior preventDefaulted a genuinely useful navigation
  // (the F3 cold-reload deep-link path skipped its openDetail
  // write).
  it('(N1) does NOT intercept on bare /instances URL with cached id (let routerLink navigate to the cached detail)', () => {
    // Boot on /instances with a cached id (the cold-reload
    // scenario the F3 path handles). ``syncDetailVisibility`` ran
    // closeDetail, so ``detailVisible`` is false but the cached
    // id survives. ``isInstancesRoute`` is true (the gate accepts
    // the list) so ``isHiddenButRecoverable`` is also true —
    // ``isOnDetailRoute`` is the ONLY signal that distinguishes
    // this case from the recoverable-on-detail-URL case.
    const { app, router, viewState } = makeApp({ url: '/instances', showWorkspace: false });
    viewState.activeInstanceId.set('inst-1');
    // Boot wrote closeDetail — the cached id survives.
    expect(viewState.detailVisible()).toBe(false);
    expect(viewState.activeInstanceId()).toBe('inst-1');
    expect(app.isInstancesRoute()).toBe(true);
    expect(app.isHiddenButRecoverable()).toBe(true);
    expect(app.isOnDetailRoute()).toBe(false); // <-- the disambiguator

    const event = makeEvent();
    app.onInstancesNavClick(event);

    // The guard must NOT intercept — the link is productive (it
    // routes to the cached detail route via routerLink, not the
    // same URL). preventDefault must NOT fire; the routerLink
    // takes the user to the cached detail.
    expect(event.preventDefaultCalls).toBe(0);
    // The overlay does NOT pop open via the guard.
    expect(viewState.detailVisible()).toBe(false);
    expect(app.onInstancesNavClickReveals).toBe(0);
    // The routerLink binding still drives the navigation — the
    // mock records it via the routerService the routerLink binds
    // to (out of scope for this unit test, but the absence of
    // preventDefault is the key observable).
  });
});

// ---------------------------------------------------------------------------
// N3 — combined workspace visible + chat hidden-but-recoverable:
// the click hides the workspace and does NOT pop the chat open
// underneath (minimize-surprise: "hide" means "hide").
// ---------------------------------------------------------------------------
describe('App.hideActiveOverlay — N3 combined workspace + chat-hidden', () => {
  it('(N3) workspace visible + chat hidden-but-recoverable → one click hides workspace, chat stays hidden, no navigation', () => {
    // Reach the recoverable state via boot → first click (chat
    // hides, cached id preserved). Then show the workspace
    // overlay (simulates the user opening it while the chat is
    // hidden). One click on hide — workspace should hide and the
    // chat should NOT pop open underneath.
    const { app, overlay, router, viewState } = makeApp({
      url: '/projects/all/instances/inst-1',
      showWorkspace: false,
    });
    // First click — hide the chat (pure toggle — recoverable state).
    app.hideActiveOverlay();
    expect(viewState.detailVisible()).toBe(false);
    expect(viewState.activeInstanceId()).toBe('inst-1');
    expect(app.isHiddenButRecoverable()).toBe(true);

    // Now show the workspace overlay.
    overlay.showWorkspace.set(true);
    expect(app.anyOverlayVisible()).toBe(true);

    // One click — workspace hides, chat stays hidden (N3 minimize-surprise).
    app.hideActiveOverlay();
    expect(overlay.hideCalls).toBe(1);
    expect(overlay.showWorkspace()).toBe(false);
    expect(router.navigateCalls).toEqual([]);
    // Chat stays hidden — no pop-under.
    expect(viewState.detailVisible()).toBe(false);
    expect(viewState.activeInstanceId()).toBe('inst-1'); // preserved

    // Sanity: now in workspace-hidden + chat-hidden-but-recoverable,
    // another click would re-show the chat (the pure-toggle path
    // runs after the workspace-hide early return).
    app.hideActiveOverlay();
    expect(viewState.detailVisible()).toBe(true);
    expect(viewState.activeInstanceId()).toBe('inst-1');
    expect(overlay.hideCalls).toBe(1); // still 1 — no extra workspace hide
  });
});

// ---------------------------------------------------------------------------
// Workspace-recoverable — the editor is hidden but still bound to a
// project. The header hide button must flip to UNHIDE affordance
// (visibility / "Show overlay") and a click re-shows the editor at
// the same project. The 5th term of anyOverlayVisible keeps the button
// rendered while the recoverable state is active.
//
// These tests pin the behavior the user-reported follow-up bug
// demanded: the prior fix flipped the icon for the chat recoverable
// state but never for the workspace editor — the button kept
// rendering as HIDE while the editor was already hidden, and there
// was no way to re-show the editor from the header.
// ---------------------------------------------------------------------------

describe('App hide-button affordance — workspace-recoverable icon/label flip', () => {
  // MERGED COVERAGE (review W4): tests formerly split as (a) and (c)
  // were duplicates — identical setup (workspace visible + chat
  // recoverable via detailVisible.set(false) on a detail URL) and
  // identical assertions (icon/label hide-precedence + click hides
  // workspace + chat stays hidden + no navigation). One test now
  // pins all three facets of the workspace-VISIBLE precedence rule:
  // the icon/label tier-1 (visibility_off / "Hide overlay"), the
  // handler's N3 early-return (workspace hides, chat does NOT pop
  // open underneath), and the no-navigation guarantee.
  it('(a) workspace visible + chat recoverable → icon visibility_off (hide precedence); click hides workspace, chat stays hidden, no nav', () => {
    // Reach the combined state: chat recoverable (cached id set +
    // detailVisible false, set directly so the hideActiveOverlay
    // call below targets the workspace), then workspace visible.
    // The icon MUST be visibility_off because workspace visible
    // takes precedence over chat recoverable (N3 minimize-surprise:
    // action = HIDE).
    const { app, overlay, router, viewState } = makeApp({
      url: '/projects/all/instances/inst-1',
      workspaceProjectId: 'proj-a',
      showWorkspace: true,
    });
    // Manually enter the chat-recoverable state (skip the toggle
    // call — it would hit the workspace-visible branch first and
    // hide the editor before this test can assert N3).
    viewState.detailVisible.set(false);
    expect(viewState.activeInstanceId()).toBe('inst-1');
    expect(app.isHiddenButRecoverable()).toBe(true);
    expect(app.isWorkspaceRecoverable()).toBe(false); // workspace is visible

    // Workspace visible wins over chat recoverable — icon stays at
    // visibility_off (action = HIDE).
    expect(app.hideOverlayIcon()).toBe('visibility_off');
    expect(app.hideOverlayAriaLabel()).toBe('Hide overlay');

    // Click — workspace hides; chat stays hidden (N3 early return).
    app.hideActiveOverlay();
    expect(overlay.hideCalls).toBe(1);
    expect(overlay.showWorkspace()).toBe(false);
    expect(viewState.detailVisible()).toBe(false); // no pop-under
    expect(router.navigateCalls).toEqual([]); // no nav side-effects
  });

  it('(b) workspace hidden + projectId bound → icon visibility, aria "Show overlay", click → showWorkspace true with SAME projectId', () => {
    // The user-reported bug: editor is hidden but the button kept
    // rendering as HIDE — no UNHIDE affordance. The fix: icon
    // flips to visibility and aria to "Show overlay" while the
    // editor is recoverable (workspaceProjectId !== null).
    const { app, overlay } = makeApp({
      url: '/',
      workspaceProjectId: 'proj-a',
      // showWorkspace deliberately omitted — hidden but bound.
    });
    expect(overlay.showWorkspace()).toBe(false);
    expect(overlay.workspaceProjectId()).toBe('proj-a');
    expect(app.isWorkspaceRecoverable()).toBe(true);

    // Icon + aria MUST reflect the unhide affordance.
    expect(app.hideOverlayIcon()).toBe('visibility');
    expect(app.hideOverlayAriaLabel()).toBe('Show overlay');

    // Click — the editor re-shows with the SAME projectId. The
    // cached binding is the source of truth (no active tab / no
    // URL coupling for the workspace overlay — see
    // isWorkspaceRecoverable docblock).
    app.hideActiveOverlay();
    expect(overlay.showWorkspace()).toBe(true);
    expect(overlay.workspaceProjectId()).toBe('proj-a'); // SAME id
    // hide() must NOT have been called — the click is a pure re-show.
    expect(overlay.hideCalls).toBe(0);
    // show() was called with the cached id.
    expect(overlay.showCalls).toEqual([{ projectId: 'proj-a' }]);
    // The "SAME id" assertion pins the id at the SERVICE-CALL boundary
    // (re-show() re-binds it verbatim); tabWorkspaceEffect may rebind the
    // bound id later once the chat host is mounted (follow-up only).
  });

  // (former test (c) removed — review W4 dedup: it duplicated (a)'s
  //  setup and assertions; the merged (a) above covers workspace-
  //  visible precedence + N3 early-return + chat-stays-hidden.)

  it('(d) anyOverlayVisible true while workspace recoverable, false when never opened (projectId null)', () => {
    // Case 1: workspace hidden but projectId bound → anyOverlayVisible
    // MUST be true so the button stays rendered (otherwise the user
    // has no UNHIDE affordance).
    const recoverable = makeApp({
      url: '/',
      workspaceProjectId: 'proj-a',
    });
    expect(recoverable.app.isWorkspaceRecoverable()).toBe(true);
    expect(recoverable.app.anyOverlayVisible()).toBe(true);

    // Case 2: cold boot — workspace never opened (projectId null),
    // chat never opened either. anyOverlayVisible MUST be false
    // (the button should NOT render — there is no overlay or
    // recoverable state to act on).
    const coldBoot = makeApp({ url: '/' });
    expect(coldBoot.overlay.workspaceProjectId()).toBeNull();
    expect(coldBoot.app.isWorkspaceRecoverable()).toBe(false);
    expect(coldBoot.app.anyOverlayVisible()).toBe(false);
  });

  it('workspace-recoverable + chat recoverable: click re-shows CHAT (N3 sequence preserved)', () => {
    // After N3 first click (workspace visible + chat recoverable →
    // workspace hides, chat stays hidden), the second click must
    // re-show CHAT, not the workspace. The workspace-recoverable
    // branch is guarded by ``!isHiddenButRecoverable`` so the
    // chat-recoverable branch wins when both are recoverable.
    const { app, overlay, viewState } = makeApp({
      url: '/projects/all/instances/inst-1',
      workspaceProjectId: 'proj-a',
      showWorkspace: true,
    });
    // Enter the chat-recoverable state directly (skip the toggle
    // call — it would hit the workspace-visible branch first and
    // hide the editor before this test can assert the N3 first
    // click). After this line: workspace VISIBLE + chat recoverable.
    viewState.detailVisible.set(false);
    expect(viewState.activeInstanceId()).toBe('inst-1');
    expect(app.isHiddenButRecoverable()).toBe(true);

    // First click (N3): hide workspace (early return). Chat
    // stays hidden (no pop-under).
    app.hideActiveOverlay();
    expect(overlay.hideCalls).toBe(1);
    expect(overlay.showWorkspace()).toBe(false);

    // Now BOTH are recoverable — workspace hidden-but-bound, chat
    // hidden-but-cached. The icon must still flip to "show".
    expect(app.isWorkspaceRecoverable()).toBe(true);
    expect(app.isHiddenButRecoverable()).toBe(true);
    expect(app.hideOverlayIcon()).toBe('visibility');

    // Second click — chat re-shows (N3 preserves the "user wanted
    // chat back" sequence). Workspace does NOT re-show.
    app.hideActiveOverlay();
    expect(viewState.detailVisible()).toBe(true);
    expect(overlay.showWorkspace()).toBe(false); // workspace stays hidden
    expect(overlay.showCalls).toEqual([]); // show() was NOT called
  });

  it('(B1) /plan + workspace hidden-but-bound → icon visibility_off, click navigates to /instances (plan branch owns the click)', () => {
    // The review blocker: on /plan with the workspace hidden-but-
    // bound, the OLD branch 2 pre-empted the plan branch and re-
    // showed the workspace — which renders at z-100 UNDER the plane
    // iframe (z-1000): a dead click wearing a "Show overlay" icon.
    // The fix gates branch 2 (and the icon/label show-tier) on
    // ``!isPlanRoute()`` so the button keeps its pre-existing plan
    // semantics: visibility_off + navigate to /instances.
    const { app, overlay, router } = makeApp({
      url: '/plan',
      workspaceProjectId: 'proj-a',
      // showWorkspace deliberately omitted — hidden but bound.
    });
    expect(app.isPlanRoute()).toBe(true);
    expect(app.isWorkspaceRecoverable()).toBe(true); // the tempting state
    expect(overlay.showWorkspace()).toBe(false);

    // The icon/label show-tier MUST be gated on /plan — the click's
    // real action is "navigate", not "show", so the affordance must
    // read visibility_off / "Hide overlay" (pre-existing plan
    // semantics, pinned by the old test (e) in App.hideActiveOverlay).
    expect(app.hideOverlayIcon()).toBe('visibility_off');
    expect(app.hideOverlayAriaLabel()).toBe('Hide overlay');

    // Click — the plan branch fires: navigate to /instances. The
    // workspace must NOT re-show (it would be invisible under the
    // iframe) and the bound id must survive untouched.
    app.hideActiveOverlay();
    expect(router.navigateCalls).toEqual([['/instances']]);
    expect(overlay.showWorkspace()).toBe(false); // NOT set true
    expect(overlay.showCalls).toEqual([]); // show() never called
    expect(overlay.workspaceProjectId()).toBe('proj-a'); // unchanged

    // No state lost: after leaving /plan the recoverable state is
    // still armed (the affordance picks back up on the next route).
    expect(app.isWorkspaceRecoverable()).toBe(true);
  });

  it('(W3) chat visible + workspace recoverable → icon visibility_off, click hides the CHAT (visible overlays take precedence)', () => {
    // The review gate: state cell #4 — chat visibly open +
    // workspace hidden-but-bound on a non-plan route. Branch 2 used
    // to pre-empt hiding the open chat. The fix gates branch 2 (and
    // the icon/label show-tier) on ``!detailVisible()`` so the click
    // hides the chat (the detail branch), exactly as it did before
    // the workspace-recoverable feature existed. Rationale: visible
    // overlays take precedence for the hide affordance (N3
    // minimize-surprise lineage) — recoverable-workspace re-show
    // only surfaces when nothing is visibly competing.
    const { app, overlay, viewState } = makeApp({
      // Real detail URL so syncDetailVisibility keeps the overlay
      // open (the constructor would close it on a non-detail URL).
      url: '/projects/all/instances/inst-1',
      workspaceProjectId: 'proj-a',
      detailVisible: true,
      // showWorkspace deliberately omitted — hidden but bound.
    });
    expect(viewState.detailVisible()).toBe(true); // chat IS visible
    expect(app.isWorkspaceRecoverable()).toBe(true); // and competing

    // The affordance must telegraph HIDE (chat is on screen), not
    // SHOW — the tier-2 gate excludes the chat-visible state.
    expect(app.hideOverlayIcon()).toBe('visibility_off');
    expect(app.hideOverlayAriaLabel()).toBe('Hide overlay');

    // Click — the detail branch fires: chat hides. The workspace
    // must NOT re-show underneath.
    app.hideActiveOverlay();
    expect(viewState.detailVisible()).toBe(false); // chat hidden
    expect(overlay.showWorkspace()).toBe(false); // NOT set true
    expect(overlay.showCalls).toEqual([]); // show() never called

    // And the workspace is STILL recoverable for the next click —
    // nothing was lost, the affordance just deferred.
    expect(app.isWorkspaceRecoverable()).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// 4) syncDetailVisibility — W5 single-writer + R4 detail-branch URL parsing.
//
// The detail route is /projects/:projectId/instances/:instanceId. The
// App root is the SINGLE writer to the view-state service for that
// pattern (W5); the stub route is inert. These tests pin the regex
// shape and the openDetail call against regressions in the URL parser.
// ---------------------------------------------------------------------------

describe('App.syncDetailVisibility — W5 single-writer URL→service flow', () => {
  // We can't import the private method directly, so we exercise it
  // through the public surface (the constructor + NavigationEnd
  // handler). Constructor seeds isPlanRoute from router.url; the
  // NavigationEnd subscriber calls syncDetailVisibility for every
  // navigation. By driving router events through MockRouter.events
  // we can assert what the production code wrote to the view-state
  // service for each URL.

  function makeAppWithNav(initialUrl: string): {
    app: TestableApp;
    router: MockRouter;
    viewState: MockInstancesViewStateService;
  } {
    const overlay = new MockWorkspaceOverlayService();
    const router = new MockRouter();
    const viewState = new MockInstancesViewStateService();
    router.url = initialUrl;
    const app = new TestableApp(overlay, router, viewState);
    return { app, router, viewState };
  }

  it('forwards a detail URL to the view-state service (project id, instance id)', () => {
    // R5 / W5 single writer: deep link to a detail URL is reconciled
    // exactly once via the constructor's syncDetailVisibility call.
    const { viewState } = makeAppWithNav('/projects/proj-a/instances/inst-1');

    expect(viewState.openDetailCalls).toEqual([
      { projectId: 'proj-a', instanceId: 'inst-1' },
    ]);
  });

  it('rejects a sub-route URL (no sticky overlay)', () => {
    // W4: the regex must NOT match sub-routes like
    // /projects/foo/instances/bar/logs — otherwise the overlay would
    // stay open after navigating away from the canonical detail URL.
    const { viewState } = makeAppWithNav('/projects/foo/instances/bar/logs');

    expect(viewState.openDetailCalls).toEqual([]);
  });

  it('rejects a query-string suffix (no poisoned project/instance ids)', () => {
    // W4: ``[^/?]+`` prevents the regex from capturing ``?`` into
    // project/instance ids. Angular's NavigationEnd
    // ``urlAfterRedirects`` includes the query string, so this would
    // have silently corrupted the persisted cache.
    const { viewState } = makeAppWithNav('/projects/foo/instances/bar?from=notifications');

    expect(viewState.openDetailCalls).toEqual([]);
  });

  it('closes the detail when the URL is the bare /instances list', () => {
    // The stub route's /instances list must NOT trigger openDetail —
    // only the canonical /projects/:pid/instances/:iid pattern does.
    const { viewState } = makeAppWithNav('/instances');

    expect(viewState.openDetailCalls).toEqual([]);
    expect(viewState.activeInstanceId()).toBeNull();
  });

  // F3 — URL-scope / polling-scope agreement.
  //
  // The brief: when the URL is project-scoped but the project tab
  // isn't open (cold-reload deep-link), ``setActiveTab`` silently
  // no-ops — leaving ``activeProjectId()`` out of sync with the
  // URL. The chat page's ``pollingScope()`` derives from
  // ``activeProjectId()``, so pollingScope would resolve to
  // ``undefined`` while the URL is project-scoped — the
  // URL-scope / polling-scope desync. The fix uses ``addTab`` when
  // the project isn't already open, so ``activeProjectId()`` agrees
  // with the URL and polling fires with the project scope the user
  // actually navigated to.

  function makeAppWithTabs(initialUrl: string, tabs: Array<{ id: string; name: string; type: string }>): {
    app: TestableApp;
    router: MockRouter;
    viewState: MockInstancesViewStateService;
    tabState: MockTabStateService;
  } {
    const overlay = new MockWorkspaceOverlayService();
    const router = new MockRouter();
    const viewState = new MockInstancesViewStateService();
    const tabState = new MockTabStateService();
    // Replace default openTabs with the test fixture.
    tabState.openTabs.set([{ id: 'all', name: 'All', type: 'all' }, ...tabs]);
    router.url = initialUrl;
    const app = new TestableApp(overlay, router, viewState, tabState);
    return { app, router, viewState, tabState };
  }

  it('F3: calls setActiveTab when the project tab is already open — URL-scope / polling-scope agree', () => {
    // Normal case: user has the proj-a tab open. Deep-link to a
    // detail URL on proj-a. setActiveTab must be called (not
    // addTab) and activeProjectId must agree with the URL.
    const { viewState, tabState } = makeAppWithTabs(
      '/projects/proj-a/instances/inst-1',
      [{ id: 'proj-a', name: 'Project A', type: 'project' }],
    );

    expect(viewState.openDetailCalls).toEqual([
      { projectId: 'proj-a', instanceId: 'inst-1' },
    ]);
    // URL-scope (proj-a) === polling-scope (activeProjectId() → proj-a)
    expect(tabState.activeProjectId()).toBe('proj-a');
    expect(tabState.setActiveTabCalls).toEqual(['proj-a']);
    expect(tabState.addTabCalls).toEqual([]);
  });

  it('F3: calls addTab when the project tab is NOT open (cold-reload deep-link) — URL-scope / polling-scope agree', () => {
    // Cold-reload case: the user navigates straight to a detail URL
    // for a project whose tab hasn't been opened yet. setActiveTab
    // would silently no-op (the tab isn't in openTabs); the OLD
    // behavior left activeProjectId() === null while the URL was
    // project-scoped → pollingScope() returned undefined while the
    // URL said proj-a. The fix routes this through addTab, which
    // creates the tab AND switches to it.
    const { viewState, tabState } = makeAppWithTabs(
      '/projects/proj-cold/instances/inst-cold',
      [], // no proj-cold tab
    );

    expect(viewState.openDetailCalls).toEqual([
      { projectId: 'proj-cold', instanceId: 'inst-cold' },
    ]);
    // URL-scope (proj-cold) === polling-scope (activeProjectId() → proj-cold)
    expect(tabState.activeProjectId()).toBe('proj-cold');
    expect(tabState.addTabCalls).toEqual([{ project_id: 'proj-cold', name: 'proj-cold' }]);
    expect(tabState.setActiveTabCalls).toEqual([]); // addTab handles the switch
  });

  it('F3: does NOT call setActiveTab or addTab when the URL project already matches activeProjectId (no-op short-circuit)', () => {
    // Regression: the guard ``projectId !== activeProjectId()`` is
    // preserved — no redundant tab-state mutation when the URL and
    // active tab already agree. The polling scope stays in sync
    // without any tab-state calls.
    const { viewState, tabState, router } = makeAppWithTabs(
      '/projects/proj-a/instances/inst-1',
      [{ id: 'proj-a', name: 'Project A', type: 'project' }],
    );
    // After construction: the constructor ran syncDetailVisibility
    // once with the initial URL. activeProjectId is now 'proj-a'.
    // setActiveTab was called exactly once (constructor pass).
    expect(tabState.activeProjectId()).toBe('proj-a');
    const initialSetActiveTabCalls = tabState.setActiveTabCalls.length;
    const initialAddTabCalls = tabState.addTabCalls.length;

    // Drive a second NavigationEnd that re-syncs the SAME URL. With
    // the guard in place, syncDetailVisibility must NOT call
    // setActiveTab or addTab again.
    expect(viewState.openDetailCalls).toEqual([
      { projectId: 'proj-a', instanceId: 'inst-1' },
    ]);
    router.url = '/projects/proj-a/instances/inst-1';
    router.events.next(new NavigationEnd(
      /* id */ 0,
      /* url */ '/projects/proj-a/instances/inst-1',
      /* urlAfterRedirects */ '/projects/proj-a/instances/inst-1',
    ));

    // No-op short-circuit: setActiveTab / addTab NOT called again.
    expect(tabState.setActiveTabCalls.length).toBe(initialSetActiveTabCalls);
    expect(tabState.addTabCalls.length).toBe(initialAddTabCalls);
    // activeProjectId stays in sync.
    expect(tabState.activeProjectId()).toBe('proj-a');
  });
});

// ────────────────────────────────────────────────────────────────────────────
// BUG 3 — lazy root-mount of the chat overlay host.
//
// The static ``<app-chat>`` element in app.html dragged the chat chunk
// into the initial bundle (6.09 MB > 6 MB ``maximumError``). Production
// now mounts the host via ``ViewContainerRef.createComponent`` on the
// FIRST ``detailVisible`` flip (see App.lazyChatMountEffect in app.ts).
// These tests pin the mount decision table through the TestableApp
// mirror above:
//   - nothing mounts while detailVisible stays false (cold boot, or
//     plain routes like / or /instances);
//   - the FIRST detail open mounts exactly once;
//   - later hide/show flips NEVER re-mount (the cache semantics the
//     root-mounted overlay exists for) — they only refresh the
//     ``visible`` input + host display style, and visible→false is
//     applied (no early bail).
// ────────────────────────────────────────────────────────────────────────────
describe('App lazy chat-host mount — BUG 3 (initial bundle budget)', () => {
  it('never mounts while detailVisible stays false (cold boot, non-detail routes)', () => {
    const { app, router } = makeApp({ url: '/' });
    app.runLazyChatMountEffect(true);
    app.runLazyChatMountEffect(true);

    expect(app.mountCalls).toEqual([]);
    expect(app.hostMounted).toBe(false);

    // Navigating between non-detail routes also never mounts.
    router.events.next(new NavigationEnd(1, '/instances', '/instances'));
    app.runLazyChatMountEffect(true);
    expect(app.mountCalls).toEqual([]);
  });

  it('mounts exactly once on the first detail open and only refreshes afterwards', () => {
    const { app, router, viewState } = makeApp({ url: '/' });
    app.runLazyChatMountEffect(true);
    expect(app.mountCalls).toEqual([]);

    // First open — deep link to a detail URL flips detailVisible.
    router.events.next(new NavigationEnd(
      1, '/projects/proj-a/instances/inst-1', '/projects/proj-a/instances/inst-1',
    ));
    expect(viewState.detailVisible()).toBe(true);

    app.runLazyChatMountEffect(true);
    app.runLazyChatMountEffect(true);   // duplicate tick — must NOT re-mount
    expect(app.mountCalls).toEqual(['import+createComponent']);
    expect(app.hostMounted).toBe(true);
    expect(app.hostVisibleState).toBe(true);
    expect(app.hostDisplay).toBe('flex');

    // Hide (navigating away) — no re-mount; display flips to none and
    // the ``visible`` input goes false (the early-bail regression the
    // effect guards against by refreshing BEFORE the not-mounted bail).
    router.events.next(new NavigationEnd(2, '/instances', '/instances'));
    expect(viewState.detailVisible()).toBe(false);
    app.runLazyChatMountEffect(true);
    expect(app.mountCalls).toEqual(['import+createComponent']);
    expect(app.hostVisibleState).toBe(false);
    expect(app.hostDisplay).toBe('none');

    // Re-show — still no re-mount; the SAME host is refreshed.
    router.events.next(new NavigationEnd(
      3, '/projects/proj-a/instances/inst-1', '/projects/proj-a/instances/inst-1',
    ));
    app.runLazyChatMountEffect(true);
    expect(app.mountCalls).toEqual(['import+createComponent']);
    expect(app.hostVisibleState).toBe(true);
    expect(app.hostDisplay).toBe('flex');
  });
});

// ────────────────────────────────────────────────────────────────────────────
// Regression: tabs not remembered on reload (cold-reload deep-link
// CLOBBER bug).
//
// User report: "opened tabs/projects are not remembered when I reload
// the page. Before it was remembered."
//
// Repro: the user has projA, projB, projC tabs open on
// /projects/projA/instances/instA, then reloads. The App constructor
// runs syncDetailVisibility(url). The F3 ``addTab`` branch runs
// because the in-memory openTabs signal is still the default
// [ALL_TAB] (no prior hydrate from localStorage). ``addTab`` triggers
// ``saveState()`` which OVERWRITES the persisted
// [All, projA, projB, projC] with [All, projA] — projB and projC are
// silently lost on the next /instances visit.
//
// Fix: App constructor eagerly calls tabStateService.restoreState()
// BEFORE syncDetailVisibility, so the F3 ``tabExists`` check finds
// the restored tab and falls through to ``setActiveTab('projA')``,
// which writes back the same state (no clobber).
//
// These tests exercise the regression directly: the persisted tab
// state survives the cold-reload boot flow with the fix, and the
// ``MockTabStateService`` mirrors the production ``saveState()``
// side effect so the localStorage writes are visible to the
// assertions.
// ────────────────────────────────────────────────────────────────────────────
describe('App constructor — regression: tabs not remembered on reload (cold-reload deep-link clobber)', () => {
  // Helper: pre-seed localStorage with the saved tabs (simulating a
  // previous session's state) and construct a TestableApp. Mirrors
  // the production App constructor flow including the fix.
  function bootAppWithSavedTabs(opts: {
    url: string;
    savedTabs: Array<{ id: string; name: string; type: 'project' }>;
    activeTabId: string;
  }): {
    app: TestableApp;
    router: MockRouter;
    viewState: MockInstancesViewStateService;
    tabState: MockTabStateService;
  } {
    // Pre-seed localStorage with the saved state from a previous session.
    localStorage.setItem(REGRESSION_STORAGE_KEY, JSON.stringify({
      openTabs: [{ id: 'all', name: 'All', type: 'all' }, ...opts.savedTabs],
      activeTabId: opts.activeTabId,
    }));
    const overlay = new MockWorkspaceOverlayService();
    const router = new MockRouter();
    const viewState = new MockInstancesViewStateService();
    const tabState = new MockTabStateService();
    router.url = opts.url;
    const app = new TestableApp(overlay, router, viewState, tabState);
    return { app, router, viewState, tabState };
  }

  // The fix: the persisted tab state is preserved across the
  // cold-reload deep-link boot flow. The user has projB as active
  // and reloads on /projects/proj-a/instances/inst-1 — the F3
  // branch fires setActiveTab (the tab is in the restored list) and
  // the saved state is preserved.
  it('FIX: persisted tabs survive a cold-reload on /projects/proj-a/instances/inst-1', () => {
    const { tabState } = bootAppWithSavedTabs({
      url: '/projects/proj-a/instances/inst-1',
      savedTabs: [
        { id: 'proj-a', name: 'Project A', type: 'project' },
        { id: 'proj-b', name: 'Project B', type: 'project' },
        { id: 'proj-c', name: 'Project C', type: 'project' },
      ],
      activeTabId: 'proj-b',  // proj-b is active in the previous session
    });

    // The F3 fix falls through to setActiveTab (NOT addTab) because
    // the eager restoreState() hydrated proj-a into openTabs. The
    // activeProjectId mismatch ('proj-a' vs 'proj-b') makes the F3
    // branch take the setActiveTab path — the canary that verifies
    // the saved state was hydrated INSIDE the constructor.
    expect(tabState.addTabCalls).toEqual([]);
    expect(tabState.setActiveTabCalls).toEqual(['proj-a']);

    // The persisted state still has all four tabs after the boot flow.
    const persisted = JSON.parse(localStorage.getItem(REGRESSION_STORAGE_KEY)!);
    const persistedTabIds = persisted.openTabs.map((t: { id: string }) => t.id);
    expect(persistedTabIds).toEqual(['all', 'proj-a', 'proj-b', 'proj-c']);
    // Active tab is now proj-a (it followed the URL).
    expect(persisted.activeTabId).toBe('proj-a');

    // The in-memory signal also has all four tabs.
    expect(tabState.openTabs().map(t => t.id)).toEqual([
      'all', 'proj-a', 'proj-b', 'proj-c',
    ]);
  });

  // The same scenario, but with the URL project already matching the
  // active tab (the F3 fix's no-op short-circuit). The persisted
  // state is preserved as-is.
  it('FIX: cold-reload with active tab already aligned to URL — no-op short-circuit preserves saved state', () => {
    const { tabState } = bootAppWithSavedTabs({
      url: '/projects/proj-a/instances/inst-1',
      savedTabs: [
        { id: 'proj-a', name: 'Project A', type: 'project' },
        { id: 'proj-b', name: 'Project B', type: 'project' },
        { id: 'proj-c', name: 'Project C', type: 'project' },
      ],
      activeTabId: 'proj-a',  // proj-a is already active
    });

    // The F3 guard ``projectId !== activeProjectId()`` short-circuits
    // — no addTab, no setActiveTab. The saved state is preserved
    // untouched.
    expect(tabState.addTabCalls).toEqual([]);
    expect(tabState.setActiveTabCalls).toEqual([]);

    // Persisted state preserved as-is.
    const persisted = JSON.parse(localStorage.getItem(REGRESSION_STORAGE_KEY)!);
    const persistedTabIds = persisted.openTabs.map((t: { id: string }) => t.id);
    expect(persistedTabIds).toEqual(['all', 'proj-a', 'proj-b', 'proj-c']);
    expect(persisted.activeTabId).toBe('proj-a');
  });

  // Negative control: the bug. Without the fix, the F3 addTab
  // clobbers the saved state with [All, proj-a] only. This test
  // demonstrates the bug by skipping the eager restoreState() and
  // verifying the clobber — a regression test that pins the
  // behavior the fix prevents.
  it('CONTROL: without the eager restore, F3 addTab clobbers the saved state', () => {
    // Pre-seed localStorage with projA, projB, projC.
    localStorage.setItem(REGRESSION_STORAGE_KEY, JSON.stringify({
      openTabs: [
        { id: 'all', name: 'All', type: 'all' },
        { id: 'proj-a', name: 'Project A', type: 'project' },
        { id: 'proj-b', name: 'Project B', type: 'project' },
        { id: 'proj-c', name: 'Project C', type: 'project' },
      ],
      activeTabId: 'proj-a',
    }));

    // Manually simulate the BROKEN path (no eager restoreState).
    const tabState = new MockTabStateService();
    // No restoreState() — tabState.openTabs is still [ALL_TAB].
    expect(tabState.openTabs().map(t => t.id)).toEqual(['all']);
    // F3 fires addTab (the bug).
    tabState.addTab({ project_id: 'proj-a', name: 'proj-a' });

    // The saved state IS clobbered. projB and projC are LOST.
    const persisted = JSON.parse(localStorage.getItem(REGRESSION_STORAGE_KEY)!);
    const persistedTabIds = persisted.openTabs.map((t: { id: string }) => t.id);
    expect(persistedTabIds).toEqual(['all', 'proj-a']);  // projB, projC GONE
  });

  // The fix handles the cold-reload deep-link to a NEW project the
  // user has never visited. addTab is still called (the tab is
  // genuinely missing) and adds the new tab alongside the saved
  // tabs.
  it('FIX: cold-reload deep-link to a NEW project appends alongside the saved tabs', () => {
    const { tabState } = bootAppWithSavedTabs({
      url: '/projects/proj-new/instances/inst-new',
      savedTabs: [
        { id: 'proj-b', name: 'Project B', type: 'project' },
      ],
      activeTabId: 'proj-b',
    });

    // F3: tabExists(proj-new) is false → addTab creates the new tab.
    expect(tabState.addTabCalls).toEqual([
      { project_id: 'proj-new', name: 'proj-new' },
    ]);

    // The persisted state preserves projB AND adds proj-new.
    const persisted = JSON.parse(localStorage.getItem(REGRESSION_STORAGE_KEY)!);
    const persistedTabIds = persisted.openTabs.map((t: { id: string }) => t.id);
    expect(persistedTabIds).toEqual(['all', 'proj-b', 'proj-new']);
    expect(persisted.activeTabId).toBe('proj-new');
  });

  // The fix is a no-op when there's no persisted state (cold boot
  // with no prior session). The F3 branch still creates the tab for
  // the URL's project.
  it('FIX: empty saved state — the first addTab still creates the tab', () => {
    localStorage.removeItem(REGRESSION_STORAGE_KEY);
    const overlay = new MockWorkspaceOverlayService();
    const router = new MockRouter();
    const viewState = new MockInstancesViewStateService();
    const tabState = new MockTabStateService();
    router.url = '/projects/proj-a/instances/inst-1';
    const app = new TestableApp(overlay, router, viewState, tabState);

    // restoreState() is a no-op (localStorage empty).
    expect(tabState.restoreStateCalls).toBe(1);
    // F3: tabExists(proj-a) is false → addTab creates the new tab.
    expect(tabState.addTabCalls).toEqual([
      { project_id: 'proj-a', name: 'proj-a' },
    ]);

    const persisted = JSON.parse(localStorage.getItem(REGRESSION_STORAGE_KEY)!);
    expect(persisted.openTabs.map((t: { id: string }) => t.id))
      .toEqual(['all', 'proj-a']);
    expect(persisted.activeTabId).toBe('proj-a');
  });

  // The fix only adds ONE restoreState call: the constructor's
  // eager restore. The NavigationEnd subscriber does not call
  // restoreState again (it's a one-shot boot operation).
  it('FIX: only one restoreState() call per App construction (no double-restore)', () => {
    const { tabState, router } = bootAppWithSavedTabs({
      url: '/projects/proj-a/instances/inst-1',
      savedTabs: [{ id: 'proj-a', name: 'Project A', type: 'project' }],
      activeTabId: 'proj-a',
    });

    // Exactly one restoreState call (the constructor).
    expect(tabState.restoreStateCalls).toBe(1);

    // Drive a NavigationEnd to a different URL — no extra restore.
    router.events.next(new NavigationEnd(
      1, '/instances', '/instances',
    ));
    expect(tabState.restoreStateCalls).toBe(1);
  });

  // Static check: the PRODUCTION App constructor must call
  // ``tabStateService.restoreState()`` BEFORE
  // ``syncDetailVisibility()``. This is the canary that guards
  // against the mirror diverging from the real source — the
  // behavioural tests above use a TestableApp mirror, so a regression
  // that fixes the mirror but breaks the production code (or vice
  // versa) would pass the behavioural tests but fail this static one.
  //
  // The check is intentionally narrow: it only verifies the
  // constructor's call ordering, not the full F3 logic. Anything
  // broader would intersect with the mirror's own assertions.
  it('FIX: production app.ts constructor calls restoreState() BEFORE syncDetailVisibility()', () => {
    const appTsPath = path.resolve(__dirname, 'app.ts');
    const source = fs.readFileSync(appTsPath, 'utf8');

    // Locate the constructor body. The mirror below reduces the
    // search to the first constructor — only one in the file.
    const constructorMatch = source.match(/constructor\s*\(\s*\)\s*\{/);
    expect(constructorMatch).not.toBeNull();
    const constructorStart = constructorMatch!.index! + constructorMatch![0].length;
    const constructorBody = source.slice(constructorStart);

    // Find the two calls. Read the first 4 KB of the body — the
    // App constructor is short enough that this is well over the
    // signal.
    const restoreIdx = constructorBody.indexOf('tabStateService.restoreState()');
    const syncIdx = constructorBody.indexOf('syncDetailVisibility(this.router.url)');

    // Both must be present in the constructor.
    expect(restoreIdx).toBeGreaterThan(-1);
    expect(syncIdx).toBeGreaterThan(-1);

    // restoreState() must come BEFORE syncDetailVisibility — the
    // ordering is the whole point. Without the eager restore, the F3
    // branch fires ``addTab`` on a cold-reload deep-link and
    // ``saveState`` clobbers every other tab the user had open.
    expect(restoreIdx).toBeLessThan(syncIdx);
  });
});
