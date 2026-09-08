# E2E Verification — job-queue-mission-tree

- **Branch/commit**: `feature/job-queue-mission-tree` @ `708ee7a5` (worktree clean at start; every worker drift-gated `git rev-parse` before each invocation — zero drift events)
- **Base (merge-base with `latest`)**: `e202e277` (merge of feature/fix-report-delivery-ensure-deferred)
- **Feature commits**: BE `327fdc1a` + `301a3d77` + `1772f631`; FE `8e832847` + `2e8e28b3` + `b58cc821` + `708ee7a5`
- **Actual branch scope (verified BASE..HEAD, 24 files)**: BE `mission_resolver.py`, `jobs_crud.py`, `missions.py`, `routers/schemas.py`, `repositories/job_queue/repository.py`, `services/job_queue_service.py`; FE `job-queue-panel/*`, `job-queue-indicator/*`, `models/{job,mission}.model.ts`, `services/job.service.ts`; tests + docs
- **Date**: 2026-09-07 (UTC) / 2026-09-08 (local +07)
- **Shape**: 23 dispatches / 21 workers (9 wave-1 focused+FE+e2e, 12 wave-2 partitions+Core, 2 adjudications); 0 retries needed; report-only arc (no fixes, no commits)

## VERDICT

**Regression: ✅ CLEAN — 0 branch-caused test failures across ~13.6k tests (20 packs) + live-stack UI smoke.**
**Acceptance: 3 of 4 PASS; #2 PARTIAL (footer link not implemented). 2 UI findings to report (no fixes applied per arc constraints).**

---

## Scope Decision

Full suite run — **warranted**: cross-module BE+FE feature + dev chains explicitly deferred full regression to this gate. Executed as: 6 focused BE packs → 11 non-integration partition packs (P-1..P-11, the repo's full-unit convention) → concurrency Core pack → FE static/jest → live-stack Playwright smoke. **Excluded** (documented): `regression_integration_opencode_e2e` partition (P-12, integration slice; mission integration contracts covered instead by the focused `m2_missions_runtime_contract` + `mission_final_vocab_runtime` packs) and the 4 ensure.md Release-Gate real-LLM E2E workflow tests (daemon-workflow surfaces untouched by this branch; this feature's E2E is the UI smoke below).

## A. BE regression

### Focused packs (wave 1)

| Pack | Result | Counts | Notes |
|---|---|---|---|
| missions_api_unit_test | ✅ PASS | 42/42 (2.65s) | baseline 38 → +4: **+6 from `327fdc1a`** (title/initiative_preview wire fields, select-budget, no-DML list path), −2 pre-feature flag-matrix collapse (`99fcab22`) |
| mission_resolver_unit_test | ✅ PASS | 63/63 (1.28s) | baseline 48 → +15 feature-lineage, all green |
| jobs_streaming_resolver_unit_test | ✅ PASS | 10/10 (0.55s) | baseline-exact |
| m2_missions_runtime_contract_integration | ✅ PASS | 11/11 (1.25s) | **query-budget 3-SELECT bound (flat 2/4/8) holds**; filters/clamps/NULLS LAST/degraded-200/W4 dead-letter green |
| mission_final_vocab_runtime_integration | ✅ PASS | 9/9 (1.02s) | settled-vocab runtime matrix green |
| **mission_tree_unit_test (NEW pack)** | ✅ PASS | 104/104 (~9s) | `test_jobs_mission_id_filter.py` 21/21 · `test_work_resolver_query_budget.py` 6/6 · `test_phase5_jobs_router.py` 34/34 (baseline-exact) · `test_jobs_cleanup_endpoint.py` 43/43 |

### Full unit regression (wave 2, partitions P-1..P-11)

