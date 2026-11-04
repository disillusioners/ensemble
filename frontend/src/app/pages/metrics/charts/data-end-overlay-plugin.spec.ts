// The HOUSE DATA-END OVERLAY PLUGIN's spec (T1 phase 8, task 8.10).
//
// The plugin is a HOUSE file — NOT a vendor package. The load-bearing
// pins:
//
//   * it draws a VERTICAL LINE at the data end's pixel, from the top of
//     the chart area to the bottom — the marker says WHERE THE DATA
//     STOPS;
//   * it draws a VISIBLE LABEL beside the line — a bare line does not
//     tell the reader what it is;
//   * it draws NOTHING when there is no data end, and nothing when the
//     scale cannot resolve the timestamp (a marker at a bogus position
//     is worse than no marker);
//   * THE LOCKFILE GAINS NO SECOND VENDOR PACKAGE — the overlay is the
//     house's own code.

import {
  HOUSE_DATA_END_COLOR,
  HOUSE_DATA_END_LABEL_FONT,
  HOUSE_DATA_END_PLUGIN_ID,
  HouseDataEnd,
  createHouseDataEndOverlayPlugin,
} from './data-end-overlay-plugin';

const T0 = Date.UTC(2026, 10, 2, 9, 0, 0);

/** A fake chart that RECORDS the drawing calls. */
function fakeChart(pixelFor: (value: number) => number = () => 120) {
  const lines: { from: { x: number; y: number }; to: { x: number; y: number } }[] = [];
  const texts: { text: string; x: number; y: number }[] = [];
  const ctx = {
    fillStyle: '',
    strokeStyle: '',
    font: '',
    textAlign: '',
    save: () => undefined,
    restore: () => undefined,
    beginPath: () => undefined,
    moveTo: (x: number, y: number) => {
      lines.push({ from: { x, y }, to: { x, y } });
    },
    lineTo: (x: number, y: number) => {
      const line = lines[lines.length - 1];
      if (line) {
        line.to = { x, y };
      }
    },
    stroke: () => undefined,
    setLineDash: () => undefined,
    fillText: (text: string, x: number, y: number) => {
      texts.push({ text, x, y });
    },
  };
  const chart = {
    ctx,
    chartArea: { top: 10, left: 20, right: 400, bottom: 300 },
    scales: { x: { getPixelForValue: pixelFor } },
  };
  return { chart, lines, texts };
}

describe('the house data-end overlay plugin (T1 phase 8, task 8.10)', () => {
  it('draws a VERTICAL line at the data end, across the chart area', () => {
    const { chart, lines } = fakeChart(() => 256);
    createHouseDataEndOverlayPlugin().afterDatasetsDraw(chart, {}, {
      dataEnd: { ms: T0, label: 'data ends 09:00Z' },
    });
    expect(lines).toHaveLength(1);
    expect(lines[0].from.x).toBe(256);
    expect(lines[0].to.x).toBe(256);
    // The line spans the chart area — top to bottom.
    expect(lines[0].from.y).toBe(chart.chartArea.top);
    expect(lines[0].to.y).toBe(chart.chartArea.bottom);
  });

  it('draws a VISIBLE label beside the line (a bare line says nothing)', () => {
    const { chart, texts } = fakeChart(() => 256);
    createHouseDataEndOverlayPlugin().afterDatasetsDraw(chart, {}, {
      dataEnd: { ms: T0, label: 'data ends 09:00Z' },
    });
    expect(texts).toHaveLength(1);
    expect(texts[0].text).toContain('data ends');
  });

  it('draws NOTHING when there is no data end', () => {
    const { chart, lines, texts } = fakeChart();
    createHouseDataEndOverlayPlugin().afterDatasetsDraw(chart, {}, { dataEnd: null });
    expect(lines).toHaveLength(0);
    expect(texts).toHaveLength(0);
  });

  it('draws NOTHING when the scale cannot resolve the timestamp (never a bogus marker)', () => {
    const { chart, lines } = fakeChart(() => Number.NaN);
    createHouseDataEndOverlayPlugin().afterDatasetsDraw(chart, {}, {
      dataEnd: { ms: T0, label: 'data ends 09:00Z' },
    });
    expect(lines).toHaveLength(0);
  });

  it('carries the house id, colour and label font', () => {
    const plugin = createHouseDataEndOverlayPlugin();
    expect(plugin.id).toBe(HOUSE_DATA_END_PLUGIN_ID);
    expect(HOUSE_DATA_END_COLOR).toMatch(/^#[0-9a-f]{6}$/i);
    expect(HOUSE_DATA_END_LABEL_FONT).toContain('px');
  });

  it('the data end is a structural type — the marker never carries a bare number', () => {
    const end: HouseDataEnd = { ms: T0, label: 'data ends 09:00Z' };
    expect(end.ms).toBe(T0);
    expect(typeof end.label).toBe('string');
  });
});
