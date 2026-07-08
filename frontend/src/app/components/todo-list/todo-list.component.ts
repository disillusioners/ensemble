import { Component, input, signal, computed, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { MatIconModule } from '@angular/material/icon';
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

  instanceId = input.required<string>();

  // Default expanded so a freshly-rendered todo list is immediately
  // visible without an extra click. Collapsing is purely cosmetic.
  isCollapsed = signal(false);

  /**
   * Filter the global todoLists signal for the entry matching this
   * component's instanceId. Returns an empty array (not undefined) so
   * downstream computed signals can call .filter / .length safely even
   * before any `todo_update` event has been received.
   */
  todos = computed<TodoItem[]>(() => {
    const all = this.sseService.todoLists();
    const found = all.find(t => t.instance_id === this.instanceId());
    return found?.items ?? [];
  });

  /**
   * Hide the entire container when there are no todos — avoids a
   * stranded empty card when the agent has not yet published a todo
   * list (e.g. immediately after a turn boundary).
   */
  isVisible = computed(() => this.todos().length > 0);

  doneCount = computed(() => this.todos().filter(t => t.status === 'done').length);
  totalCount = computed(() => this.todos().length);

  toggle(): void {
    this.isCollapsed.update(v => !v);
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
