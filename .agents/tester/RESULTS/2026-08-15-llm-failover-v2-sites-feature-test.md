# Test Report: LLM HA Failover v2 — Secondary Sites via Shared Facade

Date: 2026-08-15
Branch: `feature/llm-failover-v2-sites` — production commits `c19e2a3d` (feat) + `8b135da7` (review fixes); campaign test-only commits `c75ebd14`, `6b41dd03`, + final pack/test commit (see Code Changes)
Instance IDs: 8147c28f (recon), 822ac915, 7abe5ffc, ec9347e1, 564540b6, ea697e93 (feature suites), 0d4093d7 (adversarial author), bcfb3e7f (resilience author), 190c7f9a, 45b62705 (NEW pack runs), 7075e1fb, 8cb4365e, 57cae198, b22a2afe, 40812209, 06215834, 4de7082f, 9f4093c0, 5e456be4, db01907a (regression), 649c3b56 (commit)

## Summary
- **17 packs run, 17 resolved. ~1,290 tests. 0 NEW failures in production code anywhere.**
- v1 suites re-verified green under v2: 64/64 + 74/74 + 18/18 + 36/36 (exact baseline matches)
- NEW v2 feature suite: 41/41 PASS · NEW adversarial: 48/48 PASS · NEW resilience: 20/20 PASS
- Regression: 10 packs, baseline-clean — compaction 206/206, compaction-mm 30/30, report_repair 61/61, title 29/29 (better than baseline — 8 pre-existing failures fixed pre-branch by 8c71b862), skill_evo 47/47, captured-evo 62/62, search-unit 33/33, search-int 11/11, c2_core 167P/38F pre-existing/0 NEW, concurrency 91P/74S/0F
- ensure.md Core (scoped): 4/4 Critical PASS · 2/2 Important PASS · 1/1 Nice-to-have PASS
- Quick Fixes Applied: 2 (both TEST-CODE ONLY — `c75ebd14` facade-delegation signature assert, `6b41dd03` InstanceManager fixture missing attr; neither touches production)
- New test infrastructure: 68 adversarial/resilience tests + 3 pack scripts (committed)
- **Verdict: SHIP**

## Scope Decision
> Full suite not warranted. Scope = diff `3e83c8c5..8b135da7`: **10 files** (brief said 12; actual diff is 10 — `daemon/services/llm_failover.py` NEW, `tests/unit/test_llm_failover_v2.py` NEW, 8 modified production files), all within the LLM-invocation path (facade + 9 secondary call sites + manager base_url_backup threading). Diff grep confirms ZERO job/task/queue files (`claim_pending_task`, `turn_transitions`, `reconcile_turn_mirror`, `job_processor`, `job_locks` all absent) → **ensure.md mandatory full e2e gate NOT triggered** (verified by recon, focus area 8). ZERO frontend files → focus area 9 N/A confirmed. Ran: 5 feature packs + 2 NEW authored packs + 10 regression packs scoped to touched modules. Skipped: full suite (~40+ min across 259 packs), e2e packs (gate not triggered), PG packs, frontend.

## Test Focus Results (task's 9 focus areas)

### 1. Re-run suites — ✅ PASS (4/4 exact baseline matches)
| Pack | Tests | Result | Runtime |
|---|---|---|---|
| `llm_failover_unit_test` (v1) | 64 | ✅ 64/64 | 11s |
| `llm_error_classifier_unit_test` | 74 | ✅ 74/74 | 0.6s |
| `graph_retry_unit_test` | 18 | ✅ 18/18 | 0.73s |
| `llm_failover_adversarial_unit_test` (v1) | 36 | ✅ 36/36 | 2s |
| `llm_failover_v2_unit_test` (NEW) | 41 | ✅ 41/41 | 88s |

Dev claim (41 v2 + 156 v1 green) independently confirmed by fresh workers with pack discipline.

### 2. Zero-drift when backup unset — ✅ VERIFIED at ALL 9 sites (on the wire)
22 NEW tests (`TestZeroBehaviorChangeAllSitesBackupUnset`): each of the 9 secondary sites driven through the REAL site function × 2 backup-unset variants (`None` + `""`), asserting (a) wire attempts ≤ 3, (b) **zero requests off the primary host**, (c) the site's graceful fallback reached, (d) no `[LLM-HA]` WARNING. Plus v1's 36-test adversarial battery re-verified green.
**Accepted delta confirmed bounded**: sites that had no retry pre-v2 now get bounded retry (≤3 transient) with backup unset — intentional, latency-only, council-adjudicated. NO site gets unbounded or unexpected retry (attempt counts asserted exactly).

