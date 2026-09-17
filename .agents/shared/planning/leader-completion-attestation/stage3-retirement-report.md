# Stage-3 Retirement Report — LCA resolver R1–R8 + ledger (a)–(e)

Date: 2026-09-17. Branch `feature/lca-resolver-stage3`, worktree `/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble-wt-lca-stage3`, base `f7588291b1` (= `latest` at branch time; `c489233e` verified ancestor). Authoritative spec: `resolver-unification.md` §7 + Appendix A (user-approved item-by-item) + the two 🚩 conditional-neutrality guards. Status: implementation + scoped verification complete; **worktree left dirty for the separate review + commit step**.

---

## 1. Per-R deletion evidence (file : line ranges at base → post state, LoC)

All base line numbers are at `f7588291b1`; sites were re-located by symbol/grep per the task note (plan anchors were stale).

### R1 — outside-window diagnostic (log-only surface)
- `daemon/services/attestation_scanner.py` :242–275 — `attestation_seen_outside_window` function DELETED (34 LoC).
- `daemon/services/attestation_gate.py` — import (:89), the `(v)` computation (:941–944), `GateDecision.attest_seen_outside_window` field, the canonical-row key + arg, the DB-error-row key + arg, the fail-open-return stamp — all DELETED. `CANONICAL_LOG_SCHEMA_FIELDS` 18 → 17.
- Diagnostic note: decisions.md D-RES4 §R1. Behavior pin retained as `test_stale_outside_window_attestation_still_denies` (the stale-attestation-aged-out outcome, minus the field).

### R2 — gate-config meta-flag threading (separate checks)
- `daemon/services/attestation_gate.py` — `decide()` loses `scope_applicable` / `mode` / `attestation_enabled` params + step-1 meta branch + the unknown-mode branch (~30 LoC); `GATE_CONFIG_KEYS` −2 entries; `build_gate_config` −2 params/keys.
- `daemon/graph.py` :5161-5162 (base) — the node's `gate_config.get("attestation_enabled")` / `.get("scope_applicable")` threading DELETED; :10866-10867 (base) — the `build_gate_config(attestation_enabled=True, scope_applicable=True)` kwargs DELETED.
- KEPT (the real enforcement points): `build_instance_graph`'s `if attestation_enabled:` wiring (build-time gate existence, base :10835) + `create_attestation_should_continue` (composition shape Y) + `evaluate()`'s Term-0-mirror early return (byte-equivalent OFF baseline).

### R3 — decide()-level DRY branch
- `daemon/services/attestation_gate.py` — decide()'s `if mode == "dry"` branch DELETED (7 LoC). REPLACEMENT: `evaluate()`'s mode layer — enforce-tree decision computed with full diagnostics, then mapped to `DRY_LOG` with the counter frozen at input and the nudge disarmed (net +14 LoC incl. comments). Dry rows byte-equivalent (`decision=dry_log`, counter unchanged, zero side effects).

### R4 — the delegation arm (🚩 guard LANDED)
- `daemon/services/attestation_gate.py` — decide()'s `if not attestation_required` arm DELETED (10 LoC). REPLACEMENT: `evaluate()`'s composition bypass (plain ALLOW, counter untouched) — the predicate's Term-1 mirror (+12 LoC).
- **D10 mirror**: the Source-B scan block gains `and result.attestation_required` — the marker/length scans are SKIPPED entirely on non-delegated missions (suspicion signals not even evaluated). Log-only delta: non-delegated rows carry False/0 defaults (the judge never fired on those rows under the flip; outcomes unchanged).

### R5 — busy-suppression block
- `daemon/services/attestation_gate.py` :1245–1272 (base) — the `trigger_fires ∧ busy>0` bookkeeping (trigger_source force-clear + `trigger_suppressed_by` stamping, ~30 LoC) DELETED. The busy-mute lives in the predicate's `b_fires` term (`(marker_hit ∨ length_trigger) ∧ busy_descendants == 0`); `busy_descendants` (raw count) SURVIVES as predicate input + forensic field. Source A deliberately NOT busy-suppressed (Δ2).

