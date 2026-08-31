/**
 * Chat view component. Owns the visible message list, the optimistic
 * provisional append, the SSE mirror effect, the REST refetch path,
 * and the per-instance guards that gate async completions against
 * stale-instance drift.
 *
 * Size rationale (~1570 lines, single file): this component is the
 * load-bearing hub for the chat surface — it threads the message
 * lifecycle end-to-end (send → provisional → SSE echo → REST refetch
 * → purge on terminal). Natural future split seam: the optimistic-
 * append + SSE-mirror + REST-refetch block (the ``handleOptimisticAppend``
 * extraction and adjacent optimistic-append / SSE logic), once the
 * SSE-mirror contract stabilizes. Out-of-scope for this pass.
 */
import { Component, signal, computed, inject, OnInit, OnDestroy, effect, ViewChild, Signal, input } from '@angular/core';
import { CommonModule } from '@angular/common';
import { Router } from '@angular/router';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatSnackBar, MatSnackBarModule } from '@angular/material/snack-bar';
import { MatTooltipModule } from '@angular/material/tooltip';
import { MatDialog, MatDialogModule } from '@angular/material/dialog';
import { ApiService, extractUnknownCommandError, parseCommandAck } from '../../services/api.service';
import { SseService } from '../../services/sse.service';
import { CommandRegistryService } from '../../services/command-registry.service';
import { CommandStateService } from '../../services/command-state.service';
import {
  mergeMessagesById,
  makeProvisionalMessage,
  evictPendingByAge,
} from '../../services/message-merge.util';
import { TabStateService } from '../../services/tab-state.service';
import { WorkspaceOverlayService } from '../../services/workspace-overlay.service';
import { InstancesViewStateService } from '../../services/instances-view-state.service';
import { InstanceService, sortByCreatedAtDesc } from '../../services/instance.service';
import { ProjectService } from '../../services/project.service';
import { InstanceListComponent } from '../../components/instance-list/instance-list.component';
import { ProjectTabBarComponent } from '../../components/project-tab-bar/project-tab-bar.component';
import { ChatInterfaceComponent } from '../../components/chat-interface/chat-interface.component';
import { MessageInputComponent, MessagePayload } from '../../components/message-input/message-input.component';
import { TodoListComponent } from '../../components/todo-list/todo-list.component';
import { QuestionWizardComponent } from '../../components/question-wizard/question-wizard.component';
import {
  WatchoverDialogComponent,
  WatchoverDialogResult,
} from '../../components/watchover-dialog/watchover-dialog.component';
import type { Agent, InstanceInfo, Message, CommandAck, RejectionReason } from '../../models';

const NEXT_AGENT_STORAGE_KEY = 'ensemble-next-instance-agent';

@Component({
  selector: 'app-chat',
  standalone: true,
  imports: [
    CommonModule,
    MatButtonModule,
    MatIconModule,
    MatProgressSpinnerModule,
    MatSnackBarModule,
    MatTooltipModule,
    MatDialogModule,
    InstanceListComponent,
    ProjectTabBarComponent,
    ChatInterfaceComponent,
    MessageInputComponent,
    TodoListComponent,
    QuestionWizardComponent
  ],
  templateUrl: './chat.html',
  styleUrl: './chat.scss'
})
export class ChatComponent implements OnInit, OnDestroy {
  private readonly router = inject(Router);
  private readonly api = inject(ApiService);
  private readonly sseService = inject(SseService);
  /** Slash-command registry (advisory pre-check) + per-instance command
   *  state machine (Phase 2 / Tasks 4–8). Both are root singletons so the
   *  chat-interface card reads the same machine state the send path seeds. */
  private readonly commandRegistry = inject(CommandRegistryService);
  protected readonly commandState = inject(CommandStateService);
  protected readonly tabStateService = inject(TabStateService);
  protected readonly instanceService = inject(InstanceService);
  private readonly projectService = inject(ProjectService);
  private readonly snackBar = inject(MatSnackBar);
  private readonly dialog = inject(MatDialog);
  /**
   * Workspace overlay state is owned by the root-provided singleton
   * service so the overlay element itself can live at the App root
   * (where the Alt+` global hotkey is bound) and survive route changes.
   * Exposed to the template so the chat-header button's active-state
   * class can bind directly to the service's signals.
   */
  protected readonly workspaceOverlayService = inject(WorkspaceOverlayService);
  /**
   * Root-provided singleton owning the active instance id, project
   * context, and visibility of the detail overlay. The host element
   * (mounted at App root) writes ``visible``; the chat component reads
   * ``activeInstanceId`` as the authoritative source of the current
   * instance id — replacing the previous ``ActivatedRoute.params``
   * subscription, which only worked when the component was reached
   * via the router. When the instance id changes (deep-link, instance
   * switch from the sidebar, cached restore), the service signals
   * drive the same load logic the route subscription used to.
   */
  private readonly viewState = inject(InstancesViewStateService);
  /**
   * Visibility flag bound by the App root host. Hidden =>
   * disconnect SSE + clearEvents + stop polling so the cached overlay
   * consumes no resources while the user is on another route. Visible
   * => reconnect SSE + REST catch-up refetch so the chat resumes from
   * the latest server state. Component-local state (scroll position,
   * input drafts, expanded todo nodes) survives hide/show cycles
   * because the rendered subtree is keyed off ``renderedInstance``
   * (the capture-and-hold signal), which is deliberately NOT
   * reactive to this ``visible`` input — only explicit teardown
   * paths (id clear, confirmed 404, id switch) release the hold.
   */
  readonly visible = input<boolean>(true);

  /**
   * Tracks whether the overlay is currently hidden. Synchronized via
   * the visibility effect — true while hidden, false while visible.
   * Used to distinguish the hidden->visible transition (which must
   * reconnect SSE + REST refetch) from a steady visible state.
   */
  private isHiddenNow = false;

  /**
   * Last instance id the chat component actually loaded via
   * ``handleInstanceIdChange``. Used by the visibility+active-id
   * effect to decide whether the current tick needs to load (new
   * instance) or catch-up refetch (same instance, re-emerging from
   * hidden). The cache lives only on the component instance — when
   * the component is destroyed, the tracker is reset.
   */
  private lastLoadedInstanceId: string | null = null;

  protected get projectId(): string {
    return this.tabStateService.activeProjectId() ?? 'all';
  }

  /**
   * True when the active tab is a real project (not the 'all' pseudo-project).
   * The Workspace link points to /projects/{id}/workspace which 404s for 'all'
   * or null, so the button must be hidden in those cases.
   */
  protected get hasRealProject(): boolean {
    const activeId = this.tabStateService.activeProjectId();
    return activeId !== null && activeId !== 'all';
  }

  /** Front-end cooldown (ms) between consecutive message sends, preventing
   *  duplicate submissions from double Enter / double click. */
  private readonly SEND_COOLDOWN_MS = 3000;
  private lastSendTime = 0;

  /**
   * Wall-clock TTL for provisional pending messages (message-display-latency
   * §4.3 item 11). Multi-minute agent turns are normal, so the bar is set
   * to 10 minutes — short enough to eventually clear stuck entries, long
   * enough that a legitimate in-flight turn doesn't lose its bubble.
   * Eviction runs lazily on every merge / refetch (no background timer).
   */
  private readonly PENDING_TTL_MS = 10 * 60 * 1000;

  readonly agents = signal<Agent[]>([]);
  readonly currentInstanceId = signal<string | null>(null);
  readonly currentInstance: Signal<InstanceInfo | null> = computed(() => {
    const id = this.currentInstanceId();
    if (!id) return null;
    return this.instanceService.instances().find(i => i.instance_id === id) ?? null;
  });
  readonly messages = signal<Message[]>([]);
  readonly selectedAgent = signal<Agent | null>(null);
  /**
   * Capture-and-hold of the last-known ``InstanceInfo`` used for
   * RENDERING the chat subtree (BUG 1/2 fix).
   *
   * ``currentInstance`` is a computed over ``instanceService.instances()``
   * and transiently nulls whenever that list is wiped — most notably on
   * every re-show (the visibility effect restarts polling, and
   * ``startPolling`` clears the list before the refetch lands) and on
   * project-scope switches. Keying the template's ``@if`` guards on the
   * computed destroyed ``app-chat-interface`` / ``app-message-input`` for
   * that window, killing their component-local state (scroll position,
   * the in-progress draft, expanded todo nodes) — the Plan↔Instances
   * round-trip draft/scroll reset.
   *
   * Hold semantics (enforced by the constructor effect below):
   *   - live instance resolvable → capture/refresh the hold;
   *   - transiently unresolvable for the SAME id → KEEP the hold
   *     (list wipe mid-refetch — the subtree must survive);
   *   - id moved A→B but B not resolvable yet → release the hold
   *     (never render A's subtree under B's header while B loads);
   *   - id cleared (terminate / new-instance) or confirmed 404
   *     (``instanceNotFound`` set) → release the hold so the
   *     not-found panel / empty state renders.
   */
  readonly renderedInstance = signal<InstanceInfo | null>(null);
  /** Phase 3: tag picked in the AgentSwitcher dropdown. Persisted across
   *  navigation in the page session so the next createInstance can
   *  forward it. Reset when the user picks a different agent. */
  readonly selectedVersionTag = signal<string | null>(null);
  readonly isSending = signal(false);
  readonly sendError = signal<string | null>(null);
  readonly instanceNotFound = signal<string | null>(null);
  /**
   * Tracks the most recently queued user message (the backend accepted the
   * message but did not dispatch it to the agent). The "Message queued"
   * indicator is guarded by this signal; the stored content is shown as a
   * truncated snippet so the user can tell at a glance WHICH message is
   * waiting. Cleared when:
   *   - the next sendMessage response is NOT queued (immediate dispatch),
   *   - an assistant message / thinking event arrives for this instance,
   *   - a status_change → running event arrives for this instance,
   *   - the user switches to a different instance.
   */
  readonly queuedMessage = signal<{ content: string } | null>(null);

