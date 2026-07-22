import { Component, Input, Output, EventEmitter, signal, computed, input, inject, ViewChild, ElementRef, effect, AfterViewInit, OnDestroy, DestroyRef } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { CommonModule } from '@angular/common';
import { RouterModule } from '@angular/router';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatListModule } from '@angular/material/list';
import { MatMenuModule } from '@angular/material/menu';
import { MatDialog } from '@angular/material/dialog';
import { Agent, InstanceInfo } from '../../models';
import { AgentSwitcherComponent } from '../agent-switcher/agent-switcher.component';
import { InstanceService } from '../../services/instance.service';
import { InstancePrefsService, COLOR_OPTIONS, ICON_OPTIONS } from '../../services/instance-prefs.service';
import { TabStateService } from '../../services/tab-state.service';
import { InstanceDeleteDialogComponent, InstanceDeleteDialogData } from '../instance-delete-dialog/instance-delete-dialog.component';

export interface InstanceTreeNode {
  instance: InstanceInfo;
  children: InstanceTreeNode[];
}

/**
 * In-place stable sort for a tree level. Pinned rows come first,
 * ordered by ``pinned_at`` DESC (most recently pinned wins ties).
 * Unpinned rows follow, ordered by ``created_at`` DESC (newest first).
 * Recurses into ``children`` so the rule applies at every level.
 *
 * ``pinned_at`` is preferred over ``created_at`` for pinned rows so
 * a user can pin an old instance and have it float above a newly
 * created unpinned one (it also floats above newer pinned rows until
 * the user pins them too).
 */
function sortNodesPinnedFirst(nodes: InstanceTreeNode[]): void {
  nodes.sort((a, b) => {
    const aPinned = a.instance.pinned === true;
    const bPinned = b.instance.pinned === true;

    if (aPinned !== bPinned) {
      // Pinned group always wins.
      return aPinned ? -1 : 1;
    }

    if (aPinned) {
      // Both pinned: most recent pinned_at first.
      const aTime = a.instance.pinned_at ? new Date(a.instance.pinned_at).getTime() : 0;
      const bTime = b.instance.pinned_at ? new Date(b.instance.pinned_at).getTime() : 0;
      if (aTime !== bTime) return bTime - aTime;
    }

    // Same group + same primary key: fall back to created_at DESC.
    const aCreated = a.instance.created_at ? new Date(a.instance.created_at).getTime() : 0;
    const bCreated = b.instance.created_at ? new Date(b.instance.created_at).getTime() : 0;
    return bCreated - aCreated;
  });

  // Recurse so the rule holds at every depth.
  for (const node of nodes) {
    if (node.children.length > 0) {
      sortNodesPinnedFirst(node.children);
    }
  }
}

@Component({
  selector: 'app-instance-list',
  standalone: true,
  imports: [CommonModule, RouterModule, MatButtonModule, MatIconModule, MatListModule, MatMenuModule, AgentSwitcherComponent],
  templateUrl: './instance-list.html',
  styleUrl: './instance-list.scss'
})
export class InstanceListComponent implements AfterViewInit, OnDestroy {
  protected readonly instanceService = inject(InstanceService);
  protected readonly prefsService = inject(InstancePrefsService);
  private readonly tabStateService = inject(TabStateService);
  private readonly dialog = inject(MatDialog);

  // Signal inputs
  readonly agents = input<Agent[]>([]);
  readonly instances = input<InstanceInfo[]>([]);
  readonly currentInstanceId = input<string | null>(null);
  readonly selectedAgent = input<Agent | null>(null);
  readonly hasMore = input<boolean>(false);
  readonly isLoadingMore = input<boolean>(false);

  // Output EventEmitters
  @Output() terminateInstance = new EventEmitter<string>();
  @Output() newInstance = new EventEmitter<void>();
  @Output() agentChange = new EventEmitter<Agent>();
  @Output() loadMore = new EventEmitter<void>();
  @Output() pauseInstance = new EventEmitter<string>();
  @Output() resumeInstance = new EventEmitter<string>();

  @ViewChild('instanceListContainer') private instanceListContainer!: ElementRef<HTMLElement>;

  // Track expanded/collapsed state per instance
  readonly expandedInstances = signal<Set<string>>(new Set());

  // Manual refresh state
  readonly isRefreshing = signal(false);

  // Scroll position tracking
  private scrollTop = 0;
  private isScrolledByUser = false;
  private scrollHandler = () => {
    this.scrollTop = this.instanceListContainer.nativeElement.scrollTop;
    this.isScrolledByUser = this.scrollTop > 0;
  };

  // Build tree structure from flat instance list.
  //
  // Ordering: at each level (roots AND children) pinned instances appear
  // before unpinned ones. Within each group we sort by ``pinned_at`` DESC
  // for pinned (most recently pinned wins) and ``created_at`` DESC for
  // unpinned (newest first). This keeps existing tree-building logic
  // (parent-child via ``parent_id``, Map-based node lookup) intact and
  // only changes the final sort.
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

    // Apply pinned-first ordering at every level (mutates the children
    // arrays we just built; safe because each node has exactly one
    // owner at this point).
    sortNodesPinnedFirst(rootNodes);

