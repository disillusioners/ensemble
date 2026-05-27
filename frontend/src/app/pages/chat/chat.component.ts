import { Component, signal, computed, inject, OnInit, OnDestroy, effect, ViewChild, Signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { ActivatedRoute, Router, RouterModule } from '@angular/router';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
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
import type { Agent, InstanceInfo, Message } from '../../models';

const NEXT_AGENT_STORAGE_KEY = 'ensemble-next-instance-agent';

@Component({
  selector: 'app-chat',
  standalone: true,
  imports: [
    CommonModule,
    RouterModule,
    MatButtonModule,
    MatIconModule,
    MatProgressSpinnerModule,
    InstanceListComponent,
    ProjectTabBarComponent,
    ChatInterfaceComponent,
    MessageInputComponent
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
  private routeSubscription: Subscription | null = null;
  private processedSseMessageIds = new Set<string>();

  readonly agents = signal<Agent[]>([]);
  readonly currentInstanceId = signal<string | null>(null);
  readonly currentInstance: Signal<InstanceInfo | null> = computed(() => {
    const id = this.currentInstanceId();
    if (!id) return null;
    return this.instanceService.instances().find(i => i.instance_id === id) ?? null;
  });
  readonly messages = signal<Message[]>([]);
  readonly selectedAgent = signal<Agent | null>(null);
  readonly isSending = signal(false);
  readonly sendError = signal<string | null>(null);
  readonly instanceNotFound = signal<string | null>(null);

  private tabEffect = effect(() => {
    const projectId = this.tabStateService.activeProjectId();
    this.instanceService.startPolling(projectId ?? undefined);
  });

  // LocalStorage preferences
  readonly showThinking = signal(localStorage.getItem('ensemble-show-thinking') === 'true');
  readonly showToolCalls = signal(localStorage.getItem('ensemble-show-toolcalls') === 'true');

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

    // SSE messages update the existing message list - only process genuinely new messages
    effect(() => {
      const sseMessages = this.sseService.messages();
      
      // Filter to only truly new messages
      const newMessages = sseMessages.filter(m => !this.processedSseMessageIds.has(m.message_id));
      if (newMessages.length === 0) return;
      
      console.log('[Chat] New SSE messages:', newMessages.length);
      
      // Mark as processed
      newMessages.forEach(m => this.processedSseMessageIds.add(m.message_id));
      
      // Merge: upsert new messages into existing list
      this.messages.update(existing => {
        const result = [...existing];
        for (const msg of newMessages) {
          const idx = result.findIndex(m => m.message_id === msg.message_id);
          if (idx >= 0) {
            result[idx] = msg;
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

    // Find instance in existing list or load it
    const instance = this.instanceService.instances().find(i => i.instance_id === instanceId);
    console.log('[Chat] Instance found in list:', !!instance, 'instances count:', this.instanceService.instances().length);
    if (instance) {
      console.log('[Chat] Using instance from list, connecting SSE');
      this.loadInstanceMessages(instanceId);
    } else {
      // Try to get instance from API
      console.log('[Chat] Instance not in list, fetching from API');
      this.api.getInstance(instanceId).subscribe({
        next: (instanceData) => {
          console.log('[Chat] Got instance from API, connecting SSE');
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
   */
  private loadInstanceMessages(instanceId: string): void {
    // Clear processed IDs when loading new instance
    this.processedSseMessageIds.clear();
    
    this.api.getMessages(instanceId).subscribe({
      next: (messages) => {
        console.log('[Chat] Loaded', messages.length, 'messages from API');
        const viewModels = messages.map(m => this.toViewModel(m));
        this.messages.set(viewModels);
        // Track these so SSE doesn't duplicate them
        messages.forEach(m => this.processedSseMessageIds.add(m.message_id));
      },
      error: (err) => {
        console.warn('[Chat] Failed to load messages:', err);
        this.messages.set([]);
      },
      complete: () => {
        // Connect SSE after API messages are loaded
        this.sseService.connect(instanceId);
      }
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
    this.processedSseMessageIds.clear();
    this.sseService.disconnect();
    this.sseService.clearEvents();

    const agentPath = `./agents/${agent.id}`;
    const projectId = this.tabStateService.activeProjectId() ?? undefined;

    this.api.createInstance(agentPath, undefined, projectId).subscribe({
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

  protected onAgentChange(agent: Agent): void {
    this.selectedAgent.set(agent);
    localStorage.setItem(NEXT_AGENT_STORAGE_KEY, agent.id);
  }

  protected onSendMessage(payload: MessagePayload): void {
    const instance = this.currentInstance();
    if (!instance) return;

    // Clear any previous error
    this.sendError.set(null);
    this.isSending.set(true);
    
    this.api.sendMessage(instance.instance_id, payload.content, payload.images).subscribe({
      next: (_response) => {
        // Clear input only on success — error recovery keeps input populated
        this.messageInputRef?.clearInput();
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

  protected onToggleThinking(): void {
    this.showThinking.update(v => !v);
  }

  protected onToggleToolCalls(): void {
    this.showToolCalls.update(v => !v);
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
      this.api.resumeInstance(instanceId, message).subscribe({
        next: () => {
          this.messageInputRef?.clearInput();
        },
        error: (err: any) => {
          console.error('Resume failed:', err);
          this.sendError.set('Failed to resume instance. Please try again.');
        }
      });
    }
  }

  protected onResumeInstanceFromList(instanceId: string): void {
    this.api.resumeInstance(instanceId).subscribe({ error: (err: any) => console.error('Resume failed:', err) });
  }

  // Expose for template
  protected readonly localStorage = localStorage;
}
