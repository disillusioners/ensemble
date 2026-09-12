# Charter Reuse — Final Verification Gate (generate_chart Iterative Refinement)

**Date:** 2026-09-12 · **Gate:** final-shape verification
**Worktree:** `/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble-wt-charter-reuse`
**Branch:** `feature/generate-chart-charter-reuse`
**Feature tip:** `438cdd20` (7-commit stack `25c56265..438cdd20` on base `bb060b08`; merge-base with latest = `a28ba9a5` side drift)
**Gate-era commit added during verification:** `25053ca4` (quick fix, see §5)
**Workers:** 21 spawned (1 discovery + 12 partition packs + attribution + exoneration + acceptance + mock audit + security + legacy + ensure + quick-fix reuse); 3 stranded (P-10/sec/legacy) recovered by direct execution under leader's stranding-recovery directive — salvage found zero partial output.

## VERDICT: ✅ PASS — merge-ready (0 branch-caused failures across the full 12-partition sweep; 1 production defect found by the gate and FIXED+pinned in `25053ca4`)

**Original symptom (north star):** "generate_chart > small fix > calls generate_chart again — always creates a NEW charter instance, so the fix update won't work." → **DEMONSTRATED FIXED** at the real seam (§2).

---

## 1. Full-Suite Regression — 12 partition packs, 0 branch-caused

Leader's stated baseline (5 quarantined `TestAccessMemoryArchive` failures) is *narrower* than the repo's standing baseline: those 5 are **deselected by P-1's pack script** (appear as skips), and the standing baseline additionally contains the QUARANTINE.md-documented families below. Every failure in this sweep was adjudicated: **0 branch-caused**.

| # | Partition | Counts (P/F/E/S) | Runtime | Verdict |
|---|-----------|------------------|---------|---------|
| P-1 | unit_tools | 2,579 / 0 / 0 / 5 | 16.89s | ✅ PASS (5 skips = archive quarantine deselect; re-run post-quick-fix @25053ca4: identical 2,579P/5S) |
| P-2 | unit_services | 1,448 / 7 / 0 / 0 | 14.78s | ⚠️ FAIL-by-baseline — 7/7 = QUARANTINE row 24 `job_queue_proxy_phase1` map-gap family, byte-identical to 2026-09-07 record |
| P-3 | unit smaller-subdirs+routers | 647 / 0 / 0 / 0 | 17.59s | ✅ PASS — incl. **test_source_reservation.py 27/27 green** (`internal_chart_reuse:` pinned) |
| P-4 | unit loose a-d | 1,352 / 10 / 21 / 2 | 20.60s | ⚠️ FAIL-by-baseline — F/E/S counts **identical** to 2026-09-08 baseline (coder_developer_migration ×5, coder prompt ×1, api-size ×1, devops ×3, builtin-MCP setup ×21); +117P growth |
| P-5 | unit loose e-l | 1,168 / 19 / 0 / 0 | 46.42s | ⚠️ FAIL-by-baseline — 6/19 documented families (status-guard ×4, llm ×2); 13 = NEW `find_near_instance` family, **statically base-exonerated**: latest `02918951` widened `repo.list()` to 3-tuple, 13 test mocks stale (production correct) |
| P-6 | unit loose m-r | 1,989 / 10 / 0 / 40 | 64.48s | ⚠️ FAIL-by-baseline — 7/7 baseline families byte-match; 3 "new" = latest drift (release-tag pin v0.12.4→v0.12.5, project_manager prompt cross-refs ×2) — branch diff over involved paths EMPTY |
| P-7 | unit loose s-z | 980 / 54 / 2 / 11 | 16.60s | ⚠️ FAIL-by-baseline — **count-exact** vs 2026-09-07 baseline (delta 0); Watchover mock cascade ×47 dominant |
| P-8 | top-level a-h | 1,049 / 20 / 2 / 54 | 77.48s | ⚠️ FAIL-by-baseline — **chart suites 35/35 CLEAN inside partition**; 18 captured failures **dual-commit A/B exonerated** (verbatim-identical at base `bb060b08`); chokepoint ×2 cite only manager/worker_pool/job_recovery (NOT branch files) |
| P-9 | top-level i-q | 2,387 / 60 / 0 / 73 | 26.08s | ⚠️ FAIL-by-baseline — F-count = baseline-exact (60); 55/60 known families; 5 singles **all base-pre-existing with commit attribution** (a3aea077/ad7f8ec6/fb74611e/07c095ed/694b091c — ancestors of base) |
| P-10 | top-level r-z misc | 2,258 / 14 / 0 / 34 | 23.41s | ⚠️ FAIL-by-baseline — branch diff over all 5 failing surfaces **EMPTY** (spawn_limit ×10 stale fixtures, skill defaults ×2, orphan matrix ×1, atomic-dequeue flake sibling ×1, team_members=14 drift ×1) |
| P-11 | job_queue | 1,678 / 7 / 0 / 38 | 26.63s | ⚠️ FAIL-by-quarantine — 7/7 = Mission settled-rename stale-fixture set, node-for-node vs QUARANTINE row (stable 4+ gates) |
| P-12 | integration+opencode+e2e | 979 / 15 / 41 / 2 | 26.24s | ⚠️ FAIL-by-baseline/environment — 43/56 environment (no daemon ×8, httpx-drift opencode setup ×~34, no FE bundle ×2); 13 substantive = documented deferred classes (mirror-reconcile Phase 4b/4c ×5, WC-park restart-pending ×5, test-infra ×2, vscode-security ×1). Note: chart integration file lives at `tests/` top-level → NOT collected here; P-8's clean run is the authority |

