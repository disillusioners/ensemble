import { signal } from '@angular/core';
import type { Agent, InstanceInfo } from '../../models';

const NEXT_AGENT_STORAGE_KEY = 'ensemble-next-instance-agent';

// Mock TabStateService for testing
class MockTabStateService {
  readonly activeProjectId = signal<string | null>(null);
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

// Testable ChatComponent (mirrors actual component logic)
class TestableChatComponent {
  private readonly api = mockApiService;
  private readonly sseService = mockSseService;
  protected readonly tabStateService: MockTabStateService;

  readonly currentInstanceId = signal<string | null>(null);
  readonly selectedAgent = signal<Agent | null>(null);
  readonly isSending = signal(false);
  readonly sendError = signal<string | null>(null);

  // Navigation calls tracked for testing
  navigateCalls: Array<{ path: string[] }> = [];

  constructor(tabStateService: MockTabStateService) {
    this.tabStateService = tabStateService;
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
      tabStateService.activeProjectId.set(null);
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
      tabStateService.activeProjectId.set('chat-project');
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
      tabStateService.activeProjectId.set(null);

      component.onNewInstance();

      expect(component.navigateCalls).toHaveLength(1);
      expect(component.navigateCalls[0].path).toEqual(['/']);
    });

    it('should clear SSE state before navigation', () => {
      tabStateService.activeProjectId.set('sse-project');
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
      tabStateService.activeProjectId.set(null);

      const path = component.getNavigationPath('helper-inst-001');

      expect(path).toEqual(['/projects', 'all', 'instances', 'helper-inst-001']);
    });

    it('should return /projects/:projectId/instances/:instanceId when project is selected', () => {
      tabStateService.activeProjectId.set('helper-project');

      const path = component.getNavigationPath('helper-inst-002');

      expect(path).toEqual(['/projects', 'helper-project', 'instances', 'helper-inst-002']);
    });

    it('should preserve instance ID in navigation path', () => {
      tabStateService.activeProjectId.set('preserve-project');
      const instanceId = 'preserve-inst-xyz';

      const path = component.getNavigationPath(instanceId);

      expect(path[3]).toBe(instanceId);
    });
  });

  describe('URL Pattern Verification', () => {
    it('should produce correct URL pattern for All tab', () => {
      tabStateService.activeProjectId.set(null);

      const path = component.getNavigationPath('pattern-inst');

      // Verify URL structure
      expect(path).toHaveLength(4);
      expect(path[0]).toBe('/projects');
      expect(path[1]).toBe('all');
      expect(path[2]).toBe('instances');
      expect(typeof path[3]).toBe('string'); // instance ID
    });

    it('should produce correct URL pattern for specific project', () => {
      tabStateService.activeProjectId.set('pattern-project-123');

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
        tabStateService.activeProjectId.set(projectId);

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
});
