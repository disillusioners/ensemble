import { WorkspaceOverlayService } from './workspace-overlay.service';

describe('WorkspaceOverlayService', () => {
  let service: WorkspaceOverlayService;

  beforeEach(() => {
    service = new WorkspaceOverlayService();
  });

  describe('initial state', () => {
    it('starts hidden with no project', () => {
      expect(service.showWorkspace()).toBe(false);
      expect(service.workspaceProjectId()).toBeNull();
    });
  });

  describe('toggle(projectId)', () => {
    it('opens the overlay for a project when called for the first time', () => {
      service.toggle('proj-a');

      expect(service.showWorkspace()).toBe(true);
      expect(service.workspaceProjectId()).toBe('proj-a');
    });

    it('toggles off when called with the same project that is currently open', () => {
      service.toggle('proj-a');
      expect(service.showWorkspace()).toBe(true);

      service.toggle('proj-a');

      expect(service.showWorkspace()).toBe(false);
      // Project id is preserved — the cache key stays the same so a
      // subsequent re-open restores the same project state.
      expect(service.workspaceProjectId()).toBe('proj-a');
    });

    it('switches to a different project when one is open', () => {
      service.toggle('proj-a');
      expect(service.workspaceProjectId()).toBe('proj-a');

      service.toggle('proj-b');

      expect(service.showWorkspace()).toBe(true);
      expect(service.workspaceProjectId()).toBe('proj-b');
    });

    it('opens the overlay when switching from closed → different project', () => {
      service.toggle('proj-a');

      // Hide it
      service.hide();
      expect(service.showWorkspace()).toBe(false);

      // Now toggle to a different project — should open with the new project
      service.toggle('proj-b');
      expect(service.showWorkspace()).toBe(true);
      expect(service.workspaceProjectId()).toBe('proj-b');
    });
  });

  describe('toggle() — no argument', () => {
    it('is a no-op when no project is set yet', () => {
      service.toggle();

      expect(service.showWorkspace()).toBe(false);
      expect(service.workspaceProjectId()).toBeNull();
    });

    it('toggles off when the current project is the only one shown', () => {
      service.toggle('proj-a');
      expect(service.showWorkspace()).toBe(true);

      service.toggle();

      expect(service.showWorkspace()).toBe(false);
      expect(service.workspaceProjectId()).toBe('proj-a');
    });
  });

  describe('hide()', () => {
    it('closes the overlay', () => {
      service.toggle('proj-a');
      expect(service.showWorkspace()).toBe(true);

      service.hide();

      expect(service.showWorkspace()).toBe(false);
    });

    it('preserves the projectId when hiding', () => {
      service.toggle('proj-a');
      service.hide();

      expect(service.workspaceProjectId()).toBe('proj-a');
    });

    it('is safe to call when already hidden', () => {
      service.hide();
      expect(service.showWorkspace()).toBe(false);
      expect(service.workspaceProjectId()).toBeNull();
    });
  });

  describe('show(projectId)', () => {
    it('opens the overlay for the given project', () => {
      service.show('proj-a');

      expect(service.showWorkspace()).toBe(true);
      expect(service.workspaceProjectId()).toBe('proj-a');
    });

    it('switches projects when called while open', () => {
      service.show('proj-a');
      service.show('proj-b');

      expect(service.showWorkspace()).toBe(true);
      expect(service.workspaceProjectId()).toBe('proj-b');
    });
  });

  describe('signal independence', () => {
    it('two service instances have independent state', () => {
      // Smoke test: providedIn: "root" gives a singleton in production,
      // but the service is a plain class so two instances must not share
      // state. This guards against accidental shared-singleton regressions.
      const other = new WorkspaceOverlayService();
      service.toggle('proj-a');

      expect(service.showWorkspace()).toBe(true);
      expect(other.showWorkspace()).toBe(false);
    });
  });
});
