import { chartSeriesColor } from '../../../../components/charts/chart-theme';

/**
 * The RESOURCE TIMELINE's series vocabulary.
 *
 * These five series are the resource module's OWN domain vocabulary — not
 * chart geometry — so they live with the timeline tab, not with the
 * chart engine (task 4.12 deleted the engine that used to carry them).
 *
 * The three UNIT CLASSES the timeline's panels are grouped by (IMPL-10):
 * memory (MB — the two memory series share a scale), CPU (%) and counts
 * (`threads`, `instances_active`). Toggling a count series on must
 * never land it on an MB/% panel — the unit class travels with the
 * series.
 */

/** The timeline's unit classes — the panel grouping key. */
export type TimelineUnitClass = 'memory' | 'cpu' | 'count';

/** The series keys the timeline can show. */
export type TimelineSeriesKey =
  | 'daemon_rss_mb'
  | 'cgroup_mem_mb'
  | 'daemon_cpu'
  | 'threads'
  | 'instances_active';

/** One series definition. */
export interface TimelineSeriesDef {
  readonly key: TimelineSeriesKey;
  readonly label: string;
  readonly color: string;
  /** The unit class — the panel grouping key. */
  readonly unitClass: TimelineUnitClass;
}

/** Default-on series per T2: memory (both) + CPU. */
export const DEFAULT_TIMELINE_SERIES: readonly TimelineSeriesKey[] = [
  'daemon_rss_mb',
  'cgroup_mem_mb',
  'daemon_cpu',
];

export const TIMELINE_SERIES_DEFS: readonly TimelineSeriesDef[] = [
  {
    key: 'daemon_rss_mb',
    label: 'Daemon RSS (MB)',
    color: chartSeriesColor(0),
    unitClass: 'memory',
  },
  {
    key: 'cgroup_mem_mb',
    label: 'Cgroup memory (MB)',
    color: chartSeriesColor(1),
    unitClass: 'memory',
  },
  {
    key: 'daemon_cpu',
    label: 'Daemon CPU (%)',
    color: chartSeriesColor(2),
    unitClass: 'cpu',
  },
  {
    key: 'threads',
    label: 'Threads',
    color: chartSeriesColor(3),
    unitClass: 'count',
  },
  {
    key: 'instances_active',
    label: 'Active instances',
    color: chartSeriesColor(4),
    unitClass: 'count',
  },
];

// ── THE ENGINE-KEY COMPATIBILITY SHIM (BLOCKER B-1 fold, 2026-11-04) ────
//
// THE DAEMON SPEAKS TWO VOCABULARIES (proven — tester functional gate
// 2026-11-04 §4; reproduced live in this fold's evidence):
//
//   • `/api/resources/history` answers in the FE-style keys — the router
//     SYNTHESIZES them at `daemon/routers/resources.py:675`
//     (`daemon_cpu`, `daemon_rss_mb`, `cgroup_mem_mb`, `threads`,
//     `instances_active`).
//   • `/api/resources/anomalies` answers in the ENGINE keys VERBATIM —
//     the same router passes the detector's internal `series_key`
//     straight through at `daemon/routers/resources.py:820`
//     (`ResourceAnomaly.series_key`), and the producer observes the
//     engine keyspace at `daemon/services/resource_anomaly.py:1252-1311`.
//
// The two vocabularies' intersection is EMPTY (`[]` — gate §4). An
// engine-keyed marker therefore resolved NO unit class and NO dataset:
// `unitClassOf` returned null → the marker joined no panel → the
// Chart.js overlay received an empty marker list → nothing was drawn
// and the chart→drill-down pointer path was dead (the tester's 148
// real clicks, 0 openings).
//
// THIS TABLE IS THE COMPATIBILITY SHIM — the FE-side join layer over
// the dual vocabulary. It is NOT the durable fix. The durable fix is
// DAEMON-SIDE one-vocabulary normalization at the `resources.py:820`
// serialization site (owner: the metrics lane — routed in
// `.agents/shared/planning/metrics-unification/fe-t1-build/council-fold-record.md`).
// When that lands, engine keys disappear from the wire and this whole
// shim becomes deletable in the same lane.
//
// The displayed words are UNCHANGED by the shim: the marker label
// (`anomalyMarkerLabel`) composes `kind · shape · severity ·
// series_key · peak` from the anomaly verbatim (5.7), so an engine
// marker still reads `series daemon.rss_kb · peak 345996` — the API's
// words, never renamed in the UI.
//
// UNITS / POSITIONING: the overlay draws markers in a TIME-ONLY lane at
// the top of the plot (`anomaly-overlay-plugin.ts`: `laneY =
// chartArea.top + ANOMALY_MARKER_RADIUS`) — the series VALUE never
// touches a marker's y, so NO scale conversion is applied anywhere.
// `factorToAnchorUnit` documents the engine→panel unit relationship per
// entry (derived empirically from the real payloads, see the fold
// evidence) for the durable daemon-side fix and for any future
// value-positioned rendering; it is deliberately NOT consumed by the
// renderer.

