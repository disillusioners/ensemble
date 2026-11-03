import { chartSeriesColor } from '../../../components/charts/chart-theme';

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
  readonly unitClass: 'memory' | 'cpu' | 'count';
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
