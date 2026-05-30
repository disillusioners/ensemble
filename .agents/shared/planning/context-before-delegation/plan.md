# Plan: Enforce Context Gathering Before Delegation to External Systems

## Objective
Add a behavioral hint in always-injected prompt files so that agents who delegate to external systems (opencode) naturally gather context first. This makes the context-aware explorer auto-save feature actually useful.

## Scope Assessment
**SMALL** — Adding behavioral instructions to 1-2 prompt files. No code changes.

## Context
- **Project**: agents-ensemble
- **Working Directory**: `/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble`
- The context-aware explorer feature auto-saves `explore()` results to `{tempdir}/ensemble/context/{context-key}/`
- But agents that delegate to opencode never call `explore()` themselves — only the spawned opencode sessions do
- `knowledge.md` is RAG-gated (only injected when RAG enabled), so can't be the sole carrier of this hint
- Need: behavioral nudge in always-injected prompt files

## Analysis: Where to Put the Hint

### Prompt File Injection Summary

| File | Condition | Injected For |
|------|-----------|-------------|
| `project-experience.md` | File exists | ✅ ALL agents, always |
| `knowledge.md` | RAG enabled | ❌ Conditional |
| `innate-skills/opencode/skill.md` | Declared in `meta.json` | Agent-specific (coder, planner, reviewer, approver, tester, tidier) |
| `innate-skills/coordination/skill.md` | Declared in `meta.json` | Leader only |
| Agent `soul.md`, `workflow.md`, etc. | Per-agent | Agent-specific |

### The Problem

There are **two delegation paths** to external systems:

1. **Leader → spawns child agents** (coder, planner, etc.) → those agents use opencode
   - Leader itself does NOT have opencode skill
   - Leader needs to tell children to gather context, OR children need the nudge themselves

2. **Agents with opencode skill** (coder, planner, etc.) → delegate to opencode directly
   - These agents ARE the ones that should gather context before sending prompts to opencode

### Correct Placement

**Primary location: `innate-skills/opencode/skill.md`** — This is injected into every agent that uses opencode (coder, planner, reviewer, approver, tester, tidier). Adding a "Context Before Delegation" section here is:
- ✅ Targeted — only agents that actually delegate to opencode see it
- ✅ Proximity — the opencode skill is the exact place where delegation happens
- ✅ Always loaded for the right agents (not RAG-gated)

**No other location needed.** The leader doesn't directly use opencode — it delegates to agents that do. Those agents already have the opencode skill injected. Adding the hint to opencode/skill.md covers all delegation paths.

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Add "Context Before Delegation" section to opencode skill | Add behavioral rule about gathering context before sending prompts to external systems | `agents/_prompt_system/innate-skills/opencode/skill.md` |

## Implementation Details

### Task 1: Add Section to `opencode/skill.md`

**Location**: `agents/_prompt_system/innate-skills/opencode/skill.md`

**Add after the "## Prerequisites" section** (after line 8, before "## Usage") — this placement ensures it's read early, before the agent starts using the skill:

```markdown
## Context Before Delegation

> **Before sending any task to an external system, gather and share relevant context first.**

External agents (opencode sessions) start with zero knowledge of your session. They depend entirely on what you tell them. Before delegating:

1. **Gather context** — Use your available tools to understand the task (explore knowledge, read files, review prior results)
2. **Share context in your prompt** — Include relevant findings, constraints, and background in the message you send
3. **Check shared context directory** — If `shared_context_dir` is available (from your system prompt), reference it so the external system can read accumulated context

**Why:** External agents perform significantly better with context. A 30-second context gathering step before delegation saves minutes of back-and-forth later.
```

**Design rationale**:
- Behavioral, not tool-specific — says "gather context" not "call explore()"
- Works with or without RAG — the nudge is about the behavior, tools are secondary
- Positioned early in the skill doc — read before usage instructions
- Lightweight — 6 lines, doesn't bloat the prompt
- References `shared_context_dir` conditionally — only mentioned "if available"

## Key Files
- `agents/_prompt_system/innate-skills/opencode/skill.md` — The single file to modify

## Constraints
- No code changes — prompt-only
- Behavioral language, not tool-specific commands
- Must work without RAG (knowledge.md unavailable)
- Must not bloat prompt significantly

## Success Criteria
- [ ] `opencode/skill.md` has a "Context Before Delegation" section
- [ ] The hint is behavioral ("gather context"), not tool-specific ("use explore()")
- [ ] The hint appears early in the skill doc (before usage instructions)
- [ ] All 6 agents with opencode skill will see this nudge (coder, planner, reviewer, approver, tester, tidier)

## Tracking
- Created: 2026-05-31
- Status: draft
