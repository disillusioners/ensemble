# Phase 6 Review — Type Consistency

**Date**: 2026-04-23
**Commit**: 3999a39
**Result**: 🟡 Needs Work (1 critical)

## Key Finding
When converting `Optional[Callable[[...], RetType]]`, the `| None` must go OUTSIDE the entire `Callable[...]` expression, not inside the parameter list.

- ❌ Wrong: `Callable[[str, bool | None, None]]` — `None` absorbed as parameter type
- ✅ Correct: `Callable[[str, bool], None] | None` — `| None` at the top level

## Pattern to Watch
`Optional[Callable[[ParamTypes], ReturnType]]` → `Callable[[ParamTypes], ReturnType] | None`

The inner structure of Callable must remain untouched. Only the outer `Optional[...]` becomes `| None`.

## Stats
- 324 Optional conversions: 323 correct, 1 wrong
- 1 Union conversion: correct
- All import cleanups: correct
