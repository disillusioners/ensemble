import { signal, computed, effect, Component, Inject, inject } from '@angular/core';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import type { Agent, InstanceInfo } from '../../models';
import { ProjectTab } from '../../models/tab.model';

const NEXT_AGENT_STORAGE_KEY = 'ensemble-next-instance-agent';

// Mock TabStateService — mirrors production semantics: `activeProjectId`
// is a computed that returns null when the active tab is the 'all'
// pseudo-project, matching the real TabStateService. `setActiveTab(tabId)`
// looks up the tab in openTabs and falls back to constructing a
// project-type tab on the fly so tests that switch to non-existent
// project ids still drive the computed.
class MockTabStateService {
  readonly openTabs = signal<ProjectTab[]>([
    { id: 'all', name: 'All', type: 'all' }
  ]);
  readonly activeTab = signal<ProjectTab>({ id: 'all', name: 'All', type: 'all' });

  readonly activeProjectId = computed(() => {
    const tab = this.activeTab();
    return tab.type === 'project' ? tab.id : null;
  });

  setActiveTabCalls: string[] = [];
  setActiveTab(tabId: string): void {
    this.setActiveTabCalls.push(tabId);
    const tab = this.openTabs().find((t) => t.id === tabId);
    if (tab) {
      this.activeTab.set(tab);
      return;
    }
    // Tests may switch to a project id that hasn't been opened as a tab.
    // Production would silently no-op, but the test mock synthesizes a
    // project-type tab so the computed `activeProjectId` reflects the
    // simulated switch.
    this.activeTab.set({ id: tabId, name: tabId, type: 'project' });
  }
}

// Mock ApiService for testing
const mockApiService = {
  listAgents: jest.fn(),
  getInstance: jest.fn(),
  getMessages: jest.fn(),
  sendMessage: jest.fn(),
  deleteInstance: jest.fn(),
  pauseInstance: jest.fn(),
  resumeInstance: jest.fn(),
  setWatchover: jest.fn(),
  createInstance: jest.fn(),
};

// Mock SseService for testing
const mockSseService = {
  messages: signal<any[]>([]),
  events: signal<any[]>([]),
  statusChange: signal<{ instance_id: string; status: string } | null>(null),
  isStreaming: signal(false),
  latestError: signal<any>(null),
  connect: jest.fn(),
  disconnect: jest.fn(),
  clearEvents: jest.fn(),
};

// Mock MatSnackBar for testing
const mockSnackBar = {
  open: jest.fn(),
};

interface TestMessagePayload {
  content: string;
  images?: string[];
}

// Mock WorkspaceOverlayService — mirrors production semantics:
// `showWorkspace` and `workspaceProjectId` are independent
// WritableSignals, and the toggle/hide/show methods mutate them with
// the same contract as the real service. Tests construct one of these
// and pass it to the testable component (or provide it via TestBed),
// so they observe exactly the same state transitions production would.
class MockWorkspaceOverlayService {
  readonly showWorkspace = signal(false);
  readonly workspaceProjectId = signal<string | null>(null);

  toggle(projectId?: string): void {
    const currentId = this.workspaceProjectId();
    const targetId = projectId ?? currentId;
    if (targetId === null) return;

    if (this.showWorkspace() && currentId === targetId) {
      this.showWorkspace.set(false);
      return;
    }
    this.workspaceProjectId.set(targetId);
    this.showWorkspace.set(true);
  }

  hide(): void {
    this.showWorkspace.set(false);
  }

  show(projectId: string): void {
    this.workspaceProjectId.set(projectId);
    this.showWorkspace.set(true);
  }
}

// Testable ChatComponent (mirrors actual component logic)
class TestableChatComponent {
  private readonly api = mockApiService;
  private readonly sseService = mockSseService;
  private readonly snackBar = mockSnackBar;
  protected readonly tabStateService: MockTabStateService;
  /**
   * Singleton state holder for the workspace overlay. Mirrors the
   * production injection: in production ChatComponent injects
   * `WorkspaceOverlayService` (providedIn: 'root'); here we wire a
   * `MockWorkspaceOverlayService` so tests can observe the same
   * state that the (real) App root would see.
   */
  readonly workspaceOverlayService: MockWorkspaceOverlayService;

  readonly currentInstanceId = signal<string | null>(null);
  readonly currentInstance = signal<InstanceInfo | null>(null);
  readonly selectedAgent = signal<Agent | null>(null);
  readonly isSending = signal(false);
  readonly sendError = signal<string | null>(null);
  readonly watchoverEnabled = signal(false);
  readonly watchoverContext = signal<string | null>(null);
  readonly watchoverDenialCount = signal(0);
  readonly watchoverPending = signal(false);
  private readonly processedWatchoverDenials = new Set<string>();

  /** Mirrors ChatComponent.SEND_COOLDOWN_MS */
  private readonly SEND_COOLDOWN_MS = 3000;
  private lastSendTime = 0;

  messageInputRef: { clearInput: jest.Mock } = { clearInput: jest.fn() };

  // Navigation calls tracked for testing
  navigateCalls: Array<{ path: string[] }> = [];

  constructor(
    tabStateService: MockTabStateService,
    workspaceOverlayService: MockWorkspaceOverlayService = new MockWorkspaceOverlayService()
  ) {
    this.tabStateService = tabStateService;
    this.workspaceOverlayService = workspaceOverlayService;
    // Run the tab→workspace sync once with the initial activeProjectId,
    // mirroring the production `tabWorkspaceEffect` initial run.
    this.runTabWorkspaceEffect();
  }

