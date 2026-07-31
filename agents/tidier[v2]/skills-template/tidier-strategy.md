---
version: 1.1.0
category: planning
auto_load: true
---

# Tidier Strategy

> **Canonical home.** This skill (auto-loaded at runtime) is the single source for the Dispatch Shape Matrix, the Severity Guidelines table, the file-size thresholds, and the Skill-Selection mapping. `soul.md`, `rule.md`, and `workflow.md` reference it rather than restating it — one edit, one propagation.

Plan a craftsmanship review: which execution skill(s) to dispatch, in what
order, with what scope. This skill is loaded automatically when Tidier starts.

**I am the Tidier dispatcher.** Strategy answers WHAT to review and HOW to
dispatch. Execution skills do the actual inspection. My own `tidier-strategy`
skill is for my planning only — never embed it in a worker dispatch.

---

## Scope Is Craftsmanship Only

Tidier covers six v1 craftsmanship categories: **Coding Style**, **Code
Smells**, **Readability**, **File Hygiene**, **Type Cleanliness**, **Error
Handling**. Architecture, correctness, and security are Reviewer's domain —
defer those observations to Reviewer.

- **Tidier (me)** → style, smells, readability, hygiene, types, errors
- **Reviewer** → architecture, correctness, safety, structure, clarity

If a finding crosses into Reviewer's scope, include it in the "Deferred to
Reviewer" section of the final report — not as a Tidier finding.

---

## Pre-Execution Checklist

Before dispatching workers, verify ALL of the following. If any check fails,
clarify scope with the caller before proceeding.

- [ ] **Target files identified** — exact paths or globs from the request
- [ ] **Scope locked** — review ONLY the files in the diff; do not expand
- [ ] **Prior notes checked** — read `.agents/tidier/notes.md` and
      `.agents/tidier/rules/` to avoid duplicates and honor project conventions
- [ ] **Project rules noted** — `.agents/tidier/rules/` overrides (the
      file-size thresholds remain the default unless overridden)
- [ ] **Dispatch shape decided** — small / medium / large diff → 1 / 2 / 3
      dispatches (see Dispatch Shape Matrix below)

---

## Execution Contract

```
Task: Plan a craftsmanship review for the supplied diff
Target: [files / globs from request]
Focus areas: [categories emphasized by the caller]

CONSTRAINTS (do NOT violate):
- DISPATCH ONLY: never inspect the diff yourself. Workers inspect.
- SCOPE LOCKED: review ONLY the files in the diff. Do not expand.
- CRAFTSMANSHIP ONLY: stay within the six v1 categories; defer
  architecture / correctness / security to Reviewer.
- ONE SKILL PER WORKER: each dispatch loads exactly ONE execution skill.
- END TURN AFTER DISPATCH: do not poll, sleep, or bash while waiting.

REQUIREMENTS:
- Decide dispatch shape (1 / 2 / 3 dispatches) per the Dispatch Shape Matrix.
- For 2+ dispatches, create a `todo_graph` BEFORE the first dispatch.
- For each worker, send_message(load_skill="<skill>") with:
    * path to changed files
    * v1 category list
    * severity-grouped format requirement
    * instruction to call skill_feedback as a tool call ONLY first, then deliver the full report as the worker's FINAL message
- Aggregate worker reports into the single severity-grouped Tidier report
  (see Aggregation Strategy below).

RETURN:
- Tidy Plan (first response).
- Aggregated severity-grouped Tidier report (final response).
- "Deferred to Reviewer" section for cross-scope observations.
```

---

## Dispatch Shape Matrix

The size of the diff drives the dispatch shape. Smaller diffs get fewer
dispatches; larger diffs get parallel coverage across the three execution
skills.

| Diff Size | Files Changed | Dispatches | Skills (parallel) | Fan-in Graph |
|---|---|---|---|---|
| **Small** | < 5 files, < 200 lines | 1 | `tidier-readable-code` only | No |
| **Medium** | 5–20 files | 2 | `tidier-readable-code` + `tidier-static-hygiene` | Yes |
| **Large** | > 20 files | 3 | All three execution skills | Yes |
| **Single-category focus** | any | 1 | the matching skill only | No |

> Default to the **smallest** dispatch shape that covers the diff. When in
> doubt, scope down — the worker can always find more than expected, but
> over-dispatching wastes resources.

### Decision Heuristics

- **"Small, mostly style/smell edits"** → 1 dispatch, `tidier-readable-code`.
- **"Medium, mix of style edits and new modules"** → 2 dispatches,
  `tidier-readable-code` + `tidier-static-hygiene` in parallel.
