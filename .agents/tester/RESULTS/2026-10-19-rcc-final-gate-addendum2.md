# RESULTS ADDENDUM #2 — INTEGRATED TREE re-verified @ fb8bf87d6 ≡ latest @ 68e086c7a

**Date:** 2026-10-19 · **Parent report:** `2026-10-19-rcc-final-gate.md` (@ c9b2156bc) · **Addendum #1:** `2026-10-19-rcc-final-gate-addendum.md` (@ 8e01f637a)
**Integrated tree:** `fb8bf87d6 chore(release): bump version to 0.25.0` ≡ `latest @ 68e086c7a` (merge --no-ff, content-equal; `git diff fb8bf87d6 68e086c7a --stat` = **EMPTY** confirmed). 18 upstream commits merged in (settings-worker-slots lane, managed-key union dedupe + AST guard, version bumps 0.24.0→0.25.0, bus/notify RCA fixes, giter fence-template fix).
**Re-verification workers:** settings+guard fb096d76 · resource+selfcost 320b7310 · recipes 83ddf9de · pg-canary 8802d918 (revive) · fe f788d4e9.

## Per-item verdicts

| # | Item | Verdict | Evidence |
|---|---|---|---|
| **1** | Settings/resolver/dependency_bus + AST guard | **PASS 185/185** | daemon_api 26 + settings_resolution 36 + settings_tools 32 + dependency_bus 91. **Runtime import** confirms `MANAGED_KEYS` carries SIX keys IN ORDER with named defaults: `limits.llm_concurrency(10)` · `limits.worker_pool_size(5)` · `limits.chat_worker_pool_size(2)` · `limits.instance_cache_ttl_hours(6)` · `limits.instance_cache_max_entries(64)` · `limits.chart_render_concurrency(2)`. Both lanes' keys pinned by name (worker-slot D1 in `settings_tools.py:482/492`; rcc in `config.py LimitsConfig` Field defaults mirrored to MANAGED_KEYS descriptions). The `TestGenerationCounterBump` known-flake passed in union + isolated. **No new reds.** |
| **5** | AST duplicate-key guard | **PASS — mechanism verified** | `TestManagedKeyRegistryInvariants` 2/2 PASSED. Quoted guard mechanism: parses `settings_service.py` `MANAGED_KEYS` literal with `ast`; asserts every string key appears EXACTLY once in the literal; the deduped-runtime `len()` check cannot catch a last-wins shadow. Belt-and-braces: `literal_count == runtime_count` prevents future AST/runtime drift. **The exact pre-fix defect class (the merge's `limits.llm_concurrency` duplicate) is the one this guard mechanically prevents going forward.** Fail-closed on literal shape changes. |
| **2** | Resource family (8 files) + self-cost | **PASS 234/234 unchanged** | Per-file: 23/19/48/41/45/18/8/32 — **zero test count drift** between 7be1b40bf and fb8bf87d6. Self-cost numbers (driver, 3 runs, fence-guarded): ON@70 median **1.4684 ms** (vs baseline 1.4794, −0.7%) / max **1.9434 ms** (vs 1.9708, −1.4%); OFF median **0.4540 ms** (vs 0.4586, −1.0%) / max **0.8637 ms** (vs 0.6028, **+43% flag** — single-tick tail artifact, run-1 + run-3 each had one outlier; medians are design-relevant and stable; the budget-asserting `test_forced_tick_costs_under_hard_gate` PASSED inside the test's own hard-gate). RSSΔ 0.0 MB. |
| **3a** | LIVE-PG canary smoke | **PASS byte-stable** | Fresh throwaway `ensemble_gate_rcc_int`/`rcc_gate_int` (pre-drop+post-drop verified 0/0). **All 9 canary needles 0 hits** (`sk-REDACTEDCANARY123` the original FINDING #1 needle + 8 others including ghp/xoxb/AKIA/marker/standalone/mid-token). Redacted forms forensically correct (flag/env NAMES kept, RHS `<redacted>`). C-1 invariants hold (plain INSERT hard-asserted, 42P01 tolerated in isolation, 42703 propagates). FINDING #2 end-to-end: full `run_once()` with table MISSING → SURVIVED (sane dict, no 25P02); `begin_nested` confirmed at `flusher.py:579` and `:745`. |
| **3b** | Recipe set (tier/blend/degrade/replay) | **PASS 5/5** | storage 45/45 (incl. `TestRollDown::test_sweep_per_tier_predicates` + 3 PG-txn-abort survival); api honesty/blend 2/2 + sampler full 41/41; tier-coexistence driver PASS at PRODUCTION cutoffs (raw 50h deleted, **minute@50h + hour@50h survive** the 48h raw sweep — F1 evidence verbatim); honesty blend-max driver PASS (`attributed = 9,000,270` = max-form, `sum would be 10,001,540` — the rejected form); restart-replay 45/45 (incl. `TestRestartRecompute::test_replay_from_db_uses_recent_window` + `test_replay_from_db_minute_tier`). |
| **4** | FE gates (tsc/jest/build/e2e) | **PASS** | tsc 0 errors (0-byte log); jest 4103 total (**delta 0** from integration; the settings-worker-slots merge added 0 FE specs); the 4 pre-existing jest failures are **STILL exactly those 4** (1 stale-status-pin + 3 date-bomb — byte-identical); build 0 errors, initial 5.91MB (unchanged), resources lazy chunk-O65QVANM.js 38,914B **0 initial bytes** (3-way); playwright 3/3 incl. AC8 degraded-shape spec (`pageErrors == []`, `consoleErrors == []`, ≥8 `—` placeholders, `live-error`/`live-skeleton` count 0). |

## INTEGRATED-TREE VERDICT: **RELEASE-READY**

The 18-commit integration merge (settings-worker-slots + managed-key union dedupe + AST guard + bus/notify RCA fixes + giter fence fix + version bumps 0.24.0→0.25.0) was **orthogonal** to the resource-cost-control surface:
- **Zero** new jest specs in the FE family
- **Zero** test count drift in the 8-file resource family (234 = 234)
- **Zero** byte change in the resources lazy chunk (38,914B identical)
- **Zero** NEW-RED across settings/union/resource/recipes/canary/FE
- **All** prior gates (AC5/AC6/AC7/must-cover a–f/FE web-automation/ensure.md Core) hold byte-stable; **all** merge-blockers (FINDING #1, FINDING #3) and the advisory (FINDING #2) remain closed

The only flag is the self-cost OFF-max +43% tail-latency artifact (inside the test's own hard-gate ceiling; medians stable to <2%; the budget-asserting test passed). No code change; the document accepts a tail-latency measurement-hygiene note rather than a code action.

Open non-blockers carried from the parent report (unchanged): blanket dash-assign RHS over-redaction adjudication; `--flag <value>` residual; perf-matrix long-budget tail; R-AL xdist-crash commission; cross-router/engine-fault degrade pins.

## What I could NOT verify
1. Jest count drift of zero over a 18-commit merge is mechanical fact; not re-running the full suite (`pytest tests/`) — explicit "full-suite re-runs are NOT needed" per the brief.
2. The space-separated `--flag <value>` and the benign-RHS adjudication are unchanged residuals from the prior addendum (leader's call).
