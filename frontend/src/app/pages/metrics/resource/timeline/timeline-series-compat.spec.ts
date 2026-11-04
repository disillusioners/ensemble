import {
  ENGINE_SERIES_COMPAT,
  INSTANCE_SERIES_PATTERN,
  TIMELINE_SERIES_DEFS,
  TimelineSeriesKey,
  markerAnchorKey,
  resolveTimelineSeriesJoin,
} from './timeline-series';

/**
 * The B-1 COMPATIBILITY SHIM spec — the dual-vocabulary join table
 * (tester gate 2026-11-04 BLOCKER B-1 fold).
 *
 * The daemon speaks two vocabularies (history = FE-style keys,
 * anomalies = ENGINE keys); this spec pins the shim that joins them:
 *  - every table entry is INTERNALLY CONSISTENT (the anchor's def
 *    exists and its unit class matches the entry's);
 *  - the table covers the ENUMERATED engine keyspace (each key cited
 *    to its producer source);
 *  - resolution is FAIL-CLOSED (unknown keys join no panel — never a
 *    guess), and the FE keys pass through unchanged (the shim is
 *    additive, never a rename);
 *  - the unit scale is DOCUMENTED per entry (KB → MB ÷1024) but
 *    consumed by NOTHING — the marker lane is time-positioned.
 */

// ── The enumerated ENGINE keyspace, with its producer source ──────────
//
// `daemon/services/resource_anomaly.py` observes exactly these series
// keys (`ingest` :1252-1311) and maps kinds for them (`kind_for_series`
// :816-839, which additionally names `cgroup.current_kb` with no live
// observe site — a defensive entry the table carries too). If the
// daemon grows a NEW engine key, the mapping must gain an entry here —
// this list is the drift alarm, and the gate evidence records the
// enumeration method (real payload + producer source, READ-ONLY).
const ENUMERATED_ENGINE_KEYS: readonly string[] = [
  'daemon.rss_kb', //        :1252  (minute-row rss_kb)
  'daemon.threads', //       :1255  (minute-row threads)
  'daemon.fds', //           :1258  (minute-row fds)
  'daemon.anon_kb', //       :1269  (minute-row anon_kb — the B2 provenance split)
  'cgroup.anon_kb', //       :1279  (cg_anon_kb | cg_anon_mb×1024)
  'daemon.cpu_pct', //       :1318  (derived from the cumulative usec counters)
  'cgroup.current_kb', //    kind map :834 only (no live observe site)
];

const ENUMERATED_INSTANCE_SUFFIXES: readonly string[] = ['charged_kb', 'child_rss_kb']; // :1295-1311

