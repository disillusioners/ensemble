import {
  ChangeDetectionStrategy,
  Component,
  ElementRef,
  ViewChild,
  effect,
  inject,
  input,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { MarkdownModule } from 'ngx-markdown';
import { MatButtonModule } from '@angular/material/button';
import { MermaidActionsService } from '../../services/mermaid-actions.service';

/**
 * Toolbar action variants emitted by ``MermaidGraphComponent``. Wider than
 * ``MermaidMenuAction`` (which only covers the two CDK-overlay options from
 * chat-interface) because this standalone toolbar also surfaces fullscreen.
 */
export type MermaidGraphAction = 'image' | 'source' | 'fullscreen';

/**
 * Reusable wrapper around `<markdown [data]="..." mermaid>` for
 * non-chat Mermaid diagrams (currently the lineage tree, but kept
 * generic so any future surface — A/B test flow, runbook, … — can
 * reuse it).
 *
 * Responsibilities:
 *
 * 1. Render the supplied Mermaid source via ``ngx-markdown`` so the
 *    same dark-theme Mermaid configuration set up in
 *    ``app.config.ts`` is reused (no second ``MERMAID_OPTIONS``
 *    configuration).
 * 2. Render an action toolbar with a copy-menu trigger so users can
 *    copy SVG / PNG / source the same way they do from chat
 *    messages — wired through ``MermaidActionsService`` so the
 *    clipboard / fullscreen / status pill behaviour is identical.
 * 3. After Mermaid finishes rendering, attach a delegated click
 *    listener to the host element so any rendered SVG ``.node`` can
 *    emit its DOM-id upward via ``nodeClicked``. The SkillLineage
 *    tree component maps that DOM-id back to the underlying skill id.
 *    This is the [S2] workaround for Mermaid's ``click nodeId``
 *    directive (which requires a globally-accessible callback).
 *
 * Layout note: the host ``.mermaid-graph-container`` wraps the
 * markdown output in a horizontally-scrollable region so wide graphs
 * (20+ nodes) do not break the surrounding page layout.
 */
@Component({
  selector: 'app-mermaid-graph',
  standalone: true,
  imports: [CommonModule, MarkdownModule, MatButtonModule],
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './mermaid-graph.component.html',
  styleUrl: './mermaid-graph.component.scss',
})
export class MermaidGraphComponent {
  /** Mermaid source — passed straight to ``<markdown [data] mermaid>``. */
  readonly graphSource = input.required<string>();

  /** Optional toolbar title — left empty for inline graphs. */
  readonly title = input<string>('');

  /** Optional fullscreen dialog title override. */
  readonly fullscreenTitle = input<string>('');

  /** Forwarded node-click — emits the DOM-id of the clicked SVG ``.node``. */
  readonly nodeClicked = input<(domNodeId: string) => void>(() => undefined);

  private readonly mermaidActions = inject(MermaidActionsService);

  /**
   * Reference to the host element so the delegated click listener
   * stays scoped to this graph (multiple instances on the same page
   * don't cross-fire). Set via ``@ViewChild`` in the template.
   */
  @ViewChild('hostEl', { static: true })
  protected hostRef!: ElementRef<HTMLElement>;

  /**
   * Track whether the host-level delegated click listener has been
   * marked as "live" for the *current* rendered SVG. Mermaid
   * re-creates the inner SVG whenever the source string changes, so
   * we have to re-arm the guard. The actual delegation happens via
   * ``(click)="onHostClick($event)"`` in the template — the flag
   * here simply lets us no-op stale click targets that point to a
   * node-id which belongs to the previous render.
   */
  private listenersAttached = false;

  constructor() {
    // Re-arm the listener-flag whenever the source string changes
    // (so a re-render after lineage mutation gets a fresh marker).
    // The actual host-level click delegation stays wired the whole
    // time — it lives one level above the inner SVG so Mermaid
    // re-renders don't orphan it.
    effect(() => {
      this.graphSource();
      this.listenersAttached = false;
    });
  }

  /**
   * Forward a toolbar action to ``MermaidActionsService``. We
   * re-serialize the live SVG so the user copies / full-screens the
   * chart they actually see (Mermaid rewrites attributes between
   * renders, so this is more faithful than re-emitting the original
   * source).
   */
  protected onAction(action: MermaidGraphAction): void {
    const source = this.graphSource();
    if (action === 'fullscreen') {
      this.mermaidActions.openFullscreen({
        svg: this.serializeLiveSvg(),
        source,
        title: this.resolvedFullscreenTitle(),
      });
      return;
    }
    if (action === 'image') {
      void this.mermaidActions.copySvgAsPng(
        this.serializeLiveSvg(),
        this.title() || this.resolvedFullscreenTitle(),
      );
      return;
    }
    // 'source' — copy the original Mermaid source text.
    void this.mermaidActions.copyText(source);
  }

  /**
   * Delegated click handler. Walks up from ``event.target`` to the
   * closest ``.node`` element and, if found, hands the SVG node's
   * DOM-id off to ``nodeClicked``. The DOM-id looks like
   * ``flowchart-node0-12``; the SkillLineage tree component strips
   * the prefix and recovers the underlying skill id.
   *
   * Why delegation (not per-node ``addEventListener``): Mermaid
   * re-creates the inner SVG whenever the source string changes, so
   * any listeners attached directly to old ``.node`` elements would
   * be orphaned the next render. Delegating through the host keeps
   * the listener stable across re-renders.
   *
   * Non-node clicks are ignored so the user can still select text
   * or click the chart background without accidentally navigating.
   */
  protected onHostClick(event: Event): void {
    const target = event.target as Element | null;
    if (!target) {
      return;
    }

    const nodeEl = target.closest<SVGElement>('.node');
    if (!nodeEl || !nodeEl.id) {
      return;
    }

    // Mark as attached once a real node click comes through. The flag
    // is reset by the ``graphSource`` effect above (so a re-render
    // re-arms it). Kept for parity with the chat-interface sentinel
    // pattern that guards against clicks firing on stale DOM during
    // a re-render.
    if (!this.listenersAttached) {
      this.listenersAttached = true;
    }

    const handler = this.nodeClicked();
    if (handler) {
      handler(nodeEl.id);
    }
  }

  /**
   * Re-serialize the rendered SVG so the user can copy the chart
   * they actually see (Mermaid rewrites attributes between renders,
   * so this is more faithful than re-emitting the original source).
   */
  protected serializeLiveSvg(): string {
    const svg = this.hostRef?.nativeElement.querySelector<SVGElement>('.mermaid > svg');
    return svg?.outerHTML ?? '';
  }

  /**
   * Fullscreen-dialog title. Prefer the explicit ``fullscreenTitle``
   * input, fall back to ``title``, then a generic "Diagram".
   */
  private resolvedFullscreenTitle(): string {
    return (
      this.fullscreenTitle().trim() ||
      this.title().trim() ||
      'Diagram'
    );
  }
}