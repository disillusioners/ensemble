// The DATA-GAP DETECTOR spec (T1 phase 5, task 5.8 — ADJ-iv shape 2).
//
// The load-bearing pins:
//
//   * the derivation is the /history payload ONLY — a series that drops
//     to zero (or to a null sample) and REGROWS is one gap;
//   * the label contains "data gap" and NEVER restart/deploy/promote —
//     a planted "restart" wording FAILS the spec (the mutation leg below
//     was EXECUTED and recorded in phase5-evidence.md: planted → RED →
//     restored → GREEN);
//   * a trailing unavailable stretch never regrows and is NOT annotated;
//   * a leading unavailable stretch (the window's edge) is NOT a gap.

import { HistoryPoint } from '../../../../models/resources.models';
import { DATA_GAP_LABEL, ResourceDataGap, detectResourceDataGaps } from './data-gaps';

function point(t: string, values: Partial<HistoryPoint> = {}): HistoryPoint {
  return {
    t,
    daemon_cpu: 2,
    daemon_rss_mb: 1700,
    cgroup_mem_mb: 5800,
    threads: 40,
    instances_active: 5,
    ...values,
  };
}

const seriesOf = (gaps: readonly ResourceDataGap[]) => gaps.map((gap) => gap.seriesKey);

describe('detectResourceDataGaps — the client-side derivation (5.8)', () => {
  it('a series that drops to ZERO and regrows is one gap, bounded by the drop and the regrowth', () => {
    const gaps = detectResourceDataGaps([
      point('2026-11-03T10:00:00'),
      point('2026-11-03T10:05:00', { daemon_cpu: 0 }),
      point('2026-11-03T10:10:00', { daemon_cpu: 0 }),
      point('2026-11-03T10:15:00', { daemon_cpu: 3 }),
    ]);
    expect(gaps.length).toBe(1);
    expect(gaps[0].seriesKey).toBe('daemon_cpu');
    expect(gaps[0].fromMs).toBe(new Date('2026-11-03T10:05:00Z').getTime());
    expect(gaps[0].toMs).toBe(new Date('2026-11-03T10:15:00Z').getTime());
  });

  it('a series that goes NULL (a missing sample) and regrows is one gap', () => {
    const gaps = detectResourceDataGaps([
      point('2026-11-03T10:00:00'),
      point('2026-11-03T10:05:00', { cgroup_mem_mb: null }),
      point('2026-11-03T10:10:00', { cgroup_mem_mb: 5900 }),
    ]);
    expect(seriesOf(gaps)).toEqual(['cgroup_mem_mb']);
  });

  it('the label contains "data gap" and NEVER restart/deploy/promote', () => {
    const gaps = detectResourceDataGaps([
      point('2026-11-03T10:00:00'),
      point('2026-11-03T10:05:00', { threads: 0 }),
      point('2026-11-03T10:10:00'),
    ]);
    expect(gaps.length).toBe(1);
    const text = `${DATA_GAP_LABEL} · ${gaps[0].text}`;
    expect(/data gap/i.test(text)).toBe(true);
    expect(/restart/i.test(text)).toBe(false);
    expect(/deploy/i.test(text)).toBe(false);
    expect(/promote/i.test(text)).toBe(false);
    expect(text).toContain('Threads'); // the chip names the series
    expect(text).toContain('UTC');
  });

  it('several series can gap in the SAME interval — one annotation per series', () => {
    const gaps = detectResourceDataGaps([
      point('2026-11-03T10:00:00'),
      point('2026-11-03T10:05:00', { threads: 0, instances_active: 0 }),
      point('2026-11-03T10:10:00', { threads: 42, instances_active: 5 }),
    ]);
    expect(seriesOf(gaps)).toEqual(['threads', 'instances_active']);
  });

  it('a TRAILING unavailable stretch never regrows and is NOT annotated', () => {
    const gaps = detectResourceDataGaps([
      point('2026-11-03T10:00:00'),
      point('2026-11-03T10:05:00', { daemon_cpu: 0 }),
      point('2026-11-03T10:10:00', { daemon_cpu: 0 }),
    ]);
    expect(gaps).toEqual([]);
  });

  it('a LEADING unavailable stretch is the window edge, not a gap', () => {
    const gaps = detectResourceDataGaps([
      point('2026-11-03T10:00:00', { daemon_rss_mb: null }),
      point('2026-11-03T10:05:00', { daemon_rss_mb: 1750 }),
    ]);
    expect(gaps).toEqual([]);
  });

  it('an uninterrupted payload yields NO gaps', () => {
    expect(detectResourceDataGaps([point('2026-11-03T10:00:00'), point('2026-11-03T10:05:00')])).toEqual([]);
  });

  it('the fixed label never mentions a restart (the planted-wording mutation leg)', () => {
    // The label constant is the SSOT of the wording. The executed
    // mutation: plant `restart` into DATA_GAP_LABEL → this spec (and the
    // panel spec) go RED → restore → GREEN. Recorded in phase5-evidence.md.
    expect(/restart|deploy|promote/i.test(DATA_GAP_LABEL)).toBe(false);
    expect(DATA_GAP_LABEL).toBe('DATA GAP — series unavailable in this interval');
  });
});
