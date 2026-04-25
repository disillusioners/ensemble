# 2026-04-24-plan-review-deep-review-pattern.md

## Pattern: When to Use Deep-Review for Plans

Deep-Review is worth using when the plan involves:
1. Cross-cutting changes (shared code, registry, loaders)
2. Architecture/structural refactoring
3. Multi-file coordinated changes with dependencies
4. Changes where bugs would affect ALL agents/sessions

## What Worked

- Packed ALL context into one comprehensive prompt
- Cross-referenced plan documents against actual source code
- Found bugs that would only be visible when comparing plan vs implementation

## Key Lessons

### Plans can have "pseudocode bugs" — code in the plan that won't work
The `find_skill()` pseudocode used `Path("agents")` which is relative to CWD. The actual code should use `self._agents_dir`. Plans need implementation-accurate pseudocode, not just description.

### Dependency diagrams can miss edges
Phase 3 appeared to depend only on Phase 1, but also depends on Phase 2 (for the meta.json field). The graph was incomplete.

### Caller availability must be verified
The plan assumed `meta` was available at `load_and_cache_prompt()` but the caller doesn't read meta.json. Need to verify what variables are in scope at call sites.

### "No callers" can be verified
grep confirmed `find_skill()` has zero production callers — only test mocks. This means refactoring it has no production impact. Good news that the council correctly assessed.

### Registry safety by design
The `discover()` method skips directories without `meta.json`. This is a built-in safeguard that protects against "fake agent" discovery of the new `innate-skills/` directory.

## Verification Approach

For plan reviews, always:
1. Read the actual source code (not just plan descriptions of it)
2. Verify line numbers and function signatures
3. grep for all references to affected functions/files
4. Check what values are in scope at call sites
5. Verify dependency graphs completeness
