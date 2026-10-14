# Charter Agent + Mermaid Chart Support — Phase 1 Review

**Date:** 2026-07-02
**Commit:** 9fa6303d (feature/charter-mermaid-support)
**Verdict:** APPROVED with 1 security warning (should-fix) + suggestions

## Key Insight: INNATE_SKILL_TOOL_CATEGORIES Over-Grant

The `"chart": ["instance"]` mapping grants the ENTIRE "instance" category (5 tools:
spawn_instance, send_message, terminate_instance, list_instances, get_instance_info)
to every chart-enabled agent. This is architecturally coarse — the chart skill only
needs spawn_instance + send_message, but the category granularity grants all 5.

Pre-existing gaps compound this:
- send_message has NO team-membership check (only validates instance exists)
- terminate_instance has NO authorization guard
- list_instances/get_instance_info expose system-wide instance metadata

This is a SHOULD-FIX for a future hardening pass, not a Phase 1 blocker. The
functionality works correctly; the concern is the blast radius if an agent misbehaves.

## Charter Agent Quality
- soul.md/workflow.md/rule.md are excellent, well-designed
- mktemp per-instance temp files correctly prevent race conditions
- mmdc validation workflow with 3-retry + graceful degradation is sound
- Cleanup discipline (rm -f) is enforced in rules

## Tests
- 40 tests pass (test_spawn_team_members + test_innate_skills_refactoring)
- 1 pre-existing failure in test_tool_filter (unrelated to this change)
- Test assertions correctly updated for new team_members/innate_skills values
