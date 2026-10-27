# Verification Report — Compaction ns-trap fix (L2 precall-95 + L3 CLE `checkpoint_ns` empty-StateSnapshot trap)

- **Date**: 2026-10-27T12:14Z
- **Verifier**: Tester instance `157f568d-5049-4499-8d6a-ac9a369a7c82` (Test Leader) via 4 workers:
  W1 `1821dfb2-a376-4a44-9e30-2768e3a0d149` (baseline+L1) · W2 `1521b9d2-2e02-4187-bd33-e81e5a34a3be` (broader) · W3 `2127bdea-6ad1-463d-a7f6-616d21a0be47` (mock-vs-real+symptom) · W4 `e6883c71-1a98-451d-b206-45cc0cdc5914` (mutations, exclusive)
- **Target**: worktree `agents-ensemble-wt-compact-ns-trap-fix` · branch `fix/precall-compaction-ns-trap` @ `e8b0dded942c642ad03541d1e95412d02902dd0b` (base `373ea47ca`, 3 commits)
- **Branch blast radius** (git diff --stat base..HEAD): `daemon/graph.py` (+159), `tests/unit/services/test_proactive_compaction_fix_p1b.py` (+55), `tests/unit/services/test_proactive_compaction_ns_trap_fix.py` (+427). Nothing else.

---

## VERDICT (FINAL, updated 12:31Z after B1 resolution): **SHIP** — B1 resolved by commit #4 `1440c5fc0` (test-only pin refresh, verified); file + registered-pack re-runs green. The original 12:14Z verdict was NO-SHIP as-is **solely** due to B1; see the "B1 RESOLUTION ADDENDUM" at the end. The fix substance was fully verified on every axis in the original run (all 7 matrix items).

| Axis | Result |
|---|---|
| Fix mechanism (real langgraph) | ✅ PROVEN (Item 3) |
| Hook production config flow / original symptom | ✅ CLOSED (Item 4) |
| CLE empty-replacement interlock (data-loss guard) | ✅ mutation-proven fires (Item 5) |
| A/B vs base (tests genuinely guard the fix) | ✅ dev claim CONFIRMED: 6 assertion failures + 1 test-level ImportError (Item 6) |
| Targeted + adjacent regression | ✅ green except Blocker B1 (Items 1/2/7) |
| Branch-caused failures | 🔴 **1** — stale test pin B1 (superseded coverage exists) |
| Pre-existing failures (base A/B proven) | 2 (both outside branch diff; 1 already quarantined, 1 quarantine-ledger gap) |
| Tree state | ✅ byte-identical to baseline porcelain (` M .agents/tidier/notes.md` foreign dirt only); shas verified; zero commits/stashes/stages |

**Grand total across all waves: 758 passed / 3 failed (all attributed) across 34 distinct suite files + scratch/A-B/mutation runs. Zero TIMEOUTs; every run under `timeout 300`, `-p no:randomly`, one file per invocation.**

---

## Scope Decision

Full unit suite NOT run. Change set = 1 source file (`daemon/graph.py` compaction paths) + 2 test files → targeted + semantically-adjacent suites per mission time-box. ensure.md scoping: Core gate "no regressions in changed packs" validated via the matrix; `concurrency_atomic_unit_test`, sync-DB-call, and `dev.sh` gates are **outside this fix's blast radius** (no DB/lock/dev.sh changes) — scoped out. Release Gate not triggered (not cross-module architecture). No ensure.md contradictions encountered (all validations ran as scoped pack-style runs with dual-layer timeouts). **FE untouched by this fix** (no `frontend/` files in branch diff) → no browser automation run, per mission.

Premise correction (benign): worktree already had a worktree-local venv (`.venv/`, Python 3.13.3, pytest 9.0.2, gitignored); `uv sync` = 0s no-op audit; `uv lock --check` clean. Mission's "no venv at creation" premise outdated — likely dev synced during the fix phase.

---

## Matrix results

