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

// ── THE LAZY-ONLY DECOMPOSITION (the B4 rider — 2026-11-03) ─────────────
//
// The route fetch set is NOT all NEW bytes: a member that is ALSO in the
// initial set (index.html's refs + the transitive static-import closure) is
// downloaded at app STARTUP, whether or not the operator ever opens
// /metrics. D-1 protects NEW bytes the page fetches; the eager case stays
// caught by B1 + the initial error budget.
//
// So the route fetch set is decomposed:
//   * the LAZY-ONLY portion — the route chunk + its direct static deps
//     that are NOT in the initial set (the bytes the /metrics page really
//     adds); and
//   * the SHARED portion — the members that are already in the initial set.
//
// The two fields are ADDITIVE (no existing field's semantics change), stay
// route-keyed and absent-tolerant, and are `null` exactly when the route
// chunk is unresolvable — the tool never throws and never mis-resolves.
const routeFetchSetMembers = routeChunk ? [routeChunk, ...routeDeps] : [];
const lazyOnlyMembers = routeFetchSetMembers.filter((f) => !initial.has(f));
const sharedMembers = routeFetchSetMembers.filter((f) => initial.has(f));
const metricsRouteFetchSetLazyOnlyBytes = routeChunk
  ? lazyOnlyMembers.reduce((a, f) => a + size(f), 0)
  : null;
const metricsRouteFetchSetSharedWithInitialBytes = routeChunk
  ? sharedMembers.reduce((a, f) => a + size(f), 0)
  : null;

// the PRE-RETIREMENT /resources route chunk (Phase 7 retires the route, so it
// does not survive the lane — absent-tolerant, never mis-resolved).
//
// A6 FIX (2026-11-04, Phase 9): after the Phase-5 relocation the legacy
// `anomaly-marker` string LIVES IN THE METRICS ROUTE CHUNK, so a bare legacy
// lookup mis-resolves and would report the metrics chunk under a
// pre-retirement field name. Per the artifact's "refuse to mis-resolve" rule:
// when the legacy marker resolves to the SAME chunk as the route key, the
// field reports null (absent).
let resourcesRouteChunk = uniqueChunkByKey(LEGACY_RESOURCES_MARKER);
if (resourcesRouteChunk && routeChunk && resourcesRouteChunk === routeChunk) {
  resourcesRouteChunk = null; // the legacy marker is the metrics chunk now — ABSENT, never mis-resolved
}

// ── THE VENDOR-STATIC-EDGE INVARIANT (the B4b rider — leader ruling IMPL-24,
//    2026-11-04) ─────────────────────────────────────────────────────────
// chart.js vendor bytes may reach the /metrics page ONLY via dynamic import.
// The invariant: **the vendor chunk appears in ZERO static-import edges
// (`from "…"` / `import "…"`) across ALL chunks in the dist** — a chunk that
// is BOTH a lazy dynamic target AND statically imported somewhere is exactly
// the static-leak defect class (the builder hoists truly-shared chunks into
// the initial set, so a static edge on a lazy chunk means a static-shaped
// leak). IDENTIFICATION (deterministic, cheap): the ROUTE-KEYED route
// chunk's dynamic-import targets (`import("chunk-….js")`). On the Phase-9
// dist there is EXACTLY ONE (the chart.js barrel); if a future build carries
// several, the assertion covers ALL of them (each must remain
// dynamic-only). This probe FAILS LOUD: on a violation the tool prints a RED
// line to stderr and exits 4 (the JSON still prints — the absent-tolerance
// "never throws" contract governs MIS-RESOLUTION, not invariant violations).
const vendorDynTargets = routeChunk
  ? [...read(routeChunk).matchAll(/import\s*\(\s*["']([^"']+\.js)["']\s*\)/g)].map((m) => path.basename(m[1]))
  : [];
let vendorStaticEdges = null;
const vendorStaticEdgeSites = [];
if (vendorDynTargets.length) {
  for (const f of allJs) {
    const s = read(f);
    for (const m of s.matchAll(/(?:from|import)\s*["']([^"']+\.js)["']/g)) {
      const d = path.basename(m[1]);
      if (vendorDynTargets.includes(d)) vendorStaticEdgeSites.push(`${f} -> ${d}`);
    }
  }
  vendorStaticEdges = vendorStaticEdgeSites.length;
}
const vendorInvariantOk = vendorDynTargets.length > 0 ? vendorStaticEdges === 0 && !vendorDynTargets.some((t) => initial.has(t)) : null;

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
  // the B4 rider's decomposition (2026-11-03): the LAZY-ONLY portion of the
  // route fetch set (the bytes /metrics really adds) and the SHARED portion
  // (the members the app shell already downloads at startup).
  metrics_route_fetch_set_lazy_only_bytes: metricsRouteFetchSetLazyOnlyBytes,
  metrics_route_fetch_set_shared_with_initial_bytes: metricsRouteFetchSetSharedWithInitialBytes,
  largest_lazy: bySize[0], largest_lazy_bytes: size(bySize[0]),
  second_largest_lazy: bySize[1], second_largest_lazy_bytes: size(bySize[1]),
  pre_retirement_resources_route_chunk: resourcesRouteChunk,          // null after Phase 7
  pre_retirement_resources_route_chunk_bytes: resourcesRouteChunk ? size(resourcesRouteChunk) : null,
  // the vendor-static-edge invariant (leader ruling IMPL-24): the vendor may
  // be reached ONLY by dynamic import — zero static edges anywhere, and never
  // in the initial set. Fails loud (stderr RED + exit 4) on violation.
  vendor_chunks: vendorDynTargets,                        // the route chunk's dynamic-import targets
  vendor_static_edge_count: vendorStaticEdges,            // invariant: 0
  vendor_static_edge_sites: vendorStaticEdgeSites,        // non-empty = the leak sites
  vendor_in_initial_set: vendorDynTargets.some((t) => initial.has(t)), // invariant: false
  vendor_invariant_ok: vendorInvariantOk,                 // true | false | null (unresolvable route)
};
if (vendorInvariantOk === false) {
  console.error(`TOOL: VENDOR-INVARIANT RED — vendor chunk(s) [${vendorDynTargets.join(', ')}] carry ${vendorStaticEdges} static-import edge(s): ${vendorStaticEdgeSites.join('; ')}`);
  process.exitCode = 4;
}
console.log(JSON.stringify(out, null, 2));
