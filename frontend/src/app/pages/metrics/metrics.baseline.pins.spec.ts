// The BASELINE PINS spec (T1 phase 3, tasks 3.7 / 3.9).
//
// Two load-bearing pins:
//
//   * the NO-FORGED-CURSOR negative pin (3.9) — the door's cursor is
//     unsigned base64 JSON, so a client COULD mint one to shift the
//     window. That is out of contract and brittle, so the FE must NEVER
//     construct one. The pin scans the metrics tree for cursor-minting
//     shapes and fails when it finds one; the leg is PLANTED here (a
//     violation in memory must turn the check RED — a check that cannot
//     fail is not a pin);
//   * the RENDERER CONTRACT declaration (3.7) — the baseline is a renderer
//     capability: the Chart.js panel is the NORMAL path, the house SVG
//     carries it as FALLBACK capability, and the mini role has no page
//     surface in T1. Phase 4 implements the drawing; this phase fixes the
//     contract.
//
// The scan's scope is the SHIPPED metrics tree (the spec files are
// excluded — they are the pin's own text, and they never ship).

import { readFileSync, readdirSync, statSync } from 'fs';
import { join } from 'path';

import { METRICS_BASELINE_CONTRACT } from './metrics-baseline';
import { ChartPanelComponent } from './charts/chart-panel.component';

const metricsDir = join(__dirname);
const servicesDir = join(__dirname, '..', '..', 'services');

/** Every shipped source file of the metrics tree + the metrics service. */
function shippedSources(): string[] {
  const files: string[] = [];
  const walk = (dir: string): void => {
    for (const name of readdirSync(dir)) {
      const path = join(dir, name);
      if (statSync(path).isDirectory()) {
        walk(path);
        continue;
      }
      if (!path.endsWith('.ts') || path.endsWith('.spec.ts')) {
        continue;
      }
      files.push(path);
    }
  };
  walk(metricsDir);
  files.push(join(servicesDir, 'metrics.service.ts'));
  return files;
}

/**
 * The cursor-minting needles.
 *
 * They are assembled from parts so this spec's own source does not
 * contain the literal shapes it hunts.
 */
const NEEDLES: readonly { readonly label: string; readonly pattern: RegExp }[] = [
  { label: 'base64 encoder', pattern: new RegExp(['\\b', 'bto', 'a\\s*\\('].join('')) },
  {
    label: 'cursor constructor',
    pattern: new RegExp(['\\b', '(en|de|forge|mint|build|make|make)', 'Cursor\\b'].join(''), 'i'),
  },
  {
    label: 'cursor request parameter',
    pattern: new RegExp(['\\.set\\(\\s*[\'"]', 'cursor', '[\'"]'].join('')),
  },
  {
    label: 'cursor query string',
    pattern: new RegExp(['[\'"]cursor=', '[^=]*=', '[\'"]'].join('')),
  },
];

/** The cursor-minting hits in one source — the pin's whole verdict. */
export function cursorForgeryHits(source: string): string[] {
  const hits: string[] = [];
  for (const needle of NEEDLES) {
    if (needle.pattern.test(source)) {
      hits.push(needle.label);
    }
  }
  return hits;
}

describe('the NO-FORGED-CURSOR negative pin (3.9)', () => {
  it('the metrics tree NEVER constructs a cursor', () => {
    const hits: string[] = [];
    for (const file of shippedSources()) {
      const found = cursorForgeryHits(readFileSync(file, 'utf-8'));
      if (found.length) {
        hits.push(`${file}: ${found.join(', ')}`);
      }
    }
    expect(hits).toEqual([]);
  });

  it('the pin is NOT VACUOUS — a planted violation turns it RED', () => {
    // The planted source is the shape a client would use to shift the
    // window: an unsigned base64 JSON cursor.
    const planted = [
      'const cursor = window.',
      'b',
      'toa(JSON.stringify({ t: 0, o: 0 }));',
      'this.metrics.getSeries({ cursor });',
    ].join('');
    expect(cursorForgeryHits(planted)).not.toEqual([]);
  });

  it('the pin is NOT VACUOUS — a planted cursor PARAMETER turns it RED', () => {
    const planted = "params = params.set('cursor', payload.next_cursor);";
    expect(cursorForgeryHits(planted)).not.toEqual([]);
  });

  it('the door\'s cursor is READ, never WRITTEN — the payload\'s cursor is not a request input', () => {
    // The FE may READ `next_cursor` (it is the door's continuation
    // token); what it must never do is SEND one.
    const reading = 'const next = payload.next_cursor;';
    expect(cursorForgeryHits(reading)).toEqual([]);
  });
});

describe('the RENDERER CONTRACT declaration (3.7)', () => {
  it('names the Chart.js panel as the NORMAL path', () => {
    expect(METRICS_BASELINE_CONTRACT.normalPath).toBe('chart.js-panel');
  });

  it('names the house SVG as the FALLBACK capability', () => {
    expect(METRICS_BASELINE_CONTRACT.fallbackPath).toBe('house-svg-baseline');
  });

  it('the mini role has NO page surface in T1', () => {
    expect(METRICS_BASELINE_CONTRACT.miniRolePageSurface).toBe(false);
  });

  it('the baseline NEVER adds a request of its own', () => {
    expect(METRICS_BASELINE_CONTRACT.addsRequest).toBe(false);
  });

  it('the split is anchored on the RESPONSE\'s window, never the client clock', () => {
    expect(METRICS_BASELINE_CONTRACT.splitAnchor).toBe('response-window');
  });

  it('the Chart.js panel DECLARES the baseline dataset input', () => {
    // The contract is declared on the panel itself — the renderer reads
    // the baseline as a second dataset over the current one.
    const source = readFileSync(
      join(metricsDir, 'charts', 'chart-panel.component.ts'),
      'utf-8',
    );
    expect(source).toContain('readonly baseline = input');
    expect(ChartPanelComponent).toBeTruthy();
  });
});
