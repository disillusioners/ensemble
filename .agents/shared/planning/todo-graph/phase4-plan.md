# Phase 4: Frontend Graph Visualization

## Objective

Transform the Angular `TodoListComponent` from a flat list renderer into a graph (DAG) visualizer. Node cards display status icons, text, and comments. Directed edges (arrows) connect nodes. The component uses SVG for edge rendering with a simple layered layout algorithm. Backward-compatible: when the graph is a linear chain (most common case), it renders as a compact vertical list identical to the current UI. The `TodoNode` TypeScript interface preserves the `index` field for backward compatibility.

## Coupling

- **Depends on**: Phase 1 (frozen SSE payload schema — 6 keys: `id`, `index`, `text`, `status`, `comment`, `next_ids`)
- **Coupling type**: **loose** — frontend depends on the SSE payload schema, not backend implementation
- **Shared files with other phases**: `frontend/src/app/services/sse.service.ts` (TodoNode interface); `frontend/src/app/services/api.service.ts` (endpoint calls); `frontend/src/app/pages/chat/chat.component.ts` (direct SSE signal write)
- **Shared APIs/interfaces**: TypeScript `TodoNode` interface; SSE `todo_update` event payload shape (frozen in Phase 1)
- **Why this coupling**: Frontend only needs the wire format (JSON shape) from Phase 1. Can start implementation once the SSE schema is frozen, even before backend is fully implemented (use mock data). No dependency on Phase 2 or Phase 3.

## Context

- Current component: `frontend/src/app/components/todo-list/`
  - `todo-list.component.ts` (181 lines) — standalone Angular component, signal-based
  - `todo-list.component.html` (78 lines) — `@for` loop over `todos()` signal
  - `todo-list.component.scss` (223 lines) — dark theme, flexbox layout
- `SseService` at `frontend/src/app/services/sse.service.ts`:
  - `TodoItem` interface at line 4: `{index, text, status, comment}`
  - `todos = signal<TodoItem[]>([])` at line 47
  - SSE handler at line 315: parses `data.todos` and sets signal
- `ApiService` at `frontend/src/app/services/api.service.ts`:
  - `getTodos(instanceId)` → `GET /api/instances/{id}/todos`
  - `setTodoComment(instanceId, index, comment)` → `POST /api/instances/{id}/todos/{index}/comment`
- `ChatComponent` at `frontend/src/app/pages/chat/chat.component.ts`:
  - Line 310: `this.sseService.todos.set(data ?? [])` — directly writes to todo signal on initial REST load
  - Line 308: `this.api.getTodos(instanceId).subscribe(...)` — fetches todos on instance switch
- Angular 21.2.5 + Angular Material 21.2.5 + CDK 21.2.5
- `mermaid ^11.4.0` already installed (not needed — custom SVG is lighter and more controllable)

## Design: TypeScript Interface Changes

### TodoNode Interface (C6 fix — preserves index, no extends chain)

> **C6 fix**: The original plan had `TodoItem extends TodoNode` which would cause TS compile failures because `TodoItem` has `index: number` but `TodoNode` didn't. The fix is to include `index` directly in `TodoNode` as a required field (it's always present in the frozen SSE payload). `TodoItem` becomes a simple type alias — no inheritance, no compile issues.

```typescript
// sse.service.ts

export interface TodoNode {
  id: string;                    // Node identifier (prefixed "n-")
  index: number;                 // Insertion-order position (PRESERVED for backward compat — C4/C6 fix)
  text: string;
  status: 'pending' | 'in_progress' | 'done';
  comment: string;
  next_ids: string[];            // Successor node IDs (adjacency list)
}

// TodoItem is now a simple alias — no extends, no compile issues
// All existing `item.index` references work because TodoNode has `index`
export type TodoItem = TodoNode;
```

**Affected files and line references that must be updated** (C6 fix — all `item.index` / `editingIndex` references):