  /**
   * Truncated snippet of the queued message content (max 50 chars + "...").
   * Drives the inline indicator label so users can see WHICH message is
   * queued without flooding the UI with full-length input text.
   */
  readonly queuedSnippet = computed(() => {
    const msg = this.queuedMessage();
    if (!msg) return '';
    return msg.content.length > 50 ? msg.content.slice(0, 50) + '...' : msg.content;
  });

  /**
   * Context-usage snapshot for the currently open instance. Mirrors the
   * sseService signal so the header pill updates reactively. Null when
   * the backend hasn't sent a snapshot yet (e.g. instance has zero
   * messages, or SSE hasn't connected).
   */
  readonly contextUsage = computed(() => this.sseService.contextUsage());

  /**
   * Formatted tooltip "X / Y tokens (model)" for the header pill.
   * Re-rendered whenever the snapshot changes thanks to signal binding.
   */
  contextUsageTooltip(usage: { tokens: number; context_window: number; model_name: string }): string {
    const tokens = usage.tokens.toLocaleString();
    const window = usage.context_window.toLocaleString();
    const model = usage.model_name || 'model';
    return `${tokens} / ${window} tokens (${model})`;
  }

  /**
   * Resolve the canonical polling project scope used by BOTH
   * ``tabEffect`` and the visibility+active-id effect (R2 / S2). The
   * previous setup read ``tabStateService.activeProjectId()`` in one
   * place and ``viewState.activeProjectId()`` in another — those two
   * could disagree (tab = null, viewState = cached project), so the
   * two effects raced over ``InstanceService.startPolling`` with
   * last-writer-wins. The canonical resolution lives here so any
   * caller asking "what should polling be scoped to?" gets the same
   * answer.
   *
   * Rule:
   *   - Real project ids (anything except ``null`` and the ``'all'``
   *     pseudo-project) are returned as-is.
   *   - ``'all'`` and ``null`` both resolve to ``undefined`` so the
   *     backend sees a missing filter and returns every instance.
   *     The old routed chat code used the same ``?? undefined``
   *     translation; we keep that contract so swapping the project
   *     tab to "All" doesn't wipe the sidebar by sending a literal
   *     ``'all'`` to the API (which matches nothing).
   */
  protected pollingScope(): string | undefined {
    const pid = this.tabStateService.activeProjectId();
    if (pid && pid !== 'all') return pid;
    return undefined;
  }

  // The tab→polling sync. Gated on `visible` (R1) so hidden-overlay
  // states (default boot, hide→show transitions, notification-bell
  // clicks while hidden) never start a 60s polling ticker. The
  // visibility effect below already restarts polling on the
  // hidden→visible transition, so this gate is the only place that
  // must NOT drive polling on its own.
  //
  // CRITICAL: `this.visible()` MUST be read unconditionally at the
  // top (not inside the bail) so Angular's reactive graph keeps it
  // as a dependency — otherwise a hide→show cycle would not retrigger
  // the effect to start polling.
  private tabEffect = effect(() => {
    const visible = this.visible();   // always read → always tracked
    if (!visible) return;
    this.instanceService.startPolling(this.pollingScope());
  });

  /**
   * Sync workspace overlay state with the active tab.
   *
   * - Switching to the "All" tab (null) hides the workspace and clears
   *   workspaceProjectId.
   * - Switching between project tabs while the workspace is OPEN follows
   *   the workspace to the new project (updates workspaceProjectId only).
   * - Switching tabs while the workspace is CLOSED does NOT auto-open it.
   *
   * Uses `allowSignalWrites: true` because the effect writes back to
   * the workspace overlay service's signals (the canonical state holder),
   * mirroring the pattern used by other write-emitting effects in this
   * component.
   *
   * CRITICAL: the overlay service's `showWorkspace()` and
   * `workspaceProjectId()` MUST be read unconditionally at the top of
   * the effect (not inside the non-null branch) so Angular's reactive
   * graph keeps them as dependencies across every run. If we
   * conditionally read them, then a run that hits the `projectId ===
   * null` branch will drop those deps, and subsequent mutations to
   * those signals will not retrigger the effect.
   */
  private tabWorkspaceEffect = effect(() => {
    const projectId = this.tabStateService.activeProjectId();
    const isOpen = this.workspaceOverlayService.showWorkspace();         // always read → always tracked
    const currentId = this.workspaceOverlayService.workspaceProjectId(); // always read → always tracked

    if (projectId === null) {
      if (isOpen)    this.workspaceOverlayService.hide();
      if (currentId) this.workspaceOverlayService.workspaceProjectId.set(null);
      return;
    }

    if (isOpen && currentId !== projectId) {
      this.workspaceOverlayService.workspaceProjectId.set(projectId);
    }
  }, { allowSignalWrites: true });

  // LocalStorage preferences
  readonly showThinking = signal(localStorage.getItem('ensemble-show-thinking') === 'true');
  readonly showToolCalls = signal(localStorage.getItem('ensemble-show-toolcalls') === 'true');
  readonly showSystemPrompt = signal(localStorage.getItem('ensemble-show-system-prompt') === 'true');

  // Watchover: per-instance security monitoring toggle.
  // When ON, a watcher agent evaluates every tool call and can deny
  // destructive operations. The toggle state is sourced from the API
  // (InstanceInfo.watchover_enabled) on instance load and synced via SSE.
  readonly watchoverEnabled = signal(false);
  readonly watchoverContext = signal<string | null>(null);
  readonly watchoverDenialCount = signal(0);

  // True while a watchover toggle API call is in-flight. Disables the
  // toggle button to prevent overlapping lifecycle requests.
  readonly watchoverPending = signal(false);

  // Auto-sync watchover state from instance data (polling refreshes).
  private watchoverSyncEffect = effect(() => {
    const instance = this.renderedInstance();
    if (!instance) return;
    this.syncWatchoverState(instance);
  }, { allowSignalWrites: true });

  private readonly processedWatchoverDenials = new Set<string>();
  private readonly processedWatchoverStatusEvents = new WeakSet<object>();

  @ViewChild(MessageInputComponent) messageInputRef!: MessageInputComponent;

  // Computed instance agent
  readonly instanceAgent = computed(() => {
    // Derives from the RENDER hold, not the raw ``currentInstance``
    // computed: during a re-show the polled list transiently wipes,
    // and the agent chip / [agent] inputs must not flicker to null
    // alongside it (BUG 1/2 — same capture-and-hold rationale).
    const instance = this.renderedInstance();
    if (!instance) return null;
    return this.agents().find(a => instance.agent_id.includes(a.id)) || null;
  });

