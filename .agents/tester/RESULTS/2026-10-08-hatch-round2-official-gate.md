# OFFICIAL TEST GATE — Escape-Hatch Hardening (compaction subsystem, round 2)

- **Date:** 2026-10-08 (gate window 07:22–07:49 UTC)
- **Worktree:** `/home/nea/ensemble-src-wt-compaction-escape-hatches` · branch `feature/compaction-escape-hatches`
- **HEAD under test:** `687cc4096979d661a557687b70eb8c5ba9183ca2` (`687cc4096`, "W2-N1 loud warn + T2-N1 straddle corpus"), tree clean, verified 07:28:19 UTC
- **Base:** `74b36b478ff9668ffadca7aa2d869fb688da3c3d` · lane: `9a07fb162 → 6a1c71ea4 → aae38e74b → f85370a85 → 687cc4096`
- **Environment:** per-worktree uv venv, **CPython 3.14.7**, `daemon.__file__` resolves INSIDE the worktree (editable-install trap checked — clean). Historical py3.13 collection rot N/A on 3.14. `uv` requires full path `/home/nea/.local/bin/uv`.
- **Commissioner-mandated scope:** 5 checks; backend only; report-only (fixes route to developer).

## Verdict — **PASS** (all 5 commissioned checks green; 1 important finding routed to developer)

| # | Check | Result |
|---|---|---|
| 1 | Mutation-verify of escalation pin | ✅ PASS — pin FAILS under mutation (self-identifying kill site `graph.py:7499-7522`), PASSES after byte-identical restore |
| 2 | Hatch pin suite | ✅ 178/178 (round2 38 + corpus 140); round-1 claim re-pinned (16 mapped ids) |
| 3 | Collateral sweep | ✅ 13/14 modules PASS — 🟠 F1 (lane-caused stale test, base-side PROVEN) |
| 4 | Pre-existing-11 adjudication | ✅ IDENTICAL (SHA256 match) — failures pre-date the lane |
| 5 | Mock discipline | ✅ CLEAN — all seams REAL, zero substitution hits |

Findings ranked: 🟠 F1 test-contract update owed before merge (Check 3) · 🟢 round2 header docstring anchor drift (`7461-7484` cited vs live `7499-7522`) · 🟢 `_warm_loader_caches_sync` caller surface has zero tests in `test_manager.py` · 🟢 `test_compaction_e2e.py` 3 tests marker-deselected (env-gated, out of scope).

---

## Check 1 — OFFICIAL MUTATION-VERIFY of the escalation pin: ✅ PASS (worker 84ff8c8c, 07:46:50–07:49:00 UTC)

Target facts (inventory-verified at HEAD): escalation read block = `daemon/graph.py:7499–7522` inside `_maybe_precall_compact_95` (fn starts :7362); gate predicate consumer at :7523. Pin test: `tests/unit/test_compaction_escape_hatches_round2.py::TestLane4CIBlindSpotPinReadBlock::test_escalated_row_lowers_gate_and_hook_fires_engine`.

Evidence chain:
1. **Preflight:** HEAD `687cc4096979d661a557687b70eb8c5ba9183ca2` ✓, tree clean ✓, worktree-local import ✓
2. **Baseline (clean):** `1 passed in 0.33s` — PASS, exit 0
3. **Backup:** `/tmp/graph.py.hatch-gate.pristine`, 628,048 bytes, SHA256 `9675dc43d76e23c72fde3aff89987d922d0f0bbf110570a1ca06cb30a428de56` (matches live)
4. **Mutation** (single-line semantic kill at :7514): `if _inst_row is not None and is_proactive_escalation_active(_inst_row):` → `if False and _inst_row is not None and ...:  # GATE MUTATION (official gate)` — git diff captured verbatim (hunk `@@ -7511,7 +7511,7 @@` inside `_maybe_precall_compact_95`)
5. **Mutated run: FAIL as mandated** (exit 1, 0.36s, not 124). Excerpt:
   ```
   round2:1462 AssertionError: Lane 4 read-block pin FAIL: with a durable escalated row, gate_ratio=0.80
   MUST admit the 0.85× payload and the hook MUST reach the engine. Returning _PRECALL_NOOP here means
   either the read block at daemon/graph.py:7499-7522 was deleted/regressed, the writer did not persist
   the escalation, or the estimator was patched to an out-of-band value.
   ```
   The pin's own failure message names the kill site — the test pins exactly what it claims.
