# KB Writer Workflow

1. **Receive** — Read the knowledge text provided by the caller.
2. **Analyze** — Identify distinct domains/categories present in the text. Look for topic shifts, headers, or sections that cover different subject areas (e.g., architecture vs. configuration vs. bug patterns).
3. **Split** — If the text spans multiple unrelated domains, split it into segments so each segment belongs to one coherent domain. If the text is already focused on a single domain, keep it as a single segment.
4. **Insert** — For each segment, call:
   ```
   rag_insert_text(text=<segment>, category=<descriptive-category>)
   ```
   Choose a descriptive lowercase category label that fits the segment's domain (e.g., `architecture`, `bug-pattern`, `configuration`, `convention`, `general`).
5. **Report** — Summarize what was inserted: total number of insertions, the categories used, and any segments skipped or that failed.
