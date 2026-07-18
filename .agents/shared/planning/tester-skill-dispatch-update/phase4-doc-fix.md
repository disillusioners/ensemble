# Phase 4: Design Doc Parameter Name Fix

> **📋 v2 changes (reviewer W1):** Corrected occurrence count from 11 → **13**. Expanded line reference list to include all 13 verified line numbers.

## Objective
Correct the parameter name drift in the skill-per-worker design doc: `skill=` → `load_skill=`, bringing the documentation in line with the actual implemented API.

## Coupling
- **Depends on**: None
- **Coupling type**: independent
- **Shared files with other phases**: none
- **Why this coupling**: Design doc is standalone documentation.

## Context
The design doc `docs/plans/skill-per-worker-architecture.md` describes the architecture using `send_message("task...", skill="unit-test")` — but the implemented parameter is `load_skill`. The actual runtime code (`instance.py:708-715`) appends `<meta>{"load_skill":"..."}</meta>`.

This is a documentation-only fix.

## W1: Verified Occurrence Count = 13 (not 11)

Full grep with line numbers (verified 2026-07-17):

```
Line 47:  | unit-test | Worker (dynamic) | `send_message("task...", skill="unit-test")` |
Line 48:  | mock-test | Worker (dynamic) | `send_message("task...", skill="mock-test")` |
Line 49:  | test-pack-execution | Worker (dynamic) | `send_message("task...", skill="test-pack-execution")` |
Line 50:  | integration-test | Worker (dynamic) | `send_message("task...", skill="integration-test")` |
Line 51:  | e2e-test | Worker (dynamic) | `send_message("task...", skill="e2e-test")` |
Line 52:  | ensure-validation | Worker (dynamic) | `send_message("task...", skill="ensure-validation")` |
Line 53:  | flaky-test-management | Worker (dynamic) | `send_message("task...", skill="flaky-test-management")` |
Line 54:  | quick-fix | Worker (dynamic) | `send_message("task...", skill="quick-fix")` |
Line 61:  3. Tester spawns Worker with send_message(skill="unit-test", message="run unit tests on auth module")
Line 76:   │    skill="unit-test",         │                           │
Line 202:     skill="unit-test"  # NEW: optional skill name
Line 217: - Worker receives `skill="unit-test"` → system checks project skills
Line 273: 1. ✅ `send_message(skill="X")` reliably delivers skill X to worker
```

**Breakdown:** 8 table rows (lines 47-54) + 1 prose (line 61) + 1 ASCII diagram (line 76) + 1 code comment (line 202) + 1 prose (line 217) + 1 checklist prose (line 273) = **13 total**.

**0 occurrences of `load_skill` in the doc currently.**

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Replace `skill="` → `load_skill="` | Global text replace across the 13 occurrences | docs/plans/skill-per-worker-architecture.md |
| 2 | Review each replacement in context | Ensure no false positives (e.g., "skill" as a noun, not a parameter) | docs/plans/skill-per-worker-architecture.md |
| 3 | Check for `skill=` without quotes | Some references may use `skill=unit-test` (no quotes) — catch those too | docs/plans/skill-per-worker-architecture.md |
| 4 | Verify no other param-name drift | Scan for `skill_name=` or other variants | docs/plans/skill-per-worker-architecture.md |

## Exact Replacements (reference — all 13 lines)

```
Line 47:  `send_message("task...", skill="unit-test")`           → `load_skill="unit-test"`
Line 48:  `send_message("task...", skill="mock-test")`            → `load_skill="mock-test"`
Line 49:  `send_message("task...", skill="test-pack-execution")`  → `load_skill="test-pack-execution"`
Line 50:  `send_message("task...", skill="integration-test")`     → `load_skill="integration-test"`
Line 51:  `send_message("task...", skill="e2e-test")`              → `load_skill="e2e-test"`
Line 52:  `send_message("task...", skill="ensure-validation")`     → `load_skill="ensure-validation"`
Line 53:  `send_message("task...", skill="flaky-test-management")` → `load_skill="flaky-test-management"`
Line 54:  `send_message("task...", skill="quick-fix")`             → `load_skill="quick-fix"`
Line 61:  send_message(skill="unit-test", message="...")          → send_message(load_skill="unit-test", message="...")
Line 76:  skill="unit-test",                                      → load_skill="unit-test",
Line 202: skill="unit-test"  # NEW: optional skill name          → load_skill="unit-test"  # optional skill name
Line 217: receives `skill="unit-test"`                            → receives `load_skill="unit-test"`
Line 273: send_message(skill="X")                                 → send_message(load_skill="X")
```

## Key Files
- `docs/plans/skill-per-worker-architecture.md` (sole file)

## Constraints
- **Documentation-only** — no code changes, no runtime effect.
- Only replace `skill=` where it is a **parameter** (followed by `"..."` or a bare value in a `send_message(...)` call context). Do NOT replace the word "skill" when used as a noun (e.g., "the skill is loaded", "skill selection").
- The doc is a design/historical artifact — it's fine to fix the param name, but don't rewrite the doc's narrative.

## Deliverables
- [ ] All 13 occurrences of `skill="` → `load_skill="` in the design doc
- [ ] grep `skill="` in design doc → 0 results (after fix)
- [ ] grep `load_skill` in design doc → ≥13 results
- [ ] No false-positive replacements (noun "skill" references intact)

## Est. Time: 15 minutes
