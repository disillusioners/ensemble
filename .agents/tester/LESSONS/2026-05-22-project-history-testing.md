# Project History Testing Patterns

## Date: 2026-05-22

## Feature: project_history
End-to-end feature for recording structured history entries about project changes.

## Testing Approach

### Tool Testing Pattern
- Mock the repository layer (`AsyncSession`, repository methods)
- Test tool logic: validation, truncation, error handling
- Use `.invoke({"param": value})` pattern for tool calls
- 4 tools: add, list, search, delete — each tested independently

### Key Test Patterns Discovered

#### Truncation Testing
- Summary truncates at 300 chars
- Details truncates at 5000 chars
- Test with exact boundary values and boundary+1

#### Special Characters
- LIKE wildcards (%, _) must be handled in search
- Quotes, unicode chars in summary/details
- These test both tool input handling and repository query safety

#### Injection Testing
- `format_project_context()` renders history entries with emoji icons
- All 8 `HistoryEntryType` values have emojis: 🏆📦🔀🐛🚀📝⚙️❓
- Unknown types fall back to ❓
- Section header: `### 📜 Recent History`
- Entry format: `- {emoji} **[{type}]** {summary} — _{relative_time}_`
- Limit: 10 recent entries, ordered by most recent first
- Both CE and history sections can coexist

### Test File Structure
```
tests/
├── test_project_history.py              # Repository tests (27)
├── test_project_history_api.py          # API endpoint tests (26)
├── test_project_history_functions.py    # Integration tests (33)
├── unit/
│   ├── test_project_history_tools.py    # Agent tool tests (38)
│   └── test_project_history_injection.py # Context injection tests (28)
```

Total: 152 tests covering all layers (model → repository → API → tools → injection).
