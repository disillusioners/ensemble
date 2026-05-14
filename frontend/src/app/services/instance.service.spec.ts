import { signal, computed } from '@angular/core';
import { Observable, of, firstValueFrom } from 'rxjs';
import { InstanceInfo } from '../models';

const PAGE_SIZE = 100;

// Mock API response type
interface MockInstanceListResponse {
  instances: InstanceInfo[];
  total: number;
}

// Mock ApiService
class MockApiService {
  listInstances(limit: number, offset: number, projectId?: string): Observable<MockInstanceListResponse> {
    return of({
      instances: [],
      total: 0,
    });
  }
}

// Testable InstanceService implementation (mirrors actual service)
class TestableInstanceService {
  private readonly POLLING_INTERVAL = 10_000;
  private pollingIntervalId: ReturnType<typeof setInterval> | null = null;
  private currentProjectId: string | null = null;
  private currentOffset: number = 0;

  // Public signals
  readonly instances = signal<InstanceInfo[]>([]);
  readonly totalInstances = signal<number>(0);
  readonly isLoadingMore = signal<boolean>(false);
  readonly loading = signal<boolean>(false);

  readonly hasMoreInstances = computed(
    () => this.instances().length < this.totalInstances()
  );

  constructor(private api: MockApiService) {}

  async loadInstances(projectId?: string, append = false): Promise<void> {
    if (append) {
      this.isLoadingMore.set(true);
    } else {
      this.loading.set(true);
      this.currentOffset = 0;
    }

    try {
      const response = await firstValueFrom(
        this.api.listInstances(PAGE_SIZE, this.currentOffset, projectId)
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
    this.loadInstances(projectId);

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

      expect(mockApi.listInstances).toHaveBeenCalledWith(100, 0, 'project-123');
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

      expect(mockApi.listInstances).toHaveBeenCalledWith(100, 0, 'my-project');
    });

    it('should not pass projectId when undefined', async () => {
      mockApi.listInstances = jest.fn().mockReturnValue(
        of({ instances: [], total: 0 })
      );

      await service.loadInstances();

      expect(mockApi.listInstances).toHaveBeenCalledWith(100, 0, undefined);
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

      expect(mockApi.listInstances).toHaveBeenCalledWith(100, 0, 'test-project');
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
});
