# Phase 2 Feedback Loop Testing Lessons

## Test Coverage Discovery
- Phase 2 code changes came with comprehensive test files already written (167 tests across 7 files)
- The 10 functional/race/edge scenarios requested were mostly covered already
- Only 12 additional tests needed for double-event-delivery and atomic-transition-integration scenarios

## dev.sh Fix Pattern
- dev.sh was inheriting PORT=8088 from the environment (the production/test system port)
- Fix: Unconditionally set PORT=8079 in dev.sh to avoid conflict
- Also needed separate data_dev/ directory to avoid conflicting with production data
- **Lesson:** Always check env var inheritance when running dev scripts alongside production

## Pre-existing Test Failures
- Core unit tests had 3 pre-existing failures unrelated to Phase 2
- `daemon/utils.py::serialize_message()` was missing `type` field
- Sources dispatcher/registry tests had outdated fixture signatures
- **Lesson:** Always run full test suite on the branch before testing new feature changes

## Commit History
- `6dd1941` — Pre-existing test fixes (utils.py, sources tests)
- `80be63b` — 12 new Phase 2 verification tests
- `8f5e97a` — dev.sh fixes for running alongside production