| File | Line | Current | Updated |
|------|------|---------|---------|
| `todo-list.component.ts` | 27 | `editingIndex` comment | `editingNodeId` comment |
| `todo-list.component.ts` | 32 | `editingIndex = signal<number \| null>(null)` | `editingNodeId = signal<string \| null>(null)` |
| `todo-list.component.ts` | 100 | `this.editingIndex.set(item.index)` | `this.editingNodeId.set(item.id)` |
| `todo-list.component.ts` | 122 | `has an 'index' field` comment | `has an 'id' field` |
| `todo-list.component.ts` | 133 | `this.api.setTodoComment(targetInstanceId, item.index, comment)` | `this.api.setTodoComment(targetInstanceId, item.id, comment)` |
| `todo-list.component.ts` | 143 | `'index' in updated` | `'id' in updated` |
| `todo-list.component.ts` | 148 | `if (t.index !== item.index) return t` | `if (t.id !== item.id) return t` |
| `todo-list.component.ts` | 106 | `this.editingIndex.set(null)` | `this.editingNodeId.set(null)` |
| `todo-list.component.html` | 22 | `track item.index` | `track item.id` |
| `todo-list.component.html` | 47 | `editingIndex() === item.index` | `editingNodeId() === item.id` |
| `sse.service.ts` | 4-9 | `TodoItem { index, text, status, comment }` | `TodoNode { id, index, text, status, comment, next_ids }` + `type TodoItem = TodoNode` |
| `sse.service.ts` | 47 | `signal<TodoItem[]>` | `signal<TodoNode[]>` |
| `api.service.ts` | 109-110 | `getTodos(): Observable<TodoItem[]>` | `getTodos(): Observable<TodoNode[]>` |
| `api.service.ts` | 113-114 | `setTodoComment(instanceId, index: number, ...)` | `setTodoComment(instanceId, nodeId: string, ...)` |
| `chat.component.ts` | 310 | `this.sseService.todos.set(data ?? [])` | No code change needed — signal type flows through. But verify data shape matches `TodoNode[]`. |

### SSE Service Changes

```typescript
// sse.service.ts

// Signal type changes from TodoItem[] to TodoNode[]
todos = signal<TodoNode[]>([]);

// SSE handler — no change needed, just parses data.todos
// The dicts now have id, index, next_ids (frozen Phase 1 schema)
eventSource.addEventListener('todo_update', (e: MessageEvent) => {
  this.ngZone.run(() => {
    try {
      const data = JSON.parse(e.data);
      this.todos.set(data.todos ?? []);
    } catch (err) {
      console.error('[SSE] Failed to parse todo_update:', err);
    }
  });
});
```

### API Service Changes

```typescript
// api.service.ts

getTodos(instanceId: string): Observable<TodoNode[]> {
  return this.http.get<TodoNode[]>(`${this.API_BASE}/instances/${instanceId}/todos`);
}

// Changed: index → nodeId (string)
setTodoComment(instanceId: string, nodeId: string, comment: string): Observable<TodoNode> {
  return this.http.post<TodoNode>(
    `${this.API_BASE}/instances/${instanceId}/todos/${nodeId}/comment`,
    { comment }
  );
}

// New: edge management
addTodoEdge(instanceId: string, fromId: string, toId: string): Observable<any> {
  return this.http.post(
    `${this.API_BASE}/instances/${instanceId}/todos/edges`,
    { from_id: fromId, to_id: toId }
  );
}

removeTodoEdge(instanceId: string, fromId: string, toId: string): Observable<any> {
  return this.http.request(
    'DELETE',
    `${this.API_BASE}/instances/${instanceId}/todos/edges`,
    { body: { from_id: fromId, to_id: toId } }
  );
}
```

## Design: Graph Layout Algorithm

### Layered Layout (Sugiyama-style Lite)

> **W12 fix**: The original layout code had `startY = (containerWidth - totalHeight) / 2` which incorrectly mixed X width with Y height. The dead code is deleted. Nodes are positioned with a simple `yCursor = 0` that increments per node within each layer.

> **W13 fix**: Container width is not hardcoded to 600px. Instead, a `ResizeObserver` tracks the actual container element width and updates a signal. The layout uses the signal's value.

