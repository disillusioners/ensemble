

---

## Re-Test: Dedup Fix (2026-06-14, commit 4a66dce)

**Fixes verified:**
1. **Jaccard → Containment**: `_is_duplicate_experience` now uses `|intersection| / min(|A|, |B|)` instead of dead-code Jaccard `|intersection| / |union|`. Containment correctly fires when ≥80% of new text tokens exist in an existing file, regardless of file length.
2. **Timestamp in filename**: `{slug}_{timestamp}_experience.md` prevents data loss from same-slug overwrites. Format `%Y%m%d_%H%M%S` is sortable + human-readable.
3. **Test rewritten**: `test_experience_skips_duplicate_content` now uses different slugs (proving dedup fires on content, not slug collision). Explicitly shows Jaccard would score 0.55 (fail) vs containment 0.92 (pass).

**Results:** 110/110 unit tests pass, 480/480 regression tests pass, 0 regressions, 0 quick fixes needed.
**Status:** ✅ READY
