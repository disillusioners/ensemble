import {
  Component,
  ComponentRef,
  OnDestroy,
  ViewContainerRef,
  effect,
  input,
  untracked,
  viewChild,
} from '@angular/core';
import { VsCodeViewerComponent } from '../vscode-viewer/vscode-viewer.component';

/**
 * Maximum number of VS Code editor instances kept alive in the DOM at
 * once. VS Code iframes are RAM-heavy (each loads a full code-server
 * process into the browser), so the cap is intentionally lower than
 * the workspace state cache's five — the workspace state is cheap
 * data, the editor instance is a live iframe.
 */
const MAX_CACHED_VSCODE = 3;

/**
 * Thin LRU wrapper around multiple live `VsCodeViewerComponent`
 * instances.
 *
 * The single `<app-vscode-viewer>` previously rendered by the
 * workspace was destroyed every time the user switched projects, so
 * the iframe reloaded from scratch (2-5s wait). This component keeps
 * up to three iframes alive in the DOM and just toggles visibility
 * between them — switching back to a recently-viewed project is then
 * instant. When a fourth project is opened the LRU instance is
 * destroyed (its iframe is freed, the iframes still in the cache stay
 * alive in their current state).
 *
 * The cache uses the same Map-ordering trick as `WorkspaceService`:
 *   - `Map.set` puts the key at the tail (insertion order == recency).
 *   - The first iterated key is the least-recently-used candidate.
 *   - `delete` + `set` re-orders an existing key to the tail (MRU).
 *   - `keys().next().value` reads the LRU key in O(1).
 *
 * Visibility toggling is done by setting `display: none` on the
 * component's host element for hidden instances. The active instance
 * has `display: block` (or its inherent default). The instances stay
 * in the DOM so their iframe state — scroll, open file, terminal
 * panes — survives project switches.
 *
 * The cache is intentionally simple: no service, no persistence, no
 * cross-tab coordination. The component owns the map and dies with
 * the workspace view.
 *
 * The `VsCodeViewerComponent` itself is used untouched — this
 * component is a pure host-side performance wrapper.
 */
@Component({
  selector: 'app-vscode-editor-cache',
  standalone: true,
  imports: [VsCodeViewerComponent],
  template: `
    <div #host class="vscode-editor-cache-host"></div>
  `,
  styleUrl: './vscode-editor-cache.component.scss',
})
export class VsCodeEditorCacheComponent implements OnDestroy {
  /**
   * Currently active project ID. When this changes, the cache either
   * shows an existing instance (cache hit) or creates a new one
   * (cache miss). The affected key is promoted to MRU either way.
   */
  readonly projectId = input<string>('');

  /**
   * Validated folder path for the active project. Forwarded to the
   * active instance's `workdir` input so the iframe shows the right
   * folder after a project switch. Always paired with `projectId` —
   * a cache miss carries both into the freshly-created viewer.
   */
  readonly workdir = input<string>('');

  /**
   * View-container anchor for the dynamic viewers. Read from the
   * `<div #host>` template ref so the created components become
   * children of the host div (not siblings of this component, which
   * would put them outside the workspace's layout block).
   */
  private readonly hostVcr = viewChild('host', { read: ViewContainerRef });

  /**
   * Active instances keyed by project ID. The Map's insertion order
   * is the LRU recency list: head = oldest, tail = newest.
   */
  private readonly _cache = new Map<string, ComponentRef<VsCodeViewerComponent>>();

  /**
   * Per-project workdir cache. Records the last workdir value applied
   * to each cached viewer instance. Used by Effect 2 to detect genuine
   * workdir changes for the ACTIVE project only — without this, a
   * projectId-triggered effect re-run would forward a stale workdir
   * from the outgoing project to the newly-activated instance.
   */
  private readonly _projectWorkdirs = new Map<string, string>();

  constructor() {
    // Effect 1 — react to projectId changes. Promote-on-access (delete
    // + re-insert) kicks an existing key to the tail; create-on-miss
    // appends a new instance at the tail. Eviction runs only on miss
    // (when the cache is full) and picks the head key that is not the
    // active project. The active project is always at the tail after
    // promotion, so the LRU guard is a safety net for edge cases —
    // e.g. when the cache contains only the active project and a
    // caller disables the eviction safety net indirectly.
    effect(() => {
      const pid = this.projectId();
      const vcr = this.hostVcr();
      // `hostVcr` is undefined until the view is initialised; the
      // effect re-runs once it resolves.
      if (!vcr) return;
      // When projectId is empty (no active project), hide ALL cached
      // viewers so none leaks a visible iframe with no active
      // project. The cache entries are preserved — only visibility
      // changes — so a switch back to a recently-viewed project is
      // still instant.
      if (!pid) {
        this.hideAll();
        return;
      }

      this.activate(pid, vcr);
    });

    // Effect 2 — forward workdir changes to the active instance so
    // the iframe reacts to a freshly-validated folder path. Reads
    // `workdir` unconditionally so the effect always tracks it.
    //
    // The pid and cache reads are wrapped in `untracked` because
    // Effect 2 must NOT re-fire on a `projectId` change alone — the
    // workdir signal still holds the OUTGOING project's path during
    // a switch (the validated path arrives asynchronously). If pid
    // were tracked, a cache-hit switch (A→B→A while workdir is still
    // /path/b) would fire Effect 2 with (pid='proj-a', dir='/path/b')
    // and forward the stale outgoing value to viewerA, forcing a
    // reload that defeats the cache. With pid untracked, Effect 2
    // only fires on genuine workdir updates.
    //
    // STALE-WORKDIR GUARD: skip when we've already forwarded this
    // exact dir to this pid. `_projectWorkdirs` records the last
    // forwarded dir per project, so a no-op HTTP echo (e.g. the
    // workspace re-emitting the same workdir after validation) is
    // not forwarded a second time.
    effect(() => {
      const dir = this.workdir();

      const pid = untracked(() => this.projectId());
      const ref = untracked(() => this._cache.get(pid));
      if (!ref) return;

      const lastForProject = untracked(() => this._projectWorkdirs.get(pid) ?? '');
      if (dir === lastForProject) return;
      this._projectWorkdirs.set(pid, dir);
      ref.setInput('workdir', dir);
    });
  }

