# Explorer KB Heading Enforcement Fix — 2026-04-28

## What was done
Strengthened enforcement of `## Confidence:` and `## Need Update KB:` headings across all Explorer agent files, and cleaned up knowledge.md drift.

## Changes (Commit: 461d17d)
- **workflow.md**: Added prominent MANDATORY instruction in Step 5, restructured template so headings come first (before Answer/Sources), added concrete complete response example
- **rule.md**: Added Must rule + Immutable rule for mandatory headings
- **soul.md**: Added "Flag Knowledge Gaps" to What I Do, "Disciplined Formatter" to My Nature
- **knowledge.md**: Removed drifted content (synthesis steps, synthesis example, response format template, workflow reference), kept only pure reference knowledge (query modes, RAG result interpretation, confidence assessment)

## Key Insight
knowledge.md had drifted to include workflow/process content (response templates, synthesis instructions, workflow references) that belonged in workflow.md. The fix was to move that content and keep knowledge.md as pure reference data only.

## Lesson
When agent definition files drift, check for content that belongs in other files. The agent file structure has clear purposes: soul=identity, rules=constraints, workflow=process, knowledge=reference data, tools_note=tool docs.
