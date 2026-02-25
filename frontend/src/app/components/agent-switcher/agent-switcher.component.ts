import { Component, Input, Output, EventEmitter, signal, HostListener } from '@angular/core';
import { CommonModule } from '@angular/common';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatMenuModule } from '@angular/material/menu';
import { Agent } from '../../models';

const colorMap: Record<string, string> = {
  'accent-amber': '#f59e0b',
  'accent-cyan': '#10a7f7',
  'accent-violet': '#8b5cf6',
  'accent-emerald': '#10b981',
  'accent-rose': '#f43f5e',
  'accent-blue': '#3b82f6',
};

@Component({
  selector: 'app-agent-switcher',
  standalone: true,
  imports: [CommonModule, MatButtonModule, MatIconModule, MatMenuModule],
  templateUrl: './agent-switcher.html',
  styleUrl: './agent-switcher.scss'
})
export class AgentSwitcherComponent {
  @Input() agents: Agent[] = [];
  @Input() selectedAgent: Agent | null = null;
  @Output() agentChange = new EventEmitter<Agent>();

  isOpen = signal(false);

  getAgentColor(agent: Agent): string {
    return colorMap[agent.color] || agent.color || '#10a7f7';
  }

  get activeColor(): string {
    return this.selectedAgent ? this.getAgentColor(this.selectedAgent) : '#10a7f7';
  }

  toggleDropdown(): void {
    this.isOpen.update(v => !v);
  }

  selectAgent(agent: Agent): void {
    this.agentChange.emit(agent);
    this.isOpen.set(false);
  }

  @HostListener('document:click', ['$event'])
  onDocumentClick(event: MouseEvent): void {
    const target = event.target as HTMLElement;
    if (!target.closest('.agent-switcher-container')) {
      this.isOpen.set(false);
    }
  }
}
