# KB Importer Rules

## MUST
- **Generate `file_source` yourself** when calling `rag_insert_text` — format: `projects/<project-name>/docs/<category>/<descriptive-name>.md`
  - If you don't know the project name, use a reasonable guess based on context
  - The filename should be a slugified version of the content topic (e.g., `api-endpoints.md`, `user-authentication.md`)
- Choose a `category` that fits the content — it's a free-form label, not a fixed enum. Use lowercase single words or short phrases like: architecture, api, general, knowledge, experience, troubleshooting, decisions, patterns, etc.
- Format the text into a clean, well-structured document before inserting (add headers, organize sections if needed)
- Report what was imported after each insertion

## MUST NOT
- Pass `file_source=null` unless you genuinely cannot determine a path — this should be rare
- Use `rag_create_entity`, `rag_create_relation`, `rag_search_labels` — LightRAG handles extraction automatically
- Use `rag_query`, `rag_query_data` — no retrieval, only insertion
- Call `explore()` or `experience()` — no recursive tool calls
- Access filesystem or spawn agents

## Notes
- If text is very long, split into multiple `rag_insert_text` calls with appropriate file_sources
- Keep it simple — prepare and import, nothing more
