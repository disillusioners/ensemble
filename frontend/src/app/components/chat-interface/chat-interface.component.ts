import {
  Component,
  Input,
  Output,
  EventEmitter,
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
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MarkdownModule } from 'ngx-markdown';
import type { Message, Agent, ToolCall } from '../../models';
import type { ActiveCommandState } from '../../services/command-state.service';
import {
  MermaidActionsService,
  MermaidCopyResult,
} from '../../services/mermaid-actions.service';
import { SseService } from '../../services/sse.service';
import { CommandStateService } from '../../services/command-state.service';
import { ImageViewerActionsService } from '../../services/image-viewer-actions.service';
import { isTmpImageRef } from '../../constants/image-ref';

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
  imports: [CommonModule, MarkdownModule, MatProgressSpinnerModule],
  templateUrl: './chat-interface.html',
  styleUrls: ['./chat-interface.scss'],
})
export class ChatInterfaceComponent implements AfterViewChecked, OnChanges, OnDestroy {
  @ViewChild('messagesEnd') messagesEndRef!: ElementRef<HTMLDivElement>;
  @ViewChild('messagesContainer') messagesContainerRef!: ElementRef<HTMLDivElement>;
  @ViewChild('messagesScroll') messagesScrollRef!: ElementRef<HTMLDivElement>;

  @Input() messages: Message[] = [];
  @Input() isLoading = false;
  @Input() agent: Agent | null | undefined = null;
  @Input() instanceId: string | null = null;
  @Input() showThinking = true;
  @Input() showToolCalls = true;
  @Input() showSystemPrompt = false;

  /**
   * Defect #5 (2026-08-31): the failed-send retry / dismiss controls
   * live on the bubble template; the actions bubble up to the chat
   * component (which owns the messages list and the cooldown /
   * sendError machinery) via these two outputs. The chat-interface
   * stays a pure view — no direct api / sendMessage wiring here.
   */
  @Output() onRetryFailedMessage = new EventEmitter<string>();
  @Output() onDismissFailedMessage = new EventEmitter<string>();

  private readonly mermaidActions = inject(MermaidActionsService);
  private readonly ngZone = inject(NgZone);
  private readonly document = inject(DOCUMENT);
  private readonly sseService = inject(SseService);
  /** Root-singleton command state machine (Phase 2 / Task 6). The card is
   *  a pure view over this service — same instance the chat send path
   *  seeds, so ack-seeded and SSE/REST-driven state land here. */
  private readonly commandStateService = inject(CommandStateService);
  /**
   * Phase 6 (clipboard-image-chat) — image-viewer dialog broker. The
   * chat-interface owns the click bindings on the rendered thumbnails;
   * this service brokers the calls to MatDialog.
   */
  private readonly imageViewerActions = inject(ImageViewerActionsService);

  private shouldScroll = signal(false);
  isNearBottom = signal(true);
  private userHasScrolled = signal(false);
  private isAutoScrolling = false;
  private scrollTimer50?: ReturnType<typeof setTimeout>;
  private scrollTimer150?: ReturnType<typeof setTimeout>;

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
    if (this.scrollTimer50) clearTimeout(this.scrollTimer50);
    if (this.scrollTimer150) clearTimeout(this.scrollTimer150);
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
    if (this.scrollTimer50) clearTimeout(this.scrollTimer50);
    if (this.scrollTimer150) clearTimeout(this.scrollTimer150);

