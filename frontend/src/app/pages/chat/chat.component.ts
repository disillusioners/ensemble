import { Component, signal, computed, inject, OnInit, OnDestroy, effect } from '@angular/core';
import { CommonModule } from '@angular/common';
import { ActivatedRoute, Router, RouterModule } from '@angular/router';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { Subscription } from 'rxjs';
import { ApiService } from '../../services/api.service';
import { SseService } from '../../services/sse.service';
import { SessionListComponent } from '../../components/session-list/session-list.component';
import { ChatInterfaceComponent } from '../../components/chat-interface/chat-interface.component';
import { MessageInputComponent } from '../../components/message-input/message-input.component';
import type { Agent, SessionInfo, Message } from '../../models';

const NEXT_AGENT_STORAGE_KEY = 'ensemble-next-session-agent';

@Component({
  selector: 'app-chat',
  standalone: true,
  imports: [
    CommonModule,
    RouterModule,
    MatButtonModule,
    MatIconModule,
    MatProgressSpinnerModule,
    SessionListComponent,
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

  readonly agents = signal<Agent[]>([]);
  readonly sessions = signal<SessionInfo[]>([]);
  readonly currentSession = signal<SessionInfo | null>(null);
  readonly messages = signal<Message[]>([]);
  readonly selectedAgent = signal<Agent | null>(null);
  readonly isSending = signal(false);
  readonly sendError = signal<string | null>(null);
  readonly pendingMessage = signal<Message | null>(null);
  readonly totalSessions = signal(0);
  readonly hasMoreSessions = signal(false);
  readonly isLoadingMore = signal(false);

  // LocalStorage preferences
  readonly showThinking = signal(localStorage.getItem('ensemble-show-thinking') === 'true');
  readonly showToolCalls = signal(localStorage.getItem('ensemble-show-toolcalls') === 'true');

  readonly isStreaming = this.sseService.isStreaming;

  // Computed session agent
  readonly sessionAgent = computed(() => {
    const session = this.currentSession();
    if (!session) return null;
    return this.agents().find(a => session.agent_dir.includes(a.id)) || null;
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

    // Effect to handle SSE completed messages
    effect(() => {
      const latestMessage = this.sseService.latestCompletedMessage();
      console.log('[Chat] completed effect triggered, latestMessage:', latestMessage?.message_id, 'role:', latestMessage?.role);
      if (latestMessage && latestMessage.role === 'assistant') {
        this.messages.update(prev => {
          const existingIndex = prev.findIndex(m => m.message_id === latestMessage.message_id);
          if (existingIndex >= 0) {
            const updated = [...prev];
            updated[existingIndex] = latestMessage;
            console.log('[Chat] Updated existing message at index:', existingIndex);
            return updated;
          }
          console.log('[Chat] Added new message to list');
          return [...prev, latestMessage];
        });
        console.log('[Chat] Setting isSending to false');
        this.isSending.set(false);
      }
    });

    // Effect to handle SSE errors
    effect(() => {
      const latestError = this.sseService.latestError();
      if (latestError) {
        console.error('Message processing error:', latestError);
        this.isSending.set(false);
      }
    });

    // Effect to handle partial/progressive messages
    effect(() => {
      const partialMessages = this.sseService.partialMessages();
      console.log('[Chat] partialMessages effect, size:', partialMessages?.size);
      if (partialMessages && partialMessages.size > 0) {
        // Get the first partial message
        const firstPartial = partialMessages.values().next().value;
        if (firstPartial) {
          console.log('[Chat] Setting pendingMessage, content length:', firstPartial.content?.length);
          this.pendingMessage.set(firstPartial);
        }
      } else {
        console.log('[Chat] Clearing pendingMessage');
        this.pendingMessage.set(null);
      }
    });

    // Effect to handle title updates from SSE
    effect(() => {
      const titleUpdate = this.sseService.titleUpdates();
      if (titleUpdate) {
        this.sessions.update(prev => prev.map(s => 
          s.session_id === titleUpdate.session_id 
            ? { ...s, title: titleUpdate.title }
            : s
        ));
        // Also update currentSession if it matches
        if (this.currentSession()?.session_id === titleUpdate.session_id) {
          this.currentSession.update(s => s ? { ...s, title: titleUpdate.title } : null);
        }
      }
    });
  }

  ngOnDestroy(): void {
    this.stopPolling();
    this.sseService.disconnect();
    if (this.routeSubscription) {
      this.routeSubscription.unsubscribe();
    }
  }

  ngOnInit(): void {
    this.loadInitialData();
    this.startPolling();
    
    // Subscribe to route parameter changes
    this.routeSubscription = this.route.params.subscribe(params => {
      const sessionId = params['sessionId'];
      this.handleSessionIdChange(sessionId);
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

        this.loadSessions();
      },
      error: (err) => console.error('Failed to load agents:', err)
    });
  }

  private loadSessions(append: boolean = false): void {
    if (append) {
      this.isLoadingMore.set(true);
    }
    
    const currentSessions = this.sessions();
    const offset = append ? currentSessions.length : 0;
    
    this.api.listSessions(100, offset).subscribe({
      next: (response) => {
        if (append) {
          // Deduplicate when appending - filter out sessions we already have
          const existingIds = new Set(currentSessions.map(s => s.session_id));
          const newSessions = response.sessions.filter(s => !existingIds.has(s.session_id));
          this.sessions.update(prev => [...prev, ...newSessions]);
        } else {
          // When not appending (polling refresh), merge intelligently
          // Keep any sessions we've loaded beyond the first page that still exist
          const currentSessionIds = new Set(currentSessions.map(s => s.session_id));
          const responseSessionIds = new Set(response.sessions.map(s => s.session_id));
          
          // If user has loaded more pages, preserve sessions not in this response
          if (currentSessions.length > response.sessions.length) {
            const extraSessions = currentSessions.filter(
              s => !responseSessionIds.has(s.session_id)
            );
            this.sessions.set([...response.sessions, ...extraSessions]);
            // Recalculate has_more based on what we have vs total
            this.hasMoreSessions.set((response.sessions.length + extraSessions.length) < response.total);
          } else {
            this.sessions.set(response.sessions);
            this.hasMoreSessions.set(response.has_more);
          }
        }
        this.totalSessions.set(response.total);
        this.isLoadingMore.set(false);
        
        // Check if current session still exists when sessions are loaded
        const currentSession = this.currentSession();
        if (currentSession && !append) {
          const allSessions = this.sessions();
          const found = allSessions.find(s => s.session_id === currentSession.session_id);
          if (!found) {
            // Session not found - navigate to home
            this.router.navigate(['/']);
          }
        }
      },
      error: (err) => {
        console.error('Failed to load sessions:', err);
        this.isLoadingMore.set(false);
      }
    });
  }

  private loadMessages(sessionId: string): void {
    this.api.getMessages(sessionId).subscribe({
      next: (msgs) => {
        this.messages.set(msgs);
      },
      error: (err) => console.error('Failed to load messages:', err)
    });
  }

  private startPolling(): void {
    this.pollInterval = setInterval(() => {
      this.loadSessions();
    }, 10000);
  }

  private stopPolling(): void {
    if (this.pollInterval) {
      clearInterval(this.pollInterval);
      this.pollInterval = null;
    }
  }

  private handleSessionIdChange(sessionId: string | undefined): void {
    console.log('[Chat] handleSessionIdChange called with:', sessionId);
    // Reset sending state when switching sessions to prevent input lock
    this.isSending.set(false);
    this.pendingMessage.set(null);
    this.sendError.set(null);

    if (!sessionId) {
      console.log('[Chat] No sessionId, disconnecting SSE');
      this.currentSession.set(null);
      this.messages.set([]);
      this.sseService.disconnect();
      this.sseService.clearEvents();
      return;
    }

    // Find session in existing list or load it
    const session = this.sessions().find(s => s.session_id === sessionId);
    console.log('[Chat] Session found in list:', !!session, 'sessions count:', this.sessions().length);
    if (session) {
      console.log('[Chat] Using session from list, connecting SSE');
      this.currentSession.set(session);
      this.loadMessages(sessionId);
      this.sseService.clearEvents();
      this.sseService.connect(sessionId);
    } else {
      // Try to get session from API
      console.log('[Chat] Session not in list, fetching from API');
      this.api.getSession(sessionId).subscribe({
        next: (sessionData) => {
          console.log('[Chat] Got session from API, connecting SSE');
          this.currentSession.set(sessionData);
          this.loadMessages(sessionId);
          this.sseService.clearEvents();
          this.sseService.connect(sessionId);
        },
        error: (err) => {
          // Session not found - navigate to home
          console.warn('[Chat] Session not found:', sessionId, 'error:', err);
          this.router.navigate(['/']);
        }
      });
    }
  }

  protected onDeleteSession(sessionId: string): void {
    this.api.deleteSession(sessionId).subscribe({
      next: () => {
        this.sessions.update(prev => prev.filter(s => s.session_id !== sessionId));
        
        if (this.currentSession()?.session_id === sessionId) {
          this.currentSession.set(null);
          this.router.navigate(['/']);
        }
      },
      error: (err) => console.error('Failed to delete session:', err)
    });
  }

  protected onNewSession(): void {
    const agent = this.selectedAgent();
    if (!agent) {
      this.router.navigate(['/']);
      return;
    }

    // Reset state when creating new session
    this.isSending.set(false);
    this.pendingMessage.set(null);
    this.sendError.set(null);
    this.currentSession.set(null);
    this.messages.set([]);
    this.sseService.disconnect();
    this.sseService.clearEvents();

    const agentPath = `./agents/${agent.id}`;
    
    this.api.createSession(agentPath).subscribe({
      next: (session) => {
        this.sessions.update(prev => [session, ...prev]);
        this.router.navigate(['/sessions', session.session_id]);
      },
      error: (err) => {
        console.error('Failed to create session:', err);
      }
    });
  }

  protected onAgentChange(agent: Agent): void {
    this.selectedAgent.set(agent);
    localStorage.setItem(NEXT_AGENT_STORAGE_KEY, agent.id);
  }

  protected onSendMessage(content: string): void {
    const session = this.currentSession();
    if (!session) return;

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
    
    this.api.sendMessage(session.session_id, content).subscribe({
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

  protected onLoadMoreSessions(): void {
    if (this.hasMoreSessions() && !this.isLoadingMore()) {
      this.loadSessions(true);
    }
  }

  // Expose for template
  protected readonly localStorage = localStorage;
}
