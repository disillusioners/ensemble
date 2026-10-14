# Phase 2: Jober Agent Definition — Implementation Notes

## Date: 2026-04-24

## What Was Done
Created complete jober agent definition in `agents/jober/` with 6 files:
- meta.json — Tool filter (job, instance, self, help, time, project), no bash/filesystem
- soul.md — Identity as job orchestrator, "delegate everything" philosophy
- rule.md — Must/Must Not with critical rules (never execute, always watch)
- skill.md — 6 orchestration patterns + decision framework + notification format
- workflow.md — 5-phase methodology + batch/error/status variants
- tools_note.md — Comprehensive tool usage guide with all job tools

## Key Patterns
- Jober follows leader pattern: meta.json with allow list, no deny list
- Tool filter exclusion works by ABSENCE from allow list (not explicit deny)
- `job_create(watch=True)` — atomic watch registration before dispatch
- Notification format: `[JOB_EVENT]` header + JSON block for reliable parsing
- Source format: `internal_agent:job_event:{job_id}:{status}` → MessageType.AGENT

## Review Lessons
- Initial review caught missing tools in rule.md ALLOWED section (unwatch_job, queue_update, etc.)
- Cross-file consistency matters: tool names must match across rule.md, skill.md, workflow.md, tools_note.md
- Status capitalization: ALL_CAPS for emphasis in tables, lowercase in JSON/tool parameters
- Every tool in rule.md's ALLOWED list should have documentation in tools_note.md
- Commit: 461d5ca (1175 insertions)
