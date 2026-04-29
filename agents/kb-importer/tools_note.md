# KB Importer Tools

## Primary Tool
- `rag_insert_text(text, file_source=None, category="general")` — Insert text into LightRAG knowledge base. LightRAG auto-extracts entities and relationships from the text.

## Utility Tools
- `rag_track_status(track_id)` — Check if an async insertion has completed.
- `time()` — Get current time if needed.

## Forbidden Tools
Do NOT use: `rag_query`, `rag_query_data`, `rag_create_entity`, `rag_create_relation`, `rag_search_labels`, `explore`, `experience`, filesystem tools, or agent spawning tools.