  constructor() {
    // Wire the CommandStateService GET-fallback seam to the API layer
    // (Phase 2 / Task 8). The service itself stays dependency-free so the
    // logic-mirror specs can instantiate it without TestBed.
    this.commandState.wireFetch(instanceId => this.api.getActiveCommand(instanceId));

    // Effect to persist showThinking preference
    effect(() => {
      localStorage.setItem('ensemble-show-thinking', String(this.showThinking()));
    });

    // Effect to persist showToolCalls preference
    effect(() => {
      localStorage.setItem('ensemble-show-toolcalls', String(this.showToolCalls()));
    });

    // Effect to persist showSystemPrompt preference
    effect(() => {
      localStorage.setItem('ensemble-show-system-prompt', String(this.showSystemPrompt()));
    });

    // SSE messages drive the message list. Re-merge on every signal change
    // so in-place mutations (e.g. tool_result patching a tool_calls[i].output)
    // are reflected in the UI. The merge is idempotent (upsert by message_id),
    // so duplicate SSE deliveries (e.g. replay on reconnect) collapse naturally.
    //
    // W2 staleness guard: only merge messages that belong to the active
    // instance. SSE messages carry an ``instance_id``; if it disagrees
    // with the currently-open instance (race: user switched tabs while
    // an SSE channel was still resolving) drop the message so it cannot
    // bleed into the new instance's UI.
    //
    // Merge contract (message-display-latency §4.3 item 9): union-by-id
    // upsert via ``mergeMessagesById`` (centralized in
    // ``message-merge.util`` so the REST refetch path uses the exact
    // same logic). The merge is idempotent so duplicate SSE deliveries
    // (POST-time echo + drain-time re-emit on the same id) collapse
    // onto the single provisional bubble without producing a duplicate.
    //
    // Lazy TTL eviction runs on every merge so we don't need a
    // background timer — pending entries that have aged past 10 minutes
    // get dropped whenever any other signal touches the list.
    effect(() => {
      const sseMessages = this.sseService.messages();
      if (sseMessages.length === 0) return;
      const activeInstanceId = this.viewState.activeInstanceId();
      if (!activeInstanceId) return;
      const filtered = sseMessages.filter(
        m => !m.instance_id || m.instance_id === activeInstanceId,
      );
      if (filtered.length === 0) return;

      // Merge: upsert SSE messages into existing list using the shared
      // utility. Top-level fields from SSE win on conflict; the
      // ``pending`` flag is cleared when the incoming copy is not itself
      // pending (server-side copies are never provisional). Local-only
      // entries are preserved verbatim.
      this.messages.update(existing =>
        evictPendingByAge(
          mergeMessagesById(existing, filtered),
          this.PENDING_TTL_MS,
          Date.now(),
        )
      );
      this.isSending.set(false);
    }, { allowSignalWrites: true });

    // Reconnect catch-up (message-display-latency §4.3 item 10): the SSE
    // service bumps ``refetchRequest`` whenever a connection-level error
    // is followed by a fresh ``connected`` event. Here we react by
    // running a merge-mode REST refetch against the active instance —
    // the union-by-id merge inside ``loadInstanceMessages`` is idempotent
    // so a second refetch right after a successful reconnect is a no-op
    // for messages the SSE channel already delivered, but it picks up
    // anything LiveEventHub dropped (zero connections / QueueFull) while
    // the channel was down.
    effect(() => {
      // Read the trigger counter so the effect re-runs on each bump.
      const tick = this.sseService.refetchRequest();
      if (tick === 0) return;
      const activeInstanceId = this.viewState.activeInstanceId();
      if (!activeInstanceId) return;
      this.loadInstanceMessages(activeInstanceId, { merge: true });
    }, { allowSignalWrites: true });

    // Terminal-status pending purge (message-display-latency §4.3 item 11
    // second half): when the instance transitions to a terminal status,
    // any provisional pending entries cannot possibly resolve — drop
    // them. The trigger is bumped by the SSE ``status_change`` listener
    // (terminal statuses only); the actual mutation happens here because
    // the chat component owns the visible ``messages`` signal.
    //
    // MIN-3: the purge must fire only for the instance the user is
    // VIEWING. The SSE-side listener already drops terminal events for
    // cascade children that land on this channel; this second guard
    // covers the instance-switch race — a trigger recorded for the
    // previously-open instance must not wipe the newly-opened
    // instance's provisional bubbles.
    effect(() => {
      const tick = this.sseService.pendingPurgeRequest();
      if (tick === 0) return;
      const purgeInstanceId = this.sseService.pendingPurgeInstanceId();
      const activeInstanceId = this.viewState.activeInstanceId();
      if (!purgeInstanceId || !activeInstanceId) return;
      if (purgeInstanceId !== activeInstanceId) return;
      this.messages.update(existing => {
        const filtered = existing.filter(m => !m.pending);
        return filtered.length === existing.length ? existing : filtered;
      });
    }, { allowSignalWrites: true });

    // ── Slash-command effects (Phase 2 / Tasks 3+4+7+8) ────────────────

    // Feed SSE command_progress events into the state machine. The SSE
    // listener already applied the per-channel staleness guard; this
    // second guard (R2 layer 2) re-checks against the ACTIVE instance so
    // an event that raced an instance switch is never applied to the new
    // instance's machine state. The per-instance map (R2 layer 3) makes
    // any residual race cosmetic-only.
    effect(() => {
      const event = this.sseService.commandProgress();
      if (!event) return;
      const activeInstanceId = this.viewState.activeInstanceId();
      if (!activeInstanceId || event.instance_id !== activeInstanceId) return;
      this.commandState.onSseEvent(event);
    }, { allowSignalWrites: true });

    // REST fallback polling (Task 8): poll GET /commands/active at ~5s
    // ONLY while the card is active AND SSE is dead. The service owns the
    // timer; this effect just feeds it the two inputs it cannot see
    // (active instance + SSE liveness). The service re-evaluates its own
    // start/stop conditions after every state mutation, so poll stops on
    // terminal phase or {exists:false} without further wiring here.
    effect(() => {
      const activeInstanceId = this.viewState.activeInstanceId();
      const sseAlive = this.sseService.isStreaming();
      this.commandState.syncPolling(activeInstanceId, sseAlive);
    }, { allowSignalWrites: true });

    // Terminal-command refetch (Task 7): when a command reaches a terminal
    // phase (success / fallback_applied / failed), refetch the message
    // list so the timeline reflects the compacted context. Fires EXACTLY
    // once per command (service-side refetchTriggered flag). The instance
    // guard keeps a trigger recorded for the previously-open instance
    // from applying to the newly-opened one (SC8 stale-switch safety).
    // context_usage token-meter refresh needs no wiring here — the
    // backend re-emits the SSE signal post-compaction.
    effect(() => {
      const tick = this.commandState.refetchRequest();
      if (tick === 0) return;
      const refetchInstanceId = this.commandState.refetchInstanceId();
      const activeInstanceId = this.viewState.activeInstanceId();
      if (!refetchInstanceId || !activeInstanceId) return;
      if (refetchInstanceId !== activeInstanceId) return;
      this.loadInstanceMessages(activeInstanceId, { merge: true });
    }, { allowSignalWrites: true });

    // Fallback: Reset isSending if streaming stopped but isSending is still true
    effect(() => {
      const streaming = this.sseService.isStreaming();
      const sending = this.isSending();
      if (!streaming && sending) {
        console.log('[Chat] Fallback: Streaming stopped, resetting isSending');
        this.isSending.set(false);
      }
    }, { allowSignalWrites: true });
    
    // Handle SSE errors
    effect(() => {
      const latestError = this.sseService.latestError();
      const currentInstance = this.currentInstance();
      if (latestError && currentInstance && latestError.instance_id === currentInstance?.instance_id) {
        console.error('[Chat] SSE error:', latestError);
        this.isSending.set(false);
        this.sseService.latestError.set(null);
      }
    }, { allowSignalWrites: true });

    // Clear the queued indicator when an assistant message arrives for the
    // current instance. Both `thinking` and `assistant_message` SSE events
    // upsert messages into `sseService.messages()` (mapped to role 'assistant'
    // with a populated `thinking` field for the former), so watching the
    // message list covers both cases without subscribing to a new SSE channel.
    effect(() => {
      if (this.queuedMessage() === null) return;
      const messages = this.sseService.messages();
      if (messages.length === 0) return;
      const currentInstance = this.currentInstance();
      if (!currentInstance) return;
      const last = messages[messages.length - 1];
      if (!last || last.role !== 'assistant') return;
      // Defensive: if the message carries an instance_id it must match.
      if (last.instance_id && last.instance_id !== currentInstance.instance_id) return;
      this.queuedMessage.set(null);
    }, { allowSignalWrites: true });

    // Defensive: clear the queued indicator when the instance transitions
    // to a running/processing state. The existing `sseService.statusChange`
    // signal is the same source the instance list uses, so we don't open a
    // new SSE subscription.
    effect(() => {
      if (this.queuedMessage() === null) return;
      const statusChange = this.sseService.statusChange();
      if (!statusChange) return;
      const currentInstance = this.currentInstance();
      if (!currentInstance || statusChange.instance_id !== currentInstance.instance_id) return;
      const status = statusChange.status;
      if (status === 'running' || status === 'processing') {
        this.queuedMessage.set(null);
      }
    }, { allowSignalWrites: true });

    // Handle watchover SSE status events. The backend emits status_change
    // with special status values for watchover activation/deactivation/failure.
    // InstanceService consumes statusChange eagerly, so the retained raw event
    // is also checked to ensure these special statuses cannot be missed.
    effect(() => {
      const statusChange = this.sseService.statusChange();
      const events = this.sseService.events();
      this.handleWatchoverStatus(statusChange, false);

      const lastEvent = events[events.length - 1];
      if (lastEvent?.type === 'status_change') {
        this.handleWatchoverStatus(lastEvent.data, true);
      }
    }, { allowSignalWrites: true });

    // Handle watchover denials. ToolMessage results are normally represented
    // by SseService.messages; the raw event fallback covers tool_result payloads
    // that are patched onto their matching tool call instead of being upserted.
    effect(() => {
      const messages = this.sseService.messages();
      const events = this.sseService.events();
      const lastMessage = messages[messages.length - 1];
      this.handleWatchoverDenial(lastMessage);

      const lastEvent = events[events.length - 1];
      if (lastEvent?.type === 'tool_call' || lastEvent?.type === 'tool_result') {
        this.handleWatchoverDenial(lastEvent.data['message']);
      }
    }, { allowSignalWrites: true });

    // R6 lazy dead-id validation (companion to clearInstance on
    // termination): a cached ``activeInstanceId`` may point at an
    // instance that was deleted server-side (another tab, backend
    // cleanup, crash). When a FULL instances list loads and does not
    // contain the cached id, drop the cache so the next "Instances"
    // nav-link click never restores a dead id. The service docblock
    // already documents this contract — this effect is the missing
    // implementation.
    //
    // CRITICAL: only validate against FULL lists. When polling is
    // project-scoped (``pollingScope()`` returns a real project id)
    // the list is a subset, and an id from another project is
    // legitimately absent — clearing it would wipe a valid cache.
    // An empty list is also skipped: a transient fetch failure or a
    // scope with no instances yet must not be read as "id is dead".
    //
    // TOCTOU guard (N1 fix): a freshly created / deep-linked instance
    // is NOT in the polled list until ``handleInstanceIdChange``'s
    // getInstance fallback resolves and adds it (chat.component.ts adds
    // the row on success). Clearing on mere absence would kill the
    // just-opened detail view mid-load. The authoritative gate is the
    // API's own 404 confirmation (``instanceNotFound() === cachedId``)
    // — transient errors (500/503/network) intentionally do NOT set
    // ``instanceNotFound`` (N1b), so a flaky network during restore
    // can't wipe a valid cache. The previous "component already moved
    // off" clause (currentInstanceId() !== cachedId) is removed: it
    // raced the visibility effect on activeInstanceId.set(B), firing
    // first with a stale currentInstanceId and wiping a freshly
    // opened instance before any load started.
    //
    // Signal-read discipline (mirrors tabEffect): ``visible()`` and
    // ``activeInstanceId()`` are read unconditionally at the top so
    // they stay tracked even on the bail paths.
    effect(() => {
      const visible = this.visible();                          // always read → always tracked
      const cachedId = this.viewState.activeInstanceId();      // always read → always tracked
      const instances = this.instanceService.instances();

      if (!visible || !cachedId) return;
      if (this.pollingScope() !== undefined) return;  // scoped list — cannot validate
      if (instances.length === 0) return;             // no data yet — nothing to prove

      const stillExists = instances.some(i => i.instance_id === cachedId);
      if (stillExists) return;
      // N1: confirmed-dead gate is the API's own 404 only. Transient
      // errors must NOT clear (see N1b in the getInstance error path).
      if (this.instanceNotFound() === cachedId) {
        this.viewState.clearInstance(cachedId);
      }
    }, { allowSignalWrites: true });

    // Visibility + active-id watcher. The chat component is mounted at
    // the App root and stays alive across route changes, so it has to
    // react to BOTH:
    //   - the active instance id (deep-link, sidebar click, restored cache)
    //   - the visibility flag (visible=true ⇒ show, visible=false ⇒ hide)
    //
    // Behavior matrix:
    //   visible→false: disconnect SSE + clearEvents + stop polling.
    //     Repeat hides are no-ops (track via `isHiddenNow`).
    //   visible→true, new instance id (`activeId !== lastLoadedId`):
    //     REST refetch + SSE reconnect. `handleInstanceIdChange` runs
    //     and seeds the message list, the SSE channel, and the watchover
    //     state.
    //   visible→true, same instance id, was-hidden (re-show after hide):
    //     REST catch-up refetch + SSE reconnect. The component-local
    //     state (scroll, draft input, expanded todo nodes) is preserved
    //     because the component is never recreated AND the rendered
    //     subtree is keyed off ``renderedInstance`` — the capture-and-
    //     hold signal that bridges the transient ``currentInstance()``
    //     nulls caused by this very refetch (``startPolling`` wipes the
    //     list before the fresh rows land).
    //   visible→true, same instance, not from hidden: no-op.
    //
    // The effect intentionally reads both signals so Angular's reactive
    // graph keeps them as dependencies: a hide-then-show cycle with the
    // same instance id must still fire the catch-up refetch, even
    // though `activeInstanceId` did not change.
    effect(() => {
      const visible = this.visible();
      const activeId = this.viewState.activeInstanceId();
      const wasHidden = this.isHiddenNow;

      if (!visible) {
        if (!wasHidden) {
          this.isHiddenNow = true;
          this.sseService.disconnect();
          this.sseService.clearEvents();
          this.instanceService.stopPolling();
        }
        return;
      }

      this.isHiddenNow = false;

      const idChanged = activeId !== this.lastLoadedInstanceId;
      if (activeId && (idChanged || wasHidden)) {
        this.lastLoadedInstanceId = activeId;
        // Always restart polling on (re)load — startPolling stops any
        // existing ticker first, so this is safe even when polling is
        // already running. ``pollingScope()`` is the canonical
        // resolver shared with ``tabEffect`` (R2 / S2) — both effects
        // must agree on the polling scope.
        this.instanceService.startPolling(this.pollingScope());
        this.handleInstanceIdChange(activeId);
      }
    });

    // BUG 1/2 fix — maintain the render hold. ``currentInstance`` is
    // a computed over the polled instances list, so it transiently
    // nulls during every re-show (``startPolling`` wipes the list
    // before the refetch lands) and every project-scope switch. The
    // template's ``@if`` guards read ``renderedInstance`` instead, so
    // the chat-interface / message-input / todo subtrees survive those
    // transient windows and their component-local state (scroll,
    // draft, expanded nodes) is never destroyed.
    //
    // CRITICAL: ``renderedInstance`` deliberately does NOT react to
    // ``visible()`` — a hide/show cycle must not touch it. It is
    // released only by the explicit teardown paths encoded here:
    // id cleared, confirmed 404, or a genuine id switch (A→B).
    effect(() => {
      const id = this.currentInstanceId();
      const live = this.currentInstance();
      const notFound = this.instanceNotFound();

      // Confirmed-dead id (the API's own 404) always tears the hold
      // down so the not-found panel renders instead of a stale chat.
      if (notFound) {
        this.renderedInstance.set(null);
        return;
      }

      // Id explicitly cleared (terminate / new-instance reset) — the
      // previous instance's subtree must not linger.
      if (!id) {
        this.renderedInstance.set(null);
        return;
      }

      if (live) {
        // Live row available — refresh the hold (status/name updates
        // flow through; the object identity changes each poll).
        this.renderedInstance.set(live);
        return;
      }

      // Transient window: the id is set but no row resolves. Keep the
      // hold ONLY while it still belongs to this id — a switch A→B
      // releases it so B's loading state replaces A's subtree.
      const held = this.renderedInstance();
      if (held && held.instance_id !== id) {
        this.renderedInstance.set(null);
      }
    }, { allowSignalWrites: true });
  }

