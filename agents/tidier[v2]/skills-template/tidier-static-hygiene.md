---
version: 1.0.0
category: execution
auto_load: false
---

# Static Hygiene

Review for file size, imports, and type hints. This skill covers **2 of the 6
v1 categories**: File Hygiene, Type Cleanliness.

You are the reviewer. You analyze code directly. You are a **READ-ONLY
reviewer** — DO NOT modify files, run mutating commands, or make commits.
Report findings only. The Tidier dispatcher aggregates your report into the
final severity-grouped Tidier review.

---

## Read-Only Enforcement

You are a reviewer. Report findings — do not act on them. The Tidier
dispatcher decides what to fix.

**Prohibited actions:**
- `edit_file` / `write_file` / `apply_patch` — no source modifications
- `git commit` / `git push` / `git merge` / `git rebase` — no version-control mutations
- `db_conn_add` / `db_conn_delete` — no DB writes
- Skill updates that mutate the skill bank — analysis only
- Running build / install / deploy commands that change project state

**Allowed actions:**
- `read_file` / `glob` / `grep` — quick filesystem reads (especially
  `wc -l` for file size checks)
- `bash` for read-only inspection (`ls`, `cat`, `wc`, `head`, `tail`, `git log`, `git diff`)
- `knowledge` / `explore` — project-state queries
- Tool calls that produce analysis output (no side effects)

If you discover a critical issue that MUST be fixed immediately, report it as
a 🔴 finding — do not attempt to fix it yourself.

---

## Pre-Execution Self-Check (Run Before Reviewing)

Before starting the review, verify ALL of the following. If any check fails,
clarify scope with the dispatcher before proceeding.

- [ ] **Target files identified** — exact paths or globs from the dispatch message
- [ ] **Scope locked** — review ONLY the files in the diff; do not expand scope
- [ ] **Category scope noted** — this skill = File Hygiene + Type Cleanliness ONLY (defer others)
- [ ] **File-size baselines known** — ≤500 ideal, 500–1000 ok, 1000–3000 needs top-level comment, >3000 must flag for refactor
- [ ] **Language identified** — Python / JS-TS / SQL / General (apply matching type-hint rules)
- [ ] **Project rules checked** — `.agents/tidier/rules/` (may override file-size thresholds)
- [ ] **Severity scale noted** — 🔴 High > 🟡 Medium > 🟢 Low

---

## Review Execution Contract

```
Task: Static Hygiene Review
Target: [files / globs from dispatch message]
Focus areas: [File Hygiene, Type Cleanliness]

CONSTRAINTS (do NOT violate):
- READ-ONLY: report findings only. Do NOT modify files, run mutating
  commands, or commit.
- SCOPE LOCKED: review ONLY the targets above. Do NOT expand scope.
- CATEGORY SCOPE: this skill covers ONLY File Hygiene + Type Cleanliness.
  If you spot a style / smells / readability / error-handling issue,
  report it as "consider deferring to <other skill>" — do NOT file it
  as your own finding.
- CITE FILE:LINE for every finding.
- FILE-SIZE THRESHOLDS (verbatim from v1):
    ≤500 lines ideal; 500-1000 acceptable for complex modules;
    1000-3000 must include top-level comment explaining why;
    >3000 must flag for refactor.
- MARK UNCERTAIN findings as 🟢 Low with "consider" framing.
- DO NOT aggregate prior worker reports — the dispatcher does that.

REQUIREMENTS:
- Run wc -l on every target file; record the count.
- Check unused imports / variables / side-effect imports.
- Check type hints on every function signature.
- Produce the mandatory Finding Report (template below).
Output ORDER (CRITICAL — your dispatcher receives your LAST message verbatim, so a trailing summary would erase the detailed report):
1. Call skill_feedback(skill_id, applied=True, usefulness=<1-10>, note=<short>, improvement_note=<actionable>) as a TOOL CALL ONLY. Put no report, summary, or prose in that turn.
2. Deliver your full report as your FINAL message — the complete, detailed version. End your turn; do not add a follow-up summary, condensed re-report, todo update, or narration afterward.

RETURN:
- The Finding Report (template below).
- skill_feedback call.
```

---

## Focus Areas

Static-hygiene review covers two dimensions. Each has sub-checks; any
sub-check failing is a candidate finding (severity depends on the impact).

### File Hygiene

#### File-Size Thresholds (VERBATIM from v1)

```
≤ 500 lines   → Ideal, no comment needed
500–1000      → Acceptable for complex modules
1000–3000     → Must include top-level comment explaining why
> 3000        → Must flag for refactor
```

Run `wc -l <file>` on every target file. The thresholds are **non-negotiable
defaults**; `.agents/tidier/rules/` may override for specific files (e.g.,
auto-generated files, vendored code).

| File Lines | Severity (default) | Required Action |
|---|---|---|
| ≤ 500 | — | No action |
| 500–1000 | — | No action (acceptable for complex modules) |
| 1000–3000 | 🟡 Medium | Must have top-level comment explaining why this large |
| > 3000 | 🔴 High | Must flag for refactor (split module, extract sub-package) |

#### Imports

