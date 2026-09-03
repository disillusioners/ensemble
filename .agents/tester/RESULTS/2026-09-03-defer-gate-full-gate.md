# FINAL GATE — DEFER-GATE POST-SETTLE WINDOW (2026-09-03)

**Branch:** `fix/defer-gate-post-settle-window` @ `b46c9f8b` (+1 gate-owned test-infra commit `ab567195` — runtime-matrix pack, test-code only; final HEAD `ab567195`)
**Base:** `f77fb892` · **Phase-1 (RED) commit:** `853abb1b` · **Fix shipped:** `81e8d247` (spec: `.agents/shared/planning/defer-gate-fix/recommendation.md`)
**Change under test:** behavioral admission-gating fix — widened defer/background idle predicates (settled mirror of non-terminal instance = BUSY, both gates; claim SQL untouched), shared SQL-body constants module `daemon/repositories/job_queue/_idle_predicate_sql.py`, 3 tracked production/test/doc files + shared-SQL module.

## VERDICT: ✅ **FINAL PASS — CLEARED FOR MERGE.** Zero 🔴 caused regressions.

Dispatched: 24 worker instances (1 wave-0 discovery, 3 wave-1 [W3/red-green/…], 7 acceptance+ensure+runtime, 12 partitions, 1 base attribution). Repo modifications by gate: ONE test-infra commit `ab567195` (runtime-matrix pack pair; test-code only, zero production changes). Evidence: `/tmp/dg-gate/p01–p12.log`, `/tmp/dg-w3/`, `/tmp/dg-redgreen-853abb1b/` (removed), `/tmp/dg-base-f77fb892/` (removed).

---

## 1. Acceptance Sets — ALL GREEN (counts exact)

| Set | Expected | Actual | Verdict |
|---|---|---|---|
| A: `tests/job_queue/test_defer_gate_post_settle_window.py` | 11 + 1 desel | **11P/0F/1 desel** (desel = PG-parity `@pytest.mark.postgres`, clean marker SKIP on supplementary `-m postgres` run — no PG env) | ✅ |
| B: `tests/job_queue/test_defer_gate_probe.py` | 2 | **2P/0F** | ✅ |
| C: `tests/job_queue/test_defer_idle_gate_phase2.py` | 31 | **31P/0F** | ✅ |
| D: `test_deferred_finalize_check.py` + `test_defer_queue.py` + `test_defer_deadlock.py` | 4+21+5 | **4P + 21P + 5P = 30P/0F** (per-file exact) | ✅ |
| E: constitution drift pack (`EXPECTED_BRANCH=fix/defer-gate-post-settle-window`) | 10, census 23/6/1 | **24P/0F** (drift 10/10 + linkage 14/14); census **23/6/1 MATCH** (writers 23 bidirectional, mints 6, creators 1); `RESULT: BRANCH-CHECK` | ✅ |

### Red→green story — PROVEN (5/5 both directions)
- **RED half @ `853abb1b`** (scratch worktree, isolation proven via `daemon.__file__`): 8 collected → **5F/3P**. All 5 named Phase-1 REDs FAIL pre-fix with the defect signatures: leg-1 predicate `admission_state='active'` hides settled mirrors (repository.py:741); full-gate composition reports idle while mission live; background body `admission_state IN ('queued','active')` excludes settled mirrors (repository.py:863); `_defer_idle_check` admits (0≠1); `_select_next_eligible_job` wrongly returns the defer job. The 3 baseline tests PASS pre-fix (as committed).
- **GREEN half @ HEAD:** 3 REDs in post_settle_window.py (acc-A) + 2 REDs in probe.py (acc-B) — **5/5 PASSED**.
- Spec-wording note: 2 of the 5 REDs live in `test_defer_gate_probe.py` (the file 853abb1b added), not post_settle_window.py — the spec's §4 table is file-agnostic; counts reconcile exactly.

## 2. FULL Regression — HEAD `ab567195` vs base `f77fb892` (12 committed partition packs)

