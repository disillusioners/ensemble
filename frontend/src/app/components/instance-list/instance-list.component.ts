import { Component, Input, Output, EventEmitter, signal, computed, input, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { RouterModule } from '@angular/router';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatListModule } from '@angular/material/list';
import { Agent, InstanceInfo } from '../../models';
import { AgentSwitcherComponent } from '../agent-switcher/agent-switcher.component';
import { InstanceService } from '../../services/instance.service';

export interface InstanceTreeNode {
  instance: InstanceInfo;
  children: InstanceTreeNode[];
}

@Component({
  selector: 'app-instance-list',
  standalone: true,
  imports: [CommonModule, RouterModule, MatButtonModule, MatIconModule, MatListModule, AgentSwitcherComponent],
  templateUrl: './instance-list.html',
  styleUrl: './instance-list.scss'
})
export class InstanceListComponent {
  protected readonly instanceService = inject(InstanceService);

  readonly agents = input<Agent[]>([]);
  readonly instances = input<InstanceInfo[]>([]);
  readonly currentInstanceId = input<string | null>(null);
  readonly selectedAgent = input<Agent | null>(null);
  readonly hasMore = input<boolean>(false);
  readonly isLoadingMore = input<boolean>(false);
  @Output() deleteInstance = new EventEmitter<string>();
  @Output() newInstance = new EventEmitter<void>();
  @Output() agentChange = new EventEmitter<Agent>();
  @Output() loadMore = new EventEmitter<void>();
  @Output() pauseInstance = new EventEmitter<string>();

  // Track expanded/collapsed state per instance
  readonly expandedInstances = signal<Set<string>>(new Set());

  // Build tree structure from flat instance list
  readonly instanceTree = computed(() => {
    const instances = this.instances();
    if (!instances?.length) return [];

    const instanceMap = new Map<string, InstanceTreeNode>();
    
    // Create nodes for all instances
    instances.forEach(instance => {
      instanceMap.set(instance.instance_id, { instance, children: [] });
    });

    const rootNodes: InstanceTreeNode[] = [];

    // Build tree by attaching children to parents
    instances.forEach(instance => {
      const node = instanceMap.get(instance.instance_id)!;
      if (instance.parent_id && instanceMap.has(instance.parent_id)) {
        instanceMap.get(instance.parent_id)!.children.push(node);
      } else {
        rootNodes.push(node);
      }
    });

    return rootNodes;
  });

  readonly statusColors: Record<string, { bg: string; text: string }> = {
    idle: { bg: '#4d4d5c', text: '#c5c5d2' },
    running: { bg: '#10b98120', text: '#10b981' },
    waiting: { bg: '#f59e0b20', text: '#f59e0b' },
    paused: { bg: '#8b5cf620', text: '#8b5cf6' },
    error: { bg: '#f43f5e20', text: '#f43f5e' },
    terminated: { bg: '#343541', text: '#6e6e80' },
  };

  getAgentInfo(agentDir: string): Agent | undefined {
    const agentId = agentDir.split('/').pop() || agentDir;
    return this.agents().find(a => a.id === agentId);
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

  onDeleteInstance(instanceId: string, event: Event): void {
    event.preventDefault();
    event.stopPropagation();
    if (confirm('Delete this instance?')) {
      this.deleteInstance.emit(instanceId);
    }
  }

  onPauseInstance(instanceId: string, event: Event): void {
    event.preventDefault();
    event.stopPropagation();
    if (confirm('Pause this running instance?')) {
      this.pauseInstance.emit(instanceId);
    }
  }

  onNewInstance(): void {
    this.newInstance.emit();
  }

  onAgentChange(agent: Agent): void {
    this.agentChange.emit(agent);
  }

  getInstanceIdShort(instanceId: string): string {
    return instanceId.slice(0, 12) + '...';
  }

  isExpanded(instanceId: string): boolean {
    return this.expandedInstances().has(instanceId);
  }

  toggleExpand(instanceId: string, event: Event): void {
    event.preventDefault();
    event.stopPropagation();
    const expanded = this.expandedInstances();
    const newSet = new Set(expanded);
    if (newSet.has(instanceId)) {
      newSet.delete(instanceId);
    } else {
      newSet.add(instanceId);
    }
    this.expandedInstances.set(newSet);
  }

  onLoadMore(): void {
    this.loadMore.emit();
  }
}
