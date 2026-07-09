import { Component, input, signal, computed, inject, effect } from '@angular/core';
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
    effect(() => {
      this.instanceId(); // track
      this.isCollapsed.set(false);
    });
  }

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
