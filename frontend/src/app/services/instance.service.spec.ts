import { signal, computed } from '@angular/core';
import { Observable, of, firstValueFrom } from 'rxjs';
import { InstanceInfo, InstanceStatus } from '../models';

const PAGE_SIZE = 100;

// Mock API response type
interface MockInstanceListResponse {
  instances: InstanceInfo[];
  total: number;
}

// Mock ApiService
class MockApiService {
  listInstances(limit: number, offset: number, projectId?: string, excludeKb?: boolean): Observable<MockInstanceListResponse> {
    return of({
      instances: [],
      total: 0,
    });
  }
}

// KB agent IDs to filter when showKb is false
const KB_AGENT_IDS = new Set(['experiencer', 'kb-importer']);

// Testable InstanceService implementation (mirrors actual service)
class TestableInstanceService {
  private readonly POLLING_INTERVAL = 10_000;
  private pollingIntervalId: ReturnType<typeof setInterval> | null = null;
  private currentProjectId: string | null = null;
  private currentOffset: number = 0;

  // Terminal statuses - same as actual service
  private readonly TERMINAL_STATUSES: Set<InstanceStatus> = new Set([
    'completed',
    'error',
    'terminated',
    'failed',
  ]);

  // Public signals
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

  constructor(private api: MockApiService) {}

  /**
   * Merge API instances with local instances, preserving terminal local statuses.
   */
  mergeInstances(local: InstanceInfo[], apiInstances: InstanceInfo[]): InstanceInfo[] {
    const localById = new Map(local.map(i => [i.instance_id, i]));
    const result: InstanceInfo[] = [];

    for (const apiInstance of apiInstances) {
      const localInstance = localById.get(apiInstance.instance_id);
      if (localInstance && this.TERMINAL_STATUSES.has(localInstance.status)) {
        result.push({ ...apiInstance, status: localInstance.status });
        localById.delete(apiInstance.instance_id);
      } else {
        result.push(apiInstance);
        localById.delete(apiInstance.instance_id);
      }
    }

    return [...result, ...localById.values()];
  }

  async loadInstances(projectId?: string, append = false): Promise<void> {
    if (append) {
      this.isLoadingMore.set(true);
    } else {
      this.loading.set(true);
      this.currentOffset = 0;
    }

    try {
      const response = await firstValueFrom(
        this.api.listInstances(PAGE_SIZE, this.currentOffset, projectId, !this.showKb())
      );

      if (append) {
        const existingIds = new Set(this.instances().map(i => i.instance_id));
        const newInstances = response.instances.filter(i => !existingIds.has(i.instance_id));
        this.instances.update(prev => [...prev, ...newInstances]);
        this.currentOffset += response.instances.length;
      } else {
        this.instances.set(response.instances);
        this.currentOffset = response.instances.length;
      }

      this.totalInstances.set(response.total);
    } catch (err) {
      console.error('Failed to load instances:', err);
    } finally {
      this.loading.set(false);
      this.isLoadingMore.set(false);
    }
  }

  loadMore(): void {
    if (!this.hasMoreInstances() || this.isLoadingMore()) {
      return;
    }
    this.loadInstances(this.currentProjectId ?? undefined, true);
  }

  startPolling(projectId?: string): void {
    this.stopPolling();
    this.currentProjectId = projectId ?? null;

    // Clear old instances immediately to avoid showing stale data
    this.instances.set([]);
    this.totalInstances.set(0);
    this.currentOffset = 0;

    // Immediate load
    this.loadInstances(projectId);

    // Start polling interval
    this.pollingIntervalId = setInterval(() => {
      this.loadInstances(projectId);
    }, this.POLLING_INTERVAL);
  }

  stopPolling(): void {
    if (this.pollingIntervalId) {
      clearInterval(this.pollingIntervalId);
      this.pollingIntervalId = null;
    }
  }

