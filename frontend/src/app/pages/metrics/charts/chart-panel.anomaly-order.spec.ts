// The Chart.js panel's ANOMALY-ARRIVAL ORDER contract (R1, 2026-11-04).
//
// THE DEFECT THIS PINS. The wrapper's rebuild effect refreshed the
// chart's DATA (`chart.data = config.data`) but never its OPTIONS. The
// house overlay's MARKER LIST lives in the OPTIONS bag
// (`options.plugins[HOUSE_ANOMALY_PLUGIN_ID].markers`), and the vendor
// resolves a plugin's options from the config the chart was CONSTRUCTED
// with — so the marker list was FROZEN at mount time.
//
// The symptom: when the chart mounts BEFORE `/api/resources/anomalies`
// resolves, the markers are silently absent — the panel renders bare
// and the chart→drill-down pointer path is dead (the first gate's exact
// symptom shape under a different arrival order). The fix refreshes the
// options bag on every rebuild, so the marker list follows the payload
// WHENEVER it arrives.
//
// A NOTE ON THE SEAMS (the vendor's option resolver): `chart.options` is
// a PROXY over the option scopes, and every NESTED object it hands back
// is a sub-resolver proxy too (`chart.options.plugins[id].markers[0]` is
// a proxy, not the plain object). The marker OBJECTS are therefore read
// field-by-field through the resolved proxy, and the deep-equality
// assertions run against the RAW bag (`chart.config.options`), which is
// what the vendor's plugin resolver itself reads.
//
// The legs:
//   1. the ORDER — the chart mounts first, the anomalies arrive after ⇒
//      the marker list reaches the chart's plugin options (RED before the
//      fold: the list stayed empty);
//   2. the NORMAL path — markers present at mount are still present after
//      a rebuild (the fix must not regress the mount-time case);
//   3. the DRAW — the chart's RESOLVED plugin options reach the house
//      plugin's draw path, so the late marker is actually painted.

import { TestBed } from '@angular/core/testing';

import {
  HOUSE_ANOMALY_PLUGIN_ID,
  createHouseAnomalyOverlayPlugin,
} from './anomaly-overlay-plugin';
import { ChartPanelComponent, ChartPanelSeries } from './chart-panel.component';

const T0 = Date.UTC(2026, 10, 2, 9, 0, 0);
const T1 = Date.UTC(2026, 10, 2, 10, 0, 0);

const SERIES: readonly ChartPanelSeries[] = [
  {
    id: 'daemon_rss_mb',
    label: 'Daemon RSS (MB)',
    mode: 'line',
    points: [
      { t: T0, v: 300 },
      { t: T1, v: 345 },
    ],
  },
];

const LATE_MARKER = {
  anomalyId: 'anom-000003',
  seriesId: 'daemon_rss_mb',
  tsMs: T0,
  severity: 'high' as const,
  title: 'memory · ramp · high · daemon.rss_kb',
};

/** The mounted chart's surface — the private handle the component holds. */
interface MountedChart {
  /** The vendor's option RESOLVER (a proxy over the option scopes). */
  options?: {
    plugins?: Record<string, { markers?: { length: number; [index: number]: MarkerFields } }>;
  };
  /** The vendor's config — the RAW option bag the plugin resolver reads. */
  config?: {
    options?: {
      plugins?: Record<string, { markers?: readonly MarkerFields[] }>;
    };
  };
  update?: () => void;
  /** The vendor's scales (the marker's pixel position). */
  scales?: { x?: { getPixelForValue?: (value: number) => number } };
  /** The vendor's explicit resize (jsdom has no layout). */
  resize?: (width: number, height: number) => void;
  /** The vendor's synchronous draw (the plugin's draw path). */
  draw?: () => void;
}

/** The marker's fields (the shape the wire and the overlay travel on). */
interface MarkerFields {
  anomalyId: string;
  seriesId: string;
  tsMs: number;
  severity: string;
  title: string;
}

/**
 * The mount is ASYNC (the vendor arrives through a dynamic import), so a
 * test must let the microtask/macrotask queue drain before it can read
 * the mounted chart.
 */
async function settle(): Promise<void> {
  for (let index = 0; index < 5; index += 1) {
    await new Promise((resolve) => setTimeout(resolve, 0));
  }
}

async function mountPanel(
  anomalies: readonly MarkerFields[] = [],
): Promise<{
  fixture: ReturnType<typeof TestBed.createComponent<ChartPanelComponent>>;
  chart: MountedChart;
}> {
  await TestBed.configureTestingModule({
    imports: [ChartPanelComponent],
  }).compileComponents();
  const fixture = TestBed.createComponent(ChartPanelComponent);
  fixture.componentRef.setInput('series', SERIES);
  fixture.componentRef.setInput('unit', 'MB');
  if (anomalies.length > 0) {
    fixture.componentRef.setInput('anomalies', anomalies);
  }
  fixture.detectChanges();
  await fixture.whenStable();
  await settle();
  fixture.detectChanges();
  const chart = (fixture.componentInstance as unknown as { chart: MountedChart }).chart;
  return { fixture, chart };
}

