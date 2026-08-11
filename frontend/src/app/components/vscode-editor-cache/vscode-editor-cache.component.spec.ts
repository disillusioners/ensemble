import { ComponentFixture, TestBed } from '@angular/core/testing';
import { Component, signal } from '@angular/core';
import { By } from '@angular/platform-browser';
import { provideHttpClient } from '@angular/common/http';
import { provideHttpClientTesting } from '@angular/common/http/testing';
import { provideNoopAnimations } from '@angular/platform-browser/animations';
import { provideRouter } from '@angular/router';
import { VsCodeEditorCacheComponent } from './vscode-editor-cache.component';
import { VsCodeViewerComponent } from '../vscode-viewer/vscode-viewer.component';

/**
 * Lightweight host component that mirrors the workspace template's
 * binding shape: `<app-vscode-editor-cache [projectId]="…" [workdir]="…" />`.
 * Lets the tests drive the cache through the same signal-input surface
 * the real workspace uses.
 */
@Component({
  standalone: true,
  imports: [VsCodeEditorCacheComponent],
  template: `
    <app-vscode-editor-cache
      [projectId]="projectId()"
      [workdir]="workdir()"
    ></app-vscode-editor-cache>
  `,
})
class VsCodeEditorCacheHostComponent {
  projectId = signal<string>('');
  workdir = signal<string>('');
}

/**
 * Tests for `VsCodeEditorCacheComponent`.
 *
 * The cache is a pure performance wrapper — same DOM, same semantics
 * as a single `<app-vscode-viewer>` on a cache miss, but multiple
 * instances held alive on cache hits. The assertions therefore probe
 * three observable surfaces:
 *
 *   1. The DOM count of `<app-vscode-viewer>` hosts — proves
 *      create/destroy events.
 *   2. The host element's `display` style — proves the visibility
 *      toggle.
 *   3. The cache's public accessors (`cacheSize()`, `hasCached()`) —
 *      proves the LRU bookkeeping without exposing internals.
 *
 * `VsCodeViewerComponent` is used as-is. The cache creates instances
 * of it via `viewContainerRef.createComponent`; the tests do not
 * stub or mock the viewer.
 */