**Aggregate:** ~18,774 passed / 216 failed+63 errors — **every failure attributed to documented pre-existing families, latest-lineage drift, or environment; branch-caused = 0.** No pack exceeded 78s (cap 300s); no TIMEOUT anywhere; no TTQA/architecture-fix situations arose.

### Attribution evidence ladder (strongest used per partition)
- **Dual-commit A/B** (throwaway worktree at base `bb060b08`): P-8's 18 captured failures + both chokepoint violation lists — verbatim-identical → exonerated.
- **Static diff-disjointness** (empty `git diff bb060b08..25053ca4 -- <failing surfaces>`): P-5, P-6, P-9, P-10.
- **Count-exact baseline match**: P-4, P-7, P-9, P-11.
- **Commit-level attribution** (introducing commit is an ancestor of base): P-9 singles, P-5 find_near, P-6 singles.

## 2. Acceptance Walk — ✅ PASS (north star demonstrated at the real seam)

Pack: `tests/test_chart_tools.py + tests/test_chart_tools_reuse_integration.py` (+ pin file) → **37/37 PASS** in 1.51s @ `25053ca4` (was 35/35 @ `438cdd20` pre-fix).

- **(a) First call produces a chart:** `test_first_call_spawns_fresh` (unit :321) — `result == "mermaid output"`, `mock_invoke.assert_awaited_once()`, `mode=fresh`; T1 seeds the post-first-call COMPLETED charter state via real repo rows. (Cross-test pairing — noted caveat: no single test does call-1→call-2 in one flow; T1 covers call-2 against seeded state.)
- **(b) Refine call reaches THE SAME instance:** T1 `test_second_call_revives_same_charter_via_real_enqueue` — spawn tripwire `tripwire["called"] == 0`; real-seam enqueue `kwargs["instance_id"] == charter_id`; mid-flight revive flip `(charter_id, "completed", "running") in mgr.revive_flips`; no second instance row. Unit: `enqueue_kwargs["instance_id"] == "charter-1"`, `mock_invoke.assert_not_awaited()`.
- **(c) Refined content building on the first:** refine turn's content returned verbatim through revived instance (`result == "```mermaid\n...\nFirst refine."`" integration :615; unit "…Refined."). Structural build-on (same id + checkpoint preserved + refine turn's own output) — semantic build-on not assertable with LLM stub (by design).
- **fresh=True → NEW id:** `test_explicit_fresh_spawns_new_instance` (:413). **Third call on revived:** `test_third_call_revives_revived_charter_unchanged` (:650). **Concurrent:** busy-reject with pinned message `"Error: Charter busy; pass fresh=True for parallel charts."` (:296/:520/:550, guard-order pinned on real recorder). **ERROR charter:** one revive consumed (real-dict counter) → second ERROR → fresh spawn (:465 unit, :1101 integration). **PAUSED:** busy-reject `"Error: Charter is paused; resume it or pass fresh=True for a new charter."` (:792).

