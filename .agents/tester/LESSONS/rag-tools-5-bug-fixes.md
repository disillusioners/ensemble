# RAG Tools 5 Bug Fixes — Lessons

## Date: 2026-05-04
## Branch: fix/rag-tools-5-bugs

### Quick Fix: Missing rag_get_entity unit tests
- **Commit**: `98ce3cb`
- **Issue**: New `rag_get_entity` tool was added but had no dedicated unit tests
- **Fix**: Added `TestRAGGetEntity` class (2 tests: success + not_configured), added mock, fixed tool count 15→16
- **Lesson**: When adding new tools, always add corresponding unit tests immediately

### Bug Fix Validation Pattern
- All 5 bugs were code-level fixes (endpoint URLs, parameter forwarding, docs accuracy)
- Validation approach: code inspection chain (tool → client → endpoint → mock) + unit test execution
- This pattern works well for verifying API-layer bug fixes without running a live server
