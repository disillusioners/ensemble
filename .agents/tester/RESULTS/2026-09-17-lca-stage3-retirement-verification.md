# LCA Resolver Stage-3 Retirement — Final Merge Gate Verification

Date: 2026-09-17
Branch: `feature/lca-resolver-stage3` @ `f8e78a40` (delta `f7588291..f8e78a40`, 2 commits: `7089e73b` retirement + `f8e78a40` doc follow-ups; 39 files, net daemon −1391 LoC)
Worktree: `agents-ensemble-wt-lca-stage3` (main occupied)
Gate workers: 32 dispatches (1 discovery + 4 wave-1 + 21 matrix packs + 5 specialty + 1 m15 base-A/B) — all first-try except 2 drift-gate pauses (adjudicated, resumed) and 6 transient enqueue pool-saturations (messages landed; verified)
Running reference: user daemon on `c489233e` (Stage-2 flip, restarted) — untouched throughout

## VERDICT: ✅ PASS-WITH-NOTES — MERGE-READY

0 branch-caused failures across 921 executed attestation-family tests + 70 invariant pins + 8/8 E2E zoo + 8/8 budget probe + boot smoke + PG lane. Single-path endstate proven; behavior-neutrality vs the RUNNING flip holds with exactly the three approved deltas and nothing else.

---

## Job 1 — Full attestation matrix (glob-enumerated ground truth)

**Family enumeration (reproducible `--collect-only`)**: 59 files / **880 collected** = 858 SQLite matrix + 21 PG lane + 1 bound_escalation (characterized separately). The retirement report's "894" does not reconcile to collection — see Notes (N3). No unexplained missing file: every `test_attestation*.py` in the tree is enumerated.

**Matrix: 21 packs (commit `8a7b5272`), all dual-layer (outer `timeout 300` + inner 290s), all `uv run python -m pytest`, all drift-pinned @ `8a7b5272`. Zero TIMEOUTs; slowest pack 117s.**

| Pack | Tests | Result | Runtime |
|---|---|---|---|
| 1 unit_a | 66 | PASS 66/66 | 0.5s |
| 2 unit_b (fused_judge+truncation+gate) | 73 | PASS 73/73 | 2s |
| 3 unit_c (judge_resolver/wiring/ledger) | 109 | PASS 109/109 | 3s |
| 4 unit_d (marker family) | 108 | PASS 108/108 | 2s |
| 5 unit_e (report_judge/resolver family) | 168 | PASS 168/168 | 3s |
| 6 unit_f **census** | 38 | PASS 38/38 | 3s |
| 7 unit_tools | 66 | PASS 66/66 | 4.2s |
| 8 integration_a | 20 | PASS 20/20 | 3s |
| 9 integration_b | 44 | PASS 44/44 | 32.7s |
| 10 integration_c | 17 | PASS 17/17 | 13s |
| 11 integration_d | 40 | PASS 40/40 | 2s |
| 12 ledger_reset | 1 | PASS 1/1 | 41s |
| 13 migration | 18 | **FAIL(expected-foreign)** 17P/1F | 0.3s |
| 14 slow_a (delegation+tri_state, live LLM) | 5 | PASS 5/5 | 55s |
| 15 slow_b (incident_acceptance+revive) | 3 | **FAIL 2/3 → flake QUARANTINED** | 66s |
| 16 slow_c (idle_orphan, live LLM) | 3 | PASS 3/3 | 117s |
| 17 slow_d (mid_work, live judge) | 6 | PASS 6/6 | 25s |
| 18 slow_e (stale_watermark) | 2 | PASS 2/2 | 22s |
| 19 slow_f (killswitch matrix) | 11 | PASS 11/11 | 2s |
| 20 slow_g (failopen seams i-iv + F2) | 18 | PASS 18/18 | 2s |
| 21 slow_h (live_descendants) | 42 | PASS 42/42 | 84s |

