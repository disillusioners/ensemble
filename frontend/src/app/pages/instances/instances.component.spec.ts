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

// Mock InstancesViewStateService for W1 termination tests. Mirrors the
// subset the production InstancesComponent uses: ``clearInstance(id)``.
// Tests pass it explicitly so the W1 call site is observable.
class MockInstancesViewState {
  clearInstanceCalls: string[] = [];
  detailVisible = signal(false);
  activeInstanceId = signal<string | null>(null);
  activeProjectId = signal<string>('all');

  clearInstance(instanceId: string): void {
    this.clearInstanceCalls.push(instanceId);
    if (this.activeInstanceId() !== instanceId) return;
    this.activeInstanceId.set(null);
    this.detailVisible.set(false);
  }
}

// Testable InstancesComponent (mirrors actual component logic)
class TestableInstancesComponent {
  private readonly api = mockApiService;
  private readonly router: MockRouter;
  protected readonly tabStateService: MockTabStateService;
  private readonly viewState: MockInstancesViewState;

  readonly agents = signal<Agent[]>([]);
  readonly selectedAgent = signal<Agent | null>(null);

  constructor(
    tabStateService: MockTabStateService,
    viewState: MockInstancesViewState = new MockInstancesViewState(),
  ) {
    this.tabStateService = tabStateService;
    this.router = new MockRouter();
    this.viewState = viewState;
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
      next: () => {
        // W1: drop the cached id from the view-state service so the
        // "Instances" nav link never restores a dead instance. The
        // service is a no-op when the terminated id doesn't match the
        // current cache, so calling it for unrelated rows is safe.
        this.viewState.clearInstance(instanceId);
      },
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

    // W1 — clearInstance() must fire after a successful terminate
    // from the instances list page so a dead id is never restored via
    // the "Instances" nav link. Mirrors the same call site in the
    // chat component and the instance-delete dialog.
    it('W1: clears the cached instance id from the view-state service', () => {
      mockApiService.deleteInstance.mockReturnValue({
        subscribe: (handlers: any) => {
          handlers.next({});
          return { unsubscribe: () => {} };
        }
      });

      component.onTerminateInstance('term-inst-clear');

      expect(component.viewState.clearInstanceCalls).toContain('term-inst-clear');
    });

    it('W1: clearInstance fires even when terminating a non-active instance (idempotent no-op on the service side)', () => {
      // The view-state service itself is a no-op for non-matching ids,
      // but the component must wire the call regardless so the
      // matching-id path is exercised.
      mockApiService.deleteInstance.mockReturnValue({
        subscribe: (handlers: any) => {
          handlers.next({});
          return { unsubscribe: () => {} };
        }
      });

      component.viewState.activeInstanceId.set('current-inst');
      component.onTerminateInstance('unrelated-inst');

      expect(component.viewState.clearInstanceCalls).toContain('unrelated-inst');
      // The non-matching id must NOT clobber the cached current instance.
      expect(component.viewState.activeInstanceId()).toBe('current-inst');
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
