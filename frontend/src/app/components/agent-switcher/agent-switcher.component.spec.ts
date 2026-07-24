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

  // ── selectedVersionTag reset on selectAgent (Phase 3) ──────────────────
  describe('version tag reset on selectAgent', () => {
    it('selectAgent() clears selectedVersionTag so the next session starts fresh', () => {
      component.selectedVersionTag.set('v2');
      const planner = component.selectableAgents().find(a => a.id === 'planner')!;
      component.selectAgent(planner);
      expect(component.selectedVersionTag()).toBeNull();
    });

    it('selectAgent() emits the chosen version tag in the agentChange payload', () => {
      component.selectedVersionTag.set('v2');
      const planner = component.selectableAgents().find(a => a.id === 'planner')!;
      const emitted = jest.fn();
      component.agentChange.subscribe(emitted);
      component.selectAgent(planner);
      expect(emitted).toHaveBeenCalledWith({ agent: planner, versionTag: 'v2' });
    });

    it('selectAgent() emits null versionTag when none was picked', () => {
      component.selectedVersionTag.set(null);
      const planner = component.selectableAgents().find(a => a.id === 'planner')!;
      const emitted = jest.fn();
      component.agentChange.subscribe(emitted);
      component.selectAgent(planner);
      expect(emitted).toHaveBeenCalledWith({ agent: planner, versionTag: null });
    });
  });

  // ── shouldShowVersionPicker (Phase 3) ──────────────────────────────────
  describe('shouldShowVersionPicker', () => {
    it('returns false when no agent is selected', () => {
      fixture.componentRef.setInput('selectedAgent', null);
      expect(component.shouldShowVersionPicker()).toBe(false);
    });

    it('returns false when the selected agent has no available_versions', () => {
      fixture.componentRef.setInput('selectedAgent', AGENTS[0]);
      expect(component.shouldShowVersionPicker()).toBe(false);
    });

    it('returns false when the selected agent has exactly one version', () => {
      fixture.componentRef.setInput('selectedAgent', {
        ...AGENTS[0],
        available_versions: ['v1'],
      });
      expect(component.shouldShowVersionPicker()).toBe(false);
    });

    it('returns true when the selected agent has multiple versions', () => {
      fixture.componentRef.setInput('selectedAgent', {
        ...AGENTS[0],
        available_versions: [null, 'v2'],
      });
      expect(component.shouldShowVersionPicker()).toBe(true);
    });
  });

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
});
