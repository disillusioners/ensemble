import { signal, computed } from '@angular/core';
import { ProjectTab } from '../models/tab.model';

const STORAGE_KEY = 'ensemble-project-tabs';

export const ALL_TAB: ProjectTab = { id: 'all', name: 'All', type: 'all' };

interface StoredTabState {
  openTabs: ProjectTab[];
  activeTabId: string;
}

// Testable TabStateService implementation (mirrors actual service)
class TestableTabStateService {
  readonly openTabs = signal<ProjectTab[]>([ALL_TAB]);
  readonly activeTab = signal<ProjectTab>(ALL_TAB);

  readonly debouncedActiveProjectId = computed(() => {
    const tab = this.activeTab();
    return tab.type === 'project' ? tab.id : null;
  });

  addTab(project: { project_id: string; name: string }): void {
    const existingTab = this.openTabs().find((tab) => tab.id === project.project_id);
    if (existingTab) {
      this.setActiveTab(project.project_id);
      return;
    }

    const newTab: ProjectTab = { id: project.project_id, name: project.name, type: 'project' };
    this.openTabs.update((tabs) => [...tabs, newTab]);
    this.activeTab.set(newTab);
    this.saveState();
  }

  removeTab(tabId: string): void {
    if (tabId === ALL_TAB.id) {
      return;
    }

    const wasActive = this.activeTab().id === tabId;
    this.openTabs.update((tabs) => tabs.filter((tab) => tab.id !== tabId));

    if (wasActive) {
      this.activeTab.set(ALL_TAB);
    }

    this.saveState();
  }

  setActiveTab(tabId: string): void {
    const tab = this.openTabs().find((t) => t.id === tabId);
    if (tab) {
      this.activeTab.set(tab);
      this.saveState();
    }
  }

  restoreState(availableProjectIds?: string[]): void {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (!stored) {
      return;
    }

    try {
      const state: StoredTabState = JSON.parse(stored);
      const validTabs: ProjectTab[] = [ALL_TAB];

      if (availableProjectIds) {
        for (const tab of state.openTabs) {
          if (tab.type === 'project' && availableProjectIds.includes(tab.id)) {
            validTabs.push(tab);
          }
        }
      } else {
        validTabs.push(...state.openTabs.filter((tab) => tab.type === 'project'));
      }

      this.openTabs.set(validTabs);

      const activeTab = validTabs.find((tab) => tab.id === state.activeTabId);
      this.activeTab.set(activeTab || ALL_TAB);
    } catch {
      localStorage.removeItem(STORAGE_KEY);
    }
  }

  private saveState(): void {
    const state: StoredTabState = {
      openTabs: this.openTabs(),
      activeTabId: this.activeTab().id,
    };
    localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
  }
}

// Helper to reset localStorage before each test
function clearLocalStorage(): void {
  localStorage.removeItem(STORAGE_KEY);
}

