# Tracking: agent-instance-tools

## Final — APPROVED (2026-08-26)

- **Iteration 001** — APPROVED
- Worker: approve-worker-plan (065a77d5-3699-4f1b-b7a2-5ca73087acf7), skill: plan-approval
- Scope: whole plan dir (.agents/shared/planning/agent-instance-tools/) — plan-overview.md, phase1-plan.md, phase2-plan.md, decisions.md, architecture-recommendation.md; verified against worktree @ 6ca9541c
- Blocking issues: none
- Notes (non-blocking, from worker):
  - Line drift: create_instance_tools at daemon/tools/instance.py:943; closure list at 1880-1903 (plan says ~2240-2250) — plan itself flags "verify drift at impl"; implementer checklist catches it
  - _INJECTION_ELIGIBLE_STATUSES is a frozenset (routers/messages.py:39-42), plan calls it "named constant" — functionally identical
  - manager.graph.aget_state claim correct at cited call site, but aget_state used elsewhere on graph objects — implementer grep may find hits
  - Exception contract drift: _resolve_instance_id raises ValueError (instance.py:1660-1663), plan assumes KeyError — implementer must confirm and adjust routing helper's except clause
  - get_messages returns list[dict] (manager.py:9328-9334), not list[BaseMessage] — D12 filter still works on dict keys
  - Suggested cheap insurance: python -c import smoke of INJECTION_ELIGIBLE_STATUSES from daemon.constants before locking test k
- Positive: architect's 10 corrections all propagated; W3/W4 race tests exemplary; R-O1/R-O2 composability invariant enforced cross-test; JAFP compliance grep-mandated; deliberate R-O1 asymmetry documented; concrete rollbacks + feature flag