/** One engine-vocabulary entry of the compatibility shim. */
export interface EngineSeriesCompatEntry {
  /** The `series_key` exactly as `/api/resources/anomalies` serves it. */
  readonly engineKey: string;
  /** The FE history series the marker ANCHORS to (the dataset join). */
  readonly anchorKey: TimelineSeriesKey;
  /** The unit class — must equal the anchor def's unit class (spec-pinned). */
  readonly unitClass: TimelineUnitClass;
  /** The unit the ENGINE serves the series' values in. */
  readonly engineUnit: string;
  /**
   * Multiplier taking an ENGINE value to the ANCHOR series' plotted unit
   * (documented only — the marker lane is time-positioned, no conversion
   * is applied; `null` when the engine series has no FE counterpart and
   * the relationship is nominal).
   */
  readonly factorToAnchorUnit: number | null;
  /** WHICH source fed the entry: the real payload, the producer source, or both. */
  readonly provenance: string;
}

/**
 * The ENGINE keyspace, exactly as the producer emits it — enumerated
 * from the REAL `/api/resources/anomalies` payload of this fold's
 * fenced dev daemon (journal fired 08:1xZ: `daemon.rss_kb`,
 * `daemon.cpu_pct`, `cgroup.anon_kb`) and of the tester gate
 * (2026-11-04 §4: `daemon.rss_kb`, `cgroup.anon_kb`) — plus, for keys
 * the journals had not yet emitted, READ-ONLY from the producer's
 * observe sites (`daemon/services/resource_anomaly.py:1252-1311`) and
 * its `kind` map (`:816-839`). Each entry says which source fed it.
 */
export const ENGINE_SERIES_COMPAT: readonly EngineSeriesCompatEntry[] = [
  {
    engineKey: 'daemon.rss_kb',
    anchorKey: 'daemon_rss_mb',
    unitClass: 'memory',
    engineUnit: 'KB',
    factorToAnchorUnit: 1 / 1024,
    provenance:
      'REAL PAYLOAD (this fold: journal served series_key "daemon.rss_kb" peak 345996.0 baseline 303936.0; gate 2026-11-04 §4) + PRODUCER observe resource_anomaly.py:1252 (minute-row rss_kb). SCALE DERIVED EMPIRICALLY: baseline 303936.0 KB ÷ 1024 = 296.81 MB ≈ /history daemon_rss_mb 298.66 MB at t_start (ratio 1.0062) — clean ×1024; the peak exceeds the 30 s-resolution history max because the anomaly tier catches sub-sample spikes',
  },
  {
    engineKey: 'cgroup.anon_kb',
    anchorKey: 'cgroup_mem_mb',
    unitClass: 'memory',
    engineUnit: 'KB',
    factorToAnchorUnit: 1 / 1024,
    provenance:
      'REAL PAYLOAD (this fold: journal served series_key "cgroup.anon_kb" peak 10359808.0; gate 2026-11-04 §4) + PRODUCER observe resource_anomaly.py:1279-1292 (cg_anon_kb, or cg_anon_mb×1024 on the live roll-up path). SCALE: the UNIT factor is clean ×1024 (baseline 1576960 KB ÷ 1024 = 1540 MB), but the SERIES is the cgroup anon COMPONENT while the anchored cgroup_mem_mb is the cgroup TOTAL (anon = 22.5% of total at the same bucket) — same unit class, different measure; the anchor is the panel join only',
  },
  {
    engineKey: 'daemon.anon_kb',
    anchorKey: 'daemon_rss_mb',
    unitClass: 'memory',
    engineUnit: 'KB',
    factorToAnchorUnit: 1 / 1024,
    provenance:
      'PRODUCER SOURCE ONLY (observe resource_anomaly.py:1265-1269 — the B2 provenance split moved the daemon anon pool onto its own key; not in the journals observed so far). Anchored to the daemon-scoped memory series.',
  },
  {
    engineKey: 'daemon.cpu_pct',
    anchorKey: 'daemon_cpu',
    unitClass: 'cpu',
    engineUnit: '%',
    factorToAnchorUnit: 1,
    provenance:
      'REAL PAYLOAD (this fold: journal served series_key "daemon.cpu_pct" peak 10.2833 baseline 6.4667 — in-window /history daemon_cpu 5.37-8.57 %, the same measure and unit) + PRODUCER observe resource_anomaly.py:1295-1318 (cpu_pct derived from the cumulative usec counters, the /history delta rule). Same unit both sides (no scale).',
  },
  {
    engineKey: 'daemon.threads',
    anchorKey: 'threads',
    unitClass: 'count',
    engineUnit: 'count',
    factorToAnchorUnit: 1,
    provenance:
      'PRODUCER SOURCE ONLY (observe resource_anomaly.py:1255 — minute-row threads; not in the journals observed so far). Same unit both sides (no scale).',
  },
  {
    engineKey: 'daemon.fds',
    anchorKey: 'threads',
    unitClass: 'count',
    engineUnit: 'count',
    factorToAnchorUnit: 1,
    provenance:
      'PRODUCER SOURCE ONLY (observe resource_anomaly.py:1258 — minute-row fds; kind map resource_anomaly.py:835; not in the journals observed so far). No FE fds series exists — anchored to the count panel\'s first def.',
  },
  {
    engineKey: 'cgroup.current_kb',
    anchorKey: 'cgroup_mem_mb',
    unitClass: 'memory',
    engineUnit: 'KB',
    factorToAnchorUnit: 1 / 1024,
    provenance:
      'PRODUCER KIND MAP ONLY (resource_anomaly.py:834 — no live observe site in the current ingest; defensive entry so the key can never strand a marker if the producer emits it).',
  },
];