### R6 — trigger plumbing
- `daemon/services/attestation_gate.py` :1146–1323 (base, ~178 LoC) — the old trigger block (scan-result → judge-trigger derivation: `trigger_source`, `trigger_suppressed_by`, `marker_path` stamping, the `<pending>` sentinels) DELETED, replaced by the compact signal-producer block (scans + field stamps, ~45 LoC). `MARKER_PATH_{NONE,A,B,C,D}` constants, `marker_path`, `marker_judge_{verdict,latency_ms,error_class}`, `trigger_source`, `trigger_suppressed_by` — all gone from `GateDecision` + the canonical row. KEPT: `scan_for_mid_work_markers` (16-pattern catalog) + `scan_for_short_final_ai` (<150 words) + `marker_hit`/`marker_terms`/`length_trigger`/`final_word_count`/`busy_descendants` fields. Canonical placeholders 34 → 27.

### R7 — both legacy judge sites + the flip constant (the big one)
- `daemon/graph.py`:
  - `_LCA_STAGE2_RESOLVER_FLIP` + its 15-line comment (base :4814–4828) DELETED.
  - Legacy marker-path judge block (base :5570–6007, **438 LoC**: kill-switch resolution + judge call + the full (a)/(b)/(c)/(d) routing incl. the wrapper-fault route) DELETED.
  - Legacy would-be-deny judge block (base :6009–6174, **166 LoC**: judge_on computation + judge call + judge-yes rescue) DELETED.
  - The fused block's guard: `if _LCA_STAGE2_RESOLVER_FLIP and resolver_snapshot is not None:` → `if resolver_snapshot is not None:` (324-line body kept at its nesting).
  - Net graph.py: 11098 → 10480 (−618).
- `daemon/services/attestation_report_judge.py`: the legacy window-judge surface DELETED (net 1410 → 817, −593): `judge_completion_report_async` (:751–1002), `judge_completion_report_sync` (:1004–1043), `JudgeResult` + section (:196–269), `JUDGE_SYSTEM_PROMPT` section (:171–195), `_parse_judge_response` (:391–472), the window-slicing section `_slice_judge_window` + `_format_window_for_judge` (:301–382), `JUDGE_MAX_INPUT_CHARS` / `JUDGE_MAX_OUTPUT_CHARS` (:110–132), `JUDGE_MAX_WINDOW` / `JUDGE_DEFAULT_WINDOW` (:146–158). `_invoke_judge_llm`'s `system_prompt` default → required kwarg. KEPT: fused judge + parser + `_invoke_judge_llm` + `_AttemptOutcome` + `resolve_judge_model` + excerpt-shaping helpers + `JUDGE_TIMEOUT_S`/`JUDGE_EXCERPT_MAX_CHARS`/`FUSED_JUDGE_MAX_OUTPUT_CHARS`.
- **🚩 both neutrality mappings verified live on the single path** (R7 invariant suite green through the real node): judge error/timeout/unparsable×2 → deny+nudge bound-enforced incl. at-bound edge → `terminal_after_bound`; kill-switch OFF → deny band deny+nudge WITHOUT judge, marker/A bands plain-ALLOW.

### R8 — legacy judge event names
- The `*_marker_judge*` family (`_marker_judge`, `_marker_judge_disabled`, `_marker_judge_error`) and the bare `leader_completion_gate_judge` / `_judge_error` rows died with their call sites. Single family: `_fused_judge` / `_fused_judge_disabled` / `_fused_judge_error`. The `_disabled` row's context field swapped from retired `trigger_source=` to `marker_hit=%s length_trigger=%s`.

## 2. Ledger items (a)–(e)

