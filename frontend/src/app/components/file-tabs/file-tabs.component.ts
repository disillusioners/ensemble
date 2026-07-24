import { Component, computed, input, output } from '@angular/core';
import { CommonModule } from '@angular/common';
import { MatIconModule } from '@angular/material/icon';
import { MatButtonModule } from '@angular/material/button';
import { MatTooltipModule } from '@angular/material/tooltip';

import { OpenFileTab } from '../../models/workspace.model';

/**
 * Multi-file tab bar for the workspace editor.
 *
 * Renders one tab per open file with:
 *   - click target: tab name (emits `tabClick`)
 *   - dirty indicator (small dot) when `tab.dirty`
 *   - close (X) button (stops propagation, emits `closeTab`)
 *   - active tab visually highlighted
 *
 * When `openFiles` is empty, the bar renders nothing — the workspace
 * shows its own empty state in the viewer area. The component owns no
 * state: the parent (`WorkspaceComponent`) drives the list and active
 * path through inputs and reacts to the two outputs.
 */
@Component({
  selector: 'app-file-tabs',
  standalone: true,
  imports: [CommonModule, MatIconModule, MatButtonModule, MatTooltipModule],
  template: `
    @if (openFiles().length > 0) {
      <div class="file-tab-bar" role="tablist" aria-label="Open file tabs">
        @for (tab of openFiles(); track tab.path) {
          <button
            type="button"
            role="tab"
            class="file-tab"
            [class.active]="tab.path === activePath()"
            [attr.aria-selected]="tab.path === activePath()"
            [attr.data-testid]="'file-tab-' + tab.path"
            [matTooltip]="tab.path"
            [matTooltipDisabled]="tab.path.length <= 40"
            (click)="onTabClick(tab.path)"
          >
            <span class="file-tab-name">{{ tab.name }}</span>
            @if (tab.dirty) {
              <span
                class="dirty-dot"
                data-testid="tab-dirty-dot"
                aria-label="Unsaved changes"
                title="Unsaved changes"
              ></span>
            }
            <span
              class="close-btn"
              role="button"
              tabindex="0"
              [attr.data-testid]="'file-tab-close-' + tab.path"
              [attr.aria-label]="'Close ' + tab.name"
              (click)="onCloseClick($event, tab.path)"
              (keydown.enter)="onCloseClick($event, tab.path)"
              (keydown.space)="onCloseClick($event, tab.path)"
            >
              <mat-icon>close</mat-icon>
            </span>
          </button>
        }
      </div>
    }
  `,
  styleUrl: './file-tabs.component.scss',
})
export class FileTabsComponent {
  /** Open tabs in display order. Empty array renders nothing. */
  readonly openFiles = input<OpenFileTab[]>([]);

  /** Path of the currently active tab (null when none). */
  readonly activePath = input<string | null>(null);

  /** Emitted when a tab body is clicked. Carries the tab's path. */
  readonly tabClick = output<string>();

  /** Emitted when a tab's close button is clicked. Carries the tab's path. */
  readonly closeTab = output<string>();

  /** Compute whether the active highlight is visible (used for tests). */
  readonly hasActive = computed(() => this.activePath() !== null);

  /**
   * Tab body click → forward path to the parent. The parent decides
   * whether to actually switch (it ignores clicks on the already-active
   * tab naturally via `setActiveFile`, which is a no-op for the active
   * path).
   */
  onTabClick(path: string): void {
    this.tabClick.emit(path);
  }

  /**
   * Close-button click. Stops propagation so the click does NOT also
   * bubble up and re-activate the tab we're trying to close — closing
   * an inactive tab must not silently switch focus to it.
   */
  onCloseClick(event: Event, path: string): void {
    event.stopPropagation();
    event.preventDefault();
    this.closeTab.emit(path);
  }
}
