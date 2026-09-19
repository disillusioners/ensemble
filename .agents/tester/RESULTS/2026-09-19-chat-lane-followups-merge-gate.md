# Merge Gate: chat-lane-followups — VERDICT: **PASS** (after re-verification @ `152626a7`; initial verdict FAIL @ b702065f overturned by test-only fix commit)

**Date:** 2026-09-19
**Branch:** `feature/chat-lane-followups` @ `b702065f` (FROZEN; all runs pinned to this SHA at execution time)
**Base (merge-base with `latest`):** `3a6373ca` (= the chat-source-worker-lane merge)
**Method:** 15 workers (discovery, 11 census partitions, 3 new-pack runs incl. sibling 2-slice scope-mirror, PG harness, PG-skip verification, extraction sanity + authorized residue removal, base A/B adjudication). All `uv run python -m pytest` only, dual-layer timeouts, READ-ONLY (zero commits; the ONLY write = the leader-authorized orphan `.pyc` deletion).

⚠️ **MAIN-CHECKOUT DRIFT DISCLOSURE:** mid-gate, a foreign session moved the main checkout off `b702065f` (first to `1042de8c` favicon merge, then `cd0adb52` job-queue-hide-idle-instances). Every census worker verified `HEAD == b702065f…` at its own run time (before the drift), and the adjudicator re-verified the HEAD side via a detached worktree AT `b702065f` — results are valid and pinned. Merge should proceed from the branch ref; per shared-worktree discipline the drift was disclosed, not reverted.

---

## Verdict

**❌ FAIL — DO NOT MERGE YET.** 5 branch-introduced red node-ids (base-green → HEAD-red, proven by detached-worktree A/B at `3a6373ca`). All feature behavior, extraction invariants, PG seam, pack-split mechanics, and the 254 newly-selected integration tests are green or pre-existing — the sole blocker is one un-re-contracted test-contract class.

### Branch-introduced reds (5) — the gate blockers

**Class: superseded notify_work contract (Phase-B wake consolidation).** The branch replaced direct `worker_pool.notify_work()` calls in 3 production files with `safe_notify_all_pools` one-liners (fan-out via `pool_orchestrator.py`). Five test pins — 2 behavioral (single-pool mocks) + 3 static constitution pins on the old literal — were not re-contracted. Base: a2 file 8P / a3 file 18P / a4 file 12P — all green. HEAD: 5 red.
1. `tests/job_queue/test_a2_autopromote_notify.py::TestA2AutopromoteNotifyWork::test_autopromote_calls_notify_work_after_flip` — `notify_work call_count=0` at the single-pool mock (wake now fans via orchestrator)
2. `tests/job_queue/test_a2_autopromote_notify.py::TestA2ConstitutionStatic::test_a2_block_does_not_introduce_new_writer` — static pin: `self._worker_pool.notify_work()` literal gone from `job_recovery_service.py`
3. `tests/job_queue/test_a3_eligible_pending_sweep.py::TestA3EligiblePendingSweep::test_sweep_continues_on_notify_work_failure` — `cumulative_errors=0` (error handling moved into `_notify_all_pools`)
4. `tests/job_queue/test_a3_eligible_pending_sweep.py::TestA3ConstitutionStatic::test_a3_module_uses_existing_helpers` — static pin on `eligible_pending_sweep.py` literal
5. `tests/job_queue/test_a4_f14_orphan_detection.py::TestA4ConstitutionStatic::test_a4_block_uses_canonical_seam` — static pin on `job_feedback_observer.py` literal

Fix shape (developer): re-contract the 5 pins to the new canonical seam (`safe_notify_all_pools` / orchestrator fan-out, `pool_orchestrator.py:737`), mirroring the branch's own `feaf76ba` re-point pattern (which covered 6 files but missed these 3). NOTE: this is the second consecutive gate where a wake-contract change on this arc left un-swept pins — commit `feaf76ba`'s sweep grepped too narrowly (see Lessons).

**Production-wake health counter-evidence** (why this is test-rot, not a wake regression): NEW production enqueue→wake e2e (`test_chat_source_enqueue_wake_e2e.py`) PASS; chat pack 50/50 incl. saturation ≤3.0s + A2.2 `workers_woken_by_timeout` delta==0 (notify path); 71 re-pointed wake-site tests green; 11 wake sites verified one-liners; snapshot iteration `list(self._pools)` at `pool_orchestrator.py:712`.

---

## 1. Census (~21,400 executed)

