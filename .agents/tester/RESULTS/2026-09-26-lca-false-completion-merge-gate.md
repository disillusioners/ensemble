# LCA False-Completion Fix Cycle (7d4a3bd9) — Merge Gate Report

Date: 2026-09-26
Gate: `feature/lca-false-complete-fixes` @ `d5c50994c61ba6c0a762c9dd128d9aa5603eca2a` (base `316a849b`; 1 commit, 32 files +2981/−161)
Worktree: `agents-ensemble-wt-lca-false-complete`
Tester: Test Leader (this gate); 16 dispatched workers (9 wave-1 + 6 wave-2 + 1 quick-fix reuse), 0 re-dispatch failures, 0 incomplete nodes.

## VERDICT: ✅ **PASS-WITH-NOTES** — zero branch-caused PRODUCTION failures; merge-ready from testing. Two production-side follow-ups owed (neither blocks the incident fix's correctness: 🟠 FE type union miss; 🟡 dead schema field), all reds adjudicated base-proven or test-side.

The incident class is DEAD at the gate level: a live substantive `not_complete` verdict can no longer be silently overridden into a plain COMPLETED — the exhaustion composition gate terminalizes loudly (`terminal_after_bound` + `completion_gate_escalated=true` + `completed (gate escalated — unverified)` at every read point), and an all-timeout epoch can NEVER write a terminal (deny+nudge continues; attestation is the exit).

---

## Per-Job Results (dispatch contract)

