import { Component, signal, computed, inject, OnInit, OnDestroy } from '@angular/core';
import { CommonModule } from '@angular/common';
import { Router } from '@angular/router';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { ApiService } from '../../services/api.service';
import { SseService } from '../../services/sse.service';
import { TabStateService } from '../../services/tab-state.service';
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
  private readonly tabStateService = inject(TabStateService);
  private pollInterval: ReturnType<typeof setInterval> | null = null;

  /**
   * Get the current project context for navigation.
   * Returns 'all' when on the All tab, or the project ID otherwise.
   */
  protected getProjectContext(): string {
    return this.tabStateService.activeProjectId() ?? 'all';
  }

  readonly agents = signal<Agent[]>([]);
  readonly instances = signal<InstanceInfo[]>([]);
  readonly selectedAgent = signal<Agent | null>(null);
  readonly defaultAgentVersions = signal<Record<string, string | null>>({});
  /** Phase 3: the AgentSelector child owns the chosen version tag —
   *  each create event carries the tag explicitly in its payload. The
   *  parent does not need to mirror the tag as a separate signal. */
  readonly isLoading = signal(false);

  readonly hasInstances = computed(() => this.instances().length > 0);

  ngOnInit(): void {
    this.api.getDefaultAgentVersions().subscribe({
      next: (response) => this.defaultAgentVersions.set(response.default_versions ?? {}),
      error: () => {}
    });
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

  protected onVersionChange(event: { agentId: string; versionTag: string | null }): void {
    this.defaultAgentVersions.update(map => ({ ...map, [event.agentId]: event.versionTag }));
    this.api.setDefaultAgentVersion(event.agentId, event.versionTag).subscribe({
      error: () => {}
    });
  }

  protected onCreateInstance(payload?: { versionTag?: string | null }): void {
    const agent = this.selectedAgent();
    if (!agent) return;

    this.isLoading.set(true);
    const agentPath = `./agents/${agent.id}`;
    // The version tag is sourced entirely from the createInstance payload
    // (the AgentSelector child owns the tag via its own version picker).
    const versionTag = payload?.versionTag;

    this.api.createInstance(agentPath, undefined, undefined, versionTag ?? undefined).subscribe({
      next: (instance) => {
        this.instances.update(prev => [instance, ...prev]);
        this.router.navigate(['/projects', this.getProjectContext(), 'instances', instance.instance_id]);
      },
      error: (err) => {
        console.error('Failed to create instance:', err);
        alert(`Failed to create instance: ${err}`);
        this.isLoading.set(false);
      }
    });
  }

  protected onContinueInstance(instanceId: string): void {
    const projectId = this.getProjectContext();
    if (instanceId === 'latest' && this.instances().length > 0) {
      this.router.navigate(['/projects', projectId, 'instances', this.instances()[0].instance_id]);
    } else if (instanceId !== 'latest') {
      this.router.navigate(['/projects', projectId, 'instances', instanceId]);
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
        this.router.navigate(['/projects', this.getProjectContext(), 'instances', instance.instance_id]);
      },
      error: (err) => {
        console.error('Failed to start Mother instance:', err);
        alert(`Failed to start Mother instance: ${err}`);
        this.isLoading.set(false);
      }
    });
  }

  protected onQuickCreateInstance(payload: { agent: Agent; versionTag?: string | null }): void {
    this.isLoading.set(true);
    const agentPath = `./agents/${payload.agent.id}`;
    // The AgentSelector forwards the chosen version tag explicitly so the
    // daemon picks the right agent version. No fallback to the agent
    // object's own version_tag — that field is only the tag of the
    // row that the user clicked, not the version they picked in the
    // picker, and would silently create the wrong agent version.
    const tag = payload.versionTag ?? null;

    this.api.createInstance(agentPath, undefined, undefined, tag ?? undefined).subscribe({
      next: (instance) => {
        this.instances.update(prev => [instance, ...prev]);
        this.router.navigate(['/projects', this.getProjectContext(), 'instances', instance.instance_id]);
      },
      error: (err) => {
        console.error('Failed to create instance:', err);
        alert(`Failed to create instance: ${err}`);
        this.isLoading.set(false);
      }
    });
  }

  protected onViewInstances(): void {
    if (this.instances().length > 0) {
      this.router.navigate(['/projects', this.getProjectContext(), 'instances', this.instances()[0].instance_id]);
    }
  }
}
