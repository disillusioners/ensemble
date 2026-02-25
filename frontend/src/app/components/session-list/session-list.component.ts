import { Component, Input, Output, EventEmitter, signal, computed } from '@angular/core';
import { CommonModule } from '@angular/common';
import { RouterModule } from '@angular/router';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatListModule } from '@angular/material/list';
import { Agent, SessionInfo } from '../../models';
import { AgentSwitcherComponent } from '../agent-switcher/agent-switcher.component';

@Component({
  selector: 'app-session-list',
  standalone: true,
  imports: [CommonModule, RouterModule, MatButtonModule, MatIconModule, MatListModule, AgentSwitcherComponent],
  templateUrl: './session-list.html',
  styleUrl: './session-list.scss'
})
export class SessionListComponent {
  @Input() agents: Agent[] = [];
  @Input() sessions: SessionInfo[] = [];
  @Input() currentSessionId: string | null = null;
  @Input() selectedAgent: Agent | null = null;
  @Output() deleteSession = new EventEmitter<string>();
  @Output() newSession = new EventEmitter<void>();
  @Output() agentChange = new EventEmitter<Agent>();

  readonly statusColors: Record<string, { bg: string; text: string }> = {
    idle: { bg: '#4d4d5c', text: '#c5c5d2' },
    running: { bg: '#10b98120', text: '#10b981' },
    waiting: { bg: '#f59e0b20', text: '#f59e0b' },
    error: { bg: '#f43f5e20', text: '#f43f5e' },
    terminated: { bg: '#343541', text: '#6e6e80' },
  };

  getAgentInfo(agentDir: string): Agent | undefined {
    const agentId = agentDir.split('/').pop() || agentDir;
    return this.agents.find(a => a.id === agentId);
  }

  formatDate(dateString: string): string {
    const date = new Date(dateString);
    const now = new Date();
    const diff = now.getTime() - date.getTime();
    
    const minutes = Math.floor(diff / 60000);
    const hours = Math.floor(diff / 3600000);
    const days = Math.floor(diff / 86400000);
    
    if (minutes < 1) return 'Just now';
    if (minutes < 60) return `${minutes}m ago`;
    if (hours < 24) return `${hours}h ago`;
    return `${days}d ago`;
  }

  getStatusStyle(status: string): { backgroundColor: string; color: string } {
    const style = this.statusColors[status] || this.statusColors['idle'];
    return {
      backgroundColor: style.bg,
      color: style.text
    };
  }

  onDeleteSession(sessionId: string, event: Event): void {
    event.preventDefault();
    event.stopPropagation();
    if (confirm('Delete this session?')) {
      this.deleteSession.emit(sessionId);
    }
  }

  onNewSession(): void {
    this.newSession.emit();
  }

  onAgentChange(agent: Agent): void {
    this.agentChange.emit(agent);
  }

  getSessionIdShort(sessionId: string): string {
    return sessionId.slice(0, 12) + '...';
  }
}
