import { Component, signal, computed, inject, OnInit, OnDestroy, effect, ViewChild, Signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { ActivatedRoute, Router } from '@angular/router';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatSnackBar, MatSnackBarModule } from '@angular/material/snack-bar';
import { MatTooltipModule } from '@angular/material/tooltip';
import { MatDialog, MatDialogModule } from '@angular/material/dialog';
import { Subscription } from 'rxjs';
import { ApiService } from '../../services/api.service';
import { SseService } from '../../services/sse.service';
import { TabStateService } from '../../services/tab-state.service';
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
import { WorkspaceComponent } from '../workspace/workspace.component';
import type { Agent, InstanceInfo, Message } from '../../models';

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
    QuestionWizardComponent,
    WorkspaceComponent
  ],
  templateUrl: './chat.html',
  styleUrl: './chat.scss'
})
export class ChatComponent implements OnInit, OnDestroy {
  private readonly route = inject(ActivatedRoute);
  private readonly router = inject(Router);
  private readonly api = inject(ApiService);
  private readonly sseService = inject(SseService);
  protected readonly tabStateService = inject(TabStateService);
  protected readonly instanceService = inject(InstanceService);
  private readonly projectService = inject(ProjectService);
  private readonly snackBar = inject(MatSnackBar);
  private readonly dialog = inject(MatDialog);
  private routeSubscription: Subscription | null = null;

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

  readonly agents = signal<Agent[]>([]);
  readonly currentInstanceId = signal<string | null>(null);
  readonly currentInstance: Signal<InstanceInfo | null> = computed(() => {
    const id = this.currentInstanceId();
    if (!id) return null;
    return this.instanceService.instances().find(i => i.instance_id === id) ?? null;
  });
  readonly messages = signal<Message[]>([]);
  readonly selectedAgent = signal<Agent | null>(null);
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

