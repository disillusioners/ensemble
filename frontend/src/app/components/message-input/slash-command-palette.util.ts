import type { CommandDefinition } from '../../models';

/**
 * Pure helpers for the slash-command autocomplete palette
 * (phase2-plan.md Task 10). Kept out of the component so the palette
 * brain is logic-mirror testable without TestBed (house style).
 *
 * TRIGGER RULE (Task 10, decided): the palette is open while the ENTIRE
 * input value is a bare slash-command fragment — starts with exactly one
 * ``/`` (NOT the ``//`` escape form, which always means a literal
 * message) followed only by ``[a-z0-9-]`` characters, case-insensitive.
 * Any whitespace (args started — no longer a bare command) or an
 * interior ``/`` (path-like text, e.g. ``/etc/hosts``) closes it.
 * ``''`` and non-slash text never open it.
 */
const SLASH_TRIGGER_RE = /^\/(?!\/)[a-z0-9-]*$/i;

export function isSlashCommandTrigger(value: string): boolean {
  return SLASH_TRIGGER_RE.test(value);
}

/**
 * Text typed after the leading slash, lowercased for prefix matching.
 * ``''`` for the bare ``/`` (shows every command); ``null`` when the
 * value is not a trigger at all (or when the caller hands us a non-string,
 * which we treat as "no command context" rather than crashing on slice).
 */
export function slashCommandQuery(value: string): string | null {
  if (typeof value !== 'string') return null;
  if (!isSlashCommandTrigger(value)) return null;
  return value.slice(1).toLowerCase();
}

/** Case-insensitive prefix match on the canonical (lowercase) name. */
export function filterCommandsByPrefix(
  commands: CommandDefinition[],
  query: string,
): CommandDefinition[] {
  const q = query.toLowerCase();
  // Defensive-only: skip entries whose name is not a string (e.g. malformed
  // registry rows). Valid entries still match on the normal path.
  return commands.filter(
    c => typeof c.name === 'string' && c.name.toLowerCase().startsWith(q),
  );
}

/**
 * Highlight movement with wrap-around: ArrowDown past the last option
 * returns to the first, ArrowUp before the first goes to the last
 * (VS Code palette convention — lists here are short).
 */
export function moveHighlight(current: number, count: number, direction: -1 | 1): number {
  if (count <= 0) return 0;
  return (current + direction + count) % count;
}

/** Accepting inserts the canonical command + one trailing space. */
export function slashAcceptText(def: CommandDefinition): string {
  return `/${def.name} `;
}

/** DOM id for the option at ``index`` — also the aria-activedescendant target. */
export function slashOptionId(index: number): string {
  return `slash-command-option-${index}`;
}

/** Polite live-region copy for open/match-count changes ('' when closed). */
export function slashPaletteLiveMessage(open: boolean, matchCount: number): string {
  if (!open) return '';
  if (matchCount === 0) return 'No matching command';
  return matchCount === 1 ? '1 command available' : `${matchCount} commands available`;
}
