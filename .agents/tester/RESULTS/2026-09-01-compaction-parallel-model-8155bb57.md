# Test Report: Compaction Engine Branch — Parallel Chunked Summarization + Optional Compaction Model

Date: 2026-09-01
Branch: `feature/compaction-parallel-model` @ `8155bb57` (worktree checked out as `feature/compaction-output-structure` — both branch names point to the SAME commit 8155bb57; alias, not drift. All 7 workers verified sha `8155bb57` pre-run.)
Feature set under test:
- (A) parallel chunked summarization — semaphore pool, `COMPACTION_CHUNK_CONCURRENCY=3` default, shared deadline budget (`COMPACTION_OPERATION_BUDGET_S`) w/ cancel-in-flight keep-completed, non-contiguous partial drops
- (B) optional compaction model — precedence `COMPACTION_MODEL` env > yaml `compaction.model` > legacy > session; window math follows override
- (W1) auto-path trigger gated at min(session, override) window + one-shot WARN

Instance IDs (workers): 583ad1aa (W1 pack) · 4a949a2d (W2 per-file+budget) · 85e92cfa (W3 mock-quality) · f819861d (W4 dispatcher) · 11e6266b (W5 executor) · ed27dae0 (W6 FE) · 1321ab79 (W7 live) · cab0668d (W8 concurrency)
Round posture: **READ-ONLY verification — 0 code changes, 0 commits.** Evidence root: `/tmp/tester-evidence/compaction-parallel/`

Excluded per reviewer: 2 known env-coupled pre-existing failures in `tests/unit/test_llm_allowed_models_precedence.py` — not in any dispatched pack (exclusion automatic).

---

## Summary

| Scope | Result |
|---|---|
| Scope 1 — BE suites (5 targets) | ✅ ALL PASS, every baseline EXACT |
| Scope 1 — FE Jest | ✅ PASS 64 suites / 2342 tests, exact |
| Scope 1 — budget-deadline flake watch ×3 | ✅ NO FLAKE (3/3 stable) |
| Scope 2 — mock-quality audit | ✅ CHECK1 GENUINE · ⚠️ CHECK2 PARTIAL · ⚠️ CHECK3 prod-clean/test-weak |
| ensure.md Core Critical (concurrency pack) | ✅ PASS 98P/74S/0F baseline-exact |
| Scope 3 — LIVE verification | ⏳ PENDING W7 |

---

## Scope 1 — Suites (exact baselines re-verified)

| Target | Command | Result | Baseline | Delta | Runtime | Worker |
|---|---|---|---|---|---|---|
| compaction_unit_test pack | `timeout 300 bash test/packs/compaction_unit_test.sh` | **PASS 290/290** | 290 | 0 | 4.60s | 583ad1aa |
| test_compaction.py | `timeout 300 .venv/bin/pytest tests/unit/test_compaction.py tests/unit/test_compaction_model_config.py --tb=short -q` | **PASS 94/94** | 94 | 0 | 4.24s (combined) | 4a949a2d |
| test_compaction_model_config.py | (same invocation, per-file via --collect-only) | **PASS 31/31** | 31 | 0 | — | 4a949a2d |
| test_command_dispatcher.py | `timeout 300 .venv/bin/pytest tests/unit/services/test_command_dispatcher.py --tb=short -q` | **PASS 76/76** | 76 | 0 | 1.94s | f819861d |
| test_compact_executor*.py (3 files) | `timeout 300 .venv/bin/pytest <3 executor files> --tb=short -q` | **PASS 74/74** (65+3+6) | 74 | 0 | 8.18s | 11e6266b |
| FE full suite | `cd frontend && CI=1 timeout 240 npm test -- --no-cache` | **PASS 64 suites / 2342 tests** | 64/2342 | 0 | 10.2s | ed27dae0 |

All runs bracketed by `git rev-parse` (pre-flight + pre-invocation) — no worktree drift at any point. Dual-layer timeouts held everywhere; nothing approached a cap.

Zero failures, zero skips-beyond-baseline, zero errors across all BE targets.

