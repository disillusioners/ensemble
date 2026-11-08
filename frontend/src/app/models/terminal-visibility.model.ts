/**
 * Terminal-row visibility — the shared client-side filter behind the
 * "Show completed & cancelled" toggle on the sources and schedules
 * surfaces.
 *
 * WHY THIS EXISTS: the sources/schedules views list every row, and the
 * scheduler graveyard (one-shot alarms that fired and were stopped or
 * cancelled: patrols, checkpoints, watchdogs) buries the handful of
 * live rows. GC is healthy — this is purely a VIEW problem, solved
 * CLIENT-SIDE from the existing payloads (no API contract change).
 *
 * TERMINAL VOCABULARY: the daemon's own boot auto-start skip treats
 * ``cancelled`` as "a peer of STOPPED" (daemon/sources/registry.py —
 * "status in {STOPPED, CANCELLED}" rows are never re-armed). That is
 * the house definition of a terminal row: a row the system itself will
 * not start again. Everything else — ``running``, ``starting``,
 * ``error`` (a live adapter that errored but is resumable) — is
 * ACTIVE and stays visible by default.
 *
 * COUNTS: counts/badges on these surfaces must stay truthful for ALL
 * rows, so the split is computed in ONE pass over the UNFILTERED list
 * (see {@link splitByTerminalVisibility}) and the components derive
 * their stats from the raw signal, never from the visible subset.
 */

/** Minimal storage surface needed to persist the toggle preference. */
export type StorageLike = Pick<Storage, 'getItem' | 'setItem'>;

/**
 * localStorage key for the toggle preference — namespaced per house
 * convention (cf. ``skills-page-active-only``,
 * ``ensemble-next-instance-agent``). SHARED by the sources and
 * schedules surfaces: it is one user preference about one kind of row.
 */
export const SHOW_TERMINAL_STORAGE_KEY = 'ensemble.sources.showTerminal';

/** Terminal source/schedule statuses — hidden by default. */
export const TERMINAL_ROW_STATUSES = ['stopped', 'cancelled'] as const;

/**
 * The terminal vocabulary as a literal union — DERIVED from
 * {@link TERMINAL_ROW_STATUSES} so the list and the type can never
 * drift apart (one vocabulary, one encoding).
 */
export type TerminalRowStatus = (typeof TERMINAL_ROW_STATUSES)[number];

/**
 * True when the row's status is terminal under the house vocabulary
 * (the daemon's boot auto-start skip set: ``stopped`` + ``cancelled``).
 * Unknown statuses are NOT terminal — an unrecognized status must not
 * be hidden by default.
 */
export function isTerminalRowStatus(status: string): boolean {
  return (TERMINAL_ROW_STATUSES as readonly string[]).includes(status);
}

/** Result of splitting a row list by terminal visibility. */
export interface TerminalVisibilitySplit<T> {
  /** Rows the surface should render for the current toggle state. */
  readonly visible: T[];
  /**
   * Terminal rows — hidden from the render when the toggle is off.
   * Always derived from the FULL list, so the "N completed/cancelled
   * hidden" line is truthful even though it is derived alongside the
   * visible subset.
   */
  readonly hiddenTerminal: number;
  /** The unfiltered row count (ALL rows, before any filtering). */
  readonly total: number;
}

/**
 * Split a row list into the visible subset and the hidden-terminal
 * count, in ONE pass over the unfiltered list.
 *
 * With ``showTerminal`` on, EVERYTHING is visible and nothing counts
 * as hidden (the toggle reveals terminal rows). With it off (the
 * default), terminal rows drop out of ``visible`` and are counted in
 * ``hiddenTerminal`` so the surface can honestly state what it hid.
 */
export function splitByTerminalVisibility<T extends { status: string }>(
  rows: readonly T[],
  showTerminal: boolean,
): TerminalVisibilitySplit<T> {
  const total = rows.length;
  if (showTerminal) {
    return { visible: [...rows], hiddenTerminal: 0, total };
  }
  let hiddenTerminal = 0;
  const visible: T[] = [];
  for (const row of rows) {
    if (isTerminalRowStatus(row.status)) {
      hiddenTerminal += 1;
    } else {
      visible.push(row);
    }
  }
  return { visible, hiddenTerminal, total };
}

/**
 * Read the persisted toggle preference. Only the exact string
 * ``'true'`` enables the toggle — anything else (missing key, other
 * values, corrupt writes) falls back to the DEFAULT-ACTIVE view.
 * Storage failures (private browsing / disabled storage) also fall
 * back to the default, never throw.
 */
export function readShowTerminalPreference(storage: StorageLike | null | undefined): boolean {
  try {
    return storage?.getItem(SHOW_TERMINAL_STORAGE_KEY) === 'true';
  } catch {
    return false;
  }
}

/**
 * Persist the toggle preference as the exact strings ``'true'`` /
 * ``'false'``. Storage failures are swallowed — the preference stays
 * session-local rather than breaking the toggle interaction.
 */
export function writeShowTerminalPreference(
  storage: StorageLike | null | undefined,
  value: boolean,
): void {
  try {
    storage?.setItem(SHOW_TERMINAL_STORAGE_KEY, value ? 'true' : 'false');
  } catch {
    /* private browsing / disabled storage — preference stays session-local */
  }
}
