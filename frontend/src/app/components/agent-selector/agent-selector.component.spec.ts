import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideNoopAnimations } from '@angular/platform-browser/animations';
import { AgentSelectorComponent } from './agent-selector.component';
import type { Agent } from '../../models';

// jsdom doesn't implement scrollIntoView; the component calls it when
// keyboard navigation updates the focused row. Provide a no-op shim so the
// production code path can run during tests without touching the DOM.
if (typeof Element !== 'undefined' && !Element.prototype.scrollIntoView) {
  Element.prototype.scrollIntoView = jest.fn();
}

const AGENTS: Agent[] = [
  {
    id: 'developer',
    agent_id: 'developer',
    name: 'Developer',
    description: 'Builds and improves features',
    icon: 'code',
    color: 'accent-blue',
  },
  {
    id: 'tester',
    agent_id: 'tester',
    name: 'Tester',
    description: 'Runs automated checks',
    icon: 'bug_report',
    color: 'accent-emerald',
  },
  {
    id: 'planner',
    agent_id: 'planner',
    name: 'Planner',
    description: 'Breaks complex work into steps',
    icon: 'schedule',
    color: 'accent-violet',
  },
  {
    id: '_mother',
    agent_id: '_mother',
    name: 'Mother',
    description: 'Creates and manages agents',
    icon: 'hub',
    color: 'accent-cyan',
    system: true,
  },
];