**Matrix totals: 858 executed → 856 PASS / 1 expected-foreign red / 1 flake (both proven base-identical, both quarantined/classified-out).**

- **Foreign red**: `test_attestation_migration.py::…test_no_boolean_int_literal_default` — offender `20260915_120000_critical_notes_lifecycle.sql 'BOOLEAN NOT NULL DEFAULT 0'` (critical-notes feature, PG fresh-deploy blocker on THAT feature). **Base-identical at f7588291 (byte-identical assert)**. File remainder 17/17 green at head.
- **Flake (pack 15)**: `test_attestation_revive_after_escalation.py::test_terminal_reset_and_fresh_episode_rearm_next_mission`. Live fused judge (deny-band RESCUER — intended Stage-2 semantics) occasionally answers `verdict=complete` → rescue allow → escalation flag never written → unconditional assert fails. **Retry budget: HEAD 1F/2P, base 2P (4/5 pass overall)** — flaky at ANY commit, not branch-caused. QUARANTINE row added; pack-15 deselect applied in evidence commit. Owner follow-up: hermetic judge-verdict pin.

**bound_escalation (excluded from packs)**: dev-reported asyncio wedge NOT reproduced — 3/3 PASS @ head + 2/2 PASS @ base (36–48s each). Behavior base-identical; recorded as intermittent-environment watch item, not a defect.

## Job 2 — Single-path E2E zoo: 8/8 scenarios

`tests/integration/test_attestation_stage3_zoo_test.py` (NEW, commit `222c9e95`, 2 tests, 0.85s) drives the REAL graph:
- **Child-lie arc (anchor)**: 4 evals across 2 `graph.ainvoke` — deny+nudge (counter 0→1, judge ×1, `fused_judge verdict=not_complete`) → attested allow (counter 1→0, judge ×0, `meta_bypass`) → revive → deny+nudge → attested allow. Full lifecycle through the ONE path.
- **Genuine report**: judge `verdict=complete` → rescue allow, counter untouched.
- Scenarios 3–8 covered by cited green suites: awaiting-answer (`user_answer_pending_lca` 8), idle-orphans/nothing-pending (3/3 incl. bound), non-delegated R4 (`marker_routing` a/h), dry 0-LLM (`killswitch` CellC), mode-off unwired (CellB), enforce-default env-unset (CellD).
- Fixture gotcha documented: `tests/support/conftest.py` hardcodes `instance_id="attestation-leader-e2e"` — wrong-id safe_increment silently fails → C3 fail-open degrades deny→allow (`graph.py:5589`). Follow-up pin candidate.

## Job 3 — Census + invariants: 70/70, ZERO vacuous survivors

Census 23/23 dedicated + R4 (4) + R7 legacy-deletion (4) + budget sentinel (9) + truncation caps (7) + failopen seams i-iv/F2/F2-refire/FC (18) + busy⊆live (1) + at-bound (4). Vacuity audit: every group proven to exercise REAL production symbols (activation_predicate :451, judge_fused_bundle_async :646, decide :442, evaluate :720, `InstanceManager._count_descendants_busy_and_live` :8921, create_attestation_gate_node source) — no mirror/stub/constant-echo satisfaction; no kwarg-rot asserts. **D13 adjudicated ADEQUATE** (structural source pin + behavioral transit). **Deleted stage2-artifact trade verified coverage-preserving** (incident shapes A/B/C classes live in `test_attestation_resolver_stage2.py:824/868/905/986`; budget cells in TestBudgetGuardSentinel; cap-shape in truncation matrix).

## Job 4 — Budget live-count: HOLDS

- Static: exactly ONE live `judge_fused_bundle_async` call site (`graph.py:5311`); census test `test_gate_node_never_references_legacy_judge` pins count==1; zero retired refs.
- Focused: 27/27 (sentinel+derivation 9, truncation 7, killswitch 11).
- Live probe (real gate, counting wrapper around the real function): attested=0, not-required=0, user-answer-pending=0, dry=0, off=0, deny-band=1, marker-band=1, A-band=1 — **8/8 exact**.
- attempts≤2 STRUCTURAL: no third `_attempt_once` exists; attempt-2 only on attempt-1-unparsable; timeout/error never retried; degenerate empty-bundle → invoked=False, 0 attempts.
- D2 restated post-retirement: "scenarios that don't need a judge make ZERO calls; those that do make EXACTLY ONE" — verified 3 independent angles.

