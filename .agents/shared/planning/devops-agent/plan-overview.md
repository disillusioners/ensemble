# Plan Overview: Add DevOps Agent (Revised v2)

> **Revision:** Addresses 3 critical blockers (C1-C3), 5 warnings (W1-W5), and 3 suggestions (S2-S4) from reviewer feedback.

## Objective
Create a new `devops` agent type that works directly with bash (not via OpenCode) for infrastructure, deployment, CI/CD, Docker, shell scripting, and environment management. Deeply integrate it into ALL leader workflows (Implementation, Debug) so the agent actually receives work — not just a table entry.

## Scope Assessment
**BIG** — Creates 6 new files in `agents/devops/` + modifies 3 leader files with surgical edits to 3 separate workflow sections.

## Key Architecture Decisions

### D1: DevOps = Bash-Direct (giter pattern), NOT OpenCode-delegating (coder pattern)
The devops agent executes shell commands directly via `bash` tool. No `innate_skills: ["opencode"]`.

### D2: Routing is Embedded in Leader's Core Flow (C1 fix)
The leader's Implementation Workflow step 1 is rewritten from a hardcoded "Delegate to Coder" into a **domain classification routing decision** that appears as the FIRST instruction. Same for the decision tree in rule.md. A reference table alone would never override the hardcoded step — the routing must live in the instruction itself.

### D3: DevOps is in Debug Investigation (C2 fix)
Infrastructure bugs (pod crash-loops, CI failures, terraform drift) go to devops for investigation, not coder. A **domain classification step** is added before Phase 2 of Debug.

### D4: Self-Routing Risk Approval (C3 fix)
DevOps rule.md defines a **self-approval protocol** for TrueAuto mode: a Critical operation may be self-approved ONLY if 3 conditions hold. Otherwise STOP and report to leader. This resolves the TrueAuto "no questions" vs "explicit approval" deadlock.

## Context
- Project: agents-ensemble
- Working Directory: /Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble
- SKIP_DIRS in `daemon/registry.py:18-23`: `_trash`, `_baby_template`, `_prompt_system`, `_inner_soul` — **"devops" is NOT in SKIP_DIRS** ✅
- Auto-discovery: `AgentRegistry.discover()` scans `agents/` — just create the directory
- Prompt loading (`daemon/loader.py:296-326`): loads `soul.md`, `workflow.md`, `rule.md`, `memory.md`, `tools_note.md`, `user.md` — all are optional, skipped if missing
- AgentMetadata (`daemon/registry.py:58-98`): supports `capabilities: list[str]` field with `extra="ignore"`

## Phase Index

| Phase | Name | Objective | Dependencies | Coupling | Est. Time |
|-------|------|-----------|-------------|----------|-----------|
| 1 | Create DevOps Agent | Create `agents/devops/` with 6 files (meta.json, soul.md, workflow.md, rule.md, tools_note.md, user.md) | None | — | 2.5h |
| 2 | Integrate into Leader | Update leader soul.md (team table), workflow.md (routing in Implementation + Debug), rule.md (decision tree + delegation) | Phase 1 | loose | 1.5h |

### Coupling Assessment

| Phase Pair | Coupling | Rationale |
|------------|----------|-----------|
| Phase 1 → Phase 2 | **loose** | Phase 2 only references agent ID "devops" in text. Auto-discovery handles registration. Sequential recommended for clean testing. |

## Reviewer Feedback Mapping

| ID | Type | Addressed In | Phase |
|----|------|-------------|-------|
| C1 | Critical: Leader routing hardcoded | Phase 2 Task 2 (Implementation step 1 rewrite) + Phase 2 Task 3 (decision tree) | 2 |
| C2 | Critical: Debug has no DevOps path | Phase 2 Task 4 (Debug workflow domain classification + investigator) | 2 |
| C3 | Critical: TrueAuto vs explicit approval | Phase 1 Task 4 (rule.md self-approval protocol) | 1 |
| W1 | Warning: Missing tools_note.md + user.md | Phase 1 Tasks 5-6 | 1 |
| W2 | Warning: Multi-domain routing | Phase 2 Task 2 (routing table multi-domain row) + Phase 1 Task 3 (workflow.md multi-domain) | 1+2 |
| W3 | Warning: Giter ↔ DevOps boundary | Phase 1 Task 2 (soul.md "What I Do NOT Do" expansion) | 1 |
| W4 | Warning: Secrets handling | Phase 1 Task 4 (rule.md Secrets subsection, 7 points) | 1 |
| W5 | Warning: 4-tier risk vocabulary | Phase 1 Tasks 3-4 (workflow.md + rule.md aligned to Low/Medium/High/Critical) | 1 |
| S2 | Suggestion: capabilities in meta.json | Phase 1 Task 1 | 1 |
| S3 | Suggestion: Verify not in SKIP_DIRS | Verified: "devops" NOT in SKIP_DIRS ✅ | — |
| S4 | Suggestion: Environment targeting rule | Phase 1 Task 4 (rule.md env targeting) | 1 |

## Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Leader LLM ignores routing and defaults to coder | high | Routing is step 1 in the workflow flow (not a reference table). Decision tree in rule.md also updated. Two reinforcement points. |
| DevOps runs destructive commands in TrueAuto | high | Self-approval protocol with 3 mandatory conditions (C3 fix). If any fails → STOP. |
| DevOps leaks secrets in command output | high | 7-point secrets subsection (W4 fix). All workflow.md examples use `$VARS` not literals. |
| Scope creep — devops starts writing app code | low | soul.md "What I Do NOT Do" explicitly excludes app code, with giter boundary clarified (W3 fix) |
| Giter/devops overlap confusion | medium | Clear boundary rule: who orchestrates matters, not which tool appears inside the command (W3 fix) |

## Success Criteria
- [ ] `agents/devops/` has 6 files: meta.json, soul.md, workflow.md, rule.md, tools_note.md, user.md
- [ ] meta.json: id="devops", NO opencode innate_skills, capabilities field, 8 tools
- [ ] rule.md: TrueAuto self-approval protocol with 3 conditions (C3)
- [ ] rule.md: 4-tier risk vocabulary Low/Medium/High/Critical (W5)
- [ ] rule.md: Secrets subsection with 7 points (W4)
- [ ] rule.md: Environment targeting rule — defaults LOCAL/STAGING, prod requires SemiAuto (S4)
- [ ] workflow.md: Multi-domain routing guidance (W2)
- [ ] soul.md: Giter ↔ DevOps boundary rule (W3)
- [ ] tools_note.md: Safety-wrapped patterns for docker/kubectl/terraform/aws (W1)
- [ ] user.md: Interaction guidance (W1)
- [ ] Leader soul.md: Team table includes devops as 8th member
- [ ] Leader workflow.md: Implementation step 1 is a ROUTING DECISION (C1)
- [ ] Leader workflow.md: Debug Phase 2 has domain classification + devops investigator (C2)
- [ ] Leader rule.md: Decision tree includes devops + giter as delegation targets (C1)
- [ ] No daemon code changes needed

## Tracking
- Created: 2026-06-15
- Last Updated: 2026-06-15 (v2 — reviewer revision)
- Status: draft (v2)