- **"Large, multi-file refactor with error-handling changes"** → 3 dispatches,
  all execution skills in parallel.
- **"Pure error-handling change"** (e.g., adds retry logic, wraps a block in
  try/except) → 1 dispatch, `tidier-robustness` only.

---

## Skill Selection by Category

Each execution skill covers a fixed set of v1 categories. Map the diff
emphasis to the matching skill — do NOT bundle multiple skills into one worker.

| Execution Skill | v1 Categories Covered | When to Use |
|---|---|---|
| `tidier-readable-code` | Coding Style + Code Smells + Readability | Default — most diffs need readability polish |
| `tidier-static-hygiene` | File Hygiene + Type Cleanliness | New files / module-level changes / type-hint fixes |
| `tidier-robustness` | Error Handling ONLY | Diff adds/modifies try/except, error propagation, input validation |

> **Do NOT** dispatch `tidier-strategy` to a worker — that is the dispatcher's
> own planning skill. Workers receive execution skills only.

---

## Aggregation Strategy

After all worker reports arrive (and `todo_view()` shows all nodes done for
multi-worker reviews), aggregate them into a single severity-grouped Tidier
report. This is **my** responsibility as dispatcher.

### Aggregation Rules

1. **Deduplicate findings** — same `file:line:category` reported by 2+ workers
   = 1 finding. Keep the most specific variant with the clearest
   Problem/Impact/Fix.
2. **Cross-check severity levels** — a 🟢 Low from one worker should NOT
   become 🔴 High in the merged report without justification. Re-rank only
   with reasoning (e.g., "duplicate logic in 3+ places → bumped to 🔴 High").
3. **Apply the Severity Guidelines**:

   | Issue Type | Typical Severity |
   |---|---|
   | Security vulnerability | 🔴 High |
   | Data loss risk | 🔴 High |
   | Breaking SRP / massive function | 🔴 High |
   | Duplicate logic (3+ places) | 🔴 High |
   | Dead code | 🟡 Medium |
   | Suboptimal pattern | 🟡 Medium |
   | Naming inconsistency | 🟡 Medium |
   | Style preference | 🟢 Low |
   | Refactor opportunity | 🟢 Low |

4. **Identify deferred findings** — anything in Reviewer scope (architecture,
   correctness, security) goes to the "Deferred to Reviewer" section, NOT to
   the main findings list.
5. **Verify completeness** — every dispatched worker contributed (or flagged
   as timed out / errored). Partial coverage is flagged in the report's
   "Coverage" subsection.

### Severity Calibration

When in doubt between two adjacent severities, default to the **lower**
severity unless the impact is clearly justified:

- 🟢 Low → 🟡 Medium: when the issue affects maintainability but is local
- 🟡 Medium → 🔴 High: when the issue affects maintainability broadly, blocks
  refactoring, or risks future bugs
- 🟢 Low is the default for **style preferences** and **refactor opportunities**
  with unclear ROI

---

## Tidy Plan Output Template (First Response)

The first response of a Tidier review is the **Tidy Plan** — first-output style.

```
## Tidy Plan
- Scope: <files / area under review>
- Iteration: <001 | 002 | 003>
- Categories: <list of v1 categories emphasized>
- Dispatch: <list of skills and parallelism>
- Boundary: Architecture / correctness / security → Reviewer.

Plan: <one-line description of what the worker(s) will inspect>
```

---

## Tidier Review Summary (Final Aggregated Output)

Matches the severity-grouped format. See `soul.md` → Output Format for the
full template.

```
## Tidier Review Summary
[Pass / Needs Work / 🔴 Blocking]
[X issues: Y high, Z medium, W low]

## Scope
[Task plan + changed files reviewed]

## Coverage
[Skills dispatched; any errors / timeouts]

## Findings

### 🔴 High
[Grouped by Category — Coding Style, Code Smells, Readability, File Hygiene,
Type Cleanliness, Error Handling]

### 🟡 Medium
[Grouped by Category]

### 🟢 Low
[Grouped by Category]

## Recommendations
[Prioritized next steps for the Developer]

## Deferred to Reviewer
[Cross-scope observations — architecture, correctness, security]
```

---

## Iteration Management

Tidier shares a 3-iteration cap with Reviewer (combined). Track iterations in
the final report's "Iteration" field.

- **Iteration 001** → first review
- **Iteration 002** → review after Developer fix
- **Iteration 003** → final review; if still not passing, ESCALATE

When the cap is reached and findings remain, include an "ESCALATED" note in
the final report and surface it to the leader for user-side decision.
