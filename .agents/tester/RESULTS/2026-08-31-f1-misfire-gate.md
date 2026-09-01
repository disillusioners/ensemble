# Test Gate: f1-Misfire Fix — feature/f1-misfire-fix @ e6cd5fc8

Date: 2026-08-31
Gate: independent fix gate closing the 2026-08-31 production misfire (JobFeedbackObserver._trigger_next_job omitted work_id → fresh-UUID mint → f1 get_by_work_id miss → task-is-None misread as orphan → active→dead while instance 28c6421b's subtree legitimately worked). Gate range **e863f010..e6cd5fc8** (3 commits: 04fd0c52 f1-misfire batch — observer work_id mint + subtree-alive guard + durable terminal_reason + ENSEMBLE_ORPHAN_F1_ENABLED kill-switch (f1-only, default ON) + wipe WARN + 7 tests; 96a66e50 tz-normalization (zombie-silently-never-fires) + isolation + wipe-probe status filter; e6cd5fc8 W1 fold — 2 re-spawn mint sites + _assert_linkage_contract (4 sites) + guard-after-grace reorder + kill-switch detail pin). Production tip e6cd5fc8; gate-authored test-only commits on top (5026ff53, 778034c1, 238a14b5 — all `test:` prefix, daemon/ byte-identical, verified per worker).
Worker dispatches: 13 (≤3 concurrent).
Change set (recon): 12 files, +1571/−29 — daemon/services ×5 (job_recovery_service, job_feedback_observer, job_processor, instance_messaging [comment-only], messaging_types), repositories ×2 (task, instance), api.py + manager.py + config.py wiring, docs/setup.md env row; tests: ONLY tests/job_queue/test_orphan_active_job_recovery.py (+898/−4, 21→31 tests). No pack scripts from dev → gate authored 3 packs.

## VERDICT: ✅ PASS — ALL 8 VERIFICATION SCOPES GREEN; MISFIRE CLASS DEAD, ZOMBIE MISSION ALIVE; CLEARED FOR MERGE

Kill-switch default ON = new behavior live immediately on deploy (restart picks it up). The guard + mint fix protect from the misfire class on day one.

---

## Scope Decision

Range touches recovery machinery (job_recovery_service f1 sweep + observer/processor dispatch seams + repos + boot wiring). Scoped to 6 regression packs: job_queue anchor + the changed file's pack (orphan suites) + core (manager/api/config) + concurrency (lock semantics) + completion (observer seam) + api (boot wiring). Skipped: tools/prompt/ri-*/wc_wake packs (zero code overlap). Full suite not warranted — single-pattern surgical fix; breadth carried by job_queue (1579) + core (714).

## 1. Full regression — 6/6 packs, 0 new failures

| Pack | Result | Baseline delta |
|---|---|---|
| job_queue_unit_test | ✅ 1579P/0F/38S (37.6s) | **EXACT ANCHOR** (1569 + 10 new f1 tests, all accounted) |
| orphan_active_job_recovery_suites | ✅ 51P/0F (1.65s) | exact per recon math (41 + 10); per-file reconciliation 31+3+9+8 |
| core_unit_test | ✅ 714P/4F/30S (21.3s) | PASS-by-baseline: 4F all quarantine-matched (agents_api ×2 + migration-cascade ×1 + models_split ×1 — the 4th is a long-dormant count-literal RE-ACTIVATED by an upstream error-code add between P2 gate and this base; test file untouched by range, branch-exonerated via git diff) |
| concurrency_atomic_unit_test | ✅ 98P/0F/74S (8.8s) | EXACT — ensure Critical #2/#3 upheld; lock-before-transition ordering source-verified |
| completion_regression_test | ✅ 96P/0F/37S/1-des (2.5s) | EXACT, deselect held |
| api_unit_test | ✅ 211P/8S/2F (13.2s) | PASS-by-baseline: 2F = the handoff's KNOWN OWN-TICKET (tests/test_api.py mock-drift ×2, `Mock can't be used in 'await'` at messages.py:258) — exact-match to pre-flag, files untouched by range, deterministic (no flake re-run per protocol) |

Quarantine families all dormant: watchover 47, proxy_phase1 ×8, archive ×5, 17-test misc, dequeue flake, ab_resolution — none surfaced.

## 2. Incident replay end-to-end — PROVEN (scope 2)

Pack `f1_behavioral_real_engine_test` (gate-authored, 5026ff53): real JobRecoveryService driven through the REAL `reconcile_drift_states` entry, file-backed SQLite, real repos. Exact incident shape seeded (leader WAITING_CHILDREN + grandchild RUNNING + driving Task with MISMATCHED work_id + graces cleared):
- **f1 SKIPS**: JobItem stays ACTIVE (not dead); skip detail `orphan_active_skipped_tree_alive` recorded; seeded lock row INTACT.
- **The 11:38:18 WARNING-class now reads as SKIP** (scope 8 capstone): ALL 5 text-class substrings asserted in real caplog — "Pattern (f1) skip", "no Task linked via work_id", "lineage tree is ALIVE", "f1-misfire class (incident 2026-08-31)", "Verify the dispatch path carried work_id=job_id".
- **Subtree completes → correct terminal**: follow-up pass after grandchild terminal + Task completed asserts terminal_reason ≠ 'pattern_f1_orphan' (the strongest invariant — f1 never stamps the misfire reason on legitimate completion). Honest note: the harness's unwired bus keeps f2's fail-safe holding ACTIVE on the second pass — the gate claim "f1 didn't kill live work" holds; post-completion terminal routing is best-effort evidence per spec.

## 3. Zombie preservation — PROVEN (scope 3)

Same pack, Scenario 2 (802095d8-class: stale-running instance 7200s, no live tasks, dead tree, past grace, no linked Task): **f1 FIRES** — admission_state dead; lock RELEASED (fresh-engine read); `terminal_reason='pattern_f1_orphan'` **DURABLE** (proven via fresh `SQLModelSession(engine)` read, not the sweep's connection); detail `orphan_active_no_task_dead`. The ORIGINAL mission is intact.

## 4. Kill-switch matrix — PROVEN (scope 4)

Pack `f1_killswitch_tz_matrix_test` (238a14b5), 32P/0F:
- **Env spellings ×11** (real zombie driven through the sweep, cache reset per case): unset/"1"/""/"garbage" → ON (garbage + one-shot WARN); "0"/"false"/"off" → OFF — JobItem STAYS ACTIVE with the pinned detail key **`orphan_active_skipped_f1_disabled`** (exact string asserted; the operator/log-scraper contract). Resolver truth-table matches the documented contract at job_recovery_service.py:117-155.
- **f1-ONLY scope**: f2 fires under BOTH switch states (wrapped dev test + ON-state mirror authored). Recovery-33P file-level parity: **33P = 33P identical under ON and OFF** — the file-level proof that patterns a-e are untouched. Structural: all 10 range diff-hunks land OUTSIDE the a-e/f2 anchor bands (the 2 near-band hunks are the documented Pattern-f comment reword + the f1 kwarg at the call site).
- Kill-switch OFF skips BEFORE any f1 work (no instance lookup, no tree queries — no wasted cycles).

## 5. tz correctness — PROVEN (scope 5)

Same pack Matrix C (+ behavioral pack S3):
- AWARE stale → fires. NAIVE stale (the documented PG read-back shape, instance/repository.py:2180-2184) → **still fires** — the zombie-silently-never-fires regression class stays dead (root cause was TypeError `can't compare offset-naive and offset-aware` at the leg-2 comparison, caught by the per-row handler → silent per-cycle skip; the 96a66e50 normalization closes it; RED-proven at 04fd0c52 with the exact TypeError trace).
- FRESH naive (within 900s) → guard treats tree as ALIVE → SKIP (window not inverted by normalization).
- Malformed ISO → honest Outcome A: column-level decode fails upstream in the instance-lookup path → `orphan_active_skipped_no_deps` family (missing-instance surface owns it, not f1) — defensible, now a permanent regression pin. No live PG needed (SQLite text column IS the PG read-back shape — documented).

## 6. Mint contract — PROVEN (scope 6)

Pack `f1_mint_contract_test` (778034c1):
- Dev wrap 4/4: observer dispatch carries work_id + observer tripfire WARN; crash-recovery + orphan-resume re-spawn carry work_id.
- Structural 3/3: 7 `_assert_linkage_contract` matches (1 def + 4 call sites + imports — observer:3880, processor:977/:1057/:1278); the enqueue_message_job structural-safety comment verbatim present ("no re-mint seam to trip over", instance_messaging.py:2140-2149); source= labels Observer ×1 + JobProcessor ×3.
- **Processor tripfire behaviorally TESTED** (gate-authored 55-line test, the dev-suite gap): crash-recovery branch + mismatched AsyncMessageResult.job_id → WARN "LINKAGE CONTRACT" + "JobProcessor" fires AND dispatch never fails (WARN-never-fail semantics, await_count == 1). Root-cause repair confirmed at source (observer enqueue passes work_id=started_job.job_id; both re-spawn sites pass proc_job.job_id).

## 7. Mock fidelity + red-green — CLEAN / BINDING (scope 7)

- **Red-green** (3 worktree spots, daemon.__file__ provenance, editable-.pth hazard neutralized, all worktrees cleaned):
  - @ e863f010 (base): 8/10 RED with verbatim assertion evidence (incident-replay "live subtree ... was DEAD-finalized"; terminal_reason None; kill-switch AttributeError; mint kwargs missing work_id ×3; tripfire Captured: []).
  - **2 zombie tests GREEN-at-base — honestly adjudicated VACUOUS-AT-BASE** (pre-fix f1 fires unconditionally on zombies, coinciding with the post-fix assertion). Their discriminating moment is post-guard: verified — the tz test REDs at 04fd0c52 (exact TypeError root-cause trace) and the pair carries teeth from 04fd0c52+ where a guard bug COULD suppress the fire. The rigorous zombie coverage lives in the dedicated packs (behavioral S2/S3 + tz Matrix C) — these are belt-and-braces spots. Not a blocker; documented.
  - @ 04fd0c52: tz-naive test RED (the 96a66e50 bug proven). @ 96a66e50: both re-spawn tests RED (the e6cd5fc8 fold proven necessary).
- **Mock fidelity** (3 files read fully): **CLEAN overall** — 0 internal-under-test mocks (no mocking of the guard queries, atomic_transition, lock release, or the tripwire helper), 0 vacuous asserts (await discipline verified on every async test), boundary-only collaborators (jq/manager facades, dependency-bus stub, caplog), conjuncts honestly constructed (graces genuinely cleared, both guard legs set/cleared per scenario), durability via fresh-session reads, file-backed SQLite discipline (explicitly not StaticPool, rationale commented). **1 LOW hygiene finding**: dev test file lacks an autouse `_reset_orphan_f1_for_tests` fixture (manual try/finally today — correct but brittle if a future test touches the env var; mirror the gate pack's autouse pattern at f1_behavioral_real_engine_test.py:873-877). 🟢 follow-up, not churned in-gate.

## 8. Capstone (log-shape replay) — PROVEN (folded into scope 2)

The exact historical WARNING line class (the kill that fired at 11:38:18) now emits as the 5-substring SKIP text class on the real sweep — asserted verbatim in Scenario 1 (see §2).

## ensure.md Validation Results

- **Critical 4/4**: ✅ #1 no regressions (6/6 packs, 0 new — own-ticket ×2 + quarantine ×4 pre-existing); ✅ #2 lock/deadlock (concurrency exact, thread-identity green); ✅ #3 no sync DB on loop (10 off-loop tests + all new f1 DB access to_thread-wrapped: instance get :2476, tree_ids :2651, leg-1 :2660, leg-2 :2666); ✅ #4 dev.sh flag (grep 2 matches).
- **Important 2/2**: ✅ await-callers (3 symbols all awaited; 0 new async defs in range — 7 new defs all sync, reconcile_drift_states awaited at api.py:1194, emit_orphan_f1_boot_log sync-and-correct); ✅ deadlock scenario (concurrency pack).
- **Nice-to-have 1/1**: ✅ no dead code (kill-switch used at 6 sites; zero def removals; wipe-probe wired at manager.py:633 with try/except boot guard; removed lines = comments + consolidated import + correctly-stopped dead-writes to _REMOVED_JOB_COLUMNS members).
- Release Gate: NOT TRIGGERED (surgical single-pattern fix; scoped run). Contradictions: NONE.

## Gate-authored artifacts (all committed, test-code only)

| Commit | Artifact |
|---|---|
| 5026ff53 | test/packs/f1_behavioral_real_engine_test.{py,sh} (5 tests) |
| 238a14b5 | test/packs/f1_killswitch_tz_matrix_test.sh + tests/job_queue/test_f1_killswitch_tz_matrix.py (29 tests) |
| 778034c1 | test/packs/f1_mint_contract_test.sh + tests/job_queue/test_f1_mint_processor_tripfire.py (1 test) |

PACKS.md: 3 new pack rows + job_queue/orphan anchor stamps. QUARANTINE.md: no new rows (no new flaky failures).

## 🟢 Notes for leader (non-blocking)

1. **Deploy note**: restart picks up the fix; kill-switch default ON = guard + mint fix live day one. Instant revert = `ENSEMBLE_ORPHAN_F1_ENABLED=0` + restart (proven fully inert with pinned detail key).
2. **Residual false-skip window** (adjudicated correct by council): a zombie whose tree shows fresh activity lingers ~until activity ages past 900s (`f1_tree_activity_max_age_seconds`, config.py:1096-1116) — lingering-ACTIVE is strictly safer than killing live work; retried every 300s cycle.
3. tests/test_api.py mock-drift ×2 = standing own-ticket (one-line AsyncMock fix, owner's call).
4. LOW hygiene: autouse kill-switch reset fixture missing in the dev test file (mirror pattern exists in gate pack).
5. models_split count-literal (test_error_codes_values `assert 19 == 18`) re-activated by an upstream error-code add — quarantine family row already covers the class; the literal fix belongs to the error-code owner.

### Overall Status
- Regression ✅ 6/6 (anchor EXACT 1579; 0 new) · incident replay ✅ (SKIP + 5-substring WARN class + lock intact + no misfire reason on legit completion) · zombie ✅ (FIRES + durable reason + lock released) · kill-switch ✅ (11-row matrix, f2/a-e untouched both states, detail key pinned) · tz ✅ (4 shapes, naive-PG-read-back fires) · mint ✅ (4 sites + tripwires behaviorally + structural comment) · fidelity/red-green ✅ (CLEAN; teeth at the discriminating commits, 2 vacuous-at-base honestly documented) · capstone ✅ (log class = SKIP)
- ensure.md Core 4/4 + Important 2/2 + Nice-to-have 1/1; contradictions NONE
- **Testing Complete: ✅ READY — giter merges. Deploy note: restart activates; kill-switch default ON.**
