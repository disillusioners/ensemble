# Re-Test Report: experience() Dedup Fix

**Date:** 2026-06-14
**Branch:** `feature/experience-file-persist`
**Commit:** `4a66dce` (fix after `28698ff`)
**Sessions:** retest-primary, retest-regression

---

## Summary

| Category | Result |
|----------|--------|
| Unit Tests (feature) | ✅ PASS (110/110) |
| Tools Regression | ✅ PASS (438/438) |
| Explorer Auto-save Regression | ✅ PASS (42/42) |
| Dedup Fix Verification | ✅ PASS (4/4 checks) |
| Quick Fixes Applied | 0 |
| **Overall Status** | ✅ **READY** |

---

## Unit Test Results

### `tests/unit/tools/test_knowledge_tools.py`: 110/110 PASS

All 6 `TestExperienceAutoSave` tests pass, including the rewritten:
- `test_experience_skips_duplicate_content` ✅ — different slugs, containment ≥ 0.8 fires, second file NOT created
- `test_experience_saves_non_duplicate_content` ✅ — unrelated text, dedup does NOT fire, new file created

### Regression Sweep: 480/480 PASS

| Suite | Total | Passed | Failed |
|-------|------:|-------:|-------:|
| `tests/unit/tools/` (full directory) | 438 | 438 | 0 |
| `tests/unit/test_explorer_auto_save.py` | 42 | 42 | 0 |
| **Combined** | **480** | **480** | **0** |

**Zero regressions.** No adjacent dedup functions (`_is_duplicate_concise`, explorer Jaccard) affected.

---

## Deep Verification of 3 Fixes

### Fix 1: Containment Metric (was Jaccard) — ✅ VERIFIED

**Function:** `_is_duplicate_experience` (L476-507)

```python
overlap = len(new_tokens & existing_tokens) / min(len(new_tokens), len(existing_tokens))
```

| Check | Status |
|-------|--------|
| Uses containment: `|intersection| / min(|A|, |B|)` | ✅ |
| Threshold = 0.8 | ✅ |
| NOT Jaccard (`|union|` denominator) | ✅ |
| Correct rationale: short new text fully contained in long existing file scores high | ✅ |

**Why containment is right:** Markdown headers (`# Experience Recorded`, `**Time**:`, `**Project**:`) inflate the union with non-intersecting tokens. With Jaccard, a short new text scores 0.55 (below threshold). With containment, it scores 0.92 (above threshold). We care about "is the new text already present" → containment.

### Fix 2: Timestamp in Filename — ✅ VERIFIED

**Function:** `_save_experience_result` (L533-539)

```python
timestamp = now.strftime("%Y%m%d_%H%M%S")
file_path = dir_path / f"{slug}_{timestamp}_experience.md"
```

| Check | Status |
|-------|--------|
| Pattern: `{slug}_{timestamp}_experience.md` | ✅ |
| Format `%Y%m%d_%H%M%S` (sortable, human-readable, includes seconds) | ✅ |
| Prevents data loss: same slug → unique filename → no overwrite | ✅ |

### Fix 3: Rewritten Test Genuinely Tests Dedup — ✅ VERIFIED

**Test:** `test_experience_skips_duplicate_content` (L2674-2752)

| Check | Status |
|-------|--------|
| Different slugs for pre-seeded and new text | ✅ (`assert pre_seeded_slug != new_slug`) |
| Content >80% similar via containment | ✅ (12/13 ≈ 0.92 ≥ 0.8) |
| Jaccard would NOT fire | ✅ (12/22 ≈ 0.55 < 0.8 — old metric was dead code) |
| Second file NOT created | ✅ (`assert len(files) == 1`) |
| Pre-seeded file unchanged | ✅ (`assert files[0].name == ...`) |

### Fix 4 (bonus): Glob Pattern Compatible — ✅ VERIFIED

Glob `*_experience.md` (L494) matches both old format (`slug_experience.md`) and new format (`slug_20260614_125425_experience.md`). The timestamp is placed between slug and `_experience.md` suffix, so the glob suffix-match still works.

---

## Conclusion

All 3 fixes from commit `4a66dce` are correctly implemented and verified:
1. **Containment dedup** works — genuinely fires when ≥80% of new tokens exist in an existing file
2. **Timestamp filename** prevents data loss — each save gets a unique filename
3. **Test rewritten** — no longer masks the bug; explicitly proves Jaccard would have failed

No quick fixes needed. No regressions. Feature is ready for merge.
