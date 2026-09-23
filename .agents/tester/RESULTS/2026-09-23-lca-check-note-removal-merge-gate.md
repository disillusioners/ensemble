# LCA Completion Check Note Removal — Merge Gate Report

Date: 2026-09-23
Gate: `feature/lca-remove-check-note` @ `ff9eb8492a05aad485a17d94da12fe865bde0342` (base `6bf7bed7`, single subtractive commit)
Worktree: `agents-ensemble-wt-lca-note-rm2`
Tester instance: this gate (Test Leader); worker instances listed per lane below.

## VERDICT: ✅ **PASS-WITH-NOTES** — zero branch-caused failures; merge-ready from testing.

The subtractive change does exactly what it claims: (b)/(d)-with-pending Completion Check Note injection is REMOVED (log-only: `allowed_legitimate_pending_wakeup` + `resolver_outcome=allow_hint` + `would_be_route=allow_hint`), the `completion_check_note` stable-id kind is retired (5→4), the Final Report Reminder family is untouched and still fires live, deny/bound/escalation/watchdog protection is intact, and nothing else moved.

---

## Summary

| Lane | Result | Key evidence |
|---|---|---|
| Job 1 — Full attestation matrix (9 packs, glob ground truth) | ✅ PASS | 1,066 reported tests; 3 unexpected reds ALL base-proven pre-existing; 2 documented reds accounted (1 fired, 1 green-side) |
| Job 2 — b2f4dae9 regression, independently constructed | ✅ PASS | Real-graph `ainvoke`, 3 cells; zero injected messages (message-count +0); full surviving log row asserted |
| Job 3 — Family separation live | ✅ PASS | HOLD→Final Report Reminder injects (2 shapes); census 314 hits / 0 live code refs; kind retirement ValueError |
| Job 4a — PG lane (dev's unrun scope) | ✅ PASS | 21/21 on disposable PG14@15432, teardown verified |
| Job 4b — Boot smoke | ✅ PASS | enforce default (`DEFAULT_MODE="enforce"` @ attestation_resolver.py:111), clean SIGTERM, zero note mints |
| Job 5 — Neutrality audit | ✅ PASS | 17 files / +1518−1080, every hunk → (a) removal / (b) log-only / (c) retired surface; ZERO category-(d) |
| Job 6 — No production changes | ✅ PASS | `git diff ff9eb849..HEAD -- daemon/ scripts/ migrations/` EMPTY across all 6 gate commits |

Workers: recon `ae43411f` · packbuilder `ccc70ace` · j2-regress `5e2f787a` · j3-family `5be1caea` · j4a-pg `301251df` · j4b-boot `8ae04c28` · matrix m1–m9 `0bc3b8fe`/`66005c3c`/`fa8ba9ca`/`0ff9f2ba`/`ab26119a`/`19215a83`/`f8f7a459`/`1bad9c6d`/`8d1a8d6e` · base legs `0e1f0ff7`/`46d54ad1`. All 17 dispatched workers reported — zero re-dispatches, zero incomplete nodes, zero gaps.

## Scope Decision

Full attestation glob WAS warranted (dispatch Job 1 mandates it): 77 files enumerated, 73 partitioned into 9 matrix packs (1,063 probe-collected / 1,066 reported — parametrize expansion at runtime). Excluded with reasons: 1 postgres file → dedicated PG-lane pack (Job 4a); 2 default-addopts-DESEL files (stale_a_judge_live, lcan_sameturn_window — not delta-touched, not in prior lcan_matrix packs). PG lane scoped to the single attestation-surface PG file (delta touches no repo/migration/SQL layer); remaining 37 tests/postgres/ files out of scope for this subtractive gate. ensure.md Release Gate NOT warranted (small subtractive change).

## Job 1 — Matrix detail

| Pack | Tests | Result | Time |
|---|---|---|---|
| 1_unit_a (incl. 6 NEW witness tests) | 341 | PASS 341/341 | 2.7s |
| 2_unit_b (delta-touched unit files) | 467 | PASS 467/467 | 4.1s |
| 3_migration | 18 | PASS\* 17/18 — documented foreign red fired (20260915_120000 boolean-literal; base-identical, QUARANTINE line 78) | 2s |
| 4_intf_fast_a (incl. ship-together pinned e2e ×2) | 20 | PASS 20/20 | 159s |
| 5_intf_fast_b | 33 | PASS 33/33 (after inner-deadline fix, see Quick Fixes) | 220s |
| 6_intf_fast_c | 32 | PASS\* 28P/2F/2S — both reds base-proven pre-existing | 39s |
| 7_intf_marker (delta-touched trio) | 37 | PASS\* 36/37 — red base-proven pre-existing | 2s |
| 8_intf_real_judge_s1 (live LLM, judge ENABLED) | 4 | PASS 4/4 — documented stochastic red green-side | 203s |
| 9_intf_s2 | 114 | PASS 114/114 | 203s |

The 2 skips in pack 6 are branch-name drift-pin conditional skips in `test_attestation_stage3_zoo_test.py` (:251/:473, expects `feature/lca-resolver-stage3` prefix) — benign, tally-identical at base.

### Unexpected reds — adjudication (ALL base-proven, NONE branch-caused)

| Node | Signature | Base proof |
|---|---|---|
| `test_lcan_childlie_e2e.py::test_s1_child_lie_deny_then_attest_allow_real_graph` (:428) | ScriptedChatModel exhausted at 5th turn: post-attest clean-call HOLD injects Final Report Reminder (base graph.py:5984 ≡ tip :5831) | Solo ×2 FAIL at 6bf7bed7 (deterministic); tip identical turn-for-turn; test_s1 untouched by delta (only test_s3 re-anchored) |
| `test_lcan_legacy_checkpoint.py::test_s1_legacy_note_in_pre_removal_shape_activates_a_band` (:646) | Asserts pre-attest-first "attested 2nd turn plain-allow, no injection" (R4 short-circuit); actual: `decision=hold` + reminder injection | Full pack-shape tally byte-identical 2F/28P/2S at base AND tip; file diff base→tip EMPTY |
| `test_lcau_incident_e2e.py::test_scenario_b_formal_report_ignoring_question_denies_with_nudge` (:451) | Same class — fixture under-cans the post-attest HOLD reminder 5th turn | Same A/B; file diff EMPTY |

Class: **attest-first HOLD stale-contract rot** — these tests encode semantics that `b0a4f21d` + ship-together (already in base) replaced with HOLD-until-standalone-report + counter-independent reminder. Latent because `test_lcan_childlie_e2e.py` is `-m integration`-deselected under default addopts (dev lanes never ran it). Quarantined (QUARANTINE.md 2026-09-23 row). **Test-debt follow-up (out of gate scope): re-anchor the 3 tests to the attest-first HOLD contract + extend scripted fixtures by one turn.**

## Job 2 — b2f4dae9 regression (commit `cde5347d`)

New `tests/integration/test_lcancheck_b2f4dae9_regression.py` (1,253 LoC) through the REAL compiled graph (`ainvoke`), independent of the dev's unit witness. Cells:

- **S1 healthy-busy (RUNNING child, "awaiting" lexical FP, judge not_complete)** → `allowed_legitimate_pending_wakeup`; **message-count +0**; full surviving log row: `band=a_suspicion terms_fired=a_suspicion a_advisory_present=True … judge_invoked=True judge_verdict=not_complete resolver_outcome=allow_hint would_be_outcome=would_hint` + gate line `allowing END (log-only — note hint retired 2026-09-23, would_be_route=allow_hint)`; ledger counter untouched; judge budget 1/cell.
- **S2 PAUSED-child** and **S3 en-route-only** suspect shapes → also R2-allow WITH judge consulted (PAUSED is unconditional-live per `manager.py:9168`; en-route-only satisfies `pending_children` via dependency-watcher).

Dispatch-wording deviations (encoded code-correct, evidence in test docstrings): (1) S2/S3 resolve R2-allow, not deny — **deny-side machinery (denied_count/deny_bound/watchdog) confirmed intact and byte-identical**; deny fires when R2 inputs are absent (covered green in packs 7/8); the removal did NOT weaken completion protection. (2) Busy=0 shapes carry `band=marker` (B wins band label) while Source-A still participates in `terms_fired` — per `attestation_resolver_activation.py:1073-1078`. Coverage-survey gaps table provided (4 existing suites; this file adds real-graph + production-drain Source-A + full log-row schema).

## Job 3 — Family separation (commit `3a30f4f9`)

New `tests/integration/test_lcancheck_family_separation.py` (9 tests): HOLD injects `[SYSTEM CONTEXT: Final Report Reminder]` live in BOTH clean-attest and bundled-attest (c5d9a38a) shapes with stable-id `attestation_final_report_reminder:<iid>` and counter+1; reminder bodies scanned against all 6 retired needles = 0 matches; `completion_check_note` kind → `ValueError` while all 4 surviving kinds mint; whole-tree census 314 hits → **0 in live `daemon/` or `frontend/`** (all hits: test pins / planning history / retirement-witness docstring / docs).

## Job 4a — PG lane (commit `92dfbe85`)

`tests/postgres/test_attestation_live_descendants_pg_lca.py` 21/21 in 3.23s on disposable PG14 @15432 (initdb trust, homebrew postgresql@14; `env -u POSTGRES_* -u DATABASE_URL` + the 5 `PG_TEST_*` vars per conftest contract; `--override-ini="addopts=" -m postgres`). Teardown verified: port freed, PGDATA removed, no leaks; 5432 (shared dev DB) never touched.

## Job 4b — Boot smoke (commit `e9d167ff`)

Daemon @127.0.0.1:15800 on disposable PG@15810 + mock LLM@15820, `DATA_DIR=/tmp`; `/livez`+`/readyz` 200 <5s; attestation boot line verbatim: `mode=enforce … attestation_enabled=true llm_judge_enabled=true` with all env vars unset (matches `DEFAULT_MODE: Literal["enforce"] = "enforce"` @ `daemon/services/attestation_resolver.py:111`); MINT_HITS=0 across all 5 note shapes; SIGTERM → graceful exit 1s; ports freed; full teardown. Freebie static check: dev.sh:102 carries `--timeout-graceful-shutdown 10` (ensure.md Core #4).

## Job 5 — Neutrality audit (recon `ae43411f`)

17 files (+1518/−1080): every hunk mapped to (a) note-injection removal [graph.py mint-site + injection block @5850-5860], (b) (b)/(d)-pending → log-only [@5497-5531], (c) retired surfaces (constants/factory/citation helper/kind 5→4/test re-anchoring/docs/D-entry). **Zero category-(d) hunks.** Final Report Reminder family verified untouched (constants :4698/:4710/:4725/:4754, HOLD injection path, cap fall-through, stable-id kind).

## ensure.md Validation (scoped)

- **Critical #1 (no regressions in changed packs)**: ✅ PASS — all in-scope packs green; 3 reds base-proven pre-existing → quarantine-aware exclusion with A/B evidence.
- **Critical #2/#3 (concurrency)**: OUT OF SCOPE — delta touches no concurrency surface (gate region + context_messages only); documented, not silently skipped.
- **Critical #4 (dev.sh graceful-shutdown flag)**: ✅ PASS (static evidence, dev.sh:102).
- **Release Gate**: not warranted (small subtractive change; no architecture/cross-module impact).
- Contradiction notices: none — all validations ran as packs with dual-layer timeouts.

## Quick Fixes Applied (test-infra only)

- `afdee41f` — matrix_5 inner deadline 240s→280s (splitter-estimate miss; all 33 tests proved green in diagnostic rerun; outer 300s cap and dual-layer discipline intact).

## Code Changes Summary (all test-artifact, path-scoped, on the feature branch)

`92dfbe85` pg pack · `e9d167ff` boot pack · `953bde59` 9 matrix packs + worktree PACKS.md · `3a30f4f9` family test+pack · `cde5347d` regression test+pack · `afdee41f` matrix-5 fix. **Production diff vs ff9eb849: EMPTY at all times** (verified per-commit by every worker's drift-pin). Commits carry the known repo-local 'Councilor C2' identity (documented user-decision-pending convention — flagged, not edited).

## Notes & Follow-ups (the "with-notes")

1. 🟠 **Test-debt commission**: re-anchor the 3 quarantined latent-red tests to the attest-first HOLD contract (fixtures need +1 scripted turn; legacy_checkpoint s1 expectation must flip from R4-plain-allow to HOLD+reminder). They red on every commit until fixed.
2. 🟢 **Convention drift**: blueprint (f) says `ENSEMBLE_TEST_PG_URL` is the daemon-boot var, but boot smoke found the daemon's postgres-vs-sqlite auto-detect reads `POSTGRES_HOST+POSTGRES_DB` (`daemon/ensemble_config.py:82/124-135`); disposable-PG boot achieved via explicit `POSTGRES_*` + `env -u` (proven lcan pack pattern). Blueprint/docs should be reconciled.
3. 🟢 **Estimates calibration**: splitter header estimates 2.5–40× under actual for integration packs — re-baseline before pack reuse.
4. 🟢 venv-only `psycopg2-binary` install disclosed (boot smoke; precedent-sanctioned).
5. 🟢 Main checkout was on a foreign branch (`feature/fs-tool-guardrails` @ 948c0f06, concurrent session) throughout — all gate work confined to the dedicated worktree + detached throwaways; no contamination.

### Overall Status

- Matrix: ✅ PASS (0 branch-caused) · Regression: ✅ PASS · Family: ✅ PASS · PG: ✅ PASS · Boot: ✅ PASS · Neutrality: ✅ PASS · Production changes: NONE
- **Testing Complete: ✅ READY — PASS-WITH-NOTES (merge-ready from testing; activation of the removal still requires daemon rebuild+restart per repo convention).**
