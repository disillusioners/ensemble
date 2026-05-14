# Phase 3: Frontend — Tab Service, Component, InstanceService & Polling

## Objective
Create the tab management infrastructure (`TabStateService`), extract a dedicated `InstanceService` that owns polling lifecycle with project-based filtering, and build the `ProjectTabBarComponent` UI — all integrated into the ChatComponent sidebar only (NOT HomeComponent).

## Coupling
- **Depends on**: Phase 2 (API endpoint must support `project_id` filter)
- **Coupling type**: loose
- **Shared files with other phases**: New files only; modifies `ChatComponent` for integration
- **Shared APIs/interfaces**: `GET /api/instances?project_id=xxx`
- **Why this coupling**: Only needs the API contract from Phase 2; no shared code files

## Context
- Angular 21 with standalone components, Angular Material
- State management via Angular Signals (no NgRx)
- **Tabs are for ChatComponent ONLY** — the instance list sidebar + chat detail page. HomeComponent is for creating instances via agent selection and is NOT a tab target. (C6)
- Instance list currently loaded in ChatComponent with 10s polling via `setInterval`
- No dedicated `InstanceService` — logic spread across ChatComponent (and HomeComponent). This phase extracts it.
- `ProjectService` already exists with `listProjects()` method
- `ApiService.listInstances()` needs `project_id` parameter added
- No MatTabGroup currently used — will build custom tab bar for full control
- ChatComponent currently manages: `instances` signal, `totalInstances`, `hasMoreInstances`, `isLoadingMore`, and pagination with `append` parameter — all must move to `InstanceService`

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Add `project_id` param to `ApiService.listInstances()` | Accept optional `projectId?: string`, pass as query param to `GET /api/instances?project_id=xxx` | `frontend/src/app/services/api.service.ts` |
| 2 | Create `InstanceService` with polling ownership | Singleton (`providedIn: 'root'`). Signals: `instances`, `totalInstances`, `hasMoreInstances`, `isLoadingMore`, `loading`. Methods: `loadInstances(projectId?, append?)`, `loadMore()`, `startPolling(projectId?)`, `stopPolling()`. Components call start/stop in their lifecycle hooks. (C14) | `frontend/src/app/services/instance.service.ts` (new) |
| 3 | Move instance loading + polling from ChatComponent to InstanceService | Remove `setInterval` and instance-fetching logic from ChatComponent. ChatComponent reads `instanceService.instances()` signal instead. (W2, W9) | `frontend/src/app/pages/chat/chat.component.ts` |
| 4 | Create `TabStateService` | Manage open tabs (Signal), active tab (Signal), add/remove/switch operations. localStorage persistence using key `ensemble-project-tabs`. 100ms debounce on tab switch to prevent overlapping requests. (W4, W5) | `frontend/src/app/services/tab-state.service.ts` (new) |
| 5 | Define tab models | `ProjectTab` interface: `{ id: string, name: string, type: 'all' | 'project' }` | `frontend/src/app/models/tab.model.ts` (new) |
| 6 | Create `ProjectTabBarComponent` | Tab bar UI: All tab (fixed), project tabs, "+" button with dropdown showing unopened projects, close buttons | `frontend/src/app/components/project-tab-bar/` (new) |
| 7 | Style the tab bar | CSS matching existing dark-theme app. Include `transition` property for close animation. (W6) | `project-tab-bar.component.css` |
| 8 | Integrate tab bar into ChatComponent ONLY | Add tab bar above instance list sidebar in ChatComponent. Wire TabStateService → InstanceService: when active tab changes, call `instanceService.startPolling(projectId)`. | `frontend/src/app/pages/chat/chat.component.ts` + `.html` |
| 9 | Wire active tab to instance filtering | Effect: when active tab changes (debounced), call `instanceService.startPolling(projectId)`. Pass `null`/`undefined` for "All" tab. | `TabStateService` ↔ `InstanceService` connection |
| 10 | Persist tab state to localStorage | On tab add/remove/switch, serialize state to `ensemble-project-tabs`. On init, restore and validate (remove tabs for deleted projects). | `TabStateService` |
| 11 | Update `InstanceListComponent` to accept filtered list | Already accepts `@Input() instances` — verify it renders filtered list correctly with tree structure | `frontend/src/app/components/instance-list/` |
| 12 | Add loading state for tab switching | Show skeleton/spinner in instance list while new project's instances load. (W7) | `instance-list.component.html` + `InstanceService.loading` signal |
| 13 | Write unit tests for `TabStateService` | Test: add/remove tabs, active tab switching, localStorage round-trip with `ensemble-project-tabs` key, All tab cannot be removed, debounce behavior | `frontend/src/app/services/tab-state.service.spec.ts` (new) |
| 14 | Write unit tests for `InstanceService` | Test: loadInstances with/without projectId, startPolling/stopPolling lifecycle, pagination with append, hasMoreInstances tracking | `frontend/src/app/services/instance.service.spec.ts` (new) |
| 15 | Write unit tests for `ProjectTabBarComponent` | Test: renders correct tabs, "+" shows unopened projects only, close removes tab (not "All") | `project-tab-bar.component.spec.ts` (new) |

