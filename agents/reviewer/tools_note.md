# Tool Usage Notes

- **opencode_skill** — Primary tool for code analysis and file operations
- **opencode_skill `--council`** — Deep-Review mode. Use for auto-detected high-risk targets. **Max 1 session per review.**
- **Read** — Quick file checks (prefer opencode for complex analysis)
- **grep/ast_grep** — Quick pattern searches
- **glob** — Quick file finding

### Deep-Review Session Example
```bash
# Step 1: Initialize the session
opencode_skill init-session myapp review-deep /path/to/project

# Step 2: Run Deep-Review (sync + council)
opencode_skill --council --sync myapp review-deep "Deep-Review of payment module.
Triggers: Business-Critical Logic, Data Integrity / Security.
Focus: transaction atomicity, error recovery, edge cases in payment flow.
Provide thorough analysis of correctness, safety, and architecture."
```

 **`--council` is a flag — place it before positional arguments, like `--sync` and `--quiet`.