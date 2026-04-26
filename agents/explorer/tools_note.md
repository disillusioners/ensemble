# Tools Reference

Guide to the tools available to Explorer.

---

## RAG Tools (Primary)

These are your main tools for knowledge retrieval.

### rag_query(query, mode)

**Main query tool.** Get a generated response from the knowledge graph.

| Mode | When to Use | Behavior |
|------|-------------|----------|
| `local` | Specific entities ("what is X?", "how does Y work?") | Extracts subgraph around matching entities |
| `global` | Broad topics ("what is the overall architecture?") | Uses community summaries across graph |
| `hybrid` | Default for most queries | Combines local + global for comprehensive answers |
| `naive` | Simple keyword fallback | Basic text matching, no graph traversal |
| `mix` | When you need everything | All modes combined, slowest but most thorough |

**Tip:** Start with `hybrid`, use `local` for specific entity questions.

### rag_query_data(query, mode)

Get **structured entities and relations** from the knowledge graph. Use when you need:
- Specific entity details
- Relationship mappings between entities
- Structured data for further processing

### rag_search_labels(label, max_results)

Search for **labels in the graph**. Good for discovering what entities exist before querying. Returns matching labels and their counts.

**Example:** Search for "function" to see what functions are indexed.

### rag_get_graph(label, max_depth, max_nodes)

Get a **subgraph** around a specific entity. Use sparingly:
- Primarily for relationship exploration
- Can be expensive — limit `max_depth` and `max_nodes`
- Good for understanding entity connections

### rag_insert_text(text, description, file_paths)

**Async upsert tool.** Insert new text into the knowledge base.

**When to use:**
- After file browsing reveals information not in RAG
- Fire-and-forget — don't wait for confirmation

**Tip:** Always include `file_paths` for traceability back to source files.

---

## Filesystem Tools (Fallback)

Used when RAG confidence is MEDIUM or LOW.

### read_file(path)

Read a specific file's contents. Use when you know the file path from the query or RAG results.

### list_directory(path)

List directory contents. Use to explore structure when you don't know specific files.

### glob_files(pattern, path)

Find files matching a glob pattern.

**Example:** `glob_files("**/*.py", "/project")` to find Python files.

### grep_files(pattern, path)

Search file contents with regex. Use for finding specific patterns or code.

---

## Help Tools

### tool_help(tool_name)

Get detailed help for any tool. Use for self-discovery or when unsure about a tool.

---

## Time Tools

### time(timezone_str, format_type)

Get current time. Useful for timestamps in responses if needed.

---

## CRITICAL: NEVER USE

| Tool | Reason |
|------|--------|
| `explore()` | Would cause recursion — Explorer cannot call itself |
| `experience()` | Would cause recursion |
| `bash` | Not available — read-only agent |

---

## Tool Usage Tips

1. **RAG first, files second** — Always try RAG before filesystem
2. **Limit tool calls** — Max 2-3 calls before returning
3. **Confidence is key** — HIGH confidence = return immediately
4. **Async upsert** — Don't wait for rag_insert_text confirmation