  /**
   * Mirrors the production `tabWorkspaceEffect` in ChatComponent. In the
   * real component, `effect()` creates a reactive subscription to
   * `activeProjectId()`. Here we expose it as a plain method that tests
   * invoke after mutating activeProjectId.
   */
  runTabWorkspaceEffect(): void {
    const projectId = this.tabStateService.activeProjectId();
    const isOpen = this.workspaceOverlayService.showWorkspace();
    const currentId = this.workspaceOverlayService.workspaceProjectId();

    if (projectId === null) {
      if (isOpen)    this.workspaceOverlayService.hide();
      if (currentId) this.workspaceOverlayService.workspaceProjectId.set(null);
      return;
    }

    if (isOpen && currentId !== projectId) {
      this.workspaceOverlayService.workspaceProjectId.set(projectId);
    }
  }

  protected onSendMessage(payload: TestMessagePayload): void {
    const instance = this.currentInstance();
    if (!instance) return;

    const now = Date.now();
    const elapsed = now - this.lastSendTime;
    if (this.lastSendTime > 0 && elapsed < this.SEND_COOLDOWN_MS) {
      const remaining = Math.ceil((this.SEND_COOLDOWN_MS - elapsed) / 1000);
      this.snackBar.open(
        `Please wait ${remaining}s before sending another message.`,
        'Dismiss',
        { duration: 2000, panelClass: 'info-snackbar' }
      );
      return;
    }
    this.lastSendTime = now;

    this.sendError.set(null);
    this.isSending.set(true);

    this.api.sendMessage(instance.instance_id, payload.content, payload.images).subscribe({
      next: () => {
        this.messageInputRef.clearInput();
      },
      error: (err: any) => {
        this.sendError.set(err instanceof Error ? err.message : 'Failed to send message');
        this.isSending.set(false);
      }
    });
  }

  /**
   * Mirrors production ChatComponent.onToggleWatchover: turning OFF is
   * a no-dialog path; turning ON on a running instance is also a no-dialog
   * path (the backend builds the watcher context from the live message
   * stream); turning ON on a non-running instance opens the dialog so the
   * operator can supply the next command and tighten the guardrails.
   *
   * The dialog flow is delegated to ``openWatchoverDialog`` so the
   * surrogate (which has no MatDialog) can still expose a single hook
   * for tests to spy on or override.
   */
  onToggleWatchover(): void {
    const instance = this.currentInstance();
    if (!instance) return;

    if (this.watchoverEnabled()) {
      // Turning OFF — simple API call, no dialog.
      this.toggleWatchoverApi(instance.instance_id, false, null);
      return;
    }

    // Running instances skip the dialog.
    const isRunning = instance.status === 'running';
    if (isRunning) {
      this.toggleWatchoverApi(instance.instance_id, true, null);
      return;
    }

    // Non-running instances go through the dialog.
    this.openWatchoverDialog(instance.instance_id);
  }

  /**
   * Test seam for the watchover dialog flow. The real ChatComponent
   * injects MatDialog and opens WatchoverDialogComponent, then
   * subscribes to afterClosed() to forward the captured result to
   * ``toggleWatchoverApi``. The surrogate has no MatDialog, so it
   * exposes this as a single overridable hook — tests can spy on it
   * to assert the dialog was triggered, or override it per-test to
   * drive a fake dialog result.
   */
  openWatchoverDialog(instanceId: string): void {
    // No-op in the surrogate. Production wires MatDialog.open() here.
  }

  private toggleWatchoverApi(
    instanceId: string,
    enabled: boolean,
    requirement: string | null,
  ): void {
    this.api.setWatchover(instanceId, enabled, requirement).subscribe({
      next: (response: { watchover_enabled: boolean; instance_id: string }) => {
        this.watchoverEnabled.set(response.watchover_enabled);
        if (!response.watchover_enabled) {
          this.watchoverDenialCount.set(0);
        }
        this.persistWatchoverPreference(instanceId, response.watchover_enabled, requirement);
        this.snackBar.open(
          response.watchover_enabled ? '👁️ Watchover enabled' : '👁️ Watchover disabled',
          'Dismiss',
          { duration: 2000, panelClass: 'info-snackbar' }
        );
      },
      error: () => {
        this.snackBar.open('Failed to toggle watchover', 'Dismiss', { duration: 3000 });
        this.watchoverPending.set(false);
      }
    });
  }

  private getSavedWatchoverRequirement(instanceId: string): string | null {
    const raw = localStorage.getItem(`ensemble-watchover-${instanceId}`);
    if (!raw) return null;

    try {
      const saved = JSON.parse(raw) as { requirement?: unknown };
      return typeof saved.requirement === 'string' ? saved.requirement : null;
    } catch {
      return null;
    }
  }

  private persistWatchoverPreference(
    instanceId: string,
    enabled: boolean,
    requirement: string | null,
  ): void {
    const savedRequirement = requirement ?? this.getSavedWatchoverRequirement(instanceId);
    localStorage.setItem(
      `ensemble-watchover-${instanceId}`,
      JSON.stringify({ enabled, requirement: savedRequirement })
    );
  }

  runWatchoverStatusEffect(): void {
    const statusChange = this.sseService.statusChange();
    const currentInstance = this.currentInstance();
    if (!statusChange || !currentInstance || statusChange.instance_id !== currentInstance.instance_id) {
      return;
    }

    switch (statusChange.status) {
      case 'watchover_active':
        this.watchoverEnabled.set(true);
        break;
      case 'watchover_inactive':
        this.watchoverEnabled.set(false);
        this.watchoverDenialCount.set(0);
        break;
      case 'watchover_failed':
        this.watchoverEnabled.set(false);
        this.watchoverDenialCount.set(0);
        break;
      case 'watchover_terminated':
        this.watchoverEnabled.set(false);
        this.watchoverDenialCount.set(0);
        break;
    }
  }

  /**
   * Mirrors the production syncWatchoverState (chat.component.ts). C1 fix:
   * only syncs enabled+context from the API; the denial counter is
   * intentionally NOT synced because the backend always returns 0.
   */
  syncWatchoverState(instance: InstanceInfo): void {
    this.watchoverEnabled.set(instance.watchover_enabled ?? false);
    this.watchoverContext.set(instance.watchover_context ?? null);
    // Deliberately NOT syncing watchoverDenialCount (SSE is authoritative)
  }