```typescript
/**
 * Compute graph layout positions for SVG rendering.
 *
 * Algorithm:
 * 1. Topological sort to determine layer (depth) for each node
 * 2. Nodes at the same depth are placed side-by-side
 * 3. X position = layer index * (NODE_WIDTH + LAYER_GAP_X)
 * 4. Y position = yCursor within layer, starting at 0
 *
 * For linear chains (1 node per layer), this produces a simple
 * vertical stack identical to the current flat list.
 */
function computeLayout(
  nodes: TodoNode[],
  containerWidth: number
): Map<string, {x: number, y: number}> {
  // Build adjacency + reverse adjacency
  const adj = new Map<string, string[]>();      // node_id → successors
  const reverseAdj = new Map<string, string[]>(); // node_id → predecessors

  for (const node of nodes) {
    adj.set(node.id, node.next_ids || []);
    reverseAdj.set(node.id, []);
  }
  for (const node of nodes) {
    for (const nextId of node.next_ids || []) {
      reverseAdj.get(nextId)?.push(node.id);
    }
  }

  // Assign layers via longest-path from roots
  const layers = new Map<string, number>();
  const roots = nodes.filter(n => (reverseAdj.get(n.id) || []).length === 0);

  function assignLayer(nodeId: string, depth: number) {
    const current = layers.get(nodeId) ?? -1;
    if (depth > current) {
      layers.set(nodeId, depth);
      for (const nextId of adj.get(nodeId) || []) {
        assignLayer(nextId, depth + 1);
      }
    }
  }
  for (const root of roots) {
    assignLayer(root.id, 0);
  }

  // Group nodes by layer
  const layerGroups = new Map<number, string[]>();
  for (const [nodeId, layer] of layers) {
    if (!layerGroups.has(layer)) layerGroups.set(layer, []);
    layerGroups.get(layer)!.push(nodeId);
  }

  // Assign positions — W12 fix: use yCursor, no dead code
  const positions = new Map<string, {x: number, y: number}>();
  const NODE_HEIGHT = 48;
  const NODE_GAP_Y = 12;

  for (const [layer, nodeIds] of layerGroups) {
    let yCursor = 0;  // W12 fix: simple cursor, no math error
    for (const nodeId of nodeIds) {
      positions.set(nodeId, {
        x: layer * (NODE_WIDTH + LAYER_GAP_X),
        y: yCursor,
      });
      yCursor += NODE_HEIGHT + NODE_GAP_Y;
    }
  }

  return positions;
}
```

### Rendering Decision: Flat vs Graph

```typescript
/**
 * Detect if the graph is a simple linear chain (each node has ≤1
 * successor and ≤1 predecessor). If so, render as a flat list
 * (simpler, more compact, identical to current UI).
 */
function isLinearChain(nodes: TodoNode[]): boolean {
  if (nodes.length === 0) return true;

  const hasBranch = nodes.some(n => (n.next_ids?.length || 0) > 1);
  if (hasBranch) return false;

  // Check no node has multiple predecessors
  const predCount = new Map<string, number>();
  for (const node of nodes) {
    for (const nextId of node.next_ids || []) {
      predCount.set(nextId, (predCount.get(nextId) || 0) + 1);
    }
  }
  const hasMerge = Array.from(predCount.values()).some(c => c > 1);
  return !hasMerge;
}
```

## Design: Component Architecture

### Component Structure (C11 fix — all constants declared)

> **C11 fix**: The template referenced `NODE_WIDTH`, `graphWidth()`, and `graphHeight()` without declaring them. These are now declared as class members / computed signals.

> **W13 fix**: Container width uses `ResizeObserver` to track actual element width via a signal, not a hardcoded 600px.

