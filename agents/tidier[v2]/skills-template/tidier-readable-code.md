---
version: 1.2.0
category: execution
auto_load: false
---

# Readable Code

Review for code-level polish: naming, formatting, duplication, dead code,
complexity, nesting. This skill covers **3 of the 6 v1 categories**: Coding
Style, Code Smells, Readability.

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
- `read_file` / `glob` / `grep` — quick filesystem reads
- `bash` for read-only inspection (`ls`, `cat`, `wc`, `head`, `tail`, `git log`, `git diff`, `git show`)
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
- [ ] **Category scope noted** — this skill = Coding Style + Code Smells + Readability ONLY (defer others)
- [ ] **Language identified** — Python / JS-TS / SQL / General (apply the matching Language Traps below)
- [ ] **Project rules checked** — `.agents/tidier/rules/` (overrides global guidelines)
- [ ] **Prior notes checked** — read recent `.agents/tidier/notes.md` to avoid duplicates
- [ ] **Severity scale noted** — 🔴 High > 🟡 Medium > 🟢 Low (per `tidier-strategy.md` Severity Guidelines)

---

## Review Execution Contract

```
Task: Readable Code Review
Target: [files / globs from dispatch message]
Focus areas: [Coding Style, Code Smells, Readability]
Language: [python | js-ts | sql | general]

CONSTRAINTS (do NOT violate):
- READ-ONLY: report findings only. Do NOT modify files, run mutating
  commands, or commit.
- SCOPE LOCKED: review ONLY the targets above. Do NOT expand scope.
- CATEGORY SCOPE: this skill covers ONLY Coding Style + Code Smells +
  Readability. If you spot a hygiene / types / error-handling issue,
  report it as "consider deferring to <other skill>" — do NOT file it
  as your own finding.
- CITE FILE:LINE for every finding.
- MARK UNCERTAIN findings as 🟢 Low with "consider" framing.
- DO NOT aggregate prior worker reports — the dispatcher does that.

REQUIREMENTS:
- Read all target files end-to-end (or enough to cover the categories).
- Cross-check the Language Traps section below.
- Produce the mandatory Finding Report (template below).
Deliver your full report as your FINAL message — the complete, detailed version. End your turn; do not add a follow-up summary, condensed re-report, todo update, or narration afterward.

RETURN:
- The Finding Report (template below).
```

---

## Focus Areas

Readable-code review covers three dimensions. Each has sub-checks; any
sub-check failing is a candidate finding (severity depends on the impact).

### Coding Style

- [ ] **Naming conventions** — `snake_case` for functions/variables,
      `PascalCase` for classes, `UPPER_SNAKE_CASE` for constants; project
      may override
- [ ] **Import ordering** — stdlib → third-party → local; separated by
      blank lines; no unused imports