describe('TabStateService', () => {
  let service: TestableTabStateService;

  beforeEach(() => {
    clearLocalStorage();
    service = new TestableTabStateService();
  });

  afterEach(() => {
    clearLocalStorage();
  });

  describe('initial state', () => {
    it('should have only All tab in openTabs', () => {
      expect(service.openTabs()).toHaveLength(1);
      expect(service.openTabs()[0].id).toBe('all');
    });

    it('should have All tab as activeTab', () => {
      expect(service.activeTab().id).toBe('all');
      expect(service.activeTab().type).toBe('all');
    });
  });

  describe('addTab', () => {
    it('should add a new project tab', () => {
      service.addTab({ project_id: 'project-1', name: 'Project 1' });

      expect(service.openTabs()).toHaveLength(2);
      const projectTab = service.openTabs().find(tab => tab.id === 'project-1');
      expect(projectTab).toBeDefined();
      expect(projectTab?.name).toBe('Project 1');
      expect(projectTab?.type).toBe('project');
    });

    it('should switch to the new tab when adding', () => {
      service.addTab({ project_id: 'project-1', name: 'Project 1' });

      expect(service.activeTab().id).toBe('project-1');
    });

    it('should save state to localStorage', () => {
      service.addTab({ project_id: 'project-1', name: 'Project 1' });

      const stored = localStorage.getItem(STORAGE_KEY);
      expect(stored).not.toBeNull();

      const state: StoredTabState = JSON.parse(stored!);
      expect(state.openTabs.some(t => t.id === 'project-1')).toBe(true);
      expect(state.activeTabId).toBe('project-1');
    });

    it('should not add duplicate tab - should switch to existing', () => {
      service.addTab({ project_id: 'project-1', name: 'Project 1' });
      service.addTab({ project_id: 'project-2', name: 'Project 2' });

      // Add duplicate
      service.addTab({ project_id: 'project-1', name: 'Project 1 Updated' });

      // Should still have 3 tabs (All + 2 projects)
      expect(service.openTabs()).toHaveLength(3);
      // Should switch to existing tab
      expect(service.activeTab().id).toBe('project-1');
      // Original name should be preserved
      const projectTab = service.openTabs().find(tab => tab.id === 'project-1');
      expect(projectTab?.name).toBe('Project 1');
    });

    it('should switch to existing tab when duplicate added', () => {
      service.addTab({ project_id: 'project-1', name: 'Project 1' });
      service.addTab({ project_id: 'project-2', name: 'Project 2' });

      // Switch to project-1
      service.setActiveTab('project-1');

      // Add duplicate - should switch to existing
      service.addTab({ project_id: 'project-1', name: 'Project 1' });

      expect(service.activeTab().id).toBe('project-1');
    });
  });

  describe('removeTab', () => {
    beforeEach(() => {
      service.addTab({ project_id: 'project-1', name: 'Project 1' });
      service.addTab({ project_id: 'project-2', name: 'Project 2' });
    });

    it('should remove a project tab', () => {
      service.removeTab('project-1');

      expect(service.openTabs()).toHaveLength(2);
      expect(service.openTabs().find(t => t.id === 'project-1')).toBeUndefined();
    });

    it('should switch to All tab when removing active tab', () => {
      service.activeTab.set(service.openTabs().find(t => t.id === 'project-1')!);
      service.removeTab('project-1');

      expect(service.activeTab().id).toBe('all');
    });

    it('should not switch tabs when removing inactive tab', () => {
      service.activeTab.set(service.openTabs().find(t => t.id === 'project-1')!);
      service.removeTab('project-2');

      expect(service.activeTab().id).toBe('project-1');
    });

    it('should be no-op for removing All tab', () => {
      service.removeTab('all');

      expect(service.openTabs()).toHaveLength(3); // All + 2 projects
      // Active tab is project-2 (last added), unchanged
      expect(service.activeTab().id).toBe('project-2');
    });

    it('should save state after removal', () => {
      service.removeTab('project-1');

      const stored = localStorage.getItem(STORAGE_KEY);
      const state: StoredTabState = JSON.parse(stored!);
      expect(state.openTabs.some(t => t.id === 'project-1')).toBe(false);
    });
  });

  describe('setActiveTab', () => {
    beforeEach(() => {
      service.addTab({ project_id: 'project-1', name: 'Project 1' });
      service.addTab({ project_id: 'project-2', name: 'Project 2' });
    });

    it('should switch active tab', () => {
      service.setActiveTab('project-2');

      expect(service.activeTab().id).toBe('project-2');
    });

    it('should switch back to All tab', () => {
      service.setActiveTab('all');

      expect(service.activeTab().id).toBe('all');
    });

    it('should save state after switching', () => {
      service.setActiveTab('project-2');

      const stored = localStorage.getItem(STORAGE_KEY);
      const state: StoredTabState = JSON.parse(stored!);
      expect(state.activeTabId).toBe('project-2');
    });

    it('should do nothing for non-existent tab', () => {
      const initialActiveTab = service.activeTab();
      service.setActiveTab('non-existent-tab');

      expect(service.activeTab()).toEqual(initialActiveTab);
    });
  });

  describe('localStorage persistence', () => {
    it('should persist state across save/restore cycle', () => {
      service.addTab({ project_id: 'project-1', name: 'Project 1' });
      service.addTab({ project_id: 'project-2', name: 'Project 2' });
      service.setActiveTab('project-2');

      // Create new service instance (simulating page reload)
      const newService = new TestableTabStateService();
      newService.restoreState();

      expect(newService.openTabs()).toHaveLength(3);
      expect(newService.activeTab().id).toBe('project-2');
    });

    it('should clear corrupted localStorage', () => {
      localStorage.setItem(STORAGE_KEY, 'invalid-json{');

      service.restoreState();

      // Should have default state
      expect(service.openTabs()).toHaveLength(1);
      expect(service.activeTab().id).toBe('all');
    });
  });

  describe('restoreState', () => {
    beforeEach(() => {
      // Set up some tabs
      service.addTab({ project_id: 'project-1', name: 'Project 1' });
      service.addTab({ project_id: 'project-2', name: 'Project 2' });
      service.addTab({ project_id: 'project-3', name: 'Project 3' });
      service.setActiveTab('project-2');
    });

    it('should remove orphaned tabs not in availableProjectIds', () => {
      // Only project-1 and project-3 are available
      service.restoreState(['project-1', 'project-3']);

      // Should have 3 tabs: All + project-1 + project-3
      expect(service.openTabs()).toHaveLength(3);
      expect(service.openTabs().some(t => t.id === 'project-1')).toBe(true);
      expect(service.openTabs().some(t => t.id === 'project-3')).toBe(true);
      expect(service.openTabs().some(t => t.id === 'project-2')).toBe(false);
    });

    it('should switch to All if active tab was orphaned', () => {
      // Only project-1 is available (project-2 was active but is now orphaned)
      service.restoreState(['project-1']);

      expect(service.activeTab().id).toBe('all');
    });

    it('should keep active tab if still available', () => {
      // project-2 is available
      service.restoreState(['project-1', 'project-2', 'project-3']);

      expect(service.activeTab().id).toBe('project-2');
    });

    it('should restore to All if active tab no longer available', () => {
      // project-2 is not in available list
      service.restoreState(['project-1', 'project-3']);

      expect(service.activeTab().id).toBe('all');
    });

    it('should keep all project tabs when availableProjectIds is not provided', () => {
      service.restoreState();

      // Should keep all project tabs
      expect(service.openTabs()).toHaveLength(4); // All + 3 projects
    });

    it('should do nothing when no stored state', () => {
      clearLocalStorage();
      const newService = new TestableTabStateService();
      newService.restoreState(['project-1']);

      // Should have default state
      expect(newService.openTabs()).toHaveLength(1);
    });
  });

  describe('debouncedActiveProjectId', () => {
    it('should return null for All tab', () => {
      service.activeTab.set(ALL_TAB);

      expect(service.debouncedActiveProjectId()).toBeNull();
    });

    it('should return project id for project tab', () => {
      service.addTab({ project_id: 'project-1', name: 'Project 1' });

      expect(service.debouncedActiveProjectId()).toBe('project-1');
    });

    it('should return correct id when switching tabs', () => {
      service.addTab({ project_id: 'project-1', name: 'Project 1' });
      service.addTab({ project_id: 'project-2', name: 'Project 2' });
      service.setActiveTab('project-2');

      expect(service.debouncedActiveProjectId()).toBe('project-2');

      service.setActiveTab('all');
      expect(service.debouncedActiveProjectId()).toBeNull();
    });
  });
});
