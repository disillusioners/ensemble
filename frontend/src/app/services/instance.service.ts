import { Injectable, inject, signal, computed, WritableSignal, Signal, effect } from '@angular/core';
import { firstValueFrom } from 'rxjs';
import { ApiService } from './api.service';
import { SseService } from './sse.service';
import type { InstanceInfo, InstanceStatus } from '../models';

const PAGE_SIZE = 100;

// Keep in sync with backend: daemon/repositories/instance/repository.py (KB_AGENT_IDS)

// KB agent IDs to filter when showKb is false
const KB_AGENT_IDS = new Set(['experiencer', 'kb-importer']);

// Terminal statuses are final states that should not be overwritten by polling
const TERMINAL_STATUSES: Set<InstanceStatus> = new Set([
  'completed',
  'error',
  'terminated',
  'failed',
]);

export function sortByCreatedAtDesc(instances: InstanceInfo[]): InstanceInfo[] {
  return [...instances].sort((a, b) => {
    const aTime = a.created_at ? new Date(a.created_at).getTime() : 0;
    const bTime = b.created_at ? new Date(b.created_at).getTime() : 0;
    return bTime - aTime;
  });
}

@Injectable({
  providedIn: 'root'
})
export class InstanceService {
  private readonly api = inject(ApiService);
  private readonly sseService = inject(SseService);

  // Polling interval: 60 seconds
  private readonly POLLING_INTERVAL = 60_000;
  private pollingIntervalId: ReturnType<typeof setInterval> | null = null;
  private _currentProjectId: string | null = null;
  private currentOffset: number = 0;

  /**
   * The currently active project filter. Returns null if showing all instances.
   */
  get currentProjectId(): string | null {
    return this._currentProjectId;
  }

  // Public signals
  readonly instances: WritableSignal<InstanceInfo[]> = signal([]);
  readonly totalInstances: WritableSignal<number> = signal(0);
  readonly isLoadingMore: WritableSignal<boolean> = signal(false);
  readonly loading: WritableSignal<boolean> = signal(false);
  readonly showKb: WritableSignal<boolean> = signal(false);

  readonly hasMoreInstances: Signal<boolean> = computed(
    () => this.instances().length < this.totalInstances()
  );

  toggleKb(): void {
    this.showKb.update(v => !v);
  }

  constructor() {
    // Subscribe to SSE status change events for optimistic updates
    effect(() => {
      const statusChange = this.sseService.statusChange();
      if (!statusChange) return;

      // Filter out KB instances when showKb is false
      if (!this.showKb() && statusChange.agent_id && KB_AGENT_IDS.has(statusChange.agent_id)) {
        this.sseService.statusChange.set(null);
        return;
      }

      // Capture the statusChange data before resetting
      const { instance_id, status, agent_id } = statusChange;

      // Reset the SSE signal immediately to allow re-triggering
      this.sseService.statusChange.set(null);

      // Update status (async to fetch created_at for new instances)
      this.updateInstanceStatus(instance_id, status as InstanceStatus);
    });

    // Subscribe to SSE instance_created events for tree updates
    effect(() => {
      const newInstance = this.sseService.instanceCreated();
      if (!newInstance) return;

      // Filter out KB instances when showKb is false
      if (!this.showKb() && KB_AGENT_IDS.has(newInstance.agent_id)) {
        this.sseService.instanceCreated.set(null);
        return;
      }

      // Capture before resetting
      const instanceData = newInstance;

      // Reset the SSE signal immediately to allow re-triggering
      this.sseService.instanceCreated.set(null);

      // Add the new instance with proper tree linking
      this.addInstanceToTree(instanceData);
    });
  }

  /**
   * Optimistically update instance status locally.
   * If the instance is not in the list (e.g., direct navigation), fetches the actual
   * created_at from the backend to ensure correct sort order. Polling will correct any inconsistencies.
   */
  async updateInstanceStatus(instanceId: string, newStatus: InstanceStatus): Promise<void> {
    const existingInstances = this.instances();
    const existingIdx = existingInstances.findIndex(i => i.instance_id === instanceId);

    if (existingIdx >= 0) {
      // Update existing instance - just update status, keep existing created_at
      this.instances.update(instances =>
        sortByCreatedAtDesc(
          instances.map((instance, idx) =>
            idx === existingIdx ? { ...instance, status: newStatus } : instance
          )
        )
      );
    } else {
      // Instance not in list - fetch from API to get actual created_at
      // This ensures the new instance sorts correctly by server creation time
      try {
        const instanceData = await firstValueFrom(this.api.getInstance(instanceId));
        // Re-check if instance was already added by a concurrent call (race condition fix)
        const currentInstances = this.instances();
        if (currentInstances.some(i => i.instance_id === instanceId)) {
          // Already added, just update status
          this.instances.update(instances =>
            instances.map(i => i.instance_id === instanceId ? { ...i, status: newStatus } : i)
          );
          return;
        }
        this.instances.update(instances =>
          sortByCreatedAtDesc([instanceData, ...instances])
        );
      } catch (err) {
        // Instance not found - create minimal entry for direct navigation support
        console.warn('Instance not found in API, creating minimal entry:', instanceId);
        // Re-check if instance was already added by a concurrent call
        const currentInstances = this.instances();
        if (currentInstances.some(i => i.instance_id === instanceId)) {
          // Already added, just update status
          this.instances.update(instances =>
            instances.map(i => i.instance_id === instanceId ? { ...i, status: newStatus } : i)
          );
          return;
        }
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
        this.instances.update(instances =>
          sortByCreatedAtDesc([minimalInstance, ...instances])
        );
      }
    }
  }