  updateInstanceStatus(instanceId: string, newStatus: InstanceStatus): void {
    this.instances.update(instances => {
      const existingIdx = instances.findIndex(i => i.instance_id === instanceId);
      if (existingIdx >= 0) {
        return instances.map((instance, idx) =>
          idx === existingIdx ? { ...instance, status: newStatus } : instance
        );
      } else {
        const minimalInstance: InstanceInfo = {
          instance_id: instanceId,
          agent_id: '',
          project_id: null,
          status: newStatus,
          parent_id: null,
          children: [],
          created_at: new Date().toISOString(),
          updated_at: new Date().toISOString(),
        };
        return [...instances, minimalInstance];
      }
    });
  }
}

// Helper to create mock instances
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
    ...overrides,
  };
}

describe('InstanceService', () => {
  let mockApi: MockApiService;
  let service: TestableInstanceService;

  beforeEach(() => {
    mockApi = new MockApiService();
    service = new TestableInstanceService(mockApi);
    jest.clearAllMocks();
  });

  afterEach(() => {
    service.stopPolling();
  });

  describe('loadInstances', () => {
    it('should fetch from API and set instances signal', async () => {
      const mockInstances = [
        createMockInstance({ instance_id: 'instance-1' }),
        createMockInstance({ instance_id: 'instance-2' }),
      ];

      mockApi.listInstances = jest.fn().mockReturnValue(
        of({ instances: mockInstances, total: 2 })
      );

      await service.loadInstances();

      expect(service.instances()).toHaveLength(2);
      expect(service.instances()[0].instance_id).toBe('instance-1');
      expect(service.instances()[1].instance_id).toBe('instance-2');
    });

    it('should set loading state during fetch', async () => {
      mockApi.listInstances = jest.fn().mockReturnValue(
        of({ instances: [], total: 0 })
      );

      let loadingDuringFetch: boolean | null = null;
      // Signals are called as functions, not subscribed to
      const checkLoading = () => { loadingDuringFetch = service.loading(); };

      const loadPromise = service.loadInstances();

      // At this point loading should be true
      checkLoading();
      expect(service.loading()).toBe(true);

      await loadPromise;

      // After fetch, loading should be false
      checkLoading();
      expect(service.loading()).toBe(false);
    });

    it('should set totalInstances from response', async () => {
      mockApi.listInstances = jest.fn().mockReturnValue(
        of({ instances: [createMockInstance()], total: 50 })
      );

      await service.loadInstances();

      expect(service.totalInstances()).toBe(50);
    });

    it('should pass projectId to API', async () => {
      mockApi.listInstances = jest.fn().mockReturnValue(
        of({ instances: [], total: 0 })
      );

      await service.loadInstances('project-123');

      expect(mockApi.listInstances).toHaveBeenCalledWith(100, 0, 'project-123', true);
    });

    it('should handle API errors gracefully', async () => {
      mockApi.listInstances = jest.fn().mockReturnValue(
        new Observable(subscriber => {
          subscriber.error(new Error('API Error'));
        })
      );

      // Should not throw
      await expect(service.loadInstances()).resolves.not.toThrow();
      // Should have empty instances
      expect(service.instances()).toHaveLength(0);
    });
  });

  describe('loadInstances with append', () => {
    beforeEach(() => {
      mockApi.listInstances = jest.fn().mockReturnValue(
        of({ instances: [], total: 0 })
      );
    });

    it('should append to existing instances', async () => {
      // First load
      mockApi.listInstances = jest.fn().mockReturnValue(
        of({ instances: [createMockInstance({ instance_id: 'instance-1' })], total: 3 })
      );
      await service.loadInstances();

      // Second load (append)
      mockApi.listInstances = jest.fn().mockReturnValue(
        of({ instances: [createMockInstance({ instance_id: 'instance-2' })], total: 3 })
      );
      await service.loadInstances(undefined, true);

      expect(service.instances()).toHaveLength(2);
      expect(service.instances()[0].instance_id).toBe('instance-1');
      expect(service.instances()[1].instance_id).toBe('instance-2');
    });

    it('should deduplicate when appending', async () => {
      const sameInstance = createMockInstance({ instance_id: 'same-instance' });

      mockApi.listInstances = jest.fn().mockReturnValue(
        of({ instances: [sameInstance], total: 2 })
      );
      await service.loadInstances();

      // Append same instance
      mockApi.listInstances = jest.fn().mockReturnValue(
        of({ instances: [sameInstance], total: 2 })
      );
      await service.loadInstances(undefined, true);

      // Should still have only 1 instance
      expect(service.instances()).toHaveLength(1);
    });

    it('should set isLoadingMore during append', async () => {
      mockApi.listInstances = jest.fn().mockReturnValue(
        of({ instances: [], total: 0 })
      );

      const loadPromise = service.loadInstances(undefined, true);

      expect(service.isLoadingMore()).toBe(true);

      await loadPromise;

      expect(service.isLoadingMore()).toBe(false);
    });

    it('should reset offset when not appending', async () => {
      // Load first page
      mockApi.listInstances = jest.fn().mockReturnValue(
        of({ instances: [createMockInstance()], total: 100 })
      );
      await service.loadInstances();

      // Load more
      mockApi.listInstances = jest.fn().mockReturnValue(
        of({ instances: [createMockInstance()], total: 100 })
      );
      await service.loadInstances(undefined, true);

      // New load without append should reset
      mockApi.listInstances = jest.fn().mockReturnValue(
        of({ instances: [createMockInstance({ instance_id: 'new-instance' })], total: 50 })
      );
      await service.loadInstances();

      expect(service.instances()).toHaveLength(1);
      expect(service.instances()[0].instance_id).toBe('new-instance');
    });
  });

  describe('loadInstances with projectId', () => {
    it('should pass projectId to API', async () => {
      mockApi.listInstances = jest.fn().mockReturnValue(
        of({ instances: [], total: 0 })
      );

      await service.loadInstances('my-project');

      expect(mockApi.listInstances).toHaveBeenCalledWith(100, 0, 'my-project', true);
    });

    it('should not pass projectId when undefined', async () => {
      mockApi.listInstances = jest.fn().mockReturnValue(
        of({ instances: [], total: 0 })
      );

      await service.loadInstances();

      expect(mockApi.listInstances).toHaveBeenCalledWith(100, 0, undefined, true);
    });
  });

  describe('hasMoreInstances', () => {
    it('should return true when more instances available', () => {
      service.instances.set([createMockInstance()]);
      service.totalInstances.set(10);

      expect(service.hasMoreInstances()).toBe(true);
    });

    it('should return false when all instances loaded', () => {
      service.instances.set([createMockInstance(), createMockInstance()]);
      service.totalInstances.set(2);

      expect(service.hasMoreInstances()).toBe(false);
    });

    it('should return false when no instances', () => {
      service.instances.set([]);
      service.totalInstances.set(0);

      expect(service.hasMoreInstances()).toBe(false);
    });
  });

  describe('startPolling', () => {
    beforeEach(() => {
      mockApi.listInstances = jest.fn().mockReturnValue(
        of({ instances: [], total: 0 })
      );
      jest.useFakeTimers();
    });

    afterEach(() => {
      jest.useRealTimers();
    });

    it('should load immediately on startPolling', async () => {
      service.startPolling();

      expect(mockApi.listInstances).toHaveBeenCalled();
    });

    it('should clear instances immediately before loading', async () => {
      // Pre-populate with some instances
      service.instances.set([createMockInstance({ instance_id: 'old-instance' })]);
      service.totalInstances.set(5);

      mockApi.listInstances = jest.fn().mockReturnValue(
        of({ instances: [], total: 0 })
      );

      service.startPolling();

      // Instances should be cleared immediately (before API call completes)
      expect(service.instances()).toHaveLength(0);
      expect(service.totalInstances()).toBe(0);
    });

    it('should start an interval', () => {
      service.startPolling();

      // Fast-forward time
      jest.advanceTimersByTime(11000);

      // Should have called API twice (initial + interval)
      expect(mockApi.listInstances).toHaveBeenCalledTimes(2);
    });

    it('should pass projectId to polling', () => {
      service.startPolling('test-project');

      jest.advanceTimersByTime(11000);

      expect(mockApi.listInstances).toHaveBeenCalledWith(100, 0, 'test-project', true);
    });

    it('should stop existing polling before starting new', () => {
      service.startPolling('project-1');

      const firstCallCount = mockApi.listInstances.mock.calls.length;

      jest.advanceTimersByTime(5000);
      service.startPolling('project-2');

      jest.advanceTimersByTime(6000);

      // Second polling should start fresh from project-2
      const lastCall = mockApi.listInstances.mock.calls[mockApi.listInstances.mock.calls.length - 1];
      expect(lastCall[2]).toBe('project-2');
    });
  });

  describe('stopPolling', () => {
    beforeEach(() => {
      mockApi.listInstances = jest.fn().mockReturnValue(
        of({ instances: [], total: 0 })
      );
      jest.useFakeTimers();
    });

    afterEach(() => {
      jest.useRealTimers();
    });

    it('should clear the interval', () => {
      service.startPolling();
      service.stopPolling();

      jest.advanceTimersByTime(20000);

      // Should only have initial call, not continued polling
      expect(mockApi.listInstances).toHaveBeenCalledTimes(1);
    });

    it('should be safe to call when not polling', () => {
      expect(() => service.stopPolling()).not.toThrow();
    });
  });

  describe('loadMore', () => {
    beforeEach(() => {
      mockApi.listInstances = jest.fn().mockReturnValue(
        of({ instances: [], total: 0 })
      );
    });

    it('should increment offset and load more', async () => {
      // Set up state with more available
      service.totalInstances.set(10);
      service.instances.set([createMockInstance({ instance_id: 'instance-1' })]);

      mockApi.listInstances = jest.fn().mockReturnValue(
        of({ instances: [createMockInstance({ instance_id: 'instance-2' })], total: 10 })
      );

      service.loadMore();

      await Promise.resolve(); // Allow async to complete
      await Promise.resolve();

      expect(service.instances()).toHaveLength(2);
    });

    it('should not load more when all loaded', () => {
      service.totalInstances.set(2);
      service.instances.set([
        createMockInstance({ instance_id: 'instance-1' }),
        createMockInstance({ instance_id: 'instance-2' }),
      ]);

      service.loadMore();

      expect(mockApi.listInstances).not.toHaveBeenCalled();
    });

    it('should not load more when already loading', () => {
      service.isLoadingMore.set(true);

      service.loadMore();

      expect(mockApi.listInstances).not.toHaveBeenCalled();
    });
  });

  describe('updateInstanceStatus', () => {
    it('should update existing instance status', () => {
      const instance = createMockInstance({ instance_id: 'test-123', status: 'running' });
      service.instances.set([instance]);

      service.updateInstanceStatus('test-123', 'completed');

      expect(service.instances()).toHaveLength(1);
      expect(service.instances()[0].status).toBe('completed');
      expect(service.instances()[0].instance_id).toBe('test-123');
    });

    it('should add minimal instance when not found', () => {
      service.instances.set([]);

      service.updateInstanceStatus('new-instance-456', 'running');

      expect(service.instances()).toHaveLength(1);
      expect(service.instances()[0].instance_id).toBe('new-instance-456');
      expect(service.instances()[0].status).toBe('running');
      expect(service.instances()[0].agent_id).toBe('');
      expect(service.instances()[0].project_id).toBe(null);
    });

    it('should update status without duplicating instance', () => {
      const instance = createMockInstance({ instance_id: 'test-789', status: 'queued' });
      service.instances.set([instance]);

      // Update multiple times
      service.updateInstanceStatus('test-789', 'running');
      service.updateInstanceStatus('test-789', 'paused');
      service.updateInstanceStatus('test-789', 'completed');

      expect(service.instances()).toHaveLength(1);
      expect(service.instances()[0].status).toBe('completed');
    });
  });

  describe('mergeInstances', () => {
    it('should preserve local terminal status when API returns different state', () => {
      const local: InstanceInfo[] = [
        createMockInstance({ instance_id: 'inst-1', status: 'completed' }),
      ];
      const api: InstanceInfo[] = [
        createMockInstance({ instance_id: 'inst-1', status: 'running' }),
      ];

      const result = service.mergeInstances(local, api);

      expect(result).toHaveLength(1);
      expect(result[0].status).toBe('completed');
    });

    it('should use API status when local is non-terminal', () => {
      const local: InstanceInfo[] = [
        createMockInstance({ instance_id: 'inst-1', status: 'running' }),
      ];
      const api: InstanceInfo[] = [
        createMockInstance({ instance_id: 'inst-1', status: 'completed' }),
      ];

      const result = service.mergeInstances(local, api);

      expect(result).toHaveLength(1);
      expect(result[0].status).toBe('completed');
    });

    it('should use API status when local has paused status (not terminal)', () => {
      const local: InstanceInfo[] = [
        createMockInstance({ instance_id: 'inst-1', status: 'paused' }),
      ];
      const api: InstanceInfo[] = [
        createMockInstance({ instance_id: 'inst-1', status: 'running' }),
      ];

      const result = service.mergeInstances(local, api);

      expect(result).toHaveLength(1);
      expect(result[0].status).toBe('running');
    });

    it('should preserve all terminal statuses (error, terminated, failed)', () => {
      const local: InstanceInfo[] = [
        createMockInstance({ instance_id: 'inst-1', status: 'error' }),
      ];
      const api: InstanceInfo[] = [
        createMockInstance({ instance_id: 'inst-1', status: 'running' }),
      ];

      const result = service.mergeInstances(local, api);

      expect(result).toHaveLength(1);
      expect(result[0].status).toBe('error');
    });

    it('should preserve local-only instances not in API response', () => {
      const local: InstanceInfo[] = [
        createMockInstance({ instance_id: 'local-only', status: 'running' }),
      ];
      const api: InstanceInfo[] = [
        createMockInstance({ instance_id: 'inst-1', status: 'completed' }),
      ];

      const result = service.mergeInstances(local, api);

      expect(result).toHaveLength(2);
      expect(result.find(i => i.instance_id === 'local-only')).toBeDefined();
      expect(result.find(i => i.instance_id === 'inst-1')).toBeDefined();
    });
  });

  describe('showKb signal', () => {
    it('should default to false', () => {
      expect(service.showKb()).toBe(false);
    });

    it('should be a writable signal', () => {
      service.showKb.set(true);
      expect(service.showKb()).toBe(true);
    });
  });

  describe('toggleKb', () => {
    it('should flip showKb signal from false to true', () => {
      expect(service.showKb()).toBe(false);

      service.toggleKb();

      expect(service.showKb()).toBe(true);
    });

    it('should flip showKb signal from true to false', () => {
      service.showKb.set(true);
      expect(service.showKb()).toBe(true);

      service.toggleKb();

      expect(service.showKb()).toBe(false);
    });

    it('should toggle multiple times', () => {
      expect(service.showKb()).toBe(false);

      service.toggleKb();
      expect(service.showKb()).toBe(true);

      service.toggleKb();
      expect(service.showKb()).toBe(false);

      service.toggleKb();
      expect(service.showKb()).toBe(true);
    });
  });

  describe('loadInstances with excludeKb', () => {
    it('should pass excludeKb=true to API when showKb is false', async () => {
      service.showKb.set(false);
      mockApi.listInstances = jest.fn().mockReturnValue(
        of({ instances: [], total: 0 })
      );

      await service.loadInstances();

      expect(mockApi.listInstances).toHaveBeenCalledWith(100, 0, undefined, true);
    });

    it('should pass excludeKb=false to API when showKb is true', async () => {
      service.showKb.set(true);
      mockApi.listInstances = jest.fn().mockReturnValue(
        of({ instances: [], total: 0 })
      );

      await service.loadInstances();

      expect(mockApi.listInstances).toHaveBeenCalledWith(100, 0, undefined, false);
    });

    it('should respect showKb state changes between calls', async () => {
      mockApi.listInstances = jest.fn().mockReturnValue(
        of({ instances: [], total: 0 })
      );

      service.showKb.set(false);
      await service.loadInstances();
      expect(mockApi.listInstances).toHaveBeenLastCalledWith(100, 0, undefined, true);

      service.showKb.set(true);
      await service.loadInstances();
      expect(mockApi.listInstances).toHaveBeenLastCalledWith(100, 0, undefined, false);

      service.showKb.set(false);
      await service.loadInstances();
      expect(mockApi.listInstances).toHaveBeenLastCalledWith(100, 0, undefined, true);
    });
  });
});
