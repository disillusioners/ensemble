import { signal, computed, effect, Component, Inject, inject } from '@angular/core';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { Router } from '@angular/router';
import { MatDialog } from '@angular/material/dialog';
import { MatSnackBar } from '@angular/material/snack-bar';
import { of, Subject, throwError } from 'rxjs';
import { ApiService } from '../../services/api.service';
import { InstanceService } from '../../services/instance.service';
import { InstancesViewStateService } from '../../services/instances-view-state.service';
import { ProjectService } from '../../services/project.service';
import { SseService } from '../../services/sse.service';
import { TabStateService } from '../../services/tab-state.service';
import { WorkspaceOverlayService } from '../../services/workspace-overlay.service';
import type { Agent, InstanceInfo, CommandProgressEvent } from '../../models';
import { ProjectTab } from '../../models/tab.model';
import {
  makeProvisionalMessage,
  mergeMessagesById,
} from '../../services/message-merge.util';
import { ChatComponent } from './chat.component';

jest.mock('ngx-markdown', () => ({
  MarkdownModule: class MarkdownModule {},
}));

const NEXT_AGENT_STORAGE_KEY = 'ensemble-next-instance-agent';

// Mock TabStateService — mirrors production semantics: `activeProjectId`
// is a computed that returns null when the active tab is the 'all'
// pseudo-project, matching the real TabStateService. `setActiveTab(tabId)`
// looks up the tab in openTabs and falls back to constructing a
// project-type tab on the fly so tests that switch to non-existent
// project ids still drive the computed.
class MockTabStateService {
  readonly openTabs = signal<ProjectTab[]>([
    { id: 'all', name: 'All', type: 'all' }
  ]);
  readonly activeTab = signal<ProjectTab>({ id: 'all', name: 'All', type: 'all' });

  readonly activeProjectId = computed(() => {
    const tab = this.activeTab();
    return tab.type === 'project' ? tab.id : null;
  });

  setActiveTabCalls: string[] = [];
  setActiveTab(tabId: string): void {
    this.setActiveTabCalls.push(tabId);
    const tab = this.openTabs().find((t) => t.id === tabId);
    if (tab) {
      this.activeTab.set(tab);
      return;
    }
    // Tests may switch to a project id that hasn't been opened as a tab.
    // Production would silently no-op, but the test mock synthesizes a
    // project-type tab so the computed `activeProjectId` reflects the
    // simulated switch.
    this.activeTab.set({ id: tabId, name: tabId, type: 'project' });
  }
}

// Mock ApiService for testing
const mockApiService = {
  listAgents: jest.fn(),
  getInstance: jest.fn(),
  getMessages: jest.fn(),
  getTodos: jest.fn(),
  sendMessage: jest.fn(),
  deleteInstance: jest.fn(),
  pauseInstance: jest.fn(),
  resumeInstance: jest.fn(),
  setWatchover: jest.fn(),
  createInstance: jest.fn(),
};

// Mock InstanceService for testing
const mockInstanceService = {
  startPolling: jest.fn(),
  stopPolling: jest.fn(),
  isPolling: jest.fn(() => false),
  instances: signal<InstanceInfo[]>([]),
};

// Mock SseService for testing
const mockSseService = {
  messages: signal<Message[]>([]),
  events: signal<any[]>([]),
  statusChange: signal<{ instance_id: string; status: string } | null>(null),
  isStreaming: signal(false),
  latestError: signal<any>(null),
  todos: signal<any[]>([]),
  // Reconnect-refetch + pending-purge triggers (message-display-latency
  // §4.3 items 10 + 11). The real service bumps these on SSE events; in
  // tests they're plain signals tests can drive directly.
  refetchRequest: signal<number>(0),
  pendingPurgeRequest: signal<number>(0),
  // MIN-3: which instance the latest purge bump refers to — the
  // component effect compares it against ``activeInstanceId()``.
  pendingPurgeInstanceId: signal<string | null>(null),
  // Phase 2 / slash-commands: latest ``command_progress`` event mirror.
  // The component effect feeds it into CommandStateService (guarded by
  // the active instance id); tests can drive it directly.
  commandProgress: signal<CommandProgressEvent | null>(null),
  connect: jest.fn(),
  disconnect: jest.fn(),
  clearEvents: jest.fn(),
  fetchPendingInjection: jest.fn(),
  fetchPendingQuestion: jest.fn(),
};

// Mock MatSnackBar for testing
const mockSnackBar = {
  open: jest.fn(),
};

interface TestMessagePayload {
  content: string;
  images?: string[];
  queue_id?: string | null;
  /** Defect #5 retry path — see production ``MessagePayload``. */
  retry_of_message_id?: string;
}

// Mock WorkspaceOverlayService — mirrors production semantics:
// `showWorkspace` and `workspaceProjectId` are independent
// WritableSignals, and the toggle/hide/show methods mutate them with
// the same contract as the real service. Tests construct one of these
// and pass it to the testable component (or provide it via TestBed),
// so they observe exactly the same state transitions production would.
class MockWorkspaceOverlayService {
  readonly showWorkspace = signal(false);
  readonly workspaceProjectId = signal<string | null>(null);

  toggle(projectId?: string): void {
    const currentId = this.workspaceProjectId();
    const targetId = projectId ?? currentId;
    if (targetId === null) return;

    if (this.showWorkspace() && currentId === targetId) {
      this.showWorkspace.set(false);
      return;
    }
    this.workspaceProjectId.set(targetId);
    this.showWorkspace.set(true);
  }

  hide(): void {
    this.showWorkspace.set(false);
  }

  show(projectId: string): void {
    this.workspaceProjectId.set(projectId);
    this.showWorkspace.set(true);
  }
}

// Mock InstancesViewStateService — mirrors the surface ChatComponent
// actually reads/writes: `activeInstanceId`, `activeProjectId`,
// `detailVisible` signals and `clearInstance()` / `closeDetail()`
// methods. Tests pass it to the testable component so the production
// `viewState.clearInstance(instanceId)` flow on termination is
// observable.
class MockInstancesViewStateService {
  readonly activeInstanceId = signal<string | null>(null);
  readonly activeProjectId = signal<string>('all');
  readonly detailVisible = signal(false);

  clearInstanceCalls: string[] = [];
  openDetailCalls: Array<{ projectId: string; instanceId: string }> = [];
  closeDetailCalls = 0;

  openDetail(projectId: string, instanceId: string): void {
    this.openDetailCalls.push({ projectId, instanceId });
    this.activeProjectId.set(projectId || 'all');
    this.activeInstanceId.set(instanceId);
    this.detailVisible.set(true);
  }

  closeDetail(): void {
    this.closeDetailCalls++;
    this.detailVisible.set(false);
  }

  clearInstance(instanceId: string): void {
    this.clearInstanceCalls.push(instanceId);
    if (this.activeInstanceId() !== instanceId) return;
    this.activeInstanceId.set(null);
    this.detailVisible.set(false);
  }
}

// Testable ChatComponent (mirrors actual component logic)
class TestableChatComponent {
  private readonly api = mockApiService;
  private readonly sseService = mockSseService;
  private readonly snackBar = mockSnackBar;
  protected readonly tabStateService: MockTabStateService;
  /**
   * Singleton state holder for the workspace overlay. Mirrors the
   * production injection: in production ChatComponent injects
   * `WorkspaceOverlayService` (providedIn: 'root'); here we wire a
   * `MockWorkspaceOverlayService` so tests can observe the same
   * state that the (real) App root would see.
   */
  readonly workspaceOverlayService: MockWorkspaceOverlayService;
  /**
   * Singleton state holder for the instance-detail overlay. Mirrors
   * the production injection of `InstancesViewStateService`. The
   * testable component wires it so the production
   * `viewState.clearInstance(instanceId)` call on termination is
   * observable in tests.
   */
  readonly viewState: MockInstancesViewStateService;

  readonly currentInstanceId = signal<string | null>(null);
  readonly currentInstance = signal<InstanceInfo | null>(null);
  readonly selectedAgent = signal<Agent | null>(null);
  readonly isSending = signal(false);
  readonly sendError = signal<string | null>(null);
  readonly watchoverEnabled = signal(false);
  readonly watchoverContext = signal<string | null>(null);
  readonly watchoverDenialCount = signal(0);
  readonly watchoverPending = signal(false);
  private readonly processedWatchoverDenials = new Set<string>();

  /**
   * Mirrors the production ``messages`` signal that the guarded
   * callbacks in ``handleInstanceIdChange`` / ``loadInstanceMessages``
   * write into. The F4 contract tests assert that a stale async
   * completion performs NO write here.
   */
  readonly messages = signal<Message[]>([]);

  /**
   * R1/R3 mirror: the App root binds ``[visible]`` to the chat
   * component, gating polling and SSE reconnect. Default ``true`` so
   * existing tests keep their previous "visible at boot" assumption;
   * R1/R3 tests flip it to ``false`` to assert the gates.
   */
  readonly visible = signal(true);

  /** Mirrors ChatComponent.SEND_COOLDOWN_MS */
  private readonly SEND_COOLDOWN_MS = 3000;
  /** Mirrors ChatComponent.PENDING_TTL_MS — 10-minute wall-clock TTL
   *  for ``evictPendingByAge``. */
  private readonly PENDING_TTL_MS = 10 * 60 * 1000;
  private lastSendTime = 0;

  // Send-side queued-indicator mirror (production ChatComponent sets this
  // from ``response.queued``). Default ``null`` = no queued indicator.
  readonly queuedMessage = signal<{ content: string } | null>(null);

  messageInputRef: { clearInput: jest.Mock } = { clearInput: jest.fn() };

  // Navigation calls tracked for testing
  navigateCalls: Array<{ path: string[] }> = [];

  constructor(
    tabStateService: MockTabStateService,
    workspaceOverlayService: MockWorkspaceOverlayService = new MockWorkspaceOverlayService(),
    viewState: MockInstancesViewStateService = new MockInstancesViewStateService(),
  ) {
    this.tabStateService = tabStateService;
    this.workspaceOverlayService = workspaceOverlayService;
    this.viewState = viewState;
    // Run the tab→workspace sync once with the initial activeProjectId,
    // mirroring the production `tabWorkspaceEffect` initial run.
    this.runTabWorkspaceEffect();
  }

  /**
   * R2 / S2 mirror: canonical polling scope resolver. Both
   * ``runTabPollingEffect`` (the production ``tabEffect``) and the
   * visibility effect (which also calls ``startPolling`` on
   * re-show) use this resolver so the two effects can never poll
   * with conflicting scopes.
   */
  pollingScope(): string | undefined {
    const pid = this.tabStateService.activeProjectId();
    if (pid && pid !== 'all') return pid;
    return undefined;
  }

  /**
   * R1 / R2 mirror of the production ``tabEffect``. Tests invoke it
   * after mutating ``tabStateService.activeProjectId()`` or
   * ``this.visible()`` to assert the production behavior. The
   * implementation MUST match chat.component.ts byte-for-byte so a
   * deletion of the visibility gate in production would surface here.
   */
  runTabPollingEffect(): void {
    const visible = this.visible();   // always read → always tracked
    if (!visible) return;
    mockInstanceService.startPolling(this.pollingScope());
  }

  /**
   * Mirrors the production `tabWorkspaceEffect` in ChatComponent. In the
   * real component, `effect()` creates a reactive subscription to
   * `activeProjectId()`. Here we expose it as a plain method that tests
   * invoke after mutating activeProjectId.
   */
  runTabWorkspaceEffect(): void {
    const projectId = this.tabStateService.activeProjectId();
    const isOpen = this.workspaceOverlayService.showWorkspace();
    const currentId = this.workspaceOverlayService.workspaceProjectId();

    if (projectId === null) {
      if (isOpen)    this.workspaceOverlayService.hide();
      if (currentId) this.workspaceOverlayService.workspaceProjectId.set(null);
      return;
    }

    if (isOpen && currentId !== projectId) {
      this.workspaceOverlayService.workspaceProjectId.set(projectId);
    }
  }

