# Phase 4: Explainable Anomaly Layer + Evidence (arch §2.7)

Plan for commission `resource-cost-control`, slice 4 of 8.
Primary spec: `architecture-recommendation.md` §2.7 (anomaly layer) + §2.8 (API rows `GET /api/resources/anomalies`, `GET /api/resources/anomalies/{id}/evidence`). Architecture is ADJUDICATED — this file turns it into implementer-ready tasks; no redesign.
Date: 2026-10-18 · Author: plan-creation worker (via planner) · Status: Ready for Review

---

## Goal

Turn the resource time series into EXPLAINABLE findings: an in-daemon background pass (every 5-15 min) that consumes ONLY time-bucketed data, detects `spike | ramp | leak | step` shapes with baselines conditioned on `(n_active_workers, day-of-week, hour-of-day)` — so "5 workers ⇒ higher RSS" is never a false positive — and exposes append-only findings via `/api/resources/anomalies` plus a fully-derivable evidence payload per finding. No black-box scores: every flag carries `{baseline, observed, deviation, contributors}`.

## Structural decisions (binding, from the adjudicated arch)

1. **NO new table, NO migration in this phase.** §2.4 defines exactly 4 resource tables; findings are NOT among them. §2.7 rules findings are **append-only and recomputed from the series on restart** — derived data, held in a bounded in-RAM journal. Consequence to document in code: **finding ids are process-local and change across restart**; consumers (FE, 8 s poll) re-fetch; this is accepted behavior, not a defect.
2. **Consumes ONLY bucketed series — never raw rows.** The pass reads the monitor's own bucketed series accessor (minute resolution; the in-RAM rings from P1 are the primary store, DB minute-tier rows warm the pass on cold boot). Raw 30 s rows are never scanned by the anomaly pass.
3. **Thread model unchanged (§2.1: ONE sampler + ONE flusher).** The pass piggybacks the FLUSHER thread behind a monotonic-interval gate (`anomaly_interval_s`); it adds no thread. If a pass measures over its self-cost budget it skips its cycle (skip-on-overrun, mirroring §2.1) — sampling is never delayed.
4. **Findings feed the P3 traceback contract:** P3 reserved `findings: []` in the `/traceback` response; task 9 of this phase fills it.
5. **(2026-10-18 currency) NO migration/table impact confirmed:** d0b475466's `app_settings` table is PRE-EXISTING infrastructure — this phase adds no table and no migration (unchanged); the managed setting (`limits.llm_concurrency`) is consumed read-only via the resolver ladder (`daemon/services/settings_service.py`), never written by the anomaly pass.

## Deliverables

| # | Path | New/Existing | Content |
|---|---|---|---|
| D1 | `daemon/services/resource_anomaly.py` | NEW | Series-state store, baseline engine (conditioned), detectors (MAD-z / EWMA / CUSUM / change-point), shape classifier, bounded findings journal, restart-recompute, pass scheduler hook |
| D2 | `daemon/services/resource_anomaly.py` (same module) | — | Config consumption from the P1 `resource_monitor` block (§2.9) + the new anomaly keys below |
| D3 | `daemon/routers/resources.py` (P1-created) | EXTEND | `GET /anomalies?window=&limit=` and `GET /anomalies/{id}/evidence` handlers (thin, `asyncio.to_thread`, degrade-not-503 per snapshots DI precedent) |
| D4 | P1's schema module for `/api/resources` | EXTEND | `AnomaliesResponse`, `EvidenceResponse` — element shape byte-for-byte per §2.7/§2.8 |
| D5 | `daemon/services/resource_traceback.py` (P3) | EXTEND (1 line-ish) | `findings[]` populated from the journal instead of `[]` |
| D6 | `tests/unit/services/test_resource_anomaly.py` | NEW | Detectors, conditioning, bounds, restart-recompute |
| D7 | `tests/unit/routers/test_resources_anomalies.py` | NEW | HTTP contract: list shape, evidence shape, unknown-id 404, degrade path |

**Config keys** (added to the P1 `resource_monitor` block / `ResourceMonitorConfig`, env prefix `RESOURCE_MONITOR_`, restart-to-flip per the `LIMITS_*` convention — extends §2.9, re-opens no ruling):

