# SKILL-SYSTEM-DEFECTS — TESTER GATE (2026-11-18)

- **Lane**: `feature/skill-system-defects` · worktree `/home/nea/ensemble-src-wt-skillfix`
- **Base**: `0181dadbf8641c60d7158ad59edfa4a4b36944c8` (ancestor of `origin/latest` `eca98397d`) · **gate HEAD**: `52ae65eaf3a2338289f15dc517de349ffaff5bac` (20 lane commits) · **gate test-only commits**: `613651570` (gap pins) · `28b1c8a68` (MagicMock test fix)
- **Regression tier**: **Tier 2 — whole-tree dual-side** (mechanical: the lane touches `daemon/manager.py`, which is on ensure.md's core-seam path-prefix list; escalation is mechanical, never a judgment call)
- **Scope**: default unit scope, both legs, identical algorithm (recipes `whole-tree-base-proven-diff` + `failed-digest-equality-gate`, read by deterministic path)
- **Verdict**: **PASS-WITH-FINDINGS** — all 4 commission criteria met; 1 production-code defect found and **escalated for leader decision**; 2 ensure.md improvement notices raised

---

## 1. Gate criteria verdicts

| # | Criterion | Verdict | Evidence |
|---|---|---|---|
| 1 | Pins MUTATION-PROVEN | ✅ **PASS** | **29/29 required counter-mutation cycles RED** (25 RED on first pass; 4 initially UNPROVEN → closed by wave-2 gap pins, now RED). Mutation table §3. Venue discipline per POK mutation-01/02/03: scratch worktree venue, POSITIVE-shape counter-mutations, `git restore` + sha256 PRE==POST byte-exact on every cycle, zero commits by the gate's mutation workers. |
| 2 | Net-new reds = 0 vs the TRUE pre-fix base | ✅ **PASS (after 1 test-only fix)** | BASE 504 reds (473 FAILED + 31 ERROR) vs HEAD 499 (471 + 28) over 38,763 / 38,829 nodes, accounting closed both legs. head_only = 43 → 35 shard-order ambient + 2 env ambient + 3 flaky (quarantined) + **3 deterministic lane reds** → fixed test-side at `28b1c8a68` (base-inert proof) → **net-new = 0**. §4 |
| 3 | Quarantine discipline honored | ✅ **PASS** | Every head_only id classified against QUARANTINE rows; 0 matched the lane's blast radius; 3 flaky members re-observed/registered (one NEW row added, two cross-referenced); pre-existing reds excluded by evidence, not assumption. §5 |
| 4 | Full relevant suite green modulo recorded baselines | ✅ **PASS** | 6 phase4 reds confirmed byte-exact at base; "3 ambient observability reds" **re-measured** (base measures 7 in the observability family, not 3 — §7 disclosure); ensure.md Core 3 PASS + 1 OUT-OF-SCOPE; lane pins green (233/233) at the gap-pin commit; `test_composite_scoring.py` 14/14 after the fix. §6 |

---

## 2. Legs, accounting, integrity

| Item | BASE leg | HEAD leg |
|---|---|---|
| Tree | `git worktree add --detach /tmp/skillfix-regbase 0181dadbf` | lane worktree @ `52ae65eaf` |
| Venv | `uv sync --frozen` → CPython 3.14.7 | worktree `.venv` 3.14.7 |
| Import proof | `/tmp/skillfix-regbase/daemon/__init__.py` | `daemon.__file__` inside the lane worktree |
| Collected | 40,046 → 38,775 after marker deselect | 38,841 → 38,829 |
| Registry excludes | `tests/packs/**` (g7 `sys.exit(0)` trap), `tests/performance/test_message_api_cost.py` (12 nodes) | same |
| Fence-poison leg | 17 files / 435 nodes, ONE serial leg with `-u POSTGRES_DB` → self-heal → **435P, rc=0** | same → **435P, rc=0** |
| Executed | **38,763 nodes = 473F + 31E + pass** (closed) | **38,394 nodes = 471F + 28E + 348S + 37,547P** (closed) |
| Canonical artifact | `base_failed_ids.txt` sha256 `534e45952ae6e8f63a154b9ff963a11aef5ceaf457f40964a3cce907480068d6` | `head.failed.ids.txt` sha256 `14b07be078a71acecd6bb0fdda3d03aa5f0707c961a34fcf18b25d3308490a69` |

Both sha256s re-verified by the diff adjudicator; the house-canon comparator `sweep_digest_verdict.sh` reproduced the head_only/base_only sets **byte-identically** (43 / 48).

Budget: the 90-min Tier-2 advisory ceiling was breached by the true corpus size (38.7k nodes @ measured 10.5–13.6 nodes/s ⇒ ≈40 min/leg after re-projection). The ceiling was **raised to 150 min by the gate owner** and disclosed here; the Tier-2 shape was NOT downgraded (a Tier-1 re-scope would gut criterion 2).

---

## 3. MUTATION TABLE — 29 required cycles (+2 supplementary)

Legend: RED = pin reddened under the counter-mutation (the pin-to-beat evidence). Restore column = sha256 PRE==POST proof. All cycles ran in a detached scratch worktree at the lane tip with `daemon.__file__` proven inside the scratch; lane + main checkout untouched; **no production commit by any mutation worker**.

| # | Proof | Pin (file::node) | Counter-mutation applied | Result |
|---|---|---|---|---|
| a | W6 duplicated-anchor rejection | `tests/services/test_skill_evolution_service.py::TestDuplicatedAnchorRejection` (2 nodes) | `_apply_surgical_edits` `occurrences != 1` → `< 1` (ambiguous anchor ACCEPTED) | **RED** ✅ `c803420f…52e3ea` |
| b1 | W9 stalled predicate | W9 pack + `TestSanctionedResolution::test_find_stalled_lists_only_stalled` | removed `if comparisons != 0: continue` (`find_stalled_ab_tests:2174`) | **RED** ✅ |
| b2 | W9 age gate | `…::test_find_stalled_lists_only_stalled` | removed `if age_days < stall_timeout_days: continue` (`:2177`) | **RED** ✅ |
| b3 | W9 per-item isolation | W9 pack `::TestSweepBehaviour::test_per_item_isolation` | removed per-item try/except (`manager.py:7870`) | **RED** ✅ `618363bd…b0c` |
| b4 | W9 never-raising | W9 pack `::TestSweepBehaviour::test_never_raising` | removed outer soft-fail try/except (`manager.py:7860/7899`) | **RED** ✅ |
| b5a | W9 registration | W9 pack `::TestSweepRegistration::test_manager_registers_the_sweep` | deleted the whole `register("skill_ab_inactivity_sweep", …)` call | **GREEN ⇒ UNPROVEN (GAP-5)** — pin greps a bare literal surviving at `manager.py:7879` (actor= tag) → **closed at `613651570`**: new `…_call_shape` pin REDs the same deletion (old pin stayed green — discriminator proven) |
| b5b | W9 decision marker | `::TestSweepRegistration::test_the_manager_hunk_carries_the_decision_marker` | removed the `# [decision] D2/C2 …` marker (`manager.py:4768`) | **RED** ✅ (first attempt DISCARDED: replacement comment contained "decision" — self-satisfying; re-run with neutral wording) |
| b6 | W9 tie→park | `…::TestSanctionedResolution::test_stalled_generation_tie_parks` | `_e6_winner:2145` tie → `return new_id, old_id, None` (tie picks a winner) | **RED** ✅ |
| c0 | resolve Path 0 (forced) | `…::TestForcedWinnerResolvedAtGuard::test_forced_winner_on_a_resolved_pair_is_refused` | deleted the 4-line `resolved_at` refusal (`skill_evolution_service.py:1855-1858`) | **RED** ✅ |
| c2 | resolve Path 2 (auto/threshold) | `…::test_auto_resolution_on_a_resolved_pair_is_refused` | deleted the refusal (`:2009-2012`) | **RED** ✅ |
| c3 | resolve Path 3 (max-extension) | `…::test_path3_force_resolve_on_a_resolved_pair_is_refused` | deleted the refusal (`:2043-2046`) | **RED** ✅ |
| c4 | resolve Path 4 (extension) | `…::test_path4_extend_on_a_resolved_pair_is_refused` | deleted the refusal (`:2078-2081`) | **RED** ✅ |
| c5 | sanctioned router 409 | `tests/unit/routers/test_skills.py::TestSanctionedResolveRoute::test_sanctioned_refuses_an_already_resolved_pair` | deleted the 12-line `HTTPException(409)` (`routers/skills.py:1818-1829`) | **RED** ✅ `9081b8…108633` |
| c6 | sanctioned SERVICE guard | (no pin existed) | deleted the 4-line refusal inside `resolve_ab_test_sanctioned` (`:2274-2277`) | **GREEN ⇒ UNPROVEN (GAP-4)** — reachable via `manager.py:7871` sweep + `:1049` → **closed at `613651570`**: new `…::test_service_refuses_already_resolved_pair` REDs the same deletion |
| d | W3 reversibility round-trip | `tests/unit/test_skill_stale_deactivation.py::TestThePairResolver::test_reactivate_reverses_a_sanctioned_resolution` | receipt written AFTER the flip (pre-fix order restored) | **RED** ✅ `b5760122…0efa76` |
| e | W4 pre-check (orphan prevention) | `tests/services/test_skill_evolution_service.py::TestCorrectiveFix::test_corrective_fix_on_a_desynced_member_creates_nothing` | removed the 21-line membership pre-check | **RED** ✅ |
| f1 | E-6 "never timestamps" | `…::TestSanctionedResolution::test_stalled_resolves_by_e6_newer_generation` | `_e6_winner` compares `created_at` (newest wins) | **GREEN ⇒ UNPROVEN (GAP-1/2)** — the fixture's generation order AGREES with `created_at`, so a timestamp rule still crowns the same member → **closed at `613651570`**: new `test_e6_inversion_earlier_created_higher_generation_wins` (explicit `created_at`) REDs the timestamp mutation |
| f2 | E-6 older-generation arm | same node | inverted both `_e6_winner` arms (older wins) | **RED** ✅ |
| f3 | E-6 tie→park, zero mutation | `…::test_stalled_generation_tie_parks` | tie branch resolves (picks newer) instead of parking | **RED** ✅ |
| g1 | D5 truncation `[:3000]` | `…::TestSurgicalEvolutionDefault::test_full_regen_only_on_explicit_marker` | reinstated `[:3000]` in `_generate_evolved_content` | **RED** ✅ |
| g1b | D5 truncation, surgical prompt (supplementary) | `…::test_surgical_prompt_carries_full_parent_body` | reinstated `[:3000]` in `_generate_evolved_payload` | **RED** ✅ (a second, independently written content site) |
| g2 | D5 exact-token carry-over gate | `…::TestCarryOverContentGate::test_single_shared_generic_word_does_not_sanction_removal` + `test_multi_word_title_requires_every_token` | exact-token set check → substring `any()` | **RED** ✅ |
| g3 | D5 sentinel | `…::test_empty_llm_response_never_becomes_content` | fault-guard weakened + sentinel-rejection block deleted | **RED** ✅ (positive-shape proof: the literal sentinel was ACCEPTED as a body) |
| g4 | D5 full-regen marker-only | `…::test_full_regen_only_on_explicit_marker` + `…::test_empty_llm_response_never_becomes_content` | full-regen branch returns the marker without regenerating | **RED** ✅ (attribution gap closed: LLM calls == 0) |
| h1-h5 | C3 run_id writers #1-#5 | m2 pin + `TestC3RefusedAdmissionRunIdPin` (5 nodes) | deleted `run_id=` at `skill_metrics_service.py:757`, `skill_evolution_tools.py:594`, `skill_job_dispatcher.py:769`, `skill_tools.py:420`, `job_processor.py:827` | **RED** ✅ (one writer per cycle) |
| h6 | C3 writer #6 (ACTIVE branch) | (no pin existed anywhere) | deleted `run_id=` at `job_processor.py:879` | **GREEN ⇒ UNPROVEN (GAP-7)** → **closed at `613651570`**: new `test_m2_pin_active_execution_refusal_record_carries_a_run_id` REDs the same deletion |
| i1 | D7 lineage keys | `tests/tools/test_skill_tools.py::TestServicesAvailable::test_skill_view_renders_real_lineage_keys` | restored the pre-fix key shape (`from_skill_id`/`to_skill_id`/`relation_type`) | **RED** ✅ `70d26c67…57b6` |
| i2a | defect D `_sync_pg_url` | `tests/integration/test_skill_audit_pg_url_rewrite.py::test_counter_reconcile_bare_postgresql_url_normalized` | removed the `postgresql://`→`postgresql+psycopg://` normalization from BOTH audit scripts | **RED** ✅ |
| i2b | defect D full pre-fix revert (supplementary) | same file (+ `test_stale_deactivate_bare_postgresql_url_guards_cleanly`) | `git checkout 5b8fef156^ -- scripts/audit/*` | **RED** ✅ (2 nodes) |

**Tally: 29 required cycles — 25 RED on first pass, 4 UNPROVEN (b5a, c6, f1, h6) → all 4 closed by gap pins at `613651570` and now RED ⇒ 29/29 mutation-proven.** Supplementary: g1b RED, i2b RED.

### 3a. Wave-2 gap pins added (commit `613651570`, test-only, +362 lines / +7 nodes; 233/233 green)

| Gap | Pin | Mutation-proved RED |
|---|---|---|
| GAP-1/2 | `…::TestSanctionedResolution::test_e6_inversion_earlier_created_higher_generation_wins` | timestamp-compare ✅ and older-generation ✅ |
| GAP-3 | `…::test_missing_variant_row_parks_untouched` | missing-row park branch removed ✅ |
| GAP-4 | `…::test_service_refuses_already_resolved_pair` | service guard deleted ✅ |
| GAP-5 | `tests/unit/test_skill_ab_inactivity_sweep.py::…::test_manager_registers_the_sweep_call_shape` | register call deleted ✅ (old pin stayed green — the discriminator) |
| GAP-6 | `tests/unit/routers/test_skills.py::TestSanctionedResolveRefusals::test_sanctioned_winner_not_a_variant_returns_400` + `::test_sanctioned_defective_arm_not_a_variant_returns_400` | each refusal removed ✅ (sibling stayed green) |
| GAP-7 | `tests/services/test_skill_capture_observability.py::test_m2_pin_active_execution_refusal_record_carries_a_run_id` | `:879` run_id write deleted ✅ |

---

## 4. Net-new reds — FAILED diff (criterion 2)

`head_only = 43` · `base_only = 48` (disclosure only; none in a lane-touched file; mostly shard-order red-set rotation)

| Class | N | Members |
|---|---|---|
| AMBIENT (shard-order) | 35 | file-isolation failure SETS byte-identical on both legs (e.g. `test_injection_api` 23/23 both sides); 3/3 green in serial isolation |
| AMBIENT (env) | 2 | `tests/tools/test_bash_spawn_stamp.py` ×2 — red on BOTH legs, **3/3 green both sides** once `ENSEMBLE_BASH_TRACKING_ID` is scrubbed (harness artifact) |
| FLAKY (quarantined) | 3 | `test_job_feedback_observer::test_tier1_sibling_owned_row_skipped` (row 154 re-observed) · `test_dependency_bus::TestGenerationCounterBump::test_per_parent_lock_serializes_db_insert` (standing StaticPool row, row-147 cross-ref) · `test_chart_tools_reuse_integration::TestT1RealDispatchReviveWalk::test_third_call_revives_revived_charter_unchanged` (**NEW row**) |
| **NET-NEW (deterministic, lane)** | **3** | `tests/unit/test_composite_scoring.py::TestTieBreaking::{test_challenger_wins_tie, test_challenger_wins_when_better, test_incumbent_wins_when_better}` |

**Root cause of the 3 net-new reds** (bisected to commit `868c6c616`, "Path 2 mirror guard"): the lane's guard

```python
# daemon/services/skill_evolution_service.py:2009
if getattr(ab_test, "resolved_at", None):
    raise ValueError(f"ab_test_group {ab_test_group!r} is already resolved")
```

misfires against the **pre-existing** consumer `tests/unit/test_composite_scoring.py`, whose A/B row is a `MagicMock` — MagicMock auto-creates `resolved_at` as a **truthy** object, so `getattr(..., None)` is truthy and the guard fires although the pair was never resolved. The lane updated its own new test but missed this consumer (the import is **indented/function-local** at `tests/unit/test_composite_scoring.py:366`, so a line-anchored `^from` grep misses it). This is the project's documented MagicMock auto-attribute trap reached through the `getattr(..., None)` default-value idiom.

**Resolution (test-only, authorized) — commit `28b1c8a68`:** one line in the test's `_build_evolution_service` mock builder, `ab_row.resolved_at = None`. Verification: 3× node runs **9/9 green**; whole file **14/14 green**; **base-inert proof** — the same file applied over a `git worktree add --detach … 0181dadbf` tree runs **14/14 green at base**, so the fix does not mask anything (the guard does not exist at base). Production code untouched. **Net-new = 0.**

---

## 5. Baselines, quarantine, pre-existing reds

- **6 phase4 reds** (`tests/manager/test_phase4_metrics_trigger_init.py`): confirmed **byte-exact at base** by the BASE leg (sqlite `EXISTS` syntax family) — excluded from net-new.
- **"3 ambient observability reds"**: the recording carries a count but no names. Re-measured: the observability family yields **7** reds at base (5× `test_resource_monitor_t1_row_identity` deterministic; 1× `test_resource_monitor_pg_fence::test_a_module_level_pg_engine_override_still_triggers_the_fence` — the only provably ambient-class red, green 3× serial, red only under xdist load; 1× `test_resource_monitor_platform_fallback::TestSelfThrottleLatch::test_the_throttle_latch_is_released_when_the_ema_recovers`). Best-faith trio for "3" = the `TestDegradedSentinelDropCount` class (exactly 3, deterministic). **7 ≠ 3 under the whole-tree algorithm — disclosed; the digest comparison is unaffected** (identical algorithm both legs, 0 head_only id in any `test_resource_monitor_*` family).
- **Flake by name** `test_ab_resolution_threshold_met` / `test_ab_resolution_force_resolve`: **PASSED at base**, absent from head_only (0) — the name-exclusion was not exercised.
- **Quarantine**: 0 head_only ids matched the lane's blast radius; 3 flaky members re-observed/registered → **1 consolidated gate row added** to `.agents/tester/QUARANTINE.md` (1 new + 2 cross-referenced), with the `ENSEMBLE_BASH_TRACKING_ID` fence note for future legs.
- ensure.md Core, `concurrency_atomic_unit_test`: raw **rc=1** (5 reds) — the 5 ids are **node-for-node identical to QUARANTINE row 153** (`test_instance_metadata_atomic.py`, base-proven) and the lane's diff reaches no `repositories/`/`instance_metadata` path ⇒ adjudicated PASS on the row's own evidence form, raw RC disclosed.

---

## 6. ensure.md Core + lane pack coverage

| Requirement | Verdict |
|---|---|
| No regressions in changed packs | **PASS-with-findings** — 5 infrastructure packs green (`skill_capture_observability` 28P, `skill_counter_drift` 37P, `metrics_capture_lane_off` 19P, `skill_dual_column_deactivation` 11P + 3/3 mutation legs, `f1_mint_contract` 9/9 arms); `inner_soul_memory_skill_metrics` rc=1 ×3 = QUARANTINE row 182 (byte-identical files base↔HEAD, no lane reach) |
| Deadlock / concurrency integrity | **PASS** (quarantine-adjudicated, raw RC=1 disclosed) |
| No sync DB calls on the event loop | **PASS** (same pack) |
| `dev.sh --timeout-graceful-shutdown 10` | **PASS** (`dev.sh:159`) |
| Async-await callers (`_get_system_prompt_tokens`, `_compute_context_usage`, `get_queue_stats`) | **OUT-OF-SCOPE** — the symbols are absent from all 14 lane-changed production files (`get_queue_stats` appears only at `manager.py:13903`, far from the lane's hunks at `@@ -4767` / `@@ -7810`) |
| Quarantine awareness | **PASS** — leg-3c deselect plumbing verified (`test/packs/skill_evolution_unit_test.sh:108`) |

### ensure.md Improvement Notices (user-owned file — NOT modified)
1. **Quarantine-aware rule vs pack reality.** ensure.md assumes quarantined tests are *skipped*; `concurrency_atomic_unit_test` has **no deselect plumbing** for QUARANTINE row 153. Suggested rewrite: *"Where a registered pack cannot deselect a quarantined family, validate via node-for-node FAILED-set identity against the QUARANTINE row plus a blast-radius disjointness check, and report the pack's raw RC alongside the adjudication."*
2. **"Changed packs PASS" presumes every changed test file has a pack.** Four lane-added files had no pack script and no PACKS.md row (`test_skill_ab_inactivity_sweep.py`, `test_skill_ab_disposition_manifest.py`, `test_skill_audit_pg_url_rewrite.py`, `test_skill_capture_admission_e2e_dev.py`), and `tests/unit/routers/test_skills.py` has a PACKS.md row with **no runner script**. Suggested addition: *"First enumerate changed test files and assert each is executed by at least one pack script (not merely named in a PACKS.md row); unregistered changed test files are a REQUIRED finding, not silent no-coverage."* — **resolved by this gate's PACKS.md lane block**.

---

## 7. Defect requiring a production-code change → LEADER DECISION (STOP)

🔴 **`scripts/audit/skill_stale_deactivate.py` prints the full DSN — including the cleartext `POSTGRES_PASSWORD` — to stderr.** The instance in `skill_stale_deactivate.py` was **newly added by this lane's commit `5b8fef156`**; the same leak pre-exists in `scripts/audit/skill_counter_reconcile.py`. Observed live (pytest captured stderr ⇒ the dev credential reaches evidence packs / CI logs). Suggested fix: redact the DSN before printing + a stderr-redaction pin (≈2 lines + pin). **Not applied** — production code is frozen and the fix is a leader call (fold into this lane vs a follow-up commission). Rotation stays user-gated per `deferred_key_rotation`. Anomaly `8a0b7ba5` (major) raised by the gate worker; POK row written.

---

## 8. Disclosures (things that were NOT proven as stated, and why)

1. **Tier label corrected**: the preflight worker reported "Tier 1" while its own evidence showed `daemon/manager.py` touched (+96) — that is a **core-seam hit ⇒ Tier 2 (mechanical)**. The regression legs were dispatched in Tier-2 shape, so the run itself was correct; only the label was wrong.
2. **Tier-2 advisory budget breached** (90 min → ~40 min/leg + collection + poison legs). Raised to a 150-min hard ceiling by the gate owner; the shape was NOT downgraded. Recorded because the commission's criterion 2 outranks an advisory budget.
3. **Observability baseline count**: "3 ambient observability reds" re-measures as **7** in that family (§5) — names not recorded in the plan; the trio is identified but the count is not reproducible. Re-measure at review time; do not copy.
4. **m2-pin premise corrected**: the extended m2 pin covers **2 of 6** C3 writer sites (#1, #5); the C3 pin class covers #2/#3/#4; nothing covered #6 until this gate added GAP-7.
5. **reghead's 144-file isolation sweep is single-run per file, NOT 3×** — the diff adjudicator reused it as the HEAD file-level arm and produced the BASE arm independently.
6. **Concurrent commit during adjudication**: `613651570` landed at 02:04:08Z mid-adjudication; the HEAD arm was re-run in full against a provenance-asserted `git archive 52ae65eaf` extraction — the 3 net-new findings are invariant across both HEAD arms.
7. **Plan-body/ledger contradiction** (POK `plan-body-ledger-agreement`): `plan-light.md:613` still says *"No manager.py hunks (design avoids it)"* while Q1 (`:145`) and the diff prove `manager.py` WAS touched. The implemented state matches Q1; the stale sentence is ledger residue — the BODY wins, and the body puts this lane at Tier 2.
8. **PACKS.md node-count staleness** on pre-existing rows (e.g. `test_skill_stale_deactivation` row says 56, file collects 59; `test_skills` row 75 → 84) — the lane block records current counts.
9. **Two harness artifacts in the diff adjudicator** (parallel-append TSV race; a `sort -t` quoting error) — both caught and rebuilt from preserved logs (400 logs retained).
10. **Worker tally slip**: the adjudicator's summary table says 36/2/2/3 (ambient/ambient-env/flaky/net-new); the per-id table (authoritative) says **35/2/3/3** — three flaky members, not two. The third (`test_chart_tools_reuse_integration`) is now a quarantine row.
11. **Diff worker mis-attributed the concurrent commit** to another agent; it was this gate's own gap-pin commit.

---

## 9. Test-only changes made by this gate (no production code touched)

| Commit | Scope | Change |
|---|---|---|
| `613651570` | 4 test files, +362 lines, +7 nodes | GAP-1..7 pins (E-6 inversion fixture, missing-row park, service sanctioned guard, register-call shape, 2 surfaced variant-membership refusals, C3 site-6 run_id) |
| `28b1c8a68` | `tests/unit/test_composite_scoring.py`, 1 line | `ab_row.resolved_at = None` (MagicMock auto-attr trap) |
| docs commit (this gate) | `.agents/tester/**` | PACKS.md lane block · QUARANTINE.md gate row · this RESULTS file · LESSONS entry |

---

## 10. Evidence index

- `/tmp/skillfix-regbase/` — BASE venue (logs, xml, `base_failed_ids.txt` + sha, `base_accounting.tsv`, `crash_node_classification.txt`, `progress.tsv`, leg-plan generator + runner)
- `/tmp/skillfix-reghead/` — HEAD venue (`head.failed.ids.txt` + sha, shard map sha `911eecc7…`, `iso-sweep-all.tsv`, per-shard logs)
- `/tmp/skillfix-diff/` — adjudication (`adjudication.md`, `head_only.txt` 43, `base_only.txt` 48, `final_classification.tsv`, 400 isolation logs, `iso_gatehead.tsv` 129 obs, base/gate-head `git archive` extractions, bisect output)
- `/tmp/skillfix-mut{A..F}-report.md`, `/tmp/skillfix-pinauthor-report.md`, `/tmp/skillfix-mockfix-report.md` — per-worker mutation tables with sha256 restore proofs
- `.agents/tester/PACKS.md` lane block · `.agents/tester/QUARANTINE.md` gate row · `.agents/tester/LESSONS/2026-11-18-skill-system-defects-gate-lessons.md`
