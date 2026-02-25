import { Component, signal, computed, inject, OnInit, OnDestroy } from '@angular/core';
import { CommonModule } from '@angular/common';
import { Router } from '@angular/router';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { ApiService } from '../../services/api.service';
import { SseService } from '../../services/sse.service';
import { AgentSelectorComponent } from '../../components/agent-selector/agent-selector.component';
import type { Agent, AgentCreate, SessionInfo } from '../../models';

const NEXT_AGENT_STORAGE_KEY = 'ensemble-next-session-agent';

@Component({
  selector: 'app-home',
  standalone: true,
  imports: [CommonModule, MatProgressSpinnerModule, AgentSelectorComponent],
  templateUrl: './home.html',
  styleUrl: './home.scss'
})
export class HomeComponent implements OnInit, OnDestroy {
  private readonly api = inject(ApiService);
  private readonly router = inject(Router);
  private readonly sseService = inject(SseService);
  private pollInterval: ReturnType<typeof setInterval> | null = null;

  readonly agents = signal<Agent[]>([]);
  readonly sessions = signal<SessionInfo[]>([]);
  readonly selectedAgent = signal<Agent | null>(null);
  readonly isLoading = signal(false);

  readonly hasSessions = computed(() => this.sessions().length > 0);

  ngOnInit(): void {
    this.loadInitialData();
    this.startPolling();
  }

  ngOnDestroy(): void {
    this.stopPolling();
  }

  private loadInitialData(): void {
    this.isLoading.set(true);
    
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
      error: (err) => {
        console.error('Failed to load agents:', err);
        this.isLoading.set(false);
      }
    });
  }

  private loadSessions(): void {
    this.api.listSessions().subscribe({
      next: (response) => {
        this.sessions.set(response.sessions);
        this.isLoading.set(false);
      },
      error: (err) => {
        console.error('Failed to load sessions:', err);
        this.isLoading.set(false);
      }
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

  protected onSelectAgent(agent: Agent): void {
    this.selectedAgent.set(agent);
    localStorage.setItem(NEXT_AGENT_STORAGE_KEY, agent.id);
  }

  protected onCreateSession(): void {
    const agent = this.selectedAgent();
    if (!agent) return;

    this.isLoading.set(true);
    const agentPath = `./agents/${agent.id}`;
    
    this.api.createSession(agentPath).subscribe({
      next: (session) => {
        this.sessions.update(prev => [session, ...prev]);
        this.router.navigate(['/sessions', session.session_id]);
      },
      error: (err) => {
        console.error('Failed to create session:', err);
        alert(`Failed to create session: ${err}`);
        this.isLoading.set(false);
      }
    });
  }

  protected onContinueSession(sessionId: string): void {
    if (sessionId === 'latest' && this.sessions().length > 0) {
      this.router.navigate(['/sessions', this.sessions()[0].session_id]);
    } else if (sessionId !== 'latest') {
      this.router.navigate(['/sessions', sessionId]);
    }
  }

  protected onAddAgent(agentCreate: AgentCreate): void {
    this.api.createAgent(agentCreate).subscribe({
      next: (newAgent) => {
        this.agents.update(prev => [...prev, newAgent]);
      },
      error: (err) => {
        console.error('Failed to create agent:', err);
        alert(`Failed to create agent: ${err}`);
      }
    });
  }

  protected onDeleteAgent(agentId: string): void {
    this.api.deleteAgent(agentId).subscribe({
      next: () => {
        this.agents.update(prev => prev.filter(a => a.id !== agentId));
        
        // Clear selection if deleted agent was selected
        if (this.selectedAgent()?.id === agentId) {
          this.selectedAgent.set(null);
          localStorage.removeItem(NEXT_AGENT_STORAGE_KEY);
        }
      },
      error: (err) => {
        console.error('Failed to delete agent:', err);
        alert(`Failed to delete agent: ${err}`);
      }
    });
  }

  protected onStartMother(): void {
    this.isLoading.set(true);
    const agentPath = './agents/_mother';
    
    this.api.createSession(agentPath).subscribe({
      next: (session) => {
        this.sessions.update(prev => [session, ...prev]);
        this.router.navigate(['/sessions', session.session_id]);
      },
      error: (err) => {
        console.error('Failed to start Mother session:', err);
        alert(`Failed to start Mother session: ${err}`);
        this.isLoading.set(false);
      }
    });
  }
}
