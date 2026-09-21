# skill-capture-gates review arc (3 cycles, 2026-08-25)

Branch `feature/skill-capture-gates` @ 1a37338a — final verdict APPROVED-with-notes.
Arc: 7999f802 (REJECTED) → b48e5543 (REJECTED) → 1a37338a (APPROVED-with-notes).

## Lessons

1. **Fixture propagation is the failure mode of ctor-parameter fixes.**
   Adding an optional ctor param (here: `SkillEvolutionService(agent_id_resolver=...)`) and fixing
   ONE test fixture is not enough — sibling integration fixtures (`test_skill_cross_phase_flow_c.py`,
   `_flow_b.py`, `_e2e.py`) construct the same service. Cycle 2 failed only because the dev verified
   the one file they fixed, not the construction-site set. **Mandate: `grep -rn "SkillEvolutionService(" tests/ daemon/`
   and enumerate every construction site when a ctor signature gains a behavior-bearing optional param.**

2. **"Baseline was clean" must be scoped to what was actually run.**
   Worker's cycle-2 claim ("baseline at v0.11.1 passed 12/12") was true for flow_c ONLY; whole-suite
   baseline had a 20/19/21 pre-existing failure envelope. Acceptance gate for minimal-fix branches in
   a debt-laden suite: **no NEW failures vs baseline envelope + branch-specific reds green** — not
   suite-wide green. Verify baselines in a disposable worktree at the merge-base tag, suite-wide.

3. **Version-tag lookup duplication is a standing trap.**
   Any NEW code reading agent meta must go through the manager's W2 `_resolve_agent_meta` closure
   (instance_id → agent_tag → get_version → get_resolved), never a bare `get_version(agent_id, None)`
   (returns BASE meta → v2 agents' `skill_injection` defaults False → silent capture block).
   Mirrors the 2026-07-29 version-tag resolution fix pattern.

4. Follow-up ledger (open): no-resolver fallback in check_and_capture now unreachable from real
   callers — removal/prominence candidate; flow_b flakiness + OpenCode-bootstrap + skill_ab_tests
   StaleDataError + message_queue_e2e + flow_a `test_full_flow_feedback_only_after_metrics` are
   pre-existing suite debt (baseline envelope), tracked outside this branch.
