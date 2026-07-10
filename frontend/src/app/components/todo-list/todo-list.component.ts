import { Component, input, signal, computed, inject, effect, DestroyRef, ViewChild, ElementRef, HostListener } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { CommonModule } from '@angular/common';
import { MatIconModule } from '@angular/material/icon';
import { ApiService } from '../../services/api.service';
import { SseService, TodoNode, TodoItem, SubTask } from '../../services/sse.service';

// Module-level helpers (no closure over component state)

function isLinearChain(nodes: TodoNode[]): boolean {
  if (nodes.length === 0) return true;
  const hasBranch = nodes.some(n => (n.next_ids?.length || 0) > 1);
  if (hasBranch) return false;
  const predCount = new Map<string, number>();
  for (const node of nodes) {
    for (const nextId of node.next_ids || []) {
      predCount.set(nextId, (predCount.get(nextId) || 0) + 1);
    }
  }
  const hasMerge = Array.from(predCount.values()).some(c => c > 1);
  return !hasMerge;
}

const NODE_WIDTH = 200;
const NODE_HEIGHT = 48;
const NODE_GAP_Y = 12;
const LAYER_GAP_X = 80;
const NODE_CENTER_Y_OFFSET = NODE_HEIGHT / 2;

// Graph-mode comment popup sizing. The popup is a small absolutely-positioned
// panel anchored next to the clicked node; these values are used to decide
// whether the popup fits to the right / left / above / below the node.
const POPUP_WIDTH = 240;
const POPUP_HEIGHT = 110;
const POPUP_GAP = 8;
const GRAPH_CONTAINER_PADDING = 12;
const MAX_COMMENT_LENGTH = 1000;
const COMMENT_WARN_THRESHOLD = 800;

