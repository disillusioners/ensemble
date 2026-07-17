# Tracking: General Hallucination Loop Breaker

## Iteration 001 — 2026-07-17 19:13
**Status**: APPROVED

### Evaluation Summary
- Scope: LARGE (3 phases, 5 modules, new detection + repair engine)
- Method: Direct code verification (13 code locations checked) + council session (5 feasibility questions)

### Code Claims Verified (13/13 accurate)
1. ToolThrottleSlot pattern (graph.py:112-146) ✓
2. GII_DELAY_MAP constants (graph.py:35-41) ✓
3. _gii_throttle declaration (manager.py:731) ✓
4. bump_gii_throttle accessors (manager.py:2028-2045) ✓
5. Reactive compaction pattern (graph.py:899-958) ✓
6. _cleanup_instance_state (manager.py:2084) ✓
7. terminate_instance cleanup (instance_lifecycle.py:1419) ✓
8. hard_delete zombie sweep (instance_lifecycle.py:1843) ✓
9. cancel_graph_task cleanup (manager.py:4541) ✓
10. pause_instance_cascade cleanup (instance_lifecycle.py:1992) ✓
11. create_agent_node signature (graph.py:690-702) ✓
12. build_instance_graph signature (graph.py:1276) ✓
13. Both build sites sync (instance_lifecycle.py:1165, 2472) ✓

### Council Findings (5 feasibility questions)
| # | Question | Council | Final |
|---|----------|---------|-------|
| 1 | Walk-backwards detection | PASS (caveat: reverse tool_call_id) | PASS — plan addresses at phase1:162,175 |
| 2 | aupdate_state in agent_node | PASS | PASS — mirrors proven reactive compaction |
| 3 | Mixed RemoveMessage+SystemMessage | PASS | PASS — identical to compaction output |
| 4 | Concurrency on _loop_breaker_state | PASS (caveat: 5 cleanup paths) | PASS — phase3 documents all 5 |
| 5 | full_messages rebuild | FAIL (injected_msg loss) | **FALSE ALARM** — plan handles in phase2 Step 6, phase3 wiring, test #9 |

### Notes (non-blocking)
- Q5 council false alarm: council evaluated simplified prompt, not actual plan files. Plan correctly handles injected_msg re-append (C3 pattern) in RepairContext.injected_msg + Step 6 + Phase 3 wiring.
- Detection scan breaks correctly on non-tool messages (ghost promise AIMessage, nudge HumanMessage) — consistent with _has_recent_tool_result at graph.py:519-534.
- All 12 architecture decisions (D1-D12) are sound, well-justified with trade-offs documented.
