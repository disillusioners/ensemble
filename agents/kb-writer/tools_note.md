# KB Writer Tools

## Primary Tool: `rag_insert_text`

Signature:
```
rag_insert_text(text: str, file_source: str | None = None, category: str = "general")
```

Arguments:
- **`text`** *(required)* — The knowledge text content to insert into RAG.
- **`file_source`** *(optional)* — A source identifier / path string. Can be `None` for most cases; the system will generate a fallback if omitted. Generally you can omit this.
- **`category`** *(optional, default `"general"`)* — A descriptive lowercase label for the segment's domain. Use meaningful names such as `architecture`, `bug-pattern`, `configuration`, `convention`, `api`, `troubleshooting`, `decisions`, or `general`.

Returns:
- A `track_id` that can be used to check the asynchronous insertion status.

## Usage Notes
- Call `rag_insert_text` **once per distinct segment/category**. Do not call it many times with the same category on the same content.
- Pick the most specific descriptive category that fits the segment; fall back to `general` only when nothing better applies.
- `file_source` can usually be left as `None` for this agent's use case — the caller typically just wants the knowledge persisted, not a specific source path attached.

## Utility Tools
- `help()` — Get assistance / tool documentation.
- `time()` — Get the current time if needed for logging or timestamps.

## Forbidden Tools (NOT available — do not attempt)
- `rag_query`, `rag_query_data` — retrieval is out of scope.
- `rag_create_entity`, `rag_create_relation`, `rag_search_labels`, `rag_delete_entity` — graph manipulation is out of scope.
- `explore`, `experience` — no recursive agent calls.
- Filesystem tools, bash, agent-spawning tools — none of these are available.
