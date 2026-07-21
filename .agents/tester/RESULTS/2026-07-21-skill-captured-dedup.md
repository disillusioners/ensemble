# Test Report: CAPTURED Flow Fix + Dedup Gate

**Date:** 2026-07-21
**Branch:** `feature/skill-captured-dedup`
**Feature commit:** `07c095ed` — feat: fix CAPTURED task_message + add dedup gate for skill creation
**Gap-test commit:** `53312429` — test: fill coverage gaps for CAPTURED dedup
**Tester instance:** a70a973f (Test Leader) + 5 worker instances

---

## Summary

| Metric | Value |
|--------|-------|
| **Total tests executed** | 230 (219 existing + 11 gap-fill) |
| **Passed** | 230 |
| **Failed** | 0 |
| **Errors** | 0 |
| **Timeouts** | 0 |
| **Overall verdict** | ✅ **READY** — feature verified, no regressions, gaps filled |

### Scope Decision
> Full suite NOT run. The change touches 5 source files in the skill evolution/metrics subsystem
> (skill_evolution_service.py, skill_metrics_service.py, job_queue_service.py, task_processor.py,
> skill/repository.py) and 3 test files. Blast radius is a focused feature (CAPTURED flow + dedup gate
> + task_message plumbing) — NOT cross-module architecture. Ran the directly-affected packs + a
> targeted regression pack (skill repo + CAPTURED flow C). Skipped: frontend, e2e, all unrelated
> modules. Full suite not warranted.

---

## Test Results by Pack

### Existing tests (verified green on feature commit `07c095ed`)

| Pack | File(s) | Tests | Result | Runtime |
|------|---------|-------|--------|---------|
| evolution-core | tests/services/test_skill_evolution_service.py | 56 | ✅ PASS | 1.45s |
| metrics-plumbing | tests/services/test_skill_metrics_service.py | 34 | ✅ PASS | 1.19s |
| phase4-hook | tests/job_queue/test_phase4_metrics_hook.py | 17 | ✅ PASS | 0.33s |
| regression | tests/repositories/test_skill_repository.py + tests/integration/test_skill_cross_phase_flow_c.py | 112 | ✅ PASS | 2.39s |
| **Subtotal** | | **219** | **✅ all PASS** | ~5.4s |

### Gap-fill tests (commit `53312429`)

| Pack | File(s) | Tests | Result | Runtime |
|------|---------|-------|--------|---------|
| evolution-gap | tests/services/test_skill_evolution_service.py | +6 (62 total) | ✅ PASS | ~2.5s |
| process-msg-gap | tests/services/test_process_message_metrics.py | +2 (16 total) | ✅ PASS | ~1.2s |
| repo-symmetry-gap | tests/repositories/test_skill_repository.py | +3 (103 total) | ✅ PASS | ~0.7s |
| **Gap subtotal** | | **+11 new** | **✅ all PASS** | ~4.4s |

---

## What Was Verified (by feature area)

### 1. task_message plumbing ✅
- `_get_task_details()` / `_compute_iterations_and_duration()` extract first human message as `task_message` (1000-char cap with `...[truncated]` marker) — verified across both job-queue and process_message paths.
- Edge cases: no human messages (→ `""` fallback), very long messages (truncation with marker), earliest-human-wins ordering — all covered.
- `_record_one()` persists `task_message` on INSERT path; `update_completion()` persists on UPDATE path.
- INSERT/UPDATE symmetry: empty string `""` and `None` are no-ops on UPDATE (do NOT clobber existing value); truthy values overwrite — pinned at repo layer by 3 new tests.
- Feedback-first → completion-back-fill path: `task_message` correctly back-filled from NULL.

### 2. Dedup Layer 1 (LLM-level) ✅
- `SKIP_DUPLICATE: <skill_id>` → capture skipped, no row created, Layer 2 not run.
- `SKIP_DUPLICATE:` (bare, no id) → skipped with `skip_reason="llm_skip_duplicate_no_id"`, `new_skill_id=None`, NO garbage skill row created.
- Normal LLM content (no SKIP_DUPLICATE) → capture proceeds.
- Trailing-punctuation stripping on the id (`.`, `,`, `}`, etc.) — verified.

### 3. Dedup Layer 2 (Embedding-level) ✅
- Similarity `>= 0.85` → SKIP (boundary: exactly `0.85` skips; `0.8499` proceeds — both pinned).
- **C1 regression test (CRITICAL):** deactivated/inactive skill embeddings do NOT block creation — verified with a high-similarity (0.95) inactive skill present; capture proceeded.
- Dedup scoped to same project only (cross-project skill never queried).
- Active-skill filter fail-open: when `_list_existing_active_skills_for_project` raises, Layer 2 falls back to unfiltered scan and STILL correctly finds duplicates.

### 4. Fail-open behavior ✅ (gap-filled)
- `embed_text` raises → capture proceeds (no block).
- `get_all_for_project` raises → capture proceeds.
- `cosine_similarity` returns non-numeric → row skipped, capture proceeds.
- All 4 `_embedding_dedup_check` infra-failure paths return `None` (proceed) as documented.

