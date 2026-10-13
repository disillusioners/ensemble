# Memory

## What I Check

### Coding Style
- Naming conventions (snake_case, PascalCase, etc.)
- Formatting consistency
- Readability and clarity
- Project-specific standards

### Code Smells
- Duplicate code / copy-paste logic
- Overly long functions (>50 lines)
- Overly long function names
- Repeated logic across multiple places
- Dead code or unused variables
- Functions/classes doing too many things (SRP violations)
- Magic numbers and strings

### Structure & Design (when justified)
- Poor modularization
- Lack of clear interfaces
- Missing or improper dependency injection
- Misuse or absence of design patterns
- Overly complex logic that could be simplified

### Common Pitfalls
- Unhandled exceptions
- Missing null checks
- N+1 queries
- Race conditions in async code
- Resource leaks (unclosed files, connections)

### Language Traps
- **Python**: mutable defaults, closure over loop, `==` vs `is`
- **JS/TS**: `==` vs `===`, async errors, prototype pollution
- **SQL**: string concat, missing transactions, dirty reads
- **General**: premature optimization, over-engineering, YAGNI

### Severity Guidelines

| Issue Type | Typical Severity |
|------------|------------------|
| Security vulnerability | 🔴 High |
| Data loss risk | 🔴 High |
| Breaking SRP / massive function | 🔴 High |
| Duplicate logic (3+ places) | 🔴 High |
| Dead code | 🟡 Medium |
| Suboptimal pattern | 🟡 Medium |
| Naming inconsistency | 🟡 Medium |
| Style preference | 🟢 Low |
| Refactor opportunity | 🟢 Low |

---

## Project-Specific Standards

Before each review, check `.agents/tidier/rules/` for project-specific standards and conventions. These override all global guidelines.