function computeLayout(nodes: TodoNode[]): Map<string, { x: number; y: number }> {
  const adj = new Map<string, string[]>();
  const reverseAdj = new Map<string, string[]>();
  for (const node of nodes) {
    adj.set(node.id, node.next_ids || []);
    reverseAdj.set(node.id, []);
  }
  for (const node of nodes) {
    for (const nextId of node.next_ids || []) {
      reverseAdj.get(nextId)?.push(node.id);
    }
  }
  const layers = new Map<string, number>();
  const roots = nodes.filter(n => (reverseAdj.get(n.id) || []).length === 0);

  function assignLayer(nodeId: string, depth: number) {
    if (depth > nodes.length) return;  // Cycle guard — depth can't exceed node count in a DAG
    const current = layers.get(nodeId) ?? -1;
    if (depth > current) {
      layers.set(nodeId, depth);
      for (const nextId of adj.get(nodeId) || []) {
        if (adj.has(nextId)) {  // Only recurse to nodes that exist
          assignLayer(nextId, depth + 1);
        }
      }
    }
  }
  for (const root of roots) {
    assignLayer(root.id, 0);
  }
  const layerGroups = new Map<number, string[]>();
  for (const [nodeId, layer] of layers) {
    if (!layerGroups.has(layer)) layerGroups.set(layer, []);
    layerGroups.get(layer)!.push(nodeId);
  }
  const positions = new Map<string, { x: number; y: number }>();
  for (const [layer, nodeIds] of layerGroups) {
    let yCursor = 0;
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

interface Edge {
  from: string;
  to: string;
  fromPos: { x: number; y: number };
  toPos: { x: number; y: number };
}

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

  readonly NODE_WIDTH = NODE_WIDTH;
  readonly NODE_HEIGHT = NODE_HEIGHT;
  readonly NODE_CENTER_Y_OFFSET = NODE_CENTER_Y_OFFSET;

  instanceId = input.required<string>();

  // Default expanded so a freshly-rendered todo list is immediately visible.
  isCollapsed = signal(false);

  // Inline comment editor state — keyed by node id (was index).
  editingNodeId = signal<string | null>(null);
  editingComment = signal<string>('');
  isSavingComment = signal(false);

  // Graph-mode comment popup state. Linear mode ignores these and uses the
  // existing inline editor below each todo item.
  commentPopupNodeId = signal<string | null>(null);
  commentPopupPosition = signal<{ x: number; y: number; placement: 'right' | 'left' | 'above' | 'below' } | null>(null);

  // Graph-mode sub-task popup state. Rendered as a sibling panel — mutually
  // exclusive with the comment popup (see openCommentPopup / openSubtaskPopup
  // which both call closeAllPopups first).
  subtaskPopupNodeId = signal<string | null>(null);
  subtaskPopupPosition = signal<{ x: number; y: number; placement: 'right' | 'left' | 'above' | 'below' } | null>(null);

  // Linear-mode expansion: which todo row's sub-task checklist is open.
  expandedSubtaskNodeId = signal<string | null>(null);

  // Buffer for the inline "Add a sub-task…" input. Shared between linear and
  // graph popups so the user doesn't lose what they typed when switching
  // context.
  newSubtaskText = signal<string>('');

  // Reference to the .todo-graph container — used by openCommentPopup to read
  // scroll position and client size for edge-flip math.
  @ViewChild('graphContainer') graphContainer?: ElementRef<HTMLDivElement>;

  todos = computed<TodoNode[]>(() => this.sseService.todos());

  isVisible = computed(() => this.todos().length > 0);

  isLinear = computed(() => isLinearChain(this.todos()));

  layout = computed<Map<string, { x: number; y: number }> | null>(() => {
    if (this.isLinear()) return null;
    return computeLayout(this.todos());
  });

  graphWidth = computed(() => {
    if (this.isLinear()) return 0;
    const positions = this.layout();
    if (!positions || positions.size === 0) return 0;
    const maxX = Math.max(...Array.from(positions.values()).map(p => p.x));
    return maxX + NODE_WIDTH + 20;
  });

  graphHeight = computed(() => {
    if (this.isLinear()) return 0;
    const positions = this.layout();
    if (!positions || positions.size === 0) return 0;
    const maxY = Math.max(...Array.from(positions.values()).map(p => p.y));
    return maxY + NODE_HEIGHT + 20;
  });

  edges = computed<Edge[]>(() => {
    if (this.isLinear()) return [];
    const positions = this.layout();
    if (!positions) return [];
    const result: Edge[] = [];
    for (const node of this.todos()) {
      for (const nextId of node.next_ids || []) {
        const fromPos = positions.get(node.id);
        const toPos = positions.get(nextId);
        if (fromPos && toPos) {
          result.push({ from: node.id, to: nextId, fromPos, toPos });
        }
      }
    }
    return result;
  });

  doneCount = computed(() => this.todos().filter(t => t.status === 'done').length);
  totalCount = computed(() => this.todos().length);

  // Derived popup item — looked up from todos by the popup's node id. Null
  // when no popup is open or when the node has been removed from todos.
  commentPopupItem = computed<TodoItem | null>(() => {
    const id = this.commentPopupNodeId();
    if (!id) return null;
    return this.todos().find(t => t.id === id) ?? null;
  });

  subtaskPopupItem = computed<TodoNode | null>(() => {
    const id = this.subtaskPopupNodeId();
    if (!id) return null;
    return this.todos().find(t => t.id === id) ?? null;
  });

  commentCharCount = computed(() => this.editingComment().length);
  commentCharWarning = computed(() => this.commentCharCount() >= COMMENT_WARN_THRESHOLD);
  commentCharExceeded = computed(() => this.commentCharCount() > MAX_COMMENT_LENGTH);

  constructor() {
    // Reset collapse + close any open editor when the user switches instances.
    effect(() => {
      this.instanceId(); // track
      this.isCollapsed.set(false);
      this.cancelEdit();
    }, { allowSignalWrites: true });
  }

  /**
   * Coordinates of a node from the layout, or undefined in linear mode.
   * Used by the template to position foreignObject nodes.
   */
  positionFor(nodeId: string): { x: number; y: number } | undefined {
    return this.layout()?.get(nodeId);
  }

  toggle(): void {
    this.isCollapsed.update(v => !v);
  }

  /**
   * Defensively default each node's `subtasks` to `[]`. Mirrors the same
   * normalization applied in the SSE `todo_update` handler so REST-derived
   * payloads (refresh / subtask mutations) render without template errors
   * against older or partial backend responses.
   */
  private normalizeTodos(todos: TodoNode[] | null | undefined): TodoNode[] {
    return (todos ?? []).map(t => ({ ...t, subtasks: t.subtasks ?? [] }));
  }

  refresh(event: Event): void {
    event.stopPropagation();
    const instanceId = this.instanceId();
    this.api.getTodos(instanceId)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (data) => {
          if (this.instanceId() !== instanceId) return;
          this.sseService.todos.set(this.normalizeTodos(data));
        },
        error: (err) => {
          if (this.instanceId() !== instanceId) return;
          console.error('Failed to refresh todos:', err);
        },
      });
  }

  startEditComment(item: TodoItem, event: Event): void {
    event.stopPropagation();
    this.editingNodeId.set(item.id);
    this.editingComment.set(item.comment);
  }

  /**
   * Graph-mode entry point. Opens (or re-positions) a small popup anchored to
   * the clicked node. Called from the node's comment button in the SVG —
   * separate from `startEditComment` which linear mode uses for its inline
   * editor below each item.
   */
  openCommentPopup(item: TodoItem, event: MouseEvent): void {
    event.stopPropagation();
    // Mutually exclusive with the sub-task popup: tear down any other popup
    // (and clear the inline editor state) before computing placement.
    this.closeAllPopups();

    const pos = this.positionFor(item.id);
    const container = this.graphContainer?.nativeElement;
    if (!pos || !container) return;

    // Node edges in container coordinates. The SVG is at (12, 12) inside
    // `.todo-graph` because of its 12px padding, so SVG coords map to
    // container coords by adding the padding offset.
    const nodeLeft = pos.x + GRAPH_CONTAINER_PADDING;
    const nodeRight = nodeLeft + NODE_WIDTH;
    const nodeTop = pos.y + GRAPH_CONTAINER_PADDING;
    const nodeCenterY = pos.y + GRAPH_CONTAINER_PADDING + NODE_CENTER_Y_OFFSET;

    const visibleLeft = container.scrollLeft;
    const visibleRight = visibleLeft + container.clientWidth;
    const visibleBottom = container.clientHeight;

    // Pick horizontal placement: prefer right of the node, flip to left if
    // the popup would overflow the visible right edge, otherwise place
    // centered below as a last resort.
    const fitsRight = nodeRight + POPUP_GAP + POPUP_WIDTH <= visibleRight;
    const fitsLeft = nodeLeft - POPUP_GAP - POPUP_WIDTH >= visibleLeft;

    let placement: 'right' | 'left' | 'above' | 'below';
    let x: number;
    if (fitsRight) {
      placement = 'right';
      x = nodeRight + POPUP_GAP;
    } else if (fitsLeft) {
      placement = 'left';
      x = nodeLeft - POPUP_GAP - POPUP_WIDTH;
    } else {
      placement = 'below';
      x = nodeLeft + (NODE_WIDTH - POPUP_WIDTH) / 2;
      // Clamp horizontally into the visible area.
      x = Math.max(visibleLeft + 4, Math.min(x, visibleRight - POPUP_WIDTH - 4));
    }

    // Pick vertical position. For side placements, center on the node and
    // flip above if it overflows the bottom. For the below fallback, place
    // under the node and flip above if there's no room.
    let y: number;
    if (placement === 'right' || placement === 'left') {
      y = nodeCenterY - POPUP_HEIGHT / 2;
      if (y + POPUP_HEIGHT > visibleBottom) {
        y = nodeTop - POPUP_GAP - POPUP_HEIGHT;
        if (y < 0) y = Math.max(4, visibleBottom - POPUP_HEIGHT - 4);
      } else if (y < 0) {
        y = 4;
      }
    } else {
      y = nodeTop + NODE_HEIGHT + POPUP_GAP;
      if (y + POPUP_HEIGHT > visibleBottom) {
        y = nodeTop - POPUP_GAP - POPUP_HEIGHT;
        placement = 'above';
        if (y < 0) y = 4;
      }
    }

    // Set shared editor state (used by Save / Cancel) and popup state.
    this.editingNodeId.set(item.id);
    this.editingComment.set(item.comment);
    this.commentPopupNodeId.set(item.id);
    this.commentPopupPosition.set({ x, y, placement });
  }

  /**
   * Reset the shared comment editor state. Graph-mode popup signals are
   * also cleared so linear-mode cancel behaves identically to closing the
   * graph popups — delegates to `closeAllPopups` so the two paths can
   * never drift.
   */
  cancelEdit(event?: Event): void {
    if (event) event.stopPropagation();
    this.closeAllPopups();
  }

  /**
   * Centralized teardown for both graph-mode popups and the shared editor
   * state. Used by openCommentPopup / openSubtaskPopup to enforce mutual
   * exclusion (opening one closes the other), by outside-click / Escape
   * handlers, and by cancelEdit so all transient UI state goes away
   * together.
   */
  closeAllPopups(): void {
    this.commentPopupNodeId.set(null);
    this.commentPopupPosition.set(null);
    this.subtaskPopupNodeId.set(null);
    this.subtaskPopupPosition.set(null);
    this.editingNodeId.set(null);
    this.editingComment.set('');
  }

  // Outside-click detection for the graph-mode popups. Safe to leave
  // unconditional: when no popup is open, this is a no-op. The popup's
  // own div calls stopPropagation on click, and the opener buttons also
  // stop propagation — so this handler only fires for clicks that
  // genuinely landed outside the popup. Uses closeAllPopups (not just
  // one popup's teardown) so both popup states and the shared editor
  // state are reset together.
  @HostListener('document:click', ['$event'])
  onDocumentClick(_event: MouseEvent): void {
    if (this.commentPopupNodeId() || this.subtaskPopupNodeId()) {
      this.closeAllPopups();
    }
  }

  @HostListener('document:keydown.escape', ['$event'])
  onEscape(event: Event): void {
    if (this.commentPopupNodeId() || this.subtaskPopupNodeId()) {
      event.preventDefault();
      this.closeAllPopups();
    }
  }

  onCommentInput(event: Event): void {
    const target = event.target as HTMLTextAreaElement;
    this.editingComment.set(target.value);
  }

  /**
   * Persist the comment, then update the shared todos signal. The backend
   * response is treated as the source of truth when it looks like a TodoNode
   * (has an `id` field); otherwise we patch the local entry with the
   * just-typed comment as a graceful fallback.
   */
  saveComment(item: TodoItem, event: Event): void {
    event.stopPropagation();
    if (this.isSavingComment()) return;
    const targetInstanceId = this.instanceId();
    const comment = this.editingComment().trim();
    this.isSavingComment.set(true);
    this.api.setTodoComment(targetInstanceId, item.id, comment)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (updated) => {
          if (this.instanceId() !== targetInstanceId) {
            this.isSavingComment.set(false);
            return;
          }
          const returnedItem: TodoItem | null =
            updated && typeof updated === 'object' && 'id' in updated
              ? (updated as TodoItem)
              : null;
          this.sseService.todos.update(list =>
            list.map(t => {
              if (t.id !== item.id) return t;
              if (returnedItem) return { ...t, ...returnedItem };
              return { ...t, comment };
            })
          );
          this.isSavingComment.set(false);
          this.cancelEdit();
        },
        error: (err) => {
          if (this.instanceId() !== targetInstanceId) {
            this.isSavingComment.set(false);
            return;
          }
          console.error('Failed to save todo comment:', err);
          this.isSavingComment.set(false);
        },
      });
  }

  statusIcon(status: string): string {
    switch (status) {
      case 'in_progress': return '◐';
      case 'done': return '●';
      case 'pending':
      default: return '○';
    }
  }

  // ---- Sub-task helpers ------------------------------------------------

  /**
   * Count done vs. total sub-tasks for a node. Defensive against missing /
   * malformed `subtasks` so a stale todo payload never throws inside the
   * template's `{{ ... }}` expression.
   */
  subtaskCount(node: TodoNode): { done: number; total: number } {
    const subs = node.subtasks ?? [];
    return {
      done: subs.filter(s => s.status === 'done').length,
      total: subs.length,
    };
  }

  /**
   * Linear-mode only. Toggles which todo row's sub-task checklist is open.
   * Graph mode uses the floating popup, so this signal is unused there.
   */
  toggleSubtaskExpand(node: TodoNode, event: Event): void {
    event.stopPropagation();
    this.expandedSubtaskNodeId.update(id => (id === node.id ? null : node.id));
  }

  /**
   * Sync the shared `newSubtaskText` signal from the underlying input.
   * Bound via `(input)` on every "Add a sub-task…" input — both the
   * inline row (linear mode) and the input inside the graph popup.
   */
  onSubtaskTextInput(event: Event): void {
    const target = event.target as HTMLInputElement;
    this.newSubtaskText.set(target.value);
  }

  /**
   * Toggle a sub-task's done/pending status. Non-optimistic on purpose —
   * we wait for the API response so the backend's reminder + auto-complete
   * logic (none of which is exposed in the UI) becomes the source of truth
   * for the next render. `auto_complete` is hard-coded to `false` so the
   * UI never silently flips parent node state behind the user's back.
   */
  toggleSubtask(node: TodoNode, subtask: SubTask, event: Event): void {
    event.stopPropagation();
    const targetInstanceId = this.instanceId();
    const newStatus: 'pending' | 'done' = subtask.status === 'done' ? 'pending' : 'done';
    this.api.updateSubtask(targetInstanceId, node.id, subtask.id, newStatus, false)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (res) => {
          if (this.instanceId() !== targetInstanceId) return;
          this.sseService.todos.set(this.normalizeTodos(res.todos));
        },
        error: (err) => {
          if (this.instanceId() !== targetInstanceId) return;
          console.error('Failed to toggle subtask:', err);
        },
      });
  }

  /**
   * Add a new sub-task from the current `newSubtaskText`. Clears the
   * buffer on success. Non-optimistic: adopts the server's todo list from
   * the response so ordering and any server-side defaults stick.
   */
  addSubtask(node: TodoNode): void {
    const text = this.newSubtaskText().trim();
    if (!text) return;
    const targetInstanceId = this.instanceId();
    this.api.addSubtask(targetInstanceId, node.id, text)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (res) => {
          if (this.instanceId() !== targetInstanceId) return;
          this.sseService.todos.set(this.normalizeTodos(res.todos));
          this.newSubtaskText.set('');
        },
        error: (err) => {
          if (this.instanceId() !== targetInstanceId) return;
          console.error('Failed to add subtask:', err);
        },
      });
  }

  /**
   * Remove a sub-task. Non-optimistic — same reasoning as toggleSubtask.
   */
  removeSubtask(node: TodoNode, subtask: SubTask, event: Event): void {
    event.stopPropagation();
    const targetInstanceId = this.instanceId();
    this.api.removeSubtask(targetInstanceId, node.id, subtask.id)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (res) => {
          if (this.instanceId() !== targetInstanceId) return;
          this.sseService.todos.set(this.normalizeTodos(res.todos));
        },
        error: (err) => {
          if (this.instanceId() !== targetInstanceId) return;
          console.error('Failed to remove subtask:', err);
        },
      });
  }

  /**
   * Graph-mode entry point for the sub-task popup. Mirrors openCommentPopup
   * — closes any other popup first (mutual exclusion), then anchors the
   * panel to the clicked node with the same right/left/above/below edge-flip
   * math. The sub-task popup may be taller than the comment popup (it has
   * a checklist + input), so we use a slightly larger estimate for the
   * flip calculations; the actual rendered height is auto.
   */
  openSubtaskPopup(node: TodoNode, event: MouseEvent): void {
    event.stopPropagation();
    this.closeAllPopups();

    const pos = this.positionFor(node.id);
    const container = this.graphContainer?.nativeElement;
    if (!pos || !container) return;

    const nodeLeft = pos.x + GRAPH_CONTAINER_PADDING;
    const nodeRight = nodeLeft + NODE_WIDTH;
    const nodeTop = pos.y + GRAPH_CONTAINER_PADDING;
    const nodeCenterY = pos.y + GRAPH_CONTAINER_PADDING + NODE_CENTER_Y_OFFSET;

    const visibleLeft = container.scrollLeft;
    const visibleRight = visibleLeft + container.clientWidth;
    const visibleBottom = container.clientHeight;

    // Estimate for edge-flip math; the actual rendered popup height is
    // auto, so this just affects the direction choices below.
    const SUBTASK_POPUP_HEIGHT = 220;

    const fitsRight = nodeRight + POPUP_GAP + POPUP_WIDTH <= visibleRight;
    const fitsLeft = nodeLeft - POPUP_GAP - POPUP_WIDTH >= visibleLeft;

    let placement: 'right' | 'left' | 'above' | 'below';
    let x: number;
    if (fitsRight) {
      placement = 'right';
      x = nodeRight + POPUP_GAP;
    } else if (fitsLeft) {
      placement = 'left';
      x = nodeLeft - POPUP_GAP - POPUP_WIDTH;
    } else {
      placement = 'below';
      x = nodeLeft + (NODE_WIDTH - POPUP_WIDTH) / 2;
      x = Math.max(visibleLeft + 4, Math.min(x, visibleRight - POPUP_WIDTH - 4));
    }

    let y: number;
    if (placement === 'right' || placement === 'left') {
      y = nodeCenterY - SUBTASK_POPUP_HEIGHT / 2;
      if (y + SUBTASK_POPUP_HEIGHT > visibleBottom) {
        y = nodeTop - POPUP_GAP - SUBTASK_POPUP_HEIGHT;
        if (y < 0) y = Math.max(4, visibleBottom - SUBTASK_POPUP_HEIGHT - 4);
      } else if (y < 0) {
        y = 4;
      }
    } else {
      y = nodeTop + NODE_HEIGHT + POPUP_GAP;
      if (y + SUBTASK_POPUP_HEIGHT > visibleBottom) {
        y = nodeTop - POPUP_GAP - SUBTASK_POPUP_HEIGHT;
        placement = 'above';
        if (y < 0) y = 4;
      }
    }

    this.subtaskPopupNodeId.set(node.id);
    this.subtaskPopupPosition.set({ x, y, placement });
  }
}
