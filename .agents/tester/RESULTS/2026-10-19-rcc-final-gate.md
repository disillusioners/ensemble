# Final Gate Report — resource-cost-control (P1–P8 whole-tree integration gate)

**Date:** 2026-10-19 · **Tester instance:** 6b1630e7 mission · **Worktree:** `/home/nea/ensemble-src-wt-resource-cost-control` · **Branch:** `feature/resource-cost-control`
**Tree evaluated:** implementation @ `a385c102f` (P6 tip, all P1–P8+O-optimizations ancestors-verified) → FE-fix `dae97a500` (frontend-only, +367/−36) landed mid-gate; FE gates re-run post-fix
**Base for attribution:** `8b8b32dcc` (branch point; `latest` = 18 commits ahead, several stale-test repairs live there)
**Runner:** `/home/nea/ensemble-src/.venv/bin/python` throughout; worktree `.env` fences `ENSEMBLE_SELF_ENV=dev` / `POSTGRES_DB=ensemble_dev` (verified); live daemon/DB (9797) NEVER touched; port 8088 never touched.

---

## VERDICT SUMMARY

| Gate | Verdict | One-line evidence |
|---|---|---|
| AC5 full suite | **PASS\*** | ~24.7K tests across 9 shard packs + make-ups; **0 commission production regressions**; 9 branch-caused test-infra reds (one family — pin/manifest/fixture follow-through); ~330 base-attributed pre-existing; 1 quarantined hang cell. \*conditional on the pin/manifest debt disposition (leader) |
| AC6 FE gates | **PASS** | tsc 0 / jest 4103 (4 known pre-existing + 1 tolerated flake) / build 0 — re-run green POST-FIX @ dae97a500 |
| AC7 self-cost | **PASS** | 8/8; budgets real+fenced; numbers recorded (below) |
| MUST-COVER (a) live-PG smoke | **FAIL (FINDING #1)** | C-1 fix VERIFIED on real PG; but `--api-key=…` secret-shaped flag tokens persist in `resource_pid_map.command` (W-2 gap) |
| MUST-COVER (b) tier coexistence | **PASS** | 42/42 + production-cutoff driver: 50h-old minute/hour rows survive the 48h raw sweep |
| MUST-COVER (c) honesty columns | **PASS** | clamps quoted in-file; blend=max discriminated by driver (9,000,270 vs sum 10,001,540) |
| MUST-COVER (d) no-raw-args | **PASS\*** | 42/42 pinned shapes + 0-leak driver on 5 needles; \*shares FINDING #1 (dash-flag shape unpinned) |
| MUST-COVER (e) degrade-never-503 | **PASS** | 18/18; empty-valid shapes quoted; 2 advisory coverage gaps (cross-router + engine-fault unpinned) |
| MUST-COVER (f) restart-replay FP | **PASS** | 45/45; recency + staleness ≤65min + zero-FP continuation asserted; mutation-proof anchored 9f70fa86c |
| FE web-automation | **PASS (post-fix)** | 3/3 @ dae97a500; degraded-shape defect CONFIRMED pre-fix → CLOSED post-fix (0 pageerror/console-error, 8× '—'); Z-ISO verbatim; lazy-bundle runtime-proof |
| ensure.md Core static | **PASS 2/2** | dev.sh graceful-shutdown grep; 9/9 call-sites awaited |

**Overall: the commission's functional surface is green everywhere it is pinned. Two production findings (FINDING #1 sanitizer gap; FINDING #2 PG txn-abort window-death, advisory) and one test-infra debt family (9 reds) go to the leader for disposition.**

---

## AC7 — Self-cost numbers (recorded at HEAD, fence-guarded driver `/tmp/rcc_selfcost_measure.py`, 3 runs)

| Case | median ms | max ms | p95 ms | Budget | Headroom |
|---|---|---|---|---|---|
| Attribution ON @ 70 instances + 4 children | **1.4794** | **1.9708** | 1.6642 | <5 / <50 | 3.4× / 25.4× |
| Attribution OFF (P1 baseline) | **0.4586** | **0.6028** | 0.5339 | <5 / <50 | 10.9× / 83× |
| RSSΔ across 200 ticks | **0.0000 MB** | (ru_maxrss high-water flat @ 11.25MB baseline) | | <20 MB | 20× |

- P2 baseline reproduced: median 1.4794 vs 1.482 ms (0.2%); max within run-variance (+12%).
- Budgets are REAL and FENCED: `assert median < 5.0` (:251/:521), `assert max_cost < 50.0` (:252/:522), `assert delta_mb < 20.0` (:372) + 12 ancillary invariants (escape-guard load-bearing :206, positive control :407-411, counters, ≤65 rows/tick, throttle-fires, post-throttle cheap rows).
- Design note: values are assertion-only (surface only on breach) — a dashboard artifact would need a print/logging addition (out of gate scope).
- Stability: 3 runs, median spread 2.4% (ON) / 1.6% (OFF).

## MUST-COVER evidence index

- **(a)** Throwaway PG16 `ensemble_gate_rcc` + role `rcc_gate` (sudo -u postgres; both dropped+verified). Schema via `SQLModel.metadata.create_all` (the PG source-of-truth per the SQLite-shaped SQL file's own header). Production chain driven: sanitize → PidRegistry.register/mark_exit → run_once() (rows_written=3) → read-back (3 rows, all columns). Generated SQL = PLAIN INSERT (no ON CONFLICT — C-1 fix visible on real PG). Missing-relation tolerated in isolation (42P01 → 0 + warn). Genuine bad SQL (drifted column → 42703) correctly NOT classified missing-relation and propagates. **Canary: `canary-abc123` (env-assign value), `__ENS_SECRET__` marker id, env-overlay value all ABSENT; `sk-REDACTEDCANARY123` (dash-flag `--api-key=`) PERSISTED → FINDING #1.**
- **(b)** `test_sweep_per_tier_predicates` (storage.py:559-641) seeds minute/hour rows >24h raw-floor cutoff, asserts survival. Driver at PRODUCTION cutoffs: 6 rows seeded (raw 50h/25h, minute 50h/15d, hour 50h/200d) → sweep deletes exactly raw>48h + minute>14d + hour>180d; `min_50h`+`hr_50h` survive (the F1 class). Per-tier predicate `AND interval_s = :interval_s` (flusher.py:528-585) is the fix.
- **(c)** unattributed_kb non-negative + clamp-0 (sampler:613/645/672); coverage ∈[0,1] incl. over-charge ⇒ exactly 1.0 (:614-616/:644-646, api:428); estimated NOT NULL (:707). Blend=max NOT discriminated in-file (deep=0 there) → driver: charged 1000270/deep 5000000 + 4000270/1000 ⇒ row 9,000,270 = max-form (sum would be 10,001,540). In-pack blend-max pins live at test_resource_deep_scan.py:248 + test_resource_charge_seams.py:453 (green in AC5 shards).
- **(d)** 42/42: argv KEY=value (:884-907), `__ENS_SECRET__` (:950-971), KMS LANE-1/2 (:973-986), head-of-line string (:909-935), fallback (:937-948); LOGS covered via caplog (:907/:935). API wire NOT pinned in tests → driver 3 drives × 5 needles: 0 leaks in rows/logs/wire. Flusher write-set (flusher.py:613-645): 15 columns, no args/env/tool_args column; T2 rows locked by ALLOWED_T2_ROW_KEYS (sampler.py:101-118).
- **(e)** 18/18. Degrade simulation = monitor facade absent/disabled (app_state default). /live + /self-cost 200 with documented empty shapes; /history 200 `resolution_used` always present; attribution zero-block exact. **Advisory gaps:** no test pins a NON-resource endpoint healthy under the same condition (architectural invariant, unpinned); no thrown-engine-exception simulation; traceback degrade-header pinned in tests/unit/routers/test_resources_traceback.py (out of pack scope, green in shards).
- **(f)** 45/45. `test_replay_from_db_uses_recent_window` (:964-1065): seeds 340 rows @1-min = 5.67h (>3.2h threshold); asserts SELECT == 192 (window_buckets*4), staleness ≤65min (old ASC shape = 340min → the mutation-proof pair in 9f70fa86c), continuation observe → `run_pass() == 0` + journal empty. Supporting: replay-equivalence, minute-tier, idempotency. File count today 45 vs P4's 66 = router tests relocated to their own pack (750d03361), not dropped.

## FE web-automation (pre-fix baseline → post-fix close-out)

- Pre-fix @ a385c102f: 2/2 gate green; degraded-shape drive **CONFIRMED the review's defect**: `TypeError: Cannot read properties of null (reading 'toFixed')` at `LiveTabComponent.fmt`, skeleton-not-blank, values missing.
- Post-fix @ dae97a500: 3/3 green. In-tree `[AC8]` spec (:263) asserts `pageErrors == []`, `blockingConsoleErrors == []`, ≥8 `—` in cgroup card, `—%`/`— MB` daemon stats, `live-error`/`live-skeleton` count 0. Independent driver: 0/0 console/pageerror, no literal-null text. Regression checks intact: happy render (rss 1720 MB), gear-menu entry, drill-down (marker click → panel, kind/severity/observed/baseline/contributors), **Z-ISO verbatim `?t0=2026-10-18T16:29:00.000Z&t1=2026-10-18T16:41:00.000Z`** (query-string, both Z-suffixed), lazy bundle (resources chunks fetched ONLY on /resources nav, 3 files set-diff; prod chunk-O65QVANM.js 38,914B, 0 initial-bundle bytes 3 ways). Secondary `this.anomalies` console error: GONE post-fix (free improvement).
- jest post-fix: 4103 total (+6 degraded specs, all green); failures = exactly the 4 known pre-existing (byte-identical shapes) + job-queue-indicator order-pollution flake (passes isolated 134/134 — new watch-list entry).

## AC5 — full-suite execution + attribution ledger

**Method note (scope decision):** the brief's literal `pytest tests/ -q` cannot honor the 5-min pack cap (853K effective lines; recon-modeled 47–99 min/shard serial). Honored intent MY way: 9 shard packs + make-ups, each `timeout 300`-wrapped (dual-layer: command wall + pyproject per-test 30s), xdist `-n 4/8` inside packs where recon proved isolation. @integration/@postgres/@slow deselected by default addopts throughout (deselect counts reported per shard: 11 + 426 + 408 + 0 elsewhere).

| Pack | Scope | Collected | Ran | Passed | Failed/Err | Deselected |
|---|---|---|---|---|---|---|
| P-STD | api manager static lint perf performance property vfy vfy_pg migration repositories | 646 | 89→(stall split) | 75 | 14F (9 SQLite + 4 attr. + 1 flake) | 11 |
| P-RMD | property vfy migration repositories | 539 | 539 | 534 | 5F (attr.) | 0 |
| U-AL | unit/test_[a-l] unit/routers | 4774 | 4774 | 4704 | 40F+21E (attr.) | 0 |
| U-SVC | unit/services unit/repositories unit/job_queue | 3158 | 3158 | 3127 | 9F (base-proven) | 0 |
| U-MZ | unit/test_[m-z] + 12 small unit dirs | 5573 | 5573 | 5495 | 21F+2E (5 branch-caused) | 0 |
| R-MZ | test_[m-z] unit/tools | 7113 | 7113 | 7013 | 95F (2 known macOS + 3 env + 1 flake + 89 attr.) | 0 |
| R-AL | test_[a-l] e2e postgres | 2689 | 2263 | 2109 | 40F+4E (1 flake; 39 attr.) | 426 |
| INTG | integration job_queue message_queue_redesign services | 4695 | 4287 | 4126 | 93F+1E (4 branch-caused; 2 macOS; 44 pre-existing; 27 xdist-flake; 17 host-env) | 408 |
| Make-ups | auth×3 files (serial PG) / no_xdist×2 / perf partial | 109 | 109 | 106 | 3F (ledgered) + perf tail | — |

**Attribution (sha-compare + branch-diff grep + `git archive` base-runs where decisive):**
- **Branch-caused: 9 reds, ALL one family — new-surface pin/manifest/fixture follow-through debt, zero production-behavior regressions:**
  1. `test_post_restart_arm_notify_no_parallel.py` T6.2 mount pin (+resources_router; was already red for 2 pre-base mounts) — commit aa95fc438
  2. `test_router_scope_manifest.py` census 33v32 + 8 undeclared `/api/resources/*` routes (D4.7 manifest owed) — aa95fc438…a385c102f
  3. `test_webfetch_builtin.py` E1/E2 fixture lacks `mcp_pool.idle_reap_seconds` (manager.py:2750) — 9186cdd4a
  4. `test_router_class_manifest.py` ×3 PINNED_MANIFEST_AUTH_ON/off — resources mount
  5. `test_auth_middleware_dispatch.py` default-deny dispatch shift — resources mount
- **Pre-existing-at-base: ~330 reds** (families cross-referenced to the standing QUARANTINE 2026-10-12 consolidation; newly-ledgered families: turn_reconciler ×3 + property state-machine ×1, ui_prefs `search=` ×2, chokepoint ×2, migration-96-pin already-red-at-base-100). Migration-count pin: branch grew 100→104 but the pin (96) was stale pre-base — NOT branch-caused.
- **Flakes:** ~29 (xdist-order classes: stale_recovery ×13, InMemorySaver ×11, chart T5 ×1, worker_notification ×1, auth-latency p99 ×1, llm_failover ×1) — all pass serially.
- **Env/host:** spawn_intelligence ×5 (host allowed_models lacks 'vision'), langgraph site-packages ×6, maintenancer memory.md untracked ×6, main_entry ×3 (env-sensitive, pass ambient).

**Quarantine actions (QUARANTINE.md updated):**
- `[1x10k]` perf cell: UNINTERRUPTIBLE C-block hang (thread+signal timeout methods both defeated; only outer SIGTERM). Sibling [10x1k] passes 23s. Base-attributed (file @ ecae40032, 2026-09-04).
- New consolidated row for the rcc-gate base-pre-existing set.

**Commission's own files: ALL GREEN everywhere run:** 225/225 serial (R-MZ: api 18, sampler 41, self_cost 8, storage 42, pid_registry 42, charge_seams 23, deep_scan 19, control_seam 32) + 123 (U-SVC: anomaly 45, traceback 78) + 42 (U-AL: routers anomalies+traceback) = **390 resource-family green**.

**Could NOT verify (gaps):**
1. Perf-matrix tail: `[100x10k-variance]` (in-flight at wall), `[1000x100]`, acceptance class ×~8 — per-cell budgets are 600s BY DESIGN; incompatible with the 5-min pack cap. 5 sibling cells + the other performance file PASS. Needs a long-budget run outside pack rules (or a `slow`-marker adoption).
2. R-AL's 6 custom_agent/loader files deselected to escape an xdist worker-crash (each verified green serially + isolated -n 4; the full-pack-concurrency crash mechanism is UNPROVEN — needs its own commission).
3. e2e/integration/pg marker-deselected corpora (835 tests) — never executed (consistent with the brief's note re @integration).
4. test_manager ×30 SQLite-quarantine skips (documented class).

---

## FINDINGS (leader disposition required)

- **FINDING #1 (production, W-2 class) — sanitizer gap:** dash-prefixed `--flag=secret` argv tokens persist into `resource_pid_map.command` (real-PG proven; list-mode up to 8 tokens). `_ENV_ASSIGN_TOKEN_RE = ^[A-Za-z_][A-Za-z0-9_]*=` covers identifier-shaped KEY= + 3 marker classes only. Docstring intent ("MUST NEVER carry secret-bearing text") is violated for this shape. Fix ≈ extend the redaction class to secret-shaped `--*=` tokens + canary pin. Severity: leader rules (real-traffic exposure plausible per the PB-F1 transcript-leak family).
- **FINDING #2 (advisory) — PG window-death on missing relation:** tolerated 42P01 INSERT aborts the PG txn; the same-window sweep then dies 25P02 InFailedSqlTransaction (correctly NOT mis-classified) — window raises instead of degrading to 0. Production impact LOW (create_all precedes flusher at boot). SQLite unaffected (no mid-txn abort) — unit-green by dialect.
- **FINDING #3 (test-infra debt, 9 reds):** the pin/manifest/fixture family above. Suggested: one small fix commit updating T6.2 pin, D4.7 manifest + census, router-class manifest, webfetch fixture (all test files; no production change) OR explicit accept-as-debt for a latest-side commission.
- **Advisory coverage gaps:** cross-router resilience + engine-fault degrade unpinned (mc-e); perf-matrix long-budget class; R-AL xdist-crash mechanism; NG8113 unused-import advisories on resources.component.ts; numbers-not-printed AC7 design note.

## Documentation updated

- QUARANTINE.md: +2 rows (stall cell; rcc-gate consolidated pre-existing set)
- PACKS.md: rcc final-gate pack registrations (top, newest-first)
- RESULTS/: this file
- No source/test files modified by the gate. No production code changed. Zero repo commits by workers.

## Worker instances

recon 7641ea2b · selfcost 8a50f9a0 (+revive ×1) · ensure-core ace1eca5 · fe-tscbuild 7f6d0af1 · fe-jest d13570e2 · pg-smoke 8802d918 · mc-b 3e951be1 · mc-c e6210d2e · mc-d d633e319 · mc-e 22195765 · mc-f 81241029 · playwright 5432e2ad · calib 30b0df3c · attribution e2a6e33a (×3 revives) · u-al 7ae0dd35 · u-svc b705d9e1 · u-mz 01474156 · r-mz 2310e9ab · r-al 2d1065e1 · intg e2f53876 · fe-postfix 4d74f673 · fe-e2e-postfix 03be6845 · makeup a9e0cee2
