# CorrelationManager Migration Plan Review

**Date**: 2026-06-16
**Verdict**: 🟢 REVISED — All 5 blocking issues + 1 warning fixed
**Reviewer**: Reviewer agent (council deep-review mode)
**Revision**: 2026-06-16 — Planner applied all fixes

## Summary

The plan was architecturally sound in its high-level design (shadow mode → progressive cutover → counter removal). After review identified 5 blocking issues + 1 warning, all have been addressed:

## Fixes Applied

| Issue | Fix | Phase(s) Updated | ADR |
|-------|-----|-------------------|-----|
| C1: `waiting_for` tracks message responses, not child existence | Redesigned CM to track `(parent, child, message_id)` triples; hooks at `send_message` not `spawn_instance`; rebuild queries `waiting_for > 0` + message_queue | Phase 1, Phase 4 | ADR-001 revised |
| C2: EventBus `create_event()` persists to DB | Outbound correlation events use direct callback, not EventBus; inbound still uses EventBus subscribe_all | Phase 1, Phase 2 | ADR-008 new |
| C3: `put_nowait` silently drops events | Same as C2 — direct callback eliminates queue overflow risk | Phase 1, Phase 2 | ADR-008 new |
| C4: Cascade callers not in EventBus loop | Per-parent `asyncio.Lock` serializes all register/resolve calls | Phase 1, Phase 3 | ADR-009 new |
| C5: Race #3 not eliminated (count_pending TOCTOU) | Pure in-memory set operations replace `SELECT COUNT(*)`; no DB query in completion path | Phase 3 | ADR-010 new |
| C6: Infinite re-enqueue on lease contention | Bounded retry: 3 attempts, exponential backoff [0.5s, 1s, 2s], fallback to enqueue | Phase 0 | — |
| Scope: 97 `waiting_for` references (not 6) | Updated Phase 4 task table to audit all 97 references across 15 files | Phase 4 | — |

## Per-Phase Quality (Post-Revision)

| Phase | Quality | Can Proceed? |
|-------|---------|-------------|
| 0 | ✅ Good (revised — added retry limit C6) | Yes |
| 1 | ✅ Fixed (C1/C2/C3/C4 addressed) | Yes |
| 2 | ✅ Fixed (C2/C3 addressed — direct callback) | Yes (after Phase 1) |
| 3 | ✅ Fixed (C4/C5 addressed — Lock + pure set ops) | Yes (after Phase 2) |
| 4 | ✅ Fixed (97 references audited) | Yes (after Phase 3) |
| 5 | ✅ Good | Yes (optional, P3 dep) |
