import { InstancesViewStateService } from './instances-view-state.service';

const STORAGE_KEY = 'ensemble-instances-view-state';

describe('InstancesViewStateService', () => {
  let service: InstancesViewStateService;

  beforeEach(() => {
    localStorage.removeItem(STORAGE_KEY);
    service = new InstancesViewStateService();
  });

  afterEach(() => {
    localStorage.removeItem(STORAGE_KEY);
  });

  describe('initial state', () => {
    it('starts hidden with no active instance', () => {
      expect(service.detailVisible()).toBe(false);
      expect(service.activeInstanceId()).toBeNull();
      expect(service.activeProjectId()).toBe('all');
    });

    it('lastDetailRoute() returns null when no cache exists', () => {
      expect(service.lastDetailRoute()).toBeNull();
    });
  });

  describe('openDetail(projectId, instanceId)', () => {
    it('opens the overlay for the given instance and project', () => {
      service.openDetail('proj-a', 'inst-1');

      expect(service.detailVisible()).toBe(true);
      expect(service.activeInstanceId()).toBe('inst-1');
      expect(service.activeProjectId()).toBe('proj-a');
    });

    it('defaults a missing projectId to "all"', () => {
      service.openDetail('', 'inst-1');

      expect(service.activeProjectId()).toBe('all');
    });

    it('persists the active id and project to localStorage', () => {
      service.openDetail('proj-a', 'inst-1');

      const stored = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? 'null');
      expect(stored).toEqual({
        activeInstanceId: 'inst-1',
        activeProjectId: 'proj-a',
      });
    });

    it('switches the active id when called again with a different instance', () => {
      service.openDetail('proj-a', 'inst-1');
      service.openDetail('proj-a', 'inst-2');

      expect(service.activeInstanceId()).toBe('inst-2');
      expect(service.detailVisible()).toBe(true);
    });

    it('updates the persisted state when called again', () => {
      service.openDetail('proj-a', 'inst-1');
      service.openDetail('proj-b', 'inst-2');

      const stored = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? 'null');
      expect(stored.activeInstanceId).toBe('inst-2');
      expect(stored.activeProjectId).toBe('proj-b');
    });
  });

  describe('closeDetail()', () => {
    it('hides the overlay but preserves the active id', () => {
      service.openDetail('proj-a', 'inst-1');
      service.closeDetail();

      expect(service.detailVisible()).toBe(false);
      // Active id is preserved so a subsequent reopen restores the
      // same content.
      expect(service.activeInstanceId()).toBe('inst-1');
    });

    it('is safe to call when already hidden', () => {
      service.closeDetail();
      expect(service.detailVisible()).toBe(false);
    });
  });

  describe('clearInstance(instanceId)', () => {
    it('clears the active id when it matches the terminated instance', () => {
      service.openDetail('proj-a', 'inst-1');
      service.clearInstance('inst-1');

      expect(service.activeInstanceId()).toBeNull();
      expect(service.detailVisible()).toBe(false);
    });

    it('removes the persisted cache when the terminated instance is the active one', () => {
      service.openDetail('proj-a', 'inst-1');
      service.clearInstance('inst-1');

      expect(localStorage.getItem(STORAGE_KEY)).toBeNull();
    });

    it('is a no-op when the terminated instance is not the active one', () => {
      service.openDetail('proj-a', 'inst-1');
      service.clearInstance('other-inst');

      expect(service.activeInstanceId()).toBe('inst-1');
      expect(service.detailVisible()).toBe(true);
    });

    it('hides the overlay when the current detail is terminated', () => {
      service.openDetail('proj-a', 'inst-1');
      service.detailVisible.set(true);
      service.clearInstance('inst-1');

      expect(service.detailVisible()).toBe(false);
    });
  });

  describe('restoreState()', () => {
    it('does nothing when no persisted state exists', () => {
      service.restoreState();
      expect(service.activeInstanceId()).toBeNull();
    });

    it('restores the active id and project from localStorage', () => {
      localStorage.setItem(
        STORAGE_KEY,
        JSON.stringify({ activeInstanceId: 'inst-1', activeProjectId: 'proj-a' })
      );

      service.restoreState();

      expect(service.activeInstanceId()).toBe('inst-1');
      expect(service.activeProjectId()).toBe('proj-a');
    });

    it('drops the persisted entry when the JSON is corrupt', () => {
      localStorage.setItem(STORAGE_KEY, 'not-valid-json');

      service.restoreState();

      expect(service.activeInstanceId()).toBeNull();
      expect(localStorage.getItem(STORAGE_KEY)).toBeNull();
    });

    it('does NOT mark the overlay visible after restore (visibility is URL-driven)', () => {
      // The view-state service is persistence-only — actual visibility
      // is reconciled by the App root navigation listener. After
      // restoreFromStorage the detail should NOT be auto-shown.
      localStorage.setItem(
        STORAGE_KEY,
        JSON.stringify({ activeInstanceId: 'inst-1', activeProjectId: 'proj-a' })
      );

      service.restoreState();

      expect(service.detailVisible()).toBe(false);
      // But the cached id is available so the nav link can restore it.
      expect(service.lastDetailRoute()).toEqual(['/projects', 'proj-a', 'instances', 'inst-1']);
    });

    it('ignores an empty activeInstanceId', () => {
      localStorage.setItem(
        STORAGE_KEY,
        JSON.stringify({ activeInstanceId: '', activeProjectId: 'proj-a' })
      );

      service.restoreState();

      expect(service.activeInstanceId()).toBeNull();
    });

    // F7: JSON-valid but shape-invalid payloads must be rejected AND
    // the localStorage key must be dropped so the poison doesn't
    // survive the next reload. The previous implementation only
    // removed the key on JSON.parse throw, so a payload like
    // ``{"activeInstanceId": 42}`` was silently ignored but KEPT.
    it('drops the persisted entry when activeInstanceId is not a string (F7)', () => {
      localStorage.setItem(
        STORAGE_KEY,
        JSON.stringify({ activeInstanceId: 42, activeProjectId: 'proj-a' })
      );

      service.restoreState();

      // Defaults restored — no partial application of valid fields.
      expect(service.activeInstanceId()).toBeNull();
      expect(service.activeProjectId()).toBe('all');
      // Key is dropped so the poison doesn't survive a reload.
      expect(localStorage.getItem(STORAGE_KEY)).toBeNull();
    });

    it('drops the persisted entry when activeProjectId is a non-string (F7)', () => {
      localStorage.setItem(
        STORAGE_KEY,
        JSON.stringify({ activeInstanceId: 'inst-1', activeProjectId: 7 })
      );

      service.restoreState();

      expect(service.activeInstanceId()).toBeNull();
      expect(service.activeProjectId()).toBe('all');
      expect(localStorage.getItem(STORAGE_KEY)).toBeNull();
    });

    it('drops the persisted entry when payload is an empty object (F7)', () => {
      // JSON-valid (parses fine) but missing the required
      // activeInstanceId field. The previous implementation
      // silently ignored it but KEPT it on every reload.
      localStorage.setItem(STORAGE_KEY, JSON.stringify({}));

      service.restoreState();

      expect(service.activeInstanceId()).toBeNull();
      expect(service.activeProjectId()).toBe('all');
      expect(localStorage.getItem(STORAGE_KEY)).toBeNull();
    });

    it('accepts an absent activeProjectId (defaults to "all")', () => {
      // F7: activeProjectId is optional. A payload with only
      // activeInstanceId is valid and falls back to 'all'.
      localStorage.setItem(
        STORAGE_KEY,
        JSON.stringify({ activeInstanceId: 'inst-1' })
      );

      service.restoreState();

      expect(service.activeInstanceId()).toBe('inst-1');
      expect(service.activeProjectId()).toBe('all');
    });

    it('accepts a null activeProjectId (defaults to "all")', () => {
      // F7: explicit null is treated the same as absent.
      localStorage.setItem(
        STORAGE_KEY,
        JSON.stringify({ activeInstanceId: 'inst-1', activeProjectId: null })
      );

      service.restoreState();

      expect(service.activeInstanceId()).toBe('inst-1');
      expect(service.activeProjectId()).toBe('all');
    });
  });

  describe('lastDetailRoute()', () => {
    it('returns null when no cache exists', () => {
      expect(service.lastDetailRoute()).toBeNull();
    });

    it('returns the cached detail route when an instance is active', () => {
      service.openDetail('proj-a', 'inst-1');

      expect(service.lastDetailRoute()).toEqual(['/projects', 'proj-a', 'instances', 'inst-1']);
    });

    it('reflects the current cached instance after multiple opens', () => {
      service.openDetail('proj-a', 'inst-1');
      service.openDetail('proj-b', 'inst-2');

      expect(service.lastDetailRoute()).toEqual(['/projects', 'proj-b', 'instances', 'inst-2']);
    });

    it('returns null after the cached instance is cleared', () => {
      service.openDetail('proj-a', 'inst-1');
      service.clearInstance('inst-1');

      expect(service.lastDetailRoute()).toBeNull();
    });
  });

  describe('save/restore roundtrip', () => {
    it('roundtrips the active id and project through localStorage', () => {
      service.openDetail('proj-a', 'inst-1');

      // Fresh service that re-reads from localStorage
      const restored = new InstancesViewStateService();
      restored.restoreState();

      expect(restored.activeInstanceId()).toBe('inst-1');
      expect(restored.activeProjectId()).toBe('proj-a');
    });
  });

  describe('restoreState() — localStorage failure handling (W3)', () => {
    let warnSpy: jest.SpyInstance;
    beforeEach(() => {
      warnSpy = jest.spyOn(console, 'warn').mockImplementation(() => {});
    });
    afterEach(() => {
      warnSpy.mockRestore();
    });

    it('does not throw when localStorage.getItem is unavailable (Safari private mode)', () => {
      // Simulate Safari private-mode: every storage call throws.
      // restoreState must log+continue, leaving the service untouched.
      const originalGetItem = Storage.prototype.getItem;
      Storage.prototype.getItem = jest.fn(() => {
        throw new Error('QuotaExceededError');
      });

      try {
        expect(() => service.restoreState()).not.toThrow();
      } finally {
        Storage.prototype.getItem = originalGetItem;
      }

      expect(service.activeInstanceId()).toBeNull();
      expect(service.detailVisible()).toBe(false);
      expect(warnSpy).toHaveBeenCalledWith(
        expect.stringContaining('localStorage.getItem unavailable'),
        expect.any(Error),
      );
    });

    it('does not throw when localStorage.setItem is unavailable on save', () => {
      service.openDetail('proj-a', 'inst-1');
      const originalSetItem = Storage.prototype.setItem;
      Storage.prototype.setItem = jest.fn(() => {
        throw new Error('QuotaExceededError');
      });

      try {
        // openDetail triggers saveState — must not bubble the storage error.
        expect(() => service.openDetail('proj-b', 'inst-2')).not.toThrow();
      } finally {
        Storage.prototype.setItem = originalSetItem;
      }

      // In-memory state still updates even though persistence failed.
      expect(service.activeInstanceId()).toBe('inst-2');
      expect(warnSpy).toHaveBeenCalledWith(
        expect.stringContaining('localStorage.setItem unavailable'),
        expect.any(Error),
      );
    });

    it('does not throw when localStorage.removeItem is unavailable on clear', () => {
      service.openDetail('proj-a', 'inst-1');
      const originalRemoveItem = Storage.prototype.removeItem;
      Storage.prototype.removeItem = jest.fn(() => {
        throw new Error('QuotaExceededError');
      });

      try {
        expect(() => service.clearInstance('inst-1')).not.toThrow();
      } finally {
        Storage.prototype.removeItem = originalRemoveItem;
      }

      expect(service.activeInstanceId()).toBeNull();
      expect(warnSpy).toHaveBeenCalled();
    });
  });

  describe('restoreState() — boot semantics (R6)', () => {
    it('restores id+project without flipping detailVisible', () => {
      // R6: restoreState is called once at App boot. It seeds the
      // cached nav-link route but must NOT auto-open the overlay — the
      // URL is the source of truth for visibility. A cold reload
      // should land on a hidden detail view, not auto-open it.
      localStorage.setItem(
        STORAGE_KEY,
        JSON.stringify({ activeInstanceId: 'inst-1', activeProjectId: 'proj-a' })
      );

      service.restoreState();

      expect(service.activeInstanceId()).toBe('inst-1');
      expect(service.activeProjectId()).toBe('proj-a');
      // Visibility is the URL's job, not restoreState's.
      expect(service.detailVisible()).toBe(false);
      // But the cached id is enough for the nav-link computed.
      expect(service.lastDetailRoute()).toEqual([
        '/projects', 'proj-a', 'instances', 'inst-1',
      ]);
    });
  });

  describe('signal independence', () => {
    it('two service instances have independent state', () => {
      // providedIn: root gives a singleton in production, but the
      // service is a plain class so two instances must not share
      // state. Guards against accidental shared-singleton regressions.
      const other = new InstancesViewStateService();
      service.openDetail('proj-a', 'inst-1');

      expect(service.activeInstanceId()).toBe('inst-1');
      expect(other.activeInstanceId()).toBeNull();
    });
  });
});
