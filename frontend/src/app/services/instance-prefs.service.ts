import { Injectable, inject } from '@angular/core';
import { EMPTY, Observable, catchError, ignoreElements, tap } from 'rxjs';
import { ApiService } from './api.service';
import { InstanceService } from './instance.service';
import type { InstanceInfo } from '../models';

/**
 * Color palette exposed to the color-tag picker. The ``value`` is what
 * gets sent to the backend and stored verbatim on the instance row.
 * Keep in sync with the backend's accepted hex set; the backend will
 * reject unknown colors so the UI must only send values from this list.
 */
export const COLOR_OPTIONS: ReadonlyArray<{ name: string; value: string }> = [
  { name: 'red', value: '#ef4444' },
  { name: 'orange', value: '#f97316' },
  { name: 'yellow', value: '#eab308' },
  { name: 'green', value: '#22c55e' },
  { name: 'blue', value: '#3b82f6' },
  { name: 'purple', value: '#a855f7' },
  { name: 'pink', value: '#ec4899' },
  { name: 'gray', value: '#6b7280' },
];

/**
 * Material icons exposed to the icon-tag picker.
 */
export const ICON_OPTIONS: ReadonlyArray<{ name: string; icon: string }> = [
  { name: 'Heart', icon: 'favorite' },
  { name: 'Warning', icon: 'warning' },
  { name: 'Bug', icon: 'bug_report' },
  { name: 'Error', icon: 'error' },
  { name: 'Star', icon: 'star' },
  { name: 'Bolt', icon: 'bolt' },
  { name: 'Flag', icon: 'flag' },
  { name: 'Fire', icon: 'local_fire_department' },
  { name: 'Idea', icon: 'lightbulb' },
  { name: 'Rocket', icon: 'rocket_launch' },
];

/**
 * Frontend owner for instance UI preferences (pin + color tag + icon tag).
 *
 * Writes are optimistic: we mutate the local ``InstanceService.instances``
 * signal immediately so the UI re-orders / recolors without waiting for
 * the next 60s poll. If the backend rejects the call we revert the
 * optimistic change so the signal stays consistent with the server.
 *
 * The backend response (which contains authoritative ``pinned_at``)
 * replaces the optimistic timestamp when the PUT succeeds.
 */
@Injectable({
  providedIn: 'root',
})
export class InstancePrefsService {
  private readonly api = inject(ApiService);
  private readonly instanceService = inject(InstanceService);

  /**
   * Toggle pin state for an instance. ``pinned = true`` puts the row at
   * the top of its tree level; ``pinned = false`` removes it.
   */
  setPin(instanceId: string, pinned: boolean): Observable<void> {
    const previous = this.snapshotInstance(instanceId);
    // Optimistic update: assign pinned + a local pinned_at so the
    // pinned-first sort immediately moves the row. The PUT response
    // will overwrite pinned_at with the server timestamp on success.
    const optimisticPinnedAt = pinned ? new Date().toISOString() : null;
    this.applyLocalUpdate(instanceId, {
      pinned,
      pinned_at: optimisticPinnedAt,
    });

    return this.api.updateInstanceUiPrefs(instanceId, { pinned }).pipe(
      tap((response) => {
        // Reconcile with the authoritative server values.
        this.applyLocalUpdate(instanceId, {
          pinned: response.pinned,
          pinned_at: response.pinned_at,
        });
      }),
      ignoreElements(),
      catchError((err) => {
        // Roll back the optimistic change so the UI doesn't lie.
        console.error('[InstancePrefsService] setPin failed, reverting', err);
        if (previous) {
          this.applyLocalUpdate(instanceId, {
            pinned: previous.pinned ?? null,
            pinned_at: previous.pinned_at ?? null,
          });
        }
        return EMPTY;
      }),
    );
  }