| Partition | Result | Runtime | Reds → adjudication |
|---|---|---|---|
| P-1 unit_tools | 2691P/0F/6S | 27.7s | 0 |
| P-2 unit_services | 1808P/8F | 17.0s | 8 → pre-existing confirmed (proxy_phase1 ×7 + b1 static-pin ×1) |
| P-3 unit_subdirs+routers | 782P/0F | 14.5s | 0 — **validator family green** (exact-case 422: test_sources.py + test_source_reservation.py) |
| P-4 loose_a_d | 1705P/10F/21E | 32.1s | 31 → pre-existing (quarantine families, node-for-node) |
| P-5 loose_e_l | 1422P/22F | 74.7s | 22 → pre-existing (find_near ×13, gaia ×3, coding2 ×2, job_processor ×4); −5 count = deleted gate file |
| P-6 loose_m_r | 2096P/10F | 69.6s | 10 → pre-existing; first-order files GREEN (long_tool_nudge, recovery_wiring, claim_lane) |
| P-7 loose_s_z | 1498P/5F/2E | 34.0s | 7 → pre-existing; **`_notify_all_pools` class healed via base** |
| P-8 top_a_h | 1058P/21F/2E | 67.3s | 23 → pre-existing (SQLite trap ×9, await-rot ×2, agents/ui/enqueue ×5, chokepoint ×2 caller-list VERBATIM-identical to base, perf ×1, jsonb catalog-race ×2E, boolean-default ×1 LCA-foreign) |
| P-9 top_i_q | 2420P/58F | 55.1s | 58 → pre-existing (injection_api ×25+1, memory ×10, progressive SQLite trap ×18+1, charter ×4) |
| P-10 top_r_z_misc | 2276P/14F | 43.3s | 14 → pre-existing, decomposed exactly (skill ×2, spawn trap ×9, team ×1, orphan ×1, atomic flake ×1); **worker_notification pair GREEN; mq_redesign lane-recorder clean** |
| P-11 job_queue | 1718P/12F | 25.9s | 7 pre-existing (quarantine set, node-for-node 3rd consecutive gate) + **5 BRANCH-INTRODUCED** (the blockers above) |
| NEW sibling pack (2-slice scope mirror) | 943P/32F/5E/31S | 214s+28s | 37 → ALL pre-existing/env/flake (SQLite trap ×12, admission-mirror ×4, httpx ×5E+3F load-context, pg_type race ×1, first-timers S1-S9 all red at base too) |
| NEW opencode+e2e pack | 516P/4F | 16.8s | 4 → pre-existing (documented answer_dismiss/pause_during_report) |
| NEW chat pack | **50P/0F** | 21.7s | 0 — incl. NEW enqueue→wake e2e, saturation ≤3.0s + A2.2, boot-line, kill-switch |
| concurrency_atomic (Core #2/#3) | 98P/0F/74S | 15.6s | 0 — count-parity exact, 3rd consecutive gate |
| PG claim-lane (disposable PG14 :15441) | **9P/0F** | 1.0s | 0 — env-proven, prod-scrubbed, teardown verified |

**Adjudication coverage: 100%** (P1 5/5, P2 10/10, W1-W19 all). Base red total ≈145+, branch-introduced exactly 5.

## 2. New-selection outcome (the 254)
First-time-in-pack integration tests: 12 reds + 1 hang + 1 flake among them — **every one reproduced at base 3a6373ca** (agent_bootstrap ×2, cold_resume_ttl ×2, compaction_e2e ×2, skill_cross ×2, skill_injection ×1, migration_e2e ×3, pause_race_w7 ×1, attestation_revive hang ×1 at BOTH trees). The other ~242 newly-selected tests PASS. The 14 PG-marked entrants skip cleanly in PG-absent mode.

## 3. PG results
Claim-lane 9/9 PASS (disposable PG, PG_TEST_* explicit, POSTGRES_* scrubbed, teardown clean). PG-absent skip verification: 14/14 skipped cleanly × 2 dead-port simulations, documented skip reason verbatim; shared dev PG never touched.

## 4. Residue confirmation
Orphan `tests/unit/__pycache__/test_jobs_crud_chat_source_gate.cpython-313-pytest-9.0.2.pyc` — source-absence pre-check, deleted (leader-authorized), post-check empty, git-silent. `test/packs/fe_*` foreign artifacts left untouched (belong to live `feature/fix-source-edit-agent`).

## 5. Boot-line + extraction sanity — PASS
pool_orchestrator.py (891 lines, `PoolOrchestrator` + `safe_notify_all_pools`) is the single pool-lifecycle home; manager.py invariants verbatim (`__init__` self-assignment :1223, `_pools` alias :1196, strict delegation :6521/:6572); 11 wake sites exactly, across 9 files; `list(self._pools)` snapshot :712; boot line at orchestrator:564 with caplog pin 4/4 green; kill-switch single early-return gates both pools; 71 re-pointed tests + boot/kill-switch files 9/9 green.

## 6. ensure.md
Core #1 FAIL (5 branch-introduced). Core #2/#3 PASS (concurrency pack, exact parity). Core #4 + Important #1/#2 PASS (verified in prior gate; dev.sh + await sites unchanged by this branch — no daemon/ changes to them). Release-gate E2E live-LLM: not run (outside plan).

## 7. Action list
1. 🔴 Re-contract the 5 notify_work-seam pins (a2/a3/a4) to the orchestrator fan-out contract — then re-run P-11 (or just the 3 files) + this gate's adjudication seam. Cheap, same shape as `feaf76ba`.
2. 🟠 Register the sibling pack's runtime reality: 2-slice scope-mirror ran 214s+28s under 300s caps; the pack's own 350s/360s guards exceed the 5-min pack invariant — consider an in-script split or accept documented exception.
3. 🟢 Disclose main-checkout drift (foreign session); merge from branch ref.
4. 🟢 W19 atomic flake rate higher at HEAD (1/3 solo vs 0/3 base) — watch, pre-existing class.

**Worker instances:** disc dcff05ea · chat 5181d078 · pg 070b57fe · pgskip 4ef89570 · sanity 53da88c0 · P-1 65cdb187 · P-2 dfb4453a · P-3 abe60dfe · P-4 ea927665 · P-5 a3fc4016 · P-6 9bf0ef90 · P-7 bd3c8299 · P-8 b8484d75 · P-9 12f6386c · P-10 6bb16257 · P-11 7a328c72 · oce 64471190 · sib b63621db · adjud 63006aba

**Zero commits; single authorized `.pyc` deletion; frozen tree otherwise honored.**

---

## ADDENDUM (2026-09-19, later) — Re-verification @ `152626a7` → **FINAL VERDICT: ✅ PASS — CLEARED FOR MERGE**

The 5 branch-introduced reds were fixed in test-only commit `152626a7` ("re-contract wake-seam pins to safe_notify_all_pools (Phase-B supersession) — comprehensive census, zero stale old-shape pins"), verified direct child of `b702065f` (sole parent), exactly 3 files under tests/job_queue/, +291/−26, ZERO production files (`git show --stat` + diff-scope verified). Targeted re-verification (2 workers, pinned detached worktree at 152626a7 — the drifting main checkout was never used for execution; prod `POSTGRES_*` scrubbed pre-run): **the 3 files full green — 38/38** (a2=8, a3=18, a4=12, per-file counts exact, 2.68s) with **all 5 formerly-red node-ids PASS** in a targeted 5-selected re-run. **Census spot-audit (SHA-pinned `git grep`):** all 29 `worker_pool.notify_work()` hits classified — **zero stale positive pins**; the new canonical seam `safe_notify_all_pools` is positively pinned in a2/a3/a4/a5 (import + call-site + `site_label` + `fallback_pool`) with belt-and-braces negative pins rejecting the old inline literal. **Intent check:** a2's `call_count==1` assertion routes through the helper's `__dict__`-probe (genuine wake intent, not a shape tautology); a3 encodes the fail-soft contract in full (WARNING at helper layer + sweep continues + `cumulative_errors==0` + next-tick heals). One non-blocking note: `tests/services/test_option_b_message_branching.py:1020` self-mocks `notify_work` without routing through the new helper — pre-existing behavioral-coverage gap outside the Phase-B fixed-file scope, flagged for a future sweep. **Ref cleanup:** `feature/chat-lane-followups-mergecheck` verified a duplicate pointer AT `152626a7` (fully contained), but deletion was correctly REFUSED — it is the checked-out branch of the active gate-machinery worktree `agents-ensemble-clf-mergecheck`; both refs remain intact at `152626a7`. **Operator action:** tear down that worktree, then `git branch -D feature/chat-lane-followups-mergecheck`. The giter should merge from the BRANCH REF `152626a7` (main checkout continues to drift with foreign sessions). All initial-gate findings stand unchanged; non-blocking follow-ups carry (sibling pack 350s/360s guards vs 5-min invariant; W19 flake watch; option_b coverage gap).
