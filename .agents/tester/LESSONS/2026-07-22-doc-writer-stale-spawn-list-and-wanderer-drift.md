# LESSONS: doc-writer agent testing — quick fix + pre-existing wanderer drift

Date: 2026-07-22
Branch: feature/doc-writer-agent
Commits: 128a3f37 (quick fix), e0e6db13 (validation pack)

## 1. Quick Fix: Stale `expected_team` list in spawn iteration test

**File:** tests/test_spawn_team_members.py:159
**Commit:** 128a3f37

### Problem
`test_valid_spawn_leader_can_spawn_each_team_member` claims to test "Leader can spawn **every** agent in its team_members list" but its hardcoded `expected_team` list was stale — missing `wanderer`, `kb-writer`, and `doc-writer`. The test passed vacuously: it only iterated 9 of the 12 actual team members.

The gap was masked because a *separate* test (`test_leader_team_members_parsed`, line 408) uses set-equality and WAS updated to include doc-writer. But that test only checks the list contents — it doesn't exercise the spawn path for each member.

### Root Cause Pattern
When a new agent is added to leader.team_members, developers tend to update only the set-equality assertion (the "is doc-writer in the list?" check) but miss the iterative spawn test (the "can leader actually spawn doc-writer?" check). This is a **systematic gap**: the same 3 agents (wanderer, kb-writer, doc-writer) were all added to team_members over time without updating the iteration list.

### Fix
Added all 3 missing agents to the `expected_team` loop. 3-line insertion.

### Prevention
Consider replacing the hardcoded `expected_team` list with a dynamic read from `leader.team_members` so the iteration always covers the actual list:
```python
leader = get_registry().get("leader")
for agent_id in leader.team_members:
    # spawn each one
```
This would eliminate the stale-list class of bug entirely. (Not done in this quick fix — would change test semantics; flagged for future cleanup.)

---

## 2. Pre-existing wanderer agent drift (2 failures, NOT caused by doc-writer)

**File:** tests/unit/test_wanderer_agent.py (lines 177, 473)

### Failures
1. `test_tools_allow_has_all_declared_categories` — wanderer `tools.allow` missing `'knowledge'`
2. `test_soul_mentions_explore_experience` — wanderer `soul.md` missing word `"experience"`

### Verification
Worker 40cc645e checked out `b682d639~1` (parent of doc-writer commit) and re-ran — identical failures. The doc-writer commit did not touch `agents/wanderer/`. These are pre-existing config drift in the wanderer agent.

### Recommendation
Fix in a separate follow-up: update wanderer's `meta.json` tools.allow to include `'knowledge'` (or update the test if 'knowledge' was intentionally removed), and add "experience" to wanderer's soul.md (or update the test if the explore/experience pattern changed). Out of scope for doc-writer.

### Lesson for future test runs
When running peer-agent test packs during a new-agent feature, expect pre-existing failures in OTHER agents that haven't been maintained. Always verify via parent-commit checkout whether failures are NEW (caused by the feature) or PRE-EXISTING (drift). The worker's verification methodology (checkout parent, re-run, compare) was the right approach.
