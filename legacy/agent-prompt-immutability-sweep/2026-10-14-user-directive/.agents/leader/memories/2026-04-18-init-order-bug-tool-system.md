# Lesson: Init Order Bug in Tool System

**Date:** 2026-04-18
**Project:** agents-ensemble

## What Happened
When implementing per-agent tool filtering, our fix (`ded2c30`) didn't fully work. Another dev (Kha) had to step in with commit `8889d11`.

## Root Causes We Missed
1. **Wrong call order** — We put `scan_tools_for_full_docs()` inside `create_instance_tools()`, but `load_tools_doc_for_agent()` runs BEFORE that during system prompt generation. Metadata was empty when filtering happened.
2. **Dynamic tool invisible** — `tool_help` is created by `create_help_tool()` at runtime, not a module-level function. Module scanning never found it. Category `"help"` expanded to nothing.
3. **Missing category** — `access_memory` was a separate category from `self`. Agents listing `"self"` didn't get memory access.

## What the Fix Did
1. `ensure_registry_ready()` — eagerly scans all category modules on demand via importlib
2. Explicit `tool_help` registration in `create_help_tool()` 
3. Added `access_memory` to restricted agents' allow lists

## Lessons
- **Trace actual call sequences end-to-end** — don't assume function ordering
- **Dynamic/runtime-created tools need explicit registration** — scanning won't find them
- **Category design should minimize footguns** — we later merged access_memory into self

## Resolution
- Merged `access_memory` into `self` category (`ae17da7`)
- Added startup validation for agent tool configs (`6008605`)
