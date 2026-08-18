import { Injectable, signal, WritableSignal, Signal, computed } from '@angular/core';

const STORAGE_KEY = 'ensemble-instances-view-state';

interface StoredViewState {
  activeInstanceId: string;
  activeProjectId: string;
}

/**
 * Singleton state for the Instances detail overlay.
 *
 * The instance detail view (the chat-style UI for a single instance) is
 * root-mounted inside ``.app-main`` and display-toggled, mirroring the
 * workspace overlay pattern. Centralizing the visibility + active-id
 * signals in a root-provided service lets the App root own the host
 * element while the chat page, the stub route, the nav link, and the
 * global hide button all read/write through the same API.
 *
 * Visibility contract:
 *   - ``detailVisible`` controls whether the overlay host is shown.
 *   - ``activeInstanceId`` / ``activeProjectId`` track which instance
 *     the overlay is bound to. The chat component reads them as the
 *     authoritative source — no longer ``ActivatedRoute.params``,
 *     because the root-mounted host does not receive route params.
 *   - The host element is ALWAYS mounted; the ``[visible]`` input and
 *     ``[style.display]`` binding together control whether the
 *     detail subtree's SSE / keyboard listeners activate and whether
 *     it occupies layout. This keeps the cached component instance
 *     alive across hide/show cycles.
 *
 * Persistence:
 *   - The last active instance id + project context are persisted to
 *     ``localStorage`` so the next visit can restore the cached detail
 *     via the "Instances" nav link.
 *   - ``restoreState()`` is called once at App boot (see
 *     ``App.constructor``). It seeds ``activeInstanceId`` and
 *     ``activeProjectId`` so the nav link's ``lastDetailRoute`` is
 *     available immediately, but it intentionally leaves
 *     ``detailVisible`` at its default ``false`` value — the URL
 *     remains the source of truth for actual visibility, so a cold
 *     reload does not auto-open the overlay.
 *   - The stub route (``InstanceDetailComponent``) does NOT call
 *     ``openDetail`` itself anymore; the App root's NavigationEnd
 *     handler is the SINGLE writer (W5). Boot-time restore and
 *     runtime URL navigation are reconciled through the same path.
 *   - ``clearInstance()`` is called when an instance is terminated so
 *     a dead id is never restored. Lazy validation: if the cached id
 *     is later found to be absent from the loaded instances list, the
 *     chat page calls ``clearInstance`` again to drop the dead cache.
 *   - ``localStorage`` access is wrapped in try/catch (W3) so a Safari
 *     private-mode quota error or a quota-exhausted failure does not
 *     bubble up to the NavigationEnd subscriber and break routing.
 */
@Injectable({
  providedIn: 'root'
})
export class InstancesViewStateService {
  /**
   * Whether the detail overlay is currently visible. The host element
   * binds `[style.display]` and `[visible]` to this signal so the
   * chat subtree's hide/show conventions (SSE disconnect, keyboard
   * gating) take effect.
   */
  readonly detailVisible: WritableSignal<boolean> = signal(false);

  /**
   * The id of the instance currently shown in the detail overlay.
   * ``null`` when no instance is cached. The chat component reads
   * this signal as the authoritative source of the current instance
   * id (replacing the previous ``ActivatedRoute.params`` subscription).
   */
  readonly activeInstanceId: WritableSignal<string | null> = signal(null);

  /**
   * The project context the detail overlay is bound to. The chat
   * page uses this to scope the instance list / sidebar. ``'all'``
   * when the instance is not bound to a specific project (the
   * pseudo-project used by the "All" tab).
   */
  readonly activeProjectId: WritableSignal<string> = signal('all');

  /**
   * Computed hint for the "Instances" nav link: when the user last
   * had a detail view open, the nav link should restore them to that
   * detail route instead of the bare ``/instances`` list. Returns
   * ``null`` when no detail cache exists so the caller can fall back
   * to plain ``/instances``.
   */
  readonly lastDetailRoute: Signal<string[] | null> = computed(() => {
    const id = this.activeInstanceId();
    if (!id) return null;
    return ['/projects', this.activeProjectId(), 'instances', id];
  });

  /**
   * Open the detail overlay for ``(projectId, instanceId)`` and
   * persist the state. The chat component reads the active ids as
   * signals and re-runs its load flow when they change.
   */
  openDetail(projectId: string, instanceId: string): void {
    this.activeProjectId.set(projectId || 'all');
    this.activeInstanceId.set(instanceId);
    this.detailVisible.set(true);
    this.saveState();
  }

