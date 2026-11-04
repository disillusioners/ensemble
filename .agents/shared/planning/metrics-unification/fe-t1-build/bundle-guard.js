#!/usr/bin/env node
// The T1 BUNDLE GUARD (phase 1, task 1.9 — AM-2 + C-1 + C-3 + A-8).
//
//   Usage: node bundle-guard.js <dist-dir> <production-build-log>
//   Exit:  0 = GREEN, 1 = RED (a guard violation), 2 = USAGE/ENV error.
//
// WHY THIS EXISTS (AM-2): a LAZY Chart.js leak trips NO budget — the
// Angular `initial` budget counts entry points + their transitive STATIC
// imports, and lazy chunks are counted by no budget type. The production
// build therefore stays GREEN and both FE packs stay GREEN. The guards
// are therefore numeric + import-graph, and they live in the phase that
// can break it.
//
// THE FIVE PARTS (the named baseline artifact is the single source —
// B8: a number duplicated only in plan prose is NOT a baseline):
//
//   (a) B1 — the numeric initial-total delta vs the frozen baseline
//       (|delta| <= 2048 B);
//   (b) B2 — NO `chart.js` marker in ANY initial chunk;
//   (c) B3 — the import SHAPE + import SITE: 0 static `from 'chart.js'`
//       sites anywhere, every site inside `src/app/pages/metrics/**`,
//       every site the DYNAMIC `await import('chart.js')` form;
//   (d) B6 — the `anyComponentStyle` warning SET (not a count) against
//       the 9 paths captured mechanically from the phase-1 build log;
//   (e) THE VENDOR-STATIC-EDGE INVARIANT (N5 enforcement, 2026-11-04) —
//       `vendor_static_edge_count > 0` OR `vendor_in_initial_set == true`
//       is RED. The measurement tool asserts the invariant itself and
//       exits 4 on violation; this guard USED TO SWALLOW that exit
//       (rendering `measurement_tool = (unavailable)` on a green run) —
//       a false-green seam. The tool's exit status is now PROPAGATED:
//       any non-zero measure-tool exit fails the guard, and the tool's
//       JSON (printed even on its exit-4 path) is re-read so the
//       invariant is enforced here too (defense in depth, IMPL-24).
//
// THE INITIAL SET: the guard computes it with the SAME basis as the
// measurement tool (index.html's referenced files + the transitive
// STATIC-import closure) and CROSS-CHECKS its total against that tool's
// own output — if the two ever disagree, the guard fails loudly rather
// than measuring something else.
//
// NO DAEMON, NO LIVE CONTACT: the guard reads a production build's
// dist directory, a build log, and the source tree. It never starts a
// daemon, never opens a socket, and never touches a live install.
'use strict';

const fs = require('fs');
const path = require('path');
const { execFileSync } = require('child_process');

const PLAN_DIR = __dirname;
const BASELINE_PATH = path.join(PLAN_DIR, 'bundle-baseline.json');
const MEASURE_PATH = path.join(PLAN_DIR, 'bundle-baseline.measure.js');
const REPO_ROOT = path.resolve(PLAN_DIR, '..', '..', '..', '..', '..');
const FRONTEND_DIR = path.join(REPO_ROOT, 'frontend');

const DIST = process.argv[2];
const BUILD_LOG = process.argv[3];

if (!DIST || !BUILD_LOG) {
  console.error('usage: node bundle-guard.js <dist-dir> <production-build-log>');
  process.exit(2);
}
if (!fs.existsSync(DIST)) {
  console.error(`bundle-guard: dist dir not found: ${DIST}`);
  process.exit(2);
}
if (!fs.existsSync(BUILD_LOG)) {
  console.error(`bundle-guard: build log not found: ${BUILD_LOG}`);
  process.exit(2);
}

// ── The named baseline artifact (B8 — the single source) ──────────────────
const baseline = JSON.parse(fs.readFileSync(BASELINE_PATH, 'utf8'));
const BASELINE_BYTES = baseline.guards.B1_initial_total_delta.baseline_bytes;
// The threshold is READ from the artifact too (B8): the guard hard-codes
// NO numeric baseline of its own.
const DELTA_LIMIT = Number(
  (baseline.guards.B1_initial_total_delta.threshold.match(/(\d+)/) ?? [])[1] ?? 0,
);
const WARNING_BASELINE = new Set(
  baseline.anyComponentStyle_baseline.entries.map((entry) => entry.split(' — ')[0].trim()),
);

