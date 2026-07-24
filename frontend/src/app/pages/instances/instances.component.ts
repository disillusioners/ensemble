import { Component, signal, inject, OnInit, OnDestroy, effect } from '@angular/core';
import { CommonModule } from '@angular/common';
import { Router, RouterModule } from '@angular/router';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { ApiService } from '../../services/api.service';
import { TabStateService } from '../../services/tab-state.service';
import { InstanceService } from '../../services/instance.service';
import { ProjectService } from '../../services/project.service';
import { InstanceListComponent } from '../../components/instance-list/instance-list.component';
import { ProjectTabBarComponent } from '../../components/project-tab-bar/project-tab-bar.component';
import type { Agent } from '../../models';

@Component({
  selector: 'app-instances',
  standalone: true,
  imports: [
    CommonModule,
    RouterModule,
    MatButtonModule,
    MatIconModule,
    InstanceListComponent,
    ProjectTabBarComponent
  ],
  templateUrl: './instances.component.html',
  styleUrl: './instances.component.scss'
})
export class InstancesComponent implements OnInit, OnDestroy {
  private readonly api = inject(ApiService);
  private readonly router = inject(Router);
  protected readonly instanceService = inject(InstanceService);
  private readonly tabStateService = inject(TabStateService);
  private readonly projectService = inject(ProjectService);

  readonly agents = signal<Agent[]>([]);
  readonly selectedAgent = signal<Agent | null>(null);
  /** Phase 3: tag picked in the AgentSwitcher dropdown. Read by
   *  ``onNewInstance`` and forwarded to ``api.createInstance``. */
  readonly selectedVersionTag = signal<string | null>(null);

  private tabEffect = effect(() => {
    const projectId = this.tabStateService.activeProjectId();
    this.instanceService.startPolling(projectId ?? undefined);
  });

  /**
   * Get the current project context for navigation.
   * Returns 'all' when on the All tab, or the project ID otherwise.
   */
  protected getProjectContext(): string {
    return this.tabStateService.activeProjectId() ?? 'all';
  }

  ngOnInit(): void {
    this.projectService.listProjects().subscribe({
      next: (response) => {
        const projectIds = response.projects.map(p => p.project_id);
        this.tabStateService.restoreState(projectIds);
        this.instanceService.startPolling(this.tabStateService.activeProjectId() ?? undefined);
        this.loadAgents();
      },
      error: (err) => {
        console.error('Failed to load projects:', err);
        this.loadAgents();
      }
    });
  }

  ngOnDestroy(): void {
    this.instanceService.stopPolling();
  }

  private loadAgents(): void {
    this.api.listAgents().subscribe({
      next: (response) => {
        this.agents.set(response.agents);
        const savedAgentId = localStorage.getItem('ensemble-next-instance-agent');
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

  protected onBack(): void {
    this.router.navigate(['/']);
  }

  protected onNewInstance(): void {
    const agent = this.selectedAgent();
    if (!agent) {
      this.router.navigate(['/']);
      return;
    }

    const agentPath = `./agents/${agent.id}`;
    const projectId = this.getProjectContext();
    const actualProjectId = projectId === 'all' ? undefined : projectId;
    // Phase 3: forward the chosen version tag (null when none picked —
    // backend falls back to base).
    const versionTag = this.selectedVersionTag() ?? undefined;
    this.api.createInstance(agentPath, undefined, actualProjectId, versionTag).subscribe({
      next: (instance) => {
        this.router.navigate(['/projects', this.getProjectContext(), 'instances', instance.instance_id]);
      },
      error: (err) => console.error('Failed to create instance:', err)
    });
  }

  protected onTerminateInstance(instanceId: string): void {
    this.api.deleteInstance(instanceId).subscribe({
      next: () => {
        // Instance is removed from instanceService via its polling
      },
      error: (err) => console.error('Failed to terminate instance:', err)
    });
  }

  protected onAgentChange(payload: { agent: Agent; versionTag?: string | null }): void {
    this.selectedAgent.set(payload.agent);
    this.selectedVersionTag.set(payload.versionTag ?? null);
    localStorage.setItem('ensemble-next-instance-agent', payload.agent.id);
  }
}
