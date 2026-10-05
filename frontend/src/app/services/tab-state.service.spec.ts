import { signal, computed } from '@angular/core';
import { ProjectTab } from '../models/tab.model';

const STORAGE_KEY = 'ensemble-project-tabs';

export const ALL_TAB: ProjectTab = { id: 'all', name: 'All', type: 'all' };

/**
 * Mirror of the production ``CHAT_TAB`` sentinel defined in
 * ``tab-state.service.ts``. Kept adjacent to ``ALL_TAB`` so the
 * testable service is a single-file, self-contained copy of the
 * production logic. The duplication is intentional: the testable
 * class is an in-test re-implementation that exercises the
 * behavior without pulling in Angular DI / HttpClient.
 */
export const CHAT_TAB: ProjectTab = { id: 'chat', name: 'Chat', type: 'chat' };

interface StoredTabState {
  openTabs: ProjectTab[];
  activeTabId: string;
}

// Testable TabStateService implementation (mirrors actual service)
class TestableTabStateService {
  readonly openTabs = signal<ProjectTab[]>([ALL_TAB, CHAT_TAB]);
  readonly activeTab = signal<ProjectTab>(ALL_TAB);

  readonly activeProjectId = computed(() => {
    const tab = this.activeTab();
    return tab.type === 'project' ? tab.id : null;
  });