6. **Restore proven byte-identical (5-way):** `cmp` silent ✓ · SHA256 match ✓ · `git diff daemon/graph.py` empty ✓ · `git diff --stat` empty ✓ · `git status --porcelain` empty ✓ — zero residue
7. **Post-restore:** single pin `1 passed in 0.31s` ✓ · full module `38 passed in 14.52s` ✓ (no residual contamination)

**Phase B — F1 base adjudication:** temp worktree `/tmp/ens-sbx-hatch-base2` @ `74b36b478ff9668ffadca7aa2d869fb688da3c3d` (own venv, import verified inside, SHA confirmed): `test_reactive_compaction_returns_none` → **`1 passed in 1.15s` — PASSES on base**. Cleanup clean (worktree removed plain, list clean, dir gone). **F1 = lane-caused, PROVEN.**

## Check 2 — Hatch pin suite: ✅ ALL PASS

### 2a. Round-2 module `tests/unit/test_compaction_escape_hatches_round2.py` — PASS, 38/38, 13.45s (worker 167e1f27, 07:40:11Z)
- `TestLane2HalvingPairing` 3/3 — incl. **`test_halving_re_snap_drops_orphan_tool_messages` (C1 brick pin)** PASSED; siblings `test_halving_no_orphan_with_pair_at_cut_boundary`, `test_snap_helper_is_module_level` PASSED
- **W3 pin** `TestH4SystemPromptTokens::test_executor_helpers_carry_real_system_prompt_tokens` PASSED
- **Lane4 escalation pin** `test_escalated_row_lowers_gate_and_hook_fires_engine` PASSED (+ negative-control sibling `test_no_escalation_at_same_band_does_not_fire` PASSED)
- Counts: 38 passed / 0 failed / 0 skipped / 0 error / 0 deselected. Exit 0.

### 2b. Round-1 corpus — PASS, 140/140, 19.10s total (worker df36da1d, 07:40:24Z)
| Module | Result | vs baseline |
|---|---|---|
| `tests/unit/test_compaction_never_blocked.py` | 40/40 PASS, 9.11s | = 40 ✓ |
| `tests/unit/services/test_proactive_compaction_fix_p1b.py` | 28/28 PASS, 0.56s | = 28 ✓ |
| `tests/unit/services/test_compact_executor.py` | 72/72 PASS, 9.43s | = 72 ✓ |

**Round-1 claim re-pinned** ("3 engine skip conditions → 50%-tail floor on over-budget/force paths") — 16 mapped test ids all PASSED:
- Skip 1 (all-injected+unanswered): `test_all_injected_falls_through_to_floor` (:341–362), `test_signature1_all_injected_unanswered_engages_floor` (:476), `test_required_path_engine_never_blocks_on_all_injected` (:963)
- Skip 2 (anti-refire stamp): `test_dedup_short_circuits_all_other_layers_under_budget` (:298), `test_signature3_seam_persists_shrink_not_stamp` (:500)
- Skip 3 (non-quiescent/status-reject proactive skips preserved): `test_non_quiescent_skip_does_not_crash` (:1120), `test_status_reject_skip_does_not_crash` (:1159)
- 50%-tail floor (`COMPACTION_TYPE_TAIL_TRUNCATION_LAST_EFFORT` @ threshold 0.50) on over-budget/force paths: `test_min_messages_falls_through_to_floor` (:365), `test_under_budget_but_force_overrides_to_floor` (:445, force=True), 639-message incident replica family (:565/:651/:707/:739), `test_required_path_engine_never_blocks_on_min_messages` (:987), regular-path-unaffected pin (:247, threshold=0.50)

