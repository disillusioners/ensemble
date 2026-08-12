import { Component, input, output, signal, computed, effect, HostListener, ElementRef, ViewChild, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatMenuModule } from '@angular/material/menu';
import { Agent } from '../../models';
import { deduplicateAgentsById } from '../../utils/agent-dedup';

const RECENT_AGENTS_KEY = 'ensemble_recent_agents';
const MAX_RECENT = 5;

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

@Component({
  selector: 'app-agent-switcher',
  standalone: true,
  imports: [CommonModule, MatButtonModule, MatIconModule, MatMenuModule],
  templateUrl: './agent-switcher.html',
  styleUrl: './agent-switcher.scss'
})
export class AgentSwitcherComponent {
  readonly agents = input<Agent[]>([]);
  readonly selectedAgent = input<Agent | null>(null);
  readonly defaultVersions = input<Record<string, string | null>>({});
  /** Emits the chosen agent plus its configured default version tag. */
  readonly agentChange = output<{ agent: Agent; versionTag?: string | null }>();

  @ViewChild('triggerButton') triggerButton!: ElementRef<HTMLButtonElement>;
  @ViewChild('dropdownMenu') dropdownMenu!: ElementRef<HTMLDivElement>;

  private readonly host = inject(ElementRef<HTMLElement>);

  readonly isOpen = signal(false);
  readonly focusedIndex = signal(-1);
  readonly searchQuery = signal('');

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

  // Track recently selected agent IDs (persisted in localStorage, max 5).
  // Initialized from localStorage so the list survives page reloads.
  readonly recentAgentIds = signal<string[]>(loadRecentAgentIds());

  // Recently selected agents, mapped to live Agent objects and filtered by search.
  // Stale IDs (agent no longer in the list) are silently dropped.
  readonly recentAgents = computed<Agent[]>(() => {
    const ids = this.recentAgentIds();
    if (ids.length === 0) return [];
    const available = this.deduplicatedAgents();
    const byId = new Map(available.map(a => [a.id, a]));
    const query = this.searchQuery().trim().toLowerCase();
    const result: Agent[] = [];
    for (const id of ids) {
      const agent = byId.get(id);
      if (!agent) continue; // stale — agent removed from list
      if (!query) {
        result.push(agent);
      } else {
        const name = (agent.name ?? '').toLowerCase();
        const desc = (agent.description ?? '').toLowerCase();
        if (name.includes(query) || desc.includes(query)) {
          result.push(agent);
        }
      }
    }
    return result;
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
    this._recordRecentAgent(agent.id);
    const versionTag = this.defaultVersions()[agent.id] ?? null;
    this.agentChange.emit({ agent, versionTag });
    this.isOpen.set(false);
    this.focusedIndex.set(-1);
    this.searchQuery.set('');
  }

  closeDropdown(): void {
    this.isOpen.set(false);
    this.focusedIndex.set(-1);
    this.searchQuery.set('');
  }

  /** Record a recently-selected agent ID to localStorage and update the signal. */
  private _recordRecentAgent(id: string): void {
    const current = this.recentAgentIds();
    // Deduplicate: remove old occurrence, prepend to front, trim to MAX_RECENT
    const updated = [id, ...current.filter(x => x !== id)].slice(0, MAX_RECENT);
    this.recentAgentIds.set(updated);
    saveRecentAgentIds(updated);
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

    const items = menu.querySelectorAll('.menu-item:not(.recent-item)');
    const item = items[index] as HTMLElement;
    if (item) {
      item.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    }
  }

  private focusOption(index: number): void {
    const menu = this.dropdownMenu?.nativeElement;
    if (!menu) return;

    const items = menu.querySelectorAll('.menu-item:not(.recent-item)');
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

// ── Recent agents localStorage helpers (module-level, SSR-safe) ─────────────

function loadRecentAgentIds(): string[] {
  try {
    const raw = localStorage.getItem(RECENT_AGENTS_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter((x): x is string => typeof x === 'string').slice(0, MAX_RECENT);
  } catch {
    return [];
  }
}

function saveRecentAgentIds(ids: string[]): void {
  try {
    localStorage.setItem(RECENT_AGENTS_KEY, JSON.stringify(ids));
  } catch {
    // localStorage may be unavailable (SSR, Safari private mode) — silently skip
  }
}