| Key | Default | Env | Note |
|---|---|---|---|
| `anomaly_enabled` | `true` | `RESOURCE_MONITOR_ANOMALY_ENABLED` | kill-switch; `false` → pass silent, endpoints return empty lists |
| `anomaly_interval_s` | `600` (`ge=300`, `le=900`) | `RESOURCE_MONITOR_ANOMALY_INTERVAL_S` | the 5-15 min window |
| `anomaly_mad_threshold` | `6.0` | `RESOURCE_MONITOR_ANOMALY_MAD_THRESHOLD` | robust-z flag threshold |
| `anomaly_min_baseline_points` | `12` | `RESOURCE_MONITOR_ANOMALY_MIN_BASELINE_POINTS` | cold-start guard: pass stays quiet until ≥12 buckets (=1 h @ 5 min) of baseline |

## Algorithms (§2.7 verbatim, concretized)

- **Detectors:** robust z via **MAD** (median absolute deviation) for single-bucket outliers; **EWMA** for smoothed drift; **CUSUM** (two-sided, `cusum⁺`/`cusum⁻`) for sustained shift; **change-point** detection (post-window vs pre-window level test) for steps. A finding cites exactly ONE `deviation` metric: `mad_z` or `cusum_k` (whichever fired), with its `value` and `threshold`.
- **Shape classification** (deterministic rules over the detector outputs, ordered):
  - `spike` — single-bucket `mad_z ≥ threshold`, returns to baseline within ≤2 buckets
  - `step` — change-point: post-window median > pre-window median + k·MAD, sustained ≥ W buckets
  - `ramp` — EWMA slope positive over ≥ k consecutive buckets, partial recovery present
  - `leak` — monotonic non-recovering upward drift across the full state window (CUSUM⁺ persistent, zero recovery buckets) — the RAM-leak fingerprint
- **Baselines (the critical conditioning):** two methods, both stored on the finding:
  - `rolling_median` — trailing median over the retention window, **computed per `(series, n_active_workers)` stratum**
  - `dow_hour_mean` — day-of-week × hour-of-day mean, **keyed `(series, n_active_workers, dow, hour)`**
  - `n_active_workers` = live worker-pool size (`daemon/services/worker_pool.py:1252` `num_workers`/started count, read at pass time). A worker-count change moves the series to a DIFFERENT baseline stratum → the shift re-baselines instead of flagging. Rationale (§2.7, both councilors): without conditioning, every worker-pool scale-up and every morning load pattern becomes a false positive, training users to ignore the page.
  - **Configured-limit vs observed-count distinction (2026-10-18 currency, d0b475466):** the **configured** LLM worker limit is now a MANAGED SETTING — `limits.llm_concurrency`, resolution ladder `DB override > os.environ > yaml literal > registry default` (`daemon/services/settings_service.py`; table `daemon/models/app_settings.py`). Treat the two as DISTINCT inputs: the **configured limit** is a baseline *dimension* (read from app_settings/limits at pass time), while the **observed active-worker count** (`worker_pool.py:1252`, sampled) is the stratum key actually conditioned on. When the configured limit changes (operator sets a managed value → restart), the resulting RSS shift is CONFIGURATION-DRIVEN and must NOT be flagged — the conditioning treats a configured-limit change as a stratum change (re-baseline), never as an anomaly signal. Test note: the mandated FP-avoidance test (task 5) gains a configured-limit-change variant (limit 4→6, observed count follows, RSS rises ⇒ NO finding; identical rise at constant limit+count ⇒ finding fires).
- **Severity:** deterministic banding of `deviation.value / threshold` (e.g. `<2 low, <4 medium, ≥4 high`) — a fixed mapping in code, documented; no model, no score.

## Bounded-state arithmetic (§2.7 — repeated as the ACCEPTANCE BOUND)

Per series, keep last **4 h @ 5 min = 48 buckets** × 6 floats (`value, baseline, ewma, cusum⁺, cusum⁻, mad`) × 8 B = 48×6×8 = **2,304 B ≈ 2.3 KB/series**.
Cardinality ≈ **6 process metrics + MCP pools + ≤100 instances × 2 series ≈ ~211 series** ⇒ 211 × 2.3 KB ≈ **485 KB**.
Daily DoW×hour baseline: 7×24×211×8 B ≈ **284 KB**.
**Total ≈ 769 KB < 1 MB — the acceptance bound** (task 1 test computes this from the configured caps and asserts `< 1_048_576` bytes).
Findings journal (separate, explicitly bounded): `deque(maxlen=256)` per-process, ≈ ≤ 256 KB worst case at ~1 KB/finding — asserted by the same bounds test as a second bound.

