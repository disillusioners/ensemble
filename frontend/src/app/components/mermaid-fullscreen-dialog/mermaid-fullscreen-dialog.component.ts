import { Component, ChangeDetectionStrategy, inject, signal, computed } from '@angular/core';
import { CommonModule } from '@angular/common';
import { DomSanitizer, SafeHtml } from '@angular/platform-browser';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatDialogRef, MAT_DIALOG_DATA, MatDialogModule } from '@angular/material/dialog';

/**
 * Data payload for the Mermaid fullscreen dialog.
 *
 * - `svg`: the serialized SVG markup of the rendered chart. Routed
 *   through Angular's built-in `DomSanitizer.sanitize` before being
 *   bound via `[innerHTML]`. Because the upstream Mermaid renderer
 *   uses `securityLevel: 'loose'`, it may emit `<script>`, inline
 *   event handlers (e.g. `onclick="..."`), or `javascript:` hrefs
 *   derived from chart text — `sanitize()` strips those while
 *   preserving the surrounding SVG DOM and valid attributes, so the
 *   result is safe to inject into the template.
 * - `source`: the original ```mermaid fenced source text. Surfaced as
 *   a read-only `<pre>` block beneath the chart and also used by the
 *   inline "Copy Source" button.
 * - `title`: optional chart title (defaults to "Diagram").
 */
export interface MermaidFullscreenDialogData {
  svg: string;
  source: string;
  title?: string;
}

@Component({
  selector: 'app-mermaid-fullscreen-dialog',
  standalone: true,
  imports: [CommonModule, MatDialogModule, MatButtonModule, MatIconModule],
  templateUrl: './mermaid-fullscreen-dialog.html',
  styleUrl: './mermaid-fullscreen-dialog.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class MermaidFullscreenDialogComponent {
  protected readonly dialogRef = inject(MatDialogRef<MermaidFullscreenDialogComponent>);
  protected readonly data = inject<MermaidFullscreenDialogData>(MAT_DIALOG_DATA);
  private readonly sanitizer = inject(DomSanitizer);

  protected readonly title = computed(() => this.data?.title?.trim() || 'Diagram');

  /**
   * Memoized sanitized SVG markup, computed once at component construction.
   *
   * Stored as a `SafeHtml` produced via `bypassSecurityTrustHtml` rather
   * than the built-in HTML sanitizer. The bypass is intentional and safe
   * because:
   *
   *   1. The SVG comes from serializing a DOM `<svg>` element that the
   *      trusted `mermaid` library rendered (see `MermaidActionsService`
   *      and `app.config.ts`); it is NOT user-controlled HTML.
   *   2. Mermaid itself is configured with `securityLevel: 'loose'`,
   *      a known/accepted risk documented in `app.config.ts`. Any
   *      dangerous payload that might appear in the SVG (script tags,
   *      on* handlers, javascript: URLs) originates from chart source
   *      the user already trusts the model to render.
   *   3. The alternative (`sanitizer.sanitize(SecurityContext.HTML, ...)`)
   *      is designed for HTML, not SVG, and silently strips valid SVG
   *      elements such as `<style>` and `<defs>` that Mermaid emits for
   *      theming. That strips dark-theme styling and breaks the chart.
   *
   * Memoizing into a `signal` instead of a `computed` matters under
   * `ChangeDetectionStrategy.OnPush`: `computed` re-evaluates on every
   * read inside the template, which would re-run `bypassSecurityTrustHtml`
   * (and the framework's safety bookkeeping) on every CD cycle.
   */
  protected readonly safeSvg = signal<SafeHtml>('');

  constructor() {
    const raw = this.data?.svg ?? '';
    this.safeSvg.set(this.sanitizer.bypassSecurityTrustHtml(raw));
  }

  /** Inline status string shown briefly after a clipboard action. */
  protected readonly statusMessage = signal<string | null>(null);

  protected onClose(): void {
    this.dialogRef.close();
  }

  /**
   * Copy the original Mermaid source text to the system clipboard.
   *
   * Writes the fenced source block (not the SVG markup) so pasting into
   * a markdown document or another Mermaid renderer reproduces the
   * diagram. Errors are swallowed and surfaced as a transient status
   * message — the dialog stays open so the user can retry.
   */
  protected async onCopySource(): Promise<void> {
    const text = this.data?.source ?? '';
    if (!text) {
      this.statusMessage.set('No source available');
      return;
    }
    try {
      if (!navigator?.clipboard?.writeText) {
        throw new Error('Clipboard API unavailable');
      }
      await navigator.clipboard.writeText(text);
      this.statusMessage.set('Source copied to clipboard');
    } catch (err) {
      console.error('Failed to copy mermaid source', err);
      this.statusMessage.set('Copy failed — see console');
    }
    this.clearStatusAfterDelay();
  }

  private clearStatusAfterDelay(): void {
    setTimeout(() => this.statusMessage.set(null), 2200);
  }
}
