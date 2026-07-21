# LESSONS: CAPTURED Dedup Coverage Gaps (2026-07-21)

**Feature:** CAPTURED flow fix + dedup gate (branch `feature/skill-captured-dedup`)
**Context:** Developer wrote 107 tests across 3 files (evolution + metrics + phase4 hook). All passed. But 4 coverage gaps remained.

## Key Lesson: "All tests pass" ≠ "fully covered"

The developer's tests were thorough and well-structured (107 tests, all green). But a systematic gap analysis — reading the production code paths against the test coverage — found 4 gaps that, if left unfilled, would have left critical fail-open and wiring contracts untested:

### Gap 1 — Fail-open paths are easy to miss
The `_embedding_dedup_check` method has 4 documented fail-open paths (infra failures → return None → proceed with capture). NONE were tested. The developer tested the happy path and the skip paths, but not the "infrastructure breaks and we gracefully proceed" contract. **Lesson: when a method documents fail-open/defensive behavior, write a test per failure branch** — these are the contracts most likely to silently regress.

### Gap 2 — Prompt construction is under-tested when only the response is tested
The dedup tests thoroughly covered the LLM *response parsing* (SKIP_DUPLICATE regex, bare prefix, boundary similarities). But the *prompt construction* (`_build_capture_prompt`) — which must include the SKIP_DUPLICATE instruction AND embed the existing-skills list — was untested. If the prompt silently stopped listing existing skills, the LLM would never dedup and all tests would still pass. **Lesson: for any LLM-driven feature, test BOTH the prompt construction AND the response parsing.** A correct parser fed a broken prompt is a silent failure.

### Gap 3 — Parallel paths need parallel tests
`task_message` extraction was tested on the job-queue path (`_get_task_details` → `_extract_task_message_from_messages` in `test_phase4_metrics_hook.py`, 4 tests). But the PARALLEL process_message path (`_compute_iterations_and_duration` in `task_processor.py`) — which extracts the same `task_message` — had ZERO task_message assertions. The existing `test_process_message_metrics.py` tested iterations + duration but not the 3rd tuple element. **Lesson: when the same logic is mirrored across two code paths, write the test for BOTH paths** — don't assume "it's the same code."

### Gap 4 — Symmetry contracts need both directions pinned
The repo's `update_completion` has a symmetry contract: INSERT coerces `""` → `None`; UPDATE treats `""`/`None` as no-op. The metrics service test covered the end-to-end back-fill path, but the repo-level contract (that `""` does NOT clobber an existing value) wasn't pinned. A regression making the guard `if task_message is not None:` (clobbering with empty string) would have passed the service test but broken the symmetry. **Lesson: pin symmetry/no-op contracts at the lowest layer**, not just through end-to-end service tests.

## Finding F1: Silent test breakage from stub/model drift

The gap-test worker discovered that 8 tests in `test_process_message_metrics.py` were silently broken on this branch: the `_build_processor()` stub built a `SimpleNamespace` without `task_type`, but production reads `task.task_type` (added in ancestor commit `e858aa94`). The tests raised `AttributeError` before the assertion — which, depending on test design, can look like a pass or a collection skip rather than a failure.

**Root cause:** A production commit added `task.task_type` reads, but the test stub wasn't updated. Classic stub/model drift.
**Lesson:** When production code adds a new attribute read on a model that has test stubs (SimpleNamespace/namedtuple), grep the test suite for stubs of that model and update them. Better: add a smoke test that constructs the stub and runs one production method call to catch drift at the point of change, not 3 commits later.
**Fix applied:** 1-line stub fix (`task_type=TaskType.PROCESS_MESSAGE.value`) in commit `53312429`.

## Finding F2: pytest-timeout absent → dual-layer timeout is single-layer

`pyproject.toml` declares `timeout=30` / `timeout_method="thread"`, but `pytest-timeout` is not installed. pytest silently ignores the config (warns `Unknown config option`). Result: the script-internal timeout layer is inactive; only the outer `timeout 300` guard holds.
**Lesson:** The dual-layer timeout contract assumes both layers are live. Verify the inner timer's plugin is installed — the Pre-Send Self-Check should `pip show pytest-timeout` (or equivalent) before asserting the inner timer is live. A config option that's silently ignored is worse than no config.

## Finding F3: pytest `-m integration` footgun for mixed packs

When dispatching a pack that mixes unit + integration-marked files, `-m integration` DESELECTS all unit tests → false-positive green. Correct: `-m "integration or not integration"` with `--override-ini="addopts="`.
**Lesson:** For mixed-marker packs, never use a bare `-m integration` or `-m postgres` — it's exclusive in the opposite direction. Use the disjunction form or omit `-m` entirely with `--override-ini`.
