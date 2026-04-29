# KB Importer Rules

## MUST
- Provide a meaningful `file_source` when calling `rag_insert_text` — format: `projects/<project-name>/docs/<category>/<descriptive-name>.md`
- Choose an appropriate `category` based on content: general, architecture, api, knowledge, experience
- Format the text into a clean, well-structured document before inserting (add headers, organize sections if needed)
- Report what was imported after each insertion

## MUST NOT
- Use `rag_create_entity`, `rag_create_relation`, `rag_search_labels` — LightRAG handles extraction automatically
- Use `rag_query`, `rag_query_data` — no retrieval, only insertion
- Call `explore()` or `experience()` — no recursive tool calls
- Access filesystem or spawn agents

## Notes
- If text is very long, split into multiple `rag_insert_text` calls with appropriate file_sources
- Keep it simple — prepare and import, nothing more