/**
 * The INSTANCE-SCOPED engine series — `instance.<iid>.charged_kb` and
 * `instance.<iid>.child_rss_kb` (producer `resource_anomaly.py:1290-1311`,
 * kind map `:824-826`). The instance id is dynamic, so these are matched
 * by PATTERN, not by exact key. Source: PRODUCER ONLY (no instance row
 * was in the journals observed so far).
 */
export const INSTANCE_SERIES_PATTERN = /^instance\.[^.]+\.(charged_kb|child_rss_kb)$/;

/** The per-suffix join of the instance-scoped engine series. */
const INSTANCE_SERIES_SUFFIX_COMPAT: Readonly<
  Record<string, Omit<EngineSeriesCompatEntry, 'engineKey'>>
> = {
  charged_kb: {
    anchorKey: 'daemon_rss_mb',
    unitClass: 'memory',
    engineUnit: 'KB',
    factorToAnchorUnit: 1 / 1024,
    provenance:
      'PRODUCER SOURCE ONLY (observe resource_anomaly.py:1295-1306 — charged_bytes/1024; not in the journals observed so far).',
  },
  child_rss_kb: {
    anchorKey: 'daemon_rss_mb',
    unitClass: 'memory',
    engineUnit: 'KB',
    factorToAnchorUnit: 1 / 1024,
    provenance:
      'PRODUCER SOURCE ONLY (observe resource_anomaly.py:1307-1311; kind map :824-826; not in the journals observed so far).',
  },
};

/** The resolved join of a wire series key — null when truly unmapped. */
export interface TimelineSeriesJoin {
  readonly unitClass: TimelineUnitClass;
  readonly anchorKey: TimelineSeriesKey;
}

/**
 * Resolve BOTH vocabularies to a panel + dataset join.
 *
 *  • an FE history key (`daemon_rss_mb`, …) resolves to itself — the
 *    pass-through that predates the shim;
 *  • an ENGINE key (`daemon.rss_kb`, …) resolves through
 *    {@link ENGINE_SERIES_COMPAT} or the instance pattern;
 *  • anything else resolves to `null` — the marker joins NO panel
 *    (never a guess; the same fail-closed rule as the unit vocabulary).
 */
export function resolveTimelineSeriesJoin(key: string): TimelineSeriesJoin | null {
  const feDef = TIMELINE_SERIES_DEFS.find((def) => def.key === (key as TimelineSeriesKey));
  if (feDef) {
    return { unitClass: feDef.unitClass, anchorKey: feDef.key };
  }
  const compat = ENGINE_SERIES_COMPAT.find((entry) => entry.engineKey === key);
  if (compat) {
    return { unitClass: compat.unitClass, anchorKey: compat.anchorKey };
  }
  const instanceMatch = INSTANCE_SERIES_PATTERN.exec(key);
  if (instanceMatch) {
    const suffix = INSTANCE_SERIES_SUFFIX_COMPAT[instanceMatch[1]];
    if (suffix) {
      return { unitClass: suffix.unitClass, anchorKey: suffix.anchorKey };
    }
  }
  return null;
}

/**
 * The DATASET an anomaly marker anchors to within a unit-class panel.
 *
 * The mapped anchor is used when its series is enabled; otherwise the
 * panel's first ENABLED series anchors the marker (markers are drawn in
 * the time-only top lane, so the anchor only decides which dataset the
 * DRAW + CLICK joins read — never a value). Falls back to the mapped
 * anchor when the panel has no enabled series (the panel is not
 * rendered then anyway).
 */
export function markerAnchorKey(
  unitClass: TimelineUnitClass,
  enabled: readonly TimelineSeriesKey[],
  mappedAnchor: TimelineSeriesKey,
): TimelineSeriesKey {
  const enabledDefs = TIMELINE_SERIES_DEFS.filter(
    (def) => def.unitClass === unitClass && enabled.includes(def.key),
  );
  if (enabledDefs.some((def) => def.key === mappedAnchor)) {
    return mappedAnchor;
  }
  return enabledDefs[0]?.key ?? mappedAnchor;
}
