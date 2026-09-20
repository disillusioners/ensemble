# Merge Gate Report: LCA attest-first contract + gate HOLD-state (`feature/lca-attest-first-contract`)

Date: 2026-09-20
Delta under test: `94fc6da1..0b8f4de2` (2 commits: `f00775bb` feature + `0b8f4de2` test re-anchor; 32 files, +4378/−317)
Worktree: `agents-ensemble-wt-lca-attest-first` (clean pre-run, HEAD verified)
Workers: lca-infra-verify `70fc2b11` · lca-matrix-unit `d39d6d87` · lca-matrix-int `ae74256b` · lca-pg-lane `63ff07ad` · lca-boot-smoke `576293f6` (2 runs) · lca-ensure-core `223230b6` · lca-e2e-evidence `322c489d` · lca-hold-semantics `dccb3bad` (2 runs) · lca-tool-surface `5101eaa5`

## VERDICT: ✅ PASS WITH NOTES (2 contract-class notes for leader, 0 branch defects)

All scoped packs green, all acceptance jobs executed, evidence independently reproduced. Two findings require leader attention (neither blocks merge): the sync-merge conflict vs `latest` (planning-docs only) and the 3c ordering question (R2 pending-children vs HOLD). Details below.

## Job-by-job results

### 1. Full attestation matrix @ 0b8f4de2 — PASS (0 failed)
Ground truth = `lcau_matrix_*` cohort scripts ∪ delta test files ∪ adjacent seams (context_messages, long_tool_nudge), explicit file lists, collect-only captured before every run.

| Portion | Files | Collected | Passed | Failed | Skipped/Deselected | Runtime |
|---|---|---|---|---|---|---|
| unit + tools | 50 | 1148 | 1147 | 0 | 1S (env-gate: `langgraph.graph.message unavailable`, test_context_messages.py:2252) | 12s |
| integration | 35 | 229 | 218 | 0 | 2S (live OpenCode zoo) + 9 des (live-LLM-gated `test_service_tool_flag_off_byte_identical.py`, auto-deselect by pack design) | ~12 min cumulative, 7 sub-pack invocations, each ≤5 min |
| postgres lane | 1 | 21 | 21 | 0 | 0 (disposable PG14 :15436, conftest 5-var contract) | 2.86s |
| **TOTAL** | 86 | **1398** | **1386** | **0** | 3S + 9des | — |

- The "~932" figure from the task brief is not reproducible by def-test_-count at this commit (64 attest-named files = 822; cohort def-test union = 1055; collect counts exceed both via parametrize). Our ground truth (1398 collected) is larger and reproducible; unit cohort alone (1148) clears 932.
- Env-blocked live-LLM skips documented above; no API-key skips fired in the unit cohort.
- Integration pack split: worker correctly split along the pre-existing 6 sub-pack boundaries (per-tier timeout tuning: intf_f/m/s1/s2/s3/ints) + 1 supplementary run; every invocation under the 5-min cap.
- ⚠️ Dispatch-literal note: `-m integration` DESELECTS unmarked integration-dir tests (13/229 selected). The evidence tests are NOT integration-marked. Worker ran the literal (13P/216des ground-truth leg) then the full cohort via sub-packs. See LESSONS.

### 2. E2E evidence (the user's explicit ask) — PASS
Existing tests verified against reality: `test_e2e_attest_pure_toolcall_turn_evidence` (:188) + `test_e2e_bundled_call_corrected_evidence` (:504) exist in `tests/integration/test_attestation_attest_first_e2e.py`, drive the REAL gate node (`build_instance_graph`) + REAL tool body (no mocks on gate/tool), assert transcript shape + teacher text + decision row + counter bound. Both PASSED in-matrix (~21s each, timeout=120 override needed).

Independent variants (`tests/integration/test_attestation_attest_first_e2e_independent.py`, commit `55c1581a`, own harness — own state construction, own R2-facade/ledger stubs; real gate-node closure + real tool body): **3/3 PASS in 0.32s**
- (a) happy path: ALLOWED at report turn-end; attest msg content EMPTY + exactly 1 tool call; report = LAST AI (167w, no tool_calls); `ledger.reset` called once; `increment` never; zero reminders; counter 0 throughout.
- (b) c5d9a38a bundled: HOLD with `is_bundled_call=True`, bundled reminder injected (route=agent, reminder_count 0→1), ledger `increment`/`reset`/`set_escalated_and_reset` ALL not-called (counter-independence); corrected re-issue → ALLOWED; report LAST.
- (c) attest-only clean turn-end: HOLD with clean reminder (no bundled substring); report → ALLOWED; report LAST; counter 0.

