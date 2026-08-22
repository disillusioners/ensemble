import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideNoopAnimations } from '@angular/platform-browser/animations';
import { AgentSwitcherComponent } from './agent-switcher.component';
import { Agent } from '../../models';

/**
 * Unit tests for the search/filter feature of AgentSwitcherComponent.
 *
 * Uses the TestBed pattern (see searchable-select.component.spec.ts) because
 * the component uses decorator-based @Input() inputs (`agents`, `selectedAgent`),
 * not signal inputs. Inputs are set via `fixture.componentRef.setInput(...)`.
 *
 * Test scope: search/filter logic only — selectableAgents / filteredAgents
 * computeds, the _filterEffect focusedIndex clamp, searchQuery reset on close,
 * and onSearchInput event binding.
 */

// ── Test fixtures ──────────────────────────────────────────────────────────

const AGENTS: Agent[] = [
  { id: 'dev', agent_id: 'developer', name: 'Developer', description: 'Builds features', icon: 'code', color: 'accent-blue' },
  { id: 'tester', agent_id: 'tester', name: 'Tester', description: 'Runs tests', icon: 'bug_report', color: 'accent-emerald' },
  { id: 'planner', agent_id: 'planner', name: 'Planner', description: 'Plans work', icon: 'schedule', color: 'accent-violet' },
  // System agents — must ALWAYS be excluded from selectable/filtered results
  { id: 'sys-router', agent_id: 'router', name: 'Router', description: 'developer routes', icon: 'hub', color: 'accent-amber', system: true },
  { id: 'sys-worker', agent_id: 'worker', name: 'Worker', description: 'Background jobs', icon: 'precision_manufacturing', color: 'accent-rose', system: true },
];

const SELECTABLE_COUNT = AGENTS.filter(a => !a.system).length; // 3

