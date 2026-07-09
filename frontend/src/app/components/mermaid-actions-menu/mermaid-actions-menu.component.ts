import { ChangeDetectionStrategy, Component, EventEmitter, Output } from '@angular/core';
import { CommonModule } from '@angular/common';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';

/**
 * Dropdown actions panel shown above each rendered Mermaid chart.
 *
 * The component is *not* used with `matMenuTriggerFor` (the trigger
 * buttons live inside the markdown-rendered `.mermaid` DOM, which is
 * outside this component's view tree). Instead it is mounted via a
 * CDK `ComponentPortal` from a floating overlay anchored to the
 * trigger button. The rows use plain `mat-button` styled as menu
 * items (full-width, left-aligned icon + text, hover state, dark
 * theme) rather than `mat-menu-item`, because `mat-menu-item` is
 * designed to live inside a `mat-menu` host and its behaviors
 * degrade when used standalone via a portal.
 *
 * Emits a single `action` event so the parent only needs one listener
 * regardless of which item the user picks.
 */
@Component({
  selector: 'app-mermaid-actions-menu',
  standalone: true,
  imports: [CommonModule, MatIconModule, MatButtonModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div
      class="mermaid-menu-panel"
      role="menu"
      (click)="$event.stopPropagation()"
    >
      <button
        type="button"
        mat-button
        role="menuitem"
        class="mermaid-menu-item"
        (click)="onAction('image')"
      >
        <mat-icon class="mermaid-menu-icon">image</mat-icon>
        <span>Copy as Image</span>
      </button>
      <button
        type="button"
        mat-button
        role="menuitem"
        class="mermaid-menu-item"
        (click)="onAction('source')"
      >
        <mat-icon class="mermaid-menu-icon">code</mat-icon>
        <span>Copy Mermaid Source</span>
      </button>
    </div>
  `,
  styles: [
    `
      :host {
        display: block;
      }

      .mermaid-menu-panel {
        display: inline-flex;
        flex-direction: column;
        min-width: 200px;
        background-color: #1e293b;
        color: #ececf1;
        border: 1px solid #334155;
        border-radius: 8px;
        padding: 0.25rem 0;
        box-shadow: 0 12px 32px rgba(0, 0, 0, 0.45);
        font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
        overflow: hidden;
      }

      // mat-button is not a menu item by default — lay each one out as a
      // full-width row with a left-aligned icon and hover affordance,
      // matching what \`mat-menu-item\` previously provided visually.
      .mermaid-menu-item {
        display: flex !important;
        align-items: center;
        justify-content: flex-start;
        width: 100%;
        min-width: 0;
        padding: 0.5rem 0.75rem !important;
        margin: 0 !important;
        border-radius: 0 !important;
        background: transparent !important;
        color: #ececf1 !important;
        font-size: 0.875rem;
        font-weight: 400;
        text-align: left;
        text-transform: none;
        letter-spacing: normal;
        line-height: 1.25rem;

        &:hover,
        &:focus {
          background-color: rgba(16, 167, 247, 0.12) !important;
          color: #ececf1 !important;
        }

        // Hide the default ripple element from mat-button — it would
        // otherwise render as a square inside the rounded menu panel
        // and look out of place next to the icon.
        .mdc-button__ripple {
          display: none;
        }
      }

      .mermaid-menu-icon {
        color: #94a3b8;
        margin-right: 0.5rem;
        font-size: 1.05rem;
        width: 1.05rem;
        height: 1.05rem;
      }
    `,
  ],
})
export class MermaidActionsMenuComponent {
  /** Discriminated action chosen by the user. */
  @Output() readonly action = new EventEmitter<MermaidMenuAction>();

  protected onAction(action: MermaidMenuAction): void {
    this.action.emit(action);
  }
}

export type MermaidMenuAction = 'image' | 'source';
