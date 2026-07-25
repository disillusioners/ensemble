# LESSONS: context-injection feature test run (2026-07-25)

## Lesson: Baseline drift in PACKS.md (c2 / core packs)
- PACKS.md `core_unit_test` baseline recorded 10 failures (2026-07-12).
- Actual failures observed on `feature/context-injection`: 41.
- **Root cause:** commit `843e2c34` (2026-07-14, SQLite-incompatible `DROP CONSTRAINT IF EXISTS` migration) landed *between* the baseline run and the feature branch — 11 days later. It introduced 31 new pre-existing failures in the `test_manager.py` cluster. The PACKS.md summary line 6 already noted "39 pre-existing SQLite-path failures" from a 2026-07-23 full-suite run, but the per-pack `core_unit_test` row still shows the stale "10" count.
- **Impact on this run:** harmless — worker empirically verified by checking out parent commit `fa3f68a0` and re-running; identical failures proved 0 NEW regressions.
- **Action:** when a regression pack shows failures > baseline, ALWAYS require workers to classify pre-existing vs new via parent-commit re-run (this worker did exactly that — exemplary). Consider updating the per-pack `core_unit_test` row baseline to "41 failures (39 SQLite-migration + 2 fixture-isolation, all pre-existing)" next time it is touched, to avoid future confusion.

## Lesson: Ad-hoc feature packs (multi-file, dispatcher-authorized)
- Feature branches often ship a new test file + modify a sibling test file (here: `test_context_injection_prompt.py` new + `test_registry_skill_injection.py` modified). Neither has a PACKS.md pack at branch time.
- These run fine as an **ad-hoc pack** when explicitly authorized by the dispatcher (single timeout wrapper, 2 files, scope-locked). Workers should not be blocked by the "PACKS.md valid" Pre-Send self-check item for ad-hoc authorized runs.
- **Action:** registered this run as `context_injection_feature_test` in PACKS.md so future branches touching the same files have a real pack to reference.

## Feature verdict
`feature/context-injection` (commits `231253a9` + `5d8ec1f6`) is **safe to merge**: 20 feature tests green (all 5 claimed behaviors covered), 3 regression packs clean (0 new failures).