## Explainability payload — EXACTLY per §2.7 (no additions, no black-box scores)

```
finding_id, time_bucket, series_key
shape: "spike" | "ramp" | "leak" | "step"
baseline:  {method: "rolling_median" | "dow_hour_mean", value, n}
observed:  <float>
deviation: {metric: "mad_z" | "cusum_k", value, threshold}
contributors: [{instance_id, agent_id, work_id?,
                share_pct,
                evidence: {samples, first_seen,
                           task_status: {status, worker_id}}}]
```

Every finding carries ALL of it. Contributors are ranked by share of the deviation and resolved through the **P3 join layer** (`resource_traceback`): which instances were charged in the window (T2 `(instance_id, bucket_ts)`), which children ran (T3 `(kind, spawned_at)` — `chart_render` suspected first), each contributor's `task_status` via the J2/J3 task join. `share_pct` sums to ≤100; unattributable share is reported as an explicit `unattributed` remainder row (honesty rule §2.2 carries into §2.7).

## Task breakdown

| # | Task | Depends on | Acceptance |
|---|---|---|---|
| 1 | Series-state store: per-series 48-bucket ring + 6-float state struct + cardinality cap (config-driven, default ≈211) + DoW×hour baseline map + `state_bytes()` accounting; bounds test asserting the §2.7 arithmetic (`< 1 MB` series+baseline; findings journal `< 256 KB`) | none | bounds test green with numbers printed in the assert message; over-cap series registration is refused with drop-oldest semantics |
| 2 | Baseline engine: `rolling_median` (per worker-stratum) + `dow_hour_mean` keyed `(series, workers, dow, hour)`; cold-start quiet until `anomaly_min_baseline_points` | 1 | unit tests: baseline value correct on synthetic series; stratum switch on worker-count change produces a NEW baseline (no carry-over); <12 points → engine emits NO findings (quiet, not error) |
| 3 | Detectors: MAD-z, EWMA, CUSUM⁺/⁻, change-point — pure functions over the bucket ring | 1 | unit tests per detector: synthetic series crossing each threshold fires; identical series under threshold stays silent; NaN/degenerate series (flat, all-zero) handled without exception |
| 4 | Shape classifier: `spike`/`step`/`ramp`/`leak` rules + deterministic severity banding | 3 | unit tests: one synthetic series per shape classified correctly + negative controls (a spike misclassified as leak fails); unknown combination → conservative `spike` fallback with WARN log |
| 5 | **Mandated FP-avoidance test (critical):** series A — worker count 4→5, RSS rises 30%: assert NO finding (conditioned baseline absorbs it). Series B — identical magnitude rise at CONSTANT worker count: assert finding fires with the same detectors/thresholds. **Variant C (2026-10-18 currency, d0b475466):** configured limit `limits.llm_concurrency` 4→6 (managed setting via app_settings/limits), observed count follows, RSS rises: assert NO finding — a configuration-driven shift re-baselines, never flags | 2-4 | assertions A/B/C green in one named test (`test_worker_count_increase_is_not_anomaly`, incl. the configured-limit variant); this test is the acceptance gate for the conditioning rationale |
| 6 | Anomaly pass + scheduling: monotonic-interval gate on the FLUSHER thread (no new thread); pass duration measured; over budget → skip cycle + `missed_anomaly_cycles` counter; `anomaly_enabled=false` → inert; findings appended to the bounded journal (drop-oldest) | 2-5 | tests: interval gate fires at `anomaly_interval_s`; skip-on-overrun path logs + increments counter; kill-switch produces zero findings and empty endpoint lists; journal never exceeds maxlen |
| 7 | Restart-recompute: on monitor boot, replay retained minute-tier bucketed history through the detector pipeline to repopulate the journal (cold start warm via DB minute-tier read; in-RAM rings primary thereafter) | 6 | test: simulate series → findings; "restart" (new state + replay) → equivalent findings regenerated from the series (ids differ — assert shape/baseline/deviation equality, id-insensitive) |
| 8 | Router endpoints: `GET /anomalies?window=&limit=` (lean list per §2.8 row: `{anomalies:[{id,kind,t_start,t_end,peak,baseline,severity,series_key,shape,instance_id?}]}`) + `GET /anomalies/{id}/evidence` (`{anomaly, samples, top_pids_per_bucket, children_delta, contributors}`); evidence re-derives from the retained bucket window + T2/T3 reads; unknown id → 404; internal failure → empty-shape 200 (list) / 404 (evidence), never 503; `asyncio.to_thread` bridge | 6-7 | router tests: list shape exact-key; `limit` clamp; evidence for a seeded finding returns ≥1 sample + ≥1 contributor; unknown id → 404; degrade path 200 |
| 9 | Contributors via P3 join + wire `findings[]` into `/traceback` (P3 reserved key) + full-suite gate | 8 | test: seeded deviation resolves ≥1 contributor with `share_pct` + `evidence.task_status`; `/traceback` response now carries journal findings (shape unchanged from P3 contract); full pytest green |