  private tabEffect = effect(() => {
    const projectId = this.tabStateService.activeProjectId();
    this.instanceService.startPolling(projectId ?? undefined);
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
   * local signals (showWorkspace / workspaceProjectId), mirroring the
   * pattern used by other write-emitting effects in this component.
   */
  private tabWorkspaceEffect = effect(() => {
    const projectId = this.tabStateService.activeProjectId();
    const isOpen = this.showWorkspace();         // always read → always tracked
    const currentId = this.workspaceProjectId(); // always read → always tracked

    if (projectId === null) {
      if (isOpen)    this.showWorkspace.set(false);
      if (currentId) this.workspaceProjectId.set(null);
      return;
    }

    if (isOpen && currentId !== projectId) {
      this.workspaceProjectId.set(projectId);
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
    const instance = this.currentInstance();
    if (!instance) return;
    this.syncWatchoverState(instance);
  }, { allowSignalWrites: true });

  private readonly processedWatchoverDenials = new Set<string>();
  private readonly processedWatchoverStatusEvents = new WeakSet<object>();

  @ViewChild(MessageInputComponent) messageInputRef!: MessageInputComponent;

  // Computed instance agent
  readonly instanceAgent = computed(() => {
    const instance = this.currentInstance();
    if (!instance) return null;
    return this.agents().find(a => instance.agent_id.includes(a.id)) || null;
  });

  constructor() {
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
    effect(() => {
      const sseMessages = this.sseService.messages();
      if (sseMessages.length === 0) return;

      // Merge: upsert SSE messages into existing list.
      this.messages.update(existing => {
        const result = [...existing];
        for (const msg of sseMessages) {
          const idx = result.findIndex(m => m.message_id === msg.message_id);
          if (idx >= 0) {
            // Shallow merge: top-level fields from SSE win, but any local-only
            // top-level fields are preserved. Note this REPLACES reference
            // fields (e.g. tool_calls) wholesale with SSE's copy — that is
            // intentional so patched tool_call outputs flow through.
            result[idx] = { ...result[idx], ...msg };
          } else {
            result.push(msg);
          }
        }
        // Sort by created_at
        result.sort((a, b) => (a.created_at || '').localeCompare(b.created_at || ''));
        return result;
      });
      this.isSending.set(false);
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
    if (
      typeof content !== 'string'
      || (!content.startsWith('Watchover denied') && !content.startsWith('Watchover deferred'))
    ) {
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
    this.instanceService.stopPolling();
    this.sseService.clearEvents();
    this.sseService.disconnect();
    this.messages.set([]);
    this.currentInstanceId.set(null);
    if (this.routeSubscription) {
      this.routeSubscription.unsubscribe();
    }
  }

  ngOnInit(): void {
    // Load projects first, then restore tab state with valid project IDs
    this.projectService.listProjects().subscribe({
      next: (response) => {
        const projectIds = response.projects.map(p => p.project_id);
        this.tabStateService.restoreState(projectIds);
        
        // Continue with normal initialization after tab state is restored
        this.instanceService.startPolling(this.tabStateService.activeProjectId() ?? undefined);
        this.loadInitialData();
      },
      error: (err) => {
        console.error('[Chat] Failed to load projects:', err);
        // Still start polling even if project load fails
        this.instanceService.startPolling(this.tabStateService.activeProjectId() ?? undefined);
        this.loadInitialData();
      }
    });
    
    // Subscribe to route parameter changes
    this.routeSubscription = this.route.params.subscribe(params => {
      const instanceId = params['instanceId'];
      this.handleInstanceIdChange(instanceId);
    });
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
          console.warn('[Chat] Instance not found:', instanceId, 'error:', err);
          this.instanceNotFound.set(instanceId);
          this.currentInstanceId.set(null);
          this.messages.set([]);
          this.sseService.disconnect();
          this.sseService.clearEvents();
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
   */
  private loadInstanceMessages(instanceId: string): void {
    this.api.getMessages(instanceId).subscribe({
      next: (messages) => {
        console.log('[Chat] Loaded', messages.length, 'messages from API');
        const viewModels = messages.map(m => this.toViewModel(m));
        this.messages.set(viewModels);
      },
      error: (err) => {
        console.warn('[Chat] Failed to load messages:', err);
        this.messages.set([]);
      },
      complete: () => {
        // Connect SSE after API messages are loaded
        this.sseService.connect(instanceId);
        // Reconcile both transient pending states on every instance load.
        // Keeping these REST fallbacks here avoids duplicate requests from
        // SseService.connect() while preserving symmetric handling.
        this.sseService.fetchPendingInjection(instanceId);
        this.sseService.fetchPendingQuestion(instanceId);
      }
    });

    // Initial todo list load — mirrors the messages call above. Errors are
    // non-fatal: the SSE `todo_update` event will still populate the list
    // once the agent publishes one.
    this.api.getTodos(instanceId).subscribe({
      next: (data) => {
        // Staleness guard: if the user has switched instances since this
        // request was issued, drop the response so it doesn't overwrite the
        // newer instance's todos.
        if (this.currentInstanceId() !== instanceId) return;
        this.sseService.todos.set(data ?? []);
      },
      error: (err) => {
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
    const instance = this.currentInstance();
    if (!instance) return;

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
    
    this.api.sendMessage(instance.instance_id, payload.content, payload.images, payload.queue_id).subscribe({
      // Both 200 (PAUSED auto-resume / IDLE enqueue) and 202 (RUNNING /
      // WAITING_CHILDREN injection acceptance) are 2xx and fire `next` by
      // default in Angular's HttpClient. We treat both as success from the
      // UI's perspective — clear the input, rely on `injection_pending`
      // SSE event (and the chat-interface pendingInjection card driven
      // off the SseService signal) to reflect the injection's queued state.
      next: (response) => {
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
      },
      error: (err) => {
        console.error('Failed to send message:', err);
        this.sendError.set(err instanceof Error ? err.message : 'Failed to send message');
        this.isSending.set(false);
        // Do NOT clear input on error — user can retry
      }
    });
  }

  protected onClearError(): void {
    this.sendError.set(null);
  }

  protected onToggleWatchover(): void {
    const instance = this.currentInstance();
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

  // Workspace overlay state. The overlay covers the chat area (header +
  // interface + input) when shown. Closing it via Hide triggers the
  // WorkspaceService LRU cache to retain project state, so re-opening
  // restores the prior file tree.
  readonly showWorkspace = signal(false);
  readonly workspaceProjectId = signal<string | null>(null);

  /**
   * Handle workspace icon click from the project tab bar.
   * Same project toggles off; different project switches to that project.
   */
  protected onWorkspaceToggle(projectId: string): void {
    if (this.showWorkspace() && this.workspaceProjectId() === projectId) {
      this.showWorkspace.set(false);
      return;
    }
    this.workspaceProjectId.set(projectId);
    this.showWorkspace.set(true);
  }

  /**
   * Handle the workspace overlay's Hide button. State is preserved by
   * WorkspaceService's LRU cache, so no extra work is needed here.
   */
  protected onWorkspaceHide(): void {
    this.showWorkspace.set(false);
  }

  /**
   * Toggle the workspace overlay from the chat header button. Targets
   * the currently active project (resolved via existing projectId getter).
   */
  protected onHeaderWorkspaceToggle(): void {
    if (!this.hasRealProject) return;
    this.onWorkspaceToggle(this.projectId);
  }

  // Expose for template
  protected readonly localStorage = localStorage;
}
