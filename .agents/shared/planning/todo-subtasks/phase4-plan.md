# Phase 4: Frontend Visualization

## Objective
Render sub-tasks as an expandable checklist within todo nodes in both linear and graph modes. Add checkbox toggling, expand/collapse, and API integration for sub-task CRUD operations. Update the TypeScript `TodoNode` interface and API service.

## Coupling
- **Depends on**: Phase 3 (API endpoints must be defined — contract only, not necessarily merged)
- **Coupling type**: loose — frontend depends on the API contract (endpoints + JSON shape), not the backend implementation
- **Shared files with other phases**: None (frontend is a separate codebase layer)
- **Shared APIs/interfaces**: REST endpoint contracts from Phase 3, 7-key `TodoNode` JSON schema
- **Why this coupling**: Frontend consumes the API; once the contract is agreed, implementation can proceed independently

## Context
- Angular standalone component using signals (`signal()`, `computed()`)
- `TodoNode` TypeScript interface in `sse.service.ts` — currently 6 fields
- `ApiService` has methods: `getTodos()`, `setTodoComment()`, `addTodoEdge()`, `removeTodoEdge()`
- SSE handler: `eventSource.addEventListener('todo_update', ...)` sets `this.todos.set(data.todos ?? [])`
- Two render modes: linear (flat list) and graph (SVG with `foreignObject`)
- Graph mode uses `computeLayout()` for topological positioning with fixed `NODE_HEIGHT = 48`
- Comment popup in graph mode is an absolutely-positioned panel outside the SVG

### Critical Layout Consideration
In graph mode, nodes are rendered as `<foreignObject>` with fixed `NODE_HEIGHT = 48px`. Sub-tasks would need to either:
- **Option A:** Expand the `foreignObject` height dynamically (breaks layout positioning — edges would misalign)
- **Option B:** Render sub-tasks in a popup/overlay (like the comment popup) — preserves layout
- **Option C:** Render sub-tasks inline in linear mode only; show a badge/count in graph mode with a popup for details

**Decision: Option C** — In linear mode, render sub-tasks as an inline expandable checklist directly under the node text. In graph mode, show a sub-task count badge (e.g., "2/3") on the node card; clicking it opens a sub-task popup (reusing the comment popup pattern). This avoids breaking the SVG layout while providing full functionality.

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Add `SubTask` TypeScript interface | `{id: string, text: string, status: 'pending' \| 'done'}` in `sse.service.ts`. | `frontend/src/app/services/sse.service.ts` |
| 2 | Add `subtasks` field to `TodoNode` interface | `subtasks: SubTask[]` — 7th field. Default to `[]` in SSE handler if missing (defensive). | `frontend/src/app/services/sse.service.ts` |
| 3 | Add API methods for sub-task CRUD | `addSubtask(instanceId, nodeId, text)`, `updateSubtask(instanceId, nodeId, subtaskId, status, autoComplete?)`, `removeSubtask(instanceId, nodeId, subtaskId)`. | `frontend/src/app/services/api.service.ts` |
| 4 | Add sub-task state signals to component | `expandedSubtaskNodeId = signal<string \| null>(null)` for linear mode expand/collapse. `subtaskPopupNodeId` + `subtaskPopupPosition` for graph mode popup. | `todo-list.component.ts` |
| 5 | Add `subtaskCount(node)` computed helper | Returns `{done: number, total: number}` for a node's sub-tasks. Used for badge rendering. | `todo-list.component.ts` |
| 6 | Add `toggleSubtask(node, subtask, event)` method | Calls `api.updateSubtask()`, handles SSE reconciliation. **Non-optimistic** — waits for API response, then SSE reconciles (matches `saveComment` pattern). `auto_complete` is NOT exposed in the UI (agent-only feature, always sends `false`). | `todo-list.component.ts` |
| 7 | Add `toggleSubtaskExpand(node, event)` method | Toggles `expandedSubtaskNodeId` for linear mode. | `todo-list.component.ts` |
| 8 | Add `closeAllPopups()` helper | Centralized popup closure: resets `commentPopupNodeId`, `commentPopupPosition`, `subtaskPopupNodeId`, `subtaskPopupPosition`, `editingNodeId`, `editingComment`. Called by `onDocumentClick`, `onEscape`, and before opening any popup. | `todo-list.component.ts` |
| 9 | Add `openSubtaskPopup(node, event)` method | Opens sub-task popup in graph mode. **Calls `closeAllPopups()` first** to ensure mutual exclusion with comment popup. Positions popup using same edge-flip logic as `openCommentPopup`. | `todo-list.component.ts` |
| 10 | Update `openCommentPopup()` for mutual exclusion | **Call `closeAllPopups()` first** before setting comment popup state. This ensures opening comment popup closes sub-task popup and vice versa. | `todo-list.component.ts` |
| 11 | Update `onDocumentClick` and `onEscape` | Check both `commentPopupNodeId()` AND `subtaskPopupNodeId()` — close whichever is open via `closeAllPopups()`. | `todo-list.component.ts` |
| 12 | Render sub-tasks in linear mode | Below each todo item, if `node.subtasks.length > 0`: show a toggle button with count badge. When expanded, render checklist with checkboxes. | `todo-list.component.html` |
| 13 | Render sub-task badge in graph mode | On each `foreignObject` node card, if sub-tasks exist: show a small badge `2/3` next to the node text. Clicking it opens the sub-task popup. | `todo-list.component.html` |
| 14 | Render sub-task popup in graph mode | Reuse the comment popup pattern: absolutely-positioned panel with checklist items, checkboxes, and close behavior (outside-click/escape). | `todo-list.component.html` |
| 15 | Add sub-task styling | Checkbox styles, indented checklist, badge styling, popup styling. | `todo-list.component.scss` |
| 16 | Update SSE handler for defensive subtasks | In `todo_update` handler, map nodes to ensure `subtasks` defaults to `[]` if missing (backward compat with older payloads). | `frontend/src/app/services/sse.service.ts` |

