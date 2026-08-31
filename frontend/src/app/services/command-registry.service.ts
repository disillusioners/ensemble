import { Injectable, signal } from '@angular/core';
import type { CommandDefinition } from '../models';

/**
 * Client-side slash-command registry — the extensible command surface for
 * the Phase 2 subsystem (phase2-plan.md Task 1).
 *
 * The FE registry is an ADVISORY pre-check only: the backend is the source
 * of truth (§7 split rule — unknown-to-FE input that POSTs anyway gets a
 * 400 ``UNKNOWN_COMMAND`` + available list; valid-but-refused gets a 200
 * rejected ack). Keeping commands here typed and seeded means the
 * autocomplete palette (Task 10 stretch) and help surfacing are additive
 * one-entry changes, proven by ``command-registry.service.spec.ts``.
 *
 * Parse rules mirror the backend ``parse_slash_command``
 * (daemon/services/command_dispatcher.py) so the advisory verdict agrees
 * with the authoritative one:
 *   - ``//`` escape is checked BEFORE ``/`` (architect O-B1, Slack
 *     convention): a leading ``//`` strips exactly ONE ``/`` and the rest
 *     is delivered as plain text.
 *   - ``/`` with an empty / whitespace-only body is NOT a command.
 *   - The command name is case-insensitive (``/COMPACT`` → ``compact``),
 *     matching the backend's ``name.lower()``.
 */
export type ParseCommandOutcome =
  /** ``//foo`` → deliver ``/foo`` as a normal message (no command branch). */
  | { escape: true; text: string }
  /** ``/compact`` → a registered command. */
  | { known: true; def: CommandDefinition }
  /** ``/foo`` → leading slash but not registered. Advisory rejection —
   *  the BE 400 path stays authoritative and feeds the same toast. */
  | { known: false; name: string }
  /** Plain text — no command semantics at all. */
  | { isCommand: false };

@Injectable({
  providedIn: 'root',
})
export class CommandRegistryService {
  private readonly registry = signal<CommandDefinition[]>([
    {
      name: 'compact',
      description: 'Compact the conversation context now (on-demand compaction)',
      argsHint: null,
    },
  ]);

  /** Read-only view for autocomplete / help surfaces (Task 10 stretch). */
  readonly commands = this.registry.asReadonly();

  /**
   * Additive extension point — registering a new command is a one-entry
   * change by construction. Duplicate names are ignored (first wins) so a
   * double registration can never desync the advisory pre-check.
   */
  registerCommand(def: CommandDefinition): void {
    if (this.registry().some(c => c.name === def.name)) return;
    this.registry.update(list => [...list, def]);
  }

  hasCommand(name: string): boolean {
    return this.registry().some(c => c.name === name);
  }

  /**
   * Parse raw composer content into a discriminated outcome. Pure and
   * synchronous — mirrors the backend parser (see class doc). The escape
   * branch preserves the remainder verbatim: ``'//compact is useful'``
   * delivers ``'/compact is useful'``.
   */
  parseCommandInput(content: string): ParseCommandOutcome {
    if (!content) return { isCommand: false };

    // O-B1: escape check FIRST — before any ``/`` interpretation.
    if (content.startsWith('//')) {
      return { escape: true, text: content.slice(1) };
    }

    if (!content.startsWith('/')) return { isCommand: false };

    const body = content.slice(1);
    if (!body || !body.trim()) return { isCommand: false };

    const name = body.trimStart().split(/\s+/, 1)[0].toLowerCase();
    if (!name) return { isCommand: false };

    const def = this.registry().find(c => c.name === name);
    if (def) return { known: true, def };
    return { known: false, name };
  }
}