describe('AgentSwitcherComponent', () => {
  let fixture: ComponentFixture<AgentSwitcherComponent>;
  let component: AgentSwitcherComponent;

  beforeEach(async () => {
    localStorage.clear();
    await TestBed.configureTestingModule({
      imports: [AgentSwitcherComponent],
      providers: [provideNoopAnimations()],
    }).compileComponents();

    fixture = TestBed.createComponent(AgentSwitcherComponent);
    component = fixture.componentInstance;
    fixture.componentRef.setInput('agents', AGENTS);
    fixture.componentRef.setInput('selectedAgent', null);
    fixture.detectChanges();
  });

  // ── selectableAgents (baseline: system exclusion) ────────────────────────
  describe('selectableAgents', () => {
    it('excludes system agents', () => {
      const ids = component.selectableAgents().map(a => a.id);
      expect(ids).toEqual(['dev', 'tester', 'planner']);
    });

    it('includes all non-system agents', () => {
      expect(component.selectableAgents().length).toBe(SELECTABLE_COUNT);
    });
  });

  // ── filteredAgents — search by name ──────────────────────────────────────
  describe('search by name', () => {
    it('filters case-insensitively by name (lowercase query matches Title-case)', () => {
      component.searchQuery.set('dev');
      expect(component.filteredAgents().map(a => a.id)).toEqual(['dev']);
    });

    it('matches uppercase query', () => {
      component.searchQuery.set('DEV');
      expect(component.filteredAgents().map(a => a.id)).toEqual(['dev']);
    });

    it('matches partial substring', () => {
      component.searchQuery.set('plan');
      expect(component.filteredAgents().map(a => a.id)).toEqual(['planner']);
    });
  });

  // ── filteredAgents — search by description ───────────────────────────────
  describe('search by description', () => {
    it('matches a word in the description', () => {
      component.searchQuery.set('features');
      expect(component.filteredAgents().map(a => a.id)).toEqual(['dev']);
    });

    it('matches description substring case-insensitively', () => {
      component.searchQuery.set('TESTS');
      expect(component.filteredAgents().map(a => a.id)).toEqual(['tester']);
    });
  });

  // ── System-agent exclusion chains with search ───────────────────────────
  describe('system agents always excluded', () => {
    it('does NOT return system agents even when search matches their name', () => {
      // "router" is a system agent whose name would match
      component.searchQuery.set('router');
      expect(component.filteredAgents()).toEqual([]);
    });

    it('does NOT return system agents even when search matches their description', () => {
      // sys-router description "developer routes" contains "developer"
      component.searchQuery.set('developer');
      // Only non-system "Developer" matches; system "Router" excluded
      expect(component.filteredAgents().map(a => a.id)).toEqual(['dev']);
    });
  });

  // ── focusedIndex clamp on filter change ─────────────────────────────────
  // NOTE: _filterEffect is an Angular effect() — it is scheduled, not
  // synchronous. We must call fixture.detectChanges() (or flush microtasks)
  // after mutating the signals it depends on for it to run.
  describe('focusedIndex clamps to filtered bounds', () => {
    it('resets to 0 when idx < 0 and list is non-empty', () => {
      component.searchQuery.set(''); // all selectable
      component.focusedIndex.set(-1); // invalid
      fixture.detectChanges(); // flush _filterEffect
      expect(component.focusedIndex()).toBe(0);
    });

    it('clamps to last valid index when filter shrinks the list', () => {
      component.searchQuery.set('');
      component.focusedIndex.set(2); // valid: planner (index 2 of 3)
      // Now filter down to a single match
      component.searchQuery.set('dev');
      expect(component.filteredAgents().length).toBe(1);
      fixture.detectChanges(); // flush _filterEffect → idx(2) >= len(1) → clamp to 0
      expect(component.focusedIndex()).toBe(0);
    });

    it('resets to -1 when filter yields an empty list', () => {
      component.searchQuery.set('');
      component.focusedIndex.set(1); // valid index
      component.searchQuery.set('zzznomatch'); // no matches
      expect(component.filteredAgents().length).toBe(0);
      fixture.detectChanges(); // flush _filterEffect → len===0 → set -1
      expect(component.focusedIndex()).toBe(-1);
    });
  });

  // ── searchQuery reset on dropdown close ─────────────────────────────────
  describe('searchQuery reset on close', () => {
    it('closeDropdown() resets searchQuery to empty string', () => {
      component.searchQuery.set('dev');
      expect(component.searchQuery()).toBe('dev');
      component.closeDropdown();
      expect(component.searchQuery()).toBe('');
    });

    it('toggleDropdown() (closing) resets searchQuery to empty string', () => {
      // Open first
      component.toggleDropdown();
      expect(component.isOpen()).toBe(true);
      component.searchQuery.set('tester');
      expect(component.searchQuery()).toBe('tester');
      // Close
      component.toggleDropdown();
      expect(component.isOpen()).toBe(false);
      expect(component.searchQuery()).toBe('');
    });

    it('selectAgent() resets searchQuery to empty string', () => {
      component.searchQuery.set('plan');
      const planner = component.selectableAgents().find(a => a.id === 'planner')!;
      component.selectAgent(planner);
      expect(component.searchQuery()).toBe('');
    });
  });

  // ── default version tags and agent selection ─────────────────────────────
  describe('defaultVersions', () => {
    it('uses the selected agent default version tag', () => {
      fixture.componentRef.setInput('defaultVersions', { planner: 'v2' });
      const emitted = jest.fn();
      component.agentChange.subscribe(emitted);
      const planner = component.selectableAgents().find(a => a.id === 'planner')!;
      component.selectAgent(planner);
      expect(emitted).toHaveBeenCalledWith({ agent: planner, versionTag: 'v2' });
    });

    it('falls back to null when no default version exists', () => {
      const emitted = jest.fn();
      component.agentChange.subscribe(emitted);
      const planner = component.selectableAgents().find(a => a.id === 'planner')!;
      component.selectAgent(planner);
      expect(emitted).toHaveBeenCalledWith({ agent: planner, versionTag: null });
    });
  });
  // ── Deduplication (Phase 3 W8) ─────────────────────────────────────────
  // ── Deduplication (Phase 3 W8) ─────────────────────────────────────────
  describe('deduplicatedAgents (W8)', () => {
    it('deduplicates entries with the same id, keeping the base version', () => {
      fixture.componentRef.setInput('agents', [
        { id: 'dev', agent_id: 'dev', name: 'Developer', description: 'a', icon: 'code', color: 'accent-blue', version_tag: 'v2' },
        { id: 'dev', agent_id: 'dev', name: 'Developer', description: 'a', icon: 'code', color: 'accent-blue', version_tag: null },
      ]);
      fixture.detectChanges();
      const result = component.deduplicatedAgents();
      expect(result.length).toBe(1);
      expect(result[0].version_tag).toBeNull();
    });

    it('merges available_versions across same-id entries (including null for base)', () => {
      fixture.componentRef.setInput('agents', [
        { id: 'dev', agent_id: 'dev', name: 'Developer', description: 'a', icon: 'code', color: 'accent-blue', version_tag: null, available_versions: [null, 'v2'] },
        { id: 'dev', agent_id: 'dev', name: 'Developer', description: 'a', icon: 'code', color: 'accent-blue', version_tag: 'experimental', available_versions: ['experimental'] },
      ]);
      fixture.detectChanges();
      const result = component.deduplicatedAgents();
      expect(result.length).toBe(1);
      expect(result[0].version_tag).toBeNull();
      expect(result[0].available_versions).toEqual(
        expect.arrayContaining([null, 'v2', 'experimental']),
      );
    });

    it('uses alphabetical tiebreaker when no base exists (W8)', () => {
      fixture.componentRef.setInput('agents', [
        { id: 'dev', agent_id: 'dev', name: 'Developer', description: 'a', icon: 'code', color: 'accent-blue', version_tag: 'zeta' },
        { id: 'dev', agent_id: 'dev', name: 'Developer', description: 'a', icon: 'code', color: 'accent-blue', version_tag: 'alpha' },
      ]);
      fixture.detectChanges();
      const result = component.deduplicatedAgents();
      expect(result.length).toBe(1);
      expect(result[0].version_tag).toBe('alpha');
    });

    it('keeps entries with unique ids untouched', () => {
      fixture.componentRef.setInput('agents', AGENTS);
      fixture.detectChanges();
      const ids = component.deduplicatedAgents().map(a => a.id);
      // Phase 3: dedup sorts by name → Developer, Planner, Tester.
      expect(ids).toEqual(['dev', 'planner', 'tester']);
    });
  });

  // ── empty / no-match search behavior ────────────────────────────────────
  describe('empty and no-match search', () => {
    it('empty searchQuery shows all selectable agents', () => {
      component.searchQuery.set('');
      expect(component.filteredAgents().length).toBe(SELECTABLE_COUNT);
    });

    it('whitespace-only searchQuery shows all selectable agents (trimmed)', () => {
      component.searchQuery.set('   ');
      expect(component.filteredAgents().length).toBe(SELECTABLE_COUNT);
    });

    it('no-match search returns empty array (drives template "No agents found")', () => {
      component.searchQuery.set('zzznomatch');
      expect(component.filteredAgents()).toEqual([]);
    });
  });

  // ── onSearchInput event binding ─────────────────────────────────────────
  describe('onSearchInput', () => {
    it('reads input element value into searchQuery signal', () => {
      const input = document.createElement('input');
      input.value = 'dev';
      component.onSearchInput({ target: input } as unknown as Event);
      expect(component.searchQuery()).toBe('dev');
    });

    it('updates filteredAgents reactively after input', () => {
      const input = document.createElement('input');
      input.value = 'tester';
      component.onSearchInput({ target: input } as unknown as Event);
      expect(component.filteredAgents().map(a => a.id)).toEqual(['tester']);
    });
  });

  // ── Recent agents (localStorage tracking + recentAgents computed) ──────────
  describe('recent agents', () => {
    const RECENT_KEY = 'ensemble_recent_agents';

    it('selectAgent() writes agent id to localStorage', () => {
      const dev = component.selectableAgents().find(a => a.id === 'dev')!;
      component.selectAgent(dev);
      const stored = JSON.parse(localStorage.getItem(RECENT_KEY)!);
      expect(stored).toEqual(['dev']);
    });

    it('selecting the same agent twice moves it to front (no duplicate)', () => {
      const dev = component.selectableAgents().find(a => a.id === 'dev')!;
      const tester = component.selectableAgents().find(a => a.id === 'tester')!;
      component.selectAgent(dev);
      component.selectAgent(tester);
      component.selectAgent(dev); // move dev back to front
      const stored = JSON.parse(localStorage.getItem(RECENT_KEY)!);
      expect(stored).toEqual(['dev', 'tester']);
      expect(stored.length).toBe(2);
    });

    it('trims the list to max 5 entries', () => {
      // We need more than 5 agents to test trimming. Provide extra agents.
      fixture.componentRef.setInput('agents', [
        ...AGENTS,
        { id: 'a4', agent_id: 'a4', name: 'Agent4', description: 'desc4', icon: 'star', color: 'accent-blue' },
        { id: 'a5', agent_id: 'a5', name: 'Agent5', description: 'desc5', icon: 'star', color: 'accent-blue' },
        { id: 'a6', agent_id: 'a6', name: 'Agent6', description: 'desc6', icon: 'star', color: 'accent-blue' },
        { id: 'a7', agent_id: 'a7', name: 'Agent7', description: 'desc7', icon: 'star', color: 'accent-blue' },
      ]);
      fixture.detectChanges();

      const agents = component.deduplicatedAgents();
      // Select 7 agents (all non-system, sorted alphabetically)
      agents.forEach(a => component.selectAgent(a));
      const stored = JSON.parse(localStorage.getItem(RECENT_KEY)!);
      expect(stored.length).toBe(5);
    });

    it('recentAgents() excludes IDs not in the current agents() list (stale entries)', () => {
      // Pre-populate localStorage with an id that doesn't exist in AGENTS
      localStorage.setItem(RECENT_KEY, JSON.stringify(['ghost', 'dev']));
      // Force signal re-init by creating a fresh component
      fixture.destroy();
      fixture = TestBed.createComponent(AgentSwitcherComponent);
      component = fixture.componentInstance;
      fixture.componentRef.setInput('agents', AGENTS);
      fixture.detectChanges();

      const recent = component.recentAgents().map(a => a.id);
      expect(recent).toEqual(['dev']); // 'ghost' filtered out
    });

    it('recentAgents() respects search filter (empty → all recent; typed → matching only)', () => {
      const dev = component.selectableAgents().find(a => a.id === 'dev')!;
      const tester = component.selectableAgents().find(a => a.id === 'tester')!;
      component.selectAgent(dev);
      component.selectAgent(tester);

      // Empty search → both recent agents
      expect(component.recentAgents().map(a => a.id)).toEqual(['tester', 'dev']);

      // Search "dev" → only Developer matches among recent
      component.searchQuery.set('dev');
      expect(component.recentAgents().map(a => a.id)).toEqual(['dev']);

      // No match → empty
      component.searchQuery.set('zzznomatch');
      expect(component.recentAgents()).toEqual([]);
    });

    it('first-load with empty localStorage → recentAgents() returns []', () => {
      expect(component.recentAgents()).toEqual([]);
    });

    it('constructor initializes from existing localStorage (pre-populated)', () => {
      localStorage.setItem(RECENT_KEY, JSON.stringify(['tester', 'dev']));
      fixture.destroy();
      fixture = TestBed.createComponent(AgentSwitcherComponent);
      component = fixture.componentInstance;
      fixture.componentRef.setInput('agents', AGENTS);
      fixture.detectChanges();

      const recent = component.recentAgents().map(a => a.id);
      expect(recent).toEqual(['tester', 'dev']);
    });

    it('handles corrupt JSON in localStorage gracefully (recentAgents returns [])', () => {
      localStorage.setItem(RECENT_KEY, 'not-json{');
      fixture.destroy();
      fixture = TestBed.createComponent(AgentSwitcherComponent);
      component = fixture.componentInstance;
      fixture.componentRef.setInput('agents', AGENTS);
      fixture.detectChanges();
      expect(component.recentAgents()).toEqual([]);
    });

    it('handles non-array JSON value in localStorage (scalar → [])', () => {
      localStorage.setItem(RECENT_KEY, '"text"');
      fixture.destroy();
      fixture = TestBed.createComponent(AgentSwitcherComponent);
      component = fixture.componentInstance;
      fixture.componentRef.setInput('agents', AGENTS);
      fixture.detectChanges();
      expect(component.recentAgents()).toEqual([]);
    });

    it('filters out non-string elements from localStorage array', () => {
      localStorage.setItem(RECENT_KEY, JSON.stringify([123, 'dev', null]));
      fixture.destroy();
      fixture = TestBed.createComponent(AgentSwitcherComponent);
      component = fixture.componentInstance;
      fixture.componentRef.setInput('agents', AGENTS);
      fixture.detectChanges();
      const recent = component.recentAgents().map(a => a.id);
      expect(recent).toEqual(['dev']); // 123 and null dropped
    });

    it('handles localStorage.setItem throwing (signal still updates in-memory)', () => {
      const dev = component.selectableAgents().find(a => a.id === 'dev')!;
      // Simulate QuotaExceededError
      const original = Storage.prototype.setItem;
      Storage.prototype.setItem = jest.fn(() => {
        throw new DOMException('quota exceeded', 'QuotaExceededError');
      });
      try {
        expect(() => component.selectAgent(dev)).not.toThrow();
        // Signal still updated in-memory
        expect(component.recentAgentIds()).toEqual(['dev']);
      } finally {
        Storage.prototype.setItem = original; // always restore
      }
    });
  });
});