9 tasks ✓ (3-10 range), each independently completable.

## Acceptance criteria (with exact gate commands)

From the worktree `/home/nea/ensemble-src-wt-resource-cost-control`, dev-checkout venv (as P3):

```bash
cd /home/nea/ensemble-src-wt-resource-cost-control && python -m pytest tests/unit/services/test_resource_anomaly.py tests/unit/routers/test_resources_anomalies.py -q
# (run from the worktree root with the worktree's own venv — `uv sync` if absent; never the main checkout's editable `.venv`, which imports the wrong branch's daemon)
# → all pass, 0 skipped; MUST include test_worker_count_increase_is_not_anomaly

cd /home/nea/ensemble-src-wt-resource-cost-control && python -m pytest -q
# (run from the worktree root with the worktree's own venv — `uv sync` if absent; never the main checkout's editable `.venv`, which imports the wrong branch's daemon)
# → FULL suite green (worktree GREEN at phase boundary)
```

| # | Criterion | How to measure | Threshold |
|---|---|---|---|
| A1 | Bounded state < 1 MB | bounds test (task 1) computing series+baseline bytes from configured caps | `< 1_048_576` bytes; findings journal `< 262_144` bytes |
| A2 | Conditioning prevents the canonical FP | mandated test (task 5) | worker-count rise NOT flagged; identical rise at constant workers IS flagged; configured-limit change (managed `limits.llm_concurrency` flip → observed count follows → RSS shift) NOT flagged |
| A3 | All 4 shapes detected + classified | per-shape tests (tasks 3-4) | ≥1 positive per shape + negative controls; zero false classifications on the control set |
| A4 | Explainability completeness | schema/contract tests (task 8) | every finding carries `{baseline, observed, deviation, contributors}` — a finding missing any key fails the contract test |
| A5 | Restart durability of findings | recompute test (task 7) | post-restart findings match pre-restart on (shape, baseline, observed, deviation) — id-insensitive |
| A6 | Pass never harms the loop or sampling | self-cost assertions (task 6) | pass runs on flusher thread only; skip-on-overrun tested; `missed_anomaly_cycles` exposed; sample cadence unchanged in tests |
| A7 | API contract + degrade | router tests (task 8) | exact §2.8 list/evidence shapes; unknown evidence id → 404; forced exception → 200 empty (list); no 503 anywhere |
| A8 | Traceback contract preserved | P3 contract test still green (task 9) | `findings[]` filled WITHOUT shape change to the other 5 keys |
| A9 | No regression / no live mutation | full pytest + review | full suite exit 0; zero live-host mutations; all fixtures synthetic (fenced `tmp_path`, never live-shaped paths — incident-class 2026-10-12) |

## Coupling

- **Tight with P1** — consumes the bucketed series accessor + minute-tier tables + the `resource_monitor` config block (this phase EXTENDS it with 4 keys) + the router skeleton/schema module. Bucket cadence change (5-min assumption in the 48-bucket arithmetic) invalidates the bounds constant → P1 must keep minute-bucketing stable or this phase's bound test must read the cadence from config (spec: bound test derives bucket count from config, not a literal 48).
- **Tight with P2** — contributor identity (`instance_id, agent_id`) and child attribution (`chart_render` evidence, `top_pids_per_bucket`, `children_delta`) come from T2/T3 rows.
- **Tight with P3** — contributors resolve through `resource_traceback` (J2/J3/J5/J6); this phase fills the reserved `findings[]` key.
- **Loose with P5** — FE timeline consumes `/anomalies` markers + evidence drill-down; click → evidence + `/traceback?t0=&t1=`. Finding-id instability across daemon restart is accepted behavior FE must tolerate (re-fetch on poll) — noted for P5 planning.
- **Independent of** P6/P7/P8.