    return rootNodes;
  });

  constructor(private destroyRef: DestroyRef) {
    // Effect to restore scroll position after data refresh
    effect(() => {
      const loading = this.instanceService.loading();
      const isRefreshing = this.isRefreshing();

      // After loading completes and user had scrolled, restore position
      if (!loading && !isRefreshing && this.scrollTop > 0) {
        requestAnimationFrame(() => {
          const container = this.instanceListContainer?.nativeElement;
          if (container) {
            container.scrollTop = this.scrollTop;
          }
        });
      }
    });
  }

  ngAfterViewInit(): void {
    // Set up scroll position tracking
    const container = this.instanceListContainer.nativeElement;
    container.addEventListener('scroll', this.scrollHandler);
  }

  ngOnDestroy(): void {
    this.instanceListContainer?.nativeElement?.removeEventListener('scroll', this.scrollHandler);
  }

  readonly statusColors: Record<string, { bg: string; text: string }> = {
    idle: { bg: '#4d4d5c', text: '#c5c5d2' },
    running: { bg: '#10b98120', text: '#10b981' },
    waiting: { bg: '#f59e0b20', text: '#f59e0b' },
    waiting_children: { bg: '#3b82f620', text: '#3b82f6' },
    paused: { bg: '#8b5cf620', text: '#8b5cf6' },
    error: { bg: '#f43f5e20', text: '#f43f5e' },
    terminated: { bg: '#343541', text: '#6e6e80' },
  };

  /**
   * Get the current project context for navigation.
   * Returns 'all' when on the All tab, or the project ID otherwise.
   */
  protected getProjectContext(): string {
    return this.tabStateService.activeProjectId() ?? 'all';
  }

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

  /**
   * Open the two-step delete dialog for an instance.
   *
   * The dialog handles the API call itself (terminate or hard delete),
   * so we don't need to forward this through the ``terminateInstance``
   * output any more. The instanceService polling will pick up the
   * backend change on the next tick and drop the row from the list.
   */
  onTerminateInstance(instanceId: string, event: Event): void {
    event.preventDefault();
    event.stopPropagation();

    const instance = this.instances().find(i => i.instance_id === instanceId);
    if (!instance) {
      // Defensive: terminate button only shows for instances we have,
      // but if a stale click slips through, fall back to the existing
      // emit path so callers can still react.
      this.terminateInstance.emit(instanceId);
      return;
    }

    const data: InstanceDeleteDialogData = { instance };
    this.dialog
      .open(InstanceDeleteDialogComponent, {
        data,
        width: '460px',
        maxWidth: '95vw',
        panelClass: 'instance-delete-dialog-panel',
        autoFocus: 'first-tabbable',
        restoreFocus: true,
      })
      .afterClosed()
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe(() => {
        // List refresh is driven by the polling service; nothing to do here.
      });
  }

  onPauseInstance(instanceId: string, event: Event): void {
    event.preventDefault();
    event.stopPropagation();
    if (confirm('Pause this running instance?')) {
      this.pauseInstance.emit(instanceId);
    }
  }

  onResumeInstance(instanceId: string, event: Event): void {
    event.preventDefault();
    event.stopPropagation();
    this.resumeInstance.emit(instanceId);
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

  onToggleKb(): void {
    // Save scroll position before refresh
    this.saveScrollPosition();
    this.instanceService.toggleKb();
    this.instanceService.loadInstances();
  }

  onRefresh(): void {
    // Save scroll position before refresh
    this.saveScrollPosition();
    this.isRefreshing.set(true);
    this.instanceService.loadInstances(this.instanceService.currentProjectId ?? undefined).finally(() => {
      this.isRefreshing.set(false);
    });
  }

  /**
   * Pin / unpin handler. ``stopPropagation`` keeps the row's
   * ``routerLink`` navigation from firing on the same click; the
   * InstancePrefsService does an optimistic local update + backend
   * PUT, and rolls back if the server rejects.
   */
  onTogglePin(instanceId: string, currentPinned: boolean | null | undefined, event: Event): void {
    event.preventDefault();
    event.stopPropagation();
    // Subscribe to the cold Observable so the HTTP PUT actually fires and
    // the service's `tap` reconciliation / `catchError` rollback run. The
    // service swallows errors via `catchError(() => EMPTY)`, so a bare
    // `.subscribe()` is safe — the rollback already mutated the signal
    // before the empty completion.
    this.prefsService.setPin(instanceId, !(currentPinned === true))
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe();
  }

  /**
   * Apply a color picked from the mat-menu swatch grid. ``null``
   * clears only the color tag.
   */
  onSelectColorTag(instanceId: string, color: string | null, event: Event): void {
    event.preventDefault();
    event.stopPropagation();
    // Subscribe so the optimistic PUT actually fires; the service handles
    // reconcile + rollback internally and swallows errors with EMPTY.
    this.prefsService.setColorTag(instanceId, color)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe();
  }

  /**
   * Apply an icon picked from the mat-menu icon grid. ``null``
   * clears the icon tag.
   */
  onSelectIconTag(instanceId: string, icon: string | null, event: Event): void {
    event.preventDefault();
    event.stopPropagation();
    this.prefsService.setIconTag(instanceId, icon)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe();
  }

  /** Clear both color and icon tags from an instance. */
  onClearAllTags(instanceId: string, event: Event): void {
    event.preventDefault();
    event.stopPropagation();
    this.prefsService.clearAllTags(instanceId)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe();
  }

  /** Color swatch palette surfaced to the template. */
  protected readonly colorOptions = COLOR_OPTIONS;

  /** Material icon palette surfaced to the template. */
  protected readonly iconOptions = ICON_OPTIONS;

  /** Convenience predicate for the template's pinned styling. */
  isPinned(instance: InstanceInfo): boolean {
    return instance.pinned === true;
  }

  private saveScrollPosition(): void {
    const container = this.instanceListContainer?.nativeElement;
    if (container) {
      this.scrollTop = container.scrollTop;
    }
  }
}
