// Resources gear-menu + route spec — Phase 5, T6.
//
// Source-grep pins (house pattern, mirrors the Snapshots menu pin in
// `app.component.spec.ts`): the Resources entry is appended to the
// `settingsMenuItems` SEED array AFTER Snapshots so the conditional
// Database/Maintenance appends still land at the menu tail; the
// `/resources` route is LAZY (`loadComponent`) and there is NO
// top-level header link (D2 — the gear menu is the only entry point).

import { readFileSync } from 'fs';
import { join } from 'path';

const appDir = join(__dirname, '..', '..');
const appTs = readFileSync(join(appDir, 'app.ts'), 'utf-8');
const appHtml = readFileSync(join(appDir, 'app.html'), 'utf-8');
const routesSrc = readFileSync(join(appDir, 'app.routes.ts'), 'utf-8');

describe('Resources — gear menu entry', () => {
  it('seeds the Resources item in settingsMenuItems with the frozen shape', () => {
    const itemMatch = appTs.match(
      /\{\s*label:\s*'Resources',\s*icon:\s*'insights',\s*route:\s*'\/resources'\s*\}/,
    );
    expect(itemMatch).not.toBeNull();
  });

  it('places Resources AFTER Snapshots and BEFORE the conditional appends', () => {
    const settingsIdx = appTs.indexOf("{ label: 'Settings'");
    const snapshotsIdx = appTs.indexOf("{ label: 'Snapshots'");
    const resourcesIdx = appTs.indexOf("{ label: 'Resources'");
    const dbUpdateIdx = appTs.indexOf('settingsMenuItems.update(items => [');

    expect(settingsIdx).toBeGreaterThan(-1);
    expect(snapshotsIdx).toBeGreaterThan(-1);
    expect(resourcesIdx).toBeGreaterThan(-1);
    expect(dbUpdateIdx).toBeGreaterThan(-1);

    // Settings → Snapshots → Resources → (conditional appends).
    expect(settingsIdx).toBeLessThan(snapshotsIdx);
    expect(snapshotsIdx).toBeLessThan(resourcesIdx);
    expect(resourcesIdx).toBeLessThan(dbUpdateIdx);
  });

  it('does NOT add a top-level header link for /resources (D2 — gear menu only)', () => {
    // The gear menu renders item.route dynamically; a hard-coded
    // `'/resources'` literal in app.html would be a top-level header
    // link (rejected alternative in D2).
    expect(appHtml.includes("'/resources'")).toBe(false);
    expect(appHtml.includes('"/resources"')).toBe(false);
  });
});

describe('Resources — lazy route', () => {
  it('declares a lazy /resources route via loadComponent', () => {
    const routeMatch = routesSrc.match(
      /\{\s*path:\s*'resources',\s*loadComponent:\s*\(\)\s*=>\s*import\(['"]\.\/pages\/resources\/resources\.component['"]\)\.then\(m\s*=>\s*m\.ResourcesComponent\)/,
    );
    expect(routeMatch).not.toBeNull();
  });

  it('the /resources route is not eagerly declared (`component:` absent on that route)', () => {
    const resourcesRouteIdx = routesSrc.indexOf("path: 'resources'");
    expect(resourcesRouteIdx).toBeGreaterThan(-1);
    // Slice to the end of the route object literal.
    const routeSlice = routesSrc.slice(resourcesRouteIdx, resourcesRouteIdx + 260);
    expect(routeSlice).toContain('loadComponent');
    expect(routeSlice).not.toMatch(/\bcomponent:\s/);
  });

  it('titles the route "Resources"', () => {
    const resourcesRouteIdx = routesSrc.indexOf("path: 'resources'");
    const routeSlice = routesSrc.slice(resourcesRouteIdx, resourcesRouteIdx + 260);
    expect(routeSlice).toContain("title: 'Resources'");
  });
});
