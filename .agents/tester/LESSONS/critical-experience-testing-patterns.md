# Critical Notes Testing Lessons

## Date: 2026-05-20

### Tool Testing Pattern — Duck-Typed Store Mock
The `create_critical_notes_tools(store, ...)` factory uses duck-typed store:
- `store.get(project_id)` → returns project with `.critical_notes` attribute
- `store.update(project_id, critical_notes=[...])` → persists entries

**Key insight:** The `store.update()` side_effect must actually mutate the mock project's `critical_notes` list, otherwise subsequent tool calls see stale data:
```python
store.update.side_effect = lambda pid, **kwargs: setattr(project, 'critical_notes', kwargs.get('critical_notes', project.critical_notes))
```

### Merge Logic Testing — Unique Summaries
When testing multiple adds without merge, use unique summary words with < 2 shared keywords (> 3 chars). A helper with a list of 90 unique words prevents accidental keyword overlap:
```python
UNIQUE_WORDS = ["alpha", "bravo", "charlie", ...]
def unique_summary(idx): return f"{UNIQUE_WORDS[idx]} summary for testing {idx}"
```

### Eviction Testing — Time Ordering
Eviction sorts by `(priority_order, created_at)`. To test "oldest evicted first", entries need distinct timestamps. Using `time.sleep(0.01)` between adds ensures ordering without slowing tests.

### LangChain Tool Invocation
Tools created by `create_critical_notes_tools()` are LangChain `@tool` decorated functions. Invoke with `.invoke({"param": value})`, not direct function calls.

### JSON Column Testing
SQLite stores JSON as TEXT. The migration uses `TEXT DEFAULT '[]'`, and the Python layer handles serialization via SQLModel's JSON column type. No special test handling needed.