- **(a) FULL B-redaction** — `daemon/services/attestation_resolver_activation.py::_build_b_section`: `lines.append(f"[{shown+1}] {redact_ids(_clip(content, 1500), 'leader')}")`; `redact_ids` + `assemble_fused_bundle` docstrings rewritten truthfully (the f926de24 "lands in Stage 3" scope-note REPLACED by the shipped fix). NOTE: the task text placed this in `attestation_report_judge.py` — the bundle assembly lives in `attestation_resolver_activation.py` (recorded as a task-text mismatch).
- **(b) F2 fail-open target PIN** — NEW named test `tests/integration/test_attestation_stage2_failopen.py::TestF2FailOpenTargetPin::test_f2_fail_open_target_mirrors_kill_switch_off`: seam-(i) resolver-compute fault × 3 bands — deny → deny+nudge + increment; marker/A → plain allow, no nudge/hint/counter, zero HTTP attempts.
- **(c) F-C marker-write seam** — `daemon/services/child_reports.py`: scanner import hoisted to module top (out of `_process_child_completion_db_sync`'s hot path); context-message names joined to the existing top-level import; the note-INSERT catch splits `except ImportError` (ERROR row, `deploy_bug=true`) from `except Exception` (the existing advisory-lost WARNING).
- **(d) rescue-judge-forfeit documented** — decisions.md D-RES4 ledger-(d) paragraph (the accepted cost: fault rows forfeit the rescue; bounded by deny_bound; F2 re-fire tests prove the next clean evaluation can rescue).
- **(e) BLE001 justification** — all three fused-region suppressions carry em-dash notes; pinned by `test_noqa_ble001_comments_justified_in_fused_region`.

## 3. Census pins (new file `tests/unit/test_attestation_stage3_census.py`, 22 tests)

`TestR7CensusFlipConstantDeleted` (2), `TestR7CensusLegacyJudgeDeleted` (3), `TestR8CensusLegacyJudgeEventNamesDeleted` (3), `TestR1CensusOutsideWindowFieldDeleted` (2), `TestR2CensusGateConfigThreadingDeleted` (3), `TestR3CensusDecideDryBranchDeleted` (1), `TestR4CensusDelegationArmDeleted` (2), `TestR5R6CensusTriggerPlumbingDeleted` (4 — incl. scanner-catalog SURVIVAL pins + the BLE001 pin), `TestLedgerCMarkerWriteSeam` (2). All grep daemon/ sources; any resurrection is loud.

## 4. Family count reconciliation (baseline → after, collected)

| File | Base | After | Δ | Explanation |
|---|---|---|---|---|
| tests/unit/test_attestation_report_judge.py | 55 | 15 | −40 | legacy window-judge tests deleted; 15 survivors (model resolution + excerpt shaping); fused behavior lives in test_attestation_fused_judge{,_truncation}.py |
| tests/integration/test_attestation_stage2_budget_parity.py | 20 | 0 | −20 | stage2-branch-scoped artifact deleted per adversarial review (all 20 tests self-skipped via the `_EXPECTED_HEAD_PREFIX` drift pin — pre-condition verified by a full-file run: 20 skipped / 0 passed) |
| tests/integration/test_attestation_stage2_incident_abc.py | 8 | 0 | −8 | same — 8 skipped / 0 passed pre-condition |
| tests/integration/test_attestation_stage2_incident_de.py | 2 | 0 | −2 | same — 2 skipped / 0 passed pre-condition |
| tests/unit/test_attestation_scanner.py | 23 | 21 | −2 | the two outside-window-helper tests retired with R1 |
| tests/integration/test_attestation_stage2_failopen.py | 17 | 18 | +1 | ledger (b) named pin |
| tests/unit/test_attestation_marker_wiring.py | 42 | 43 | +1 | adversarial-review D10 belt pin (`test_quick_question_marker_scan_seam_never_invoked`) |
| tests/unit/test_attestation_resolver_stage2.py | 29 | 30 | +1 | `TestOldSitesDeadButPresent` (3) → `TestLegacySitesDeleted` (4) |
| tests/unit/test_attestation_stage3_census.py | 0 | 23 | +23 | NEW census (22) + the review-round error-event precision pin (+1) |
| **TOTAL** | **940** | **894** | **−46** | −40 −20 −8 −2 −2 +1 +1 +1 +23 = −46 — no unexplained deltas |

Stage2-branch-scoped artifacts deleted per adversarial review — semantics owned by census + TestLegacySitesDeleted + R7 suite.

All other family files have IDENTICAL collected counts (re-contracts are in-place, not deletions): e.g. `test_attestation_gate.py` 45 (meta/dry classes re-contracted to the composition layer; matrix kept via a kwarg-dropping helper), `test_attestation_marker_wiring.py` 42 (log-field assertions re-contracted to key-absence + surviving signals; the D10 quick-question tests now pin the scan-skip), `test_attestation_judge_wiring.py` 26 (timeout-flow tests re-driven through `judge_fused_bundle_async`), `test_attestation_user_answer_pending_decide.py` 15, dry-mode/dry-logging, conditional_*, runbook_drift (schema 17), config, incident_abc/de, killswitch, budget_parity, marker_routing, marker_bound_enforcement, live_descendants (schema 17 + decide kwargs), user_answer_pending_lca, nudge_chaos (config kwargs), corpus_replay (fixture contract documented as historical), mid_work_report_testcase (stub re-contracted to the shared LLM seam), migration 18.

## 5. Verification matrix

- **SQLite lane** (full 65-file family, `uv run python -m pytest`, timeout 120s): **890 passed / 30 skipped / 22 deselected / 1 failed** in 645s. (Round-1 matrix, pre-review; the review round's post-fix daemon delta is docstrings-only and the 3 deleted artifacts were 30 of the 30 skipped — their removal changes no passed/failed outcome.) The 1 failure is `tests/migration/test_attestation_migration.py::TestNoBooleanIntegerDefaultInShippedMigrations::test_no_boolean_int_literal_default` — the DOCUMENTED pre-existing foreign defect (critical-notes migration `20260915_120000` `BOOLEAN NOT NULL DEFAULT 0`; critical-notes board 🟢 row; NOT in my diff — `daemon/migrations/` untouched).
- **PG lane** (disposable PG 14.22 on :15433 — :15432 was occupied by a foreign leftover; `PG_TEST_HOST/PORT/DB/USER/PASSWORD` per the file's own recipe; teardown done + dir removed): `tests/postgres/test_attestation_live_descendants_pg_lca.py` → **21/21 passed** (`--override-ini="addopts=" -m postgres`). The MethodType-bindings concern did not materialize (no manager helper surface touched).
- **Pre-existing hang (excluded, both lanes)**: `tests/integration/test_attestation_bound_escalation.py::test_bound_plus_one_escalates_once_without_fourth_nudge` (the file's ONLY test) wedges in an asyncio selector past the pytest-timeout thread dump — verified hanging at BASELINE (zero edits) in this worktree; environmental/pre-existing, left for the tester lane.
- **Compile gate**: `python -m py_compile` green on all 6 edited daemon files; family `--collect-only` post-edit clean (torn-write tail gate).
- **Sanity greps**: daemon/ carries ZERO live references to the retired symbols (only retirement-note prose); tests/ live code clean (remaining mentions: census negatives, stage2-branch-scoped acceptance tests that self-skip via their drift-pin, and historical comments).
- **Pre-existing out-of-family red (untouched)**: `tests/test_progressive_dispatch.py` fails on the documented fresh-SQLite migration trap (PG-only `DROP CONSTRAINT`, migration `20260714_000001`) — fails identically at base; unrelated to LCA.

## 6. LoC delta (deleted vs remaining)

| File | Base | After | Δ |
|---|---|---|---|
| daemon/graph.py | 11098 | 10480 | −618 |
| daemon/services/attestation_gate.py | 1569 | 1387 | −182 |
| daemon/services/attestation_report_judge.py | 1410 | 817 | −593 |
| daemon/services/attestation_resolver_activation.py | 1133 | 1136 | +3 |
| daemon/services/attestation_scanner.py | 595 | 563 | −32 |
| daemon/services/child_reports.py | 4618 | 4649 | +31 |
| **daemon total** | | | **−1391** |

Whole-tree diffstat: 30 files, +1037 / −3081 (net −2044; the rest is tests + docs + the new census/report).

## 7. Docs updated

- `decisions.md` — **D-RES4** (retirement record; the user OVERRIDE of the Stage-2 soak gate with the redeploy-earlier-build revert seam; per-R sections; ledger (a)–(e); the D10 plan-vs-code mismatch correction; the pre-existing hang note).
- `requirements.md` — **R-RES4-1..12** final-state acceptance criteria (incl. R-RES4-2 superseding R-RES2-8, R-RES4-3 restating both 🚩 R7 mappings, R-RES4-4 the FR-3/D10 wording) + the FR-3 Stage-3 amendment paragraph.
- `docs/setup.md` — Stage-2 section rewritten to the FINAL single-resolver architecture (behavior map incl. the D10 scan-skip row, single fused event family, 17-field/27-placeholder canonical row, revert = redeploy earlier build + the kill-switch matrix); the Phase-6 judge section, the 18-field schema claim, the marker/length/busy sections, the 98b59dd7 retry section, and the forensics greps all updated to post-retirement truth with explicit Stage-3 notes.

## 8. Spec-vs-code mismatches found

1. **D10 "today" claim was wrong**: resolver-unification §2.2 states "when `attestation_required=False`, the marker scan never runs today (gate.py:1101)" — false at BOTH f2611f07 and f7588291 (the gate scanned the conditional-off branch, log-only; the JUDGE never fired there via the flip's `act.fired` gate). Post-R4 the claim is literally true (the scan is skipped). Recorded in D-RES4 so nobody "restores" the old scan-on-¬required behavior as a fix.
2. **Ledger (a) file attribution**: the task text names `attestation_report_judge.py`; `assemble_fused_bundle`/`_build_b_section` live in `attestation_resolver_activation.py`. Fix landed in the right module.
3. **Ledger (c) file attribution**: the task text names `attestation_marker_scanner.py`; the marker-write catch + hot-path lazy import live in `child_reports.py` (`_process_child_completion_db_sync`). Fix landed there.
4. Plan line anchors were stale as warned (Stage-2 shift); every site was re-located by symbol.

## 9. Left undone / notes for review

- `tests/integration/test_attestation_stage2_incident_de.py` + `test_attestation_stage2_incident_abc.py` + `test_attestation_stage2_budget_parity.py` — DELETED in the adversarial-review round (all 30 tests self-skipped via the stage2-branch drift pin; semantics owned by census + TestLegacySitesDeleted + the R7 suite).
- NO COMMIT / NO push / no merge — worktree left dirty on `feature/lca-resolver-stage3` (31 modified/new files) for the review + commit step.
- A foreign stash (`stash@{0}`, "stale-foreign-residue" parked 2026-09-16) exists in the shared stash — NOT mine, untouched.
- Revert seam for ops: redeploy an earlier build (no env lever); the kill-switch matrix (`LLM_JUDGE_ENABLED=0` / `MODE=dry|off`) remains the pre-redeploy brake.


---

## 10. Adversarial review round (2026-09-17) — verdict CLEAN, 0 CRITICAL / 0 MAJOR; 9 MINORs folded

1. `attestation_marker_scanner.py` `_flatten_ai_content` docstring — dangling `:func:` ref to the deleted `_format_window_for_judge` replaced with direct semantics prose.
2. `attestation_marker_scanner.py` module docstring — rewritten to post-retirement truth (activation-signal producers; no `marker_path` / (a)/(b)/(c) routing role; D10 scan-skip stated).
3. NEW D10 belt pin `test_quick_question_marker_scan_seam_never_invoked` (marker_wiring) — spies `scan_for_mid_work_markers` itself: zero scanner invocations + `marker_hit=False` default on a marker-laden ¬required row.
4. Census `_daemon_sources()` — roots extended with `daemon/clients/` + `daemon/sources/`; docstring over-claim fixed.
5. Census precision — the bare legacy ERROR row `event=leader_completion_gate_judge_error` now pinned explicitly (new test; total 23); the verdict-row pin hardened with quote-terminated variants; the dead `if False else` expression removed.
6. `docs/setup.md` retry paragraph — `_parse_judge_response` → `_parse_fused_judge_response` (present tense).
7. `requirements.md` FR-12 — one-line Stage-3 supersession marker added atop the section (historical text kept).
8. The 3 stage2-branch-scoped artifacts (`incident_de` 2, `incident_abc` 8, `budget_parity` 20) DELETED after the per-file pre-condition run proved ALL their tests self-skip on this branch (30 skipped / 0 passed); reconciliation updated (§4, total 940 → 894). The three tester pack scripts that drove them (`test/packs/lca2_{incident_de,incident_abc,budget_parity}_integration_test.sh`) carry RETIRED banners pointing at the census/R7 ownership.
9. `test_attestation_mid_work_report_testcase.py` — three stale `judge_completion_report_async` prose blocks rewritten to the fused-seam truth.

Scoped re-verify (per the review bounds — no family re-run): `py_compile` green on `attestation_marker_scanner.py` (+ every touched test file); census **23/23**; marker_wiring **43/43** (incl. the belt pin + both quick-question tests); family `--collect-only` totals recomputed (§4); retired-symbol re-grep clean across daemon/ + the fixed prose sites. Worktree still dirty, NO COMMIT, NO push.
