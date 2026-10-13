# Tool Usage Notes

- **`opencode-skill`** — Primary tool for code analysis and file operations
- **`council=True` on `external_opencode_send_message`** — Deep-Review mode. Pass `council=True` to enable the @council subagent hint trailer. **Max 1 council session per review.**
- **`Read`** — Quick file checks (prefer opencode for complex analysis)
- **`grep` / `ast_grep`** — Quick pattern searches
- **`glob`** — Quick file finding

### Deep-Review Session Example
```python
# Step 1: Initialize the session
external_opencode_init_session(
    project="myapp",
    session_name="review-deep",
    working_dir="/path/to/project",
)

# Step 2: Run Deep-Review (council mode + wait for completion)
external_opencode_send_message(
    project="myapp",
    session_name="review-deep",
    message="Deep-Review of payment module.\n"
            "Triggers: Business-Critical Logic, Data Integrity / Security.\n"
            "Focus: transaction atomicity, error recovery, edge cases in payment flow.\n"
            "Provide thorough analysis of correctness, safety, and architecture.",
    council=True,
)
external_opencode_wait_for_result(
    project="myapp",
    session_name="review-deep",
)
```
