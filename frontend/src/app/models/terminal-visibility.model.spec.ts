// Terminal-visibility model spec — the shared client-side filter behind
// the "Show completed & cancelled" toggle on the sources + schedules
// surfaces.
//
// The COMPONENT wiring is pinned separately against the real source
// text (source-list.terminal-pins.spec.ts /
// schedules.terminal-pins.spec.ts); THIS spec exercises the REAL
// production logic imported from terminal-visibility.model.ts:
//   - default active-only rendering (terminal rows drop out)
//   - toggle reveals terminal rows
//   - localStorage persistence (initial read + toggle write roundtrip)
//   - hidden-count line's N (terminal count over ALL rows)
//   - counts truthful over ALL rows (total/visible/hidden from one pass)

import {
  SHOW_TERMINAL_STORAGE_KEY,
  TERMINAL_ROW_STATUSES,
  isTerminalRowStatus,
  readShowTerminalPreference,
  splitByTerminalVisibility,
  writeShowTerminalPreference,
} from './terminal-visibility.model';
import type { StorageLike } from './terminal-visibility.model';
import type { Schedule } from './scheduler.model';
import type { Source } from './index';

// ── Fixtures ─────────────────────────────────────────────────────────

interface Row {
  id: string;
  status: string;
}

function makeRow(id: string, status: string): Row {
  return { id, status };
}

/** The graveyard shape from the field report: one armed alarm among terminal once-alarms. */
const GRAVEYARD_ROWS: Row[] = [
  makeRow('patrol-1', 'stopped'),
  makeRow('patrol-2', 'stopped'),
  makeRow('checkpoint-1', 'cancelled'),
  makeRow('watchdog-1', 'stopped'),
  makeRow('armed-alarm', 'running'),
  makeRow('firing-alarm', 'starting'),
  makeRow('errored-alarm', 'error'),
];

function makeSchedule(id: string, status: Schedule['status']): Schedule {
  return {
    id,
    name: id,
    status,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    config: { type: 'one-time', agent: 'ari', message: 'ping' },
  };
}

function makeSource(id: string, status: Source['status']): Source {
  return {
    source_id: id,
    source_type: 'scheduler',
    name: id,
    config: {},
    enabled: true,
    status,
    created_at: '2026-01-01T00:00:00Z',
  };
}

// ── In-memory StorageLike (mirrors jobs.component.spec's localStorage mock) ──

function makeStorage(): StorageLike & { data: Record<string, string> } {
  const data: Record<string, string> = {};
  return {
    data,
    getItem: (key: string) => data[key] ?? null,
    setItem: (key: string, value: string) => {
      data[key] = value;
    },
  };
}

// ── isTerminalRowStatus — the vocabulary pin ─────────────────────────

describe('isTerminalRowStatus', () => {
  it('treats exactly the daemon boot-skip set {stopped, cancelled} as terminal', () => {
    expect(TERMINAL_ROW_STATUSES).toEqual(['stopped', 'cancelled']);
    expect(isTerminalRowStatus('stopped')).toBe(true);
    expect(isTerminalRowStatus('cancelled')).toBe(true);
  });

  it('treats every non-terminal status as active (never hidden by default)', () => {
    expect(isTerminalRowStatus('running')).toBe(false);
    expect(isTerminalRowStatus('starting')).toBe(false);
    expect(isTerminalRowStatus('error')).toBe(false);
  });

  it('treats unknown statuses as NOT terminal (fail-visible, not fail-hidden)', () => {
    expect(isTerminalRowStatus('')).toBe(false);
    expect(isTerminalRowStatus('paused')).toBe(false);
    expect(isTerminalRowStatus('something-new-from-the-wire')).toBe(false);
  });
});

// ── splitByTerminalVisibility ─────────────────────────────────────────

describe('splitByTerminalVisibility — default active-only view', () => {
  it('hides terminal rows by default and counts them for the hidden line', () => {
    const split = splitByTerminalVisibility(GRAVEYARD_ROWS, false);
    expect(split.visible.map((r) => r.id)).toEqual(['armed-alarm', 'firing-alarm', 'errored-alarm']);
    expect(split.hiddenTerminal).toBe(4);
    expect(split.total).toBe(GRAVEYARD_ROWS.length);
  });

  it('preserves input order of the visible subset', () => {
    const rows = [
      makeRow('b', 'running'),
      makeRow('a-terminal', 'stopped'),
      makeRow('c', 'cancelled'),
      makeRow('d', 'running'),
    ];
    const split = splitByTerminalVisibility(rows, false);
    expect(split.visible.map((r) => r.id)).toEqual(['b', 'd']);
  });

  it('works on the Source shape (sources surface payload)', () => {
    const sources = [
      makeSource('sched-1', 'stopped'),
      makeSource('sched-2', 'cancelled'),
      makeSource('live-telegram', 'running'),
    ];
    const split = splitByTerminalVisibility(sources, false);
    expect(split.visible.map((s) => s.source_id)).toEqual(['live-telegram']);
    expect(split.hiddenTerminal).toBe(2);
    expect(split.total).toBe(3);
  });

  it('works on the Schedule shape (schedules surface payload)', () => {
    const schedules = [
      makeSchedule('done-alarm', 'stopped'),
      makeSchedule('live-alarm', 'running'),
    ];
    const split = splitByTerminalVisibility(schedules, false);
    expect(split.visible.map((s) => s.id)).toEqual(['live-alarm']);
    expect(split.hiddenTerminal).toBe(1);
    expect(split.total).toBe(2);
  });

  it('is honest on the all-terminal edge (never empty-by-accident)', () => {
    const rows = [makeRow('t1', 'stopped'), makeRow('t2', 'cancelled')];
    const split = splitByTerminalVisibility(rows, false);
    expect(split.visible).toEqual([]);
    expect(split.hiddenTerminal).toBe(2);
    expect(split.total).toBe(2);
  });

  it('handles an empty list (nothing hidden, nothing shown)', () => {
    const split = splitByTerminalVisibility([], false);
    expect(split.visible).toEqual([]);
    expect(split.hiddenTerminal).toBe(0);
    expect(split.total).toBe(0);
  });
});

