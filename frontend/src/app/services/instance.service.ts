import { Injectable, inject, signal, computed, WritableSignal, Signal, effect } from '@angular/core';
import { firstValueFrom } from 'rxjs';
import { ApiService } from './api.service';
import { SseService } from './sse.service';
import type { InstanceInfo, InstanceStatus } from '../models';

const PAGE_SIZE = 100;

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

  readonly hasMoreInstances: Signal<boolean> = computed(
    () => this.instances().length < this.totalInstances()
  );

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
   * Polling will correct any inconsistencies.
   */
  updateInstanceStatus(instanceId: string, newStatus: InstanceStatus): void {
    this.instances.update(instances =>
      instances.map(instance =>
        instance.instance_id === instanceId
          ? { ...instance, status: newStatus }
          : instance
      )
    );
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
        this.api.listInstances(PAGE_SIZE, this.currentOffset, projectId)
      );

      if (append) {
        // Deduplicate when appending
        const existingIds = new Set(this.instances().map((i: InstanceInfo) => i.instance_id));
        const newInstances = response.instances.filter(i => !existingIds.has(i.instance_id));
        this.instances.update((prev: InstanceInfo[]) => [...prev, ...newInstances]);
        this.currentOffset += response.instances.length;
      } else {
        this.instances.set(response.instances);
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