  /**
   * Mirror of the production ``activeSpecialTabId`` — the id of the
   * active special tab (``"all"`` or ``"chat"``) when one of the
   * two permanent special tabs is active, ``null`` when a project
   * tab is active.
   */
  readonly activeSpecialTabId = computed(() => {
    const tab = this.activeTab();
    if (tab.type === 'all' || tab.type === 'chat') {
      return tab.id;
    }
    return null;
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
    if (tabId === ALL_TAB.id || tabId === CHAT_TAB.id) {
      // Cannot close the permanent special tabs.
      return;
    }

    const currentTabs = this.openTabs();
    const tabIndex = currentTabs.findIndex(t => t.id === tabId);
    if (tabIndex === -1) return;

    const wasActive = this.activeTab().id === tabId;

    // Remove the tab
    this.openTabs.update((tabs) => tabs.filter((tab) => tab.id !== tabId));

    // If the removed tab was active, switch to adjacent tab
    if (wasActive) {
      const remainingTabs = this.openTabs();
      // Prefer the tab to the right, then left, then "All"
      const adjacentIndex = Math.min(tabIndex, remainingTabs.length - 1);
      const adjacentTab = remainingTabs[adjacentIndex] || ALL_TAB;
      this.activeTab.set(adjacentTab);
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
      // Always re-seed the two permanent special tabs in their canonical
      // order (All first, Chat second). Mirrors the production restore
      // behavior.
      const validTabs: ProjectTab[] = [ALL_TAB, CHAT_TAB];

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
    it('should have All tab as the first tab in openTabs', () => {
      // Two permanent special tabs are always seeded: All + Chat.
      // The All tab is the canonical first entry (matches the
      // pre-feature behavior) so existing consumers iterating
      // ``openTabs[0]`` see no shape change.
      expect(service.openTabs()).toHaveLength(2);
      expect(service.openTabs()[0].id).toBe('all');
      expect(service.openTabs()[0].type).toBe('all');
    });

    it('should have Chat tab as the second tab in openTabs', () => {
      // The Chat tab is seeded alongside All — non-closable, never
      // persisted by id across a restoreState cycle (it's always
      // re-added by the restore logic, see TestRestoreState below).
      expect(service.openTabs()[1].id).toBe('chat');
      expect(service.openTabs()[1].type).toBe('chat');
    });

    it('should have All tab as activeTab by default', () => {
      expect(service.activeTab().id).toBe('all');
      expect(service.activeTab().type).toBe('all');
    });

    it('should expose activeSpecialTabId="all" by default', () => {
      // The new computed signal is the wire the InstancesComponent
      // uses to drive the chat source filter. Must resolve to "all"
      // on a fresh service so the All tab continues to render
      // without a source filter (back-compat).
      expect(service.activeSpecialTabId()).toBe('all');
    });

    it('should expose activeProjectId=null by default', () => {
      // activeProjectId is null on the two permanent special tabs —
      // it is the wire for the project-id filter. Back-compat with
      // the pre-feature behavior (null = "no project filter").
      expect(service.activeProjectId()).toBeNull();
    });
  });

  describe('Chat tab activation', () => {
    it('should switch to the Chat tab and expose activeSpecialTabId="chat"', () => {
      service.setActiveTab('chat');

      expect(service.activeTab().id).toBe('chat');
      expect(service.activeTab().type).toBe('chat');
      expect(service.activeSpecialTabId()).toBe('chat');
      // activeProjectId must still be null on the Chat tab (it's
      // not a project — the InstancesComponent uses
      // activeSpecialTabId for the source-filter decision, NOT
      // activeProjectId).
      expect(service.activeProjectId()).toBeNull();
    });

    it('should round-trip the active tab through localStorage', () => {
      service.setActiveTab('chat');
      const stored = localStorage.getItem(STORAGE_KEY);
      expect(stored).not.toBeNull();
      const state: StoredTabState = JSON.parse(stored!);
      expect(state.activeTabId).toBe('chat');
    });

    it('should expose activeSpecialTabId=null on a project tab', () => {
      service.addTab({ project_id: 'project-1', name: 'Project 1' });

      expect(service.activeTab().id).toBe('project-1');
      expect(service.activeSpecialTabId()).toBeNull();
      // activeProjectId resolves to the project id.
      expect(service.activeProjectId()).toBe('project-1');
    });
  });

  describe('removeTab — non-closable special tabs', () => {
    it('cannot close the All tab', () => {
      service.removeTab('all');
      // All tab is still present in openTabs.
      expect(service.openTabs().some(t => t.id === 'all')).toBe(true);
    });

    it('cannot close the Chat tab', () => {
      // Pre-condition: Chat tab is in openTabs (seeded by default).
      expect(service.openTabs().some(t => t.id === 'chat')).toBe(true);

      service.removeTab('chat');

      // Chat tab is still present in openTabs. The setActiveTab and
      // the activeTab are also unchanged (the call is a no-op).
      expect(service.openTabs().some(t => t.id === 'chat')).toBe(true);
    });

    it('removeTab on Chat does not change the active tab when Chat is active', () => {
      service.setActiveTab('chat');
      service.removeTab('chat');

      // The active tab must remain Chat — the no-op close attempt
      // must not silently fall through to a different tab.
      expect(service.activeTab().id).toBe('chat');
    });
  });

  describe('addTab', () => {
    it('should add a new project tab', () => {
      service.addTab({ project_id: 'project-1', name: 'Project 1' });

      // 2 permanent special tabs (All + Chat) + 1 project = 3.
      expect(service.openTabs()).toHaveLength(3);
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

      // Should still have 4 tabs (All + Chat + 2 projects)
      expect(service.openTabs()).toHaveLength(4);
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

      // All + Chat + project-2 = 3 tabs.
      expect(service.openTabs()).toHaveLength(3);
      expect(service.openTabs().find(t => t.id === 'project-1')).toBeUndefined();
    });

    it('should switch to adjacent tab when active tab is removed', () => {
      // Tabs: [All, project-1, project-2], active = project-1
      service.activeTab.set(service.openTabs().find(t => t.id === 'project-1')!);
      service.removeTab('project-1');

      // Should switch to adjacent (project-2, since it was to the right)
      expect(service.activeTab().id).toBe('project-2');
    });

    it('should switch to previous tab when last project tab is removed', () => {
      // Tabs: [All, project-1, project-2], active = project-2 (last)
      service.activeTab.set(service.openTabs().find(t => t.id === 'project-2')!);
      service.removeTab('project-2');

      // Should switch to adjacent (project-1, since it was to the left)
      expect(service.activeTab().id).toBe('project-1');
    });

    it('should switch to adjacent tab when only project tab is open and removed', () => {
      // Remove project-2, leaving only [All, Chat, project-1]
      service.activeTab.set(service.openTabs().find(t => t.id === 'project-2')!);
      service.removeTab('project-2');

      // Now remove project-1 (only project tab remaining)
      service.activeTab.set(service.openTabs().find(t => t.id === 'project-1')!);
      service.removeTab('project-1');

      // With the Chat tab seeded between All and project tabs, the
      // adjacent-tab fallback lands on Chat (the tab at the same
      // index), not All. The behavior the test is really pinning is
      // "the active tab must NOT silently fall to undefined or
      // throw" — Chat is a valid, sensible target.
      expect(service.activeTab().id).toBe('chat');
    });

    it('should not switch tabs when removing inactive tab', () => {
      service.activeTab.set(service.openTabs().find(t => t.id === 'project-1')!);
      service.removeTab('project-2');

      expect(service.activeTab().id).toBe('project-1');
    });

    it('should be no-op for removing All tab', () => {
      service.removeTab('all');

      expect(service.openTabs()).toHaveLength(4); // All + Chat + 2 projects
      // Active tab is project-2 (last added), unchanged
      expect(service.activeTab().id).toBe('project-2');
    });

    it('should be no-op for removing Chat tab', () => {
      // Mirrors the All tab test — Chat is also a permanent special
      // tab and the close attempt must be a no-op.
      service.removeTab('chat');

      expect(service.openTabs()).toHaveLength(4); // All + Chat + 2 projects
      // Chat tab is still present in openTabs.
      expect(service.openTabs().some(t => t.id === 'chat')).toBe(true);
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

      // All + Chat + 2 projects = 4 tabs after restore.
      expect(newService.openTabs()).toHaveLength(4);
      expect(newService.activeTab().id).toBe('project-2');
    });

    it('should clear corrupted localStorage', () => {
      localStorage.setItem(STORAGE_KEY, 'invalid-json{');

      service.restoreState();

      // Corrupted storage is dropped → service falls back to the
      // canonical default (All + Chat only, no project tabs).
      expect(service.openTabs()).toHaveLength(2);
      expect(service.openTabs().some(t => t.id === 'all')).toBe(true);
      expect(service.openTabs().some(t => t.id === 'chat')).toBe(true);
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

      // Should have 4 tabs: All + Chat + project-1 + project-3
      // (the two permanent special tabs are always re-seeded).
      expect(service.openTabs()).toHaveLength(4);
      expect(service.openTabs().some(t => t.id === 'all')).toBe(true);
      expect(service.openTabs().some(t => t.id === 'chat')).toBe(true);
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

      // All + Chat + 3 projects = 5 tabs.
      expect(service.openTabs()).toHaveLength(5);
    });

    it('should do nothing when no stored state', () => {
      clearLocalStorage();
      const newService = new TestableTabStateService();
      newService.restoreState(['project-1']);

      // No stored state → the service retains its canonical default
      // (All + Chat), the availableProjectIds list is ignored.
      expect(newService.openTabs()).toHaveLength(2);
      expect(newService.openTabs().some(t => t.id === 'all')).toBe(true);
      expect(newService.openTabs().some(t => t.id === 'chat')).toBe(true);
    });

    it('should re-seed Chat tab even when it was the active tab before save', () => {
      // Pre-condition: Chat is active, stored state references it.
      service.setActiveTab('chat');
      // Create a new service + restore (simulates page reload while
      // the Chat tab was the active view).
      const newService = new TestableTabStateService();
      newService.restoreState();
      // The Chat tab must be re-seeded AND the active tab preserved
      // (the restore code finds the matching id in validTabs).
      expect(newService.openTabs().some(t => t.id === 'chat')).toBe(true);
      expect(newService.activeTab().id).toBe('chat');
    });
  });

  describe('activeProjectId', () => {
    it('should return null for All tab', () => {
      service.activeTab.set(ALL_TAB);

      expect(service.activeProjectId()).toBeNull();
    });

    it('should return project id for project tab', () => {
      service.addTab({ project_id: 'project-1', name: 'Project 1' });

      expect(service.activeProjectId()).toBe('project-1');
    });

    it('should return correct id when switching tabs', () => {
      service.addTab({ project_id: 'project-1', name: 'Project 1' });
      service.addTab({ project_id: 'project-2', name: 'Project 2' });
      service.setActiveTab('project-2');

      expect(service.activeProjectId()).toBe('project-2');

      service.setActiveTab('all');
      expect(service.activeProjectId()).toBeNull();
    });
  });
});
