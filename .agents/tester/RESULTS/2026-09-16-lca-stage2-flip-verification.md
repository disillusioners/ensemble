# LCA Resolver Stage 2 (THE FLIP) — Final Merge Gate Verification

Date: 2026-09-16
Gate owner: Tester (instance 6bc61f42 tree)
Delta under test: `0ea60d91..f926de24` on `feature/lca-resolver-stage2` (commits `07e9e8cc` flip + `f926de24` council follow-ups; 17 files ~+3550/−509)
Worktrees: branch worktree `agents-ensemble-wt-lca-stage2` (shared, sibling-safe protocols); detached clean-tip worktrees for matrix (`f926de24`) and base A/B leg (`0ea60d91`), both removed post-run.
Workers: 10 (matrix 8d507ac2, e2e-abc eca898f1, e2e-de 10650982, pg 8f802d66, killswitch fb95695e + 1 adjudication follow-up, failopen 45c74774, boot bda4198e, llm-probe f8f5397f, trunc-verify c9a80d47, budget 9c10a315, ensure 99a1f882). Zero production-code changes across all workers (verified per-commit path-scoping + `git diff f926de24..HEAD` ⊆ {tests/, test/packs/}).

## VERDICT: ❌ FAIL — MERGE BLOCKED (one confirmed introduced-by-delta product defect; fix is small and targeted; all 9 leader jobs executed)

**Blocking finding F-A**: the fused judge — now AUTHORITATIVE under the flip — truncates the LLM response to hardcoded `JUDGE_MAX_OUTPUT_CHARS=400` **before** parsing (`daemon/services/attestation_report_judge.py:120` definition; truncation sites `:1315-1318`/`:1353-1356` fused, `:893-899`/`:938-941` legacy). The Stage-2 fused system prompt (`:1059-1079`) mandates a payload (5×120-char evidence_cited + 240 advisory + 240 rationale) that **exceeds 400 chars by construction** — a compliant model response can never parse. Every verbose-but-correct verdict → `unparsable` ×2 attempts → DP-5 conservative `deny_nudge`. The rescue path (`graph.py:5375-5399`) is UNREACHABLE under `MODE=enforce` with realistic models: genuine reports (98b59dd7 shape) false-deny up to the bound (4 cycles, `d+1>b`) → `terminal_after_bound` forced allow. `MODE=dry` unaffected; judge kill-switch=0 is NOT a workaround (kills rescue entirely). No runtime knob exists (hardcoded constant).

Evidence: live probe (both payloads carried the CORRECT `"verdict"` verbatim in raw output yet returned unparsable; latency 39.8s/62.6s) + deterministic credentials-free repro `tests/probe/lca2_judge_truncation_repro.py` @ `20024fda` (compact 122-char JSON → `complete`; compliant 997-char JSON → truncated to exactly 400 → `unparsable`×2). Attribution: cap+order pre-existing on the LEGACY window-judge (its output shape fits); **introduced-by-delta on the fused authoritative path** (`07e9e8cc` reused the shared cap). Recommended fix (NOT applied per Job 9): separate `FUSED_JUDGE_MAX_OUTPUT_CHARS=2048` at the two fused sites (legacy keeps 400); conservative fail-safe preserved either way.

**Strongly-recommended pre-merge finding F-B** (hard, robustness): the fused block (`graph.py:5242-5524`) sits OUTSIDE the gate node's try/except (`:5176`, same indent) — any raise in verdict mapping / hint injection / row emission (`emit_resolver_eval_row` `:5507`) propagates to LangGraph and **crashes the gate on all bands** (no fail-open, no `leader_completion_gate_error` row, no `gate_exception_seen`). Recommend wrapping the fused block + `_persist_gate_exception_marker` (Stage-3 scope candidate).

**Secondary finding F-C** (FR-13 deviations): (1) resolver-compute faults (seams i/ii, caught at `attestation_gate.py:1483-1494`) log `resolver_eval_error` but do NOT fail-open-allow — deny-band proceeds DENIED with counter increment; (2) `gate_exception_seen` is stamped by NO Stage-2 seam-fault path (only the pre-Stage-2 scanner path does).

## Per-job results

