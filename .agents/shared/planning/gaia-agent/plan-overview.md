# Plan: Gaia Agent — Environment Setup Assistant

## Objective
Add a new "Gaia" agent to the ensemble that guides users through environment/dependency setup by reading instructional scripts from `gaia/scripts/`. Includes the first script (`npx.md`) for npx setup required by Context7 MCP.

## Scope Assessment
**SMALL** — 6 new files (4 agent definition files + 1 script + this plan). No daemon code changes. No existing file modifications. Auto-discovered by `AgentRegistry.discover()`.

## Tasks

| # | Task | Key Files |
|---|------|-----------|
| 1 | Create `agents/gaia/meta.json` with agent metadata | `agents/gaia/meta.json` |
| 2 | Create `agents/gaia/soul.md` — Gaia's identity as nurturing environment-mother | `agents/gaia/soul.md` |
| 3 | Create `agents/gaia/rule.md` — constraints and behavior rules | `agents/gaia/rule.md` |
| 4 | Create `agents/gaia/workflow.md` — how Gaia operates (list scripts → read → guide → verify) | `agents/gaia/workflow.md` |
| 5 | Create `agents/gaia/tools_note.md` — tool usage guidance | `agents/gaia/tools_note.md` |
| 6 | Create `gaia/scripts/npx.md` — first setup script for npx/Context7 | `gaia/scripts/npx.md` |

## Key Design Decisions

| Decision | Choice | Why |
|----------|--------|-----|
| Tools | `bash`, `filesystem`, `help` | Needs bash for verification commands, filesystem to read scripts |
| `system` | `false` | Gaia is a user-facing agent, not a system agent |
| `innate_skills` | `[]` | No special skills needed — just reads files and runs commands |
| `llm_model` | `null` (default) | Not a high-volume agent; default model is fine |
| Scripts path | `gaia/scripts/` at project root | Keeps scripts separate from agent definition; accessible to other tools |
| Script format | Markdown | Consistent with project's everything-in-markdown philosophy |

## File Content Guidelines

### `meta.json`
```json
{
  "id": "gaia",
  "name": "Gaia",
  "description": "Environment setup assistant — guides users through dependency and tool installation",
  "icon": "🌍",
  "color": "accent-green",
  "version": "1.0.0",
  "system": false,
  "capabilities": [],
  "tags": ["environment", "setup", "dependencies"],
  "innate_skills": [],
  "tools": {
    "allow": ["bash", "filesystem", "help"],
    "deny": []
  },
  "llm_model": null
}
```

### `soul.md`
- **Identity**: Earth-mother / nurturing figure — patient, encouraging, growth-oriented
- **Tone**: Warm, supportive, uses nature/growth metaphors
- **Core job**: Help users cultivate their development environment
- **Key behavior**: List available scripts → guide through chosen script step by step → verify success

### `rule.md`
- Must read scripts from `gaia/scripts/` directory (project root relative)
- Must not modify scripts, only read and guide
- Must run verification commands after setup to confirm success
- Must handle errors gracefully and suggest troubleshooting
- Scripts directory path is `{project_root}/gaia/scripts/`

### `workflow.md`
- Simple linear flow: greet → list scripts → user picks → read script → guide steps → verify
- Reference existing agent workflows (like explorer's simplicity) for style

### `tools_note.md`
- Brief guidance on using `bash` for verification, `filesystem` for reading scripts
- Can be minimal (like coder's: "All tools are common tools")

### `gaia/scripts/npx.md`
- **What npx is**: Node.js package runner (bundled with npm 5.2+)
- **Why needed**: Context7 MCP requires npx to run
- **Install instructions**:
  - macOS: `brew install node` or download from nodejs.org
  - Linux: `curl -fsSL https://deb.nodesource.com/setup_lts.x | sudo -E bash - && sudo apt install -y nodejs` or nvm
  - Windows: Download from nodejs.org or `winget install OpenJS.NodeJS.LTS`
- **Verification**: `npx --version`
- **Troubleshooting**: Node not in PATH, old Node version, permission errors

## Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Script path not found at runtime | Low — Gaia can't read scripts | Gaia should check path exists and report clearly |
| npx instructions become outdated | Low — Node.js install methods are stable | Scripts are markdown, easy to update |

## Success Criteria
- [ ] `agents/gaia/` directory exists with all 5 required files
- [ ] `meta.json` is valid JSON matching the agent schema
- [ ] `soul.md` defines Gaia's nurturing personality clearly
- [ ] `rule.md` specifies the scripts directory path and constraints
- [ ] `workflow.md` describes a clear list → read → guide → verify flow
- [ ] `gaia/scripts/npx.md` covers what/why/install/verify/troubleshoot
- [ ] Agent is auto-discovered by `AgentRegistry.discover()` (no code changes needed)

## Tracking
- Created: 2025-07-25
- Status: draft
