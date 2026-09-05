# LESSONS — Proactive-Compaction-Fix Test Gate (2026-09-04)

Gate: `feature/proactive-compaction-fix` — branch range 673270ec..71bb09ab (+ gate-owned test commits 6ba33f90 FE specs, 36d30c45 acceptance test). 24 workers. Verdict: SHIP (0 branch-caused regressions).

## 1. Latency-regression allegations under parallel gate load: verify the mechanism BEFORE believing the number

The P3 partition flagged `test_ack_returns_and_waiting_emits_while_pause_blocked` as a prima facie NEW blocker (ack 533ms vs 500ms pin; partition clean at prior gate; branch visibly added `asyncio.to_thread` to `instance_messaging.py`).

Refutation protocol (cheap, decisive):
1. Locate the added await/to_thread in the diff and trace WHICH path it's on. Here: `_maybe_compact_context` bg-task body (instance_messaging.py:1161) — the ack returns before the task runs (command_dispatcher.py:1115 `record_start → create_task → return`). Mechanism impossible by construction.
2. Solo 3× at HEAD + solo 3× at base (worktree, uv sync inside). Result: 174ms vs 175ms mean — 65% margin on both sides, zero branch delta.

Lesson: a single latency overshoot measured while ~17 pytest partitions run concurrently is load noise until proven otherwise. Partition-context timing assertions need either generous margins or dedicated solo adjudication before they can block a gate.

## 2. plane_sync joined the context-flake family

`test_plane_sync.py::TestEdgeCaseConcurrentSync::test_service_concurrent_calls_dont_crash` (`assert 1 == 2`, threading + asyncio.run + StaticPool) failed once under xdist partition, 3/3 PASS solo at HEAD AND base. Same shape as the TestDequeueAtomicClaim rows. QUARANTINE.md row added (family: context-flake, sweep-visible). Rising family count is a quality signal to watch.

## 3. §4 audit follow-ups (test-quality, non-blocking) — candidates for the branch's own follow-up commit

- `tests/unit/services/test_proactive_compaction_fix_p2.py:390` — `assert args[1] is ... or True` is vacuous (always green). Delete the `or True`.
- `test_proactive_compaction_fix_p1.py:768-807` — hard-coded `/Users/nguyenminhkha/...` REPO_ROOT fallback; portable idiom exists (`Path(__file__).resolve().parents[2]`, cf. test_message_metadata_hook_placement.py:64).
- Coverage gap: production `_is_terminal_checkpoint` treats `next=None` as quiescent (gate proceeds) but no new test pins that edge (only `state=None` and tuple shapes are pinned).

## 4. Cross-lineage ledger comparison is safe on counts-identity, noisy on collected totals

Prior-gate partition baselines came from a DIFFERENT branch lineage (13782089, queue-status-missions-badge). P10 showed Δcollected = −20 with P/F/S identical → lineage collection drift, benign. Decision rule that held: adjudicate on failure-set identity (node-for-node + signature), treat collected-total deltas as data unless unexplained by known adds. P2's +99 was exactly reconcilable (83 new p1/p1b/p2 + 14 param growth + 2 gate-owned acceptance tests).

## 5. "AST gate (11)" is a DISTRIBUTED set, not a file

11 AST/source-pinning tests live in three files: p1 TestT3ASTPersistIdentityPin (4), p2 TestP2GuardRemovalAST (4), test_compact_executor.py (3: test_two_import_sites, test_guard_ordering_source_level_supplementary, test_engine_skipped_mapping_is_complete_against_engine_emitters). Future gates: don't hunt for a nonexistent `*ast_gate*` file.

## 6. Kill-switch verification recipe that worked (reuse for future flags)

Fresh-subprocess matrix (env -i + controlled env + `load_config(config_path=tmp_yaml)`) resolved through the REAL loader: unset/=1/=0/empty-string + yaml-on-env-0 + yaml-off-env-1. Empty-string row doubles as the boot-crash check. CLE-armed proof = run the CLE-path unit subset with the flag forced off (5/5 PASS here). Resolver: config.py:2147 `_resolve_proactive_enabled`; gates: instance_messaging.py:1221 (proactive) + graph.py:2836 (95% hook); CLE handler graph.py:3900-3994 ungated by design.