## 3. Mock-Quality Audit — ✅ trustworthy at all load-bearing seams

- **CompletionRegistry:** load-bearing pins run against REAL `CompletionRegistry` subclasses (real idempotent register, double-complete rejection, buffered drain, wait_for timeout). W1 (stale-buffered misattribution) **closed + regression-pinned on real registry** (T8.14); W2 (shared-event coalescing) **latent but honestly avoided** — documented accepted-deferred; mitigation (T5 busy guard) pinned with real recorder.
- **enqueue_message revive flip:** harness flip is a documented semantically faithful hand-mirror of `instance_messaging.py:1948-1958`; non-vacuous relative to stub (`revive_flips` stays empty unless flip ran); real-branch regressions covered by the source-independent revive-fix family elsewhere. `internal_chart_reuse:` is NOT in trust-stamp branches (exact-prefix match :511-542) → bare HumanMessage, correct HUMAN classification.
- **get_children ordering:** real repo has NO ORDER BY (unspecified); production pick is total & order-independent (`max(key=(act!="", act, created_iso, instance_id))` with PK tie-break) — provably deterministic; tests feed reverse/NULL/tie rows on mock AND real SQL.
- **Vacuity:** Phase-3 `MagicMock(return_value=0)` trap confirmed FIXED (real dict callables + `== {}` negative control). Mode-log format pins byte-match production. Enqueue kwargs ⊆ real signature + T6 kwarg-drift gate.

## 4. Security Pin — ✅ PASS

- Static: `is_reserved_source("internal_chart_reuse:foo") → True`; `"internal_chart_reuse:"` → True; bare/no-colon, case-variant, near-miss → False; `RESERVED_SOURCE_PREFIXES` count == 18.
- E2E probe (`test/packs/origin_contract_e2e_probe_test.sh`, exit 0): Part 1 **31/31**; **Part 2: 18 reserved members, 18 observed mint sites, 0 missing/over-reserved** (`internal_chart_reuse:` minted at `daemon/tools/chart_tools.py:289`); Part 3 overlap clean. (Docstring nit: probe .py line-12 comment says "8 reserved prefixes" vs 10 colon cases — enumeration itself correct.)

## 5. Legacy Regression — ✅ PASS **after gate-time quick fix** (`25053ca4`)

- No-prior-charter → fresh spawn (`test_first_call_spawns_fresh`, `enqueue_message.assert_not_awaited` on discovery-miss).
- **REAL DEFECT FOUND by the gate's gap-close probe:** legacy single-shot `await invoke_agent_and_wait` at `chart_tools.py:499` had **no try/except** — an exception propagated out of the tool coroutine, violating the never-raise contract (only the `None` branch was handled). Reproducer failed pre-fix with the exact symptom (gold-standard non-vacuity), None-control passed (harness proven).
- **Quick fix LANDED** (`25053ca4`, criteria met: <20 lines, single file, obvious root cause): `except Exception → return "Error: Charter agent invocation failed: {exc}"`. Reuse paths untouched (they spawn via enqueue, not direct invoke). Verified: pin 2/2; charter pack 37/37; P-1 partition re-run baseline-exact (2,579P/5S, +0F). LESSONS: `LESSONS/2026-09-12-charter-reuse-raise-path-error-contract.md`.

