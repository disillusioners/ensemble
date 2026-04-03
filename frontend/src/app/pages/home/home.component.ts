import { Component, signal, computed, inject, OnInit, OnDestroy } from '@angular/core';
import { CommonModule } from '@angular/common';
import { Router } from '@angular/router';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { ApiService } from '../../services/api.service';
import { SseService } from '../../services/sse.service';
import { AgentSelectorComponent } from '../../components/agent-selector/agent-selector.component';
import type { Agent, AgentCreate, InstanceInfo } from '../../models';

const NEXT_AGENT_STORAGE_KEY = 'ensemble-next-instance-agent';

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
  readonly instances = signal<InstanceInfo[]>([]);
  readonly selectedAgent = signal<Agent | null>(null);
  readonly isLoading = signal(false);

  readonly hasInstances = computed(() => this.instances().length > 0);

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
        
        this.loadInstances();
      },
      error: (err) => {
        console.error('Failed to load agents:', err);
        this.isLoading.set(false);
      }
    });
  }

  private loadInstances(): void {
    this.api.listInstances().subscribe({
      next: (response) => {
        this.instances.set(response.instances);
        this.isLoading.set(false);
      },
      error: (err) => {
        console.error('Failed to load instances:', err);
        this.isLoading.set(false);
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

  protected onSelectAgent(agent: Agent): void {
    this.selectedAgent.set(agent);
    localStorage.setItem(NEXT_AGENT_STORAGE_KEY, agent.id);
  }

  protected onCreateInstance(): void {
    const agent = this.selectedAgent();
    if (!agent) return;

    this.isLoading.set(true);
    const agentPath = `./agents/${agent.id}`;
    
    this.api.createInstance(agentPath).subscribe({
      next: (instance) => {
        this.instances.update(prev => [instance, ...prev]);
        this.router.navigate(['/instances', instance.instance_id]);
      },
      error: (err) => {
        console.error('Failed to create instance:', err);
        alert(`Failed to create instance: ${err}`);
        this.isLoading.set(false);
      }
    });
  }

  protected onContinueInstance(instanceId: string): void {
    if (instanceId === 'latest' && this.instances().length > 0) {
      this.router.navigate(['/instances', this.instances()[0].instance_id]);
    } else if (instanceId !== 'latest') {
      this.router.navigate(['/instances', instanceId]);
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
    
    this.api.createInstance(agentPath).subscribe({
      next: (instance) => {
        this.instances.update(prev => [instance, ...prev]);
        this.router.navigate(['/instances', instance.instance_id]);
      },
      error: (err) => {
        console.error('Failed to start Mother instance:', err);
        alert(`Failed to start Mother instance: ${err}`);
        this.isLoading.set(false);
      }
    });
  }
}