```typescript
@Component({
  selector: 'app-todo-list',
  standalone: true,
  imports: [CommonModule, MatIconModule],
  templateUrl: './todo-list.component.html',
  styleUrl: './todo-list.component.scss'
})
export class TodoListComponent {
  private readonly sseService = inject(SseService);
  private readonly api = inject(ApiService);
  private readonly destroyRef = inject(DestroyRef);

  instanceId = input.required<string>();
  isCollapsed = signal(false);

  // Changed: TodoNode instead of TodoItem
  todos = computed<TodoNode[]>(() => this.sseService.todos());

  // New: detect if graph is linear (use flat rendering) or branching (use SVG)
  isLinear = computed(() => isLinearChain(this.todos()));

  // C11 fix: Declare layout constants as class members
  readonly NODE_WIDTH = 200;        // px — width of each node card in SVG
  readonly NODE_HEIGHT = 48;        // px — height of each node card
  readonly LAYER_GAP_X = 80;        // px — horizontal gap between layers

  // W13 fix: Container width via ResizeObserver (not hardcoded)
  containerWidth = signal(600);     // default, updated by ResizeObserver
  private resizeObserver?: ResizeObserver;

  // New: computed layout positions for SVG rendering
  // Only computed when isLinear() is false
  layout = computed(() => {
    if (this.isLinear()) return null;
    return computeLayout(this.todos(), this.containerWidth());
  });

  // C11 fix: graphWidth and graphHeight as computed signals
  graphWidth = computed(() => {
    if (this.isLinear()) return 0;
    const maxLayer = Math.max(0, ...(this.layout()?.values() ?
      Array.from(this.layout()!.values()).map(p => p.x) : [0]));
    return maxLayer + this.NODE_WIDTH + 20;  // padding
  });

  graphHeight = computed(() => {
    if (this.isLinear()) return 0;
    const maxY = Math.max(0, ...(this.layout()?.values() ?
      Array.from(this.layout()!.values()).map(p => p.y) : [0]));
    return maxY + this.NODE_HEIGHT + 20;  // padding
  });

  // New: computed edges for SVG rendering
  edges = computed(() => {
    if (this.isLinear()) return [];
    const edges: {from: string, to: string, fromPos: {x,y}, toPos: {x,y}}[] = [];
    const positions = this.layout();
    if (!positions) return [];
    for (const node of this.todos()) {
      for (const nextId of node.next_ids || []) {
        const fromPos = positions.get(node.id);
        const toPos = positions.get(nextId);
        if (fromPos && toPos) {
          edges.push({from: node.id, to: nextId, fromPos, toPos});
        }
      }
    }
    return edges;
  });

  // Existing (adapted)
  isVisible = computed(() => this.todos().length > 0);
  doneCount = computed(() => this.todos().filter(t => t.status === 'done').length);
  totalCount = computed(() => this.todos().length);

  // Comment editor — C6 fix: changed from editingIndex to editingNodeId
  editingNodeId = signal<string | null>(null);
  editingComment = signal<string>('');
  isSavingComment = signal(false);

  // W13 fix: Set up ResizeObserver on graph container
  // Called via #graphContainer ViewChild after view init
  setupResizeObserver(element: HTMLElement) {
    this.resizeObserver = new ResizeObserver(entries => {
      for (const entry of entries) {
        this.containerWidth.set(entry.contentRect.width);
      }
    });
    this.resizeObserver.observe(element);
  }

  // ... methods adapted for node IDs (see C6 fix table above)
}
```

### Template: Linear Mode (Flat List — Same as Current, C6 fix: track item.id)

```html
@if (isVisible()) {
  <div class="todo-list-container">
    <div class="todo-header" (click)="toggle()">
      <span class="todo-title">📋 Tasks ({{ doneCount() }}/{{ totalCount() }} completed)</span>
      <div class="header-actions">
        <button class="icon-btn refresh-btn" type="button" (click)="refresh($event)">
          <mat-icon>refresh</mat-icon>
        </button>
        <button class="toggle-btn" type="button">
          <mat-icon>{{ isCollapsed() ? 'expand_more' : 'expand_less' }}</mat-icon>
        </button>
      </div>
    </div>
    @if (!isCollapsed()) {
      @if (isLinear()) {
        <!-- Linear chain: flat list (same as current UI) -->
        <div class="todo-items">
          @for (item of todos(); track item.id) {
            <div class="todo-item" [class.done]="item.status === 'done'">
              <span class="status-icon"
                    [class.in-progress]="item.status === 'in_progress'"
                    [class.complete]="item.status === 'done'">
                {{ statusIcon(item.status) }}
              </span>
              <div class="todo-body">
                <div class="todo-row">
                  <span class="todo-text">{{ item.text }}</span>
                  <button class="icon-btn comment-btn" type="button"
                          (click)="startEditComment(item, $event)">
                    <mat-icon>{{ item.comment ? 'chat_bubble' : 'add_comment' }}</mat-icon>
                  </button>
                </div>
                @if (item.comment) { <div class="todo-comment">{{ item.comment }}</div> }
                @if (editingNodeId() === item.id) {
                  <!-- Inline comment editor (same as current) -->
                }
              </div>
            </div>
          }
        </div>
      } @else {
        <!-- Branching graph: SVG rendering -->
        <div class="todo-graph" #graphContainer>
          <svg class="graph-svg" [attr.width]="graphWidth()" [attr.height]="graphHeight()">
            <!-- Draw edges (arrows) -->
            @for (edge of edges(); track $index) {
              <line
                [attr.x1]="edge.fromPos.x + NODE_WIDTH"
                [attr.y1]="edge.fromPos.y + NODE_HEIGHT/2"
                [attr.x2]="edge.toPos.x"
                [attr.y2]="edge.toPos.y + NODE_HEIGHT/2"
                class="graph-edge"
                marker-end="url(#arrowhead)"
              />
            }
            <!-- Arrowhead marker definition -->
            <defs>
              <marker id="arrowhead" markerWidth="8" markerHeight="6" refX="8" refY="3" orient="auto">
                <polygon points="0 0, 8 3, 0 6" fill="#475569" />
              </marker>
            </defs>
            <!-- Draw nodes as foreignObject (HTML inside SVG) -->
            @for (item of todos(); track item.id) {
              <foreignObject
                [attr.x]="layout()?.get(item.id)?.x"
                [attr.y]="layout()?.get(item.id)?.y"
                [attr.width]="NODE_WIDTH"
                [attr.height]="NODE_HEIGHT"
              >
                <div class="graph-node" [class.done]="item.status === 'done'">
                  <span class="status-icon">{{ statusIcon(item.status) }}</span>
                  <span class="node-text">{{ item.text }}</span>
                  <button class="icon-btn comment-btn" (click)="startEditComment(item, $event)">
                    <mat-icon>{{ item.comment ? 'chat_bubble' : 'add_comment' }}</mat-icon>
                  </button>
                </div>
              </foreignObject>
            }
          </svg>
        </div>
      }
    }
  </div>
}
```

