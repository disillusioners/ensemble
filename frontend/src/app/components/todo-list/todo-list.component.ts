import { Component, input, signal, computed, inject, effect, DestroyRef } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { CommonModule } from '@angular/common';
import { MatIconModule } from '@angular/material/icon';
import { ApiService } from '../../services/api.service';
import { SseService, TodoNode, TodoItem } from '../../services/sse.service';

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

  refresh(event: Event): void {
    event.stopPropagation();
    const instanceId = this.instanceId();
    this.api.getTodos(instanceId)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (data) => {
          if (this.instanceId() !== instanceId) return;
          this.sseService.todos.set(data ?? []);
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

  cancelEdit(event?: Event): void {
    if (event) event.stopPropagation();
    this.editingNodeId.set(null);
    this.editingComment.set('');
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
}