| Partition | Collected | Result | Failure inventory | Attribution |
|---|---|---|---|---|
| p01 unit_tools | 1,104 | **PASS 1,103P/1S/0F** | none | — |
| p02 unit_services | 1,134 | FAIL 1,127P/**7F** | proxy_phase1 ×7 (all) | ledger row-17 family; 7/8 base-verified pre-existing; the 8th (`test_started_at_sourced_from_instance_last_activity_at`) PASSES at HEAD — see §2.1 |
| p03 smaller_subdirs_routers | 537 | **PASS 537P/0F** | none (slash_commands extinct — mission-fixed at base, confirmed 40/40P at base) | — |
| p04 unit_loose_a_d | 1,050 | FAIL 1,017P/10F/21E | slash-fixture 21E + TestApiModuleSize ×1 + coder_*/devops ×9 — **exact baseline** | ledger rows 16/17 + formal row |
| p05 unit_loose_e_l | 1,116 | FAIL 1,105P/11F | misc cluster 9 (hide_kb 5 + job_processor_status_guard 4) + llm_allowed_models ×2 — **exact baseline** | ledger row 17 |
| p06 unit_loose_m_r | 1,890 | FAIL 1,843P/7F | models_split ×1 + phase4 ×1 + paused_auto_resume ×5 — **exact baseline, zero drift** | ledger rows 16/17 |
| p07 unit_loose_s_z | 1,036 | FAIL 971P/52F/2E | watchover **47 exact** + webfetch ×2E + wanderer ×2 + validate_agent_id ×1 + vision ×1 + terminal_reason_mirror ×1 — **exact parity** | ledger rows 14/15/17 |
| p08 top_level_a_h | 1,072 | FAIL 1,002P/18F/2E | sqlite 9 + subdirs 4 + misc 3 (incl. `test_enqueue_shared` ×1 — the charter's expected known pre-existing) + test_api mock-await ×2 + jsonb ×2E-of-3 (flake non-repro −1F) | ledger rows 16/17 + flake family |
| p09 top_level_i_q | 2,443 | FAIL 2,310P/60F | sqlite/progressive_dispatch 18 + memory_integration 10 (**verbatim row-16 signatures**: inner_soul MagicMock ×9 + Access-denied ×1) + injection mock-await 27 + innate/llm ×2 + error_codes ×1 + compaction_guard ×1 + meta-test ×1; skill-evolution flake ×0 (non-repro, −1F) | ledger rows 16/17 |
| p10 top_level_r_z_misc | 2,311 | FAIL 2,257P/15F | sqlite 9 + skill_evolution_config 2 + terminal_orphan_matrix 1 + context-flakes 3 (**+1 new family member**) | ledger + new flake row (see §2.2) |
| p11 job_queue (**branch's module**) | 1,671 | FAIL 1,626P/**7F**/38S | observer-guard ×4 + settled-vocabulary ×3 — deterministic ×2 runs | **🔴-candidate → base A/B: 7/7 PRE-EXISTING at f77fb892, verbatim signature match** (see §2.1) |
| p12 integration_opencode_e2e | 734 coll (+~262 addopts-desel) | FAIL 734P/22F/19E | httpx env-class 26 (19E+7F, incl. vscode setup errors re-bucketed by signature) + context_injection_hybrid 8 (needs live daemon) + bucket5 6 + complete_cancel 4 + e2e-stale 3 + answer_dismiss 1 + w7 1 — net −1 vs stated baseline | ledger row-37 httpx family (env-class, pinned httpx 0.28.1 private API) + rows 18/20/21/36; **zero diff-overlap with branch** (branch touches no httpx/gzip/vscode/wc-wake path) |

**Raw F+E ≈ 235 nodes — inside the stable prior-gate band (~241–259). UNRECOGNIZED failures: ZERO across all 12 partitions.**

### 2.1 Base attribution (scratch worktree @ `f77fb892`, isolation proven, removed clean)
- **Set A — the 7 job_queue failures: 7/7 PRE-EXISTING at base, verbatim signature match.** They are the mission program's documented **"7-node stale-fixture" set** (settled-rename `c482f954` shipped `'failed'→'settled'` across 5 surfaces; these fixtures not migrated; observer-guard ×4 = observer now consults `get_job_by_instance`/`per_kind_status_for` where stale asserts forbid it). **NOT caused by the defer-gate branch.** → QUARANTINE.md new family row (sweep-visible).
- **Set B — "improvement" spot-checks: hypothesis REFUTED by base data (good — better attribution):**
  - `upgrade_registration` ×2: F at base AND F at HEAD → pre-existing (mission final gate had already re-documented count=2; p01's 0F this gate = non-reproduction variance within the family, not a fix).
  - `slash_commands`: 40/40P at base AND HEAD → extinct via mission program (M2's 1F was on the mission lineage).
  - `proxy_phase1`: 8F at base, 7F at HEAD → 7/8 pre-existing; the ONE true improvement is `TestTimingColumnsFromInstance::test_started_at_sourced_from_instance_last_activity_at` (P at HEAD, F at base) — plausibly lineage-adjacent (mission program); **not claimed for defer-gate** (branch diff has zero observer/timing overlap; flagged as 🟢 observation only).
- **Set C — p10's +1 (`test_dequeue_with_instance_filter_under_concurrency`): 3/3 PASS at base solo** → context-flake (fails only under xdist partition context); sibling of QUARANTINE row 11 (`TestDequeueAtomicClaim` family). → QUARANTINE.md sibling row.

**Anything HEAD-fail/base-pass = 🔴 caused: NONE.**

## 3. W3 — Empirical Hold-Reach on prod PG (spec §5.3) ✅
Read-only session on `ensemble_prod` (`SET default_transaction_read_only=on`, 5s statement timeout; INSERT probe correctly rejected; evidence `/tmp/dg-w3/`).

| Metric | Value |
|---|---|
| Projects BUSY under OLD predicate | **0** (no active non-defer JobItems anywhere) |
| Projects BUSY under NEW predicate | **1** — `83da04de` (agents-ensemble itself) |
| Delta (held ONLY by the new clause) | **1 project, 33 rows, 2 distinct live instances** — `809e2a59` (**waiting_children**) + `8d8a5591` (**paused**) — exactly genuinely-live mission statuses |
| Blocking-mirror age range | 0.11 d – 2.25 d (no stale holds; **0 rows > 7 days**) |
| **Terminal-filter proof** | **231** ancient done-mirror-of-terminal-instance rows exist across 8 projects → **0 projects held by them** — the no-over-blocking guarantee holds at production scale ✅ |
| Background gate (system-wide) | flips **IDLE → BUSY** — driven 100% by the same 33 rows / 1 project (no cross-project starvation; follow-up #3 guard clean) — intended semantics; operator note: during active dev on `agents-ensemble`, system-wide background work is held |
| Surprises | 0 NULL-instance done mirrors; 0 dead-with-live; **1,528 orphan mirrors** (instance_id → vanished instance rows; correctly EXCLUDED by 3-valued-logic LEFT JOIN — data-integrity follow-up, not a gate issue); **270 duplicate-mirror groups** (top 56/instance — emission-path follow-up) |

## 4. Runtime Gate Matrix (integration level) ✅
New pack `test/packs/defer_gate_runtime_matrix_test.{py,sh}` (commit `ab567195`; spec in MOCK_TESTS.md) — real repositories + real gate entry points, per-scenario file-backed SQLite (WAL/NullPool; no StaticPool):

| Scenario | Verdict | Layers exercised |
|---|---|---|
| S1 settled mirror + live instance → defer BLOCKED | ✅ | A `has_active_non_deferred_work`=True · B `_defer_idle_check`=1 · C `_select_next_eligible_job`=None |
| S2 settled mirror + TERMINAL instance → ADMITTED | ✅ | A=False · B=0 · C returns candidate |
| S3 PAUSED instance → blocked (by-design, `7ecf09e2`) | ✅ | A=True · C=None |
| S4 folding layering proof — gate blocks (mission liveness) WHILE claim `claim_pending_task` t2 guard correctly proceeds (task liveness) | ✅ | Leg1 C=None · Leg2 D=proceeds — two-leg architecture documented at runtime |
| S5 self-deadlock exclusion — defer candidate's own live instance does NOT block itself (queue-type exclusion) | ✅ | A=False · C=admits (`DEFER_EXCLUDED_QUEUE_TYPES`) |

Determinism: **5/5 PASS on 5 independent executions** (authoring run + 4 re-verifications, incl. post-commit at `ab567195`).

## 5. ensure.md
- **Core #1** (no regressions in changed packs): ✅ — acceptance sets A–E + job_queue-scoped packs all PASS at HEAD (the 7 partition failures in p11 are base-evidenced pre-existing stale fixtures, now quarantined).
- **Core #2/#3** (concurrency/atomic + no sync-DB-on-loop): ✅ — `concurrency_atomic_unit_test` **98P/74S/0F** (baseline 91P/74S/0F; +7 = upstream test adds; skipped exact; 0F exact).
- **Core #4** (dev.sh graceful-shutdown flag): ✅ — `--timeout-graceful-shutdown 10` at dev.sh:102.
- **Release Gate (live-daemon E2E): NOT RUN — out of the leader's gate contract** (§4 specified integration-level runtime matrix instead; this branch's claim SQL is untouched and the runtime matrix covers the admission seam directly). Improvement notice: if the leader wants live-daemon defer E2E (`e2e_workflows` #4/#5) before merge, it is a separate ~10-min dispatch requiring `./dev.sh` + queue cleanup per ensure.md prerequisites.

## 6. Quarantine changes (this gate)
1. NEW family row: mission settled-rename 7-node stale-fixture set (p11; base-evidenced dual-commit, verbatim signatures).
2. NEW sibling row: `test_dequeue_with_instance_filter_under_concurrency` context-flake (row-11 family).
3. No un-quarantines. Standing families re-verified by exact-count parity (watchover 47, memory_integration 10, sqlite cascades, misc cluster).

## 7. Follow-ups surfaced (non-blocking, out of gate scope)
- 🟠 **7-node stale-fixture migration** (the QUARANTINE row) — the standing ledger item; owner: mission-program follow-up, not defer-gate.
- 🟠 **1,528 orphan message-mirror rows** in prod (data integrity; correctly filtered by the gate's 3-valued logic) — cleanup/reaper ticket.
- 🟡 **270 duplicate-mirror groups** (top 56 per instance) — emission hot-path investigation.
- 🟢 p01 `upgrade_registration` family showed 0F this gate vs 2F at base — within-family variance; watch.
- 🟢 Operator note: system-wide background gate is BUSY whenever any project has a live mission (currently agents-ensemble) — intended semantics, monitor for starvation complaints.

## 8. Process lessons (see LESSONS/2026-09-03-defer-gate-gate.md)
- **Advisory-to-undispatched-workers contamination:** a mid-gate drift advisory sent to 6 spawned-but-not-yet-dispatched partition workers was their ONLY message — 4 ran the advisory-referenced pack instead of their partitions (harmless here: 4× extra matrix PASS + read-only posture, but a real dispatch defect). Rule: task message FIRST; advisories only to already-dispatched workers; context-reset re-dispatch recovered all 6 at zero correctness cost.
- **Improvement attribution requires base evidence too:** 3 presumed "improvements" (fail-at-M2 → pass-at-HEAD) were refuted or re-attributed by base A/B; only base-verified deltas may be credited (and none credit to this branch).

## 9. Overall Status
- Acceptance: ✅ (5/5 sets exact; census 23/6/1; red→green proven both halves)
- FULL regression: ✅ (zero caused; zero unrecognized; band-stable F+E; all ledger families exact-parity)
- W3 empirical: ✅ (1 project newly-held by genuinely-live missions; terminal-filter 0-over-block at scale; no >7d holds)
- Runtime matrix: ✅ 5/5 (×5 runs)
- ensure.md Core: ✅ 4/4 (Release Gate E2E excluded per gate contract — disclosed)
- **TESTING COMPLETE: ✅ READY — merge cleared (`ab567195`; production code identical to reviewed `b46c9f8b` + gate test pack).**