  /**
   * Add a new instance to the tree with proper parent-child linking.
   */
  private addInstanceToTree(newInstance: InstanceInfo): void {
    this.instances.update(instances => {
      // Check if instance already exists (deduplication)
      if (instances.some(i => i.instance_id === newInstance.instance_id)) {
        return instances;
      }

      let updatedInstances = [...instances];

      if (newInstance.parent_id) {
        // Try to find and link to parent
        const parentIdx = updatedInstances.findIndex(i => i.instance_id === newInstance.parent_id);
        if (parentIdx >= 0) {
          // Parent exists - add as child and update parent's children array
          updatedInstances[parentIdx] = {
            ...updatedInstances[parentIdx],
            children: [...updatedInstances[parentIdx].children, newInstance.instance_id],
          };
        }
        // If parent not found, the instance will be added as root
        // The tree builder will reorganize on next poll
      }

      // Add the new instance (as child if parent found, otherwise as root)
      updatedInstances = sortByCreatedAtDesc([newInstance, ...updatedInstances]);

      return updatedInstances;
    });
  }

  /**
   * Merge API instances with local instances to handle timing between SSE and polling.
   *
   * Background: SSE delivers status updates asynchronously (event-driven), while polling
   * reads from the DB on a 60-second interval. Due to network timing, SSE may arrive
   * before the API's next poll sees the updated DB status.
   *
   * Example race condition this solves:
   *   T=0: SSE arrives with status="completed"
   *   T=1: User navigates or starts polling
   *   T=2: Poll API returns status="running" (DB not yet updated)
   * Without merging: User sees "running" for ~58 more seconds
   *   With merging: User sees "completed" immediately (SSE wins)
   *
   * This function also preserves local-only instances (e.g., created via direct
   * navigation to a URL that doesn't appear in the paginated list response).
   */
  private mergeInstances(local: InstanceInfo[], apiInstances: InstanceInfo[]): InstanceInfo[] {
    const localById = new Map(local.map(i => [i.instance_id, i]));
    const result: InstanceInfo[] = [];

    for (const apiInstance of apiInstances) {
      const localInstance = localById.get(apiInstance.instance_id);
      if (localInstance && TERMINAL_STATUSES.has(localInstance.status)) {
        // Preserve local terminal status - SSE already updated it to a final state
        result.push({ ...apiInstance, status: localInstance.status });
        localById.delete(apiInstance.instance_id);
      } else {
        result.push(apiInstance);
        localById.delete(apiInstance.instance_id);
      }
    }

    // Prepend any local-only instances that weren't in the API response.
    // These are newer than everything in the API response (since API returns newest first),
    // so they should appear at the top to maintain correct sort order.
    return sortByCreatedAtDesc([...localById.values(), ...result]);
  }

  /**
   * Load instances from the API.
   * @param projectId Optional project filter
   * @param append If true, append to existing instances; otherwise replace
   */
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
        // Deduplicate when appending
        const existingIds = new Set(this.instances().map((i: InstanceInfo) => i.instance_id));
        const newInstances = response.instances.filter(i => !existingIds.has(i.instance_id));
        this.instances.update((prev: InstanceInfo[]) => sortByCreatedAtDesc([...prev, ...newInstances]));
        this.currentOffset += response.instances.length;
      } else {
        // Merge with existing instances to avoid overwriting SSE updates.
        // See mergeInstances() docstring for the SSE-vs-polling race condition it handles.
        const merged = this.mergeInstances(this.instances(), response.instances);
        this.instances.set(merged);
        this.currentOffset = response.instances.length;
      }

      this.totalInstances.set(response.total);

      // Update hasMoreInstances based on whether we have more to load
      // (hasMoreInstances is a computed signal, so it auto-updates)
    } catch (err) {
      console.error('Failed to load instances:', err);
    } finally {
      this.loading.set(false);
      this.isLoadingMore.set(false);
    }
  }

  /**
   * Load the next page of instances.
   */
  loadMore(): void {
    if (!this.hasMoreInstances() || this.isLoadingMore()) {
      return;
    }
    this.loadInstances(this._currentProjectId ?? undefined, true);
  }

  /**
   * Start polling for instance updates.
   * @param projectId Optional project filter
   */
  startPolling(projectId?: string): void {
    this.stopPolling();
    this._currentProjectId = projectId ?? null;

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

  /**
   * Stop polling for instance updates.
   */
  stopPolling(): void {
    if (this.pollingIntervalId) {
      clearInterval(this.pollingIntervalId);
      this.pollingIntervalId = null;
    }
  }
}