  /**
   * Set or clear the color tag for an instance. Pass ``null`` to remove.
   */
  setColorTag(instanceId: string, color: string | null): Observable<void> {
    const previous = this.snapshotInstance(instanceId);
    this.applyLocalUpdate(instanceId, { color_tag: color });

    return this.api.updateInstanceUiPrefs(instanceId, { color_tag: color }).pipe(
      tap((response) => {
        this.applyLocalUpdate(instanceId, { color_tag: response.color_tag });
      }),
      ignoreElements(),
      catchError((err) => {
        console.error('[InstancePrefsService] setColorTag failed, reverting', err);
        if (previous) {
          this.applyLocalUpdate(instanceId, {
            color_tag: previous.color_tag ?? null,
          });
        }
        return EMPTY;
      }),
    );
  }

  /**
   * Set or clear the Material icon tag for an instance. Pass ``null`` to remove.
   */
  setIconTag(instanceId: string, icon: string | null): Observable<void> {
    const previous = this.snapshotInstance(instanceId);
    this.applyLocalUpdate(instanceId, { icon_tag: icon });

    return this.api.updateInstanceUiPrefs(instanceId, { icon_tag: icon }).pipe(
      tap((response) => {
        this.applyLocalUpdate(instanceId, { icon_tag: response.icon_tag });
      }),
      ignoreElements(),
      catchError((err) => {
        console.error('[InstancePrefsService] setIconTag failed, reverting', err);
        if (previous) {
          this.applyLocalUpdate(instanceId, {
            icon_tag: previous.icon_tag ?? null,
          });
        }
        return EMPTY;
      }),
    );
  }

  /**
   * Clear both the color and icon tags for an instance.
   */
  clearAllTags(instanceId: string): Observable<void> {
    const previous = this.snapshotInstance(instanceId);
    this.applyLocalUpdate(instanceId, {
      color_tag: null,
      icon_tag: null,
    });

    return this.api.updateInstanceUiPrefs(instanceId, {
      color_tag: null,
      icon_tag: null,
    }).pipe(
      tap((response) => {
        this.applyLocalUpdate(instanceId, {
          color_tag: response.color_tag,
          icon_tag: response.icon_tag,
        });
      }),
      ignoreElements(),
      catchError((err) => {
        console.error('[InstancePrefsService] clearAllTags failed, reverting', err);
        if (previous) {
          this.applyLocalUpdate(instanceId, {
            color_tag: previous.color_tag ?? null,
            icon_tag: previous.icon_tag ?? null,
          });
        }
        return EMPTY;
      }),
    );
  }

  /**
   * Reset all UI preferences (pin + color tag + icon tag) for an instance.
   * Used by future "clear all" UI affordances; kept here so any
   * mutation goes through the same optimistic path.
   */
  reset(instanceId: string): Observable<void> {
    const previous = this.snapshotInstance(instanceId);
    this.applyLocalUpdate(instanceId, {
      pinned: null,
      pinned_at: null,
      color_tag: null,
      icon_tag: null,
    });

    return this.api.resetInstanceUiPrefs(instanceId).pipe(
      ignoreElements(),
      catchError((err) => {
        console.error('[InstancePrefsService] reset failed, reverting', err);
        if (previous) {
          this.applyLocalUpdate(instanceId, {
            pinned: previous.pinned ?? null,
            pinned_at: previous.pinned_at ?? null,
            color_tag: previous.color_tag ?? null,
            icon_tag: previous.icon_tag ?? null,
          });
        }
        return EMPTY;
      }),
    );
  }

  /**
   * Capture the current state of a single instance so we can revert on
   * failure. Returns ``null`` if the instance is no longer in the local
   * signal (e.g. it was removed by an SSE / poll event mid-flight).
   */
  private snapshotInstance(instanceId: string): InstanceInfo | null {
    return (
      this.instanceService.instances().find((i) => i.instance_id === instanceId) ?? null
    );
  }

  /**
   * Patch a single instance in the ``InstanceService.instances`` signal.
   * Only the provided keys are touched; everything else (status,
   * created_at, children, ...) is preserved so the optimistic update
   * doesn't disturb other state.
   */
  private applyLocalUpdate(
    instanceId: string,
    patch: Partial<Pick<InstanceInfo, 'pinned' | 'pinned_at' | 'color_tag' | 'icon_tag'>>,
  ): void {
    this.instanceService.instances.update((instances) =>
      instances.map((instance) =>
        instance.instance_id === instanceId ? { ...instance, ...patch } : instance,
      ),
    );
  }
}