## Check 3 — Collateral sweep (Lane-3 estimator callers): 13/14 modules PASS, 1 FINDING

### 3a. Compaction-cluster adjacent — 7/7 PASS, 255/255 tests (worker 6540ccb8, 07:42:43Z)
`test_compaction.py` 130 (single run 18.33s, no split needed) · `test_compaction_empty_guard_fallback.py` 9 (6a1c71ea4) · `test_compaction_model_config.py` 31 · `test_proactive_compaction_fix_p1.py` 42 · `test_compact_executor_revive_brick_e2e.py` 10 · `test_compact_executor_defect1_pause_resume_lifecycle.py` 3 · `test_compaction_multimodal.py` 30 (Lane-3 content-block surface). Zero failures/timeouts; tree clean before and after.

### 3b. Estimator-caller surfaces outside the cluster — 6/7 PASS (worker 39b9d720, 07:43:45Z)
`test_loader.py` 70 (estimator owner) ✓ · `test_prompt_cache.py` 11 ✓ · `test_snapshot_digest_injection.py` 15 ✓ · `test_snapshot_executor.py` 17 ✓ · `test_graph.py` 2 ✓ · `test_manager.py` 13 passed + 30 skipped (pre-existing SQLite-migration quarantine family per QUARANTINE.md 2026-08-26 — unrelated) ✓

**🟠 FINDING F1 — lane-caused stale test (base-side adjudication CONFIRMED lane-caused in Check-1 Phase B: PASSES on base `74b36b478` (`1 passed in 1.15s`), FAILS on HEAD):**
- `tests/unit/test_graph_retry_integration.py::TestReactiveCompaction::test_reactive_compaction_returns_none` — FAIL on HEAD (1 failed, 18 passed)
- `AssertionError: Expected 'compact_state' to have been called once. Called 2 times.` — second call carries `force=True`
- Captured logs show the lane's NEW punch-through: "first-pass engine returned None … re-invoking with force=True to punch through the dedup window" → "SECOND engine invocation (force=True) STILL returned None — cannot recover, re-raising"
- Worker diagnosis: stale test encoding the OLD single-invocation contract; the CLE re-raise contract itself still holds (`pytest.raises(ContextLengthExceededError)` passes; only the call-count/kwargs assertion fails). NOT an estimator regression. Test-contract update owed to the developer (or, if reactive-path punch-through was NOT intended, a code fix) — routes back through developer per commission.
- Coverage note: `-k 'warm or loader or cache'` selects zero tests in `test_manager.py` — the `_warm_loader_caches_sync` caller surface has no tests in that module (informational).
- Out of scope (env-gated): `tests/integration/test_compaction_e2e.py` — 3 tests marker-deselected; not force-run.

## Check 4 — Pre-existing-failures adjudication: ✅ IDENTICAL — same-11 CONFIRMED (worker 729eaaf4, 07:39–07:41Z)

- Method: HEAD-side runs in dev worktree vs temp worktree `git worktree add --detach /tmp/ens-sbx-hatch-base 74b36b478` (own uv venv, import verified inside temp worktree, base SHA confirmed), both sides timeout-wrapped, then mandatory `git worktree remove` (succeeded plain, exit 0; worktree list clean; `ls` → No such file or directory).
- **Failure sets byte-identical** — `diff` exit 0; SHA256 of both normalized lists: `c1e6683ad411484f0d6c6b8c851b17b6fc48eaaaafcd409f6a2614157e8432d4`
- `tests/services/test_process_message_metrics.py`: 10 failed / 6 passed (2.45s HEAD, 4.23s base) — all 10 in `TestRecordMetricsWiring`, root cause `AttributeError: 'types.SimpleNamespace' object has no attribute 'priority'` at `daemon/services/task_processor.py:552`
- `tests/services/test_instance_messaging_queue_routing.py`: 1 failed / 15 passed (4.54s both sides) — `test_router_forwards_queue_id_to_enqueue_message_job`, `TypeError: 'MagicMock' object can't be awaited` at `daemon/routers/messages.py:325`
- **Adjudication: the 11 failures pre-date the lane (present on base `74b36b478`) — NOT regressions from this work.** The reviewer's "coherent but unproven" claim is now officially proven.

