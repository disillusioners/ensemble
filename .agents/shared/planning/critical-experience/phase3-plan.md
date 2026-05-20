# Phase 3: Experiencer Agent & Leader Agent Updates

## Objective
Update the Experiencer agent to include critical experience routing logic, update the Leader agent to access critical experience tools, and create shared prompt documentation for writing concise critical experience entries.

## Coupling
- **Depends on**: Phase 2 (tools)
- **Coupling type**: **loose** — Phase 3 references tool names and categories from Phase 2 but doesn't import Phase 2 code. The tools are accessed via LangGraph's tool system at runtime.
- **Shared files with other phases**: None (separate agent config files)
- **Shared APIs/interfaces**: None
- **Why this coupling**: Agent configs reference tool names defined in Phase 2. The tools exist independently; agents just need to know they're available.

## Context
- Phase 2 completed: `project_ce_add`, `project_ce_list`, `project_ce_remove` tools are registered under `critical_experience` category
- Experiencer currently has 8-phase workflow, tools limited to `["rag", "help", "time", "mcp"]`
- Leader currently has tools `["time", "instance", "self", "project", "help", "knowledge", "mcp"]`

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Update Experiencer `meta.json` tools | Add `"critical_experience"` to the `tools.allow` list. New value: `["rag", "help", "time", "mcp", "critical_experience"]`. | `agents/experiencer/meta.json` |
| 2 | Update Leader `meta.json` tools | Add `"critical_experience"` to the `tools.allow` list. New value: `["time", "instance", "self", "project", "help", "knowledge", "mcp", "critical_experience"]`. | `agents/leader/meta.json` |
| 3 | Create `agents/_prompt_system/critical-experience.md` | Shared prompt content with: (a) What critical experience is, (b) Writing guidelines for 200-char summaries, (c) Category definitions and examples, (d) Priority assignment guide, (e) When to use CE vs RAG. Include all example entries from the design spec. | `agents/_prompt_system/critical-experience.md` (new) |
| 4 | Update Experiencer `workflow.md` | Insert new **Phase 7.5: Critical Experience Routing** between current Phase 7 (Insert Document) and Phase 8 (Confirm & Report). Add the routing decision tree for RAG vs CE. | `agents/experiencer/workflow.md` |
| 5 | Update Experiencer `rule.md` | Add new "Must" rule: "Route high-impact knowledge to critical_experience when criteria met". Add corresponding "Must Not" rule: "Never route general programming knowledge to critical_experience". | `agents/experiencer/rule.md` |
| 6 | Update Experiencer `soul.md` | Add brief mention of critical experience curation as part of the role. Update the "My Role" table to include CE as an output. | `agents/experiencer/soul.md` |
| 7 | Update Experiencer `tools_note.md` | Add documentation for the 3 CE tools: `project_ce_add`, `project_ce_list`, `project_ce_remove`. Include usage examples and the routing criteria. | `agents/experiencer/tools_note.md` |

## Key Files
- `agents/experiencer/meta.json` — Tool access configuration
- `agents/leader/meta.json` — Tool access configuration
- `agents/_prompt_system/critical-experience.md` — New shared prompt file
- `agents/experiencer/workflow.md` — Workflow with CE routing phase
- `agents/experiencer/rule.md` — Rules for CE routing
- `agents/experiencer/soul.md` — Role identity update
- `agents/experiencer/tools_note.md` — Tool usage notes

## Detailed Implementation Notes

### Task 1: Experiencer meta.json

```json
{
  "id": "experiencer",
  "name": "Experiencer",
  "description": "Specializes in extracting entities and relationships from text and recording them into the RAG knowledge base, and routing critical project knowledge to the project's critical experience list",
  "icon": "🧠",
  "color": "accent-purple",
  "version": "1.1.0",
  "llm_model": "quick",
  "tools": {
    "allow": ["rag", "help", "time", "mcp", "critical_experience"]
  }
}
```

### Task 2: Leader meta.json

```json
{
  "id": "leader",
  "name": "Leader",
  "description": "Coordinates tasks and manages workflow delegation",
  "icon": "👑",
  "color": "accent-amber",
  "version": "1.1.0",
  "innate_skills": ["coordination"],
  "tools": {
    "allow": ["time", "instance", "self", "project", "help", "knowledge", "mcp", "critical_experience"]
  }
}
```

### Task 3: Critical Experience Prompt

File: `agents/_prompt_system/critical-experience.md`

Contents should include:
1. **What is Critical Experience**: Structured list of concise, high-value project knowledge attached to a project. Always visible to all agents working on the project.
2. **Writing Guidelines**: 
   - Must be actionable (tells agent WHAT to do or NOT do)
   - Must be ≤200 characters
   - Must be project-specific (not general knowledge)
   - Use imperative mood: "Use yarn, not npm" not "This project uses yarn"
3. **Categories**:
   - `convention`: Standards the project follows
   - `pattern`: Recurring solutions used in the project
   - `risk`: Things that can go wrong or must be avoided
   - `decision`: Key architectural or design decisions made
   - `constraint`: Technical limitations that must be respected
