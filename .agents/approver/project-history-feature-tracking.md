# Plan Improvement Tracking: Project History Feature

## Iteration 001 — 2026-05-22 04:24

**Verdict: REJECTED**

### Blocking Issues

#### 1. Repository methods use `self.session` — actual codebase uses `Session(self.engine)` context manager
- **Plan says** (phase1-plan.md, lines 99-187): All repository methods use `self.session.add()`, `self.session.commit()`, `self.session.exec()`, etc.
- **Actual codebase**: `SQLModelProjectRepository` uses `with Session(self.engine) as session:` inside each method — session is a local variable, NOT an instance attribute (`self.session` does NOT exist).
- **Impact**: Every proposed repository method would fail with `AttributeError: 'SQLModelProjectRepository' object has no attribute 'session'`.
- **Fix required**: Rewrite all 6 repository methods to use `with Session(self.engine) as session:` pattern, with `session` as a local variable.

#### 2. Migration sequence number collision
- **Plan says** (phase1-plan.md, line 35): Migration filename `20260521_000001_add_project_history_table.sql`
- **Actual codebase**: `20260520_000001_add_critical_experience_to_projects.sql` already uses sequence `000001` for the `20260520` date prefix. The sequence resets per-date-prefix, so `20260521_000001` is technically valid for a new date.
- **Impact**: LOW — the naming is actually valid (sequence resets per date). However, the plan should confirm this follows the convention unambiguously.
- **Fix required**: Verify naming convention. If sequences are global (not per-date), must use `000002`.

### Notes (Non-blocking)
- Phase 3 approach of adding `store` parameter to `format_project_context()` is viable but unconventional — the council noted the project object is pre-loaded by callers. However, since history is a separate table not embedded in the project object, passing store is reasonable.
- Phase 4 correctly identifies existing schemas and router patterns.
- Tool layer (Phase 2) follows the correct factory pattern.

## Iteration 002 — 2026-05-22 04:36

**Verdict: APPROVED**

### Previous Issues (Iteration 001) — Resolution Status
1. **Repository methods using `self.session`** — ✅ FIXED. Phase 1 implementation notes now correctly use `with Session(self.engine) as session:` pattern throughout all 6 methods.
2. **Migration sequence number collision** — ✅ RESOLVED. `20260521_000001_` is valid (sequence resets per date prefix, no conflicting files exist).

### Verification Summary
- Council session confirmed all codebase patterns match plan references
- No internal contradictions between phases
- Cross-phase dependencies correctly identified
- Phase 3 `store=None` approach is backward-compatible
- All edge cases covered (NULL details, CASCADE deletion, ownership validation)

### Notes (Non-blocking)
- Phase 4 doesn't explicitly mention router registration in `daemon/routers/__init__.py` and `daemon/api.py` — standard practice, implementer will handle
- `datetime.utcnow` is deprecated in Python 3.12+ in favor of `datetime.now(timezone.utc)` — minor, won't block
