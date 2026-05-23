import { Injectable, inject, signal, computed, WritableSignal, Signal, effect } from '@angular/core';
import { firstValueFrom } from 'rxjs';
import { ApiService } from './api.service';
import { SseService } from './sse.service';
import type { InstanceInfo, InstanceStatus } from '../models';

const PAGE_SIZE = 100;

// Terminal statuses are final states that should not be overwritten by polling
const TERMINAL_STATUSES: Set<InstanceStatus> = new Set([
  'completed',
  'error',
  'terminated',
  'failed',
]);

@Injectable({
  providedIn: 'root'
})
export class InstanceService {
  private readonly api = inject(ApiService);
  private readonly sseService = inject(SseService);

  // Polling interval: 10 seconds
  private readonly POLLING_INTERVAL = 10_000;
  private pollingIntervalId: ReturnType<typeof setInterval> | null = null;
  private currentProjectId: string | null = null;
  private currentOffset: number = 0;

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
      if (statusChange) {
        this.updateInstanceStatus(statusChange.instance_id, statusChange.status as InstanceStatus);
        this.sseService.statusChange.set(null);  // Reset for re-trigger
      }
    });
  }

  /**
   * Optimistically update instance status locally.
   * If the instance is not in the list (e.g., direct navigation), creates a minimal entry
   * so computed signals like currentInstance can pick it up. Polling will correct any inconsistencies.
   */
  updateInstanceStatus(instanceId: string, newStatus: InstanceStatus): void {
    this.instances.update(instances => {
      const existingIdx = instances.findIndex(i => i.instance_id === instanceId);
      if (existingIdx >= 0) {
        // Update existing instance
        return instances.map((instance, idx) =>
          idx === existingIdx ? { ...instance, status: newStatus } : instance
        );
      } else {
        // Instance not in list - create minimal entry for direct navigation support
        // Required fields for InstanceInfo: instance_id, agent_id, project_id, status, parent_id, children, created_at
        const minimalInstance: InstanceInfo = {
          instance_id: instanceId,
          agent_id: '',           // Will be filled by polling
          project_id: null,       // Will be filled by polling
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

  /**
   * Merge API instances with local instances to handle timing between SSE and polling.
   *
   * Background: SSE delivers status updates asynchronously (event-driven), while polling
   * reads from the DB on a 10-second interval. Due to network timing, SSE may arrive
   * before the API's next poll sees the updated DB status.
   *
   * Example race condition this solves:
   *   T=0: SSE arrives with status="completed"
   *   T=1: User navigates or starts polling
   *   T=2: Poll API returns status="running" (DB not yet updated)
   *   Without merging: User sees "running" for ~8 more seconds
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

    // Append any local-only instances that weren't in the API response
    // (e.g., instances created via direct navigation)
    return [...result, ...localById.values()];
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
        this.instances.update((prev: InstanceInfo[]) => [...prev, ...newInstances]);
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
    this.loadInstances(this.currentProjectId ?? undefined, true);
  }

  /**
   * Start polling for instance updates.
   * @param projectId Optional project filter
   */
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