| Partition | Result | Counts | Failures attributed | NEW |
|---|---|---|---|---|
| P-1 regression_unit_tools | ✅ PASS | 1,161P/0F/1S (10.9s) | TestAccessMemoryArchive ×5 deselected per QUARANTINE (the task's known 5) | 0 |
| P-2 regression_unit_services | ⚠️ FAIL-by-baseline | 1,352P/7F (16.9s) | 7 = QUARANTINE row 24 (proxy_phase1 `_STATUS_CANONICAL_MAP`), base-evidenced | 0 |
| P-3 regression_unit_smaller_subdirs_routers | ✅ PASS | 647P/0F (11.7s) | none — even baseline slash_commands flake passed | 0 |
| P-4 regression_unit_loose_a_d | ⚠️ FAIL-by-baseline | 1,235P/10F/21E (14.6s) | ApiModuleSize pin 1F + coder/devops drift 9F + builtin-mcp mock fixture 21E — all QUARANTINE | 0 |
| P-5 regression_unit_loose_e_l | ⚠️ FAIL-by-baseline | 1,125P/11F (52.4s) | 11 = QUARANTINE row 25 stale-contract families, base-evidenced `e866c116` | 0 |
| P-6 regression_unit_loose_m_r | ⚠️ FAIL-by-baseline | 1,848P/7F (66.3s) | 7 = models_split/phase4 ledger + paused_auto_resume ×5; plane_sync quarantine node PASSED | 0 |
| P-7 regression_unit_loose_s_z | ⚠️ FAIL-by-baseline | 980P/54F/2E (16.8s) | watchover 47 + task_reconciliation family (2/6 firing) + misc base-evidenced + vision/terminal + blueprint-fixture 2E — all QUARANTINE-known | 0 |
| P-8 regression_top_level_a_h | ⚠️ FAIL-by-baseline | 1,026P/19F/2E (66.0s) | 13F+2E baseline (sqlite-20260714 trap, ledger, test_api, jsonb) + 1 perf-matrix WATCH load-flake + **5 suspects → adjudicated PRE-EXISTING** (below) | 0 |
| P-9 regression_top_level_i_q | ⚠️ FAIL-by-baseline | 2,370P/60F (21.0s) | sqlite-trap 19 + injection mock-await 26 + QUARANTINE rows 15/27 (14) + subprocess 1 | 0 |
| P-10 regression_top_level_r_z_misc | ⚠️ FAIL-by-baseline | 2,260P/12F (18.0s) | signature SHIFT (14 baseline healed, 12 appeared) → **all 12 adjudicated PRE-EXISTING** (below) | 0 |
| P-11 regression_job_queue | ⚠️ FAIL-by-quarantine | 1,677P/7F/39S (21.6s) | 7 = mission settled-rename stale-fixture family, node-for-node + signature-for-signature exact vs QUARANTINE row | 0 |
| concurrency_atomic_unit_test (ensure.md Core) | ✅ PASS | 98P/74S/0F (6.7s) | baseline-exact (91→98 documented +7 watchdog growth) | 0 |

**Partition totals**: ~14,000 collected; every single failure attributed to a QUARANTINE row, a documented baseline cluster, or base-evidenced pre-existing debt. **Branch-caused: 0.**

### Adjudications (merge-base worktree `e202e277` + 3× solo determinism at HEAD)

- **P-8 ×5 — ALL PRE-EXISTING** (byte-identical signatures BASE↔HEAD, deterministic): `test_agents_api` ×2 (real `agents/` dir leaks 34 entries into expected-empty fixture), `test_instance_ui_prefs_api` ×2 (`_ManagerStandin` lacks `search=` kwarg added at `instances.py:424` — not in branch diff), `test_enqueue_shared::TestTitleGeneration` ×1 (title-gen bridge double-fire).
- **P-10 ×12 — ALL PRE-EXISTING**: `TestSpawnLimitEdgeCases` ×9 (class-wide `MigrationError: 20260714_000001` PG-only `DROP CONSTRAINT` on SQLite — the documented fresh-SQLite trap, masks all assertions; NOT spawn-limit logic), `test_skill_evolution_config` ×2 (defaults drift: `ab_sample_size 20≠10`, `embedding_base_url` non-None), `test_terminal_orphan_matrix[pending-True-active]` ×1 (reconciler invariant).
- Evidence: disposable worktrees with `daemon.__file__` isolation proof; worktrees removed; main worktree untouched.

## B. FE verification

- **tsc**: `npx tsc --noEmit -p tsconfig.app.json` → **0 errors** (Stage-1, drift-bracketed).
- **Build**: `npm run build` → SUCCESS (25 initial + 25 lazy chunks, 5.85 MB) — **exactly 10 known warnings** (1 TS NG8113, 1 bundle-initial budget, 2 SCSS deprecation, 6 SCSS budget) — **0 NEW**.
- **Jest full**: **2520/2520 across 70 suites, 10.05s** (baseline 1907/52 suites @ 2026-08-12 → +613/+18, consistent with feature + upstream growth; brief's ~2515 expectation ±5). Rev-parse bracketed, `--no-cache`.

## C. Focused UI smoke (live stack, Playwright)

Provenance: worker booted its own dev daemon (`./dev.sh` on disposable `ensemble_dev` PG — **prod `ensemble_prod` never touched**) + own FE (`npm start` :4199); pre-existing dev data sufficed (no seeding, zero API writes); all self-started processes stopped, ports freed, repo footprint zero. `dev.sh` grep: `--timeout-graceful-shutdown 10` **present**.

| # | Acceptance criterion | Verdict | Evidence |
|---|---|---|---|
| 1 | Mission > jobs tree (missions top, badge+title, collapsed default, jobs on expand) | ✅ PASS | 8 mission nodes each with liveness badge (`check_circle completed`) + real title + meta, all `aria-expanded="false"`, 0 child rows pre-expand; expand → child row with SETTLED chip + agent/project badges |
| 2 | Panel wider (~560px) + more info (header stats, footer link) | ⚠️ **PARTIAL** | Width **exactly 560px** (`min(560px, calc(100vw-32px))`) ✓; header stats `⧗ 8 · ● 82` ✓; **footer link: FAIL — 0 footer elements in live DOM and none in template/SCSS (statically absent, not a render condition)** |
| 3 | Segmented pill (⧗ X/Y │ ● N) + honest degraded handling | ✅ PASS (+1 finding) | Pill `⧗ 0/8 │ ● 82` segmented, jobs leg matches API-derived X/Y exactly; degraded (aborted `/api/missions`): retained last values (never bare 0/0), `.degraded` class + honest aria-label, 0 pageerrors, recovered on restore. **Finding F-1 below (healthy-path semantics)** |
| 4 | BE: missions expose real title + initiative_preview; `GET /api/jobs?mission_id=` filters | ✅ PASS | `/api/missions` → real `title` + `initiative_preview` every row; `/api/jobs?mission_id=ce70a28c…` → exactly 1 job (mission-linked) vs unfiltered 20 jobs across ≥9 instance_ids |

Console: **0 pageerrors** (normal + degraded windows). 1 console error total, pre-existing and unrelated (Plane iframe CSP `frame-ancestors`). Escape closes panel; Enter/Space expand/collapse works with `aria-expanded` + tabindex.

**Findings (reported, NOT fixed — report-only arc):**
- **F-1 (🟠 important) — Pill missions-segment semantics**: shows unfiltered mission **total (82)** where the semantic is "live missions" (true value 7 = processing 7/pending 0/paused 0). `fetchBadgeSignals` polls `listMissions({limit:20})` with **no liveness filter**; `missionCountFromListResponse` uses `response.total ?? rows.length`. Tooltip self-contradicts: "Live missions: 82 (processing 0, pending 0, paused 0)". Overstates on any dataset with >20 missions / >0 settled. Suggested direction: pass `liveness=processing,pending,paused` (total-based, per FE blueprint convention) or count live rows client-side.
- **F-2 (🔴 for acceptance-2) — Footer link not implemented** in `job-queue-panel` (absent from template + SCSS + DOM). Original acceptance said the panel "stores more info (header stats, footer link)" — header stats landed, footer did not.
- **F-3 (🟢 minor) — Arrow-key tree nav not implemented**: ArrowRight/ArrowLeft are no-ops on focused mission rows (Enter/Space + Escape work). Was in the smoke checklist, not in the original acceptance list.
- **F-4 (🟢 minor) — LIVE MISSIONS section unexercised live**: dataset had zero live-liveness rows in the recent-20 page, so the top "Live missions" section never rendered live (template-level confirmation only).

Artifacts: `/tmp/mission-tree-smoke/` — `mission-tree-smoke.js`, `tooltip-probe.js`, screenshots `01-app-with-pill.png`, `02-panel-open.png`, `03-tree-expanded.png`, `04-degraded.png`.

## D. Pre-existing baseline-hygiene findings on `latest` (base `e202e277`, base-evidenced)

17 nodes, all reproducing at BASE with identical signatures (candidates for quarantine/fix on latest — **not** this branch's responsibility):

1. `tests/test_agents_api.py::test_list_agents_success` + `::test_list_agents_empty_directory` — real `agents/` dir (34 entries) leaks past fixture scoping.
2. `tests/api/test_instance_ui_prefs_api.py` ×2 — `_ManagerStandin` fixture predates `list_instances(search=)` kwarg (`instances.py:424`).
3. `tests/test_enqueue_shared.py::TestTitleGeneration::test_triggers_title_on_idle_to_running` — title-gen bridge fires 2× (`run_async_no_wait` call_count 2≠1); un-awaited-coroutine warning on both BASE and HEAD.
4. `tests/test_spawn_limit_edge_cases.py::TestSpawnLimitEdgeCases` ×9 — class-wide fresh-SQLite boot trap (`MigrationError 20260714_000001`, PG-only `DROP CONSTRAINT`); masks all spawn-limit assertions.
5. `tests/test_skill_evolution_config.py` ×2 — defaults drift (`ab_sample_size` 20≠10; `embedding_base_url` non-None default).
6. `tests/test_terminal_orphan_matrix.py::test_jobitem_task_status_matrix[pending-True-active]` — reconciler invariant violation (admission_state=active, has_lock=False).

## ensure.md Validation Results

Core (always-on):
- ✅ **Critical** — No regressions in changed packs: all 8 blast-radius packs PASS (focused ×6, FE ×2)
- ✅ **Critical** — Deadlock/concurrency integrity: `concurrency_atomic_unit_test` 98P/0F
- ✅ **Critical** — No sync DB calls on asyncio loop: covered by same pack (thread-identity tests) — PASS
- ✅ **Critical** — `dev.sh` includes `--timeout-graceful-shutdown 10`: static grep verbatim present
- ✅ **Important** — Original deadlock scenario: covered by concurrency pack — PASS
- ⚪ **Important** — async-await callers grep (`_get_system_prompt_tokens` etc.): **out of blast radius** (branch does not touch those functions) — not in scope this gate
- ⚪ **Nice-to-have** — dead-code check: N/A (no deletions in scope)

Release Gate (warranted — full regression was the ask):
- ✅ (with notes) **Full non-integration suite via packs**: P-1..P-11 all ran; every failure attributed to QUARANTINE rows / documented baseline clusters / §D base-evidenced pre-existing debt. Branch-caused = 0. Note: the 17 §D nodes are NOT yet in QUARANTINE.md — surfaced here for the baseline-hygiene backlog.
- ⚪ **E2E real-LLM workflow tests (4)**: not run — daemon-workflow surfaces untouched by this branch; this feature's E2E is the live-stack UI smoke (§C). Documented exclusion.

Contradiction handling: none found — ensure.md is already pack-mapped and timeout-capped; no Improvement Notices.

## Gaps (honest)

1. Footer link: verified absent statically AND live — cannot pass acceptance-2 fully (finding F-2).
2. "Missions on top" live ordering + LIVE MISSIONS section: unexercised live (no live-liveness data); template-level only (F-4).
3. Arrow-key nav: not implemented (F-3); Enter/Space/Escape verified.
4. Integration slice P-12 (`tests/{integration,opencode,e2e}` bulk) not re-run this gate; mission-critical integration contracts covered by the 2 focused integration packs.
5. ensure.md Release-Gate real-LLM E2E (4 tests) not run (justified exclusion above).

## Action Needed

- [ ] Decide on F-2 footer link: implement or re-scope acceptance #2 (original user ask said "footer link").
- [ ] F-1 pill missions-segment: add liveness filter (or client-side live count) — honesty issue on real datasets.
- [ ] Baseline-hygiene backlog on `latest`: 17 nodes in §D (quarantine or fix upstream).
- [ ] (cosmetic) `fe_static_typecheck_build_test.sh` hardcodes `EXPECTED_BRANCH=feature/mission-class` — re-runners must pass `EXPECTED_BRANCH=<branch>` env override (hit by both FE workers this gate).
- [ ] (cosmetic) `mission_resolver_unit_test.sh:18-21` — `set -euo pipefail` + `EXIT_CODE=$?` breaks the `RESULT: FAIL` banner on failing runs (PASS path unaffected).

## Repro commands

```bash
cd /Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble   # feature/job-queue-mission-tree @ 708ee7a5
# focused
timeout 300 bash test/packs/missions_api_unit_test.sh
timeout 300 bash test/packs/mission_resolver_unit_test.sh
timeout 300 bash test/packs/mission_tree_unit_test.sh          # NEW this gate
timeout 300 bash test/packs/m2_missions_runtime_contract_integration_test.sh
# partitions (P-1..P-11) — one per invocation, e.g.
timeout 300 bash test/packs/regression_job_queue_test.sh
# FE (from frontend/) — note the EXPECTED_BRANCH override
EXPECTED_BRANCH=feature/job-queue-mission-tree timeout 300 bash test/packs/fe_static_typecheck_build_test.sh
timeout 300 bash test/packs/frontend_full_unit_test.sh
# UI smoke (own daemon on disposable PG + own FE)
nohup ./dev.sh > /tmp/mission-tree-smoke/dev.log 2>&1 &   # POSTGRES_DB=ensemble_dev; never ensemble_prod
cd frontend && nohup npm start > /tmp/mission-tree-smoke/fe.log 2>&1 &
timeout 300 node /tmp/mission-tree-smoke/mission-tree-smoke.js
```

## Re-Verification Round (2026-09-08) — fix commits `30266275` + `c6670144`

Trigger: the 3 gaps flagged above were fixed frontend-only. 4 workers (scope/infra check, live-stack re-smoke, targeted jest, FE static), all drift-gated; HEAD verified exactly `c6670144`, both fix commits ancestors, no extra commits.

**Per-item verdicts:**

1. **Commit scope — ✅ PASS**: `30266275` = 9/9 paths under `frontend/`; `c6670144` = 4/4 under `frontend/`. Zero BE paths → "no BE re-run needed" holds. Touched specs: `job-queue-indicator.component.spec.ts`, `job-queue-panel.component.spec.ts`, `job.model.spec.ts`.
2. **T1 footer (F-2) — ✅ PASS**: footer renders `Open full queue →` (chevron); click → `/jobs` + panel closes; Enter → single-emit navigation + close; Space → same. No double-emit.
3. **T2 pill live count (F-1) — ⚠️ PARTIAL**: the **82→7 bug is dead** (pill shows L=7 from the liveness-filtered count endpoint; `bugClass82Detected=false`); degraded path still honest (retains 7, honest aria-label, recovers on restore); 0 pageerrors. **NEW FINDING F-5 (🟠)**: the fix split the data sources — count from `?liveness=processing,pending,paused&limit=1`, but tooltip breakdown (`processing X · pending Y · paused Z`) still derived from the unfiltered recent-20 list (`job-queue-indicator.component.ts:240-251`). On this dataset: tooltip reads "Live missions: 7 (processing 0, pending 0, paused 0)" — self-contradictory — and the panel's LIVE MISSIONS section is empty while the header says `● 7` (same split; the 7 processing missions fall outside the recent-20 by `last_activity_at`). Fix direction: source breakdown (+ panel live rows) from the same payload as the count (e.g., extend count endpoint to `{total, breakdown}`).
4. **T3 arrow keys (F-3) — ✅ PASS (medium-high confidence)**: real DOM focus (`document.activeElement` on `.mission-row`, tabindex=0); ↑/↓ move between visible treeitems (collapsed children skipped); → expands (`aria-expanded` false→true, child `ul[role=group]` visible); ← collapses + focus returns to parent; Enter/Space toggle; Escape closes. 8/8 sub-checks. Caveat: exercised on RECENT items — the live tree was empty in this dataset (same data-source split as F-5); nav is a single code path (`onTreeKeydown` → `visibleItems()`), so structurally identical.
5. **Scoped regression — ✅ PASS**: targeted jest (NEW ad-hoc pack `mission_tree_fe_targeted_test.sh`) **292/292 across exactly the 3 touched suites** (2.0s); `tsc --noEmit` **0 errors**; `npm run build` SUCCESS — **10/10 known warning identities, 0 NEW** (bundle-initial 5.85→5.86 MB, same identity).
6. **Worktree/infra — ✅ PASS**: all 4 uncommitted infra files present, flags correct (`M` PACKS.md; `??` RESULTS/LESSONS/mission_tree_unit_test.sh + new `mission_tree_fe_targeted_test.sh`), 10/10 content markers PASS, zero unexpected entries.

Console: 0 pageerrors; 0 NEW console errors (16 observed = known pre-existing + intentional route-abort artifacts). Stack provenance: worker-booted daemon on `ensemble_dev` PG (non-prod; `ensemble_prod` untouched) + own FE; clean teardown, ports freed. Artifacts: `/tmp/mission-tree-resmoke/` (`playwright_resmoke.js`, `results.json`, 12 screenshots).

**Re-verification verdict: 5.5/6 PASS (T2 partial — original bug dead, one new consistency finding F-5).**

## Final Merge-Readiness (both rounds combined)

- **Regression: ✅ CLEAN** (round 1: 0 branch-caused failures across ~13.6k BE + 2,520 FE; round 2: 292/292 targeted + tsc/build clean; commits verified frontend-only).
- **Acceptance: #1 ✅ · #2 ✅ (footer now implemented + verified) · #3 ✅ core + degraded honest, with F-5 follow-up (tooltip breakdown + panel live-section data-source split) · #4 ✅.**
- **Merge-readiness: ✅ READY from the testing perspective** — no test blockers; recommend either landing F-5 as a small FE follow-up on this branch (preferred: unify count/breakdown/live-rows data source) or accepting it as a tracked follow-up. Round-1 hygiene backlog (17 pre-existing-on-latest nodes) remains mainline debt, not branch debt.

## Files created/modified this gate (ALL UNCOMMITTED — report-only arc; leader decides)

- `test/packs/mission_tree_unit_test.sh` (NEW pack script, untracked)
- `.agents/tester/PACKS.md` (Last Run row updates for all 20 packs + 2 new rows: mission_tree pack, fe_static registration)
- `.agents/tester/RESULTS/2026-09-07-job-queue-mission-tree-e2e-verification.md` (this file)
- `.agents/tester/LESSONS/2026-09-07-latest-baseline-hygiene-e202e277.md` (§D findings for future gates)

Zero production-code changes. Zero commits.

---

## F-5 Mini-Verification (2026-09-08) — commit `906e51bf` — **VERDICT: F-5 NOT FIXED**

Setup: HEAD verified exactly `906e51bf` (sole commit after `c6670144`), scope claim HOLDS (2 files, both `job-queue-indicator/`: component.ts + spec.ts). Stack: worker-booted daemon on `ensemble_dev` (non-prod) + own FE; dataset persisted from prior rounds (L=7 live processing, U=82, live ∩ recent-20 = ∅ — exactly the shape F-5 must wire correctly). tsc clean (exit 0). Artifacts: `/tmp/mission-tree-fv/` (diag scripts, screenshots, `d3-summary.json`, API blobs).

| Check | Verdict | Evidence |
|---|---|---|
| 1a Pill count == L | ✅ PASS | Pill `⧗ 0/8 │ 7`; count leg correct (F-1 remains dead) |
| **1b Tooltip consistent** | ❌ **FAIL** | Verbatim: "Live missions: **7** (processing **0**, pending **0**, paused **0**) refreshed 0s ago" — 7 ≠ 0+0+0. F-5 contradiction intact |
| **1c LIVE MISSIONS rows == L** | ❌ **FAIL** | Panel renders `[Queued] 0 rows, [Recent] 8 rows` — **no live-missions section at all** (its `@if (tree().liveMissions.length > 0)` gate collapses on an empty source). Header says `● 7` over an absent section |
| 2 No live bleed into RECENT | ✅ PASS | All 8 RECENT rows terminal (`check_circle completed`); zero processing/pending/paused |
| 3 Degraded path (LEG A abort) | ✅ PASS | Retains `0/8 │ 7`, honest stale aria-label, recovers on restore |

**Root cause (code-level, reproduced twice, network-verified):** production `applyFetchResults` writes only `liveMissionCountRaw.set(count)` from the filtered leg — it never writes `liveMissionsPayload`. `grep liveMissionsPayload.set` across `components/` hits **exactly one site: the spec mirror harness** (`job-queue-indicator.component.spec.ts:447`, carrying the comment "F-5: also store the LEG A payload so liveMissionBreakdown and liveMissionsList can read its missions rows (the count-only write leaves the breakdown at 0)" — i.e., the fix was written INTO THE TEST MIRROR but never propagated to `job-queue-indicator.component.ts`). Consequence: tooltip breakdown + panel live-rows read an eternally-empty list while the pill count is correct. The targeted jest bracket is green (**299/299**, +7 spec tests) precisely because the repo's FE specs are plain-TS logic mirrors — the mirror was fixed, production was not. Proposed one-liner (NOT applied — report-only): in `applyFetchResults`, after the count write: `this.liveMissionsPayload.set(liveMissions);`.

**Merge signal: ❌ F-5 acceptance NOT met by `906e51bf`.** Everything else stands (scope ✓, tsc ✓, jest 299/299 ✓, pill-count/degraded/RECENT ✓). Recommend: apply the one-line production fix (+ propagate to the real component), re-run this mini-verification (single Playwright pass, ~2 min) — or merge with F-5 explicitly re-opened as a tracked defect. Systemic note: spec-mirror-only fixes are invisible to the jest suite by construction — see `LESSONS/2026-09-08-fe-spec-mirror-fix-blindness.md`.

### Final infra list for giter (all UNCOMMITTED, verified present + intact + executable)

1. `.agents/tester/PACKS.md` (M — 20+ Last-Run row updates + new pack rows; one ad-hoc section)
2. `.agents/tester/RESULTS/2026-09-07-job-queue-mission-tree-e2e-verification.md` (?? — this report, 3 rounds)
3. `.agents/tester/LESSONS/2026-09-07-latest-baseline-hygiene-e202e277.md` (??)
4. `.agents/tester/LESSONS/2026-09-08-fe-spec-mirror-fix-blindness.md` (?? — new, this round)
5. `test/packs/mission_tree_unit_test.sh` (??, 755)
6. `test/packs/mission_tree_fe_targeted_test.sh` (??, 755; pin re-rolled `c6670144 → 906e51bf` under tester authorization — noted for reviewers)

`git status --porcelain` = exactly these 6 entries, nothing else. Zero commits; zero production changes by the tester arc.

---

## F-5 Re-Run @ `bbcae9a2` (2026-09-08) — **VERDICT: F-5 FIXED — ALL CRITERIA PASS → merge proceeds**

Sole new commit `bbcae9a2` ("fix(fe): F-5 — write Leg A live-missions payload in production applyFetchResults") touches exactly the production component + its spec — the prior mirror-only omission is corrected (production write confirmed at `job-queue-indicator.component.ts` ~line 713, same non-null/non-degraded gate as the count).

Live-stack verification (same persisted dataset: L=7 live processing, U=82, live ∩ recent-20 = ∅ — the exact shape that broke 906e51bf):

| Criterion | Verdict | Evidence |
|---|---|---|
| S1 Pill == L | ✅ | `⧗ 0/8 │ 7`; leg-A API oracle total=7 {processing:7} |
| S2 Tooltip real split | ✅ | verbatim "Live missions: **7 (processing 7, pending 0, paused 0)**" — sum==7; the 0/0/0 contradiction is structurally impossible now |
| S3 LIVE MISSIONS renders L rows | ✅ | section present, exactly 7 rows, all `aria-expanded="false"` collapsed, row ids == leg-A API ids exactly (2571c8e4/42f6b690/608988ef/9cc269d8/b100e0ec/c6a6f549/fe3f43ee); liveness badges + titles (2 real titles; 5 agent_id fallbacks = faithful render of API `title=null` data shape, not a UI defect) |
| S4 No live bleed into RECENT | ✅ | 8 RECENT rows all terminal (`check_circle completed`); nonTerminal=[] |
| S5 Degraded honest | ✅ | retains `0/8 │ 7` + last-good tooltip during abort, honest stale aria-label + `.degraded`, clean recovery; 0 pageerrors |

Bracket: `mission_tree_fe_targeted` **PASS 300/300** (3/3 suites; +1 net-new indicator test from the fix; pack pin re-rolled to bbcae9a2 under tester authorization). Console: 0 pageerrors, 0 NEW console errors (known bucket + intentional route-abort artifacts only). Stack: worker-booted daemon on `ensemble_dev` (prod untouched) + own FE; full teardown; 8088 never touched. Artifacts: `/tmp/mission-tree-f5r/` (script, report.json, 6 screenshots, API oracle snapshots). Honest gaps: (a) 5/7 live rows show agent_id fallback titles because the API returns `title=null` for them (data shape, not UI); (b) degraded-retention verified for one ~18s window, not multi-cycle.

**Merge-readiness: ✅ READY.** F-5 closed; acceptance 1-4 all verified; regression-clean across all rounds (~13.6k BE + 2,520→2,521 FE, 0 branch-caused failures). Round-1 hygiene backlog (17 pre-existing-on-latest nodes @ `e202e277`) remains mainline debt.

### Final infra list for giter (re-confirmed at bbcae9a2 — 6 entries, all uncommitted, markers + exec-bits verified, zero porcelain extras)

1. `.agents/tester/PACKS.md` (M) · 2. `RESULTS/2026-09-07-job-queue-mission-tree-e2e-verification.md` (??) · 3. `LESSONS/2026-09-07-latest-baseline-hygiene-e202e277.md` (??) · 4. `LESSONS/2026-09-08-fe-spec-mirror-fix-blindness.md` (??) · 5. `test/packs/mission_tree_unit_test.sh` (??, 0755) · 6. `test/packs/mission_tree_fe_targeted_test.sh` (??, 0755; pin now reads `bbcae9a2` — both re-rolls tester-authorized, noted for reviewers)
