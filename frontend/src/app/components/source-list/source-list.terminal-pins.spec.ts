// Source-list terminal-visibility pins — proves the production component
// and template actually consume terminal-visibility.model.ts (default
// active-only view, toggle, persistence, hidden-count line, truthful
// counts). Plain-TS source-grep pins (no TestBed) — house style.
//
// If any production wiring drifts away from the shared model, these pins
// fail before the behavior spec would otherwise pass silently.

import { readFileSync } from 'fs';
import { join } from 'path';

const dir = join(__dirname, '.');
const componentSrc = readFileSync(join(dir, 'source-list.component.ts'), 'utf-8');
const templateSrc = readFileSync(join(dir, 'source-list.html'), 'utf-8');
const modelSrc = readFileSync(
  join(__dirname, '../../models/terminal-visibility.model.ts'),
  'utf-8',
);

describe('SourceListComponent — terminal-visibility wiring (pins)', () => {
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

    it('no restore machinery remains (guard flag gone, effect import gone)', () => {
      expect(componentSrc).not.toMatch(/showTerminalRestored/);
      expect(componentSrc).not.toContain('effect');
      expect(componentSrc).toContain('readShowTerminalPreference(localStorage)');
    });

    it('toggle handler writes to localStorage (persistence on toggle)', () => {
      expect(componentSrc).toContain('writeShowTerminalPreference(localStorage, checked)');
    });

    it('derives visible + hidden counts in ONE pass over the unfiltered list', () => {
      expect(componentSrc).toContain('splitByTerminalVisibility(this.sources(), this.showTerminal())');
      expect(componentSrc).toContain('visibleSources');
      expect(componentSrc).toContain('hiddenTerminalCount');
    });
  });

  describe('template wiring', () => {
    it('grid iterates the visible subset (not the raw sources)', () => {
      expect(templateSrc).toContain('@for (source of visibleSources()');
    });

    it('grid is gated on visibleSources (never empty-by-accident)', () => {
      expect(templateSrc).toContain('@if (visibleSources().length > 0)');
    });

    it('renders the hidden-count line bound to hiddenTerminalCount()', () => {
      expect(templateSrc).toContain('hiddenTerminalCount()');
      expect(templateSrc).toContain('completed/cancelled hidden');
    });

    it('hidden-count line is gated on !showTerminal() && hiddenTerminalCount() > 0', () => {
      expect(templateSrc).toContain('@if (!showTerminal() && hiddenTerminalCount() > 0)');
    });

    it('renders the toggle checkbox bound to showTerminal()', () => {
      expect(templateSrc).toContain('[checked]="showTerminal()"');
      expect(templateSrc).toContain('onShowTerminalChange($event.checked)');
    });
  });

  describe('truthful counts (sources page has no count badges today)', () => {
    it('raw `sources` signal is still the mutation source of truth', () => {
      expect(componentSrc).toContain('readonly sources = signal<Source[]>([])');
    });
  });
});
