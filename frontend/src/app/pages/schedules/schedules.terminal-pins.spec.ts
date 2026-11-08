// Schedules terminal-visibility pins — proves the production component
// and template consume terminal-visibility.model.ts (default active-only
// view, toggle, persistence, hidden-count line) AND that the stats
// badges stay truthful over ALL rows (derived from the raw signal,
// never the visible subset). Plain-TS source-grep pins (no TestBed) —
// house style.

import { readFileSync } from 'fs';
import { join } from 'path';

const dir = join(__dirname, '.');
const componentSrc = readFileSync(join(dir, 'schedules.component.ts'), 'utf-8');
const templateSrc = readFileSync(join(dir, 'schedules.component.html'), 'utf-8');

describe('SchedulesComponent — terminal-visibility wiring (pins)', () => {
  describe('model consumption', () => {
    it('imports the shared persistence + split helpers', () => {
      expect(componentSrc).toContain('readShowTerminalPreference');
      expect(componentSrc).toContain('splitByTerminalVisibility');
      expect(componentSrc).toContain('writeShowTerminalPreference');
    });

    it('toggle state is EAGERLY initialized from storage at field-init (no one-render flash)', () => {
      expect(componentSrc).toMatch(
        /readonly showTerminal = signal\(readShowTerminalPreference\(localStorage\)\)/,
      );
    });

    it('no restore machinery remains (guard flag gone; storage read is the field initializer)', () => {
      expect(componentSrc).not.toMatch(/showTerminalRestored/);
      expect(componentSrc).toContain('readShowTerminalPreference(localStorage)');
    });

    it('toggle handler writes to localStorage (persistence on toggle)', () => {
      expect(componentSrc).toContain('writeShowTerminalPreference(localStorage, checked)');
    });

    it('terminal split runs over the UNFILTERED schedules signal', () => {
      expect(componentSrc).toContain('splitByTerminalVisibility(this.schedules(), this.showTerminal())');
    });

    it('filteredSchedules applies the terminal split BEFORE the chip/type filters', () => {
      const computedBody =
        /readonly filteredSchedules = computed[\s\S]*?let filtered: Schedule\[\] = this\.terminalSplit\(\)\.visible;[\s\S]*?statusFilter\(\)[\s\S]*?typeFilter\(\)[\s\S]*?return filtered;/;
      expect(componentSrc).toMatch(computedBody);
    });
  });

  describe('stats badges stay truthful over ALL rows', () => {
    it('active/stopped/total counts derive from the raw signal, never the visible subset', () => {
      expect(componentSrc).toMatch(
        /readonly activeCount = computed\(\(\): number =>\s*\n\s*this\.schedules\(\)\.filter/,
      );
      expect(componentSrc).toMatch(
        /readonly stoppedCount = computed\(\(\): number =>\s*\n\s*this\.schedules\(\)\.filter/,
      );
      expect(componentSrc).toMatch(
        /readonly totalCount = computed\(\(\): number => this\.schedules\(\)\.length\)/,
      );
    });

    it('header badges still bind the all-rows counts', () => {
      expect(templateSrc).toContain('{{ activeCount() }} active');
      expect(templateSrc).toContain('{{ stoppedCount() }} stopped');
    });
  });

  describe('template wiring', () => {
    it('renders the toggle checkbox bound to showTerminal()', () => {
      expect(templateSrc).toContain('[checked]="showTerminal()"');
      expect(templateSrc).toContain('onShowTerminalChange($event.checked)');
    });

    it('renders the hidden-count line bound to hiddenTerminalCount()', () => {
      expect(templateSrc).toContain('hiddenTerminalCount()');
      expect(templateSrc).toContain('completed/cancelled hidden');
    });

    it('hidden-count line is gated on !showTerminal() && hiddenTerminalCount() > 0', () => {
      expect(templateSrc).toContain('@if (!showTerminal() && hiddenTerminalCount() > 0)');
    });
  });

  describe('include_cancelled gating (review follow-up 🟡1)', () => {
    it('the schedules fetch passes the toggle: ON → ?include_cancelled=true, OFF → bare request', () => {
      expect(componentSrc).toContain(
        'this.schedulerService.listSchedules(this.showTerminal())',
      );
    });

    it('the fetch REACTS to the toggle flip (constructor effect drives loadSchedules)', () => {
      expect(componentSrc).toMatch(
        /constructor\(\)[\s\S]*?effect\(\(\) => \{\s*this\.loadSchedules\(\);\s*\}\);/,
      );
    });

    it('initial load is single-sourced in the effect (ngOnInit no longer fetches separately)', () => {
      const ngOnInitBody = componentSrc.match(/ngOnInit\(\): void \{([\s\S]*?)\n  \}/);
      expect(ngOnInitBody).not.toBeNull();
      expect(ngOnInitBody![1]).toContain('loadAgents');
      expect(ngOnInitBody![1]).not.toContain('loadSchedules');
    });

    it('Refresh re-issues through the toggle-aware load (never the bare service refresh)', () => {
      const onRefreshBody = componentSrc.match(/onRefresh\(\): void \{([\s\S]*?)\n  \}/);
      expect(onRefreshBody).not.toBeNull();
      expect(onRefreshBody![1]).toContain('this.loadSchedules()');
      expect(onRefreshBody![1]).not.toContain('this.schedulerService.refreshSchedules');
    });
  });

  describe('terminal-stage empty copy points at the toggle (review follow-up 🟡2)', () => {
    it('a terminal-stage emptiness computed exists (toggle OFF + matching rows all hidden)', () => {
      expect(componentSrc).toContain('readonly isTerminalStageEmpty = computed');
    });

    it('filtered-empty block branches on it (terminal-stage → toggle copy, one line)', () => {
      expect(templateSrc).toContain('@if (isTerminalStageEmpty())');
      expect(templateSrc).toContain('to reveal hidden rows');
    });

    it('genuine filter-miss cases keep the original copy (clear-all promise intact)', () => {
      expect(templateSrc).toContain('No schedules match your filters');
      expect(templateSrc).toContain('clear all filters');
    });
  });
});
