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
 *     this.workspaceOverlayService.showWorkspace()
 *       || (this.isWorkspaceRecoverable() && !this.isPlanRoute())
 *   );
 *
 *   hideActiveOverlay(): void {
 *     if (this.workspaceOverlayService.showWorkspace()) {
 *       this.workspaceOverlayService.hide();
 *       return;
 *     }
 *     if (this.showTierActive()) {
 *       const boundProjectId = this.workspaceOverlayService.workspaceProjectId();
 *       if (boundProjectId !== null) {
 *         this.workspaceOverlayService.show(boundProjectId);
 *       }
 *       return;
 *     }
 *     // Defensive no-op.
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

  /**
   * Mirrors the production ``anyOverlayVisible``. Round 4 (D1): the
   * workspace editor toggle ONLY, with the additional /plan guard
   * that drops the workspace-recoverable term on /plan (a recoverable
   * click on /plan would re-show the workspace UNDER the plane iframe
   * z-1000 — a dead click). The chat terms (detailVisible,
   * isHiddenButRecoverable) and the old "navigate to /instances on
   * /plan" affordance are gone from the button's contract. The
   * workspace editor is the only driver:
   *
   *   - showWorkspace() → button rendered (always)
   *   - isWorkspaceRecoverable() && !isPlanRoute() → button rendered
   *   - isWorkspaceRecoverable() && isPlanRoute() → button ABSENT (D1)
   *   - !isWorkspaceRecoverable() → button ABSENT (always)
   *
   * ``isHiddenButRecoverable`` is kept as a standalone computed for
   * the Instances nav-link dead-click guard (see
   * ``onInstancesNavClick``), but it does NOT feed the button.
   */
  readonly anyOverlayVisible = computed(
    () => this.workspaceOverlayService.showWorkspace()
      || (this.isWorkspaceRecoverable() && !this.isPlanRoute())
  );

  /** Mirrors ``isHiddenButRecoverable``: chat is hidden but cached
   *  id is set and the URL is on an instances route. Drives the
   *  Instances nav-link dead-click guard. Round 3: NO LONGER feeds
   *  the header button (the button is the workspace editor toggle
   *  only). */
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
   *  show-tier gate is the workspace tier gated on ``!isPlanRoute()``
   *  (B1 /plan dead-click guard). Both the icon / aria-label and
   *  the handler's branch-2 mirror read this single computed so
   *  they can never drift from each other (or from the production
   *  computed). */
  readonly showTierActive = computed(
    () => this.isWorkspaceRecoverable() && !this.isPlanRoute()
  );

  /** Hide-button icon — mirrors production precedence rule (Round 3
   *  two-state workspace gate):
   *    - workspace visible → visibility_off (HIDE);
   *    - workspace recoverable → visibility (SHOW);
   *    - default → visibility_off (button hidden by anyOverlayVisible
   *      anyway). */
  readonly hideOverlayIcon = computed(() => {
    const workspaceVisible = this.workspaceOverlayService.showWorkspace();
    const showTier = this.showTierActive();
    if (workspaceVisible) return 'visibility_off';
    if (showTier) return 'visibility';
    return 'visibility_off';
  });

  /** Hide-button aria-label — mirrors the icon flip. Round 3 labels
   *  are "Hide editor" / "Show editor" (the button is the workspace
   *  editor toggle only). */
  readonly hideOverlayAriaLabel = computed(() => {
    const workspaceVisible = this.workspaceOverlayService.showWorkspace();
    const showTier = this.showTierActive();
    if (workspaceVisible) return 'Hide editor';
    if (showTier) return 'Show editor';
    return 'Hide editor';
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
   * Mirror of the public hideActiveOverlay method. Round 4: the
   * header button is the WORKSPACE EDITOR toggle ONLY. Two branches
   * (Round 4 dropped the previous branch-3 plan-navigate path):
   *
   * 1. Workspace visible → ``workspaceOverlayService.hide()`` and
   *    early-return. workspaceProjectId is preserved so the button
   *    stays rendered (recoverable state arms).
   *
   * 2. Workspace recoverable + !isPlanRoute → re-show the workspace
   *    via ``workspaceOverlayService.show(workspaceProjectId())``.
   *    The guard collapses to ``showTierActive()`` — reachability
   *    guarantees the predicates by the time the click reaches the
   *    handler.
   *
   * Anything else is a defensive no-op — the button is hidden via
   * anyOverlayVisible, so the click is unreachable in practice
   * (Round 4 D1: on /plan + workspace hidden-but-bound, the button
   * is ABSENT — no need for a plan-navigate branch).
   */
  hideActiveOverlay(): void {
    if (this.workspaceOverlayService.showWorkspace()) {
      this.workspaceOverlayService.hide();
      return;
    }
    // Workspace-recoverable + !isPlanRoute → re-show the workspace
    // via the same button. ``showTierActive()`` is the single
    // workspace-only gate (replaces the previous triple-check).
    if (this.showTierActive()) {
      const boundProjectId = this.workspaceOverlayService.workspaceProjectId();
      if (boundProjectId !== null) {
        this.workspaceOverlayService.show(boundProjectId);
      }
      return;
    }
    // Defensive no-op — see docblock.
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
  // Round 3 contract: the button is the workspace editor toggle ONLY.
  // The previously-tested chat / plan / detail terms are gone from
  // the visibility predicate. The tests below pin the new contract:
  //   - workspace visible → button rendered
  //   - workspace hidden but projectId bound → button rendered
  //   - workspace not present (cold boot, /plan, chat-only) → button
  //     NOT rendered, regardless of cached chat id

  it('(a) returns false when the workspace is not visible and no projectId is bound', () => {
    const { app } = makeApp({ url: '/', showWorkspace: false });
    expect(app.anyOverlayVisible()).toBe(false);
  });

  it('(b) returns true when workspace overlay showWorkspace() is true', () => {
    const { app } = makeApp({ url: '/', showWorkspace: true });
    expect(app.anyOverlayVisible()).toBe(true);
  });

  // Round 3: the /plan route no longer drives the button. The plane
  // iframe has its own dismiss surface; the button is only rendered
  // when the workspace editor has presence.
  it('(c) returns false when on /plan with the workspace editor hidden and unbound', () => {
    const { app } = makeApp({ url: '/plan', showWorkspace: false });
    expect(app.anyOverlayVisible()).toBe(false);
  });

  // Round 3: the chat detail visible state no longer drives the
  // button. The button is hidden when the chat is visible alone (no
  // workspace presence) — the user-reported bug repro is exactly this
  // case (chat-visible + workspace-never-opened → button still
  // rendered with HIDE affordance was the bug).
  it('(g) returns false when chat detail is visible but the workspace editor is hidden and unbound', () => {
    const { app } = makeApp({
      url: '/projects/proj-1/instances/inst-1',
      showWorkspace: false,
      detailVisible: true,
    });
    expect(app.anyOverlayVisible()).toBe(false);
  });

  it('(h) returns false when both chat detail and workspace are hidden (no recoverable state)', () => {
    const { app } = makeApp({
      url: '/',
      showWorkspace: false,
      detailVisible: false,
    });
    expect(app.anyOverlayVisible()).toBe(false);
  });

  // Round 3: the chat cached-id branch was REMOVED. The button does
  // NOT stay visible after hiding the chat overlay — the configured
  // mechanism for re-showing the chat is the Instances nav-link's
  // dead-click guard (onInstancesNavClick), not the header button.
  // The button is rendered ONLY when the workspace is visible or
  // recoverable.
  it('(R7) returns false when chat is hidden-but-recoverable but the workspace editor is hidden and unbound', () => {
    // Boot on a detail URL — the constructor's syncDetailVisibility
    // seeds the cached id via openDetail; the user hides the chat
    // via the (removed) button path. The button is NOT rendered — the
    // Instances nav-link dead-click guard is the chat re-show path.
    const { app, viewState } = makeApp({
      url: '/projects/all/instances/inst-1',
      showWorkspace: false,
    });
    expect(viewState.activeInstanceId()).toBe('inst-1');
    // Simulate the chat being hidden (the production hide path used
    // to be hideActiveOverlay chat branch; with Round 3 the chat is
    // hidden by navigating away — here we just toggle the signal so
    // the test is hermetic).
    viewState.detailVisible.set(false);
    expect(viewState.activeInstanceId()).toBe('inst-1'); // preserved
    // The hide button is NOT rendered — the chat-cached-id branch is
    // gone from anyOverlayVisible.
    expect(app.anyOverlayVisible()).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// 3) hideActiveOverlay — three behaviour tests.
// ---------------------------------------------------------------------------

describe('App.hideActiveOverlay', () => {
  // Round 3 contract: the handler is the workspace editor toggle ONLY.
  // Chat hide/re-show branches were removed (chat toggling moved to
  // the Instances nav-link dead-click guard). The tests below pin:
  //   - branch 1: workspace visible → hide(), no navigation
  //   - branch 2: workspace recoverable + !isPlanRoute → re-show,
  //     binding the SAME projectId
  //   - branch 3: /plan → navigate to /instances (the dead-click
  //     guard for the rare case where the workspace is visible on
  //     /plan — branch 1 hides the workspace first, then the user
  //     can recover it via the workspace button on the project tab)
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

  it('(e) on /plan with workspace hidden-but-bound → click is a defensive no-op (D1: button is absent)', () => {
    // Round 4 (D1): ``anyOverlayVisible`` drops the recoverable
    // term on /plan, so the button is NOT rendered in this state.
    // The handler is still reachable in theory (e.g. a stale click
    // or a race) and must be a defensive no-op — no navigate, no
    // show, no hide. The workspace stays hidden-but-recoverable
    // (the bound projectId survives) so the affordance picks back
    // up when the user leaves /plan.
    const { app, overlay, router } = makeApp({
      url: '/plan',
      workspaceProjectId: 'proj-a',
      // showWorkspace deliberately omitted — hidden but bound.
    });

    // The button is NOT rendered for this state (D1 — see
    // anyOverlayVisible docblock).
    expect(app.anyOverlayVisible()).toBe(false);

    app.hideActiveOverlay();

    // No navigation (branch 3 was removed in Round 4).
    expect(router.navigateCalls).toEqual([]);
    // The workspace is NOT re-shown — that would render under the
    // plane iframe (a dead click), which is exactly what D1
    // prevents by removing the affordance.
    expect(overlay.showWorkspace()).toBe(false);
    expect(overlay.showCalls).toEqual([]);
    expect(overlay.hideCalls).toBe(0);
    // Bound id is preserved — the affordance picks back up on /sources.
    expect(overlay.workspaceProjectId()).toBe('proj-a');
  });

  it('(f) on /plan with workspace visible → workspace hides first, no navigation (early return)', () => {
    // Production ``hideActiveOverlay`` early-returns after the
    // workspace hide branch (workspace visible wins). On /plan
    // with the workspace visible, the click hides the editor —
    // no navigate (Round 4 removed branch 3).
    const { app, overlay, router } = makeApp({
      url: '/plan',
      showWorkspace: true,
    });

    app.hideActiveOverlay();

    expect(overlay.hideCalls).toBe(1);
    expect(overlay.showWorkspace()).toBe(false);
    // No navigation — the early return suppressed it. After this
    // click the user is still on /plan with the workspace
    // hidden-but-unbound; the button is now absent (no further
    // click target until the user re-opens the editor via the
    // project tab).
    expect(router.navigateCalls).toEqual([]);
  });

  // Round 3: workspace-recoverable branch re-shows the editor at
  // the same projectId. The chat is no longer part of the
  // contract.
  it('(R4) workspace recoverable + !plan → click re-shows editor at the SAME projectId', () => {
    const { app, overlay } = makeApp({
      url: '/',
      workspaceProjectId: 'proj-a',
      // showWorkspace deliberately omitted — hidden but bound.
    });
    expect(overlay.showWorkspace()).toBe(false);
    expect(overlay.workspaceProjectId()).toBe('proj-a');
    expect(app.isWorkspaceRecoverable()).toBe(true);

    app.hideActiveOverlay();

    // Workspace re-shows with the SAME projectId.
    expect(overlay.showWorkspace()).toBe(true);
    expect(overlay.workspaceProjectId()).toBe('proj-a');
    // hide() was NOT called (the click is a re-show, not a hide).
    expect(overlay.hideCalls).toBe(0);
    expect(overlay.showCalls).toEqual([{ projectId: 'proj-a' }]);
  });

  it('(R4) workspace recoverable + plan → click is a defensive no-op (D1: button is absent)', () => {
    // Round 4 (D1): the previous B1 lane (navigate-to-/instances
    // on /plan with the workspace recoverable) is now enforced by
    // ABSENCE — the button is NOT rendered in this state. If the
    // handler is invoked anyway (stale click, race), it is a
    // defensive no-op: no navigate, no show, no hide. The bound
    // id is preserved so the affordance picks back up once the
    // user leaves /plan.
    const { app, overlay, router } = makeApp({
      url: '/plan',
      workspaceProjectId: 'proj-a',
    });

    expect(app.anyOverlayVisible()).toBe(false); // D1 absence
    expect(app.isWorkspaceRecoverable()).toBe(true);

    app.hideActiveOverlay();

    // No navigation — the previous branch 3 was removed in Round 4.
    expect(router.navigateCalls).toEqual([]);
    // The workspace was NOT re-shown (would be invisible under the
    // iframe) and not hidden either (it was already hidden).
    expect(overlay.showWorkspace()).toBe(false);
    expect(overlay.hideCalls).toBe(0);
    expect(overlay.showCalls).toEqual([]);
    expect(overlay.workspaceProjectId()).toBe('proj-a'); // preserved
  });

  it('no workspace presence + no overlay visible = hide button is a defensive no-op', () => {
    // The button is hidden via anyOverlayVisible when neither the
    // workspace is visible nor recoverable. The handler is still
    // callable (e.g. a race) and is a defensive no-op in that case.
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

  // Round 3: chat hide/re-show via the button is gone. The user
  // re-shows the chat via the Instances nav-link's dead-click
  // guard (onInstancesNavClick). The chat hide route is the URL —
  // navigating away from the detail URL closes the chat via
  // syncDetailVisibility. This test pins that the chat cached id
  // survives an UNRELATED button click (workspace-recoverable case)
  // — the new contract.
  it('(R7) chat cached id survives a workspace-recoverable button click (no chat state mutation)', () => {
    const { app, overlay, viewState } = makeApp({
      url: '/projects/all/instances/inst-1',
      workspaceProjectId: 'proj-a',
      // showWorkspace deliberately omitted — hidden but bound.
    });
    expect(viewState.activeInstanceId()).toBe('inst-1');
    expect(viewState.detailVisible()).toBe(true);

    // Click — workspace re-shows; chat state is untouched.
    app.hideActiveOverlay();
    expect(overlay.showWorkspace()).toBe(true);
    expect(viewState.activeInstanceId()).toBe('inst-1'); // preserved
    expect(viewState.activeProjectId()).toBe('all');    // preserved
    expect(viewState.detailVisible()).toBe(true);       // preserved
    expect(viewState.lastDetailRoute()).toEqual([
      '/projects', 'all', 'instances', 'inst-1',
    ]);
  });
});

// ---------------------------------------------------------------------------
// Round 3: anyOverlayVisible is the workspace editor toggle ONLY — the
// chat / plan terms are gone. The chat-cached-id predicate
// (isHiddenButRecoverable) is RETAINED as a standalone computed for
// the Instances nav-link dead-click guard (onInstancesNavClick), but
// it no longer drives the header button. The N2 isInstancesRoute gate
// that previously protected the chat-cached-id branch is now
// obsolete for the button — but the gate itself is preserved for
// the nav-link dead-click guard (see onInstancesNavClick tests
// below). These tests pin the new contract: anyOverlayVisible is
// driven by the workspace state alone.
// ---------------------------------------------------------------------------
describe('App.anyOverlayVisible — workspace-only contract', () => {
  it('(Workspace-only) cached chat id + non-instances route → anyOverlayVisible is false', () => {
    // Round 3: the chat-cached-id branch is gone from
    // anyOverlayVisible. The cached id is irrelevant to the button
    // visibility — the workspace is the only driver.
    const { app, viewState } = makeApp({ url: '/sources', showWorkspace: false });
    viewState.activeInstanceId.set('inst-1');
    expect(viewState.detailVisible()).toBe(false);

    // The hide button is NOT rendered — no workspace presence.
    expect(app.anyOverlayVisible()).toBe(false);
    // isHiddenButRecoverable is preserved (false here because the
    // URL is not an instances route — N2 gate still applies for the
    // nav-link dead-click guard, which is a separate surface).
    expect(app.isHiddenButRecoverable()).toBe(false);
  });

  it('(Workspace-only) cached chat id + instances route + hidden → anyOverlayVisible is false', () => {
    // Round 3: even when the chat is recoverable (N1 dead-click
    // guard would fire), the header button is NOT rendered. The
    // chat-cached-id gated branch is gone.
    const { app, viewState } = makeApp({
      url: '/projects/all/instances/inst-1',
      showWorkspace: false,
    });
    viewState.detailVisible.set(false);
    expect(viewState.activeInstanceId()).toBe('inst-1');
    expect(app.isInstancesRoute()).toBe(true);
    // isHiddenButRecoverable is still true (the nav-link dead-click
    // guard would fire) — but the button is NOT rendered.
    expect(app.isHiddenButRecoverable()).toBe(true);
    expect(app.anyOverlayVisible()).toBe(false);
  });

  it('(Workspace-only) workspace hidden but projectId bound → anyOverlayVisible is true', () => {
    const { app } = makeApp({
      url: '/',
      workspaceProjectId: 'proj-a',
    });
    expect(app.isWorkspaceRecoverable()).toBe(true);
    expect(app.anyOverlayVisible()).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// The Round 3 hide-button affordance describe block (visibility_off /
// "Hide editor" + visibility / "Show editor") was merged into the
// canonical (a)/(b)/(B1)/(W3) matrix block below in Round 4. The
// (a) and (b) cases are pinned there verbatim; this block was
// redundant. See ``App hide-button affordance — workspace-recoverable
// icon/label flip`` for the canonical matrix.
// ---------------------------------------------------------------------------

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
    // Set up the recoverable state: boot on a detail URL — the
    // constructor's syncDetailVisibility seeds the cached id via
    // openDetail. Round 3: the chat hide is no longer a button
    // action (the button is the workspace editor toggle only). The
    // test puts the chat in hidden-but-recoverable state by
    // toggling the signal directly (the production chat-hide path
    // is navigation away from the detail URL; not under test here).
    const { app, router, viewState } = makeApp({
      url: '/projects/all/instances/inst-1',
      showWorkspace: false,
    });
    viewState.detailVisible.set(false);
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
    // simulate the chat-hide (Round 3: chat hide is via URL nav,
    // not the button — toggle the signal directly).
    const { app, viewState } = makeApp({
      url: '/projects/all/instances/inst-1',
      showWorkspace: false,
    });
    viewState.detailVisible.set(false);
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
    viewState.detailVisible.set(false);
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
// Round 3 — the N3 combined test pattern (workspace visible + chat
// hidden-but-recoverable combined flow) no longer applies. The chat
// branches were removed from the handler. The button is the workspace
// editor toggle ONLY — when the workspace is visible, the click hides
// it via branch 1 and returns; the chat state is untouched (the
// user's chat re-show is the Instances nav-link dead-click guard).
// ---------------------------------------------------------------------------
describe('App.hideActiveOverlay — workspace-only flow', () => {
  it('workspace visible + chat visible (no recoverable state) → click hides workspace, chat state untouched', () => {
    // Both the workspace and the chat are visible. The button
    // click hides the workspace (branch 1, early return). The chat
    // state is NOT touched by the button — the user re-shows the
    // chat via the Instances nav-link dead-click guard.
    const { app, overlay, router, viewState } = makeApp({
      url: '/projects/all/instances/inst-1',
      showWorkspace: true,
    });
    expect(viewState.detailVisible()).toBe(true);
    expect(viewState.activeInstanceId()).toBe('inst-1');

    app.hideActiveOverlay();

    expect(overlay.hideCalls).toBe(1);
    expect(overlay.showWorkspace()).toBe(false);
    expect(router.navigateCalls).toEqual([]);
    // Chat state is untouched — the button does not flip chat
    // visibility. The Instances nav-link dead-click guard is the
    // chat re-show path.
    expect(viewState.detailVisible()).toBe(true);
    expect(viewState.activeInstanceId()).toBe('inst-1');
  });
});

// ---------------------------------------------------------------------------
// Workspace-recoverable — the editor is hidden but still bound to a
// project. The header hide button must flip to UNHIDE affordance
// (visibility / "Show editor") and a click re-shows the editor at
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
  // Round 3: the button is the workspace editor toggle ONLY. The
  // previous merit badges regarding the chat-recoverable state
  // (formerly the chat-wins precedence) are gone — the chat
  // toggling surface is owned by the Instances nav-link dead-click
  // guard, not the header button. The tests below pin the two-state
  // workspace contract: visible → HIDE, recoverable → SHOW.

  it('(a) workspace visible → icon visibility_off / "Hide editor", click hides workspace (no chat mutation)', () => {
    const { app, overlay, router, viewState } = makeApp({
      url: '/projects/all/instances/inst-1',
      workspaceProjectId: 'proj-a',
      showWorkspace: true,
    });
    // The chat is also visible (boot on a detail URL); the button
    // cares only about the workspace state.
    expect(viewState.detailVisible()).toBe(true);
    expect(app.isWorkspaceRecoverable()).toBe(false); // workspace is visible

    // Icon MUST be visibility_off ("Hide editor").
    expect(app.hideOverlayIcon()).toBe('visibility_off');
    expect(app.hideOverlayAriaLabel()).toBe('Hide editor');

    // Click — workspace hides. Chat state is untouched
    // (Round 3: the button does not touch chat state).
    app.hideActiveOverlay();
    expect(overlay.hideCalls).toBe(1);
    expect(overlay.showWorkspace()).toBe(false);
    expect(viewState.detailVisible()).toBe(true); // untouched
    expect(router.navigateCalls).toEqual([]);
  });

  it('(b) workspace hidden + projectId bound → icon visibility / "Show editor", click re-shows SAME projectId', () => {
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
    expect(app.hideOverlayAriaLabel()).toBe('Show editor');

    // Click — the editor re-shows with the SAME projectId.
    app.hideActiveOverlay();
    expect(overlay.showWorkspace()).toBe(true);
    expect(overlay.workspaceProjectId()).toBe('proj-a'); // SAME id
    expect(overlay.hideCalls).toBe(0);
    expect(overlay.showCalls).toEqual([{ projectId: 'proj-a' }]);
  });

  it('(d) anyOverlayVisible true while workspace recoverable, false when never opened (projectId null)', () => {
    const recoverable = makeApp({
      url: '/',
      workspaceProjectId: 'proj-a',
    });
    expect(recoverable.app.isWorkspaceRecoverable()).toBe(true);
    expect(recoverable.app.anyOverlayVisible()).toBe(true);

    const coldBoot = makeApp({ url: '/' });
    expect(coldBoot.overlay.workspaceProjectId()).toBeNull();
    expect(coldBoot.app.isWorkspaceRecoverable()).toBe(false);
    expect(coldBoot.app.anyOverlayVisible()).toBe(false);
  });

  it('(B1) /plan + workspace hidden-but-bound → button ABSENT (D1), click is unreachable no-op', () => {
    // Round 4 (D1): on /plan with the workspace hidden-but-bound,
    // ``anyOverlayVisible`` drops the recoverable term — the button
    // is NOT rendered. The previous Round 3 behavior rendered the
    // button with a "Hide editor" affordance and routed the click
    // to a "navigate to /instances" branch (B1: the plane iframe
    // z-1000 covers the workspace z-100, so a re-show would be a
    // dead click). Round 4 enforcement moved from "button + dead
    // branch" to "absence": the affordance simply isn't there, and
    // the dead-click path is structurally impossible.
    const { app, overlay, router } = makeApp({
      url: '/plan',
      workspaceProjectId: 'proj-a',
      // showWorkspace deliberately omitted — hidden but bound.
    });
    expect(app.isPlanRoute()).toBe(true);
    expect(app.isWorkspaceRecoverable()).toBe(true);
    expect(overlay.showWorkspace()).toBe(false);

    // The button is ABSENT — the D1 guard.
    expect(app.anyOverlayVisible()).toBe(false);
    expect(app.showTierActive()).toBe(false);
    // No state lost: the workspace stays recoverable — the
    // affordance picks back up once the user leaves /plan.
    expect(app.isWorkspaceRecoverable()).toBe(true);
    expect(overlay.workspaceProjectId()).toBe('proj-a');

    // If the handler is invoked anyway (stale click, race), it is
    // a defensive no-op: no navigate, no show, no hide. The bound
    // id survives.
    app.hideActiveOverlay();
    expect(router.navigateCalls).toEqual([]);
    expect(overlay.showWorkspace()).toBe(false);
    expect(overlay.showCalls).toEqual([]);
    expect(overlay.hideCalls).toBe(0);
    expect(overlay.workspaceProjectId()).toBe('proj-a');
  });

  it('(B1+) /plan + workspace visible → button rendered (workspace tier wins), click hides editor', () => {
    // The visible case on /plan: anyOverlayVisible is true (the
    // visible term always wins), so the button is rendered. Click
    // takes branch 1 and hides the editor — same as on any other
    // route. No navigate (Round 4 dropped branch 3).
    const { app, overlay, router } = makeApp({
      url: '/plan',
      showWorkspace: true,
    });

    expect(app.anyOverlayVisible()).toBe(true);
    expect(app.isPlanRoute()).toBe(true);

    app.hideActiveOverlay();
    expect(overlay.hideCalls).toBe(1);
    expect(overlay.showWorkspace()).toBe(false);
    expect(router.navigateCalls).toEqual([]);
  });

  // Round 3: the chat-visible + workspace-recoverable combined
  // state is no longer special. The button is the workspace toggle
  // ONLY — when the workspace is recoverable, the icon is "Show
  // editor" regardless of whether the chat is visible. The user
  // surfaces the chat via the nav-link dead-click guard, not the
  // header button.
  it('(W3) chat visible + workspace recoverable → icon visibility / "Show editor", click re-shows workspace (chat state untouched)', () => {
    const { app, overlay, viewState } = makeApp({
      url: '/projects/all/instances/inst-1',
      workspaceProjectId: 'proj-a',
      detailVisible: true,
      // showWorkspace deliberately omitted — hidden but bound.
    });
    expect(viewState.detailVisible()).toBe(true);
    expect(app.isWorkspaceRecoverable()).toBe(true);

    // The show-tier is closed by the workspace-recoverable branch
    // (the chat is no longer a competing signal in the icon flip).
    expect(app.hideOverlayIcon()).toBe('visibility');
    expect(app.hideOverlayAriaLabel()).toBe('Show editor');

    // Click — the workspace-recoverable branch fires: editor
    // re-shows at the same project. Chat state is untouched.
    app.hideActiveOverlay();
    expect(overlay.showWorkspace()).toBe(true);
    expect(overlay.showCalls).toEqual([{ projectId: 'proj-a' }]);
    expect(viewState.detailVisible()).toBe(true); // untouched
  });

  // Round 4 (D1): chat-recoverable + workspace unbound → button
  // ABSENT. The cached chat id is irrelevant to the button (chat
  // toggling is URL-driven; the Instances nav-link dead-click
  // guard is the chat re-show path). This is the canonical pin
  // that the chat-recoverable state never feeds the button's
  // visibility predicate.
  it('(W4) chat-recoverable + workspace unbound → button is hidden (anyOverlayVisible false)', () => {
    const { app, viewState } = makeApp({
      url: '/projects/all/instances/inst-1',
      showWorkspace: false,
    });
    // Simulate the chat being hidden (Round 4: production hides via
    // URL nav; the test toggles the signal directly for hermeticity).
    viewState.detailVisible.set(false);
    expect(viewState.activeInstanceId()).toBe('inst-1');
    expect(app.isHiddenButRecoverable()).toBe(true);
    // The button is NOT rendered — the chat-recoverable state is
    // not part of the button contract.
    expect(app.anyOverlayVisible()).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// URL → chat-hide: the chat overlay visibility is URL-driven (Round
// 4 contract). When the URL leaves the detail pattern,
// ``syncDetailVisibility`` calls ``closeDetail`` on the view-state
// service. The header button does NOT participate in chat hide
// (Round 3 removed those branches); this pin prevents a future
// refactor from silently dropping the URL→service write path.
// ---------------------------------------------------------------------------
describe('App URL→chat-hide — closeDetail fires when URL leaves detail route', () => {
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

  it('(URLH1) boot on a detail URL → closeDetail NOT called, openDetail called with project+instance', () => {
    // Sanity baseline: a deep-link to a detail URL opens the chat
    // and does NOT call closeDetail.
    const { viewState } = makeAppWithNav('/projects/proj-a/instances/inst-1');
    expect(viewState.openDetailCalls).toEqual([
      { projectId: 'proj-a', instanceId: 'inst-1' },
    ]);
    expect(viewState.closeDetailCalls).toBe(0);
    expect(viewState.detailVisible()).toBe(true);
  });

  it('(URLH1) NavigationEnd away from the detail URL → closeDetail fires (chat hides)', () => {
    // The chat-hide path is the URL — when the user navigates
    // away from a detail URL (e.g. via the Jobs nav link), the
    // NavigationEnd subscriber in the App constructor calls
    // ``syncDetailVisibility(urlAfterRedirects)`` which fires
    // ``closeDetail``. The chat overlay's display binding reads
    // ``detailVisible()`` and flips to none.
    const { router, viewState } = makeAppWithNav('/projects/proj-a/instances/inst-1');
    expect(viewState.detailVisible()).toBe(true);
    const beforeCloseCount = viewState.closeDetailCalls;

    // Drive a NavigationEnd to a non-detail URL.
    router.events.next(new NavigationEnd(
      1, '/jobs', '/jobs',
    ));

    // closeDetail fired exactly once — the URL→service write.
    expect(viewState.closeDetailCalls).toBe(beforeCloseCount + 1);
    expect(viewState.detailVisible()).toBe(false);
  });

  it('(URLH1) NavigationEnd back to the detail URL → openDetail fires (chat re-shows)', () => {
    // Round 4: the round-trip is symmetric. Leave detail URL →
    // closeDetail fires; return to the same detail URL →
    // openDetail fires again. The cached id survives the trip
    // (it's written to localStorage on every openDetail), so the
    // chat re-shows with the SAME instance.
    const { router, viewState } = makeAppWithNav('/projects/proj-a/instances/inst-1');
    expect(viewState.openDetailCalls.length).toBe(1);

    // Leave.
    router.events.next(new NavigationEnd(1, '/jobs', '/jobs'));
    expect(viewState.detailVisible()).toBe(false);

    // Return to the same detail URL.
    router.events.next(new NavigationEnd(
      2,
      '/projects/proj-a/instances/inst-1',
      '/projects/proj-a/instances/inst-1',
    ));

    // openDetail fired again — the round-trip re-opens the chat.
    expect(viewState.openDetailCalls.length).toBe(2);
    expect(viewState.detailVisible()).toBe(true);
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