// ── The initial set (the builder's own basis) ─────────────────────────────
const read = (file) => fs.readFileSync(path.join(DIST, file), 'utf8');
const size = (file) => fs.statSync(path.join(DIST, file)).size;

function computeInitialSet() {
  const html = read('index.html');
  const refs = [...html.matchAll(/(?:src|href)="([^"]+\.(?:js|css))"/g)].map((m) => m[1]);
  const initial = new Set(refs);
  const queue = [...refs];
  while (queue.length) {
    const file = queue.pop();
    if (!fs.existsSync(path.join(DIST, file))) continue;
    const source = read(file);
    for (const match of [
      ...source.matchAll(/from\s*"([^"]+\.js)"/g),
      ...source.matchAll(/import\s*"([^"]+\.js)"/g),
    ]) {
      const dep = path.basename(match[1]);
      if (!initial.has(dep)) {
        initial.add(dep);
        queue.push(dep);
      }
    }
  }
  return [...initial].filter((file) => fs.existsSync(path.join(DIST, file)));
}

// ── (a) the numeric initial-total delta ──────────────────────────────────
// A build that FAILED (the `initial` budget tripped — the eager-leak case)
// leaves no dist to measure. The guard must report that as a VIOLATION,
// never as an uncaught crash and never as a silent green.
let initialFiles = [];
let initialTotal = 0;
let distError = null;
try {
  initialFiles = computeInitialSet();
  initialTotal = initialFiles.reduce((sum, file) => sum + size(file), 0);
} catch (err) {
  distError = err instanceof Error ? err.message : String(err);
  initialFiles = [];
  initialTotal = 0;
}
const delta = initialTotal - BASELINE_BYTES;

// ── the cross-check against the measurement tool (one measurement, two readers)
//
// N5 (2026-11-04, tester gate): the tool's own vendor-invariant RED is a
// NON-ZERO EXIT (4) — this block used to swallow it and render
// `measurement_tool = (unavailable)` while the guard stayed GREEN: a
// false-green seam. The exit status is now PROPAGATED (any non-zero tool
// exit fails the guard), and the tool's JSON is still parsed — from
// stdout, which the tool prints even on its exit-4 path — so the vendor
// invariant is enforced HERE too (defense in depth, IMPL-24).
let measureTotal = null;
let measureJson = null;
let measureFailure = null;
try {
  measureJson = JSON.parse(
    execFileSync(process.execPath, [MEASURE_PATH, DIST], {
      encoding: 'utf8',
      stdio: ['ignore', 'pipe', 'ignore'],
    }),
  );
  measureTotal = measureJson.initial_total_bytes;
} catch (err) {
  measureTotal = null;
  measureJson = null;
  // err.status = the tool's exit code (execFileSync); err.stderr = its stderr.
  const status = typeof err?.status === 'number' ? err.status : null;
  const stderrTail = String(err?.stderr ?? '')
    .trim()
    .split('\n')
    .filter(Boolean)
    .slice(-1)[0];
  measureFailure = `the measurement tool exited ${
    status === null ? 'with a non-zero status' : status
  }${stderrTail ? ` — ${stderrTail}` : ''}`;
  // N5: the tool prints its JSON even on its exit-4 invariant path —
  // parse that stdout so the invariant below is enforced even when the
  // tool failed (the failure and the evidence are not mutually exclusive).
  const stdoutText = typeof err?.stdout === 'string' ? err.stdout : '';
  if (stdoutText.trim()) {
    try {
      measureJson = JSON.parse(stdoutText);
      if (typeof measureJson?.initial_total_bytes === 'number') {
        measureTotal = measureJson.initial_total_bytes;
      }
    } catch {
      measureJson = null; // non-JSON stdout — the failure line already carries the seam
    }
  }
}

// ── (b) the `chart.js` marker scan over the initial set ──────────────────
const markerHits = [];
for (const file of initialFiles) {
  if (read(file).includes('chart.js')) {
    markerHits.push(file);
  }
}