  private handleWatchoverStatus(statusChange: unknown, notify: boolean): void {
    if (!statusChange || typeof statusChange !== 'object') return;
    const candidate = statusChange as Record<string, unknown>;
    const instanceId = candidate['instance_id'];
    const status = candidate['status'];
    if (typeof instanceId !== 'string' || typeof status !== 'string') return;

    const currentInstance = this.currentInstance();
    if (!currentInstance || instanceId !== currentInstance.instance_id) return;
    const shouldNotify = notify && !this.processedWatchoverStatusEvents.has(statusChange);

    switch (status) {
      case 'watchover_active':
        this.watchoverEnabled.set(true);
        if (shouldNotify) {
          this.snackBar.open('👁️ Watchover activated', 'Dismiss', {
            duration: 2000,
            panelClass: 'info-snackbar',
          });
        }
        break;
      case 'watchover_inactive':
        this.watchoverEnabled.set(false);
        this.watchoverDenialCount.set(0);
        if (shouldNotify) {
          this.snackBar.open('👁️ Watchover deactivated', 'Dismiss', {
            duration: 2000,
            panelClass: 'info-snackbar',
          });
        }
        break;
      case 'watchover_failed':
        this.watchoverEnabled.set(false);
        this.watchoverDenialCount.set(0);
        if (shouldNotify) {
          this.snackBar.open('⚠️ Watchover operation failed', 'Dismiss', { duration: 3000 });
        }
        break;
      case 'watchover_terminated':
        this.watchoverEnabled.set(false);
        this.watchoverDenialCount.set(0);
        if (shouldNotify) {
          this.snackBar.open(
            '🛑 Instance terminated by Watchover (3 denials reached)',
            'Dismiss',
            { duration: 6000 }
          );
        }
        break;
      case 'mistake':
        // Mistake verdict — amber/warning toast, NOT red/danger
        // Does NOT increment denial count
        if (shouldNotify) {
          const mistakeReason = (candidate['reason'] as string) || 'tool call has an error';
          this.snackBar.open(
            `⚠️ Watchover noticed a mistake: ${mistakeReason}. No action counted.`,
            'Dismiss',
            { duration: 3000, panelClass: 'info-snackbar' }
          );
        }
        break;
      default:
        return;
    }

    if (notify) {
      this.processedWatchoverStatusEvents.add(statusChange);
    }
  }

  private syncWatchoverState(instance: InstanceInfo): void {
    this.watchoverEnabled.set(instance.watchover_enabled ?? false);
    this.watchoverContext.set(instance.watchover_context ?? null);
    // Deliberately NOT syncing watchoverDenialCount from the API.
    // The backend always returns 0 (graph state is not fetched per-instance
    // in list/get endpoints). The real-time SSE denial tracker is the
    // authoritative source for the counter.
  }

