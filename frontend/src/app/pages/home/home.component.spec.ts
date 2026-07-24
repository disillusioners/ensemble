import { signal, computed } from '@angular/core';
import type { Agent, InstanceInfo } from '../../models';

const NEXT_AGENT_STORAGE_KEY = 'ensemble-next-instance-agent';

// Mock TabStateService for testing
class MockTabStateService {
  readonly activeProjectId = signal<string | null>(null);
}

// Mock ApiService for testing
const mockApiService = {
  listAgents: jest.fn(),
  listInstances: jest.fn(),
  createInstance: jest.fn(),
  createAgent: jest.fn(),
  deleteAgent: jest.fn(),
};

// Testable HomeComponent (mirrors actual component logic)
class TestableHomeComponent {
  private readonly api = mockApiService;
  private readonly tabStateService: MockTabStateService;

  readonly agents = signal<Agent[]>([]);
  readonly instances = signal<InstanceInfo[]>([]);
  readonly selectedAgent = signal<Agent | null>(null);
  /** Phase 3: the AgentSelector child owns the version tag — each create
   *  event payload carries the tag explicitly. The parent does not
   *  mirror the tag as a separate signal. */
  readonly isLoading = signal(false);

  // Navigation calls tracked for testing
  navigateCalls: Array<{ path: string[] }> = [];

  constructor(tabStateService: MockTabStateService) {
    this.tabStateService = tabStateService;
  }

  protected getProjectContext(): string {
    return this.tabStateService.activeProjectId() ?? 'all';
  }

  protected onSelectAgent(agent: Agent): void {
    this.selectedAgent.set(agent);
    localStorage.setItem(NEXT_AGENT_STORAGE_KEY, agent.id);
  }

  protected onCreateInstance(payload?: { versionTag?: string | null }): void {
    const agent = this.selectedAgent();
    if (!agent) return;

    this.isLoading.set(true);
    const agentPath = `./agents/${agent.id}`;
    // The version tag is sourced entirely from the payload (the child
    // AgentSelector owns the tag via its own version picker).
    const versionTag = payload?.versionTag;

    this.api.createInstance(agentPath, undefined, undefined, versionTag ?? undefined).subscribe({
      next: (instance: InstanceInfo) => {
        this.instances.update(prev => [instance, ...prev]);
        this.navigateCalls.push({
          path: ['/projects', this.getProjectContext(), 'instances', instance.instance_id]
        });
      },
      error: () => {
        this.isLoading.set(false);
      }
    });
  }

  protected onContinueInstance(instanceId: string): void {
    const projectId = this.getProjectContext();
    if (instanceId === 'latest' && this.instances().length > 0) {
      this.navigateCalls.push({
        path: ['/projects', projectId, 'instances', this.instances()[0].instance_id]
      });
    } else if (instanceId !== 'latest') {
      this.navigateCalls.push({
        path: ['/projects', projectId, 'instances', instanceId]
      });
    }
  }

  protected onAddAgent(agentCreate: { name: string; description: string }): void {
    this.api.createAgent(agentCreate).subscribe({
      next: (newAgent: Agent) => {
        this.agents.update(prev => [...prev, newAgent]);
      },
      error: () => {}
    });
  }

  protected onDeleteAgent(agentId: string): void {
    this.api.deleteAgent(agentId).subscribe({
      next: () => {
        this.agents.update(prev => prev.filter(a => a.id !== agentId));
        if (this.selectedAgent()?.id === agentId) {
          this.selectedAgent.set(null);
          localStorage.removeItem(NEXT_AGENT_STORAGE_KEY);
        }
      },
      error: () => {}
    });
  }

  protected onStartMother(): void {
    this.isLoading.set(true);
    const agentPath = './agents/_mother';

    this.api.createInstance(agentPath).subscribe({
      next: (instance: InstanceInfo) => {
        this.instances.update(prev => [instance, ...prev]);
        this.navigateCalls.push({
          path: ['/projects', this.getProjectContext(), 'instances', instance.instance_id]
        });
      },
      error: () => {
        this.isLoading.set(false);
      }
    });
  }