### 5. `_build_capture_prompt` wiring ✅ (gap-filled)
- Prompt contains `SKIP_DUPLICATE` instruction.
- Prompt embeds the `existing_skills` list (each skill's id + name visible to the LLM).

### 6. No regressions ✅
- skill repo schema/CRUD: 100 tests green.
- CAPTURED flow integration (flow C, 12 tests): end-to-end capture path unbroken.

---

## Coverage Gaps Identified & Filled

4 gaps were found during analysis (after the existing 219 tests passed) and filled with 11 new test functions:

| # | Gap | Location | Tests added |
|---|-----|----------|-------------|
| 1 | `_embedding_dedup_check` fail-open paths (embed_text raises, get_all_for_project raises, cosine_sim non-numeric, active-filter raises → unfiltered fallback) | test_skill_evolution_service.py :: TestEvolveCapturedDedupFailOpen | 4 |
| 2 | `_build_capture_prompt` dedup wiring (SKIP_DUPLICATE instruction + existing-skills list) | test_skill_evolution_service.py :: TestBuildCapturePrompt | 2 |
| 3 | `_compute_iterations_and_duration` task_message extraction on process_message path | test_process_message_metrics.py :: TestRecordMetricsWiring | 2 |
| 4 | Repo-level `update_completion` task_message no-op symmetry ("" / None no-op; truthy overwrites) | test_skill_repository.py :: TestSkillUsage | 3 |

**No production bugs were revealed by the gap tests** — all 11 passed against the feature's production code unchanged. This confirms the feature is correct across all tested paths.

---

## Findings (non-blocking, informational)

### F1: Pre-existing test-harness breakage (FOUND & FIXED in gap-test commit)
- **What:** 8 tests in `test_process_message_metrics.py` (`TestRecordMetricsWiring`) were silently broken on this branch — the `_build_processor()` stub built a `SimpleNamespace` task WITHOUT a `task_type` attribute, but production `ProcessMessageProcessor.process()` reads `task.task_type` (task_processor.py:272, added in ancestor commit `e858aa94`). The tests raised `AttributeError` before reaching the metrics hook, so they had **zero effective coverage**.
- **Severity:** The metrics hook itself was fine; the tests just couldn't exercise it. This means the Phase-4 metrics hook (from the previous bugfix) had no live test coverage on this branch until the gap-test commit.
- **Fix:** 1-line addition to the stub: `task_type=TaskType.PROCESS_MESSAGE.value` (matching the production `Task` model default). Applied in commit `53312429`. Test-code only, no production change.
- **Recommendation:** Consider adding a static check / collection assertion that guards against stub/model drift (a stub missing a production-read attribute silently passes via AttributeError before the assertion).

### F2: `pytest-timeout` plugin not installed in venv
- **What:** `pyproject.toml` declares `timeout=30` / `timeout_method="thread"`, but `pytest-timeout` is not installed. pytest emits `PytestConfigWarning: Unknown config option` and silently ignores the per-test timeout. The dual-layer timeout's Layer 2 (script-internal) is therefore NOT enforced — only the Layer 1 outer `timeout 300` guard holds.
- **Severity:** Low. All packs finished in <3s, so no risk this run. But if a test hangs, the inner 30s interrupt won't fire; only the 5-min outer cap catches it.
- **Recommendation:** Either install `pytest-timeout` (to honor the config) or remove the stale config keys. Flagged by all 3 test-pack workers independently.

### F3: pytest marker-filter footgun for mixed unit+integration packs
- **What:** When a pack mixes unit + integration-marked test files, `-m integration` DESELECTS all unit tests (100 tests skipped, false-positive green). The correct incantation is `-m "integration or not integration"` with `--override-ini="addopts="`.
- **Severity:** Process — could cause false-positive test reports if a dispatcher naively uses `-m integration` for a mixed pack. Caught by the regression worker before it produced a false report.
- **Recommendation:** Recorded as improvement note on the `test-pack-execution` skill. Dispatchers should use `-m "integration or not integration"` (or omit `-m` with `--override-ini="addopts="`).

---

## PostgreSQL Parity

**Not run — not warranted for this feature.** Rationale:
- This feature adds **no new DB columns** (the `task_message` column already existed on `skill_usage_records`; this branch only wires the `update_completion` param). The critical PG concern from project notes (`_ensure_postgres_columns()` for new columns) does not apply.
- The code change is service-layer logic (dedup gate, pure extraction function) and a repo method signature — none SQL-dialect-specific.
- The existing `skill_evolution_pg_test` pack covers seed/clone schema parity and already passed per PACKS.md; re-running it wouldn't exercise the dedup logic (embedding-level mocked).

---

## Code Changes Summary

| Commit | Type | Files | Production code? |
|--------|------|-------|------------------|
| `07c095ed` | feature | 5 source + 3 test | Yes (the feature under test) |
| `53312429` | test | 3 test files (+758 lines) | **No** (test code only) |

All test changes committed. No uncommitted changes remain.

---

## Documentation Updated

- [x] RESULTS/2026-07-21-skill-captured-dedup.md — this report
- [x] PACKS.md — added 3 new pack entries (gap packs) + updated last-run status for affected packs
- [x] LESSONS/2026-07-21-captured-dedup-coverage-gaps.md — gap-analysis methodology + findings

---

## Overall Status

- **Unit Tests (feature + gap-fill):** ✅ PASS (230/230)
- **Regression:** ✅ PASS (no regressions in skill repo or CAPTURED flow C)
- **ensure.md:** N/A for this scoped change (no critical concurrency/deadlock requirements in scope; the changed packs all PASS which satisfies Core Critical requirement #1)
- **PostgreSQL:** N/A (no schema change)
- **Coverage Gaps:** ✅ 4 gaps found, 11 tests added, all green
- **Pre-existing issues found:** F1 (harness breakage, FIXED), F2 (pytest-timeout, informational), F3 (marker footgun, recorded)
- **Verdict: ✅ READY** — feature is correct, fully tested, no regressions, gaps closed.