  runWatchoverDenialEffect(): void {
    const messages = this.sseService.messages();
    const last = messages[messages.length - 1];
    if (!last || last.role !== 'tool') return;
    if (
      typeof last.content !== 'string'
      || (!last.content.startsWith('Watchover denied') && !last.content.startsWith('Watchover deferred'))
    ) {
      return;
    }

    const currentInstance = this.currentInstance();
    if (!currentInstance || (last.instance_id && last.instance_id !== currentInstance.instance_id)) return;
    const messageKey = `${currentInstance.instance_id}:${last.message_id ?? last.created_at ?? ''}:${last.content}`;
    if (this.processedWatchoverDenials.has(messageKey)) return;
    this.processedWatchoverDenials.add(messageKey);
    this.watchoverDenialCount.update(count => count + 1);
  }

  protected onTerminateInstance(instanceId: string): void {
    this.api.deleteInstance(instanceId).subscribe({
      next: () => {
        if (this.currentInstanceId() === instanceId) {
          this.currentInstanceId.set(null);
          this.navigateCalls.push({ path: ['/'] });
        }
      },
      error: () => {}
    });
  }

  protected onNewInstance(): void {
    const agent = this.selectedAgent();
    if (!agent) {
      this.navigateCalls.push({ path: ['/'] });
      return;
    }

    // Reset state when creating new instance
    this.isSending.set(false);
    this.sendError.set(null);
    this.currentInstanceId.set(null);
    this.sseService.clearEvents();
    this.sseService.disconnect();

    const agentPath = `./agents/${agent.id}`;
    const projectContext = this.tabStateService.activeProjectId() ?? 'all';

    this.api.createInstance(agentPath).subscribe({
      next: (instance: InstanceInfo) => {
        this.navigateCalls.push({
          path: ['/projects', projectContext, 'instances', instance.instance_id]
        });
      },
      error: () => {}
    });
  }

  protected onBackToHome(): void {
    this.navigateCalls.push({ path: ['/'] });
  }

  // Workspace overlay handler — mirrors ChatComponent. After the
  // refactor that lifts workspace state into the root-provided
  // WorkspaceOverlayService, this handler is a thin delegator: it
  // exists so the template binding (e.g. the project-tab workspace
  // icon) still works, but the actual state lives in
  // `workspaceOverlayService`. The Hide action is bound directly in
  // app.html to `workspaceOverlayService.hide()`, so no separate
  // `onWorkspaceHide` shim is needed here.
  protected onWorkspaceToggle(projectId: string): void {
    this.workspaceOverlayService.toggle(projectId);
  }

  protected get hasRealProject(): boolean {
    const activeId = this.tabStateService.activeProjectId();
    return activeId !== null && activeId !== 'all';
  }

  protected get projectId(): string {
    return this.tabStateService.activeProjectId() ?? 'all';
  }

  protected onHeaderWorkspaceToggle(): void {
    if (!this.hasRealProject) return;
    this.workspaceOverlayService.toggle(this.projectId);
  }

  // Simulates the navigation that happens after instance creation in handleInstanceIdChange
  protected getNavigationPath(instanceId: string): string[] {
    const projectContext = this.tabStateService.activeProjectId() ?? 'all';
    return ['/projects', projectContext, 'instances', instanceId];
  }
}

// Helper to create mock agent
function createMockAgent(overrides: Partial<Agent> = {}): Agent {
  return {
    id: 'test-agent-' + Math.random().toString(36).substr(2, 9),
    name: 'Test Agent',
    description: 'A test agent',
    icon: '🤖',
    ...overrides,
  };
}

// Helper to create mock instance
function createMockInstance(overrides: Partial<InstanceInfo> = {}): InstanceInfo {
  return {
    instance_id: 'instance-' + Math.random().toString(36).substr(2, 9),
    agent_id: 'test-agent',
    status: 'running',
    parent_id: null,
    children: [],
    title: null,
    created_at: new Date().toISOString(),
    updated_at: null,
    project_id: null,
    ...overrides,
  };
}