// ─────────────────────────────────────────────────────────────────────────
// F6 — Document-handler gate on viewState.detailVisible()
//
// Mirrors todo-list.component.spec.ts's lightweight testable-class
// pattern: drive only the document-handler slice of
// AgentSwitcherComponent in isolation so the F6 fix
// (gate `onDocumentClick` / `onDocumentKeydown` on
// `viewState.detailVisible()`) is pinned without spinning up the
// full TestBed + ViewChild + ElementRef plumbing.
// ─────────────────────────────────────────────────────────────────────────

import { signal } from '@angular/core';

/**
 * Mock InstancesViewStateService — mirrors the subset the
 * document-level handlers read: `detailVisible`. The F6 gate bails
 * on the handlers when the detail overlay is hidden, so tests flip
 * this signal to drive both gate branches.
 */
class MockInstancesViewStateServiceForSwitcher {
  readonly detailVisible = signal(false);
}

/**
 * TestableAgentSwitcher — mirrors the production
 * `AgentSwitcherComponent` document-handler slice:
 * `onDocumentClick` and `onDocumentKeydown`. The handler logic
 * must match production byte-for-byte so a regression in the
 * `detailVisible()` gate is caught here.
 */
class TestableAgentSwitcher {
  readonly isOpen = signal(false);
  /** Mirrors the production `inject(InstancesViewStateService)`. */
  readonly viewState = new MockInstancesViewStateServiceForSwitcher();