### 3. Facade failover E2E (MockTransport) — ✅ VERIFIED both families
`TestMockTransportFailoverBothFamilies` (7 tests): title_generation (LangChain) + skill_search, skill_evolution (raw-SDK) + keyword_extraction (LangChain). Primary 500s → wire call-log shows requests landing on backup URL → `[LLM-HA]` WARNING captured via caplog → LLM-derived result returned (not fallback). Both-legs-down → graceful fallback respecting the 6-attempt HA budget (3 primary + 3 backup).

### 4. Embedding guard matrix — ✅ VERIFIED on the wire (24 tests)
- (a) `embedding_base_url` unset → failover ACTIVE (swap fires, succeeds on backup)
- (b) same endpoint different formatting — trailing slash, host case, scheme case, query/fragment — → failover **STILL ACTIVE** (the v2 Fix 2 behavior; verified by wire-swap, not just comparator unit test)
- (c) genuinely different `embedding_base_url` (different host/port/path/scheme) → failover **DISABLED**: zero backup hits, bounded primary retry only, no WARNING — conservative direction preserved
- 11 comparator unit pins: `_normalize_endpoint_url` port preservation (`https://x:443` ≠ `https://x` — explicit default port correctly disables), path case-sensitivity, userinfo dropped, malformed/empty fallback
- **Mutation-tested**: re-introducing the pre-Fix-2 raw-string comparator fails 8 guard tests; treating `""` as configured backup fails all empty-variant zero-drift tests

### 5. Latency caps (30s) — ✅ VERIFIED structural + functional (8 tests)
- 5 AST structural pins: `asyncio.wait_for(timeout=...)` present at title_generation (30s), child_reports summarize (30s), child_reports repair (`config.timeout_seconds`), compaction (30s), keyword_extraction (`timeout_s`)
- 2 functional: hanging LLM + monkeypatched short wait_for → timeout fires at site boundary into graceful fallback; **no retry-storm amplification** (facade attempts ≤3 under cap)
- 1 config-default pin: repair timeout is config-driven
- No literal 30s waits used (per task allowance)

### 6. Fallback composition — ✅ VERIFIED at all 8 fallback sites (8 tests, one per site)
Facade patched to raise → each site's exact graceful default: title skipped / `[]` keywords / canned count-summary string / `None` → `_combine_messages` / `[]` trigger queries / `""` evolution text / `_degraded_select` / `_truncate_fallback`. No exception bubbles to callers. (skill_search `_llm_select` propagates by design — its fallback lives one level up in `search()`; pinned in tests.)

### 7. Concurrency — ✅ VERIFIED (4 tests)
10 threads × 5 calls with `threading.Barrier`: zero cross-talk in `current_failover_url()` (each thread sees only its own URL); thread-local cleared in `finally:` even under mid-call exception; no leak between sequential calls on same thread; nested-call clobber pinned as documented single-depth limitation.

### 8. Regression sweep — ✅ 0 NEW failures (10 packs vs baseline)
| Pack | Result | Baseline comparison |
|---|---|---|
| `compaction_unit_test` | 206/206 | Zero deviation |
| `compaction_multimodal_unit_test` | 30/30 | Exact match |
| `report_repair_unit_test` | 61/61 | Baseline 46 → 61 tests since (suite growth), 0 failures |
| `title_generation_trigger_test` | 29/29 | **Better than baseline** (21P/8F): the 8 pre-existing failures were fixed pre-branch by test commit `8c71b862` |
| `skill_evolution_unit_test` | 47/47 | Exact match |
| `skill_captured_evolution_unit_test` | 62/62 | Exact match |
| `skill_search_interval_unit_test` | 33/33 | Baseline 22 → 33 (growth), 0 failures |
| `skill_search_interval_messaging_integration_test` | 11/11 after quick fix | 2 fixture failures → fixed (`6b41dd03`) |
| `c2_core_regression_unit_test` | 167P/38F pre-existing/0 NEW | 38 = documented SQLite migration `20260714_000001` class, 1:1 |
| `concurrency_atomic_unit_test` | 91P/74S/0F | Exact match 2026-08-14 |

**Diff confirms e2e gate NOT triggered**: zero job/task/queue files in `3e83c8c5..8b135da7` (recon verified via git diff grep). Full e2e gate correctly scoped out.

### 9. Frontend — ✅ N/A confirmed
Zero frontend files in diff. No browser automation run (correctly scoped out).