describe('ChatComponent - Project-Aware Navigation', () => {
  let tabStateService: MockTabStateService;
  let component: TestableChatComponent;

  beforeEach(() => {
    localStorage.clear();
    mockSseService.messages.set([]);
    mockSseService.events.set([]);
    mockSseService.statusChange.set(null);
    tabStateService = new MockTabStateService();
    component = new TestableChatComponent(tabStateService);
    jest.clearAllMocks();
  });

  describe('onNewInstance() - Navigation Point', () => {
    beforeEach(() => {
      const agent = createMockAgent({ id: 'chat-agent' });
      component.selectedAgent.set(agent);
    });

    it('should navigate to /projects/all/instances/:instanceId when on All tab', () => {
      tabStateService.setActiveTab('all');
      const instanceId = 'chat-inst-001';
      mockApiService.createInstance.mockReturnValue({
        subscribe: (handlers: any) => {
          handlers.next(createMockInstance({ instance_id: instanceId }));
          return { unsubscribe: () => {} };
        }
      });

      component.onNewInstance();

      expect(component.navigateCalls).toHaveLength(1);
      expect(component.navigateCalls[0].path).toEqual(['/projects', 'all', 'instances', instanceId]);
    });

    it('should navigate to /projects/:projectId/instances/:instanceId when project is selected', () => {
      tabStateService.setActiveTab('chat-project');
      const instanceId = 'chat-inst-002';
      mockApiService.createInstance.mockReturnValue({
        subscribe: (handlers: any) => {
          handlers.next(createMockInstance({ instance_id: instanceId }));
          return { unsubscribe: () => {} };
        }
      });

      component.onNewInstance();

      expect(component.navigateCalls).toHaveLength(1);
      expect(component.navigateCalls[0].path).toEqual(['/projects', 'chat-project', 'instances', instanceId]);
    });

    it('should navigate to home when no agent is selected', () => {
      component.selectedAgent.set(null);
      tabStateService.setActiveTab('all');

      component.onNewInstance();

      expect(component.navigateCalls).toHaveLength(1);
      expect(component.navigateCalls[0].path).toEqual(['/']);
    });

    it('should clear SSE state before navigation', () => {
      tabStateService.setActiveTab('sse-project');
      const instanceId = 'sse-clear-inst';
      mockApiService.createInstance.mockReturnValue({
        subscribe: (handlers: any) => {
          handlers.next(createMockInstance({ instance_id: instanceId }));
          return { unsubscribe: () => {} };
        }
      });

      component.onNewInstance();

      expect(mockSseService.clearEvents).toHaveBeenCalled();
      expect(mockSseService.disconnect).toHaveBeenCalled();
    });
  });

  describe('getNavigationPath() - Instance Navigation Helper', () => {
    it('should return /projects/all/instances/:instanceId when on All tab', () => {
      tabStateService.setActiveTab('all');

      const path = component.getNavigationPath('helper-inst-001');

      expect(path).toEqual(['/projects', 'all', 'instances', 'helper-inst-001']);
    });

    it('should return /projects/:projectId/instances/:instanceId when project is selected', () => {
      tabStateService.setActiveTab('helper-project');

      const path = component.getNavigationPath('helper-inst-002');

      expect(path).toEqual(['/projects', 'helper-project', 'instances', 'helper-inst-002']);
    });

    it('should preserve instance ID in navigation path', () => {
      tabStateService.setActiveTab('preserve-project');
      const instanceId = 'preserve-inst-xyz';

      const path = component.getNavigationPath(instanceId);

      expect(path[3]).toBe(instanceId);
    });
  });

  describe('URL Pattern Verification', () => {
    it('should produce correct URL pattern for All tab', () => {
      tabStateService.setActiveTab('all');

      const path = component.getNavigationPath('pattern-inst');

      // Verify URL structure
      expect(path).toHaveLength(4);
      expect(path[0]).toBe('/projects');
      expect(path[1]).toBe('all');
      expect(path[2]).toBe('instances');
      expect(typeof path[3]).toBe('string'); // instance ID
    });

    it('should produce correct URL pattern for specific project', () => {
      tabStateService.setActiveTab('pattern-project-123');

      const path = component.getNavigationPath('pattern-inst');

      expect(path).toHaveLength(4);
      expect(path[0]).toBe('/projects');
      expect(path[1]).toBe('pattern-project-123');
      expect(path[2]).toBe('instances');
    });

    it('should handle various project ID formats', () => {
      const projectIds = ['proj-1', 'my_project', 'project.with.dots', '123-numbers'];

      for (const projectId of projectIds) {
        component.navigateCalls = [];
        tabStateService.setActiveTab(projectId);

        const path = component.getNavigationPath('test-inst');

        expect(path[1]).toBe(projectId);
        expect(path[0]).toBe('/projects');
        expect(path[2]).toBe('instances');
      }
    });
  });

  describe('onTerminateInstance() - Navigation', () => {
    it('should navigate to home after terminating current instance', () => {
      const instanceId = 'terminate-inst';
      component.currentInstanceId.set(instanceId);

      mockApiService.deleteInstance.mockReturnValue({
        subscribe: (handlers: any) => {
          handlers.next({});
          return { unsubscribe: () => {} };
        }
      });

      component.onTerminateInstance(instanceId);

      expect(component.navigateCalls).toHaveLength(1);
      expect(component.navigateCalls[0].path).toEqual(['/']);
    });

    it('should not navigate when terminating non-current instance', () => {
      component.currentInstanceId.set('current-inst');

      mockApiService.deleteInstance.mockReturnValue({
        subscribe: (handlers: any) => {
          handlers.next({});
          return { unsubscribe: () => {} };
        }
      });

      component.onTerminateInstance('other-inst');

      expect(component.navigateCalls).toHaveLength(0);
    });
  });

  describe('onBackToHome() - Navigation', () => {
    it('should navigate to home', () => {
      component.onBackToHome();

      expect(component.navigateCalls).toHaveLength(1);
      expect(component.navigateCalls[0].path).toEqual(['/']);
    });
  });

  describe('onSendMessage() - Send Cooldown', () => {
    beforeEach(() => {
      component.currentInstance.set(createMockInstance({ instance_id: 'cooldown-inst' }));
      mockApiService.sendMessage.mockReturnValue({
        subscribe: (handlers: any) => {
          handlers.next({});
          return { unsubscribe: () => {} };
        }
      });
    });

    afterEach(() => {
      jest.restoreAllMocks();
    });

    it('should send the message on the first attempt', () => {
      jest.spyOn(Date, 'now').mockReturnValue(1000);
      component.onSendMessage({ content: 'hello' });

      expect(mockApiService.sendMessage).toHaveBeenCalledWith('cooldown-inst', 'hello', undefined);
      expect(mockSnackBar.open).not.toHaveBeenCalled();
    });

    it('should block a second send within 3s and show a notification', () => {
      jest.spyOn(Date, 'now').mockReturnValue(1000);
      component.onSendMessage({ content: 'hello' });
      component.onSendMessage({ content: 'hello again' });

      expect(mockApiService.sendMessage).toHaveBeenCalledTimes(1);
      expect(mockSnackBar.open).toHaveBeenCalledWith(
        expect.stringContaining('Please wait'),
        'Dismiss',
        { duration: 2000, panelClass: 'info-snackbar' }
      );
    });

    it('should allow another send after the 3s cooldown elapses', () => {
      const nowSpy = jest.spyOn(Date, 'now');
      nowSpy.mockReturnValueOnce(1000);
      nowSpy.mockReturnValueOnce(1000 + 3001);

      component.onSendMessage({ content: 'hello' });
      expect(mockApiService.sendMessage).toHaveBeenCalledTimes(1);

      component.onSendMessage({ content: 'hello again' });
      expect(mockApiService.sendMessage).toHaveBeenCalledTimes(2);
    });

    it('should clear input on a successful send', () => {
      component.onSendMessage({ content: 'hello' });

      expect(component.messageInputRef.clearInput).toHaveBeenCalled();
    });

    it('should not send when no instance is set', () => {
      component.currentInstance.set(null);
      component.onSendMessage({ content: 'hello' });

      expect(mockApiService.sendMessage).not.toHaveBeenCalled();
    });
  });

  describe('Watchover integration', () => {
    // TECHNICAL DEBT: These tests run against a hand-maintained
    // TestableChatComponent surrogate that mirrors production logic.
    // The surrogate can diverge from the real ChatComponent. When
    // adding new watchover behavior, update BOTH the surrogate and
    // these tests to stay in sync. Future work: migrate to TestBed
    // component testing against the real ChatComponent.
    afterEach(() => {
      jest.restoreAllMocks();
    });

    it('should call the API directly when enabling watchover on a running instance', () => {
      // Running instances skip the dialog — the backend builds the
      // watcher context from the live message stream. Mirrors the
      // no-dialog branch in production ChatComponent.onToggleWatchover.
      const instanceId = 'watchover-enable-running-inst';
      component.currentInstanceId.set(instanceId);
      component.currentInstance.set(createMockInstance({ instance_id: instanceId, status: 'running' }));
      mockApiService.setWatchover.mockReturnValue({
        subscribe: (handlers: any) => {
          handlers.next({ watchover_enabled: true, instance_id: instanceId });
          return { unsubscribe: () => {} };
        }
      });

      component.onToggleWatchover();

      expect(mockApiService.setWatchover).toHaveBeenCalledWith(instanceId, true, null);
      expect(component.watchoverEnabled()).toBe(true);
    });

    it('should open the watchover dialog when enabling on a non-running instance', () => {
      // Non-running (terminal/idle) instances need the operator to
      // supply the next command + guardrails, so the dialog is opened
      // and the API is NOT called yet.
      const instanceId = 'watchover-enable-terminal-inst';
      component.currentInstanceId.set(instanceId);
      component.currentInstance.set(createMockInstance({ instance_id: instanceId, status: 'paused' }));
      const dialogSpy = jest.spyOn(component, 'openWatchoverDialog');

      component.onToggleWatchover();

      expect(dialogSpy).toHaveBeenCalledWith(instanceId);
      expect(mockApiService.setWatchover).not.toHaveBeenCalled();
    });

    it('should increment the denial counter for a watchover ToolMessage', () => {
      const instanceId = 'watchover-denial-inst';
      component.currentInstance.set(createMockInstance({ instance_id: instanceId }));
      mockSseService.messages.set([{
        message_id: 'watchover-denial-message',
        role: 'tool',
        content: 'Watchover denied this tool call: destructive delete. Please adjust your approach.',
        created_at: new Date().toISOString(),
        instance_id: instanceId,
      }]);

      component.runWatchoverDenialEffect();

      expect(component.watchoverDenialCount()).toBe(1);
    });

    it('should update watchover state for active and inactive status changes', () => {
      const instanceId = 'watchover-status-inst';
      component.currentInstance.set(createMockInstance({ instance_id: instanceId }));

      mockSseService.statusChange.set({ instance_id: instanceId, status: 'watchover_active' });
      component.runWatchoverStatusEffect();
      expect(component.watchoverEnabled()).toBe(true);

      component.watchoverDenialCount.set(2);
      mockSseService.statusChange.set({ instance_id: instanceId, status: 'watchover_inactive' });
      component.runWatchoverStatusEffect();
      expect(component.watchoverEnabled()).toBe(false);
      expect(component.watchoverDenialCount()).toBe(0);
    });

    it('should open the dialog with the saved requirement available as default for non-running instances', () => {
      // The legacy window.prompt flow used the saved requirement as
      // the prompt's default value. The dialog flow exposes a single
      // openWatchoverDialog hook instead, but the saved requirement
      // is still read from localStorage so the dialog (or any caller)
      // can pre-fill its requirement field. This test guards that
      // read path so the dialog initializer can rely on it.
      const instanceId = 'watchover-storage-inst';
      const storageKey = `ensemble-watchover-${instanceId}`;
      component.currentInstanceId.set(instanceId);
      component.currentInstance.set(createMockInstance({ instance_id: instanceId, status: 'paused' }));
      localStorage.setItem(
        storageKey,
        JSON.stringify({ enabled: false, requirement: 'Saved monitoring requirement' })
      );
      const dialogSpy = jest.spyOn(component, 'openWatchoverDialog');

      component.onToggleWatchover();

      expect(dialogSpy).toHaveBeenCalledWith(instanceId);
      expect(mockApiService.setWatchover).not.toHaveBeenCalled();
      // Saved requirement remains readable so the dialog (or any
      // caller) can pre-fill its requirement field. Read localStorage
      // directly because ``getSavedWatchoverRequirement`` is private.
      expect(JSON.parse(localStorage.getItem(storageKey) ?? '{}')).toEqual({
        enabled: false,
        requirement: 'Saved monitoring requirement',
      });
    });

    it('should call the API with enabled=false when toggling off watchover', () => {
      const instanceId = 'watchover-disable-inst';
      component.currentInstanceId.set(instanceId);
      component.currentInstance.set(createMockInstance({ instance_id: instanceId }));
      component.watchoverEnabled.set(true);
      mockApiService.setWatchover.mockReturnValue({
        subscribe: (handlers: any) => {
          handlers.next({ watchover_enabled: false, instance_id: instanceId });
          return { unsubscribe: () => {} };
        }
      });

      component.onToggleWatchover();

      expect(mockApiService.setWatchover).toHaveBeenCalledWith(instanceId, false, null);
      expect(component.watchoverEnabled()).toBe(false);
    });

    it('should show error snackbar and not change state on API failure', () => {
      // Running-instance path so no dialog is involved — the API call
      // fails synchronously and the error handler must surface the
      // snackbar without flipping the watchover toggle.
      const instanceId = 'watchover-error-inst';
      component.currentInstanceId.set(instanceId);
      component.currentInstance.set(createMockInstance({ instance_id: instanceId, status: 'running' }));
      mockApiService.setWatchover.mockReturnValue({
        subscribe: (handlers: any) => {
          handlers.error(new Error('Network error'));
          return { unsubscribe: () => {} };
        }
      });

      component.onToggleWatchover();

      expect(component.watchoverEnabled()).toBe(false);
      expect(mockSnackBar.open).toHaveBeenCalledWith('Failed to toggle watchover', 'Dismiss', { duration: 3000 });
      expect(component.watchoverPending()).toBe(false);
    });

    it('should reset toggle and denial count on watchover_failed SSE event', () => {
      const instanceId = 'watchover-failed-inst';
      component.currentInstance.set(createMockInstance({ instance_id: instanceId }));
      component.watchoverEnabled.set(true);
      component.watchoverDenialCount.set(2);

      mockSseService.statusChange.set({ instance_id: instanceId, status: 'watchover_failed' });
      component.runWatchoverStatusEffect();

      expect(component.watchoverEnabled()).toBe(false);
      expect(component.watchoverDenialCount()).toBe(0);
    });

    it('should clear watchover state on watchover_terminated SSE event', () => {
      const instanceId = 'watchover-terminated-inst';
      component.currentInstance.set(createMockInstance({ instance_id: instanceId }));
      component.watchoverEnabled.set(true);
      component.watchoverDenialCount.set(3);

      mockSseService.statusChange.set({ instance_id: instanceId, status: 'watchover_terminated' });
      component.runWatchoverStatusEffect();

      expect(component.watchoverEnabled()).toBe(false);
      expect(component.watchoverDenialCount()).toBe(0);
    });

    it('should NOT overwrite denial count when syncing from API poll (C1 fix)', () => {
      const instanceId = 'watchover-c1-inst';
      const instance = createMockInstance({
        instance_id: instanceId,
        watchover_enabled: true,
        watchover_context: 'test context',
        watchover_denial_count: 0,  // backend always returns 0
      });
      component.currentInstance.set(instance);
      component.watchoverDenialCount.set(2);  // SSE incremented this

      component.syncWatchoverState(instance);

      // watchoverEnabled and watchoverContext sync from API...
      expect(component.watchoverEnabled()).toBe(true);
      // ...but denial count is preserved (not reset to 0 from API)
      expect(component.watchoverDenialCount()).toBe(2);
    });
  });

  describe('Workspace overlay', () => {
    it('should start hidden with no project id', () => {
      expect(component.workspaceOverlayService.showWorkspace()).toBe(false);
      expect(component.workspaceOverlayService.workspaceProjectId()).toBeNull();
    });

    it('should open the overlay for the toggled project', () => {
      component.onWorkspaceToggle('proj-a');

      expect(component.workspaceOverlayService.showWorkspace()).toBe(true);
      expect(component.workspaceOverlayService.workspaceProjectId()).toBe('proj-a');
    });

    it('should toggle off when the same project is clicked again', () => {
      component.onWorkspaceToggle('proj-a');
      component.onWorkspaceToggle('proj-a');

      expect(component.workspaceOverlayService.showWorkspace()).toBe(false);
      expect(component.workspaceOverlayService.workspaceProjectId()).toBe('proj-a');
    });

    it('should switch projects when a different project is clicked while open', () => {
      component.onWorkspaceToggle('proj-a');
      component.onWorkspaceToggle('proj-b');

      expect(component.workspaceOverlayService.showWorkspace()).toBe(true);
      expect(component.workspaceOverlayService.workspaceProjectId()).toBe('proj-b');
    });

    it('should require a real project for the header toggle', () => {
      tabStateService.setActiveTab('all');
      component.onHeaderWorkspaceToggle();

      expect(component.workspaceOverlayService.showWorkspace()).toBe(false);
    });

    it('should open the overlay from the header for the active project', () => {
      tabStateService.setActiveTab('proj-header');
      component.onHeaderWorkspaceToggle();

      expect(component.workspaceOverlayService.showWorkspace()).toBe(true);
      expect(component.workspaceOverlayService.workspaceProjectId()).toBe('proj-header');
    });
  });

  describe('Workspace ↔ tab sync (tabWorkspaceEffect)', () => {
    it('should follow workspace to the newly active project when workspace is open', () => {
      // Open workspace on proj-a
      component.onWorkspaceToggle('proj-a');
      expect(component.workspaceOverlayService.showWorkspace()).toBe(true);
      expect(component.workspaceOverlayService.workspaceProjectId()).toBe('proj-a');

      // Simulate user clicking the proj-b tab — activeProjectId changes
      tabStateService.setActiveTab('proj-b');
      component.runTabWorkspaceEffect();

      expect(component.workspaceOverlayService.showWorkspace()).toBe(true);
      expect(component.workspaceOverlayService.workspaceProjectId()).toBe('proj-b');
    });

    it('should NOT auto-open workspace when switching to a project tab while closed', () => {
      // Workspace is closed by default
      expect(component.workspaceOverlayService.showWorkspace()).toBe(false);

      tabStateService.setActiveTab('proj-b');
      component.runTabWorkspaceEffect();

      expect(component.workspaceOverlayService.showWorkspace()).toBe(false);
      expect(component.workspaceOverlayService.workspaceProjectId()).toBeNull();
    });

    it('should hide the workspace when switching to the All tab', () => {
      // Open workspace on a project
      component.onWorkspaceToggle('proj-a');
      expect(component.workspaceOverlayService.showWorkspace()).toBe(true);

      // Switch to All tab
      tabStateService.setActiveTab('all');
      component.runTabWorkspaceEffect();

      expect(component.workspaceOverlayService.showWorkspace()).toBe(false);
      expect(component.workspaceOverlayService.workspaceProjectId()).toBeNull();
    });

    it('should be a no-op when switching to the same project that is already open', () => {
      component.onWorkspaceToggle('proj-a');
      tabStateService.setActiveTab('proj-a');
      component.runTabWorkspaceEffect();

      expect(component.workspaceOverlayService.showWorkspace()).toBe(true);
      expect(component.workspaceOverlayService.workspaceProjectId()).toBe('proj-a');
    });
  });

  describe('Workspace icon click flow (project-tab-bar → ChatComponent)', () => {
    it('should switch the active tab AND open the workspace when icon is clicked on a different project', () => {
      // Simulate: user is on proj-a, clicks workspace icon on proj-b
      // ProjectTabBarComponent.onWorkspaceClick:
      //   1. setActiveTab('proj-b')
      //   2. emit workspaceToggle('proj-b')
      // ChatComponent.onWorkspaceToggle handler runs:
      //   since workspaceProjectId !== 'proj-b', workspaceProjectId = 'proj-b', showWorkspace = true
      tabStateService.setActiveTab('proj-b');
      component.onWorkspaceToggle('proj-b');
      // Then the effect fires (it would have fired on setActiveTab too, but
      // for proj-b the condition (showWorkspace && workspaceProjectId !== proj-b)
      // would already be false since onWorkspaceToggle just set them equal).
      component.runTabWorkspaceEffect();

      expect(tabStateService.setActiveTabCalls).toContain('proj-b');
      expect(component.workspaceOverlayService.showWorkspace()).toBe(true);
      expect(component.workspaceOverlayService.workspaceProjectId()).toBe('proj-b');
    });

    it('should switch the active tab AND close the workspace when icon is clicked on the same project', () => {
      // Open workspace on proj-a while on proj-a tab
      tabStateService.setActiveTab('proj-a');
      component.onWorkspaceToggle('proj-a');
      expect(component.workspaceOverlayService.showWorkspace()).toBe(true);

      // Click the icon again — setActiveTab('proj-a') then toggle
      tabStateService.setActiveTab('proj-a');
      component.onWorkspaceToggle('proj-a');
      component.runTabWorkspaceEffect();

      expect(tabStateService.setActiveTabCalls).toContain('proj-a');
      expect(component.workspaceOverlayService.showWorkspace()).toBe(false);
    });
  });
});