  protected onQuickCreateInstance(payload: { agent: Agent; versionTag?: string | null }): void {
    this.isLoading.set(true);
    const agentPath = `./agents/${payload.agent.id}`;
    // The child AgentSelector forwards the chosen version tag explicitly.
    // No fallback to the agent object's own version_tag — that field is
    // only the tag of the row the user clicked, not the version they
    // picked in the picker, and would silently create the wrong version.
    const tag = payload.versionTag ?? null;

    this.api.createInstance(agentPath, undefined, undefined, tag ?? undefined).subscribe({
      next: (instance: InstanceInfo) => {
        this.instances.update(prev => [instance, ...prev]);
        this.navigateCalls.push({
          path: ['/projects', this.getProjectContext(), 'instances', instance.instance_id]
        });
      },
      error: () => {
        this.isLoading.set(false);
      }
    });
  }

  protected onViewInstances(): void {
    if (this.instances().length > 0) {
      this.navigateCalls.push({
        path: ['/projects', this.getProjectContext(), 'instances', this.instances()[0].instance_id]
      });
    }
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

describe('HomeComponent - Project-Aware Navigation', () => {
  let tabStateService: MockTabStateService;
  let component: TestableHomeComponent;

  beforeEach(() => {
    tabStateService = new MockTabStateService();
    component = new TestableHomeComponent(tabStateService);
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
      tabStateService.activeProjectId.set('project-123');

      expect(component.getProjectContext()).toBe('project-123');
    });

    it('should return specific project ID correctly', () => {
      tabStateService.activeProjectId.set('my-awesome-project');

      expect(component.getProjectContext()).toBe('my-awesome-project');
    });
  });

  describe('Navigation URL Pattern', () => {
    it('should navigate to /projects/all/instances/:instanceId when on All tab', () => {
      tabStateService.activeProjectId.set(null);
      const instance = createMockInstance({ instance_id: 'inst-001' });
      component.instances.set([instance]);

      component.onViewInstances();

      expect(component.navigateCalls).toHaveLength(1);
      expect(component.navigateCalls[0].path).toEqual(['/projects', 'all', 'instances', 'inst-001']);
    });

    it('should navigate to /projects/:projectId/instances/:instanceId when project is selected', () => {
      tabStateService.activeProjectId.set('project-abc');
      const instance = createMockInstance({ instance_id: 'inst-002' });
      component.instances.set([instance]);

      component.onViewInstances();

      expect(component.navigateCalls).toHaveLength(1);
      expect(component.navigateCalls[0].path).toEqual(['/projects', 'project-abc', 'instances', 'inst-002']);
    });
  });

  describe('onCreateInstance() - Navigation Point 1', () => {
    beforeEach(() => {
      const agent = createMockAgent({ id: 'my-agent' });
      component.agents.set([agent]);
      component.selectedAgent.set(agent);
    });

    it('should navigate to /projects/all/instances/:instanceId when on All tab', () => {
      tabStateService.activeProjectId.set(null);
      const instanceId = 'create-inst-001';
      mockApiService.createInstance.mockReturnValue({
        subscribe: (handlers: any) => {
          handlers.next(createMockInstance({ instance_id: instanceId }));
          return { unsubscribe: () => {} };
        }
      });

      component.onCreateInstance();

      expect(component.navigateCalls).toHaveLength(1);
      expect(component.navigateCalls[0].path).toEqual(['/projects', 'all', 'instances', instanceId]);
    });

    it('should navigate to /projects/:projectId/instances/:instanceId when project is selected', () => {
      tabStateService.activeProjectId.set('my-project');
      const instanceId = 'create-inst-002';
      mockApiService.createInstance.mockReturnValue({
        subscribe: (handlers: any) => {
          handlers.next(createMockInstance({ instance_id: instanceId }));
          return { unsubscribe: () => {} };
        }
      });

      component.onCreateInstance();

      expect(component.navigateCalls).toHaveLength(1);
      expect(component.navigateCalls[0].path).toEqual(['/projects', 'my-project', 'instances', instanceId]);
    });

    it('should not navigate when no agent is selected', () => {
      component.selectedAgent.set(null);

      component.onCreateInstance();

      expect(component.navigateCalls).toHaveLength(0);
    });
  });

  // ── Phase 3: version_tag threading (C3) ─────────────────────────────────
  describe('onCreateInstance() - version_tag forwarding', () => {
    beforeEach(() => {
      const agent = createMockAgent({ id: 'my-agent' });
      component.agents.set([agent]);
      component.selectedAgent.set(agent);
      mockApiService.createInstance.mockReturnValue({
        subscribe: (handlers: any) => {
          handlers.next(createMockInstance({ instance_id: 'inst' }));
          return { unsubscribe: () => {} };
        }
      });
    });

    it('forwards the version_tag from the payload as the 4th arg to createInstance', () => {
      component.onCreateInstance({ versionTag: 'v2' });

      expect(mockApiService.createInstance).toHaveBeenCalledWith(
        './agents/my-agent',
        undefined,
        undefined,
        'v2',
      );
    });

    it('forwards undefined when the payload omits versionTag (W3: no parent fallback)', () => {
      component.onCreateInstance();

      expect(mockApiService.createInstance).toHaveBeenCalledWith(
        './agents/my-agent',
        undefined,
        undefined,
        undefined,
      );
    });

    it('forwards undefined when the payload omits versionTag even after a previous tag was picked', () => {
      // W3: the parent no longer mirrors the version tag as a state
      // signal, so a stale value cannot leak into the next create call.
      component.onCreateInstance({ versionTag: 'v2' });
      expect(mockApiService.createInstance).toHaveBeenLastCalledWith(
        './agents/my-agent',
        undefined,
        undefined,
        'v2',
      );

      component.onCreateInstance();
      expect(mockApiService.createInstance).toHaveBeenLastCalledWith(
        './agents/my-agent',
        undefined,
        undefined,
        undefined,
      );
    });
  });

  describe('onQuickCreateInstance() - version_tag forwarding', () => {
    it('forwards the versionTag from the payload as the 4th arg to createInstance', () => {
      const agent = createMockAgent({ id: 'q-agent', version_tag: 'v3' } as Agent);
      mockApiService.createInstance.mockReturnValue({
        subscribe: (handlers: any) => {
          handlers.next(createMockInstance({ instance_id: 'q-inst' }));
          return { unsubscribe: () => {} };
        }
      });

      component.onQuickCreateInstance({ agent, versionTag: 'v2' });

      expect(mockApiService.createInstance).toHaveBeenCalledWith(
        './agents/q-agent',
        undefined,
        undefined,
        'v2',
      );
    });

    it('ignores agent.version_tag — only the payload versionTag is forwarded (W1)', () => {
      // W1: the original spec forwarded agent.version_tag as a fallback.
      // The real AgentSelector child owns the version picker and emits
      // the tag explicitly, so the row's own version_tag is no longer a
      // source of truth. The parent must take the tag from the payload.
      const agent = createMockAgent({ id: 'q-agent', version_tag: 'v3' } as Agent);
      mockApiService.createInstance.mockReturnValue({
        subscribe: (handlers: any) => {
          handlers.next(createMockInstance({ instance_id: 'q-inst' }));
          return { unsubscribe: () => {} };
        }
      });

      component.onQuickCreateInstance({ agent });

      expect(mockApiService.createInstance).toHaveBeenCalledWith(
        './agents/q-agent',
        undefined,
        undefined,
        undefined,
      );
    });

    it('passes undefined when neither payload tag nor fallback is set', () => {
      const agent = createMockAgent({ id: 'q-agent' });
      mockApiService.createInstance.mockReturnValue({
        subscribe: (handlers: any) => {
          handlers.next(createMockInstance({ instance_id: 'q-inst' }));
          return { unsubscribe: () => {} };
        }
      });

      component.onQuickCreateInstance({ agent });

      expect(mockApiService.createInstance).toHaveBeenCalledWith(
        './agents/q-agent',
        undefined,
        undefined,
        undefined,
      );
    });
  });

  describe('onSelectAgent() - selection update', () => {
    it('updates selectedAgent and persists the new id; no version-tag state to reset (W3)', () => {
      const a = createMockAgent({ id: 'a' });
      const b = createMockAgent({ id: 'b' });
      component.selectedAgent.set(a);

      component.onSelectAgent(b);

      expect(component.selectedAgent()).toBe(b);
    });
  });

  describe('onContinueInstance() - Navigation Points 2 & 3', () => {
    beforeEach(() => {
      const instance = createMockInstance({ instance_id: 'existing-inst' });
      component.instances.set([instance]);
    });

    it('should navigate to /projects/all/instances/:instanceId for "latest" on All tab', () => {
      tabStateService.activeProjectId.set(null);

      component.onContinueInstance('latest');

      expect(component.navigateCalls).toHaveLength(1);
      expect(component.navigateCalls[0].path[0]).toBe('/projects');
      expect(component.navigateCalls[0].path[1]).toBe('all');
      expect(component.navigateCalls[0].path[2]).toBe('instances');
      expect(component.navigateCalls[0].path[3]).toBe('existing-inst');
    });

    it('should navigate to /projects/all/instances/:instanceId for specific instance on All tab', () => {
      tabStateService.activeProjectId.set(null);

      component.onContinueInstance('specific-inst-123');

      expect(component.navigateCalls).toHaveLength(1);
      expect(component.navigateCalls[0].path).toEqual(['/projects', 'all', 'instances', 'specific-inst-123']);
    });

    it('should navigate to /projects/:projectId/instances/:instanceId for "latest" on project tab', () => {
      tabStateService.activeProjectId.set('active-project');

      component.onContinueInstance('latest');

      expect(component.navigateCalls).toHaveLength(1);
      expect(component.navigateCalls[0].path).toEqual(['/projects', 'active-project', 'instances', 'existing-inst']);
    });

    it('should navigate to /projects/:projectId/instances/:instanceId for specific instance on project tab', () => {
      tabStateService.activeProjectId.set('active-project');

      component.onContinueInstance('specific-inst-456');

      expect(component.navigateCalls).toHaveLength(1);
      expect(component.navigateCalls[0].path).toEqual(['/projects', 'active-project', 'instances', 'specific-inst-456']);
    });

    it('should not navigate for invalid "latest" when no instances exist', () => {
      component.instances.set([]);
      tabStateService.activeProjectId.set(null);

      component.onContinueInstance('latest');

      expect(component.navigateCalls).toHaveLength(0);
    });
  });

  describe('onStartMother() - Navigation Point 4', () => {
    it('should navigate to /projects/all/instances/:instanceId when on All tab', () => {
      tabStateService.activeProjectId.set(null);
      const instanceId = 'mother-inst-001';
      mockApiService.createInstance.mockReturnValue({
        subscribe: (handlers: any) => {
          handlers.next(createMockInstance({ instance_id: instanceId }));
          return { unsubscribe: () => {} };
        }
      });

      component.onStartMother();

      expect(component.navigateCalls).toHaveLength(1);
      expect(component.navigateCalls[0].path).toEqual(['/projects', 'all', 'instances', instanceId]);
    });

    it('should navigate to /projects/:projectId/instances/:instanceId when project is selected', () => {
      tabStateService.activeProjectId.set('mother-project');
      const instanceId = 'mother-inst-002';
      mockApiService.createInstance.mockReturnValue({
        subscribe: (handlers: any) => {
          handlers.next(createMockInstance({ instance_id: instanceId }));
          return { unsubscribe: () => {} };
        }
      });

      component.onStartMother();

      expect(component.navigateCalls).toHaveLength(1);
      expect(component.navigateCalls[0].path).toEqual(['/projects', 'mother-project', 'instances', instanceId]);
    });
  });

  describe('onQuickCreateInstance() - Navigation Point 5', () => {
    it('should navigate to /projects/all/instances/:instanceId when on All tab', () => {
      const agent = createMockAgent({ id: 'quick-agent' });
      component.agents.set([agent]);
      tabStateService.activeProjectId.set(null);
      const instanceId = 'quick-inst-001';
      mockApiService.createInstance.mockReturnValue({
        subscribe: (handlers: any) => {
          handlers.next(createMockInstance({ instance_id: instanceId }));
          return { unsubscribe: () => {} };
        }
      });

      component.onQuickCreateInstance({ agent });

      expect(component.navigateCalls).toHaveLength(1);
      expect(component.navigateCalls[0].path).toEqual(['/projects', 'all', 'instances', instanceId]);
    });

    it('should navigate to /projects/:projectId/instances/:instanceId when project is selected', () => {
      const agent = createMockAgent({ id: 'quick-agent' });
      component.agents.set([agent]);
      tabStateService.activeProjectId.set('quick-project');
      const instanceId = 'quick-inst-002';
      mockApiService.createInstance.mockReturnValue({
        subscribe: (handlers: any) => {
          handlers.next(createMockInstance({ instance_id: instanceId }));
          return { unsubscribe: () => {} };
        }
      });

      component.onQuickCreateInstance({ agent });

      expect(component.navigateCalls).toHaveLength(1);
      expect(component.navigateCalls[0].path).toEqual(['/projects', 'quick-project', 'instances', instanceId]);
    });
  });

  describe('onViewInstances() - Navigation Point 6', () => {
    beforeEach(() => {
      const instance = createMockInstance({ instance_id: 'view-inst' });
      component.instances.set([instance]);
    });

    it('should navigate to /projects/all/instances/:instanceId when on All tab', () => {
      tabStateService.activeProjectId.set(null);

      component.onViewInstances();

      expect(component.navigateCalls).toHaveLength(1);
      expect(component.navigateCalls[0].path).toEqual(['/projects', 'all', 'instances', 'view-inst']);
    });

    it('should navigate to /projects/:projectId/instances/:instanceId when project is selected', () => {
      tabStateService.activeProjectId.set('view-project');

      component.onViewInstances();

      expect(component.navigateCalls).toHaveLength(1);
      expect(component.navigateCalls[0].path).toEqual(['/projects', 'view-project', 'instances', 'view-inst']);
    });

    it('should not navigate when no instances exist', () => {
      component.instances.set([]);
      tabStateService.activeProjectId.set(null);

      component.onViewInstances();

      expect(component.navigateCalls).toHaveLength(0);
    });
  });

  describe('All 6 Navigation Points - URL Verification', () => {
    const testProjectId = 'test-project-xyz';

    beforeEach(() => {
      tabStateService.activeProjectId.set(testProjectId);
    });

    it('should produce correct URL pattern for all navigation methods', () => {
      const instance = createMockInstance({ instance_id: 'all-nav-inst' });
      component.instances.set([instance]);

      // onContinueInstance with specific ID
      component.onContinueInstance('specific-inst');
      expect(component.navigateCalls[component.navigateCalls.length - 1].path).toEqual(
        ['/projects', testProjectId, 'instances', 'specific-inst']
      );
    });

    it('should correctly format navigation path array', () => {
      const agent = createMockAgent({ id: 'fmt-agent' });
      component.selectedAgent.set(agent);
      component.agents.set([agent]);
      tabStateService.activeProjectId.set('my-project');
      const instanceId = 'fmt-inst-123';

      mockApiService.createInstance.mockReturnValue({
        subscribe: (handlers: any) => {
          handlers.next(createMockInstance({ instance_id: instanceId }));
          return { unsubscribe: () => {} };
        }
      });

      component.onCreateInstance();

      const navPath = component.navigateCalls[0].path;
      // Verify the path structure
      expect(navPath).toHaveLength(4);
      expect(navPath[0]).toBe('/projects');
      expect(navPath[1]).toBe('my-project');
      expect(navPath[2]).toBe('instances');
      expect(navPath[3]).toBe(instanceId);
    });
  });
});
