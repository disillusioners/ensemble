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

    it('default toggle state is ACTIVE-only (false)', () => {
      expect(componentSrc).toMatch(/readonly showTerminal = signal<boolean>\(false\)/);
    });

    it('restores the persisted preference on first construction (reads storage)', () => {
      expect(componentSrc).toContain('readShowTerminalPreference(localStorage)');
      expect(componentSrc).toMatch(/showTerminalRestored/);
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
});
