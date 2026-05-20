# Plan Approval Tracking: Critical Experience for Project Model

---

## Iteration 001 — 2026-05-20 04:34

**Verdict: APPROVED**

### Evaluation Summary

Evaluated all 5 phases independently using 2 sequential council sessions. Verified Phase 1 schema claims against actual code, Phase 2 logic against repository/tool patterns, and Phase 4 injection approach.

### Findings

| Area | Status | Detail |
|------|--------|--------|
| Phase 1: CriticalExperience model placement | ✅ CONFIRMED | Follows existing enum/model patterns |
| Phase 1: JSON column pattern | ✅ CONFIRMED | Matches `relationships`, `project_metadata` fields |
| Phase 1: Migration naming + format | ✅ CONFIRMED | `20260520_000001` follows `YYYYMMDD_NNNNNN` pattern |
| Phase 1: `to_data()` bug | ✅ CONFIRMED | Bug exists at repository.py:140 — `to_data()` not a method on Project |
| Phase 1: `to_dict()` update | ✅ CONFIRMED | Pattern correct |
| Phase 2: Wire-in location (line 608) | ✅ CONFIRMED | Correct placement after `tools.extend(project_tools)` |
| Phase 2: Merge algorithm | ✅ CONFIRMED | Conservative, threshold ≥2 shared keywords |
| Phase 2: Eviction algorithm | ✅ CONFIRMED | Sort by (priority, created_at), remove lowest/oldest |
| Phase 2: Add tool sequence | ✅ CONFIRMED | Evict-then-append order prevents exceeding 30 |

### Notes (Non-Blocking)

1. **Method name**: Phase 2 code calls `store.update_critical_experience()` which doesn't exist. Should use existing `store.update(project_id, critical_experience=...)`. Trivial fix during implementation.
2. **Zero-keyword edge case**: Very short summaries (all words ≤3 chars) produce empty keyword sets, preventing merge. Acceptable design trade-off — conservative by intent.
3. **All-critical eviction**: If all 30 entries are critical priority, oldest critical entry gets evicted. Expected behavior worth documenting in tool docstring.