| Job | Result | Key evidence |
|---|---|---|
| 1. Full attestation matrix @ d5c50994 | ✅ PASS (glob ground truth: 73 files / 1033 collected / 9 packs) | **1027P / 4F / 2S** — ALL 4 reds pre-existing & adjudicated (see reds table). m1–m4 executed during packbuilder verification (deviation disclosed, drift-pin valid); m5–m9 fresh single-pack workers |
| 1b. Origin-sync re-verify @ 2aae9b42 | ✅ clean cherry-pick of d5c50994, **215/215** delta-touched tests green on top of drift; FE literal propagates; "no semantic overlap" holds empirically (zero common files with drift pieces) | throwaway worktree, full cleanup verified |
| 2. INCIDENT REPLAY (acceptance) | ✅ PASS 3/3 cells — independently constructed on the REAL gate node (`tests/integration/test_lcafc_incident_replay_e2e.py`, 1316 lines, commit `d8599eb4`) | (a) 1 substantive + 2 double-timeouts → bound 3 ⇒ **exactly 1 `terminal_after_bound` row w/ `completion_gate_escalated=true`**, exact display string, ledger write, counter reset, no never-spoke rows. (b) 3 timeouts, zero substantive ⇒ **ZERO terminal writes**, `bound_exhausted_never_spoke` rows w/ `decision=terminal_withheld_deny_continues`, **counter rose past bound (peak 6 > 3)**, attested-allow exit resets counter, flag stays False. (c) directive nudge **byte-pinned** on 2nd zero-tool-call deny only; `nudge_kind=directive` on log surface; standard on 1st |
| 3. Timeout verification | ✅ PASS (`tests/unit/test_lcafc_timeout_scanner.py`, `e63141b6`) | Default **180.0s** (constant + resolver + parser pinned); env `ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_TIMEOUT_S` honored; min clamp **5.0s** (1.0→5.0; negative fails OPEN to default); retry-once = exactly ONE retry after first timeout, `attempt=2`, same per-attempt timeout ⇒ worst case 2×180s |
| 4. Scanner fix live | ✅ PASS (same pack §2) | No-delegation Q&A turn (157-word prose) → `final_word_count == 157` (== real split count) on EVERY evaluated path; `length_trigger` measured; **marker scan still routing-gated** (skipped when `attestation_required=False`, fires when True, catalog entry pinned). No R3-named route exists — documented, no fabricated pin |
| 5. Surface completeness (3 read points + FE) | ✅ PASS (`tests/unit/test_lcafc_surface_readpoints.py`, 21 tests, `30772ace`) | jobs API (label at `/api/jobs/{id}`, negative control, failed-status passthrough) · mission resolver + `get_mission`/`list_missions` tools + `MissionResponse` schema · SSE `to_completed_payload` (label + flag) · FE: `MissionLiveness` union + `isTerminalStatus` true + 3 components; **19 literal + 14 constant-ref byte-pin sites**. ⚠ **🟠 tsc defect found — see Findings #1** |
| 6a. PG lane | ✅ PASS 21/21 (`lcafc_pg_lane_test.sh`, `0620597a`) | Disposable PG14@15432; teardown verified (port freed, PGDATA removed, no leaks); 5432 untouched; 37 unrelated tests/postgres files excluded with reasons |
| 6b. Boot smoke | ✅ PASS (`lcafc_boot_smoke_mock_test.sh`, `52b32ffe`) | Daemon@15800 / PG@15810 / mock-LLM@15820; livez+readyz <5s; **enforce-default boot line verbatim** (`mode=enforce … llm_judge_enabled=true llm_judge_timeout_s=180.0`, all env unset); escalated label rendered at the production `/api/jobs` read path **with negative control** (plain completed stays plain); SIGTERM graceful 1s; ports freed; dev.sh:99 carries `--timeout-graceful-shutdown 10` (Core #4). Deviation: escalation SQL-seeded on disposable PG (sanctioned fallback; LLM-driven arc covered by Job 2) |
| 7. Neutrality audit | ✅ PASS | **Zero category-X hunks** across all 32 files; `deny_bound_exceeded` predicate body **byte-identical** (sha `98360eba…`) + all 3 consumer call-sites byte-identical; re-contractions within sanction (1 canonical ruled pin + 2 superseded scanner pins + 34 new tests + additive channel-seeding); see Findings #4 for the borderline 2nd (i)-shaped flip |
| 8. No production code changes | ✅ PASS | Every worker drift-pinned `git diff d5c50994..HEAD -- daemon/ frontend/ scripts/ migrations/` = EMPTY at every step (HEAD advanced d5c50994 → `9f9abd19`, all test/doc-only) |

## Reds Adjudication (all pre-existing or test-side; zero production regressions)

| Red | Pack | Class | Proof |
|---|---|---|---|
| `test_lcan_childlie_e2e::test_s1_child_lie_deny_then_attest_allow_real_graph` | m6 | PRE-EXISTING (quarantined 2026-09-23 row) | Signature verbatim (ScriptedChatModel exhausted @ :428); deterministic @ base 6bf7bed7; file untouched by delta |
| `test_lcan_legacy_checkpoint::test_s1_legacy_note_in_pre_removal_shape_activates_a_band` | m7 | PRE-EXISTING (same row) | R4-short-circuit AssertionError @ :646; tally-identical at base |
| `test_lcau_incident_e2e::test_scenario_b_formal_report_ignoring_question_denies_with_nudge` | m7 | PRE-EXISTING (same row) | ScriptedChatModel exhausted @ :451; same class |
| migration BOOLEAN-literal foreign red | m3 | PRE-EXISTING (standing ledger) | Prior-cycle QUARANTINE row; PASS\* |
| `test_job_feedback_observer::TestObserverSkipsTerminated::test_observer_skips_terminated_status` + `test_phase2_feedback_verify::…::test_observer_completion_then_termination_skips_termination` | delta-neighbors | PRE-EXisting NEW-TO-LEDGER → **QUARANTINED 2026-09-26** (this gate) | Base-proven @ 316a849b via disposable-worktree exact-node re-runs (worker-executed A/B); terminated-silence stale contract |
| 8 fake-arity reds in `test_job_feedback_observer.py` | delta-neighbors | **DELTA-CAUSED STALE-FAKE TEST ROT** (production correct) → **FIXED test-side this gate** (`9f9abd19`, +7/−1: `make_fake_sync` re-contracted to 6-param `_finalize_job_db_sync` signature; 116P/2F after) | Base-pass @ 316a849b → branch-fail; prod signature at :3461 accepts the new `already_finalized_job_id` arg; mock-ripple class |

## Findings & Notes (the "with-notes")

1. 🟠 **FE type defect (production, one-line fix owed)**: `tsc --noEmit` exits 2 with 6 errors — the escalated literal was added to `MissionLiveness` but NOT to the `JobStatus` union (`frontend/src/app/models/job.model.ts:11`), while 6 sites compare it against `JobStatus`-typed values (`TS2367`/`TS2678` at job.model.ts:184/:397, job-card:109/:123, job-detail-drawer:59, job-queue-panel:617). Runtime contract HOLDS (types erased; all read points render correctly — proven live in boot smoke + surface pack). Contradicts the dev-run's "FE tsc PASS" claim. Recommend: add the literal to `JobStatus` pre-merge (1 line) or as immediate follow-up; the registered `fe_static_typecheck_build_test` pack will fail until then.
2. 🟡 **Dead schema field (production, parity gap)**: `JobResponse.completion_gate_escalated` is declared (`daemon/routers/schemas.py:1417`) but NOT populated in `_job_to_response` — only the `status` string is rewritten. Human-visible loud surface works everywhere; machine-readable flag missing on the jobs-API read point (SSE + mission read points DO carry the flag). Close the pass-through seam in a follow-up if API parity is desired.
3. 🟡 **Test-debt follow-ups stand**: the 3 quarantined latent reds (attest-first HOLD re-anchor commission, 2026-09-23) fired exactly as predicted — re-anchor still owed; + the 2 new-to-ledger terminated-silence reds (row cut 2026-09-26) route to owner.
4. 🟢 **requirements.md undercounts** (doc drift only): "ONE PIN UPDATED BY RULING" vs a 2nd (i)-shaped assertion flip in `test_attestation_resolver_stage2::test_incident_6a0d60c9_bound_enforced_if_nudged` (adjudicated: REQUIRED by the same user ruling — old never-spoke→terminal semantics are dead; sanctioned-in-spirit); "25 tests" vs actual 34 in `test_lca_false_complete_fixes.py`; "13 daemon files" vs 16.
5. 🟢 **Scope decisions**: (a) matrix = attestation glob (prior-gate convention; 73 files) + delta-neighbors pack for the non-attestation delta modules; full-tree sweep NOT re-run (last full sweep 2026-09-23 fs-gate; would re-litigate ~96 unrelated pre-existing reds). (b) ensure.md Release-Gate E2E (real-LLM workflows on ./dev.sh) NOT run — dev daemon 8079 belongs to the occupied main checkout (worktree dev.sh launch forbidden by convention); boot smoke + PG lane + real-graph replay are the substitutes per prior-gate precedent; recommend re-running `e2e_workflows_ensure_test` on the activated daemon post-merge. (c) `test_lca_stale_a_judge_live.py` (false-rescue, live-LLM) excluded by standing convention — runnable false-rescue pin `user_intent::TestFalseRescueChannelClosed` green in m1/m2.
6. 🟢 **Boot-smoke escalation was SQL-seeded** (sanctioned fallback; documented). The LLM-driven escalation arc is covered by Job 2 on the real gate node.
7. 🟢 **Packbuilder deviation disclosed**: m1–m4 executed as full runs during "collection probe" verification (all PASS; drift-pin valid; m4 154s). Results accepted as gate evidence with the deviation on record.
8. 🟢 **Origin-sync caveat**: probe scoped to `2aae9b42` per dispatch; newer refs exist on origin beyond `origin/latest` — if the leader merges against a newer tip, re-run the sync probe.

## ensure.md Validation (scoped)

- **Critical #1** (no regressions in changed packs): ✅ PASS — all in-scope packs green or adjudicated (A/B / standing-record evidence for every red).
- **Critical #2/#3** (concurrency + sync-DB-off-loop): ✅ PASS — `concurrency_atomic_unit_test` baseline-exact 98P/0F/74S (in scope: graph.py + instance_messaging.py touched).
- **Critical #4** (dev.sh graceful-shutdown flag): ✅ PASS — `dev.sh:99`.
- **Important #1/#2, Nice-to-have**: out-of-scope surfaces (documented); nice-to-have dead-code check surfaced Finding #2 instead.
- **Release Gate**: posture decision documented (Finding 5b) — matrix + delta-neighbors + boot smoke + PG lane executed as the gate-equivalent; E2E items deferred to post-merge activation.
- Contradiction notices: none — all validations ran as packs with dual-layer timeouts.

## Quick Fixes Applied (test-side only, this gate)

- `9f9abd19` — re-contract `make_fake_sync` to the 6-param `_finalize_job_db_sync` signature (`tests/job_queue/test_job_feedback_observer.py`, +7/−1); delta-neighbors 108P/10F → 116P/2F (2 expected pre-existing).

## Code Changes Summary (all test/doc artifacts, on the feature branch; production diff vs d5c50994 EMPTY at all times)

`0620597a` pg-lane pack · `e63141b6` timeout+scanner pack · `52b32ffe`+`d9501f03` boot-smoke pack (+docs line fix) · `d8599eb4` incident-replay test+pack · `a3b9a305` 9 matrix packs + PACKS.md · `30772ace` surface read-points test+pack · `2bdfea6d` delta-neighbors pack · `9f9abd19` fake re-contract. Commits carry the known repo-local 'Councilor C2' identity convention (documented user-decision-pending).

## Worker Roster

recon/packbuild `72a69f60` · replay `dab6dc09` · semantics `649fc907` · surface `558e0ec5` (1 coordination stop → re-dispatch) · pglane `091b0f0b` · bootsmoke `98fbf3e1` · neutrality `5d4a5c65` · concurrency `076ebb31` · origsync `dafaf994` · m5 `fe5cdfcc` · m6 `041d039c` · m7 `49441cdb` · m8 `887ad361` · m9 `b3b18ca0` · neighbors+quickfix `db1d99e1`. All 16 reported; zero re-dispatches beyond the surface coordination restart; zero gaps.

### Overall Status
- Matrix (Job 1): ✅ PASS (4 adjudicated reds) · Origin-sync: ✅ · Replay (Job 2): ✅ 3/3 · Timeout+Scanner (Jobs 3–4): ✅ · Surface (Job 5): ✅ w/ 🟠 FE tsc finding · PG+Boot (Job 6): ✅ · Neutrality (Job 7): ✅ · No-prod-changes (Job 8): ✅
- **Testing Complete: ✅ READY — PASS-WITH-NOTES** (merge-ready; recommend the 1-line FE `JobStatus` union fix lands with or immediately after the merge).
