# Workflow

1. **Receive** — Agent provides content + intent ("remember", "learn", "change")
2. **Classify** — Determine action type and target file
3. **Validate** — Check against growth.md rules
4. **Route**:
   - Memory event → `memories/YYYYMMDD_HHMM_description.md`
   - Core memory → `memory.md` (if important enough)
   - Workflow change → Propose after 3+ patterns
   - Soul change → Request user approval
5. **Apply** — Execute the change
6. **Confirm** — Tell agent what was done
