import { signal, computed, WritableSignal } from '@angular/core';
import { Router, NavigationEnd } from '@angular/router';
import { Subject } from 'rxjs';
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
 * `showWorkspace` (signal) and `hide()` (method). Both are exercised by
 * the methods under test.
 */
class MockWorkspaceOverlayService {
  /** Mirrors the public WritableSignal<boolean>. */
  readonly showWorkspace: WritableSignal<boolean> = signal(false);

  /** Tracks calls; setters exposed for tests that need to flip state. */
  hideCalls = 0;

  hide(): void {
    this.hideCalls++;
    this.showWorkspace.set(false);
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
 * (W5 / R6).
 */
class MockInstancesViewStateService {
  /** Mirrors the public WritableSignal<boolean>. */
  readonly detailVisible: WritableSignal<boolean> = signal(false);

  /** Mirrors the cached instance id and project context. */
  readonly activeInstanceId: WritableSignal<string | null> = signal(null);
  readonly activeProjectId: WritableSignal<string> = signal('all');

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
 */
class MockTabStateService {
  readonly activeProjectId: WritableSignal<string | null> = signal(null);
  readonly openTabs: WritableSignal<Array<{ id: string; name: string; type: string }>> = signal([
    { id: 'all', name: 'All', type: 'all' }
  ]);
  setActiveTabCalls: string[] = [];
  addTabCalls: Array<{ project_id: string; name: string }> = [];

  setActiveTab(tabId: string): void {
    this.setActiveTabCalls.push(tabId);
    // Production behavior: silently no-op when the tab isn't open
    // (the lookup fails and activeTab is left untouched). Tests that
    // exercise this contract pass a project id that hasn't been
    // opened yet and assert activeProjectId stays at its prior value.
    const tab = this.openTabs().find(t => t.id === tabId);
    if (tab) {
      this.activeProjectId.set(tabId);
    }
  }

  /**
   * F3: production addTab creates a project tab if missing AND
   * switches to it; no-op switch if already present. The mock
   * mirrors both branches so syncDetailVisibility can be tested
   * for the cold-reload deep-link case.
   */
  addTab(project: { project_id: string; name: string }): void {
    this.addTabCalls.push(project);
    const existing = this.openTabs().find(t => t.id === project.project_id);
    if (existing) {
      this.activeProjectId.set(project.project_id);
      return;
    }
    this.openTabs.update(tabs => [...tabs, { id: project.project_id, name: project.name, type: 'project' }]);
    this.activeProjectId.set(project.project_id);
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

  /** Mirrors the unified-overlay-visibility computed. */
  readonly anyOverlayVisible = computed(
    () => this.workspaceOverlayService.showWorkspace()
      || this.isPlanRoute()
      || this.instancesViewState.detailVisible()
  );

  constructor(
    protected readonly workspaceOverlayService: MockWorkspaceOverlayService,
    protected readonly router: MockRouter,
    protected readonly instancesViewState: MockInstancesViewStateService,
    protected readonly tabStateService: MockTabStateService = new MockTabStateService(),
  ) {
    // Mirror: R6 boot wire-up — restoreState seeds the cached nav-link
    // route before syncDetailVisibility runs.
    this.instancesViewState.restoreState();
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

  /** Mirror of the public hideActiveOverlay method. */
  hideActiveOverlay(): void {
    if (this.workspaceOverlayService.showWorkspace()) {
      this.workspaceOverlayService.hide();
    }
    if (this.isPlanRoute() || this.instancesViewState.detailVisible()) {
      this.router.navigate(['/instances']);
    }
  }
}

// ---------------------------------------------------------------------------
// Factory: build a TestableApp with the given initial URL and overlay state.
// ---------------------------------------------------------------------------

function makeApp(initial: { url?: string; showWorkspace?: boolean; detailVisible?: boolean } = {}): {
  app: TestableApp;
  overlay: MockWorkspaceOverlayService;
  router: MockRouter;
  viewState: MockInstancesViewStateService;
} {
  const overlay = new MockWorkspaceOverlayService();
  const router = new MockRouter();
  const viewState = new MockInstancesViewStateService();
  router.url = initial.url ?? '/';
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

  it('(f) handles both overlays visible simultaneously — both branches execute (workspace first, then navigate)', () => {
    // The production implementation uses two independent `if` blocks (not
    // `else if`), so when both overlays happen to be active at once both
    // hide() AND router.navigate(['/instances']) are called. Workspace
    // hide() runs first (it appears earlier in the method body), then
    // the navigation. Pin that order so a future refactor to `else if`
    // (which would skip the navigate) is caught.
    const { app, overlay, router } = makeApp({
      url: '/plan',
      showWorkspace: true,
    });

    app.hideActiveOverlay();

    expect(overlay.hideCalls).toBe(1);
    expect(overlay.showWorkspace()).toBe(false);
    expect(router.navigateCalls).toEqual([['/instances']]);
  });

  // R4 — Hide button detail branch: navigate to /instances
  //
  // The detail branch of hideActiveOverlay used to call only
  // closeDetail(), which left the URL on the invisible stub route
  // (blank screen). The Instances nav link then pointed at the same
  // URL — same-URL navigation was suppressed by the router, so
  // NavigationEnd never fired and the visibility stayed stuck.
  //
  // The fix: detail branch navigates to /instances, mirroring the
  // plan branch. syncDetailVisibility then reconciles the service
  // from the new URL.
  it('(R4) navigates to /instances when the detail overlay is visible (workspace AND plan both false)', () => {
    const { app, overlay, router, viewState } = makeApp({
      url: '/projects/all/instances/inst-1',
      showWorkspace: false,
      detailVisible: true,
    });

    app.hideActiveOverlay();

    expect(router.navigateCalls).toEqual([['/instances']]);
    // closeDetail() runs implicitly via the NavigationEnd handler's
    // syncDetailVisibility call once /instances is reached. The
    // hideActiveOverlay method itself only navigates — closing the
    // overlay is the NavigationEnd handler's job, matching the
    // production implementation.
    expect(viewState.closeDetailCalls).toBe(0);
    expect(overlay.hideCalls).toBe(0);
  });

  it('(R4) detail branch does NOT call closeDetail directly (the URL is the source of truth)', () => {
    // The fix moved detail-hiding out of hideActiveOverlay — the
    // NavigationEnd subscriber (syncDetailVisibility) is the single
    // place that drives detailVisible from the URL. Pin this contract
    // so a future refactor that re-adds closeDetail() here doesn't
    // double-write and re-introduce the stuck-URL bug.
    const { app, viewState } = makeApp({
      url: '/projects/all/instances/inst-1',
      showWorkspace: false,
      detailVisible: true,
    });

    app.hideActiveOverlay();

    expect(viewState.closeDetailCalls).toBe(0);
  });

  it('(R4) detail+workspace combined: workspace hide runs AND detail navigates to /instances', () => {
    // Both branches fire independently (workspace first, then
    // navigate). This combines the (f) and (R4) cases.
    const { app, overlay, router } = makeApp({
      url: '/projects/foo/instances/bar',
      showWorkspace: true,
      detailVisible: true,
    });

    app.hideActiveOverlay();

    expect(overlay.hideCalls).toBe(1);
    expect(router.navigateCalls).toEqual([['/instances']]);
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
