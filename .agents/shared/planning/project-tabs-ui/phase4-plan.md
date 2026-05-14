# Phase 4: Frontend — UX Polish & Edge Cases

## Objective
Polish the tab UX with smooth animations (including CSS `transition` property), handle edge cases (deleted projects, empty projects), validate localStorage on init, and verify background tab polling is fully inactive.

## Coupling
- **Depends on**: Phase 3 (TabStateService, InstanceService, ProjectTabBarComponent must exist)
- **Coupling type**: tight
- **Shared files with other phases**: `frontend/src/app/services/instance.service.ts`, `frontend/src/app/services/tab-state.service.ts`, `frontend/src/app/components/project-tab-bar/`
- **Shared APIs/interfaces**: TabStateService signals
- **Why this coupling**: Directly modifies and polishes components/services from Phase 3

## Context
- Phase 3 created `TabStateService`, `InstanceService`, `ProjectTabBarComponent`, integrated into ChatComponent
- InstanceService owns polling — this phase focuses on UX refinements and edge cases
- Tab switching already has loading state (Phase 3 task 12)
- No HomeComponent changes needed (tabs are ChatComponent only)

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Add tab transition animations with explicit `transition` property | CSS `transition: all 0.15s ease-out` on `.tab` class. Handle add (fade-in + slide-down), close (fade-out + slide-up + width collapse). Ensure `transition` property is set for close animation. (W6) | `project-tab-bar.component.css` |
| 2 | Handle edge case: project deleted while tab open | If API returns 404 for a project or instance fetch fails due to deleted project, auto-close that tab and switch to "All". Show brief notification. | `tab-state.service.ts` |
| 3 | Handle edge case: empty project (no instances) | Show empty state message: "No instances in this project" with appropriate icon | `instance-list.component.html` |
| 4 | Validate localStorage state on app init | On `TabStateService` init: fetch current projects, filter out tabs for deleted projects, if active tab was removed switch to "All", save cleaned state | `tab-state.service.ts` |
| 5 | Verify polling stop for inactive tabs | Confirm with DevTools: only ONE `GET /api/instances` request per 10s interval, matching the active tab's project filter | Manual verification |
| 6 | Add keyboard accessibility for tab bar | Tab key navigation, Enter to select, Escape to close dropdown, Delete/Backspace to close tab | `project-tab-bar.component.ts` + `.html` |
| 7 | Write unit tests for edge cases | Test: project deletion auto-closes tab, empty project shows message, invalid localStorage cleaned up | `tab-state.service.spec.ts`, `instance.service.spec.ts` |

## Key Files
- `frontend/src/app/components/project-tab-bar/project-tab-bar.component.css` — Animations
- `frontend/src/app/services/tab-state.service.ts` — Edge cases, localStorage validation
- `frontend/src/app/services/tab-state.service.spec.ts` — Edge case tests
- `frontend/src/app/components/instance-list/instance-list.component.html` — Empty state

## Implementation Notes

### CSS Transitions — Must Include `transition` Property (W6)
```css
.tab {
  transition: all 0.15s ease-out;  /* EXPLICIT transition property required */
  opacity: 1;
  transform: translateY(0);
  overflow: hidden;
}

.tab.entering {
  opacity: 0;
  transform: translateY(4px);
}

.tab.closing {
  opacity: 0;
  transform: translateY(-4px);
  max-width: 0;
  padding: 0;
  margin: 0;
  border: 0;
}
```

### Project Deleted While Tab Open
```typescript
// In InstanceService or TabStateService
// When loadInstances returns empty or errors for a specific project
if (error?.status === 404 || projectNotFound) {
  this.tabState.removeTab(tabId);
  // Optional: show snackbar notification
}
```

### Empty Project State
```html
<!-- In instance-list.component.html -->
@if (instanceService.instances().length === 0 && !instanceService.loading()) {
  <div class="empty-state">
    <mat-icon>inbox</mat-icon>
    <p>No instances in this project</p>
  </div>
}
```

### localStorage Validation on Init
```typescript
async initTabs(): Promise<void> {
  const saved = localStorage.getItem('ensemble-project-tabs');
  if (!saved) return;
  
  const { openTabs, activeTabId } = JSON.parse(saved);
  const currentProjects = await this.projectService.listProjects().toPromise();
  const validProjectIds = new Set(currentProjects.map(p => p.project_id));
  
  // Filter out tabs for deleted projects
  const validTabs = openTabs.filter(t => 
    t.type === 'all' || validProjectIds.has(t.id)
  );
  
  // If active tab was removed, switch to All
  const activeStillValid = validTabs.some(t => t.id === activeTabId);
  const newActiveId = activeStillValid ? activeTabId : 'all';
  
  this.openTabs.set(validTabs);
  this.setActiveTab(newActiveId);
  this.saveState();
}
```

## Constraints
- Animations must be subtle (150-200ms) to feel snappy
- `transition` CSS property must be explicitly set for close animation to work (W6)
- Polling lifecycle already handled by InstanceService from Phase 3 — this phase verifies it works
- No changes to HomeComponent
- Keyboard accessibility should follow standard tab-navigation patterns

## Deliverables
- [ ] Smooth tab add/close animations with explicit `transition` CSS property
- [ ] Project deletion gracefully closes orphaned tabs
- [ ] Empty state for project with no instances
- [ ] localStorage validated on app init (orphaned tabs removed)
- [ ] Verified: only active tab generates network requests
- [ ] Keyboard accessible tab bar
- [ ] Unit tests for all edge cases pass
