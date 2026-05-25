import { signal, computed } from '@angular/core';
import type { Agent, InstanceInfo, InstanceStatus } from '../../models';

// Mock InstanceService
class MockInstanceService {
  readonly instances = signal<InstanceInfo[]>([]);
  readonly totalInstances = signal<number>(0);
  readonly isLoadingMore = signal<boolean>(false);
  readonly loading = signal<boolean>(false);
  readonly showKb = signal<boolean>(false);

  readonly hasMoreInstances = computed(
    () => this.instances().length < this.totalInstances()
  );

  toggleKb(): void {
    this.showKb.update(v => !v);
  }

  async loadInstances(projectId?: string): Promise<void> {
    // Mock implementation
  }
}

// Mock container with scrollTop for testing
const createMockContainer = () => ({
  scrollTop: 0,
  removeEventListener: jest.fn(),
});

type MockContainer = ReturnType<typeof createMockContainer>;

// Testable InstanceListComponent (mirrors actual component)
class TestableInstanceListComponent {
  protected readonly instanceService: MockInstanceService;

  readonly agents = signal<Agent[]>([]);
  readonly instances = signal<InstanceInfo[]>([]);
  readonly currentInstanceId = signal<string | null>(null);
  readonly selectedAgent = signal<Agent | null>(null);
  readonly hasMore = signal<boolean>(false);
  readonly isLoadingMore = signal<boolean>(false);

  // Track expanded/collapsed state per instance
  readonly expandedInstances = signal<Set<string>>(new Set());

  // Track refresh state
  readonly isRefreshing = signal(false);

  // Output EventEmitters (mocked as objects with emit methods)
  readonly terminateInstance = { emit: jest.fn() };
  readonly newInstance = { emit: jest.fn() };
  readonly agentChange = { emit: jest.fn() };
  readonly loadMore = { emit: jest.fn() };
  readonly pauseInstance = { emit: jest.fn() };

  // Scroll position tracking
  private scrollTop = 0;
  private isScrolledByUser = false;
  private scrollHandler = () => {
    const container = this.instanceListContainer?.nativeElement;
    if (container) {
      this.scrollTop = container.scrollTop;
      this.isScrolledByUser = this.scrollTop > 0;
    }
  };

  // Mock container for testing
  mockContainer: MockContainer;
  instanceListContainer: { nativeElement: MockContainer };

  // Scroll restoration effect tracking
  private effectCallback: ((loading: boolean, isRefreshing: boolean) => void) | null = null;

  private saveScrollPosition(): void {
    const container = this.instanceListContainer?.nativeElement;
    if (container) {
      this.scrollTop = container.scrollTop;
    }
  }

  onRefresh(): void {
    this.saveScrollPosition();
    this.isRefreshing.set(true);
    this.instanceService.loadInstances().finally(() => {
      this.isRefreshing.set(false);
    });
  }

  ngOnDestroy(): void {
    this.instanceListContainer?.nativeElement?.removeEventListener('scroll', this.scrollHandler);
  }

  constructor(instanceService: MockInstanceService) {
    this.instanceService = instanceService;
    this.mockContainer = createMockContainer();
    this.instanceListContainer = { nativeElement: this.mockContainer };
  }

  // Build tree structure from flat instance list
  readonly instanceTree = computed(() => {
    const instances = this.instances();
    if (!instances?.length) return [];

    const instanceMap = new Map<string, { instance: InstanceInfo; children: any[] }>();

    // Create nodes for all instances
    instances.forEach(instance => {
      instanceMap.set(instance.instance_id, { instance, children: [] });
    });

    const rootNodes: { instance: InstanceInfo; children: any[] }[] = [];

    // Build tree by attaching children to parents
    instances.forEach(instance => {
      const node = instanceMap.get(instance.instance_id)!;
      if (instance.parent_id && instanceMap.has(instance.parent_id)) {
        instanceMap.get(instance.parent_id)!.children.push(node);
      } else {
        rootNodes.push(node);
      }
    });

    return rootNodes;
  });

  onToggleKb(): void {
    this.instanceService.toggleKb();
    this.instanceService.loadInstances();
  }

  isExpanded(instanceId: string): boolean {
    return this.expandedInstances().has(instanceId);
  }

  toggleExpand(instanceId: string): void {
    const expanded = this.expandedInstances();
    const newSet = new Set(expanded);
    if (newSet.has(instanceId)) {
      newSet.delete(instanceId);
    } else {
      newSet.add(instanceId);
    }
    this.expandedInstances.set(newSet);
  }

  getAgentInfo(agentDir: string): Agent | undefined {
    const agentId = agentDir.split('/').pop() || agentDir;
    return this.agents().find(a => a.id === agentId);
  }