| Job | Result | Evidence |
|---|---|---|
| 1. Full attestation matrix @ clean tip f926de24 | **PASS** — 840 collected / 839 P / 1 F; single red pre-existing foreign, base-identical at 0ea60d91; 0 branch-caused | 15 partition packs `test/packs/lca2_matrix_1..15_*` (commits `ebfb1473`+`4f3fe0f0`); red = `tests/migration/test_attestation_migration.py::...test_no_boolean_int_literal_default` (offender `20260915_120000_critical_notes_lifecycle.sql`, critical-notes lineage, out of scope). Glob: 55 files; excluded postgres file ran in Job 6 |
| 2a-c. Incident E2E (independently constructed, real gate node) | **PASS 8/8** | `tests/integration/test_attestation_stage2_incident_abc.py` @ `348b8e4f`: D1 payload carries child evidence (a1); 4-cycle bound → terminal_after_bound, terminal cycle judge_invoked=False (a2); retry entries==1 ∧ attempts==2 (a3); legacy sites silent (a4); rescue no-counter-movement (b1); judge field correctness (b2); answer-pending zero fused rows (c1); sentinel fields co-travel (c2) |
| 2d-e. Child-lie arc + non-delegated marker | **PASS 2/2** | `test_attestation_stage2_incident_de.py` @ `f3a7e7c0`: real `build_instance_graph`+`ainvoke` ×3 evals (A-band allow+hint w/ D4 citation → quiet deny+nudge → attested allow + counter reset 0); budget 2 invocations × ≤2 attempts; Δ1 fusion (SOURCE A+C in payload); legacy zero calls; (e) R4 short-circuit end-to-end: zero judge attempts, `bypass_reason=meta_bypass`, no hints/nudges |
| 3. Budget behavior LIVE (parity) | **PASS 20/20** | `test_attestation_stage2_budget_parity.py` @ `460daf24`: flip ON/OFF × 9 scenarios; ONLY A-band-alone adds a call (fused 1 vs legacy 0); all else 1→1 / 0→0; ≤1 invocation/eval; attempts ≤2; dry/kill-switch/meta-bypass 0→0 |
| 4. Kill-switch matrix EXECUTED | **PASS 11/11** | `test_attestation_stage2_killswitch.py` @ `ceb7694e`: JUDGE=0 → deny-band deny+nudge, marker/A plain allow, 0 LLM constructions; MODE=off → gate unwired, ZERO rows; MODE=dry → 0 LLM, DRY_LOG rows; default=enforce runtime-confirmed. (D.2 annotation discrepancy resolved: mock served not_complete; mapping correct at `graph.py:5400-5406`) |
| 5. Resolver-fault fail-open | **PASS-with-findings 14/14** (tests pin actual behavior) | `test_attestation_stage2_failopen.py` @ `8be94ef2`: seams i-iii survive w/ loud rows; DP-5 per-band confirmed; budget intact; **F2 wakeup re-fire PASSES both scenarios**; findings F-B (seam iv crashes gate, all bands) + F-C |
| 6. PG lane (real PostgreSQL) | **PASS 21/21** (3.12s) | `lca2_pg_attestation_integration_test.sh` @ `a3a57ee7`; disposable PG14 port 15433 (conftest envs `PG_TEST_HOST/PORT/DB/USER/PASSWORD`); teardown verified; foreign migration defect did not surface (create_all bypasses migration runner) |
| 7. Boot smoke (branch, enforce default) | **PASS** (25s pack) | `lca2_boot_smoke_mock_test.sh` + `lca2_helpers/mock_llm.py` @ `473e1428`: boot line `mode=enforce ... llm_judge_enabled=true`; `/livez`+`/readyz` 200; disposable PG 15434; scripted leader→spawn→fused judge (stub) `not_complete→deny_nudge` then `complete→allow` with `judge_invoked=True` rows; ports 15777/15778/15434 freed; zero leaks |
| 8. Live-LLM probe | **FAIL (finding F-A)** | Probe @ `fe437722`: creds present (main .env, values never printed); model `quick`; genuine report 2434 chars → `unparsable` (raw contains `"verdict": "complete"`); child-lie 1614 chars → `unparsable` (raw contains `"verdict": "not_complete"`); both invoked=True, error_class=None → pipeline defect, not model failure. Verified F-A above |
| 9. No production changes | **HONORED** | All commits test/pack-only; `git diff f926de24..HEAD --name-only` ⊆ {tests/, test/packs/} verified by every worker + at aggregation |
| ensure.md Core | **2/2 PASS** | `concurrency_atomic_unit_test` 98P/74S/0F in 7.81s (baseline-exact vs prior gates); `dev.sh:102` `--timeout-graceful-shutdown 10` present |