## Check 5 — Mock-discipline audit: ✅ CLEAN, zero flags (worker db616405, 07:33:39Z, static read-only)

- Lane test files audited: `test_compaction_escape_hatches_round2.py` (new, 1620 lines), `test_proactive_compaction_fix_p1b.py`, `test_compaction.py`, `test_compaction_empty_guard_fallback.py`, `test_compaction_never_blocked.py` (last four: zero NEW mock lines added by the lane)
- Escalation pin genuinely end-to-end: **S1 REAL** (`set_proactive_escalation_metadata` real production writer, round2:1422–1428; durable landing verified via `set_metadata_many_calls` + real `is_proactive_escalation_active` read) · **S2 REAL** (live unpatched `daemon/graph.py:7499–7522` read block, consumed through normal getattr/to_thread path) · **S3 REAL** (`_maybe_precall_compact_95` imported from `daemon.graph`, round2:66/252; **zero `patch(` calls in the entire file**)
- C1 pin & W3 pin: zero mocks — real 12-message corpus + real `compact_state(force=True)`; W3 is an anti-stub real-source contract (`inspect.getsource` assertions incl. comment-stripping filter)
- Cross-lane seam-substitution sweep (`_maybe_precall_compact_95` / `set_proactive_escalation_metadata` / `is_proactive_escalation_active`): **zero hits**
- Substitutes present are all acceptable isolation (DB harness `_Lane4FakeRepo` with production-semantics merge, state harness, LLM summarizer stub, estimator token-knob lambda, MagicMock facade shells whose only consumed attr is the fake repo)
- Mutation-soundness cross-check (static): disabling the read block keeps gate at 0.95 → 170 < 0.95×200=190 → `_PRECALL_NOOP` → pin fails loudly at round2:1457–1470 with a diagnostic naming the `graph.py:7499-7522` anchor
- Cosmetic non-flag: round2 header docstring cites the BASE location `7461-7484`; class docstring/error message correctly cite `7499-7522`

## Scope & ensure.md note

- Lane diff = 11 files (6 daemon + 5 test), **no dev.sh / Makefile / scripts/ / concurrency-cluster touch** (verified via lane diff grep) → ensure.md Core concurrency pack + dev.sh static grep are OUT of this change set's blast radius; the commissioned module packs ARE the scoped packs (all green except F1 above). Release Gate N/A — worktree gate, pre-merge; `dev.sh` cannot boot in a worktree (mechanical guard by design).
- No PACKS.md-registered pack scripts were created — all runs were commission-scoped ad-hoc module packs, each command-level `timeout 300`, `-q --tb=short -p no:cacheprovider`, no `-x`.

## Workers

| Worker | Instance | Check |
|---|---|---|
| hatch-gate-env-setup | 924f386a | env + inventory |
| hatch-gate-mock-audit | db616405 | 5 |
| hatch-pin-round2 | 167e1f27 | 2a |
| hatch-pin-corpus | df36da1d | 2b |
| hatch-sweep-cluster | 6540ccb8 | 3a |
| hatch-sweep-callers | 39b9d720 | 3b |
| hatch-base-adjudicate | 729eaaf4 | 4 |
| hatch-mutation-verify | 84ff8c8c | 1 (+F1 base adjudication) |
