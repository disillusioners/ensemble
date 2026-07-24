import { Component, input, output, signal, computed, effect, HostListener, ElementRef, ViewChild, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatMenuModule } from '@angular/material/menu';
import { Agent } from '../../models';
import { VersionPickerComponent } from '../version-picker/version-picker.component';

const colorMap: Record<string, string> = {
  'accent-amber': '#f59e0b',
  'accent-cyan': '#10a7f7',
  'accent-violet': '#8b5cf6',
  'accent-emerald': '#10b981',
  'accent-rose': '#f43f5e',
  'accent-blue': '#3b82f6',
  'accent-purple': '#a855f7',
  'accent-indigo': '#6366f1',
  'accent-green': '#22c55e',
};

/**
 * Deduplicate agents by id (Phase 3, W8). Base version wins; when no base
 * exists, the alphabetically smaller tag wins. Merges `available_versions`
 * so the surviving entry advertises the union of all tags seen for that id.
 */
function deduplicateAgentsById(agents: Agent[]): Agent[] {
  const byId = new Map<string, Agent>();
  for (const agent of agents) {
    const existing = byId.get(agent.id);
    if (!existing) {
      byId.set(agent.id, { ...agent, available_versions: [...(agent.available_versions ?? [])] });
      continue;
    }
    const existingIsBase =
      existing.version_tag === null || existing.version_tag === undefined;
    const agentIsBase =
      agent.version_tag === null || agent.version_tag === undefined;
    if (!existingIsBase && agentIsBase) {
      byId.set(agent.id, {
        ...agent,
        available_versions: mergeVersionList(
          agent.available_versions,
          existing.version_tag,
        ),
      });
    } else if (!existingIsBase && !agentIsBase) {
      const next = (agent.version_tag ?? '') < (existing.version_tag ?? '')
        ? agent
        : existing;
      byId.set(agent.id, {
        ...next,
        available_versions: mergeVersionList(
          next.available_versions,
          next === agent ? existing.version_tag : agent.version_tag,
        ),
      });
    } else {
      const merged = mergeVersionList(
        existing.available_versions,
        agent.version_tag,
      );
      if (merged.length !== (existing.available_versions?.length ?? 0)) {
        byId.set(agent.id, { ...existing, available_versions: merged });
      }
    }
  }
  return Array.from(byId.values()).sort((a, b) =>
    a.name.localeCompare(b.name),
  );
}

function mergeVersionList(
  current: (string | null)[] | null | undefined,
  extra: string | null | undefined,
): (string | null)[] {
  const list = [...(current ?? [])];
  if (extra === undefined) return list;
  if (!list.includes(extra)) list.push(extra);
  return list;
}

@Component({
  selector: 'app-agent-switcher',
  standalone: true,
  imports: [CommonModule, MatButtonModule, MatIconModule, MatMenuModule, VersionPickerComponent],
  templateUrl: './agent-switcher.html',
  styleUrl: './agent-switcher.scss'
})
export class AgentSwitcherComponent {
  readonly agents = input<Agent[]>([]);
  readonly selectedAgent = input<Agent | null>(null);
  /** Emits the chosen agent plus an optional version tag for the next
   *  instance creation. Phase 3: ``versionTag`` is ``null``/undefined when
   *  the user hasn't picked a tag (use base). */
  readonly agentChange = output<{ agent: Agent; versionTag?: string | null }>();

  @ViewChild('triggerButton') triggerButton!: ElementRef<HTMLButtonElement>;
  @ViewChild('dropdownMenu') dropdownMenu!: ElementRef<HTMLDivElement>;

  private readonly host = inject(ElementRef<HTMLElement>);

  isOpen = signal(false);
  focusedIndex = signal(-1);
  searchQuery = signal('');

  /** Phase 3: version tag the user picked in the dropdown. Reset to null
   *  whenever the dropdown opens so the new selection doesn't inherit a
   *  stale tag from a previous session. */
  selectedVersionTag = signal<string | null>(null);

  // Filter out system agents from selection
  readonly selectableAgents = computed(() =>
    this.agents().filter(agent => !agent.system)
  );

  // Phase 3 (W8): deduplicate by id, base wins, alphabetical tiebreaker.
  readonly deduplicatedAgents = computed(() =>
    deduplicateAgentsById(this.selectableAgents()),
  );

  // Further filter by search query (case-insensitive, matches name OR description)
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

