# DLQ Retry Feature Review — 2026-04-18

## Project: agents-ensemble
## Branch: feature/dlq-retry @ 61c498e

### Key Patterns Observed
- Backend uses FastAPI with async endpoints but synchronous service layer (blocking DB calls in async handlers)
- DI pattern is inconsistent — some routers use lazy-init globals, others use FastAPI Depends
- Frontend uses Angular signals (signal(), computed(), input(), output()) correctly
- DLQ schemas are defined in dlq.py router instead of shared schemas.py module
- Exception messages leak internal details via str(e) in HTTP responses
- No authentication on any API endpoints (project-wide, not just this feature)

### Recurring Issues
1. **Blocking I/O in async handlers** — All DLQ endpoints are async but call sync service methods directly
2. **Session management** — SQLAlchemy objects returned from repositories may be detached when accessed later
3. **TOCTOU races** — Count-then-iterate pattern in bulk operations
4. **Schema duplication** — DLQ schemas in router instead of shared module
5. **No pagination limits** — Bulk replay-all has no cap on items processed
