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
// RED at base by design; green post-M2 merge; verified by the integrator
// on the merged tree.
//
// ── HARDENING (cleanup wave) — the GATE pins are COMMENT-PROOF ────────
//
// The review INFO found the 2nd (refocus-refresh) marker was
// COMMENT-SATISFIABLE: its old regex (`refocus|visibilityState|
// onVisibility`) matched PROSE, so a file whose gate code was deleted
// but whose DOC COMMENT still mentioned "refocus" stayed GREEN — the
// pin could not see the gate's removal.
//
// Two hardening layers, both proven by mutation (see the header of the
// harness commit):
//
//   1. The GATE pins match against the COMMENT-STRIPPED source, so a
//      prose mention can never satisfy them. House precedent:
//      `pages/metrics/metrics.bindings.pins.spec.ts` strips comments
//      before its import-shape match for exactly this reason.
//   2. The 2nd marker is CODE-SHAPED — not "the word appears somewhere"
//      but "an identifier mentioning `refocus` is ASSIGNED a
//      `setTimeout(...)`", i.e. the debounced refocus refresh itself.
//
// The INTERVAL pins keep matching the RAW source: the Emperor contract
// there is about the LITERAL value, and the raw text is what the FE
// compiles.

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

/**
 * Strip the comments so a marker can only be satisfied by CODE.
 *
 * House precedent: `pages/metrics/metrics.bindings.pins.spec.ts`
 * (`stripComments`) — the block-comment pass keeps the line count (the
 * line anchors stay meaningful); the line-comment pass leaves a `//`
 * inside a URL alone.
 */
function stripComments(source: string): string {
  return source
    .replace(/\/\*[\s\S]*?\*\//g, (block) => block.replace(/[^\n]/g, ' '))
    .replace(/(^|[^:'"`])\/\/[^\n]*/g, '$1');
}

/**
 * The CODE-SHAPED refocus-refresh marker.
 *
 * It demands the debounced refresh ITSELF — an identifier mentioning
 * `refocus` assigned a `setTimeout(...)` — so neither a doc comment nor
 * an unrelated `setTimeout` can satisfy it.
 */
const REFOCUS_REFRESH_CODE = /\brefocus\w*\s*\??=\s*(?:[\w$]+\.)*setTimeout\(/i;

/** The visibility-gate marker, matched against the CODE (comments stripped). */
const VISIBILITY_GATE_CODE = /visibilitychange|document\.hidden/;

/** Every pinned surface, with its raw source and its comment-stripped code. */
const surfaces: readonly { name: string; src: string; code: string }[] = [
  { name: 'job-queue-indicator.component.ts', src: badgeSrc, code: stripComments(badgeSrc) },
  { name: 'home.component.ts', src: homeSrc, code: stripComments(homeSrc) },
  { name: 'live-tab.component.ts', src: liveSrc, code: stripComments(liveSrc) },
  { name: 'instance.service.ts', src: instanceServiceSrc, code: stripComments(instanceServiceSrc) },
  { name: 'mcps-gateway.component.ts', src: mcpsGatewaySrc, code: stripComments(mcpsGatewaySrc) },
  { name: 'command-state.service.ts', src: commandStateSrc, code: stripComments(commandStateSrc) },
];

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
  for (const s of surfaces) {
    it(`${s.name} contains a visibility-gate marker (visibilitychange|document.hidden)`, () => {
      // Matched against the CODE — a comment can never satisfy this.
      expect(s.code).toMatch(VISIBILITY_GATE_CODE);
    });

    it(`${s.name} contains a refocus-refresh marker`, () => {
      // CODE-SHAPED: the debounced refocus refresh itself, not prose.
      expect(s.code).toMatch(REFOCUS_REFRESH_CODE);
    });
  }
});