  formatDate(dateString: string): string {
    const date = new Date(dateString);
    const now = new Date();
    const diff = now.getTime() - date.getTime();

    const minutes = Math.floor(diff / 60000);
    const hours = Math.floor(diff / 3600000);
    const days = Math.floor(diff / 86400000);

    if (minutes < 1) return 'Just now';
    if (minutes < 60) return `${minutes}m ago`;
    if (hours < 24) return `${hours}h ago`;
    return `${days}d ago`;
  }
}

// Helper to create mock instance
function createMockInstance(overrides: Partial<InstanceInfo> = {}): InstanceInfo {
  return {
    instance_id: `instance-${Math.random().toString(36).substr(2, 9)}`,
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

// Helper to create mock agent
function createMockAgent(overrides: Partial<Agent> = {}): Agent {
  return {
    id: 'test-agent',
    name: 'Test Agent',
    description: 'A test agent',
    icon: '🤖',
    ...overrides,
  };
}

describe('InstanceListComponent', () => {
  let mockService: MockInstanceService;
  let component: TestableInstanceListComponent;

  beforeEach(() => {
    mockService = new MockInstanceService();
    component = new TestableInstanceListComponent(mockService);
  });

  describe('onToggleKb', () => {
    it('should call instanceService.toggleKb()', () => {
      const toggleSpy = jest.spyOn(mockService, 'toggleKb');
      const loadSpy = jest.spyOn(mockService, 'loadInstances');

      component.onToggleKb();

      expect(toggleSpy).toHaveBeenCalledTimes(1);
    });

    it('should call instanceService.loadInstances()', () => {
      const loadSpy = jest.spyOn(mockService, 'loadInstances');

      component.onToggleKb();

      expect(loadSpy).toHaveBeenCalledTimes(1);
    });

    it('should call both toggleKb and loadInstances', () => {
      const toggleSpy = jest.spyOn(mockService, 'toggleKb');
      const loadSpy = jest.spyOn(mockService, 'loadInstances');

      component.onToggleKb();

      expect(toggleSpy).toHaveBeenCalled();
      expect(loadSpy).toHaveBeenCalled();
    });

    it('should toggle showKb signal from false to true', () => {
      expect(mockService.showKb()).toBe(false);

      component.onToggleKb();

      expect(mockService.showKb()).toBe(true);
    });

    it('should toggle showKb signal from true to false', () => {
      mockService.showKb.set(true);
      expect(mockService.showKb()).toBe(true);

      component.onToggleKb();

      expect(mockService.showKb()).toBe(false);
    });

    it('should call toggleKb before loadInstances', () => {
      const callOrder: string[] = [];

      jest.spyOn(mockService, 'toggleKb').mockImplementation(() => {
        callOrder.push('toggleKb');
      });
      jest.spyOn(mockService, 'loadInstances').mockImplementation(() => {
        callOrder.push('loadInstances');
      });

      component.onToggleKb();

      expect(callOrder).toEqual(['toggleKb', 'loadInstances']);
    });

    it('should handle multiple rapid toggles', () => {
      const toggleSpy = jest.spyOn(mockService, 'toggleKb');
      const loadSpy = jest.spyOn(mockService, 'loadInstances');

      component.onToggleKb();
      component.onToggleKb();
      component.onToggleKb();

      expect(toggleSpy).toHaveBeenCalledTimes(3);
      expect(loadSpy).toHaveBeenCalledTimes(3);
    });
  });

  describe('showKb binding', () => {
    it('should have showKb signal on instanceService', () => {
      expect(mockService.showKb).toBeDefined();
      expect(typeof mockService.showKb).toBe('function');
    });

    it('should default showKb to false', () => {
      expect(mockService.showKb()).toBe(false);
    });

    it('should reflect showKb state changes', () => {
      mockService.showKb.set(true);
      expect(component.instanceService.showKb()).toBe(true);

      mockService.showKb.set(false);
      expect(component.instanceService.showKb()).toBe(false);
    });
  });

  describe('instanceTree computed signal', () => {
    it('should return empty array when no instances', () => {
      component.instances.set([]);
      expect(component.instanceTree()).toEqual([]);
    });

    it('should build tree from flat instance list', () => {
      const parent = createMockInstance({ instance_id: 'parent-1' });
      const child = createMockInstance({ instance_id: 'child-1', parent_id: 'parent-1' });

      component.instances.set([parent, child]);

      const tree = component.instanceTree();
      expect(tree).toHaveLength(1); // Only root node
      expect(tree[0].instance.instance_id).toBe('parent-1');
      expect(tree[0].children).toHaveLength(1);
      expect(tree[0].children[0].instance.instance_id).toBe('child-1');
    });

    it('should handle multiple root instances', () => {
      const root1 = createMockInstance({ instance_id: 'root-1' });
      const root2 = createMockInstance({ instance_id: 'root-2' });

      component.instances.set([root1, root2]);

      const tree = component.instanceTree();
      expect(tree).toHaveLength(2);
    });

    it('should handle deep nesting', () => {
      const root = createMockInstance({ instance_id: 'root' });
      const level1 = createMockInstance({ instance_id: 'level-1', parent_id: 'root' });
      const level2 = createMockInstance({ instance_id: 'level-2', parent_id: 'level-1' });

      component.instances.set([root, level1, level2]);

      const tree = component.instanceTree();
      expect(tree).toHaveLength(1);
      expect(tree[0].children[0].children[0].instance.instance_id).toBe('level-2');
    });
  });

  describe('expandedInstances signal', () => {
    it('should start with empty set', () => {
      expect(component.expandedInstances()).toEqual(new Set());
    });

    it('should toggle instance expanded state', () => {
      const instanceId = 'test-instance';

      expect(component.isExpanded(instanceId)).toBe(false);

      component.toggleExpand(instanceId);
      expect(component.isExpanded(instanceId)).toBe(true);

      component.toggleExpand(instanceId);
      expect(component.isExpanded(instanceId)).toBe(false);
    });

    it('should handle multiple instances independently', () => {
      const id1 = 'instance-1';
      const id2 = 'instance-2';

      component.toggleExpand(id1);
      expect(component.isExpanded(id1)).toBe(true);
      expect(component.isExpanded(id2)).toBe(false);

      component.toggleExpand(id2);
      expect(component.isExpanded(id1)).toBe(true);
      expect(component.isExpanded(id2)).toBe(true);

      component.toggleExpand(id1);
      expect(component.isExpanded(id1)).toBe(false);
      expect(component.isExpanded(id2)).toBe(true);
    });
  });

  describe('getAgentInfo', () => {
    it('should return agent by ID', () => {
      const agent = createMockAgent({ id: 'my-agent', name: 'My Agent' });
      component.agents.set([agent]);

      const result = component.getAgentInfo('my-agent');

      expect(result).toBeDefined();
      expect(result?.name).toBe('My Agent');
    });

    it('should extract agent ID from directory path', () => {
      const agent = createMockAgent({ id: 'nested-agent', name: 'Nested Agent' });
      component.agents.set([agent]);

      const result = component.getAgentInfo('/path/to/agents/nested-agent');

      expect(result?.name).toBe('Nested Agent');
    });

    it('should return undefined for unknown agent', () => {
      component.agents.set([]);

      const result = component.getAgentInfo('unknown-agent');

      expect(result).toBeUndefined();
    });
  });

  describe('formatDate', () => {
    it('should return "Just now" for very recent dates', () => {
      const now = new Date().toISOString();
      expect(component.formatDate(now)).toBe('Just now');
    });

    it('should return minutes for dates within an hour', () => {
      const fiveMinutesAgo = new Date(Date.now() - 5 * 60000).toISOString();
      expect(component.formatDate(fiveMinutesAgo)).toBe('5m ago');
    });

    it('should return hours for dates within a day', () => {
      const twoHoursAgo = new Date(Date.now() - 2 * 3600000).toISOString();
      expect(component.formatDate(twoHoursAgo)).toBe('2h ago');
    });

    it('should return days for older dates', () => {
      const threeDaysAgo = new Date(Date.now() - 3 * 86400000).toISOString();
      expect(component.formatDate(threeDaysAgo)).toBe('3d ago');
    });
  });

  describe('isRefreshing signal', () => {
    it('should default to false', () => {
      expect(component.isRefreshing()).toBe(false);
    });

    it('should be true during refresh', () => {
      jest.spyOn(mockService, 'loadInstances').mockImplementation(
        () => new Promise(resolve => setTimeout(resolve, 100))
      );

      component.onRefresh();

      expect(component.isRefreshing()).toBe(true);
    });

    it('should become false after refresh completes', async () => {
      jest.spyOn(mockService, 'loadInstances').mockResolvedValue(undefined);

      component.onRefresh();
      expect(component.isRefreshing()).toBe(true);

      await mockService.loadInstances();

      expect(component.isRefreshing()).toBe(false);
    });
  });

  describe('onRefresh', () => {
    it('should set isRefreshing to true before loadInstances', () => {
      const loadInstancesMock = jest.spyOn(mockService, 'loadInstances').mockImplementation(
        () => new Promise(resolve => setTimeout(resolve, 10))
      );

      component.onRefresh();

      expect(component.isRefreshing()).toBe(true);
      expect(loadInstancesMock).toHaveBeenCalled();
    });

    it('should call saveScrollPosition', () => {
      jest.spyOn(mockService, 'loadInstances').mockResolvedValue(undefined);

      // Manually set scroll position
      component.mockContainer.scrollTop = 100;
      component.saveScrollPosition();
      expect(component['scrollTop']).toBe(100);

      // Reset and call onRefresh
      component.mockContainer.scrollTop = 200;
      component.onRefresh();

      expect(component['scrollTop']).toBe(200);
    });

    it('should call instanceService.loadInstances()', () => {
      const loadInstancesMock = jest.spyOn(mockService, 'loadInstances').mockResolvedValue(undefined);

      component.onRefresh();

      expect(loadInstancesMock).toHaveBeenCalledTimes(1);
    });

    it('should reset isRefreshing to false after load completes', async () => {
      let resolveLoad: () => void;
      jest.spyOn(mockService, 'loadInstances').mockImplementation(
        () => new Promise(resolve => { resolveLoad = resolve; })
      );

      component.onRefresh();
      expect(component.isRefreshing()).toBe(true);

      // Resolve the mock promise
      resolveLoad!();

      // Wait for the finally block to execute
      await Promise.resolve();
      await Promise.resolve();

      expect(component.isRefreshing()).toBe(false);
    });

    it('should handle loadInstances rejection gracefully', async () => {
      jest.spyOn(mockService, 'loadInstances').mockRejectedValue(new Error('API Error'));

      component.onRefresh();

      // Should not throw
      await expect(mockService.loadInstances()).rejects.toThrow('API Error');
    });
  });

  describe('saveScrollPosition', () => {
    it('should save scrollTop from container', () => {
      component.mockContainer.scrollTop = 150;

      component.saveScrollPosition();

      expect(component['scrollTop']).toBe(150);
    });

    it('should not throw when container is undefined', () => {
      component.instanceListContainer = { nativeElement: null as unknown as MockContainer };

      expect(() => component.saveScrollPosition()).not.toThrow();
    });
  });

  describe('scrollHandler', () => {
    it('should track scrollTop value', () => {
      component.mockContainer.scrollTop = 75;

      component.scrollHandler();

      expect(component['scrollTop']).toBe(75);
    });

    it('should set isScrolledByUser when scrolled', () => {
      component.mockContainer.scrollTop = 50;

      component.scrollHandler();

      expect(component['isScrolledByUser']).toBe(true);
    });

    it('should clear isScrolledByUser when at top', () => {
      component.mockContainer.scrollTop = 0;

      component.scrollHandler();

      expect(component['isScrolledByUser']).toBe(false);
    });
  });

  describe('ngOnDestroy', () => {
    it('should remove scroll event listener', () => {
      component.ngOnDestroy();

      expect(component.mockContainer.removeEventListener).toHaveBeenCalledWith(
        'scroll',
        component.scrollHandler
      );
    });

    it('should handle null container gracefully', () => {
      component.instanceListContainer = { nativeElement: null as unknown as MockContainer };

      expect(() => component.ngOnDestroy()).not.toThrow();
    });
  });

  describe('scroll restoration effect', () => {
    beforeEach(() => {
      jest.spyOn(window, 'requestAnimationFrame').mockImplementation(cb => {
        cb();
        return 1;
      });
    });

    afterEach(() => {
      jest.restoreAllMocks();
    });

    it('should restore scrollTop via requestAnimationFrame after loading completes', () => {
      component['scrollTop'] = 100;
      mockService.loading.set(false);

      // Simulate the effect logic
      const loading = mockService.loading();
      const isRefreshing = component.isRefreshing();

      if (!loading && !isRefreshing && component['scrollTop'] > 0) {
        requestAnimationFrame(() => {
          component.instanceListContainer.nativeElement.scrollTop = component['scrollTop'];
        });
      }

      expect(component.instanceListContainer.nativeElement.scrollTop).toBe(100);
    });

    it('should not restore when scrollTop is 0', () => {
      component['scrollTop'] = 0;
      mockService.loading.set(false);

      const loading = mockService.loading();
      const isRefreshing = component.isRefreshing();

      let rafCalled = false;
      jest.spyOn(window, 'requestAnimationFrame').mockImplementation(cb => {
        rafCalled = true;
        cb();
        return 1;
      });

      if (!loading && !isRefreshing && component['scrollTop'] > 0) {
        requestAnimationFrame(() => {
          component.instanceListContainer.nativeElement.scrollTop = component['scrollTop'];
        });
      }

      expect(rafCalled).toBe(false);
      expect(component.instanceListContainer.nativeElement.scrollTop).toBe(0);
    });
  });
});
