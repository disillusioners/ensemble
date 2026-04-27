# Knowledge

## Knowledge Tools

You have access to two knowledge tools:

### `explore(query, mode?, project_id?)` — PRIMARY knowledge retrieval
**Always try explore() FIRST** when you need project knowledge, architecture info, or answers about a codebase.
- It queries the RAG knowledge base AND browses project files if needed
- It is faster and more comprehensive than manual file browsing
- It may also update the knowledge base asynchronously if it finds stale data
- **Do NOT run explore() in parallel with bash/file exploration.** Call explore(), evaluate the result, and ONLY use other tools if explore() was insufficient.

### `experience(text, project_id?)` — Record knowledge
Use to record project experience, architectural decisions, or learned knowledge into the RAG knowledge base.
- Returns immediately, processes in background


