# Phase 4: Frontend — Todo-List Component

## Objective
Create a standalone Angular `todo-list` component that displays the agent's todo list above the chat input box, updates in real-time via SSE `todo_update` events, and is collapsible with a minimize/collapse button. Only visible when the todo list is not empty.

## Coupling
- **Depends on**: Phase 2 (SSE event payload contract)
- **Coupling type**: loose
- **Shared files with other phases**: None (different codebase — Angular frontend)
- **Shared APIs/interfaces**: SSE event payload `{event_type: "todo_update", instance_id, todos: [{index, text, status}]}`
- **Why this coupling**: Frontend only needs the SSE event contract to implement against. Can code the component in parallel with backend, but integration testing requires Phase 2's SSE emission working. Loose coupling — code against spec, test against running backend.

## Context
- All components are **standalone** (Angular 21 signals pattern)
- Chat page: `frontend/src/app/pages/chat/chat.component.ts` + `chat.html`
- Layout in `chat.html`: `app-chat-interface` and `app-message-input` are siblings stacked in `.chat-area`
- SSE Service: `frontend/src/app/services/sse.service.ts` — per-instance events via `EventSource`
- Theme: Material Design dark theme, `#0f172a` background, `#1e293b` borders, `#e2e8f0` text, violet primary
- Collapsible pattern exists: `job-card.component` uses `expanded` signal + `fadeIn` animation

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Add `todo_update` SSE event handler | Add `addEventListener('todo_update', ...)` in SseService following existing pattern. Parse payload, expose via signal/subject. Store todos per instance_id. | `frontend/src/app/services/sse.service.ts` |
| 2 | Create todo-list component (TS) | Standalone component. Inputs: `instanceId`. Uses signals for `todos`, `isCollapsed`. Subscribes to SSE events filtered by instanceId. Status indicator mapping: pending→○, in_progress→◐, done→●. | `frontend/src/app/components/todo-list/todo-list.component.ts` (NEW) |
| 3 | Create todo-list template (HTML) | Collapsible header with toggle button. List of items with status indicators + text. Hidden when empty. FadeIn animation on expand. | `frontend/src/app/components/todo-list/todo-list.component.html` (NEW) |
| 4 | Create todo-list styles (SCSS) | Material dark theme colors. Compact, clean styling. Status indicator colors: pending=slate, in_progress=cyan, done=violet/green. | `frontend/src/app/components/todo-list/todo-list.component.scss` (NEW) |
| 5 | Integrate into chat page | Import `TodoListComponent` in `chat.component.ts`. Add `<app-todo-list [instanceId]="selectedInstanceId()" />` in `chat.html` between `<app-chat-interface>` and `<app-message-input>`. | `frontend/src/app/pages/chat/chat.component.ts`, `chat.html` |

## Key Files
- `frontend/src/app/components/todo-list/todo-list.component.ts` (NEW)
- `frontend/src/app/components/todo-list/todo-list.component.html` (NEW)
- `frontend/src/app/components/todo-list/todo-list.component.scss` (NEW)
- `frontend/src/app/services/sse.service.ts` (MODIFY — add todo_update handler)
- `frontend/src/app/pages/chat/chat.component.ts` (MODIFY — import TodoListComponent)
- `frontend/src/app/pages/chat/chat.html` (MODIFY — add `<app-todo-list>`)

## Detailed Design

### SSE Handler (sse.service.ts)

```typescript
// In connect()/reconnect(), add listener:
eventSource.addEventListener('todo_update', (e: MessageEvent) => {
  this.ngZone.run(() => {
    const data = JSON.parse(e.data);
    // data: { instance_id, event_type: "todo_update", todos: [{index, text, status}] }
    this.todos.update(todos => {
      const filtered = todos.filter(t => t.instance_id !== data.instance_id);
      return [...filtered, { instance_id: data.instance_id, items: data.todos }];
    });
  });
});

// Public signal for components to consume
todos = signal<{instance_id: string, items: TodoItem[]}[]>([]);
```

### Component Structure (todo-list.component.ts)

