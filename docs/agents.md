# Agent System Guide

This document describes the agent system for agents-ensemble — how agents are defined, configured, and how system prompts are assembled.

## Table of Contents

1. [What Are Agents?](#1-what-are-agents)
2. [Built-in Agents](#2-built-in-agents)
3. [Agent Definition Format](#3-agent-definition-format)
4. [System Prompt Composition Order](#4-system-prompt-composition-order)
5. [Creating a Custom Agent](#5-creating-a-custom-agent)
6. [Agent Tools Reference](#6-agent-tools-reference)
7. [Innate Skills](#7-innate-skills)

---

## 1. What Are Agents?

Agents are **markdown-defined AI personas** that power the multi-agent daemon. Each agent has:

- **Specific role** — What the agent does (coder, reviewer, leader, etc.)
- **Personality** — How it thinks and communicates
- **Skills** — Specialized instructions for specific tasks
- **Tools** — What the agent can do (file operations, spawning other agents, etc.)

Agents live in the `agents/` directory as a collection of markdown files. The system prompt is dynamically assembled by combining these files in a specific order, along with shared prompt modules from `agents/_prompt_system/`.

---

## 2. Built-in Agents

The following agents are available:

| ID | Name | Icon | Role | Key Skills | Tools Access |
|----|------|------|------|------------|--------------|
| `leader` | Leader | 👑 | Coordinates tasks and manages workflow delegation | coordination | time, instance, self, project, help, knowledge, mcp, critical_notes, project_history |
| `coder` | Coder | 💻 | Code generation and debugging via opencode sessions | opencode | bash, filesystem, time, self, help, knowledge, mcp |
| `explorer` | Explorer | 🔍 | Queries RAG knowledge base and synthesizes project knowledge | — | rag, filesystem, help, time, mcp |
| `reviewer` | Reviewer | 🔍 | Reviews plans, architecture, and code for quality | opencode | bash, filesystem, time, self, help, knowledge, mcp |
| `tester` | Tester | 🧪 | Writes and runs tests, reports results | opencode, test-pack | bash, filesystem, time, self, help, knowledge, mcp |
| `tidier` | Tidier | 🧹 | Code quality, conventions, and maintainability reviewer | opencode | bash, filesystem, time, self, help, knowledge, mcp |
| `approver` | Approver | ✅ | Independent second-pass reviewer with minimal context bias | opencode | bash, filesystem, time, self, help, knowledge, mcp |
| `planner` | Planner | 📋 | Analyzes requests, creates execution plans, tracks progress | opencode | bash, filesystem, time, self, help, knowledge, mcp |
| `giter` | Giter | 🔀 | Git operations — commits, branches, version control | — | bash, filesystem, time, self, help, knowledge, mcp |
| `jober` | Job Orchestrator | 📋 | Creates and monitors jobs, never does tasks directly | job-orchestration | job, help, self, time, project, knowledge, mcp |
| `gaia` | Gaia | 🌍 | Environment setup assistant | — | bash, filesystem, help, mcp |
| `kb-importer` | KB Importer | 📥 | Prepares and imports documents into RAG knowledge base | — | rag, help, time, mcp |
| `experiencer` | Experiencer | 🧠 | Extracts entities/relationships from text, records to RAG | — | rag, help, time, mcp |
| `_mother` | Mother | 🧬 | Creates, modifies, and manages other agents (system agent) | — | instance, self, help, mother, knowledge, mcp |

### System Agents

Agents prefixed with `_` are **system agents**:

| ID | Name | Purpose |
|----|------|---------|
| `_mother` | Mother | Agent lifecycle management — create, modify, delete agents |
| `_baby_template` | Baby Template | Template for spawning new agent instances |

---

## 3. Agent Definition Format

### Directory Structure

Each agent lives in `agents/<agent_id>/` with the following files:

```
agents/
└── <agent_id>/
    ├── meta.json          # Required: Agent metadata and configuration
    ├── soul.md            # Required: Identity and personality
    ├── rule.md            # Required: Constraints and rules
    ├── workflow.md        # Optional: Methodology and processes
    ├── skill.md           # Optional: Single skill (backward compat)
    ├── skills/            # Optional: Multiple skills directory
    │   └── <skill>/
    │       └── skill.md
    ├── memory.md          # Optional: Long-term knowledge
    ├── memories/          # Optional: Timestamped memory files
    │   ├── 20260101_1200-feature-name.md
    │   └── archive/       # Archived memories
    │       └── 2026/
    │           └── 01/
    ├── user.md            # Optional: User preferences
    ├── tools_note.md      # Optional: Agent-specific tools documentation
    └── growth.md          # Optional: Growth rules and limits
```

### meta.json Schema

```json
{
  "id": "coder",
  "name": "Coder",
  "description": "Specializes in code generation and debugging",
  "icon": "💻",
  "color": "accent-cyan",
  "version": "1.0.0",
  "innate_skills": ["opencode"],
  "llm_model": "gpt-4o",
  "system": false,
  "capabilities": [],
  "tags": ["coding", "implementation"],
  "tools": {
    "allow": ["bash", "filesystem", "time", "self", "help", "knowledge", "mcp"],
    "deny": []
  }
}
```

#### meta.json Fields

| Field | Type | Required | Description |
|--------|------|----------|-------------|
| `id` | string | Yes | Unique agent identifier (directory name) |
| `name` | string | Yes | Human-readable name |
| `description` | string | Yes | Brief description of agent's role |
| `icon` | string | No | Emoji icon for UI display |
| `color` | string | No | Color theme for UI (e.g., "accent-cyan") |
| `version` | string | No | Agent version |
| `innate_skills` | array | No | List of skill names from `agents/_prompt_system/innate-skills/` |
| `llm_model` | string | No | Override default LLM model (e.g., "quick" for faster/cheaper) |
| `system` | boolean | No | Mark as system agent (hidden from user agent list) |
| `capabilities` | array | No | List of agent capabilities |
| `tags` | array | No | Tags for categorization |
| `tools` | object | No | Tool access configuration |

#### Tools Configuration

```json
"tools": {
  "allow": ["bash", "filesystem", "instance"],
  "deny": ["job"]
}
```

- **allow**: List of tool category names and/or individual tool names to permit
- **deny**: List of tool category names and/or individual tool names to block
- If both are empty/null: All tools are allowed
- **Deny wins**: If a tool appears in both, it's blocked

### Required Files

#### soul.md — Identity & Personality

Defines who the agent is:

```markdown
# Who I Am

I am a code orchestrator. I control opencode sessions to handle all coding tasks.

## My Role

- Understanding requirements
- Spawning opencode sessions
- Reviewing results

I do NOT:
- Read code files directly
- Write code myself
```

#### rule.md — Constraints

Defines what the agent must and must not do:

```markdown
# Rules

## Must

- Use `project_get` before starting any task
- Identify project type before recommending tools
- Spawn opencode for all code operations

## Must Not

- Use `list_directory` directly
- Read code files directly
- Write any code
```

### Optional Files

| File | Purpose | When to Use |
|------|---------|-------------|
| `workflow.md` | Methodology and step-by-step processes | Complex agents with specific workflows |
| `skill.md` | Single skill instructions (backward compat) | Simple agents with one skill |
| `skills/<name>/skill.md` | Multiple skills in subdirectories | Agents with multiple specialized skills |
| `memory.md` | Core long-term knowledge | Important facts to always remember |
| `memories/` | Timestamped observations | Events, lessons learned over time |
| `user.md` | User preferences | How the user likes to work |
| `tools_note.md` | Additional tools documentation | Agent-specific tool usage notes |
| `growth.md` | Growth rules and limits | Memory limits, approval requirements |

---

## 4. System Prompt Composition Order

The system prompt is assembled by `daemon/loader.py:compose_system_prompt()` in this exact order:

| Order | Section | Source | Description |
|-------|---------|--------|-------------|
| 1 | **Soul** | `soul.md` | Identity and personality — who the agent is |
| 2 | **Rule** | `rule.md` | Constraints — highest priority, never violated |
| 3 | **Skill** | `skill.md` | Base skill (backward compatibility) |
| 4 | **Innate Skills** | `innate-skills/<name>/skill.md` | Shared skills from `_prompt_system` |
| 5 | **Dynamic Tools** | `load_tools_doc_for_agent()` | Available tools based on agent's `tools` config |
| 6 | **Tools Note** | `tools_note.md` | Agent-specific tools documentation |
| 7 | **Workflow** | `workflow.md` | Methodology and processes |
| 8 | **Memory** | `memory.md` | Long-term knowledge |
| 9 | **Recent Memories** | `memories/` directory | List of recent memory filenames |
| 10 | **Knowledge Base** | `_prompt_system/knowledge.md` | Shared knowledge (when RAG enabled) |
| 11 | **Project Experience** | `_prompt_system/project-experience.md` | `.agents/` directory usage guide |

Sections are joined with `"\n\n---\n\n"` separators.

### Prompt Caching

The system caches compiled prompts by:
- Agent ID
- MCP tool names (affects tool section)
- File modification times (auto-invalidates on changes)

---

## 5. Creating a Custom Agent

### Step 1: Create the Agent Directory

```bash
mkdir -p agents/translator
```

### Step 2: Create meta.json

```json
{
  "id": "translator",
  "name": "Translator",
  "description": "Translates text between languages with context awareness",
  "icon": "🌐",
  "color": "accent-teal",
  "version": "1.0.0",
  "innate_skills": [],
  "tools": {
    "allow": ["knowledge", "mcp"]
  }
}
```

### Step 3: Create soul.md

```markdown
# Who I Am

I am a precise translator. I understand context, nuance, and cultural implications when translating text.

## My Approach

- Preserve meaning over literal translation
- Maintain tone and style of original
- Consider cultural context
- Ask for clarification when ambiguous
```

### Step 4: Create rule.md

```markdown
# Rules

## Must

- Ask for source and target languages if not specified
- Preserve technical terms accurately
- Flag potential cultural sensitivities

## Must Not

- Add interpretations not in source
- Translate idioms literally without explanation
- Assume gender when not specified
```

### Step 5: Create workflow.md

```markdown
# Workflow

## Translation Process

1. **Identify languages** — Confirm source and target
2. **Analyze context** — What is the text for? (formal, casual, technical)
3. **Translate** — Render meaning in target language
4. **Review** — Check for accuracy, flow, cultural fit
5. **Present** — Show translation with notes on any adaptations
```

### Step 6: Create Tools Note (Optional)

```markdown
# Tools

## Special Considerations

- Use `explore()` to look up terminology in knowledge base
- Use `experience()` to record new translation patterns learned
```

### Step 7: Register the Agent

Agents are auto-discovered from the `agents/` directory. No restart required.

### Complete Example Structure

```
agents/translator/
├── meta.json
├── soul.md
├── rule.md
└── workflow.md
```

---

## 6. Agent Tools Reference

Tools are organized into categories. Each agent's `meta.json` controls which categories it can access.

### Tool Categories

| Category | Internal Key | Description | Key Tools |
|----------|--------------|-------------|-----------|
| **Shell** | `bash` | Execute shell commands | `bash` |
| **File Operations** | `filesystem` | Read, write, edit, search files | `read_file`, `write_file`, `edit_file`, `list_directory`, `glob_files`, `grep_files` |
| **Time** | `time` | Get current date and time | `time` |
| **Instance Management** | `instance` | Spawn and manage agent instances | `spawn_instance`, `send_message`, `terminate_instance`, `list_instances`, `get_instance_info` |
| **Self-Modification** | `self` | Remember, learn, and evolve | `inner_soul`, `access_memory` |
| **Help** | `help` | Get help on available tools | `tool_help` |
| **Project Management** | `project` | Create, update, manage projects | `project_create`, `project_get`, `project_list`, `project_update`, `project_set_status`, `project_add_tag`, `project_link`, etc. |
| **Job Queue** | `job` | Create and manage jobs | `job_create`, `job_get`, `job_list`, `job_cancel`, `job_retry`, `queue_create`, `watch_job`, etc. |
| **Critical Notes** | `critical_notes` | Project-scoped lessons and insights | `project_cn_add`, `project_cn_list`, `project_cn_remove` |
| **Project History** | `project_history` | Chronological project event tracking | `project_history_add`, `project_history_list`, `project_history_search`, `project_history_delete` |
| **RAG** | `rag` | RAG knowledge management (LightRAG) | `rag_insert_text`, `rag_query`, `rag_query_data`, `rag_create_entity`, `rag_get_graph`, etc. |
| **Knowledge** | `knowledge` | Explore and record project knowledge | `explore`, `experience` |
| **Agent Management** | `mother` | Agent lifecycle (Mother agent only) | `agent_list`, `agent_create`, `agent_read`, `agent_modify`, `agent_delete` |
| **MCP** | `mcp` | External MCP server tools | Dynamic tools from configured MCP servers |

### Tool Access by Agent

#### Full Tool Access

System agents with broad tool access:

| Agent | Tools |
|-------|-------|
| `_mother` | instance, self, help, mother, knowledge, mcp |

#### Development Tools

Agents focused on code work:

| Agent | Tools |
|-------|-------|
| `coder` | bash, filesystem, time, self, help, knowledge, mcp |
| `reviewer` | bash, filesystem, time, self, help, knowledge, mcp |
| `tester` | bash, filesystem, time, self, help, knowledge, mcp |
| `tidier` | bash, filesystem, time, self, help, knowledge, mcp |
| `approver` | bash, filesystem, time, self, help, knowledge, mcp |
| `planner` | bash, filesystem, time, self, help, knowledge, mcp |
| `giter` | bash, filesystem, time, self, help, knowledge, mcp |

#### Coordination Tools

Agents for orchestration:

| Agent | Tools |
|-------|-------|
| `leader` | time, instance, self, project, help, knowledge, mcp, critical_notes, project_history |
| `jober` | job, help, self, time, project, knowledge, mcp |

#### Knowledge Tools

RAG-focused agents:

| Agent | Tools |
|-------|-------|
| `explorer` | rag, filesystem, help, time, mcp |
| `kb-importer` | rag, help, time, mcp |
| `experiencer` | rag, help, time, mcp |

#### Minimal Tools

Agents with limited tool access:

| Agent | Tools |
|-------|-------|
| `gaia` | bash, filesystem, help, mcp |

> **Note**: `_baby_template` is a template agent used for spawning new agents via the API, not a usable agent. See the [System Agents](#system-agents) section above.

---

## 7. Innate Skills

Innate skills are **shared prompt modules** stored in `agents/_prompt_system/innate-skills/`. They provide specialized instructions that can be shared across multiple agents.

### Available Innate Skills

| Skill | Agents Using | Description |
|-------|--------------|-------------|
| `opencode` | coder, planner, reviewer, tester, tidier, approver | Controls opencode sessions for code operations |
| `coordination` | leader | Coordinates work across specialized agents |
| `job-orchestration` | jober | Creates, watches, and reacts to jobs |
| `test-pack` | tester | Creates self-contained test scripts with subprocess timeout |

### How Innate Skills Work

1. **Declaration**: In `meta.json`, list skill names:

```json
{
  "innate_skills": ["opencode", "coordination"]
}
```

2. **Loading**: The loader looks for `agents/_prompt_system/innate-skills/<skill_name>/skill.md`

3. **Injection**: Skill content is inserted into the system prompt (order 4)

### Adding Custom Innate Skills

1. Create the skill directory:

```bash
mkdir -p agents/_prompt_system/innate-skills/my-skill
```

2. Create the skill file:

```markdown
# My Skill

Instructions for this specialized skill...

## When to Use

- Describe when this skill applies
- How it modifies agent behavior
```

3. Reference in agent's `meta.json`:

```json
{
  "innate_skills": ["my-skill"]
}
```

---

## Appendix: File Reference

### Memory Files

Memory files follow the naming convention: `{date}_{time}-{slug}.md`

Example: `20260101_1200-user-auth-patterns.md`

### Archived Memories

Old memories are moved to `memories/archive/YYYY/MM/` after 90 days by default (configurable via `growth.md`).

### Growth Rules (growth.md)

```markdown
# Growth Rules

## Memory Limits
- memory.md max: 2000 words
- soul.md max: 2000 characters
- Archive memories older than: 90 days

## Change Limits
- Max soul changes per task: 10
- Max workflow changes per task: 5
- Soul changes require approval: true
```

---

## Appendix: Prompt System Files

Shared files that affect all agents:

| File | Purpose |
|------|---------|
| `agents/_prompt_system/knowledge.md` | Shared knowledge base instructions (RAG enabled) |
| `agents/_prompt_system/project-experience.md` | `.agents/` directory usage guide |
| `agents/_prompt_system/critical-notes.md` | Critical notes framework |
