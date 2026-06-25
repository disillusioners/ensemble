# Phase 1: Core Agent Definition

## Objective
Rename the `agents/coder/` directory to `agents/developer/`, update `meta.json` ID from `"coder"` to `"developer"`, and update the agent's own prompt files (soul.md, rule.md, workflow.md) to self-reference as "developer" instead of "coder".

## Coupling
- **Depends on**: None (root phase)
- **Coupling type**: — (root)
- **Shared files with other phases**: `agents/developer/meta.json` (read by Phase 2, 5, 6)
- **Shared APIs/interfaces**: The `agent_id` string `"developer"` is consumed by all downstream phases
- **Why this coupling**: Every other phase depends on the agent directory existing at its new path

## Context
- Agent discovery is filesystem-based: `daemon/registry.py:discover()` scans `agents/` and reads each `meta.json`
- The `id` field in meta.json becomes the canonical agent_id used throughout the system
- Directory name is used as fallback if `id` is missing from meta.json (line 162: `agent_id = meta.get("id", agent_path.name)`)

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Rename directory | `git mv agents/coder agents/developer` | `agents/coder/` → `agents/developer/` |
| 2 | Update meta.json | Change `id` to `"developer"`, `name` to `"Developer"`. Keep description, icon, color, tools, version. | `agents/developer/meta.json` |
| 3 | Update soul.md | Change self-references from "coder" to "developer" (1 ref: `.agents/coder/memories/` path) | `agents/developer/soul.md` |
| 4 | Update rule.md | Change self-references (4 refs: "opencode as a coder" → "opencode as a developer", etc.) | `agents/developer/rule.md` |
| 5 | Update workflow.md | Change any self-references (check for "coder" in workflow context) | `agents/developer/workflow.md` |
| 6 | Verify no remaining "coder" refs in agent dir | `grep -rn "coder" agents/developer/` should return 0 (excluding legitimate uses like "coder" as a verb) | All files in `agents/developer/` |

## Key Files
- `agents/developer/meta.json` — Agent definition (id, name, description, tools config)
- `agents/developer/soul.md` — Agent persona/self-identity (1 "coder" ref: memory path)
- `agents/developer/rule.md` — Agent behavioral rules (4 "coder" refs)
- `agents/developer/workflow.md` — Agent workflow instructions (check for refs)
- `agents/developer/memory.md` — Agent memory file (0 refs, no change needed)
- `agents/developer/growth.md` — Agent growth file (0 refs, no change needed)
- `agents/developer/tools_note.md` — Tool usage notes (0 refs, no change needed)
- `agents/developer/user.md` — User context (0 refs, no change needed)

## Detailed Change: meta.json

**Before:**
```json
{
  "id": "coder",
  "name": "Coder",
  "description": "Specializes in code generation and debugging",
  "icon": "💻",
  "color": "accent-cyan",
  "version": "1.0.0",
  "innate_skills": ["opencode"],
  "tools": {
    "allow": ["bash", "filesystem", "time", "self", "help", "knowledge", "mcp", "context", "db"]
  }
}
```

**After:**
```json
{
  "id": "developer",
  "name": "Developer",
  "description": "Specializes in code generation and debugging",
  "icon": "💻",
  "color": "accent-cyan",
  "version": "1.0.0",
  "innate_skills": ["opencode"],
  "tools": {
    "allow": ["bash", "filesystem", "time", "self", "help", "knowledge", "mcp", "context", "db"]
  }
}
```

## Detailed Changes: soul.md

Line 54: `.agents/coder/memories/` → `.agents/developer/memories/`

## Detailed Changes: rule.md

| Line | Current | New |
|------|---------|-----|
| 24 | `opencode as a dumb file I/O tool ... You are an orchestrator, not a coder` | `opencode as a dumb file I/O tool ... You are an orchestrator, not a developer` *(or rephrase to avoid confusion)* |
| 105 | `block coder workflow` | `block developer workflow` |
| 151 | `Run full test suite from coder session` | `Run full test suite from developer session` |

> **Note**: Line 24 uses "coder" both as the agent_id AND as a generic noun ("you are not a coder"). Consider keeping the generic noun usage but updating the agent_id reference. Recommended: rephrase to "You are an orchestrator, not a line-by-line typist."

## Constraints
- Use `git mv` to preserve git history
- Do NOT delete the old directory — `git mv` handles the rename atomically
- The description, icon, color, version, and tools config should remain unchanged
- Verify the registry can discover the agent after rename: `registry.discover()` should list `"developer"`

## Deliverables
- [ ] `agents/developer/` directory exists with all files from `agents/coder/`
- [ ] `agents/coder/` directory no longer exists
- [ ] `agents/developer/meta.json` has `"id": "developer"` and `"name": "Developer"`
- [ ] `agents/developer/soul.md` references `.agents/developer/memories/`
- [ ] `agents/developer/rule.md` uses "developer" for self-references
- [ ] `grep -rn "coder" agents/developer/` returns 0 matches (or only legitimate verb usage)
