# Phase 5: Experiencer Agent Definition

## What Was Done
Created the complete Experiencer agent definition under `agents/experiencer/` — a lightweight agent specialized in extracting entities and relationships from text and inserting them into the RAG knowledge base.

## Files Created
- `meta.json` — Agent metadata with minimal toolset (rag, help, time)
- `soul.md` — Identity as knowledge architect and curator
- `rule.md` — Must/Must Not/Core Principles for insertion behavior
- `workflow.md` — 8-phase extraction workflow with decision tree and anti-patterns
- `tools_note.md` — RAG tool documentation with usage patterns
- `knowledge.md` — Domain expertise on entities, relationships, structuring

## Key Design Decisions
1. **Minimal toolset** — Only rag + help + time. No filesystem, bash, or instance tools.
2. **Deduplication-first** — Always search before creating entities
3. **Dual insertion strategy** — Structured (create_entity + create_relation) for explicit facts, unstructured (insert_text) for narrative content
4. **No recursion** — Never calls explore() or experience()
5. **Error tolerant** — Individual insertion failures don't stop batch processing

## Pattern Reference
Follows `agents/jober/` pattern exactly — same structure, formatting, and style conventions.

## Commit
`9a2a69c` — `feat: add experiencer agent definition for RAG knowledge extraction`