### 3. Hold-state semantics live — PASS (7 pass + 1 strict-xfail pin; 1 contract note)
`tests/integration/test_attestation_hold_semantics_independent.py` (commits `771c7688` + `8f15bb16`), real gate node:
- **Reminder cap**: HOLD(0→1), HOLD(1→2), 3rd turn-end → `fall_through_to_meta_bypass_allow`, reminder count cleared, NO 4th reminder; dedicated cap log row `event=leader_completion_gate_hold_reminder_cap`. No infinite loop. `ATTESTATION_REMINDER_CAP==2` pinned.
- **Counter static at bound**: denied_count pinned 2/3 across HOLD cycles; no `terminal_after_bound`; proper reset on attested allow (trigger 1).
- **No-attestation precedence (both task-required constructions PASS)**: user_answer_pending=True + would-HOLD shape → plain allow (step 1 precedes attested step; no reminder/nudge/marker side-effects); no-attest + pending_children=1 → R2 allow.
- **Window integrity**: attest → HOLD+reminder → report (no new attest) → attest STILL in-window → `decision=allowed`, not fresh deny.
- ⚠️ **NOTE N1 (3c, pinned xfail-strict)**: `attestation_present + no report + pending_children=1` → **HOLD fires**; R2 pending-children does NOT precede the attested step (decide() order: 1 user_answer_pending → 2 attested-split → 3 R2). The D-entry specifies HOLD unconditionally on attestation_present (no pending-children carve-out), so landed behavior matches the decision text; the task brief's "branch-5 allow still precede the attested step" holds verbatim only for user_answer_pending. Pinned `@pytest.mark.xfail(strict=True)` — XPASS will force removal if decide() ever gains an R2 carve-out. Leader ratification recommended: should pending-children suppress HOLD?

### 4. Window integrity — PASS (see job 3 row)

### 5. Tool surface — PASS (72/72 in 0.36s; 10 new chain pins, commit `01d0e3a9`)
- Static: description/docstring (:364) AND `_full_doc_` (:442) LEAD verbatim with the call-alone contract sentence; teacher constants byte-exact vs D-entry; ContextVar trio present; `wrapped_tools_node` hook fires `set_attest_caller_content` for attest_completion before ToolNode (:662-690).
- Coverage audit found dev gaps: existing tests bypassed the runtime hook (direct setter) and pinned description as substring-only. New file pins: R1 bundled chain through REAL wrapped_tools_node → BUNDLED teacher; R2 clean chain → CLEAN teacher; sibling-tool isolation; R3 degradation (reset + hook-absent, 5 invocations stable CLEAN); stack-inspector fallback both arms (bundled-in-locals → BUNDLED; none → CLEAN); verbatim-LEADING position pins; byte-exact constant pins.
- ⚠️ Minor caveat (documented in-file): the stack-inspector fallback can observe AIMessages held in caller-frame locals; tests use a whitespace-only sentinel to route around it. Runtime path always supplies ContextVar via the hook — production unaffected.

