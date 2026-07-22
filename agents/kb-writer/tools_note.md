# KB Writer Tools

## Primary Tool: `rag_insert_text`

Signature:
```
rag_insert_text(text: str, file_source: str | None = None, category: str = "general")
```

Arguments:
- **`text`** *(required)* — The knowledge text content to insert into RAG.
- **`file_source`** *(optional, but SHOULD be provided)* — A descriptive source identifier / path string following the pattern `projects/<project>/knowledge/<category>/<descriptive-name>.md`. The tool is technically tolerant of `None` (it logs a warning and generates a fallback), but you SHOULD always supply a descriptive `file_source` so the knowledge can be traced back to its origin. Never omit `file_source` without good reason.
- **`category`** *(optional, default `"general"`)* — A descriptive lowercase label for the segment's domain. Use meaningful names such as `architecture`, `bug-pattern`, `configuration`, `convention`, `api`, `troubleshooting`, `decisions`, or `general`.

Returns:
- A `track_id`. **This is informational only.** It is meant for the caller (Leader/Explorer/Librarian) that dispatched you — NOT for you to act on.

### About `track_id` — IMPORTANT, READ CAREFULLY
- You **have no tool** to query, poll, resolve, or verify a `track_id`. It is not an input to any of your tools.
- Do **not** attempt to "check the status," "confirm completion," or "use the track_id in a follow-up call." There is no follow-up call.
- The `track_id` confirms **submission**, not processing completion. Processing is asynchronous and handled by LightRAG — it is out of your scope (you are write-only).
- Correct usage: receive the `track_id` → echo it in your report → STOP. Nothing else.
- Incorrect usage (do NOT do this): calling `rag_insert_text` again "to verify," "to make sure," or "because you got a track_id and want to act on it."

## Usage Rules
- Call `rag_insert_text` **exactly once per distinct segment/category**. Even if the same text and the same category would match multiple "passes," you submit one time and then stop. There is no second pass.
- If `rag_insert_text` returns a `track_id`, that segment is **DONE for this session**. Never re-submit it.
- If `rag_insert_text` returns an error (e.g., a 409 conflict — document already exists / still processing), that segment is **also DONE** — record it as "skipped" and move on. Never retry.
- Pick the most specific descriptive category that fits the segment; fall back to `general` only when nothing better applies.
- Always generate a descriptive `file_source` for each insertion — e.g., `projects/my-project/knowledge/architecture/event-bus-patterns.md`. This avoids the "no file_source provided" warning and keeps the knowledge traceable.

## Utility Tools
- `tool_help()` — Get assistance / tool documentation.
- `time()` — Get the current time if needed for logging or timestamps.

## Forbidden Tools (NOT available — do not attempt)
- `rag_query`, `rag_query_data` — retrieval is out of scope.
- `rag_create_entity`, `rag_create_relation`, `rag_search_labels`, `rag_delete_entity` — graph manipulation is out of scope.
- `explore`, `experience` — no recursive agent calls.
- Filesystem tools, bash, agent-spawning tools — none of these are available.
- Any hypothetical "track status" / "poll track_id" tool — **does not exist**. Do not invent one.

## Why This Restricted Tool Set?

`kb-writer` is intentionally granted only `rag_insert_text` (and the `tool_help` / `time` utilities), **not** the full `rag` category. The `rag` category contains ~16 graph-manipulation tools (`rag_query`, `rag_create_entity`, `rag_create_relation`, etc.), which would silently grant kb-writer retrieval and graph-mutation abilities — directly violating its write-only contract. Future maintainers: do **not** widen this agent's `tools.allow` to `"rag"` without an explicit design review.