```typescript
interface TodoItem {
  index: number;
  text: string;
  status: 'pending' | 'in_progress' | 'done';
}

@Component({
  selector: 'app-todo-list',
  standalone: true,
  imports: [CommonModule, MatIconModule],
  templateUrl: './todo-list.component.html',
  styleUrl: './todo-list.component.scss'
})
export class TodoListComponent {
  private sseService = inject(SseService);
  
  instanceId = input.required<string>();
  isCollapsed = signal(false);
  
  // Filter todos for this instance
  todos = computed(() => {
    const all = this.sseService.todos();
    const found = all.find(t => t.instance_id === this.instanceId());
    return found?.items ?? [];
  });
  
  // Only show when not empty
  isVisible = computed(() => this.todos().length > 0);
  
  toggle() { this.isCollapsed.update(v => !v); }
  
  statusIcon(status: string): string {
    return { pending: '○', in_progress: '◐', done: '●' }[status] ?? '○';
  }
}
```

### Template (todo-list.component.html)

```html
@if (isVisible()) {
  <div class="todo-list-container">
    <div class="todo-header" (click)="toggle()">
      <span class="todo-title">📋 Tasks ({{ todos().length }})</span>
      <button class="toggle-btn">
        <mat-icon>{{ isCollapsed() ? 'expand_more' : 'expand_less' }}</mat-icon>
      </button>
    </div>
    @if (!isCollapsed()) {
      <div class="todo-items">
        @for (item of todos(); track item.index) {
          <div class="todo-item" [class.done]="item.status === 'done'">
            <span class="status-icon">{{ statusIcon(item.status) }}</span>
            <span class="todo-text">{{ item.text }}</span>
          </div>
        }
      </div>
    }
  </div>
}
```

### Styles (todo-list.component.scss)

```scss
.todo-list-container {
  background: #0f172a;
  border: 1px solid #1e293b;
  border-radius: 8px;
  margin: 0 16px 8px;
  overflow: hidden;
}

.todo-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 8px 12px;
  cursor: pointer;
  background: #1e293b;
  
  .todo-title { color: #e2e8f0; font-size: 0.875rem; font-weight: 500; }
}

.todo-items {
  padding: 4px 0;
  animation: fadeIn 0.3s ease;
}

.todo-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 12px;
  font-size: 0.85rem;
  color: #e2e8f0;
  
  &.done .todo-text { text-decoration: line-through; opacity: 0.6; }
  
  .status-icon {
    width: 16px;
    text-align: center;
    color: #94a3b8;  /* pending - slate */
    &.in-progress { color: #06b6d4; }  /* in_progress - cyan */
    &.complete { color: #8b5cf6; }     /* done - violet */
  }
}

@keyframes fadeIn {
  from { opacity: 0; transform: translateY(-8px); }
  to { opacity: 1; transform: translateY(0); }
}
```

### Chat Page Integration (chat.html)

```html
<!-- Existing structure, add todo-list between chat-interface and message-input -->
<div class="chat-area">
  <div class="chat-header">...</div>
  <app-chat-interface />
  <app-todo-list [instanceId]="selectedInstanceId()" />   <!-- NEW -->
  <app-message-input />
</div>
```

## Constraints
- Component must be standalone (Angular 21 pattern, no NgModules)
- Use Angular signals (`signal()`, `computed()`, `input.required()`) — not RxJS subjects
- Dark theme colors: `#0f172a` bg, `#1e293b` border, `#e2e8f0` text
- Only visible when todo list is non-empty
- Must handle instance switching (todos are per-instance, component receives `instanceId` input)
- Collapsed state can be per-component (resets on instance switch) or persisted — keep simple (resets)
- No drag-and-drop or manual editing — this is a read-only display (agent manages todos via tools)

## Deliverables
- [ ] `todo-list.component.ts` — Standalone component with signal-based state
- [ ] `todo-list.component.html` — Collapsible template with status indicators
- [ ] `todo-list.component.scss` — Dark theme styling
- [ ] `sse.service.ts` — `todo_update` event handler
- [ ] `chat.component.ts` + `chat.html` — TodoListComponent integrated
- [ ] Frontend compiles without errors (`npm run build`)
