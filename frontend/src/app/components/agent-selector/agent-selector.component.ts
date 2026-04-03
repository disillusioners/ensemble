import { Component, Input, Output, EventEmitter, signal, computed } from '@angular/core';
import { CommonModule } from '@angular/common';
import { MatCardModule } from '@angular/material/card';
import { MatButtonModule } from '@angular/material/button';
import { MatDialogModule, MatDialog } from '@angular/material/dialog';
import type { Agent, AgentCreate } from '../../models';
import { AddAgentModalComponent } from '../add-agent-modal/add-agent-modal.component';

@Component({
  selector: 'app-agent-selector',
  standalone: true,
  imports: [CommonModule, MatCardModule, MatButtonModule, MatDialogModule],
  templateUrl: './agent-selector.html',
  styleUrl: './agent-selector.scss'
})
export class AgentSelectorComponent {
  @Input() agents: Agent[] = [];
  @Input() selectedAgent: Agent | null = null;
  @Input() hasInstances = false;
  @Input() isLoading = false;

  @Output() selectAgent = new EventEmitter<Agent>();
  @Output() createInstance = new EventEmitter<void>();
  @Output() continueInstance = new EventEmitter<string>();
  @Output() addAgent = new EventEmitter<AgentCreate>();
  @Output() deleteAgent = new EventEmitter<string>();
  @Output() startMother = new EventEmitter<void>();

  protected readonly colorMap: Record<string, string> = {
    'accent-amber': '#f59e0b',
    'accent-cyan': '#10a7f7',
    'accent-violet': '#8b5cf6',
    'accent-emerald': '#10b981',
    'accent-rose': '#f43f5e',
    'accent-blue': '#3b82f6',
    'accent-indigo': '#6366f1',
    'accent-green': '#22c55e',
    'accent-purple': '#a855f7',
  };

  protected isAddModalOpen = signal(false);

  protected filteredAgents = computed(() =>
    this.agents.filter(agent => agent.id !== '_mother')
  );

  protected activeColor = computed(() => {
    return this.selectedAgent ? this.getAgentColor(this.selectedAgent) : '#10a7f7';
  });

  constructor(private dialog: MatDialog) {}

  protected getAgentColor(agent: Agent): string {
    return this.colorMap[agent.color] || agent.color || '#10a7f7';
  }

  protected onSelect(agent: Agent): void {
    this.selectAgent.emit(agent);
  }

  protected onCreateInstance(): void {
    this.createInstance.emit();
  }

  protected onContinueInstance(instanceId: string): void {
    this.continueInstance.emit(instanceId);
  }

  protected onStartMother(): void {
    this.startMother.emit();
  }

  protected onDeleteAgent(agent: Agent, event: Event): void {
    event.stopPropagation();
    if (confirm(`Delete agent "${agent.name}"? This will move it to trash.`)) {
      this.deleteAgent.emit(agent.id);
    }
  }

  protected openAddModal(): void {
    const dialogRef = this.dialog.open(AddAgentModalComponent, {
      width: '480px',
      maxWidth: '95vw',
      panelClass: 'dark-modal-panel',
      data: {}
    });

    dialogRef.afterClosed().subscribe((result: AgentCreate | undefined) => {
      if (result) {
        this.addAgent.emit(result);
      }
    });
  }
}