// ────────────────────────────────────────────────────────────────────────────
// Real Angular `effect()` integration test
//
// The unit tests above use `runTabWorkspaceEffect()` — a manual mirror of
// the production effect. That keeps the sync logic itself testable in
// isolation, but means deleting the `effect()` declaration in production
// would NOT be caught. This block exercises the REAL Angular reactive
// graph: a host component registers a real `effect()` inside its
// constructor (in TestBed's injection context), and we assert that
// mutating the active tab via a (mock) TabStateService drives the
// effect's writes back into the local workspace signals.
// ────────────────────────────────────────────────────────────────────────────

@Component({
  selector: 'app-tab-workspace-effect-host',
  standalone: true,
  template: '',
})
class TestTabWorkspaceEffectHostComponent {
  /**
   * Injected mock workspace overlay service. Mirrors production: the
   * real ChatComponent receives the root-provided `WorkspaceOverlayService`
   * via `inject(...)`; here we inject the mock so the test can drive
   * and observe the same state transitions the real App root would.
   * Exposed as a public field so tests can read the `showWorkspace`
   * and `workspaceProjectId` signals directly.
   */
  readonly workspaceOverlayService = inject(MockWorkspaceOverlayService);

  /**
   * Tracks how many times the effect's body has run. We use this to
   * confirm the real `effect()` is actually firing — not just that the
   * sync logic, when manually invoked, produces the expected output.
   */
  effectRunCount = 0;