  /**
   * Hide the detail overlay. The active instance id is preserved so
   * a subsequent reopen (e.g. via the cached nav link) restores the
   * same content. If the caller wants to also forget the cache, call
   * ``clearInstance(activeInstanceId)`` afterwards.
   */
  closeDetail(): void {
    this.detailVisible.set(false);
  }

  /**
   * Drop the cached instance id from memory and storage. Called when
   * an instance is terminated so the persisted id is never restored
   * to a dead instance. The detail view is also hidden if the
   * terminated instance is the one currently shown.
   */
  clearInstance(instanceId: string): void {
    if (this.activeInstanceId() !== instanceId) return;
    this.activeInstanceId.set(null);
    this.detailVisible.set(false);
    this.saveState();
  }

  /**
   * Restore the persisted instance id and project context from
   * ``localStorage``. Called once at App boot so the "Instances" nav
   * link's ``lastDetailRoute`` computed has a value to return even
   * before the first NavigationEnd fires (which would otherwise mean
   * the nav link falls back to plain ``/instances``).
   *
   * Important behavior:
   *   - Restores ONLY ``activeInstanceId`` and ``activeProjectId``.
   *   - Does NOT flip ``detailVisible`` — the URL is the source of
   *     truth for visibility, and the overlay should stay hidden on a
   *     cold reload until the user explicitly opens it.
   *   - If a later REST load (e.g. from ``ChatComponent``) discovers
   *     the restored id is no longer present, ``clearInstance`` drops
   *     the dead cache. See ``App.constructor`` for the boot call.
   */
  restoreState(): void {
    let stored: string | null = null;
    try {
      stored = localStorage.getItem(STORAGE_KEY);
    } catch (err) {
      // Safari private mode + other restricted contexts can throw on
      // localStorage access; treat as no-persisted-state and continue.
      console.warn('[InstancesViewStateService] localStorage.getItem unavailable:', err);
      return;
    }
    if (!stored) return;
    // F7: validate payload shape BEFORE applying any field. A JSON-valid
    // but shape-invalid payload (wrong types, missing required keys)
    // must not be partially applied — and the localStorage key must be
    // dropped so the poison doesn't survive the next reload. The
    // previous implementation only removed the key on JSON.parse
    // throw, so a payload like ``{ "foo": 1 }`` would be silently
    // ignored but KEPT, then re-parsed on every subsequent reload.
    let state: StoredViewState | null = null;
    try {
      state = JSON.parse(stored) as StoredViewState;
    } catch (err) {
      // Corrupt JSON: drop the key and bail.
      console.warn('[InstancesViewStateService] localStorage payload corrupt, dropping:', err);
      try {
        localStorage.removeItem(STORAGE_KEY);
      } catch (removeErr) {
        console.warn('[InstancesViewStateService] localStorage.removeItem unavailable:', removeErr);
      }
      return;
    }

    // Shape validation: activeInstanceId must be a non-empty string;
    // activeProjectId is optional (defaults to 'all' when absent or
    // null) but if present must be a string. ANY invalid shape drops
    // the key — a poisoned payload must not survive a reload.
    const idOk =
      !!state &&
      typeof (state as { activeInstanceId?: unknown }).activeInstanceId === 'string' &&
      ((state as { activeInstanceId: string }).activeInstanceId ?? '').length > 0;
    const projectOk =
      !state ||
      (state as { activeProjectId?: unknown }).activeProjectId === undefined ||
      (state as { activeProjectId?: unknown }).activeProjectId === null ||
      typeof (state as { activeProjectId?: unknown }).activeProjectId === 'string';
    if (!idOk || !projectOk) {
      console.warn('[InstancesViewStateService] localStorage payload shape invalid, dropping:', state);
      try {
        localStorage.removeItem(STORAGE_KEY);
      } catch (removeErr) {
        console.warn('[InstancesViewStateService] localStorage.removeItem unavailable:', removeErr);
      }
      return;
    }

    // Safe to apply: shape-validated above.
    this.activeInstanceId.set(state.activeInstanceId);
    this.activeProjectId.set(state.activeProjectId ?? 'all');
  }

  private saveState(): void {
    const id = this.activeInstanceId();
    if (!id) {
      try {
        localStorage.removeItem(STORAGE_KEY);
      } catch (err) {
        console.warn('[InstancesViewStateService] localStorage.removeItem unavailable:', err);
      }
      return;
    }
    const state: StoredViewState = {
      activeInstanceId: id,
      activeProjectId: this.activeProjectId(),
    };
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
    } catch (err) {
      // Safari private mode throws on setItem when storage is
      // disabled. Log and continue — the in-memory signals are still
      // valid for the current session.
      console.warn('[InstancesViewStateService] localStorage.setItem unavailable:', err);
    }
  }
}