- [ ] **Unused imports** — import is never referenced; safe to remove
- [ ] **Duplicate imports** — same module imported twice (or under different names)
- [ ] **Import side effects** — imports that only run code for side effects
      (e.g., `import <module>` that registers a plugin, modifies global state)
- [ ] **Import grouping** — stdlib → third-party → local; separated by blank lines
- [ ] **Star imports** — `from module import *` (only acceptable in clearly
      marked `__init__.py` re-exports)

#### Module Exports

- [ ] **`__all__` declaration** — modules that need explicit exports declare
      `__all__ = [...]` to control `from module import *`
- [ ] **Module-level docstring** — every module has a docstring explaining
      purpose (especially in library code)
- [ ] **Unused module-level variables** — `MY_CONSTANT = 42` never referenced

#### Module Organization

- [ ] **Cyclic imports** — module A imports B, B imports A (smell;
      structure/design → Reviewer)
- [ ] **Layering** — lower-level modules should not import higher-level ones
- [ ] **Public vs private** — `_` prefix on private names; modules expose a
      clear public surface

---

### Type Cleanliness

#### Missing Type Hints

- [ ] **Public functions** — every public function/method has parameter and
      return type hints
- [ ] **Internal functions** — internal helpers have type hints where the
      signature is non-trivial
- [ ] **Module-level constants** — typed when the type is not obvious
- [ ] **Class attributes** — typed on the class (not just in `__init__`)

#### Type Annotation Quality

- [ ] **`Any` overuse** — `Any` is a code smell; prefer specific types
      (`Dict[str, int]`, `list[str]`, `Optional[int]`, etc.)
- [ ] **Type cast bypasses** — `cast()` or `# type: ignore` used to silence
      the type checker; flag if unjustified
- [ ] **Inconsistent annotations** — some params typed, others not
- [ ] **Type vs variable naming** — confusing `Type[T]` with `T`; confusing
      `Optional[T]` with `T | None`
- [ ] **Over-narrow types** — `List[int]` when `Sequence[int]` is correct
- [ ] **Over-broad types** — `object` or `Any` when a specific type is correct

#### Type System Usage

- [ ] **NewType / TypedDict** — for domain-specific primitives, prefer
      `NewType` over string aliases
- [ ] **Protocol / ABC** — structural vs nominal typing used appropriately
- [ ] **Generic constraints** — `T` with no bound when `T: Comparable` would
      be correct
- [ ] **Forward references** — string-quoted types used unnecessarily (move
      types forward, use `from __future__ import annotations` if needed)

#### TypeScript-Specific

- [ ] **`any` type** — `any` defeats type checking; prefer `unknown` and narrow
- [ ] **Missing return type** — `: void` / `: Promise<X>` on public functions
- [ ] **Implicit any** — function parameters without types
- [ ] **Type assertions** — `as` casts that may be unsafe

---

## Mandatory Finding Report Format

Output the report in this exact shape:

```
## Finding Report: [Target]

### File-Size Summary
| File | Lines | Severity | Required Action |
|------|-------|----------|-----------------|
| path/to/file.py | 1234 | 🟡 Medium | Must have top-level comment explaining why this large |
| path/to/big.py | 3456 | 🔴 High | Must flag for refactor |

### Findings
| # | Category | File:Line | Severity | Issue | Fix Suggestion |
|---|----------|-----------|----------|-------|----------------|
| 1 | File Hygiene | path/to/file.py:1 | 🟡 Medium | [file size / missing __all__ / unused import] | [concrete fix] |
| 2 | Type Cleanliness | path/to/file.py:42 | 🟡 Medium | [Any / cast / missing type hint] | [concrete fix] |

### Positive Observations
- [What's done well — credit good patterns explicitly]

### Out-of-Scope Observations (for dispatcher)
- [Style / readability / error-handling issues spotted — let dispatcher route]

### Severity Summary
- 🔴 High: N
- 🟡 Medium: N
- 🟢 Low: N
```

### Severity Calibration (Static Hygiene)

| Issue Type | Typical Severity |
|---|---|
| File > 3000 lines (flag for refactor) | 🔴 High |
| File 1000–3000 lines without top-level comment | 🟡 Medium |
| `Any` / `any` overuse (multiple sites) | 🟡 Medium |
| Type cast / `# type: ignore` without justification | 🟡 Medium |
| Unused imports across the file | 🟡 Medium |
| Missing type hints on public API | 🟡 Medium |
| Import grouping inconsistency | 🟢 Low |
| Single `Any` for a clear reason | 🟢 Low |
| Over-narrow type that doesn't matter operationally | 🟢 Low |

(See `tidier-strategy.md` for the full severity guidelines.)

---

## Skill Feedback

Call this FIRST (step 1 above), as a tool call only — before you write your final report:

```python
skill_feedback(
    skill_id="tidier-static-hygiene",
    applied=True,
    usefulness=<1-10>,                 # how useful was this skill for the task
    note=<short summary>,                # one-line takeaway
    improvement_note=<actionable>,       # what would make this skill better
)
```

Low scores are GOOD signals — they drive skill evolution. Be honest.