  private handleWatchoverDenial(message: unknown): void {
    if (!message || typeof message !== 'object') return;
    const candidate = message as Record<string, unknown>;
    if (candidate['role'] !== undefined && candidate['role'] !== 'tool') return;

    const content = candidate['content'];
    if (typeof content !== 'string') return;

    // Check for mistake messages — show toast but DON'T increment count
    if (content.startsWith('Watchover noticed a mistake')) {
      const currentInstance = this.currentInstance();
      if (!currentInstance) return;
      const messageInstanceId = candidate['instance_id'];
      if (typeof messageInstanceId === 'string' && messageInstanceId !== currentInstance.instance_id) {
        return;
      }
      const messageKey = [
        currentInstance.instance_id,
        candidate['message_id'] ?? candidate['tool_call_id'] ?? candidate['created_at'] ?? '',
        content,
      ].join(':');
      if (this.processedWatchoverDenials.has(messageKey)) return;
      this.processedWatchoverDenials.add(messageKey);

      // Mistake — amber toast, NO count increment
      const reason = content.replace(/^Watchover noticed a mistake.*?:\s*/, '').split('.')[0];
      this.snackBar.open(
        `⚠️ Watchover noticed a mistake: ${reason || 'fix and retry'}`,
        'Dismiss',
        { duration: 3000, panelClass: 'info-snackbar' }
      );
      return;  // IMPORTANT: return early, do NOT increment count
    }

    // Existing denial/deferred handling — unchanged below
    if (!content.startsWith('Watchover denied') && !content.startsWith('Watchover deferred')) {
      return;
    }

    const currentInstance = this.currentInstance();
    if (!currentInstance) return;
    const messageInstanceId = candidate['instance_id'];
    if (typeof messageInstanceId === 'string' && messageInstanceId !== currentInstance.instance_id) {
      return;
    }

    const messageKey = [
      currentInstance.instance_id,
      candidate['message_id'] ?? candidate['tool_call_id'] ?? candidate['created_at'] ?? '',
      content,
    ].join(':');
    if (this.processedWatchoverDenials.has(messageKey)) return;
    this.processedWatchoverDenials.add(messageKey);

    const nextCount = this.watchoverDenialCount() + 1;
    this.watchoverDenialCount.set(nextCount);
    const reason = content.replace(/^Watchover (denied|deferred).*?:\s*/, '').split('.')[0];
    this.snackBar.open(
      `⚠️ Watchover denied: ${reason || 'tool call blocked'} (${nextCount}/3)`,
      'Dismiss',
      { duration: 4000 }
    );
    if (nextCount >= 3) {
      this.snackBar.open(
        '🛑 Watchover limit reached — instance will be terminated',
        'Dismiss',
        { duration: 6000 }
      );
    }
  }

  /**
   * Convert SSE message to view model (adds instance_id).
   */
  private toViewModel(m: Message): Message {
    const currentInstance = this.currentInstance();
    return {
      ...m,
      instance_id: m.instance_id || currentInstance?.instance_id,
    };
  }

  ngOnDestroy(): void {
    // The chat component is now mounted at the App root and survives
    // route changes, so most of the original cleanup was relocated to
    // the visibility watcher (visible->false) which disconnects SSE,
    // clears events, and stops polling. On full component destruction
    // we still disconnect SSE and clear events to free the EventSource
    // and prevent stale state from leaking into the next session.
    // Phase 2: stop the command poll / eviction timers (per-instance
    // command states are intentionally KEPT — they survive re-mounts and
    // are re-synced from the server by the load-time GET reconcile).
    this.commandState.stopAllTimers();
    this.sseService.clearEvents();
    this.sseService.disconnect();
    this.messages.set([]);
    this.currentInstanceId.set(null);
  }

  ngOnInit(): void {
    // Skip starting polling when the overlay mounts hidden — the
    // visibility effect remains the canonical handler and will
    // start polling on the hidden→visible transition. Avoids a
    // wasted start/stop round-trip on boot when the overlay is
    // hidden by default. REST loads stay unconditional so tab
    // state and agent defaults hydrate regardless of visibility.
    const visibleAtInit = this.visible();
    // Load projects first, then restore tab state with valid project IDs
    this.projectService.listProjects().subscribe({
      next: (response) => {
        const projectIds = response.projects.map(p => p.project_id);
        this.tabStateService.restoreState(projectIds);

        // Continue with normal initialization after tab state is restored
        if (visibleAtInit) {
          // F3: use the canonical pollingScope() resolver (R2 / S2) —
          // never the raw activeProjectId() — so the 'all' pseudo-id
          // can't leak into the API filter from ANY startPolling path.
          this.instanceService.startPolling(this.pollingScope());
        }
        this.loadInitialData();
      },
      error: (err) => {
        console.error('[Chat] Failed to load projects:', err);
        // Still start polling even if project load fails — but only if the
        // overlay is currently visible (see visibility gate above).
        if (visibleAtInit) {
          this.instanceService.startPolling(this.pollingScope());
        }
        this.loadInitialData();
      }
    });

    // The active instance id now comes from InstancesViewStateService
    // (set up in the constructor's effect). The previous
    // ``route.params`` subscription was removed because the App root
    // host does not receive route params — the view-state service is
    // the authoritative source for both deep links and restored caches.
  }

  private loadInitialData(): void {
    this.api.listAgents().subscribe({
      next: (response) => {
        this.agents.set(response.agents);

        // Initialize selectedAgent from localStorage
        const savedAgentId = localStorage.getItem(NEXT_AGENT_STORAGE_KEY);
        if (savedAgentId) {
          const savedAgent = response.agents.find(a => a.id === savedAgentId);
          if (savedAgent) {
            this.selectedAgent.set(savedAgent);
            // Seed selectedVersionTag from persisted defaults so the detail
            // page's "create instance" button uses the saved version rather
            // than always falling back to the base version.
            this.api.getDefaultAgentVersions().subscribe({
              next: (resp) => {
                // Guard: don't clobber if user already switched to a different agent
                if (this.selectedAgent()?.id === savedAgent.id) {
                  const defaults = resp.default_versions ?? {};
                  this.selectedVersionTag.set(defaults[savedAgent.id] ?? null);
                }
              },
              error: () => {
                // Leave selectedVersionTag at its current value on error.
              },
            });
          }
        }
      },
      error: (err) => console.error('Failed to load agents:', err)
    });
  }

  private handleInstanceIdChange(instanceId: string | undefined): void {
    // Clear the stale not-found panel before every instance change: a stale
    // panel is gated from the chat UI, so switching to an in-list instance
    // must not inherit the previous id's 404 state.
    this.instanceNotFound.set(null);
    console.log('[Chat] handleInstanceIdChange called with:', instanceId);
    // Reset sending state when switching instances to prevent input lock
    this.isSending.set(false);
    this.sendError.set(null);
    // The queued indicator is tied to a specific instance; clear it on
    // every route change so a stale "queued" badge never bleeds across
    // chats.
    this.queuedMessage.set(null);

    // Reset watchover UI state synchronously while the next instance's
    // metadata loads. InstanceInfo (not localStorage) remains authoritative.
    this.watchoverEnabled.set(false);
    this.watchoverDenialCount.set(0);
    this.watchoverContext.set(null);

    // Clear the denial dedup set so it doesn't grow unboundedly across
    // instance switches. The keys are instance-scoped, so clearing on
    // switch is safe — denials are re-tracked by SSE for the new instance.
    this.processedWatchoverDenials.clear();

    if (!instanceId) {
      console.log('[Chat] No instanceId, disconnecting SSE');
      this.currentInstanceId.set(null);
      this.messages.set([]);
      this.sseService.disconnect();
      this.sseService.clearEvents();
      return;
    }

    // Set current instance ID - currentInstance computed will derive from instances list
    this.currentInstanceId.set(instanceId);

    // Synchronous clear of the stale todo list from the previous instance
    // before any async work begins. Without this, a late getTodos response
    // for the previous instance (or a stray todo_update from its still-open
    // SSE channel) can leak into the new instance's UI.
    this.sseService.todos.set([]);

    // Find instance in existing list or load it
    const instance = this.instanceService.instances().find(i => i.instance_id === instanceId);
    console.log('[Chat] Instance found in list:', !!instance, 'instances count:', this.instanceService.instances().length);
    if (instance) {
      console.log('[Chat] Using instance from list, connecting SSE');
      this.syncWatchoverState(instance);
      this.loadInstanceMessages(instanceId);
    } else {
      // Try to get instance from API
      console.log('[Chat] Instance not in list, fetching from API');
      this.api.getInstance(instanceId).subscribe({
        next: (instanceData) => {
          // R3 / W2: bail when the user hid the overlay or switched to a
          // different instance while this request was in-flight. Without
          // this guard ``loadInstanceMessages`` would connect an SSE
          // channel for the old id while hidden, leaking an EventSource
          // and writing the wrong messages into the (now-stale) signal.
          if (!this.visible() || this.viewState.activeInstanceId() !== instanceId) {
            return;
          }
          console.log('[Chat] Got instance from API, connecting SSE');
          this.syncWatchoverState(instanceData);
          // Add to instanceService list so currentInstance computed can find it
          this.instanceService.instances.update(list => {
            if (!list.find(i => i.instance_id === instanceId)) {
              return sortByCreatedAtDesc([...list, instanceData]);
            }
            return list;
          });
          this.instanceNotFound.set(null);
          this.loadInstanceMessages(instanceId);
        },
        error: (err) => {
          // Same staleness guard for the error path so we don't flash
          // the "instance not found" panel for an instance the user
          // has already navigated away from.
          if (!this.visible() || this.viewState.activeInstanceId() !== instanceId) {
            return;
          }
          // N1b: only a confirmed 404 marks the cached id as dead and
          // surfaces the not-found panel. Transient failures
          // (500/503/network blip) must NOT clear the cache or flash
          // the not-found panel — the user can retry, and the cache
          // survives so a refresh doesn't wipe a freshly opened
          // instance. Logging is sufficient; the user stays on the
          // existing view (a real 404 will set instanceNotFound and
          // re-render the not-found panel).
          if (err?.status === 404) {
            console.warn('[Chat] Instance not found (404):', instanceId);
            // Drop the dead id from the navigation cache, but keep the
            // not-found UI state for the current render.
            this.viewState.clearInstance(instanceId);
            this.instanceNotFound.set(instanceId);
            this.currentInstanceId.set(null);
            this.messages.set([]);
            this.sseService.disconnect();
            this.sseService.clearEvents();
          } else {
            console.warn('[Chat] Transient getInstance error, leaving cache intact:', instanceId, 'error:', err);
          }
        }
      });
    }
  }

