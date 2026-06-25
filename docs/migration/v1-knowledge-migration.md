# V1 Knowledge Migration Guide

## Overview

This guide documents the migration from file-based project memory (`.agents/{agent-id}/memories/`) to the RAG knowledge system using `explore()` and `experience()` tools.

### What Changed

| Before (File-Based) | After (RAG Knowledge) |
|---------------------|----------------------|
| `.agents/{agent-id}/memories/*.md` files | RAG knowledge base via `experience(text)` |
| `access_memory(filename)` tool | `explore(query)` tool |
| `inner_soul(intent="remember", content="...")` → creates memory file | `inner_soul(intent="remember", content="...")` → redirects to `experience()` |
| Memory files listed in system prompt | Knowledge queried on-demand via `explore()` |
| Per-agent memory silos | Shared knowledge base across all agents |

### What Didn't Change

- **Agent core memory** (`agents/{agent}/soul.md`, `agents/{agent}/rule.md`, `agents/{agent}/memory.md`) — NOT affected by this migration
- **Self-modification** via `inner_soul` for soul, user, workflow targets — works exactly as before
- **Shared project space** (`.agents/shared/`) — still uses file-based system
- **Planning files** (`.agents/shared/planning/`) — unchanged

---

## Tool Comparison

| Old Tool | New Tool | Notes |
|----------|----------|-------|
| `access_memory(filename)` | `explore(query)` | Query by semantic meaning instead of filename |
| `inner_soul(intent="remember", content="...")` | `experience(text)` | Direct recording to knowledge base |
| `write_file(path, content)` to `.agents/{id}/memories/` | `experience(text)` | Use experience() for knowledge recording |

---

## inner_soul Redirect Behavior

The `inner_soul` tool now intelligently redirects knowledge-oriented requests to the RAG system:

### Redirected to experience()

| Request Example | Classification | Why Redirected |
|----------------|---------------|----------------|
| "I learned that early testing catches bugs" | knowledge | Project knowledge |
| "Pattern: always when we use k8s..." | pattern | Observed pattern |
| "Today we discussed the API design" | event | Event/observation |
| "I can now do Docker deployments" | skill | Learned skill |
| "I made a mistake with the SQL query" | mistake | Lesson learned |
| "The project uses postgresql://..." | project_knowledge | Project-specific info |
| `intent="remember"` (no target) | any | Memory intent |
| `intent="learn"` (no target) | any | Learning intent |

### NOT Redirected (Self-Modification Preserved)

| Request Example | Classification | Targets | Why Not Redirected |
|----------------|---------------|---------|-------------------|
| "My name is Cody" | identity | soul.md | Self-modification |
| "Be more friendly" | personality | soul.md + user.md | Self-modification |
| "User likes TypeScript" | user_preference | user.md | User preference |
| "Always check tests before commit" | workflow | workflow.md | Workflow change |
| `intent="change", target="workflow"` | any | workflow.md | Explicit self-modification |

---

## Environment Variables

The RAG knowledge system requires these environment variables:

| Variable | Required | Description |
|----------|----------|-------------|
| `LIGHTRAG_HOST` | Yes | LightRAG server URL (e.g., `http://localhost:8724`) |
| `LIGHTRAG_API_KEY` | No | API key for authentication |
| `LIGHTRAG_WORKSPACE` | No | Workspace name (default: `default`) |
| `LIGHTRAG_TIMEOUT` | No | Request timeout in seconds (default: 60) |

### Graceful Degradation

When RAG is not configured:
- `explore()` returns a helpful message about configuring RAG
- `experience()` returns a helpful message about configuring RAG
- `inner_soul` redirect still works — guides agents to use experience() when available
- All other agent functionality works normally

---

## Migration Steps

> ⚠️ **WARNING: Run the migration script only ONCE.** Running it multiple times will create duplicate entries in the RAG knowledge base. Use `--dry-run` first to preview what will be migrated.

### For Existing Projects

1. **Set up LightRAG server** — See `docs/configuration/rag-configuration.md`
2. **Configure environment variables** — Set `LIGHTRAG_HOST` and optionally `LIGHTRAG_API_KEY`
3. **Import existing memories** (optional):
   ```bash
   python scripts/migrate_memory_to_rag.py --all
   # or for a specific agent:
   python scripts/migrate_memory_to_rag.py --agent developer
   # dry-run first to preview:
   python scripts/migrate_memory_to_rag.py --dry-run --all
   # force re-migration (ignore state file):
   python scripts/migrate_memory_to_rag.py --force --all
   ```
4. **Verify** — Use `explore(query="what do you know about this project?")` to test
5. **Clean up** — After verifying all memories are imported, optionally remove old memory files

### For New Projects

No migration needed — just configure the RAG environment variables and start using `explore()` and `experience()`.

---

## Rollback Instructions

If you need to rollback to the file-based system:

1. `git checkout` the following files:
   - `daemon/tools/inner_soul.py`
   - `daemon/tools/access_memory.py`
   - `agents/_prompt_system/project-experience.md`
   - `agents/*/meta.json`
   - `agents/*/knowledge.md`
2. Restart the daemon
3. Old memory files in `.agents/{agent-id}/memories/` are preserved (migration script doesn't delete them)

---

## Troubleshooting

### Q: `explore()` returns "RAG not configured"
**A:** Set `LIGHTRAG_HOST` environment variable and restart the daemon.

### Q: `inner_soul` redirects knowledge that should go to soul.md
**A:** Be explicit: `inner_soul(intent="change", target="soul", content="...")` to force soul.md update.

### Q: Old memories not appearing in explore() results
**A:** Run the migration script: `python scripts/migrate_memory_to_rag.py --all`

### Q: Can I still use write_file to create .agents/ files?
**A:** Yes, the `.agents/shared/` directory still uses files. Only `.agents/{agent-id}/memories/` is replaced by RAG.
