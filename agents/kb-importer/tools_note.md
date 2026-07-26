# KB Importer Tools

## Primary Tool
- `rag_insert_text(text, file_source, category)` — Insert text into LightRAG knowledge base.
  - **You MUST provide `file_source`** — generate a path like `projects/<project>/docs/<category>/<name>.md`
  - `file_source` is NOT optional — always generate one yourself
  - `category` is a free-form label — use any descriptive lowercase tag (e.g., architecture, api, general, knowledge, experience, troubleshooting, decisions)
  - LightRAG auto-extracts entities and relationships from the text.

## Utility Tools
- `time()` — Get current time if needed.

## Forbidden Tools
Do NOT use: `rag_query`, `rag_query_data`, `rag_create_entity`, `rag_create_relation`, `rag_search_labels`, `explore`, `experience`, filesystem tools, or agent spawning tools.
