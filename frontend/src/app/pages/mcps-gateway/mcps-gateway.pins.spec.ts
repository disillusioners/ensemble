// MCP Gateway — the route + gear-menu pins (Phase C).
//
// Source-grep pins (the house pattern — the precedent is
// `pages/resources/resources-gear-menu.spec.ts`):
//
// * the page is reachable ONLY through the gear menu (there is NO
//   top-level header link — the header nav is for the primary
//   destinations, the gear menu is where the operator pages live);
// * the route is LAZY (`loadComponent`) and titled;
// * the gear-menu entry is seeded AFTER Resources and BEFORE the
//   conditional Database/Maintenance appends, so those appends still
//   land at the menu tail (the `resources-gear-menu.spec.ts` ordering
//   pin must stay green);
// * the service is GET-only — no write verb anywhere in it.
//
// CONVENTION: positive and negative needles NEVER share a test function
// (the `test_docs_cleanup_truth_split.py` precedent).

import { readFileSync } from 'fs';
import { join } from 'path';

const appDir = join(__dirname, '..', '..');
const appTs = readFileSync(join(appDir, 'app.ts'), 'utf-8');
const appHtml = readFileSync(join(appDir, 'app.html'), 'utf-8');
const routesSrc = readFileSync(join(appDir, 'app.routes.ts'), 'utf-8');
const serviceSrc = readFileSync(join(__dirname, 'mcps-gateway.service.ts'), 'utf-8');

describe('MCP Gateway — lazy route', () => {
  it('declares a lazy /mcps-gateway route via loadComponent', () => {
    const routeMatch = routesSrc.match(
      /\{\s*path:\s*'mcps-gateway',\s*loadComponent:\s*\(\)\s*=>\s*import\(['"]\.\/pages\/mcps-gateway\/mcps-gateway\.component['"]\)\.then\(m\s*=>\s*m\.McpsGatewayComponent\)/,
    );
    expect(routeMatch).not.toBeNull();
  });

  it('the /mcps-gateway route is not eagerly declared (`component:` absent on that route)', () => {
    const routeIdx = routesSrc.indexOf("path: 'mcps-gateway'");
    expect(routeIdx).toBeGreaterThan(-1);
    const routeSlice = routesSrc.slice(routeIdx, routeIdx + 260);
    expect(routeSlice).toContain('loadComponent');
    expect(routeSlice).not.toMatch(/\bcomponent:\s/);
  });

  it('titles the route "MCP Gateway"', () => {
    const routeIdx = routesSrc.indexOf("path: 'mcps-gateway'");
    const routeSlice = routesSrc.slice(routeIdx, routeIdx + 260);
    expect(routeSlice).toContain("title: 'MCP Gateway'");
  });

  it('the /mcps-gateway route is declared BEFORE the catch-all wildcard', () => {
    const routeIdx = routesSrc.indexOf("path: 'mcps-gateway'");
    const wildcardIdx = routesSrc.indexOf("path: '**'");
    expect(routeIdx).toBeGreaterThan(-1);
    expect(wildcardIdx).toBeGreaterThan(-1);
    expect(routeIdx).toBeLessThan(wildcardIdx);
  });
});

describe('MCP Gateway — gear menu entry', () => {
  it('seeds the MCP Gateway item in settingsMenuItems with the frozen shape', () => {
    const itemMatch = appTs.match(
      /\{\s*label:\s*'MCP Gateway',\s*icon:\s*'computer',\s*route:\s*'\/mcps-gateway'\s*\}/,
    );
    expect(itemMatch).not.toBeNull();
  });

  it('places MCP Gateway AFTER Resources and BEFORE the conditional appends', () => {
    const resourcesIdx = appTs.indexOf("{ label: 'Resources'");
    const gatewayIdx = appTs.indexOf("{ label: 'MCP Gateway'");
    const dbUpdateIdx = appTs.indexOf('settingsMenuItems.update(items => [');

    expect(resourcesIdx).toBeGreaterThan(-1);
    expect(gatewayIdx).toBeGreaterThan(-1);
    expect(dbUpdateIdx).toBeGreaterThan(-1);

    // Resources → MCP Gateway → (conditional Database/Maintenance appends).
    expect(resourcesIdx).toBeLessThan(gatewayIdx);
    expect(gatewayIdx).toBeLessThan(dbUpdateIdx);
  });

  it('does NOT add a top-level header link for /mcps-gateway (the gear menu is the only entry point)', () => {
    // The gear menu renders item.route dynamically; a hard-coded
    // `'/mcps-gateway'` literal in app.html would be a top-level header
    // link.
    expect(appHtml.includes("'/mcps-gateway'")).toBe(false);
    expect(appHtml.includes('"/mcps-gateway"')).toBe(false);
  });
});

describe('MCP Gateway — the read surface is GET-only', () => {
  it('the service issues no write verb', () => {
    expect(serviceSrc).not.toMatch(/\.(post|put|delete|patch)\(/);
  });

  it('the service talks to the three read endpoints', () => {
    expect(serviceSrc).toContain('/api/mcps-gateway/machines');
    expect(serviceSrc).toContain('/api/mcps-gateway/sessions');
    expect(serviceSrc).toContain('/api/mcps-gateway/audit');
  });
});
