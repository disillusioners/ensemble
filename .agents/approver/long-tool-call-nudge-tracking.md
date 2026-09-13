# Tracking: Long Tool Call Detection → Parent Nudge (long-tool-call-nudge)

Branch: plan/long-tool-call-nudge @ 3b64262e (6-file package, ~1,733 lines)

## Iteration 001 — 2026-09-13 — VERDICT: REJECTED

Workers: 2 section-parallel, both `plan-approval`, both pinned tip 3b64262e (no drift start/end).
- ea7a8ca0 approve-worker-design (plan-overview.md + decisions.md + architecture-recommendation.md): APPROVED, 0 blocking, 10 notes. Anchors spot-verified (graph.py:7879, instance_messaging.py:1896-1902/:2078, repository.py:1931, constants.py:36).
- dbdae18f approve-worker-phases (phase1/2/3-plan.md): REJECTED, 1 blocking.

### Blocking (carried into verdict)
1. Episode-close gate contradiction: phase1-plan.md Task 3 step (4) + phase2-plan.md Task 5 specify `close_episode` UNCONDITIONALLY on tool_end; decisions.md AD-9 (canonical) mandates close on HEALTHY completion only. As written, bd4b36ef replay (5 sequential long calls) emits 5 nudges — violates SC2 ("5 sequential long calls = 1 nudge"). Fix: gate close on `duration_seconds < effective_threshold` in Task 3 step (4); align Phase 2 Task 5 "Definition of episode close"; long completions stay open until healthy completion or AD-37 TTL belt.

### Key non-blocking notes (deduped)
- _Stamp/record_start parent_id (Task 2) not aligned with AD-42 Option (ii) — merged A-N6 + B-N1; two valid resolution paths, pick one.
- Wrapper→close_episode access path unspecified (B-N2): scanner ref absent from _wrapped_tools_node.
- phase3 D9 stale open-decision, resolved by AM-3/AD-35 — restate or remove (B-N3).
- phase1 Task 7 lifespan example ambiguous vs "reuse watchdog repo" instruction (B-N4).
- TASK_TIMEOUT_S 300 vs 7200 divergence — carried as P-1 pin, separate investigation (A-N2).
- Kill-switch OFF ≠ zero overhead: per-completion log + stamp registry tick by design (A-N3).

Next: iteration 002 upon re-submission with the AD-9 close-gate pinned in both phase docs.

## Iteration 002 — 2026-09-13 — VERDICT: APPROVED

Re-submission at new tip ce17a36a (package re-verified: 6 files, ~1,753 lines). Workers: 2 section-parallel, both plan-approval, cold prompts (no iteration-001 history), both pinned ce17a36a with tree state unchanged start/end.

- 412cd41d approve-worker-design (plan-overview + decisions + architecture-recommendation): APPROVED, 0 blocking, 7 notes.
- 6c541a52 approve-worker-phases (phase1/2/3-plan.md, cross-ref design core): APPROVED, 0 blocking, 7 notes.

### Iteration-001 blocking issue — resolution verified fresh
Episode-close gate (iter-001): close_episode was unconditional vs AD-9 healthy-completion gate. Iter-002: both workers independently confirm AD-42 Option (ii) wrapper-side close with cached parent_id pinned consistently across Phase 1 Task 3 + Phase 2 Task 5 + architecture-recommendation §3.5; layered close = AD-9 healthy close + AD-37 TTL belt (7200s) + orphan-episode hygiene; regression pin U2b (bd4b36ef 5-distinct-bash-calls → exactly 1 nudge) present. Resolved.

### Deduped notes (all non-blocking; recovery during implementer pass)
1. AD-40 terminal-parent skip visibility: overview diagram shows PAUSED-only skip (A-N1); phase2 Task 3(a) body lacks the terminal-parent bullet — covered only by U5t + AD-40 (B-N4). Add one-liners at both loci.
2. Naming/contract hygiene: LongToolCallScanner→LongToolNudgeScanner typo (B-N2, phase2 Coupling); Task 1 title wording (B-N7); EpisodeCtx vs LongToolNudgeEpisodeCtx field-set variance — canonical name per Working-Names Table; keep 7 fields or amend AD-7 for scanner-internal timestamps (B-N1).
3. Stale phase3 risk rows 4-5 → mark RESOLVED (AD-32/AM-1; AD-36/AM-12); Context Anchor 12 stale vs Task 6 correct conditional (B-N3/N5/N6).
4. Graph-task-cap source (TASK_TIMEOUT_S 300 vs 7200) unreconciled — decoupled via STALE_STAMP_TTL_SECONDS=7200 module constant (P-1 implementer pin) (A-N4 + B-unverified).
5. Before coding: confirm AM-1..AM-12 fully applied to phase plans (A-N2; B verified AM-9/AM-11 in — AM-10 line-number corrections worth a sweep).
6. Awareness notes: exposure category-wide ~15 agents, accepted by architect (A-N3); restart required for code AND meta.json changes (A-N6); notice text locked 5-section, no bash-timeout env var reference (A-N5); KNOWN_TOOL_NAMES = boot-validation completeness not exposure (AM-2; B verified).

Final state: APPROVED at iteration 002. Tracking file preserved as historical record.