describe('the B-1 engine-key compatibility shim (timeline-series)', () => {
  describe('ENGINE_SERIES_COMPAT — the mapping table', () => {
    it('every entry is internally consistent: the anchor def exists and the unit classes agree', () => {
      for (const entry of ENGINE_SERIES_COMPAT) {
        const anchorDef = TIMELINE_SERIES_DEFS.find((def) => def.key === entry.anchorKey);
        expect(anchorDef).toBeDefined();
        expect(anchorDef!.unitClass).toBe(entry.unitClass);
      }
    });

    it('covers EXACTLY the enumerated engine keyspace — no gaps, no invented keys', () => {
      const tableKeys = ENGINE_SERIES_COMPAT.map((entry) => entry.engineKey).sort();
      expect(tableKeys).toEqual([...ENUMERATED_ENGINE_KEYS].sort());
    });

    it('documents the unit scale per entry: KB entries carry ÷1024, % and counts carry 1', () => {
      for (const entry of ENGINE_SERIES_COMPAT) {
        if (entry.engineUnit === 'KB') {
          expect(entry.factorToAnchorUnit).toBeCloseTo(1 / 1024, 12);
        } else {
          expect(entry.factorToAnchorUnit).toBe(1);
        }
      }
    });

    it('carries a provenance string per entry citing its source (real payload vs producer site)', () => {
      for (const entry of ENGINE_SERIES_COMPAT) {
        expect(entry.provenance.length).toBeGreaterThan(20);
        // Every provenance names its evidence: the gate's real payload, the
        // producer's observe site / kind map, or both.
        expect(entry.provenance).toMatch(/PAYLOAD|observe|kind map/);
      }
    });
  });

  describe('INSTANCE_SERIES_PATTERN — the instance-scoped engine series', () => {
    it.each(ENUMERATED_INSTANCE_SUFFIXES)('matches instance.<iid>.%s (the producer\'s shape)', (suffix) => {
      expect(INSTANCE_SERIES_PATTERN.test(`instance.i-abc123.${suffix}`)).toBe(true);
    });

    it('rejects malformed / non-instance shapes', () => {
      expect(INSTANCE_SERIES_PATTERN.test('instance..child_rss_kb')).toBe(false);
      expect(INSTANCE_SERIES_PATTERN.test('instance.i-1.other_kb')).toBe(false);
      expect(INSTANCE_SERIES_PATTERN.test('daemon.rss_kb')).toBe(false);
    });
  });

  describe('resolveTimelineSeriesJoin — the dual-vocabulary resolver', () => {
    it('passes FE history keys through to themselves (the shim is additive, never a rename)', () => {
      for (const def of TIMELINE_SERIES_DEFS) {
        expect(resolveTimelineSeriesJoin(def.key)).toEqual({
          unitClass: def.unitClass,
          anchorKey: def.key,
        });
      }
    });

    it.each(ENUMERATED_ENGINE_KEYS)('resolves the ENGINE key %s to its panel + anchor', (engineKey) => {
      const join = resolveTimelineSeriesJoin(engineKey);
      expect(join).not.toBeNull();
      const entry = ENGINE_SERIES_COMPAT.find((candidate) => candidate.engineKey === engineKey)!;
      expect(join).toEqual({ unitClass: entry.unitClass, anchorKey: entry.anchorKey });
    });

    it.each(ENUMERATED_INSTANCE_SUFFIXES)(
      'resolves the instance-scoped engine series instance.<iid>.%s to the memory panel',
      (suffix) => {
        const join = resolveTimelineSeriesJoin(`instance.i-42.${suffix}`);
        expect(join).toEqual({ unitClass: 'memory', anchorKey: 'daemon_rss_mb' });
      },
    );

    it('resolves UNKNOWN keys to null — the marker joins NO panel (never a guess)', () => {
      expect(resolveTimelineSeriesJoin('bogus.metric')).toBeNull();
      expect(resolveTimelineSeriesJoin('daemon.rss_mb')).toBeNull(); // near-miss spelling
      expect(resolveTimelineSeriesJoin('DAEMON.rss_kb')).toBeNull(); // case-exact, no folding
      expect(resolveTimelineSeriesJoin('')).toBeNull();
    });
  });

  describe('markerAnchorKey — the enabled-aware dataset anchor', () => {
    it('uses the mapped anchor when its series is enabled', () => {
      const enabled: TimelineSeriesKey[] = ['daemon_rss_mb', 'daemon_cpu'];
      expect(markerAnchorKey('memory', enabled, 'daemon_rss_mb')).toBe('daemon_rss_mb');
      expect(markerAnchorKey('memory', enabled, 'cgroup_mem_mb')).toBe('daemon_rss_mb');
    });

    it('falls back to the panel\'s FIRST ENABLED series when the mapped anchor is toggled off', () => {
      const enabled: TimelineSeriesKey[] = ['cgroup_mem_mb', 'daemon_cpu'];
      expect(markerAnchorKey('memory', enabled, 'daemon_rss_mb')).toBe('cgroup_mem_mb');
    });

    it('returns the mapped anchor when the panel has NO enabled series (the panel is not rendered then)', () => {
      expect(markerAnchorKey('count', [], 'threads')).toBe('threads');
    });
  });
});