## Key Files

### New Files
- `frontend/src/app/models/tab.model.ts` — Tab interface
- `frontend/src/app/services/tab-state.service.ts` — Tab state management
- `frontend/src/app/services/tab-state.service.spec.ts` — Tab state tests
- `frontend/src/app/services/instance.service.ts` — Centralized instance loading + polling
- `frontend/src/app/services/instance.service.spec.ts` — Instance service tests
- `frontend/src/app/components/project-tab-bar/project-tab-bar.component.ts` — Tab bar component
- `frontend/src/app/components/project-tab-bar/project-tab-bar.component.html` — Tab bar template
- `frontend/src/app/components/project-tab-bar/project-tab-bar.component.css` — Tab bar styles
- `frontend/src/app/components/project-tab-bar/project-tab-bar.component.spec.ts` — Tab bar tests

### Modified Files
- `frontend/src/app/services/api.service.ts` — Add `projectId` param
- `frontend/src/app/pages/chat/chat.component.ts` — Integrate tab bar, delegate to InstanceService, remove polling
- `frontend/src/app/pages/chat/chat.component.html` — Add tab bar element
- `frontend/src/app/components/instance-list/instance-list.component.html` — Loading state

### NOT Modified
- `frontend/src/app/pages/home/` — HomeComponent is for agent selection/instance creation, NOT a tab target (C6)

## Implementation Notes

### InstanceService — Singleton with Lifecycle-Managed Polling (W2, W9)
```typescript
@Injectable({ providedIn: 'root' })
class InstanceService {
  private pollingIntervalId: ReturnType<typeof setInterval> | null = null;
  private readonly POLLING_INTERVAL = 10_000;
  private currentProjectId: string | null = null;

  readonly instances = signal<InstanceInfo[]>([]);
  readonly totalInstances = signal(0);
  readonly hasMoreInstances = computed(() => this.instances().length < this.totalInstances());
  readonly isLoadingMore = signal(false);
  readonly loading = signal(false);

  async loadInstances(projectId?: string, append = false): Promise<void> {
    this.loading.set(!append);
    this.isLoadingMore.set(append);
    const result = await this.api.listInstances({ projectId, ... });
    if (append) {
      this.instances.update(existing => [...existing, ...result]);
    } else {
      this.instances.set(result);
    }
    this.loading.set(false);
    this.isLoadingMore.set(false);
  }

  loadMore(): void {
    if (!this.hasMoreInstances()) return;
    this.loadInstances(this.currentProjectId ?? undefined, append: true);
  }

  startPolling(projectId?: string): void {
    this.stopPolling();
    this.currentProjectId = projectId ?? null;
    this.loadInstances(projectId);  // Immediate load
    this.pollingIntervalId = setInterval(() => this.loadInstances(projectId), this.POLLING_INTERVAL);
  }

  stopPolling(): void {
    if (this.pollingIntervalId) {
      clearInterval(this.pollingIntervalId);
      this.pollingIntervalId = null;
    }
  }
}
```
Components call `startPolling`/`stopPolling` in their `ngOnInit`/`ngOnDestroy` hooks. The service is a singleton but each component manages its own lifecycle window.

