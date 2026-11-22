// Report — the route + gear-menu pins (Phase 4, D-E / BR-18 / BR-19).
//
// Source-grep pins (house pattern — mirrors
// `pages/mcps-gateway/mcps-gateway.pins.spec.ts`):
//
// * the page is reachable ONLY through the gear menu (there is NO
//   top-level header link — the header nav is for the primary
//   destinations, the gear menu is where the operator pages live);
// * the route is LAZY (`loadComponent`) and titled;
// * the gear-menu entry is seeded AFTER Metrics and BEFORE the
//   conditional Database/Maintenance appends, so those appends still
//   land at the menu tail;
// * the service is GET-only — no write verb anywhere in it.

import { readFileSync } from 'fs';
import { join } from 'path';

const appDir = join(__dirname, '..', '..');
const appTs = readFileSync(join(appDir, 'app.ts'), 'utf-8');
const appHtml = readFileSync(join(appDir, 'app.html'), 'utf-8');
const routesSrc = readFileSync(join(appDir, 'app.routes.ts'), 'utf-8');
const serviceSrc = readFileSync(join(__dirname, 'report.service.ts'), 'utf-8');

describe('Report — lazy route', () => {
  it('declares a lazy /report route via loadComponent', () => {
    const routeMatch = routesSrc.match(
      /\{\s*path:\s*'report',\s*loadComponent:\s*\(\)\s*=>\s*import\(['"]\.\/pages\/report\/report\.component['"]\)\.then\(m\s*=>\s*m\.ReportComponent\)/,
    );
    expect(routeMatch).not.toBeNull();
  });

  it('the /report route is not eagerly declared (`component:` absent on that route)', () => {
    const routeIdx = routesSrc.indexOf("path: 'report'");
    expect(routeIdx).toBeGreaterThan(-1);
    const routeSlice = routesSrc.slice(routeIdx, routeIdx + 260);
    expect(routeSlice).toContain('loadComponent');
    expect(routeSlice).not.toMatch(/\bcomponent:\s/);
  });

  it('titles the route "Report"', () => {
    const routeIdx = routesSrc.indexOf("path: 'report'");
    const routeSlice = routesSrc.slice(routeIdx, routeIdx + 260);
    expect(routeSlice).toContain("title: 'Report'");
  });

  it('the /report route is declared BEFORE the catch-all wildcard', () => {
    const routeIdx = routesSrc.indexOf("path: 'report'");
    const wildcardIdx = routesSrc.indexOf("path: '**'");
    expect(routeIdx).toBeGreaterThan(-1);
    expect(wildcardIdx).toBeGreaterThan(-1);
    expect(routeIdx).toBeLessThan(wildcardIdx);
  });
});

describe('Report — gear menu entry', () => {
  it('seeds the Report item in settingsMenuItems with the frozen shape', () => {
    const itemMatch = appTs.match(
      /\{\s*label:\s*'Report',\s*icon:\s*'summarize',\s*route:\s*'\/report'\s*\}/,
    );
    expect(itemMatch).not.toBeNull();
  });

  it('places Report AFTER Metrics and BEFORE the conditional appends', () => {
    const metricsIdx = appTs.indexOf("{ label: 'Metrics'");
    const reportIdx = appTs.indexOf("{ label: 'Report'");
    const dbUpdateIdx = appTs.indexOf('settingsMenuItems.update(items => [');

    expect(metricsIdx).toBeGreaterThan(-1);
    expect(reportIdx).toBeGreaterThan(-1);
    expect(dbUpdateIdx).toBeGreaterThan(-1);

    expect(metricsIdx).toBeLessThan(reportIdx);
    expect(reportIdx).toBeLessThan(dbUpdateIdx);
  });

  it('does NOT add a top-level header link for /report', () => {
    expect(appHtml.includes("'/report'")).toBe(false);
    expect(appHtml.includes('"/report"')).toBe(false);
  });
});

describe('Report — the read surface is GET-only', () => {
  it('the service issues no write verb', () => {
    expect(serviceSrc).not.toMatch(/\.(post|put|delete|patch)\(/);
  });

  it('the service talks to the maintenance-reports endpoint', () => {
    expect(serviceSrc).toContain('/api/maintenance-reports');
  });
});
