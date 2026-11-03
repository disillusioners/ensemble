// Lane-base bundle baseline — SINGLE SOURCE for the T1 bundle guards.
// Reproduce:  cd frontend && node <this file> dist/frontend/browser
//
// Semantics (the builder's own basis): the `initial` budget sums the chunks
// flagged `initial` (entry points + transitive STATIC imports) — see
// node_modules/@angular/build/src/utils/bundle-calculator.js:155-167.
// index.html is an ASSET, not a chunk, so it is NOT in the budget total.
//
// ROUTE-CHUNK IDENTIFICATION (B3) — route-keyed, never content-marker-only:
//   * PRIMARY  — the lane-controlled marker constant the metrics shell exports
//     (Phase 2's task 2.13, literal `ensemble-metrics-route-v1`). The lane
//     controls this key and cannot invalidate it, so the route-fetch-set
//     baseline stays measurable AFTER Phases 7 and 8 rewrite the page.
//   * FALLBACK — a legacy content marker, used ONLY to reproduce the lane-base
//     baseline (measured before the marker constant exists). It is reported in
//     `route_chunk_locator` so the evidence always says WHICH locator resolved.
//   * ABSENT-TOLERANT — if neither resolves, the route fields are null and the
//     measurement reports "unavailable". It never throws and never mis-resolves
//     (e.g. the retired /resources marker matching the metrics chunk).
const fs = require('fs');
const path = require('path');
const DIST = process.argv[2];
const read = (f) => fs.readFileSync(path.join(DIST, f), 'utf8');
const size = (f) => fs.statSync(path.join(DIST, f)).size;

// --- initial set: index.html's referenced files + the transitive STATIC-import closure
const html = read('index.html');
const refs = [...html.matchAll(/(?:src|href)="([^"]+\.(?:js|css))"/g)].map((m) => m[1]);
const initial = new Set(refs);
const q = [...refs];
while (q.length) {
  const f = q.pop();
  if (!fs.existsSync(path.join(DIST, f))) continue;
  const s = read(f);
  for (const m of [...s.matchAll(/from\s*"([^"]+\.js)"/g), ...s.matchAll(/import\s*"([^"]+\.js)"/g)]) {
    const d = path.basename(m[1]);
    if (!initial.has(d)) { initial.add(d); q.push(d); }
  }
}
const initialFiles = [...initial].filter((f) => fs.existsSync(path.join(DIST, f)));
const initialTotal = initialFiles.reduce((a, f) => a + size(f), 0);

const allJs = fs.readdirSync(DIST).filter((f) => f.endsWith('.js'));
const lazy = allJs.filter((f) => !initial.has(f));
const bySize = lazy.slice().sort((a, b) => size(b) - size(a));

// --- route-chunk locators (B3): route-keyed primary, legacy fallback, absent-tolerant
const ROUTE_KEY = process.env.ENSEMBLE_METRICS_ROUTE_KEY || 'ensemble-metrics-route-v1';
const LEGACY_ROUTE_MARKER = 'metrics-empty-panel';
const LEGACY_RESOURCES_MARKER = 'anomaly-marker';

const uniqueChunkByKey = (key) => {
  const hits = allJs.filter((f) => read(f).includes(key));
  return hits.length === 1 ? hits[0] : null;   // EXACTLY one, else null
};

// the route chunk: route-key first, legacy fallback only if the key is absent
let routeChunk = uniqueChunkByKey(ROUTE_KEY);
let routeChunkLocator = routeChunk ? 'route-key' : null;
if (!routeChunk) {
  routeChunk = uniqueChunkByKey(LEGACY_ROUTE_MARKER);
  if (routeChunk) routeChunkLocator = 'legacy-content-marker';
}

// the route fetch set: the route chunk + its DIRECT static deps (absent-tolerant)
const routeDeps = routeChunk
  ? [...read(routeChunk).matchAll(/(?:from|import)\s*"([^"]+\.js)"/g)].map((m) => path.basename(m[1]))
  : [];
const routeFetchSetBytes = routeChunk
  ? routeDeps.reduce((a, d) => a + size(d), size(routeChunk))
  : null;

// the PRE-RETIREMENT /resources route chunk (Phase 7 retires the route, so it
// does not survive the lane — absent-tolerant, never mis-resolved)
const resourcesRouteChunk = uniqueChunkByKey(LEGACY_RESOURCES_MARKER);

const out = {
  initial_total_bytes: initialTotal,
  initial_file_count: initialFiles.length,
  error_budget_bytes: 6000000,
  headroom_bytes: 6000000 - initialTotal,
  index_html_bytes: size('index.html'),
  whole_initial_payload_bytes: initialTotal + size('index.html'),
  lazy_chunk_count: lazy.length,
  lazy_bytes_total: lazy.reduce((a, f) => a + size(f), 0),
  route_chunk_locator: routeChunkLocator,                 // 'route-key' | 'legacy-content-marker' | null
  route_chunk_key: ROUTE_KEY,
  route_chunk_resolved_to_exactly_one: routeChunk !== null,
  metrics_route_chunk: routeChunk,                        // null when unavailable
  metrics_route_chunk_bytes: routeChunk ? size(routeChunk) : null,
  metrics_route_fetch_set_bytes: routeFetchSetBytes,       // null when unavailable
  largest_lazy: bySize[0], largest_lazy_bytes: size(bySize[0]),
  second_largest_lazy: bySize[1], second_largest_lazy_bytes: size(bySize[1]),
  pre_retirement_resources_route_chunk: resourcesRouteChunk,          // null after Phase 7
  pre_retirement_resources_route_chunk_bytes: resourcesRouteChunk ? size(resourcesRouteChunk) : null,
};
console.log(JSON.stringify(out, null, 2));