  /** Tracks calls so the tests can observe closeDropdown() firing. */
  closeDropdownCalls = 0;

  /**
   * Mirrors `@HostListener('document:click')`. The F6 fix adds the
   * `!detailVisible()` early return at the top — the rest of the
   * body matches production.
   */
  onDocumentClick(event: MouseEvent): void {
    if (!this.viewState.detailVisible()) return;
    const target = event.target as HTMLElement;
    if (!target.closest('.agent-switcher-container')) {
      this.closeDropdown();
    }
  }

  /**
   * Mirrors `@HostListener('document:keydown')`. The F6 fix adds
   * the `!detailVisible()` early return at the top.
   */
  onDocumentKeydown(event: KeyboardEvent): void {
    if (!this.viewState.detailVisible()) return;
    if (event.key === 'Escape' && this.isOpen()) {
      const activeElement = document.activeElement;
      const container = document.querySelector('.agent-switcher-container');
      if (container?.contains(activeElement)) {
        event.preventDefault();
        this.closeDropdown();
      }
    }
  }

  private closeDropdown(): void {
    this.closeDropdownCalls++;
    this.isOpen.set(false);
  }
}

describe('AgentSwitcherComponent — F6 detail-visibility gate on document handlers', () => {
  let component: TestableAgentSwitcher;

  beforeEach(() => {
    component = new TestableAgentSwitcher();
  });

  describe('onDocumentClick', () => {
    it('is a no-op while detailVisible=false: dropdown stays open', () => {
      // Outside click on the underlying list page while the
      // detail overlay is hidden — the dropdown the user cannot
      // see must NOT be torn down.
      component.isOpen.set(true);
      component.viewState.detailVisible.set(false);

      component.onDocumentClick({ target: document.body } as unknown as MouseEvent);

      expect(component.closeDropdownCalls).toBe(0);
      expect(component.isOpen()).toBe(true);
    });

    it('closes the dropdown while detailVisible=true (visible overlay)', () => {
      // Outside click on a route other than the detail overlay —
      // but the overlay is currently visible, so the click is
      // inside the overlay's chrome and the dropdown must close
      // normally.
      component.isOpen.set(true);
      component.viewState.detailVisible.set(true);

      component.onDocumentClick({ target: document.body } as unknown as MouseEvent);

      expect(component.closeDropdownCalls).toBe(1);
      expect(component.isOpen()).toBe(false);
    });

    it('does NOT close when the click is inside the agent-switcher container (visible)', () => {
      // Production branch: target.closest('.agent-switcher-container')
      // matches — closeDropdown is NOT called. With the gate
      // present this still works as before.
      component.isOpen.set(true);
      component.viewState.detailVisible.set(true);
      const inside = document.createElement('div');
      inside.classList.add('agent-switcher-container');
      document.body.appendChild(inside);

      try {
        component.onDocumentClick({ target: inside } as unknown as MouseEvent);
        expect(component.closeDropdownCalls).toBe(0);
        expect(component.isOpen()).toBe(true);
      } finally {
        document.body.removeChild(inside);
      }
    });
  });

  describe('onDocumentKeydown', () => {
    it('is a no-op while detailVisible=false: dropdown stays open AND Escape is NOT consumed', () => {
      // Same shape as todo-list's onEscape test: the underlying
      // page still receives the Escape (no preventDefault steal
      // from an invisible overlay).
      component.isOpen.set(true);
      component.viewState.detailVisible.set(false);
      const event = {
        key: 'Escape',
        defaultPrevented: false,
        preventDefault(): void { (this as { defaultPrevented: boolean }).defaultPrevented = true; },
      } as unknown as KeyboardEvent;

      component.onDocumentKeydown(event);

      expect(component.closeDropdownCalls).toBe(0);
      expect(component.isOpen()).toBe(true);
      expect(event.defaultPrevented).toBe(false);
    });

    it('closes the dropdown when detailVisible=true and Escape fires (visible overlay)', () => {
      component.isOpen.set(true);
      component.viewState.detailVisible.set(true);
      // Build a real container + child element so document.activeElement
      // is inside it (production branch).
      const inside = document.createElement('button');
      const container = document.createElement('div');
      container.classList.add('agent-switcher-container');
      container.appendChild(inside);
      document.body.appendChild(container);
      inside.focus();
      const event = {
        key: 'Escape',
        defaultPrevented: false,
        preventDefault(): void { (this as { defaultPrevented: boolean }).defaultPrevented = true; },
      } as unknown as KeyboardEvent;

      try {
        component.onDocumentKeydown(event);
        expect(component.closeDropdownCalls).toBe(1);
        expect(component.isOpen()).toBe(false);
      } finally {
        document.body.removeChild(container);
      }
    });

    it('does NOT close when detailVisible=true but the dropdown is closed', () => {
      // Production branch: isOpen() is false, so the inner
      // Escape branch does not fire even when the overlay is
      // visible. The gate is independent of this branch.
      component.isOpen.set(false);
      component.viewState.detailVisible.set(true);
      const event = {
        key: 'Escape',
        defaultPrevented: false,
        preventDefault(): void { (this as { defaultPrevented: boolean }).defaultPrevented = true; },
      } as unknown as KeyboardEvent;

      component.onDocumentKeydown(event);

      expect(component.closeDropdownCalls).toBe(0);
      expect(event.defaultPrevented).toBe(false);
    });
  });
});
