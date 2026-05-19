# `_inner_soul/` — Inner Soul Behavior Definition

> **This is NOT a real agent.** It is a documentation directory that defines
> the behavior of the `inner_soul` tool function (`daemon/tools/inner_soul.py`).

## Why This Exists

The `inner_soul` tool allows agents to remember, learn, and modify themselves.
This directory contains markdown files that describe the intended behavior of
that tool — serving as a design reference and documentation.

## Files

| File | Purpose |
|------|---------|
| `soul.md` | Describes the "personality" and purpose of the inner_soul function |
| `rule.md` | Classification rules and compaction instructions for memory management |
| `workflow.md` | Step-by-step workflow for how inner_soul processes requests |

## Why No `meta.json`?

This directory **intentionally lacks** `meta.json`. This prevents agent discovery
(`daemon/registry.py`) from registering it as a real agent. Without `meta.json`,
the directory is invisible to the agent system at runtime.

## Runtime Loading

**None of these files are loaded at runtime.** The actual behavior is implemented
in `daemon/tools/inner_soul.py` as Python code. These markdown files serve as
human-readable documentation only.

## History

- **Phase 3 (Memory Compaction):** Updated `rule.md` with compaction instructions
- **Phase 4 (Archive Lifecycle):** Added archive documentation to `rule.md`
- **Phase 6 (Cleanup):** Audited references, added this README
