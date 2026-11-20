// Poll-visibility bindings pins — jobs-badge-loop-hog M3 (slot 4).
//
// Source-grep pins (house pattern: readFileSync + __dirname). Two
// pin families per surface, for the six ungated always-on pollers:
//
//   (a) INTERVAL pin — always-on, passes at base. Reddens if ANY
//       visible-tab interval changes (Emperor contract: 8s badge
//       cadence stays; fix is BACKEND COST, not cadence).
//   (b) GATE pin   — asserts each file carries the visibility-gate
//       markers (visibilitychange|document.hidden + a refocus-refresh
//       marker). The gates land via the parallel M2 module that merges
//       BEFORE this one; at this module's base these gate pins are
//       EXPECTED-RED.
//
// RED at base by design; green post-M2 merge; verified by the
// integrator on the merged tree.

import { readFileSync } from 'fs';
import { join } from 'path';

const dir = __dirname;

const badgeSrc = readFileSync(join(dir, 'components', 'job-queue-indicator', 'job-queue-indicator.component.ts'), 'utf-8');
const homeSrc = readFileSync(join(dir, 'pages', 'home', 'home.component.ts'), 'utf-8');
const liveSrc = readFileSync(join(dir, 'pages', 'metrics', 'resource', 'live', 'live-tab.component.ts'), 'utf-8');
const instanceServiceSrc = readFileSync(join(dir, 'services', 'instance.service.ts'), 'utf-8');
const mcpsGatewaySrc = readFileSync(join(dir, 'pages', 'mcps-gateway', 'mcps-gateway.component.ts'), 'utf-8');
const mcpsGatewayServiceSrc = readFileSync(join(dir, 'pages', 'mcps-gateway', 'mcps-gateway.service.ts'), 'utf-8');
const commandStateSrc = readFileSync(join(dir, 'services', 'command-state.service.ts'), 'utf-8');

describe('poll-visibility bindings — INTERVAL pins (Emperor: cadence unchanged)', () => {
  it('badge: POLL_INTERVAL_MS = 8000', () => {
    expect(badgeSrc).toMatch(/POLL_INTERVAL_MS\s*=\s*8000/);
  });

  it('home: literal 120000', () => {
    expect(homeSrc).toContain('120000');
  });

  it('live: POLL_INTERVAL_MS = 8000', () => {
    expect(liveSrc).toMatch(/POLL_INTERVAL_MS\s*=\s*8000/);
  });

  it('instance.service: 60000', () => {
    expect(instanceServiceSrc).toMatch(/POLLING_INTERVAL\s*=\s*60_?000/);
  });

  it('mcps-gateway: 10000 (via service)', () => {
    expect(mcpsGatewayServiceSrc).toMatch(/POLL_INTERVAL_MS\s*=\s*10_?000/);
  });

  it('command-state: POLL_INTERVAL_MS = 5000', () => {
    expect(commandStateSrc).toMatch(/POLL_INTERVAL_MS\s*=\s*5000/);
  });
});

// RED at base by design; green post-M2 merge; verified by the integrator
// on the merged tree.
describe('poll-visibility bindings — GATE pins (visibility + refocus-refresh marker)', () => {
  const surfaces: { name: string; src: string }[] = [
    { name: 'job-queue-indicator.component.ts', src: badgeSrc },
    { name: 'home.component.ts', src: homeSrc },
    { name: 'live-tab.component.ts', src: liveSrc },
    { name: 'instance.service.ts', src: instanceServiceSrc },
    { name: 'mcps-gateway.component.ts', src: mcpsGatewaySrc },
    { name: 'command-state.service.ts', src: commandStateSrc },
  ];

  for (const s of surfaces) {
    it(`${s.name} contains a visibility-gate marker (visibilitychange|document.hidden)`, () => {
      expect(s.src).toMatch(/visibilitychange|document\.hidden/);
    });

    it(`${s.name} contains a refocus-refresh marker`, () => {
      expect(s.src).toMatch(/refocus|visibilityState|onVisibility/i);
    });
  }
});
