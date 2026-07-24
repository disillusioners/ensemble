import {
  Component,
  input,
  output,
  signal,
  computed,
  effect,
  ElementRef,
  ViewChild,
  inject,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { MatDialogModule, MatDialog } from '@angular/material/dialog';
import type { Agent, AgentCreate } from '../../models';
import { AddAgentModalComponent } from '../add-agent-modal/add-agent-modal.component';
import { VersionPickerComponent } from '../version-picker/version-picker.component';
import { deduplicateAgentsById } from '../../utils/agent-dedup';

const colorMap: Record<string, string> = {
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

/**
 * Searchable agent picker.
 *
 * Shows a search input plus a scrollable list of agents (name + description)
 * with real-time case-insensitive filtering and keyboard navigation.
 *
 * Signals are used for all inputs so ``computed()`` re-evaluates correctly
 * when the parent (HomeComponent) updates ``agents()``.
 */
@Component({
  selector: 'app-agent-selector',
  standalone: true,
  imports: [CommonModule, MatDialogModule, VersionPickerComponent],
  templateUrl: './agent-selector.html',
  styleUrl: './agent-selector.scss',
})
export class AgentSelectorComponent {
  // ── Signal inputs / outputs ────────────────────────────────────────────
  readonly agents = input<Agent[]>([]);
  readonly selectedAgent = input<Agent | null>(null);
  readonly hasInstances = input(false);
  readonly isLoading = input(false);

  readonly selectAgent = output<Agent>();
  /** Carries the chosen version tag when present (Phase 3). The parent
   *  forwards this to ApiService.createInstance(). */
  readonly createInstance = output<{ versionTag?: string | null }>();
  readonly continueInstance = output<string>();
  readonly addAgent = output<AgentCreate>();
  readonly deleteAgent = output<string>();
  readonly startMother = output<void>();
  /** Emits the chosen agent plus the currently-selected version tag (or
   *  null when no tag is picked). The parent uses `versionTag` to forward
   *  to ``ApiService.createInstance()`` so the daemon picks the right
   *  agent version. */
  readonly quickCreateInstance = output<{ agent: Agent; versionTag?: string | null }>();
  readonly viewInstances = output<void>();

  @ViewChild('searchInput') searchInput!: ElementRef<HTMLInputElement>;
  @ViewChild('agentList') agentList?: ElementRef<HTMLDivElement>;

  private readonly dialog = inject(MatDialog);

  // ── Search / filter state ──────────────────────────────────────────────
  readonly searchQuery = signal('');
  readonly focusedIndex = signal(-1);

  /** Currently selected version tag (Phase 3). Reset to null whenever the
   *  selected agent changes so a new agent doesn't inherit a stale tag. */
  readonly selectedVersionTag = signal<string | null>(null);

  /** Exclude system agents (e.g. Mother) from the pickable list. */
  readonly selectableAgents = computed(() =>
    this.agents().filter(agent => !agent.system),
  );

  /** Deduplicated agents — base version wins for each id, available_versions
   *  merged across entries (Phase 3, W8). Sorted by name. */
  readonly deduplicatedAgents = computed(() =>
    deduplicateAgentsById(this.selectableAgents()),
  );

  /** Case-insensitive filter over deduplicated agent name AND description. */
  readonly filteredAgents = computed(() => {
    const query = this.searchQuery().trim().toLowerCase();
    const base = this.deduplicatedAgents();
    if (!query) return base;
    return base.filter(agent => {
      const name = (agent.name ?? '').toLowerCase();
      const desc = (agent.description ?? '').toLowerCase();
      return name.includes(query) || desc.includes(query);
    });
  });

  /** Reset the chosen version tag whenever the selected agent id changes so
   *  a new agent never inherits the previous selection's tag. Performed
   *  imperatively in `onSelect` (W2) so the reset has no chance of running
   *  before the new agent value is committed. */
  private readonly _filterEffect = effect(() => {
    // Establish reactive dependencies.
    this.searchQuery();
    const len = this.filteredAgents().length;
    const idx = this.focusedIndex();
    if (len === 0) {
      if (idx !== -1) this.focusedIndex.set(-1);
    } else if (idx < 0) {
      this.focusedIndex.set(0);
    } else if (idx >= len) {
      this.focusedIndex.set(len - 1);
    }
  });

  // ── Helpers ────────────────────────────────────────────────────────────
  getAgentColor(agent: Agent): string {
    return colorMap[agent.color] || agent.color || '#10a7f7';
  }

  get activeColor(): string {
    const sel = this.selectedAgent();
    return sel ? this.getAgentColor(sel) : '#10a7f7';
  }

  get activeDescendant(): string {
    const idx = this.focusedIndex();
    const agents = this.filteredAgents();
    if (idx >= 0 && idx < agents.length) {
      return `agent-item-${agents[idx].id}`;
    }
    return '';
  }

  // ── Event handlers ─────────────────────────────────────────────────────
  onSearchInput(event: Event): void {
    const inputEl = event.target as HTMLInputElement;
    this.searchQuery.set(inputEl.value);
  }

  onSearchKeydown(event: KeyboardEvent): void {
    const agents = this.filteredAgents();
    if (agents.length === 0 && !['Escape', 'Tab'].includes(event.key)) {
      return;
    }

    switch (event.key) {
      case 'ArrowDown': {
        event.preventDefault();
        // Wrap: from last item, going down returns to the first option.
        const current = this.focusedIndex();
        const next = current < 0 ? 0 : current >= agents.length - 1 ? 0 : current + 1;
        this.focusedIndex.set(next);
        this.scrollToFocused(next);
        break;
      }
      case 'ArrowUp': {
        event.preventDefault();
        // Wrap: from first item, going up returns to the last option.
        const current = this.focusedIndex();
        const prev = current <= 0 ? agents.length - 1 : current - 1;
        this.focusedIndex.set(prev);
        this.scrollToFocused(prev);
        break;
      }
      case 'Home': {
        event.preventDefault();
        this.focusedIndex.set(0);
        this.scrollToFocused(0);
        break;
      }
      case 'End': {
        event.preventDefault();
        const last = agents.length - 1;
        this.focusedIndex.set(last);
        this.scrollToFocused(last);
        break;
      }
      case 'Enter': {
        event.preventDefault();
        const idx = this.focusedIndex();
        if (idx >= 0 && idx < agents.length) {
          this.onSelect(agents[idx]);
        }
        break;
      }
      case 'Escape': {
        event.preventDefault();
        if (this.searchQuery() !== '') {
          this.clearSearch();
        } else {
          this.searchInput?.nativeElement.blur();
        }
        break;
      }
    }
  }

  onOptionMouseEnter(index: number): void {
    this.focusedIndex.set(index);
  }

  onOptionFocus(index: number): void {
    this.focusedIndex.set(index);
  }

  onOptionKeydown(event: KeyboardEvent, agent: Agent, index: number): void {
    const agents = this.filteredAgents();
    if (agents.length === 0 && !['Escape', 'Tab'].includes(event.key)) {
      return;
    }

    switch (event.key) {
      case 'ArrowDown': {
        event.preventDefault();
        // Wrap: from last item, going down returns to the first option.
        const next = index >= agents.length - 1 ? 0 : index + 1;
        this.focusedIndex.set(next);
        this.focusOption(next);
        break;
      }
      case 'ArrowUp': {
        event.preventDefault();
        // Wrap: from first item, going up returns to the last option.
        const previous = index <= 0 ? agents.length - 1 : index - 1;
        this.focusedIndex.set(previous);
        this.focusOption(previous);
        break;
      }
      case 'Home': {
        event.preventDefault();
        this.focusedIndex.set(0);
        this.focusOption(0);
        break;
      }
      case 'End': {
        event.preventDefault();
        const last = agents.length - 1;
        this.focusedIndex.set(last);
        this.focusOption(last);
        break;
      }
      case 'Enter':
      case ' ': {
        event.preventDefault();
        this.onSelect(agent);
        break;
      }
      case 'Escape': {
        event.preventDefault();
        this.clearSearch();
        break;
      }
    }
  }

  /** Select an agent without starting a conversation. The version tag is
   *  reset imperatively (W2) so a new agent never inherits the previous
   *  selection's tag. */
  onSelect(agent: Agent): void {
    this.selectedVersionTag.set(null);
    this.selectAgent.emit(agent);
  }

  /**
   * Start a new conversation with the given agent: record the selection
   * (so the parent persists it) then ask the parent to create an instance.
   */
  onStartConversation(agent: Agent): void {
    this.onSelect(agent);
    this.onQuickCreate(agent);
  }

  onQuickCreate(agent: Agent, event?: Event): void {
    event?.stopPropagation();
    this.quickCreateInstance.emit({
      agent,
      versionTag: this.selectedVersionTag(),
    });
  }

  /**
   * Restore a previous conversation. ``instanceId`` may be ``'latest'`` to
   * resume the most recent instance, or a specific instance id.
   */
  onContinueInstance(instanceId: string = 'latest'): void {
    this.continueInstance.emit(instanceId);
  }

  /** Ask the parent to create a new instance using its currently selected agent. */
  onCreateInstance(): void {
    this.createInstance.emit({ versionTag: this.selectedVersionTag() });
  }

  /** Version picker changed — record the new tag for the next create. */
  onVersionTagChange(tag: string | null): void {
    this.selectedVersionTag.set(tag);
  }

  /** Convenience: does the selected agent actually have a version picker
   *  to show? (more than one version available). Implemented as a
   *  ``computed`` so the template re-evaluates automatically when the
   *  selected agent or its ``available_versions`` change. */
  readonly shouldShowVersionPicker = computed(() => {
    const sel = this.selectedAgent();
    if (!sel) return false;
    const versions = sel.available_versions ?? [];
    return versions.length > 1;
  });

  /**
   * Delete an agent. The agent-selector confirms with the user before
   * emitting because the parent deletes irreversibly.
   */
  onDeleteAgent(agent: Agent, event?: Event): void {
    event?.stopPropagation();
    if (typeof confirm === 'function' &&
        !confirm(`Delete agent "${agent.name}"? This will move it to trash.`)) {
      return;
    }
    this.deleteAgent.emit(agent.id);
  }

  private focusOption(index: number): void {
    this.scrollToFocused(index);
    const list = this.agentList?.nativeElement;
    if (!list) return;
    const items = list.querySelectorAll('.agent-item');
    (items[index] as HTMLElement | undefined)?.focus();
  }

  clearSearch(): void {
    this.searchQuery.set('');
    // Reset focus to the top of the list.
    const len = this.filteredAgents().length;
    this.focusedIndex.set(len > 0 ? 0 : -1);
    this.searchInput?.nativeElement.focus();
  }

  private scrollToFocused(index: number): void {
    const list = this.agentList?.nativeElement;
    if (!list) return;
    const items = list.querySelectorAll('.agent-item');
    const item = items[index] as HTMLElement | undefined;
    item?.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  }

  // ── Passthrough handlers (unchanged behavior) ──────────────────────────
  protected onStartMother(): void {
    this.startMother.emit();
  }

  protected onViewInstances(): void {
    this.viewInstances.emit();
  }

  protected openAddModal(): void {
    const dialogRef = this.dialog.open(AddAgentModalComponent, {
      width: '480px',
      maxWidth: '95vw',
      panelClass: 'dark-modal-panel',
      data: {},
    });

    dialogRef.afterClosed().subscribe((result: AgentCreate | undefined) => {
      if (result) {
        this.addAgent.emit(result);
      }
    });
  }
}
