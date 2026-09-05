# Re-Gate Cycle 2: proactive-compaction-fix fix commit db0d788d

Date: 2026-09-04 · Mode: Standard Review, 3 workers × `code-review` · Verdict: **BLOCKED (W-4 residual only)**

## Per-warning closure
- **W-3 CLOSED** — pack c2 PASS (62/14), guard 12/12 (real pinnings, 33 assertions, seam mid_turn=False + fail_open + no-as_node asserted), multi-reuse 11/11; scope expansion (8 tests migrated + as_node-tracker + inverted negative control) adjudicated SOUND; 0 old-polarity stragglers.
- **W-1 CLOSED** — `_resolve_proactive_enabled` (config.py:2107-2175) + load_config wiring (:2306-2323); empty env via REAL load_config → True, no ValidationError; test now setenv("") (p1.py:987) + legacy spelling (:1035).
- **W-2 CLOSED** — env>yaml both spellings, empirically (yaml=True+env=0 → False; ENS beats CPE); explicit bool init kwarg verified as the load-bearing step; matrix tests added (p1.py:1054, :1084, :1108).
- **W-4 NOT-CLOSED + NEW-ISSUE** — (1) engine emits THREE skipped_* types; `_ENGINE_SKIPPED_TYPES_TO_NOOP_REASON` (compact_executor.py:228-230) maps only 2 → `skipped_preserved_within_threshold` (compaction.py:2136, emergency-bail) leaks RAW string to FE CompactedType AND engages seam (60s dedup) on user-facing /compact — both halves of cycle-1 W-4 re-opened on this path. (2) BE NoopReason.INJECTIONS_DOMINATE (command_dispatcher.py:182) has NO FE counterpart — FE NoopReason type (frontend/src/app/models/index.ts:47) lacks 'injections_dominate' → TS contract violated on the PRIMARY mapped path (runtime tolerant via default case). T4/T4-ext auto-path stamping still pinned (p1:684-750, p1b:409-732).

## Reusable review lessons
- **Missed-enumeration pattern:** the fix author wrote a future-engine-value guard (`test_unknown_engine_value_does_not_match_skipped_mapping`) while a PRESENT engine value was already unmapped. When verifying mapping fixes: enumerate the emitter's FULL current output set (grep the producer), assert `set(mapping.keys()) == set(emitters)` — don't trust the fix's own parametrization.
- **Wire-contract fixes have TWO enum surfaces:** compacted_type AND noop_reason both have FE types — a BE-only enum addition is drift even when runtime-tolerant. Check both on any NoopReason/CompactedType change.
- Migration realness check that worked: `git show <base>:<test-file>` side-by-side + assertion-count + tripwire-mechanics read (would the tracker actually fail on the old bug?).
- Non-gating 🟡 carried: legacy `_parse_proactive_enabled` field validator still raises ValidationError on empty env for DIRECT `CompactionConfig()` construction (no production caller; load_config path fixed). Divergent-behavior trap if direct construction ever appears.

## Unblock list (small, then spot-verify only)
1. compact_executor.py: add `"skipped_preserved_within_threshold"` → new NoopReason `PRESERVED_WITHIN_THRESHOLD` to the mapping.
2. command_dispatcher.py: add the enum value.
3. frontend models/index.ts:47: add `'injections_dominate'` (and `'preserved_within_threshold'`) to NoopReason type + switch cases in chat-interface.component.ts.
4. Tests: set-equality guard on mapping keys vs engine emitters; wire test for the third path; TestUserFacingNoopSkipsSeam for emergency-bail.
