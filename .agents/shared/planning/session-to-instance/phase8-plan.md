# Phase 8: Frontend — TypeScript Models, API Service, Components, Pages

## Objective
Rename all session references in the Angular frontend: TypeScript type definitions, API service methods, component names, component directory, page components, and SSE service.

## Context
- **Phase 6 completed**: Backend API routes changed from `/sessions` to `/instances`
- Frontend must match the new API contract exactly
- This phase ensures the UI builds and communicates with the renamed backend

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | **Update frontend/src/app/models/index.ts** | Rename types: `SessionInfo`→`InstanceInfo`, `SessionListResponse`→`InstanceListResponse`, `SessionStatus`→`InstanceStatus`, `SessionMappingCreate`→`InstanceMappingCreate`, `SessionMappingInfo`→`InstanceMappingInfo`, `SessionMappingListResponse`→`InstanceListResponse`. Rename fields: `session_id`→`instance_id`, `agent_session_id`→`agent_instance_id`, `sessions[]`→`instances[]` in list response. | `frontend/src/app/models/index.ts` |
| 2 | **Update frontend/src/app/api.service.ts** | Rename methods: `createSession`→`createInstance`, `listSessions`→`listInstances`, `getSession`→`getInstance`, `deleteSession`→`deleteInstance`. Update URL paths: `/sessions`→`/instances`. Update type references to new model names. ~9 method renames. | `frontend/src/app/api.service.ts` |
| 3 | **Rename component directory** `session-list/` → `instance-list/` | Use `git mv frontend/src/app/components/session-list frontend/src/app/components/instance-list`. | Directory rename |
| 4 | **Update instance-list component files** | In all 3 files (.ts, .html, .scss): `SessionTreeNode`→`InstanceTreeNode`, `SessionListComponent`→`InstanceListComponent`, `session-list`→`instance-list` selector, all `session_id`→`instance_id`, all `SessionInfo`→`InstanceInfo`. Update HTML template references. | `frontend/src/app/components/instance-list/` |
| 5 | **Update frontend/src/app/pages/chat/chat.component.ts** | ~85 session references. Rename: `currentSession`→`currentInstance`, `currentSessionId`→`currentInstanceId`, `session_id`→`instance_id`, route params, SSE event handling. Update routerLink references from `/sessions/` → `/instances/`. | `frontend/src/app/pages/chat/chat.component.ts` (~479 lines) |
| 6 | **Update chat.component.html** | Update template references: any `session` variable names in template bindings, routing paths. | `frontend/src/app/pages/chat/chat.component.html` |
| 7 | **Update frontend/src/app/services/sse.service.ts** | ~56 session references. Rename: `currentSessionId`→`currentInstanceId`, `isValidSessionEvent`→`isValidInstanceEvent`, `session_id`→`instance_id` in SSE type definitions. Update event payload parsing. | `frontend/src/app/services/sse.service.ts` |
| 8 | **Update app module/routing** | Update any module declarations: `SessionListComponent`→`InstanceListComponent`, import paths from `session-list`→`instance-list`. Update route definitions: `/sessions` → `/instances`. | `frontend/src/app/app.module.ts` or `app.routes.ts` |
| 9 | **Update any other components** | Search for remaining `session` references in other frontend files: navbar, sidebar, other pages. Use `grep -rn "session" frontend/src/` to find all. | Various frontend files |
| 10 | **Update Angular-specific references** | Check `app.component.ts`, any guards, interceptors, or resolvers that reference session routes/params. | Various |

## Key Files
- `frontend/src/app/models/index.ts` — TypeScript type definitions
- `frontend/src/app/api.service.ts` — HTTP API client
- `frontend/src/app/components/session-list/` → `instance-list/` — Component directory
- `frontend/src/app/pages/chat/chat.component.ts` — Chat page (~479 lines, ~85 references)
- `frontend/src/app/pages/chat/chat.component.html` — Chat template
- `frontend/src/app/services/sse.service.ts` — SSE client (~56 references)
- `frontend/src/app/app.module.ts` or routing config — Module declarations & routes

## Detailed Rename Map

### TypeScript Types (models/index.ts)
| Old | New |
|-----|-----|
| `SessionStatus` | `InstanceStatus` |
| `SessionInfo` | `InstanceInfo` |
| `SessionListResponse` | `InstanceListResponse` |
| `SessionMappingCreate` | `InstanceMappingCreate` |
| `SessionMappingInfo` | `InstanceMappingInfo` |
| `SessionMappingListResponse` | `InstanceMappingListResponse` |
| `session_id` field | `instance_id` field |
| `agent_session_id` field | `agent_instance_id` field |
| `sessions` array field | `instances` array field |

### API Service Methods
| Old Method | New Method | URL Change |
|------------|------------|------------|
| `createSession()` | `createInstance()` | `POST /instances` |
| `listSessions()` | `listInstances()` | `GET /instances` |
| `getSession()` | `getInstance()` | `GET /instances/:id` |
| `deleteSession()` | `deleteInstance()` | `DELETE /instances/:id` |

### Component Renames
| Old | New |
|-----|-----|
| `SessionListComponent` | `InstanceListComponent` |
| `SessionTreeNode` | `InstanceTreeNode` |
| `session-list` (selector) | `instance-list` (selector) |
| `<app-session-list>` | `<app-instance-list>` |

### Route Changes
| Old Path | New Path |
|----------|----------|
| `/sessions` | `/instances` |
| `/sessions/:sessionId` | `/instances/:instanceId` |

## Constraints
- Angular requires component selectors to match module declarations — update both
- Routing changes must be consistent between route definitions and `routerLink` directives
- SSE event format must match what the backend sends (Phase 6)
- Template HTML bindings must match renamed component properties

## Verification
```bash
# 1. Old component directory removed
ls frontend/src/app/components/session-list/  # should fail
ls frontend/src/app/components/instance-list/  # should exist

# 2. No old type names in models
grep -rn "SessionInfo\|SessionStatus\|SessionListResponse\|SessionMapping" frontend/src/app/models/

# 3. No old API methods
grep -rn "createSession\|listSessions\|getSession\|deleteSession" frontend/src/app/api.service.ts

# 4. No old route paths
grep -rn '"/sessions' frontend/src/

# 5. Frontend builds
cd frontend && npm run build  # or ng build

# 6. No old session references (excluding unrelated concepts)
grep -rn "session_id\|sessionId\|currentSession" frontend/src/ | grep -v "node_modules"
```

## Deliverables
- [ ] All TypeScript types renamed in models/index.ts
- [ ] API service methods and URLs updated
- [ ] Component directory renamed: `session-list/` → `instance-list/`
- [ ] Component class, selector, template updated
- [ ] Chat component (~85 references) fully updated
- [ ] SSE service (~56 references) fully updated
- [ ] Routing config updated
- [ ] `ng build` succeeds without errors
- [ ] Grep shows 0 old session names in frontend/src/