describe('splitByTerminalVisibility — toggle reveals terminal rows', () => {
  it('returns ALL rows (including terminal) with nothing hidden', () => {
    const split = splitByTerminalVisibility(GRAVEYARD_ROWS, true);
    expect(split.visible.map((r) => r.id)).toEqual(GRAVEYARD_ROWS.map((r) => r.id));
    expect(split.hiddenTerminal).toBe(0);
    expect(split.total).toBe(GRAVEYARD_ROWS.length);
  });

  it('does not mutate the input list', () => {
    const rows = [makeRow('a', 'stopped'), makeRow('b', 'running')];
    const snapshot = [...rows];
    splitByTerminalVisibility(rows, true);
    expect(rows).toEqual(snapshot);
  });
});

describe('splitByTerminalVisibility — counts truthful over ALL rows', () => {
  it('total and hiddenTerminal are derived from the unfiltered list in the same pass', () => {
    // 9 terminal + 1 active — the field-report shape (76 rows, ~75 terminal).
    const rows: Row[] = [];
    for (let i = 0; i < 75; i++) rows.push(makeRow(`t${i}`, i % 2 === 0 ? 'stopped' : 'cancelled'));
    rows.push(makeRow('the-one-armed-alarm', 'running'));

    const hidden = splitByTerminalVisibility(rows, false);
    expect(hidden.total).toBe(76);
    expect(hidden.hiddenTerminal).toBe(75);
    expect(hidden.visible).toHaveLength(1);

    const shown = splitByTerminalVisibility(rows, true);
    // The TOTAL stays truthful regardless of toggle state — badges/counters
    // on the surfaces must never silently shrink to the visible subset.
    expect(shown.total).toBe(76);
    expect(shown.visible).toHaveLength(76);
    expect(shown.hiddenTerminal).toBe(0);
  });
});

// ── localStorage persistence ──────────────────────────────────────────

describe('showTerminal localStorage persistence', () => {
  it('write → read roundtrips the toggle choice', () => {
    const storage = makeStorage();
    expect(readShowTerminalPreference(storage)).toBe(false); // default when unset

    writeShowTerminalPreference(storage, true);
    expect(readShowTerminalPreference(storage)).toBe(true);

    writeShowTerminalPreference(storage, false);
    expect(readShowTerminalPreference(storage)).toBe(false);
    expect(storage.data[SHOW_TERMINAL_STORAGE_KEY]).toBe('false');
  });

  it('persists under the namespaced key ensemble.sources.showTerminal', () => {
    const storage = makeStorage();
    writeShowTerminalPreference(storage, true);
    expect(storage.data[SHOW_TERMINAL_STORAGE_KEY]).toBe('true');
    expect(SHOW_TERMINAL_STORAGE_KEY).toBe('ensemble.sources.showTerminal');
  });

  it('initial load reads the stored value written by a previous page load', () => {
    const storage = makeStorage();
    storage.data[SHOW_TERMINAL_STORAGE_KEY] = 'true';
    expect(readShowTerminalPreference(storage)).toBe(true);
  });

  it('only the exact string "true" enables the toggle', () => {
    for (const garbage of ['1', 'yes', 'TRUE', 'false', '', 'true ']) {
      const storage = makeStorage();
      storage.data[SHOW_TERMINAL_STORAGE_KEY] = garbage;
      expect(readShowTerminalPreference(storage)).toBe(false);
    }
  });

  it('falls back to the default (false) when storage is unavailable', () => {
    expect(readShowTerminalPreference(null)).toBe(false);
    expect(readShowTerminalPreference(undefined)).toBe(false);

    const throwingStorage: StorageLike = {
      getItem: () => {
        throw new Error('localStorage unavailable');
      },
      setItem: () => {
        throw new Error('localStorage unavailable');
      },
    };
    expect(readShowTerminalPreference(throwingStorage)).toBe(false);
    expect(() => writeShowTerminalPreference(throwingStorage, true)).not.toThrow();
  });
});