  // Keep focusedIndex within bounds as the filtered list shrinks/grows
  private readonly _filterEffect = effect(() => {
    // Establish reactive dependencies
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

  getAgentColor(agent: Agent): string {
    return colorMap[agent.color] || agent.color || '#10a7f7';
  }

  get activeColor(): string {
    return this.selectedAgent() ? this.getAgentColor(this.selectedAgent()!) : '#10a7f7';
  }

  get activeDescendant(): string {
    const idx = this.focusedIndex();
    const agents = this.filteredAgents();
    if (idx >= 0 && idx < agents.length) {
      return `agent-option-${agents[idx].id}`;
    }
    return '';
  }

  get focusedAgentId(): string | null {
    const idx = this.focusedIndex();
    const agents = this.filteredAgents();
    if (idx >= 0 && idx < agents.length) {
      return agents[idx].id;
    }
    return null;
  }

  toggleDropdown(): void {
    this.isOpen.update(v => !v);
    if (this.isOpen()) {
      this.updateDropdownMaxHeight();
      // When opening, focus the trigger button and set initial focused index
      const agents = this.filteredAgents();
      if (agents.length === 0) {
        this.focusedIndex.set(-1);
        setTimeout(() => this.triggerButton?.nativeElement?.focus(), 0);
        return;
      }
      const currentIndex = agents.findIndex(a => a.id === this.selectedAgent()?.id);
      this.focusedIndex.set(currentIndex >= 0 ? currentIndex : 0);
      setTimeout(() => this.triggerButton?.nativeElement?.focus(), 0);
    } else {
      this.focusedIndex.set(-1);
      this.searchQuery.set('');
    }
  }

  private updateDropdownMaxHeight(): void {
    const trigger = this.triggerButton?.nativeElement;
    if (!trigger) return;
    const rect = trigger.getBoundingClientRect();
    const spaceBelow = window.innerHeight - rect.bottom - 8;
    const maxHeight = Math.max(120, spaceBelow);
    this.host.nativeElement.style.setProperty('--dropdown-max-height', `${maxHeight}px`);
  }

  selectAgent(agent: Agent): void {
    this.agentChange.emit({ agent, versionTag: this.selectedVersionTag() });
    this.isOpen.set(false);
    this.focusedIndex.set(-1);
    this.searchQuery.set('');
    this.selectedVersionTag.set(null);
  }

  /** Phase 3: track version tag selection from the picker. */
  onVersionTagChange(tag: string | null): void {
    this.selectedVersionTag.set(tag);
  }

  /** Show version picker only when the currently selected agent has more
   *  than one available version. The picker sits in the dropdown footer. */
  get shouldShowVersionPicker(): boolean {
    const sel = this.selectedAgent();
    if (!sel) return false;
    const versions = sel.available_versions ?? [];
    return versions.length > 1;
  }

  closeDropdown(): void {
    this.isOpen.set(false);
    this.focusedIndex.set(-1);
    this.searchQuery.set('');
  }

  onTriggerKeydown(event: KeyboardEvent): void {
    const agents = this.filteredAgents();
    const currentIndex = this.focusedIndex();

    switch (event.key) {
      case 'Enter':
      case ' ':
        event.preventDefault();
        if (!this.isOpen()) {
          this.toggleDropdown();
        } else if (currentIndex >= 0 && currentIndex < agents.length) {
          this.selectAgent(agents[currentIndex]);
        }
        break;

      case 'ArrowDown':
        event.preventDefault();
        if (!this.isOpen()) {
          const agents = this.filteredAgents();
          if (agents.length === 0) return;
          this.isOpen.set(true);
          this.focusedIndex.set(0);
          setTimeout(() => this.triggerButton?.nativeElement?.focus(), 0);
        } else {
          const agents = this.filteredAgents();
          if (agents.length === 0) return;
          const nextIndex = currentIndex < agents.length - 1 ? currentIndex + 1 : 0;
          this.focusedIndex.set(nextIndex);
          this.scrollToFocused(nextIndex);
        }
        break;

      case 'ArrowUp':
        event.preventDefault();
        if (!this.isOpen()) {
          const agents = this.filteredAgents();
          if (agents.length === 0) return;
          this.isOpen.set(true);
          this.focusedIndex.set(agents.length - 1);
          setTimeout(() => this.triggerButton?.nativeElement?.focus(), 0);
        } else {
          const agents = this.filteredAgents();
          if (agents.length === 0) return;
          const prevIndex = currentIndex > 0 ? currentIndex - 1 : agents.length - 1;
          this.focusedIndex.set(prevIndex);
          this.scrollToFocused(prevIndex);
        }
        break;

      case 'Home':
        event.preventDefault();
        if (this.isOpen() && agents.length > 0) {
          this.focusedIndex.set(0);
          this.scrollToFocused(0);
        }
        break;

      case 'End':
        event.preventDefault();
        if (this.isOpen() && agents.length > 0) {
          const lastIndex = agents.length - 1;
          this.focusedIndex.set(lastIndex);
          this.scrollToFocused(lastIndex);
        }
        break;

      case 'Escape':
        event.preventDefault();
        this.closeDropdown();
        this.triggerButton?.nativeElement?.focus();
        break;

      case 'Tab':
        // Allow natural tab behavior but close dropdown
        this.closeDropdown();
        break;
    }
  }

  onOptionKeydown(event: KeyboardEvent, agent: Agent): void {
    switch (event.key) {
      case 'Enter':
      case ' ':
        event.preventDefault();
        this.selectAgent(agent);
        this.triggerButton?.nativeElement?.focus();
        break;

      case 'Escape':
        event.preventDefault();
        this.closeDropdown();
        this.triggerButton?.nativeElement?.focus();
        break;

      case 'ArrowDown':
        event.preventDefault();
        this.handleArrowDown();
        break;

      case 'ArrowUp':
        event.preventDefault();
        this.handleArrowUp();
        break;

      case 'Home':
        event.preventDefault();
        this.focusFirst();
        break;

      case 'End':
        event.preventDefault();
        this.focusLast();
        break;

      case 'Tab':
        // Close dropdown and allow natural tab behavior
        this.closeDropdown();
        break;
    }
  }

  private focusFirst(): void {
    if (this.filteredAgents().length === 0) return;
    this.focusedIndex.set(0);
    this.scrollToFocused(0);
    this.focusOption(0);
  }

  private focusLast(): void {
    const lastIndex = this.filteredAgents().length - 1;
    if (lastIndex < 0) return;
    this.focusedIndex.set(lastIndex);
    this.scrollToFocused(lastIndex);
    this.focusOption(lastIndex);
  }

  private handleArrowDown(): void {
    const agents = this.filteredAgents();
    if (agents.length === 0) return;
    const currentIndex = this.focusedIndex();
    const nextIndex = currentIndex < agents.length - 1 ? currentIndex + 1 : 0;
    this.focusedIndex.set(nextIndex);
    this.scrollToFocused(nextIndex);
    this.focusOption(nextIndex);
  }

  private handleArrowUp(): void {
    const agents = this.filteredAgents();
    if (agents.length === 0) return;
    const currentIndex = this.focusedIndex();
    const prevIndex = currentIndex > 0 ? currentIndex - 1 : agents.length - 1;
    this.focusedIndex.set(prevIndex);
    this.scrollToFocused(prevIndex);
    this.focusOption(prevIndex);
  }

  private scrollToFocused(index: number): void {
    const menu = this.dropdownMenu?.nativeElement;
    if (!menu) return;

    const items = menu.querySelectorAll('.menu-item');
    const item = items[index] as HTMLElement;
    if (item) {
      item.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    }
  }

  private focusOption(index: number): void {
    const menu = this.dropdownMenu?.nativeElement;
    if (!menu) return;

    const items = menu.querySelectorAll('.menu-item');
    const item = items[index] as HTMLElement;
    if (item) {
      item.focus();
    }
  }

  onMenuFocus(): void {
    // Ensure focusedIndex is valid when menu receives focus
    const agents = this.filteredAgents();
    if (agents.length === 0) return;
    if (this.focusedIndex() < 0) {
      const currentIndex = agents.findIndex(a => a.id === this.selectedAgent()?.id);
      this.focusedIndex.set(currentIndex >= 0 ? currentIndex : 0);
    }
  }

  onSearchInput(event: Event): void {
    const input = event.target as HTMLInputElement;
    this.searchQuery.set(input.value);
  }

  onSearchKeydown(event: KeyboardEvent): void {
    const agents = this.filteredAgents();
    switch (event.key) {
      case 'ArrowDown':
        event.preventDefault();
        if (agents.length === 0) return;
        this.focusedIndex.set(0);
        this.scrollToFocused(0);
        this.focusOption(0);
        break;

      case 'ArrowUp':
        event.preventDefault();
        if (agents.length === 0) return;
        const lastIdx = agents.length - 1;
        this.focusedIndex.set(lastIdx);
        this.scrollToFocused(lastIdx);
        this.focusOption(lastIdx);
        break;

      case 'Enter':
        event.preventDefault();
        const idx = this.focusedIndex();
        if (idx >= 0 && idx < agents.length) {
          this.selectAgent(agents[idx]);
        }
        break;

      case 'Escape':
        event.preventDefault();
        event.stopPropagation();
        if (this.searchQuery() !== '') {
          this.searchQuery.set('');
          const len = this.filteredAgents().length;
          if (len > 0) this.focusedIndex.set(0);
        } else {
          this.closeDropdown();
          this.triggerButton?.nativeElement?.focus();
        }
        break;

      case 'Tab':
        event.stopPropagation();
        this.closeDropdown();
        break;

      // Other keys: allow typing into the input normally
    }
  }

  onOptionFocus(index: number): void {
    this.focusedIndex.set(index);
  }

  onOptionMouseEnter(index: number): void {
    this.focusedIndex.set(index);
  }

  @HostListener('document:click', ['$event'])
  onDocumentClick(event: MouseEvent): void {
    const target = event.target as HTMLElement;
    if (!target.closest('.agent-switcher-container')) {
      this.closeDropdown();
    }
  }

  @HostListener('document:keydown', ['$event'])
  onDocumentKeydown(event: KeyboardEvent): void {
    // Close dropdown when pressing Escape and it's not handled by another element
    if (event.key === 'Escape' && this.isOpen()) {
      const activeElement = document.activeElement;
      const container = document.querySelector('.agent-switcher-container');
      if (container?.contains(activeElement)) {
        event.preventDefault();
        this.closeDropdown();
        this.triggerButton?.nativeElement?.focus();
      }
    }
  }

  @HostListener('window:resize')
  onWindowResize(): void {
    if (this.isOpen()) {
      this.updateDropdownMaxHeight();
    }
  }
}