### TabStateService — with Debounce and localStorage
```typescript
@Injectable({ providedIn: 'root' })
class TabStateService {
  readonly openTabs = signal<ProjectTab[]>([{ id: 'all', name: 'All', type: 'all' }]);
  readonly activeTab = signal<ProjectTab>({ id: 'all', name: 'All', type: 'all' });

  private readonly STORAGE_KEY = 'ensemble-project-tabs'; // (W5)

  // Debounced active tab change (100ms) to prevent overlapping requests (W4)
  readonly debouncedActiveProjectId = computed(() => {
    const tab = this.activeTab();
    return tab.type === 'project' ? tab.id : null;
  });

  addTab(project: Project): void {
    if (this.openTabs().some(t => t.id === project.project_id)) return;
    const newTab: ProjectTab = { id: project.project_id, name: project.name, type: 'project' };
    this.openTabs.update(tabs => [...tabs, newTab]);
    this.setActiveTab(newTab.id);
    this.saveState();
  }

  removeTab(tabId: string): void {
    if (tabId === 'all') return; // Cannot close "All"
    this.openTabs.update(tabs => tabs.filter(t => t.id !== tabId));
    if (this.activeTab().id === tabId) {
      this.setActiveTab('all');
    }
    this.saveState();
  }

  setActiveTab(tabId: string): void {
    const tab = this.openTabs().find(t => t.id === tabId);
    if (tab) {
      this.activeTab.set(tab);
      this.saveState();
    }
  }

  private saveState(): void {
    localStorage.setItem(this.STORAGE_KEY, JSON.stringify({
      openTabs: this.openTabs(),
      activeTabId: this.activeTab().id,
    }));
  }

  private loadState(): void {
    const saved = localStorage.getItem(this.STORAGE_KEY);
    if (saved) {
      // Validate against current projects, remove orphaned tabs
    }
  }
}
```

### ChatComponent Integration (NOT HomeComponent — C6)
```typescript
// In ChatComponent
private tabEffect = effect(() => {
  const projectId = this.tabState.debouncedActiveProjectId();
  this.instanceService.startPolling(projectId ?? undefined);
});

ngOnDestroy(): void {
  this.instanceService.stopPolling();
}
```

Template change — tab bar goes above instance list sidebar:
```html
<div class="chat-layout">
  <div class="sidebar">
    <app-project-tab-bar />  <!-- NEW -->
    <app-instance-list [instances]="instanceService.instances()" />
  </div>
  <div class="chat-area">...</div>
</div>
```

### Loading State for Tab Switch (W7)
```html
<!-- In instance-list.component.html -->
@if (instanceService.loading()) {
  <div class="loading-state">
    <mat-spinner diameter="24"></mat-spinner>
  </div>
} @else {
  <!-- normal instance tree -->
}
```

## Constraints
- Tab bar appears in **ChatComponent sidebar only** — NOT HomeComponent (C6)
- All tab is ALWAYS present and cannot be closed
- "+" menu only shows projects not already open as tabs
- Tab state persists to localStorage key `ensemble-project-tabs` (W5)
- InstanceService is singleton but components manage polling lifecycle via start/stop (W9)
- 100ms debounce on tab switch to prevent overlapping requests (W4)
- InstanceService must handle all pagination: `append` param, `hasMoreInstances`, `loadMore()` (C14)
- No URL routing changes — tabs are purely UI state

## Deliverables
- [ ] `InstanceService` owns instance loading + polling + pagination
- [ ] `TabStateService` with add/remove/switch/persist/debounce functionality
- [ ] `ProjectTabBarComponent` rendered in ChatComponent sidebar only
- [ ] "+" button shows unopened projects context menu
- [ ] Close button removes project tab (not "All")
- [ ] Active tab filters instance list via API
- [ ] Tab state persists to `ensemble-project-tabs` in localStorage
- [ ] Loading spinner during tab switch
- [ ] Polling extracted from ChatComponent into InstanceService
- [ ] Unit tests for all 3 new services/components pass
- [ ] No regressions in existing functionality