### Item 1 — Targeted baseline ✅ (dev claims reproduced EXACTLY)
- `test_proactive_compaction_ns_trap_fix.py`: **10 passed** · `test_proactive_compaction_fix_p1b.py`: **29 passed** → combined **39 passed** (dev claim: 39) ✅
- 5-suite compaction family = the five `tests/unit/services/test_proactive_compaction_*.py` files (determined by `--collect-only` subset-sum, NOT guessed; mission's "4 known members" hint sums to 119, not 96 — it is wrong): ns_trap_fix 10 + p1b 29 + p1 42 + p2 13 + symptom_acceptance 2 = **96 passed** (dev claim: 96) ✅

### Item 2 — Broader regression ✅ (one known red, see B1)
18 symbol-matched/mandated files run individually, verbatim:
`test_compaction.py` 129P/1F (B1) · `test_symptom_repair_ladder.py` 31P · `test_compaction_escape_hatches_round2.py` 38P · `test_ladder_misc_p2.py` 9P · `test_compact_executor.py` 72P · `test_injected_notes_hoisting_acceptance.py` 2P · `test_compact_executor_revive_brick_e2e.py` 10P · `tests/integration/test_message_metadata_hook_placement.py` 11P · `test_instance_messaging_compaction_guard.py` 12P · `test_multi_reuse_lifecycle.py` 11P · `test_compact_executor_defect1_pause_resume_lifecycle.py` 3P · `test_command_dispatcher.py` 76P (/compact mandated) · `test_compaction_multimodal.py` 30P · `test_compaction_never_blocked.py` 40P · `test_compaction_model_config.py` 31P · `test_compaction_empty_guard_fallback.py` 9P · `test_injection_compaction.py` 9P · `test_compact_fired_watchers_deliver_before_compact.py` 13P.
- Registered pack `test/packs/compaction_unit_test.sh`: exit 1 — "1 failed, 129 passed in 9.94s" → **B1 reproduces at pack level** (post-merge the pack stays red until B1 lands).
- Integration fast-pack: **none exists** (only `compaction_unit_test.sh`; `tests/integration/test_compaction_e2e.py` is harness-blocked pre-existing — `tests/conftest.py` installs a mock `langgraph` namespace; ImportError under `-m integration`; branch diff on conftest/pyproject/uv.lock empty).

### Item 3 — Mock-vs-real (TrueAuto rule) ✅ PROVEN in REAL langgraph 1.0.9 (project pin matches)
No pre-existing real-langgraph ns-trap test existed; `/tmp/ns_trap_real_proof.py` (pure langgraph, no daemon imports):
```
langgraph version: 1.0.9
A: bool(snapshot)= True next= () messages_len= 3      ← root/thread-id-only config returns REAL state
A: first= 'msg-0' last= 'msg-2'
B: bool(snapshot)= True str(next)= () messages_len= 0  ← ns-stamped config returns truthy-but-EMPTY snapshot
ITEM3: PROVEN
```
Nuance (honest): with a plain node named `agent`, the ns-stamped read raises `ValueError: Subgraph agent not found`; the truthy-empty form reproduces when `agent` is a nested subgraph (production shape; matches the live instance-84b96108 observation). Either way root state is unreachable without stripping `checkpoint_ns` → the fix is required in both forms.

### Item 4 — Original-symptom behavioral close ✅ CLOSED
Closure chain: (Item 3 mechanism real in langgraph 1.0.9) + (cited tests pin the hook's production config flow):
- `test_proactive_compaction_ns_trap_fix.py::TestNsTrapL2EngineSeesRealMessages::test_compaction_proceeds_with_ns_stamped_thread_config` (:138) — hook handed ns-stamped caller config (`checkpoint_ns='agent:task-1'`, the production shape): :174 `assert captured["ctx_messages"] == graph_msgs` ("engine must be fed the checkpoint channel (real list), not empty"), :178 `outcome.rebuilt_payload is not None`, :179 `compacted_at` stamped, :182 `checkpoint_ns is None` on every aget_state config.
- `::test_aget_state_configs_recorded_thread_id_only` (:188) — ≥2 reads, all thread-id-only.
Fix sites: `_root_thread_config` def @ `daemon/graph.py:7532`; hook uses @ `:7746` (L2 read), `:7889` (L2 re-read), `:9313` (CLE).
Never the frozen `messages=0` + `skipped_injections_dominate` signature → original symptom (0 relief over ≥50 attempts, 665,838→950,994 tokens = 136% of 700k window) is closed at the hook level.

### Item 5 — CLE interlock + AST/absence pins + mutation spot-check ✅
- `TestSeamEmptyReplacementInterlock` @ `ns_trap_fix.py:361`: **2 passed**. Pins: empty replacement → `msgs_writes == []` AND exactly one `compacted_at` write (verbatim assertions captured).
- AST/absence pins: **7 passed** — helper-exists pin; SOURCE pin (`thread_config = _root_thread_config(instance_id)` + `abort_policy="raise"` count==1); **AST pins** warn+raise @ graph.py:9321 (CLE empty-snapshot guard) and :9483 (empty-replacement branch); **ABSENCE pin** (no direct `aupdate_state(` in CLE handler region); tap-label set-membership pin.
- **Mutation spot-check — pin FIRES**: neutered `if False and not result.replacement_messages:` (seam `:147`, ONE site, diff-verified). RED verbatim: `AssertionError: empty replacement must NOT reach a messages write … Left contains one more item: (…, {'messages': [RemoveMessage(… id='__remove_all__')]}, {'as_node': 'agent'})` — i.e., the neutered guard let the empty replacement land a live-channel wipe (`__remove_all__`), exactly the data-loss mode the interlock exists to prevent. Restore sha-verified (`1f697be4d34162e0f443e213eef0724620e0f6e740a71aaf26b059ff3da5c04e` == pre), GREEN re-run 2P.

### Item 6 — A/B base-run ✅ dev claim CONFIRMED (precise split)
BASE `daemon/graph.py` (`git show 373ea47ca:…`; sha `2edaffe2…` ≠ HEAD sha `3586ad4e…`; only graph.py in diff):
- `test_proactive_compaction_ns_trap_fix.py` vs BASE: **`7 failed, 3 passed in 0.34s`** = exactly **6 pure assertion failures** (#1 engine-fed-empty, #2 checkpoint_ns-in-config, #3 guard-return, #5 source-substring, #6/#7 AST-If-absent) + **1 test-level ImportError** (#4 `_root_thread_config` absent at base; file collected fine — NOT a collection error). **Dev's "6 real assertion failures" claim: CONFIRMED** with the 6+1 split.
- Secondary (p1b vs BASE): `2 failed, 27 passed` — both assertion failures (seam-routing pin `assert 0 == 1`; direct-aupdate_state absence pin) — consistent with fix narrative.
- Restore sha-verified (== `3586ad4e5fc8a83db3a115d47be571fef0f2e4b7c0a935af17eb402b80da9a7e`); green re-run both files: **39 passed** ✅.

### Item 7 — L1 regression ✅ (lane untouched, no drift)
18-file `_maybe_compact_context` sweep: **375 passed / 3 failed / 7 deselected**. The three direct L1 drivers green (guard 12P, multi_reuse 11P, never_blocked 40P); full instance_messaging family 100% green. 3 failures attributed: B2 (quarantined, base A/B proven at `639b7129b` per QUARANTINE.md:135), B3 (sealed-hash drift, source files byte-identical base↔HEAD → fails identically at base; quarantine-ledger gap), B4 (harness, deselected-by-default integration file).

---

## Defect findings

**B1 🔴 BLOCKER (branch-caused, test-side — the sole NO-SHIP reason)**
`tests/unit/test_compaction.py::TestSentinelAcrossPersistSites::test_persist_site_invariants_graph_reactive_uses_as_node` — FAILS at HEAD (`assert -1 >= 0`), PASSES at base (literal existed @ base graph.py:9437). The fix deliberately moved the CLE persist into `daemon/services/_compaction_persist_seam.py`; the pin still greps graph.py for `'messages': replacement_messages`. **Production behavior correct and superseding coverage EXISTS**: p1b `TestPreCall95InjectionDominatedSkip::test_both_seam_writes_carry_as_node_agent` (:475) pins the seam mid_turn arm (messages write FIRST with `as_node="agent"`, then `compacted_at`, both `as_node="agent"`, order-pinned) + `TestCLEHandlerNoDirectAupdateState` absence pin + the interlock pin. Impact as-is: `test_compaction.py` red at HEAD and registered pack `compaction_unit_test.sh` exits 1 → ensure.md Core critical "every changed-pack PASS" fails post-merge. **Remediation (branch, ≤10 lines, test-side): update/retire the one stale pin to target the seam; then re-run `tests/unit/test_compaction.py` + the pack — expect 130P and pack PASS; verdict flips to SHIP with no other re-runs needed.** Not fixed here: mission mandate is verification-only, no commits.

**B2 🟢 pre-existing** — `tests/integration/test_explorer_shared_context_paths.py` 1F: quarantined, base A/B proven (QUARANTINE.md:135). No action.
**B3 🟢 pre-existing** — `tests/test_chart_image_delivery_e2e.py::…test_phase_d_does_not_modify_sealed_artifacts` 1F: pins sealed hashes of `daemon/sources/registry.py`/`daemon/constants.py`, byte-identical base↔HEAD → fails identically at base. **Not in QUARANTINE.md — ledger gap; owner should add a row.**
**B4 🟢 pre-existing harness** — `tests/integration/test_compaction_e2e.py` unrunnable under `-m integration` (tests/conftest.py mock-langgraph namespace leak → ImportError); blocks any future compaction integration pack. Branch diff on harness files empty.
**P1 🟡 pack-script contract** — `test/packs/compaction_unit_test.sh`: `set -euo pipefail` aborts on pytest failure BEFORE the RESULT echo → no explicit `RESULT: FAIL` line on red (violates explicit-output pack contract). Suggest trap-based RESULT emission.
**P2 🟡 PACKS.md drift (pre-existing)** — `compaction_unit_test` row describes a "22-file authoritative set … 781/0/0", actual script is single-file (130 tests). Re-baseline row.

## ensure.md status (Core, blast-radius scoped)
- Critical "no regressions in changed packs": ❌ as-is (compaction pack red via B1) → the only failing gate; flips ✅ with B1's follow-up.
- All other in-scope validations: ✅ (matrix above). Out-of-blast-radius gates (concurrency pack, sync-DB, dev.sh): scoped out, rationale above. No contradictions → no Improvement Notices.

## Final tree state (worktree)
`git status --porcelain` = ` M .agents/tidier/notes.md` (foreign baseline dirt, untouched) — **plus this RESULTS file (uncommitted by design; giter commits)**. No commits/stashes/stages/pushes by any wave. Sanctioned mutations (5.3 seam neuter; 6.2 base swap) each restored with sha256 verification (both verified == pre-mutation).

**Path to SHIP** (pre-specified at 12:14Z): land B1's pin refresh on the branch → re-run `tests/unit/test_compaction.py` (expect 130P) + `timeout 300 bash test/packs/compaction_unit_test.sh` (expect PASS) → SHIP. No other re-runs required. **→ EXECUTED AND SATISFIED, see addendum below.**

---

## B1 RESOLUTION ADDENDUM (2026-10-27T12:31Z) — FINAL VERDICT: **SHIP**

Pre-specified confirmation scope executed exactly (worker `4e0ce6c9-c98e-4e89-bc36-56f703936efd`; nothing else re-ran).

**Commit verified**: `1440c5fc0f128395c192902f5dd5b1b1a59ef942` ("test(compaction): refresh reactive as_node pin to the seam-routed CLE call site"), commit #4 on top of `e8b0dded9`.

**1. Test-only delta CONFIRMED** — `git diff --stat e8b0dded9..HEAD`: `tests/unit/test_compaction.py | 21 ++++++++++++--------- · 1 file changed, 12 insertions(+), 9 deletions(-)` (numstat `12  9`). `daemon/` diff vs e8b0dded9: EMPTY. `tests/unit/services/` name-only: EMPTY (family byte-identical). Only changed path in the range = the one test file, single method, exactly as claimed.

**2. Refreshed pin verified** (full hunk inspected) — anchors on `'abort_policy="raise"'` (comment documents uniqueness: the CLE reactive seam call is the only one; the L2 hook uses `"fail_open"`), carves the enclosing `persist_compaction_result(` call (`rfind` before the anchor), and asserts `"mid_turn=True" in call` — i.e., mid_turn is asserted INSIDE the same seam call as the raise policy. Docstring points at the complementary behavioral pin `test_proactive_compaction_fix_p1b.py::test_both_seam_writes_carry_as_node_agent`. Line classification: executable +5/−6 (strictly simpler — old `if idx < 0` fallback branch dropped); prose +7/−3.

**3. Bound adjudication (the +12/−9 vs ≤10-line bound)**: raw added lines = 12 > 10, but the executable delta is 5 lines (net −1 executable); the overage is purely docstring/comment prose that documents the anchor's uniqueness rationale and the complementary pin. The bound's intent — trivial, single-file, test-only, obviously-correct refresh, zero production change, no other files — is fully met. **ACCEPTED; no further action.**

**4. Re-runs (exactly one each, both `timeout 300`):**
- `tests/unit/test_compaction.py` (`-p no:randomly`): **`130 passed in 9.85s`** — exit 0 (dev claim matched).
- `test/packs/compaction_unit_test.sh`: verbatim tail `130 passed in 9.92s` / **`RESULT: PASS`** — exit 0 (dev claim matched; pack emits RESULT on the success path — the P1 note applies to the failure path only).

**5. Final tree**: `git status --porcelain` = ` M .agents/tidier/notes.md` + uncommitted RESULTS file only. Zero commits/stages/stages by verification waves.

**Minor note (no action)**: the refreshed pin carves the call span up to the first `)` after the call opening — a source-inspection brittleness acceptable here because the behavioral guarantee is redundantly pinned (p1b as_node/order pin + interlock + absence pins); this pin's job is to catch the anchor/flag regression cheaply.

**FINAL VERDICT: SHIP** — all 7 matrix items green; sole blocker B1 cleared at `1440c5fc0`; branch `fix/precall-compaction-ns-trap` is merge-ready from the testing standpoint. Residual non-blocking items unchanged and owner-tracked: B2 (quarantined), B3 (quarantine-ledger gap), B4 (harness), P1 (pack RESULT-on-fail), P2 (PACKS.md row re-baseline).