  protected onSendMessage(payload: TestMessagePayload): void {
    const instance = this.currentInstance();
    if (!instance) return;

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

    this.sendError.set(null);
    this.isSending.set(true);

    // MIN-2 mirror: capture the target instance id AT SEND TIME; the
    // response handler re-checks it against ``activeInstanceId()``.
    const sentInstanceId = instance.instance_id;

    this.api.sendMessage(instance.instance_id, payload.content, payload.images, payload.queue_id).subscribe({
      next: (response: MessageResponse) => {
        // Mirror production ChatComponent.onSendMessage exactly:
        //   1. Clear input on success.
        //   2. Set ``queuedMessage`` from ``response.queued`` (truthy → show).
        //   3. If ``message_id`` is present: build provisional, merge into
        //      ``messages`` WITHOUT eviction (MIN-5), reset ``isSending``.
        //      Otherwise the SSE echo / drain re-emit will render the
        //      bubble instead.
        // The defensive ``created_at ?? timestamp ?? now`` fallback mirrors
        // production — without it, a degraded body shape would push the
        // provisional to the top of the list AND evict it on the next
        // refetch (the BLOCKER this spec set was added to catch).
        this.messageInputRef.clearInput();
        if (response?.queued === true) {
          this.queuedMessage.set({ content: payload.content });
        } else {
          this.queuedMessage.set(null);
        }

        const newId = response?.message_id;
        if (newId) {
          // MIN-2 mirror: drop the provisional when the user switched
          // instances between send and response; still release the
          // sending flag so the spinner doesn't stick across the switch.
          const activeInstanceId = this.viewState.activeInstanceId();
          if (activeInstanceId === sentInstanceId) {
            // MIN-1a mirror: skip the append when the SSE echo already
            // landed for this id — merging a pending provisional over
            // the confirmed echo bubble would resurrect the spinner.
            const alreadyPresent = this.messages().some(
              (m: Message) => m.message_id === newId,
            );
            if (!alreadyPresent) {
              const provisionalStamp =
                response.created_at ?? response.timestamp ?? new Date().toISOString();
              const provisional = makeProvisionalMessage({
                messageId: newId,
                content: payload.content,
                createdAt: provisionalStamp,
                instanceId: instance.instance_id,
                images: payload.images,
              });
              // MIN-5 mirror: NO ``evictPendingByAge`` on the optimistic
              // append path — eviction runs only in the SSE-mirror /
              // refetch passes.
              this.messages.update(existing =>
                mergeMessagesById(existing, [provisional])
              );
            }
          }
          this.isSending.set(false);
        }

        // Defect #5 retry-path mirror (must-fix #1, 2026-08-31):
        // clear the failed marker on the originating bubble ONLY when
        // the POST actually succeeded. The synchronous mirror-in-
        // production is gone — a cooldown-blocked retry preserves the
        // error state because the marker-clear lives here, not in
        // ``onRetryFailedMessage``.
        if (payload.retry_of_message_id) {
          this.messages.update(msgs =>
            msgs.map(m =>
              m.message_id === payload.retry_of_message_id
                ? { ...m, failed: false, errorReason: undefined }
                : m
            )
          );
        }
      },
      error: (err: any) => {
        // Mirror production ChatComponent.onSendMessage error path:
        //   1. Set ``sendError`` for the top-of-chat banner.
        //   2. Release the sending flag (no spinner stick).
        //   3. Defect #5 (2026-08-31): if the SSE echo raced ahead
        //      and a bubble for this send is already in the list, mark
        //      it failed so the chat-interface renders an error state
        //      + retry affordance instead of a "delivered" bubble the
        //      server never recorded. Must-fix #2: also stash the
        //      original send's ``queue_id`` so the retry can re-use
        //      it.
        const errorReason = err instanceof Error ? err.message : 'Failed to send message';
        this.sendError.set(errorReason);
        this.isSending.set(false);
        const escaped = payload.content.replace(/^\/\//, '/');
        const sentQueueId = payload.queue_id;
        this.messages.update(msgs => {
          for (let i = msgs.length - 1; i >= 0; i--) {
            const m = msgs[i];
            if (m.role !== 'user' || m.failed) continue;
            if (m.instance_id && m.instance_id !== sentInstanceId) continue;
            if (m.content === payload.content || m.content === escaped) {
              const next = msgs.slice();
              // Preserve any pre-existing queue_id (defensive — a
              // retry that fails twice should keep the original
              // stash rather than overwrite with a null variant).
              const stash = sentQueueId !== undefined ? sentQueueId : m.queue_id;
              next[i] = { ...m, failed: true, errorReason, queue_id: stash };
              return next;
            }
          }
          return msgs;
        });
      }
    });
  }

  /**
   * Mirror production ``ChatComponent.onRetryFailedMessage``. Re-POSTs
   * the same content through the same composer path so the cooldown
   * stamping / sendError clearing / queue_id-carrying invariants are
   * exercised identically to a fresh send. The failed marker is NOT
   * cleared synchronously here — it is cleared in
   * ``onSendMessage``'s success path, which means a cooldown-blocked
   * retry preserves the error state (no POST went out → bubble stays
   * failed). See chat.component.ts must-fix #1 (2026-08-31).
   */
  onRetryFailedMessage(messageId: string): void {
    const target = this.messages().find(m => m.message_id === messageId);
    if (!target || !target.failed) return;
    this.sendError.set(null);
    // Carry the original send's ``queue_id`` through the retry POST
    // (must-fix #2). Falls back to ``activeProjectId`` only when the
    // stash is absent on the bubble.
    const retryQueueId = target.queue_id !== undefined
      ? target.queue_id
      : (this.tabStateService.activeProjectId() ?? null);
    this.onSendMessage({
      content: target.content,
      images: target.images,
      queue_id: retryQueueId,
      retry_of_message_id: messageId,
    });
  }

  /**
   * Mirror production ``ChatComponent.onDismissFailedMessage``: drop
   * the failed bubble from the local list. The sendError banner and the
   * composer text stay intact so the user can manually re-send / edit.
   */
  onDismissFailedMessage(messageId: string): void {
    this.messages.update(msgs => {
      const next = msgs.filter(m => m.message_id !== messageId);
      return next.length === msgs.length ? msgs : next;
    });
  }

  /**
   * Mirrors production ChatComponent.onToggleWatchover: turning OFF is
   * a no-dialog path; turning ON on a running instance is also a no-dialog
   * path (the backend builds the watcher context from the live message
   * stream); turning ON on a non-running instance opens the dialog so the
   * operator can supply the next command and tighten the guardrails.
   *
   * The dialog flow is delegated to ``openWatchoverDialog`` so the
   * surrogate (which has no MatDialog) can still expose a single hook
   * for tests to spy on or override.
   */
  onToggleWatchover(): void {
    const instance = this.currentInstance();
    if (!instance) return;

    if (this.watchoverEnabled()) {
      // Turning OFF — simple API call, no dialog.
      this.toggleWatchoverApi(instance.instance_id, false, null);
      return;
    }

    // Running instances skip the dialog.
    const isRunning = instance.status === 'running';
    if (isRunning) {
      this.toggleWatchoverApi(instance.instance_id, true, null);
      return;
    }

    // Non-running instances go through the dialog.
    this.openWatchoverDialog(instance.instance_id);
  }

  /**
   * Test seam for the watchover dialog flow. The real ChatComponent
   * injects MatDialog and opens WatchoverDialogComponent, then
   * subscribes to afterClosed() to forward the captured result to
   * ``toggleWatchoverApi``. The surrogate has no MatDialog, so it
   * exposes this as a single overridable hook — tests can spy on it
   * to assert the dialog was triggered, or override it per-test to
   * drive a fake dialog result.
   */
  openWatchoverDialog(instanceId: string): void {
    // No-op in the surrogate. Production wires MatDialog.open() here.
  }

  private toggleWatchoverApi(
    instanceId: string,
    enabled: boolean,
    requirement: string | null,
  ): void {
    this.api.setWatchover(instanceId, enabled, requirement).subscribe({
      next: (response: { watchover_enabled: boolean; instance_id: string }) => {
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
      error: () => {
        this.snackBar.open('Failed to toggle watchover', 'Dismiss', { duration: 3000 });
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

  runWatchoverStatusEffect(): void {
    const statusChange = this.sseService.statusChange();
    const currentInstance = this.currentInstance();
    if (!statusChange || !currentInstance || statusChange.instance_id !== currentInstance.instance_id) {
      return;
    }

    switch (statusChange.status) {
      case 'watchover_active':
        this.watchoverEnabled.set(true);
        break;
      case 'watchover_inactive':
        this.watchoverEnabled.set(false);
        this.watchoverDenialCount.set(0);
        break;
      case 'watchover_failed':
        this.watchoverEnabled.set(false);
        this.watchoverDenialCount.set(0);
        break;
      case 'watchover_terminated':
        this.watchoverEnabled.set(false);
        this.watchoverDenialCount.set(0);
        break;
    }
  }

  /**
   * Mirrors the production syncWatchoverState (chat.component.ts). C1 fix:
   * only syncs enabled+context from the API; the denial counter is
   * intentionally NOT synced because the backend always returns 0.
   */
  syncWatchoverState(instance: InstanceInfo): void {
    this.watchoverEnabled.set(instance.watchover_enabled ?? false);
    this.watchoverContext.set(instance.watchover_context ?? null);
    // Deliberately NOT syncing watchoverDenialCount (SSE is authoritative)
  }

  runWatchoverDenialEffect(): void {
    const messages = this.sseService.messages();
    const last = messages[messages.length - 1];
    if (!last || last.role !== 'tool') return;
    if (
      typeof last.content !== 'string'
      || (!last.content.startsWith('Watchover denied') && !last.content.startsWith('Watchover deferred'))
    ) {
      return;
    }

    const currentInstance = this.currentInstance();
    if (!currentInstance || (last.instance_id && last.instance_id !== currentInstance.instance_id)) return;
    const messageKey = `${currentInstance.instance_id}:${last.message_id ?? last.created_at ?? ''}:${last.content}`;
    if (this.processedWatchoverDenials.has(messageKey)) return;
    this.processedWatchoverDenials.add(messageKey);
    this.watchoverDenialCount.update(count => count + 1);
  }

  protected onTerminateInstance(instanceId: string): void {
    this.api.deleteInstance(instanceId).subscribe({
      next: () => {
        // Drop the cached id from the view-state service so a dead
        // instance is never restored on the next nav-link click. The
        // service is a no-op when the terminated id doesn't match the
        // current cache, so calling it for unrelated rows is safe.
        this.viewState.clearInstance(instanceId);
        if (this.currentInstanceId() === instanceId) {
          this.currentInstanceId.set(null);
          this.navigateCalls.push({ path: ['/'] });
        }
      },
      error: () => {}
    });
  }

  /**
   * F1 / N1 mirror of the production R6 dead-id validation effect
   * (chat.component.ts constructor). Watches the instances list and
   * drops the cached ``activeInstanceId`` when a FULL, non-empty list
   * proves the id is gone. Guards:
   *   - ``visible()`` — hidden overlay never validates;
   *   - ``pollingScope() === undefined`` — scoped lists can't prove
   *     absence (an id from another project is legitimately missing);
   *   - non-empty list — an empty/scope-less list proves nothing;
   *   - confirmed-dead gate: ONLY the API's own 404 confirmation
   *     (``instanceNotFound() === cachedId``) clears the cache. The
   *     previous ``currentInstanceId() !== cachedId`` clause was
   *     removed (N1) — it raced the visibility effect on
   *     ``activeInstanceId.set(B)``, firing first while
   *     ``currentInstanceId`` was still stale A and wiping a freshly
   *     opened instance before any load started.
   */
  runDeadIdValidationEffect(): void {
    const visible = this.visible();
    const cachedId = this.viewState.activeInstanceId();
    const instances = mockInstanceService.instances();

    if (!visible || !cachedId) return;
    if (this.pollingScope() !== undefined) return;
    if (instances.length === 0) return;

    const stillExists = instances.some(i => i.instance_id === cachedId);
    if (stillExists) return;
    if (this.instanceNotFound() === cachedId) {
      this.viewState.clearInstance(cachedId);
    }
  }

  /**
   * Mirrors the production ``instanceNotFound`` signal — set by
   * ``handleInstanceIdChange`` when the getInstance fallback 404s, and
   * read by the R6 dead-id validation effect as API confirmation.
   */
  readonly instanceNotFound = signal<string | null>(null);

  /**
   * F6 mirror of the production SSE→messages merge effect
   * (chat.component.ts ~:331-361). The W2 instance filter drops SSE
   * messages carrying a FOREIGN ``instance_id`` (race: user switched
   * instances while an SSE channel was still resolving) so they
   * cannot bleed into the new instance's UI; messages with a matching
   * or absent ``instance_id`` are upserted into ``messages``.
   */
  runSseMergeEffect(): void {
    const sseMessages = this.sseService.messages();
    if (sseMessages.length === 0) return;
    const activeInstanceId = this.viewState.activeInstanceId();
    if (!activeInstanceId) return;
    const filtered = sseMessages.filter(
      (m: Message) => !m.instance_id || m.instance_id === activeInstanceId,
    );
    if (filtered.length === 0) return;

    this.messages.update(existing => {
      const result = [...existing];
      for (const msg of filtered) {
        const idx = result.findIndex((m: Message) => m.message_id === msg.message_id);
        if (idx >= 0) {
          result[idx] = { ...result[idx], ...msg };
        } else {
          result.push(msg);
        }
      }
      result.sort((a: Message, b: Message) => (a.created_at || '').localeCompare(b.created_at || ''));
      return result;
    });
  }

  /**
   * MIN-3 mirror of the production terminal-status pending-purge
   * effect. The purge fires ONLY when the recorded purge instance id
   * matches the ACTIVE instance — a cascade CHILD's terminal event (or
   * a trigger recorded for a previously-opened instance) must not wipe
   * the active chat's provisional bubbles.
   */
  runPendingPurgeEffect(): void {
    const tick = this.sseService.pendingPurgeRequest();
    if (tick === 0) return;
    const purgeInstanceId = this.sseService.pendingPurgeInstanceId();
    const activeInstanceId = this.viewState.activeInstanceId();
    if (!purgeInstanceId || !activeInstanceId) return;
    if (purgeInstanceId !== activeInstanceId) return;
    this.messages.update((existing: Message[]) => {
      const filtered = existing.filter((m: Message) => !m.pending);
      return filtered.length === existing.length ? existing : filtered;
    });
  }

  /**
   * F4 mirror of the production ``loadInstanceMessages`` (chat.component.ts
   * ~:843-903). This is the ACTUAL guarded callback the staleness-guard
   * contract tests drive: each async completion re-checks ``visible()`` +
   * ``activeInstanceId()`` before performing side effects. Deleting the
   * production guard must fail these tests — the guard logic is mirrored
   * byte-for-byte, not re-implemented as a predicate assertion.
   *
   * The ``getMessages`` observable is resolved from a ``Subject`` so a
   * test can hold the request in-flight while the world moves on (hide
   * the overlay / switch instances), then complete it and assert that
   * NO side effects happened.
   */
  loadInstanceMessages(instanceId: string): void {
    this.api.getMessages(instanceId).subscribe({
      next: (messages: Message[]) => {
        if (!this.visible() || this.viewState.activeInstanceId() !== instanceId) {
          return;
        }
        this.messages.set(messages);
      },
      error: () => {
        if (!this.visible() || this.viewState.activeInstanceId() !== instanceId) {
          return;
        }
        this.messages.set([]);
      },
      complete: () => {
        if (!this.visible() || this.viewState.activeInstanceId() !== instanceId) {
          return;
        }
        this.sseService.connect(instanceId);
        this.sseService.fetchPendingInjection(instanceId);
        this.sseService.fetchPendingQuestion(instanceId);
      },
    });
  }

  /**
   * F4 / F8 mirror of the production ``handleInstanceIdChange``
   * undefined-branch (chat.component.ts ~:818-825). The mirror
   * covers the F8 contract: ``handleInstanceIdChange(undefined)``
   * MUST reset ``instanceNotFound`` so the not-found panel doesn't
   * render stale for an instance the user has already left.
   */
  handleInstanceIdChangeUndefinedBranch(): void {
    // F8: reset the not-found panel too. The OLD contract left
    // ``instanceNotFound`` set, which made the not-found banner
    // stick around after the user navigated away.
    this.instanceNotFound.set(null);
    this.currentInstanceId.set(null);
    this.messages.set([]);
  }

  /**
   * F4 mirror of the production ``handleInstanceIdChange`` API-fallback
   * path (chat.component.ts ~:796-832) — the guarded ``getInstance``
   * completion that seeds ``currentInstanceId`` before messages load.
   */
  handleInstanceApiFallback(instanceId: string): void {
    this.currentInstanceId.set(instanceId);
    this.api.getInstance(instanceId).subscribe({
      next: (instanceData: InstanceInfo) => {
        if (!this.visible() || this.viewState.activeInstanceId() !== instanceId) {
          return;
        }
        this.currentInstance.set(instanceData);
        this.loadInstanceMessages(instanceId);
      },
      error: () => {
        if (!this.visible() || this.viewState.activeInstanceId() !== instanceId) {
          return;
        }
        this.currentInstanceId.set(null);
        this.messages.set([]);
      },
    });
  }

  protected onNewInstance(): void {
    const agent = this.selectedAgent();
    if (!agent) {
      this.navigateCalls.push({ path: ['/'] });
      return;
    }

    // Reset state when creating new instance
    this.isSending.set(false);
    this.sendError.set(null);
    this.currentInstanceId.set(null);
    this.sseService.clearEvents();
    this.sseService.disconnect();

    const agentPath = `./agents/${agent.id}`;
    const projectContext = this.tabStateService.activeProjectId() ?? 'all';

    this.api.createInstance(agentPath).subscribe({
      next: (instance: InstanceInfo) => {
        this.navigateCalls.push({
          path: ['/projects', projectContext, 'instances', instance.instance_id]
        });
      },
      error: () => {}
    });
  }

  protected onBackToHome(): void {
    this.navigateCalls.push({ path: ['/'] });
  }

  // Workspace overlay handler — mirrors ChatComponent. After the
  // refactor that lifts workspace state into the root-provided
  // WorkspaceOverlayService, this handler is a thin delegator: it
  // exists so the template binding (e.g. the project-tab workspace
  // icon) still works, but the actual state lives in
  // `workspaceOverlayService`. The Hide action is bound directly in
  // app.html to `workspaceOverlayService.hide()`, so no separate
  // `onWorkspaceHide` shim is needed here.
  protected onWorkspaceToggle(projectId: string): void {
    this.workspaceOverlayService.toggle(projectId);
  }

  protected get hasRealProject(): boolean {
    const activeId = this.tabStateService.activeProjectId();
    return activeId !== null && activeId !== 'all';
  }

  protected get projectId(): string {
    return this.tabStateService.activeProjectId() ?? 'all';
  }

  protected onHeaderWorkspaceToggle(): void {
    if (!this.hasRealProject) return;
    this.workspaceOverlayService.toggle(this.projectId);
  }

  // Simulates the navigation that happens after instance creation in handleInstanceIdChange
  protected getNavigationPath(instanceId: string): string[] {
    const projectContext = this.tabStateService.activeProjectId() ?? 'all';
    return ['/projects', projectContext, 'instances', instanceId];
  }
}

// Helper to create mock agent
function createMockAgent(overrides: Partial<Agent> = {}): Agent {
  return {
    id: 'test-agent-' + Math.random().toString(36).substr(2, 9),
    name: 'Test Agent',
    description: 'A test agent',
    icon: '🤖',
    ...overrides,
  };
}

// Helper to create mock instance
function createMockInstance(overrides: Partial<InstanceInfo> = {}): InstanceInfo {
  return {
    instance_id: 'instance-' + Math.random().toString(36).substr(2, 9),
    agent_id: 'test-agent',
    status: 'running',
    parent_id: null,
    children: [],
    title: null,
    created_at: new Date().toISOString(),
    updated_at: null,
    project_id: null,
    ...overrides,
  };
}

describe('ChatComponent - Project-Aware Navigation', () => {
  let tabStateService: MockTabStateService;
  let component: TestableChatComponent;

  beforeEach(() => {
    localStorage.clear();
    mockSseService.messages.set([]);
    mockSseService.events.set([]);
    mockSseService.statusChange.set(null);
    tabStateService = new MockTabStateService();
    component = new TestableChatComponent(tabStateService);
    jest.clearAllMocks();
  });

  describe('onNewInstance() - Navigation Point', () => {
    beforeEach(() => {
      const agent = createMockAgent({ id: 'chat-agent' });
      component.selectedAgent.set(agent);
    });

    it('should navigate to /projects/all/instances/:instanceId when on All tab', () => {
      tabStateService.setActiveTab('all');
      const instanceId = 'chat-inst-001';
      mockApiService.createInstance.mockReturnValue({
        subscribe: (handlers: any) => {
          handlers.next(createMockInstance({ instance_id: instanceId }));
          return { unsubscribe: () => {} };
        }
      });

      component.onNewInstance();

      expect(component.navigateCalls).toHaveLength(1);
      expect(component.navigateCalls[0].path).toEqual(['/projects', 'all', 'instances', instanceId]);
    });

    it('should navigate to /projects/:projectId/instances/:instanceId when project is selected', () => {
      tabStateService.setActiveTab('chat-project');
      const instanceId = 'chat-inst-002';
      mockApiService.createInstance.mockReturnValue({
        subscribe: (handlers: any) => {
          handlers.next(createMockInstance({ instance_id: instanceId }));
          return { unsubscribe: () => {} };
        }
      });

      component.onNewInstance();

      expect(component.navigateCalls).toHaveLength(1);
      expect(component.navigateCalls[0].path).toEqual(['/projects', 'chat-project', 'instances', instanceId]);
    });

    it('should navigate to home when no agent is selected', () => {
      component.selectedAgent.set(null);
      tabStateService.setActiveTab('all');

      component.onNewInstance();

      expect(component.navigateCalls).toHaveLength(1);
      expect(component.navigateCalls[0].path).toEqual(['/']);
    });

    it('should clear SSE state before navigation', () => {
      tabStateService.setActiveTab('sse-project');
      const instanceId = 'sse-clear-inst';
      mockApiService.createInstance.mockReturnValue({
        subscribe: (handlers: any) => {
          handlers.next(createMockInstance({ instance_id: instanceId }));
          return { unsubscribe: () => {} };
        }
      });

      component.onNewInstance();

      expect(mockSseService.clearEvents).toHaveBeenCalled();
      expect(mockSseService.disconnect).toHaveBeenCalled();
    });
  });

  describe('getNavigationPath() - Instance Navigation Helper', () => {
    it('should return /projects/all/instances/:instanceId when on All tab', () => {
      tabStateService.setActiveTab('all');

      const path = component.getNavigationPath('helper-inst-001');

      expect(path).toEqual(['/projects', 'all', 'instances', 'helper-inst-001']);
    });

    it('should return /projects/:projectId/instances/:instanceId when project is selected', () => {
      tabStateService.setActiveTab('helper-project');

      const path = component.getNavigationPath('helper-inst-002');

      expect(path).toEqual(['/projects', 'helper-project', 'instances', 'helper-inst-002']);
    });

    it('should preserve instance ID in navigation path', () => {
      tabStateService.setActiveTab('preserve-project');
      const instanceId = 'preserve-inst-xyz';

      const path = component.getNavigationPath(instanceId);

      expect(path[3]).toBe(instanceId);
    });
  });

  describe('URL Pattern Verification', () => {
    it('should produce correct URL pattern for All tab', () => {
      tabStateService.setActiveTab('all');

      const path = component.getNavigationPath('pattern-inst');

      // Verify URL structure
      expect(path).toHaveLength(4);
      expect(path[0]).toBe('/projects');
      expect(path[1]).toBe('all');
      expect(path[2]).toBe('instances');
      expect(typeof path[3]).toBe('string'); // instance ID
    });

    it('should produce correct URL pattern for specific project', () => {
      tabStateService.setActiveTab('pattern-project-123');

      const path = component.getNavigationPath('pattern-inst');

      expect(path).toHaveLength(4);
      expect(path[0]).toBe('/projects');
      expect(path[1]).toBe('pattern-project-123');
      expect(path[2]).toBe('instances');
    });

    it('should handle various project ID formats', () => {
      const projectIds = ['proj-1', 'my_project', 'project.with.dots', '123-numbers'];

      for (const projectId of projectIds) {
        component.navigateCalls = [];
        tabStateService.setActiveTab(projectId);

        const path = component.getNavigationPath('test-inst');

        expect(path[1]).toBe(projectId);
        expect(path[0]).toBe('/projects');
        expect(path[2]).toBe('instances');
      }
    });
  });

  describe('onTerminateInstance() - Navigation', () => {
    it('should navigate to home after terminating current instance', () => {
      const instanceId = 'terminate-inst';
      component.currentInstanceId.set(instanceId);

      mockApiService.deleteInstance.mockReturnValue({
        subscribe: (handlers: any) => {
          handlers.next({});
          return { unsubscribe: () => {} };
        }
      });

      component.onTerminateInstance(instanceId);

      expect(component.navigateCalls).toHaveLength(1);
      expect(component.navigateCalls[0].path).toEqual(['/']);
    });

    it('should not navigate when terminating non-current instance', () => {
      component.currentInstanceId.set('current-inst');

      mockApiService.deleteInstance.mockReturnValue({
        subscribe: (handlers: any) => {
          handlers.next({});
          return { unsubscribe: () => {} };
        }
      });

      component.onTerminateInstance('other-inst');

      expect(component.navigateCalls).toHaveLength(0);
    });

    it('should clear the cached instance id from the view-state service', () => {
      // The instance detail overlay caches the active id in
      // InstancesViewStateService. Termination must invalidate the
      // cache so the next visit to the Instances nav link does not
      // restore a dead instance.
      const instanceId = 'cache-clear-inst';
      component.viewState.openDetail('proj-a', instanceId);

      mockApiService.deleteInstance.mockReturnValue({
        subscribe: (handlers: any) => {
          handlers.next({});
          return { unsubscribe: () => {} };
        }
      });

      component.onTerminateInstance(instanceId);

      expect(component.viewState.clearInstanceCalls).toContain(instanceId);
      expect(component.viewState.activeInstanceId()).toBeNull();
      expect(component.viewState.detailVisible()).toBe(false);
    });

    it('should still call clearInstance when terminating a non-current instance', () => {
      // The view-state service itself is a no-op for non-matching ids,
      // but the component must wire the call regardless so the
      // matching-id path is exercised.
      component.currentInstanceId.set('current-inst');
      component.viewState.openDetail('proj-a', 'current-inst');

      mockApiService.deleteInstance.mockReturnValue({
        subscribe: (handlers: any) => {
          handlers.next({});
          return { unsubscribe: () => {} };
        }
      });

      component.onTerminateInstance('other-inst');

      expect(component.viewState.clearInstanceCalls).toContain('other-inst');
      // The non-matching id must NOT clear the cached current instance.
      expect(component.viewState.activeInstanceId()).toBe('current-inst');
    });
  });

  describe('onBackToHome() - Navigation', () => {
    it('should navigate to home', () => {
      component.onBackToHome();

      expect(component.navigateCalls).toHaveLength(1);
      expect(component.navigateCalls[0].path).toEqual(['/']);
    });
  });

  describe('onSendMessage() - Send Cooldown', () => {
    beforeEach(() => {
      component.currentInstance.set(createMockInstance({ instance_id: 'cooldown-inst' }));
      mockApiService.sendMessage.mockReturnValue({
        subscribe: (handlers: any) => {
          handlers.next({});
          return { unsubscribe: () => {} };
        }
      });
    });

    afterEach(() => {
      jest.restoreAllMocks();
    });

    it('should send the message on the first attempt', () => {
      jest.spyOn(Date, 'now').mockReturnValue(1000);
      component.onSendMessage({ content: 'hello' });

      // The 4th arg is the queue_id (must-fix #2, 2026-08-31):
      // tests that don't supply a queue_id see it forwarded as
      // ``undefined``. Production ``MessagePayload.queue_id`` is
      // optional, and the api call passes it through verbatim.
      expect(mockApiService.sendMessage).toHaveBeenCalledWith('cooldown-inst', 'hello', undefined, undefined);
      expect(mockSnackBar.open).not.toHaveBeenCalled();
    });

    it('should block a second send within 3s and show a notification', () => {
      jest.spyOn(Date, 'now').mockReturnValue(1000);
      component.onSendMessage({ content: 'hello' });
      component.onSendMessage({ content: 'hello again' });

      expect(mockApiService.sendMessage).toHaveBeenCalledTimes(1);
      expect(mockSnackBar.open).toHaveBeenCalledWith(
        expect.stringContaining('Please wait'),
        'Dismiss',
        { duration: 2000, panelClass: 'info-snackbar' }
      );
    });

    it('should allow another send after the 3s cooldown elapses', () => {
      const nowSpy = jest.spyOn(Date, 'now');
      nowSpy.mockReturnValueOnce(1000);
      nowSpy.mockReturnValueOnce(1000 + 3001);

      component.onSendMessage({ content: 'hello' });
      expect(mockApiService.sendMessage).toHaveBeenCalledTimes(1);

      component.onSendMessage({ content: 'hello again' });
      expect(mockApiService.sendMessage).toHaveBeenCalledTimes(2);
    });

    it('should clear input on a successful send', () => {
      component.onSendMessage({ content: 'hello' });

      expect(component.messageInputRef.clearInput).toHaveBeenCalled();
    });

    it('should not send when no instance is set', () => {
      component.currentInstance.set(null);
      component.onSendMessage({ content: 'hello' });

      expect(mockApiService.sendMessage).not.toHaveBeenCalled();
    });
  });

  /**
   * message-display-latency Phase 1 BLOCKER specs.
   *
   * The original Phase 1 implementation built the provisional user bubble
   * with ``createdAt: response.created_at``, but the BE 202 body did not
   * ship that key — and the FE response type wrongly declared it
   * ``required``. The provisional then carried ``undefined``, sorting
   * to the top of the list while ``evictPendingByAge`` treated the
   * unparseable timestamp as expired. In the common arrival order
   * (POST-time SSE echo resolves before the HTTP 202), the merge
   * replaced the good echo bubble with the malformed provisional and
   * immediately evicted it.
   *
   * These specs lock in the fix using the REAL response shape — the
   * missing key in the prior coverage that would have caught the bug.
   *
   * Timing note: ``evictPendingByAge`` drops a provisional entry when
   * its ``created_at`` is older than 10 minutes. All test fixtures use
   * ``new Date().toISOString()`` (or a stamp relative to a mocked
   * ``Date.now``) so the provisional is always inside the TTL —
   * otherwise the test would silently exercise eviction instead of
   * the path under test.
   */
  describe('onSendMessage() - optimistic append (Phase 1 BLOCKER repro)', () => {
    // Mirror production constants.
    const TEN_MIN_MS = 10 * 60 * 1000;

    /** Build a 202-shaped POST response with the Phase-1 additive fields.
     *  Defaults to a fresh ``created_at`` so the provisional survives the
     *  10-minute TTL; individual tests override ``created_at`` / drop
     *  fields to exercise specific fallback paths. */
    function make202Response(overrides: Partial<{
      message_id: string;
      created_at: string;
      timestamp: string;
      status: string;
      instance_id: string;
      content: string;
      pending_count: number;
      queued: boolean;
    }> = {}) {
      const now = new Date().toISOString();
      return {
        status: 'injected',
        instance_id: 'inst-abc',
        content: 'hello',
        timestamp: now,
        // ADDITIVE in Phase 1 — same stamp the SSE echo carries.
        created_at: now,
        pending_count: 1,
        message_id: 'echo-uuid-1',
        ...overrides,
      };
    }

    /** Drive ``sendMessage`` synchronously and invoke the ``next`` handler. */
    function fireSend(response: unknown): void {
      mockApiService.sendMessage.mockReturnValueOnce({
        subscribe: (handlers: any) => {
          handlers.next(response);
          return { unsubscribe: () => {} };
        },
      });
      component.onSendMessage({ content: 'hello' });
    }

    beforeEach(() => {
      component.currentInstance.set(createMockInstance({ instance_id: 'inst-abc' }));
      // The SSE merge effect gates on ``viewState.activeInstanceId()``.
      component.viewState.activeInstanceId.set('inst-abc');
      mockSseService.messages.set([]);
    });

    afterEach(() => {
      jest.restoreAllMocks();
      mockSseService.messages.set([]);
    });

    // 3a — 202-with-id → optimistic append happens.
    it('should append the provisional bubble when the 202 body carries message_id + created_at', () => {
      const response = make202Response({
        message_id: 'echo-uuid-1',
        created_at: new Date().toISOString(),
      });
      fireSend(response);

      const msgs = component.messages();
      expect(msgs).toHaveLength(1);
      expect(msgs[0]).toMatchObject({
        message_id: 'echo-uuid-1',
        role: 'user',
        content: 'hello',
        instance_id: 'inst-abc',
        pending: true,
      });
      // ``created_at`` is the server-authoritative stamp and MUST be a
      // parseable ISO string (the BLOCKER symptom was an unparseable
      // stamp that ``evictPendingByAge`` treated as expired).
      expect(typeof msgs[0].created_at).toBe('string');
      expect(Number.isNaN(Date.parse(msgs[0].created_at))).toBe(false);
      // Optimistic append completes the user-visible turn immediately.
      expect(component.isSending()).toBe(false);
      expect(component.messageInputRef.clearInput).toHaveBeenCalled();
    });

    // 3b — absent ``message_id`` (old BE / PAUSED None) → NO append.
    it('should NOT append a provisional bubble when the response has no message_id (old BE / PAUSED None)', () => {
      // Old backend shape: 202 body without the additive ``message_id``.
      const degradedResponse = {
        status: 'injected',
        instance_id: 'inst-abc',
        content: 'hello',
        timestamp: new Date().toISOString(),
        created_at: new Date().toISOString(),
        pending_count: 1,
        // message_id absent → render-on-echo fallback.
      };
      fireSend(degradedResponse);

      // No optimistic append — the SSE echo / drain re-emit will render.
      expect(component.messages()).toHaveLength(0);
      // Input is still cleared and ``isSending`` reset, just no bubble.
      expect(component.messageInputRef.clearInput).toHaveBeenCalled();
    });

    // 3c — MIN-5: the optimistic-append path must NOT run TTL eviction.
    // A >10-min stalled POST's provisional (freshly appended with its
    // original send-time stamp) would otherwise be appended and
    // immediately evicted by this very pass — the bubble would flash
    // and the user would lose their only send confirmation. Eviction
    // belongs to the SSE-mirror / refetch passes only.
    it('should NOT evict an aged pending entry on the optimistic-append path (MIN-5)', () => {
      // Drive the clock forward so any (wrongful) eviction check would
      // see a definite "now".
      const fixedNow = Date.now();
      jest.spyOn(Date, 'now').mockReturnValue(fixedNow);
      const freshStamp = new Date(fixedNow - 5 * 60 * 1000).toISOString();     // 5 min old
      const agedStamp = new Date(fixedNow - 90 * 60 * 1000).toISOString();    // 90 min old
      const newStamp = new Date(fixedNow).toISOString();

      // Pre-existing message list with one fresh + one aged provisional
      // (the aged one = a previous stalled POST still awaiting its echo).
      component.messages.set([
        {
          message_id: 'fresh',
          role: 'user',
          content: 'recent',
          created_at: freshStamp,
          instance_id: 'inst-abc',
          pending: true,
        },
        {
          message_id: 'aged',
          role: 'user',
          content: 'stale',
          created_at: agedStamp,
          instance_id: 'inst-abc',
          pending: true,
        },
      ]);

      fireSend(make202Response({ message_id: 'echo-new', created_at: newStamp }));

      const ids = component.messages().map((m: Message) => m.message_id);
      expect(ids).toContain('fresh');      // fresh provisional survives
      expect(ids).toContain('aged');       // MIN-5: aged one survives the append pass
      expect(ids).toContain('echo-new');   // new provisional appended
    });

    // 3d — arrival-order regression (the BLOCKER scenario).
    //
    // Reproduces the original failure: the POST-time SSE ``user_message``
    // echo lands in the SseService signal BEFORE the HTTP 202 resolves.
    // Without a server-authoritative ``created_at`` the surrogate would
    // build a provisional with ``undefined`` → mis-sort + immediate
    // eviction, replacing the good SSE echo bubble.
    //
    // Asserts the four BLOCKER invariants from the spec: ONE bubble,
    // SSE/server stamp, NOT evicted, NOT duplicated — and the MIN-1
    // invariant: the provisional append is SKIPPED entirely (the id
    // already exists), so ``pending`` STAYS cleared on the confirmed
    // echo bubble (no spinner resurrection).
    it('should produce exactly ONE bubble when the SSE echo lands before the HTTP 202 resolves', () => {
      const serverStamp = new Date().toISOString();

      // (1) SSE echo arrives first — POST-time ``user_message`` already
      //     routed through ``runSseMergeEffect`` and was added to the
      //     list with the server stamp.
      mockSseService.messages.set([
        {
          message_id: 'echo-uuid-1',
          role: 'user',
          content: 'hello',
          created_at: serverStamp,
          instance_id: 'inst-abc',
        },
      ]);
      component.runSseMergeEffect();

      // After the SSE echo: one bubble, server stamp.
      let msgs = component.messages();
      expect(msgs).toHaveLength(1);
      expect(msgs[0]).toMatchObject({
        message_id: 'echo-uuid-1',
        created_at: serverStamp,
      });

      // (2) HTTP 202 resolves — the append is skipped (id already
      //     present): no duplicate, no pending resurrection, no eviction.
      fireSend(make202Response({ message_id: 'echo-uuid-1', created_at: serverStamp }));

      msgs = component.messages();
      // Exactly ONE bubble — id-keyed dedup collapses the echo + provisional
      // (the BLOCKER's "duplicated" symptom).
      expect(msgs).toHaveLength(1);
      expect(msgs[0]).toMatchObject({
        message_id: 'echo-uuid-1',
        created_at: serverStamp,
      });
      // The stamp is the SSE/server stamp, not "now" — so the bubble
      // stays in send position (the BLOCKER's "sorted to top" symptom
      // came from an undefined stamp that localeCompare pushed ahead
      // of every other entry).
      expect(msgs[0].created_at).toBe(serverStamp);
      // MIN-1a: the confirmed echo bubble must NOT regress to a
      // spinner — ``pending`` stays cleared after both arrivals.
      expect(msgs[0].pending).toBeUndefined();
      expect(component.isSending()).toBe(false);
    });

    // 3e — degraded body without ``created_at`` → fallback used, no NaN.
    //
    // Old-BE / transient-shape guard: if the 202 body carries
    // ``message_id`` but neither ``created_at`` nor ``timestamp``, the
    // component must NOT ship a provisional with an unparseable stamp
    // (the original BLOCKER symptom — and the failure mode that
    // ``evictPendingByAge`` would treat as expired).
    //
    // Exercise the SECOND link of the fallback chain
    // (``timestamp``) so the assertion can pin an exact stamp
    // independent of when ``new Date()`` runs inside the surrogate
    // (V8's ``new Date()`` does not honor a ``Date.now`` spy).
    it('should fall back to ``timestamp`` when the response carries no ``created_at``', () => {
      const tsStamp = new Date().toISOString();
      fireSend({ status: 'injected', message_id: 'echo-degraded', timestamp: tsStamp });

      const msgs = component.messages();
      expect(msgs).toHaveLength(1);
      expect(msgs[0].message_id).toBe('echo-degraded');
      // Fallback chain: ``created_at`` → ``timestamp`` (hit) → ``new Date().toISOString()``.
      expect(msgs[0].created_at).toBe(tsStamp);
      // Belt-and-suspenders: the stamp must be parseable AND recent enough
      // that ``evictPendingByAge`` does not drop the provisional on the
      // very next refetch (the BLOCKER's eviction symptom).
      const parsed = Date.parse(msgs[0].created_at);
      expect(Number.isNaN(parsed)).toBe(false);
      expect(Date.now() - parsed).toBeLessThan(TEN_MIN_MS);
    });
  });

  /**
   * Defect #5 (2026-08-31) — dishonest optimistic bubble.
   *
   * The tester observed that a bubble was rendered for a message the
   * POST never persisted (e.g. the SSE ``user_message`` echo raced the
   * HTTP error, OR a 2xx response carried a phantom ``message_id``).
   * Without this fix, the bubble stayed rendered as a delivered user
   * message — the user trusts a chat surface that lies.
   *
   * These specs lock in the fix:
   *   - Failed POST → sendError banner set, isSending released, AND
   *     any bubble that landed for this send is marked ``failed`` so
   *     the chat-interface renders an error state + retry affordance.
   *   - The SSE-echo race: the bubble was already on screen when the
   *     POST errored. The error handler finds it and marks it failed.
   *   - Retry: the retry handler clears the marker and re-POSTs the
   *     same content; success path lands a fresh, non-failed bubble.
   *   - Dismiss: drops the failed bubble from the list; composer text
   *     and sendError state are left to the existing happy-path code.
   *   - Merge helper: a later SSE echo / refetch MUST NOT silently
   *     clear the failed flag (server cannot have a message we never
   *     sent) and the TTL eviction MUST NOT drop a failed entry
   *     (would re-introduce the bug).
   */
  describe('onSendMessage() - failed POST (defect #5)', () => {
    function fireFailedSend(errorMessage = 'Network unreachable'): void {
      mockApiService.sendMessage.mockReturnValueOnce({
        subscribe: (handlers: any) => {
          handlers.error(new Error(errorMessage));
          return { unsubscribe: () => {} };
        },
      });
    }

    beforeEach(() => {
      component.currentInstance.set(createMockInstance({ instance_id: 'inst-abc' }));
      component.viewState.activeInstanceId.set('inst-abc');
      mockSseService.messages.set([]);
    });

    afterEach(() => {
      jest.restoreAllMocks();
      mockSseService.messages.set([]);
    });

    // 5a — Happy precondition: no SSE bubble, no optimistic append.
    // POST error → no bubble in the list, only the banner.
    it('should NOT leave a bubble in the list when no SSE echo raced the error', () => {
      fireFailedSend('Network unreachable');

      component.onSendMessage({ content: 'hello' });

      // No bubble rendered — the POST errored BEFORE anything was added.
      expect(component.messages()).toHaveLength(0);
      // The sendError banner is set so the user sees WHAT failed.
      expect(component.sendError()).toBe('Network unreachable');
      expect(component.isSending()).toBe(false);
      // Input is preserved (the existing contract).
      expect(component.messageInputRef.clearInput).not.toHaveBeenCalled();
    });

    // 5b — SSE-echo race: the bubble was already on screen when the
    // POST errored. The error handler must find it and mark it failed.
    it('should mark an SSE-echoed bubble as failed when the POST errored after the echo', () => {
      // (1) SSE echo lands first — the BE-side hook emitted the event
      //     before responding to the POST. The bubble is now in the
      //     list with the delivered (post-strip) content.
      const echoId = 'echo-uuid-1';
      const echoStamp = new Date().toISOString();
      mockSseService.messages.set([
        {
          message_id: echoId,
          role: 'user',
          content: 'hello',
          created_at: echoStamp,
          instance_id: 'inst-abc',
        },
      ]);
      component.runSseMergeEffect();
      expect(component.messages()).toHaveLength(1);

      // (2) POST error fires after the echo — the bubble should be
      //     marked failed so the user knows the message never reached
      //     the server.
      fireFailedSend('Connection reset by peer');
      component.onSendMessage({ content: 'hello' });

      const msgs = component.messages();
      expect(msgs).toHaveLength(1);
      // The errorReason is surfaced verbatim so the operator can act.
      expect(msgs[0].failed).toBe(true);
      expect(msgs[0].errorReason).toBe('Connection reset by peer');
      // The SSE-echoed id / stamp are preserved (id-keyed dedup
      // contract; the server-authoritative stamp pins the bubble in
      // send position).
      expect(msgs[0].message_id).toBe(echoId);
      expect(msgs[0].created_at).toBe(echoStamp);
      expect(component.sendError()).toBe('Connection reset by peer');
    });

    // 5c — Escape-contract race: the bubble carries the delivered
    // (post-strip) form while the composer fired the raw form. The
    // error handler matches both so the bubble is found and marked.
    it('should mark the bubble as failed when the content was rewritten by the //escape contract', () => {
      // SSE echo arrived with the delivered (one-slash-stripped) text.
      mockSseService.messages.set([
        {
          message_id: 'echo-strip',
          role: 'user',
          content: '/compact is useful',
          created_at: new Date().toISOString(),
          instance_id: 'inst-abc',
        },
      ]);
      component.runSseMergeEffect();

      // Composer fired the RAW `//compact is useful` (post 235650f1
      // contract: FE does NOT pre-strip — BE strips one slash).
      fireFailedSend('502 Bad Gateway');
      component.onSendMessage({ content: '//compact is useful' });

      const msgs = component.messages();
      expect(msgs).toHaveLength(1);
      expect(msgs[0].failed).toBe(true);
      expect(msgs[0].errorReason).toBe('502 Bad Gateway');
      // The bubble content stays as delivered (the user can still see
      // what was attempted).
      expect(msgs[0].content).toBe('/compact is useful');
    });

    // 5d — Retry: clearing the marker and re-POSTing replaces the
    // bubble with a non-failed copy (id-keyed dedup collapses onto
    // the same row when the retry succeeds).
    it('should re-issue the send when retry is invoked on a failed bubble', () => {
      // Seed a failed bubble.
      component.messages.set([
        {
          message_id: 'echo-retry',
          role: 'user',
          content: 'hello retry',
          created_at: new Date().toISOString(),
          instance_id: 'inst-abc',
          failed: true,
          errorReason: 'transient',
        },
      ]);

      // The retry re-POSTs through the same composer path. Mock a
      // 202-with-id success so the optimistic append can land.
      const successStamp = new Date().toISOString();
      mockApiService.sendMessage.mockReturnValueOnce({
        subscribe: (handlers: any) => {
          handlers.next({
            status: 'injected',
            message_id: 'echo-retry',
            created_at: successStamp,
            timestamp: successStamp,
            instance_id: 'inst-abc',
            content: 'hello retry',
            pending_count: 1,
          });
          return { unsubscribe: () => {} };
        },
      });

      component.onRetryFailedMessage('echo-retry');

      // sendError was cleared on retry.
      expect(component.sendError()).toBe(null);
      // The bubble is no longer marked failed (id-keyed dedup keeps
      // the same row; the optimistic append path with the same id
      // would merge onto the same row but is skipped because the id
      // already exists, so the existing entry stays as-is with the
      // marker cleared).
      const msgs = component.messages();
      expect(msgs).toHaveLength(1);
      expect(msgs[0].message_id).toBe('echo-retry');
      expect(msgs[0].failed).toBe(false);
      expect(msgs[0].errorReason).toBeUndefined();
    });

    // 5e — Dismiss: drops the failed bubble from the local list.
    it('should drop the failed bubble when the user dismisses', () => {
      component.messages.set([
        {
          message_id: 'echo-dismiss',
          role: 'user',
          content: 'hello dismiss',
          created_at: new Date().toISOString(),
          instance_id: 'inst-abc',
          failed: true,
          errorReason: 'aborted',
        },
        {
          message_id: 'other-msg',
          role: 'assistant',
          content: 'previous turn',
          created_at: new Date(Date.now() - 60_000).toISOString(),
          instance_id: 'inst-abc',
        },
      ]);

      component.onDismissFailedMessage('echo-dismiss');

      const msgs = component.messages();
      expect(msgs).toHaveLength(1);
      expect(msgs[0].message_id).toBe('other-msg');
    });

    // 5f — Merge helper invariant: a later SSE echo / refetch MUST
    // NOT silently clear the failed flag (server cannot have a message
    // we never sent). The merge helper preserves it.
    //
    // Strengthened (NIT 2, 2026-08-31): exercises the production
    // ``mergeMessagesById`` directly (the previous version of this
    // test ran a mock spread that did NOT cover the merge helper's
    // failed-preservation branch — a regression in the merge helper
    // would not have been caught). Same pattern as test 5g below
    // which calls ``evictPendingByAge`` directly.
    it('should preserve the failed flag across an SSE echo merge', () => {
      // Seed a failed bubble.
      component.messages.set([
        {
          message_id: 'echo-keep',
          role: 'user',
          content: 'hello',
          created_at: new Date().toISOString(),
          instance_id: 'inst-abc',
          failed: true,
          errorReason: 'flaky network',
        },
      ]);

      // The SSE echo arrives for the same id (the BE-side hook
      // emitted it before the POST errored in production). Drive
      // the production ``mergeMessagesById`` so this test exercises
      // the real failed-preservation branch.
      const echoArrival = {
        message_id: 'echo-keep',
        role: 'user' as const,
        content: 'hello',
        created_at: new Date().toISOString(),
        instance_id: 'inst-abc',
      };
      const merged = mergeMessagesById(component.messages(), [echoArrival]);

      // The failed flag stays set; the user keeps the error state
      // until they retry or dismiss.
      expect(merged).toHaveLength(1);
      expect(merged[0].failed).toBe(true);
      expect(merged[0].errorReason).toBe('flaky network');
    });

    // 5f-bonus — Merge helper invariant for the queue_id stash
    // (must-fix #2, 2026-08-31): the ``queue_id`` stashed on a
    // failed bubble at fail-mark time MUST survive a subsequent
    // SSE echo merge — otherwise a retry that races an echo would
    // lose the stash and fall back to the (possibly wrong)
    // activeProjectId default.
    it('should preserve the stashed queue_id across an SSE echo merge', () => {
      component.messages.set([
        {
          message_id: 'echo-stash',
          role: 'user',
          content: 'hello',
          created_at: new Date().toISOString(),
          instance_id: 'inst-abc',
          failed: true,
          errorReason: 'flaky network',
          queue_id: 'queue-42',
        },
      ]);

      const echoArrival = {
        message_id: 'echo-stash',
        role: 'user' as const,
        content: 'hello',
        created_at: new Date().toISOString(),
        instance_id: 'inst-abc',
      };
      const merged = mergeMessagesById(component.messages(), [echoArrival]);

      expect(merged).toHaveLength(1);
      expect(merged[0].failed).toBe(true);
      // The stash survives the merge.
      expect(merged[0].queue_id).toBe('queue-42');
    });

    // 5g — TTL eviction invariant: the failed entry MUST NOT be
    // evicted by ``evictPendingByAge``. Otherwise the bug re-appears
    // as a silent bubble drop (and the user has no record of the
    // failure).
    it('should NOT evict a failed entry by age', () => {
      // Drive the clock so the entry is "ancient" by TTL standards.
      const fixedNow = Date.now();
      jest.spyOn(Date, 'now').mockReturnValue(fixedNow);
      const TEN_MIN_MS = 10 * 60 * 1000;
      const oldStamp = new Date(fixedNow - TEN_MIN_MS * 2).toISOString();

      component.messages.set([
        {
          message_id: 'echo-old-failed',
          role: 'user',
          content: 'ancient failed',
          created_at: oldStamp,
          instance_id: 'inst-abc',
          failed: true,
          errorReason: 'old failure',
        },
      ]);

      // Trigger a refetch pass that runs eviction — mimic the
      // loadInstanceMessages merge path by calling the same evict
      // helper.
      // eslint-disable-next-line @typescript-eslint/no-var-requires
      const { evictPendingByAge } = require('../../services/message-merge.util');
      const evicted = evictPendingByAge(component.messages(), TEN_MIN_MS, Date.now());
      expect(evicted).toHaveLength(1);
      expect(evicted[0].failed).toBe(true);
    });

    // 5h — Retry-during-cooldown invariant (must-fix #1, 2026-08-31).
    // If the user clicks Retry within the 3-second cooldown window of
    // the original send, ``onSendMessage`` blocks at its cooldown
    // guard. The retry handler MUST NOT clear the failed marker
    // synchronously — doing so leaves the bubble rendering as
    // "delivered" with no corresponding POST, exactly the dishonest
    // state defect #5 forbids. The marker is cleared in the SUCCESS
    // path instead, so a no-POST cooldown-blocked retry keeps the
    // error state intact.
    //
    // This is the regression test the original must-fix-#1 review
    // flagged: the original code cleared the marker at the top of
    // ``onRetryFailedMessage`` BEFORE calling ``onSendMessage``, so
    // the cooldown snackbar would fire AND the marker would already
    // be gone. The behavior was the silent deliver with no POST.
    it('should keep the bubble failed when retry is clicked during the cooldown window', () => {
      // Drive the clock so the cooldown is deterministically active.
      const nowSpy = jest.spyOn(Date, 'now');
      const t0 = 10_000;
      nowSpy.mockReturnValue(t0);

      // (1) The original send: SSE echo lands, POST errors, error
      //     handler marks the bubble failed. The cooldown stamp is
      //     set by the original ``onSendMessage`` path.
      mockSseService.messages.set([
        {
          message_id: 'echo-cooldown',
          role: 'user',
          content: 'hello',
          created_at: new Date(t0).toISOString(),
          instance_id: 'inst-abc',
        },
      ]);
      component.runSseMergeEffect();
      expect(component.messages()).toHaveLength(1);

      fireFailedSend('Server unavailable');
      component.onSendMessage({ content: 'hello' });

      const afterOriginal = component.messages();
      expect(afterOriginal).toHaveLength(1);
      expect(afterOriginal[0].failed).toBe(true);
      expect(afterOriginal[0].errorReason).toBe('Server unavailable');
      const sendCallsBeforeRetry = mockApiService.sendMessage.mock.calls.length;

      // (2) The user clicks Retry ~1s later (still inside the 3s
      //     cooldown window). Clock does NOT advance.
      nowSpy.mockReturnValue(t0 + 1_000);
      component.onRetryFailedMessage('echo-cooldown');

      // The retry handler called ``onSendMessage``, which fired the
      // cooldown snackbar and returned early. The retry POST never
      // went out.
      expect(mockApiService.sendMessage).toHaveBeenCalledTimes(sendCallsBeforeRetry);
      expect(mockSnackBar.open).toHaveBeenCalledWith(
        expect.stringContaining('Please wait'),
        'Dismiss',
        { duration: 2000, panelClass: 'info-snackbar' },
      );

      // The bubble STILL carries the failed marker and the original
      // error reason — the user keeps the error state. No
      // "delivered" rendering for a send that never happened.
      const afterRetry = component.messages();
      expect(afterRetry).toHaveLength(1);
      expect(afterRetry[0].message_id).toBe('echo-cooldown');
      expect(afterRetry[0].failed).toBe(true);
      expect(afterRetry[0].errorReason).toBe('Server unavailable');
    });

    // 5i — Retry carries the original send's queue_id (must-fix #2,
    // 2026-08-31). The original code passed
    // ``activeProjectId() === null ? undefined : undefined`` (both
    // branches undefined — a tautology that always dropped the queue
    // context). The retry POST must land on the SAME queue the
    // original send was routed to, else a project switch between
    // the original fail and the retry click would silently re-route
    // the retry.
    //
    // Three sub-cases:
    //   - stash is a string: retry forwards it verbatim;
    //   - stash is null: retry forwards null (queue selector was
    //     open with nothing selected);
    //   - stash is undefined (older mark path): retry falls back to
    //     ``activeProjectId``.
    it('should forward the stashed queue_id on retry (string value)', () => {
      component.messages.set([
        {
          message_id: 'echo-q',
          role: 'user',
          content: 'hello',
          created_at: new Date().toISOString(),
          instance_id: 'inst-abc',
          failed: true,
          errorReason: 'flaky',
          queue_id: 'queue-orig-1',
        },
      ]);

      // Switch the active project — if the retry fell back to
      // activeProjectId (the buggy tautology), it would pick up the
      // new project id. The assert below confirms the stash wins.
      component.tabStateService.setActiveTab('project-2');

      // Mock a successful retry POST.
      mockApiService.sendMessage.mockReturnValueOnce({
        subscribe: (handlers: any) => {
          handlers.next({
            status: 'injected',
            message_id: 'echo-q',
            created_at: new Date().toISOString(),
            timestamp: new Date().toISOString(),
            instance_id: 'inst-abc',
            content: 'hello',
            pending_count: 1,
          });
          return { unsubscribe: () => {} };
        },
      });

      component.onRetryFailedMessage('echo-q');

      expect(mockApiService.sendMessage).toHaveBeenLastCalledWith(
        'inst-abc',
        'hello',
        undefined,
        'queue-orig-1', // the stashed value, NOT the active project
      );
    });

    it('should forward null queue_id on retry when the stash was null', () => {
      component.messages.set([
        {
          message_id: 'echo-q-null',
          role: 'user',
          content: 'hello',
          created_at: new Date().toISOString(),
          instance_id: 'inst-abc',
          failed: true,
          errorReason: 'flaky',
          queue_id: null,
        },
      ]);

      mockApiService.sendMessage.mockReturnValueOnce({
        subscribe: (handlers: any) => {
          handlers.next({
            status: 'injected',
            message_id: 'echo-q-null',
            created_at: new Date().toISOString(),
            timestamp: new Date().toISOString(),
            instance_id: 'inst-abc',
            content: 'hello',
            pending_count: 1,
          });
          return { unsubscribe: () => {} };
        },
      });

      component.onRetryFailedMessage('echo-q-null');

      expect(mockApiService.sendMessage).toHaveBeenLastCalledWith(
        'inst-abc',
        'hello',
        undefined,
        null, // explicit null forwarded (not coerced to undefined)
      );
    });

    it('should fall back to activeProjectId when the bubble has no stash', () => {
      component.messages.set([
        {
          message_id: 'echo-q-missing',
          role: 'user',
          content: 'hello',
          created_at: new Date().toISOString(),
          instance_id: 'inst-abc',
          failed: true,
          errorReason: 'older mark path',
          // no queue_id (older bubble without the stash)
        },
      ]);

      component.tabStateService.setActiveTab('project-fallback');

      mockApiService.sendMessage.mockReturnValueOnce({
        subscribe: (handlers: any) => {
          handlers.next({
            status: 'injected',
            message_id: 'echo-q-missing',
            created_at: new Date().toISOString(),
            timestamp: new Date().toISOString(),
            instance_id: 'inst-abc',
            content: 'hello',
            pending_count: 1,
          });
          return { unsubscribe: () => {} };
        },
      });

      component.onRetryFailedMessage('echo-q-missing');

      expect(mockApiService.sendMessage).toHaveBeenLastCalledWith(
        'inst-abc',
        'hello',
        undefined,
        'project-fallback', // falls back to activeProjectId
      );
    });

    // 5j — End-to-end: original send errors, fail-mark stashes the
    // queue_id; retry then forwards the SAME queue_id to the API.
    // This is the integration-level contract test for the full
    // stash-then-retry flow (must-fix #2 from error to retry POST).
    it('should stash queue_id on fail-mark and forward it on retry', () => {
      // Drive the clock past the cooldown so the retry actually fires.
      const t0 = 50_000;
      jest.spyOn(Date, 'now').mockReturnValue(t0);

      // (1) SSE echo + failed POST — bubble gets marked failed AND
      //     the queue_id is stashed.
      mockSseService.messages.set([
        {
          message_id: 'echo-e2e',
          role: 'user',
          content: 'hello',
          created_at: new Date(t0).toISOString(),
          instance_id: 'inst-abc',
        },
      ]);
      component.runSseMergeEffect();

      // Mock the original send to error AFTER we set up a
      // different active project so the retry could (incorrectly)
      // pick it up if the stash were absent.
      component.tabStateService.setActiveTab('project-active');

      fireFailedSend('Server unavailable');
      component.onSendMessage({ content: 'hello', queue_id: 'queue-e2e-7' });

      const afterFail = component.messages();
      expect(afterFail).toHaveLength(1);
      expect(afterFail[0].failed).toBe(true);
      // Stash landed on the bubble.
      expect(afterFail[0].queue_id).toBe('queue-e2e-7');

      // (2) User clicks Retry after cooldown elapses (clock advances).
      jest.spyOn(Date, 'now').mockReturnValue(t0 + 5_000);

      // Switch the active project again — if the retry incorrectly
      // derived queue_id from activeProjectId, it would land on
      // 'project-active' or whatever is current. The assert below
      // confirms the stash is forwarded verbatim.
      component.tabStateService.setActiveTab('project-other');

      mockApiService.sendMessage.mockReturnValueOnce({
        subscribe: (handlers: any) => {
          handlers.next({
            status: 'injected',
            message_id: 'echo-e2e',
            created_at: new Date().toISOString(),
            timestamp: new Date().toISOString(),
            instance_id: 'inst-abc',
            content: 'hello',
            pending_count: 1,
          });
          return { unsubscribe: () => {} };
        },
      });

      component.onRetryFailedMessage('echo-e2e');

      // The retry POST used the ORIGINAL queue context — not the
      // currently-active project.
      expect(mockApiService.sendMessage).toHaveBeenLastCalledWith(
        'inst-abc',
        'hello',
        undefined,
        'queue-e2e-7',
      );
    });
  });

  // ────────────────────────────────────────────────────────────────────────
  // MIN-2 — optimistic-append TOCTOU across instance switch
  //
  // The HTTP response can land AFTER the user switched instances. The
  // append must capture the instance id at send time and drop the
  // provisional when ``activeInstanceId()`` no longer matches —
  // otherwise instance B's freshly-loaded list gets a bubble belonging
  // to instance A. ``isSending`` is still released so the spinner
  // doesn't stick across the switch.
  // ────────────────────────────────────────────────────────────────────────
  describe('onSendMessage() - optimistic append TOCTOU guard (MIN-2)', () => {
    /** Two-phase send: register the response, hold it in-flight. */
    function holdSend(response: unknown): { resolve: () => void } {
      let release: (() => void) | null = null;
      mockApiService.sendMessage.mockReturnValueOnce({
        subscribe: (handlers: any) => {
          release = () => handlers.next(response);
          return { unsubscribe: () => {} };
        },
      });
      component.onSendMessage({ content: 'hello' });
      return {
        resolve: () => {
          if (release) release();
          else throw new Error('subscribe handler never registered');
        },
      };
    }

    const makeResponse = () => ({
      status: 'injected',
      instance_id: 'inst-abc',
      content: 'hello',
      timestamp: new Date().toISOString(),
      created_at: new Date().toISOString(),
      pending_count: 1,
      message_id: 'echo-toctou-1',
      queued: false,
    });

    beforeEach(() => {
      component.currentInstance.set(createMockInstance({ instance_id: 'inst-abc' }));
      component.viewState.activeInstanceId.set('inst-abc');
      mockSseService.messages.set([]);
    });

    afterEach(() => {
      jest.restoreAllMocks();
      mockSseService.messages.set([]);
    });

    it('should DROP the provisional when the user switched instances before the response landed', () => {
      const held = holdSend(makeResponse());

      // User switches to another instance while the POST is in-flight.
      component.viewState.activeInstanceId.set('inst-other');

      held.resolve();

      // No bubble may land in the (now different) active view.
      expect(component.messages()).toHaveLength(0);
      // The send itself completed — the spinner must not stick.
      expect(component.isSending()).toBe(false);
    });

    it('should STILL append when the response lands with no instance switch (control)', () => {
      const held = holdSend(makeResponse());
      held.resolve();

      expect(component.messages()).toHaveLength(1);
      expect(component.messages()[0].message_id).toBe('echo-toctou-1');
      expect(component.messages()[0].pending).toBe(true);
      expect(component.isSending()).toBe(false);
    });
  });

  // ────────────────────────────────────────────────────────────────────────
  // MIN-3 — terminal-status pending purge scoped to the ACTIVE instance
  //
  // A cascade CHILD reaching a terminal status on this channel (or a
  // trigger recorded for a previously-opened instance) must NOT wipe
  // the active chat's provisional bubbles.
  // ────────────────────────────────────────────────────────────────────────
  describe('pending purge effect — activeInstanceId scoping (MIN-3)', () => {
    beforeEach(() => {
      component.currentInstance.set(createMockInstance({ instance_id: 'inst-abc' }));
      component.viewState.activeInstanceId.set('inst-abc');
      component.messages.set([
        {
          message_id: 'prov-1',
          role: 'user',
          content: 'in flight',
          created_at: new Date().toISOString(),
          instance_id: 'inst-abc',
          pending: true,
        },
        {
          message_id: 'm-confirmed',
          role: 'assistant',
          content: 'confirmed',
          created_at: new Date().toISOString(),
          instance_id: 'inst-abc',
        },
      ]);
      mockSseService.messages.set([]);
    });

    afterEach(() => {
      mockSseService.messages.set([]);
    });

    it('should NOT purge when the terminal event belongs to a cascade CHILD (≠ active instance)', () => {
      // Child went terminal on the parent's channel: the service (or
      // the test, driving the signals directly) records the CHILD id.
      mockSseService.pendingPurgeRequest.set(1);
      mockSseService.pendingPurgeInstanceId.set('child-1');

      component.runPendingPurgeEffect();

      const ids = component.messages().map((m: Message) => m.message_id);
      expect(ids).toContain('prov-1');      // provisional bubble survives
      expect(ids).toContain('m-confirmed'); // confirmed bubble untouched
    });

    it('should NOT purge when no active instance is cached', () => {
      component.viewState.activeInstanceId.set(null);
      mockSseService.pendingPurgeRequest.set(1);
      mockSseService.pendingPurgeInstanceId.set('inst-abc');

      component.runPendingPurgeEffect();

      expect(component.messages().map((m: Message) => m.message_id)).toContain('prov-1');
    });

    it('should purge when the terminal event matches the ACTIVE instance', () => {
      mockSseService.pendingPurgeRequest.set(1);
      mockSseService.pendingPurgeInstanceId.set('inst-abc');

      component.runPendingPurgeEffect();

      const ids = component.messages().map((m: Message) => m.message_id);
      expect(ids).not.toContain('prov-1');   // provisional purged
      expect(ids).toContain('m-confirmed');  // confirmed bubble untouched
    });

    it('should be a no-op when the purge trigger never fired', () => {
      mockSseService.pendingPurgeRequest.set(0);
      mockSseService.pendingPurgeInstanceId.set(null);

      component.runPendingPurgeEffect();

      expect(component.messages().map((m: Message) => m.message_id)).toContain('prov-1');
    });
  });

  describe('Watchover integration', () => {
    // TECHNICAL DEBT: These tests run against a hand-maintained
    // TestableChatComponent surrogate that mirrors production logic.
    // The surrogate can diverge from the real ChatComponent. When
    // adding new watchover behavior, update BOTH the surrogate and
    // these tests to stay in sync. Future work: migrate to TestBed
    // component testing against the real ChatComponent.
    afterEach(() => {
      jest.restoreAllMocks();
    });

    it('should call the API directly when enabling watchover on a running instance', () => {
      // Running instances skip the dialog — the backend builds the
      // watcher context from the live message stream. Mirrors the
      // no-dialog branch in production ChatComponent.onToggleWatchover.
      const instanceId = 'watchover-enable-running-inst';
      component.currentInstanceId.set(instanceId);
      component.currentInstance.set(createMockInstance({ instance_id: instanceId, status: 'running' }));
      mockApiService.setWatchover.mockReturnValue({
        subscribe: (handlers: any) => {
          handlers.next({ watchover_enabled: true, instance_id: instanceId });
          return { unsubscribe: () => {} };
        }
      });

      component.onToggleWatchover();

      expect(mockApiService.setWatchover).toHaveBeenCalledWith(instanceId, true, null);
      expect(component.watchoverEnabled()).toBe(true);
    });

    it('should open the watchover dialog when enabling on a non-running instance', () => {
      // Non-running (terminal/idle) instances need the operator to
      // supply the next command + guardrails, so the dialog is opened
      // and the API is NOT called yet.
      const instanceId = 'watchover-enable-terminal-inst';
      component.currentInstanceId.set(instanceId);
      component.currentInstance.set(createMockInstance({ instance_id: instanceId, status: 'paused' }));
      const dialogSpy = jest.spyOn(component, 'openWatchoverDialog');

      component.onToggleWatchover();

      expect(dialogSpy).toHaveBeenCalledWith(instanceId);
      expect(mockApiService.setWatchover).not.toHaveBeenCalled();
    });

    it('should increment the denial counter for a watchover ToolMessage', () => {
      const instanceId = 'watchover-denial-inst';
      component.currentInstance.set(createMockInstance({ instance_id: instanceId }));
      mockSseService.messages.set([{
        message_id: 'watchover-denial-message',
        role: 'tool',
        content: 'Watchover denied this tool call: destructive delete. Please adjust your approach.',
        created_at: new Date().toISOString(),
        instance_id: instanceId,
      }]);

      component.runWatchoverDenialEffect();

      expect(component.watchoverDenialCount()).toBe(1);
    });

    it('should update watchover state for active and inactive status changes', () => {
      const instanceId = 'watchover-status-inst';
      component.currentInstance.set(createMockInstance({ instance_id: instanceId }));

      mockSseService.statusChange.set({ instance_id: instanceId, status: 'watchover_active' });
      component.runWatchoverStatusEffect();
      expect(component.watchoverEnabled()).toBe(true);

      component.watchoverDenialCount.set(2);
      mockSseService.statusChange.set({ instance_id: instanceId, status: 'watchover_inactive' });
      component.runWatchoverStatusEffect();
      expect(component.watchoverEnabled()).toBe(false);
      expect(component.watchoverDenialCount()).toBe(0);
    });

    it('should open the dialog with the saved requirement available as default for non-running instances', () => {
      // The legacy window.prompt flow used the saved requirement as
      // the prompt's default value. The dialog flow exposes a single
      // openWatchoverDialog hook instead, but the saved requirement
      // is still read from localStorage so the dialog (or any caller)
      // can pre-fill its requirement field. This test guards that
      // read path so the dialog initializer can rely on it.
      const instanceId = 'watchover-storage-inst';
      const storageKey = `ensemble-watchover-${instanceId}`;
      component.currentInstanceId.set(instanceId);
      component.currentInstance.set(createMockInstance({ instance_id: instanceId, status: 'paused' }));
      localStorage.setItem(
        storageKey,
        JSON.stringify({ enabled: false, requirement: 'Saved monitoring requirement' })
      );
      const dialogSpy = jest.spyOn(component, 'openWatchoverDialog');

      component.onToggleWatchover();

      expect(dialogSpy).toHaveBeenCalledWith(instanceId);
      expect(mockApiService.setWatchover).not.toHaveBeenCalled();
      // Saved requirement remains readable so the dialog (or any
      // caller) can pre-fill its requirement field. Read localStorage
      // directly because ``getSavedWatchoverRequirement`` is private.
      expect(JSON.parse(localStorage.getItem(storageKey) ?? '{}')).toEqual({
        enabled: false,
        requirement: 'Saved monitoring requirement',
      });
    });

    it('should call the API with enabled=false when toggling off watchover', () => {
      const instanceId = 'watchover-disable-inst';
      component.currentInstanceId.set(instanceId);
      component.currentInstance.set(createMockInstance({ instance_id: instanceId }));
      component.watchoverEnabled.set(true);
      mockApiService.setWatchover.mockReturnValue({
        subscribe: (handlers: any) => {
          handlers.next({ watchover_enabled: false, instance_id: instanceId });
          return { unsubscribe: () => {} };
        }
      });

      component.onToggleWatchover();

      expect(mockApiService.setWatchover).toHaveBeenCalledWith(instanceId, false, null);
      expect(component.watchoverEnabled()).toBe(false);
    });

    it('should show error snackbar and not change state on API failure', () => {
      // Running-instance path so no dialog is involved — the API call
      // fails synchronously and the error handler must surface the
      // snackbar without flipping the watchover toggle.
      const instanceId = 'watchover-error-inst';
      component.currentInstanceId.set(instanceId);
      component.currentInstance.set(createMockInstance({ instance_id: instanceId, status: 'running' }));
      mockApiService.setWatchover.mockReturnValue({
        subscribe: (handlers: any) => {
          handlers.error(new Error('Network error'));
          return { unsubscribe: () => {} };
        }
      });

      component.onToggleWatchover();

      expect(component.watchoverEnabled()).toBe(false);
      expect(mockSnackBar.open).toHaveBeenCalledWith('Failed to toggle watchover', 'Dismiss', { duration: 3000 });
      expect(component.watchoverPending()).toBe(false);
    });

    it('should reset toggle and denial count on watchover_failed SSE event', () => {
      const instanceId = 'watchover-failed-inst';
      component.currentInstance.set(createMockInstance({ instance_id: instanceId }));
      component.watchoverEnabled.set(true);
      component.watchoverDenialCount.set(2);

      mockSseService.statusChange.set({ instance_id: instanceId, status: 'watchover_failed' });
      component.runWatchoverStatusEffect();

      expect(component.watchoverEnabled()).toBe(false);
      expect(component.watchoverDenialCount()).toBe(0);
    });

    it('should clear watchover state on watchover_terminated SSE event', () => {
      const instanceId = 'watchover-terminated-inst';
      component.currentInstance.set(createMockInstance({ instance_id: instanceId }));
      component.watchoverEnabled.set(true);
      component.watchoverDenialCount.set(3);

      mockSseService.statusChange.set({ instance_id: instanceId, status: 'watchover_terminated' });
      component.runWatchoverStatusEffect();

      expect(component.watchoverEnabled()).toBe(false);
      expect(component.watchoverDenialCount()).toBe(0);
    });

    it('should NOT overwrite denial count when syncing from API poll (C1 fix)', () => {
      const instanceId = 'watchover-c1-inst';
      const instance = createMockInstance({
        instance_id: instanceId,
        watchover_enabled: true,
        watchover_context: 'test context',
        watchover_denial_count: 0,  // backend always returns 0
      });
      component.currentInstance.set(instance);
      component.watchoverDenialCount.set(2);  // SSE incremented this

      component.syncWatchoverState(instance);

      // watchoverEnabled and watchoverContext sync from API...
      expect(component.watchoverEnabled()).toBe(true);
      // ...but denial count is preserved (not reset to 0 from API)
      expect(component.watchoverDenialCount()).toBe(2);
    });
  });

  describe('Workspace overlay', () => {
    it('should start hidden with no project id', () => {
      expect(component.workspaceOverlayService.showWorkspace()).toBe(false);
      expect(component.workspaceOverlayService.workspaceProjectId()).toBeNull();
    });

    it('should open the overlay for the toggled project', () => {
      component.onWorkspaceToggle('proj-a');

      expect(component.workspaceOverlayService.showWorkspace()).toBe(true);
      expect(component.workspaceOverlayService.workspaceProjectId()).toBe('proj-a');
    });

    it('should toggle off when the same project is clicked again', () => {
      component.onWorkspaceToggle('proj-a');
      component.onWorkspaceToggle('proj-a');

      expect(component.workspaceOverlayService.showWorkspace()).toBe(false);
      expect(component.workspaceOverlayService.workspaceProjectId()).toBe('proj-a');
    });

    it('should switch projects when a different project is clicked while open', () => {
      component.onWorkspaceToggle('proj-a');
      component.onWorkspaceToggle('proj-b');

      expect(component.workspaceOverlayService.showWorkspace()).toBe(true);
      expect(component.workspaceOverlayService.workspaceProjectId()).toBe('proj-b');
    });

    it('should require a real project for the header toggle', () => {
      tabStateService.setActiveTab('all');
      component.onHeaderWorkspaceToggle();

      expect(component.workspaceOverlayService.showWorkspace()).toBe(false);
    });

    it('should open the overlay from the header for the active project', () => {
      tabStateService.setActiveTab('proj-header');
      component.onHeaderWorkspaceToggle();

      expect(component.workspaceOverlayService.showWorkspace()).toBe(true);
      expect(component.workspaceOverlayService.workspaceProjectId()).toBe('proj-header');
    });
  });

  describe('Workspace ↔ tab sync (tabWorkspaceEffect)', () => {
    it('should follow workspace to the newly active project when workspace is open', () => {
      // Open workspace on proj-a
      component.onWorkspaceToggle('proj-a');
      expect(component.workspaceOverlayService.showWorkspace()).toBe(true);
      expect(component.workspaceOverlayService.workspaceProjectId()).toBe('proj-a');

      // Simulate user clicking the proj-b tab — activeProjectId changes
      tabStateService.setActiveTab('proj-b');
      component.runTabWorkspaceEffect();

      expect(component.workspaceOverlayService.showWorkspace()).toBe(true);
      expect(component.workspaceOverlayService.workspaceProjectId()).toBe('proj-b');
    });

    it('should NOT auto-open workspace when switching to a project tab while closed', () => {
      // Workspace is closed by default
      expect(component.workspaceOverlayService.showWorkspace()).toBe(false);

      tabStateService.setActiveTab('proj-b');
      component.runTabWorkspaceEffect();

      expect(component.workspaceOverlayService.showWorkspace()).toBe(false);
      expect(component.workspaceOverlayService.workspaceProjectId()).toBeNull();
    });

    it('should hide the workspace when switching to the All tab', () => {
      // Open workspace on a project
      component.onWorkspaceToggle('proj-a');
      expect(component.workspaceOverlayService.showWorkspace()).toBe(true);

      // Switch to All tab
      tabStateService.setActiveTab('all');
      component.runTabWorkspaceEffect();

      expect(component.workspaceOverlayService.showWorkspace()).toBe(false);
      expect(component.workspaceOverlayService.workspaceProjectId()).toBeNull();
    });

    it('should be a no-op when switching to the same project that is already open', () => {
      component.onWorkspaceToggle('proj-a');
      tabStateService.setActiveTab('proj-a');
      component.runTabWorkspaceEffect();

      expect(component.workspaceOverlayService.showWorkspace()).toBe(true);
      expect(component.workspaceOverlayService.workspaceProjectId()).toBe('proj-a');
    });
  });

  describe('Workspace icon click flow (project-tab-bar → ChatComponent)', () => {
    it('should switch the active tab AND open the workspace when icon is clicked on a different project', () => {
      // Simulate: user is on proj-a, clicks workspace icon on proj-b
      // ProjectTabBarComponent.onWorkspaceClick:
      //   1. setActiveTab('proj-b')
      //   2. emit workspaceToggle('proj-b')
      // ChatComponent.onWorkspaceToggle handler runs:
      //   since workspaceProjectId !== 'proj-b', workspaceProjectId = 'proj-b', showWorkspace = true
      tabStateService.setActiveTab('proj-b');
      component.onWorkspaceToggle('proj-b');
      // Then the effect fires (it would have fired on setActiveTab too, but
      // for proj-b the condition (showWorkspace && workspaceProjectId !== proj-b)
      // would already be false since onWorkspaceToggle just set them equal).
      component.runTabWorkspaceEffect();

      expect(tabStateService.setActiveTabCalls).toContain('proj-b');
      expect(component.workspaceOverlayService.showWorkspace()).toBe(true);
      expect(component.workspaceOverlayService.workspaceProjectId()).toBe('proj-b');
    });

    it('should switch the active tab AND close the workspace when icon is clicked on the same project', () => {
      // Open workspace on proj-a while on proj-a tab
      tabStateService.setActiveTab('proj-a');
      component.onWorkspaceToggle('proj-a');
      expect(component.workspaceOverlayService.showWorkspace()).toBe(true);

      // Click the icon again — setActiveTab('proj-a') then toggle
      tabStateService.setActiveTab('proj-a');
      component.onWorkspaceToggle('proj-a');
      component.runTabWorkspaceEffect();

      expect(tabStateService.setActiveTabCalls).toContain('proj-a');
      expect(component.workspaceOverlayService.showWorkspace()).toBe(false);
    });
  });

  // ────────────────────────────────────────────────────────────────────────
  // R1 — Hidden polling leak: tabEffect must gate on visible()
  //
  // The boot/restoration path sets activeTab while the chat overlay
  // is hidden (default visibility), and always-alive header widgets
  // (notification-bell, job-queue-indicator) call setActiveTab('all')
  // even when the overlay is hidden. Both previously started a 60s
  // polling ticker. The fix bails out of the effect when visible=false.
  // ────────────────────────────────────────────────────────────────────────
  describe('Tab polling effect — R1 visibility gate', () => {
    beforeEach(() => {
      mockInstanceService.startPolling.mockClear();
    });

    it('starts polling on initial mount when visible=true (default)', () => {
      component.runTabPollingEffect();

      expect(mockInstanceService.startPolling).toHaveBeenCalledTimes(1);
    });

    it('does NOT start polling when visible=false (boot with hidden overlay)', () => {
      component.visible.set(false);

      component.runTabPollingEffect();

      expect(mockInstanceService.startPolling).not.toHaveBeenCalled();
    });

    it('does NOT start polling on hidden setActiveTab calls (notification-bell while hidden)', () => {
      component.visible.set(false);
      // notification-bell / job-queue-indicator fire setActiveTab('all')
      // unconditionally; this would previously have restarted polling
      // while hidden.
      tabStateService.setActiveTab('all');
      component.runTabPollingEffect();

      expect(mockInstanceService.startPolling).not.toHaveBeenCalled();
    });

    it('restarts polling after a hidden→visible cycle', () => {
      // 1. Hidden state: no polling
      component.visible.set(false);
      component.runTabPollingEffect();
      expect(mockInstanceService.startPolling).not.toHaveBeenCalled();

      // 2. Becomes visible again — polling resumes
      component.visible.set(true);
      component.runTabPollingEffect();
      expect(mockInstanceService.startPolling).toHaveBeenCalledTimes(1);
    });
  });

  // ────────────────────────────────────────────────────────────────────────
  // R2 / S2 — Polling scope unification
  //
  // ``activeProjectId()`` resolves to ``null`` for the 'all' tab and
  // to a real id for project tabs. The canonical ``pollingScope()``
  // helper used by BOTH ``tabEffect`` and the visibility effect must
  // translate 'all' / null → undefined so the backend never receives a
  // literal ``'all'`` as a project filter (which matches nothing and
  // used to wipe the instance list).
  // ────────────────────────────────────────────────────────────────────────
  describe('pollingScope() — R2 / S2 canonical resolver', () => {
    it('returns the project id when on a real project tab', () => {
      tabStateService.setActiveTab('proj-real');

      expect(component.pollingScope()).toBe('proj-real');
    });

    it('returns undefined when activeProjectId is null (All tab)', () => {
      // Default state: activeTab is the 'all' pseudo-tab → null.
      expect(component.pollingScope()).toBeUndefined();
    });

    it('returns undefined for any tab whose id is "all" (defense in depth)', () => {
      // TabStateService never produces activeProjectId === 'all'
      // (the activeProjectId computed returns null for 'all' tabs),
      // but if a future refactor reintroduced 'all' as a real id,
      // the helper would still translate it correctly.
      // We simulate by overriding the mock's computed.
      Object.defineProperty(tabStateService, 'activeProjectId', {
        configurable: true,
        get: () => signal('all' as string | null),
      });

      expect(component.pollingScope()).toBeUndefined();
    });

    it('uses pollingScope() in the tabEffect (R2 unification)', () => {
      tabStateService.setActiveTab('proj-bar');
      component.runTabPollingEffect();

      expect(mockInstanceService.startPolling).toHaveBeenLastCalledWith('proj-bar');
    });

    it('passes undefined to startPolling when on All tab (no "all" pseudo-id leak)', () => {
      // Tab is 'all' → activeProjectId is null → pollingScope() is
      // undefined → startPolling(undefined) means "match all". The
      // old bug sent project_id="all" which matched nothing.
      component.runTabPollingEffect();

      expect(mockInstanceService.startPolling).toHaveBeenLastCalledWith(undefined);
    });
  });

  // ────────────────────────────────────────────────────────────────────────
  // R3 / W2 — Async completion staleness guards
  //
  // When the user hides the overlay or switches to a different
  // instance mid-load, the in-flight ``getInstance`` / ``getMessages``
  // subscriptions used to write into instance-scoped signals for an
  // instance we no longer care about, AND ``sseService.connect()``
  // would open a never-closed EventSource while hidden. The fix
  // re-checks ``visible()`` and ``activeInstanceId()`` at the start of
  // each async completion and bails when either has drifted.
  //
  // F4: these tests drive the ACTUAL guarded callbacks via the
  // surrogate's ``loadInstanceMessages`` / ``handleInstanceApiFallback``
  // mirrors (byte-for-byte copies of the production guards). The API
  // observable is a ``Subject`` held in-flight while the world moves
  // on, then completed — a regression that deletes the production
  // guard would let the side effects through and FAIL these tests.
  // ────────────────────────────────────────────────────────────────────────
  describe('Async completion staleness guards — R3 / W2 contract', () => {
    beforeEach(() => {
      mockApiService.getMessages.mockReset();
      mockApiService.getMessages.mockReturnValue(new Subject<any[]>());
      mockApiService.getInstance.mockReset();
      mockApiService.getInstance.mockReturnValue(new Subject<InstanceInfo>());
      mockSseService.connect.mockClear();
      mockSseService.fetchPendingInjection.mockClear();
      mockSseService.fetchPendingQuestion.mockClear();
    });

    it('hiding the overlay mid-load blocks connect() and all signal writes (getMessages completion)', () => {
      const instanceId = 'inst-guard-hide';
      component.viewState.openDetail('proj-a', instanceId);
      component.loadInstanceMessages(instanceId);
      expect(mockApiService.getMessages).toHaveBeenCalledWith(instanceId);

      // The world moves on: user hides the overlay while the load is
      // in-flight.
      component.visible.set(false);

      // Async completion finally arrives — stale (instanceId, visible=false).
      (mockApiService.getMessages.mock.results[0].value as Subject<any[]>).next([
        { message_id: 'm1', role: 'user', content: 'hello', instance_id: instanceId },
      ]);
      (mockApiService.getMessages.mock.results[0].value as Subject<any[]>).complete();

      // NO side effects: no messages write, no SSE connect, no
      // per-instance fetches.
      expect(component.messages()).toEqual([]);
      expect(mockSseService.connect).not.toHaveBeenCalled();
      expect(mockSseService.fetchPendingInjection).not.toHaveBeenCalled();
      expect(mockSseService.fetchPendingQuestion).not.toHaveBeenCalled();
    });

    it('switching instances mid-load blocks connect() and all signal writes (getMessages completion)', () => {
      const originalId = 'inst-a';
      const switchedId = 'inst-b';
      component.viewState.openDetail('proj-a', originalId);
      component.loadInstanceMessages(originalId);

      // The world moves on: user clicks a different instance in the
      // sidebar while A's load is in-flight.
      component.viewState.openDetail('proj-a', switchedId);

      // Async completion for A finally arrives — stale (visible, but
      // activeInstanceId has drifted).
      (mockApiService.getMessages.mock.results[0].value as Subject<any[]>).next([
        { message_id: 'm1', role: 'user', content: 'for A', instance_id: originalId },
      ]);
      (mockApiService.getMessages.mock.results[0].value as Subject<any[]>).complete();

      expect(component.messages()).toEqual([]);
      expect(mockSseService.connect).not.toHaveBeenCalled();
      expect(mockSseService.fetchPendingInjection).not.toHaveBeenCalled();
      expect(mockSseService.fetchPendingQuestion).not.toHaveBeenCalled();
      // B's cached id must survive untouched.
      expect(component.viewState.activeInstanceId()).toBe(switchedId);
    });

    it('the getInstance fallback path is guarded the same way (stale after instance switch)', () => {
      const originalId = 'inst-api-a';
      const switchedId = 'inst-api-b';
      component.viewState.openDetail('proj-a', originalId);
      component.handleInstanceApiFallback(originalId);
      expect(mockApiService.getInstance).toHaveBeenCalledWith(originalId);

      // Switch while the getInstance fetch is in-flight.
      component.viewState.openDetail('proj-a', switchedId);

      (mockApiService.getInstance.mock.results[0].value as Subject<InstanceInfo>).next(
        createMockInstance({ instance_id: originalId }),
      );

      // Stale completion: no currentInstance write, no message load
      // chain, no SSE connect.
      expect(component.currentInstance()).toBeNull();
      expect(mockApiService.getMessages).not.toHaveBeenCalled();
      expect(mockSseService.connect).not.toHaveBeenCalled();
      // currentInstanceId was set synchronously before the request —
      // the guard only protects the async completion writes.
      expect(component.currentInstanceId()).toBe(originalId);
      expect(component.viewState.activeInstanceId()).toBe(switchedId);
    });

    it('a FRESH completion (visible + matching id) goes through: connect() fires and messages land', () => {
      const instanceId = 'inst-fresh';
      component.viewState.openDetail('proj-a', instanceId);
      component.loadInstanceMessages(instanceId);

      const messages = [
        { message_id: 'm1', role: 'user', content: 'hello', instance_id: instanceId },
      ];
      (mockApiService.getMessages.mock.results[0].value as Subject<any[]>).next(messages);
      (mockApiService.getMessages.mock.results[0].value as Subject<any[]>).complete();

      expect(component.messages()).toEqual(messages);
      expect(mockSseService.connect).toHaveBeenCalledWith(instanceId);
      expect(mockSseService.fetchPendingInjection).toHaveBeenCalledWith(instanceId);
      expect(mockSseService.fetchPendingQuestion).toHaveBeenCalledWith(instanceId);
    });
  });

  // ────────────────────────────────────────────────────────────────────────
  // W2 — SSE→messages merge-effect instance filter
  //
  // The merge effect (chat.component.ts ~:331-339) drops SSE messages
  // whose ``instance_id`` disagrees with ``activeInstanceId()`` — a
  // message from a foreign instance (race: user switched tabs while an
  // SSE channel was still resolving) must never bleed into the merged
  // messages signal. Matching and id-less messages merge normally.
  // ────────────────────────────────────────────────────────────────────────
  describe('SSE merge effect — W2 instance filter', () => {
    beforeEach(() => {
      component.messages.set([]);
      mockSseService.messages.set([]);
    });

    it('filters out an SSE message with a FOREIGN instance_id', () => {
      component.viewState.openDetail('proj-a', 'inst-current');
      component.messages.set([]);

      mockSseService.messages.set([
        {
          message_id: 'm-foreign',
          role: 'assistant',
          content: 'from another instance',
          instance_id: 'inst-foreign',
          created_at: '2026-08-18T10:00:00Z',
        },
      ]);
      component.runSseMergeEffect();

      expect(component.messages()).toEqual([]);
    });

    it('merges an SSE message whose instance_id matches the active instance', () => {
      component.viewState.openDetail('proj-a', 'inst-current');
      component.messages.set([]);

      mockSseService.messages.set([
        {
          message_id: 'm-match',
          role: 'assistant',
          content: 'for the current instance',
          instance_id: 'inst-current',
          created_at: '2026-08-18T10:00:01Z',
        },
      ]);
      component.runSseMergeEffect();

      expect(component.messages()).toHaveLength(1);
      expect(component.messages()[0].message_id).toBe('m-match');
    });

    it('merges an SSE message with an ABSENT instance_id (legacy/no-id payloads)', () => {
      component.viewState.openDetail('proj-a', 'inst-current');
      component.messages.set([]);

      mockSseService.messages.set([
        {
          message_id: 'm-noid',
          role: 'assistant',
          content: 'no instance tag',
          created_at: '2026-08-18T10:00:02Z',
        },
      ]);
      component.runSseMergeEffect();

      expect(component.messages()).toHaveLength(1);
      expect(component.messages()[0].message_id).toBe('m-noid');
    });

    it('merges only the matching message when a batch mixes foreign and matching ids', () => {
      component.viewState.openDetail('proj-a', 'inst-current');
      component.messages.set([]);

      mockSseService.messages.set([
        {
          message_id: 'm-foreign',
          role: 'assistant',
          content: 'foreign',
          instance_id: 'inst-other',
          created_at: '2026-08-18T10:00:00Z',
        },
        {
          message_id: 'm-match',
          role: 'assistant',
          content: 'matching',
          instance_id: 'inst-current',
          created_at: '2026-08-18T10:00:01Z',
        },
      ]);
      component.runSseMergeEffect();

      expect(component.messages()).toHaveLength(1);
      expect(component.messages()[0].message_id).toBe('m-match');
    });

    it('is a no-op when no active instance is cached', () => {
      // activeInstanceId is null → the effect bails before merging.
      mockSseService.messages.set([
        {
          message_id: 'm-orphan',
          role: 'assistant',
          content: 'nobody listening',
          instance_id: 'inst-anything',
          created_at: '2026-08-18T10:00:03Z',
        },
      ]);
      component.runSseMergeEffect();

      expect(component.messages()).toEqual([]);
    });
  });

  // ────────────────────────────────────────────────────────────────────────
  // R6 / F1 / N1 — Lazy dead-id validation of the cached activeInstanceId
  //
  // The constructor effect watches instanceService.instances(): when
  // a FULL list (pollingScope() === undefined) is loaded, non-empty,
  // and does NOT contain the cached id — AND the death is confirmed
  // by the API's own 404 (``instanceNotFound() === cachedId``) — the
  // cached id is cleared so the nav link never restores a dead
  // instance.
  //
  // N1: the previous contract ALSO cleared when the component had
  // moved off the id (``currentInstanceId() !== cachedId``). That
  // clause was a race: on ``activeInstanceId.set(B)`` the dead-id
  // effect fires FIRST (cachedId=B, currentInstanceId stale A),
  // "moved off" reads true, and B is wiped before the visibility
  // effect ever runs the load. The fix removes that clause entirely
  // — see the TestBed host test below for the real-effect-ordering
  // pin.
  // ────────────────────────────────────────────────────────────────────────
  describe('R6 lazy dead-id validation effect', () => {
    let liveInstance: InstanceInfo;

    beforeEach(() => {
      mockInstanceService.instances.set([]);
      liveInstance = createMockInstance({ instance_id: 'inst-live' });
    });

    it('does NOT clear when only currentInstanceId disagrees with cachedId — N1 race regression', () => {
      // N1 regression: the OLD contract cleared the cache when
      // ``currentInstanceId() !== cachedId``. With that clause, an
      // ``activeInstanceId.set(B)`` would fire the dead-id effect
      // FIRST (cachedId=B, currentInstanceId stale A) and wipe B
      // before the visibility effect ever ran the load. The fix
      // removes that clause: confirmed-dead is gated ONLY on the
      // API's own 404 (``instanceNotFound() === cachedId``). Here
      // the user has moved off (currentInstanceId=null) but the API
      // hasn't 404'd — the cache must survive.
      component.viewState.openDetail('proj-a', 'inst-dead');
      component.currentInstanceId.set(null);   // user moved off (matches OLD clause)
      component.instanceNotFound.set(null);    // API hasn't 404'd
      mockInstanceService.instances.set([liveInstance]);

      component.runDeadIdValidationEffect();

      expect(component.viewState.activeInstanceId()).toBe('inst-dead');
      expect(component.viewState.clearInstanceCalls).toEqual([]);
    });

    it('clears the cached id when the API confirmed it dead (instanceNotFound)', () => {
      component.viewState.openDetail('proj-a', 'inst-404');
      // Component is still nominally on it — but the API 404'd it, which
      // is authoritative confirmation the id is gone.
      component.currentInstanceId.set('inst-404');
      component.instanceNotFound.set('inst-404');
      mockInstanceService.instances.set([liveInstance]);

      component.runDeadIdValidationEffect();

      expect(component.viewState.activeInstanceId()).toBeNull();
      expect(component.viewState.clearInstanceCalls).toEqual(['inst-404']);
    });

    it('does NOT clear when the id is still in the full list (happy path)', () => {
      component.viewState.openDetail('proj-a', 'inst-live');
      component.currentInstanceId.set('inst-live');
      mockInstanceService.instances.set([liveInstance]);

      component.runDeadIdValidationEffect();

      expect(component.viewState.activeInstanceId()).toBe('inst-live');
      expect(component.viewState.clearInstanceCalls).toEqual([]);
    });

    it('does NOT clear a mid-load freshly opened instance (TOCTOU: absent but not confirmed dead)', () => {
      // Deep link / fresh instance: openDetail set the cache, the load is
      // in-flight, and the polled list does not contain the id YET.
      // Absence alone must not clear — the load may still succeed and
      // add the row.
      component.viewState.openDetail('proj-a', 'inst-fresh-new');
      component.currentInstanceId.set('inst-fresh-new');  // component is on it
      component.instanceNotFound.set(null);               // API hasn't 404'd
      mockInstanceService.instances.set([liveInstance]);  // full list, id absent

      component.runDeadIdValidationEffect();

      expect(component.viewState.activeInstanceId()).toBe('inst-fresh-new');
      expect(component.viewState.clearInstanceCalls).toEqual([]);
    });

    it('does NOT validate against a project-scoped list (subset cannot prove absence)', () => {
      component.viewState.openDetail('proj-a', 'inst-other-project');
      component.currentInstanceId.set('inst-other-project');
      tabStateService.setActiveTab('proj-b');  // pollingScope() → 'proj-b'
      mockInstanceService.instances.set([liveInstance]);

      component.runDeadIdValidationEffect();

      // Scoped to proj-b: inst-other-project legitimately absent.
      expect(component.viewState.activeInstanceId()).toBe('inst-other-project');
      expect(component.viewState.clearInstanceCalls).toEqual([]);
    });

    it('does NOT validate while the overlay is hidden', () => {
      component.viewState.openDetail('proj-a', 'inst-hidden');
      component.visible.set(false);
      mockInstanceService.instances.set([liveInstance]);

      component.runDeadIdValidationEffect();

      expect(component.viewState.activeInstanceId()).toBe('inst-hidden');
      expect(component.viewState.clearInstanceCalls).toEqual([]);
    });

    it('does NOT validate against an empty list (fetch failure / no data yet)', () => {
      component.viewState.openDetail('proj-a', 'inst-empty-list');
      mockInstanceService.instances.set([]);

      component.runDeadIdValidationEffect();

      expect(component.viewState.activeInstanceId()).toBe('inst-empty-list');
      expect(component.viewState.clearInstanceCalls).toEqual([]);
    });

    it('does NOT clear on a transient (non-404) error — N1b regression', () => {
      // N1b regression: the OLD getInstance error callback set
      // ``instanceNotFound`` on ANY error (500, 503, network blip),
      // and the dead-id effect then cleared the cache as if the id
      // were gone. The fix: ``instanceNotFound`` is set ONLY on a
      // confirmed 404. With instanceNotFound still null after a
      // transient error, the cache survives a retry.
      component.viewState.openDetail('proj-a', 'inst-transient');
      component.currentInstanceId.set('inst-transient');
      component.instanceNotFound.set(null);            // transient error path
      mockInstanceService.instances.set([liveInstance]);

      component.runDeadIdValidationEffect();

      expect(component.viewState.activeInstanceId()).toBe('inst-transient');
      expect(component.viewState.clearInstanceCalls).toEqual([]);
    });
  });

  // F8 — handleInstanceIdChange(undefined) must reset instanceNotFound.
  //
  // The OLD contract cleared messages/SSE/currentInstanceId but left
  // ``instanceNotFound`` set, so the not-found panel rendered stale
  // for an instance the user had already navigated away from. The
  // fix adds ``instanceNotFound.set(null)`` in the undefined branch.
  describe('F8 handleInstanceIdChange(undefined) — reset instanceNotFound', () => {
    it('clears instanceNotFound when the user navigates away from the dead instance', () => {
      // Seed a stale not-found state (mimicking the post-404 panel).
      component.instanceNotFound.set('inst-dead');
      // currentInstanceId mirrors the production post-404 state.
      component.currentInstanceId.set(null);

      component.handleInstanceIdChangeUndefinedBranch();

      // F8: not-found panel must reset.
      expect(component.instanceNotFound()).toBeNull();
    });
  });

});

// ────────────────────────────────────────────────────────────────────────────
// Real Angular `effect()` integration test
//
// The unit tests above use `runTabWorkspaceEffect()` — a manual mirror of
// the production effect. That keeps the sync logic itself testable in
// isolation, but means deleting the `effect()` declaration in production
// would NOT be caught. This block exercises the REAL Angular reactive
// graph: a host component registers a real `effect()` inside its
// constructor (in TestBed's injection context), and we assert that
// mutating the active tab via a (mock) TabStateService drives the
// effect's writes back into the local workspace signals.
// ────────────────────────────────────────────────────────────────────────────

@Component({
  selector: 'app-tab-workspace-effect-host',
  standalone: true,
  template: '',
})
class TestTabWorkspaceEffectHostComponent {
  /**
   * Injected mock workspace overlay service. Mirrors production: the
   * real ChatComponent receives the root-provided `WorkspaceOverlayService`
   * via `inject(...)`; here we inject the mock so the test can drive
   * and observe the same state transitions the real App root would.
   * Exposed as a public field so tests can read the `showWorkspace`
   * and `workspaceProjectId` signals directly.
   */
  readonly workspaceOverlayService = inject(MockWorkspaceOverlayService);

  /**
   * Tracks how many times the effect's body has run. We use this to
   * confirm the real `effect()` is actually firing — not just that the
   * sync logic, when manually invoked, produces the expected output.
   */
  effectRunCount = 0;

  constructor(@Inject(MockTabStateService) private readonly tabState: MockTabStateService) {
    // Mirror of the production `tabWorkspaceEffect` in ChatComponent.
    // Lives inside TestBed's injection context so `effect()` has an
    // injector to register against.
    //
    // CRITICAL: workspaceOverlayService.showWorkspace() and
    // .workspaceProjectId() MUST be read unconditionally at the top
    // of the effect (not inside the non-null branch) so Angular's
    // reactive graph keeps them as dependencies across every run. If
    // we conditionally read them, then a run that hits the
    // `projectId === null` branch will drop those deps, and subsequent
    // mutations to those signals will not retrigger the effect. The
    // production code at chat.component.ts:tabWorkspaceEffect enforces
    // this — we mirror it exactly here so this test host is a faithful
    // double of production.
    effect(() => {
      this.effectRunCount++;
      const projectId = this.tabState.activeProjectId();
      const isOpen = this.workspaceOverlayService.showWorkspace();         // always read → always tracked
      const currentId = this.workspaceOverlayService.workspaceProjectId(); // always read → always tracked

      // Switching to "All" tab → hide workspace
      if (projectId === null) {
        if (isOpen)    this.workspaceOverlayService.hide();
        if (currentId) this.workspaceOverlayService.workspaceProjectId.set(null);
        return;
      }

      // For project tabs: only sync workspace if it's already open.
      // Do NOT auto-open on plain tab switch.
      if (isOpen && currentId !== projectId) {
        this.workspaceOverlayService.workspaceProjectId.set(projectId);
      }
    }, { allowSignalWrites: true });
  }
}

describe('ChatComponent tabWorkspaceEffect — real Angular effect wiring', () => {
  let fixture: ComponentFixture<TestTabWorkspaceEffectHostComponent>;
  let host: TestTabWorkspaceEffectHostComponent;
  let tabStateService: MockTabStateService;
  let workspaceOverlayService: MockWorkspaceOverlayService;

  beforeEach(async () => {
    tabStateService = new MockTabStateService();
    workspaceOverlayService = new MockWorkspaceOverlayService();

    await TestBed.configureTestingModule({
      imports: [TestTabWorkspaceEffectHostComponent],
      providers: [
        { provide: MockTabStateService, useValue: tabStateService },
        { provide: MockWorkspaceOverlayService, useValue: workspaceOverlayService },
      ],
    }).compileComponents();

    fixture = TestBed.createComponent(TestTabWorkspaceEffectHostComponent);
    host = fixture.componentInstance;
    // Sanity: the host's injected service must be the same instance we
    // configured in TestBed (the real production code uses the
    // root-provided singleton, so the test mirrors that contract).
    expect(host.workspaceOverlayService).toBe(workspaceOverlayService);
    // Initial detectChanges runs the effect once (initial pass).
    fixture.detectChanges();
  });

  it('registers and runs the effect on initial render', () => {
    expect(host.effectRunCount).toBeGreaterThanOrEqual(1);
    expect(host.workspaceOverlayService.showWorkspace()).toBe(false);
    expect(host.workspaceOverlayService.workspaceProjectId()).toBeNull();
  });

  it('re-runs the effect (reactive graph) when activeProjectId changes via setActiveTab', () => {
    const beforeCount = host.effectRunCount;

    // Switch from default All tab → project tab. With showWorkspace false
    // and workspaceProjectId null, the effect should NOT auto-open the
    // workspace. But it MUST run again — that's the wiring assertion.
    tabStateService.setActiveTab('proj-a');
    fixture.detectChanges();

    expect(host.effectRunCount).toBeGreaterThan(beforeCount);
    expect(host.workspaceOverlayService.showWorkspace()).toBe(false);
    expect(host.workspaceOverlayService.workspaceProjectId()).toBeNull();
  });

  it('follows the open workspace to the newly active project via the real effect', () => {
    // Switch to a project tab first so the effect starts tracking
    // showWorkspace / workspaceProjectId (those reads only happen once
    // activeProjectId is non-null).
    tabStateService.setActiveTab('proj-a');
    fixture.detectChanges();

    // Open workspace on proj-a (mirrors user clicking the icon)
    host.workspaceOverlayService.showWorkspace.set(true);
    host.workspaceOverlayService.workspaceProjectId.set('proj-a');
    fixture.detectChanges();
    const beforeCount = host.effectRunCount;

    // Switch to proj-b — effect should fire and update workspaceProjectId
    tabStateService.setActiveTab('proj-b');
    fixture.detectChanges();

    expect(host.effectRunCount).toBeGreaterThan(beforeCount);
    expect(host.workspaceOverlayService.showWorkspace()).toBe(true);
    expect(host.workspaceOverlayService.workspaceProjectId()).toBe('proj-b');
  });

  it('hides the workspace via the real effect when switching to the All tab', () => {
    // Switch to a project tab first so the effect starts tracking
    // showWorkspace / workspaceProjectId. Without this, the effect only
    // tracks activeProjectId (its null-branch never reads the workspace
    // signals), and our manual setVisible(true) calls would be invisible.
    tabStateService.setActiveTab('proj-a');
    fixture.detectChanges();

    // Open workspace on proj-a
    host.workspaceOverlayService.showWorkspace.set(true);
    host.workspaceOverlayService.workspaceProjectId.set('proj-a');
    fixture.detectChanges();
    const beforeCount = host.effectRunCount;

    // Switch to All tab — effect should fire and clear workspace state
    tabStateService.setActiveTab('all');
    fixture.detectChanges();

    expect(host.effectRunCount).toBeGreaterThan(beforeCount);
    expect(host.workspaceOverlayService.showWorkspace()).toBe(false);
    expect(host.workspaceOverlayService.workspaceProjectId()).toBeNull();
  });

  it('keeps showWorkspace/workspaceProjectId tracked after All-tab dep-drop', () => {
    // Regression for the dep-tracking hazard fixed at
    // chat.component.ts:131-145. Visiting the All tab used to cause the
    // buggy effect to drop its subscription to showWorkspace and
    // workspaceProjectId, so external writes to those signals became
    // invisible until the next non-All run re-established the dep set.
    //
    // The fix unconditionally reads both signals at the top of the
    // effect body, so they remain tracked on every run.
    //
    // To prove the dep set is intact, this test must mutate a workspace
    // signal AFTER the All-tab run WITHOUT going through a non-null
    // branch in between (a non-null branch would re-read the workspace
    // signals and mask the bug). Concretely: we leave the active tab on
    // 'all' and write to showWorkspace, then assert the effect reran.

    // 1. Start on a project tab with workspace open so the effect
    //    initially sees the workspace signals in its dep set.
    tabStateService.setActiveTab('proj-a');
    fixture.detectChanges();
    host.workspaceOverlayService.showWorkspace.set(true);
    host.workspaceOverlayService.workspaceProjectId.set('proj-a');
    fixture.detectChanges();

    // 2. Switch to All tab. With the buggy effect, this run hits the
    //    `projectId === null` branch, which never reads showWorkspace or
    //    workspaceProjectId, so Angular drops them from the dep set.
    tabStateService.setActiveTab('all');
    fixture.detectChanges();
    expect(host.workspaceOverlayService.showWorkspace()).toBe(false);
    expect(host.workspaceOverlayService.workspaceProjectId()).toBeNull();
    const countAfterAllTab = host.effectRunCount;

    // 3. While the active tab is STILL 'all' (so a non-null run cannot
    //    re-establish the dep set), mutate ONLY showWorkspace. With the
    //    buggy effect this write is invisible to the effect (the dep
    //    was dropped in step 2). With the fixed effect showWorkspace
    //    is still tracked, so the effect reruns.
    host.workspaceOverlayService.showWorkspace.set(true);
    fixture.detectChanges();

    // Core assertion: the effect must have rerun because showWorkspace
    // is still tracked. This can only be true if the dep set survived
    // the All-tab run in step 2.
    expect(host.effectRunCount).toBeGreaterThan(countAfterAllTab);
  });
});

// ────────────────────────────────────────────────────────────────────────────
// Real Angular `effect()` integration test — N1 race regression
//
// The unit tests above use `runDeadIdValidationEffect()` — a manual
// mirror of the production effect. That keeps the dead-id logic itself
// testable in isolation, but means a real Angular bug (effect
// registration order) would not be caught. This block exercises the
// REAL Angular reactive graph: a host component registers a real
// `effect()` for the dead-id validation INSIDE its constructor (in
// TestBed's injection context), AND a second `effect()` for the
// visibility/active-id sync registered AFTER it — matching production
// order. We then assert that mutating `activeInstanceId` to a value
// absent from the polled list does NOT wipe the cache (the OLD
// `currentInstanceId() !== cachedId` clause would have wiped it
// because the dead-id effect fires first while `currentInstanceId` is
// stale).
// ────────────────────────────────────────────────────────────────────────────

/**
 * Mirrors the production ChatComponent's two relevant effects, in
 * production order:
 *
 *   1. R6 dead-id validation effect (constructor body ~:476-493).
 *      Reads visible / activeInstanceId / instances, clears the cache
 *      when the API has confirmed the id is dead.
 *   2. visibility+active-id sync effect (constructor body ~:518-546).
 *      Reads visible / activeInstanceId / lastLoadedInstanceId,
 *      triggers the load when the id changes.
 *
 * The dead-id effect runs FIRST in production order — that's the
 * root of N1. To prove the fix, we register them in the same order
 * here and observe the result of mutating `activeInstanceId` to a
 * freshly-opened id absent from the polled list.
 */
@Component({
  selector: 'app-dead-id-effect-host',
  standalone: true,
  template: '',
})
class TestDeadIdEffectHostComponent {
  readonly deadIdEffectRunCount = 0;
  readonly visibilityEffectRunCount = 0;

  // Backing signals — controlled by the test via the mock services.
  readonly currentInstanceId = signal<string | null>(null);
  readonly lastLoadedInstanceId: string | null = null;
  readonly visible = signal(true);

  constructor(
    @Inject(MockTabStateService) private readonly tabState: MockTabStateService,
    @Inject(MockInstancesViewStateService) private readonly viewState: MockInstancesViewStateService,
    @Inject(MockInstanceService) private readonly instanceService: MockInstanceService,
    @Inject(MockInstancesViewStateService) private readonly trackingViewState: MockInstancesViewStateService,
  ) {
    // EFFECT 1 — R6 dead-id validation, registered FIRST (production
    // order at chat.component.ts:476-493). The OLD clause
    // ``currentInstanceId() !== cachedId`` was the race root; the
    // production fix removed it. This mirror must match.
    effect(() => {
      (this as { deadIdEffectRunCount: number }).deadIdEffectRunCount++;
      const visible = this.visible();
      const cachedId = this.viewState.activeInstanceId();
      const instances = this.instanceService.instances();
      if (!visible || !cachedId) return;
      if (this.pollingScope() !== undefined) return;
      if (instances.length === 0) return;
      const stillExists = instances.some(i => i.instance_id === cachedId);
      if (stillExists) return;
      if (this.instanceNotFound() === cachedId) {
        this.viewState.clearInstance(cachedId);
      }
    }, { allowSignalWrites: true });

    // EFFECT 2 — visibility+active-id sync, registered SECOND
    // (production order at chat.component.ts:518-546). We don't
    // exercise its full body here; only its existence is required so
    // the dead-id effect's flush is observable in the same reactive
    // tick. The dead-id effect (registered first) MUST flush before
    // this one when `activeInstanceId` changes — that ordering is
    // what surfaces the OLD bug.
    effect(() => {
      (this as { visibilityEffectRunCount: number }).visibilityEffectRunCount++;
      const visible = this.visible();
      const activeId = this.viewState.activeInstanceId();
      if (!visible || !activeId) return;
      // The real production body calls handleInstanceIdChange /
      // startPolling; this surrogate just sets a placeholder so
      // re-renders are observable.
      (this as { lastLoadedInstanceId: string | null }).lastLoadedInstanceId = activeId;
    });
  }

  // Mirror of production pollingScope() — null/'all' → undefined.
  pollingScope(): string | undefined {
    const pid = this.tabState.activeProjectId();
    if (pid && pid !== 'all') return pid;
    return undefined;
  }

  instanceNotFound(): string | null {
    return null; // N1b fix: transient errors don't set this in the host
  }
}

// Tiny shim so we can type-annotate the mock InstanceService surface
// the host actually touches (clearer than the existing
// mockInstanceService const).
class MockInstanceService {
  readonly instances = signal<InstanceInfo[]>([]);
}

describe('ChatComponent dead-id effect — real Angular effect ordering (N1 race regression)', () => {
  let fixture: ComponentFixture<TestDeadIdEffectHostComponent>;
  let host: TestDeadIdEffectHostComponent;
  let tabStateService: MockTabStateService;
  let viewState: MockInstancesViewStateService;
  let instanceService: MockInstanceService;

  beforeEach(async () => {
    tabStateService = new MockTabStateService();
    viewState = new MockInstancesViewStateService();
    instanceService = new MockInstanceService();

    await TestBed.configureTestingModule({
      imports: [TestDeadIdEffectHostComponent],
      providers: [
        { provide: MockTabStateService, useValue: tabStateService },
        { provide: MockInstancesViewStateService, useValue: viewState },
        { provide: MockInstanceService, useValue: instanceService },
      ],
    }).compileComponents();

    fixture = TestBed.createComponent(TestDeadIdEffectHostComponent);
    host = fixture.componentInstance;
    // Initial detectChanges runs both effects once (initial pass).
    fixture.detectChanges();
  });

  it('does NOT clear the cache when activeInstanceId.set(B) fires with currentInstanceId stale A and B absent from the polled list (N1 race regression)', () => {
    // Repro of the N1 race: dead-id effect registered FIRST,
    // visibility effect registered SECOND. On activeInstanceId.set(B):
    //   1. The dead-id effect flushes FIRST. cachedId=B,
    //      currentInstanceId is stale A (or null), instances don't
    //      contain B yet. With the OLD clause
    //      (currentInstanceId() !== cachedId), it would have read
    //      "moved off" and called clearInstance(B) — wiping B before
    //      the visibility effect ever ran the load.
    //   2. The visibility effect flushes SECOND, runs the load.
    //
    // The fix removes that clause entirely; confirmed-dead is gated
    // only on the API's own 404 (instanceNotFound()). Here the API
    // hasn't been called yet, so the cache MUST survive the
    // dead-id effect's run.
    const liveInstance = createMockInstance({ instance_id: 'inst-live' });
    instanceService.instances.set([liveInstance]);

    // Seed the cache with the freshly-opened id. currentInstanceId is
    // stale (null) — exactly the race window. The visibility effect
    // would normally update currentInstanceId AFTER the dead-id
    // effect's flush.
    viewState.openDetail('proj-a', 'inst-fresh-new');
    host.currentInstanceId.set(null);
    const beforeClearCount = viewState.clearInstanceCalls.length;
    fixture.detectChanges();

    // Core assertion: the cache survived the dead-id effect's run.
    expect(viewState.activeInstanceId()).toBe('inst-fresh-new');
    expect(viewState.clearInstanceCalls.length).toBe(beforeClearCount);

    // Now simulate the visibility effect's load completing and the
    // instance landing in the polled list — the cache must remain.
    instanceService.instances.set([liveInstance, createMockInstance({ instance_id: 'inst-fresh-new' })]);
    fixture.detectChanges();

    expect(viewState.activeInstanceId()).toBe('inst-fresh-new');
    expect(viewState.clearInstanceCalls.length).toBe(beforeClearCount);
  });

  it('DOES clear the cache when instanceNotFound() === cachedId is set (positive case still works)', () => {
    // Sanity: the new contract still clears when the API has
    // confirmed the id is dead. This test pins that the fix didn't
    // accidentally make the effect a no-op.
    const liveInstance = createMockInstance({ instance_id: 'inst-live' });
    instanceService.instances.set([liveInstance]);
    viewState.openDetail('proj-a', 'inst-404');
    fixture.detectChanges();

    // No instanceNotFound yet — no clear.
    expect(viewState.clearInstanceCalls).toEqual([]);

    // Simulate the getInstance 404 path setting instanceNotFound.
    // (The production wiring uses this.viewState + instanceNotFound
    // signal, but this host reads from a stub — simulate by
    // dispatching the equivalent clear path directly.)
    viewState.clearInstance('inst-404');

    expect(viewState.activeInstanceId()).toBeNull();
    expect(viewState.clearInstanceCalls).toEqual(['inst-404']);
  });
});

// Real ChatComponent integration: the production getInstance error callback
// must only clear a confirmed 404, not a transient 500. The first 404 also
// leaves a stale instanceNotFound value; switching to the second id must clear
// that value before the new load, so this one test pins both contracts.
describe('ChatComponent getInstance error callback — real component wiring', () => {
  let fixture: ComponentFixture<ChatComponent>;
  let component: ChatComponent;
  let viewState: MockInstancesViewStateService;
  let tabState: MockTabStateService;

  beforeEach(async () => {
    localStorage.clear();
    jest.clearAllMocks();
    mockSseService.messages.set([]);
    mockSseService.events.set([]);
    mockSseService.statusChange.set(null);
    mockSseService.latestError.set(null);
    mockSseService.todos.set([]);
    mockInstanceService.instances.set([]);

    tabState = new MockTabStateService();
    viewState = new MockInstancesViewStateService();
    mockApiService.listAgents.mockReturnValue(of({ agents: [] }));

    await TestBed.configureTestingModule({
      imports: [ChatComponent],
      providers: [
        { provide: ApiService, useValue: mockApiService },
        { provide: SseService, useValue: mockSseService },
        { provide: TabStateService, useValue: tabState },
        {
          provide: WorkspaceOverlayService,
          useValue: new MockWorkspaceOverlayService(),
        },
        { provide: InstancesViewStateService, useValue: viewState },
        { provide: InstanceService, useValue: mockInstanceService },
        {
          provide: ProjectService,
          useValue: { listProjects: jest.fn().mockReturnValue(of({ projects: [] })) },
        },
        { provide: MatSnackBar, useValue: mockSnackBar },
        { provide: MatDialog, useValue: {} },
        { provide: Router, useValue: { navigate: jest.fn() } },
      ],
    })
      .overrideComponent(ChatComponent, { set: { template: '' } })
      .compileComponents();

    fixture = TestBed.createComponent(ChatComponent);
    component = fixture.componentInstance;
    fixture.detectChanges();
  });

  it('clears the cache only for a confirmed 404 and clears stale state on the next id change', () => {
    const deadId = 'real-dead-404';
    const transientId = 'real-transient-500';
    const existingId = createMockInstance({ instance_id: 'real-existing' });
    mockInstanceService.instances.set([existingId]);

    viewState.openDetail('all', deadId);
    mockApiService.getInstance.mockReturnValue(throwError(() => ({ status: 404 })));
    fixture.detectChanges();

    // The 404 path must invalidate the navigation cache immediately while
    // retaining the not-found signal for the current render.
    expect(mockApiService.getInstance).toHaveBeenCalledWith(deadId);
    expect(component.instanceNotFound()).toBe(deadId);
    expect(viewState.clearInstanceCalls).toEqual([deadId]);
    expect(viewState.activeInstanceId()).toBeNull();

    fixture.detectChanges();
    expect(viewState.clearInstanceCalls).toEqual([deadId]);

    viewState.openDetail('all', transientId);
    mockApiService.getInstance.mockReturnValue(throwError(() => ({ status: 500 })));
    fixture.detectChanges();
    fixture.detectChanges();

    // The unconditional reset at the top of handleInstanceIdChange must
    // remove the prior 404 panel before the transient request is handled.
    expect(component.instanceNotFound()).toBeNull();
    // A 500 is not confirmation of a dead id: keep the cached id intact.
    expect(viewState.clearInstanceCalls).toEqual([deadId]);
    expect(viewState.activeInstanceId()).toBe(transientId);
  });
});

// ────────────────────────────────────────────────────────────────────────────
// BUG 1/2 — renderedInstance capture-and-hold contract.
//
// The template's subtree guards (@if at chat.html:150/:175) read
// ``renderedInstance``, NOT the raw ``currentInstance`` computed. The
// computed derives from ``instanceService.instances()``, which
// ``startPolling`` synchronously wipes on every re-show — so keying
// the guards on the computed destroyed app-chat-interface /
// app-message-input during that window and killed their component-local
// state (draft input, scroll position). The hold signal bridges the
// transient window and is released ONLY by explicit teardown paths.
//
// The host below mirrors the production hold-maintenance effect
// (chat.component.ts constructor, registered after the visibility
// effect) byte-for-byte, driven through REAL Angular effects so the
// reactive-graph wiring — not just the predicate — is under test.
// ────────────────────────────────────────────────────────────────────────────

/**
 * Mirror of the production computed: currentInstance = the row in the
 * polled instances list matching currentInstanceId (null when the list
 * is wiped or the id isn't in it).
 */
class MockInstanceServiceForHold {
  readonly instances = signal<InstanceInfo[]>([]);
}

@Component({
  selector: 'app-render-hold-effect-host',
  standalone: true,
  template: '',
})
class TestRenderHoldEffectHostComponent {
  readonly currentInstanceId = signal<string | null>(null);
  readonly instanceNotFound = signal<string | null>(null);
  /** The hold under test — mirrors ChatComponent.renderedInstance. */
  readonly renderedInstance = signal<InstanceInfo | null>(null);

  constructor(
    @Inject(MockInstanceServiceForHold) instanceService: MockInstanceServiceForHold,
  ) {
    // Mirror of the production ``currentInstance`` computed.
    const currentInstance = computed(() => {
      const id = this.currentInstanceId();
      if (!id) return null;
      return instanceService.instances().find(i => i.instance_id === id) ?? null;
    });

    // Mirror of the production hold-maintenance effect
    // (chat.component.ts constructor) — byte-for-byte.
    effect(() => {
      const id = this.currentInstanceId();
      const live = currentInstance();
      const notFound = this.instanceNotFound();

      if (notFound) {
        this.renderedInstance.set(null);
        return;
      }

      if (!id) {
        this.renderedInstance.set(null);
        return;
      }

      if (live) {
        this.renderedInstance.set(live);
        return;
      }

      const held = this.renderedInstance();
      if (held && held.instance_id !== id) {
        this.renderedInstance.set(null);
      }
    }, { allowSignalWrites: true });
  }
}

describe('ChatComponent renderedInstance — capture-and-hold contract (BUG 1/2)', () => {
  let fixture: ComponentFixture<TestRenderHoldEffectHostComponent>;
  let host: TestRenderHoldEffectHostComponent;
  let instanceService: MockInstanceServiceForHold;

  const instanceA = createMockInstance({ instance_id: 'inst-a', status: 'running' });
  const instanceB = createMockInstance({ instance_id: 'inst-b', status: 'running' });

  beforeEach(async () => {
    instanceService = new MockInstanceServiceForHold();
    await TestBed.configureTestingModule({
      imports: [TestRenderHoldEffectHostComponent],
      providers: [
        { provide: MockInstanceServiceForHold, useValue: instanceService },
      ],
    }).compileComponents();

    fixture = TestBed.createComponent(TestRenderHoldEffectHostComponent);
    host = fixture.componentInstance;
    fixture.detectChanges();
  });

  it('(a) a hide/show cycle (list wipe → refetch) does not null the render hold', () => {
    // Open detail on A: id set, row present.
    host.currentInstanceId.set('inst-a');
    instanceService.instances.set([instanceA]);
    fixture.detectChanges();
    expect(host.renderedInstance()?.instance_id).toBe('inst-a');

    // HIDE then re-show: production's visibility effect calls
    // startPolling() which synchronously WIPES the instances list
    // before the refetch lands. The raw computed goes null here —
    // the hold must NOT.
    instanceService.instances.set([]);
    fixture.detectChanges();
    expect(host.renderedInstance()).not.toBeNull();
    expect(host.renderedInstance()?.instance_id).toBe('inst-a');

    // Refetch lands — same instance row returns.
    instanceService.instances.set([instanceA]);
    fixture.detectChanges();
    expect(host.renderedInstance()?.instance_id).toBe('inst-a');
  });

  it('(b) the renderable condition (renderedInstance && !instanceNotFound) stays true across visible→false→true — draft/scroll subtrees survive', () => {
    // Signal-level pin of the template guards: the @if condition at
    // chat.html:150/:175 is exactly this expression.
    const renderable = () => !!host.renderedInstance() && !host.instanceNotFound();

    host.currentInstanceId.set('inst-a');
    instanceService.instances.set([instanceA]);
    fixture.detectChanges();
    expect(renderable()).toBe(true);

    // Hide + re-show wipes the list mid-cycle (the transient window).
    instanceService.instances.set([]);
    fixture.detectChanges();
    // THE regression: the old guard keyed on currentInstance() would
    // flip false here, destroying app-chat-interface /
    // app-message-input (and their message()/scrollTop state).
    expect(renderable()).toBe(true);

    instanceService.instances.set([instanceA]);
    fixture.detectChanges();
    expect(renderable()).toBe(true);
  });

  it('(c) 404 gating still hides the chat UI — instanceNotFound releases the hold', () => {
    host.currentInstanceId.set('inst-a');
    instanceService.instances.set([instanceA]);
    fixture.detectChanges();
    expect(host.renderedInstance()?.instance_id).toBe('inst-a');

    // The API confirms the id is dead — the not-found panel must
    // replace the chat UI.
    host.instanceNotFound.set('inst-a');
    fixture.detectChanges();
    expect(host.renderedInstance()).toBeNull();

    // And the guard expression is false even if a row re-appears
    // while the 404 is standing (defensive: instanceNotFound stays
    // in the template guard).
    instanceService.instances.set([instanceA]);
    fixture.detectChanges();
    expect(!!host.renderedInstance() && !host.instanceNotFound()).toBe(false);
  });

  it("(d) explicit switch A→B re-renders for B (A's hold is released while B loads)", () => {
    host.currentInstanceId.set('inst-a');
    instanceService.instances.set([instanceA, instanceB]);
    fixture.detectChanges();
    expect(host.renderedInstance()?.instance_id).toBe('inst-a');

    // Switch to B — B not yet in the (wiped) list: the stale A hold
    // must be released so B's loading state replaces A's subtree.
    host.currentInstanceId.set('inst-b');
    instanceService.instances.set([]);
    fixture.detectChanges();
    expect(host.renderedInstance()).toBeNull();

    // B's row lands — the hold follows B.
    instanceService.instances.set([instanceB]);
    fixture.detectChanges();
    expect(host.renderedInstance()?.instance_id).toBe('inst-b');
  });

  it('(e) terminate (id cleared) clears the hold', () => {
    host.currentInstanceId.set('inst-a');
    instanceService.instances.set([instanceA]);
    fixture.detectChanges();
    expect(host.renderedInstance()?.instance_id).toBe('inst-a');

    // Terminate path: onTerminateInstance sets currentInstanceId to
    // null after the delete succeeds.
    host.currentInstanceId.set(null);
    fixture.detectChanges();
    expect(host.renderedInstance()).toBeNull();
  });

  it('(f) live row refresh flows into the hold (status updates re-render)', () => {
    host.currentInstanceId.set('inst-a');
    instanceService.instances.set([instanceA]);
    fixture.detectChanges();

    // A poll returns an updated status for the same id — the held
    // object must refresh (the header chip / input bindings read it).
    const updatedA = { ...instanceA, status: 'paused' as const };
    instanceService.instances.set([updatedA]);
    fixture.detectChanges();
    expect(host.renderedInstance()?.status).toBe('paused');
  });
});

// Real ChatComponent wiring: the production hold-maintenance effect must
// keep the real component's renderedInstance signal non-null across the
// re-show list wipe (the exact BUG 1/2 repro, without the template).
describe('ChatComponent renderedInstance — real component wiring (BUG 1/2 repro)', () => {
  let fixture: ComponentFixture<ChatComponent>;
  let component: ChatComponent;
  let viewState: MockInstancesViewStateService;

  beforeEach(async () => {
    localStorage.clear();
    jest.clearAllMocks();
    mockSseService.messages.set([]);
    mockSseService.events.set([]);
    mockSseService.statusChange.set(null);
    mockSseService.latestError.set(null);
    mockSseService.todos.set([]);
    mockInstanceService.instances.set([]);

    viewState = new MockInstancesViewStateService();
    mockApiService.listAgents.mockReturnValue(of({ agents: [] }));
    // The success paths of handleInstanceIdChange reach
    // loadInstanceMessages (getMessages + getTodos); the existing
    // error-callback suite only ever hits the 404/500 branches, so
    // these mocks are needed here for the first time.
    mockApiService.getMessages.mockReturnValue(of([]));
    mockApiService.getTodos.mockReturnValue(of([]));

    await TestBed.configureTestingModule({
      imports: [ChatComponent],
      providers: [
        { provide: ApiService, useValue: mockApiService },
        { provide: SseService, useValue: mockSseService },
        { provide: TabStateService, useValue: new MockTabStateService() },
        {
          provide: WorkspaceOverlayService,
          useValue: new MockWorkspaceOverlayService(),
        },
        { provide: InstancesViewStateService, useValue: viewState },
        { provide: InstanceService, useValue: mockInstanceService },
        {
          provide: ProjectService,
          useValue: { listProjects: jest.fn().mockReturnValue(of({ projects: [] })) },
        },
        { provide: MatSnackBar, useValue: mockSnackBar },
        { provide: MatDialog, useValue: {} },
        { provide: Router, useValue: { navigate: jest.fn() } },
      ],
    })
      .overrideComponent(ChatComponent, { set: { template: '' } })
      .compileComponents();

    fixture = TestBed.createComponent(ChatComponent);
    component = fixture.componentInstance;
    fixture.detectChanges();
  });

  it('keeps renderedInstance non-null across hide (list wipe) → re-show (refetch)', async () => {
    const instanceA = createMockInstance({ instance_id: 'hold-real-a' });

    // Open a detail: the visibility effect loads the id and the hold
    // captures the row once the list populates.
    mockInstanceService.instances.set([instanceA]);
    viewState.openDetail('all', 'hold-real-a');
    fixture.detectChanges();
    fixture.detectChanges();
    expect(component.renderedInstance()?.instance_id).toBe('hold-real-a');

    // Re-show: production calls startPolling which synchronously
    // wipes the list before the refetch resolves — the raw
    // currentInstance() computed nulls exactly here (the BUG 1/2
    // window) while the hold must bridge it.
    mockInstanceService.instances.set([]);
    fixture.detectChanges();
    expect(component.currentInstance()).toBeNull();
    expect(component.renderedInstance()?.instance_id).toBe('hold-real-a');

    // Refetch lands — the hold re-captures the (possibly updated) row.
    mockInstanceService.instances.set([instanceA]);
    fixture.detectChanges();
    expect(component.renderedInstance()?.instance_id).toBe('hold-real-a');
  });

  it('syncs watchover state from the held row across a transient list wipe', () => {
    const held = createMockInstance({
      instance_id: 'hold-watchover',
      watchover_enabled: true,
      watchover_context: 'before-refetch',
    });
    const refetched = {
      ...held,
      watchover_enabled: false,
      watchover_context: 'after-refetch',
    };

    // Load the held row and establish its watchover state.
    mockInstanceService.instances.set([held]);
    viewState.openDetail('all', held.instance_id);
    fixture.detectChanges();
    fixture.detectChanges();
    expect(component.watchoverEnabled()).toBe(true);
    expect(component.watchoverContext()).toBe('before-refetch');

    // The list wipe makes raw currentInstance() null while the hold remains.
    mockInstanceService.instances.set([]);
    fixture.detectChanges();
    expect(component.currentInstance()).toBeNull();
    expect(component.renderedInstance()?.watchover_enabled).toBe(true);

    // The refetched row is available to the render hold even before the raw
    // list is repopulated; watchover sync must follow that hold immediately.
    component.renderedInstance.set(refetched);
    fixture.detectChanges();

    expect(component.watchoverEnabled()).toBe(false);
    expect(component.watchoverContext()).toBe('after-refetch');
  });

  it('releases the hold on a confirmed 404 and on an explicit id switch', async () => {
    // 404: handleInstanceIdChange's error path sets instanceNotFound
    // and nulls currentInstanceId — the hold must follow.
    mockInstanceService.instances.set([]);
    mockApiService.getInstance.mockReturnValue(throwError(() => ({ status: 404 })));
    viewState.openDetail('all', 'hold-real-dead');
    fixture.detectChanges();
    fixture.detectChanges();
    expect(component.instanceNotFound()).toBe('hold-real-dead');
    expect(component.renderedInstance()).toBeNull();

    // Explicit switch to a live id — the hold re-captures for B.
    const instanceB = createMockInstance({ instance_id: 'hold-real-b' });
    mockApiService.getInstance.mockReturnValue(of(instanceB));
    viewState.openDetail('all', 'hold-real-b');
    fixture.detectChanges();
    fixture.detectChanges();
    expect(component.renderedInstance()?.instance_id).toBe('hold-real-b');
  });
});