### SCSS Additions (Graph Mode)

```scss
// New styles for graph rendering

.todo-graph {
  padding: 12px;
  overflow-x: auto;
  overflow-y: hidden;
}

.graph-svg {
  display: block;
}

.graph-edge {
  stroke: #475569;
  stroke-width: 1.5;
  fill: none;
}

.graph-node {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 10px;
  background: #1e293b;
  border: 1px solid #334155;
  border-radius: 6px;
  font-size: 0.85rem;
  color: #e2e8f0;
  height: 100%;
  box-sizing: border-box;

  &.done .node-text {
    text-decoration: line-through;
    opacity: 0.6;
  }
}

.node-text {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
```

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Update `TodoItem` → `TodoNode` interface | Add `id: string`, `next_ids: string[]`. PRESERVE `index: number` (C4/C6 fix). Make `TodoItem` a type alias (`type TodoItem = TodoNode`), NOT an extends. | `frontend/src/app/services/sse.service.ts` |
| 2 | Update `todos` signal type | `signal<TodoNode[]>` instead of `signal<TodoItem[]>`. | `frontend/src/app/services/sse.service.ts` |
| 3 | Update `ApiService.getTodos()` | Return type `Observable<TodoNode[]>`. | `frontend/src/app/services/api.service.ts` |
| 4 | Update `ApiService.setTodoComment()` | Change `index: number` → `nodeId: string` in URL path. | `frontend/src/app/services/api.service.ts` |
| 5 | Add `ApiService.addTodoEdge()` | New method for POST edges endpoint. | `frontend/src/app/services/api.service.ts` |
| 6 | Add `ApiService.removeTodoEdge()` | New method for DELETE edges endpoint. | `frontend/src/app/services/api.service.ts` |
| 7 | Implement `isLinearChain()` function | Detect if graph is linear (≤1 successor + ≤1 predecessor per node). | `todo-list.component.ts` |
| 8 | Implement `computeLayout()` function | Topological sort → layered positions. Use `yCursor = 0` (W12 fix — no dead code, no math error). Return `Map<nodeId, {x, y}>`. | `todo-list.component.ts` |
| 9 | Update component signals | `isLinear`, `layout`, `edges`, `graphWidth`, `graphHeight` computed signals (C11 fix). Change `editingIndex` → `editingNodeId` (C6 fix). | `todo-list.component.ts` |
| 10 | Declare layout constants (C11 fix) | `NODE_WIDTH = 200`, `NODE_HEIGHT = 48`, `LAYER_GAP_X = 80` as class members. `graphWidth()` and `graphHeight()` as computed signals. | `todo-list.component.ts` |
| 11 | Set up ResizeObserver (W13 fix) | Track `#graphContainer` element width via `ResizeObserver`. Update `containerWidth` signal. Clean up on destroy. | `todo-list.component.ts` |
| 12 | Update `startEditComment()` / `saveComment()` | Use `node.id` instead of `node.index` (C6 fix — lines 100, 133, 143, 148). | `todo-list.component.ts` |
| 13 | Update `refresh()` | SSE signal type change flows through. | `todo-list.component.ts` |
| 14 | Update `statusIcon()` | No change — same 3 statuses. | `todo-list.component.ts` |
| 15 | Update HTML template | Add `@if (isLinear())` branch. Linear path uses `track item.id` (C6 fix — was `track item.index`). Graph path uses SVG with `foreignObject`. | `todo-list.component.html` |
| 16 | Add graph SCSS styles | `.todo-graph`, `.graph-svg`, `.graph-edge`, `.graph-node`, `.node-text`. | `todo-list.component.scss` |
| 17 | Update `track` functions | Change `track item.index` → `track item.id` in all `@for` loops (C6 fix). | `todo-list.component.html` |
| 18 | Verify comment editor works with node IDs | `editingNodeId()` replaces `editingIndex()`. Comment save calls `setTodoComment(instanceId, item.id, comment)`. | `todo-list.component.ts` + `.html` |
| 19 | Verify `chat.component.ts` SSE write (C12 fix) | Line 310: `this.sseService.todos.set(data ?? [])` — verify data shape matches `TodoNode[]`. No code change needed, but this file is in scope for verification. | `frontend/src/app/pages/chat/chat.component.ts` |

