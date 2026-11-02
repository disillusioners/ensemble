# Architecture Recommendation — central-metrics-v1 (query API, history buffer, FE page, extension subset)

Date: 2026-11-02 (UTC)
Author: technical-analysis worker (commission: central-metrics-v1 architecture analysis)
Analysis depth: deep-dive
Status: Draft — feeds planner → architect → plan council → fresh approver
Companion: `decisions.md` (same directory) holds the per-design-point rulings
(D1–D5, each with Evidence / Options / Ruling / Promote-time-disclosure). This document is the
system view; the decisions record is the contract.

## Question

Grow the landed default-OFF central-metrics lane (registry `daemon/checkpoint_metrics.py` +
`:9798` exposition listener + `/api/metrics` Prometheus text route) into the v1 system: a
read/serve query API with history for an FE Metrics page, plus the family extension subset —
resolving activation posture, charting approach, storage/retention, query-API architecture, and
the 18→27 extension derivation WITH EVIDENCE.

## Context Summary

The metrics-collection lane (P0) landed a dependency-free registry (1573 L, per-metric locks,
copy-on-publish at a 15 s cadence, three-tier never-exported enforcement, hard ≤2000
cardinality cap with ≈29% consumed at full catalog), a loopback-only unauthenticated exposition
listener (:9798, default-OFF via `ENSEMBLE_METRICS_ENABLED` / managed tier), and an
auth-gated `/api/metrics` Prometheus-classic-text route (scope `maintenance:read`, 503
fail-closed). Eighteen metric objects are live (≈89 series): F0.1/F0.2/F0.4, partial F7, F7b,
F7c. The catalog defines 26 daemon-origin families (580 series; fully-wired ceiling ≈1145;
budget ≤2000).

