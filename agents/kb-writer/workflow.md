# KB Writer Workflow

1. **Receive** — Read the knowledge text provided by the caller.
2. **Analyze** — Identify distinct domains/categories present in the text. Look for topic shifts, headers, or sections that cover different subject areas (e.g., architecture vs. configuration vs. bug patterns).
3. **Split** — If the text spans multiple unrelated domains, split it into segments so each segment belongs to one coherent domain. If the text is already focused on a single domain, keep it as a single segment.
4. **Insert** — For each segment, call:
   ```
   rag_insert_text(text=<segment>, file_source=<descriptive-path>, category=<descriptive-category>)
   ```
   Choose a descriptive lowercase category label that fits the segment's domain (e.g., `architecture`, `bug-pattern`, `configuration`, `convention`, `general`).

   For `file_source`, ALWAYS generate a descriptive path of the form `projects/<project>/knowledge/<category>/<descriptive-name>.md` (e.g., `projects/my-project/knowledge/architecture/event-bus-patterns.md`). This avoids the "no file_source provided" warning and keeps each segment traceable to its origin. Omitting `file_source` is tolerated by the tool but discouraged.
5. **Report** — Summarize the segments submitted for insertion: total number of submissions, the categories used, the `track_id` returned for each call, and any segments skipped or that failed. Note: `rag_insert_text` is asynchronous — a `track_id` confirms submission, not completion.
