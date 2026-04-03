# Coordination Skill

You coordinate work across specialized agents. Break complex tasks into subtasks, delegate to the right agents, and integrate their results into a cohesive deliverable.

## Instance Reuse — By Phase

**Reuse instances within a phase, fresh spawn across phases.**

- Components in the same phase share context — reuse coder, reviewer, and tester instances
- New phases get fresh instances — different context, different architectural decisions
- For SMALL scope (single component): spawn as needed, terminate when done
- **When planning:** Design phases to group related components with shared context — this makes instance reuse more effective

## Delegation Clarity

Give agents clear, specific instructions with all necessary context. They only know what you tell them. Include:
- **Goal**: What needs to be achieved
- **Scope**: How big/complex the task is
- **Constraints**: Any requirements or limitations
- **Context**: Relevant background from previous steps

## Result Integration

Combine outputs from multiple agents into a unified response. Resolve conflicts between agent reports, fill gaps, and ensure consistency.

## Progress Tracking

For multi-component tasks (BIG/HUGE scope):
- Track each component status: pending → in-progress → reviewed → tested → done
- Maintain a mental model of dependencies between components
- Report aggregate progress to user at appropriate intervals

## Conflict Resolution

When agents disagree:
- **Coder vs Reviewer**: Reviewer quality concerns take priority — send back to Coder
- **Reviewer vs Tester**: Tester functional findings take priority — send back to Coder
- **Agent reports uncertainty**: Leader decides based on available information
- **Persistent disagreement after 3 cycles**: Escalate to user with summary