## Key Files
- `frontend/src/app/components/todo-list/todo-list.component.ts` — 400 lines, estimated +120-150 lines
- `frontend/src/app/components/todo-list/todo-list.component.html` — 202 lines, estimated +80-100 lines
- `frontend/src/app/components/todo-list/todo-list.component.scss` — estimated +60-80 lines
- `frontend/src/app/services/sse.service.ts` — 405 lines, ~10 lines changed
- `frontend/src/app/services/api.service.ts` — 178 lines, ~20 lines added

## TypeScript Interface Changes

### `sse.service.ts`

```typescript
export interface SubTask {
  id: string;
  text: string;
  status: 'pending' | 'done';
}

export interface TodoNode {
  id: string;
  index: number;
  text: string;
  status: 'pending' | 'in_progress' | 'done';
  comment: string;
  next_ids: string[];
  subtasks: SubTask[];  // NEW — 7th field
}
```

### Defensive SSE handling

```typescript
eventSource.addEventListener('todo_update', (e: MessageEvent) => {
  this.ngZone.run(() => {
    try {
      const data = JSON.parse(e.data);
      const todos = (data.todos ?? []).map((t: any) => ({
        ...t,
        subtasks: t.subtasks ?? [],  // Defensive default
      }));
      this.todos.set(todos);
    } catch (err) {
      console.error('[SSE] Failed to parse todo_update:', err);
    }
  });
});
```

## API Service Methods

```typescript
addSubtask(instanceId: string, nodeId: string, text: string): Observable<TodoNode> {
  return this.http.post<TodoNode>(
    `${this.API_BASE}/instances/${instanceId}/todos/${nodeId}/subtasks`,
    { text }
  );
}

updateSubtask(
  instanceId: string, nodeId: string, subtaskId: string,
  status: string, autoComplete: boolean = false
): Observable<any> {
  return this.http.patch(
    `${this.API_BASE}/instances/${instanceId}/todos/${nodeId}/subtasks/${subtaskId}`,
    { status, auto_complete: autoComplete }
  );
}

removeSubtask(instanceId: string, nodeId: string, subtaskId: string): Observable<TodoNode> {
  return this.http.delete<TodoNode>(
    `${this.API_BASE}/instances/${instanceId}/todos/${nodeId}/subtasks/${subtaskId}`
  );
}
```

## Linear Mode Rendering (HTML)