describe('AgentSelectorComponent', () => {
  let fixture: ComponentFixture<AgentSelectorComponent>;
  let component: AgentSelectorComponent;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [AgentSelectorComponent],
      providers: [provideNoopAnimations()],
    }).compileComponents();

    fixture = TestBed.createComponent(AgentSelectorComponent);
    component = fixture.componentInstance;
    fixture.componentRef.setInput('agents', AGENTS);
    fixture.detectChanges();
  });

  describe('filteredAgents', () => {
    it('returns all selectable agents when the query is empty', () => {
      // Phase 3: dedup sorts by name → Developer, Planner, Tester.
      expect(component.filteredAgents().map(agent => agent.id)).toEqual([
        'developer',
        'planner',
        'tester',
      ]);
    });

    it('filters case-insensitively by agent name', () => {
      component.searchQuery.set('DEVEL');

      expect(component.filteredAgents().map(agent => agent.id)).toEqual(['developer']);
    });

    it('filters by a substring in the description', () => {
      component.searchQuery.set('automated');

      expect(component.filteredAgents().map(agent => agent.id)).toEqual(['tester']);
    });

    it('matches either name or description', () => {
      component.searchQuery.set('steps');

      expect(component.filteredAgents().map(agent => agent.id)).toEqual(['planner']);
    });

    it('trims whitespace around the query', () => {
      component.searchQuery.set('  tester  ');

      expect(component.filteredAgents().map(agent => agent.id)).toEqual(['tester']);
    });

    it('excludes the Mother agent from the searchable list', () => {
      component.searchQuery.set('mother');

      expect(component.filteredAgents()).toEqual([]);
    });

    it('returns no agents when there are no matches', () => {
      component.searchQuery.set('does-not-exist');

      expect(component.filteredAgents()).toEqual([]);
    });
  });

  describe('search and keyboard interactions', () => {
    it('updates the query from the search input event', () => {
      const input = document.createElement('input');
      input.value = 'planner';

      component.onSearchInput({ target: input } as unknown as Event);

      expect(component.searchQuery()).toBe('planner');
      expect(component.filteredAgents().map(agent => agent.id)).toEqual(['planner']);
    });

    it('selects the highlighted agent when Enter is pressed in the search field', () => {
      const selected = jest.fn();
      component.selectAgent.subscribe(selected);
      // Phase 3: filteredAgents is sorted by name → [developer, planner, tester].
      // Index 1 maps to AGENTS[2] (planner). Use objectContaining because
      // dedup adds an `available_versions` field to every emitted agent.
      component.focusedIndex.set(1);

      component.onSearchKeydown(new KeyboardEvent('keydown', { key: 'Enter' }));

      expect(selected).toHaveBeenCalledWith(
        expect.objectContaining({ id: 'planner', name: 'Planner' }),
      );
    });

    it('emits selection and start events for a conversation action', () => {
      const selected = jest.fn();
      const started = jest.fn();
      component.selectAgent.subscribe(selected);
      component.quickCreateInstance.subscribe(started);

      component.onStartConversation(AGENTS[0]);

      expect(selected).toHaveBeenCalledWith(AGENTS[0]);
      // W1: quickCreateInstance payload now carries the version tag so
      // the parent can forward it to ApiService.createInstance().
      expect(started).toHaveBeenCalledWith({ agent: AGENTS[0], versionTag: null });
    });

    it('emits quickCreateInstance with the currently selected version tag', () => {
      const started = jest.fn();
      component.quickCreateInstance.subscribe(started);
      component.selectedVersionTag.set('v2');

      component.onQuickCreate(AGENTS[0]);

      expect(started).toHaveBeenCalledWith({ agent: AGENTS[0], versionTag: 'v2' });
    });

    it('clears the version tag when a new agent is selected (W2)', () => {
      component.selectedVersionTag.set('v2');
      component.onSelect(AGENTS[1]);
      expect(component.selectedVersionTag()).toBeNull();
    });

    it('clears the query when Escape is pressed', () => {
      component.searchQuery.set('tester');

      component.onSearchKeydown(new KeyboardEvent('keydown', { key: 'Escape' }));

      expect(component.searchQuery()).toBe('');
    });

    it('does not emit selection when Enter is pressed with no focusable agent', () => {
      const selected = jest.fn();
      component.selectAgent.subscribe(selected);
      component.focusedIndex.set(-1);

      component.onSearchKeydown(new KeyboardEvent('keydown', { key: 'Enter' }));

      expect(selected).not.toHaveBeenCalled();
    });

    it('ignores navigation keys when the filtered list is empty', () => {
      component.searchQuery.set('no-match');
      component.focusedIndex.set(-1);

      component.onSearchKeydown(new KeyboardEvent('keydown', { key: 'ArrowDown' }));
      component.onSearchKeydown(new KeyboardEvent('keydown', { key: 'ArrowUp' }));

      expect(component.focusedIndex()).toBe(-1);
    });

    it('still allows Escape to clear the search when the filtered list is empty', () => {
      component.searchQuery.set('no-match');

      component.onSearchKeydown(new KeyboardEvent('keydown', { key: 'Escape' }));

      expect(component.searchQuery()).toBe('');
    });
  });

  describe('keyboard navigation wrap-around', () => {
    it('wraps ArrowDown from the last item back to the first', () => {
      component.focusedIndex.set(2); // last selectable agent (index 2 = 'planner')

      component.onSearchKeydown(new KeyboardEvent('keydown', { key: 'ArrowDown' }));

      expect(component.focusedIndex()).toBe(0);
    });

    it('wraps ArrowUp from the first item back to the last', () => {
      component.focusedIndex.set(0);

      component.onSearchKeydown(new KeyboardEvent('keydown', { key: 'ArrowUp' }));

      expect(component.focusedIndex()).toBe(2);
    });

    it('moves ArrowDown normally when not at the boundary', () => {
      component.focusedIndex.set(0);

      component.onSearchKeydown(new KeyboardEvent('keydown', { key: 'ArrowDown' }));

      expect(component.focusedIndex()).toBe(1);
    });

    it('moves ArrowUp normally when not at the boundary', () => {
      component.focusedIndex.set(2);

      component.onSearchKeydown(new KeyboardEvent('keydown', { key: 'ArrowUp' }));

      expect(component.focusedIndex()).toBe(1);
    });

    it('wraps ArrowDown from focusedIndex -1 to 0', () => {
      component.focusedIndex.set(-1);

      component.onSearchKeydown(new KeyboardEvent('keydown', { key: 'ArrowDown' }));

      expect(component.focusedIndex()).toBe(0);
    });

    it('jumps to the first item on Home', () => {
      component.focusedIndex.set(2);

      component.onSearchKeydown(new KeyboardEvent('keydown', { key: 'Home' }));

      expect(component.focusedIndex()).toBe(0);
    });

    it('jumps to the last item on End', () => {
      component.focusedIndex.set(0);

      component.onSearchKeydown(new KeyboardEvent('keydown', { key: 'End' }));

      expect(component.focusedIndex()).toBe(2);
    });

    it('wraps ArrowDown from row keydown at the last item', () => {
      component.focusedIndex.set(2);

      component.onOptionKeydown(
        new KeyboardEvent('keydown', { key: 'ArrowDown' }),
        AGENTS[2],
        2,
      );

      expect(component.focusedIndex()).toBe(0);
    });

    it('wraps ArrowUp from row keydown at the first item', () => {
      component.focusedIndex.set(0);

      component.onOptionKeydown(
        new KeyboardEvent('keydown', { key: 'ArrowUp' }),
        AGENTS[0],
        0,
      );

      expect(component.focusedIndex()).toBe(2);
    });
  });

  describe('focusedIndex correction when filtering changes the list', () => {
    it('clamps focusedIndex to a valid range when filtering shrinks the list', () => {
      component.focusedIndex.set(2);

      component.searchQuery.set('developer');
      fixture.detectChanges();

      expect(component.focusedIndex()).toBe(0);
    });

    it('resets focusedIndex to -1 when filtering yields no results', () => {
      component.focusedIndex.set(2);

      component.searchQuery.set('does-not-exist');
      fixture.detectChanges();

      expect(component.focusedIndex()).toBe(-1);
    });

    it('restores focusedIndex to 0 when the filter expands the list back', () => {
      component.searchQuery.set('no-match');
      fixture.detectChanges();
      expect(component.focusedIndex()).toBe(-1);

      component.searchQuery.set('');
      fixture.detectChanges();

      expect(component.focusedIndex()).toBe(0);
    });

    it('keeps focusedIndex valid when the filter keeps the same count', () => {
      component.focusedIndex.set(1);

      component.searchQuery.set('tester');
      fixture.detectChanges();

      expect(component.focusedIndex()).toBe(0);
    });
  });

  describe('restored control events', () => {
    it('emits deleteAgent with the agent id when deletion is confirmed', () => {
      const deleted = jest.fn();
      component.deleteAgent.subscribe(deleted);
      const confirmSpy = jest.spyOn(window, 'confirm').mockReturnValue(true);

      component.onDeleteAgent(AGENTS[0]);

      expect(confirmSpy).toHaveBeenCalled();
      expect(deleted).toHaveBeenCalledWith(AGENTS[0].id);

      confirmSpy.mockRestore();
    });

    it('does not emit deleteAgent when deletion is cancelled', () => {
      const deleted = jest.fn();
      component.deleteAgent.subscribe(deleted);
      const confirmSpy = jest.spyOn(window, 'confirm').mockReturnValue(false);

      component.onDeleteAgent(AGENTS[0]);

      expect(deleted).not.toHaveBeenCalled();

      confirmSpy.mockRestore();
    });

    it('still emits deleteAgent when window.confirm is unavailable (jsdom/test env)', () => {
      const deleted = jest.fn();
      component.deleteAgent.subscribe(deleted);
      const original = window.confirm;
      // @ts-expect-error: simulate SSR/non-browser env
      delete window.confirm;

      try {
        component.onDeleteAgent(AGENTS[1]);
      } finally {
        window.confirm = original;
      }

      expect(deleted).toHaveBeenCalledWith(AGENTS[1].id);
    });

    it('emits continueInstance with "latest" by default', () => {
      const continued = jest.fn();
      component.continueInstance.subscribe(continued);

      component.onContinueInstance();

      expect(continued).toHaveBeenCalledWith('latest');
    });

    it('emits continueInstance with the provided instance id', () => {
      const continued = jest.fn();
      component.continueInstance.subscribe(continued);

      component.onContinueInstance('instance-abc');

      expect(continued).toHaveBeenCalledWith('instance-abc');
    });

    it('emits createInstance when starting a new chat with the selected agent', () => {
      const created = jest.fn();
      component.createInstance.subscribe(created);

      component.onCreateInstance();

      expect(created).toHaveBeenCalledTimes(1);
    });

    it('emits createInstance with the currently selected version tag', () => {
      const created = jest.fn();
      component.createInstance.subscribe(created);
      component.selectedVersionTag.set('v2');

      component.onCreateInstance();

      expect(created).toHaveBeenCalledWith({ versionTag: 'v2' });
    });

    it('emits createInstance with null versionTag by default', () => {
      const created = jest.fn();
      component.createInstance.subscribe(created);

      component.onCreateInstance();

      expect(created).toHaveBeenCalledWith({ versionTag: null });
    });
  });

  // ── Version picker state (Phase 3) ─────────────────────────────────────
  describe('version picker state', () => {
    it('onVersionTagChange updates selectedVersionTag', () => {
      component.onVersionTagChange('v2');
      expect(component.selectedVersionTag()).toBe('v2');

      component.onVersionTagChange(null);
      expect(component.selectedVersionTag()).toBeNull();
    });

    it('shouldShowVersionPicker is false when no agent is selected', () => {
      fixture.componentRef.setInput('selectedAgent', null);
      expect(component.shouldShowVersionPicker()).toBe(false);
    });

    it('shouldShowVersionPicker is false for a single-version agent', () => {
      fixture.componentRef.setInput('selectedAgent', {
        ...AGENTS[0],
        available_versions: ['v1'],
      });
      expect(component.shouldShowVersionPicker()).toBe(false);
    });

    it('shouldShowVersionPicker is true when the selected agent has multiple versions', () => {
      fixture.componentRef.setInput('selectedAgent', {
        ...AGENTS[0],
        available_versions: [null, 'v2'],
      });
      expect(component.shouldShowVersionPicker()).toBe(true);
    });
  });

  // ── Deduplication (Phase 3 W8) ─────────────────────────────────────────
  describe('deduplicatedAgents (W8)', () => {
    it('keeps the base version when both base and tagged entries share an id', () => {
      fixture.componentRef.setInput('agents', [
        { id: 'developer', agent_id: 'developer', name: 'Developer', description: 'a', icon: 'code', color: 'accent-blue', version_tag: 'v2' },
        { id: 'developer', agent_id: 'developer', name: 'Developer', description: 'a', icon: 'code', color: 'accent-blue', version_tag: null },
      ]);
      fixture.detectChanges();
      const result = component.deduplicatedAgents();
      expect(result.length).toBe(1);
      expect(result[0].version_tag).toBeNull();
    });

    it('uses alphabetical tiebreaker when no base version exists (W8)', () => {
      fixture.componentRef.setInput('agents', [
        { id: 'developer', agent_id: 'developer', name: 'Developer', description: 'a', icon: 'code', color: 'accent-blue', version_tag: 'zeta' },
        { id: 'developer', agent_id: 'developer', name: 'Developer', description: 'a', icon: 'code', color: 'accent-blue', version_tag: 'alpha' },
      ]);
      fixture.detectChanges();
      const result = component.deduplicatedAgents();
      expect(result.length).toBe(1);
      expect(result[0].version_tag).toBe('alpha');
    });

    it('merges available_versions across same-id entries', () => {
      fixture.componentRef.setInput('agents', [
        { id: 'developer', agent_id: 'developer', name: 'Developer', description: 'a', icon: 'code', color: 'accent-blue', version_tag: null, available_versions: [null, 'v2'] },
        { id: 'developer', agent_id: 'developer', name: 'Developer', description: 'a', icon: 'code', color: 'accent-blue', version_tag: 'experimental', available_versions: ['experimental'] },
      ]);
      fixture.detectChanges();
      const result = component.deduplicatedAgents();
      expect(result.length).toBe(1);
      expect(result[0].available_versions).toEqual(
        expect.arrayContaining([null, 'v2', 'experimental']),
      );
    });

    it('keeps unique-id entries untouched (sorted by name)', () => {
      // AGENTS fixture (in the test setup) has unique ids and no version tags.
      const ids = component.deduplicatedAgents().map(a => a.id);
      // Mother is a system agent — must be excluded by selectableAgents filter.
      // Phase 3: dedup sorts by name → Developer, Planner, Tester.
      expect(ids).toEqual(['developer', 'planner', 'tester']);
    });
  });

  describe('aria-activedescendant tracking', () => {
    it('returns the id of the currently focused option', () => {
      component.focusedIndex.set(1);

      // Phase 3: filteredAgents is sorted by name → [developer, planner, tester].
      // Index 1 = planner.
      expect(component.activeDescendant).toBe('agent-item-planner');
    });

    it('returns an empty string when no agent is focused', () => {
      component.focusedIndex.set(-1);

      expect(component.activeDescendant).toBe('');
    });
  });
});