  /**
   * Load initial messages via REST API, then connect SSE for real-time updates.
   * Also kicks off a parallel REST fetch for the persisted todo list, so the
   * UI has the current state (including any saved comments) ready before or
   * shortly after SSE connect — the next `todo_update` event will reconcile
   * any drift either way.
   *
   * ``mode``: ``'replace'`` (default) wipes the local message list and seeds
   * it with the server's view. Used on initial instance load and instance
   * switch — there's nothing useful in the local list to preserve.
   * ``'merge'`` upserts by ``message_id`` and preserves local-only pending
   * / provisional entries (message-display-latency §4.3 item 9). Used by
   * the reconnect catch-up effect so a refetch mid-pending never wipes
   * the user's just-rendered optimistic bubble.
   */
  private loadInstanceMessages(
    instanceId: string,
    options: { merge?: boolean } = {},
  ): void {
    this.api.getMessages(instanceId).subscribe({
      next: (messages) => {
        // R3 / W2: bail when the user hid the overlay or switched
        // instances mid-load. Writing into ``messages`` for an
        // instance we no longer care about would overwrite the
        // current instance's UI on the next tick.
        if (!this.visible() || this.viewState.activeInstanceId() !== instanceId) {
          return;
        }
        const viewModels = messages.map(m => this.toViewModel(m));
        if (options.merge) {
          // Union-by-id merge — never wipe local-only provisional entries.
          // The shared ``mergeMessagesById`` helper enforces the same
          // contract as the SSE mirror effect, so a refetch that lands
          // before the drain-time SSE echo doesn't lose the optimistic
          // bubble. Eviction runs in the same pass so a stuck pending
          // entry ages out on every refetch.
          this.messages.update(existing =>
            evictPendingByAge(
              mergeMessagesById(existing, viewModels),
              this.PENDING_TTL_MS,
              Date.now(),
            )
          );
        } else {
          this.messages.set(viewModels);
        }
      },
      error: (err) => {
        // Same staleness guard for the error path.
        if (!this.visible() || this.viewState.activeInstanceId() !== instanceId) {
          return;
        }
        console.warn('[Chat] Failed to load messages:', err);
        if (!options.merge) {
          this.messages.set([]);
        }
      },
      complete: () => {
        // R3 / W2: the SSE channel is the most consequential side
        // effect — a connect() that lands while the overlay is hidden
        // opens an EventSource that's never closed. Same guard as
        // getMessages' ``next`` handler.
        if (!this.visible() || this.viewState.activeInstanceId() !== instanceId) {
          return;
        }
        // Connect SSE after API messages are loaded
        this.sseService.connect(instanceId);
        // Reconcile both transient pending states on every instance load.
        // Keeping these REST fallbacks here avoids duplicate requests from
        // SseService.connect() while preserving symmetric handling.
        this.sseService.fetchPendingInjection(instanceId);
        this.sseService.fetchPendingQuestion(instanceId);
        // Slash-command recovery (Phase 2 / Task 8a): load-time GET
        // reconcile — a reload or instance re-entry mid-command restores
        // the card from server truth (server wins). Silent on
        // {exists:false} and on network failure by service contract.
        void this.commandState.reconcileFromServer(instanceId);
      }
    });

    // Initial todo list load — mirrors the messages call above. Errors are
    // non-fatal: the SSE `todo_update` event will still populate the list
    // once the agent publishes one.
    this.api.getTodos(instanceId).subscribe({
      next: (data) => {
        // Staleness guard: if the user has switched instances since this
        // request was issued, drop the response so it doesn't overwrite the
        // newer instance's todos. Also guards against hidden-overlay
        // writes (R3) — a connect-then-hide leaves this REST in-flight.
        if (!this.visible() || this.viewState.activeInstanceId() !== instanceId) return;
        this.sseService.todos.set(data ?? []);
      },
      error: (err) => {
        if (!this.visible() || this.viewState.activeInstanceId() !== instanceId) return;
        console.warn('[Chat] Failed to load todos:', err);
        // Clear stale todos on REST failure so the previous instance's data
        // doesn't linger indefinitely.
        this.sseService.todos.set([]);
      },
    });
  }

  protected onTerminateInstance(instanceId: string): void {
    this.api.deleteInstance(instanceId).subscribe({
      next: () => {
        // Drop the cached id from the view-state service so a dead
        // instance is never restored on the next nav-link click. The
        // service is a no-op when the terminated id doesn't match the
        // current cache, so calling it for unrelated rows is safe.
        this.viewState.clearInstance(instanceId);

        // Instance is removed from instanceService via its polling
        if (this.currentInstanceId() === instanceId) {
          this.currentInstanceId.set(null);
          this.router.navigate(['/']);
        }
      },
      error: (err) => console.error('Failed to delete instance:', err)
    });
  }

  protected onNewInstance(): void {
    const agent = this.selectedAgent();
    if (!agent) {
      this.router.navigate(['/']);
      return;
    }

    // Reset state when creating new instance
    this.isSending.set(false);
    this.sendError.set(null);
    this.currentInstanceId.set(null);
    this.messages.set([]);
    this.sseService.disconnect();
    this.sseService.clearEvents();

    const agentPath = `./agents/${agent.id}`;
    const projectId = this.tabStateService.activeProjectId() ?? undefined;
    // Phase 3: forward the version tag the user picked in the switcher
    // dropdown (or null when none was picked — backend falls back to base).
    const versionTag = this.selectedVersionTag() ?? undefined;

    this.api.createInstance(agentPath, undefined, projectId, versionTag).subscribe({
      next: (instance) => {
        // Instance will appear in instanceService via polling
        const projectContext = this.tabStateService.activeProjectId() ?? 'all';
        this.router.navigate(['/projects', projectContext, 'instances', instance.instance_id]);
      },
      error: (err) => {
        console.error('Failed to create instance:', err);
      }
    });
  }

  protected onAgentChange(payload: { agent: Agent; versionTag?: string | null }): void {
    this.selectedAgent.set(payload.agent);
    this.selectedVersionTag.set(payload.versionTag ?? null);
    localStorage.setItem(NEXT_AGENT_STORAGE_KEY, payload.agent.id);
  }

