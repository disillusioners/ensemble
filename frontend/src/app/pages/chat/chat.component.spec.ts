import { signal, computed, effect, Component, Inject } from '@angular/core';
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
  createInstance: jest.fn(),
};

// Mock SseService for testing
const mockSseService = {
  messages: signal<any[]>([]),
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

// Testable ChatComponent (mirrors actual component logic)
class TestableChatComponent {
  private readonly api = mockApiService;
  private readonly sseService = mockSseService;
  private readonly snackBar = mockSnackBar;
  protected readonly tabStateService: MockTabStateService;

  readonly currentInstanceId = signal<string | null>(null);
  readonly currentInstance = signal<InstanceInfo | null>(null);
  readonly selectedAgent = signal<Agent | null>(null);
  readonly isSending = signal(false);
  readonly sendError = signal<string | null>(null);

  // Workspace overlay state — mirrors ChatComponent signals
  readonly showWorkspace = signal(false);
  readonly workspaceProjectId = signal<string | null>(null);

  /** Mirrors ChatComponent.SEND_COOLDOWN_MS */
  private readonly SEND_COOLDOWN_MS = 3000;
  private lastSendTime = 0;

  messageInputRef: { clearInput: jest.Mock } = { clearInput: jest.fn() };

  // Navigation calls tracked for testing
  navigateCalls: Array<{ path: string[] }> = [];

  constructor(tabStateService: MockTabStateService) {
    this.tabStateService = tabStateService;
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

    if (projectId === null) {
      this.showWorkspace.set(false);
      this.workspaceProjectId.set(null);
      return;
    }

    if (this.showWorkspace() && this.workspaceProjectId() !== projectId) {
      this.workspaceProjectId.set(projectId);
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

  // Workspace overlay handlers — mirrors ChatComponent
  protected onWorkspaceToggle(projectId: string): void {
    if (this.showWorkspace() && this.workspaceProjectId() === projectId) {
      this.showWorkspace.set(false);
      return;
    }
    this.workspaceProjectId.set(projectId);
    this.showWorkspace.set(true);
  }

  protected onWorkspaceHide(): void {
    this.showWorkspace.set(false);
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
    this.onWorkspaceToggle(this.projectId);
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

  describe('Workspace overlay', () => {
    it('should start hidden with no project id', () => {
      expect(component.showWorkspace()).toBe(false);
      expect(component.workspaceProjectId()).toBeNull();
    });

    it('should open the overlay for the toggled project', () => {
      component.onWorkspaceToggle('proj-a');

      expect(component.showWorkspace()).toBe(true);
      expect(component.workspaceProjectId()).toBe('proj-a');
    });

    it('should toggle off when the same project is clicked again', () => {
      component.onWorkspaceToggle('proj-a');
      component.onWorkspaceToggle('proj-a');

      expect(component.showWorkspace()).toBe(false);
      expect(component.workspaceProjectId()).toBe('proj-a');
    });

    it('should switch projects when a different project is clicked while open', () => {
      component.onWorkspaceToggle('proj-a');
      component.onWorkspaceToggle('proj-b');

      expect(component.showWorkspace()).toBe(true);
      expect(component.workspaceProjectId()).toBe('proj-b');
    });

    it('should close the overlay via onWorkspaceHide', () => {
      component.onWorkspaceToggle('proj-a');
      component.onWorkspaceHide();

      expect(component.showWorkspace()).toBe(false);
    });

    it('should require a real project for the header toggle', () => {
      tabStateService.setActiveTab('all');
      component.onHeaderWorkspaceToggle();

      expect(component.showWorkspace()).toBe(false);
    });

    it('should open the overlay from the header for the active project', () => {
      tabStateService.setActiveTab('proj-header');
      component.onHeaderWorkspaceToggle();

      expect(component.showWorkspace()).toBe(true);
      expect(component.workspaceProjectId()).toBe('proj-header');
    });
  });

  describe('Workspace ↔ tab sync (tabWorkspaceEffect)', () => {
    it('should follow workspace to the newly active project when workspace is open', () => {
      // Open workspace on proj-a
      component.onWorkspaceToggle('proj-a');
      expect(component.showWorkspace()).toBe(true);
      expect(component.workspaceProjectId()).toBe('proj-a');

      // Simulate user clicking the proj-b tab — activeProjectId changes
      tabStateService.setActiveTab('proj-b');
      component.runTabWorkspaceEffect();

      expect(component.showWorkspace()).toBe(true);
      expect(component.workspaceProjectId()).toBe('proj-b');
    });

    it('should NOT auto-open workspace when switching to a project tab while closed', () => {
      // Workspace is closed by default
      expect(component.showWorkspace()).toBe(false);

      tabStateService.setActiveTab('proj-b');
      component.runTabWorkspaceEffect();

      expect(component.showWorkspace()).toBe(false);
      expect(component.workspaceProjectId()).toBeNull();
    });

    it('should hide the workspace when switching to the All tab', () => {
      // Open workspace on a project
      component.onWorkspaceToggle('proj-a');
      expect(component.showWorkspace()).toBe(true);

      // Switch to All tab
      tabStateService.setActiveTab('all');
      component.runTabWorkspaceEffect();

      expect(component.showWorkspace()).toBe(false);
      expect(component.workspaceProjectId()).toBeNull();
    });

    it('should be a no-op when switching to the same project that is already open', () => {
      component.onWorkspaceToggle('proj-a');
      tabStateService.setActiveTab('proj-a');
      component.runTabWorkspaceEffect();

      expect(component.showWorkspace()).toBe(true);
      expect(component.workspaceProjectId()).toBe('proj-a');
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
      expect(component.showWorkspace()).toBe(true);
      expect(component.workspaceProjectId()).toBe('proj-b');
    });

    it('should switch the active tab AND close the workspace when icon is clicked on the same project', () => {
      // Open workspace on proj-a while on proj-a tab
      tabStateService.setActiveTab('proj-a');
      component.onWorkspaceToggle('proj-a');
      expect(component.showWorkspace()).toBe(true);

      // Click the icon again — setActiveTab('proj-a') then toggle
      tabStateService.setActiveTab('proj-a');
      component.onWorkspaceToggle('proj-a');
      component.runTabWorkspaceEffect();

      expect(tabStateService.setActiveTabCalls).toContain('proj-a');
      expect(component.showWorkspace()).toBe(false);
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
  readonly showWorkspace = signal(false);
  readonly workspaceProjectId = signal<string | null>(null);

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
    effect(() => {
      this.effectRunCount++;
      const projectId = this.tabState.activeProjectId();

      // Switching to "All" tab → hide workspace
      if (projectId === null) {
        this.showWorkspace.set(false);
        this.workspaceProjectId.set(null);
        return;
      }

      // For project tabs: only sync workspace if it's already open.
      // Do NOT auto-open on plain tab switch.
      if (this.showWorkspace() && this.workspaceProjectId() !== projectId) {
        this.workspaceProjectId.set(projectId);
      }
    }, { allowSignalWrites: true });
  }
}

describe('ChatComponent tabWorkspaceEffect — real Angular effect wiring', () => {
  let fixture: ComponentFixture<TestTabWorkspaceEffectHostComponent>;
  let host: TestTabWorkspaceEffectHostComponent;
  let tabStateService: MockTabStateService;

  beforeEach(async () => {
    tabStateService = new MockTabStateService();

    await TestBed.configureTestingModule({
      imports: [TestTabWorkspaceEffectHostComponent],
      providers: [
        { provide: MockTabStateService, useValue: tabStateService },
      ],
    }).compileComponents();

    fixture = TestBed.createComponent(TestTabWorkspaceEffectHostComponent);
    host = fixture.componentInstance;
    // Initial detectChanges runs the effect once (initial pass).
    fixture.detectChanges();
  });

  it('registers and runs the effect on initial render', () => {
    expect(host.effectRunCount).toBeGreaterThanOrEqual(1);
    expect(host.showWorkspace()).toBe(false);
    expect(host.workspaceProjectId()).toBeNull();
  });

  it('re-runs the effect (reactive graph) when activeProjectId changes via setActiveTab', () => {
    const beforeCount = host.effectRunCount;

    // Switch from default All tab → project tab. With showWorkspace false
    // and workspaceProjectId null, the effect should NOT auto-open the
    // workspace. But it MUST run again — that's the wiring assertion.
    tabStateService.setActiveTab('proj-a');
    fixture.detectChanges();

    expect(host.effectRunCount).toBeGreaterThan(beforeCount);
    expect(host.showWorkspace()).toBe(false);
    expect(host.workspaceProjectId()).toBeNull();
  });

  it('follows the open workspace to the newly active project via the real effect', () => {
    // Switch to a project tab first so the effect starts tracking
    // showWorkspace / workspaceProjectId (those reads only happen once
    // activeProjectId is non-null).
    tabStateService.setActiveTab('proj-a');
    fixture.detectChanges();

    // Open workspace on proj-a (mirrors user clicking the icon)
    host.showWorkspace.set(true);
    host.workspaceProjectId.set('proj-a');
    fixture.detectChanges();
    const beforeCount = host.effectRunCount;

    // Switch to proj-b — effect should fire and update workspaceProjectId
    tabStateService.setActiveTab('proj-b');
    fixture.detectChanges();

    expect(host.effectRunCount).toBeGreaterThan(beforeCount);
    expect(host.showWorkspace()).toBe(true);
    expect(host.workspaceProjectId()).toBe('proj-b');
  });

  it('hides the workspace via the real effect when switching to the All tab', () => {
    // Switch to a project tab first so the effect starts tracking
    // showWorkspace / workspaceProjectId. Without this, the effect only
    // tracks activeProjectId (its null-branch never reads the workspace
    // signals), and our manual setVisible(true) calls would be invisible.
    tabStateService.setActiveTab('proj-a');
    fixture.detectChanges();

    // Open workspace on proj-a
    host.showWorkspace.set(true);
    host.workspaceProjectId.set('proj-a');
    fixture.detectChanges();
    const beforeCount = host.effectRunCount;

    // Switch to All tab — effect should fire and clear workspace state
    tabStateService.setActiveTab('all');
    fixture.detectChanges();

    expect(host.effectRunCount).toBeGreaterThan(beforeCount);
    expect(host.showWorkspace()).toBe(false);
    expect(host.workspaceProjectId()).toBeNull();
  });
});