## ensure.md Validation Results (Core, blast-radius scoped)
- **Critical 4/4**:
  - ✅ No regressions in changed packs — all 17 packs PASS/baseline-clean
  - ✅ Deadlock/concurrency integrity — `concurrency_atomic_unit_test` PASS (91P/74S/0F, exact baseline)
  - ✅ No sync DB calls on asyncio loop — covered by same pack, thread-identity tests PASS
  - ✅ `dev.sh --timeout-graceful-shutdown 10` — static grep PASS (dev.sh:102)
- **Important 2/2**: async-await callers (no production code changed — covered by c2_core + concurrency packs green) · original deadlock scenario PASS
- **Nice-to-have 1/1**: no dead code from fix — inert `ainvoke` deletion verified in v2 suite (binding has no ainvoke; deleted in review round)
- **Release Gate: NOT RUN — correctly scoped out** (LLM-path-only change, no job/task/queue touches → mandatory-gate trigger absent)

## ensure.md Improvement Notices
None — no contradictions found this campaign.

## Quick Fixes Applied
1. **`c75ebd14`** — `tests/unit/test_phase4_manager_decomposition.py:844-846` (c2_core pack)
   - Root cause: production `pause_instance_cascade` gained `suspension_reason` kwarg in an earlier hotfix; facade delegation test still asserted the old signature. NOT a v2 bug.
   - Fix: assert `suspension_reason=None` in the delegation assert. 2 lines, test-only.
2. **`6b41dd03`** — `tests/services/test_skill_search_interval_messaging.py` (search-int pack)
   - Root cause: Watchover commit `12378edb1` (2026-08-06) added `_deferred_watchover_terminate` to `_cleanup_instance_state`; two minimal-fixture tests (built via `InstanceManager.__new__`) lacked the attr. Pre-existing latent breakage, surfaced on this branch. NOT a v2 bug.
   - Fix: `mgr._deferred_watchover_terminate = set()` added to both fixtures. 2 lines, test-only.

## New Test Infrastructure (committed)
- `tests/unit/test_llm_failover_v2_adversarial.py` — 48 tests (zero-drift ×9 sites, embedding guard matrix, MockTransport E2E both families)
- `tests/unit/test_llm_failover_v2_resilience.py` — 20 tests (latency caps, fallback composition ×8 sites, concurrency)
- `test/packs/llm_failover_v2_{unit,adversarial_unit,resilience_unit}_test.sh` — 3 dual-layer pack scripts

## Warnings (non-blocking, pre-existing)
- `PytestConfigWarning: Unknown config option: timeout/timeout_method` on every pack — pytest-timeout plugin absent from this venv; bash-level `timeout` is the real enforcement. Environmental noise, identical across baselines.
- Follow-up recommendation (from search-int worker): audit other tests using `InstanceManager.__new__` fixtures for the same missing-attr gap class that `6b41dd03` fixed.

## Action Needed
- [ ] (optional, post-merge) Audit `InstanceManager.__new__`-style fixtures for missing-attr gaps (watchover class of breakage)
- [ ] (optional) Document single-depth nesting limitation of `current_failover_url()` in daemon docstrings (already pinned by test)

## Documentation Updated
- [x] PACKS.md — 3 new pack entries + campaign summary line
- [x] LESSONS/2026-08-15-llm-failover-v2-campaign.md — adversarial patterns + quick-fix records
- [x] RESULTS/2026-08-15-llm-failover-v2-sites-feature-test.md — this report

## Code Changes Summary (campaign-produced, all test-side)
- `tests/unit/test_llm_failover_v2_adversarial.py` — NEW (48 tests)
- `tests/unit/test_llm_failover_v2_resilience.py` — NEW (20 tests)
- `test/packs/llm_failover_v2_unit_test.sh` + `llm_failover_v2_adversarial_unit_test.sh` + `llm_failover_v2_resilience_unit_test.sh` — NEW pack scripts
- `tests/unit/test_phase4_manager_decomposition.py` — quick fix @ `c75ebd14`
- `tests/services/test_skill_search_interval_messaging.py` — quick fix @ `6b41dd03`
- Final infra commit: see commit worker report (hash recorded in campaign log)
- Production code: **ZERO changes** (frozen at `8b135da7` for the entire campaign)

---

### Overall Status
- Unit/feature suites: ✅ PASS (64+74+18+36 v1 re-verified, 41+48+20 NEW)
- Regression: ✅ PASS (0 NEW failures across 10 packs)
- ensure.md (scoped): ✅ PASS (Critical 4/4, Important 2/2, Nice-to-have 1/1)
- **Testing Complete: ✅ READY — Verdict: SHIP**
