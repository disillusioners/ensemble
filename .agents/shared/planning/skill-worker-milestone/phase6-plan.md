# Phase 6: Testing & Validation

## Objective
Comprehensive integration testing across all phases to validate clean 1:1 attribution, auto_load metrics visibility, meta-tag skill loading, worker reuse, and composite-score A/B resolution. Run against PostgreSQL (primary dev/test DB).

## Coupling
- **Depends on**: Phases 1-5 (all prior code must be complete)
- **Coupling type**: tight (tests exercise all prior code paths)
- **Shared files with other phases**: Tests in `tests/` directory
- **Why this coupling**: Phase 6 is the validation gate. Cannot test until all implementation is done.

## Tasks

### Task 6.1: Integration Test — Meta Tag Skill Loading

**File**: `tests/test_skill_meta_tag_integration.py` (NEW)

```python
"""
Integration: Skill loading via <meta> tag.
Tests Phase 1 end-to-end.
"""
import pytest

@pytest.mark.asyncio
async def test_meta_tag_loads_skill_to_worker(test_db, worker_instance, seeded_skills):
    """Worker receives skill via meta tag → skill loaded + tracked."""
    # 1. Seed skill bank with tester skills (clone-on-miss source)
    # 2. Spawn worker instance
    # 3. Send message: "run tests\n<meta>{"load_skill": "unit-test"}</meta>"
    # 4. Assert: instance_metadata["last_injected_skill_ids"] == [unit_test_id]
    # 5. Assert: skill content in injected HumanMessage
    # 6. Assert: message body has meta tag STRIPPED (agent doesn't see it)

@pytest.mark.asyncio
async def test_meta_tag_worker_reuse_different_skill(test_db, worker_instance, seeded_skills):
    """Worker reuse: different meta-tag skill clears old scope."""
    # 1. Send: <meta>{"load_skill": "unit-test"}</meta>
    # 2. Assert: last_injected_skill_ids == [unit_test_id]
    # 3. Send: <meta>{"load_skill": "mock-test"}</meta>
    # 4. Assert: last_injected_skill_ids == [mock_test_id] (REPLACED, not merged)

@pytest.mark.asyncio
async def test_meta_tag_clone_on_miss(test_db, fresh_project, worker_instance):
    """Meta tag triggers clone-on-miss from skill bank."""
    # 1. Ensure project has NO skills cloned yet
    # 2. Send: <meta>{"load_skill": "unit-test"}</meta>
    # 3. Assert: skill cloned from bank to project scope
    # 4. Assert: last_injected_skill_ids == [cloned_skill_id]

@pytest.mark.asyncio
async def test_meta_tag_no_skill_falls_through(test_db, worker_instance):
    """Message without meta tag → normal processing, no explicit injection."""
    # 1. Send: "just a regular task" (no meta tag)
    # 2. Assert: normal injection pipeline runs (first-message search)
    # 3. Assert: no explicit skill loading
```

### Task 6.1b: Meta Tag Fuzz Tests (C1 Security Hardening)

**File**: `tests/test_skill_meta_tag_fuzz.py` (NEW)

C1 fuzz tests covering: nested JSON braces (original bug), malformed tags, multiline payloads, non-dict JSON rejection, multiple tags (last-wins), schema allow-list, agent-crafted injection attempts.

**Test cases (22 total):**

| Test | Input | Expected |
|------|-------|----------|
|  |  | Full parse, no truncation |
|  | 5-level nesting | Full parse |
|  | Missing  | No match, msg unchanged |
|  | Missing  | No match |
|  | Garbage text inside | Parse fails, tag stripped |
|  |  | isinstance guard rejects |
|  |  | isinstance guard rejects |
|  |  | isinstance guard rejects |
|  |  | isinstance guard rejects |
|  | 2 valid tags | Last wins, all stripped |
|  | Malformed + valid | Valid wins |
|  | 2 malformed tags | All stripped, meta=None |
|  |  | Matches |
|  | JSON across lines | Parse succeeds |
|  |  | No valid JSON, stripped |
|  | Whitespace inside | Parse fails, stripped |
|  | Unknown keys present | Logged, not fatal |
|  | Simulated injection | Extracted, stripped from msg |
|  |  | None |
|  |  | None |
|  |  in JSON value | Parsed as string value |
|  |  in content | Last-wins, all stripped |

### Task 6.1c: C2 Finalize-on-Replace + C3 Ordering Integration Tests

