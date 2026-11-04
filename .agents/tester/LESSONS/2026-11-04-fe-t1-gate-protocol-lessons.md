# Lessons — FE T1 metrics functional gate (2026-11-04, wi-32a99618)

Companion to `RESULTS/2026-11-04-fe-t1-metrics-functional-gate.md`. Reusable gate-protocol lessons.

## 1. Warm the dev daemon ≥ one anomaly interval before pixel runs
The anomaly engine's first pass fires at `anomaly_interval_s=600`. A daemon ~4 min old has ZERO
anomalies → PX-1 (tab-1 with data) is a **false RED** (`anomaly_rows≈0`); the evidence endpoint also
has no real ids yet. Warm ≥600 s (the dev's phase-9 run was 21-min warm, so the trap was latent).
Symptom signature: cold 15/16 with only PX-1 failing, warm 16/16.

## 2. `/api/resources/traceback` speaks ISO-8601, not epoch seconds
`t0`/`t1` epoch ints → `422 t0 is not a valid ISO-8601 timestamp`. Same for any gate leg that
probes the traceback window. (The door's other 5 endpoints take plain query params.)

## 3. Serialize pack MUTATION runs against concurrent census/guard legs
Wave-3 ran pack (with a planted-missing-file mutation of `app.routes.spec.ts`) concurrently with the
whole-FE census and the guard build. The plant produced: (a) the census HEAD leg reporting 152 suites
vs `--listTests`'s 153 (file vanished mid-run → "collected but never reported"), and (b) the guard
worker observing a transient source deletion. Both self-healed and were explainable only by
cross-referencing the three reports. **Rule: mutation proofs run AFTER (or serialized against) any
concurrent suite-execution wave** — otherwise the mutation reads as a phantom jest/deletion anomaly.

## 4. jest base↔HEAD FAILED diffs MUST key on worktree-relative paths
`testResults[].name` is ABSOLUTE. Keying on it yields an absurd diff (every failure appears
base-only AND head-only). Normalize to worktree-relative before diffing. (Also: `--listTests` counts
include `e2e/` specs that jest's `testPathIgnorePatterns` excludes — a naive `git ls-tree|grep` census
is off by 30 on this repo.)

## 5. Vendor-invariant guards on esbuild output are plant-shape-sensitive
A source-level static `import 'chart.js'` triggers facade re-splitting: the route's dynamic target
becomes a ~1 kB re-export shim and the vendor bulk binds STATICALLY into the fetch set — the
structural `vendor_static_edge_count == 0` invariant stays green while the leak is real. The numeric
layers (B3 static-source-scan, B4b lazy-only ≤ threshold, B1 initial-delta) caught every planted
shape. Structural invariants on bundles must be paired with numeric budgets; dist-level plants
(static edge appended to an initial chunk) are the shape that reddens the structural check itself.

## 6. The resources door has a cross-layer vocabulary break (B-1, blocker)
`/api/resources/history` synthesizes FE-facing names (`daemon_rss_mb`, …) while
`/api/resources/anomalies` passes engine-internal `series_key` (`daemon.rss_kb`, …) — intersection
EMPTY. Any FE feature joining anomalies to timeline panels via series keys is dead-on-arrival until
this is normalized (daemon-side at `resources.py:820`, or FE-side mapping). Gate legs that prove
"rows exist + drilldown opens via row click" do NOT prove the chart-marker path — 148 real canvas
clicks produced zero drilldowns while the row path worked.

## 7. Misc
- House-SVG chart renderer is the mount-FAILURE fallback — its `house-anomaly-marker` hooks are
  absent on the healthy path; pointer legs must target the real DOM contract (canvas + overlay plugin).
- Pack `${leg}_ERROR_SUMMARY` grep (`^(FAILED|ERROR) `) never matches jest's `FAIL ` summary lines —
  read the tee'd leg log, not the summary var, for failure names.
- The pack's prod-build leg timeout (900 s) EQUALS the pack self-cap (900 s) — a slow build alone
  can exhaust the budget (exit 124) with no test failure.
