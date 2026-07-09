import { Component, input, signal, computed, inject, effect } from '@angular/core';
import { CommonModule } from '@angular/common';
import { MatIconModule } from '@angular/material/icon';
import { ApiService } from '../../services/api.service';
import { SseService, TodoItem } from '../../services/sse.service';

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

  instanceId = input.required<string>();

  // Default expanded so a freshly-rendered todo list is immediately
  // visible without an extra click. Collapsing is purely cosmetic.
  isCollapsed = signal(false);

  /**
   * Inline comment editor state. `editingIndex` is the index of the
   * todo currently being edited, or null when no editor is open.
   * `editingComment` holds the in-progress text; `isSavingComment`
   * disables the action buttons while the POST is in flight.
   */
  editingIndex = signal<number | null>(null);
  editingComment = signal<string>('');
  isSavingComment = signal(false);

  /**
   * Single-instance passthrough: the SSE service holds one todos signal
   * that is overwritten on every todo_update, so we can read it directly.
   * Always returns an array (never undefined) so downstream computed
   * signals can call .filter / .length safely even before any
   * `todo_update` event has been received.
   */
  todos = computed<TodoItem[]>(() => this.sseService.todos());

  /**
   * Hide the entire container when there are no todos — avoids a
   * stranded empty card when the agent has not yet published a todo
   * list (e.g. immediately after a turn boundary).
   */
  isVisible = computed(() => this.todos().length > 0);

  doneCount = computed(() => this.todos().filter(t => t.status === 'done').length);
  totalCount = computed(() => this.todos().length);

  constructor() {
    // Reset the collapse state whenever the user switches to a different
    // instance, so a freshly-bound todo list starts expanded and visible.
    // Also close any open inline editor so stale drafts don't leak
    // across instance boundaries.
    effect(() => {
      this.instanceId(); // track
      this.isCollapsed.set(false);
      this.cancelEdit();
    });
  }

  toggle(): void {
    this.isCollapsed.update(v => !v);
  }

  /**
   * Pull the latest todo list for the current instance via REST. Uses
   * the same signal the SSE service writes into, so subsequent reads
   * (including the next todo_update event) stay consistent.
   */
  refresh(event: Event): void {
    event.stopPropagation();
    const instanceId = this.instanceId();
    this.api.getTodos(instanceId).subscribe({
      next: (data) => {
        this.sseService.todos.set(data ?? []);
      },
      error: (err) => console.error('Failed to refresh todos:', err),
    });
  }

  /**
   * Open the inline editor for a given todo item, pre-filled with the
   * current comment (empty string if none).
   */
  startEditComment(item: TodoItem, event: Event): void {
    event.stopPropagation();
    this.editingIndex.set(item.index);
    this.editingComment.set(item.comment);
  }

  cancelEdit(event?: Event): void {
    if (event) event.stopPropagation();
    this.editingIndex.set(null);
    this.editingComment.set('');
  }

  /**
   * Two-way binding shim — keeps the editor textarea uncontrolled by
   * Angular forms so we don't need FormsModule for this small affordance.
   */
  onCommentInput(event: Event): void {
    const target = event.target as HTMLTextAreaElement;
    this.editingComment.set(target.value);
  }

  /**
   * Persist the comment, then update the shared todos signal. The
   * backend response is treated as the source of truth when it looks
   * like a TodoItem (has an `index` field); otherwise we patch the
   * local entry with the just-typed comment as a graceful fallback.
   */
  saveComment(item: TodoItem, event: Event): void {
    event.stopPropagation();
    if (this.isSavingComment()) return;
    const instanceId = this.instanceId();
    const comment = this.editingComment();
    this.isSavingComment.set(true);
    this.api.setTodoComment(instanceId, item.index, comment).subscribe({
      next: (updated) => {
        const returnedItem: TodoItem | null =
          updated && typeof updated === 'object' && 'index' in updated
            ? (updated as TodoItem)
            : null;
        this.sseService.todos.update(list =>
          list.map(t => {
            if (t.index !== item.index) return t;
            if (returnedItem) return { ...t, ...returnedItem };
            return { ...t, comment };
          })
        );
        this.isSavingComment.set(false);
        this.cancelEdit();
      },
      error: (err) => {
        console.error('Failed to save todo comment:', err);
        this.isSavingComment.set(false);
      },
    });
  }

  /**
   * Unicode glyph returned into the template for the per-item status
   * indicator. Using plain glyphs (not Material icons) keeps the row
   * height tight and the colour theming is driven entirely by the
   * .in-progress / .complete modifier classes on .status-icon.
   */
  statusIcon(status: string): string {
    switch (status) {
      case 'in_progress': return '◐';
      case 'done': return '●';
      case 'pending':
      default: return '○';
    }
  }
}