## Risks + rollback

| # | Risk | Impact | Likelihood | Mitigation |
|---|---|---|---|---|
| R1 | 🔴 False-positive storm (conditioning wrong or incomplete strata) | High — page fatigue, subsystem ignored | Medium | mandated conditioning test (A2) + `anomaly_min_baseline_points` cold-start quiet + deterministic severity bands + `anomaly_enabled` kill-switch |
| R2 | 🔴 Self-cost overrun — pass becomes the problem | High — violates the §2.8 budget | Low | runs on flusher thread behind interval gate; duration measured; skip-on-overrun + counter; state provably < 1 MB (A1); no DB scans of raw rows |
| R3 | 🟡 Restart-recompute drift (findings differ after replay) | Med — confusing UX, distrust | Medium | replay is deterministic over the same bucketed series (A5 asserts equality on stable fields); id instability documented in module docstring + listed for P5 |
| R4 | 🟡 Detector pathology on degenerate series (flat/NaN/short) | Med — crash loop in background pass | Medium | pure-function detectors with degenerate-input tests (task 3); pass-level try/except per series — one bad series never kills the pass |
| R5 | 🟡 Baseline memory semantics vs naive-UTC buckets | Low-Med — DoW/hour miskeyed | Low | the pass keys DoW/hour from the bucket index/derivation of `bucket_ts` (naive-UTC digits — UTC hour is the documented convention); any wallclock math still routes through `timestamps.coerce_to_aware_utc`; TZ-discipline test carried over from P3's seam |
| R6 | 🟢 P3 not yet merged when P4 starts | Low — contributors unavailable | Medium | hard dependency: P4 task 9 requires P3; earlier tasks (1-7) are join-independent and may proceed |

**Rollback:** additive-only module + endpoints; zero migrations (findings are derived, in-RAM). `RESOURCE_MONITOR_ANOMALY_ENABLED=false` disables the pass and empties the endpoints with no restart of sampling. Full revert of the phase commit restores the P3 state exactly (`findings: []`). No data at risk — findings are recomputable by design.

## Dependencies

- **Blocked by:** P1 (bucketed series + minute tier + config block + router skeleton) and P2 (T2 `agent_id` enrichment, T3 pid rows for evidence). Tasks 1-7 additionally need P3 only for task 9; tasks 8-9 need P3's join layer for contributors.
- **Blocks:** P5 FE (anomaly markers on the timeline tab ← `/anomalies`; marker click → evidence + traceback drill-down). P6/P7/P8 unaffected.
- **Exit criterion:** A1-A9 green, full suite green, phase commit independently mergeable with the worktree GREEN; `/anomalies` populated from live series on a dev-boot smoke (observation only — ZERO live-host mutations per the standing ruling).

## Verified anchors appendix (read-only, this session)

- conditioning key: `daemon/services/worker_pool.py:1252` `num_workers` (live worker count source)
- bucket/series storage + roll-down: arch §2.4 T1/T2 + §2.5 (P1-delivered; minute tier = pass input)
- normalization: `daemon/services/timestamps.py` `coerce_to_aware_utc` (assume-UTC policy) — reused, not reimplemented
- naive-UTC buckets / aware events split: `daemon/repositories/task/models.py:273-275`, `daemon/repositories/event/models.py:80` (same hazard as P3 — carried discipline)
- router/DI/degrade precedent: `daemon/routers/snapshots.py:16-31`, re-export `daemon/routers/__init__.py:26`, mount `daemon/api.py:3894`
- **(2026-10-18 currency re-verification @ `8b8b32dcc`:)** d0b475466 shifted `api.py` — router mount now `:4094` (`api_router.include_router(snapshots_router)`; `app.include_router(api_router)` at `:4157`); `worker_pool.py:1252` conditioning key CONFIRMED unchanged; `limits.llm_concurrency` managed-setting ladder (d0b475466) = the configured-limit dimension referenced above; P4 adds no table/migration (`app_settings` pre-existing)
- no-5th-table basis: arch §2.4 (4 tables) + §2.7 (append-only, restart-recompute); retention rulings (48 h/14 d/180 d, knob-tunable) bind the replay window
