import { Component, signal, computed, inject, OnInit, OnDestroy, effect } from '@angular/core';
import { CommonModule } from '@angular/common';
import { ActivatedRoute, Router, RouterModule } from '@angular/router';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { Subscription } from 'rxjs';
import { ApiService } from '../../services/api.service';
import { SseService } from '../../services/sse.service';
import { InstanceListComponent } from '../../components/instance-list/instance-list.component';
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
  private pollInterval: ReturnType<typeof setInterval> | null = null;
  private routeSubscription: Subscription | null = null;
  private processedSseMessageIds = new Set<string>();

  readonly agents = signal<Agent[]>([]);
  readonly instances = signal<InstanceInfo[]>([]);
  readonly currentInstance = signal<InstanceInfo | null>(null);
  readonly messages = signal<Message[]>([]);
  readonly selectedAgent = signal<Agent | null>(null);
  readonly isSending = signal(false);
  readonly sendError = signal<string | null>(null);
  readonly totalInstances = signal(0);
  readonly hasMoreInstances = signal(false);
  readonly isLoadingMore = signal(false);
  readonly instanceNotFound = signal<string | null>(null);

  // LocalStorage preferences
  readonly showThinking = signal(localStorage.getItem('ensemble-show-thinking') === 'true');
  readonly showToolCalls = signal(localStorage.getItem('ensemble-show-toolcalls') === 'true');

  readonly isStreaming = this.sseService.isStreaming;

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
    this.stopPolling();
    this.sseService.clearEvents();
    this.sseService.disconnect();
    this.messages.set([]);
    this.currentInstance.set(null);
    if (this.routeSubscription) {
      this.routeSubscription.unsubscribe();
    }
  }

  ngOnInit(): void {
    this.loadInitialData();
    this.startPolling();
    
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

        this.loadInstances();
      },
      error: (err) => console.error('Failed to load agents:', err)
    });
  }

  private loadInstances(append: boolean = false): void {
    if (append) {
      this.isLoadingMore.set(true);
    }
    
    const currentInstances = this.instances();
    const offset = append ? currentInstances.length : 0;
    
    this.api.listInstances(100, offset).subscribe({
      next: (response) => {
        if (append) {
          // Deduplicate when appending - filter out instances we already have
          const existingIds = new Set(currentInstances.map(i => i.instance_id));
          const newInstances = response.instances.filter(i => !existingIds.has(i.instance_id));
          this.instances.update(prev => [...prev, ...newInstances]);
        } else {
          // When not appending (polling refresh), merge intelligently
          // Keep any instances we've loaded beyond the first page that still exist
          const responseInstanceIds = new Set(response.instances.map(i => i.instance_id));
          
          // Always preserve instances from loaded pages not in this response
          // (e.g., page 2+ instances when polling only refreshes page 1)
          const extraInstances = currentInstances.filter(
            i => !responseInstanceIds.has(i.instance_id)
          );
          
          if (extraInstances.length > 0) {
            // User has loaded more pages - preserve those instances
            this.instances.set([...response.instances, ...extraInstances]);
            // Recalculate has_more based on what we have vs total
            this.hasMoreInstances.set((response.instances.length + extraInstances.length) < response.total);
          } else {
            // No extra pages loaded - just use the response
            this.instances.set(response.instances);
            this.hasMoreInstances.set(response.has_more);
          }
        }
        this.totalInstances.set(response.total);
        this.isLoadingMore.set(false);
        
        // Check if current instance still exists when instances are loaded
        const currentInstance = this.currentInstance();
        if (currentInstance && !append) {
          const allInstances = this.instances();
          const found = allInstances.find(i => i.instance_id === currentInstance.instance_id);
          if (!found) {
            // Instance not found in list - mark as not found instead of redirecting
            console.warn('[Chat] Current instance not found in instances list:', currentInstance.instance_id);
            this.instanceNotFound.set(currentInstance.instance_id);
          }
        }
      },
      error: (err) => {
        console.error('Failed to load instances:', err);
        this.isLoadingMore.set(false);
      }
    });
  }

  private startPolling(): void {
    this.pollInterval = setInterval(() => {
      this.loadInstances();
    }, 10000);
  }

  private stopPolling(): void {
    if (this.pollInterval) {
      clearInterval(this.pollInterval);
      this.pollInterval = null;
    }
  }

  private handleInstanceIdChange(instanceId: string | undefined): void {
    console.log('[Chat] handleInstanceIdChange called with:', instanceId);
    // Reset sending state when switching instances to prevent input lock
    this.isSending.set(false);
    this.sendError.set(null);

    if (!instanceId) {
      console.log('[Chat] No instanceId, disconnecting SSE');
      this.currentInstance.set(null);
      this.messages.set([]);
      this.sseService.disconnect();
      this.sseService.clearEvents();
      return;
    }

    // Find instance in existing list or load it
    const instance = this.instances().find(i => i.instance_id === instanceId);
    console.log('[Chat] Instance found in list:', !!instance, 'instances count:', this.instances().length);
    if (instance) {
      console.log('[Chat] Using instance from list, connecting SSE');
      this.currentInstance.set(instance);
      this.loadInstanceMessages(instanceId);
    } else {
      // Try to get instance from API
      console.log('[Chat] Instance not in list, fetching from API');
      this.api.getInstance(instanceId).subscribe({
        next: (instanceData) => {
          console.log('[Chat] Got instance from API, connecting SSE');
          this.instanceNotFound.set(null);
          this.currentInstance.set(instanceData);
          this.loadInstanceMessages(instanceId);
        },
        error: (err) => {
          console.warn('[Chat] Instance not found:', instanceId, 'error:', err);
          this.instanceNotFound.set(instanceId);
          this.currentInstance.set(null);
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

  protected onDeleteInstance(instanceId: string): void {
    this.api.deleteInstance(instanceId).subscribe({
      next: () => {
        this.instances.update(prev => prev.filter(i => i.instance_id !== instanceId));
        
        if (this.currentInstance()?.instance_id === instanceId) {
          this.currentInstance.set(null);
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
    this.currentInstance.set(null);
    this.messages.set([]);
    this.processedSseMessageIds.clear();
    this.sseService.disconnect();
    this.sseService.clearEvents();

    const agentPath = `./agents/${agent.id}`;
    
    this.api.createInstance(agentPath).subscribe({
      next: (instance) => {
        this.instances.update(prev => [instance, ...prev]);
        this.router.navigate(['/instances', instance.instance_id]);
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
        // Wait for the message to arrive via SSE - server assigns message_id
        // The SSE service will upsert it when received
      },
      error: (err) => {
        console.error('Failed to send message:', err);
        this.sendError.set(err instanceof Error ? err.message : 'Failed to send message');
        this.isSending.set(false);
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
    if (this.hasMoreInstances() && !this.isLoadingMore()) {
      this.loadInstances(true);
    }
  }

  protected onStopInstance(): void {
    const instanceId = this.currentInstance()?.instance_id;
    if (instanceId) {
      this.api.stopInstance(instanceId).subscribe({ error: (err: any) => console.error('Stop failed:', err) });
    }
  }

  // Expose for template
  protected readonly localStorage = localStorage;
}