```html
<!-- After the todo-body div, inside the todo-item -->
@if (item.subtasks && item.subtasks.length > 0) {
  <div class="subtask-section">
    <button class="subtask-toggle" (click)="toggleSubtaskExpand(item, $event)">
      <mat-icon>{{ expandedSubtaskNodeId() === item.id ? 'expand_less' : 'expand_more' }}</mat-icon>
      <span class="subtask-badge">{{ subtaskCount(item).done }}/{{ subtaskCount(item).total }}</span>
    </button>
    @if (expandedSubtaskNodeId() === item.id) {
      <div class="subtask-list">
        @for (st of item.subtasks; track st.id) {
          <div class="subtask-item" [class.done]="st.status === 'done'">
            <label class="subtask-checkbox">
              <input
                type="checkbox"
                [checked]="st.status === 'done'"
                (change)="toggleSubtask(item, st, $event)"
              />
              <span class="subtask-text">{{ st.text }}</span>
            </label>
          </div>
        }
      </div>
    }
  </div>
}
```

## Graph Mode Rendering (HTML)

On the node card (inside `foreignObject`):
```html
<span class="node-text" [title]="item.text">{{ item.text }}</span>
@if (item.subtasks && item.subtasks.length > 0) {
  <span class="subtask-badge-graph" (click)="openSubtaskPopup(item, $event)">
    {{ subtaskCount(item).done }}/{{ subtaskCount(item).total }}
  </span>
}
```

Sub-task popup (sibling of SVG, like comment popup):
```html
@if (subtaskPopupItem(); as popupItem) {
  @if (subtaskPopupPosition(); as popupPos) {
    <div class="graph-popup subtask-popup"
         [style.left.px]="popupPos.x"
         [style.top.px]="popupPos.y"
         (click)="$event.stopPropagation()"
         role="dialog"
         aria-label="Sub-tasks">
      <div class="subtask-popup-header">
        <span>{{ popupItem.text }}</span>
        <span class="subtask-badge">{{ subtaskCount(popupItem).done }}/{{ subtaskCount(popupItem).total }}</span>
      </div>
      <div class="subtask-popup-list">
        @for (st of popupItem.subtasks; track st.id) {
          <label class="subtask-checkbox">
            <input
              type="checkbox"
              [checked]="st.status === 'done'"
              (change)="toggleSubtask(popupItem, st, $event)"
            />
            <span class="subtask-text" [class.done]="st.status === 'done'">{{ st.text }}</span>
          </label>
        }
      </div>
    </div>
  }
}
```

## Constraints
- `auto_complete` is NOT exposed in the frontend UI — it's an agent-only feature (agents can pass it via tools). The frontend always sends `auto_complete: false`.
- Graph mode must NOT change `NODE_HEIGHT` — sub-tasks are shown in a popup, not inline
- Checkbox toggling is **non-optimistic** — waits for API response, then SSE reconciles. Matches the existing `saveComment` pattern. (If optimistic updates are needed later, suppress SSE for the toggled node until the API response arrives to avoid double-render.)
- SSE `todo_update` events will include the full node list with subtasks — the frontend replaces the entire `todos` signal
- The sub-task popup and comment popup are **mutually exclusive** — opening one closes the other via `closeAllPopups()`. Both `openSubtaskPopup()` and `openCommentPopup()` call `closeAllPopups()` before setting their own state.
- Outside-click and escape close any open popup via `closeAllPopups()` — `onDocumentClick` and `onEscape` check both `commentPopupNodeId()` and `subtaskPopupNodeId()`

## Deliverables
- [ ] `SubTask` interface and `TodoNode.subtasks` field added
- [ ] 3 API methods added to `ApiService`
- [ ] Sub-task checklist rendering in linear mode (expandable)
- [ ] Sub-task badge + popup in graph mode
- [ ] Checkbox toggle with API call (non-optimistic, SSE-reconciled)
- [ ] Sub-task styling (checkboxes, badges, popup)
- [ ] Defensive SSE handling (subtasks default to `[]`)
- [ ] Popup coexistence (sub-task popup + comment popup)
- [ ] Manual testing: linear mode, graph mode, SSE updates, toggle, expand/collapse