- [ ] **Formatting** — consistent indentation (project's formatter), spacing
      around operators, line length within project norms
- [ ] **Project-specific style** — `pyproject.toml` / `eslint` / `prettier` /
      `.editorconfig` settings honored
- [ ] **Quote consistency** — single vs double quotes; project style
- [ ] **Trailing commas, semicolons** — match project style

### Code Smells

- [ ] **Duplication / copy-pasted logic** — same pattern in 3+ places is 🔴
      High; 2 places is 🟡 Medium
- [ ] **Magic numbers / strings** — values that should be named constants
- [ ] **Dead code** — unused variables, functions, classes, imports,
      unreachable branches
- [ ] **Long / unclear names** — names that don't reveal intent
- [ ] **Short / cryptic names** — single-letter names outside tight loops
- [ ] **Single-Responsibility violations** — functions doing too many things
      (> 50 lines is a smell; > 100 lines is 🔴 High)
- [ ] **Parameter lists too long** — > 5 params, consider a struct / options object
- [ ] **Boolean blindness** — `True`/`False` params that should be enums or
      named flags

### Readability

- [ ] **Docstrings** — public functions/classes/methods have docstrings
      explaining intent (not just restating the signature)
- [ ] **Comments** — explain WHY, not WHAT; misleading comments are 🟡 Medium
- [ ] **TODO/FIXME** — unaddressed TODOs are 🟡 Medium (dead intent)
- [ ] **Deep nesting** — > 3 levels of indentation; extract early-returns or
      helper functions
- [ ] **Inconsistent abstraction** — a function mixing high-level business
      logic with low-level details
- [ ] **Complex lines** — single line doing too much (regex, nested
      comprehensions, chained ternaries)
- [ ] **Misleading names** — function does X but is named Y

---

## Language-Specific Traps

These language traps apply to the matching language. Watch for these in
addition to the general checks above.

### Python

- **Mutable default arguments** — `def f(items=[]):` shares the list across
  calls. Use `def f(items=None): items = items or []`.
- **Closure over loop variable** — `funcs = [lambda i=i: i for i in range(10)]`
  to capture the loop value explicitly.
- **`==` vs `is`** — `==` compares value; `is` compares identity. Use `is` for
  `None` / `True` / `False` / sentinel; use `==` for value comparison.
- **Late binding** — closures in loops capture the variable, not the value.
- **`except Exception:` too broad** — catches everything including
  `KeyboardInterrupt`-adjacent cases. Catch specific exception types.

### JavaScript / TypeScript

- **`==` vs `===`** — always prefer `===` (strict equality) to avoid
  coercion surprises (`"" == false` is `true`).
- **Async errors** — `async` function returning `Promise` with no `await`
  inside; unhandled rejections.
- **Prototype pollution** — `Object.assign(target, source)` mutates; spread
  (`{...source}`) does not.
- **Truthy/falsy pitfalls** — `0`, `""`, `null`, `undefined`, `NaN` are all
  falsy; `if (!value)` is not specific.
- **`this` binding** — arrow functions inherit `this`; regular functions
  do not. Watch for callback contexts.

### SQL

- **String concat for queries** — SQL injection vector. Use parameterized
  queries (`%s`, `?`, named binds).
- **Missing transactions** — multi-statement writes without `BEGIN`/`COMMIT`
  can leave the DB in a torn state.
- **Dirty reads / isolation** — knowing the isolation level; using
  `READ COMMITTED` vs `SERIALIZABLE` deliberately.
- **`SELECT *`** — pulls all columns; can break when schema changes; explicit
  columns are clearer.

### General

- **Premature optimization** — micro-optimizations that hurt readability.
- **Over-engineering** — abstraction for one caller; configurable knobs that
  nobody uses.
- **YAGNI** — features built for hypothetical future use.
- **Speculative generality** — `Any` types, generic factories, plugin points
  with no plugin.

---

## Mandatory Finding Report Format

Output the report in this exact shape:

```
## Finding Report: [Target]

### Findings
| # | Category | File:Line | Severity | Issue | Fix Suggestion |
|---|----------|-----------|----------|-------|----------------|
| 1 | Coding Style | path/to/file.py:42 | 🟡 Medium | [concise issue] | [concrete fix] |
| 2 | Code Smells | path/to/file.py:78 | 🔴 High | [concise issue] | [concrete fix] |
| 3 | Readability | path/to/file.py:120 | 🟢 Low | [consider: ...] | [optional fix] |

### Positive Observations
- [What's done well — credit good patterns explicitly]

### Out-of-Scope Observations (for dispatcher)
- [Hygiene / types / error-handling issues spotted — let dispatcher route]

### Severity Summary
- 🔴 High: N
- 🟡 Medium: N
- 🟢 Low: N
```

### Severity Calibration (Readable-Code)

| Issue Type | Typical Severity |
|---|---|
| Duplicate logic (3+ places) | 🔴 High |
| Function > 100 lines / clear SRP violation | 🔴 High |
| Misleading comments or names causing real risk | 🔴 High |
| Duplicate logic (2 places) | 🟡 Medium |
| Dead code (unused import, function) | 🟡 Medium |
| Magic numbers without named constants | 🟡 Medium |
| Naming inconsistency (project has style) | 🟡 Medium |
| Missing docstrings on public API | 🟡 Medium |
| Style preference (project has no rule) | 🟢 Low |
| Refactor opportunity with unclear ROI | 🟢 Low |
| Speculative abstraction | 🟢 Low |

(See `tidier-strategy.md` for the full severity guidelines.)
