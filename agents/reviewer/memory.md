# Memory

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
