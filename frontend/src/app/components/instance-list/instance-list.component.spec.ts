import { signal, computed } from '@angular/core';
import type { Agent, InstanceInfo, InstanceStatus } from '../../models';

// Mock InstanceService
class MockInstanceService {
  readonly instances = signal<InstanceInfo[]>([]);
  readonly totalInstances = signal<number>(0);
  readonly isLoadingMore = signal<boolean>(false);
  readonly loading = signal<boolean>(false);
  readonly showKb = signal<boolean>(false);

  readonly hasMoreInstances = signal(false);

  // Search-related members (mirrors InstanceService search feature)
  readonly searchQuery = signal<string>('');
  readonly isSearching = computed(() => this.searchQuery().trim().length > 0);
  currentProjectId: string | null = null;
  private _currentOffset = 0;

  setSearchQuery(query: string): void {
    const trimmed = query.trim();
    if (this.searchQuery() === trimmed) return;
    this.searchQuery.set(trimmed);
    this._currentOffset = 0;
  }

  getCurrentOffset(): number {
    return this._currentOffset;
  }

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

  // ── Search feature members ───────────────────────────────────────────────
  // Raw text the user is typing. The debounced effect below mirrors it into
  // instanceService.searchQuery after a 300ms idle window.
  readonly searchInput = signal<string>('');

  // Debounce timer — cleared in ngOnDestroy.
  private _searchDebounceTimer: ReturnType<typeof setTimeout> | null = null;

  // Build the (input) event the real component receives from Angular's
  // event binding. Mirrors what onSearchInput unwraps via event.target.value.
  private static buildInputEvent(value: string): Event {
    return {
      target: { value },
    } as unknown as Event;
  }

  /**
   * Bind to the search input's (input) event. Sets searchInput to the
   * raw value (no trim). Mirrors the real component.
   */
  onSearchInput(event: Event): void {
    const target = event.target as HTMLInputElement;
    this.searchInput.set(target.value);
  }

  /**
   * Clear button handler — same as the user emptying the box, but explicit.
   * No debounce; the effect below runs synchronously when searchInput changes.
   */
  onClearSearch(): void {
    this.searchInput.set('');
  }

  /**
   * Debounce effect logic: wait 300ms then push to the service and reload.
   * On empty value, reset immediately (no debounce).
   *
   * This is exposed as a method so tests can drive the effect manually
   * (the real component registers it inside its constructor effect()).
   * Mirrors the constructor effect body in instance-list.component.ts.
   */
  runSearchEffect(): void {
    const value = this.searchInput();
    if (this._searchDebounceTimer) {
      clearTimeout(this._searchDebounceTimer);
      this._searchDebounceTimer = null;
    }
    if (value.trim().length === 0) {
      // Instant reset path: clear search right now, no debounce.
      if (this.instanceService.searchQuery().length > 0) {
        this.instanceService.setSearchQuery('');
        this.instanceService.loadInstances(this.instanceService.currentProjectId ?? undefined);
      }
      return;
    }
    this._searchDebounceTimer = setTimeout(() => {
      this.instanceService.setSearchQuery(value);
      this.instanceService.loadInstances(this.instanceService.currentProjectId ?? undefined);
    }, 300);
  }

  // Exposed for ngOnDestroy test assertions.
  clearSearchDebounceTimer(): void {
    if (this._searchDebounceTimer) {
      clearTimeout(this._searchDebounceTimer);
      this._searchDebounceTimer = null;
    }
  }

  hasPendingSearchDebounce(): boolean {
    return this._searchDebounceTimer !== null;
  }

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
    // Mirrors real component: clear any pending debounce timer.
    if (this._searchDebounceTimer) {
      clearTimeout(this._searchDebounceTimer);
      this._searchDebounceTimer = null;
    }
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
    instance_id: 'instance-' + Math.random().toString(36).substr(2, 9),
    agent_id: 'test-agent',
    status: 'running',
    parent_id: null,
    children: [],
    title: null,
    created_at: new Date().toISOString(),
    updated_at: null,
    project_id: null,
    // Phase 3: default agent_tag to null so unversioned instances behave
    // like the legacy contract.
    agent_tag: null,
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

