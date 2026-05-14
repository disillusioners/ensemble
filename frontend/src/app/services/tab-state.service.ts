import { Injectable, signal, computed, WritableSignal, Signal } from '@angular/core';
import { ProjectTab } from '../models/tab.model';

const STORAGE_KEY = 'ensemble-project-tabs';

export const ALL_TAB: ProjectTab = { id: 'all', name: 'All', type: 'all' };

interface StoredTabState {
  openTabs: ProjectTab[];
  activeTabId: string;
}

@Injectable({
  providedIn: 'root'
})
export class TabStateService {
  readonly openTabs: WritableSignal<ProjectTab[]> = signal([ALL_TAB]);
  readonly activeTab: WritableSignal<ProjectTab> = signal(ALL_TAB);

  /**
   * Returns the active project id if viewing a project tab, null if viewing All tab.
   * Debouncing is handled in the component using rxjs.
   */
  readonly debouncedActiveProjectId: Signal<string | null> = computed(() => {
    const tab = this.activeTab();
    return tab.type === 'project' ? tab.id : null;
  });

  /**
   * Add a project tab, switch to it, and persist state.
   * No-op if tab already exists.
   */
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

  /**
   * Remove a tab and switch to All if the removed tab was active.
   * Cannot remove the 'all' tab.
   */
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

  /**
   * Switch to a different tab and persist state.
   */
  setActiveTab(tabId: string): void {
    const tab = this.openTabs().find((t) => t.id === tabId);
    if (tab) {
      this.activeTab.set(tab);
      this.saveState();
    }
  }

  /**
   * Restore state from localStorage.
   * Validates that tabs still exist in availableProjectIds, removes orphaned tabs.
   */
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
