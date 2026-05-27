import { signal } from '@angular/core';
import type { Agent, InstanceInfo } from '../../models';

// Mock TabStateService for testing
class MockTabStateService {
  readonly activeProjectId = signal<string | null>(null);
}

// Mock ApiService for testing
const mockApiService = {
  listAgents: jest.fn(),
  createInstance: jest.fn(),
  deleteInstance: jest.fn(),
};

// Testable InstancesComponent (mirrors actual component logic)
class TestableInstancesComponent {
  private readonly api = mockApiService;
  private readonly router: MockRouter;
  protected readonly tabStateService: MockTabStateService;

  readonly agents = signal<Agent[]>([]);
  readonly selectedAgent = signal<Agent | null>(null);

  constructor(tabStateService: MockTabStateService) {
    this.tabStateService = tabStateService;
    this.router = new MockRouter();
  }

  protected getProjectContext(): string {
    return this.tabStateService.activeProjectId() ?? 'all';
  }

  protected onBack(): void {
    this.router.navigate(['/']);
  }

  protected onNewInstance(): void {
    const agent = this.selectedAgent();
    if (!agent) {
      this.router.navigate(['/']);
      return;
    }

    const agentPath = `./agents/${agent.id}`;
    this.api.createInstance(agentPath).subscribe({
      next: (instance: InstanceInfo) => {
        this.router.navigate(['/projects', this.getProjectContext(), 'instances', instance.instance_id]);
      },
      error: () => {}
    });
  }

  protected onTerminateInstance(instanceId: string): void {
    this.api.deleteInstance(instanceId).subscribe({
      next: () => {},
      error: () => {}
    });
  }

  protected onAgentChange(agent: Agent): void {
    this.selectedAgent.set(agent);
    localStorage.setItem('ensemble-next-instance-agent', agent.id);
  }
}

// Mock Router for tracking navigation
class MockRouter {
  navigateCalls: Array<{ path: string[] }> = [];

  navigate(path: string[]): void {
    this.navigateCalls.push({ path });
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

describe('InstancesComponent - Project-Aware Navigation', () => {
  let tabStateService: MockTabStateService;
  let component: TestableInstancesComponent;

  beforeEach(() => {
    tabStateService = new MockTabStateService();
    component = new TestableInstancesComponent(tabStateService);
    jest.clearAllMocks();
  });

  describe('getProjectContext()', () => {
    it('should return "all" when no project is selected (All tab)', () => {
      tabStateService.activeProjectId.set(null);

      expect(component.getProjectContext()).toBe('all');
    });

    it('should return "all" when activeProjectId is null', () => {
      tabStateService.activeProjectId.set(null);

      expect(component.getProjectContext()).toBe('all');
    });

    it('should return project ID when a project is selected', () => {
      tabStateService.activeProjectId.set('instances-project-123');

      expect(component.getProjectContext()).toBe('instances-project-123');
    });
  });

  describe('onNewInstance() - Navigation Point', () => {
    beforeEach(() => {
      const agent = createMockAgent({ id: 'instances-agent' });
      component.agents.set([agent]);
      component.selectedAgent.set(agent);
    });

    it('should navigate to /projects/all/instances/:instanceId when on All tab', () => {
      tabStateService.activeProjectId.set(null);
      const instanceId = 'instances-inst-001';
      mockApiService.createInstance.mockReturnValue({
        subscribe: (handlers: any) => {
          handlers.next(createMockInstance({ instance_id: instanceId }));
          return { unsubscribe: () => {} };
        }
      });

      component.onNewInstance();

      expect(component.router.navigateCalls).toHaveLength(1);
      expect(component.router.navigateCalls[0].path).toEqual(['/projects', 'all', 'instances', instanceId]);
    });

    it('should navigate to /projects/:projectId/instances/:instanceId when project is selected', () => {
      tabStateService.activeProjectId.set('instances-project');
      const instanceId = 'instances-inst-002';
      mockApiService.createInstance.mockReturnValue({
        subscribe: (handlers: any) => {
          handlers.next(createMockInstance({ instance_id: instanceId }));
          return { unsubscribe: () => {} };
        }
      });

      component.onNewInstance();

      expect(component.router.navigateCalls).toHaveLength(1);
      expect(component.router.navigateCalls[0].path).toEqual(['/projects', 'instances-project', 'instances', instanceId]);
    });

    it('should navigate to home when no agent is selected', () => {
      component.selectedAgent.set(null);
      tabStateService.activeProjectId.set(null);

      component.onNewInstance();

      expect(component.router.navigateCalls).toHaveLength(1);
      expect(component.router.navigateCalls[0].path).toEqual(['/']);
    });
  });

  describe('URL Pattern Verification', () => {
    it('should produce correct URL pattern for All tab', () => {
      tabStateService.activeProjectId.set(null);

      // Verify getProjectContext returns 'all'
      expect(component.getProjectContext()).toBe('all');
    });

    it('should produce correct URL pattern for specific project', () => {
      tabStateService.activeProjectId.set('verify-project');

      expect(component.getProjectContext()).toBe('verify-project');
    });

    it('should handle various project ID formats', () => {
      const projectIds = ['proj-abc', 'my_project', 'project-123', 'special-chars-'];

      for (const projectId of projectIds) {
        component.router.navigateCalls = [];
        tabStateService.activeProjectId.set(projectId);

        expect(component.getProjectContext()).toBe(projectId);
      }
    });
  });

  describe('onBack() - Navigation', () => {
    it('should navigate to home', () => {
      component.onBack();

      expect(component.router.navigateCalls).toHaveLength(1);
      expect(component.router.navigateCalls[0].path).toEqual(['/']);
    });
  });

  describe('onTerminateInstance() - No Navigation', () => {
    it('should not navigate after terminating instance', () => {
      mockApiService.deleteInstance.mockReturnValue({
        subscribe: (handlers: any) => {
          handlers.next({});
          return { unsubscribe: () => {} };
        }
      });

      component.onTerminateInstance('term-inst-123');

      expect(component.router.navigateCalls).toHaveLength(0);
    });
  });

  describe('onAgentChange() - Agent Selection', () => {
    it('should update selected agent', () => {
      const agent = createMockAgent({ id: 'change-agent' });

      component.onAgentChange(agent);

      expect(component.selectedAgent()).toEqual(agent);
    });
  });
});
