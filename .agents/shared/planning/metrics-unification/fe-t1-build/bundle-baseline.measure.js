// Lane-base bundle baseline — SINGLE SOURCE for the T1 bundle guards.
// Reproduce:  cd frontend && node <this file> dist/frontend/browser
// (the builder's own "Initial chunk files" table is the budget's basis;
//  see node_modules/@angular/build/src/utils/bundle-calculator.js:155-167
//  — InitialCalculator sums the chunks flagged `initial`; index.html is an
//  ASSET, not a chunk, so it is NOT in the budget total.)
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

// --- the /metrics route fetch set: the route chunk + its DIRECT static deps
const routeChunk = allJs.find((f) => read(f).includes('metrics-empty-panel'));
const routeDeps = [...read(routeChunk).matchAll(/(?:from|import)\s*"([^"]+\.js)"/g)].map((m) => path.basename(m[1]));
const routeFetchSet = routeDeps.reduce((a, d) => a + size(d), size(routeChunk));

const out = {
  initial_total_bytes: initialTotal,
  initial_file_count: initialFiles.length,
  error_budget_bytes: 6000000,
  headroom_bytes: 6000000 - initialTotal,
  index_html_bytes: size('index.html'),
  whole_initial_payload_bytes: initialTotal + size('index.html'),
  lazy_chunk_count: lazy.length,
  lazy_bytes_total: lazy.reduce((a, f) => a + size(f), 0),
  metrics_route_chunk: routeChunk,
  metrics_route_chunk_bytes: size(routeChunk),
  metrics_route_fetch_set_bytes: routeFetchSet,
  largest_lazy: bySize[0], largest_lazy_bytes: size(bySize[0]),
  second_largest_lazy: bySize[1], second_largest_lazy_bytes: size(bySize[1]),
  resources_route_chunk: allJs.find((f) => read(f).includes('anomaly-marker')),
  resources_route_chunk_bytes: size(allJs.find((f) => read(f).includes('anomaly-marker'))),
};
console.log(JSON.stringify(out, null, 2));
