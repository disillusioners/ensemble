# Security Gap: instance tools lack team-membership checks

**Date:** 2026-07-02
**Discovered during:** Charter Agent Phase 1 verification
**Source:** Config verification session (ses_0dcf8da42ffeEHjICx323VDpIx)

## Issue
`send_message` and `terminate_instance` tools in `daemon/tools/instance.py` (lines ~668, ~822) have **NO team-membership or ownership authorization checks**.

This means any agent with the "instance" category access — granted by `innate_skills: ["chart"]` (via `INNATE_SKILL_TOOL_CATEGORIES` mapping) — can message/terminate ANY instance, not just instances it spawned.

## Impact
With the new chart feature, 6 agents (developer, tester, planner, reviewer, tidier, approver) now have "instance" category access. Any of them can:
- `send_message` to any instance (including cross-agent)
- `terminate_instance` on any instance (including those spawned by other agents)

## Scope
This is a **pre-existing design gap**, NOT introduced by the chart feature. The chart feature broadens the blast radius (more agents now have instance access).

## Recommendation
Track as a follow-up security hardening task. Consider adding ownership/team-membership checks to `send_message` and `terminate_instance` similar to the `_check_team_membership()` gate already enforced in `spawn_instance`.