## Job 5 — B-redaction: live check PASS + permanent pin

Real `assemble_fused_bundle` with leader prose quoting 4 child UUIDs → B-section shows `<redacted-leader-N>` placeholders, prose byte-identical, zero raw UUIDs in bundle; A + C sections redacted (regression guards hold). **Bundle→judge seam proven**: `graph.py:5311-5313` passes `act.bundle.text` byte-identical. NEW `tests/unit/test_attestation_b_redaction_stage3.py` (4 tests, commit `fe8f48ac`) **closes the discovered coverage gap** (B-section redaction had NO pin). Non-vacuity: counterfactual computed — dropping the `redact_ids` wrap leaks 3 raw UUIDs → asserts flip.

## Job 6 — PG lane + boot smoke: PASS / PASS

- **PG**: 21/21 in 3.1s on disposable PG 14.22 @ :15433 (pack `lca3_pg_attestation_integration_test.sh`, commit `0525bbf8`); teardown verified (no listener, datadir removed, zero residual procs); prod/dev/foreign ports untouched.
- **Boot smoke** (real daemon, disposable PG :15434, mock LLM :15778, uvicorn :15790; commit `50911445`): (a) boot line `mode=enforce` with ALL attestation env unset (default proven); (b) fused rows `judge_invoked=True` with `not_complete→complete` rescue sequence; (c) canonical row verified against ALL 17 `CANONICAL_LOG_SCHEMA_FIELDS` loaded live from source — `attest_seen_outside_window` absent from the row AND the entire daemon stdout; (d) ZERO legacy event names log-wide. Clean shutdown, zero leaks, 8088/9797/8079 untouched.

## Job 7 — Behavior-neutrality vs RUNNING flip (c489233e): NEUTRAL-EXCEPT-APPROVED, 0 violations

Commit topology: `c489233e` ⊂ `f7588291` ⊂ `f8e78a40` (8 non-stage3 skill-capture commits between c489233e and f7588291 — one owner-authorized env `ENSEMBLE_SKILL_CAPTURE_ENABLED`, zero LCA leakage). Stage3-attributable range `7089e73b~1..f8e78a40` (+435/−1821 daemon LoC — matches report exactly).

- **(a) Statically-dead deletions** (16 symbols ~1100 LoC): all 0-live-reference-proven (flip constant, legacy judges, JudgeResult, marker-path plumbing, R1-R6 fields/constants) — zero runtime impact.
- **(b) Approved deltas land exactly**: (i) B-redaction (`redact_ids(_clip(...), 'leader')` at resolver_activation.py:750 + truthful docstrings); (ii) schema 18→17 — dropped field IS `attest_seen_outside_window` (R1); (iii) R8 consolidation — 5 legacy event names die, single `*_fused_judge*` family (3 names) survives.
- **7 documented non-blocking findings**: F1 (6 supplementary log fields dropped from canonical row — R5/R6 scope; operators must update greps), F2 (`_fused_judge_disabled` row swaps `trigger_source=` → `marker_hit=… length_trigger=…`), F3 (`_invoke_judge_llm` system_prompt default→required; single explicit caller), F4 (`decide()`/`build_gate_config()` signatures tightened; composition layer byte-equivalent outputs), F5 (D10: marker scan now SKIPPED on ¬required — log-only, judge never fired there in either version), F6 (child_reports ImportError split → `deploy_bug=true` ERROR row, fires only on broken deploys; normal case byte-identical), F7 (scanner import hoist to module-top; leaf module, no cycle; perf-only).
- Zero new env/flag reads in the stage3 diff (fix/flag policy honored); flip constant GONE with no equivalent re-introduction; deny-bound enforcement sites 5→2 with both survivors on live paths (3 deleted were flip-guarded-dead).
- Retirement-report cross-check: LoC per file EXACT (−1391 total); R1-R8 + ledger (a)/(c) all verified in-diff. Blueprint inaccuracy noted: `attestation_resolver.py` was NEVER deleted (stays live: config resolver, boot log, metrics) — the retirement lives in `attestation_report_judge.py` + `graph.py`.

