import {
  Component,
  Input,
  ViewChild,
  ElementRef,
  AfterViewChecked,
  OnChanges,
  OnDestroy,
  SimpleChanges,
  NgZone,
  inject,
  signal,
  DOCUMENT,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { MarkdownModule } from 'ngx-markdown';
import { Message, Agent, ToolCall } from '../../models';
import {
  MermaidActionsService,
  MermaidCopyResult,
} from '../../services/mermaid-actions.service';

interface MermaidChartContext {
  /** Bubble that owns this chart — used to look up the source message. */
  bubble: HTMLElement;
  /** The `.mermaid` wrapper itself. */
  mermaidEl: HTMLElement;
  /** The rendered SVG element inside the wrapper. */
  svg: SVGElement;
  /** Original Mermaid source for the "copy source" action. */
  source: string;
}

/**
 * Chat surface — renders user / assistant messages, thinking blocks,
 * tool calls, and Markdown bodies (with Mermaid diagram support).
 *
 * After ngx-markdown + Mermaid finish rendering each message, the
 * component scans the chat scroll container for new `.mermaid` divs
 * and injects a small overlay (copy-menu + fullscreen) over each
 * chart. The overlay is added as raw DOM rather than an Angular
 * component because the Markdown output is opaque to Angular's view
 * tree — only the `MermaidActionsService` is "Angular-aware".
 */
@Component({
  selector: 'app-chat-interface',
  standalone: true,
  imports: [CommonModule, MarkdownModule],
  templateUrl: './chat-interface.html',
  styleUrls: ['./chat-interface.scss'],
})
export class ChatInterfaceComponent implements AfterViewChecked, OnChanges, OnDestroy {
  @ViewChild('messagesEnd') messagesEndRef!: ElementRef<HTMLDivElement>;
  @ViewChild('messagesContainer') messagesContainerRef!: ElementRef<HTMLDivElement>;

  @Input() messages: Message[] = [];
  @Input() isLoading = false;
  @Input() agent: Agent | null | undefined = null;
  @Input() instanceId: string | null = null;
  @Input() showThinking = true;
  @Input() showToolCalls = true;

  private readonly mermaidActions = inject(MermaidActionsService);
  private readonly ngZone = inject(NgZone);
  private readonly document = inject(DOCUMENT);

  private shouldScroll = signal(false);
  isNearBottom = signal(true);
  private userHasScrolled = signal(false);
  private isAutoScrolling = false;

  /**
   * Track which chart overlay buttons we've already injected so a
   * MutationObserver re-fire (or a re-render of the bubble) doesn't
   * double up. Keyed by the rendered SVG element identity.
   */
  private readonly injectedCharts = new WeakSet<Element>();
  /**
   * Active transient status pills, indexed by chart bubble, so we can
   * clear them when the bubble unmounts or a new status supersedes.
   */
  private readonly activeStatusByBubble = new WeakMap<HTMLElement, HTMLElement>();
  /** Track which chart's copy-menu is currently open (so we don't double-open). */
  private openMenuChart: HTMLElement | null = null;

  /** Debounce handle for `scanForMermaidCharts` re-runs. */
  private scanHandle: number | null = null;
  /** Observer that watches for new `.mermaid` divs being added. */
  private mutationObserver: MutationObserver | null = null;

  agentColorMap: Record<string, string> = {
    'leader': '#f59e0b',
    'developer': '#10a7f7',
    'coder': '#10a7f7',  // backward compat for cached responses
    'reviewer': '#8b5cf6',
    'charter': '#3b82f6',
  };

  ngOnChanges(changes: SimpleChanges): void {
    const instanceIdChanged = changes['instanceId'];
    const messagesChanged = changes['messages'] && changes['messages'].currentValue?.length !== changes['messages'].previousValue?.length;
    const isLoadingChanged = changes['isLoading'];

    // Reset scroll state when switching instances
    if (instanceIdChanged) {
      this.userHasScrolled.set(false);
      this.isNearBottom.set(true);
      this.shouldScroll.set(true);
    } else if ((messagesChanged || isLoadingChanged) && !this.userHasScrolled()) {
      this.shouldScroll.set(true);
    }

    // While a stream is in flight the message bubble DOM is mutated
    // constantly; scanning for `.mermaid` charts during that window
    // either races the renderer or wastes work. `scanForMermaidCharts`
    // itself short-circuits when `isLoading` is true (see W3 below),
    // but we still need a one-shot scan the moment streaming finishes
    // so the chart overlays are injected as soon as the final SVG
    // markup is in the DOM. Without this the user would have to wait
    // for the next MutationObserver tick — which works in practice
    // for most cases, but breaks if the final chunk arrives in the
    // same frame as the `isLoading=false` flip and the observer
    // debounce hasn't fired yet.
    if (
      isLoadingChanged &&
      isLoadingChanged.previousValue === true &&
      isLoadingChanged.currentValue === false
    ) {
      this.scheduleScan();
    }
  }

  ngAfterViewChecked(): void {
    if (this.shouldScroll()) {
      this.scrollToBottom();
      this.shouldScroll.set(false);
    }

    // Lazily create the MutationObserver once the host view is ready.
    this.ensureMutationObserver();

    // Sweep for any charts the observer might have missed (e.g. those
    // rendered before the observer attached). The sweep is debounced
    // via requestAnimationFrame so we don't run on every CD cycle.
    this.scheduleScan();
  }

  ngOnDestroy(): void {
    // Tear down any open CDK overlays (copy menus) first so their
    // `onDismiss` callbacks fire BEFORE we disconnect the observer
    // and cancel the pending rAF. This lets the service null out
    // its `activeMenuOverlay` and any associated state cleanly.
    this.mermaidActions.closeAll();
    if (this.mutationObserver) {
      this.mutationObserver.disconnect();
      this.mutationObserver = null;
    }
    if (this.scanHandle !== null) {
      cancelAnimationFrame(this.scanHandle);
      this.scanHandle = null;
    }
  }

  onScroll(event: Event): void {
    // Ignore scroll events during auto-scroll animation
    if (this.isAutoScrolling) {
      return;
    }

    const container = event.target as HTMLDivElement;
    const scrollThreshold = 100; // pixels from bottom to consider "near bottom"
    const distanceFromBottom = container.scrollHeight - container.scrollTop - container.clientHeight;

    const nearBottom = distanceFromBottom <= scrollThreshold;
    this.isNearBottom.set(nearBottom);

    // If user scrolls to bottom manually, reset the flag
    if (nearBottom) {
      this.userHasScrolled.set(false);
    } else {
      this.userHasScrolled.set(true);
    }
  }

  scrollToBottom(): void {
    if (this.messagesEndRef) {
      this.isAutoScrolling = true;
      this.messagesEndRef.nativeElement.scrollIntoView({ behavior: 'smooth' });
      this.isNearBottom.set(true);
      this.userHasScrolled.set(false);
      // Reset auto-scrolling flag after animation completes
      setTimeout(() => {
        this.isAutoScrolling = false;
      }, 500);
    }
  }

  get agentColor(): string {
    return this.agent ? this.agentColorMap[this.agent.id] || '#10a7f7' : '#10a7f7';
  }

  formatToolArgs(args: string | Record<string, unknown>): string {
    if (typeof args === 'string') return args;
    try {
      return JSON.stringify(args, null, 2);
    } catch {
      return '[Unable to display]';
    }
  }

  formatToolOutput(output: string | unknown): string {
    if (typeof output === 'string') return output;
    try {
      return JSON.stringify(output, null, 2);
    } catch {
      return '[Unable to display]';
    }
  }

  getFormattedToolCalls(toolCalls: ToolCall[] | undefined) {
    if (!toolCalls) return [];
    return toolCalls.map(tc => ({
      ...tc,
      formattedArgs: this.formatToolArgs(tc.arguments),
      formattedOutput: tc.output ? this.formatToolOutput(tc.output) : null
    }));
  }

  trackByMessageId(index: number, message: Message): string {
    return message.message_id || index.toString();
  }

  formatTime(dateString: string): string {
    return new Date(dateString).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  }

  hasMeaningfulContent(message: Message): boolean {
    const content = message.content;
    // Check if content exists and has non-whitespace characters
    return content != null && content.trim().length > 0;
  }

  /**
   * Check if a message has any visible content to display.
   * Used to determine if the entire message row should be rendered.
   */
  hasVisibleContent(message: Message): boolean {
    // User messages are always shown
    if (message.role === 'user') return true;

    // For assistant messages, check if there's anything to display
    const hasContent = this.hasMeaningfulContent(message);
    const hasThinking = this.showThinking && !!this.getThinkingContent(message);
    const hasToolCalls = this.showToolCalls && !!message.tool_calls && message.tool_calls.length > 0;

    return hasContent || hasThinking || hasToolCalls;
  }

  getThinkingContent(message: Message): string | null {
    // Prioritize thinking (from metadata) over thinking_extracted (from tags)
    if (message.thinking && message.thinking.trim()) {
      return message.thinking;
    }
    if (message.thinking_extracted && message.thinking_extracted.trim()) {
      return message.thinking_extracted;
    }
    return null;
  }

  // ─── Mermaid chart overlay wiring ──────────────────────────────────────

  /**
   * Build the observer once the view is available. Run inside
   * `NgZone.runOutsideAngular` because the observer fires on every
   * Mermaid render — flooding the zone with CD ticks would be
   * wasteful. We re-enter the zone only for the actual injection.
   */
  private ensureMutationObserver(): void {
    if (this.mutationObserver) {
      return;
    }
    const container = this.messagesContainerRef?.nativeElement;
    if (!container) {
      return;
    }
    this.ngZone.runOutsideAngular(() => {
      this.mutationObserver = new MutationObserver(() => {
        // Coalesce through `scheduleScan()` so a burst of mutations
        // during a streaming response collapses into a single rAF
        // callback instead of a synchronous `querySelectorAll` per
        // batch. The actual scan still runs outside the zone — no
        // CD round is triggered either way.
        this.scheduleScan();
      });
      this.mutationObserver.observe(container, {
        childList: true,
        subtree: true,
      });
    });
  }

  /**
   * Coalesce scan calls into at most one per animation frame so the
   * MutationObserver firing in a tight loop can't keep us busy.
   */
  private scheduleScan(): void {
    if (this.scanHandle !== null) {
      return;
    }
    this.scanHandle = requestAnimationFrame(() => {
      this.scanHandle = null;
      this.scanForMermaidCharts();
    });
  }

  /**
   * Walk the messages container and attach an overlay to every
   * rendered Mermaid chart that doesn't already have one.
   *
   * During streaming (`isLoading === true`) the message bubble DOM
   * is mutated continuously as tokens arrive — each mutation can
   * trigger a re-render of in-progress Mermaid blocks. Scanning in
   * that window either races the renderer (injecting an overlay
   * onto a half-built SVG) or wastes work on charts that will be
   * replaced in the next chunk. We short-circuit here and rely on
   * the `isLoading` true→false handler in `ngOnChanges` to schedule
   * a single post-stream scan.
   */
  private scanForMermaidCharts(): void {
    if (this.isLoading) {
      return;
    }
    const container = this.messagesContainerRef?.nativeElement;
    if (!container) {
      return;
    }
    const charts = container.querySelectorAll<SVGElement>('.mermaid > svg');
    charts.forEach((svg) => {
      const mermaidEl = svg.parentElement as HTMLElement | null;
      if (!mermaidEl || !mermaidEl.classList.contains('mermaid')) {
        return;
      }
      if (this.injectedCharts.has(svg)) {
        return;
      }
      const bubble = mermaidEl.closest<HTMLElement>('.message-bubble');
      if (!bubble) {
        // Charts outside a bubble (e.g. debug surfaces) are skipped.
        return;
      }
      const source = this.resolveMermaidSource(bubble, mermaidEl);
      this.injectOverlay({ bubble, mermaidEl, svg, source });
      this.injectedCharts.add(svg);
    });
  }

  /**
   * Extract the original Mermaid source for a given chart.
   *
   * Mermaid swaps the text inside `.mermaid` for an SVG once it
   * renders, so the source text is no longer in the DOM. We recover
   * it by re-parsing the owning message's `content` for fenced
   * ```mermaid blocks and selecting the Nth block, where N matches
   * this chart's position in the bubble's `.mermaid` list.
   *
   * Result is cached on the element's dataset so subsequent lookups
   * are O(1) and so chart re-renders don't re-trigger extraction.
   */
  private resolveMermaidSource(bubble: HTMLElement, mermaidEl: HTMLElement): string {
    const cached = (mermaidEl.dataset?.['mermaidSource'] ?? '').trim();
    if (cached) {
      return cached;
    }
    const messageId = bubble.dataset?.['messageId'] ?? '';
    const message = this.findMessageById(messageId);
    if (!message || !message.content) {
      return '';
    }
    const blocks = extractMermaidBlocks(message.content);
    const chartIndex = this.chartIndexWithinBubble(bubble, mermaidEl);
    const source = blocks[chartIndex] ?? '';
    if (source) {
      mermaidEl.dataset['mermaidSource'] = source;
    }
    return source;
  }

  /**
   * Find the position of `mermaidEl` within its bubble's `.mermaid`
   * siblings. Used to pick the right block out of multiple charts in
   * the same message.
   */
  private chartIndexWithinBubble(bubble: HTMLElement, mermaidEl: HTMLElement): number {
    const mermaids = Array.from(bubble.querySelectorAll<HTMLElement>('.mermaid'));
    return Math.max(0, mermaids.indexOf(mermaidEl));
  }

  private findMessageById(messageId: string): Message | undefined {
    if (!messageId) {
      return undefined;
    }
    return this.messages.find((m) => m.message_id === messageId);
  }

  /**
   * Build and attach the overlay element to a chart.
   *
   * The overlay is a plain `<div>` containing two `<button>` elements
   * (copy-menu trigger + fullscreen). All wiring is done via
   * `addEventListener` with closures that capture the chart context
   * — Angular event bindings can't reach these elements because they
   * live outside the view tree.
   */
  private injectOverlay(ctx: MermaidChartContext): void {
    const overlay = this.document.createElement('div');
    overlay.className = 'mermaid-overlay';

    const copyBtn = this.buildIconButton({
      title: 'Copy chart',
      ariaLabel: 'Open chart copy menu',
      svgPath: ICON_COPY,
      onClick: (event) => this.onCopyMenuClick(event, ctx, copyBtn),
    });
    copyBtn.classList.add('mermaid-overlay-copy');

    const fullscreenBtn = this.buildIconButton({
      title: 'Open chart fullscreen',
      ariaLabel: 'Open chart in fullscreen',
      svgPath: ICON_FULLSCREEN,
      onClick: () => this.onFullscreenClick(ctx),
    });
    fullscreenBtn.classList.add('mermaid-overlay-fullscreen');

    overlay.appendChild(copyBtn);
    overlay.appendChild(fullscreenBtn);
    ctx.mermaidEl.appendChild(overlay);
  }

  /**
   * Build a small icon-only button. The icon is inlined as an SVG
   * path so we don't depend on the Material icon font being loaded
   * for the overlay.
   */
  private buildIconButton(opts: {
    title: string;
    ariaLabel: string;
    svgPath: string;
    onClick: (event: MouseEvent) => void;
  }): HTMLButtonElement {
    const btn = this.document.createElement('button');
    btn.type = 'button';
    btn.className = 'mermaid-overlay-btn';
    btn.title = opts.title;
    btn.setAttribute('aria-label', opts.ariaLabel);
    btn.setAttribute('aria-haspopup', 'menu');

    const svg = this.document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('viewBox', '0 0 24 24');
    svg.setAttribute('fill', 'none');
    svg.setAttribute('stroke', 'currentColor');
    svg.setAttribute('stroke-width', '2');
    svg.setAttribute('stroke-linecap', 'round');
    svg.setAttribute('stroke-linejoin', 'round');
    svg.setAttribute('aria-hidden', 'true');
    const path = this.document.createElementNS('http://www.w3.org/2000/svg', 'path');
    path.setAttribute('d', opts.svgPath);
    svg.appendChild(path);
    btn.appendChild(svg);

    btn.addEventListener('click', (event) => {
      // Stop the click from bubbling to the chart beneath; some
      // Mermaid outputs use <a> nodes that would otherwise navigate.
      event.stopPropagation();
      event.preventDefault();
      // Re-enter the Angular zone so CDK Overlay / MatDialog interactions
      // (e.g. `MermaidActionsService.openCopyMenu`) and any reactive state
      // changes triggered from the handler run inside change detection.
      // The MutationObserver that creates this button runs outside the zone,
      // so without this re-entry, click handlers would never trigger CD.
      this.ngZone.run(() => {
        opts.onClick(event);
      });
    });
    return btn;
  }

  private onCopyMenuClick(
    _event: MouseEvent,
    ctx: MermaidChartContext,
    button: HTMLButtonElement,
  ): void {
    if (this.openMenuChart === ctx.mermaidEl) {
      // A second click on the same trigger lets the CDK overlay
      // close itself (backdrop click); nothing else to do here.
      return;
    }
    // NOTE: `openMenuChart` is assigned AFTER `openCopyMenu` returns.
    // `MermaidActionsService.openCopyMenu` will dispose any active
    // menu first, and that disposal fires the previous menu's
    // `onDismiss` callback — which is `resetOpenMenuSentinel()` —
    // clearing `openMenuChart`. If we set the sentinel here (before
    // the call) the previous menu's dismiss would clear the value we
    // just assigned, leaving the sentinel `null` even though a new
    // menu is now active. Setting it after the call ensures the
    // sentinel ends up pointing at the NEW chart.
    this.mermaidActions.openCopyMenu(
      button,
      {
        svg: this.serializeSvg(ctx.svg),
        source: ctx.source,
        title: 'Mermaid Diagram',
      },
      (result) => this.onCopyResult(ctx, result),
      () => this.resetOpenMenuSentinel(),
    );
    this.openMenuChart = ctx.mermaidEl;
  }

  /**
   * Reset the "open menu" sentinel when the CDK overlay closes via
   * a backdrop click rather than a menu-item selection.
   */
  private resetOpenMenuSentinel(): void {
    if (this.openMenuChart) {
      this.openMenuChart = null;
    }
  }

  private onFullscreenClick(ctx: MermaidChartContext): void {
    this.mermaidActions.openFullscreen({
      svg: this.serializeSvg(ctx.svg),
      source: ctx.source,
      title: 'Mermaid Diagram',
    });
  }

  /**
   * Re-serialize the live SVG so we capture the version the user
   * actually sees (mermaid sometimes rewrites attributes between
   * renders). `OuterHTML` is the simplest faithful representation.
   */
  private serializeSvg(svg: SVGElement): string {
    return svg.outerHTML;
  }

  /**
   * Show a transient inline status pill on the chart so the user
   * gets feedback that a copy action fired. Auto-dismisses.
   */
  private onCopyResult(ctx: MermaidChartContext, result: MermaidCopyResult): void {
    this.resetOpenMenuSentinel();
    this.showChartStatus(ctx, result.message, result.success ? 'info' : 'error');
  }

  private showChartStatus(
    ctx: MermaidChartContext,
    message: string,
    variant: 'info' | 'error',
  ): void {
    if (!message) {
      return;
    }
    // Clear any existing pill for this chart first.
    const prev = this.activeStatusByBubble.get(ctx.mermaidEl);
    if (prev) {
      prev.remove();
    }
    const pill = this.document.createElement('div');
    pill.className = `mermaid-status-pill${variant === 'error' ? ' mermaid-status-error' : ''}`;
    pill.textContent = message;
    ctx.mermaidEl.appendChild(pill);
    this.activeStatusByBubble.set(ctx.mermaidEl, pill);
    setTimeout(() => {
      if (pill.parentNode) {
        pill.remove();
      }
      if (this.activeStatusByBubble.get(ctx.mermaidEl) === pill) {
        this.activeStatusByBubble.delete(ctx.mermaidEl);
      }
    }, 2400);
  }
}

/**
 * Inline SVG path data for the overlay icons. Material's icon font
 * isn't guaranteed to be loaded when the chart is rendered, so we
 * inline the glyphs to keep the overlay self-contained.
 */
const ICON_COPY =
  'M16 1H4a2 2 0 0 0-2 2v14h2V3h12V1zm3 4H8a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h11a2 2 0 0 0 2-2V7a2 2 0 0 0-2-2zm0 16H8V7h11v14z';
const ICON_FULLSCREEN =
  'M4 4h6V2H2v8h2V4zm12 0v6h2V2h-8v2h6zM4 14H2v8h8v-2H4v-6zm14 0v6h-6v2h8v-8h-2z';

const MERMAID_FENCE_RE = /^[ \t]{0,3}```[ \t]*mermaid[ \t]*\r?\n([\s\S]*?)\r?\n?[ \t]{0,3}```/gim;

/**
 * Pull every ```mermaid fenced block out of a markdown string and
 * return their inner text in document order. Tolerant of CRLF and a
 * few leading spaces of indentation.
 */
function extractMermaidBlocks(content: string): string[] {
  if (!content) {
    return [];
  }
  const blocks: string[] = [];
  for (const match of content.matchAll(MERMAID_FENCE_RE)) {
    const body = (match[1] ?? '').replace(/\r\n/g, '\n');
    blocks.push(body);
  }
  return blocks;
}
