# KB Writer Workflow

This workflow runs **exactly once per incoming knowledge text**. When you reach the final step (`STOP`), your turn is over — do not loop back, do not re-analyze, do not call the tool again.

## Steps

1. **Receive** — Read the knowledge text provided by the caller. Treat it as a single, fixed input that you will not re-process.
2. **Analyze** — Identify distinct domains/categories present in the text. Look for topic shifts, headers, or sections that cover different subject areas (e.g., architecture vs. configuration vs. bug patterns).
3. **Split** — If the text spans multiple unrelated domains, split it into segments so each segment belongs to one coherent domain. If the text is already focused on a single domain, keep it as a single segment.
4. **Insert** — For **each segment, call `rag_insert_text` exactly once**:
   ```
   rag_insert_text(text=<segment>, file_source=<descriptive-path>, category=<descriptive-category>)
   ```
   Choose a descriptive lowercase category label that fits the segment's domain (e.g., `architecture`, `bug-pattern`, `configuration`, `convention`, `general`).

   For `file_source`, ALWAYS generate a descriptive path of the form `projects/<project>/knowledge/<category>/<descriptive-name>.md` (e.g., `projects/my-project/knowledge/architecture/event-bus-patterns.md`). This avoids the "no file_source provided" warning and keeps each segment traceable to its origin. Omitting `file_source` is tolerated by the tool but discouraged.
5. **Report** — Summarize the segments submitted for insertion: total number of submissions, the categories used, the `track_id` returned for each call, and any segments skipped or that failed.

   About `track_id`: it is **informational only**. You have NO tool to query, verify, or act on a `track_id`. Do not attempt to "check its status," "confirm completion," or use it in any subsequent call. Just include it in the report for the caller's reference, then move on. See step 6.
6. **STOP** — After the report, your task is **COMPLETE**. Your single permitted turn ends here.

   - Do NOT re-analyze the input text.
   - Do NOT call `rag_insert_text` again — not for the same segment, not for "verification," not because you "want to be thorough."
   - Do NOT loop back to step 1. The workflow runs once and terminates.
   - A returned `track_id` (or a 409 conflict on a duplicate) is the signal that work for that segment is done. Stop immediately.

If `rag_insert_text` returns an error (e.g., a 409 conflict meaning the document already exists / is processing), record it as "skipped — already present" in the report and **do not retry**. Move to the next segment if any remain, otherwise go to step 6 (`STOP`).
