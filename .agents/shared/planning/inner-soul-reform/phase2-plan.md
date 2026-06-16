# Phase 2: Agent Prompt Decision Table

## Objective
Add a "Record/Remember Decision Table" to agent prompts so all agents (especially leader) have clear guidance on which tool to use for recording: `inner_soul` for persona/behavioral, `project_history_add()` for project events, `experience()` for project knowledge.

## Coupling
- **Depends on**: None (can run in parallel with Phase 1)
- **Coupling type**: independent
- **Shared files with other phases**: None — Phase 1 touches `daemon/tools/inner_soul.py`; this phase touches `agents/` markdown files
- **Shared APIs/interfaces**: None
- **Why this coupling**: Prompt text is completely independent from backend code changes

## Context
- Previous phase: None required
- Key findings from exploration:
  - **Zero** guidance about `inner_soul` exists in any leader file
  - `agents/leader/rule.md:127-128` has one sentence about `project_history_add()`
  - Shared `agents/_prompt_system/knowledge.md` documents `explore()` and `experience()` but NOT `inner_soul`
  - Two variants: `knowledge.md` (strict) and `knowledge_no_force_explore.md` (for leader/orchestrator)
  - Prompt composition: soul → rule → skills → tools → tools_note → workflow → memory → recent memories → knowledge → project experience

## Tasks

### Task 1: Add Decision Table to Leader Rule
**File**: `agents/leader/rule.md`

**Current state** (lines 127-128):
```markdown
### Project History
After completing a meaningful task (feature, fix, architectural change), consider recording it with `project_history_add()` so future sessions have context. Use your judgment — not every task needs recording, but significant outcomes should not vanish.
```

**Replace with** (expand into a decision table):
```markdown
### Recording — Decision Table

**Which tool to use when you want to "remember" or "record" something.**

| Content Type | Tool | Example |
|--------------|------|---------|
| **Project event** — feature shipped, bug fixed, deployment done | `project_history_add()` | "Added database tools category with connection management" |
| **Project knowledge** — architecture, patterns, gotchas, how systems connect | `experience()` | "The job queue uses a 7-state lifecycle with lock-first pattern" |
| **Persona/behavioral change** — how YOU should act | `inner_soul(intent="change", target="soul")` | "Be more concise in responses" |
| **User preference** — how the USER likes things | `inner_soul(intent="remember", target="user")` | "User prefers TypeScript over JavaScript" |
| **Self-reflection** — what YOU learned about your own behavior | `inner_soul(intent="learn", target="soul")` | "I rush too much on SMALL tasks, should trust agents more" |

**Rule**: inner_soul is INTENSELY PERSONAL — it's about YOU and the USER as personas. NEVER use it for project state, task progress, code, git operations, deployments, or anything about the project itself. If in doubt, use `project_history_add()` for events or `experience()` for knowledge.

**inner_soul WILL REJECT project content** and tell you which tool to use instead.
```

### Task 2: Add Decision Table to Shared Knowledge Prompt
**File**: `agents/_prompt_system/knowledge.md` (strict version, 101 lines)
**File**: `agents/_prompt_system/knowledge_no_force_explore.md` (leader version)

Add a new section **after** the "Knowledge Update Duty" section (after line 57 in strict version) and before "Guidelines":

```markdown
---

## Memory Tools: When to Use What

You have several tools for recording information. Using the wrong one means future sessions can't find it.

| What you want to record | Tool | Scope |
|-------------------------|------|-------|
| Project events (features, fixes, deployments) | `project_history_add()` | This project's timeline |
| Project knowledge (architecture, patterns, gotchas) | `experience()` | Cross-project knowledge base |
| Your persona or behavioral changes | `inner_soul()` | Your own agent files |
| User preferences | `inner_soul()` | Your own agent files |

**`inner_soul()` is for SELF-REFLECTION only** — your persona, your behavioral patterns, and user interaction preferences. It will **REJECT** project-related content (code, configs, git operations, task progress, deployments) and tell you to use `project_history_add()` or `experience()` instead.
```

**⚠️ Apply to BOTH variants** — the strict `knowledge.md` and the `knowledge_no_force_explore.md`. The text is identical in both files.

### Task 3: Update Leader tools_note.md (optional but recommended)
**File**: `agents/leader/tools_note.md`

Currently line 33-34 labels `.agents/leader/memories/*.md` as "Project knowledge (timestamped files)". This is misleading — it implies the leader should write project knowledge there.

**Fix**: Change the label to clarify it's for agent-personal memories, not project knowledge:

```markdown
# Current (line 33-34):
| `.agents/leader/memories/*.md` | Project knowledge (timestamped files) |

# Change to:
| `.agents/leader/memories/*.md` | Agent-personal observations (NOT project knowledge — use experience() for that) |
```

## Key Files
- `agents/leader/rule.md` — Leader rules (185 lines): add decision table, expand Project History section
- `agents/_prompt_system/knowledge.md` — Shared knowledge prompt (101 lines): add tool decision table
- `agents/_prompt_system/knowledge_no_force_explore.md` — Leader variant (73 lines): add same table
- `agents/leader/tools_note.md` — File permissions (56 lines): fix misleading label

## Constraints
- Both knowledge.md variants MUST get the same decision table (keep in sync)
- Don't duplicate the table across too many files — rule.md has the detailed version, knowledge.md has the summary
- Keep text concise — these are injected into every agent's system prompt (token cost)
- **F2 note**: The decision table should NOT assume RAG is enabled. `experience()` requires RAG backend; if unavailable, agents should use `project_history_add()` for events. Note this in the table.

## Deliverables
- [ ] Leader `rule.md` has expanded "Recording — Decision Table" replacing the single-sentence Project History note
- [ ] Both `knowledge.md` variants have "Memory Tools: When to Use What" section
- [ ] Leader `tools_note.md` has corrected label for memories directory
- [ ] Decision table clearly states inner_soul will REJECT project content