  protected onSendMessage(payload: MessagePayload): void {
    // BUG 1/2 companion: resolve the target through the RENDER hold.
    // ``currentInstance()`` transiently nulls during a re-show (the
    // polling restart wipes the list before the refetch lands), and
    // the send button is clickable during that window — bailing on
    // null here would silently drop the user's click.
    // The click→switch race is a single microtask: Angular batches signal updates within it, so the hold-bail finishes before the next paint and is not user-perceptible.
    const instance = this.renderedInstance();
    if (!instance) return;

    // ── Slash-command pre-parse (Phase 2 / Task 5) ────────────────────
    // Runs BEFORE the cooldown guard so an advisory rejection never
    // stamps the duplicate-send cooldown. Branches:
    //   ``//x``  → strip ONE slash, deliver as a plain message (O-B1 —
    //              load-bearing: the BE would re-parse the stripped text
    //              as a command if we POSTed it unrewritten);
    //   plain    → fall through to the unchanged message path;
    //   known    → command send path (duplicate guard + ack handling);
    //   unknown  → inline validation error, ZERO network call (advisory;
    //              the BE 400 UNKNOWN_COMMAND + available list remains
    //              authoritative and feeds the same inline surface when
    //              it fires).
    let effectiveContent = payload.content;
    const parse = this.commandRegistry.parseCommandInput(payload.content);
    if ('escape' in parse) {
      effectiveContent = parse.text;
    } else if ('known' in parse && parse.known) {
      if (this.commandState.isActive(instance.instance_id)) {
        // Advisory duplicate-command guard (SC5) — BE ``busy`` /
        // ``rate_limited`` refusals stay authoritative (§6).
        this.messageInputRef?.showCommandValidationError(
          'A command is already in progress on this instance.',
        );
        return;
      }
      this.sendCommand(instance.instance_id, payload);
      return;
    } else if ('known' in parse && !parse.known) {
      this.messageInputRef?.showCommandValidationError(`Unknown command: /${parse.name}`);
      return;
    }

    // Cooldown guard: block consecutive sends within SEND_COOLDOWN_MS to
    // prevent duplicate submissions from double Enter / double click. Both
    // the send button and the Enter key route through this handler, so this
    // single check covers both triggers.
    const now = Date.now();
    const elapsed = now - this.lastSendTime;
    if (this.lastSendTime > 0 && elapsed < this.SEND_COOLDOWN_MS) {
      const remaining = Math.ceil((this.SEND_COOLDOWN_MS - elapsed) / 1000);
      this.snackBar.open(
        `Please wait ${remaining}s before sending another message.`,
        'Dismiss',
        { duration: 2000, panelClass: 'info-snackbar' }
      );
      return;
    }
    this.lastSendTime = now;

    // Clear any previous error
    this.sendError.set(null);
    this.isSending.set(true);

    // MIN-2: capture the target instance id AT SEND TIME. The HTTP
    // response can land after the user switched instances (TOCTOU) —
    // the ``next`` handler below re-checks ``activeInstanceId()``
    // against this captured value before touching ``messages``, the
    // same guard pattern the SSE mirror effect uses.
    const sentInstanceId = instance.instance_id;

    this.api.sendMessage(instance.instance_id, effectiveContent, payload.images, payload.queue_id).subscribe({
      // Both 200 (PAUSED auto-resume / IDLE enqueue) and 202 (RUNNING /
      // WAITING_CHILDREN injection acceptance) are 2xx and fire `next` by
      // default in Angular's HttpClient. We treat both as success from the
      // UI's perspective — clear the input, rely on `injection_pending`
      // SSE event (and the chat-interface pendingInjection card driven
      // off the SseService signal) to reflect the injection's queued state.
      next: (rawResponse) => {
        // Single parsing point (Phase 2 / Task 2): plain-text content is
        // never a command, so a command ack here would mean BE registry
        // drift (R7) — surface its rejection copy defensively and bail.
        const parsed = parseCommandAck(rawResponse);
        if (parsed.kind === 'command') {
          this.isSending.set(false);
          if (parsed.ack.state === 'rejected') {
            this.messageInputRef?.showCommandValidationError(this.rejectionCopy(parsed.ack), 8000);
          }
          return;
        }
        const response = parsed.message;
        // Clear input only on success — error recovery keeps input populated
        this.messageInputRef?.clearInput();
        // Surface a "queued" indicator when the backend accepted the message
        // but did not dispatch it to the running agent. Store the typed
        // content so the template can render a truncated snippet letting
        // the user see WHICH message is queued.
        if (response.queued === true) {
          this.queuedMessage.set({ content: payload.content });
        } else {
          this.queuedMessage.set(null);
        }

        // Optimistic append (message-display-latency §4.3 item 12).
        // Cover both routing paths with a single branch: when the POST
        // response carries a ``message_id``, render the user's bubble
        // immediately. The id-keyed dedup on the SSE side (POST-time
        // echo + drain-time re-emit collapse onto the same row) plus
        // the union-by-id refetch merge guarantee we never produce a
        // duplicate — even if the SSE echo arrives within the same
        // tick. When ``message_id`` is absent (old backend / PAUSED
        // ``None`` case) we degrade to today's render-on-echo flow and
        // do NOT append locally.
        //
        // We intentionally do NOT ship a content-matching reconciler
        // (deliberately out of scope per design §9.7): the server id
        // is authoritative and the POST response is the canonical
        // confirmation for the send-before-SSE-connect race.
        const newId = response.message_id;
        if (newId) {
          // MIN-2: drop the provisional when the user switched instances
          // between send and response — appending here would land a
          // bubble for ``sentInstanceId`` into the newly-opened
          // instance's list. ``isSending`` is still released: the send
          // itself completed and the spinner must not stick across the
          // switch.
          const activeInstanceId = this.viewState.activeInstanceId();
          if (activeInstanceId === sentInstanceId) {
            // MIN-1a: skip the append when a message with this id
            // already exists — the SSE echo can land BEFORE the HTTP
            // response, and merging a ``pending: true`` provisional
            // over the already-confirmed echo bubble would resurrect
            // the spinner (pending-flag resurrection). The confirmed
            // copy stays untouched.
            const alreadyPresent = this.messages().some(
              m => m.message_id === newId,
            );
            if (!alreadyPresent) {
              // message-display-latency fix: defensive read of the
              // server-authoritative stamp. The 202 body now ships
              // ``created_at`` (same value as the SSE echo's created_at),
              // but older backends / degraded shapes may only carry
              // ``timestamp``. Fall back in order, ending with ``now``
              // only as a last resort so the provisional never gets an
              // unparseable stamp (which would mis-sort to the top AND
              // get evicted by ``evictPendingByAge`` on the next refetch).
              const provisionalStamp =
                response.created_at ?? response.timestamp ?? new Date().toISOString();
              const provisional = makeProvisionalMessage({
                messageId: newId,
                // Phase 2: the DELIVERED content (``//x`` was rewritten to
                // ``/x`` pre-POST) — the bubble must show what the model
                // sees, not the raw composer text.
                content: effectiveContent,
                createdAt: provisionalStamp,
                instanceId: instance.instance_id,
                images: payload.images,
              });
              // MIN-5: TTL eviction runs ONLY in the SSE-mirror /
              // refetch passes — never here. A slow POST whose 202
              // carries the original send-time ``created_at`` would
              // otherwise be appended and immediately evicted by this
              // very pass (>10 min old by stamp), flashing the bubble
              // and dropping the user's only send confirmation. The
              // next mirror / refetch pass handles eviction.
              this.messages.update(existing =>
                mergeMessagesById(existing, [provisional])
              );
            }
          }
          // The optimistic bubble is the user's confirmation that the
          // message landed — the SSE echo / drain re-emit will clear
          // the ``pending`` flag via the merge helper. Reset the
          // sending flag now so the input is immediately interactive;
          // the user-visible state is the bubble, not a spinner.
          this.isSending.set(false);
        }
      },
      error: (err) => {
        console.error('Failed to send message:', err);
        this.sendError.set(err instanceof Error ? err.message : 'Failed to send message');
        this.isSending.set(false);
        // Do NOT clear input on error — user can retry
      }
    });
  }

  /**
   * Slash-command send path (Phase 2 / Task 5). Posts the command text to
   * the SAME ``POST /messages`` endpoint (BE-side router intercept — Q1
   * ratified) and branches on the discriminated ack:
   *
   * - ``accepted`` → clear the input (parent-owned clearing contract) and
   *   seed the CommandStateService card in ``waiting`` IMMEDIATELY —
   *   before any SSE event (the ack→first-SSE gap can be ≤30s, R5).
   * - ``rejected``  → NO machine start; keep the input populated for
   *   retry; render reason-specific inline copy — ``terminal_instance``
   *   renders the ack ``detail`` guidance VERBATIM (§9-12 / SC14).
   *
   * In BOTH ack cases the command NEVER enters the message echo/merge
   * pipeline — no ``makeProvisionalMessage``, no ``mergeMessagesById`` —
   * so a command cannot produce a provisional row or a duplicate timeline
   * entry (R4 / SC6). The card (out-of-timeline, Q3 confirmed) is the
   * command's only UI surface.
   */
  private sendCommand(instanceId: string, payload: MessagePayload): void {
    this.sendError.set(null);
    this.isSending.set(true);

    // MIN-2 TOCTOU capture — same rationale as the message path (R2).
    const sentInstanceId = instanceId;

    this.api.sendMessage(instanceId, payload.content, payload.images, payload.queue_id).subscribe({
      next: (response) => {
        this.isSending.set(false);
        const parsed = parseCommandAck(response);
        if (parsed.kind !== 'command') {
          // Defensive legacy fallback: an old backend without the intercept
          // answered the command text with a normal message body. The text
          // WAS delivered as a message — clear the input and surface the
          // queued indicator when the body says so; never seed the card.
          this.messageInputRef?.clearInput();
          if (parsed.message?.queued === true) {
            this.queuedMessage.set({ content: payload.content });
          }
          return;
        }

        const ack = parsed.ack;
        if (ack.state === 'accepted') {
          this.messageInputRef?.clearInput();
          // R2: apply the seed only when the user is still viewing the
          // instance the command was sent to (sentInstanceId guard).
          if (this.viewState.activeInstanceId() === sentInstanceId) {
            this.commandState.startCommand(sentInstanceId, ack);
          }
          return;
        }

        // Rejected — reason-specific inline copy; input stays populated.
        this.messageInputRef?.showCommandValidationError(this.rejectionCopy(ack), 8000);
      },
      error: (err) => {
        this.isSending.set(false);
        // HTTP 400 UNKNOWN_COMMAND (§7 split rule / O13) → typed error
        // carrying ``details.available``; offered inline as the recovery.
        const unknown = extractUnknownCommandError(err);
        if (unknown) {
          const available = unknown.available.length > 0
            ? ` Available: ${unknown.available.map(c => '/' + c).join(', ')}`
            : '';
          this.messageInputRef?.showCommandValidationError(`Unknown command.${available}`, 6000);
          return;
        }
        console.error('Failed to send command:', err);
        this.sendError.set(err instanceof Error ? err.message : 'Failed to send command');
        // Do NOT clear input on error — user can retry
      },
    });
  }