// ── (c) the import shape + import site ────────────────────────────────────
const STATIC_IMPORT_PATTERNS = [
  /^\s*import\s[^\n]*\bfrom\s+['"]chart\.js['"]/m,
  /^\s*import\s+['"]chart\.js['"]/m,
];
const DYNAMIC_IMPORT_PATTERN = /\bimport\s*\(\s*['"]chart\.js['"]\s*\)/g;

function stripComments(source) {
  return source
    .replace(/\/\*[\s\S]*?\*\//g, (block) => block.replace(/[^\n]/g, ' '))
    .replace(/(^|[^:'"`])\/\/[^\n]*/g, '$1');
}

function collectSourceFiles(root) {
  const skip = new Set(['node_modules', 'dist', '.angular', 'coverage']);
  const out = [];
  const walk = (dir) => {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      if (skip.has(entry.name)) continue;
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) walk(full);
      else if (entry.name.endsWith('.ts')) out.push(full);
    }
  };
  walk(root);
  return out;
}

const importSites = collectSourceFiles(FRONTEND_DIR)
  .map((file) => {
    const code = stripComments(fs.readFileSync(file, 'utf8'));
    return {
      path: path.relative(FRONTEND_DIR, file).split(path.sep).join('/'),
      static: STATIC_IMPORT_PATTERNS.flatMap((pattern) => code.match(pattern) ?? []),
      dynamic: code.match(DYNAMIC_IMPORT_PATTERN) ?? [],
    };
  })
  .filter((site) => site.static.length > 0 || site.dynamic.length > 0);

const staticSites = importSites.filter((site) => site.static.length > 0);
const outsideMetrics = importSites.filter(
  (site) => !site.path.startsWith('src/app/pages/metrics/'),
);

// ── (d) the `anyComponentStyle` warning SET (not a count) ────────────────
const log = fs.readFileSync(BUILD_LOG, 'utf8');
const warningPaths = new Set(
  [...log.matchAll(/([^\s]+\.scss) exceeded maximum budget/g)].map((m) => m[1]),
);
const addedWarnings = [...warningPaths].filter((p) => !WARNING_BASELINE.has(p)).sort();
const removedWarnings = [...WARNING_BASELINE].filter((p) => !warningPaths.has(p)).sort();

// ── (e) the vendor-static-edge invariant (N5 enforcement, 2026-11-04) ────
// The measurement tool fails loud on its own (exit 4), but the guard used
// to SWALLOW that exit; the invariant is now enforced HERE, from the
// tool's own JSON — RED on `vendor_static_edge_count > 0` OR
// `vendor_in_initial_set == true`. When the tool never answered (the
// MEASURE failure), the fields are unavailable and the tool failure
// itself is the violation — the guard is fail-closed, never silent-green.
const vendorStaticEdgeCount =
  measureJson && typeof measureJson.vendor_static_edge_count === 'number'
    ? measureJson.vendor_static_edge_count
    : null;
const vendorInInitialSet =
  measureJson && typeof measureJson.vendor_in_initial_set === 'boolean'
    ? measureJson.vendor_in_initial_set
    : null;

// ── The verdict ───────────────────────────────────────────────────────────
const failures = [];
if (distError !== null) {
  failures.push(
    `B1 the dist could not be measured (${distError}) — a build that failed leaves no initial set to compare`,
  );
}
if (distError === null && Math.abs(delta) > DELTA_LIMIT) {
  failures.push(
    `B1 initial-total delta ${delta} B exceeds |${DELTA_LIMIT}| B (measured ${initialTotal}, baseline ${BASELINE_BYTES})`,
  );
}
if (measureTotal !== null && measureTotal !== initialTotal) {
  failures.push(
    `B1 the guard and the measurement tool disagree on the initial total (guard ${initialTotal}, tool ${measureTotal})`,
  );
}
if (markerHits.length > 0) {
  failures.push(`B2 the chart.js marker is present in ${markerHits.length} initial chunk(s): ${markerHits.join(', ')}`);
}
if (staticSites.length > 0) {
  failures.push(
    `B3 ${staticSites.length} STATIC chart.js import site(s): ${staticSites.map((s) => s.path).join(', ')}`,
  );
}
if (outsideMetrics.length > 0) {
  failures.push(
    `B3 ${outsideMetrics.length} chart.js import site(s) outside src/app/pages/metrics/**: ${outsideMetrics.map((s) => s.path).join(', ')}`,
  );
}
if (importSites.length === 0) {
  failures.push('B3 NO chart.js import site was found — the guard is VACUOUS');
}
if (addedWarnings.length > 0) {
  failures.push(`B6 warning set ADDED ${addedWarnings.length}: ${addedWarnings.join(', ')}`);
}
if (removedWarnings.length > 0) {
  failures.push(`B6 warning set REMOVED ${removedWarnings.length}: ${removedWarnings.join(', ')}`);
}
if (measureFailure !== null) {
  failures.push(
    `MEASURE ${measureFailure} — a swallowed non-zero measure-tool exit is a false-green seam (N5)`,
  );
}
if (vendorStaticEdgeCount !== null && vendorStaticEdgeCount > 0) {
  const sites =
    Array.isArray(measureJson?.vendor_static_edge_sites) &&
    measureJson.vendor_static_edge_sites.length > 0
      ? measureJson.vendor_static_edge_sites.join('; ')
      : 'sites unknown';
  failures.push(
    `VENDOR-INVARIANT RED — vendor_static_edge_count ${vendorStaticEdgeCount} > 0: ${sites}`,
  );
}
if (vendorInInitialSet === true) {
  failures.push(
    'VENDOR-INVARIANT RED — a vendor chunk is in the initial set (vendor_in_initial_set = true)',
  );
}

// ── The report ────────────────────────────────────────────────────────────
console.log('=== T1 bundle guard (phase 1, task 1.9) ===');
console.log(`baseline_artifact: ${path.relative(REPO_ROOT, BASELINE_PATH)}`);
console.log('');
console.log('(a) B1 — the numeric initial-total delta');
console.log(`    initial_total_bytes = ${initialTotal}`);
console.log(`    baseline_bytes      = ${BASELINE_BYTES}`);
console.log(`    delta               = ${delta} B (limit |${DELTA_LIMIT}| B)`);
console.log(`    initial_file_count  = ${initialFiles.length}`);
console.log(`    measurement_tool    = ${measureTotal === null ? '(unavailable)' : measureTotal} (cross-check)`);
console.log('');
console.log('(e) the vendor-static-edge invariant (N5 enforcement)');
console.log(
  `    vendor_static_edge_count = ${
    vendorStaticEdgeCount === null ? '(unavailable)' : vendorStaticEdgeCount
  } (invariant: 0)`,
);
console.log(
  `    vendor_in_initial_set    = ${
    vendorInInitialSet === null ? '(unavailable)' : vendorInInitialSet
  } (invariant: false)`,
);
console.log(`    measurement_tool_rc      = ${measureFailure === null ? '0' : 'NON-ZERO — failed loud (see MEASURE failure)'}`);
console.log('');
console.log('(b) B2 — the chart.js marker scan over the initial set');
console.log(`    initial_chunks_scanned = ${initialFiles.length}`);
console.log(`    marker_hits            = ${markerHits.length}`);
if (markerHits.length > 0) {
  for (const hit of markerHits) {
    console.log(`      HIT ${hit}`);
  }
}
console.log('');
console.log('(c) B3 — the import shape + import site');
console.log(`    chart_js_import_sites  = ${importSites.length}`);
console.log(`    static_sites           = ${staticSites.length}`);
console.log(`    outside_metrics_tree   = ${outsideMetrics.length}`);
for (const site of importSites) {
  console.log(
    `      ${site.path}: dynamic=${site.dynamic.length} static=${site.static.length}`,
  );
}
console.log('');
console.log('(d) B6 — the anyComponentStyle warning SET (not a count)');
console.log(`    baseline_set_size = ${WARNING_BASELINE.size}`);
console.log(`    measured_set_size = ${warningPaths.size}`);
console.log(`    added   = ${addedWarnings.length}${addedWarnings.length ? ': ' + addedWarnings.join(', ') : ''}`);
console.log(`    removed = ${removedWarnings.length}${removedWarnings.length ? ': ' + removedWarnings.join(', ') : ''}`);
console.log('    baseline set:');
for (const entry of [...WARNING_BASELINE].sort()) {
  console.log(`      ${entry}`);
}
console.log('');
if (failures.length === 0) {
  console.log('RESULT: GREEN (B1 + B2 + B3 + B6 + vendor-invariant + measure-propagation)');
  process.exit(0);
}
console.log(`RESULT: RED (${failures.length} violation(s))`);
for (const failure of failures) {
  console.log(`  - ${failure}`);
}
process.exit(1);