  /**
   * Show the instance for `pid`, creating it on a miss and evicting
   * the LRU if the cache is at capacity. Idempotent — calling with
   * the already-active pid only does MRU promotion + visibility
   * bookkeeping.
   */
  private activate(pid: string, vcr: ViewContainerRef): void {
    if (this._cache.has(pid)) {
      // Cache hit — promote to MRU and make sure the right one is
      // visible while hiding the others.
      this.promoteToMru(pid);
      this.applyVisibility(pid);
      return;
    }

    // Cache miss — evict LRU first so the new + retained count never
    // exceeds `MAX_CACHED_VSCODE`. Then create and insert.
    if (this._cache.size >= MAX_CACHED_VSCODE) {
      this.evictLru(pid);
    }

    const ref = vcr.createComponent(VsCodeViewerComponent);
    ref.setInput('projectId', pid);
    // Initialize with the per-project workdir if we have a record of
    // it (e.g. the viewer was previously evicted but the workdir path
    // is still known). Otherwise start empty so the iframe doesn't
    // load the wrong folder — Effect 2 will forward the validated
    // path once the HTTP completes.
    //
    // NOTE: We deliberately do NOT read `this.workdir()` here. That
    // signal still holds the OUTGOING project's path during a switch
    // (the validated path arrives asynchronously). Reading it would
    // initialize the new viewer with the wrong folder and the effect
    // dependency-tracking would also cause Effect 1 to re-fire on
    // every workdir change.
    const knownWorkdir = this._projectWorkdirs.get(pid);
    ref.setInput('workdir', knownWorkdir ?? '');
    // Sync the per-project guard so the workdir effect doesn't
    // redundantly forward the same value to the freshly-created
    // instance on its next run (the effect re-fires on any tracked
    // signal change, and `workdir` is one of them — without this
    // sync, the first effect run after creation would call
    // `setInput('workdir', …)` a second time with the identical
    // value).
    this._projectWorkdirs.set(pid, knownWorkdir ?? '');
    this._cache.set(pid, ref);
    this.applyVisibility(pid);
  }

  /**
   * Re-insert the existing key to move it to the Map tail (MRU).
   * Uses delete+set so the same key is reordered correctly — regular
   * `set` on an existing key does NOT move it in JS Map insertion
   * order, per the spec.
   */
  private promoteToMru(pid: string): void {
    const ref = this._cache.get(pid);
    if (!ref) return;
    this._cache.delete(pid);
    this._cache.set(pid, ref);
  }

  /**
   * Hide every cached instance, then show the one for `pid`. The map
   * is iterated in insertion order so the run is bounded by the cap
   * (3–4 elements).
   */
  private applyVisibility(activePid: string): void {
    for (const [key, ref] of this._cache) {
      const host = ref.location.nativeElement as HTMLElement;
      host.style.display = key === activePid ? 'block' : 'none';
      host.style.height = key === activePid ? '100%' : '';
    }
  }

  /**
   * Evict the LRU instance. The `activePid` skip is a defensive
   * safety net — in current call paths `evictLru` is only invoked
   * from the cache-miss branch where `activePid` is not yet in the
   * cache, so the guard is unreachable. Retained as protection
   * against future call-site changes.
   *
   * Also drops the evicted project's entry from `_projectWorkdirs`
   * so the per-project workdir map does not accumulate entries for
   * destroyed viewers.
   */
  private evictLru(activePid: string): void {
    for (const [key, ref] of this._cache) {
      if (key === activePid) continue;
      this._cache.delete(key);
      this._projectWorkdirs.delete(key);
      ref.destroy();
      return;
    }
  }

  /**
   * Public accessor for the cache size. Intended for tests and
   * debug tooling — production code should not need it.
   */
  cacheSize(): number {
    return this._cache.size;
  }

  /**
   * True when the cache currently holds a live instance for `pid`.
   * Used by tests to verify cache hits without probing the DOM.
   */
  hasCached(pid: string): boolean {
    return this._cache.has(pid);
  }

  /**
   * Hide every cached instance — used when no project is active so
   * no viewer leaks a visible iframe with no active project. The
   * cache entries are preserved (visibility is a presentation-layer
   * concern; the iframes stay alive so a switch back is instant).
   */
  private hideAll(): void {
    for (const ref of this._cache.values()) {
      const host = ref.location.nativeElement as HTMLElement;
      host.style.display = 'none';
    }
  }

  ngOnDestroy(): void {
    // Destroy every live instance so the iframes are torn down and
    // their host elements are removed from the DOM. Without this,
    // switching away from the workspace view would leak iframes until
    // the browser tab closes.
    for (const ref of this._cache.values()) {
      ref.destroy();
    }
    this._cache.clear();
    this._projectWorkdirs.clear();
  }
}