describe('VsCodeEditorCacheComponent', () => {
  /**
   * Resolve the cache component instance from the host fixture's
   * anchor element. The cache is a child of the host, so the test
   * needs to descend one level.
   */
  function getCacheComponent(hostFixture: ComponentFixture<VsCodeEditorCacheHostComponent>): VsCodeEditorCacheComponent {
    return hostFixture.debugElement.query(
      By.directive(VsCodeEditorCacheComponent),
    ).componentInstance as VsCodeEditorCacheComponent;
  }

  /**
   * Count the `<app-vscode-viewer>` host elements currently in the
   * DOM. Each one is a live instance in the cache.
   */
  function countViewers(hostFixture: ComponentFixture<VsCodeEditorCacheHostComponent>): number {
    return hostFixture.debugElement.queryAll(By.css('app-vscode-viewer')).length;
  }

  /**
   * Resolve the `<app-vscode-viewer>` host element whose
   * `projectId` signal input matches `pid`. Returns the host DOM
   * element so the test can probe its style.
   */
  function findViewerFor(
    hostFixture: ComponentFixture<VsCodeEditorCacheHostComponent>,
    pid: string,
  ): HTMLElement | null {
    const viewers = hostFixture.debugElement.queryAll(By.directive(VsCodeViewerComponent));
    for (const v of viewers) {
      const inst = v.componentInstance as VsCodeViewerComponent;
      if (inst.projectId() === pid) {
        return v.nativeElement as HTMLElement;
      }
    }
    return null;
  }

  let hostFixture: ComponentFixture<VsCodeEditorCacheHostComponent>;
  let host: VsCodeEditorCacheHostComponent;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [VsCodeEditorCacheHostComponent],
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        provideNoopAnimations(),
        provideRouter([]),
      ],
    }).compileComponents();

    hostFixture = TestBed.createComponent(VsCodeEditorCacheHostComponent);
    host = hostFixture.componentInstance;
  });

  // Explicitly destroy the fixture between tests so iframes and DOM
  // nodes from one test do not leak into the next. Without this, the
  // `ngOnDestroy` assertion below would see viewers left behind by
  // earlier tests (whose fixtures were never torn down).
  afterEach(() => {
    hostFixture.destroy();
  });

  // 1. Cache miss creates a new instance ─────────────────────────────

  describe('cache miss', () => {
    it('creates a VsCodeViewerComponent on the first project ID', () => {
      host.projectId.set('proj-1');
      hostFixture.detectChanges();

      expect(countViewers(hostFixture)).toBe(1);
      expect(getCacheComponent(hostFixture).cacheSize()).toBe(1);
      expect(getCacheComponent(hostFixture).hasCached('proj-1')).toBe(true);
    });

    it('initialises the new viewer with the active projectId and workdir', () => {
      host.projectId.set('proj-1');
      host.workdir.set('/path/to/proj-1');
      hostFixture.detectChanges();

      const viewer = hostFixture.debugElement.query(
        By.directive(VsCodeViewerComponent),
      ).componentInstance as VsCodeViewerComponent;
      expect(viewer.projectId()).toBe('proj-1');
      expect(viewer.workdir()).toBe('/path/to/proj-1');
    });

    it('creates a new instance for each new project ID', () => {
      host.projectId.set('proj-1');
      hostFixture.detectChanges();
      host.projectId.set('proj-2');
      hostFixture.detectChanges();
      host.projectId.set('proj-3');
      hostFixture.detectChanges();

      expect(countViewers(hostFixture)).toBe(3);
      expect(getCacheComponent(hostFixture).cacheSize()).toBe(3);
    });

    it('does not create an instance when projectId is empty', () => {
      hostFixture.detectChanges();

      expect(countViewers(hostFixture)).toBe(0);
      expect(getCacheComponent(hostFixture).cacheSize()).toBe(0);
    });
  });

  // 2. Cache hit shows an existing instance ──────────────────────────

  describe('cache hit', () => {
    it('returns the same instance when switching back to a cached project', () => {
      host.projectId.set('proj-1');
      hostFixture.detectChanges();
      const firstRef = hostFixture.debugElement.query(
        By.directive(VsCodeViewerComponent),
      ).componentInstance as VsCodeViewerComponent;

      host.projectId.set('proj-2');
      hostFixture.detectChanges();
      host.projectId.set('proj-1');
      hostFixture.detectChanges();

      // No new instance — same component ref still hooked up.
      const afterRef = hostFixture.debugElement.queryAll(By.directive(VsCodeViewerComponent))
        .map((de) => de.componentInstance as VsCodeViewerComponent)
        .find((v) => v.projectId() === 'proj-1');
      expect(afterRef).toBe(firstRef);
      expect(countViewers(hostFixture)).toBe(2);
      expect(getCacheComponent(hostFixture).cacheSize()).toBe(2);
    });
  });

  // 3. Hidden instances stay in DOM ──────────────────────────────────

  describe('hidden instances stay in DOM', () => {
    it('keeps non-active viewers in the DOM but with display: none', () => {
      host.projectId.set('proj-1');
      hostFixture.detectChanges();
      host.projectId.set('proj-2');
      hostFixture.detectChanges();

      expect(countViewers(hostFixture)).toBe(2);

      const activeEl = findViewerFor(hostFixture, 'proj-2');
      const hiddenEl = findViewerFor(hostFixture, 'proj-1');
      expect(activeEl).not.toBeNull();
      expect(hiddenEl).not.toBeNull();

      // Active project is visible…
      expect(activeEl!.style.display).toBe('block');
      // …while the cached-but-not-active one is hidden via inline
      // style. `display: none` keeps the iframe in the DOM and alive
      // (scroll, open file, terminal panes) without consuming layout.
      expect(hiddenEl!.style.display).toBe('none');
    });
  });

  // 4 + 5. LRU eviction at capacity ──────────────────────────────────

  describe('LRU eviction at capacity (MAX = 3)', () => {
    beforeEach(() => {
      host.workdir.set('/shared/dir');
    });

    it('keeps the cache at or below the cap when a 4th project is added', () => {
      host.projectId.set('proj-1');
      hostFixture.detectChanges();
      host.projectId.set('proj-2');
      hostFixture.detectChanges();
      host.projectId.set('proj-3');
      hostFixture.detectChanges();

      expect(getCacheComponent(hostFixture).cacheSize()).toBe(3);

      host.projectId.set('proj-4');
      hostFixture.detectChanges();

      // Eviction fires — total stays at 3.
      expect(getCacheComponent(hostFixture).cacheSize()).toBe(3);
      expect(countViewers(hostFixture)).toBe(3);
    });

    it('evicts the least-recently-used entry (the first inserted)', () => {
      host.projectId.set('proj-1');
      hostFixture.detectChanges();
      host.projectId.set('proj-2');
      hostFixture.detectChanges();
      host.projectId.set('proj-3');
      hostFixture.detectChanges();
      // Touch proj-1 to make it MRU; proj-2 is now the LRU.
      host.projectId.set('proj-1');
      hostFixture.detectChanges();

      // Cache ordering just before eviction: proj-2 (LRU), proj-3, proj-1 (MRU).
      host.projectId.set('proj-4');
      hostFixture.detectChanges();

      // proj-2 was the LRU and should be evicted.
      expect(getCacheComponent(hostFixture).hasCached('proj-2')).toBe(false);
      expect(getCacheComponent(hostFixture).hasCached('proj-1')).toBe(true);
      expect(getCacheComponent(hostFixture).hasCached('proj-3')).toBe(true);
      expect(getCacheComponent(hostFixture).hasCached('proj-4')).toBe(true);
    });

    it('destroys the evicted component ref (its DOM element is removed AND the cache entry is dropped)', () => {
      host.projectId.set('proj-1');
      hostFixture.detectChanges();
      host.projectId.set('proj-2');
      hostFixture.detectChanges();
      host.projectId.set('proj-3');
      hostFixture.detectChanges();

      const cache = getCacheComponent(hostFixture);
      expect(cache.cacheSize()).toBe(3);
      expect(countViewers(hostFixture)).toBe(3);

      host.projectId.set('proj-4');
      hostFixture.detectChanges();

      // The evicted project-1's host element is gone from the DOM —
      // Angular's `ComponentRef.destroy()` removes the host node.
      expect(findViewerFor(hostFixture, 'proj-1')).toBeNull();
      // The other three are still around.
      expect(findViewerFor(hostFixture, 'proj-2')).not.toBeNull();
      expect(findViewerFor(hostFixture, 'proj-3')).not.toBeNull();
      expect(findViewerFor(hostFixture, 'proj-4')).not.toBeNull();
      // The cache entry is also gone. Together with the DOM check
      // above, this proves `ref.destroy()` was actually called —
      // without destroy() the ComponentRef would still be tracked
      // and its host element would still be in the view tree.
      expect(cache.hasCached('proj-1')).toBe(false);
      expect(cache.hasCached('proj-2')).toBe(true);
      expect(cache.hasCached('proj-3')).toBe(true);
      expect(cache.hasCached('proj-4')).toBe(true);
    });

    it('MRU promotion reorders the Map so the re-activated project becomes the new MRU', () => {
      // Initial fill: proj-1, proj-2, proj-3 in insertion order
      // (LRU → MRU).
      host.projectId.set('proj-1');
      hostFixture.detectChanges();
      host.projectId.set('proj-2');
      hostFixture.detectChanges();
      host.projectId.set('proj-3');
      hostFixture.detectChanges();

      // Re-activate proj-1 (cache hit). `promoteToMru` does
      // `delete` + `set` to move the key to the Map tail. The
      // insertion order is now proj-2, proj-3, proj-1.
      host.projectId.set('proj-1');
      hostFixture.detectChanges();

      // Adding proj-4 must evict the head of the iteration order:
      // proj-2 (the new LRU). If MRU promotion were broken, the
      // first-inserted key (proj-1) would still be the LRU and
      // would be evicted instead — a regression that would defeat
      // the cache for the most recently-viewed project.
      host.projectId.set('proj-4');
      hostFixture.detectChanges();

      const cache = getCacheComponent(hostFixture);
      expect(cache.hasCached('proj-1')).toBe(true); // MRU, not evicted
      expect(cache.hasCached('proj-2')).toBe(false); // new LRU, evicted
      expect(cache.hasCached('proj-3')).toBe(true);
      expect(cache.hasCached('proj-4')).toBe(true);
    });

    it('does not evict when the cache is exactly at capacity', () => {
      // Populate exactly 3 (the cap).
      host.projectId.set('proj-1');
      hostFixture.detectChanges();
      host.projectId.set('proj-2');
      hostFixture.detectChanges();
      host.projectId.set('proj-3');
      hostFixture.detectChanges();

      const cache = getCacheComponent(hostFixture);
      expect(cache.cacheSize()).toBe(3);

      // Re-activating any cached project is a cache hit, so the
      // cache-miss eviction branch does not run — no eviction.
      host.projectId.set('proj-1');
      hostFixture.detectChanges();
      expect(cache.cacheSize()).toBe(3);
      expect(cache.hasCached('proj-1')).toBe(true);
      expect(cache.hasCached('proj-2')).toBe(true);
      expect(cache.hasCached('proj-3')).toBe(true);

      // Another cache hit — still no eviction, still 3 entries.
      host.projectId.set('proj-2');
      hostFixture.detectChanges();
      expect(cache.cacheSize()).toBe(3);
      expect(cache.hasCached('proj-1')).toBe(true);
      expect(cache.hasCached('proj-2')).toBe(true);
      expect(cache.hasCached('proj-3')).toBe(true);
    });

    it('promotes the active project to MRU so a re-access does not evict it', () => {
      host.projectId.set('proj-1');
      hostFixture.detectChanges();
      host.projectId.set('proj-2');
      hostFixture.detectChanges();
      host.projectId.set('proj-3');
      hostFixture.detectChanges();

      // proj-1 is the LRU. Switch back to it — promote to MRU.
      host.projectId.set('proj-1');
      hostFixture.detectChanges();
      expect(getCacheComponent(hostFixture).hasCached('proj-1')).toBe(true);

      // Adding a 4th should now evict proj-2 (the new LRU), not proj-1.
      host.projectId.set('proj-4');
      hostFixture.detectChanges();

      expect(getCacheComponent(hostFixture).hasCached('proj-1')).toBe(true);
      expect(getCacheComponent(hostFixture).hasCached('proj-2')).toBe(false);
    });

    it('does not evict when re-activating the only cached project', () => {
      // When the cache holds only the active project and the caller
      // re-activates the same projectId, no eviction is needed —
      // the cache-miss branch does not run and capacity is not
      // breached. The active project is always at the MRU tail after
      // promotion, so this test exercises the no-op promote path.
      host.projectId.set('proj-1');
      hostFixture.detectChanges();
      expect(getCacheComponent(hostFixture).cacheSize()).toBe(1);

      // Re-activating the same project — capacity is not breached, so
      // no eviction is needed.
      host.projectId.set('proj-1');
      hostFixture.detectChanges();
      expect(getCacheComponent(hostFixture).cacheSize()).toBe(1);
      expect(getCacheComponent(hostFixture).hasCached('proj-1')).toBe(true);
    });
  });

  // 6. projectId switching toggles visibility ────────────────────────

  describe('projectId switching visibility', () => {
    it('hides the previously-active viewer and shows the new one', () => {
      host.projectId.set('proj-1');
      hostFixture.detectChanges();
      host.projectId.set('proj-2');
      hostFixture.detectChanges();

      const proj1 = findViewerFor(hostFixture, 'proj-1');
      const proj2 = findViewerFor(hostFixture, 'proj-2');
      expect(proj1!.style.display).toBe('none');
      expect(proj2!.style.display).toBe('block');
    });

    it('shows exactly one viewer at any time, even with multiple cached', () => {
      host.projectId.set('proj-1');
      hostFixture.detectChanges();
      host.projectId.set('proj-2');
      hostFixture.detectChanges();
      host.projectId.set('proj-3');
      hostFixture.detectChanges();

      const visibleCount = hostFixture.debugElement
        .queryAll(By.css('app-vscode-viewer'))
        .filter((de) => (de.nativeElement as HTMLElement).style.display !== 'none').length;
      expect(visibleCount).toBe(1);
    });

    it('returns the previously-active viewer to visible on a hit', () => {
      host.projectId.set('proj-1');
      hostFixture.detectChanges();
      host.projectId.set('proj-2');
      hostFixture.detectChanges();

      // proj-1 is now hidden.
      expect(findViewerFor(hostFixture, 'proj-1')!.style.display).toBe('none');

      host.projectId.set('proj-1');
      hostFixture.detectChanges();

      // proj-1 is visible again, proj-2 is hidden.
      expect(findViewerFor(hostFixture, 'proj-1')!.style.display).toBe('block');
      expect(findViewerFor(hostFixture, 'proj-2')!.style.display).toBe('none');
    });

    it('hides all cached viewers when projectId becomes empty (preserving the cache)', () => {
      // Populate the cache with two live viewers.
      host.projectId.set('proj-1');
      hostFixture.detectChanges();
      host.projectId.set('proj-2');
      hostFixture.detectChanges();

      const cache = getCacheComponent(hostFixture);
      expect(cache.cacheSize()).toBe(2);
      // proj-2 is the active project, proj-1 is hidden but cached.
      expect(findViewerFor(hostFixture, 'proj-2')!.style.display).toBe('block');
      expect(findViewerFor(hostFixture, 'proj-1')!.style.display).toBe('none');

      // Switch to an empty projectId — no active project. Without
      // hideAll(), the previously-active viewer would leak a visible
      // iframe with no active project. The cache entries are
      // preserved so a switch back is still instant.
      host.projectId.set('');
      hostFixture.detectChanges();

      expect(cache.cacheSize()).toBe(2); // cache preserved
      expect(findViewerFor(hostFixture, 'proj-1')!.style.display).toBe('none');
      expect(findViewerFor(hostFixture, 'proj-2')!.style.display).toBe('none');
    });
  });

  // 7. ngOnDestroy cleans up all instances ───────────────────────────

  describe('ngOnDestroy cleanup', () => {
    it('flushes the cache map on destroy so no references leak', () => {
      host.projectId.set('proj-1');
      hostFixture.detectChanges();
      host.projectId.set('proj-2');
      hostFixture.detectChanges();

      const cache = getCacheComponent(hostFixture);
      expect(cache.cacheSize()).toBe(2);

      hostFixture.destroy();

      // The cache component is destroyed; its `ngOnDestroy` runs and
      // empties the internal map. The DOM detachment is a consequence
      // of `ComponentRef.destroy()` which is covered by the LRU
      // eviction test above (the evicted viewer's host element is
      // removed from the DOM, as verified by `findViewerFor`).
      expect(cache.cacheSize()).toBe(0);
    });

    it('calls ComponentRef.destroy() on every cached instance during ngOnDestroy', () => {
      // Populate the cache with three live viewers.
      host.projectId.set('proj-1');
      hostFixture.detectChanges();
      host.projectId.set('proj-2');
      hostFixture.detectChanges();
      host.projectId.set('proj-3');
      hostFixture.detectChanges();

      const cache = getCacheComponent(hostFixture);
      expect(cache.cacheSize()).toBe(3);

      // Reach the private `_cache` Map via reflection so we can
      // install destroy() spies before ngOnDestroy runs. The public
      // API exposes only `cacheSize()` and `hasCached()` — neither
      // surfaces the underlying `ComponentRef`s, so spies need
      // direct access to the Map. We deliberately do NOT use
      // `hostFixture.destroy()` here: that path also tears down
      // children via Angular's lifecycle, which would call
      // `ref.destroy()` itself and falsely satisfy the spies
      // regardless of whether our `ngOnDestroy` did the work.
      const internalCache = (cache as unknown as {
        _cache: Map<string, ComponentRef<VsCodeViewerComponent>>;
      })._cache;
      const destroySpies = Array.from(internalCache.values()).map((ref) =>
        jest.spyOn(ref, 'destroy'),
      );
      expect(destroySpies.length).toBe(3);

      // Call ngOnDestroy directly. The internal loop must call
      // `ref.destroy()` on every entry — without it, the iframes
      // would leak until the browser tab closed.
      cache.ngOnDestroy();

      for (const spy of destroySpies) {
        expect(spy).toHaveBeenCalled();
      }
    });
  });

  // workdir forwarding — natural extension of the public contract ────

  describe('workdir forwarding', () => {
    it('forwards a workdir change to the active instance', () => {
      host.projectId.set('proj-1');
      hostFixture.detectChanges();
      host.workdir.set('/path/a');
      hostFixture.detectChanges();

      const viewer = hostFixture.debugElement.query(
        By.directive(VsCodeViewerComponent),
      ).componentInstance as VsCodeViewerComponent;
      expect(viewer.workdir()).toBe('/path/a');

      host.workdir.set('/path/b');
      hostFixture.detectChanges();

      expect(viewer.workdir()).toBe('/path/b');
    });

    it('does not forward workdir to a non-active cached instance', () => {
      host.projectId.set('proj-1');
      host.workdir.set('/path/a');
      hostFixture.detectChanges();
      host.projectId.set('proj-2');
      host.workdir.set('/path/b');
      hostFixture.detectChanges();

      const proj1 = hostFixture.debugElement.queryAll(By.directive(VsCodeViewerComponent))
        .map((de) => de.componentInstance as VsCodeViewerComponent)
        .find((v) => v.projectId() === 'proj-1')!;
      // proj-1 was created with /path/a and the workdir effect only
      // targets the active instance — proj-1's workdir must remain
      // unchanged.
      expect(proj1.workdir()).toBe('/path/a');
    });

    it('does not forward the stale workdir when switching back to a cached project', () => {
      // Reproduces the cache-defeating reload bug: when the user
      // switches projects, `projectId` changes synchronously but
      // `workdir` still holds the OUTGOING project's path until the
      // async HTTP round-trip completes. Without the guard, the
      // workdir effect would re-fire on the projectId change and
      // forward the stale outgoing workdir to the newly-activated
      // cached instance, changing its iframe src and triggering a
      // reload. With the guard, the workdir effect is a no-op when
      // `workdir` hasn't changed since the last forward.

      // 1. Activate proj-a with its own workdir (cache miss).
      host.projectId.set('proj-a');
      host.workdir.set('/path/a');
      hostFixture.detectChanges();

      const viewerA = hostFixture.debugElement.queryAll(By.directive(VsCodeViewerComponent))
        .map((de) => de.componentInstance as VsCodeViewerComponent)
        .find((v) => v.projectId() === 'proj-a')!;
      expect(viewerA.workdir()).toBe('/path/a');

      // 2. Switch to proj-b with its own workdir (cache miss for b).
      host.projectId.set('proj-b');
      host.workdir.set('/path/b');
      hostFixture.detectChanges();

      // 3. Switch back to proj-a WITHOUT yet updating workdir to
      //    /path/a (simulates the HTTP round-trip still in flight:
      //    `workdir` still holds the OUTGOING proj-b value).
      host.projectId.set('proj-a');
      // Note: workdir is NOT updated here — still '/path/b' from step 2.
      hostFixture.detectChanges();

      // proj-a's viewer must still have its ORIGINAL workdir, NOT
      // the stale outgoing proj-b workdir. The guard must skip the
      // forward because `workdir()` has not changed since the last
      // time the effect forwarded (which was for proj-a in step 1).
      expect(viewerA.workdir()).toBe('/path/a');

      // 4. Now the HTTP round-trip completes — workdir updates to
      //    /path/a. The guard sees this is the same value last
      //    forwarded (when proj-a was first activated), so the
      //    effect is a no-op and the iframe does NOT reload.
      host.workdir.set('/path/a');
      hostFixture.detectChanges();

      // Still '/path/a' — no change, no reload.
      expect(viewerA.workdir()).toBe('/path/a');
    });
  });
});
