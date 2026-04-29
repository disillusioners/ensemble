# KB Importer Workflow

1. **Receive** — Read the input text content
2. **Prepare** — Format into a clean document. Add descriptive title/header. Determine category.
3. **Import** — Call `rag_insert_text(text, file_source=<descriptive-path>, category=<chosen-category>)`
4. **Confirm** — Report what was imported (file_source, category, approximate size)
