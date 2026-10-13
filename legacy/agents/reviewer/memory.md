# Memory

## 🔴 Deep-Review Trigger Checklist

Auto-detect these patterns in the review target. If **any** match, activate Deep-Review mode.

### 1. Data Integrity / Security
Anything that could lose data, corrupt state, or break security.
- Authentication & authorization logic
- Encryption/decryption implementations
- Transaction handling (ACID guarantees)
- Data migration scripts
- Input validation boundaries
- Secrets/credentials management
- Database schema changes (ALTER, DROP)
- File system write operations (bulk)

### 2. Cross-Cutting Changes
Changes to shared interfaces, contracts, or system-wide patterns.
- API contract changes (endpoints, request/response schemas)
- Event/message schema changes
- Shared library/module updates
- Configuration format changes
- Database migrations affecting multiple tables
- Interface/protocol changes
- Dependency version upgrades (major)
- Build/pipeline configuration changes

### 3. Complex Concurrency / State
Multi-step logic with state machines, race conditions, or distributed coordination.
- State machine transitions
- Lock/semaphore/mutex logic
- Distributed coordination (consensus, leader election)
- Error recovery & retry logic
- Queue/worker implementations
- Caching invalidation strategies
- WebSocket/real-time connection handling
- Background job scheduling

### 4. Business-Critical Logic
Core business rules where errors have real-world consequences.
- Payment processing & billing
- User permissions & access control
- Data transformation pipelines
- Notification/delivery systems
- Rate limiting & quota enforcement
- Accounting/financial calculations
- Workflow/engine orchestration
- Compliance/regulatory logic

### 5. Architecture / Workflow Changes
Structural changes that reshape the system.
- New agent type or agent behavior changes
- Message routing changes
- Persistence layer changes
- Deployment/infrastructure changes
- Core library/framework upgrades

### Trigger Decision
- **1 trigger match** → Deep-Review
- **Multiple trigger matches** → Deep-Review (note all triggered categories in plan)
- **No trigger matches** → Standard Review
- **User explicitly requests** → Always honor (either activate or skip)

---

## What I Check

### Architecture & Plans
- Requirements completeness
- Component boundaries respected
- Dependencies flow correctly
- No circular dependencies
- Scalability considerations
- Trade-offs justified

### Code Quality
- Naming conventions
- Function/variable scope
- Error handling
- Resource cleanup
- Type safety
- DRY, SRP principles

### Common Pitfalls
- Race conditions
- Memory leaks
- SQL injection, XSS
- Hardcoded secrets
- Unhandled exceptions
- N+1 queries
- Missing null checks
- Deadlocks

### Language Traps
- **Python**: mutable defaults, closure over loop, `==` vs `is`
- **JS/TS**: `==` vs `===`, async errors, prototype pollution
- **SQL**: string concat, missing transactions, dirty reads
- **General**: premature optimization, over-engineering, YAGNI

### Severity Guidelines
| Issue Type | Typical Severity |
|------------|------------------|
| Security vulnerability | 🔴 Critical |
| Breaks architecture | 🔴 Critical |
| Data loss risk | 🔴 Critical |
| Memory/thread leak | 🔴 Critical |
| Bad practice | 🟡 Warning |
| Suboptimal pattern | 🟡 Warning |
| Style preference | 🟢 Suggestion |
| Refactor opportunity | 🟢 Suggestion |

---

## Project-Specific Standards

Before each review, check `.agents/reviewer/memory.md` for project-specific standards and conventions.
