import { Component, Input, Output, EventEmitter, signal, computed, HostListener, ElementRef, ViewChild } from '@angular/core';
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
  'accent-purple': '#a855f7',
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

  @ViewChild('triggerButton') triggerButton!: ElementRef<HTMLButtonElement>;
  @ViewChild('dropdownMenu') dropdownMenu!: ElementRef<HTMLDivElement>;

  isOpen = signal(false);
  focusedIndex = signal(-1);

  // Filter out system agents from selection
  readonly selectableAgents = computed(() => 
    this.agents.filter(agent => !agent.system)
  );

  getAgentColor(agent: Agent): string {
    return colorMap[agent.color] || agent.color || '#10a7f7';
  }

  get activeColor(): string {
    return this.selectedAgent ? this.getAgentColor(this.selectedAgent) : '#10a7f7';
  }

  get activeDescendant(): string {
    const idx = this.focusedIndex();
    if (idx >= 0 && idx < this.selectableAgents().length) {
      return `agent-option-${this.selectableAgents()[idx].id}`;
    }
    return '';
  }

  get focusedAgentId(): string | null {
    const idx = this.focusedIndex();
    if (idx >= 0 && idx < this.selectableAgents().length) {
      return this.selectableAgents()[idx].id;
    }
    return null;
  }

  toggleDropdown(): void {
    this.isOpen.update(v => !v);
    if (this.isOpen()) {
      // When opening, focus the trigger button and set initial focused index
      const agents = this.selectableAgents();
      const currentIndex = agents.findIndex(a => a.id === this.selectedAgent?.id);
      this.focusedIndex.set(currentIndex >= 0 ? currentIndex : 0);
      setTimeout(() => this.triggerButton?.nativeElement?.focus(), 0);
    } else {
      this.focusedIndex.set(-1);
    }
  }

  selectAgent(agent: Agent): void {
    this.agentChange.emit(agent);
    this.isOpen.set(false);
    this.focusedIndex.set(-1);
  }

  closeDropdown(): void {
    this.isOpen.set(false);
    this.focusedIndex.set(-1);
  }

  onTriggerKeydown(event: KeyboardEvent): void {
    const agents = this.selectableAgents();
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
          this.isOpen.set(true);
          this.focusedIndex.set(0);
          setTimeout(() => this.triggerButton?.nativeElement?.focus(), 0);
        } else {
          const nextIndex = currentIndex < agents.length - 1 ? currentIndex + 1 : 0;
          this.focusedIndex.set(nextIndex);
          this.scrollToFocused(nextIndex);
        }
        break;

      case 'ArrowUp':
        event.preventDefault();
        if (!this.isOpen()) {
          this.isOpen.set(true);
          this.focusedIndex.set(agents.length - 1);
          setTimeout(() => this.triggerButton?.nativeElement?.focus(), 0);
        } else {
          const prevIndex = currentIndex > 0 ? currentIndex - 1 : agents.length - 1;
          this.focusedIndex.set(prevIndex);
          this.scrollToFocused(prevIndex);
        }
        break;

      case 'Home':
        event.preventDefault();
        if (this.isOpen()) {
          this.focusedIndex.set(0);
          this.scrollToFocused(0);
        }
        break;

      case 'End':
        event.preventDefault();
        if (this.isOpen()) {
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

      case 'Tab':
        // Close dropdown and allow natural tab behavior
        this.closeDropdown();
        break;
    }
  }

  private handleArrowDown(): void {
    const agents = this.selectableAgents();
    const currentIndex = this.focusedIndex();
    const nextIndex = currentIndex < agents.length - 1 ? currentIndex + 1 : 0;
    this.focusedIndex.set(nextIndex);
    this.scrollToFocused(nextIndex);
    this.focusOption(nextIndex);
  }

  private handleArrowUp(): void {
    const agents = this.selectableAgents();
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
    if (this.focusedIndex() < 0) {
      const currentIndex = this.selectableAgents().findIndex(a => a.id === this.selectedAgent?.id);
      this.focusedIndex.set(currentIndex >= 0 ? currentIndex : 0);
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
}