  /**
   * Reason-specific rejection copy (Task 5). ``terminal_instance`` shows
   * the backend ``detail`` guidance VERBATIM — the copy table in
   * architecture-recommendation.md §9-12 pins "Send a message to start a
   * new turn, then /compact." as the string the user must see.
   */
  private rejectionCopy(ack: CommandAck): string {
    if (ack.reason === 'terminal_instance') {
      return ack.detail || 'Send a message to start a new turn, then /compact.';
    }
    switch (ack.reason) {
      case 'busy':
        return 'A command is already running on this instance — wait for it to finish (busy).';
      case 'rate_limited':
        return 'Please wait a moment before running another command (rate_limited).';
      case 'pending_injections':
        return 'Deliver the pending injections first, then retry (pending_injections).';
      case 'compaction_disabled':
        return 'Compaction is disabled for this instance (compaction_disabled).';
      case 'quiescence_timeout':
        return 'The instance could not be quiesced in time — try again (quiescence_timeout).';
      default:
        return ack.detail || 'The command was rejected.';
    }
  }

  protected onClearError(): void {
    this.sendError.set(null);
  }

  protected onToggleWatchover(): void {
    // BUG 1/2 companion: same rationale as onSendMessage — the toggle
    // is clickable during the transient re-show window where
    // ``currentInstance()`` is null.
    const instance = this.renderedInstance();
    if (!instance) return;

    if (this.watchoverEnabled()) {
      // Turning OFF — simple API call, no dialog
      this._toggleWatchoverApi(instance.instance_id, false, null);
      return;
    }

    // Turning ON — check instance state.
    // Running instances: no dialog. The backend's intelligent context
    // builder derives guardrails from the live message stream, so the
    // operator only needs to click the button.
    // Terminal / idle instances: show the dialog so the operator can
    // name the next command (required) and tighten the watcher's
    // guardrails (optional). The backend forwards ``next_command``
    // as the resume message on the post-activation graph turn.
    //
    // NOTE: the task spec referenced 'running' | 'active' as the
    // running states, but `InstanceStatus` (models/index.ts) only
    // includes 'running' — 'active' is not part of the instance
    // status union. Only 'running' is checked here so the type
    // stays consistent with the backend contract.
    const status = instance.status;
    const isRunning = status === 'running';

    if (isRunning) {
      this._toggleWatchoverApi(instance.instance_id, true, null);
      this.snackBar.open(
        '👁️ Watchover enabled — analyzing instance activity...',
        'Dismiss',
        { duration: 3000, panelClass: 'info-snackbar' }
      );
    } else {
      this._openWatchoverDialog(instance.instance_id, instance.instance_name);
    }
  }

  /**
   * Open the watchover activation dialog for a non-running instance.
   * On confirm, the captured result is forwarded to
   * ``_toggleWatchoverApi`` so the watchover preference is persisted
   * with the requirement and the next command is sent to the backend
   * via the new ``next_command`` field. Cancel / no-op result is
   * silently ignored — the watchover state stays unchanged.
   */
  private _openWatchoverDialog(
    instanceId: string,
    instanceName?: string | null,
  ): void {
    const dialogRef = this.dialog.open(WatchoverDialogComponent, {
      data: { instanceId, instanceName },
      width: '520px',
      backdropClass: 'watchover-dialog-backdrop',
      panelClass: 'watchover-dialog-panel',
    });

    dialogRef.afterClosed().subscribe((result: WatchoverDialogResult | null) => {
      if (!result) return; // Cancelled or backdrop-dismissed

      this._toggleWatchoverApi(
        instanceId,
        true,
        result.watchoverRequirement,
        result.nextCommand,
      );
    });
  }

  private _toggleWatchoverApi(
    instanceId: string,
    enabled: boolean,
    requirement: string | null,
    nextCommand?: string | null,
  ): void {
    this.watchoverPending.set(true);
    this.api.setWatchover(instanceId, enabled, requirement, nextCommand).subscribe({
      next: (response) => {
        // Guard: ignore stale responses after instance switch
        if (this.currentInstanceId() !== instanceId) return;
        this.watchoverEnabled.set(response.watchover_enabled);
        if (!response.watchover_enabled) {
          this.watchoverDenialCount.set(0);
        }
        this.persistWatchoverPreference(instanceId, response.watchover_enabled, requirement);
        this.snackBar.open(
          response.watchover_enabled ? '👁️ Watchover enabled' : '👁️ Watchover disabled',
          'Dismiss',
          { duration: 2000, panelClass: 'info-snackbar' }
        );
      },
      error: (err) => {
        console.error('[Chat] Watchover toggle failed:', err);
        this.snackBar.open('Failed to toggle watchover', 'Dismiss', { duration: 3000 });
        this.watchoverPending.set(false);
      },
      complete: () => {
        this.watchoverPending.set(false);
      }
    });
  }

  private getSavedWatchoverRequirement(instanceId: string): string | null {
    const raw = localStorage.getItem(`ensemble-watchover-${instanceId}`);
    if (!raw) return null;

    try {
      const saved = JSON.parse(raw) as { requirement?: unknown };
      return typeof saved.requirement === 'string' ? saved.requirement : null;
    } catch {
      return null;
    }
  }

  private persistWatchoverPreference(
    instanceId: string,
    enabled: boolean,
    requirement: string | null,
  ): void {
    const savedRequirement = requirement ?? this.getSavedWatchoverRequirement(instanceId);
    localStorage.setItem(
      `ensemble-watchover-${instanceId}`,
      JSON.stringify({ enabled, requirement: savedRequirement })
    );
  }

  protected onToggleThinking(): void {
    this.showThinking.update(v => !v);
  }

  protected onToggleToolCalls(): void {
    this.showToolCalls.update(v => !v);
  }

  protected onToggleSystemPrompt(): void {
    this.showSystemPrompt.update(v => !v);
  }

  protected onBackToHome(): void {
    this.router.navigate(['/']);
  }

  protected onLoadMoreInstances(): void {
    this.instanceService.loadMore();
  }

  protected onPauseInstance(): void {
    const instanceId = this.currentInstanceId();
    if (instanceId) {
      this.api.pauseInstance(instanceId).subscribe({ error: (err: any) => console.error('Pause failed:', err) });
    }
  }

  protected onPauseInstanceFromList(instanceId: string): void {
    this.api.pauseInstance(instanceId).subscribe({ error: (err: any) => console.error('Pause failed:', err) });
  }

  protected onResumeInstance(message?: string): void {
    const instanceId = this.currentInstanceId();
    if (instanceId) {
      this.sendError.set(null);
      // Store message in case API fails - we'll restore it
      const failedMessage = message || '';
      this.api.resumeInstance(instanceId, message).subscribe({
        next: () => {
          // Already cleared in handleResume(), just update state if needed
        },
        error: (err: any) => {
          console.error('Resume failed:', err);
          this.sendError.set('Failed to resume instance. Please try again.');
          // Restore message so user can retry
          this.messageInputRef?.message.set(failedMessage);
        }
      });
    }
  }

  protected onResumeInstanceFromList(instanceId: string): void {
    this.api.resumeInstance(instanceId).subscribe({ error: (err: any) => console.error('Resume failed:', err) });
  }

  // Workspace overlay state. The overlay element itself is mounted at
  // the App root so it survives route changes and can be toggled by
  // the Alt+` global hotkey. State (whether the overlay is shown and
  // which project it is bound to) lives in the root-provided
  // WorkspaceOverlayService singleton.
  //
  // The toggle handlers below delegate to the service. The chat-header
  // button's active-state binding reads the service's signals directly
  // (see `workspaceOverlayService` above) so the UI stays in sync with
  // the same source of truth that the App root uses.

  /**
   * Handle workspace icon click from the project tab bar.
   * Same project toggles off; different project switches to that project.
   */
  protected onWorkspaceToggle(projectId: string): void {
    this.workspaceOverlayService.toggle(projectId);
  }

  /**
   * Toggle the workspace overlay from the chat header button. Targets
   * the currently active project (resolved via existing projectId getter).
   */
  protected onHeaderWorkspaceToggle(): void {
    if (!this.hasRealProject) return;
    this.workspaceOverlayService.toggle(this.projectId);
  }

  // Expose for template
  protected readonly localStorage = localStorage;
}
