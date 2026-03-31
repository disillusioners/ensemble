# Memory

## Workflow Patterns

- Planning workflow produces markdown-only deliverables (plans, roadmaps, analysis)
- Implementation workflow produces code changes — always follow with review + test for SMALL scope and above
- Sequential invocations are common: Planning first, then Implementation using the approved plan

## Scope Indicators

- "Fix", "Change", "Update" single thing → likely TINY or SMALL
- "Add", "Implement", "Build" feature → likely SMALL
- "Migrate", "Redesign", "Integrate" across modules → likely BIG
- "Rebuild", "Create from scratch", "Platform" → likely HUGE

- ## Yedda Store Status Management — Infrastructure Info

### Kubernetes + Telepresence
- Database runs on k8s, accessible via Telepresence
- Telepresence must be connected before testing

### Database Connection
- **Host:** psql-postgresql.postgres.svc.cluster.local:5432
- **Database:** ydstatus_service
- **User:** ydstatus_service
- **Password:** FOkDZ5aVJt9pUnvw
- **DSN (sqlx):** postgres://ydstatus_service:FOkDZ5aVJt9pUnvw@psql-postgresql.postgres.svc.cluster.local:5432/ydstatus_service?sslmode=disable

### Notes
- Always include this DSN in tester instructions for ydstatus project
- Telepresence must be active for live testing