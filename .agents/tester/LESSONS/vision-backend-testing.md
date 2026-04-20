# Vision Backend Testing Notes

## Testing Approach
- Vision tests live in `tests/unit/test_vision.py` — 45 tests covering validation, multimodal construction, serialization, DB storage
- Vision changes touch config, models, API, manager, graph, and utils — test packs that cover these need `images=None` assertion updates
- Tool binding is independent of vision config — verified in `daemon/graph.py` lines 427-430

## Gotchas
1. **test_api.py assertions**: When `enqueue_message()` signature changes (e.g., adding `images` param), mock assertions in test_api.py need to include the new parameter with default value (`images=None`)
2. **Test pack stale references**: `test/packs/core_unit_test.sh` and `compaction_unit_test.sh` had references to non-existent test files that needed cleanup
3. **Pre-existing failures**: job_queue integration tests have 5 flaky tests (race conditions) — these are NOT related to vision changes

## Quick Fixes Applied
- Commit `731a74e`: Fixed test expectations for vision backend (images=None assertion, stale file refs)
- Commit `5d1f15a`: Added 8 edge-case vision tests (build_message_content paths, HTTP 400, enqueue with images)
