# Phase 3 Implementation Experience — Job Queue Management API + Frontend

### Key Learnings:
1. **queue_id filter propagation**: When adding a new filter parameter to a router, must ensure it flows through ALL layers: router → service → repository → SQL query. The review caught a critical bug where the router passed `queue_id` to service.list_jobs() but the service didn't accept it.

2. **ng-zorro-antd replacement**: The job-create-dialog was using `NzSelectModule` (ng-zorro). When replacing with Angular Material, use native `<select>` or `mat-select`. The constraint is clear: Angular Material ONLY.

3. **Signal-based project selection**: When a signal like `selectedProjectId` is needed in multiple places, prefer `computed()` deriving from existing state rather than duplicating signals. We derived it from `filters().project_id`.

4. **Queue name vs queue_id on UI**: Users see queue names, not UUIDs. Always maintain a lookup map (queue_id → queue_name) when displaying queue references in UI components.

### Architecture:
- Queue router follows the same dependency injection pattern as jobs router (module-level global + setter)
- IDOR protection returns 404 (never 403) to prevent information leakage
- Frontend components use signals throughout for reactive state management
- Queue sidebar is a separate component with input/output bindings for loose coupling

- Phase 1 refactoring plan read. Key constraint: use `from daemon.models import ...` NOT `from daemon.models.common import ...` for validate_agent_id (common module doesn't exist yet until Phase 2). Plan has detailed task breakdown for constants, utils, and validate_agent_id relocation.

- Comprehensive agents-ensemble architecture investigation completed. Key findings documented in 2026-04-23-architecture-report.md memory. Tool system uses CATEGORY_MODULES registry with 9 categories. Job queue has 7-state lifecycle with lock-first pattern. Agent system uses markdown files (meta.json, soul.md, rule.md, skill.md, etc.). Three event buses exist but agents cannot subscribe to events directly.