## 6. Edge Cases — ✅ PASS

- NULL `last_activity_at` + reverse-ordered rows → deterministic pick: `test_discovery_picks_latest_charter` (unit :658, 5 sub-cases) + `test_discovery_determinism_latest_last_activity_wins` (integration :726, real SQLModel repo).
- T8.14 stale buffered completion drained — **behavioral** (`test_stale_buffered_completion_is_drained_before_register` :917): asserts new-turn content returned, stale content removed from `_buffered`, on a real registry subclass; call-sequence pins subsidiary.

## ensure.md (Core, scoped) — ✅ 4/4

- #1 changed packs: PASS (aggregate above). #2/#3 concurrency_atomic pack: **98P/74S/0F in 7.62s**. #4 `dev.sh` `--timeout-graceful-shutdown 10`: grep-exact (:102). Contradictions: none. Release Gate: NOT RUN (single-BE-feature blast radius; not big/critical/architecture).

## Scope Decision

Full suite run — warranted: leader explicitly requested broad regression for a final-shape gate; executed as the 12 established partition packs (each ≤ 78s ≪ 300s cap, quarantine deselects intact). FE pack `fe_static_typecheck_build_test.sh` **skipped**: zero `frontend/` files in the branch diff (pure BE feature; fresh worktree lacks node_modules). PG-specific suites not run (default SQLite suite is the standing regression surface; PG-conditional classes documented as environment).

## Latest-lineage follow-ups surfaced (NOT this branch — for owning areas)

1. 🔴 Chokepoint gate red at base: 3 direct-caller + 1 over-budget entries (`manager._resume_processing_background`/`_schedule_explicit_handle_resume`, `worker_pool._usage_limit_episode_decide` ×2) + 2 direct-SQL sites (`manager.py:5663`, `job_recovery_service.py:1993`) — need Appendix A entries or named-transition migration.
2. 🟠 `test_find_near_instance` ×13 stale 2-tuple mocks vs `repo.list()` 3-tuple (`02918951`); `repository.py:723` stale return annotation.
3. 🟠 `daemon/repositories/task/repository.py:3032` logger kwargs bug (production, test-covered).
4. 🟠 P-9/P-6 singles: release-tag pin lag (v0.12.5), project_manager prompt cross-refs, coder `llm_models` pin, leader `question` innate pin, ErrorCodes 19-member pin, A/B-resolution deactivate gap.
5. 🟠 P-10: spawn_limit ×10 stale fixtures (dead-knob removal aftermath), skill_evolution defaults ×2.
6. 🟢 opencode httpx drift (~34 setup errors), vscode-security expectation inversion.

## Gaps / Limitations

- 3 workers stranded (P-10, security, legacy) with zero partial output; recovered by direct execution under the leader's stranding-recovery directive (results above; drift-pinned @25053ca4).
- Acceptance walk (a) is cross-test pairing (fresh-spawn test + T1 seeded-state walk), not one continuous two-call test; (c) is structural build-on (LLM seam stubbed by design).
- Test-level green ≠ live: running daemon needs restart for both the branch and the quick fix to activate (consistent with repo's restart-pending convention).
- Leader's "5-failure baseline" understates the standing baseline (documented quarantine families ≈ 216 F + 63 E across partitions, all base-attributed).

## Code Changes Summary (this gate)

- `25053ca4`: `daemon/tools/chart_tools.py` (+13 net — raise-guard + comment) + `tests/test_chart_tools_legacy_error_contract.py` (new, +58) — "fix(chart): generate_chart returns Error string when charter invoke raises (never-raise contract) + regression pin"
- Bookkeeping commit (this file + LESSONS + PACKS.md gate block + QUARANTINE row): see git log.

**Overall: Unit/partition sweep ✅ (0 branch-caused) · Acceptance walk ✅ · Mock audit ✅ · Security pin ✅ · Legacy ✅ (post-fix) · Edge ✅ · ensure.md Core 4/4 ✅ — TESTING COMPLETE: READY**