### Budget-deadline flake watch (reviewer watch item)
- `-k "budget or deadline"` selected 3 tests in test_compaction.py: `TestPartialSummaryWS34::test_c_budget_exhaustion_partial_summary`, `TestOperationBudgetWS33::test_budget_exhaustion_stops_remaining_chunks`, `TestOperationBudgetWS33::test_chunked_deadline_cancels_in_flight`
- 3 total runs (combined base run + 2 dedicated `-v` runs): **3/3 PASS every time**; wall times stable (3.11s / 3.11s, Δ<0.05s)
- **VERDICT: NO FLAKE detected** in one watch cycle. (Reviewer flagged 0.8s-budget jitter risk — stable under this cycle's load, which included 2-3 concurrent workers.)

### FE diff note
Branch DOES touch FE: `frontend/src/app/components/chat-interface/chat-interface.component.ts` (+4/−1) — string-literal reword of the `partial_summary` compaction fallback message (non-contiguous-survival phrasing). No spec references either string; baseline held exactly.

---

## Scope 2 — Mock-Quality Checks (read-only audit, worker 85e92cfa)

Blast surface (`git diff --stat 7394e716..8155bb57`): `config.yaml` +16 · `daemon/compaction.py` +362/− · `daemon/config.py` +95 · FE chat-interface +4/−1 · `tests/unit/test_compaction.py` +352/− · `tests/unit/test_compaction_model_config.py` +612 (new). 6 files, +1288/−154.

### CHECK 1 — Deadline-cancels-in-flight: **GENUINE PIN** ✅
- Stub (`tests/unit/test_compaction.py:2056-2072`) does `await asyncio.sleep(5)` inside try, `except asyncio.CancelledError: cancelled_batches.append(idx); raise` — cancellation physically reaches the await point; NOT an orchestrator status flag.
- Assertions (`:2084-2094`): `stop_reason == "budget"` + survivors `[compaction-0,2,3,4,5]` (batch-1 slot empty = non-contiguous hole) + `failed_batches == [1]` + `cancelled_batches == [1]` — both legs (cancel-reached-task AND completed-survive) pinned together.
- Companion `test_budget_exhaustion_stops_remaining_chunks` (`:1956-2029`) proves batches 2-5 never reached the await (semaphore-held cancels).

### CHECK 2 — Window-gating asymmetry: **PARTIAL** ⚠️
- Gate side FULLY pinned both directions (`TestWindowGatedAtSessionWindow`, 6 tests, `tests/unit/test_compaction_model_config.py:495-611`): override>session → gate=session(200); override<session → gate=override(100); no-override → session; WARN exactly-once w/ both model names + both windows; no-WARN when ≤ or absent.
- **Sizing side only pinned in the SYMMETRIC case** (override<session, where gate and sizing necessarily agree — `test_threshold_math_uses_compaction_model_window` `:441-488` uses override=100/session=128000, cannot distinguish gate-picked-100 from sizing-picked-100). NO end-to-end test with `override > session` tracing chunk-sizing math to the override window. The asymmetry (design intent) is asserted only at `_trigger_window` return-value level, never through `compact_state` with divergent windows.

### CHECK 3 — Index-reassembly: **PROD CLEAN / TEST WEAK** ⚠️
- Production: `as_completed` appears ONLY in two warning comments (`daemon/compaction.py:1128`, `:1216` — "NEVER as_completed here"). Reassembly = `asyncio.gather` (order-preserving by contract, `:1267`) + positional pre-allocate `summaries_by_idx` (`:1239`) + index-keyed assignment (`:1250`) + order-preserving list-comp (`:1317`) + explicit `sorted()` for failed_batches (`:1321`). No bug surface.
- Tests: both reassembly-sensitive tests have completion order COINCIDENTALLY equal to index order (synchronous stubs / semaphore-serialized fast batches). An `as_completed`-based refactor would still pass them. No test drives out-of-order completion (e.g., batch-0 slow + batch-1 fast, concurrency ≥2) and asserts final order.

### Bonus red flags (report-only)
1. `test_c_budget_exhaustion_partial_summary` (`test_compaction.py:1678-1743`) stubs `_summarize_chunked` itself — it pins the OUTER handler mapping of `ChunkedOutcome`, not budget cancellation; the name misleads.
2. `test_threshold_math_uses_compaction_model_window` stubs `_summarize_chunked` → the "threshold math" claim is only half-proven (trigger gate yes, chunking arithmetic no).
3. Good precedent noted: `test_parallel_pool_resolves_override_consistently` drives REAL `_summarize_chunked`, stubs only `_summarize_single_batch` — cleanest pattern in the corpus.

**None of the Scope 2 findings are behavior bugs** — production code is correct per audit; the findings are test-coverage gaps (asymmetry untested end-to-end; ordering untestable-by-refactor). Recommend as follow-up test additions, non-blocking.

---

## ensure.md Validation (Core, scoped to blast radius)

| Requirement | Pack | Result |
|---|---|---|
| Critical: No regressions in changed packs | all scoped packs above | ✅ PASS (290+125+76+74 all exact) |
| Critical: Deadlock/concurrency integrity | `concurrency_atomic_unit_test` | ✅ PASS 98P/74S/0F baseline-exact (10.60s, worker cab0668d; all 13 files present+run) — directly relevant: branch adds async concurrency to compaction |
| Critical: No sync DB calls on asyncio loop | same pack (thread-identity tests) | ✅ PASS |
| Critical: dev.sh `--timeout-graceful-shutdown 10` | static grep | ✅ PASS — confirmed at dev.sh:102 (W7 Phase 0.5; also: no COMPACTION refs in dev.sh, expected env-only knobs) |
| Excluded pre-existing | test_llm_allowed_models_precedence.py ×2 | not in any pack |

---

## Scope 3 — LIVE Verification (worker 1321ab79) — ALL OBSERVED ✅

Evidence root: `/tmp/tester-evidence/compaction-parallel/live/` (PHASE0-findings.md, mitm-calls.jsonl w/ 33+ calls start/end/model/status, S1/S2/S3 evidence JSONs, daemon logs daemon-s1/s2/s3/s3b.log, config-test.yaml + dev-wrapper.sh harness in /tmp only).

### S1 — Parallelism OBSERVED (serial is gone)
- Setup: `COMPACTION_CHUNK_CONCURRENCY=3`, fresh instance `31d23701` (agent developer), 27 exchanges × ~800-word fillers → daemon estimator: 56 compactable groups → **3 batches**, 34,895 compactable tokens > 30,000 gate.
- `/compact` accepted 08:34:58Z → success at 45.7s; `Compaction triggered: 32894 tokens … force=True, regular=56` → `32894 -> 5351, type=summarization`.
- **Concurrency proof: 3 batch calls started within 5 ms of each other (.822/.827/.827), 3 pairwise overlaps (durations 12.3/12.9/17.5s), merge call AFTER the pool completed (08:35:16)** — exactly the designed pool→merge structure.

### S2 — Model override OBSERVED
- Daemon rebooted with `COMPACTION_MODEL=agentic-mini`; history rebuilt (51 compactable → 3 batches, 33,411 tok); compact at 08:42:03Z.
- **All 4 compact-window calls `model=agentic-mini`** (3 concurrent batches 29.5/32.8/80.9s + merge 56.4s); **all 24 normal-turn rebuild calls + fresh-instance turn (`2b5768e1`) `model=agentic`** (session model).
- Caveat: window-math-follows-override is NOT live-proven — override `agentic-mini` shares the 50,000 window with `agentic` (substring match), so window behavior identical by construction. Model-selection precedence IS live-proven; window math rests on suite evidence (31/31).

### S3 — Budget behavior OBSERVED
- budget=60: not binding (completed ~21s) — as predicted; escalated per instructions to **14s**.
- Budget=14: `WARNING: Operation budget deadline hit with 2/3 batch summaries complete (1 in-flight cancelled, 0 never started); keeping completed summaries.` — cutoff at exactly +14s, 2 completed calls kept (ended 11.6s/13.8s), in-flight cancelled (upstream ran to 20.8s server-side), op finished fast, no hang.
- Result: command phase `fallback_applied`, `compacted_type=partial_summary, failure_kind=timeout`, tokens `31283 → 5333`. (Engine `stop_reason=budget` → wire `partial_summary`/`fallback_applied` — consistent with the engine→wire total-by-construction map.)

### Disclosed deviations (flagged by worker, accepted)
1. `.env` `OPENAI_BASE_URL=http://localhost:4123/v1` DEAD (curl exit 7) → proceeded on the ambient-env proxy `https://llm.ensem.dev/v1` (same llm-supervisor-proxy family, .env key accepted) via the sanctioned env-override mechanism. Proof: proxy-remote-ensem.json.
2. Sizing harness: stock config makes the chunked path unreachable live (`agentic`→600,000 window ⇒ 360k-token gate) → boots used `ENSEMBLE_CONFIG=/tmp/.../config-test.yaml` (window 50,000, threshold 0.95, preserved window 2). Env-var mechanism, zero repo edits, feature code paths production-identical.
3. dev.sh:58-64 `set -a; source .env` clobbers command-line-prefixed vars → /tmp dev-wrapper.sh applies overrides AFTER .env source (verified by reading dev.sh).
4. Soft budget exceeded (~65 min vs ~25): two failed boots (empty-env pydantic error; port-race errno-48 from a reload-supervisor orphan) + per-scenario history rebuilds (each /compact consumes the fixture).
5. Mitm capture observational only (timestamp/model logging, byte-exact pass-through); no client-visible behavior change.

### Cleanup confirmed
Dev daemon + capture proxy stopped via verified PIDs; ports 8079/4321 FREE; **8088 never touched**; prod code-server left alone; user proxy untouched; `git status` clean (only 3 pre-existing untracked .agents/* files), HEAD still `8155bb57`.

---

## Verdict

# ✅ SHIP — zero blockers

- **Scope 1**: every suite PASS at EXACT reviewer baselines (290 · 94 · 31 · 76 · 74 · FE 64/2342); budget-deadline flake watch 3/3 stable — the flagged 0.8s-jitter risk did NOT materialize in this cycle.
- **Scope 2**: production code audited correct (no `as_completed` in reassembly; gather+positional-index+sorted; gate=min + WARN-once). Two NON-BLOCKING test-coverage gaps + naming flags → follow-up recommendations below.
- **ensure.md Core**: 4/4 Critical green (scoped packs · concurrency 98P/74S/0F · thread-identity · dev.sh flag).
- **Scope 3 LIVE**: all three features OBSERVED with network-layer + daemon-log evidence; deviations disclosed and env-sanctioned; clean teardown.

### Follow-up recommendations (non-blocking, 🟢 nice-to-have)
1. Add an end-to-end asymmetry test: `override > session` config, token band where gate(session) fires but sizing(override) differs — traces `_effective_model_name` window into chunk arithmetic (closes CHECK-2 gap).
2. Add an out-of-order-completion ordering test: batch-0 slow + batch-1 fast, concurrency ≥2, assert final summaries order by index (makes an `as_completed` refactor observable; closes CHECK-3 gap).
3. Rename `test_c_budget_exhaustion_partial_summary` (it pins outer-handler mapping, not budget cancellation) or split the concerns.
4. Live-proof window-math-follows-override with a distinct-window override model when the proxy offers one (S2 caveat).
5. Consider a canonical proxy health precheck in dev tooling (dead `.env` pointer found during live round).

## Documentation Updated
- [x] RESULTS/2026-09-01-compaction-parallel-model-8155bb57.md — this report
- [x] PACKS.md — gate entry + last-run stamps
- [ ] rules/ensure.md — user-maintained, no changes (correct)

## Code Changes Summary
None — READ-ONLY round. 0 commits. Worktree left on `feature/compaction-output-structure` @ `8155bb57` (= feature/compaction-parallel-model alias).