## Scope Decision

Full gate scope — no reduction: final merge gate on an architecture flip (~+3550/−509, 17 files); the leader's 9-job directive defined the acceptance set and was executed in full. Exclusions (explicit, not silent): ensure.md **Release-Gate** items (full ~16k non-integration suite + real-LLM `./dev.sh` E2E) were NOT run — outside the leader's declared scope, and the main checkout is occupied (dev.sh hardcodes 8079); the delta's blast radius is covered by the attestation matrix + PG lane + boot smoke + live probe. Post-fix re-verification does NOT require re-running this whole gate (see below).

## Reconciliation notes (actuals vs references)

- Matrix collected **840** (54 non-PG files) + PG lane **21** = 861 attestation-family tests at tip; the "~901" developer reference ≈ 840 + postgres-lane estimate; the "~92 follow-up" figure does not match tip actuals (`test_attestation_resolver_stage2.py` = **29** tests, all passed). Actuals reported here are glob-enumerated ground truth at f926de24.
- Quarantine: zero overlap between the attestation glob and QUARANTINE families (`TestAccessMemoryArchive` grep-zero in glob). Note: `QUARANTINE.md` is untracked residue in the branch worktree (absent at f926de24) — not committed by this gate.

## Commit hygiene notes

- One sibling collision occurred mid-gate: a commit co-mingling the truncation probe with incident_de work was reset (`42adbed4` → HEAD~1) and recommitted cleanly (`f3e7e7c0`); probe re-committed standalone (`20024fda`). Reflog preserved. All other commits path-scoped and verified.
- Branch commit lineup (test-only): `a3a57ee7` (pg pack) → `ebfb1473`+`4f3fe0f0` (matrix packs) → `348b8e4f` (incident_abc) → `ceb7694e` (killswitch) → `8be94ef2` (failopen) → `f3e7e7c0` (incident_de) → `20024fda` (truncation repro probe) → `473e1428` (boot smoke) → `460daf24` (budget parity) → tester-evidence commit (this report; see below).
- Test-architecture note for future matrix runs: judge-heavy integration packs make REAL gateway LLM calls (quick @ llm.ensem.dev, ~25-55s each) — packs 14/15 ran 257s/278s against the 290s inner guard; expect latency variance, not pass/fail variance. Slow packs isolated; fast packs keep default timeout=30.

## Post-fix re-verification recipe (when F-A is fixed)

1. `tests/probe/lca2_judge_truncation_repro.py` → case (b) must parse `complete` (flip the probe's expectation).
2. Live-LLM probe rerun (Job 8 payloads) → both verdicts as expected.
3. `test/packs/lca2_incident_abc_integration_test.sh` + `lca2_budget_parity_integration_test.sh` (stub lengths no longer need the ≤400 guard).
4. If F-B also fixed: `test/packs/lca2_failopen_integration_test.sh` seam-(iv) cells flip from crash-pin to fail-open-pin.
No full matrix rerun required — no other files change.

## Documentation updated

- [x] RESULTS/2026-09-16-lca-stage2-flip-verification.md (this file)
- [x] LESSONS/2026-09-16-lca-stage2-fused-judge-truncation-defect.md (F-A)
- [x] LESSONS/2026-09-16-lca-stage2-failopen-seam-findings.md (F-B, F-C, clarifications)
- [x] PACKS.md — verification-gate entry + new pack registrations (lca2_matrix_1..15, lca2_pg_attestation, lca2_incident_abc/de, lca2_killswitch, lca2_failopen, lca2_budget_parity, lca2_boot_smoke)
- [x] MOCK_TESTS.md — boot-smoke mock spec (ports 15777/15778, PG 15434)
- [ ] rules/ensure.md — untouched (user-owned)

## Overall Status

- Jobs: 8 PASS / 1 FAIL (Job 8 → F-A) / 0 TIMEOUT / 0 incomplete
- ensure.md Core: ✅ 2/2 (Release Gate scoped out, see Scope Decision)
- **Testing Complete: ❌ NOT READY — merge blocked on F-A (fused-judge truncation); F-B strongly recommended pre-merge; F-C + clarifications documented for Stage-3**
