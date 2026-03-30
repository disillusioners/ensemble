# Leader Agent Instructions — Review & Improvement Plan

## Status: COMPLETED ✅

All top 3 improvements have been implemented.

---

## What Was Changed

### 1. Eliminated Redundancy ✅

**Before:** ~44KB across 4 main files with massive overlap. Same concepts repeated 3-6 times.
**After:** ~16KB across 4 main files with zero duplication.

| File | Before | After | Reduction |
|------|--------|-------|-----------|
| `soul.md` | 7.5KB | ~1.5KB | 80% |
| `rule.md` | 11.3KB | ~4.5KB | 60% |
| `workflow.md` | 14.6KB | ~6.5KB | 55% |
| `tools.md` | 9.6KB | ~3.0KB | 69% |
| `skills/coordination/skill.md` | 0.7KB | ~1.5KB | expanded |
| **Total** | **~44KB** | **~17KB** | **~61% reduction** |

**Estimated token savings: ~8,000 tokens per API call.**

### 2. Fixed Scope Default to SMALL ✅

- Consistent across all files: default scope is SMALL
- Auto-detection rules: if uncertain, use coder to explore
- Upgrade/downgrade rules: SMALL → BIG if complexity emerges, BIG → SMALL if simple
- User explicit declaration always overrides

### 3. Multiple Workflow Types ✅

**Two workflows defined:**
- **Planning Workflow**: Planner → Reviewer → Leader decides (markdown only)
- **Implementation Workflow**: Coder → Reviewer → Tester (code changes)

**Key design decisions:**
- Scope is SEPARATE from workflow type
- Leader auto-detects workflow unless user specifies
- Workflows can be invoked sequentially in same session (Planning → Implementation)
- Scope controls rigor within each workflow (Tiny skips review/test, Huge gets phased execution)

---

## File Ownership (No More Redundancy)

| Concept | Lives In | Mentioned Elsewhere |
|---------|----------|-------------------|
| Identity & personality | `soul.md` | Nowhere else |
| TrueAuto mode | `soul.md` | Nowhere else |
| Team roster | `soul.md` | Nowhere else |
| Brain-only rule | `rule.md` | Nowhere else |
| Scope rules | `rule.md` | Nowhere else |
| Workflow selection rules | `rule.md` | Nowhere else |
| Decision authority | `rule.md` | Nowhere else |
| Planning workflow execution | `workflow.md` | Nowhere else |
| Implementation workflow execution | `workflow.md` | Nowhere else |
| Reviewer/tester protocols | `workflow.md` | Nowhere else |
| Anti-patterns | `workflow.md` | Nowhere else |
| Tool reference | `tools.md` | Nowhere else |
| Delegation examples | `tools.md` | Nowhere else |
| Coordination patterns | `skills/coordination/skill.md` | Nowhere else |

---

## Original Review (Preserved for Reference)

### What Was GOOD ✅
1. "Brain Only" role separation — kept and consolidated
2. 4-tier scope classification — kept, default changed to SMALL
3. `send_message()` emphasis — kept in workflow.md
4. Anti-patterns with WRONG/RIGHT — kept in workflow.md
5. Goals-vs-Commands delegation — kept in tools.md
6. Decision authority tables — kept in rule.md
7. 3-cycle loop limit — kept in workflow.md
8. Reviewer scope-creep control — kept in workflow.md

### What Was BAD ❌ (Fixed)
1. ~~Massive redundancy~~ → Eliminated. Each concept appears exactly once.
2. ~~Contradictory scope defaults~~ → Fixed. SMALL is default everywhere.
3. ~~Planner defined but never used~~ → Fixed. Planning workflow uses Planner → Reviewer loop.
4. ~~soul.md overloaded~~ → Fixed. Now identity only.
5. ~~Empty placeholder files~~ → memory.md populated with useful scope indicators.
6. ~~Coordination skill too thin~~ → Expanded with progress tracking and conflict resolution.
7. ~~No error recovery~~ → Not addressed in this iteration (future improvement).
8. ~~No concurrency management~~ → Not addressed in this iteration (future improvement).