## Key Files

- `frontend/src/app/components/todo-list/todo-list.component.ts` — **PRIMARY** — component logic, layout algorithm, signals, constants (C11), ResizeObserver (W13)
- `frontend/src/app/components/todo-list/todo-list.component.html` — template with linear/graph branches, `track item.id` (C6)
- `frontend/src/app/components/todo-list/todo-list.component.scss` — graph styles
- `frontend/src/app/services/sse.service.ts` — `TodoNode` interface with `index` preserved (C4/C6), signal type
- `frontend/src/app/services/api.service.ts` — endpoint method updates for node IDs + edge endpoints
- `frontend/src/app/pages/chat/chat.component.ts` — **C12 fix** — line 310 directly writes to todo signal; verify `TodoNode[]` shape compatibility

## Constraints

- **Angular 21** — standalone components, signal-based, `@if`/`@for` control flow
- **No new npm dependencies** — use SVG + `foreignObject` for graph rendering (no D3, dagre, etc.)
- **Performance** — typical graphs are 5-20 nodes; no virtual scrolling needed. SVG with ~20 nodes renders instantly.
- **Dark theme** — match existing color palette (`#0f172a`, `#1e293b`, `#e2e8f0`, `#06b6d4`, `#8b5cf6`)
- **Responsive** — graph container has `overflow-x: auto` for wide graphs; container width tracked via `ResizeObserver` (W13)
- **Backward compatible rendering** — linear chains render as flat list (identical to current UI)
- **Backward compatible interface** — `TodoNode` includes `index: number` (C4/C6 fix); `TodoItem = TodoNode` type alias
- **Comment feature** — must work in both linear and graph modes; keyed by `node.id` (C6 fix)
- **Accessibility** — SVG nodes should have `title`/`aria-label` for screen readers
- **All constants declared** — `NODE_WIDTH`, `NODE_HEIGHT`, `LAYER_GAP_X`, `graphWidth()`, `graphHeight()` must be class members or computed signals (C11 fix)
- **Frontend type-check passes** — `ng build` succeeds with no TS errors (W14)

## Deliverables

- [ ] `TodoNode` TypeScript interface with `id`, `index` (preserved), `next_ids` fields (C4/C6)
- [ ] `TodoItem = TodoNode` type alias (no extends — C6 fix)
- [ ] `todos` signal typed as `TodoNode[]`
- [ ] `isLinearChain()` detection function
- [ ] `computeLayout()` layered layout algorithm with `yCursor` (W12 fix — no dead code)
- [ ] `NODE_WIDTH`, `NODE_HEIGHT`, `LAYER_GAP_X` declared as class members (C11 fix)
- [ ] `graphWidth()`, `graphHeight()` declared as computed signals (C11 fix)
- [ ] `containerWidth` tracked via `ResizeObserver` (W13 fix)
- [ ] Linear mode renders as flat list (same as current)
- [ ] Graph mode renders SVG with node cards + directed edge arrows
- [ ] Comment editor works with node IDs in both modes (C6 fix — all references updated)
- [ ] `track` functions use `item.id` instead of `item.index` (C6 fix)
- [ ] `ApiService` methods updated for node IDs + edge endpoints
- [ ] `chat.component.ts` verified for SSE signal compatibility (C12 fix)
- [ ] Graph SCSS styles match dark theme
- [ ] No new npm dependencies added
- [ ] **Frontend type-check passes (`ng build` succeeds)** (W14 fix)