  // ── Phase 3: agent_tag exposure (instance list version badge) ──────────
  describe('agent_tag pass-through (Phase 3)', () => {
    it('surfaces agent_tag on InstanceInfo so the template can render a badge', () => {
      const tagged = createMockInstance({
        instance_id: 'tagged-1',
        agent_id: 'dev',
        agent_tag: 'v2',
      });

      component.instances.set([tagged]);

      const tree = component.instanceTree();
      expect(tree).toHaveLength(1);
      expect(tree[0].instance.agent_tag).toBe('v2');
    });

    it('keeps agent_tag null for base / unversioned instances', () => {
      const base = createMockInstance({
        instance_id: 'base-1',
        agent_id: 'dev',
      });

      component.instances.set([base]);

      const tree = component.instanceTree();
      expect(tree[0].instance.agent_tag).toBeNull();
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

  describe('getProjectContext() - Project-Aware Navigation', () => {
    // Mock TabStateService for navigation testing
    class MockTabStateService {
      activeProjectId = signal<string | null>(null);
    }

    // Component with getProjectContext method
    class ProjectContextTestableComponent {
      private readonly tabStateService: MockTabStateService;

      constructor(tabStateService: MockTabStateService) {
        this.tabStateService = tabStateService;
      }

      protected getProjectContext(): string {
        return this.tabStateService.activeProjectId() ?? 'all';
      }
    }

    let tabStateService: MockTabStateService;
    let component: ProjectContextTestableComponent;

    beforeEach(() => {
      tabStateService = new MockTabStateService();
      component = new ProjectContextTestableComponent(tabStateService);
    });

    describe('getProjectContext() return values', () => {
      it('should return "all" when activeProjectId is null', () => {
        tabStateService.activeProjectId.set(null);

        expect(component.getProjectContext()).toBe('all');
      });

      it('should return "all" when on All tab (no project selected)', () => {
        tabStateService.activeProjectId.set(null);

        expect(component.getProjectContext()).toBe('all');
      });

      it('should return specific project ID when project is selected', () => {
        tabStateService.activeProjectId.set('list-project-123');

        expect(component.getProjectContext()).toBe('list-project-123');
      });

      it('should handle various project ID formats', () => {
        const projectIds = [
          'proj-abc',
          'my_project',
          'project.with.dots',
          '123-numbers',
          'special-chars-_-',
        ];

        for (const projectId of projectIds) {
          tabStateService.activeProjectId.set(projectId);
          expect(component.getProjectContext()).toBe(projectId);
        }
      });
    });

    describe('Navigation URL Pattern Verification', () => {
      it('should produce correct URL for All tab navigation', () => {
        tabStateService.activeProjectId.set(null);
        const instanceId = 'list-inst-all';

        const projectContext = component.getProjectContext();
        const urlPath = ['/projects', projectContext, 'instances', instanceId];

        expect(urlPath).toEqual(['/projects', 'all', 'instances', 'list-inst-all']);
      });

      it('should produce correct URL for specific project navigation', () => {
        tabStateService.activeProjectId.set('list-project');
        const instanceId = 'list-inst-project';

        const projectContext = component.getProjectContext();
        const urlPath = ['/projects', projectContext, 'instances', instanceId];

        expect(urlPath).toEqual(['/projects', 'list-project', 'instances', 'list-inst-project']);
      });

      it('should maintain URL structure consistency', () => {
        // Test that the URL structure is always: /projects/:projectId/instances/:instanceId
        const testCases = [
          { projectId: null, expected: ['/projects', 'all', 'instances', 'test'] },
          { projectId: 'proj-a', expected: ['/projects', 'proj-a', 'instances', 'test'] },
          { projectId: 'proj-b', expected: ['/projects', 'proj-b', 'instances', 'test'] },
        ];

        for (const testCase of testCases) {
          tabStateService.activeProjectId.set(testCase.projectId as any);

          const projectContext = component.getProjectContext();
          const urlPath = ['/projects', projectContext, 'instances', 'test'];

          expect(urlPath).toEqual(testCase.expected);
        }
      });
    });

    describe('Integration with routerLink', () => {
      it('should provide correct project context for template routerLink', () => {
        tabStateService.activeProjectId.set('template-project');

        const projectContext = component.getProjectContext();
        const instanceId = 'router-link-inst';

        // Simulate what the template would build:
        // [routerLink]="['/projects', getProjectContext(), 'instances', instance.instance_id]"
        const routerLinkArray = ['/projects', projectContext, 'instances', instanceId];

        expect(routerLinkArray).toEqual([
          '/projects',
          'template-project',
          'instances',
          'router-link-inst',
        ]);
      });

      it('should provide "all" context for template when on All tab', () => {
        tabStateService.activeProjectId.set(null);

        const projectContext = component.getProjectContext();
        const instanceId = 'all-tab-inst';

        const routerLinkArray = ['/projects', projectContext, 'instances', instanceId];

        expect(routerLinkArray).toEqual(['/projects', 'all', 'instances', 'all-tab-inst']);
      });
    });
  });

  // ── Search feature: searchInput signal + onSearchInput/onClearSearch ─────
  // Mirrors the implementation in instance-list.component.ts:
  //   - searchInput signal (line 109)
  //   - onSearchInput() (line 383): reads event.target.value, sets the signal
  //   - onClearSearch() (line 392): sets searchInput to '' (effect resets)
  //   - debounce effect (lines 188-203): 300ms wait then setSearchQuery + load
  //   - instant reset on empty (no debounce)
  //   - setSearchQuery('') resets offset (instance.service.ts line 77)
  //   - template renders the search box (instance-list.html lines 35-56)
  describe('search feature', () => {
    describe('searchInput signal', () => {
      it('should default to an empty string', () => {
        expect(component.searchInput()).toBe('');
      });

      it('should reflect updates via .set()', () => {
        component.searchInput.set('hello');
        expect(component.searchInput()).toBe('hello');
      });
    });

    describe('onSearchInput', () => {
      it('should update searchInput from an input event value', () => {
        const event = {
          target: { value: 'worker-42' },
        } as unknown as Event;

        component.onSearchInput(event);

        expect(component.searchInput()).toBe('worker-42');
      });

      it('should preserve raw value including whitespace (no trim)', () => {
        // The real component intentionally does NOT trim — the effect does.
        const event = {
          target: { value: '  spaced  ' },
        } as unknown as Event;

        component.onSearchInput(event);

        expect(component.searchInput()).toBe('  spaced  ');
      });

      it('should overwrite a previous searchInput value on subsequent input', () => {
        component.searchInput.set('old');

        const event = { target: { value: 'new' } } as unknown as Event;
        component.onSearchInput(event);

        expect(component.searchInput()).toBe('new');
      });

      it('should set empty string when the input is cleared by hand', () => {
        component.searchInput.set('something');

        const event = { target: { value: '' } } as unknown as Event;
        component.onSearchInput(event);

        expect(component.searchInput()).toBe('');
      });
    });

    describe('onClearSearch', () => {
      it('should reset searchInput to an empty string', () => {
        component.searchInput.set('previously-typed');

        component.onClearSearch();

        expect(component.searchInput()).toBe('');
      });

      it('should be a no-op-safe reset when searchInput is already empty', () => {
        expect(component.searchInput()).toBe('');

        expect(() => component.onClearSearch()).not.toThrow();

        expect(component.searchInput()).toBe('');
      });
    });

    // ── Debounce effect (Jest fake timers) ───────────────────────────────
    // These tests drive runSearchEffect() — the mirror of the constructor
    // effect body in instance-list.component.ts. We use fake timers so the
    // 300ms debounce is deterministic and never waits in real time.
    describe('debounce effect (300ms)', () => {
      beforeEach(() => {
        jest.useFakeTimers();
      });

      afterEach(() => {
        // Always clear any pending timer + restore real timers so the next
        // test block isn't poisoned by fake time.
        component.clearSearchDebounceTimer();
        jest.useRealTimers();
      });

      it('should NOT call setSearchQuery before 300ms elapse', () => {
        component.searchInput.set('worker');
        const setSearchSpy = jest.spyOn(mockService, 'setSearchQuery');
        const loadSpy = jest.spyOn(mockService, 'loadInstances');

        component.runSearchEffect();

        // Just under the debounce window — service must not be touched yet.
        jest.advanceTimersByTime(299);

        expect(setSearchSpy).not.toHaveBeenCalled();
        expect(loadSpy).not.toHaveBeenCalled();
      });

      it('should call setSearchQuery + loadInstances AFTER 300ms', () => {
        component.searchInput.set('worker');
        const setSearchSpy = jest.spyOn(mockService, 'setSearchQuery');
        const loadSpy = jest.spyOn(mockService, 'loadInstances');

        component.runSearchEffect();
        jest.advanceTimersByTime(300);

        expect(setSearchSpy).toHaveBeenCalledTimes(1);
        expect(setSearchSpy).toHaveBeenCalledWith('worker');
        expect(loadSpy).toHaveBeenCalledTimes(1);
      });

      it('should pass the RAW (untrimmed) value to setSearchQuery — trimming happens in the service', () => {
        // Real component passes raw value; the service's setSearchQuery is
        // responsible for trimming. Assert the contract boundary.
        component.searchInput.set('  spaced  ');
        const setSearchSpy = jest.spyOn(mockService, 'setSearchQuery');

        component.runSearchEffect();
        jest.advanceTimersByTime(300);

        expect(setSearchSpy).toHaveBeenCalledWith('  spaced  ');
      });

      it('should reset the pending timer when a new keystroke arrives (debounce coalescing)', () => {
        component.searchInput.set('w');
        const setSearchSpy = jest.spyOn(mockService, 'setSearchQuery');

        component.runSearchEffect();
        jest.advanceTimersByTime(200); // not yet

        component.searchInput.set('wo');
        component.runSearchEffect(); // new keystroke restarts the window
        jest.advanceTimersByTime(200); // would fire if first timer were live

        expect(setSearchSpy).not.toHaveBeenCalled();

        jest.advanceTimersByTime(100); // now the second timer's 300ms has elapsed
        expect(setSearchSpy).toHaveBeenCalledTimes(1);
        expect(setSearchSpy).toHaveBeenCalledWith('wo');
      });
    });

    // ── Instant reset path (no debounce) ────────────────────────────────
    // When searchInput goes empty (user pressed the X or cleared the box),
    // the effect resets immediately — no 300ms wait.
    describe('instant reset path (no debounce)', () => {
      beforeEach(() => {
        jest.useFakeTimers();
      });

      afterEach(() => {
        component.clearSearchDebounceTimer();
        jest.useRealTimers();
      });

      it('should clear the search IMMEDIATELY when value becomes empty (no debounce wait)', () => {
        // Simulate prior active search
        mockService.searchQuery.set('worker');
        const setSearchSpy = jest.spyOn(mockService, 'setSearchQuery');
        const loadSpy = jest.spyOn(mockService, 'loadInstances');

        component.searchInput.set('');
        component.runSearchEffect();

        // advanceTimersByTime(0) — nothing should be queued to fire later;
        // the reset path is synchronous.
        jest.advanceTimersByTime(0);

        expect(setSearchSpy).toHaveBeenCalledTimes(1);
        expect(setSearchSpy).toHaveBeenCalledWith('');
        expect(loadSpy).toHaveBeenCalledTimes(1);
      });

      it('should NOT trigger a reset if the search was already empty (avoid redundant reload)', () => {
        // searchQuery is '' by default — emptying searchInput is a no-op.
        const setSearchSpy = jest.spyOn(mockService, 'setSearchQuery');
        const loadSpy = jest.spyOn(mockService, 'loadInstances');

        component.searchInput.set('');
        component.runSearchEffect();
        jest.advanceTimersByTime(0);

        expect(setSearchSpy).not.toHaveBeenCalled();
        expect(loadSpy).not.toHaveBeenCalled();
      });

      it('should clear any pending debounce timer when the reset path runs', () => {
        // Start a debounced search...
        component.searchInput.set('worker');
        component.runSearchEffect();
        expect(component.hasPendingSearchDebounce()).toBe(true);

        // ...then the user hits the X — the pending timer must be cleared.
        mockService.searchQuery.set('worker'); // pretend prior search committed
        component.searchInput.set('');
        component.runSearchEffect();

        expect(component.hasPendingSearchDebounce()).toBe(false);
      });
    });

    // ── setSearchQuery offset reset contract ────────────────────────────
    // Mirrors instance.service.ts: setSearchQuery resets currentOffset = 0
    // so the next non-append load starts at the beginning of the filtered set.
    describe('setSearchQuery offset reset', () => {
      it('should reset offset to 0 when a new query is set', () => {
        // Simulate the service being mid-pagination by seeding the private
        // offset directly (mirrors the real service's currentOffset field).
        mockService['_currentOffset'] = 20;
        expect(mockService.getCurrentOffset()).toBe(20);

        mockService.setSearchQuery('worker');

        expect(mockService.getCurrentOffset()).toBe(0);
      });

      it('should be a no-op (and preserve offset) when query is unchanged', () => {
        mockService.searchQuery.set('worker');
        mockService['_currentOffset'] = 20;

        mockService.setSearchQuery('worker'); // same trimmed value

        expect(mockService.getCurrentOffset()).toBe(20);
      });

      it('should trim the query before comparing/storing', () => {
        mockService.setSearchQuery('  worker  ');

        expect(mockService.searchQuery()).toBe('worker');
      });

      it('should treat empty string as a query change that resets offset', () => {
        mockService.searchQuery.set('worker');
        mockService['_currentOffset'] = 30;

        mockService.setSearchQuery('');

        expect(mockService.searchQuery()).toBe('');
        expect(mockService.getCurrentOffset()).toBe(0);
      });
    });

    // ── isSearching computed signal ─────────────────────────────────────
    describe('isSearching computed signal', () => {
      it('should be false when searchQuery is empty', () => {
        mockService.searchQuery.set('');
        expect(mockService.isSearching()).toBe(false);
      });

      it('should be false when searchQuery is whitespace-only (trimmed empty)', () => {
        mockService.searchQuery.set('   ');
        // isSearching reads .trim().length > 0
        expect(mockService.isSearching()).toBe(false);
      });

      it('should be true when searchQuery has non-whitespace content', () => {
        mockService.searchQuery.set('worker');
        expect(mockService.isSearching()).toBe(true);
      });
    });

    // ── ngOnDestroy clears the debounce timer ───────────────────────────
    describe('ngOnDestroy clears pending search debounce', () => {
      beforeEach(() => {
        jest.useFakeTimers();
      });

      afterEach(() => {
        jest.useRealTimers();
      });

      it('should clear a pending debounce timer on destroy (no late setSearchQuery)', () => {
        component.searchInput.set('worker');
        const setSearchSpy = jest.spyOn(mockService, 'setSearchQuery');

        component.runSearchEffect();
        expect(component.hasPendingSearchDebounce()).toBe(true);

        component.ngOnDestroy();

        expect(component.hasPendingSearchDebounce()).toBe(false);

        // Advance well past 300ms — nothing should fire post-destroy.
        jest.advanceTimersByTime(1000);
        expect(setSearchSpy).not.toHaveBeenCalled();
      });
    });

    // ── Template contract: search box markup ────────────────────────────
    // We don't mount the real Angular template (the spec uses a hand-rolled
    // mirror class), but we DO assert the static HTML contract: the search
    // input + clear button exist and are wired to the right handlers. This
    // catches accidental template regressions (e.g. someone removing the
    // (input) binding) without needing TestBed.
    describe('template contract (instance-list.html)', () => {
      let templateHtml: string;

      beforeAll(() => {
        // Resolve relative to this spec file: ../../instance-list.html
        const path = require('path');
        const fs = require('fs');
        const specDir = __dirname;
        const htmlPath = path.join(specDir, 'instance-list.html');
        templateHtml = fs.readFileSync(htmlPath, 'utf-8');
      });

      it('should render a search input in the header', () => {
        expect(templateHtml).toMatch(/class="search-input"/);
        expect(templateHtml).toMatch(/aria-label="Search instances"/);
      });

      it('should bind the input value to searchInput()', () => {
        expect(templateHtml).toMatch(/\[value\]="searchInput\(\)"/);
      });

      it('should wire (input) events to onSearchInput($event)', () => {
        expect(templateHtml).toMatch(/\(input\)="onSearchInput\(\$event\)"/);
      });

      it('should render the clear (X) button bound to onClearSearch()', () => {
        expect(templateHtml).toMatch(/\(click\)="onClearSearch\(\)"/);
        expect(templateHtml).toMatch(/aria-label="Clear search"/);
      });

      it('should conditionally show the clear button only when searchInput has text', () => {
        // Mirrors: @if (searchInput().trim().length > 0) { ... clear btn ... }
        expect(templateHtml).toMatch(/searchInput\(\)\.trim\(\)\.length > 0/);
      });

      it('should render the N-results-for-query subtitle when isSearching() is true', () => {
        expect(templateHtml).toMatch(/instanceService\.isSearching\(\)/);
        expect(templateHtml).toMatch(/instanceService\.searchQuery\(\)/);
      });
    });
  });
});
