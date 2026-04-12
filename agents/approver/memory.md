# Memory

## Evaluation Checklist — Plans

- [ ] All requirements from the original request are addressed
- [ ] Plan is internally consistent (no contradictions between sections)
- [ ] Dependencies between components are identified
- [ ] Assumptions are stated explicitly
- [ ] Error handling and edge cases are considered
- [ ] Rollback/recovery strategy exists for risky changes
- [ ] Scope is bounded — no scope creep within the plan

## Evaluation Checklist — Architecture Decisions

- [ ] Problem statement is clear and specific
- [ ] Solution addresses the stated problem directly
- [ ] Trade-offs are acknowledged, not hidden
- [ ] No obvious simpler alternative is missed
- [ ] Security implications are addressed
- [ ] Performance implications are considered
- [ ] Migration path exists from current state

## Common Approval Traps

1. **Halo effect** — A well-written plan feels correct even when it has gaps. Verify each claim independently.
2. **Missing negative cases** — Plans often describe what happens when things go right. Check what happens when things go wrong.
3. **Implicit assumptions** — Plans may assume context not stated. Flag anything that relies on unstated assumptions.
4. **Complexity hiding** — A complex plan may be necessary, but verify the complexity is justified, not accidental.
5. **Dependency blindness** — Plans may understate dependencies. Verify that stated dependencies are complete.

## Severity Guidelines (for REJECTED verdicts only)

- **Blocking**: Missing requirement, contradiction, infeasible approach, unaddressed safety issue
- Everything else is a note, not a rejection reason

## Project-Specific Standards

Check `.agents/approver/memory.md` before each approval for project-specific criteria.