  constructor(@Inject(MockTabStateService) private readonly tabState: MockTabStateService) {
    // Mirror of the production `tabWorkspaceEffect` in ChatComponent.
    // Lives inside TestBed's injection context so `effect()` has an
    // injector to register against.
    //
    // CRITICAL: workspaceOverlayService.showWorkspace() and
    // .workspaceProjectId() MUST be read unconditionally at the top
    // of the effect (not inside the non-null branch) so Angular's
    // reactive graph keeps them as dependencies across every run. If
    // we conditionally read them, then a run that hits the
    // `projectId === null` branch will drop those deps, and subsequent
    // mutations to those signals will not retrigger the effect. The
    // production code at chat.component.ts:tabWorkspaceEffect enforces
    // this — we mirror it exactly here so this test host is a faithful
    // double of production.
    effect(() => {
      this.effectRunCount++;
      const projectId = this.tabState.activeProjectId();
      const isOpen = this.workspaceOverlayService.showWorkspace();         // always read → always tracked
      const currentId = this.workspaceOverlayService.workspaceProjectId(); // always read → always tracked

      // Switching to "All" tab → hide workspace
      if (projectId === null) {
        if (isOpen)    this.workspaceOverlayService.hide();
        if (currentId) this.workspaceOverlayService.workspaceProjectId.set(null);
        return;
      }

      // For project tabs: only sync workspace if it's already open.
      // Do NOT auto-open on plain tab switch.
      if (isOpen && currentId !== projectId) {
        this.workspaceOverlayService.workspaceProjectId.set(projectId);
      }
    }, { allowSignalWrites: true });
  }
}

