import { Component, signal, inject, OnInit, OnDestroy } from '@angular/core';
import { CommonModule } from '@angular/common';
import { Router, RouterModule } from '@angular/router';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { ApiService } from '../../services/api.service';
import { InstanceService } from '../../services/instance.service';
import { InstanceListComponent } from '../../components/instance-list/instance-list.component';
import type { Agent } from '../../models';

@Component({
  selector: 'app-instances',
  standalone: true,
  imports: [
    CommonModule,
    RouterModule,
    MatButtonModule,
    MatIconModule,
    InstanceListComponent
  ],
  templateUrl: './instances.component.html',
  styleUrl: './instances.component.scss'
})
export class InstancesComponent implements OnInit, OnDestroy {
  private readonly api = inject(ApiService);
  private readonly router = inject(Router);
  protected readonly instanceService = inject(InstanceService);

  readonly agents = signal<Agent[]>([]);
  readonly selectedAgent = signal<Agent | null>(null);

  ngOnInit(): void {
    this.loadAgents();
    this.instanceService.startPolling();
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
    this.api.createInstance(agentPath).subscribe({
      next: (instance) => {
        this.router.navigate(['/instances', instance.instance_id]);
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

  protected onAgentChange(agent: Agent): void {
    this.selectedAgent.set(agent);
    localStorage.setItem('ensemble-next-instance-agent', agent.id);
  }
}
