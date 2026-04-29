# KB Importer Workflow

1. **Receive** — Read the input text content
2. **Prepare** — Format into a clean document. Add descriptive title/header.
3. **Choose category** — Pick a descriptive lowercase tag (architecture, api, general, knowledge, experience, troubleshooting, decisions, etc.). Category is free-form.
4. **Generate file_source** — Create a path like `projects/<project-name>/docs/<category>/<topic>.md`
5. **Import** — Call `rag_insert_text(text, file_source=<generated-path>, category=<chosen-category>)`
6. **Confirm** — Report what was imported (file_source, category, approximate size)