### 6. PG lane + boot smoke — PASS (2 live boots + PG pytest lane)
- PG lane: 21/21 (`test_attestation_live_descendants_pg_lca.py`, disposable PG14 :15436, 5-var conftest contract, teardown verified).
- Boot smoke A (happy path): real daemon (uvicorn :8090) on disposable PG (:15433), mock LLM (:18080). Boot log: **default mode = enforce** (`DEFAULT_MODE: Literal["enforce"]`, attestation_resolver.py:111). Transcript: AI[0] pure attest (empty content, 1 tool call) → AI[1] LAST = standalone report 182w → COMPLETED at that turn-end; counter 0; reminder absent; canonical `decision=allowed attestation_present=True` row. Clean shutdown, all ports freed.
- Boot smoke B (delegated mission, c5d9a38a replay): gate row with **`attestation_required=True`** (branch A couldn't reach), `delegation_tool_call_total=1`, decision=allowed at report turn-end. Live transcript: bundled AI[1] (196w + attest_call) → **tool-teacher correction in-turn via ContextVar hook** → clean attest AI[2] (empty) → report AI[3] LAST (199w). Counter 0.
- ⚠️ **NOTE N2 (documented limitation)**: gate HOLD did NOT fire live in either boot — LangGraph re-invokes the model after every tool_call, so a cooperative model always corrects WITHIN the turn (the bundled message is never final AI at `graph_end_candidate`). Live flow: tool-teacher is the primary correction; gate HOLD is the turn-end backstop for defiant/edge turn-end shapes (the c5d9a38a incident class). HOLD enforcement itself is proven at the real-gate-node seam (jobs 2/3). Not a defect.

### 7. Sync-merge with latest `da1f796d` — ⚠️ CONFLICTED (planning docs only)
- `git merge-tree --write-tree da1f796d 0b8f4de2` → exit 1: **content conflict in `.agents/shared/planning/leader-completion-attestation/decisions.md`**; `requirements.md` auto-merges cleanly. Overlap set = exactly those 2 files (both branches appended to the same LCA planning docs off shared base `94fc6da1`). **Zero code conflicts.**
- Task premise "disjoint files / zero conflicts" is falsified. Resolution class: append-append on planning notes — routine, content-level, owner-side. The gate did NOT merge anything; branch NOT moved; branch-forward testing not needed (all jobs ran at `0b8f4de2` + gate-owned test commits).
- Post-gate branch state: gate-owned test-only commits on top of `0b8f4de2`: `01d0e3a9` → `55c1581a` → `771c7688` → `8f15bb16` (+ docs commit, see below).

### 8. No production code changes — ✅ HELD
Zero production files modified by the gate. All 4 gate-owned commits are test-file-only (verified per-commit: branch pre-check, `merge-base --is-ancestor 0b8f4de2 HEAD`, `git diff --name-only 0b8f4de2..HEAD` ⊂ tests/).

## ensure.md Validation (Core, scoped)
- Critical 2/2 relevant: ✅ concurrency pack `test/packs/concurrency_atomic_unit_test.sh` — **98P/0F/74S, 11.13s, EXACT baseline parity**; ✅ `dev.sh` `--timeout-graceful-shutdown 10` PRESENT (:99 comment, :102 flag). "No regressions in changed packs" — ✅ all scoped packs green (see job 1-6).
- ⚠️ Anomaly: `concurrency_atomic_unit_test` is NOT registered in PACKS.md (script-presence fallback used). Registration follow-up logged below.

## Anomalies & follow-ups
1. 🔴→🟠 Sync-merge conflict (job 7): decisions.md content conflict vs latest. Owner resolves planning-doc append-append before merge. No code conflict.
2. 🟠 N1/3c ordering question (job 3): R2 pending-children vs HOLD precedence — leader ratification; xfail-strict pin in place.
3. 🟢 PACKS.md registration drift: `concurrency_atomic_unit_test` (and the `lcau_matrix_*` family) lack registry entries — this gate entry documents invocations; a registry-table pass is a docs follow-up (main-checkout `PACKS.md.new` leftover also observed, untouched).
4. 🟢 `docs/setup.md` NOT in the delta (D-entry file-list said it would be) — either landed in an earlier branch commit or was dropped; doc-claim vs delta mismatch, informational.
5. 🟢 "~932" not reproducible as a count at this commit (see job 1); ground truth redefined and captured.
6. 🟢 Live-HOLD masking (N2): tool-teacher corrects in-turn; gate HOLD is backstop — consider one defiant-mock boot in a future gate if system-level HOLD evidence is wanted.

## Code changes summary (all gate-owned, test/docs only)
- `tests/unit/tools/test_attestation_surface_chain.py` (+656, 10 tests) — `01d0e3a9`
- `tests/integration/test_attestation_attest_first_e2e_independent.py` (+1269, 3 tests) — `55c1581a`
- `tests/integration/test_attestation_hold_semantics_independent.py` (+1006, 8 tests; 7P+1xfail) — `771c7688` + `8f15bb16`
- `.agents/tester/MOCK_TESTS.md` boot-smoke registrations (boot-smoke worker) + this report + PACKS.md gate entry + LESSONS — docs commit (hash below).

## Overall Status
- Matrix: ✅ 1386P/0F/3S/9des (1398 collected)
- E2E evidence: ✅ independent 3/3 + dev 2/2 in-matrix
- Hold-state semantics: ✅ (N1 pinned)
- Tool surface: ✅ 72/72
- PG lane + boot smoke: ✅ (N2 documented)
- ensure.md Core: ✅ 2/2 + changed-packs green
- **Testing Complete: ✅ READY — PASS WITH NOTES (N1 ratification + N2 awareness + sync-merge resolution before merge)**