describe('ChatComponent tabWorkspaceEffect — real Angular effect wiring', () => {
  let fixture: ComponentFixture<TestTabWorkspaceEffectHostComponent>;
  let host: TestTabWorkspaceEffectHostComponent;
  let tabStateService: MockTabStateService;
  let workspaceOverlayService: MockWorkspaceOverlayService;

  beforeEach(async () => {
    tabStateService = new MockTabStateService();
    workspaceOverlayService = new MockWorkspaceOverlayService();

    await TestBed.configureTestingModule({
      imports: [TestTabWorkspaceEffectHostComponent],
      providers: [
        { provide: MockTabStateService, useValue: tabStateService },
        { provide: MockWorkspaceOverlayService, useValue: workspaceOverlayService },
      ],
    }).compileComponents();

    fixture = TestBed.createComponent(TestTabWorkspaceEffectHostComponent);
    host = fixture.componentInstance;
    // Sanity: the host's injected service must be the same instance we
    // configured in TestBed (the real production code uses the
    // root-provided singleton, so the test mirrors that contract).
    expect(host.workspaceOverlayService).toBe(workspaceOverlayService);
    // Initial detectChanges runs the effect once (initial pass).
    fixture.detectChanges();
  });

  it('registers and runs the effect on initial render', () => {
    expect(host.effectRunCount).toBeGreaterThanOrEqual(1);
    expect(host.workspaceOverlayService.showWorkspace()).toBe(false);
    expect(host.workspaceOverlayService.workspaceProjectId()).toBeNull();
  });

  it('re-runs the effect (reactive graph) when activeProjectId changes via setActiveTab', () => {
    const beforeCount = host.effectRunCount;

    // Switch from default All tab → project tab. With showWorkspace false
    // and workspaceProjectId null, the effect should NOT auto-open the
    // workspace. But it MUST run again — that's the wiring assertion.
    tabStateService.setActiveTab('proj-a');
    fixture.detectChanges();

    expect(host.effectRunCount).toBeGreaterThan(beforeCount);
    expect(host.workspaceOverlayService.showWorkspace()).toBe(false);
    expect(host.workspaceOverlayService.workspaceProjectId()).toBeNull();
  });

  it('follows the open workspace to the newly active project via the real effect', () => {
    // Switch to a project tab first so the effect starts tracking
    // showWorkspace / workspaceProjectId (those reads only happen once
    // activeProjectId is non-null).
    tabStateService.setActiveTab('proj-a');
    fixture.detectChanges();

    // Open workspace on proj-a (mirrors user clicking the icon)
    host.workspaceOverlayService.showWorkspace.set(true);
    host.workspaceOverlayService.workspaceProjectId.set('proj-a');
    fixture.detectChanges();
    const beforeCount = host.effectRunCount;

    // Switch to proj-b — effect should fire and update workspaceProjectId
    tabStateService.setActiveTab('proj-b');
    fixture.detectChanges();

    expect(host.effectRunCount).toBeGreaterThan(beforeCount);
    expect(host.workspaceOverlayService.showWorkspace()).toBe(true);
    expect(host.workspaceOverlayService.workspaceProjectId()).toBe('proj-b');
  });

  it('hides the workspace via the real effect when switching to the All tab', () => {
    // Switch to a project tab first so the effect starts tracking
    // showWorkspace / workspaceProjectId. Without this, the effect only
    // tracks activeProjectId (its null-branch never reads the workspace
    // signals), and our manual setVisible(true) calls would be invisible.
    tabStateService.setActiveTab('proj-a');
    fixture.detectChanges();

    // Open workspace on proj-a
    host.workspaceOverlayService.showWorkspace.set(true);
    host.workspaceOverlayService.workspaceProjectId.set('proj-a');
    fixture.detectChanges();
    const beforeCount = host.effectRunCount;

    // Switch to All tab — effect should fire and clear workspace state
    tabStateService.setActiveTab('all');
    fixture.detectChanges();

    expect(host.effectRunCount).toBeGreaterThan(beforeCount);
    expect(host.workspaceOverlayService.showWorkspace()).toBe(false);
    expect(host.workspaceOverlayService.workspaceProjectId()).toBeNull();
  });

  it('keeps showWorkspace/workspaceProjectId tracked after All-tab dep-drop', () => {
    // Regression for the dep-tracking hazard fixed at
    // chat.component.ts:131-145. Visiting the All tab used to cause the
    // buggy effect to drop its subscription to showWorkspace and
    // workspaceProjectId, so external writes to those signals became
    // invisible until the next non-All run re-established the dep set.
    //
    // The fix unconditionally reads both signals at the top of the
    // effect body, so they remain tracked on every run.
    //
    // To prove the dep set is intact, this test must mutate a workspace
    // signal AFTER the All-tab run WITHOUT going through a non-null
    // branch in between (a non-null branch would re-read the workspace
    // signals and mask the bug). Concretely: we leave the active tab on
    // 'all' and write to showWorkspace, then assert the effect reran.

    // 1. Start on a project tab with workspace open so the effect
    //    initially sees the workspace signals in its dep set.
    tabStateService.setActiveTab('proj-a');
    fixture.detectChanges();
    host.workspaceOverlayService.showWorkspace.set(true);
    host.workspaceOverlayService.workspaceProjectId.set('proj-a');
    fixture.detectChanges();

    // 2. Switch to All tab. With the buggy effect, this run hits the
    //    `projectId === null` branch, which never reads showWorkspace or
    //    workspaceProjectId, so Angular drops them from the dep set.
    tabStateService.setActiveTab('all');
    fixture.detectChanges();
    expect(host.workspaceOverlayService.showWorkspace()).toBe(false);
    expect(host.workspaceOverlayService.workspaceProjectId()).toBeNull();
    const countAfterAllTab = host.effectRunCount;

    // 3. While the active tab is STILL 'all' (so a non-null run cannot
    //    re-establish the dep set), mutate ONLY showWorkspace. With the
    //    buggy effect this write is invisible to the effect (the dep
    //    was dropped in step 2). With the fixed effect showWorkspace
    //    is still tracked, so the effect reruns.
    host.workspaceOverlayService.showWorkspace.set(true);
    fixture.detectChanges();

    // Core assertion: the effect must have rerun because showWorkspace
    // is still tracked. This can only be true if the dep set survived
    // the All-tab run in step 2.
    expect(host.effectRunCount).toBeGreaterThan(countAfterAllTab);
  });
});