The gap this commission closes: the registry has NO windowing/retention (process-lifetime
cumulative), there is NO query API (agents have zero native metrics reads —
`git grep 'readiness|loop_lag|pool_state|leak_signal' daemon/tools/` = ZERO hits), and the FE
has NO metrics page (`/api/metrics` has zero FE consumers; the only chart UI is the
svg-timeline primitive on the Resources page). Two exposition surfaces exist; neither is
query-shaped. The house architecture split (leader-ratified decision 4) already assigns durable
time-series to the resource_monitor PG tier and alerting to Prometheus (deferred item #3);
this lane adds the third leg — the in-process quick-read/query tier — without disturbing the
split.

Governing constraints carried forward: dependency-free renderer (decision 5, reaffirmed),
Prometheus classic text NOT OpenMetrics (MA-16), separate loopback listener never a main-app
route (decision 3/CR-2), catalog §1 is authoritative on names/labels, ≤2000 cardinality budget
with registry-cap enforcement, tiered-gate doctrine (any `daemon/api.py`/`daemon/manager.py`
wiring = mechanical Tier-2 leg), and NO live promote in this lane.

## Component Map

```
                    ┌──────────────────────────────────────────────────────────┐
                    │  REGISTRY (landed, untouched core)                        │
                    │  daemon/checkpoint_metrics.py                             │
                    │  - per-metric locks, copy-on-publish (15s cadence)        │
                    │  - published_snapshot()  ← the lock-free read substrate   │
                    │  - ≤2000 cardinality cap, VOCABULARY_VERSION gauge        │
                    └───────┬──────────────────────┬───────────────────────┬───┘
                            │ published refs       │ published refs        │ render_metrics()
              ┌─────────────▼─────────┐   ┌────────▼──────────┐   ┌────────▼─────────────┐
              │ HISTORY SAMPLER       │   │ QUERY PROVIDER    │   │ EXPOSITION (landed)  │
              │ (new, daemon thread)  │   │ (new, protocol)   │   │ :9798 listener (sw-g)│
              │ reads published only  │   │ families()        │   │ /api/metrics text    │
              │ 10s / ring per series │   │ snapshot()        │   │ (auth-gated, landed) │
              │ ≤48MB adaptive depth  │   │ history()         │   │ Prometheus classic   │
              └───────────┬───────────┘   └────────┬──────────┘   └──────────┬───────────┘
                          │ appends                │ pure reads              │ scrape (deferred #3)
                  ┌───────▼────────┐      ┌────────▼─────────────────────┐   │
                  │ RING BUFFER    │◄─────│ QUERY ROUTES (new, same fam) │   │
                  │ per-series     │      │ GET /api/metrics/families    │   │
                  │ deque(maxlen)  │      │ GET /api/metrics/snapshot    │   │
                  │ 2h @ 10s       │      │ GET /api/metrics/series      │   │
                  └────────────────┘      │ scope maintenance:read       │   │
                                          └────────┬─────────────────────┘   │
                                                   │ Accept: application/json│
                                          ┌────────▼─────────────────────┐   │
                                          │ FE METRICS PAGE (new)        │   │
                                          │ /metrics lazy route + gear   │   │
                                          │ Overview 10s / Charts 15s    │   │
                                          │ svg-line-chart primitive     │   │
                                          │ (generalized, ZERO deps)     │   │
                                          └──────────────────────────────┘   │
                                                                             │
        FAMILY WIRING (new module(s)) ── registers IN-subset families ──► REGISTRY
        (pull seams only: F0.3/F1/F3/F4/F5/F6/F9/F10/F12/F17 + partials F2/F7/F8/F11/F13/F18/F21)
```

Lifecycle wiring (lifespan order, `daemon/api.py`): after resource_monitor + llm_stream_watchdog
(A27 import placement precedent, `api.py:2373-2375`) → census sampler start (:2454-2483
precedent) → **NEW: history sampler start** (reads `limits.metrics_sampler_interval_s` once) →
listener start (:2393-2447 precedent). Shutdown reverse order; sampler stop joins its
daemon thread with the detached-join discipline (listener `stop()` :551-593 precedent).
Provider constructed once, stored `app.state.metrics_query` beside `app.state.metrics_listener`.

## Data Flow (prose diagrams-in-prose)

**Observe → registry (landed, unchanged):** production code calls `handle.inc/set/observe` at
seams (fail-open per C-14) → per-metric lock, bucket update O(log B), dirty flag → at most once
per cadence window the published copy is rebuilt O(series) (`_maybe_publish_locked`
`checkpoint_metrics.py:868-882`). No gate on the observe path — accumulation is unconditional.

**Publish → history:** every sampler tick (default 10 s), for each registered metric take the
`published_snapshot` reference (lock-free); if the identity is unchanged since the last tick,
skip (dedupe); else append `(ts, value)` — or `(ts, count, sum, buckets)` for histograms — to
that series' ring. Adaptive depth guard runs per tick: `depth = clamp(POINT_BUDGET //
live_series, 60, window_s // interval_s)`; shrinking re-truncates rings lazily. A wedged writer
means the ring receives stale copies — visible downstream as `last_publish_age_s`, never
fabricated.

**Query read path:** HTTP request → `_require_auth_on` (503 fail-closed) → `scope_required`
(403 on miss) → provider method → published refs / rings → pure derivations (quantiles at
query time from published buckets via the render-side pure helpers) → JSON envelope
`{schema_version, vocabulary_version, generated_at_utc, …}`. The path NEVER acquires a metric
lock and NEVER calls render_metrics — the exposition's try-acquire/budget machinery is
structurally out of the query path.

**Family availability path:** families endpoint derives per-family availability from runtime
facts: census running? postgres? proc available (Darwin)? capture-active (F1/F2 dark-when-off =
absence)? Each dark family carries `reason` — the FE renders dark-with-reason; nothing is
silent.

**FE path:** page polls `snapshot` (10 s, Overview stat cards) / `series` (15 s, Charts tab,
visible-gated) / `families` (manual, Families tab) with `Accept: application/json` (JSON_HEADERS
precedent, `mcps-gateway.service.ts:67`); models mirror the envelope verbatim; error banner +
retry retaining last-good; supersede tokens on concurrent polls (timeline-tab precedent).

**Grafana path (deferred #3, anticipation-only):** Prometheus scrapes the unchanged
`/api/metrics` text exposition; the query API plays no role. Operator enables the listener via
managed key (≤5 s TTL).

**Agent-tool path (deferred #2, anticipation-only):** a future `daemon/tools/metrics_read.py`
imports the provider functions directly (same substrate as the routes — the ONE DOOR has two
doorsills: HTTP and tool factory).

## Seam Inventory (anchors verified in worktree)

| # | Seam | Anchor | Used by | Read/mutate |
|---|---|---|---|---|
| S1 | Published snapshots | `checkpoint_metrics.py:902-904` (`published_snapshot`), publish `:868-882`, cadence `:128` | sampler, provider | read-only, lock-free |
| S2 | Registry introspection | registered objects (name/kind/label_keys/allowed_values/buckets), `known_label_keys()` `:392-398`, render helpers `:1319-1336`, `:1394-1409` | families endpoint, query quantiles | read-only |
| S3 | Resource spine counters | `resource_monitor/flusher.py:161` FlusherCounters; `sampler.py` SamplerCounters; canonical-counters rule CAT F0.3 | F0.3 wiring | read-only pull |
| S4 | Loop-lag quantiles | `wedge_capture/lag_probe.py:107-116, :185` | F1 wiring | read-only pull (dark when capture off) |
| S5 | Thread CPU jiffies | `wedge_capture/sampler.py:151-192` | F2 wiring | read-only pull (Darwin-dark) |
| S6 | Wedge capture counters | `wedge_capture/triggers.py:202/:237/:281` + `watchdog.py:417-418` | F3 wiring | read-only pull |
| S7 | Pool state samples | `readiness.py:525/:552`; `wedge_capture/collector.py:19-29`; published `/readyz` detail.pool_state `api.py:5015` | F4/F5 wiring | read-only pull |
| S8 | Census snapshot | `metrics_census.py` (frozen CensusSnapshot dataclass; "deliberately registers NO metric families"; sampler `api.py:2454-2483`, gated by switch `metrics_census.py:335-340`) | F6 wiring + availability facts | read-only pull |
| S9 | Readiness composite | published `api.py:5015` (`detail.component_classes/grace/leak_signal/failure_streak`); taxonomy `readiness.py:64-79` | F9 wiring | read-only pull |
| S10 | Queue admission counts | `job_queue_items.admission_state` (`:40-43`) + repository count seams | F10 wiring | read-only pull |
| S11 | Worker pool stats | `worker_pool.py:1518` get_stats (+ `:1537`); busy-derivation `instance_read.py:2092`; heartbeat `task/models.py:284`, cadence `worker_pool.py:45` | F11 wiring | read-only pull |
| S12 | Sweep counters() | `watch_reconcile_sweep.py:356`, `eligible_pending_sweep.py:404`, `orphan_watcher_sweep.py:260` + RDRS lane counters | F12 wiring (metrics-layer accumulation) | read-only pull |
| S13 | Instance capacity summary | `instance_read.py:2019` `_build_capacity_summary` | F13 wiring | read-only pull |
| S14 | MCP quarantine snapshot | `mcp/init_guard.py:145-147`; published `/readyz` detail.mcp `api.py:3840-3878`; consumer `mcp_servers.py:302` | F17 wiring | read-only pull |
| S15 | Gateway store | `mcps_gateway/store.py:79/:103`; `tombstones.py`; audit table (migration 20261022_000001; outcomes `audit.py:146`) | F18 wiring | read-only pull |
| S16 | Journal twin accessors | `tools/upgrade_journal.py:159/:165/:255` | F21 wiring | read-only pull |
| S17 | Saver-op bytes param | `checkpoint_perf.py:148` (`bytes_=`) at sole production call `persistence.py:632` | F7 completion | wiring (existing observe gains real value) |
| S18 | Router family | `daemon/routers/metrics.py` (prefix `/metrics` :59; scope `:70/:113`; fail-closed `:91-107`); mount `api.py:5278` | query routes | extension, same family |
| S19 | Managed keys | `config.py:774-850` (seven keys); `metrics_sampler_interval_s` `:825-833` (UNWIRED today — grep confirms settings-registry spec only, `settings_service.py:545-550`) | history sampler cadence | config read (boot-applied) |
| S20 | Cages | class manifest `tests/integration/test_router_class_manifest.py:385` (33); scope manifest `tests/unit/test_router_scope_manifest.py:161-169` (`"/api/metrics": "maintenance:read"`) + `:298-300` (metrics_router route-count 34) | query routes | pin bumps only |

## Design Rulings (summary — full contract in decisions.md)

- **D1 activation posture**: surface-class posture. Listener stays switch-gated default-OFF
  (the one unauthenticated surface); `/api/metrics` and the new query routes are auth/scope-only
  (`maintenance:read`, 503 fail-closed); the query API does NOT consult
  `ENSEMBLE_METRICS_ENABLED`; NO config default changes ship. Emergency-off semantics stay
  narrow; FE page works immediately; listener enabling remains a live managed-key flip.
- **D2 FE charting**: zero-dependency generalization of the svg-timeline inline-SVG primitive
  into a shared chart component family; pure scale functions unit-tested; feature-frozen shape
  (lines/areas/stat cards/native tooltips; no zoom/brush). No first-ever charting dependency;
  the d3 mermaid-transitive is off-limits; Grafana (deferred #3) bounds the sophistication need.
- **D3 storage/retention**: in-RAM ring buffer owned by a dedicated query-layer sampler thread;
  cadence = reused `limits.metrics_sampler_interval_s` (default 10 s, unwired key);
  window 2 h; adaptive depth clamped by a 750k-point (≈48 MB worst-case) budget; per-series
  `deque(maxlen)` eviction; identity-dedupe against copy-on-publish; lost-on-restart BY DESIGN
  (Prometheus TSDB owns long history; resource_monitor PG tier owns per-instance views —
  decision 4 split preserved). Zero migrations.
- **D4 query API**: three JSON routes UNDER the existing `/api/metrics` family (families /
  snapshot / series) — api_families stays 33; scope-manifest route-count pin bumps 34→37;
  envelope carries `schema_version` + mirrored `vocabulary_version`; provider protocol
  (`families()/snapshot()/history()`) reads ONLY published state (read-only enforced by pin);
  backend swap = provider replacement under frozen routes; NOT Prometheus-compatible by design;
  degradation = availability/staleness metadata (lock-contention structurally impossible on
  this path; census/Darwin/capture-off darkness reported per family with reasons).
- **D5 extension subset**: 17 of 26 families wired (10 full + 7 partial), ≈+295 series
  (catalog-quoted) → ≈384 registered post-lane (≈19% of budget); F7 op-enum narrowing folded
  (VOCABULARY_VERSION 1→2); no in-scope family needs the bucket fix; commission-OUT list and
  per-family adjudications in decisions.md D5 table.

## Phase-Alignment Preview (tiered-gate doctrine)

Mechanical trigger list (gate doctrine §3): `daemon/api.py` wiring escalates its gate leg to
**Tier 2** (whole-tree dual-side, ≤90 min advisory). Additive new-module phases are the
canonical D1/D2 Tier-1 shape (lane-owned HEAD-only pins + dual-side comparable set).

| Phase | Work cluster | Files touched | Gate leg | Tier |
|---|---|---|---|---|
| **Q — query substrate** | provider protocol + history sampler + ring buffer + F7 op-enum narrowing (registry-side introspection + `checkpoint_metrics.py:1011` allowed_values + VOCABULARY_VERSION 1→2) | new `daemon/services/metrics_query/*` + `checkpoint_metrics.py` + `routers/metrics.py` + `api.py` lifespan wiring | single whole-tree leg (api.py wiring) | **Tier 2** |
| **W1 — P1a-order wiring** | F0.3, F1, F2(last), F3, F4, F5, F6 (census registration) | new wiring module(s) + `metrics_census.py` (additive registration) | additive D1/D2 | Tier 1 |
| **W2 — P1b-order wiring** | F9, F10, F12 (+C17 docstring docs-fix), F11-partial, F13-partial, F7-completion, F8-partial | wiring module(s) only (producers untouched except the docs line) | additive D1/D2 | Tier 1 |
| **W3 — P1c-order wiring** | F17, F18-partial, F21-partial | wiring module(s) only | additive D1/D2 | Tier 1 |
| **F — FE page** | route + gear item + service + models + chart primitive + 3 tabs + specs + playwright e2e + ordering pins | `frontend/src/app/*` only | FE pack + e2e | Tier 1 (Tier 0 docs if staged separately) |
| **X — cages & pins** | scope-manifest route-count 34→37; class-manifest untouched (33); registry pins extended for narrowing (45-node registry pack + 18-node vocabulary pack re-derivation) | tests only | changed-pack PASS | Tier 0/1 |

Notes:
- The scope-manifest cage update rides the same commit as the routes (cage and code never
  drift); the pin bump is mechanical (family scope already pinned `maintenance:read` — no
  scope-set amendment needed since all three routes carry the pinned scope).
- W-phases must ship family clusters so the vocabulary test packs (frozen sets re-derived from
  runtime sources) can re-derive per cluster — one cluster per pack run, never all-at-once.
- The FE phase includes the mandatory `ng build --configuration development` AOT leg after
  template-touching commits (no repo-level AOT gate — NG8002/NG0303 escape tsc+jest).
- Test-pack extension points (research 1e/1f): `test_checkpoint_metrics_registry.py` (45),
  `test_checkpoint_metrics_vocabulary.py` (18), `test_metrics_listener_registry.py` (15) for
  exposition-integrated families; new packs for sampler/ring/provider; `test_metrics_route.py`
  for the three routes (route exists, auth-off 503, scope 403, auth-ON 401, switch-OFF
  invariance pin).

## Risk Register (top risks)

| # | Risk | Likelihood | Impact | Mitigation / disposition |
|---|---|---|---|---|
| R1 | api_families manifest bump churn | Avoided by design | — | Query routes extend the EXISTING family; class-manifest stays 33. Residual: none unless council demands a separate family (then: one-line bump, 32→33 precedent documented in the test comment). |
| R2 | Scope-cage regression (new routes undeclared or mis-scoped) | Medium | Gate red (loud) | Routes carry the pinned `maintenance:read`; route-count pin bumped same-commit; negative test: undeclared-mount refusal already proven by the rogue-router leg. |
| R3 | AOT-only template errors (no repo AOT gate) | Medium | Broken page found late | Mandatory dev-build leg per template-touching commit; FE phase exit includes it. |
| R4 | Kill-switch truth-table regressions | Low | Silent semantic drift | ZERO new consultation sites (D1); add a pin: query routes serve identical data under switch ON and OFF (invariance pin); existing 35 gate + 29 listener + 34 census tests untouched. |
| R5 | FE bundle/style budget | Low | Build warn/error | No new deps (D2); shared primitive (no duplication); per-component SCSS under 8 kB warn; chart colors from existing palette. |
| R6 | Cardinality drift from new families | Low | Cap rejections (visible, counted) | +295 catalog-quoted toward a 2000 cap; registry cap + `ensemble_metrics_cap_rejected_total` enforce; families endpoint surfaces `live_series_count` so drift is observable per family. |
| R7 | History-buffer memory underestimate at fully-wired ceiling | Medium | Memory pressure | Adaptive depth clamp (POINT_BUDGET // live_series, floor 60) — depth shrinks before memory grows; ≈48 MB worst case stated; identity-dedupe reduces steady state. |
| R8 | Sampler-thread lifecycle bugs (wedge/shutdown) | Medium | Stranded thread / lost data | Daemon thread + detached-join discipline (listener `stop()` precedent); sampler reads published refs only — a wedged loop cannot block it; lifespan teardown ordering pinned in a wiring test (A27/api-wiring precedent). |
| R9 | F18 audit-vocabulary drift (2 vs 4 live outcomes) | Certain (found) | Wrong label set if folded silently | Routed as open question #1: plan amends CAT §8.10 to the live set (+VOCABULARY_VERSION ride-along) or folds explicitly — never silently. |
| R10 | F2 ships series that are permanently dark on prod (Darwin) | Certain | Perceived dead code | Disclosure (D5): family is in-scope per the mechanical rule, renders absence on macOS (catalog §13 documented); sequenced last; FE renders dark-with-reason rather than zero. |
| R11 | Registry/`checkpoint_adapter.py`/`readiness.py` registry-adjacent drift | Low | Tier escalation surprise | This lane EDITS none of them (pull seams are read-only imports); only `checkpoint_metrics.py` introspection + `metrics_census.py` additive registration — both Tier-1-scoped, bundled under Q's Tier-2 leg where api.py is touched anyway. |
| R12 | Producer hot-loop cost of F12 accumulation | Low | Loop-lag regression | Accumulation lives in the metrics pull layer (reads `counters()` dicts — no producer edits, no hot-path code added); sampler cadence 10 s bounds read frequency; registry observe paths untouched. |

## Anticipation-Only Hooks (deferred items — NO implementation in this lane)

1. **Agent metrics tools (deferred #2)**: the `MetricsQueryProvider` is importable,
   side-effect-free, and reads only published state — a future `daemon/tools/metrics_read.py`
   (maintenance/devops-class agents hold `maintenance:read` for the HTTP door; the tool lane
   binds its own category gate) calls the SAME provider functions. Hook contract: provider
   stays HTTP-agnostic (no Request/FastAPI types in its signatures); routes are thin adapters.
   The inventory's load-bearing negative finding (zero native metrics reads for agents) closes
   at THAT commission, not this one.
2. **Grafana stack (deferred #3)**: exposition contract unchanged (Prometheus classic text, MA-16;
   `server_version="ensemble-metrics/1.0"`); listener enabling stays a managed-key flip. Hook
   contract: the query API MUST NOT grow Prometheus-compatibility surfaces (it complements);
   never break the exposition format; never move the listener to the main app (decision 3).
3. **Write-seam commission (J-4)**: `_charge_checkpoint_write` with its own budget/labels and
   the bucket-fix for `DEFAULT_LATENCY_BUCKETS` residuals — this lane folds ONLY the interim
   enum narrowing; the hook is the narrowed enum + VOCABULARY_VERSION 2, which the write
   commission bumps again when write ops become observable.

## Open Questions

Carried in decisions.md (Open questions): F18 audit-vocabulary amendment vs fold; F21
phase-family pure-read conditionality (+30 series); F2 sequencing given prod-Darwin darkness;
sampler-key reuse vs dedicated constant. None block planning; all four have recommendations.

## References

- Research: `.agents/shared/planning/central-metrics-v1/research-findings.md` (wave-0, three
  explorer reports; all registry/kill-switch/gate/FE/inventory anchors).
- Catalog (authoritative names/labels/counts): `.agents/shared/planning/metrics-collection/metric-catalog.md`
  §1 (name registry), §8 (vocabularies), §9 (census discipline), §13 (platform matrix), §14
  (reconciled counts ledger — M-2 pointer target for every series number in this document).
- Ratified decisions: `.agents/shared/planning/metrics-collection/decisions.md` §ACTIVE
  (§D rows 1–5 leader-ratified; J-4 write-seam; G-6 claim-latency; G-11 queue durations; MA-4/
  MA-5/MA-2/CR-4 lineage).
- Plan: `.agents/shared/planning/metrics-collection/plan-overview.md` (§3 architecture,
  §4 phases, §11 non-goals incl. per-child Darwin non-goal).
- Code anchors: as cited per row above (verified in worktree `/home/nea/ensemble-src-wt-central-metrics-v1`,
  branch `feature/central-metrics-v1`, base == origin/latest).
- Gate doctrine: `.agents/tester/skills/` canon + research findings §3 (Tier-2 mechanical
  trigger list; D1/D2 additive-lane shape).
