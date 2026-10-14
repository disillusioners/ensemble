# Deep-Review: proactive-compaction-fix (673270ec..bbed9782)

Date: 2026-09-04 · Mode: 🔴 Deep-Review (governor 661cc26e, 2/2 councilors × `code-review`) · Verdict: **APPROVED-WITH-NOTES** (unanimous)

## Load-bearing mechanism finding (reusable)
Doc A.5's mid-superstep durability premise is **falsified on real langgraph 1.0.9** (T2-ext canary `test_compact_executor_revive_brick_e2e.py:1712-1979`): mid-flight `aupdate_state` (any `as_node`) is **superseded by the running task's own commit**; no-`as_node` mid-superstep raises `InvalidUpdateError`. Proven replacement: **return-carried persist** — sentinel-first outgoing prefix in the node's return (`graph.py:4151-4203`); task commit lands the compaction atomically; dedup stamp rides the return; seam shadow persist (`graph.py:2923-2975`) is rebuild-seed only. **This is the template for the pre-existing CLE supersession follow-up** (CLE handler still uses superseded mid-flight persist, `graph.py:3951-3955`, locked byte-unchanged in this branch).

## Warnings (merge conditions, in order)
1. **W-3** — `tests/services/test_instance_messaging_compaction_guard.py` 6 RED (pin OLD inverted polarity; file is in ACTIVE pack `test/packs/c2_messaging_lifecycle_unit_test.sh` → pack FAIL). Migrate or quarantine with attribution. **CI blocker first.**
2. **W-1** — empty `ENSEMBLE_PROACTIVE_COMPACTION=` → `ValidationError` → daemon boot crash (`config.py:801-824`); covering test dodges via `delenv` not `setenv("")` (`test_proactive_compaction_fix_p1.py:987-1006`). Normalize empty→default.
3. **W-2** — yaml init-kwarg beats env for the kill-switch (yaml `proactive_enabled=True` silently defeats env `=0`) — **pydantic-settings init>env inversion recurrence**; mirror `_resolve_compaction_model` (`config.py:2040-2070`) with explicit `_resolve_*`. Weakens the incident-revert path (cf. D2.5-FLIP soak convention).
4. **W-4** — `/compact` wire change in 5%-floor↔min-messages/all-injected bands: `success` + raw `compacted_type="skipped_*"` outside FE `CompactedType` enum + no-op stamps 60s dedup (`compaction.py:1994-2020` vs `compact_executor.py:1433-1435`). Map to existing `NoopReason` vocabulary.

## Pattern recurrences for future reviews
- **Pydantic-settings init-kwarg>env inversion: 3rd occurrence** (compaction.model → proactive_enabled). Any new kill-switch/flag touching `CompactionConfig` needs an explicit `_resolve_*` in `load_config` + empty-string normalization + a test that uses `setenv("")` not `delenv`.
- Dev internal review PASS-WITH-NOTES was too generous — missed all 4 warnings. Adjacent-suite sweep beyond the named test files (esp. files inside active packs) is mandatory for behavior-flip fixes; `git grep` the OLD behavior's test pins.
- Dev's falsification-by-experiment (real-langgraph canary) is the gold standard for doc-premise deviations — demand the canary, not the claim.

## Priority verdicts
P1 return-carried persist CONFIRMED (unanimous) · P2 injected non-selectable CONFIRMED (no livelock; dedup-bound) · P3 is_retry CONFIRMED (real-graph proof, not mock) · P4 gates CONFIRMED + W-4 divergence · P5 flag DIVERGENT (W-1/W-2) · P6 refire CONFIRMED (bounded pre-filter miss window, doc-A.4-tolerable) · P7 -O CONFIRMED (36/28/13 + canary 3/3 green under -O).
