# Explore Sequential Rule Enforcement — 2026-04-29

## What was done
Added "Explore Tool Workflow" section to knowledge.md files for all 11 agents with `knowledge` tool access:
- developer, reviewer, tester, planner, tidier, giter, jober, approver, leader, _mother, _baby_template

## Rule enforced
**explore() must always be called ALONE in a turn — never parallel with other tools.**
- Multiple explore() calls in same turn ✅
- explore() + any other tool in same turn ❌

## Key discovery
- `explore` is NOT directly in meta.json tools.allow — it's part of the `"knowledge"` tool category
- 11 agents have "knowledge" in tools.allow; 2 agents (explorer, experiencer) use "rag" tool directly instead
- Previous rule was weaker: "Do NOT run explore() in parallel with bash/file exploration" — only covered bash/file tools

## Review notes
- Core workflow content is identical across all 11 files
- Minor structural inconsistencies (experience() placement, --- separators) — pre-existing, not introduced by this change
- Commit: 1840bca