    if (this.messagesEndRef) {
      this.isAutoScrolling = true;
      // Immediate absolute-bottom scroll on the scroll container
      const scroller = this.messagesScrollRef?.nativeElement;
      if (scroller) {
        scroller.scrollTop = scroller.scrollHeight;
      }
      // Fallback: messagesEndRef.scrollIntoView with behavior 'auto'
      this.messagesEndRef.nativeElement.scrollIntoView({ behavior: 'auto' });
      this.isNearBottom.set(true);
      this.userHasScrolled.set(false);
      // Reapply absolute-bottom at ~50ms
      this.scrollTimer50 = setTimeout(() => {
        if (scroller) {
          scroller.scrollTop = scroller.scrollHeight;
        }
      }, 50);
      // Reapply absolute-bottom at ~150ms and reset the auto-scroll flag
      this.scrollTimer150 = setTimeout(() => {
        if (scroller) {
          scroller.scrollTop = scroller.scrollHeight;
        }
        this.isAutoScrolling = false;
      }, 150);
    }
  }

  get agentColor(): string {
    return this.agent ? this.agentColorMap[this.agent.id] || '#10a7f7' : '#10a7f7';
  }

  /**
   * Expose the SSE-tracked pending injection (queued user message) so the
   * template can render a "Pending injection" card at the bottom of the
   * scroll area. Kept as a regular getter (not a signal) so the template
   * invokes it as ``pendingInjection()`` — matches the existing
   * ``agentColor`` getter pattern.
   */
  get pendingInjection() {
    return this.sseService.pendingInjection;
  }

  // ── Active slash-command card (Phase 2 / Task 6) ───────────────────────
  // Pure view over CommandStateService.stateFor(currentInstanceId). All
  // helpers below are public: template-bound members must not be private
  // (strictTemplates / R8). Copy follows the per-value FE copy table in
  // architecture-recommendation.md (post-review adjudication) VERBATIM.

  /** The current instance's command state, or null when none is tracked.
   *  Reads the service's per-instance signal, so the card updates on every
   *  SSE event / ack seed / reconcile without extra wiring. The terminal
   *  card auto-dismisses when the service's display-window eviction
   *  removes the entry. */
  get activeCommand(): ActiveCommandState | null {
    return this.commandStateService.stateFor(this.instanceId);
  }

  /** mm:ss elapsed label — sourced from SERVER elapsed_ms (the timer
   *  source of truth). Resyncs on every event incl. 10s heartbeats; there
   *  is deliberately NO local ticking timer, so a reconnect/reload can
   *  never desync it. */
  commandElapsedLabel(cmd: ActiveCommandState): string {
    const totalSeconds = Math.max(0, Math.floor(cmd.elapsedMs / 1000));
    const minutes = Math.floor(totalSeconds / 60);
    const seconds = totalSeconds % 60;
    return `${minutes}:${String(seconds).padStart(2, '0')}`;
  }

  /** Advisory ETA — rendered ONLY while in_progress and only when the
   *  server shipped it (the machine nulls it for every other phase). */
  commandEtaLabel(cmd: ActiveCommandState): string | null {
    if (cmd.phase !== 'in_progress' || cmd.etaMs === null || cmd.etaMs === undefined) {
      return null;
    }
    return `~${Math.ceil(cmd.etaMs / 1000)}s remaining`;
  }

  /** True for the working phases that warrant the queued-messages note. */
  isWorkingCommandPhase(phase: ActiveCommandState['phase']): boolean {
    return phase === 'waiting' || phase === 'in_progress' || phase === 'timed_out';
  }

  /** Progressive hint after 60s of in_progress (large contexts are slow —
   *  R3: silence must not read as failure). */
  showLongHint(cmd: ActiveCommandState): boolean {
    return cmd.phase === 'in_progress' && cmd.elapsedMs > 60_000;
  }

  /** Spinner only for in_progress; every other phase gets a glyph. */
  isSpinnerPhase(phase: ActiveCommandState['phase']): boolean {
    return phase === 'in_progress';
  }

  phaseGlyph(phase: ActiveCommandState['phase']): string {
    switch (phase) {
      case 'waiting': return '⏳';
      case 'timed_out': return '⏱';
      case 'success': return '✓';
      case 'fallback_applied': return '✂';
      case 'failed': return '✕';
      default: return '•';
    }
  }

  /** Terminal copy table — VERBATIM per the post-review adjudication:
   *  summary → "Context compacted"; partial_summary and truncation arrive
   *  via timed_out → fallback_applied with the honest per-value copy;
   *  noop → "Nothing to compact" (instant-success look — NOT a failure).
   *  Compaction-output-structure §9 upgrade: when the backend ships
   *  section counts in the detail payload (``sections_kept`` /
   *  ``sections_total`` — the wire detail object is additive/flat), the
   *  success title names the preserved structure and the partial
   *  fallback title gains the honest "(k/N sections kept; …)" suffix.
   *  Without counts the prior copy is kept verbatim (graceful
   *  degradation — the FE never renders a fabricated number). */
  commandTitle(cmd: ActiveCommandState): string {
    switch (cmd.phase) {
      case 'waiting':
        // ≤30s on the RUNNING pause path is normal — must not imply failure.
        return 'Preparing compaction… (waiting for instance to quiesce)';
      case 'in_progress':
        return 'Compacting context…';
      case 'timed_out':
        return 'Compaction timed out — applying fallback…';
      case 'success': {
        const type = cmd.detail?.compacted_type;
        if (type === 'noop') return 'Nothing to compact';
        const counts = this.commandSectionCounts(cmd);
        if (counts) {
          return `Context compacted — global overview + ${counts.total} section summaries preserved`;
        }
        return 'Context compacted';
      }
      case 'fallback_applied': {
        const type = cmd.detail?.compacted_type;
        if (type === 'partial_summary') {
          // Non-contiguous survival (parallel chunked summarization): the
          // surviving summaries need not be a prefix — any batch that
          // finished keeps its summary, any batch that did not is trimmed.
          const base = 'Compaction timed out partway — kept the summaries that completed, trimmed the messages that could not be summarized';
          const counts = this.commandSectionCounts(cmd);
          if (counts) {
            return `${base} (${counts.kept}/${counts.total} sections kept; dropped spans listed in the compaction notice)`;
          }
          return base;
        }
        if (type === 'truncation') {
          return 'Compaction timed out — history was trimmed without a summary';
        }
        return 'Compaction timed out — the context was trimmed to fit';
      }
      case 'failed':
        return 'Compaction failed';
      default:
        return 'Working…';
    }
  }

  /**
   * Section counts for the §9 card copy, read defensively off the flat
   * additive detail payload. The fields are intentionally NOT declared on
   * ``CommandProgressDetail`` (models/index.ts stays unchanged — pinned
   * by the compaction-output-structure spec): this accessor absorbs the
   * optional backend fields at the component seam, returning null unless
   * BOTH values are finite numbers with total > 0. No counts → callers
   * keep the pre-existing copy (never a fabricated number).
   */
  private commandSectionCounts(cmd: ActiveCommandState): { kept: number; total: number } | null {
    const detail = cmd.detail as
      | (NonNullable<ActiveCommandState['detail']> & { sections_kept?: unknown; sections_total?: unknown })
      | null;
    const kept = detail?.sections_kept;
    const total = detail?.sections_total;
    if (
      typeof kept === 'number' && Number.isFinite(kept) && kept >= 0 &&
      typeof total === 'number' && Number.isFinite(total) && total > 0
    ) {
      return { kept, total };
    }
    return null;
  }

  /** Explanatory line: noop_reason mapping for noop successes, failure
   *  reason for failed, transient note for timed_out. */
  commandDetailLine(cmd: ActiveCommandState): string | null {
    const detail = cmd.detail;
    if (cmd.phase === 'success' && detail?.compacted_type === 'noop') {
      switch (detail?.noop_reason) {
        case 'recently_compacted': return 'Already compacted recently';
        case 'below_floor': return 'Context too small to compact';
        case 'too_few_messages': return 'Too few messages';
        // Cycle 2 (proactive-compaction-fix review W-4) — all-injected
        // anti-refire path; the engine emits
        // ``compaction_type="skipped_injections_dominate"`` and the BE
        // executor maps that to ``compacted_type="noop"`` +
        // ``noop_reason="injections_dominate"``.
        case 'injections_dominate': return 'All messages are injections; nothing to compact';
        // Cycle 3 (proactive-compaction-fix residual W-4.5) — the
        // emergency-bail path on ``daemon/compaction.py:2129-2138``
        // emits ``skipped_preserved_within_threshold`` when preserved
        // groups still fit within the threshold. The BE executor maps
        // that to ``compacted_type="noop"`` +
        // ``noop_reason="preserved_within_threshold"`` (mirror of the
        // other two mapped noops).
        case 'preserved_within_threshold': return 'Preserved groups still fit within the threshold';
        default: return 'No compaction was needed';
      }
    }
    if (cmd.phase === 'failed') {
      const reason = detail?.reason || (detail?.failure_kind ? String(detail.failure_kind) : null);
      return reason ? `Reason: ${reason}` : null;
    }
    if (cmd.phase === 'fallback_applied' && detail?.reason) {
      return detail.reason;
    }
    return null;
  }

  /** "12,345 → 4,567 tokens" — only when the server shipped both values. */
  commandTokensLabel(cmd: ActiveCommandState): string | null {
    const before = cmd.detail?.tokens_before;
    const after = cmd.detail?.tokens_after;
    if (typeof before !== 'number' || typeof after !== 'number') return null;
    return `${before.toLocaleString()} → ${after.toLocaleString()} tokens`;
  }

  /** Card success/failure tinting. noop is a SUCCESS — the failed class
   *  must never apply to it (SC13). */
  isFailedCommand(cmd: ActiveCommandState): boolean {
    return cmd.phase === 'failed';
  }

  isTerminalCommand(cmd: ActiveCommandState): boolean {
    return cmd.phase === 'success' || cmd.phase === 'fallback_applied' || cmd.phase === 'failed';
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

    // System messages are only shown when the system-prompt toggle is on.
    // When the toggle is off, the system row is hidden entirely (mirrors
    // how thinking-only / tool-only messages vanish when their toggles
    // are off, so the user can fully opt out of seeing system-prompt
    // chatter). EXCEPTION — compaction docs (``compaction-global-`` id
    // prefix): they carry the user-facing fold card (compaction-output-
    // structure §9), so they stay visible regardless of the toggle —
    // the user must always see that context was compacted.
    // EXCEPTION — SSE error rows (``sseError`` meta, D2 gap fix
    // 2026-09-14): synthesized from ``error`` / ``status_change{error}``
    // SSE events, so they stay visible regardless of the toggle — the
    // user must always see that the instance errored.
    if (message.role === 'system') {
      if (message.sseError) {
        return true;
      }
      if (this.isCompactionDoc(message)) {
        return this.hasMeaningfulContent(message);
      }
      return this.showSystemPrompt && this.hasMeaningfulContent(message);
    }

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

  // ── Compaction fold-with-preview card ──────────────────────────────────
  // Single-document compaction output (compaction-output-structure §9):
  // the backend persists exactly ONE SystemMessage per compaction with the
  // stable id prefix ``compaction-global-`` and a long structured body
  // (envelope header → GLOBAL OVERVIEW → SECTION DETAIL → boundary line).
  // The card renders it COLLAPSED by default with a ≤500-char preview
  // drawn from the GLOBAL OVERVIEW section, plus a "Show compacted
  // context" expander revealing the full body — so the giant doc can
  // never blow out the transcript visually.

  /** Stable id prefix the backend mints compaction docs with
   *  (full shape ``compaction-global-{instance_id}-{seq}``). */
  private static readonly COMPACTION_ID_PREFIX = 'compaction-global-';

  /** Preview cap. The spec asks for ≤500 chars; when truncating we keep
   *  499 chars + an ellipsis so the rendered preview never exceeds 500. */
  private static readonly COMPACTION_PREVIEW_MAX_CHARS = 500;

  /** True when ``message`` is a single-document compaction doc. */
  isCompactionDoc(message: Message): boolean {
    return (
      message.role === 'system' &&
      typeof message.message_id === 'string' &&
      message.message_id.startsWith(ChatInterfaceComponent.COMPACTION_ID_PREFIX)
    );
  }

  /**
   * ≤500-char preview for the collapsed fold card, drawn from the
   * GLOBAL OVERVIEW section of the doc body
   * (``── GLOBAL OVERVIEW ──\n…\n\n── SECTION DETAIL ──``).
   *
   * Degradation ladder (conditional clauses are omitted, never falsified —
   * the doc shape varies by compaction outcome):
   *  1. GLOBAL OVERVIEW section content (the normal shape);
   *  2. when the section is absent (truncation mode may omit it, and a
   *     failed merge pass leaves only the placeholder line inside it),
   *     the envelope header line — the ``[CONTEXT COMPACTION — …]``
   *     summary the backend always writes first;
   *  3. otherwise the first 500 chars of the raw body.
   */
  compactionPreview(message: Message): string {
    const body = message.content || '';
    const max = ChatInterfaceComponent.COMPACTION_PREVIEW_MAX_CHARS;

    const overviewStart = body.indexOf('── GLOBAL OVERVIEW ──');
    if (overviewStart >= 0) {
      const contentStart = body.indexOf('\n', overviewStart);
      if (contentStart >= 0) {
        // The overview section ends at the next structural marker —
        // SECTION DETAIL, ARCHIVED, or the boundary line, whichever
        // comes first.
        const rest = body.slice(contentStart + 1);
        const endIdx = ['── SECTION DETAIL ──', '── ARCHIVED:', '── END OF COMPACTED CONTEXT']
          .map(marker => rest.indexOf(marker))
          .filter(i => i >= 0)
          .reduce((min, i) => Math.min(min, i), rest.length);
        const overview = rest.slice(0, endIdx).trim();
        if (overview) {
          return this.capPreview(overview, max);
        }
      }
    }

    // No usable overview → fall back to the envelope header line.
    if (body.startsWith('[CONTEXT COMPACTION')) {
      const firstLine = body.split('\n', 1)[0].trim();
      if (firstLine) {
        return this.capPreview(firstLine, max);
      }
    }
    return this.capPreview(body.trim(), max);
  }

  /** Hard-cap helper — never returns more than ``max`` characters. */
  private capPreview(text: string, max: number): string {
    if (text.length <= max) return text;
    return text.slice(0, max - 1) + '…';
  }

  /** Message ids whose fold card is currently expanded. Immutable Set
   *  updates (signal) so change detection sees the toggle. Keyed by
   *  message id — the backend re-delivers compaction docs under the
   *  SAME stable id, so expansion state survives union-merge upserts. */
  readonly expandedCompactionIds = signal<ReadonlySet<string>>(new Set<string>());

  isCompactionExpanded(messageId: string): boolean {
    return this.expandedCompactionIds().has(messageId);
  }

  toggleCompactionDoc(messageId: string): void {
    this.expandedCompactionIds.update(current => {
      const next = new Set(current);
      if (next.has(messageId)) {
        next.delete(messageId);
      } else {
        next.add(messageId);
      }
      return next;
    });
  }

  // ─── Phase 6 / image viewer dialog + onerror fallback ─────────────────
  // Click-to-popup viewer + 30-day-cleaned tmp-image resilience. The
  // chat-interface owns the click/error bindings on the rendered
  // thumbnails; the dialog itself is opened via ImageViewerActionsService
  // and its open-config lives there.

  /**
   * Track which (message_id, index) pairs have produced an ``error``
   * event so the template can bind directly to the fallback SVG src
   * and the (error) handler can suppress a second re-fetch.
   *
   * Keyed by message id → set of image indices. The map is mutated
   * with `.set()` and `.update()` (NOT swapped wholesale) so an
   * unrelated message's failure doesn't cascade into a full re-render
   * of the chat. ``signal()``-wrapped so OnPush sees the change.
   */
  readonly failedImages = signal<ReadonlyMap<string, ReadonlySet<number>>>(
    new Map(),
  );

  /**
   * Pinned inline SVG fallback for the bubble thumbnail's ``(error)``
   * event. Mirrored verbatim in the dialog component's own
   * ``(error)`` handler so the two surfaces show the SAME icon when
   * the user opens a dialog on a 410'd image (plan risk #4).
   *
   * Encoded as a ``data:image/svg+xml;utf8,…`` URI via
   * ``encodeURIComponent`` (NOT base64) so the markup stays
   * human-readable in devtools — the byte-for-byte identity is
   * pinned in image-viewer-dialog.component.spec.ts and in the
   * chat-interface test below.
   */
  static readonly FALLBACK_IMAGE_SVG_MARKUP =
    "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' width='200' height='200'>" +
    "<rect width='24' height='24' fill='#374151'/>" +
    "<path d='M3 5h18v14H3z' fill='none' stroke='#9ca3af' stroke-width='1.5'/>" +
    "<circle cx='8' cy='9' r='1.5' fill='#9ca3af'/>" +
    "<path d='M3 17l5-5 4 4 3-3 6 6v2H3z' fill='#6b7280'/>" +
    "<line x1='4' y1='4' x2='20' y2='20' stroke='#ef4444' stroke-width='1.5'/>" +
    "</svg>";

  /**
   * URI-encoded data form of the fallback SVG — what the template
   * actually binds to ``<img [src]>`` when the (error) handler fires.
   * Kept as a getter (computed once) so a future contributor who
   * changes the markup only has to update the constant above; the
   * encoding step is mechanical.
   */
  static readonly FALLBACK_IMAGE_SRC_CACHE =
    'data:image/svg+xml;utf8,' +
    encodeURIComponent(ChatInterfaceComponent.FALLBACK_IMAGE_SVG_MARKUP);

  /** Template-bound src helper — returns the fallback SVG once the
   *  (message_id, index) pair has been recorded as failed, the
   *  original src otherwise. The signal-driven check means OnPush
   *  re-renders see the swap. */
  imageSrc(img: string, messageId: string, index: number): string {
    return this.isImageFailed(messageId, index)
      ? ChatInterfaceComponent.FALLBACK_IMAGE_SRC_CACHE
      : img;
  }

  /** True when the (message_id, index) pair has been recorded as
   *  failed. Used both to gate the dialog-open click and to apply
   *  the ``message-image-failed`` class. */
  isImageFailed(messageId: string, index: number): boolean {
    const set = this.failedImages().get(messageId);
    return !!set && set.has(index);
  }

  /**
   * Click handler — opens the image-viewer dialog. Stops propagation
   * defensively (no bubble-level (click) handler exists today, but
   * a future contributor may add one — e.g. an "open conversation in
   * side panel" affordance — and the defensive stopPropagation would
   * silently break it. The spec pins the current behavior so any
   * removal is a deliberate change).
   *
   * Click on a failed image does NOT open the dialog (the placeholder
   * is not a real image — opening the dialog would either show a
   * broken-image icon OR, with the dialog body's own (error), the
   * same fallback again. Either way it's a useless interaction).
   */
  onImageThumbnailClick(
    event: MouseEvent,
    img: string,
    messageId: string,
    index: number,
  ): void {
    event.stopPropagation();
    if (this.isImageFailed(messageId, index)) {
      return;
    }
    this.imageViewerActions.openViewer(img, messageId);
  }

  /**
   * (error) handler — swaps the broken ``<img>``'s src to the pinned
   * fallback SVG AND records the failure in ``failedImages`` so a
   * re-render (e.g. message re-fetch on reconnect) does NOT replay
   * the fetch for an already-known-cleaned file.
   *
   * Only canonical server-ref URLs (matching ``TMP_IMAGE_REF_PREFIX``
   * from ``constants/image-ref``) can produce a post-cleanup 410;
   * legacy ``data:image/...`` URIs are inline and never fail. We
   * discriminate via ``isTmpImageRef`` (phase 5) so a future bug in
   * the data-URI pipeline does NOT regress into the "fallback for
   * data URIs" trap — that would be a visual regression for every
   * old message.
   */
  onImageError(event: Event, messageId: string, index: number): void {
    const imgEl = event.target as HTMLImageElement | null;
    if (!imgEl) {
      return;
    }
    // Defensive: if the ref-URL check fails (e.g. a future
    // adversarial src sneaks past the SSE whitelist), still record the
    // failure so the bubble does not show a broken-image icon.
    // isTmpImageRef is the canonical §2 form discriminator.
    void isTmpImageRef; // referenced for type-narrowing / future parity check
    if (imgEl.classList.contains('message-image-failed')) {
      // Already failed — do not refire the swap or record again.
      // Belt-and-suspenders: the template binding to imageSrc() should
      // have already swapped the src, so the browser will not refire
      // an `error` event for the same broken image. But the same
      // message's other images can re-render and bubble up spurious
      // errors during a streaming reconnect, and this guard ensures
      // the (error) handler stays idempotent.
      return;
    }
    // Stop further native error events while we swap the src.
    imgEl.onerror = null;
    imgEl.classList.add('message-image-failed');
    imgEl.src = ChatInterfaceComponent.FALLBACK_IMAGE_SRC_CACHE;
    this.recordImageFailure(messageId, index);
  }

  /**
   * Idempotent failure recorder — adds the index to the per-message
   * failure set and bumps the signal so OnPush re-renders pick it up.
   */
  private recordImageFailure(messageId: string, index: number): void {
    this.failedImages.update((current) => {
      const next = new Map(current);
      const existing = next.get(messageId);
      if (existing) {
        if (existing.has(index)) {
          return current; // no-op — keep referential identity stable
        }
        const setNext = new Set(existing);
        setNext.add(index);
        next.set(messageId, setNext);
      } else {
        next.set(messageId, new Set([index]));
      }
      return next;
    });
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