**File**: `tests/test_finalize_on_replace_integration.py` (NEW)

| Test | What it validates |
|------|------------------|
|  | C2: Dropped skill gets SUPERSEDED record (superseded=True, total_selections++, total_completions NOT incremented) |
|  | C2: Same skill re-injected → no finalize (dropped set empty) |
|  | C2: 5 normal + 3 superseded → completion_rate = 3/5 (not 3/8) |
|  | C2: Stale pending records swept → marked superseded |
|  | C3: Explicit REPLACE → auto_load MERGE (additive, explicit preserved) |

### Task 6.2: Integration Test — Auto_load Metrics Tracking

**File**: `tests/test_auto_load_metrics_integration.py` (NEW)

```python
"""
Integration: Auto_load skills visible in metrics.
Tests Phase 2 end-to-end.
"""
import pytest

@pytest.mark.asyncio
async def test_auto_load_skill_gets_usage_record(test_db, tester_instance, seeded_tester_skills):
    """Auto_load skill (test-strategy) appears in usage records after task completion."""
    # 1. Spawn tester instance (has test-strategy as auto_load)
    # 2. Verify: instance_metadata["last_injected_skill_ids"] contains test-strategy ID
    # 3. Complete a task
    # 4. Assert: skill_usage_records has a row for test-strategy
    # 5. Assert: skills table counters (total_selections) incremented for test-strategy

@pytest.mark.asyncio
async def test_auto_load_dedup_merge_with_on_demand(test_db, tester_instance):
    """Auto_load + on-demand injection: both tracked in metadata."""
    # 1. Tester has test-strategy as auto_load → metadata has [test_strategy_id]
    # 2. First message triggers on-demand injection → metadata has [test_strategy_id, on_demand_id]
    # 3. Assert: both IDs present (dedup-merge, not replace)
```

### Task 6.3: Integration Test — A/B Composite Scoring

**File**: `tests/test_ab_composite_integration.py` (NEW)

```python
"""
Integration: A/B testing with composite score.
Tests Phase 3 end-to-end.
"""
import pytest

@pytest.mark.asyncio
async def test_ab_resolution_uses_composite_score(test_db, seeded_ab_test):
    """A/B winner determined by composite score, not just completion_rate."""
    # 1. Seed an A/B test with two variants
    # 2. Insert usage records: variant A (high completion, low applied_rate)
    #    variant B (same completion, high applied_rate)
    # 3. All records tagged with ab_test_group
    # 4. Trigger check_ab_test_resolution
    # 5. Assert: winner is B (higher composite due to applied_rate)

@pytest.mark.asyncio
async def test_ab_tie_break_challenger_wins(test_db, seeded_ab_test):
    """Equal composite scores → challenger (new variant) wins."""
    # 1. Seed A/B test with identical-performing variants
    # 2. Insert identical usage records for both
    # 3. Trigger resolution
    # 4. Assert: winner is the NEW variant (skill_id_new)

@pytest.mark.asyncio
async def test_ab_stats_only_test_period(test_db, seeded_ab_test):
    """A/B stats exclude pre-test records."""
    # 1. Insert records WITHOUT ab_test_group (pre-test history)
    # 2. Insert records WITH ab_test_group (test period)
    # 3. Call get_ab_comparison_stats
    # 4. Assert: stats only reflect test-period records

@pytest.mark.asyncio
async def test_ab_sample_size_20(test_db):
    """A/B resolution waits until 20 comparisons (not 10)."""
    # 1. Seed A/B test
    # 2. Insert 19 comparisons → assert reason="needs_more_data"
    # 3. Insert 20th comparison → assert resolution eligible
```

### Task 6.4: Integration Test — Trigger & Tier 2

**File**: `tests/test_trigger_tier2_integration.py` (NEW)

```python
"""
Integration: Trigger routing and Tier 2 analysis.
Tests Phase 4 end-to-end.
"""
import pytest

@pytest.mark.asyncio
async def test_consecutive_failures_routes_to_analyze(test_db, seeded_skills):
    """consecutive_failures trigger → Tier 2 analysis (not evolve_fix)."""
    # 1. Seed skill with consecutive_failures = 3
    # 2. Run trigger engine evaluate_all()
    # 3. Assert: flagged skill has action="analyze" (not "evolve_fix")

@pytest.mark.asyncio
async def test_tier2_prompt_includes_new_metrics(test_db, seeded_skill):
    """Tier 2 analysis prompt includes applied_rate, avg_iterations, avg_duration."""
    # 1. Call _build_analysis_prompt with stats including new fields
    # 2. Assert: prompt text contains "applied_rate", "avg_iterations", "avg_duration"

@pytest.mark.asyncio
async def test_first_use_failure_marks_fallback(test_db):
    """First-use failure (consecutive_failures=0) marks fallback=True."""
    # 1. Create usage record for a skill where task fails on first use
    # 2. Assert: fallback=True (not False)
```

