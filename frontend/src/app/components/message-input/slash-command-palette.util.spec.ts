import { CommandRegistryService } from '../../services/command-registry.service';
import {
  filterCommandsByPrefix,
  isSlashCommandTrigger,
  moveHighlight,
  slashAcceptText,
  slashCommandQuery,
  slashOptionId,
  slashPaletteLiveMessage,
} from './slash-command-palette.util';
import type { CommandDefinition } from '../../models';

/**
 * Logic-mirror specs for the slash-command autocomplete palette helpers
 * (phase2-plan.md Task 10). Plain TS, NO TestBed — the component wiring
 * and template/a11y attributes are covered by the TestBed spec
 * (message-input.component.autocomplete.spec.ts) and the e2e scenario.
 */
describe('slash-command-palette.util', () => {
  describe('isSlashCommandTrigger — trigger rule (^\\/fragments only, // never)', () => {
    it.each([
      ['/', true],
      ['/c', true],
      ['/compact', true],
      ['/COMPACT', true], // case-insensitive (registry parses case-insensitively too)
      ['/ComPact', true],
      ['/clear-history', true], // hyphen allowed (command-name charset)
      ['/compact-2', true],
    ])('%s → trigger', (value, expected) => {
      expect(isSlashCommandTrigger(value)).toBe(expected);
    });

    it.each([
      ['//compact', false], // escape form NEVER triggers
      ['//', false],
      ['//x', false],
      ['', false],
      ['hello', false],
      [' /compact', false], // must start at position 0
      ['/compact ', false], // whitespace → args started, no longer bare
      ['/compact arg', false],
      ['/com\tpact', false],
      ['/com pact', false],
      ['/compact\n', false],
      ['/etc/hosts', false], // interior slash = path-like text
      ['/compact!', false], // non command-name charset
      ['\\/compact', false],
    ])('%s → NOT a trigger', (value) => {
      expect(isSlashCommandTrigger(value)).toBe(false);
    });
  });

  describe('slashCommandQuery', () => {
    it("bare '/' → empty query (shows every command)", () => {
      expect(slashCommandQuery('/')).toBe('');
    });

    it("'/CO' → 'co' (lowercased)", () => {
      expect(slashCommandQuery('/CO')).toBe('co');
    });

    it.each(['//compact', 'hello', '/compact arg'])('%s → null (not a trigger)', (value) => {
      expect(slashCommandQuery(value)).toBeNull();
    });
  });

  describe('filterCommandsByPrefix — case-insensitive prefix match', () => {
    let commands: CommandDefinition[];

    beforeEach(() => {
      const registry = new CommandRegistryService();
      commands = registry.commands();
    });

    it("empty query shows every command (bare '/' case)", () => {
      expect(filterCommandsByPrefix(commands, '')).toEqual(commands);
    });

    it('prefix matches are case-insensitive on both sides', () => {
      expect(filterCommandsByPrefix(commands, 'CO').map(c => c.name)).toEqual(['compact']);
      expect(filterCommandsByPrefix(commands, 'compact').map(c => c.name)).toEqual(['compact']);
    });

    it('non-prefix substring does NOT match', () => {
      expect(filterCommandsByPrefix(commands, 'ompact')).toEqual([]);
      expect(filterCommandsByPrefix(commands, 'z')).toEqual([]);
    });

    it('extended registry filters across all entries', () => {
      const registry = new CommandRegistryService();
      registry.registerCommand({ name: 'clear', description: 'Clear the context' });
      const all = registry.commands();
      expect(filterCommandsByPrefix(all, '').map(c => c.name)).toEqual(['compact', 'clear']);
      expect(filterCommandsByPrefix(all, 'cl').map(c => c.name)).toEqual(['clear']);
      expect(filterCommandsByPrefix(all, 'c').map(c => c.name)).toEqual(['compact', 'clear']);
    });
  });

  describe('moveHighlight — wrap-around navigation', () => {
    it('empty list stays at 0 (no crash)', () => {
      expect(moveHighlight(0, 0, 1)).toBe(0);
      expect(moveHighlight(2, 0, -1)).toBe(0);
    });

    it('single option never moves', () => {
      expect(moveHighlight(0, 1, 1)).toBe(0);
      expect(moveHighlight(0, 1, -1)).toBe(0);
    });

    it('ArrowDown moves forward and wraps last → first', () => {
      expect(moveHighlight(0, 3, 1)).toBe(1);
      expect(moveHighlight(1, 3, 1)).toBe(2);
      expect(moveHighlight(2, 3, 1)).toBe(0); // wrap
    });

    it('ArrowUp moves backward and wraps first → last', () => {
      expect(moveHighlight(2, 3, -1)).toBe(1);
      expect(moveHighlight(1, 3, -1)).toBe(0);
      expect(moveHighlight(0, 3, -1)).toBe(2); // wrap
    });
  });

  describe('slashAcceptText — insert form', () => {
    it('inserts canonical command + exactly one trailing space', () => {
      expect(slashAcceptText({ name: 'compact', description: 'd' })).toBe('/compact ');
    });
  });

  describe('slashOptionId', () => {
    it('stable ids for aria-activedescendant', () => {
      expect(slashOptionId(0)).toBe('slash-command-option-0');
      expect(slashOptionId(12)).toBe('slash-command-option-12');
    });
  });

  describe('slashPaletteLiveMessage — polite announcement copy', () => {
    it('closed → empty (nothing announced)', () => {
      expect(slashPaletteLiveMessage(false, 3)).toBe('');
      expect(slashPaletteLiveMessage(false, 0)).toBe('');
    });

    it('open with matches → count announced', () => {
      expect(slashPaletteLiveMessage(true, 1)).toBe('1 command available');
      expect(slashPaletteLiveMessage(true, 2)).toBe('2 commands available');
    });

    it('open with zero matches → no-match hint announced', () => {
      expect(slashPaletteLiveMessage(true, 0)).toBe('No matching command');
    });
  });
});