/** The chart's RESOLVED marker list (the vendor's sub-resolver proxy). */
function resolvedMarkers(chart: MountedChart): { length: number; [index: number]: MarkerFields } {
  return (
    chart.options?.plugins?.[HOUSE_ANOMALY_PLUGIN_ID]?.markers ?? { length: 0 }
  );
}

/** The chart's RAW marker list (the bag the plugin resolver reads). */
function rawMarkers(chart: MountedChart): readonly MarkerFields[] {
  return chart.config?.options?.plugins?.[HOUSE_ANOMALY_PLUGIN_ID]?.markers ?? [];
}

describe('chart-panel — the anomaly ARRIVAL ORDER (R1)', () => {
  it('delivers the markers when the anomalies arrive AFTER the chart mounted', async () => {
    const { fixture, chart } = await mountPanel();
    expect(chart).toBeTruthy();
    // At mount there were no anomalies ⇒ no markers.
    expect(resolvedMarkers(chart).length).toBe(0);

    // THE ORDER: the payload arrives now, after the mount.
    fixture.componentRef.setInput('anomalies', [LATE_MARKER]);
    fixture.detectChanges();
    await settle();
    fixture.detectChanges();

    // The marker list must have FOLLOWED the payload. Before the fold the
    // rebuild assigned only `chart.data`, so the list stayed frozen at
    // the mount-time snapshot ([]) and the marker was never drawn.
    expect(resolvedMarkers(chart).length).toBe(1);
    expect(resolvedMarkers(chart)[0].anomalyId).toBe('anom-000003');
    expect(resolvedMarkers(chart)[0].tsMs).toBe(LATE_MARKER.tsMs);
    expect(resolvedMarkers(chart)[0].seriesId).toBe('daemon_rss_mb');
    expect(rawMarkers(chart)).toEqual([LATE_MARKER]);

    fixture.destroy();
  });

  it('keeps the mount-time markers across a rebuild (the normal path)', async () => {
    const { fixture, chart } = await mountPanel([LATE_MARKER]);
    expect(chart).toBeTruthy();
    expect(resolvedMarkers(chart).length).toBe(1);
    expect(rawMarkers(chart)).toEqual([LATE_MARKER]);

    // A rebuild (new data) must not drop the markers.
    fixture.componentRef.setInput('series', [
      {
        ...SERIES[0],
        points: [
          { t: T0, v: 300 },
          { t: T1, v: 346 },
        ],
      },
    ]);
    fixture.detectChanges();
    await settle();
    fixture.detectChanges();

    expect(resolvedMarkers(chart).length).toBe(1);
    expect(rawMarkers(chart)).toEqual([LATE_MARKER]);
    fixture.destroy();
  });

  it("draws the late marker — the VENDOR's own draw path paints it", async () => {
    const { fixture, chart } = await mountPanel();
    const canvas = fixture.nativeElement.querySelector(
      'canvas',
    ) as HTMLCanvasElement;
    const ctx = canvas.getContext('2d') as unknown as {
      __getEvents?: () => { type: string; props?: Record<string, unknown> }[];
      __clearEvents?: () => void;
    };
    const arcsNow = (): { type: string; props?: Record<string, unknown> }[] =>
      (ctx.__getEvents?.() ?? []).filter((event) => event.type === 'arc');

    // jsdom has no layout, so the chart's canvas is 0×0 and the vendor's
    // draw() returns early. An explicit resize gives the chart a real
    // chart area, and the draw then runs the house overlay's
    // `beforeDatasetsDraw` — the REAL plugin path, with the plugin's own
    // option resolver in play.
    chart.resize?.(800, 300);
    chart.draw?.();
    // BEFORE the payload: nothing is painted (no markers exist).
    expect(arcsNow()).toHaveLength(0);

    // The payload arrives AFTER the mount.
    fixture.componentRef.setInput('anomalies', [LATE_MARKER]);
    fixture.detectChanges();
    await settle();
    fixture.detectChanges();
    ctx.__clearEvents?.();
    chart.draw?.();

    const arcs = arcsNow();
    expect(arcs).toHaveLength(1);
    // The disc sits at the marker's own pixel (the vendor's scale).
    expect((arcs[0].props as { x?: number }).x).toBe(
      chart.scales?.x?.getPixelForValue?.(LATE_MARKER.tsMs),
    );

    fixture.destroy();
  });
});