### Task 6.5: End-to-End Validation — Clean 1:1 Attribution

**File**: `tests/test_skill_worker_e2e.py` (NEW)

```python
"""
End-to-end: Clean 1:1 skill attribution.
Validates the core promise of Milestone 2.
"""
import pytest

@pytest.mark.asyncio
async def test_clean_attribution_one_worker_one_skill(
    test_db, worker_instance, seeded_skills
):
    """One worker + one skill = one clean usage record."""
    # 1. Spawn worker, send task with <meta>{"load_skill": "unit-test"}</meta>
    # 2. Worker executes task
    # 3. Worker calls skill_feedback(applied=True, note="...")
    # 4. Task completes
    # 5. Assert: exactly 1 usage record for unit-test skill
    # 6. Assert: record.task_succeeded matches actual outcome
    # 7. Assert: record.applied == True (from skill_feedback)
    # 8. Assert: record.iterations == worker's actual iterations
    # 9. Assert: record.duration_seconds == worker's actual duration
    # 10. Assert: NO other skill has a usage record for this instance

@pytest.mark.asyncio
async def test_parallel_workers_different_skills(
    test_db, seeded_skills
):
    """Parallel workers with different skills → independent usage records."""
    # 1. Spawn worker_a with <meta>{"load_skill": "unit-test"}</meta>
    # 2. Spawn worker_b with <meta>{"load_skill": "mock-test"}</meta>
    # 3. Both complete tasks
    # 4. Assert: unit-test has records from worker_a only
    # 5. Assert: mock-test has records from worker_b only
    # 6. Assert: no cross-attribution

@pytest.mark.asyncio
async def test_auto_load_skill_visible_in_metrics_e2e(
    test_db, tester_instance, seeded_tester_skills
):
    """test-strategy (auto_load) is visible in metrics end-to-end."""
    # 1. Tester instance has test-strategy as auto_load
    # 2. Tester completes a planning task
    # 3. Assert: skill_usage_records has test-strategy rows
    # 4. Assert: trigger engine can evaluate test-strategy
    # 5. Assert: A/B testing can target test-strategy
```

### Task 6.6: Run Full Test Suite on PostgreSQL

```bash
# Ensure PostgreSQL is the test DB
# Run the full test suite
pytest tests/ -v --timeout=300 -x

# Specifically run skill-related tests
pytest tests/test_skill_*.py -v --timeout=300
pytest tests/test_ab_*.py -v --timeout=300
pytest tests/test_trigger_*.py -v --timeout=300
```

## Key Files

| File | Change Type | Purpose |
|------|------------|---------|
| `tests/test_skill_meta_tag_integration.py` | NEW | Phase 1 integration tests |
| `tests/test_auto_load_metrics_integration.py` | NEW | Phase 2 integration tests |
| `tests/test_ab_composite_integration.py` | NEW | Phase 3 integration tests |
| `tests/test_trigger_tier2_integration.py` | NEW | Phase 4 integration tests |
| `tests/test_skill_worker_e2e.py` | NEW | End-to-end validation |

## Constraints
- ALL tests must run on PostgreSQL (primary dev/test DB)
- No SQLite-only syntax in test queries
- Integration tests need a running ensemble daemon or mocked services
- Tests must be deterministic (use fixed timestamps, seeded data)

## Deliverables
- [ ] Meta tag skill loading integration test passes
- [ ] Worker reuse with different skills passes
- [ ] Auto_load metrics tracking integration test passes
- [ ] A/B composite scoring integration test passes
- [ ] Tie-breaking favors challenger
- [ ] A/B stats filtered to test period only
- [ ] consecutive_failures routes to analyze
- [ ] Tier 2 prompt includes new metrics
- [ ] Clean 1:1 attribution end-to-end test passes
- [ ] Parallel workers independent attribution test passes
- [ ] Full test suite passes on PostgreSQL
