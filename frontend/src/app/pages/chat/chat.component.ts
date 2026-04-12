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
import { MessageInputComponent } from '../../components/message-input/message-input.component';
import type { Agent, InstanceInfo, Message, MessageDelta } from '../../models';

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
  private messagesSubscription: Subscription | null = null;

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

    // Simplified: Handle message deltas directly - update messages in-place
    // FIX: Swap-and-clear pattern prevents race condition where deltas arrive during processing
    effect(() => {
      const deltas = this.sseService.messageDeltas();
      const currentInstance = this.currentInstance();
      
      if (!currentInstance || deltas.length === 0) return;
      
      console.log('[Chat] Processing', deltas.length, 'message deltas');
      
      // Atomically swap: take ownership and clear immediately to prevent race condition
      this.sseService.messageDeltas.set([]);
      
      // Process all deltas, updating the messages array
      this.messages.update(msgs => {
        let updated = [...msgs];
        
        for (const delta of deltas) {
          // Only process deltas for current instance
          if (delta.instance_id !== currentInstance.instance_id) continue;
          
          let msgIndex = updated.findIndex(m => m.message_id === delta.message_id);
          
          switch (delta.type) {
            case 'processing_started':
              // Only add placeholder if not exists (deduplication)
              if (msgIndex === -1) {
                const placeholder: Message = {
                  type: 'message',
                  message_id: delta.message_id,
                  role: 'assistant',
                  content: '',
                  thinking: undefined,
                  thinking_extracted: undefined,
                  tool_calls: [],
                  created_at: new Date().toISOString(),
                  instance_id: delta.instance_id,
                };
                updated.push(placeholder);
                msgIndex = updated.length - 1;
                console.log('[Chat] Added placeholder for message:', delta.message_id);
              }
              break;
              
            case 'content_chunk':
              // Auto-create placeholder if out of order (FIX: was silently dropped before)
              if (msgIndex === -1) {
                updated.push({
                  type: 'message',
                  message_id: delta.message_id,
                  role: 'assistant',
                  content: '',
                  thinking: undefined,
                  thinking_extracted: undefined,
                  tool_calls: [],
                  created_at: new Date().toISOString(),
                  instance_id: delta.instance_id,
                });
                msgIndex = updated.length - 1;
              }
              // FIX: Enforce immutability with spread operator
              updated[msgIndex] = {
                ...updated[msgIndex],
                content: (updated[msgIndex].content || '') + (delta.content || ''),
              };
              break;
              
            case 'thinking':
              // Auto-create placeholder if out of order
              if (msgIndex === -1) {
                updated.push({
                  type: 'message',
                  message_id: delta.message_id,
                  role: 'assistant',
                  content: '',
                  thinking: '',
                  thinking_extracted: undefined,
                  tool_calls: [],
                  created_at: new Date().toISOString(),
                  instance_id: delta.instance_id,
                });
                msgIndex = updated.length - 1;
              }
              updated[msgIndex] = {
                ...updated[msgIndex],
                thinking: delta.content,
              };
              break;
              
            case 'tool_call':
              // Auto-create placeholder if out of order
              if (msgIndex === -1) {
                updated.push({
                  type: 'message',
                  message_id: delta.message_id,
                  role: 'assistant',
                  content: '',
                  thinking: undefined,
                  thinking_extracted: undefined,
                  tool_calls: [],
                  created_at: new Date().toISOString(),
                  instance_id: delta.instance_id,
                });
                msgIndex = updated.length - 1;
              }
              // FIX: Deduplicate by tool_call.id
              const existingTool = updated[msgIndex].tool_calls?.find(
                tc => tc.id === delta.tool_call?.id
              );
              if (!existingTool) {
                const toolCalls = [...(updated[msgIndex].tool_calls || [])];
                toolCalls.push({
                  id: delta.tool_call?.id || `tool-${Date.now()}`,
                  name: delta.tool_call?.name || '',
                  arguments: delta.tool_call?.arguments || {},
                  output: '',
                });
                updated[msgIndex] = { ...updated[msgIndex], tool_calls: toolCalls };
              }
              break;
              
            case 'tool_complete':
              if (msgIndex >= 0 && delta.tool_call?.id) {
                const toolCalls = (updated[msgIndex].tool_calls || []).map(tc => {
                  if (tc.id === delta.tool_call?.id) {
                    return { ...tc, output: delta.tool_call?.output || '' };
                  }
                  return tc;
                });
                updated[msgIndex] = { ...updated[msgIndex], tool_calls: toolCalls };
              }
              break;
              
            case 'processing_completed':
              console.log('[Chat] Message completed:', delta.message_id, 'success:', delta.success);
              this.isSending.set(false);
              break;
              
            case 'processing_failed':
              console.error('[Chat] Message failed:', delta.message_id, 'error:', delta.error);
              this.isSending.set(false);
              break;
              
            case 'message_completed':
              // Finalize message with canonical content from message_completed event
              console.log('[Chat] Message finalized with canonical content:', delta.message_id);
              if (msgIndex >= 0 && delta.message) {
                updated[msgIndex] = {
                  ...updated[msgIndex],
                  role: (delta.message.role as 'user' | 'assistant' | 'system') || 'assistant',
                  content: delta.message.content ?? updated[msgIndex].content,
                  thinking: delta.message.thinking ?? undefined,
                  thinking_extracted: delta.message.thinking_extracted ?? undefined,
                  tool_calls: delta.message.tool_calls || updated[msgIndex].tool_calls,
                };
              } else {
                console.warn('[Chat] message_completed: message not found in state, creating from canonical payload', delta.message_id);
                // Create message from canonical payload as fallback
                updated.push({
                  type: 'message',
                  message_id: delta.message_id || '',
                  role: (delta.message?.role as 'user' | 'assistant' | 'system') || 'assistant',
                  content: delta.message?.content || '',
                  thinking: delta.message?.thinking ?? undefined,
                  thinking_extracted: delta.message?.thinking_extracted ?? undefined,
                  tool_calls: delta.message?.tool_calls || [],
                  created_at: delta.message?.created_at || new Date().toISOString(),
                });
              }
              break;
          }
        }
        
        return updated;
      });
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

    // Handle title updates from SSE
    effect(() => {
      const titleUpdate = this.sseService.titleUpdates();
      const currentInstance = this.currentInstance();
      if (titleUpdate && titleUpdate.instance_id === currentInstance?.instance_id) {
        this.instances.update(prev => prev.map(i => 
          i.instance_id === titleUpdate.instance_id 
            ? { ...i, title: titleUpdate.title }
            : i
        ));
        if (this.currentInstance()?.instance_id === titleUpdate.instance_id) {
          this.currentInstance.update(i => i ? { ...i, title: titleUpdate.title } : null);
        }
        this.sseService.titleUpdates.set(null);
      }
    }, { allowSignalWrites: true });
    
    // Handle SSE errors
    effect(() => {
      const latestError = this.sseService.latestError();
      const currentInstance = this.currentInstance();
      if (latestError && currentInstance && latestError.instance_id === currentInstance?.instance_id) {
        console.error('Message processing error:', latestError);
        this.isSending.set(false);
        this.sseService.latestError.set(null);
      }
    }, { allowSignalWrites: true });
  }

  ngOnDestroy(): void {
    this.stopPolling();
    if (this.messagesSubscription) {
      this.messagesSubscription.unsubscribe();
      this.messagesSubscription = null;
    }
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

  private loadMessages(instanceId: string): void {
    // FIX: Cancel previous subscription to prevent race conditions
    if (this.messagesSubscription) {
      this.messagesSubscription.unsubscribe();
      this.messagesSubscription = null;
    }
    
    this.messagesSubscription = this.api.getMessages(instanceId).subscribe({
      next: (msgs) => {
        // FIX: Only set messages if still on the same instance
        const currentInstance = this.currentInstance();
        if (currentInstance?.instance_id === instanceId) {
          this.messages.set(msgs);
        }
      },
      error: (err) => console.error('Failed to load messages:', err)
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
      this.messages.set([]);
      this.loadMessages(instanceId);
      this.sseService.connect(instanceId);
    } else {
      // Try to get instance from API
      console.log('[Chat] Instance not in list, fetching from API');
      this.api.getInstance(instanceId).subscribe({
        next: (instanceData) => {
          console.log('[Chat] Got instance from API, connecting SSE');
          this.instanceNotFound.set(null);
          this.currentInstance.set(instanceData);
          this.messages.set([]);
          this.loadMessages(instanceId);
          this.sseService.connect(instanceId);
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

  protected onSendMessage(content: string): void {
    const instance = this.currentInstance();
    if (!instance) return;

    // Clear any previous error
    this.sendError.set(null);

    // Add user message to UI immediately
    const userMessage: Message = {
      type: 'message',
      message_id: `temp-${Date.now()}`,
      role: 'user',
      content,
      created_at: new Date().toISOString(),
    };
    this.messages.update(prev => [...prev, userMessage]);

    this.isSending.set(true);
    
    this.api.sendMessage(instance.instance_id, content).subscribe({
      next: (_response) => {
        // The assistant response will come via SSE
        // Note: We don't need to update the user message's ID since the queue message_id
        // is different from the message IDs used in the UI
      },
      error: (err) => {
        console.error('Failed to send message:', err);
        this.sendError.set(err instanceof Error ? err.message : 'Failed to send message');
        this.messages.update(prev => prev.map(m => 
          m.message_id === userMessage.message_id 
            ? { ...m, error: 'Failed to send' }
            : m
        ));
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