4. **Priority Assignment**:
   - `critical`: Security issues, data loss risks, race conditions, breaking changes
   - `high`: Important patterns, key architectural decisions, critical dependencies
   - `medium`: Conventions, nice-to-know patterns, soft constraints
5. **All example entries** from design spec (12 examples)
6. **RAG vs CE routing criteria**

### Task 4: Workflow Phase 7.5

Insert after Phase 7 and before Phase 8:

```markdown
## Phase 7.5: Critical Experience Routing

\```raw
1. Review the knowledge extracted in Phases 2-7
2. For each piece of knowledge, evaluate:
   a. Is it actionable? (tells an agent WHAT to do or NOT do)
   b. Is it project-specific? (not general programming knowledge)
   c. Can it be expressed in ≤200 characters?
   d. Would it prevent mistakes or speed up work for future agents?
3. If ALL four criteria are met → route to critical experience:
   a. Use project_ce_add(
        project_id=...,
        category=<convention|pattern|risk|decision|constraint>,
        priority=<critical|high|medium>,
        summary=<concise actionable statement>,
        reference=<optional link to source>
      )
   b. If similar entry exists → it will be merged automatically
4. If NOT all criteria met → stays in RAG only (already inserted)
5. Proceed to Phase 8
\```
```

### Task 5: Rule Updates

Add to "Must" section:

```markdown
### Route High-Impact Knowledge to Critical Experience

When processing knowledge that meets ALL of these criteria:

1. **Actionable** — Tells an agent WHAT to do or NOT do
2. **Concise** — Can be expressed in ≤200 characters
3. **Project-specific** — Not general programming knowledge
4. **High-impact** — Would prevent mistakes or speed up future work

→ Use `project_ce_add()` to add to the project's critical experience list.

Priority assignment:
- **critical**: Security, data loss risks, race conditions, breaking changes
- **high**: Important patterns, architectural decisions, critical dependencies
- **medium**: Conventions, soft constraints, nice-to-know patterns
```

Add to "Must Not" section:

```markdown
### Never Route General Knowledge to Critical Experience

Critical experience is for **project-specific, actionable** knowledge only:

- Do NOT add general programming tips (e.g., "Use try/except for error handling")
- Do NOT add verbose explanations (keep summaries ≤200 chars)
- Do NOT add knowledge that isn't actionable (e.g., "The project was started in 2024")
- Do NOT add knowledge that's already well-known (e.g., "Tests are important")

When in doubt, keep it in RAG only.
```

### Task 6: Soul Update

In the "My Role" table, update the Output row:
```markdown
| **Output** | Structured entities and relationships in RAG, critical project knowledge in critical experience list |
```

Add to the numbered list after step 7:
```
7.5. Evaluate if any knowledge is critical enough for the project's experience list
```

Add to "What Makes Me Effective":
```markdown
- **Critical routing awareness** — I know which knowledge deserves immediate visibility vs. RAG-only storage
```

### Task 7: Tools Note Update

Add a new section for Critical Experience tools:

```markdown
## Critical Experience Tools

These tools manage a project's critical experience list — high-impact, concise knowledge
that is always visible to all agents working on the project.

### project_ce_add
Adds or merges a critical experience entry. If a similar entry exists (same category + theme),
it will be merged automatically.

**When to use:** After extracting knowledge that meets ALL critical experience criteria
(actionable, concise, project-specific, high-impact).

**Parameters:**
- `project_id` — The project to add to
- `category` — One of: convention, pattern, risk, decision, constraint
- `priority` — One of: critical, high, medium
- `summary` — Max 200 chars, actionable statement
- `reference` — (optional) Link to source doc, file, or memory

### project_ce_list
Returns all critical experience entries for a project.

**When to use:** To review current entries before adding (avoid duplicates).

### project_ce_remove
Removes a specific entry by ID.

**When to use:** When an entry is outdated or incorrect.
```

## Constraints
- Only Experiencer and Leader agents should have `critical_experience` in their tools.allow
- The routing criteria must be clear enough for the LLM to make consistent decisions
- Shared prompt file goes in `agents/_prompt_system/` (existing pattern)
- Experiencer version bumped from 1.0.0 to 1.1.0 (semantic: minor feature addition)
- Leader version bumped from 1.0.0 to 1.1.0

## Deliverables
- [ ] Experiencer `meta.json` updated with `critical_experience` tool access
- [ ] Leader `meta.json` updated with `critical_experience` tool access
- [ ] `agents/_prompt_system/critical-experience.md` created with writing guidelines and examples
- [ ] Experiencer `workflow.md` updated with Phase 7.5 routing logic
- [ ] Experiencer `rule.md` updated with routing rules (Must + Must Not)
- [ ] Experiencer `soul.md` updated with CE-aware role description
- [ ] Experiencer `tools_note.md` updated with CE tool documentation
