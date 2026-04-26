# Knowledge

## Knowledge Tools

Use `explore(query)` to query project knowledge and `experience(text)` to record new knowledge.

- **explore(query)** — Search the RAG knowledge base for project-specific knowledge. Use this to recall past experiences, architectural decisions, and technical details about the current project.
- **experience(text)** — Record new knowledge about the current project to the RAG knowledge base. Use this when you learn something worth remembering for future sessions.

These tools replace the old file-based memory system (`.agents/<agent-id>/memories/`). Project knowledge is now stored centrally in the RAG knowledge base where it can be queried and cross-referenced.
