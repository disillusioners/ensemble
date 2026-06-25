# Agent Skills Architecture Analysis (2026-04-24)

## Purpose
Comprehensive architectural analysis of the agent definition and skills system, performed for a major refactoring effort.

## Key Findings

### 1. Agent Definition Structure
- **9 active agents**: leader, coder, reviewer, tester, planner, tidier, approver, jober, giter
- **2 system agents**: _mother, _inner_soul
- **1 template**: _baby_template
- Each agent has: meta.json, soul.md, rule.md, workflow.md, and optional memory.md, tools_note.md, growth.md, knowledge.md, user.md

### 2. Skills System
- Skills live in `agents/<name>/skills/<skill-name>/skill.md`
- **4 unique skills**: opencode (220 lines), coordination (54), job-orchestration (232), test-pack (86)
- **CRITICAL DUPLICATION**: opencode skill copied identically across 6 agents (coder, reviewer, tester, planner, tidier, approver) = 1,320 duplicated lines
- All 6 copies are byte-for-byte IDENTICAL

### 3. Prompt Composition (daemon/loader.py)
- 10-step composition order: soul.md → rule.md → skill.md → skills/*/ → dynamic_tools → tools_note.md → workflow.md → memory.md → recent memories → project-experience.md
- Mtime-based caching in PromptCache class
- Skills directories discovered by iterating `skills/` subdirectories
- Missing files silently skipped

### 4. No Sharing/Inheritance Mechanism
- NO shared skill directories
- NO symlinks
- NO skill inheritance
- NO cross-agent skill loading in loader.py
- Only shared item: project-experience.md (content injected into all)

### 5. meta.json Schema
- Fields: id, name, description, icon, color, version?, system?, tools: {allow: [...]}
- Tool categories: bash, filesystem, time, instance, self, project, job, help, mother
- Most agents get: bash, filesystem, time, self, help
- leader: time, instance, self, project, help
- jober: job, help, self, time, project
- giter: bash, filesystem, time, self, help

### 6. Refactoring Opportunities
- Extract opencode skill to shared location → save 1,100 lines
- Could add skill inheritance to loader.py (e.g., skills from _shared/)
- Could add skill composition (agent inherits base skills, adds own)
- tools_note.md could be partially shared (many agents have similar tool docs)