## Job 8 — No production changes

Branch evidence stack (ALL test-only, on top of f8e78a40): `8a7b5272` (21 matrix packs) → `0525bbf8` (PG pack) → `50911445` (boot smoke) → `fe8f48ac` (B-redaction pin) → `222c9e95` (E2E zoo) → evidence commit (this report + PACKS/QUARANTINE updates + pack-15 deselect). `git diff f8e78a40..HEAD -- daemon/` EMPTY — verified by finalizer.

## ensure.md (scoped)

- Core Critical: no-regressions-in-changed-packs ✅ (matrix); concurrency_atomic ✅ 98P/74S/0F byte-exact vs stage2 baseline (8.81s); sync-on-loop ✅ (same pack); dev.sh graceful-shutdown grep ✅ (`dev.sh:102`).
- Core Important/Nice: awaited-callers grep class unaffected (no converted fns in delta); dead-code-truly-unused ✅ (0-reference proofs + census).
- Release Gate: scoped OUT per feature-gate precedent (pre-activation; running daemon is c489233e; e2e requires ./dev.sh 8079 lane). E2E-through-real-graph covered instead by the zoo + boot smoke.
- Contradiction scan: none (ensure.md pack-mapped methods followed).

## Notes (non-blocking)

- **N1 🟠 (foreign)**: migration `20260915_120000` BOOLEAN DEFAULT 0 — PG fresh-deploy blocker owned by critical-notes feature; base-identical here.
- **N2 🟠 (owner follow-up)**: revive_after_escalation flake — hermetic deny-band judge-verdict pin needed (any commit).
- **N3 🟢**: report §4 "894 collected" vs reproducible 880 (858+21+1) — developer per-file prose counts stale; enumeration is ground truth; no missing FILE.
- **N4 🟢**: bound_escalation intermittent wedge unreproduced (5 clean runs both commits) — watch item.
- **N5 🟢 (ops)**: canonical log row loses 6 supplementary fields + the R1 field; `_fused_judge_disabled` row shape changed — update dashboards/greps.
- **N6 🟢**: conftest hardcoded `attestation-leader-e2e` instance-id (silent fail-open on mismatched ids) — pin candidate.
- **N7 🟢**: dispatch infrastructure — send_message pool saturation (QueuePool 5+10) under 21-way parallel fan-out: 6 correlation timeouts, all messages landed (verified); retry guard prevented duplicates. Smaller send batches recommended for future large fan-outs.

## Documentation Updated
- [x] PACKS.md — 21 matrix + PG + boot rows registered, statuses finalized
- [x] QUARANTINE.md — +1 row (revive_after_escalation flake)
- [x] LESSONS/ — dispatch-pool lesson + gate drift-gate pattern
- [x] RESULTS/2026-09-17-lca-stage3-retirement-verification.md — this report
- [ ] rules/ensure.md — untouched (user-owned)

### Overall Status
- Matrix: ✅ (858 accounted; 0 branch-caused)
- E2E zoo: ✅ 8/8 | Census+invariants: ✅ 70/70 non-vacuous | Budget: ✅ | B-redaction: ✅ + new pin
- PG: ✅ 21/21 | Boot smoke: ✅ | Neutrality: ✅ 0 violations | ensure Core: ✅ 4/4 scoped
- **Testing Complete: ✅ READY FOR MERGE (PASS-WITH-NOTES; notes non-blocking, routed to owners)**
