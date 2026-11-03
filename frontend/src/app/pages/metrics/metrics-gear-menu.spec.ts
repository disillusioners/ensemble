// The METRICS route + gear-menu pins (P5 task 5.6/5.8 · D1 pack
// `metrics-fe-page`).
//
// House pattern (mirrors `pages/resources/resources-gear-menu.spec.ts`
// and `pages/mcps-gateway/mcps-gateway.pins.spec.ts`): source-grep
// pins over the production sources.
//
// The load-bearing pin is P5-M7: the gear-menu entry's EXPECTED
// POSITION. The Metrics entry is appended AFTER MCP Gateway and BEFORE
// the conditional Database/Maintenance appends — those appends must
// stay at the menu tail (the resources ordering pin and the gateway
// ordering pin both depend on it).

import { readFileSync } from 'fs';
import { join } from 'path';

const appDir = join(__dirname, '..', '..');
const appTs = readFileSync(join(appDir, 'app.ts'), 'utf-8');
const appHtml = readFileSync(join(appDir, 'app.html'), 'utf-8');
const routesSrc = readFileSync(join(appDir, 'app.routes.ts'), 'utf-8');

describe('Metrics — the gear menu entry', () => {
  it('seeds the Metrics item in settingsMenuItems with the frozen shape', () => {
    const itemMatch = appTs.match(
      /\{\s*label:\s*'Metrics',\s*icon:\s*'query_stats',\s*route:\s*'\/metrics'\s*\}/,
    );
    expect(itemMatch).not.toBeNull();
  });

  it('places Metrics AFTER MCP Gateway and BEFORE the conditional appends (P5-M7)', () => {
    const resourcesIdx = appTs.indexOf("{ label: 'Resources'");
    const gatewayIdx = appTs.indexOf("{ label: 'MCP Gateway'");
    const metricsIdx = appTs.indexOf("{ label: 'Metrics'");
    const dbUpdateIdx = appTs.indexOf('settingsMenuItems.update(items => [');

    expect(resourcesIdx).toBeGreaterThan(-1);
    expect(gatewayIdx).toBeGreaterThan(-1);
    expect(metricsIdx).toBeGreaterThan(-1);
    expect(dbUpdateIdx).toBeGreaterThan(-1);

    // Resources → MCP Gateway → Metrics → (conditional appends).
    expect(resourcesIdx).toBeLessThan(gatewayIdx);
    expect(gatewayIdx).toBeLessThan(metricsIdx);
    expect(metricsIdx).toBeLessThan(dbUpdateIdx);
  });

  it('keeps the Resources ordering pin intact (Resources stays before the appends)', () => {
    const resourcesIdx = appTs.indexOf("{ label: 'Resources'");
    const dbUpdateIdx = appTs.indexOf('settingsMenuItems.update(items => [');
    expect(resourcesIdx).toBeGreaterThan(-1);
    expect(dbUpdateIdx).toBeGreaterThan(-1);
    expect(resourcesIdx).toBeLessThan(dbUpdateIdx);
  });

  it('does NOT add a top-level header link for /metrics', () => {
    expect(appHtml.includes("'/metrics'")).toBe(false);
    expect(appHtml.includes('"/metrics"')).toBe(false);
  });
});

describe('Metrics — the lazy route', () => {
  it('declares a lazy /metrics route via loadComponent', () => {
    const routeMatch = routesSrc.match(
      /\{\s*path:\s*'metrics',\s*loadComponent:\s*\(\)\s*=>\s*import\(['"]\.\/pages\/metrics\/metrics\.component['"]\)\.then\(m\s*=>\s*m\.MetricsComponent\)/,
    );
    expect(routeMatch).not.toBeNull();
  });

  it('the /metrics route is not eagerly declared (`component:` absent on that route)', () => {
    const routeIdx = routesSrc.indexOf("path: 'metrics'");
    expect(routeIdx).toBeGreaterThan(-1);
    const routeSlice = routesSrc.slice(routeIdx, routeIdx + 260);
    expect(routeSlice).toContain('loadComponent');
    expect(routeSlice).not.toMatch(/\bcomponent:\s/);
  });

  it('titles the route "Metrics"', () => {
    const routeIdx = routesSrc.indexOf("path: 'metrics'");
    const routeSlice = routesSrc.slice(routeIdx, routeIdx + 260);
    expect(routeSlice).toContain("title: 'Metrics'");
  });

  it('the /metrics route is declared BEFORE the catch-all wildcard', () => {
    const routeIdx = routesSrc.indexOf("path: 'metrics'");
    const wildcardIdx = routesSrc.indexOf("path: '**'");
    expect(routeIdx).toBeGreaterThan(-1);
    expect(wildcardIdx).toBeGreaterThan(-1);
    expect(routeIdx).toBeLessThan(wildcardIdx);
  });

  it('the /metrics route is declared BEFORE the :id route family (first-match-wins)', () => {
    const routeIdx = routesSrc.indexOf("path: 'metrics'");
    const idRouteIdx = routesSrc.indexOf("path: 'skills/:id'");
    expect(routeIdx).toBeGreaterThan(-1);
    expect(idRouteIdx).toBeGreaterThan(-1);
    expect(routeIdx).toBeLessThan(idRouteIdx);
  });
});
